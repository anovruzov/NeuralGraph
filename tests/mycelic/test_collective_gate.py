"""G6: the commit gate (``pushdown/gate.py``): a table of cases with the exact status and reason list, determinism, and
the port from vr034p.

Pure tests over hand-built verdict bodies (no site, no model, no clock), plus two orchestrator-backed cases on a
small synthetic world in a temporary directory.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from mycelic.collective.edge.egress import verdict_id_of
from mycelic.collective.jsonio import canonical_bytes
from mycelic.collective.packs.loader import load_pack
from mycelic.collective.pushdown import gate
from mycelic.collective.pushdown.gate import STATUS_RANK, GateParams, VerdictRecord, evaluate, hq_record_body
from mycelic.collective.pushdown.questions import build_question, question_window

ROOT = Path(__file__).resolve().parents[2]
DQ = load_pack("device_quality")
CI = load_pack("claims_integrity")
Q_AS_OF = "2026-04-26"                     # closes 2026-W15 (device lag 14): the window is 2026-W10..2026-W15
QUESTION = build_question(DQ, entity_type="product", entity_id="SD-9", predicate="crack",
                          window=question_window(DQ, as_of=Q_AS_OF), as_of=Q_AS_OF)
QID = QUESTION["question_id"]
REF = "0123456789abcdef"


def body(site: str, verdict: str = "confirm", *, support: str | None = "3-9", roots: str | None = "3-9",
         reporters: str | None = "3-9", entity: str | None = "3-9", newest: str | None = "2026-W15",
         reason: str | None = None, truncated: bool = False, quality: str = "ok", secret_mode: str = "seeded-demo",
         window: dict[str, str] | None = None, question_id: str = QID, pack_hash: str | None = None,
         **extra: Any) -> dict[str, Any]:
    """A verdict body that passes the Boundary's spec (unless ``extra`` breaks it), with a recomputed verdict id."""
    confirm, refute = verdict == "confirm", verdict == "refute"
    out = {"schema_version": 1, "pack": DQ.id, "pack_hash": pack_hash or DQ.config_hash, "site": site,
           "question_id": question_id, "verdict": verdict, "reason": reason,
           "window": window or dict(QUESTION["window"]), "support_bucket": support if confirm else None,
           "roots_bucket": roots if confirm else None, "reporters_bucket": reporters if confirm else None,
           "entity_records_bucket": entity if refute else None, "newest_week": newest if confirm else None,
           "truncated": truncated, "quality": quality,
           "secret_mode": "none" if reason == "no_secret" else secret_mode}
    out.update(extra)
    out["verdict_id"] = verdict_id_of(out)
    out["evidence_ref"] = REF if confirm or refute else None
    return out


def rec(site: str, b: Any, *, seq: int = 1, received: str = Q_AS_OF, source: str = "site") -> VerdictRecord:
    return VerdictRecord(site=site, seq=seq, source=source, body=b, received_as_of=received)


def hq(site: str, reason: str, *, seq: int = 1) -> VerdictRecord:
    return rec(site, hq_record_body(QID, site, reason), seq=seq, source="hq")


C2 = {"a": "contributing", "b": "contributing"}
SUPPORTED_AB = "supported: 2 confirming sites; independent roots, lower bound 6; independent reporters, lower bound 6"
OTHER_WINDOW = {"start_week": "2026-W09", "end_week": "2026-W15"}

# (name, routes, records, as_of, status, reasons); question defaults to QUESTION
CASES: list[tuple[str, dict[str, str], list[VerdictRecord], str, str, list[str]]] = [
    ("two contributing confirms", C2, [rec("a", body("a")), rec("b", body("b"))], Q_AS_OF, "supported",
     [SUPPORTED_AB]),
    ("'<k' roots count as 1", {"a": "contributing", "b": "contributing", "c": "contributing"},
     [rec(s, body(s, roots="<k")) for s in "abc"], Q_AS_OF, "supported",
     ["supported: 3 confirming sites; independent roots, lower bound 3; independent reporters, lower bound 9"]),
    ("all unknown", {"a": "contributing", "b": "contributing", "c": "sibling", "d": "sibling"},
     [rec("a", body("a", "unknown")), rec("b", body("b", "unknown", reason="budget")),
      rec("c", body("c", "unknown", quality="degraded")), rec("d", body("d", "unknown", reason="no_secret"))],
     Q_AS_OF, "hypothesis",
     ["hypothesis: no evidence (no site confirms)", "a unknown", "b unknown (budget)", "c unknown (degraded)",
      "d unknown (no_secret)"]),
    ("truncated subset", C2, [rec("a", body("a", truncated=True)), rec("b", body("b"))], Q_AS_OF, "supported",
     [SUPPORTED_AB, "a judged a truncated subset (verify_max_records)"]),
    ("identical duplicate", C2, [rec("a", body("a")), rec("a", body("a"), seq=2), rec("b", body("b"))], Q_AS_OF,
     "supported", [SUPPORTED_AB]),
    ("conflicting duplicate", C2, [rec("a", body("a", "refute")), rec("a", body("a"), seq=2), rec("b", body("b"))],
     Q_AS_OF, "supported", [SUPPORTED_AB, "a answered again; the latest verdict is used"]),
    ("a sibling confirm counts", {"a": "contributing", "s": "sibling"}, [rec("a", body("a")), rec("s", body("s"))],
     Q_AS_OF, "supported", [SUPPORTED_AB, "sibling s confirms (counted as support)"]),
    ("a weak sibling confirm does not count", {"a": "contributing", "s": "sibling"},
     [rec("a", body("a")), rec("s", body("s", support="<k", roots="<k", reporters="<k"))], Q_AS_OF, "hypothesis",
     ["hypothesis: 1 confirming site(s) with at least 3 records; min_confirming_sites is 2",
      "s confirms with fewer than 3 records (not counted)"]),
    ("sibling refutes do not contest", {**C2, "c": "sibling", "d": "sibling"},
     [rec("a", body("a")), rec("b", body("b")), rec("d", body("d", "refute")), rec("c", body("c", "refute"))],
     Q_AS_OF, "supported",
     [SUPPORTED_AB, "not observed at c (sibling refutes; scoped negative evidence)",
      "not observed at d (sibling refutes; scoped negative evidence)"]),
    ("a contributing refute contests", {**C2, "c": "contributing"},
     [rec("a", body("a")), rec("c", body("c", "refute")), rec("b", body("b", "refute"))], Q_AS_OF, "contested",
     ["contested: contributing site b refutes", "contested: contributing site c refutes"]),
    ("fresh at exactly freshness_days", C2,
     [rec("a", body("a", newest="2026-W11")), rec("b", body("b", newest="2026-W10"))], Q_AS_OF, "supported",
     [SUPPORTED_AB]),
    ("stale one day over", C2, [rec("a", body("a", newest="2026-W11")), rec("b", body("b", newest="2026-W10"))],
     "2026-04-27", "stale", ["stale: the newest confirming week 2026-W11 ended more than 42 days before 2026-04-27"]),
    ("a verdict received after as_of", C2, [rec("a", body("a")), rec("b", body("b"), received="2026-04-27")],
     Q_AS_OF, "hypothesis", ["excluded: b verdict arrived after as_of",
                             "hypothesis: 1 confirming site(s) with at least 3 records; min_confirming_sites is 2"]),
    ("another question id", C2, [rec("a", body("a", question_id="f" * 64)), rec("b", body("b"))], Q_AS_OF,
     "hypothesis", ["excluded: a answered another question",
                    "hypothesis: 1 confirming site(s) with at least 3 records; min_confirming_sites is 2"]),
    ("a schema-invalid verdict", C2, [rec("a", {**body("a"), "support_bucket": 7}), rec("b", body("b"))], Q_AS_OF,
     "hypothesis", ["excluded: a verdict fails the schema at $.support_bucket (enum)",
                    "hypothesis: 1 confirming site(s) with at least 3 records; min_confirming_sites is 2"]),
    ("all three support reasons", C2, [rec("a", body("a", roots="<k", reporters="<k"))], Q_AS_OF, "hypothesis",
     ["hypothesis: 1 confirming site(s) with at least 3 records; min_confirming_sites is 2",
      "hypothesis: independent roots, lower bound 1; min_independent_roots is 3",
      "hypothesis: independent reporters, lower bound 1; min_independent_reporters is 3"]),
    ("an unrouted site", C2, [rec("a", body("a")), rec("b", body("b")), rec("x", body("x"))], Q_AS_OF, "supported",
     ["excluded: x was not routed this question", SUPPORTED_AB]),
    ("another pack hash", C2, [rec("a", body("a", pack_hash="0" * 64)), rec("b", body("b"))], Q_AS_OF,
     "hypothesis", ["excluded: a answered under another pack hash",
                    "hypothesis: 1 confirming site(s) with at least 3 records; min_confirming_sites is 2"]),
    ("another window", C2, [rec("a", body("a", window=OTHER_WINDOW)), rec("b", body("b"))], Q_AS_OF, "hypothesis",
     ["excluded: a answered for another window",
      "hypothesis: 1 confirming site(s) with at least 3 records; min_confirming_sites is 2"]),
    ("weak confirms only", C2, [rec(s, body(s, support="<k", roots="<k", reporters="<k")) for s in "ab"], Q_AS_OF,
     "hypothesis",
     ["hypothesis: 0 confirming site(s) with at least 3 records; min_confirming_sites is 2",
      "hypothesis: independent roots, lower bound 0; min_independent_roots is 3",
      "hypothesis: independent reporters, lower bound 0; min_independent_reporters is 3",
      "a confirms with fewer than 3 records (not counted)", "b confirms with fewer than 3 records (not counted)"]),
    ("HQ records", {**C2, "c": "sibling", "d": "sibling"},
     [rec("a", body("a")), rec("b", body("b")), hq("c", "timeout"), hq("d", "error")], Q_AS_OF, "supported",
     [SUPPORTED_AB, "c unknown (timeout)", "d unknown (error)"]),
    ("a late verdict supersedes a timeout", C2, [rec("a", body("a")), hq("b", "timeout"), rec("b", body("b"), seq=2)],
     Q_AS_OF, "supported", [SUPPORTED_AB, "b answered again; the latest verdict is used"]),
    ("supported with notes in group order", {"a": "contributing", "b": "contributing", "s": "sibling",
                                             "t": "sibling", "u": "sibling"},
     [rec("a", body("a", "refute")), rec("a", body("a", truncated=True), seq=2), rec("b", body("b")),
      rec("s", body("s")), rec("t", body("t", "refute")), rec("u", body("u", support="<k", roots="<k",
                                                                            reporters="<k"))],
     Q_AS_OF, "supported",
     ["supported: 3 confirming sites; independent roots, lower bound 9; independent reporters, lower bound 9",
      "a answered again; the latest verdict is used", "not observed at t (sibling refutes; scoped negative evidence)",
      "sibling s confirms (counted as support)", "u confirms with fewer than 3 records (not counted)",
      "a judged a truncated subset (verify_max_records)"]),
]


def other_question(**changes: Any) -> dict[str, Any]:
    return {**QUESTION, **changes}


QUESTION_CASES = [
    ("a question of another pack hash", other_question(pack_hash="0" * 64), Q_AS_OF,
     ["rejected: the question was made under another pack hash"], {"authorization": False}),
    ("a question that fails the schema", {k: v for k, v in QUESTION.items() if k != "as_of"}, Q_AS_OF,
     ["rejected: the question fails the schema at $.as_of (required)"], {"authorization": True, "schema": False}),
    ("look-ahead: as_of before the question", QUESTION, "2026-04-25",
     ["rejected: the question window ends after the last week closed at as_of (look-ahead)"],
     {"authorization": True, "schema": True, "temporal": False}),
]


class GateTableTests(unittest.TestCase):
    def test_there_are_at_least_twenty_cases(self) -> None:
        self.assertGreaterEqual(len(CASES) + len(QUESTION_CASES), 20)
        self.assertGreaterEqual(len(CASES), 20)

    def test_every_case_gives_its_status_and_exact_reasons(self) -> None:
        for name, routes, records, as_of, status, reasons in CASES:
            with self.subTest(case=name):
                result = evaluate(DQ, QUESTION, routes, records, as_of=as_of)
                self.assertEqual((result.status, list(result.reasons)), (status, reasons))
                self.assertEqual(result.checks["support"], status == "supported")
                self.assertEqual(result.to_dict()["reasons"], reasons)

    def test_question_level_failures_reject(self) -> None:
        for name, question, as_of, reasons, checks in QUESTION_CASES:
            with self.subTest(case=name):
                result = evaluate(DQ, question, C2, [rec("a", body("a")), rec("b", body("b"))], as_of=as_of)
                self.assertEqual((result.status, list(result.reasons)), ("rejected", reasons))
                for check, value in checks.items():
                    self.assertEqual(result.checks[check], value)
                self.assertEqual((result.excluded, result.used), ((), ()))

    def test_a_window_not_closed_at_as_of_is_look_ahead(self) -> None:
        # asked at 2026-05-10 (window 2026-W12..2026-W17), gated at 2026-05-01, which closes only 2026-W15
        later = build_question(DQ, entity_type="product", entity_id="SD-9", predicate="crack",
                               window=question_window(DQ, as_of="2026-05-10"), as_of="2026-05-10")
        records = [rec(s, body(s, question_id=later["question_id"], window=later["window"], newest="2026-W17"))
                   for s in "ab"]
        result = evaluate(DQ, later, C2, records, as_of="2026-05-01")
        self.assertEqual((result.status, list(result.reasons)),
                         ("rejected", ["rejected: the question window ends after the last week closed at as_of "
                                       "(look-ahead)"]))
        self.assertEqual(evaluate(DQ, later, C2, records, as_of="2026-05-10").status, "supported")
        # a question claiming an as_of that does not close its window fails the question schema first, so the
        # window-end branch of the look-ahead check is defence in depth behind it
        early = {**later, "as_of": "2026-05-09"}
        self.assertEqual(list(evaluate(DQ, early, C2, records, as_of="2026-05-10").reasons),
                         ["rejected: the question fails the schema at $.window.end_week (range)"])

    def test_support_and_freshness_blocks(self) -> None:
        result = evaluate(DQ, QUESTION, {**C2, "s": "sibling"},
                          [rec("a", body("a", roots="<k")), rec("b", body("b", support="10-49", newest="2026-W12")),
                           rec("s", body("s", "refute"))], as_of=Q_AS_OF)
        d = result.to_dict()
        self.assertEqual(d["support"], {"confirming_sites": ["a", "b"], "weak_confirming_sites": [],
                                        "refuting_contributing": [], "refuting_siblings": ["s"], "unknown_sites": [],
                                        "roots_lb": 4, "reporters_lb": 6, "support_lb": 13,
                                        "newest_week": "2026-W15", "truncated_sites": []})
        self.assertEqual(d["freshness"], {"newest_week": "2026-W15", "age_days": 14, "freshness_days": 42,
                                          "stale": False})
        self.assertEqual([u["counted"] for u in d["used"]], [True, True, False])
        self.assertEqual({u["role"] for u in d["used"]}, {"contributing", "sibling"})
        self.assertEqual(d["checks"], {"authorization": True, "schema": True, "provenance": True, "temporal": True,
                                       "support": True})

    def test_an_hq_record_must_have_the_hq_shape(self) -> None:
        good = hq_record_body(QID, "a", "timeout")
        for bad in ({**good, "site": "b"}, {**good, "reason": "budget"}, {**good, "verdict": "confirm"},
                    {**good, "extra": 1}, [good]):
            with self.subTest(bad=repr(bad)[:40]), self.assertRaises(ValueError):
                VerdictRecord(site="a", seq=1, source="hq", body=bad, received_as_of=Q_AS_OF)
        for kw in ({"seq": 0}, {"seq": True}, {"source": "elsewhere"}, {"received_as_of": "2026-02-30"}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                VerdictRecord(**{"site": "a", "seq": 1, "source": "site", "body": {}, "received_as_of": Q_AS_OF,
                                 **kw})
        with self.assertRaises(ValueError):
            hq_record_body(QID, "a", "budget")
        with self.assertRaises(ValueError):
            evaluate(DQ, QUESTION, C2, [], as_of="2026-4-26")

    def test_orchestrator_duplicates_make_no_version_and_conflicts_make_one(self) -> None:
        from tests.mycelic.test_collective_pushdown import MiniWorld, crack_records
        with tempfile.TemporaryDirectory() as tmp:
            world = MiniWorld(Path(tmp), {"a": crack_records("a", 4), "b": crack_records("b", 4)})
            self.addCleanup(world.close)
            replies: dict[str, Any] = {}
            orch = world.orchestrator(handlers={sid: (lambda q, sid=sid: replies.setdefault(sid, world.answer(sid, q)))
                                                for sid in ("a", "b")})
            first = orch.verify_candidate(world.candidate(), as_of=world.as_of)
            self.assertEqual((first.status, first.version), ("supported", 1))
            self.assertEqual(orch.receive_verdict(replies["a"], site="a", as_of=world.as_of), "duplicate")
            same = orch.regate(first.question_id, as_of=world.as_of)
            self.assertEqual((same.version, len(orch.versions(first.conclusion_id))), (1, 1))
            conflicting = world.forged_refute("a", first.question_id)
            self.assertEqual(orch.receive_verdict(conflicting, site="a", as_of=world.as_of), "accepted")
            second = orch.regate(first.question_id, as_of=world.as_of)
            self.assertEqual((second.status, second.version), ("contested", 2))
            self.assertIn("a answered again; the latest verdict is used", second.reasons)
            self.assertEqual([v.version for v in orch.versions(first.conclusion_id)], [1, 2])
            self.assertEqual(orch.conclusion(first.conclusion_id, 1).status, "supported")


def _case_bytes(order_seed: int | None) -> bytes:
    name, routes, records, as_of, _, _ = CASES[-1]
    shuffled = list(records)
    if order_seed is not None:
        random.Random(order_seed).shuffle(shuffled)
    return canonical_bytes(evaluate(DQ, QUESTION, routes, shuffled, as_of=as_of).to_dict())


class GateDeterminismTests(unittest.TestCase):
    def test_identical_bytes_on_repeat_and_under_shuffled_records(self) -> None:
        base = _case_bytes(None)
        self.assertEqual(_case_bytes(None), base)
        for seed in range(10):
            with self.subTest(seed=seed):
                self.assertEqual(_case_bytes(seed), base)
        for name, routes, records, as_of, _, _ in CASES:
            with self.subTest(case=name):
                a = canonical_bytes(evaluate(DQ, QUESTION, routes, records, as_of=as_of).to_dict())
                b = canonical_bytes(evaluate(DQ, QUESTION, dict(reversed(list(routes.items()))),
                                             list(reversed(records)), as_of=as_of).to_dict())
                self.assertEqual(a, b)

    def test_two_hash_seeds_give_identical_bytes(self) -> None:
        code = ("import sys; from tests.mycelic.test_collective_gate import _case_bytes; "
                "sys.stdout.write(_case_bytes(3).hex())")
        outs = []
        for hash_seed in ("0", "1"):
            r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                               env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONHASHSEED": hash_seed})
            self.assertEqual(r.returncode, 0, r.stderr)
            outs.append(r.stdout)
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(bytes.fromhex(outs[0]), _case_bytes(None))

    def test_evaluate_reads_no_clock(self) -> None:
        def boom(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("a clock was read")

        with mock.patch("time.time", boom), mock.patch("time.monotonic", boom), \
                mock.patch("time.perf_counter", boom), mock.patch("time.time_ns", boom):
            result = evaluate(DQ, QUESTION, C2, [rec("a", body("a")), rec("b", body("b"))], as_of=Q_AS_OF)
        self.assertEqual(result.status, "supported")


class GatePortTests(unittest.TestCase):
    def test_the_header_cites_the_port_and_each_deviation(self) -> None:
        doc = gate.__doc__
        self.assertIn("Ported (adapted, not merged) from origin/claude/mycelic-implementation-vr034p@388aa30, "
                      "mycelic/knowledge/gate.py and support.py", " ".join(doc.split()))
        flat = " ".join(doc.split())
        for needle in ("no Authorizer or OrgService", "``as_of`` is injected", "bucketed verdicts",
                       "counts as its lower bound 1", "weak confirm", "excludes that verdict rather than rejecting",
                       "contested only from a contributing site's refute"):
            self.assertIn(needle, flat)
        for check in ("authorization", "schema", "provenance", "temporal validity", "support"):
            self.assertIn(f"**{check}**", flat)

    def test_status_rank_order(self) -> None:
        self.assertEqual(sorted(STATUS_RANK, key=STATUS_RANK.get, reverse=True),
                         ["supported", "hypothesis", "stale", "contested", "rejected"])
        self.assertEqual(dict(STATUS_RANK), {"supported": 4, "hypothesis": 3, "stale": 2, "contested": 1,
                                             "rejected": 0})

    def test_gate_params_from_both_packs(self) -> None:
        self.assertEqual(GateParams.from_pack(DQ).to_dict(), {
            "k": 3, "labels": ["<k", "3-9", "10-49", "50+"], "min_confirming_sites": 2, "min_independent_roots": 3,
            "min_independent_reporters": 3, "freshness_days": 42, "close_lag_days": 14})
        self.assertEqual(GateParams.from_pack(CI).to_dict(), {
            "k": 5, "labels": ["<k", "5-9", "10-49", "50+"], "min_confirming_sites": 2, "min_independent_roots": 3,
            "min_independent_reporters": 3, "freshness_days": 56, "close_lag_days": 21})
        self.assertEqual(json.loads(json.dumps(GateParams.from_pack(DQ).to_dict()))["labels"][0], "<k")


if __name__ == "__main__":
    unittest.main()
