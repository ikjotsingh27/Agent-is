# Discord connector (notes for whoever works on this next)

- `guide.json`: the 6 beginner-friendly setup steps shown in the app (Discord Developer Portal, Message Content Intent, bot token, private server invite, User ID, Test connection).
- `test.py`: validates the token shape and numeric User ID, checks `GET /users/@me`, opens a DM channel with `POST /users/@me/channels`, and sends a hello DM. Error `50007` (user does not share a server with the bot or blocks server DMs) returns `nearly=True` in amber with clear instructions.
- `gateway.py`: small pure-stdlib RFC 6455 TLS WebSocket client for Discord Gateway v10 (no third-party packages).
- `connector.py`: `DiscordClient` (REST API v10 + multipart file upload + CDN attachment download), `DiscordRunner` (Gateway background thread with heartbeats, reconnects, and close-code checks for `4004` / `4014`), and `DiscordConnector`.

Rules that matter:

- Only `owner_user_id` gets answers, and only in private DMs (`guild_id` must be absent). Server channels, bots, and strangers are ignored silently.
- Every Discord HTTP request must send a `DiscordBot (...)` User-Agent header so Cloudflare does not block Python's default `urllib` User-Agent.
- Attachment downloads only accept `https://cdn.discordapp.com` and `https://media.discordapp.net` (capped at 50 MB and workspace 5 GB disk cap). Outgoing uploads cap at Discord's 25 MB standard bot limit.
- Never log or display `bot_token` anywhere. Waiting on the Gateway costs no model tokens.
- Runs while the Pilot window is open; always-on background running comes in Session 5.

Discord API reference: https://discord.com/developers/docs/reference
