"""Re-scoring a pilot audit from its audit.json alone (``tools/market/rescore_audit.py``), offline."""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "market"))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "market" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


R = _load("rescore_audit")
RW = _load("reactive_world")
PACKS = ("device_quality", "claims_integrity")


def _alert(available: str, key: str) -> dict[str, Any]:
    """An alert on a week that closes two days after its Sunday (``available`` is a Tuesday in these tests)."""
    t, eid, pred = key.split(":")
    year, week, _ = (date.fromisoformat(available) - timedelta(days=2)).isocalendar()
    return {"week": f"{year:04d}-W{week:02d}", "available_date": available, "rank": 1, "score": 1.0, "key": key,
            "entity_type": t, "entity_id": eid, "predicate": pred, "sites": [], "record_refs": []}


def _audit_doc(outcomes: list[Any], channel_alerts: dict[str, list[dict[str, Any]] | None], settings: dict[str, Any],
               weeks: dict[str, str]) -> dict[str, Any]:
    """An audit document shaped as ``audit()`` writes it, scored by the audit's own ``score_channel`` with
    ``settings`` (its keyword arguments). ``weeks`` gives the first, last and first evaluated week."""
    from mycelic.collective.pilot import audit as A
    channels: dict[str, Any] = {}
    for name in A.CHANNELS:
        alerts = channel_alerts.get(name)
        if alerts is None:
            channels[name] = {"summary": None, "by_outcome": [], "reason": "not run here"}
            continue
        s = A.score_channel(outcomes, alerts, **settings)
        channels[name] = {"summary": s["summary"], "by_outcome": s["by_outcome"],
                          "alert_timeline": s["alert_timeline"], "reason": None}
    return {"kind": A.KIND, "label": "synthetic test audit", "pack": {"id": "device_quality"},
            "settings": {"lookback_weeks": settings["lookback"], "post_weeks": settings["post"], "tie_salt": "pilot"},
            "weeks": {**weeks, "evaluated_weeks": settings["evaluated_weeks"],
                      "available_first": settings["available_first"].isoformat()},
            "channels": channels}


def _doc(channel_alerts: dict[str, list[dict[str, Any]] | None]) -> dict[str, Any]:
    """The small audit: three outcomes, 20 weeks, 8-week windows."""
    from mycelic.collective.pilot import audit as A
    outcomes = [A.Outcome("O1", "2024-06-03", "product", "SD-9", None),
                A.Outcome("O2", "2024-05-06", "product", "IP-7", "crack"),
                A.Outcome("O3", "2024-07-01", "lot", "L10001", None)]
    return _audit_doc(outcomes, channel_alerts,
                      {"lookback": 8, "post": 8, "available_first": date(2024, 3, 5), "evaluated_weeks": 20},
                      {"first": "2023-W52", "last": "2024-W29", "evaluated_from": "2024-W09"})


def _iso_week(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year:04d}-W{week:02d}"


X_ALERTS = [_alert("2024-05-07", "product:SD-9:leak"),        # O1 found
            _alert("2024-06-11", "product:SD-9:leak"),        # O1's own reaction
            _alert("2024-04-02", "lot:L10001:crack")]         # nothing: outside O3's look-back
S_ALERTS = [_alert("2024-04-09", "product:IP-7:crack"),       # O2 found
            _alert("2024-05-14", "product:IP-7:crack"),       # O2's own reaction
            _alert("2024-05-21", "product:SD-9:leak")]        # O1 found again
R_ALERTS = [_alert("2024-06-18", "lot:L10001:noise"),         # O3 found
            _alert("2024-04-16", "product:IP-7:leak")]        # another predicate: not O2's


class DemoRescoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from mycelic.collective.pilot import audit as A
        cls._tmp = tempfile.TemporaryDirectory()
        cls.paths = {}
        for pack_id in PACKS:
            out = Path(cls._tmp.name) / pack_id
            with contextlib.redirect_stdout(io.StringIO()):
                assert A.main(["demo", "--pack", pack_id, "--out", str(out), "--weeks", "48"]) == 0
            cls.paths[pack_id] = out / "audit.json"

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def doc(self, pack_id: str) -> dict[str, Any]:
        return json.loads(self.paths[pack_id].read_text(encoding="utf-8"))

    def test_every_channel_reproduces_the_audit_exactly(self) -> None:
        for pack_id in PACKS:
            doc = self.doc(pack_id)
            got = R.rescore(doc)
            self.assertEqual(got["available_first_from"], "audit")
            for name in ("X", "S", "R_mf"):
                with self.subTest(pack=pack_id, channel=name):
                    self.assertIsNotNone(doc["channels"][name]["summary"])
                    self.assertEqual(got["channels"][name]["summary"], doc["channels"][name]["summary"])
                    self.assertTrue(got["channels"][name]["matches_audit"])
            found_any = {r["outcome_id"] for c in ("X", "S", "R_mf") for r in doc["channels"][c]["by_outcome"]
                         if r["found"]}
            self.assertEqual(got["union"]["summary"]["found"], len(found_any))
            self.assertEqual(got["union"]["of"], ["X", "S", "R_mf"])

    def test_an_audit_without_the_start_date_takes_it_from_its_alert_timeline(self) -> None:
        for pack_id in PACKS:
            with self.subTest(pack=pack_id):
                doc = self.doc(pack_id)
                want = R.rescore(doc)
                del doc["weeks"]["available_first"]
                got = R.rescore(doc)
                self.assertEqual(got["available_first_from"], "alert_timeline")
                self.assertEqual(got["available_first"], want["available_first"])
                self.assertEqual(got["channels"], want["channels"])
                self.assertEqual(got["union"], want["union"])

    def test_cli_prints_the_rescore_and_names_errors(self) -> None:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(R.main([str(self.paths["device_quality"])]), 0)
        got = json.loads(buf.getvalue())
        self.assertEqual(got["kind"], R.KIND)
        self.assertTrue(all(c["matches_audit"] for c in got["channels"].values()))
        with tempfile.TemporaryDirectory() as tmp:
            old = self.doc("device_quality")
            del old["channels"]["S"]["alert_timeline"]
            path = Path(tmp) / "audit.json"
            path.write_text(json.dumps(old), encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(R.main([str(path)]), 1)
                self.assertEqual(R.main([]), 2)
            self.assertIn("channel S has no alert_timeline", err.getvalue())


class UnionTests(unittest.TestCase):
    def test_the_union_finds_what_any_channel_finds_over_all_their_alerts(self) -> None:
        doc = _doc({"X": X_ALERTS, "S": S_ALERTS, "R_mf": R_ALERTS})
        got = R.rescore(doc)
        for name in ("X", "S", "R_mf"):
            self.assertTrue(got["channels"][name]["matches_audit"], name)
        self.assertEqual([doc["channels"][c]["summary"]["found"] for c in ("X", "S", "R_mf")], [1, 2, 1])
        u = got["union"]["summary"]
        self.assertEqual((u["found"], u["outcomes"], u["alerts"]), (3, 3, 8))
        for name in ("X", "S", "R_mf"):
            s = doc["channels"][name]["summary"]
            self.assertGreaterEqual(u["expected_found"], s["expected_found"])
            self.assertGreaterEqual(u["expected_found_excluding_own_post"], s["expected_found_excluding_own_post"])

    def test_the_start_date_from_the_timeline_is_the_audits_own(self) -> None:
        doc = _doc({"X": X_ALERTS, "S": S_ALERTS, "R_mf": R_ALERTS})
        want = R.rescore(doc)
        del doc["weeks"]["available_first"]
        got = R.rescore(doc)
        self.assertEqual((got["available_first"], got["available_first_from"]), ("2024-03-05", "alert_timeline"))
        self.assertEqual((got["channels"], got["union"]), (want["channels"], want["union"]))

    def test_a_channel_that_did_not_run_is_left_out(self) -> None:
        doc = _doc({"X": X_ALERTS, "S": S_ALERTS, "R_mf": None})
        got = R.rescore(doc)
        self.assertEqual(got["union"]["of"], ["X", "S"])
        self.assertEqual(got["channels"]["R_mf"], {"summary": None, "reason": "not run here"})
        self.assertEqual(got["union"]["summary"]["found"], 2)

    def test_inconsistent_files_are_refused(self) -> None:
        doc = _doc({"X": X_ALERTS, "S": S_ALERTS, "R_mf": R_ALERTS})
        bad = copy.deepcopy(doc)
        bad["channels"]["S"]["by_outcome"] = bad["channels"]["S"]["by_outcome"][:2]
        with self.assertRaisesRegex(R.RescoreError, "different outcomes"):
            R.rescore(bad)
        bad = copy.deepcopy(doc)
        bad["channels"]["X"]["alert_timeline"][0]["keys"] = ["product:SD-9", "lot:L10001:crack"]
        with self.assertRaisesRegex(R.RescoreError, "not one alert's"):
            R.rescore(bad)
        bad = copy.deepcopy(doc)
        del bad["weeks"]["available_first"]
        bad["channels"]["X"]["alert_timeline"][0]["available_date"] = "2024-05-08"
        with self.assertRaisesRegex(R.RescoreError, "closing lags"):
            R.rescore(bad)


class LargerAuditTests(unittest.TestCase):
    """Audits built from the synthetic timelines of ``tools/market/reactive_world.py``, seeds 1 to 5: 40 outcomes, 87
    weeks, 26-week windows, and every channel with alerts on the outcomes' keys before and after their openings. In
    the demo audits S and R_mf have no alert on an outcome's key, and the small audit above does not notice the date
    the rotations start from; these audits notice both."""

    SEEDS = range(1, 6)

    @classmethod
    def setUpClass(cls) -> None:
        cls.settings = {"lookback": RW.LOOKBACK, "post": RW.POST, "available_first": RW.AVAILABLE_FIRST,
                        "evaluated_weeks": RW.EVALUATED_WEEKS}
        weeks = {"first": _iso_week(RW.AVAILABLE_FIRST), "evaluated_from": _iso_week(RW.AVAILABLE_FIRST),
                 "last": _iso_week(RW.AVAILABLE_FIRST + timedelta(weeks=RW.EVALUATED_WEEKS - 1))}
        cls.audits = {}
        for seed in cls.SEEDS:
            outcomes, x = RW.world("presignal", seed)        # pre-signal, reactions and background
            _, s = RW.world("lasting", seed)                 # reactions that outlast the post window
            _, r = RW.world("background", seed + 100)        # the same keys, at times unrelated to these openings
            alerts = {"X": x, "S": s, "R_mf": r}
            cls.audits[seed] = (outcomes, alerts, _audit_doc(outcomes, alerts, cls.settings, weeks))

    def test_the_audits_exercise_every_channel(self) -> None:
        for seed, (outcomes, alerts, doc) in self.audits.items():
            for name in ("X", "S", "R_mf"):
                with self.subTest(seed=seed, channel=name):
                    s = doc["channels"][name]["summary"]
                    self.assertEqual(s["outcomes"], RW.OUTCOMES)
                    self.assertGreater(s["expected_found"], 0)
                    self.assertGreater(s["expected_found_excluding_own_post"], 0)
                    self.assertTrue(any(r["post_alerts"] for r in doc["channels"][name]["by_outcome"]))
            self.assertGreater(doc["channels"]["X"]["summary"]["found"], 0)
            self.assertGreater(doc["channels"]["R_mf"]["summary"]["found"], 0)

    def test_every_channel_and_the_union_reproduce_exactly(self) -> None:
        from mycelic.collective.pilot import audit as A
        for seed, (outcomes, alerts, doc) in self.audits.items():
            with self.subTest(seed=seed):
                got = R.rescore(doc)
                for name in ("X", "S", "R_mf"):
                    self.assertTrue(got["channels"][name]["matches_audit"], name)
                union = A.score_channel(outcomes, alerts["X"] + alerts["S"] + alerts["R_mf"], **self.settings)
                self.assertEqual(got["union"]["summary"], union["summary"])
                self.assertEqual(got["union"]["of"], ["X", "S", "R_mf"])

    def test_the_start_date_from_the_timeline_reproduces_and_a_week_off_does_not(self) -> None:
        for seed, (_, _, doc) in self.audits.items():
            with self.subTest(seed=seed):
                want = R.rescore(doc)
                old = copy.deepcopy(doc)
                del old["weeks"]["available_first"]
                got = R.rescore(old)
                self.assertEqual((got["available_first"], got["available_first_from"]),
                                 (RW.AVAILABLE_FIRST.isoformat(), "alert_timeline"))
                self.assertEqual((got["channels"], got["union"]), (want["channels"], want["union"]))
                off = copy.deepcopy(doc)
                off["weeks"]["available_first"] = (RW.AVAILABLE_FIRST + timedelta(days=7)).isoformat()
                self.assertFalse(all(c["matches_audit"] for c in R.rescore(off)["channels"].values()))


if __name__ == "__main__":
    unittest.main()
