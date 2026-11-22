"""The Telegram connection check: is the token right, and who should Pilot answer?

Steps: 1) the token looks like a bot token, 2) Telegram knows the bot, 3) someone has messaged the bot.
That someone (during setup, the user) becomes the only person Pilot answers. Then the bot says hello to them.
"""
from __future__ import annotations

from ..base import CheckResult
from .connector import TOKEN_SHAPE, TelegramClient, TelegramError


def _latest_private_sender(updates: list):
    for update in reversed(updates):
        msg = update.get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") == "private":
            sender = msg.get("from") or {}
            return chat.get("id"), sender.get("first_name") or sender.get("username") or "you"
    return None, ""


def run(token: str, saved_data: dict, transport=None) -> CheckResult:
    if not TOKEN_SHAPE.match(token):
        return CheckResult(False, "That does not look like a bot token.",
                           "A token looks like 123456789:ABCdef... Copy the whole token from BotFather "
                           "(send /token to BotFather to see it again), then Paste.")
    client = TelegramClient(token, transport=transport)
    try:
        me = client.get_me()
        updates = client.get_updates(offset=None, timeout=0)
    except TelegramError as exc:
        return CheckResult(False, exc.message, exc.fix)
    bot = me.get("username", "")
    owner_id, owner_name = _latest_private_sender(updates)
    if owner_id is None and saved_data.get("owner_chat_id") and saved_data.get("bot_username") == bot:
        owner_id, owner_name = saved_data["owner_chat_id"], saved_data.get("owner_name", "you")  # re-test, same bot
    if owner_id is None:
        return CheckResult(False, f"The token works: your bot is @{bot}. It has not had a message yet.",
                           f"In Telegram, open t.me/{bot}, tap Start, send hi, then press Test connection again.",
                           nearly=True)
    offset = max((u.get("update_id", 0) for u in updates), default=saved_data.get("offset", 0) - 1) + 1
    try:
        client.send_message(owner_id, "Pilot is connected. Send me a message any time Pilot is open on your computer.")
    except TelegramError as exc:
        return CheckResult(False, exc.message, exc.fix)
    return CheckResult(
        True, f"Connected to @{bot}. Pilot will answer only {owner_name}. Check Telegram for a hello.",
        data={"bot_username": bot, "owner_chat_id": owner_id, "owner_name": owner_name, "offset": offset},
        secrets={"bot_token": token},
    )
