"""The Gmail connection check: validates address & App Password, verifies read-only IMAP mailbox access."""
from __future__ import annotations

import re

from ..base import CheckResult
from .connector import GmailClient, GmailError

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def check_gmail(values: dict, saved_secrets: dict, saved_data: dict,
                imap_factory=None, smtp_factory=None) -> CheckResult:
    email_addr = (values.get("email") or saved_data.get("email") or "").strip()
    raw_pw = values.get("app_password") or saved_secrets.get("app_password") or ""
    clean_pw = re.sub(r"\s+", "", raw_pw)
    mailbox = (values.get("mailbox") or saved_data.get("mailbox") or "INBOX").strip() or "INBOX"
    access = (values.get("access") or saved_data.get("access") or "read_only").strip()
    if access not in ("read_only", "read_and_send"):
        access = "read_only"

    if not email_addr:
        return CheckResult(False, "Type your Gmail address first.",
                           "Enter the full address, like you@gmail.com.")
    if not EMAIL_RE.match(email_addr):
        return CheckResult(False, "That does not look like an email address.",
                           "Use your full Gmail address, like you@gmail.com.")
    if not clean_pw:
        return CheckResult(False, "Paste your 16-letter Google App Password first.",
                           "Open Google App Passwords (Step 2) to generate one for Pilot.")
    if len(clean_pw) < 12:
        return CheckResult(False, "That App Password looks too short.",
                           "Google App Passwords are 16 letters (spaces are fine). Do not use your normal account password.")

    client = GmailClient(email_addr, clean_pw, mailbox=mailbox,
                         imap_factory=imap_factory, smtp_factory=smtp_factory)
    try:
        count = client.verify()
    except GmailError as exc:
        return CheckResult(False, exc.message, exc.fix)

    mode_lbl = "read-only" if access == "read_only" else "read & send (with approval)"
    return CheckResult(
        True,
        f"Connected to {email_addr} ({mailbox}: {count} messages, {mode_lbl}).",
        data={"email": email_addr, "mailbox": mailbox, "access": access},
        secrets={"app_password": clean_pw},
    )
