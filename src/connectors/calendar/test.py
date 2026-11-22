"""The Google Calendar connection check: validates HTTPS secret iCal link and parses events."""
from __future__ import annotations

from ..base import CheckResult
from .connector import CalendarClient, CalendarError, validate_ical_url


def check_calendar(values: dict, saved_secrets: dict, saved_data: dict,
                   transport=None, now=None) -> CheckResult:
    name = (values.get("calendar_name") or saved_data.get("calendar_name") or "My Calendar").strip() or "My Calendar"
    raw_url = (values.get("ical_url") or saved_secrets.get("ical_url") or "").strip()
    try:
        url = validate_ical_url(raw_url)
        client = CalendarClient(url, transport=transport, now=now)
        all_events = client.fetch_events()
        upcoming = client.upcoming(days=7)
    except CalendarError as exc:
        return CheckResult(False, exc.message, exc.fix)

    return CheckResult(
        True,
        f"Connected to '{name}' ({len(all_events)} events found, {len(upcoming)} in the next 7 days).",
        data={"calendar_name": name},
        secrets={"ical_url": url},
    )
