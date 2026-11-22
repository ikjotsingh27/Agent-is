"""What the agent may do on its own, what needs the user's OK, and what is never allowed.

Each kind of risky action has its own setting, "ask" (the default) or "allow", which the user can change in
Settings later as they come to trust the agent. The user can also save "always allow" rules from an approval
card. Some things are blocked whatever the settings say (the blueprint's "never allowed" list).

The command check is a best effort reading of the command text, not a security boundary: it is a fence, not a
vault. When it cannot tell what a command does, it asks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# kind of action -> what the user sees in Settings and on approval cards
CATEGORIES = {
    "delete": "Delete files",
    "install": "Install or update software",
    "network": "Use the internet from the terminal or a script",
    "outside": "Read or change files outside the workspace",
    "launch": "Open other programs or windows",
    "system": "Change Windows settings, services or other programs",
    "other": "Run commands Pilot cannot check",
}
DEFAULT_POLICY = {name: "ask" for name in CATEGORIES}

NEVER = [  # (pattern, reason) - blocked even if the user allows everything
    (r"login data|logins\.json|key[34]\.db|web data|cookies\.sqlite|local state",
     "reading browser passwords, saved cards or cookies"),
    (r"\bcmdkey\b|\bvaultcmd\b|credentialmanager|get-storedcredential|\bdpapi\b|protecteddata",
     "reading saved passwords"),
    (r"mimikatz|sekurlsa|\blsass\b|\\sam\b|hklm:?\\sam", "reading Windows password data"),
    (r"set-mppreference.*-disable|add-mppreference.*exclusion|disable-?windows ?defender",
     "turning off Windows security"),
    (r"settings\.json", "reading Pilot's own settings, which hold your keys"),
    (r"\b(alpaca[-_]?trade|robin_?stocks|ccxt|python-binance|cbpro)\b.*(order|buy|sell|withdraw|transfer)"
     r"|\b(stripe\.transfer|stripe\.payout|paypalrestsdk.*payout|web3.*send_?transaction|eth_sendtransaction)\b"
     r"|api\.(binance|coinbase|kraken|alpaca\.markets|broker\.alpaca\.markets).*(order|withdraw|transfer)",
     "moving money between accounts or trading securities"),
]

SAFE_COMMANDS = {
    "dir", "ls", "gci", "get-childitem", "type", "cat", "gc", "get-content", "echo", "write-output",
    "write-host", "pwd", "get-location", "cd", "sl", "set-location", "select-string", "sls", "measure-object",
    "measure", "sort-object", "sort", "select-object", "select", "where-object", "where", "?", "foreach-object",
    "%", "format-table", "ft", "format-list", "fl", "get-date", "get-filehash", "test-path", "new-item", "ni",
    "mkdir", "md", "set-content", "add-content", "out-file", "copy-item", "copy", "cp", "move-item", "move",
    "mv", "rename-item", "ren", "python", "py", "python3", "compare-object", "get-unique", "group-object",
    "convertto-json", "convertfrom-json", "convertto-csv", "convertfrom-csv", "import-csv", "export-csv",
    "get-item", "gi", "resolve-path", "split-path", "join-path", "findstr", "tree", "expand-archive",
    "compress-archive", "out-string", "get-member", "write-error", "sleep", "start-sleep", "get-random",
}
KIND_WORDS = {
    "delete": {"rm", "del", "erase", "rd", "rmdir", "remove-item", "ri", "clear-content", "clc", "clear-item"},
    "install": {"pip", "pip3", "winget", "choco", "scoop", "npm", "npx", "yarn", "pnpm", "install-module",
                "install-package", "msiexec", "update-module", "conda", "uv"},
    "network": {"curl", "curl.exe", "wget", "iwr", "irm", "invoke-webrequest", "invoke-restmethod",
                "start-bitstransfer", "git", "ssh", "scp", "sftp", "ftp", "telnet", "nc", "ncat", "ping",
                "test-netconnection", "tnc", "nslookup", "resolve-dnsname", "send-mailmessage"},
    "launch": {"start", "start-process", "saps", "invoke-item", "ii", "explorer", "explorer.exe", "notepad",
               "msedge", "chrome", "code", "mspaint", "calc"},
    "system": {"reg", "reg.exe", "regedit", "set-itemproperty", "new-itemproperty", "remove-itemproperty",
               "set-executionpolicy", "schtasks", "sc", "sc.exe", "net", "net.exe", "netsh", "bcdedit",
               "diskpart", "format", "shutdown", "restart-computer", "stop-computer", "stop-process", "kill",
               "taskkill", "set-service", "stop-service", "start-service", "new-service", "wmic", "bitsadmin",
               "setx", "icacls", "takeown", "attrib", "set-acl", "new-localuser", "add-localgroupmember",
               "set-date", "set-timezone", "powercfg"},
    "other": {"iex", "invoke-expression", "powershell", "powershell.exe", "pwsh", "cmd", "cmd.exe", "&", ".",
              "invoke-command", "icm", "start-job", "add-type", "wsl", "bash"},
}
NETWORK_TEXT = re.compile(r"https?://|net\.webclient|system\.net|urllib|requests\.|http\.client|socket|"
                          r"smtplib|ftplib|aiohttp|httpx|websocket", re.I)
DELETE_TEXT = re.compile(r"os\.remove|os\.unlink|shutil\.rmtree|\.unlink\(|os\.rmdir|send2trash", re.I)
SYSTEM_TEXT = re.compile(r"\bwinreg\b|subprocess|os\.system|ctypes|hk(lm|cu|ey_)|registry::|\[environment\]::set",
                         re.I)
INSTALL_TEXT = re.compile(r"-m\s+pip\b|pip\s+install|ensurepip", re.I)


@dataclass
class Verdict:
    blocked: str = ""  # why it is never allowed, or ""
    kinds: list = field(default_factory=list)  # risky kinds found, in CATEGORIES
    reasons: list = field(default_factory=list)  # plain words, one per finding
    rule: str = ""  # the "always allow" rule this command would match, e.g. "pip install"

    @property
    def risky(self) -> bool:
        return bool(self.kinds)


def split_parts(command: str) -> list:
    """Split a command line at ; | && || and new lines (outside quotes)."""
    parts, buf, quote = [], "", ""
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            buf += ch
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote, buf = ch, buf + ch
        elif ch in ";|\n" or command[i:i + 2] == "&&":
            parts.append(buf)
            buf = ""
            if command[i:i + 2] in ("&&", "||"):
                i += 1
        else:
            buf += ch
        i += 1
    parts.append(buf)
    return [p.strip() for p in parts if p.strip()]


def words(part: str) -> list:
    return re.findall(r"\"[^\"]*\"|'[^']*'|\S+", part)


def _outside(token: str) -> bool:
    t = token.strip("'\"").lower()
    return bool(re.match(r"^([a-z]:[\\/]|\\\\|~|\$env:|\$home|/)", t) or ".." in t.replace("\\", "/").split("/")
                or t.startswith("$env") or re.match(r"^%[a-z_][a-z0-9_]*%", t) is not None  # %APPDATA%, not "%Y-%m"
                or "appdata" in t or "programdata" in t)


def _script_text(first: str, ws: list, files_dir) -> str:
    """For python commands, the code that will run: inline -c code or the script file in the workspace."""
    if first not in ("python", "py", "python3"):
        return ""
    if "-c" in ws:
        idx = ws.index("-c")
        return " ".join(ws[idx + 1:])
    for token in ws[1:]:
        if token.lower().endswith(".py") and files_dir is not None:
            try:
                path = (files_dir / token.strip("'\"")).resolve()
                if path.is_file() and path.stat().st_size < 2_000_000:
                    return path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    return ""


def classify(command: str, files_dir=None) -> Verdict:
    verdict = Verdict()
    low = command.lower()
    for pattern, why in NEVER:
        if re.search(pattern, low):
            verdict.blocked = f"Never allowed: {why}."
            return verdict
    found: dict = {}
    rules = []

    def add(kind, why):
        found.setdefault(kind, why)

    for part in split_parts(command):
        ws = words(part)
        if not ws:
            continue
        first = ws[0].strip("'\"").lower().replace("\\", "/").split("/")[-1]
        second = ws[1].lower() if len(ws) > 1 else ""
        matched = False
        for kind, names in KIND_WORDS.items():
            if first in names:
                add(kind, f"'{ws[0]}' ({CATEGORIES[kind].lower()})")
                rules.append(f"{first} {second}" if kind in ("install", "network")
                             and re.fullmatch(r"[a-z][a-z0-9-]*", second) else first)
                matched = True
        if not matched and first not in SAFE_COMMANDS:
            add("other", f"'{ws[0]}' is a command Pilot does not know")
            rules.append(first)
        if any(_outside(t) for t in ws[1:]) or (first in ("cd", "sl", "set-location") and len(ws) > 1 and _outside(ws[1])):
            add("outside", "it names a place outside the workspace")
        code = _script_text(first, ws, files_dir) + " " + part
        low_code = code.lower()
        for pattern, why in NEVER:
            if re.search(pattern, low_code):
                verdict.blocked = f"Never allowed: {why}."
                return verdict
        if INSTALL_TEXT.search(code):
            add("install", "it installs Python packages")
            rules.append("pip install")
        if NETWORK_TEXT.search(code):
            add("network", "it uses the internet")
        if DELETE_TEXT.search(code):
            add("delete", "it deletes files")
        if SYSTEM_TEXT.search(code):
            add("system", "it can start other programs or change Windows settings")
        if re.search(r"settings\.json|data[\\/]+(usage|tool_output)", code, re.I):
            verdict.blocked = "Never allowed: reading Pilot's own settings, which hold your keys."
            return verdict
        if re.search(r"\$\(|`|-enc(odedcommand)?\b|\[system\.|\[io\.", part, re.I):
            add("other", "it uses tricks Pilot cannot check")
    verdict.kinds = list(found)
    verdict.reasons = list(found.values())
    # "Always allow" rules are offered only for plain, checkable commands. Anything outside the workspace,
    # system changes and commands Pilot cannot read can be allowed only by switching the whole kind on in Settings.
    simple = not set(found) & {"other", "outside", "system"}
    verdict.rule = rules[0] if simple and len(set(rules)) == 1 else ""
    return verdict


def decide(verdict: Verdict, policy: dict, always_allow: list) -> str:
    """'block', 'ask' or 'run' for this command under the user's current permissions."""
    if verdict.blocked:
        return "block"
    needs_ok = [k for k in verdict.kinds if (policy or {}).get(k, "ask") != "allow"]
    if not needs_ok:
        return "run"
    if verdict.rule and verdict.rule in (always_allow or []):
        return "run"
    return "ask"
