import unittest

from src.core.agent import CUT_OFF_MESSAGE, FIRST_CALL_TARGET_TOKENS
from src.core.providers.base import ProviderError, Response
from src.core.tokens import estimate_tokens
from src.core.tools import LOAD_TOOL, Tool
from src.core.usage import CapReached
from tests.helpers import make_agent, text_response, tool_response

TIME_ON = {"enabled_tools": ["current_time"]}


class AgentTests(unittest.TestCase):
    def test_hello_is_one_small_call_with_no_tools_sent(self):
        agent, provider, usage, _ = make_agent()
        result = agent.run_task("hello")
        self.assertEqual(result.calls, 1)
        self.assertEqual(result.text, "Mock reply: hello")
        self.assertIsNone(provider.calls[0]["tools"])
        self.assertEqual(usage.totals()["calls"], 1)

    def test_fresh_hello_is_under_the_target(self):
        agent, *_ = make_agent()
        system, messages, tools = agent.baseline_request()
        total = estimate_tokens(system) + estimate_tokens(messages) + estimate_tokens(tools)
        self.assertLessEqual(total, FIRST_CALL_TARGET_TOKENS)

    def test_tools_load_lazily(self):
        script = [
            tool_response(("t1", "load_tool", {"name": "current_time"})),
            tool_response(("t2", "current_time", {})),
            text_response("It is time."),
        ]
        agent, provider, _, _ = make_agent(script, TIME_ON)
        result = agent.run_task("what time is it?")
        self.assertEqual(result.calls, 3)
        self.assertEqual(result.text, "It is time.")
        first, second = provider.calls[0]["tools"], provider.calls[1]["tools"]
        self.assertEqual([t["name"] for t in first], ["load_tool"])            # only the tiny loader at first
        self.assertEqual([t["name"] for t in second], ["load_tool", "current_time"])  # full definition after asking
        self.assertEqual(provider.calls[0]["system"], provider.calls[2]["system"])    # stable prompt for caching
        self.assertIn("current_time", provider.calls[0]["system"])
        self.assertEqual(first[0], LOAD_TOOL)

    def test_loading_an_unknown_tool_is_an_error_result_not_a_crash(self):
        script = [tool_response(("t1", "load_tool", {"name": "nope"})), text_response("ok")]
        agent, provider, _, _ = make_agent(script, TIME_ON)
        agent.run_task("go")
        result_block = provider.calls[1]["messages"][-1]["content"][0]
        self.assertTrue(result_block["is_error"])

    def test_call_cap_stops_the_loop_and_keeps_history_valid(self):
        script = [tool_response((f"t{i}", "load_tool", {"name": "current_time"})) for i in range(10)]
        agent, provider, _, _ = make_agent(script, dict(TIME_ON, limits={"max_calls_per_task": 3}))
        result = agent.run_task("loop forever")
        self.assertTrue(result.hit_call_cap)
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(agent.conversation.turns[-1][-1]["role"], "assistant")

    def test_daily_cap_blocks_then_override_allows(self):
        agent, provider, usage, _ = make_agent(overrides={"limits": {"daily_tokens": 1}})
        usage.record(task="x", model="claude-sonnet-5-5", response=text_response(input_tokens=5),
                     started=0, ended=1)
        with self.assertRaises(CapReached):
            agent.run_task("hello")
        self.assertEqual(agent.conversation.turns, [])  # rolled back, so a retry is clean
        agent.cap_override = True
        self.assertEqual(agent.run_task("hello").calls, 1)

    def test_provider_error_rolls_back_the_turn(self):
        class Broken:
            name = "broken"

            def complete(self, **kw):
                raise ProviderError("nope")

        agent, *_ = make_agent(provider=Broken())
        with self.assertRaises(ProviderError):
            agent.run_task("hello")
        self.assertEqual(agent.conversation.turns, [])

    def test_big_tool_output_is_trimmed_and_saved(self):
        big = Tool("big", "makes a lot of text", "desc", {"type": "object", "properties": {}}, lambda a: "x" * 10000)
        script = [
            tool_response(("t1", "load_tool", {"name": "big"})),
            tool_response(("t2", "big", {})),
            text_response("done"),
        ]
        agent, provider, _, tmp = make_agent(script, {"enabled_tools": ["big"], "limits": {"max_tool_output_chars": 100}},
                                             tools=[big])
        agent.run_task("make text")
        sent = provider.calls[2]["messages"][-1]["content"][0]["content"]
        self.assertLess(len(sent), 400)
        self.assertIn("Trimmed", sent)
        saved = list((tmp / "tool_output").glob("*-big.txt"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(len(saved[0].read_text(encoding="utf-8")), 10000)

    def test_a_failing_tool_does_not_crash_the_loop(self):
        def boom(args):
            raise RuntimeError("kaput")
        bad = Tool("bad", "always fails", "desc", {"type": "object", "properties": {}}, boom)
        script = [tool_response(("t1", "load_tool", {"name": "bad"})), tool_response(("t2", "bad", {})),
                  text_response("sorry")]
        agent, provider, *_ = make_agent(script, {"enabled_tools": ["bad"]}, tools=[bad])
        self.assertEqual(agent.run_task("try").text, "sorry")
        block = provider.calls[2]["messages"][-1]["content"][0]
        self.assertTrue(block["is_error"])
        self.assertIn("kaput", block["content"])

    def test_context_stays_small_over_a_long_chat(self):
        agent, provider, *_ = make_agent(overrides={"context": {"recent_turns": 2}})
        for n in range(10):
            agent.run_task(f"message {n}")
        self.assertLessEqual(len(provider.calls[-1]["messages"]), 4)

    def test_output_limit_rises_when_tools_are_enabled(self):
        plain_agent, plain_provider, *_ = make_agent()
        plain_agent.run_task("hello")
        self.assertEqual(plain_provider.calls[0]["max_tokens"], 1024)

        tool_agent, tool_provider, *_ = make_agent(overrides=TIME_ON)
        tool_agent.run_task("hello")
        self.assertEqual(tool_provider.calls[0]["max_tokens"], 4096)

    def test_cut_off_reply_retries_once_in_smaller_parts_and_recovers(self):
        cut = Response(text="", content=[], stop_reason="length", truncated=True, input_tokens=10, output_tokens=1024)
        script = [cut, text_response("Done in smaller parts.")]
        agent, provider, *_ = make_agent(script, TIME_ON)
        result = agent.run_task("write a big file")
        self.assertEqual(result.calls, 2)
        self.assertEqual(result.text, "Done in smaller parts.")
        retry_messages = provider.calls[1]["messages"]
        self.assertEqual([m["role"] for m in retry_messages], ["user", "assistant", "user"])
        self.assertIn("smaller parts", retry_messages[-1]["content"])

    def test_cut_off_twice_returns_friendly_message_without_settings_jargon(self):
        cut = Response(text="", content=[], stop_reason="length", truncated=True, input_tokens=10, output_tokens=1024)
        agent, provider, *_ = make_agent([cut, cut], TIME_ON)
        result = agent.run_task("write something huge")
        self.assertEqual(result.calls, 2)
        self.assertEqual(result.text, CUT_OFF_MESSAGE)
        self.assertNotIn("settings.json", result.text)
        self.assertEqual(agent.conversation.turns[-1][-1]["role"], "assistant")

    def test_partial_text_cut_off_keeps_text_and_adds_note(self):
        partial = Response(text="Part one of the answer", content=[{"type": "text", "text": "Part one of the answer"}],
                           stop_reason="length", truncated=False, input_tokens=10, output_tokens=1024)
        agent, *_ = make_agent([partial])
        result = agent.run_task("explain everything")
        self.assertTrue(result.text.startswith("Part one of the answer"))
        self.assertIn("cut short", result.text)
        self.assertNotIn("settings.json", result.text)


if __name__ == "__main__":
    unittest.main()
