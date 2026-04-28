from telethon import TelegramClient
from telethon.sessions import StringSession

api_id = 1234
api_hash = "your_api_hash"

with TelegramClient("yoursession", api_id, api_hash) as client:
    print(StringSession.save(client.session))