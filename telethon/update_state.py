import logging
import pickle
from collections import deque
from pprint import pprint
from queue import Queue, Empty
from datetime import datetime
from threading import RLock, Thread

from telethon.tl.types.updates import DifferenceSlice

from .tl import types as tl

__log__ = logging.getLogger(__name__)


class UpdateState:
    """Used to hold the current state of processed updates.
       To retrieve an update, .poll() should be called.
    """
    WORKER_POLL_TIMEOUT = 5.0  # Avoid waiting forever on the workers

    def __init__(self, workers=None, initial_state=None):
        """
        Performs ``get_updates`` requests and reflects the current state of
        updates in the API.

        Args:
            workers (:obj:`int`): Three possible cases:
                workers is None: Updates will *not* be stored on self.
                workers = 0:     Another thread is responsible for calling
                                 self.poll()
                workers > 0:     'workers' background threads will be spawned,
                                 any of them will invoke the ``self.handler``.
        """
        self._workers = workers
        self._worker_threads = []

        self.handler = None
        self._updates_lock = RLock()

        # The difference queue of unprocessed updates to the current state
        # Items in this queue take precedence over ones in _updates while
        # processing.
        self._difference_updates = Queue()
        # The queue for incoming updates
        self._updates = Queue()

        # https://core.telegram.org/api/updates
        if initial_state is None:
            self._state = tl.updates.State(0, 0, datetime.now(), 0, 0)
        else:
            self._state = initial_state

    @property
    def state(self):
        return self._state

    def can_poll(self):
        """Returns True if a call to .poll() won't lock"""
        return self._updates.not_empty or self._difference_updates.not_empty

    def poll(self, timeout=None):
        """Polls an update or blocks until an update object is available.
           If 'timeout is not None', it should be a floating point value,
           and the method will 'return None' if waiting times out.
        """
        update = None

        # First see if there are unprocessed catch_up differences available
        if self._difference_updates.not_empty:
            try:
                update = self._difference_updates.get(block=False)
            except Empty:
                pass  # Could possibly happen in a threaded environment

        if not update:
            # Pick a new update from the queue
            try:
                update = self._updates.get(timeout=timeout)
            except Empty:
                return

        if isinstance(update, Exception):
            raise update  # Some error was set through (surely StopIteration)

        return update

    def get_workers(self):
        return self._workers

    def set_workers(self, n):
        """Changes the number of workers running.
           If 'n is None', clears all pending updates from memory.
        """
        self.stop_workers()
        self._workers = n
        if n is None:
            while self._updates:
                self._updates.get()
            while self._difference_updates:
                __log__.info("Picking from DIFFERENCE QUEUE")
                self._difference_updates.get()
        else:
            self.setup_workers()

    workers = property(fget=get_workers, fset=set_workers)

    def stop_workers(self):
        """Raises "StopIterationException" on the worker threads to stop them,
           and also clears all of them off the list
        """
        if self._workers:
            with self._updates_lock:
                # Insert at the beginning so the very next poll causes an error
                # on all the worker threads
                for _ in range(self._workers):
                    self._difference_updates.put(StopIteration())
                    self._updates.put(StopIteration())

        for t in self._worker_threads:
            t.join()

        self._worker_threads.clear()

    def setup_workers(self):
        if self._worker_threads or not self._workers:
            # There already are workers, or workers is None or 0. Do nothing.
            return

        for i in range(self._workers):
            thread = Thread(
                target=UpdateState._worker_loop,
                name='UpdateWorker{}'.format(i),
                daemon=True,
                args=(self, i)
            )
            self._worker_threads.append(thread)
            thread.start()

    def _worker_loop(self, wid):
        while True:
            try:
                update = self.poll(timeout=UpdateState.WORKER_POLL_TIMEOUT)
                if update and self.handler:
                    self.handler(update)
            except StopIteration:
                break
            except:
                # We don't want to crash a worker thread due to any reason
                __log__.exception('Unhandled exception on worker %d', wid)

    def put_difference(self, difference):
        """Processes the difference returned by a GetDifferenceRequest"""
        new_updates = (difference.new_messages or []) + \
                      (difference.new_encrypted_messages or []) + \
                      (difference.other_updates or [])
        # invalid = [x for x in new_updates if not hasattr(x, 'id')]
        # types_printed = list()
        # for i in invalid:
        #     if type(i) not in types_printed:
        #         pprint(i.to_dict())
        #         types_printed.append(type(i))
        # TODO: nasty hack
        new_updates.sort(key=lambda ud: ud.id if hasattr(ud, 'id') else -1)

        # Put the received updates in the queue
        for u in new_updates:
            self._difference_updates.put_nowait(u)

        if difference.intermediate_state:
            self._state = difference.intermediate_state


    def process(self, update):
        """Processes an update object. This method is normally called by
           the library itself.
        """
        if self._workers is None:
            return  # No processing needs to be done if nobody's working

        with self._updates_lock:
            if isinstance(update, tl.updates.State):
                self._state = update
                __log__.debug('Saved new updates state')
                return  # Nothing else to be done

            if hasattr(update, 'pts'):
                self._state.pts = update.pts

            # After running the script for over an hour and receiving over
            # 1000 updates, the only duplicates received were users going
            # online or offline. We can trust the server until new reports.
            if isinstance(update, tl.UpdateShort):
                self._updates.put(update.update)
            # Expand "Updates" into "Update", and pass these to callbacks.
            # Since .users and .chats have already been processed, we
            # don't need to care about those either.
            elif isinstance(update, (tl.Updates, tl.UpdatesCombined)):
                for u in update.updates:
                    self._updates.put(u)
            # TODO Handle "tl.UpdatesTooLong"
            else:
                self._updates.put(update)
