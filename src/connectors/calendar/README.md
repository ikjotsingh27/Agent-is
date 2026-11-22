# Google Calendar connector

Reads upcoming events from one Google Calendar using its private iCal (`.ics`) secret address and
Python's standard library only (`urllib.request` + RFC 5545 parser).

## Rules
- **Strictly read-only** and scoped to the single calendar chosen by the user.
- **Secret link protection:** the secret iCal URL is stored under `connector_secrets.calendar.ical_url`
  and redacted everywhere like an API key.
- **SSRF protection:** validates public `https://` URLs via `check_url()` (blocking localhost/private IPs).
- **Prompt-injection defense:** calendar descriptions are wrapped with an untrusted-text warning.
- **Lazy tool:** `calendar_events` is only loaded when Google Calendar is connected and turned on.
