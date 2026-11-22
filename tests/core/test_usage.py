import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.core.settings import Settings
from src.core.usage import CapReached, UsageTracker
from tests.helpers import text_response


def tracker(**overrides):
    tmp = Path(tempfile.mkdtemp(prefix="pilot-test-"))
    settings = Settings(overrides)
    fixed = datetime(2026, 11, 21, 12, 0).astimezone()
    return UsageTracker(settings, usage_dir=tmp, now=lambda: fixed), tmp


class UsageTests(unittest.TestCase):
    def test_cost_with_prices(self):
        t, _ = tracker()
        self.assertAlmostEqual(t.cost("claude-sonnet-5-5", 1000, 500), (1000 * 2 + 500 * 10) / 1e6)

    def test_cost_is_blank_when_no_price(self):
        t, _ = tracker()
        self.assertIsNone(t.cost("some-new-model", 1000, 500))

    def test_missing_cache_write_price_falls_back_to_input_price(self):
        t, _ = tracker()
        self.assertAlmostEqual(t.cost("claude-haiku-5-5", 1000, 0, cache_write=1000), (1000 * 0.10 + 1000 * 0.10) / 1e6)

    def test_record_writes_a_line_and_totals_add_up(self):
        t, tmp = tracker()
        t.record(task="a", model="claude-sonnet-5-5", response=text_response(input_tokens=100, output_tokens=20,
                 cache_read_tokens=50), started=1.0, ended=3.5)
        t.record(task="b", model="claude-sonnet-5-5", response=text_response(input_tokens=10, output_tokens=2),
                 started=4.0, ended=5.0)
        self.assertTrue((tmp / "2026-11-21.jsonl").exists())
        totals = t.totals()
        self.assertEqual(totals["calls"], 2)
        self.assertEqual(totals["total_tokens"], 100 + 20 + 50 + 10 + 2)
        self.assertTrue(totals["usd_complete"])
        self.assertEqual(t.session["calls"], 2)

    def test_unpriced_model_marks_total_incomplete(self):
        t, _ = tracker()
        r = text_response()
        r.model = "mystery"
        t.record(task="a", model="mystery", response=r, started=0, ended=1)
        self.assertFalse(t.totals()["usd_complete"])

    def test_token_cap(self):
        t, _ = tracker(limits={"daily_tokens": 100})
        t.record(task="a", model="claude-sonnet-5-5", response=text_response(input_tokens=90, output_tokens=20),
                 started=0, ended=1)
        with self.assertRaises(CapReached):
            t.check_caps()

    def test_money_cap(self):
        t, _ = tracker(limits={"daily_tokens": 0, "daily_usd": 0.001})
        t.record(task="a", model="claude-sonnet-5-5", response=text_response(input_tokens=1000, output_tokens=0),
                 started=0, ended=1)
        with self.assertRaises(CapReached):
            t.check_caps()

    def test_under_the_cap_is_fine(self):
        t, _ = tracker()
        t.record(task="a", model="claude-sonnet-5-5", response=text_response(), started=0, ended=1)
        t.check_caps()

    def test_bad_log_lines_are_skipped(self):
        t, tmp = tracker()
        (tmp / "2026-11-21.jsonl").write_text("garbage\n", encoding="utf-8")
        self.assertEqual(t.totals()["calls"], 0)


if __name__ == "__main__":
    unittest.main()
