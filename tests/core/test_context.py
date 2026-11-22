import unittest

from src.core.context import Conversation


class ContextTests(unittest.TestCase):
    def test_keeps_only_recent_turns_and_summarizes_the_rest(self):
        c = Conversation(recent_turns=2)
        for n in range(4):
            c.add_user(f"question {n}")
            c.add_message({"role": "assistant", "content": [{"type": "text", "text": f"answer {n}"}]})
        self.assertEqual(len(c.turns), 2)
        self.assertEqual(len(c.summary_lines), 2)
        window = c.window()
        self.assertEqual(len(window), 4)
        self.assertIn("question 0", window[0]["content"])
        self.assertIn("question 2", window[0]["content"].split("]")[1])
        self.assertEqual(window[0]["role"], "user")

    def test_window_does_not_change_stored_messages(self):
        c = Conversation(recent_turns=1)
        c.add_user("one")
        c.add_user("two")
        c.window()
        self.assertEqual(c.turns[0][0]["content"], "two")

    def test_tool_exchange_is_never_split(self):
        c = Conversation(recent_turns=1)
        c.add_user("use a tool")
        c.add_message({"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "x", "input": {}}]})
        c.add_message({"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "r"}]})
        c.add_message({"role": "assistant", "content": [{"type": "text", "text": "done"}]})
        self.assertEqual(len(c.window()), 4)
        c.add_user("next")
        self.assertEqual(len(c.window()), 1)  # the whole old turn rolled into the summary together

    def test_summary_is_capped(self):
        c = Conversation(recent_turns=1, max_summary_chars=100)
        for n in range(30):
            c.add_user(f"a fairly long question number {n} " * 3)
        self.assertLessEqual(len(c.summary()), 100)

    def test_rollback_and_reset(self):
        c = Conversation()
        idx = c.add_user("hello")
        c.rollback(idx)
        self.assertEqual(c.turns, [])
        c.add_user("again")
        c.reset()
        self.assertEqual((c.turns, c.summary_lines), ([], []))


if __name__ == "__main__":
    unittest.main()
