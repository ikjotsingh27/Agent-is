# Telegram connector (notes for whoever works on this next)

- `guide.json`: the steps shown in the app. Keep them plain and short; a beginner follows them without help.
- `test.py`: the connection check. Order: token shape, `getMe`, `getUpdates` for the owner's first message,
  then a hello from the bot. The newest private chat that messaged the bot becomes `owner_chat_id`.
- `connector.py`: `TelegramClient` (Bot API over HTTPS, standard library), `TelegramRunner` (long polling in a
  background thread), `TelegramConnector` (ties both to the framework).

Rules that matter:

- Only `owner_chat_id` gets answers. Everyone else is ignored without a reply.
- The bot token is part of Telegram's URL, so never log or show URLs. Errors are written by hand without it.
- Waiting for messages costs no model tokens; `/start` and non-text messages get a fixed reply with no model call.
- The read position (`offset`) is saved after every message, so nothing is answered twice after a restart.
- Two programs reading the same bot get HTTP 409 from Telegram. A short overlap after a restart is normal and
  is not reported; a lasting one is.
- Plain text only: no `parse_mode`, so model replies with stray `*` or `_` can never fail to send.
- Files: incoming documents, photos, audio, video, voice notes, animations and stickers (up to Telegram's 20 MB bot download cap) are saved to `data/agent/files/inbox/`; outgoing files from `send_file` are sent via `sendDocument` (up to Telegram's 50 MB upload cap).
- Not yet: running with the window closed (Session 5).

Telegram Bot API reference: https://core.telegram.org/bots/api
