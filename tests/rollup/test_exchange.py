import json
from pathlib import Path
import tempfile
import unittest

from rollup.backends import Fixture
from rollup.exchange import advance, current
from rollup.protocol import digest
from rollup.run import save
from tests.rollup.test_protocol import summary


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)
        save(self.run / "state.json", {"week": 1, "role": "advocate", "history": {}, "watch": [], "records": []})
        save(self.run / "edge.json", {"weeks": {str(w): [summary(week=w)] for w in range(1, 27)}})

    def call(self, raw=None, events=None):
        state, _, _, _, batches = current(self.run)
        prompt = batches[state.get("batch_index", 0)][1]
        raw = Fixture().complete(prompt) if raw is None else raw
        audit = {"prompt_sha256": digest(prompt), "reply_sha256": digest(raw), "events": events or [],
                 "reported_usage": "unavailable", "transcript_source": "unit-test synthetic transcript"}
        return advance(self.run, raw, audit)

    def test_missing_audit_and_forbidden_tool_void_call(self):
        with self.assertRaises(ValueError):
            advance(self.run, "{}", {})
        with self.assertRaises(ValueError):
            self.call(events=[{"operation": "read_answer_file", "run": str(self.run)}])
        self.assertEqual(json.loads((self.run / "state.json").read_text())["role"], "advocate")

    def test_four_roles_close_one_week_and_checkpoint(self):
        for _ in range(4):
            state = self.call()
        self.assertEqual(state["week"], 2)
        self.assertEqual(state["records"], [1])
        self.assertEqual(len(state["ledger"]), 4)
        self.assertTrue((self.run / "week-01.json").exists())

    def test_one_retry_then_empty_argument_both_attempts_counted(self):
        with self.assertRaisesRegex(ValueError, "one retry"):
            self.call("bad")
        state = self.call("bad")
        self.assertEqual(state["role"], "skeptic")
        self.assertEqual(len(state["ledger"]), 2)
        self.assertTrue(all(not x["text"] for x in state["replies"]["advocate"].values()))

    def test_two_weeks_stop_without_capacity_evidence(self):
        for _ in range(8):
            self.call()
        with self.assertRaisesRegex(ValueError, "weekly capacity"):
            self.call()


if __name__ == "__main__":
    unittest.main()
