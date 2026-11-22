import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

from src.core.agent_tools import AgentTools, ApprovalRequest
from src.core.approvals import ApprovalQueue, second_check
from src.core.context import Conversation
from src.core.memory import MemoryStore
from src.core.router import pick_model
from src.core.settings import Settings
from src.core.workspace import Workspace
from src.ui.controller import Controller
from tests.helpers import make_agent, text_response, tool_response


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pilot-mem-test-"))
        self.store = MemoryStore(self.tmp / "memory")

    def test_save_list_edit_append_search_and_delete(self):
        note = self.store.save_note("About Sam", "Likes criminology and coffee.")
        self.assertEqual(note.slug, "about-sam")
        self.assertTrue((self.tmp / "memory" / "about-sam.md").is_file())
        self.assertIn("# About Sam", note.path.read_text(encoding="utf-8"))

        # Append adds to the existing note
        updated = self.store.save_note("About Sam", "Prefers dark mode.", append=True)
        self.assertIn("Likes criminology", updated.content)
        self.assertIn("Prefers dark mode.", updated.content)

        # Search finds by title or body
        self.store.save_note("Weekend Ideas", "Hiking and reading.")
        self.assertEqual(len(self.store.list_notes()), 2)
        hits = self.store.search("criminology")
        self.assertEqual([n.slug for n in hits], ["about-sam"])

        # Survives a new MemoryStore instance on the same folder (restart)
        reloaded = MemoryStore(self.tmp / "memory")
        self.assertEqual(reloaded.get_note("About Sam").content, updated.content)

        self.assertTrue(reloaded.delete_note("about-sam"))
        self.assertIsNone(reloaded.get_note("About Sam"))
        self.assertFalse(reloaded.delete_note("nope"))

    def test_memory_tools_remember_recall_forget(self):
        settings = Settings({}, path=self.tmp / "settings.json")
        ws = Workspace(settings, root=self.tmp / "agent")
        tools = {t.name: t.func for t in AgentTools(settings, ws, memory=self.store).tools()}
        self.assertIn("Saved memory note 'Preferences'", tools["remember"]({"title": "Preferences", "content": "Short answers"}))
        self.assertIn("Short answers", tools["recall"]({"query": "Preferences"}))
        self.assertIn("Deleted memory note", tools["forget"]({"title": "Preferences"}))
        self.assertIn("no saved memory notes", tools["recall"]({}))

    def test_conversation_and_memory_survive_controller_restart(self):
        settings_path = self.tmp / "settings.json"
        c1 = Controller(Settings({"provider": "mock"}, path=settings_path))
        c1.save_memory_note("Goal", "Finish Session 4")
        c1.send("hello before restart")
        self.assertEqual(len(c1.agent.conversation.turns), 1)

        # Simulate closing and reopening Pilot on the same settings file
        c2 = Controller(Settings({"provider": "mock"}, path=settings_path))
        self.assertEqual(c2.memory_notes()[0].content, "Finish Session 4")
        self.assertEqual(len(c2.agent.conversation.turns), 1)
        transcript = c2.saved_transcript()
        self.assertEqual(transcript[0][:2], ("you", "hello before restart"))
        self.assertEqual(transcript[1][0], "pilot")

        # New chat clears saved conversation file
        c2.reset_chat()
        c3 = Controller(Settings({"provider": "mock"}, path=settings_path))
        self.assertEqual(c3.agent.conversation.turns, [])


class ApprovalQueueAndSecondCheckTests(unittest.TestCase):
    def test_second_check_flags_odd_actions_and_hides_secrets(self):
        secret = "AIza-secret-key-999999999"
        redact = lambda s: s.replace(secret, "[hidden]")

        # 1. Recursive delete
        f1 = second_check("Remove-Item -Recurse temp_dir", "clean temp folder", ["delete"], redact=redact)
        self.assertIn("Second check:", f1)
        self.assertIn("whole folders", f1)

        # 2. Reason mismatch (deletes files, but reason says "check the date")
        f2 = second_check(f"Remove-Item notes.txt # {secret}", "check the date", ["delete"], redact=redact)
        self.assertIn("does not mention deleting", f2)
        self.assertNotIn(secret, f2)

        # 3. Personal folder outside workspace
        f3 = second_check(r"Get-Content C:\Users\Sam\Documents\tax.txt", "read tax file", ["outside"], redact=redact)
        self.assertIn("personal folders", f3)

        # 4. Normal transparent single-file delete with matching reason -> no extra flag needed
        f4 = second_check("Remove-Item old.txt", "Delete old.txt to tidy up", ["delete"], redact=redact)
        self.assertEqual(f4, "")

    def test_approval_queue_pending_badge_and_cross_screen_resolution(self):
        q = ApprovalQueue()
        changes = []
        q.on_change = lambda: changes.append(q.pending_count())
        req = ApprovalRequest("Remove-Item -Recurse cache", "clean up", ["delete"], "remove-item")
        item = q.enqueue(req)
        self.assertEqual(q.pending_count(), 1)
        self.assertIn("Second check:", item.flag)
        self.assertEqual(req.flag, item.flag)

        notified = []
        q.add_listener(item.id, notified.append)

        # Resolve from the Approvals screen while another thread waits
        result_box = {}
        t = threading.Thread(target=lambda: result_box.setdefault("ans", q.wait(item, timeout=2.0)))
        t.start()
        self.assertTrue(q.resolve(item.id, "once"))
        t.join(timeout=2.0)

        self.assertEqual(result_box["ans"], "once")
        self.assertEqual(q.pending_count(), 0)
        self.assertEqual(notified, ["once"])
        self.assertEqual(q.history()[0].status, "once")
        self.assertIn(1, changes)
        self.assertEqual(changes[-1], 0)


class CheapModelRouterTests(unittest.TestCase):
    def test_routes_easy_greetings_to_cheap_and_tool_tasks_to_main(self):
        s = Settings({"models": {"main": "claude-sonnet-5-5", "cheap": "claude-haiku-5-5"}})
        self.assertEqual(pick_model("hello!", s), "claude-haiku-5-5")
        self.assertEqual(pick_model("thanks, how are you?", s), "claude-haiku-5-5")
        self.assertEqual(pick_model("make me a pdf file for criminology", s), "claude-sonnet-5-5")
        self.assertEqual(pick_model("search the web for python 3.13", s), "claude-sonnet-5-5")
        self.assertEqual(pick_model("hello!", s, has_loaded_tools=True), "claude-sonnet-5-5")

    def test_agent_uses_cheap_model_for_first_call_on_easy_turn(self):
        agent, provider, *_ = make_agent(
            [text_response("Hi!")],
            overrides={"models": {"main": "claude-sonnet-5-5", "cheap": "claude-haiku-5-5"}},
        )
        agent.run_task("hi there")
        self.assertEqual(provider.calls[0]["model"], "claude-haiku-5-5")


class OfficeAndPdfReadTests(unittest.TestCase):
    def test_read_file_extracts_docx_and_pdf_text(self):
        tmp = Path(tempfile.mkdtemp(prefix="pilot-doc-test-"))
        settings = Settings({}, path=tmp / "settings.json")
        ws = Workspace(settings, root=tmp / "agent")
        tools = {t.name: t.func for t in AgentTools(settings, ws).tools()}

        # Build a valid minimal .docx (ZIP with word/document.xml)
        docx_path = ws.files / "essay.docx"
        with zipfile.ZipFile(docx_path, "w") as zf:
            zf.writestr(
                "word/document.xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>Hello from Word document</w:t></w:r></w:p></w:body></w:document>",
            )
        self.assertIn("Hello from Word document", tools["read_file"]({"path": "essay.docx"}))

        # Build a valid minimal PDF with a text stream
        pdf_path = ws.files / "note.pdf"
        stream = b"BT /F1 12 Tf (Hello from PDF file) Tj ET"
        pdf_bytes = (
            b"%PDF-1.4\n"
            b"1 0 obj\n<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
            + stream + b"\nendstream\nendobj\n%%EOF\n"
        )
        pdf_path.write_bytes(pdf_bytes)
        self.assertIn("Hello from PDF file", tools["read_file"]({"path": "note.pdf"}))


if __name__ == "__main__":
    unittest.main()
