import json
import unittest

from src.core.providers import MockProvider, ProviderError, get_provider
from src.core.providers.anthropic_provider import AnthropicProvider
from src.core.providers.openai_compatible import (OpenAICompatibleProvider, pick_models, to_chat_messages,
                                                  to_chat_tools)
from src.core.settings import Settings

KEY = "sk-test-1234567890"


def fake_transport(status, body, seen):
    def transport(url, headers, raw, timeout):
        seen.append({"url": url, "headers": headers, "payload": json.loads(raw.decode("utf-8"))})
        return status, json.dumps(body).encode("utf-8")
    return transport


OK_BODY = {
    "model": "claude-sonnet-5-5",
    "content": [{"type": "text", "text": "Hi there"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 30, "output_tokens": 7, "cache_read_input_tokens": 100,
              "cache_creation_input_tokens": 20},
}


class AnthropicProviderTests(unittest.TestCase):
    def call(self, status=200, body=OK_BODY, **kw):
        seen = []
        provider = AnthropicProvider(KEY, transport=fake_transport(status, body, seen), **kw)
        return provider, seen

    def test_parses_text_and_usage(self):
        provider, seen = self.call()
        r = provider.complete(model="m", system="sys", messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(r.text, "Hi there")
        self.assertEqual((r.input_tokens, r.output_tokens, r.cache_read_tokens, r.cache_write_tokens),
                         (30, 7, 100, 20))
        self.assertEqual(seen[0]["headers"]["x-api-key"], KEY)

    def test_prompt_caching_marks_system_block(self):
        provider, seen = self.call(prompt_caching=True)
        provider.complete(model="m", system="sys", messages=[])
        self.assertEqual(seen[0]["payload"]["system"][0]["cache_control"], {"type": "ephemeral"})

    def test_caching_off_sends_plain_system(self):
        provider, seen = self.call(prompt_caching=False)
        provider.complete(model="m", system="sys", messages=[])
        self.assertEqual(seen[0]["payload"]["system"], "sys")

    def test_no_tools_key_when_none(self):
        provider, seen = self.call()
        provider.complete(model="m", system="sys", messages=[])
        self.assertNotIn("tools", seen[0]["payload"])

    def test_parses_tool_use(self):
        body = dict(OK_BODY, content=[{"type": "tool_use", "id": "t1", "name": "load_tool",
                                       "input": {"name": "x"}}], stop_reason="tool_use")
        provider, _ = self.call(body=body)
        r = provider.complete(model="m", system="", messages=[])
        self.assertEqual(r.tool_calls[0].name, "load_tool")
        self.assertEqual(r.content[0]["type"], "tool_use")
        self.assertFalse(r.truncated)

    def test_max_tokens_marks_empty_or_incomplete_tool_use_as_truncated(self):
        empty = dict(OK_BODY, content=[], stop_reason="max_tokens")
        r1 = self.call(body=empty)[0].complete(model="m", system="", messages=[])
        self.assertTrue(r1.truncated)
        self.assertEqual(r1.tool_calls, [])

        cut_tool = dict(OK_BODY, content=[{"type": "tool_use", "id": "t1", "name": "write_file", "input": {}}],
                        stop_reason="max_tokens")
        r2 = self.call(body=cut_tool)[0].complete(model="m", system="", messages=[])
        self.assertTrue(r2.truncated)
        self.assertEqual(r2.tool_calls, [])
        self.assertEqual(r2.content, [])

    def test_friendly_errors_never_contain_the_key(self):
        for status in (400, 401, 404, 429, 500, 529):
            provider, _ = self.call(status=status, body={"error": {"message": f"bad {KEY} here"}})
            with self.assertRaises(ProviderError) as ctx:
                provider.complete(model="m", system="", messages=[])
            self.assertNotIn(KEY, str(ctx.exception))
        provider, _ = self.call(status=401, body={})
        with self.assertRaises(ProviderError) as ctx:
            provider.complete(model="m", system="", messages=[])
        self.assertIn("setup", str(ctx.exception))

    def test_no_key_is_a_friendly_error(self):
        with self.assertRaises(ProviderError):
            AnthropicProvider("").complete(model="m", system="", messages=[])

    def test_network_problem_passes_through(self):
        def boom(*a):
            raise ProviderError("Could not reach the model service.")
        with self.assertRaises(ProviderError):
            AnthropicProvider(KEY, transport=boom).complete(model="m", system="", messages=[])


CHAT_OK = {
    "model": "gemini-x-flash-lite",
    "choices": [{"message": {"role": "assistant", "content": "Hi there"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 40, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 10}},
}


def chat_transport(status, body, seen):
    def transport(method, url, headers, raw, timeout):
        seen.append({"method": method, "url": url, "headers": headers,
                     "payload": json.loads(raw.decode("utf-8")) if raw else None})
        return status, json.dumps(body).encode("utf-8")
    return transport


class GeminiProviderTests(unittest.TestCase):
    def call(self, status=200, body=CHAT_OK, **kw):
        seen = []
        p = OpenAICompatibleProvider(KEY, "https://example.test/v1", transport=chat_transport(status, body, seen), **kw)
        return p, seen

    def test_parses_text_and_usage_and_keeps_key_out_of_url(self):
        p, seen = self.call(reasoning_effort="low")
        r = p.complete(model="m", system="sys", messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(r.text, "Hi there")
        self.assertEqual((r.input_tokens, r.output_tokens, r.cache_read_tokens), (30, 5, 10))
        self.assertEqual(seen[0]["headers"]["Authorization"], f"Bearer {KEY}")
        self.assertNotIn(KEY, seen[0]["url"])
        self.assertEqual(seen[0]["payload"]["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual(seen[0]["payload"]["reasoning_effort"], "low")
        self.assertNotIn("tools", seen[0]["payload"])

    def test_translates_tool_round_trip(self):
        msgs = [{"role": "user", "content": "time?"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "load_tool",
                                                   "input": {"name": "x"}, "extra_content": {"google": {"s": 1}}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "Loaded"}]}]
        out = to_chat_messages("", msgs)
        self.assertEqual(out[1]["tool_calls"][0]["function"], {"name": "load_tool", "arguments": '{"name": "x"}'})
        self.assertEqual(out[1]["tool_calls"][0]["extra_content"], {"google": {"s": 1}})
        self.assertEqual(out[2], {"role": "tool", "tool_call_id": "t1", "content": "Loaded"})
        tools = to_chat_tools([{"name": "a", "description": "d", "input_schema": {"type": "object"}}])
        self.assertEqual(tools[0]["function"]["parameters"], {"type": "object"})

    def test_parses_tool_calls(self):
        body = {"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "load_tool", "arguments": '{"name": "x"}'}}]},
            "finish_reason": "tool_calls"}], "usage": {}}
        p, _ = self.call(body=body)
        r = p.complete(model="m", system="", messages=[])
        self.assertEqual((r.tool_calls[0].name, r.tool_calls[0].input), ("load_tool", {"name": "x"}))
        self.assertEqual(r.content[0]["type"], "tool_use")
        self.assertFalse(r.truncated)

    def test_length_empty_reply_is_marked_truncated(self):
        body = {"choices": [{"message": {"content": None}, "finish_reason": "length"}], "usage": {}}
        r = self.call(body=body)[0].complete(model="m", system="", messages=[])
        self.assertTrue(r.truncated)
        self.assertEqual(r.text, "")
        self.assertEqual(r.tool_calls, [])

    def test_broken_tool_call_json_is_marked_truncated_and_dropped(self):
        body = {"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": '{"path": "a.txt", "content": "cut off'}}]},
            "finish_reason": "length"}], "usage": {}}
        r = self.call(body=body)[0].complete(model="m", system="", messages=[])
        self.assertTrue(r.truncated)
        self.assertEqual(r.tool_calls, [])
        self.assertEqual(r.content, [])

    def test_errors_are_friendly_and_hide_the_key(self):
        for status in (400, 401, 403, 404, 429, 500):
            p, _ = self.call(status=status, body=[{"error": {"message": f"API key {KEY} bad"}}])
            with self.assertRaises(ProviderError) as ctx:
                p.complete(model="m", system="", messages=[])
            self.assertNotIn(KEY, str(ctx.exception))
        p, _ = self.call(status=429, body={"error": {"message": "quota"}})
        with self.assertRaises(ProviderError) as ctx:
            p.complete(model="m", system="", messages=[])
        self.assertIn("free usage limit", str(ctx.exception))

    def test_list_models_and_pick(self):
        p, seen = self.call(body={"data": [{"id": "models/gemini-9.0-flash"}, {"id": "models/gemini-9.0-flash-lite"},
                                            {"id": "models/gemini-9.5-flash-lite-preview"}, {"id": "models/gemini-9.0-pro"},
                                            {"id": "models/gemini-9.0-flash-image"}, {"id": "models/embedding-1"}]})
        ids = p.list_models()
        self.assertEqual(seen[0]["method"], "GET")
        self.assertIn("gemini-9.0-flash-lite", ids)
        picked = pick_models(ids)
        self.assertEqual(picked["main"], "gemini-9.5-flash-lite-preview")
        self.assertEqual(picked["flash"], "gemini-9.0-flash")
        self.assertEqual(pick_models([])["main"], "")


class RegistryTests(unittest.TestCase):
    def test_get_provider(self):
        self.assertEqual(get_provider(Settings({"provider": "mock"})).name, "mock")
        self.assertEqual(get_provider(Settings({"provider": "anthropic", "api_keys": {"anthropic": KEY}})).name,
                         "anthropic")
        self.assertEqual(get_provider(Settings({"api_keys": {"gemini": KEY}})).name, "gemini")

    def test_unknown_provider(self):
        with self.assertRaises(ProviderError):
            get_provider(Settings({"provider": "nope"}))

    def test_mock_echoes_last_user_text(self):
        r = MockProvider().complete(model="m", system="s", messages=[{"role": "user", "content": "yo"}])
        self.assertEqual(r.text, "Mock reply: yo")


if __name__ == "__main__":
    unittest.main()
