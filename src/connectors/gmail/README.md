# Gmail connector

Connects Pilot to one Gmail mailbox/label using Python's standard library (`imaplib` + `smtplib` over SSL)
and a Google App Password — no Google Cloud Console or third-party packages required.

## Rules
- **Scoped to one mailbox/label** (default `INBOX`, configurable in setup). Pilot never reads Google Drive.
- **Read-only by default.** Even when `read_and_send` is chosen, `gmail_send` always asks for Approve or Deny
  (with Pilot's second-check safety review) and never offers "Always allow".
- **Never deletes mail.** IMAP opens the mailbox with `readonly=True`.
- **Prompt-injection defense:** every email summary and body is wrapped with an untrusted-text warning.
- **Lazy tools:** `gmail_list`, `gmail_read` (and optionally `gmail_send`) are only registered when Gmail is
  connected and turned on, so idle token count is unaffected when Gmail is off.
