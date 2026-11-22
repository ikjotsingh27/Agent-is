"""The Discord connection check: verify the bot token and User ID, open a DM, and say hello.

Steps:
1) Check that the bot token and User ID look right.
2) Ask Discord who the bot is (GET /users/@me).
3) Open a DM channel with the owner's User ID and send a hello DM.
"""
from __future__ import annotations

from ..base import CheckResult
from .connector import TOKEN_SHAPE, USER_ID_SHAPE, DiscordClient, DiscordError


def run(token: str, owner_user_id: str, saved_data: dict, transport=None) -> CheckResult:
    if not TOKEN_SHAPE.match(token):
        return CheckResult(
            False, "That does not look like a Discord bot token.",
            "A Discord bot token has three parts separated by dots. Open the Discord Developer Portal -> Bot -> "
            "Reset Token, copy all of it, then Paste.",
        )
    if not USER_ID_SHAPE.match(owner_user_id):
        return CheckResult(
            False, "That does not look like a Discord User ID.",
            "A User ID is a long number (17 to 20 digits), not your @username. Turn on Developer Mode in Discord "
            "(Settings -> Advanced), right-click your own name, and click Copy User ID.",
        )
    client = DiscordClient(token, transport=transport)
    try:
        me = client.get_me()
    except DiscordError as exc:
        return CheckResult(False, exc.message, exc.fix)
    bot_name = me.get("username") or "your bot"
    try:
        dm = client.open_dm(owner_user_id)
    except DiscordError as exc:
        return CheckResult(False, exc.message, exc.fix)
    channel_id = str(dm.get("id") or "")
    if not channel_id:
        return CheckResult(False, "Discord did not open a DM channel for that User ID.",
                           "Check your User ID and try again.")
    recipients = dm.get("recipients") or []
    owner_name = ""
    if recipients and isinstance(recipients[0], dict):
        owner_name = recipients[0].get("global_name") or recipients[0].get("username") or ""
    owner_name = owner_name or saved_data.get("owner_name") or "you"
    try:
        client.send_message(
            channel_id,
            "Pilot is connected. Send me a DM or any file here any time Pilot is open on your computer.",
        )
    except DiscordError as exc:
        return CheckResult(False, exc.message, exc.fix, nearly=(exc.kind == "dm_blocked"))
    return CheckResult(
        True,
        f"Connected to {bot_name}. Pilot will answer only {owner_name} in Discord DMs. Check Discord for a hello.",
        data={
            "bot_username": bot_name,
            "owner_user_id": str(owner_user_id),
            "owner_name": owner_name,
            "dm_channel_id": channel_id,
        },
        secrets={"bot_token": token},
    )
