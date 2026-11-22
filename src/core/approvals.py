"""Approvals queue and the 'second check' review step for risky actions.

Every risky action waits here until the user clicks Approve, Deny, or Always allow (either on the inline
card in Chat or on the Approvals screen). If nobody answers within 10 minutes, the answer is Deny.

Before an action is shown, second_check() reviews it against the user's rules and flags anything unusual in
plain words (for example recursive deletes, touching personal folders outside the workspace, downloading and
running code, system/startup changes, or a reason that does not match the command). The review step never sees
API keys or connector secrets.
"""
from __future__ import annotations

import itertools
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime


EMAIL_TO_RE = re.compile(r"send email to\s+([^\s\r\n<>]+@[^\s\r\n<>]+)", re.I)
LARGE_MONEY_RE = re.compile(r"(?:[\$£€]\s*(\d[\d,]*(?:\.\d+)?)|\b(\d[\d,]*(?:\.\d+)?)\s*(?:usd|dollars|eur|gbp)\b)", re.I)


def extract_email_recipient(action: str) -> str:
    m = EMAIL_TO_RE.search(action or "")
    return m.group(1).strip(".,;:").lower() if m else ""


def second_check(action: str, reason: str, risks: list | None = None, redact=None,
                 known_recipients: set[str] | list[str] | None = None) -> str:
    """Flag anything odd about a risky action in plain words. Never sees API keys or secrets."""
    clean_action = redact(action or "") if redact else (action or "")
    clean_reason = redact(reason or "") if redact else (reason or "")
    low = clean_action.lower()
    low_reason = clean_reason.lower()
    flags: list[str] = []

    if re.search(r"-recurse\b|\b/s\b|shutil\.rmtree|\*\.\*|\bremove-item\b.*\*|\bdel\b.*\*|\brm\b.*-r", low):
        flags.append("This deletes whole folders or multiple files at once, which cannot be undone.")
    if re.search(r"\biex\b|invoke-expression|\|\s*(powershell|pwsh|cmd|bash|sh)\b|exec\(|eval\(", low):
        flags.append("This runs dynamically generated or downloaded code in one step.")
    if re.search(r"\\(desktop|documents|downloads|pictures|onedrive)\\", low):
        flags.append("This reaches into your personal folders outside Pilot's workspace.")
    if re.search(r"\b(schtasks|reg|regedit|bcdedit|netsh|sc|set-service|new-service)\b|\\currentversion\\run", low):
        flags.append("This changes Windows system or startup settings that stay after Pilot closes.")
    if re.search(r"\b(buy|purchase|checkout|payment|transfer|wire|crypto|wallet|order)\b", low):
        big_amt = ""
        for m in LARGE_MONEY_RE.finditer(clean_action + " " + clean_reason):
            raw_num = (m.group(1) or m.group(2) or "").replace(",", "")
            try:
                if float(raw_num) >= 100.0:
                    big_amt = m.group(0).strip()
                    break
            except ValueError:
                pass
        if big_amt:
            flags.append(f"This looks like a large purchase or payment ({big_amt}). Double-check every detail first.")
        else:
            flags.append("This looks related to money or a purchase. Double-check the details first.")
    recipient = extract_email_recipient(clean_action)
    known = {str(r).strip().lower() for r in (known_recipients or []) if r}
    if recipient:
        if recipient not in known:
            flags.append(f"This sends an email to a new address ({recipient}) you have not approved before. Check the recipient carefully.")
    elif re.search(r"send-mailmessage|smtplib|mailto:", low):
        flags.append("This sends a message to an external address. Check the recipient carefully.")
    if clean_reason in ("", "(no reason given)") or len(clean_reason.strip()) < 4:
        flags.append("The agent did not give a clear reason for running this command.")
    elif "delete" in " ".join(risks or []).lower() and not re.search(
        r"delet|remov|clean|tidy|clear|eras|replace|old|temp", low_reason
    ):
        flags.append("This command deletes files, even though the stated reason does not mention deleting.")

    if not flags:
        return ""
    return "Second check: " + " ".join(flags)


@dataclass
class ApprovalItem:
    id: str
    action: str
    reason: str
    risks: list = field(default_factory=list)
    rule: str = ""
    flag: str = ""  # plain-words second-check warning, or ""
    connector: str = "Terminal"  # which connector or tool requested this action
    created_at: str = ""
    status: str = "pending"  # "pending", "once", "always", "deny"
    _event: threading.Event = field(default_factory=threading.Event, repr=False)
    _listeners: list = field(default_factory=list, repr=False)


class ApprovalQueue:
    def __init__(self, redact=None, max_history: int = 30, known_recipients_fn=None):
        self._redact = redact
        self._max_history = max_history
        self._known_recipients_fn = known_recipients_fn or (lambda: ())
        self._lock = threading.RLock()
        self._counter = itertools.count(1)
        self._pending: dict[str, ApprovalItem] = {}
        self._history: list[ApprovalItem] = []
        self.on_change = None  # optional callback fn() whenever pending count or list changes

    def pending(self) -> list[ApprovalItem]:
        with self._lock:
            return list(self._pending.values())

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def history(self) -> list[ApprovalItem]:
        with self._lock:
            return list(self._history)

    def enqueue(self, request) -> ApprovalItem:
        """Create a pending approval item with a second-check review flag."""
        flag = getattr(request, "flag", "") or second_check(
            request.action, request.reason, getattr(request, "risks", []),
            redact=self._redact, known_recipients=self._known_recipients_fn(),
        )
        request.flag = flag
        connector = getattr(request, "connector", "") or "Terminal"
        with self._lock:
            item_id = f"approval_{next(self._counter)}"
            request.item_id = item_id
            item = ApprovalItem(
                id=item_id,
                action=request.action,
                reason=request.reason,
                risks=list(getattr(request, "risks", []) or []),
                rule=getattr(request, "rule", "") or "",
                flag=flag,
                connector=connector,
                created_at=datetime.now().strftime("%H:%M"),
            )
            self._pending[item_id] = item
        self._notify()
        return item

    def add_listener(self, item_id: str, callback) -> None:
        """Register a UI callback fn(choice) invoked when this item is resolved from any screen."""
        with self._lock:
            item = self._pending.get(item_id)
            if item is not None:
                item._listeners.append(callback)

    def resolve(self, item_id: str, choice: str) -> bool:
        """Resolve a pending item with 'once', 'always', or 'deny'. Returns True if it was still pending."""
        if choice not in ("once", "always", "deny"):
            choice = "deny"
        with self._lock:
            item = self._pending.pop(item_id, None)
            if item is None:
                return False
            item.status = choice
            self._history.insert(0, item)
            del self._history[self._max_history:]
            listeners = list(item._listeners)
            item._event.set()
        for fn in listeners:
            try:
                fn(choice)
            except Exception:
                pass
        self._notify()
        return True

    def wait(self, item: ApprovalItem, timeout: float = 600.0) -> str:
        item._event.wait(timeout)
        if item.status == "pending":
            self.resolve(item.id, "deny")
        return item.status

    def _notify(self) -> None:
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                pass
