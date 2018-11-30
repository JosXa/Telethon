import logging

from telethon import TelegramClient
from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Button

API_ID = "CHANGEME"  # TODO
API_HASH = "CHANGEME"  # TODO
BOT_TOKEN = "CHANGEME"  # TODO
TEST_PEER = '@lonami'

logging.basicConfig(level=logging.DEBUG)
# For instance, show only warnings and above
logging.getLogger('telethon').setLevel(level=logging.DEBUG)

files = {}  # global


async def initialize(client: TelegramClient):
    # Send some stickers to TEST_PEER and store the documents in `files`
    msg = await client.send_file(TEST_PEER, 'sticker.webp')
    files[1] = msg.media.document

    msg2 = await client.send_file(TEST_PEER, 'sticker2.webp')
    files[2] = msg2.media.document

    await msg.delete()
    await msg2.delete()

    print('Media stored.')


async def send_shit(event: NewMessage.Event):
    buttons = [Button.inline("Change shit around", data='change')]
    await event.reply(file=files[1], buttons=[buttons])


async def edit_shit(event: CallbackQuery.Event):
    await event.edit(file=files[2])  # will raise MEDIA_PREV_INVALID


if __name__ == '__main__':
    client = TelegramClient(session=BOT_TOKEN, api_id=API_ID, api_hash=API_HASH)
    client.start(bot_token=BOT_TOKEN)
    client.loop.run_until_complete(initialize(client))

    # Use /trigger to test
    client.add_event_handler(send_shit, NewMessage(pattern='/trigger'))

    client.add_event_handler(edit_shit, CallbackQuery(data='change'))
    client.run_until_disconnected()
