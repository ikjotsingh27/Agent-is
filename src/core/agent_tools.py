"""The agent's hands: files and a terminal in its workspace, and a read-only browser.

Every tool stays inside the workspace's limits (5 GB disk, 2 GB memory by default). Terminal commands are
checked by permissions.py: safe ones run, risky ones wait for the user's Approve (unless the user has allowed
that kind of action in Settings), and a few are never allowed.

approve(request) is supplied by whoever runs the agent (the window shows a card, the terminal asks y/n) and
returns "once", "always" or "deny".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys
import time

from .browser import Browser, page_for_model, results_for_model
from .memory import MemoryStore
from .pdf import extract_office_text, extract_pdf_text, markdown_to_html
from .permissions import CATEGORIES, DEFAULT_POLICY, classify, decide
from .tools import Tool, builtin_tools
from .workspace import Workspace, WorkspaceError, file_size_label, human

TOOL_GROUPS = {
    "files": ["list_files", "read_file", "write_file", "make_pdf", "send_file"],
    "memory": ["remember", "recall", "forget"],
    "terminal": ["run_command"],
    "browser": ["web_search", "open_page", "page_screenshot"],
}
DEFAULT_GROUPS = {"files": True, "memory": True, "terminal": True, "browser": True}
MAX_READ = 20000
MAX_PDF_SOURCE_BYTES = 2 * 1024 * 1024


@dataclass
class SharedFile:
    path: Path  # absolute path inside workspace
    rel_path: str  # workspace-relative path (forward slashes)
    name: str
    size: int
    note: str = ""


@dataclass
class ApprovalRequest:
    action: str  # exactly what will run
    reason: str  # why the agent wants it, in its own words
    risks: list = field(default_factory=list)  # plain words, from permissions.py
    rule: str = ""  # what "always allow" would save, or "" when that is not offered
    flag: str = ""  # plain-words second-check warning from approvals.py, or ""
    item_id: str = ""  # assigned by ApprovalQueue when queued
    connector: str = "Terminal"  # which connector or tool requested this action


def enabled_tool_names(settings) -> list:
    groups = {**DEFAULT_GROUPS, **(settings.get("agent_tools", default={}) or {})}
    names = list(settings.get("enabled_tools", default=[]) or [])
    for group, on in groups.items():
        if on:
            names += [n for n in TOOL_GROUPS.get(group, []) if n not in names]
    return names


class AgentTools:
    def __init__(self, settings, workspace: Workspace, approve=None, on_activity=None, browser: Browser | None = None,
                 save_settings=None, on_send_file=None, memory: MemoryStore | None = None, on_command=None):
        self.settings = settings
        self.ws = workspace
        self.approve = approve or (lambda request: "deny")
        self.on_activity = on_activity or (lambda text: None)
        self.browser = browser or Browser(workspace)
        self.save_settings = save_settings or settings.save
        self.on_send_file = on_send_file or (lambda shared: None)
        self.memory = memory or MemoryStore(workspace.root.parent / "memory")
        self.on_command = on_command or (lambda cmd, started, ended, exit_code: None)

    # --- files ----------------------------------------------------------------------------------
    def list_files(self, args: dict) -> str:
        folder = self.ws.path(args.get("path", ""))
        if not folder.exists():
            return f"There is no folder called {self.ws.show(folder)} in the workspace."
        if folder.is_file():
            return f"{self.ws.show(folder)} is a file ({human(folder.stat().st_size)})."
        entries = sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        lines = [f"{'[folder] ' if p.is_dir() else ''}{self.ws.show(p)}" + ("" if p.is_dir() else f"  ({human(p.stat().st_size)})")
                 for p in entries[:200]]
        more = f"\n...and {len(entries) - 200} more" if len(entries) > 200 else ""
        return (f"Workspace: {self.ws.summary()}\n" + ("\n".join(lines) if lines else "(empty)") + more)

    def read_file(self, args: dict) -> str:
        path = self.ws.path(args.get("path", ""))
        if not path.is_file():
            return f"There is no file called {self.ws.show(path)} in the workspace."
        ext = path.suffix.lower()
        if ext == ".pdf":
            text = extract_pdf_text(path)
            if not text.strip():
                return (f"{self.ws.show(path)} is a PDF file ({file_size_label(path.stat().st_size)}), "
                        "but no selectable text was found in it (it may be a scanned image PDF).")
        elif ext in (".docx", ".pptx", ".xlsx", ".odt"):
            text = extract_office_text(path)
            if not text.strip():
                return f"{self.ws.show(path)} ({file_size_label(path.stat().st_size)}) had no readable text inside."
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_READ:
            return text[:MAX_READ] + f"\n[Showing the first {MAX_READ:,} of {len(text):,} characters.]"
        return text

    def write_file(self, args: dict) -> str:
        path = self.ws.path(args.get("path", ""))
        content = str(args.get("content", ""))
        data = content.encode("utf-8")
        self.ws.check_space(len(data))
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if args.get("append") else "w"
        with open(path, mode, encoding="utf-8") as fh:
            fh.write(content)
        return f"Saved {self.ws.show(path)} ({human(path.stat().st_size)}). Workspace: {self.ws.summary()}."

    def make_pdf(self, args: dict) -> str:
        raw_path = str(args.get("path", "")).strip()
        if not raw_path:
            return "Give a file name for the PDF, like notes.pdf."
        if not raw_path.lower().endswith(".pdf"):
            raw_path += ".pdf"
        dest = self.ws.path(raw_path)
        if dest.exists() and dest.is_dir():
            return f"{self.ws.show(dest)} is a folder. Give a PDF file name instead."
        title = str(args.get("title", "")).strip()
        content = str(args.get("content", "") or "")
        source_file = str(args.get("source_file", "")).strip()
        if content.strip() and source_file:
            return "Give either 'content' or 'source_file', not both."
        if source_file:
            src = self.ws.path(source_file)
            if not src.is_file():
                return f"There is no file called {self.ws.show(src)} in the workspace."
            if src.stat().st_size > MAX_PDF_SOURCE_BYTES:
                return f"{self.ws.show(src)} is too large to turn into a PDF at once (over 2 MB)."
            content = src.read_text(encoding="utf-8", errors="replace")
        if not content.strip():
            return "Give either 'content' (text or Markdown) or 'source_file' (a text or Markdown file in the workspace)."
        html_doc = markdown_to_html(content, title=title)
        self.ws.check_space(max(len(html_doc.encode("utf-8")) * 2, 16 * 1024))
        self.on_activity(f"Creating PDF: {self.ws.show(dest)}")
        self.browser.print_pdf(html_doc, dest)
        return f"Saved PDF as {self.ws.show(dest)} ({human(dest.stat().st_size)}). Workspace: {self.ws.summary()}."

    def send_file(self, args: dict) -> str:
        raw_path = str(args.get("path", "")).strip()
        if not raw_path:
            return "Give the path of a file inside your workspace to send."
        path = self.ws.path(raw_path)
        if not path.exists():
            return f"There is no file called {self.ws.show(path)} in the workspace."
        if path.is_dir():
            return f"{self.ws.show(path)} is a folder. Send a single file (or zip the folder first)."
        note = str(args.get("note", "") or args.get("caption", "")).strip()[:200]
        shared = SharedFile(path=path, rel_path=self.ws.show(path), name=path.name,
                            size=path.stat().st_size, note=note)
        self.on_activity(f"Sending file: {shared.name}")
        self.on_send_file(shared)
        return f"Sent {shared.rel_path} ({file_size_label(shared.size)}) to the user."

    # --- terminal -------------------------------------------------------------------------------
    def run_command(self, args: dict) -> str:
        command = str(args.get("command", "")).strip()
        reason = str(args.get("reason", "")).strip() or "(no reason given)"
        if not command:
            return "No command was given."
        verdict = classify(command, self.ws.files)
        policy = {**DEFAULT_POLICY, **(self.settings.get("permissions", default={}) or {})}
        always = self.settings.get("always_allow", default=[]) or []
        decision = decide(verdict, policy, always)
        if decision == "block":
            return f"{verdict.blocked} This is blocked even with the user's permission. Do not try another way."
        if decision == "ask":
            self.on_activity("Waiting for your OK...")
            answer = self.approve(ApprovalRequest(command, reason, verdict.reasons, verdict.rule))
            if answer == "deny":
                return ("The user said no to this command. Do not try another way to do the same thing. "
                        "Tell them what you wanted to do and ask how they would like to go on.")
            if answer == "always" and verdict.rule:
                self.settings.set("always_allow", sorted(set(always) | {verdict.rule}))
                self.save_settings()
        self.on_activity("Running a command...")
        t0 = time.time()
        if sys.platform == "win32":
            script = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; $ProgressPreference = 'SilentlyContinue'; " + command
            argv = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]
        else:
            argv = ["/bin/bash", "-c", command]
        result = self.ws.run(argv)
        self.on_command(command, t0, time.time(), result.exit_code)
        notes = []
        if result.timed_out:
            notes.append(f"Stopped after {self.ws.command_seconds} seconds (the time limit). Anything it started was closed.")
        if result.hit_memory_limit:
            notes.append(f"It reached the memory limit ({human(self.ws.max_ram)}).")
        try:
            self.ws.check_space()
        except WorkspaceError as exc:
            notes.append(str(exc))
        output = result.output.strip() or "(no output)"
        return f"Exit code {result.exit_code}.\n{output}" + ("\n" + "\n".join(notes) if notes else "")

    # --- browser --------------------------------------------------------------------------------
    def web_search(self, args: dict) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "No search words were given."
        self.on_activity(f"Searching the web for: {query[:60]}")
        return results_for_model(query, self.browser.search(query))

    def open_page(self, args: dict) -> str:
        self.on_activity("Reading a web page...")
        return page_for_model(self.browser.read(str(args.get("url", ""))))

    def page_screenshot(self, args: dict) -> str:
        self.on_activity("Taking a screenshot of a page...")
        path = self.browser.screenshot(str(args.get("url", "")), str(args.get("name", "")))
        return f"Saved the screenshot as {self.ws.show(path)} in the workspace."

    # --- memory ---------------------------------------------------------------------------------
    def remember(self, args: dict) -> str:
        title = str(args.get("title", "")).strip()
        content = str(args.get("content", "")).strip()
        if not title or not content:
            return "Give both a short 'title' and the 'content' to remember."
        self.on_activity(f"Saving to memory: {title[:40]}")
        note = self.memory.save_note(title, content, append=bool(args.get("append", False)))
        return f"Saved memory note '{note.title}' ({note.slug}.md)."

    def recall(self, args: dict) -> str:
        query = str(args.get("query", "")).strip()
        self.on_activity("Checking memory...")
        notes = self.memory.search(query)
        if not notes:
            return f"No memory notes matched '{query}'." if query else "There are no saved memory notes yet."
        parts = [f"Memory notes ({len(notes)}):"]
        for n in notes[:20]:
            preview = n.content[:1500] + ("..." if len(n.content) > 1500 else "")
            parts.append(f"\n## {n.title} ({n.slug}.md)\n{preview or '(empty)'}")
        return "\n".join(parts)

    def forget(self, args: dict) -> str:
        title = str(args.get("title", "")).strip()
        if not title:
            return "Give the title or file name of the memory note to delete."
        if self.memory.delete_note(title):
            return f"Deleted memory note '{title}'."
        return f"There is no memory note called '{title}'."

    # --- registry -------------------------------------------------------------------------------
    def _wrap(self, func):
        def run(args):
            try:
                return func(args)
            except WorkspaceError as exc:
                return str(exc)
        return run

    def tools(self) -> list:
        obj = {"type": "object"}
        path = {"type": "string", "description": "Path inside the workspace, like notes/todo.txt. Empty means the top."}
        return builtin_tools() + [
            Tool("list_files", "list files and folders in your workspace",
                 "List files and folders in your workspace folder, with sizes and how much space is used.",
                 {**obj, "properties": {"path": path}}, self._wrap(self.list_files)),
            Tool("read_file", "read a file in your workspace (text, PDF, Word, Excel, PowerPoint)",
                 "Read the text of a file in your workspace (plain text, Markdown, code, CSV, .pdf, .docx, .xlsx, "
                 ".pptx, or .odt — the first 20,000 characters).",
                 {**obj, "properties": {"path": path}, "required": ["path"]}, self._wrap(self.read_file)),
            Tool("write_file", "create or change a text file in your workspace",
                 "Write a text file in your workspace. Folders are made as needed. Set append to add to the end.",
                 {**obj, "properties": {"path": path, "content": {"type": "string"},
                                        "append": {"type": "boolean"}}, "required": ["path", "content"]},
                 self._wrap(self.write_file)),
            Tool("make_pdf", "save text or Markdown as a PDF file in your workspace",
                 "Create a clean PDF file in your workspace from Markdown or plain text (nothing to install). "
                 "Give 'path' (like report.pdf) and either 'content' directly or 'source_file' (an existing text or "
                 "Markdown file in your workspace, helpful for very long documents written in chunks). Optional 'title'.",
                 {**obj, "properties": {
                     "path": {"type": "string", "description": "Output PDF path inside the workspace, like report.pdf."},
                     "title": {"type": "string", "description": "Optional document title shown at the top."},
                     "content": {"type": "string", "description": "Markdown or plain text for the PDF."},
                     "source_file": {"type": "string", "description": "Existing text or Markdown file in the workspace to convert."},
                 }, "required": ["path"]},
                 self._wrap(self.make_pdf)),
            Tool("send_file", "send any file from your workspace to the user",
                 "Send any file in your workspace (PDF, image, document, archive, script, etc.) directly to the user "
                 "in Chat, Telegram, or Discord. Use this whenever you created or edited a file for the user.",
                 {**obj, "properties": {
                     "path": path,
                     "note": {"type": "string", "description": "Optional short description of the file."},
                 }, "required": ["path"]},
                 self._wrap(self.send_file)),
            Tool("run_command", "run a PowerShell command in your workspace",
                 "Run one PowerShell command on the user's Windows computer, starting in your workspace folder. "
                 "Limits: 2 minutes and 2 GB of memory. Safe commands run at once; deleting, installing, the "
                 "internet, other programs, system changes or paths outside the workspace wait for the user's OK. "
                 "Always say why in 'reason', in a short plain sentence the user will read.",
                 {**obj, "properties": {"command": {"type": "string"},
                                        "reason": {"type": "string", "description": "Why, in plain words."}},
                  "required": ["command", "reason"]}, self._wrap(self.run_command)),
            Tool("web_search", "search the web",
                 "Search the web and get the top results (title, address, short summary).",
                 {**obj, "properties": {"query": {"type": "string"}}, "required": ["query"]},
                 self._wrap(self.web_search)),
            Tool("open_page", "read the text and links of a web page",
                 "Open a web page and get its readable text and links. Read only: no logins, clicks or forms.",
                 {**obj, "properties": {"url": {"type": "string"}}, "required": ["url"]}, self._wrap(self.open_page)),
            Tool("page_screenshot", "save a picture of a web page in your workspace",
                 "Save a screenshot of a web page as a PNG in the workspace's screenshots folder.",
                 {**obj, "properties": {"url": {"type": "string"}, "name": {"type": "string"}}, "required": ["url"]},
                 self._wrap(self.page_screenshot)),
            Tool("remember", "save or update a note in your long-term memory",
                 "Save a plain Markdown note in long-term memory (survives restarts; the user can also view and edit "
                 "it on the Memory screen). Set append=true to add to an existing note.",
                 {**obj, "properties": {
                     "title": {"type": "string", "description": "Short note title, like 'About Sam' or 'Preferences'."},
                     "content": {"type": "string", "description": "Plain text or Markdown facts to remember."},
                     "append": {"type": "boolean"},
                 }, "required": ["title", "content"]},
                 self._wrap(self.remember)),
            Tool("recall", "read or search your long-term memory notes",
                 "Read or search saved memory notes. Leave query empty to list and read all saved notes.",
                 {**obj, "properties": {"query": {"type": "string", "description": "Optional search words or title."}}},
                 self._wrap(self.recall)),
            Tool("forget", "delete a note from your long-term memory",
                 "Delete a saved memory note by its title or file name.",
                 {**obj, "properties": {"title": {"type": "string"}}, "required": ["title"]},
                 self._wrap(self.forget)),
        ]


def permission_rows(settings) -> list:
    """For the Settings screen: (kind, plain label, 'ask' or 'allow')."""
    policy = {**DEFAULT_POLICY, **(settings.get("permissions", default={}) or {})}
    return [(kind, label, policy[kind]) for kind, label in CATEGORIES.items()]
