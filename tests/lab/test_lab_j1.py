"""Judge test J001 (``lab.j1``, the lab's ``j1`` experiment): the question rule, the shipped payload and verdict rule,
the lexical judge, the runner through the repo's fake model server, its failures, stops and pin refusals, the scoring
and headline, the aggregate, and the request and plan. Nothing here measures a model: every server is the collective's
fake, every complaint archive is built in the test, and no test downloads NHTSA's file.

One full dry run of J001's own request (``lab/models.json``, three gguf models, 150 records in 6 parts: 18 units in 9
shards), through ``lab.dryrun`` with the shards on the fake provider, is shared by the classes that read it;
:class:`~tests.lab.helpers.DryTree` copies of it are changed and re-sealed for the aggregate's cases.
"""
from __future__ import annotations

import contextlib
import functools
import hashlib
import io
import json
import os
import random
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from typing import Any, Callable
from unittest import mock

from lab import aggregate as lab_aggregate
from lab import dryrun as lab_dryrun
from lab import goldlabels
from lab import j1
from lab import prereg as lab_prereg
from lab import summary as lab_summary
from lab import units
from lab.goldlabels import NHTSA_MAKES, NHTSA_PACK, build_labels
from lab.manifest import load_manifest
from lab.notes import (J1_INCOMPLETE, J1_NOT_MEASURED, J1_PRIOR_NOTE, J1_STOPPED, J1_WITHHELD, NO_MEASUREMENT_LINE,
                       RESULT_CONTRADICTS_EXIT)
from lab.plan import build_plan
from lab.request import (J1_LABELS_PROBLEM, NHTSA_HOSTED, RequestError, load_request, validate)
from lab.responder import Responder
from mycelic.collective import stats
from mycelic.collective.edge.records import WindowRecord
from mycelic.collective.edge.verify import decide, judge_payload, lexical_judge
from mycelic.collective.inference.fakeserver import FakeOpenAIServer, request_payload
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.inference.tasks import REPAIR_MARKER
from mycelic.collective.jsonio import canonical_bytes, canonical_dumps
from mycelic.collective.packs import loader
from mycelic.collective.packs.canonical import Canonicaliser
from tests.lab.helpers import (LAB_MANIFEST, MANIFEST_TEST, ROOT, DryTree, call_main, check_sources,
                               kill_mentioning, plumbing_min, run_plan, write_json)

PACK = loader.load_pack(ROOT / NHTSA_PACK)
MANIFEST = load_manifest(MANIFEST_TEST)
LABELS = {"source": "nhtsa", "pack": NHTSA_PACK, "n": 40, "seed": 1}
J1_BLOCK = {"minutes": 5, "labels": LABELS, "parts": 2, "seed": 1, "bootstrap_b": 1000}
J001_REQUEST = {
    "schema_version": 1,
    "purpose": "Judge test J001 (docs/collective/replay/vehicles/CHOICE-J001.md), as the run will request it.",
    "provider": "llama-server", "models": ["a-0p5b", "a-1p5b", "a-4b"], "job_minutes": 330, "max_parallel": 9,
    "retention_days": 30,
    "experiments": {"j1": {"minutes": 150, "labels": {**LABELS, "n": 150}, "parts": 6, "seed": 1,
                           "bootstrap_b": 10000, "bootstrap_seed": 1}}}
COMPONENTS = (
    ("AIR BAGS", "The air bags did not deploy when the car hit a pole."),
    ("ENGINE", "The engine stalled on the highway at speed."),
    ("ENGINE AND ENGINE COOLING", "The engine overheated and coolant sprayed out."),
    ("SERVICE BRAKES, HYDRAULIC", "The brakes failed at a light and the pedal went to the floor."),
    ("SERVICE BRAKES", "The service brakes made a grinding noise."),
    ("STEERING", "The steering wheel locked while turning."),
    ("TIRES", "A tire blew out on the interstate."),
    ("ELECTRICAL SYSTEM", "The dashboard went dark while driving."),
    ("FUEL SYSTEM, GASOLINE", "Gasoline leaked under the car."),
    ("VISIBILITY/WIPER", "The wiper motor stopped in the rain."),
    ("SEAT BELTS", "The belt would not latch."),
    ("UNKNOWN OR OTHER", "Something odd happened."),
)


def _row(export: Any, odino: str, make: str, comp: str, text: str, year: str) -> str:
    r = [""] * 51
    r[export.C_ODINO], r[export.C_MAKE], r[export.C_MODEL], r[export.C_YEAR] = odino, make, "MODEL X", year
    r[export.C_COMP], r[export.C_STATE], r[export.C_LDATE] = comp, "TX", "20230510"
    r[export.C_DESCR], r[export.C_PROD] = text, "V"
    r[50] = "Operator Name Never Exported"
    return "\t".join(r)


@functools.lru_cache(maxsize=None)
def archive(per_make: int = 30) -> bytes:
    """A complaint archive in NHTSA's flat layout: per make, complaints with one or two components (twin pairs and
    the generic code among them); about 180 eligible records."""
    export = goldlabels.nhtsa_export()
    lines, n = [], 0
    for make in NHTSA_MAKES:
        for i in range(per_make):
            n += 1
            odino, year = str(100000 + n), str(2019 + i % 4)
            comp, text = COMPONENTS[i % len(COMPONENTS)]
            if comp == "UNKNOWN OR OTHER":           # keep it eligible with a specific second code
                lines.append(_row(export, odino, make, comp, text, year))
                comp, text = COMPONENTS[(i + 5) % (len(COMPONENTS) - 1)]
            lines.append(_row(export, odino, make, comp, text, year))
            if i % 4 == 0:
                other, more = COMPONENTS[(i * 3 + 1) % (len(COMPONENTS) - 1)]
                lines.append(_row(export, odino, make, other, more, year))
            if i % 6 == 2:                           # the old and the new name of one component
                lines.append(_row(export, odino, make, "ENGINE", "", year))
                lines.append(_row(export, odino, make, "ENGINE AND ENGINE COOLING", "", year))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("CMPL.txt", "\n".join(lines) + "\n")
    return buf.getvalue()


@functools.lru_cache(maxsize=None)
def labels(n: int = 40) -> bytes:
    data, _ = build_labels("nhtsa", NHTSA_PACK, n, 1, fetch=archive)
    return data


def questions(n: int = 40, parts: int = 2, seed: int = 1) -> list[dict[str, Any]]:
    return j1.read_questions(j1.build_questions(PACK, labels(n), seed=seed, parts=parts)[0])


def records(n: int = 40) -> dict[str, dict[str, Any]]:
    return {r["record_ref"]: r for r, _ in j1.parse_labels(labels(n))}


SPECIFIC = sorted(p for p in PACK.predicates if p != "unknown_or_other")


@functools.lru_cache(maxsize=None)
def skewed_labels(n: int, seed: int = 7) -> bytes:
    """Constructed label lines (not NHTSA's): each record filed under one predicate, or two for about three in ten,
    drawn with weights that halve every third predicate, so a few predicates hold most filings."""
    rng = random.Random(seed)
    weights = [2 ** -(i / 3) for i in range(len(SPECIFIC))]
    lines = []
    for i in range(n):
        filed = {rng.choices(SPECIFIC, weights)[0]}
        if rng.random() < 0.3:
            filed.add(rng.choices(SPECIFIC, weights)[0])
        record = {"record_ref": f"R{i:05d}", "language": "en", "codes": [], "entities": {"vehicle": ["V-1"]},
                  "narrative": "constructed"}
        gold = [{"entity_type": "vehicle", "entity_id": "V-1", "predicate": p} for p in sorted(filed)]
        lines.append(canonical_dumps({"record": record, "gold": gold}) + "\n")
    return "".join(lines).encode("utf-8")


def expected_negative(qs: list[dict[str, Any]], q: dict[str, Any], seed: int = 1) -> str:
    """Rule 2's negative for ``q``'s record, computed here from the rule's words, not from ``lab.j1``: the candidates
    are the pack's predicates less the filed ones, their twins and unknown_or_other; each holds as many positions as
    other records' positives ask it; ``randrange(total)`` of the record's seeded generator picks one."""
    ref, filed = q["record_ref"], q["filed"]
    twins = {j1.twin_of(p) for p in filed} - {None}
    left = sorted(set(PACK.predicates) - set(filed) - {"unknown_or_other"} - twins)
    asked = [o["predicate"] for o in qs if o["kind"] == "positive" and o["record_ref"] != ref]
    positions = [p for p in left for _ in range(asked.count(p))]
    return positions[random.Random(f"j1:{seed}:{ref}:negative").randrange(len(positions))]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------------------------------------------- questions

class QuestionTests(unittest.TestCase):
    def test_the_same_bytes_twice(self) -> None:
        a = j1.build_questions(PACK, labels(), seed=1, parts=2)
        b = j1.build_questions(PACK, labels(), seed=1, parts=2)
        self.assertEqual(a, b)
        self.assertEqual(a[1]["sha256"], hashlib.sha256(a[0]).hexdigest())
        self.assertNotEqual(j1.build_questions(PACK, labels(), seed=2, parts=2)[0], a[0])

    def test_one_positive_and_one_negative_by_the_rule(self) -> None:
        recs = records()
        qs = questions()
        self.assertEqual(len(qs), 2 * len(recs))
        gold = {r["record_ref"]: g for r, g in j1.parse_labels(labels())}
        for i in range(0, len(qs), 2):
            pos, neg = qs[i], qs[i + 1]
            ref = pos["record_ref"]
            filed = sorted({g["predicate"] for g in gold[ref]})
            twins = {j1.twin_of(p) for p in filed} - {None}
            left = sorted(set(PACK.predicates) - set(filed) - {"unknown_or_other"} - twins)
            with self.subTest(record=ref):
                self.assertEqual((pos["kind"], neg["kind"], neg["record_ref"]), ("positive", "negative", ref))
                self.assertEqual((pos["question_id"], neg["question_id"]), (f"{ref}:positive", f"{ref}:negative"))
                self.assertEqual(pos["filed"], filed)
                self.assertEqual(pos["predicate"], random.Random(f"j1:1:{ref}:positive").choice(filed))
                self.assertEqual(neg["predicate"], expected_negative(qs, pos))
                self.assertEqual(j1.candidates(PACK, filed), left)
                self.assertIn(pos["predicate"], filed)
                self.assertNotIn(neg["predicate"], [*filed, "unknown_or_other", *twins])
                # a negative is a predicate some other record's positive asks
                self.assertIn(neg["predicate"], {o["predicate"] for o in qs[0::2] if o["record_ref"] != ref})
                self.assertEqual(pos["entity_id"], recs[ref]["entities"]["vehicle"][0])
                self.assertEqual((pos["entity_type"], neg["entity_id"]), ("vehicle", pos["entity_id"]))

    def test_the_weighted_draw(self) -> None:
        """Each choice holds as many positions as its weight, in sorted order; a choice of weight 0 is never drawn; the
        same key draws the same choice; no weight at all is refused."""
        weights = {"a": 1, "b": 0, "c": 3}
        for i in range(200):
            key = f"k{i}"
            pick = random.Random(key).randrange(4)
            with self.subTest(key=key):
                self.assertEqual(j1.draw_negative(["a", "b", "c"], weights, key), ["a", "c", "c", "c"][pick])
        drawn = {j1.draw_negative(["a", "b", "c"], weights, f"k{i}") for i in range(200)}
        self.assertEqual(drawn, {"a", "c"})
        self.assertEqual(j1.draw_negative(["b", "z"], {"z": 2}, "k"), "z")
        with self.assertRaises(j1.J1Error):
            j1.draw_negative(["a", "b"], {"c": 5}, "k")
        with self.assertRaises(j1.J1Error):
            j1.draw_negative(["a"], {"a": -1}, "k")

    def test_a_record_no_other_positive_can_answer_is_refused(self) -> None:
        """Two records filed under the same one predicate: neither has a negative any other record's positive asks."""
        lines = [line for line in skewed_labels(40).split(b"\n") if line][:2]
        same = [json.loads(line) for line in lines]
        for doc in same:
            doc["gold"] = [dict(doc["gold"][0], predicate="engine")]
        with self.assertRaises(j1.J1Error):
            j1.build_questions(PACK, "".join(canonical_dumps(d) + "\n" for d in same).encode("utf-8"), seed=1,
                               parts=1)

    def test_the_predicate_alone_tells_almost_nothing(self) -> None:
        """On constructed, skewed draws (twenty seeds, not one), each common predicate is asked about as often as a
        negative as as a positive, and the most any predicate-only judge could score (the bound, fitted to the
        answers) stays low. The same records with the first rule's uniform negatives give the bound far more on every
        seed: that is the leak the amended rule closes."""
        for seed in range(7, 27):
            data = skewed_labels(600, seed)
            qs = j1.read_questions(j1.build_questions(PACK, data, seed=1, parts=1)[0])
            uniform = [dict(q, predicate=random.Random(f"j1:1:{q['record_ref']}:negative").choice(
                       j1.candidates(PACK, q["filed"]))) if q["kind"] == "negative" else q for q in qs]
            amended, first = j1.prior_bound(qs)["value"], j1.prior_bound(uniform)["value"]
            with self.subTest(seed=seed):
                self.assertLess(amended, 0.6)
                self.assertGreater(first, 0.7)
                self.assertGreater(first - amended, 0.15)
        data = skewed_labels(600)
        qs = j1.read_questions(j1.build_questions(PACK, data, seed=1, parts=1)[0])
        self.assertEqual(j1.read_questions(j1.build_questions(PACK, data, seed=1, parts=1)[0]), qs)
        for pos, neg in zip(qs[0::2], qs[1::2]):
            self.assertEqual(neg["predicate"], expected_negative(qs, pos))
        common = 0
        for p in SPECIFIC:
            pos = sum(1 for q in qs if q["predicate"] == p and q["kind"] == "positive")
            neg = sum(1 for q in qs if q["predicate"] == p and q["kind"] == "negative")
            if pos + neg >= 40:
                common += 1
                with self.subTest(predicate=p):
                    self.assertLess(abs(pos / (pos + neg) - 0.5), 0.15, (pos, neg))
        self.assertGreaterEqual(common, 5)

    def test_a_negative_is_never_a_filed_component_s_other_name(self) -> None:
        """Records filed under one name of a pair, across many seeds: the other name is never asked as a negative."""
        recs = j1.parse_labels(labels(150))
        paired = [r for r, g in recs if any(j1.twin_of(x["predicate"]) for x in g)]
        both = [r for r, g in recs if {"engine", "engine_and_engine_cooling"} <= {x["predicate"] for x in g}]
        self.assertTrue(paired)
        self.assertTrue(both)
        for seed in range(1, 40):
            for q in j1.read_questions(j1.build_questions(PACK, labels(150), seed=seed, parts=6)[0]):
                if q["kind"] == "negative":
                    self.assertNotIn(q["predicate"], {j1.twin_of(p) for p in q["filed"]})
                    self.assertNotEqual(q["predicate"], "unknown_or_other")

    def test_dropping_a_record_changes_only_what_it_weighed(self) -> None:
        """The other records' positives and entities stay; their negatives are drawn again with the counts of the
        records left (the amended rule 2 weighs every other record's positive), and still follow the rule."""
        lines = labels().split(b"\n")
        shorter = b"\n".join(lines[1:])
        dropped = j1.parse_labels(lines[0])[0][0]["record_ref"]
        after = j1.read_questions(j1.build_questions(PACK, shorter, seed=1, parts=2)[0])

        def keep(qs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
            return [{k: v for k, v in q.items() if k != "part"} for q in qs
                    if q["record_ref"] != dropped and q["kind"] == kind]

        self.assertEqual(keep(questions(), "positive"), keep(after, "positive"))
        self.assertNotIn(dropped, {q["record_ref"] for q in after})
        for pos, neg in zip(after[0::2], after[1::2]):
            self.assertEqual(neg["predicate"], expected_negative(after, pos))

    def test_parts(self) -> None:
        _, record = j1.build_questions(PACK, labels(150), seed=1, parts=6)
        self.assertEqual((record["records"], record["questions"], record["part_records"]), (150, 300, [25] * 6))
        qs = j1.read_questions(j1.build_questions(PACK, labels(150), seed=1, parts=6)[0])
        refs = [q["record_ref"] for q in qs if q["kind"] == "positive"]
        self.assertEqual(refs, sorted(refs))
        self.assertEqual([q["part"] for q in qs if q["kind"] == "positive"], [i * 6 // 150 + 1 for i in range(150)])
        with self.assertRaises(j1.J1Error):
            j1.build_questions(PACK, labels(), seed=1, parts=31)

    def test_a_record_with_codes_is_refused(self) -> None:
        record = dict(next(iter(records().values())), codes=["NHT-001"])
        with self.assertRaises(j1.J1Error):
            j1.window_record(record)


# --------------------------------------------------------------------------------------------------- payload, rule

class PayloadTests(unittest.TestCase):
    def test_the_shipped_payload_with_the_codes_hidden(self) -> None:
        recs = records()
        for q in questions()[:6]:
            record = recs[q["record_ref"]]
            window = WindowRecord(record_ref=record["record_ref"], iso_week="", root_ref=record["record_ref"],
                                  reporter_id=None, language=record["language"], codes=[],
                                  structured={k: list(v) for k, v in record["entities"].items()},
                                  narrative=record["narrative"])
            expected = judge_payload(PACK, {"params": {"entity_type": "vehicle", "entity_id": q["entity_id"],
                                                       "predicate": q["predicate"]}}, window)
            got = j1.payload(PACK, q, record)
            with self.subTest(question=q["question_id"]):
                self.assertEqual(got, expected)
                self.assertEqual(got["record"]["codes"], [])
                self.assertEqual(got["record"]["entities"], {"vehicle": [q["entity_id"]]})
                self.assertEqual(sorted(got["record"]), ["codes", "entities", "language", "text"])
                self.assertEqual(got["question"]["predicate"], q["predicate"])
                self.assertNotIn(record["record_ref"], canonical_dumps(got))
        self.assertNotIn("Operator", labels().decode("utf-8"))

    def test_the_verdict_is_the_verifier_s_rule_over_one_record(self) -> None:
        w = j1.window_record(next(iter(records().values())))
        cases = {("yes", "yes"): "confirm", ("yes", "no"): "refute", ("yes", "unclear"): "unknown",
                 ("no", "yes"): "unknown", ("no", "no"): "unknown", ("unclear", "no"): "unknown",
                 ("unclear", "unclear"): "unknown"}
        for (m, d), want in cases.items():
            reply = {"mentions_entity": m, "describes_predicate": d}
            self.assertEqual(j1.verdict(w, reply), want, (m, d))
            self.assertEqual(j1.verdict(w, reply), decide([w], [(w, reply)], 0).verdict)
        self.assertEqual(j1.verdict(w, None), "unknown")


class LexicalTests(unittest.TestCase):
    def test_the_lexical_judge_on_every_payload(self) -> None:
        recs, qs = records(), questions()
        lines = j1.lexical_verdicts(PACK, labels(), qs)
        handler = lexical_judge(PACK, Canonicaliser(PACK))
        self.assertEqual(len(lines), len(qs))
        for q, line in zip(qs, lines):
            record = recs[q["record_ref"]]
            reply = handler(j1.payload(PACK, q, record))
            w = j1.window_record(record)
            with self.subTest(question=q["question_id"]):
                self.assertEqual(sorted(line), sorted(j1.LINE_KEYS))
                self.assertEqual((line["mentions_entity"], line["describes_predicate"]),
                                 (reply["mentions_entity"], reply["describes_predicate"]))
                self.assertEqual(line["verdict"], decide([w], [(w, reply)], 0).verdict)
                self.assertEqual(line["mentions_entity"], "yes")        # the structured vehicle resolves
                self.assertNotEqual(line["verdict"], "unknown")
                self.assertEqual((line["scored"], line["error_kind"]), (True, None))
        verdicts = {(line["kind"], line["verdict"]) for line in lines}
        self.assertIn(("positive", "confirm"), verdicts)
        self.assertIn(("negative", "refute"), verdicts)


def _q(ref: str, kind: str, predicate: str) -> dict[str, Any]:
    return {"question_id": f"{ref}:{kind}", "record_ref": ref, "part": 1, "kind": kind, "entity_type": "vehicle",
            "entity_id": "V-1", "predicate": predicate, "filed": []}


def whole_file_counts() -> dict[str, int]:
    """Filed rows per specific pack predicate in the whole complaint file, from ``nhtsa-probe.json``'s component counts
    through the pack's mapping and codes (rule 5's words, computed here, not by ``lab.j1``)."""
    probe = json.loads((ROOT / j1.PRIOR_SOURCE).read_text(encoding="utf-8"))
    pack_dir = ROOT / NHTSA_PACK
    value_map = json.loads((pack_dir / "mapping.json").read_text(encoding="utf-8"))["codes"][0]["value_map"]
    codes = json.loads((pack_dir / "codes.json").read_text(encoding="utf-8"))
    out: dict[str, int] = {}
    for component, rows in probe["complaints"]["top_components"]:
        code = value_map.get(component)
        if code and codes[code]["specific"]:
            out[codes[code]["predicate"]] = out.get(codes[code]["predicate"], 0) + rows
    return out


def leave_one_out(qs: list[dict[str, Any]]) -> list[str]:
    """The first amended control, from its words (kept here only to show why it was replaced): confirm when the
    predicate was asked more often as a positive than as a negative among the other records' questions."""
    out = []
    for q in qs:
        others = [o for o in qs if o["record_ref"] != q["record_ref"] and o["predicate"] == q["predicate"]]
        pos = sum(1 for o in others if o["kind"] == "positive")
        neg = sum(1 for o in others if o["kind"] == "negative")
        out.append("confirm" if pos > neg else "refute")
    return out


def balanced(names: list[str], records: int = 8) -> list[dict[str, Any]]:
    """An exactly balanced draw: record i asks names[i % k] as its positive and names[(i + 1) % k] as its negative,
    so with records a multiple of k each predicate is asked as often as a positive as as a negative."""
    k = len(names)
    return [q for i in range(records) for q in (_q(f"B{i}", "positive", names[i % k]),
                                                _q(f"B{i}", "negative", names[(i + 1) % k]))]


class PriorTests(unittest.TestCase):
    """The record-blind control and the predicate-only bound of rule 5 (amended again before any run)."""

    def test_the_four_are_the_whole_file_s_most_filed(self) -> None:
        counts = whole_file_counts()
        order = sorted(counts, key=lambda p: (-counts[p], p))
        self.assertEqual(j1.PRIOR_TOP, tuple(order[:4]))
        self.assertEqual(j1.PRIOR_TOP, ("engine", "electrical_system", "air_bags", "power_train"))
        self.assertTrue(set(j1.PRIOR_TOP) <= set(PACK.predicates) - set(j1.EXCLUDED))
        self.assertGreater(counts[order[3]], counts[order[4]])      # no tie at the cut

    def test_by_hand(self) -> None:
        """A: engine+, tires-. B: seats+, air_bags-. C: tires+, power_train-. D: electrical_system+, engine-.
        Each question is confirmed exactly when its predicate is one of the four, whatever the other questions ask."""
        qs = [_q("A", "positive", "engine"), _q("A", "negative", "tires"), _q("B", "positive", "seats"),
              _q("B", "negative", "air_bags"), _q("C", "positive", "tires"), _q("C", "negative", "power_train"),
              _q("D", "positive", "electrical_system"), _q("D", "negative", "engine")]
        lines = j1.prior_verdicts(qs)
        self.assertEqual([line["verdict"] for line in lines],
                         ["confirm", "refute", "refute", "confirm", "refute", "confirm", "confirm", "confirm"])
        for i, (q, line) in enumerate(zip(qs, lines)):
            with self.subTest(question=q["question_id"]):
                self.assertEqual(sorted(line), sorted(j1.LINE_KEYS))
                self.assertEqual((line["question_id"], line["kind"], line["predicate"]),
                                 (q["question_id"], q["kind"], q["predicate"]))
                self.assertEqual((line["mentions_entity"], line["error_kind"], line["scored"]), ("yes", None, True))
                reply = {"mentions_entity": "yes", "describes_predicate": line["describes_predicate"]}
                self.assertEqual(line["verdict"], j1.verdict(j1.stand_in(q["record_ref"]), reply))
                self.assertIsNone(j1.line_problem(q, line))
                self.assertEqual(j1.prior_verdicts([q]), [line])      # no other question counts
        score = j1.score(qs, lines, bootstrap_b=1000, bootstrap_seed=1)
        self.assertEqual((score["sensitivity"]["value"], score["specificity"]["value"]), (0.5, 0.25))

    def test_it_reads_only_each_question_s_predicate(self) -> None:
        """The same predicates give the same lines whatever the records, the entities or the other questions say."""
        qs = questions()
        lines = j1.prior_verdicts(qs)
        moved = [dict(q, entity_id="OTHER", filed=["tires"]) for q in qs]
        self.assertEqual([line["verdict"] for line in j1.prior_verdicts(moved)], [line["verdict"] for line in lines])
        self.assertEqual(j1.prior_verdicts(list(reversed(qs))), list(reversed(lines)))
        self.assertEqual([line["verdict"] for line in lines],
                         ["confirm" if q["predicate"] in j1.PRIOR_TOP else "refute" for q in qs])

    def test_the_bound_by_hand(self) -> None:
        """engine: 2 positives, 1 negative; tires: 1 positive, 2 negatives; seats: 1 positive, 1 negative. The best
        predicate-only judge confirms engine and refutes tires: 2 + 2 + 1 right of 8 questions."""
        qs = [_q("A", "positive", "engine"), _q("A", "negative", "tires"), _q("B", "positive", "engine"),
              _q("B", "negative", "tires"), _q("C", "positive", "tires"), _q("C", "negative", "engine"),
              _q("D", "positive", "seats"), _q("D", "negative", "seats")]
        self.assertEqual(j1.prior_bound(qs), {"value": 5 / 8, "records": 4, "questions": 8})
        self.assertEqual(j1.prior_bound(qs, ["A", "B"]), {"value": 1.0, "records": 2, "questions": 4})
        self.assertEqual(j1.prior_bound(qs, ["D"]), {"value": 0.5, "records": 1, "questions": 2})
        self.assertEqual(j1.prior_bound(qs, []), {"value": None, "records": 0, "questions": 0})
        # no predicate-only judge beats it: every subset of the predicates confirmed
        names = sorted({q["predicate"] for q in qs})
        for mask in range(2 ** len(names)):
            confirm = {p for i, p in enumerate(names) if mask >> i & 1}
            right = sum((q["predicate"] in confirm) == (q["kind"] == "positive") for q in qs)
            self.assertLessEqual(right / len(qs), j1.prior_bound(qs)["value"])

    def test_a_balanced_draw(self) -> None:
        """Each predicate asked as often both ways: any judge that answers by the predicate alone scores exactly 0.5,
        and so do the control and the bound. The first control, leaving each record out, got every question wrong."""
        for names in (["air_bags", "engine", "seats", "tires"], ["engine", "tires"], ["seats", "tires", "wheels"]):
            qs = balanced(names, records=4 * len(names))
            with self.subTest(names=names):
                score = j1.score(qs, j1.prior_verdicts(qs), bootstrap_b=1000, bootstrap_seed=1)
                self.assertEqual(score["balanced_accuracy"]["value"], 0.5)
                self.assertEqual(j1.prior_bound(qs)["value"], 0.5)
                for mask in range(2 ** len(names)):
                    confirm = {p for i, p in enumerate(names) if mask >> i & 1}
                    right = sum((q["predicate"] in confirm) == (q["kind"] == "positive") for q in qs)
                    self.assertEqual(right / len(qs), 0.5)
                first = [dict(line, verdict=v) for line, v in zip(j1.prior_verdicts(qs), leave_one_out(qs))]
                self.assertEqual(j1.score(qs, first, bootstrap_b=1000, bootstrap_seed=1)["balanced_accuracy"]["value"],
                                 0.0)


# --------------------------------------------------------------------------------------------------- scoring

def _line(q: dict[str, Any], verdict: str, scored: bool = True, error_kind: str | None = None) -> dict[str, Any]:
    answers = {"confirm": ("yes", "yes"), "refute": ("yes", "no"), "unknown": ("yes", "unclear")}[verdict]
    if error_kind is not None:
        answers = (None, None)
    return {"question_id": q["question_id"], "record_ref": q["record_ref"], "kind": q["kind"],
            "predicate": q["predicate"], "mentions_entity": answers[0], "describes_predicate": answers[1],
            "verdict": verdict, "error_kind": error_kind, "scored": scored}


class ScoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.qs = questions()
        rng = random.Random(7)
        self.lines = [_line(q, rng.choice(("confirm", "refute", "unknown"))) for q in self.qs]

    def test_balanced_accuracy_equals_accuracy_and_the_intervals_are_the_cluster_bootstrap(self) -> None:
        s = j1.score(self.qs, self.lines, bootstrap_b=1000, bootstrap_seed=1)
        self.assertEqual(s["balanced_accuracy"], s["accuracy"])
        self.assertAlmostEqual(s["balanced_accuracy"]["value"],
                               (s["sensitivity"]["value"] + s["specificity"]["value"]) / 2, places=12)
        pos = [[float(line["verdict"] == "confirm")] for line in self.lines if line["kind"] == "positive"]
        neg = [[float(line["verdict"] == "refute")] for line in self.lines if line["kind"] == "negative"]
        both = [p + n for p, n in zip(pos, neg)]
        unknown = [[float(a["verdict"] == "unknown"), float(b["verdict"] == "unknown")]
                   for a, b in zip(self.lines[0::2], self.lines[1::2])]
        for key, clusters in (("sensitivity", pos), ("specificity", neg), ("accuracy", both),
                              ("unknown_share", unknown)):
            ref = stats.cluster_bootstrap_mean(clusters, B=1000, seed="j1:1")
            self.assertEqual(s[key], {"value": ref["mean"], "ci_low": ref["ci_low"], "ci_high": ref["ci_high"]}, key)
        self.assertEqual((s["records_scored"], s["records_dropped"], s["questions_scored"]), (40, 0, 80))
        self.assertEqual(sum(s["verdicts"]["positive"].values()) + sum(s["verdicts"]["negative"].values()), 80)

    def test_a_transport_failure_leaves_its_record_out_and_a_model_failure_counts(self) -> None:
        lines = list(self.lines)
        lines[0] = _line(self.qs[0], "unknown", scored=False, error_kind="timeout")
        lines[3] = _line(self.qs[3], "unknown", error_kind="schema_invalid")
        s = j1.score(self.qs, lines, bootstrap_b=1000, bootstrap_seed=1)
        self.assertEqual((s["records_scored"], s["records_dropped"], s["dropped_share"]), (39, 1, 1 / 40))
        self.assertEqual(s["failures"]["transport"], {"questions": 1, "by_kind": {"timeout": 1}})
        self.assertEqual(s["failures"]["model"], {"questions": 1, "by_kind": {"schema_invalid": 1}})
        self.assertEqual(s["answers"]["negative"].get("failed"), 1)
        kept = j1.scored_records(self.qs, lines)
        self.assertNotIn(self.qs[0]["record_ref"], kept)
        lexical = j1.score(self.qs, self.lines, bootstrap_b=1000, bootstrap_seed=1, records=kept)
        self.assertEqual((lexical["records_scored"], lexical["records_not_judged"]), (39, 0))
        # Without ``records``, the record the model lost has no lexical line and reads as not judged.
        filtered = j1.score(self.qs, [line for line in self.lines if line["record_ref"] in kept],
                            bootstrap_b=1000, bootstrap_seed=1)
        self.assertEqual(filtered["records_not_judged"], 1)
        self.assertEqual(lexical, dict(filtered, records_not_judged=0))

    def test_confirms_per_predicate(self) -> None:
        """``by_predicate``: per predicate and kind, the scored questions, the confirms and their rate."""
        s = j1.score(self.qs, self.lines, bootstrap_b=1000, bootstrap_seed=1)
        by = s["by_predicate"]
        self.assertEqual(sorted(by), sorted({q["predicate"] for q in self.qs}))
        for kind in j1.KINDS:
            self.assertEqual(sum(cells[kind]["questions"] for cells in by.values()), 40)
        for predicate, cells in by.items():
            for kind in j1.KINDS:
                mine = [line for q, line in zip(self.qs, self.lines)
                        if q["predicate"] == predicate and q["kind"] == kind]
                confirmed = sum(1 for line in mine if line["verdict"] == "confirm")
                with self.subTest(predicate=predicate, kind=kind):
                    self.assertEqual(cells[kind], {"questions": len(mine), "confirmed": confirmed,
                                                   "confirm_rate": confirmed / len(mine) if mine else None})
        lines = list(self.lines)
        lines[0] = _line(self.qs[0], "unknown", scored=False, error_kind="timeout")
        dropped = j1.score(self.qs, lines, bootstrap_b=1000, bootstrap_seed=1)["by_predicate"]
        for kind in j1.KINDS:
            self.assertEqual(sum(cells[kind]["questions"] for cells in dropped.values()), 39)

    def test_a_part_that_stopped_leaves_records_not_judged(self) -> None:
        s = j1.score(self.qs, self.lines[:21], bootstrap_b=1000, bootstrap_seed=1)
        self.assertEqual((s["records_scored"], s["records_not_judged"], s["records_dropped"]), (10, 30, 0))

    def test_the_paired_difference(self) -> None:
        other = [_line(q, "confirm" if q["kind"] == "positive" else "refute") for q in self.qs]
        p = j1.paired(self.qs, self.lines, other, bootstrap_b=1000, bootstrap_seed=1)
        mine = [(float(a["verdict"] == "confirm") + float(b["verdict"] == "refute")) / 2
                for a, b in zip(self.lines[0::2], self.lines[1::2])]
        ref = stats.paired_bootstrap(mine, [1.0] * 40, B=1000, seed="j1:1")
        self.assertEqual((p["n"], p["mean_diff"], p["ci_low"], p["ci_high"]),
                         (40, ref["mean_diff"], ref["ci_low"], ref["ci_high"]))
        s = j1.score(self.qs, self.lines, bootstrap_b=1000, bootstrap_seed=1)
        self.assertAlmostEqual(p["mean_diff"], s["balanced_accuracy"]["value"] - 1.0, places=12)

    def test_the_headline_at_its_boundaries(self) -> None:
        ba = {"value": 0.7, "ci_low": 0.6, "ci_high": 0.8}
        self.assertEqual(j1.headline(ba, 0.59), "better")
        self.assertEqual(j1.headline(ba, 0.6), "not_told_apart")
        self.assertEqual(j1.headline(ba, 0.7), "not_told_apart")
        self.assertEqual(j1.headline(ba, 0.8), "not_told_apart")
        self.assertEqual(j1.headline(ba, 0.81), "worse")
        self.assertIsNone(j1.headline({"value": None, "ci_low": None, "ci_high": None}, 0.5))

    def test_when_a_model_gets_a_headline(self) -> None:
        ba = {"value": 0.7, "ci_low": 0.6, "ci_high": 0.8}
        go = dict(complete=True, display_class="model", measurement=True, dropped_share=0.0, model_ba=ba,
                  lexical_ba=0.5)
        self.assertEqual(j1.headline_for(**go), ("better", None))
        self.assertEqual(j1.headline_for(**{**go, "dropped_share": 0.01}), ("better", None))
        self.assertEqual(j1.headline_for(**{**go, "dropped_share": 2 / 150}), (None, "withheld"))
        self.assertEqual(j1.headline_for(**{**go, "complete": False}), (None, "incomplete"))
        self.assertEqual(j1.headline_for(**{**go, "display_class": "plumbing"}), (None, "not_measured"))
        self.assertEqual(j1.headline_for(**{**go, "measurement": False}), (None, "not_measured"))


# --------------------------------------------------------------------------------------------------- the runner

class _Scripted:
    """The lab's responder, except for the payloads of chosen questions: ``invalid`` ones get a reply the schema
    refuses (so the repair fails too); ``repaired`` ones get it only before the repair note, then the good reply;
    ``drop`` ones a dropped connection; ``slow`` ones wait that many seconds first, after setting their ``arrived``
    event. ``seen`` counts every call and ``repairs`` holds the payload of every request that carried a repair note."""

    def __init__(self, invalid: list[dict[str, Any]] = (), drop: list[dict[str, Any]] = (),
                 repaired: list[dict[str, Any]] = (), slow: dict[bytes, float] | None = None) -> None:
        self.base = Responder(PACK)
        self.invalid = {canonical_bytes(p) for p in invalid}
        self.drop = {canonical_bytes(p) for p in drop}
        self.repaired = {canonical_bytes(p) for p in repaired}
        self.slow = dict(slow or {})
        self.arrived = threading.Event()
        self.seen = 0
        self.repairs: list[bytes] = []

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        self.seen += 1
        key = canonical_bytes(request_payload(request))
        repair = REPAIR_MARKER in request["messages"][-1]["content"]
        if repair:
            self.repairs.append(key)
        if key in self.slow:
            self.arrived.set()
            time.sleep(self.slow[key])
        if key in self.drop:
            raise RuntimeError("dropped")
        if key in self.invalid or (key in self.repaired and not repair):
            return {"mentions_entity": "perhaps", "describes_predicate": "yes"}
        return self.base(request)


class _Prereg:
    """A J1 plan (fake models, 40 records in 2 parts) preregistered with the test archive, made once."""

    tmp: Path | None = None

    @classmethod
    def get(cls) -> "type[_Prereg]":
        if cls.tmp is None:
            cls.tmp = Path(tempfile.mkdtemp(prefix="lab-j1-prereg-"))
            request = plumbing_min()
            request["experiments"] = {"j1": dict(J1_BLOCK)}
            path = write_json(cls.tmp / "j1-test.json", request)
            code, _, err = run_plan(["--request", str(path), "--manifest", str(MANIFEST_TEST),
                                     "--out", str(cls.tmp / "plan")])
            assert code == 0, err
            cls.plan_path = cls.tmp / "plan" / "plan.json"
            cls.plan = json.loads(cls.plan_path.read_text(encoding="utf-8"))
            cls.manifest = lab_prereg.preregister(cls.plan_path, nhtsa_fetch=archive)
            cls.dir = cls.tmp / "plan" / "prereg" / "j1"
        return cls


def tearDownModule() -> None:
    for holder in (_Prereg, _J001):
        if holder.tmp is not None:
            kill_mentioning(str(holder.tmp))
            shutil.rmtree(holder.tmp, True)


class PreregTests(unittest.TestCase):
    def test_the_preregistration(self) -> None:
        p = _Prereg.get()
        m = p.manifest["j1"]
        self.assertEqual(sorted(path.name for path in p.dir.iterdir()),
                         ["labels.json", "labels.jsonl", "lexical.json", "lexical.jsonl", "prereg.json", "prior.json",
                          "prior.jsonl", "questions.json", "questions.jsonl", "routing.json"])
        doc = json.loads((p.dir / "prereg.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(doc), sorted(j1.PREREG_KEYS))
        self.assertEqual((doc["kind"], doc["boundary"], doc["data_label"], doc["parts"], doc["seed"]),
                         ("lab_j1_prereg", "site:lab", "public", 2, 1))
        self.assertEqual(doc["labels"]["sha256"], hashlib.sha256((p.dir / "labels.jsonl").read_bytes()).hexdigest())
        self.assertEqual(doc["questions"]["sha256"],
                         hashlib.sha256((p.dir / "questions.jsonl").read_bytes()).hexdigest())
        self.assertEqual(doc["lexical"]["sha256"], hashlib.sha256((p.dir / "lexical.jsonl").read_bytes()).hexdigest())
        self.assertEqual(doc["prior"], {"sha256": hashlib.sha256((p.dir / "prior.jsonl").read_bytes()).hexdigest(),
                                        "rule": "whole_file_top_four", "top": list(j1.PRIOR_TOP),
                                        "source": "docs/collective/replay/vehicles/nhtsa-probe.json"})
        self.assertEqual(doc["questions"]["negative_draw"], "other_records_positive_frequency")
        self.assertEqual(doc["task"], j1.task_pins())
        self.assertEqual((doc["task"]["name"], doc["task"]["max_tokens"]), ("judge_record", 256))
        self.assertEqual(doc["code_hash"], j1.code_hash())
        for name in ("mycelic/collective/edge/verify.py", "mycelic/collective/experiments/e1_extract.py",
                     "mycelic/collective/experiments/common.py", "lab/j1.py"):
            self.assertIn(name, doc["code_files"])
        self.assertEqual([e["name"] for e in doc["endpoints"]], ["fake-a", "fake-b"])
        self.assertEqual({e["boundary"] for e in doc["endpoints"]}, {"site:lab"})
        self.assertEqual((doc["twins"], doc["excluded"]), ([list(t) for t in j1.TWINS], ["unknown_or_other"]))
        routing = json.loads((p.dir / "routing.json").read_text(encoding="utf-8"))
        self.assertEqual((sorted(routing["endpoints"]), routing["routes"]), (["fake-a", "fake-b"], {}))
        self.assertEqual(m["lexical"], json.loads((p.dir / "lexical.json").read_text(encoding="utf-8")))
        self.assertEqual((m["endpoints"], m["prereg"], m["parts"]), (["fake-a", "fake-b"], "prereg/j1/prereg.json", 2))
        lexical = [json.loads(line) for line in (p.dir / "lexical.jsonl").read_text(encoding="utf-8").splitlines()]
        qs = j1.read_questions((p.dir / "questions.jsonl").read_bytes())
        self.assertEqual(lexical, j1.lexical_verdicts(PACK, (p.dir / "labels.jsonl").read_bytes(), qs))
        self.assertEqual(m["lexical"], j1.score(qs, lexical, bootstrap_b=1000, bootstrap_seed=1))
        prior = [json.loads(line) for line in (p.dir / "prior.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(prior, j1.prior_verdicts(qs))
        self.assertEqual(m["prior"], json.loads((p.dir / "prior.json").read_text(encoding="utf-8")))
        self.assertEqual(m["prior"], j1.score(qs, prior, bootstrap_b=1000, bootstrap_seed=1))
        self.assertEqual(m["prior_bound"], j1.prior_bound(qs))
        self.assertEqual((m["prior_bound"]["records"], m["prior_bound"]["questions"]), (40, 80))
        lab_prereg.load_prereg(p.plan_path)

    def test_the_plan_summary_shows_the_preregistration(self) -> None:
        p = _Prereg.get()
        md, sources = lab_summary.render_plan(p.tmp / "plan")
        self.assertIn("`J1` labels: source `nhtsa`, records 40", md)
        self.assertIn("lexical judge, every record: balanced accuracy", md)
        self.assertIn("`J1` record-blind control, every record: balanced accuracy", md)
        self.assertIn(f"`J1` predicate-only bound, every record: {p.manifest['j1']['prior_bound']['value']:.3f}", md)
        self.assertIn(J1_PRIOR_NOTE, md)
        check_sources(self, md, sources, p.tmp / "plan")

    def test_the_archive_is_fetched_once(self) -> None:
        calls = []
        once = lab_prereg._once(lambda: calls.append(1) or b"x")
        self.assertEqual((once(), once(), len(calls)), (b"x", b"x", 1))


class RunTests(unittest.TestCase):
    """``lab.j1 run`` in this process against the collective's fake server answered by the lab's responder."""

    def setUp(self) -> None:
        self.p = _Prereg.get()
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-j1-run-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.qs = [q for q in j1.read_questions((self.p.dir / "questions.jsonl").read_bytes()) if q["part"] == 1]
        self.recs = {r["record_ref"]: r for r, _ in j1.parse_labels((self.p.dir / "labels.jsonl").read_bytes())}

    def server(self, responder: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> FakeOpenAIServer:
        server = FakeOpenAIServer("valid", responder=responder or Responder(PACK)).start()
        self.addCleanup(server.stop)
        return server

    def routing(self, base_url: str, model: str = "fake-a", change: Callable[[dict[str, Any]], None] | None = None
                ) -> Path:
        doc = j1.routing_doc(self.p.plan["models"], [model], base_url)
        if change is not None:
            change(doc)
        return write_json(self.tmp / f"routing-{model}.json", doc)

    def argv(self, routing: Path, *, endpoint: str = "fake-a", part: int = 1, run_id: str = "r1",
             budget: int = 600, **files: Path) -> list[str]:
        d = self.p.dir
        return ["run", f"--prereg={files.get('prereg', d / 'prereg.json')}",
                f"--labels={files.get('labels', d / 'labels.jsonl')}",
                f"--questions={files.get('questions', d / 'questions.jsonl')}", f"--routing={routing}",
                f"--endpoint={endpoint}", f"--part={part}", f"--run-id={run_id}", f"--runs-dir={self.tmp / 'runs'}",
                f"--budget-seconds={budget}"]

    def run_j1(self, argv: list[str]) -> tuple[int, str, str, Path]:
        code, out, err = call_main(j1, argv)
        return code, out, err, self.tmp / "runs" / "j1" / argv[7].split("=", 1)[1]

    def read(self, run: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        doc = json.loads((run / "run.json").read_text(encoding="utf-8"))
        lines = [json.loads(line) for line in (run / "verdicts.jsonl").read_text(encoding="utf-8").splitlines()]
        return doc, lines

    def test_the_fake_server_gives_every_question_the_lexical_judge_s_verdict(self) -> None:
        server = self.server()
        code, out, err, run = self.run_j1(self.argv(self.routing(server.base_url)))
        self.assertEqual(code, 0, err)
        doc, lines = self.read(run)
        lexical = {json.loads(x)["question_id"]: json.loads(x)
                   for x in (self.p.dir / "lexical.jsonl").read_text(encoding="utf-8").splitlines()}
        self.assertEqual([line["question_id"] for line in lines], [q["question_id"] for q in self.qs])
        self.assertEqual(lines, [lexical[q["question_id"]] for q in self.qs])
        self.assertEqual((doc["kind"], doc["complete"], doc["stopped"], doc["measurement"], doc["listed_fake"]),
                         ("lab_j1_run", True, None, False, True))
        self.assertEqual((doc["questions_planned"], doc["questions_done"], doc["part"], doc["parts"]), (40, 40, 1, 2))
        self.assertEqual(doc["verdicts_sha256"], hashlib.sha256((run / "verdicts.jsonl").read_bytes()).hexdigest())
        self.assertEqual(doc["ledger_sha256"], hashlib.sha256((run / "ledger.jsonl").read_bytes()).hexdigest())
        self.assertEqual(doc["prereg_sha256"], hashlib.sha256((self.p.dir / "prereg.json").read_bytes()).hexdigest())
        self.assertEqual(doc["pinned"]["task"], j1.task_pins())
        rows = read_ledger(run / "ledger.jsonl")
        self.assertEqual(len(rows), 40)
        self.assertEqual({(r["task"], r["boundary"], r["endpoint_boundary"], r["boundary_mode"], r["data_label"])
                          for r in rows}, {("judge_record", "site:lab", "site:lab", "own", "public")})
        self.assertEqual(rows[0]["ref"], "j1-1-0000")
        self.assertTrue(all(r["fake_marker"] for r in rows))
        # the request is the shipped task's: the judge's schema by name, max_tokens 256 and the shipped payload
        chat = server.chat_requests
        self.assertEqual(len(chat), 40)
        first = chat[0]["json"]
        self.assertEqual((first["response_format"]["json_schema"]["name"], first["max_tokens"], first["temperature"]),
                         ("judge_record", 256, 0))
        self.assertEqual(request_payload(first), j1.payload(PACK, self.qs[0], self.recs[self.qs[0]["record_ref"]]))
        self.assertNotIn("narrative", (run / "verdicts.jsonl").read_text(encoding="utf-8"))
        self.assertIn("complete=true", out)

    def test_a_model_failure_is_unknown_and_a_transport_failure_is_not_scored(self) -> None:
        bad, gone = self.qs[3], self.qs[6]
        scripted = _Scripted(invalid=[j1.payload(PACK, bad, self.recs[bad["record_ref"]])],
                             drop=[j1.payload(PACK, gone, self.recs[gone["record_ref"]])])
        server = self.server(scripted)
        code, _, err, run = self.run_j1(self.argv(self.routing(server.base_url)))
        self.assertEqual(code, 0, err)
        doc, lines = self.read(run)
        by_id = {line["question_id"]: line for line in lines}
        self.assertEqual((by_id[bad["question_id"]]["verdict"], by_id[bad["question_id"]]["error_kind"],
                          by_id[bad["question_id"]]["scored"], by_id[bad["question_id"]]["mentions_entity"]),
                         ("unknown", "schema_invalid", True, None))
        self.assertEqual((by_id[gone["question_id"]]["error_kind"], by_id[gone["question_id"]]["scored"]),
                         ("network", False))
        self.assertEqual(doc["failures"]["model"], {"questions": 1, "by_kind": {"schema_invalid": 1}})
        self.assertEqual(doc["failures"]["transport"], {"questions": 1, "by_kind": {"network": 1}})
        self.assertTrue(doc["complete"])
        # rule 3's one repair: the invalid reply was asked again once, with the repair note, and failed again
        rows = read_ledger(run / "ledger.jsonl")
        ref_bad, ref_gone = (f"j1-1-{self.qs.index(q):04d}" for q in (bad, gone))
        self.assertEqual([(r["attempt"], r["error_kind"]) for r in rows if r["ref"] == ref_bad],
                         [(1, "schema_invalid"), (2, "schema_invalid")])
        self.assertEqual(scripted.repairs, [canonical_bytes(j1.payload(PACK, bad, self.recs[bad["record_ref"]]))])
        self.assertEqual([(r["attempt"], r["error_kind"]) for r in rows if r["ref"] == ref_gone], [(1, "network")])
        score = j1.score(self.qs, lines, bootstrap_b=1000, bootstrap_seed=1)
        self.assertEqual((score["records_scored"], score["records_dropped"]), (19, 1))
        self.assertEqual(score["failures"]["model"]["questions"], 1)
        # the lab counts the invalid reply as the model's answer, so the part stays valid
        record, problem = units.participation("j1", rows, ["judge_record"], doc)
        self.assertEqual(record["tasks"]["judge_record"]["attempted"], 40)
        self.assertEqual(record["tasks"]["judge_record"]["ok"], 39)
        self.assertIsNone(problem)

    def test_one_repair_after_an_invalid_reply(self) -> None:
        """A first reply the schema refuses, then a valid one after the repair note: the verdict is the repaired
        reply's, with no error, and the ledger holds both attempts."""
        fixed = self.qs[5]
        payload = j1.payload(PACK, fixed, self.recs[fixed["record_ref"]])
        scripted = _Scripted(repaired=[payload])
        server = self.server(scripted)
        code, _, err, run = self.run_j1(self.argv(self.routing(server.base_url)))
        self.assertEqual(code, 0, err)
        doc, lines = self.read(run)
        lexical = {json.loads(x)["question_id"]: json.loads(x)
                   for x in (self.p.dir / "lexical.jsonl").read_text(encoding="utf-8").splitlines()}
        line = next(line for line in lines if line["question_id"] == fixed["question_id"])
        self.assertEqual(line, lexical[fixed["question_id"]])
        self.assertEqual((line["error_kind"], line["scored"], line["mentions_entity"]), (None, True, "yes"))
        rows = read_ledger(run / "ledger.jsonl")
        self.assertEqual([(r["attempt"], r["ok"], r["error_kind"]) for r in rows if r["ref"] == "j1-1-0005"],
                         [(1, False, "schema_invalid"), (2, True, None)])
        self.assertEqual(scripted.repairs, [canonical_bytes(payload)])
        self.assertEqual(doc["failures"], {"model": {"questions": 0, "by_kind": {}},
                                           "transport": {"questions": 0, "by_kind": {}}})
        record, problem = units.participation("j1", rows, ["judge_record"], doc)
        self.assertEqual((record["tasks"]["judge_record"]["ok"], problem), (40, None))

    def test_participation_reads_each_call_s_last_row(self) -> None:
        """A call whose repair hit a transport failure is not answered; one whose repair failed validation is."""

        def row(ref: str, attempt: int, ok: bool, kind: str | None) -> dict[str, Any]:
            return {"task": "judge_record", "ref": ref, "attempt": attempt, "ok": ok, "error_kind": kind}

        rows = [row("a", 1, True, None), row("b", 1, False, "schema_invalid"), row("b", 2, False, "json_invalid"),
                row("c", 1, False, "schema_invalid"), row("c", 2, False, "timeout"), row("d", 1, False, "network"),
                row("e", 1, False, "json_invalid"), row("e", 2, True, None)]
        record, _ = units.participation("j1", rows, ["judge_record"], {})
        self.assertEqual((record["tasks"]["judge_record"]["attempted"], record["tasks"]["judge_record"]["ok"]), (5, 3))

    def test_a_call_still_running_at_the_budget_is_cut_short(self) -> None:
        """The budget's timer stops a call in flight: the run keeps what it judged, writes its final run.json at
        once, and reads as stopped (J1_STOPPED), not as a timeout."""
        slow = self.qs[1]
        scripted = _Scripted(slow={canonical_bytes(j1.payload(PACK, slow, self.recs[slow["record_ref"]])): 4.0})
        server = self.server(scripted)
        handlers = {name: signal.getsignal(getattr(signal, name)) for name in ("SIGALRM", "SIGTERM", "SIGINT")}
        t0 = time.monotonic()
        code, _, err, run = self.run_j1(self.argv(self.routing(server.base_url), budget=1))
        elapsed = time.monotonic() - t0
        self.assertEqual(code, 1, err)
        self.assertLess(elapsed, 3.5)
        doc, lines = self.read(run)
        self.assertEqual((doc["complete"], doc["stopped"], doc["questions_done"], len(lines)), (False, "budget", 1, 1))
        self.assertEqual(lines[0]["question_id"], self.qs[0]["question_id"])
        self.assertEqual(doc["verdicts_sha256"], hashlib.sha256((run / "verdicts.jsonl").read_bytes()).hexdigest())
        self.assertEqual(doc["ledger_sha256"], hashlib.sha256((run / "ledger.jsonl").read_bytes()).hexdigest())
        self.assertIsNotNone(doc["finished_at"])
        self.assertEqual(units.harness_status("j1", code, False, doc), ("failed", J1_STOPPED))
        self.assertEqual({name: signal.getsignal(getattr(signal, name)) for name in handlers}, handlers)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_sigterm_stops_the_run_and_writes_its_final_run_json(self) -> None:
        """``lab.j1 run`` in its own process, sent SIGTERM during a call (as a shard stops a unit): exit 130, and the
        final run.json says interrupted, with the verdicts it kept and its files' hashes."""
        slow = self.qs[2]
        scripted = _Scripted(slow={canonical_bytes(j1.payload(PACK, slow, self.recs[slow["record_ref"]])): 20.0})
        server = self.server(scripted)
        argv = self.argv(self.routing(server.base_url))
        proc = subprocess.Popen([sys.executable, "-m", "lab.j1", *argv], cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        self.assertTrue(scripted.arrived.wait(60))
        proc.send_signal(signal.SIGTERM)
        _, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 130, err.decode("utf-8", "replace"))
        doc, lines = self.read(self.tmp / "runs" / "j1" / "r1")
        self.assertEqual((doc["complete"], doc["stopped"], doc["questions_done"], len(lines)),
                         (False, "interrupted", 2, 2))
        run = self.tmp / "runs" / "j1" / "r1"
        self.assertEqual(doc["verdicts_sha256"], hashlib.sha256((run / "verdicts.jsonl").read_bytes()).hexdigest())
        self.assertEqual(doc["ledger_sha256"], hashlib.sha256((run / "ledger.jsonl").read_bytes()).hexdigest())

    def test_a_slow_unit_stops_at_its_budget_not_its_timeout(self) -> None:
        """``lab.units.run_unit`` with a fake server that takes five seconds a call, a unit of nine seconds and the
        margin cut to three, so the budget is six: the second call is still running when the budget ends, and would
        end after the unit's timeout. The run cuts it short and exits on its own, so the unit reads J1_STOPPED with
        its final run.json, not timed_out."""
        unit = next(u for u in self.p.plan["units"] if u["unit"] == "j1-fake-b-p1")
        plan = json.loads(json.dumps(self.p.plan))
        plan["models"]["fake-b"]["persona"] = "slow"
        out = self.tmp / "shard"
        with mock.patch.object(units, "SIM_BUDGET_MARGIN_S", 3):
            record = units.run_unit(dict(unit, shard="s002-fake-b"), plan, out, timeout_s=9,
                                    provider_override="fake", prereg=lab_prereg.load_prereg(self.p.plan_path))
        self.assertEqual((record["status"], record["status_reason"], record["exit_code"]), ("failed", J1_STOPPED, 1))
        self.assertLess(record["wall_s"], 9)
        doc = json.loads((out / "runs" / "j1" / unit["run_id"] / "run.json").read_text(encoding="utf-8"))
        self.assertEqual((doc["complete"], doc["stopped"], doc["questions_done"]), (False, "budget", 1))
        self.assertEqual(doc["budget_seconds"], 6)
        self.assertIsNotNone(doc["verdicts_sha256"])

    def test_it_stops_at_its_budget(self) -> None:
        """The check before each question, driven by the test's clock alone: no real timer is armed, so the stop
        never depends on how fast this runner is (the timer has its own tests above)."""
        server = self.server()
        ticks = iter([0.0, 0.0, 0.5, 2.0, 3.0, 4.0])
        with mock.patch.object(j1, "_clock", lambda: next(ticks)), \
                mock.patch.object(j1.signal, "setitimer") as setitimer:
            code, out, err, run = self.run_j1(self.argv(self.routing(server.base_url), budget=1))
        self.assertEqual(setitimer.call_args_list,
                         [mock.call(signal.ITIMER_REAL, 1.0), mock.call(signal.ITIMER_REAL, 0)])
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        self.assertEqual(code, 1, err)
        doc, lines = self.read(run)
        self.assertEqual((doc["complete"], doc["stopped"], doc["questions_done"], len(lines)),
                         (False, "budget", 2, 2))
        self.assertEqual(len(server.chat_requests), 2)
        self.assertEqual(units.harness_status("j1", 1, False, doc), ("failed", J1_STOPPED))

    def test_it_stops_when_the_server_stays_down(self) -> None:
        code, _, err, run = self.run_j1(self.argv(self.routing(f"http://127.0.0.1:{free_port()}/v1")))
        self.assertEqual(code, 1, err)
        doc, lines = self.read(run)
        self.assertEqual((doc["complete"], doc["stopped"], len(lines)), (False, "server_down", 2))
        self.assertEqual({line["error_kind"] for line in lines}, {"network"})
        self.assertEqual({line["scored"] for line in lines}, {False})

    def test_every_pin_is_checked_before_any_call(self) -> None:
        server = self.server()
        good = self.routing(server.base_url)
        d = self.p.dir

        def changed(name: str, edit: Callable[[bytes], bytes]) -> Path:
            target = self.tmp / name
            target.write_bytes(edit((d / name).read_bytes()))
            return target

        def prereg_with(change: Callable[[dict[str, Any]], None]) -> Path:
            doc = json.loads((d / "prereg.json").read_text(encoding="utf-8"))
            change(doc)
            return write_json(self.tmp / "prereg.json", doc)

        cases = [
            ("labels", {"labels": changed("labels.jsonl", lambda b: b.replace(b"stalled", b"stopped", 1))}),
            ("questions", {"questions": changed("questions.jsonl", lambda b: b + b"\n")}),
            ("pack", {"prereg": prereg_with(lambda p: p["pack"].update(vocabulary_hash="0" * 64))}),
            ("code", {"prereg": prereg_with(lambda p: p.update(code_hash="0" * 64))}),
            ("task", {"prereg": prereg_with(lambda p: p["task"].update(max_tokens=512))}),
            ("kind", {"prereg": prereg_with(lambda p: p.update(kind="e1_prereg"))}),
            ("transport", {"routing": self.routing(server.base_url, change=lambda r: r["endpoints"]["fake-a"].update(
                transport_schema="reduced"))}),
            ("model", {"routing": self.routing(server.base_url, change=lambda r: r["endpoints"]["fake-a"].update(
                model="another-alias"))}),
            ("boundary", {"routing": self.routing(server.base_url, change=lambda r: r["endpoints"]["fake-a"].update(
                boundary="central"))}),
            ("escalation", {"routing": self.routing(server.base_url, change=lambda r: r["routes"]["judge_record"]
                                                    .update(escalate_to="fake-a"))}),
            ("part", {"part": 3}),
            ("endpoint", {"endpoint": "fake-slow"}),
        ]
        for i, (label, change) in enumerate(cases):
            routing = change.pop("routing", good)
            argv = self.argv(routing, run_id=f"pin-{i}", **change)
            with self.subTest(pin=label):
                code, _, err, run = self.run_j1(argv)
                self.assertEqual(code, 2, label)
                self.assertTrue(err.startswith("error: "), err)
                self.assertFalse(run.exists())
        self.assertEqual(server.chat_requests, [])
        self.assertEqual(code, 2)
        with mock.patch.object(j1, "code_hash", lambda: "1" * 64):
            code, _, _, run = self.run_j1(self.argv(good, run_id="pin-code-now"))
        self.assertEqual((code, run.exists()), (2, False))

    def test_the_unit_adapter_runs_it(self) -> None:
        """``lab.units.run_unit`` with the fake provider: the argv, the routing, the files and the status."""
        unit = next(u for u in self.p.plan["units"] if u["unit"] == "j1-fake-b-p2")
        prereg = lab_prereg.load_prereg(self.p.plan_path)
        out = self.tmp / "shard"
        record = units.run_unit(dict(unit, shard="s002-fake-b"), self.p.plan, out, timeout_s=300,
                                provider_override="fake", prereg=prereg)
        self.assertEqual((record["status"], record["measurement_class"]), ("ok", "plumbing"), record["status_reason"])
        self.assertEqual(record["notes"], ["plumbing", "public_narratives", "runner_hardware"])
        base = f"runs/j1/{unit['run_id']}/"
        self.assertEqual(sorted(record["files"]), [base + "ledger.jsonl", base + "run.json", base + "verdicts.jsonl"])
        argv = record["argv"]
        self.assertEqual(argv[2:4], ["lab.j1", "run"])
        self.assertIn("--part=2", argv)
        self.assertIn("--endpoint=fake-b", argv)
        self.assertIn(f"--budget-seconds={300 - units.SIM_BUDGET_MARGIN_S}", argv)
        routing = json.loads((out / "routing" / f"{unit['unit']}.json").read_text(encoding="utf-8"))
        self.assertEqual((sorted(routing["endpoints"]), routing["routes"]),
                         (["fake-b"], {"judge_record": {"endpoint": "fake-b"}}))
        self.assertEqual(record["participation"]["tasks"]["judge_record"]["attempted"], 40)
        self.assertEqual(record["harness_measurement"], False)

    def test_the_status_reading(self) -> None:
        done = {"kind": "lab_j1_run", "complete": True}
        stopped = {"kind": "lab_j1_run", "complete": False}
        self.assertEqual(units.harness_status("j1", 0, False, done), ("ok", None))
        self.assertEqual(units.harness_status("j1", 1, False, stopped), ("failed", J1_STOPPED))
        self.assertEqual(units.harness_status("j1", 0, False, stopped), ("failed", RESULT_CONTRADICTS_EXIT))
        self.assertEqual(units.harness_status("j1", 1, False, done), ("failed", RESULT_CONTRADICTS_EXIT))
        self.assertEqual(units.harness_status("j1", 0, False, None)[0], "failed")
        self.assertEqual(units.harness_status("j1", 2, False, done)[0], "failed")
        self.assertEqual(units.harness_status("j1", 0, True, done)[0], "timed_out")
        self.assertEqual(units.harness_measurement("j1", {"measurement": True}), True)
        self.assertIs(units.harness_verdict("j1", {"measurement": False}), False)
        self.assertEqual(units.required_tasks({"experiment": "j1", "params": {}}), ["judge_record"])
        self.assertTrue(units.public_text({"experiment": "j1", "params": {}}))


# --------------------------------------------------------------------------------------------------- J001's dry run

class _J001:
    """``lab.dryrun`` of J001's own request (written outside ``lab/requests/``), the archive stubbed, made once."""

    tmp: Path | None = None

    @classmethod
    def get(cls) -> "type[_J001]":
        if cls.tmp is None:
            cls.tmp = Path(tempfile.mkdtemp(prefix="lab-j1-j001-"))
            request = write_json(cls.tmp / "judge-001.json", J001_REQUEST)
            cls.out = cls.tmp / "D"
            original = lab_prereg.preregister
            stdout = io.StringIO()
            with mock.patch.object(lab_prereg, "preregister", functools.partial(original, nhtsa_fetch=archive)), \
                    contextlib.redirect_stdout(stdout):
                cls.code = lab_dryrun.main(["--request", str(request), "--out", str(cls.out),
                                            "--manifest", str(LAB_MANIFEST)])
            cls.stdout = stdout.getvalue()
            cls.plan = json.loads((cls.out / "plan" / "plan.json").read_text(encoding="utf-8"))
            cls.report = json.loads((cls.out / "report" / "report.json").read_text(encoding="utf-8"))
        return cls


class J001DryRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dry = _J001.get()

    def test_the_plan(self) -> None:
        plan = self.dry.plan
        self.assertEqual((len(plan["units"]), len(plan["shards"]), plan["result_class"]), (18, 9, "real"))
        self.assertEqual([s["units"] for s in plan["shards"]],
                         [[f"j1-{m}-p{k}", f"j1-{m}-p{k + 1}"] for m in ("a-0p5b", "a-1p5b", "a-4b")
                          for k in (1, 3, 5)])
        self.assertEqual({(s["planned_minutes"], s["timeout_minutes"]) for s in plan["shards"]}, {(300, 325)})
        self.assertEqual(sorted(e["key"] for e in plan["provision"] if e["target"] == "gguf"),
                         ["a-0p5b", "a-1p5b", "a-4b"])

    def test_every_unit_ran_against_the_fake_provider(self) -> None:
        self.assertEqual(self.dry.code, 0, self.dry.stdout)
        lines = self.dry.stdout.splitlines()
        self.assertIn("plan: 18 units in 9 shards (real)", lines)
        self.assertIn("prereg: e1 no x1 no e2 no j1 yes", lines)
        units_ = self.dry.report["units"]
        self.assertEqual({(u["status"], u["display_class"]) for u in units_}, {("ok", "plumbing")})

    def test_the_report_block(self) -> None:
        block = self.dry.report["j1"]
        self.assertEqual((block["reason"], block["display_class"], sorted(block["models"])),
                         (None, "plumbing", ["a-0p5b", "a-1p5b", "a-4b"]))
        self.assertEqual((block["labels"]["records"], block["questions"]["questions"],
                          block["questions"]["part_records"]), (150, 300, [25] * 6))
        lexical, prior = block["lexical"], block["prior"]
        manifest = json.loads((self.dry.out / "plan" / "prereg" / "prereg.json").read_text(encoding="utf-8"))
        self.assertEqual((lexical, prior), (manifest["j1"]["lexical"], manifest["j1"]["prior"]))
        self.assertEqual(block["questions"]["negative_draw"], "other_records_positive_frequency")
        qs = j1.read_questions((self.dry.out / "plan" / "prereg" / "j1" / "questions.jsonl").read_bytes())
        self.assertEqual(block["prior_bound"], j1.prior_bound(qs))
        self.assertEqual(block["prior_bound"], manifest["j1"]["prior_bound"])
        self.assertEqual((block["prior_bound"]["records"], block["prior_bound"]["questions"]), (150, 300))
        for name, entry in block["models"].items():
            with self.subTest(model=name):
                self.assertEqual((entry["complete"], entry["parts_finished"], entry["display_class"]),
                                 (True, 6, "plumbing"))
                self.assertEqual((entry["headline"], entry["headline_reason"]), (None, J1_NOT_MEASURED))
                for metric in j1.METRICS:
                    self.assertEqual(entry["scores"][metric], lexical[metric], metric)
                    self.assertEqual(entry["lexical"][metric], lexical[metric], metric)
                    self.assertEqual(entry["prior"][metric], prior[metric], metric)
                # the fake answers as the lexical judge does, predicate by predicate
                self.assertEqual(entry["scores"]["by_predicate"], lexical["by_predicate"])
                self.assertEqual(entry["prior"]["by_predicate"], prior["by_predicate"])
                self.assertEqual(entry["prior_bound"], block["prior_bound"])      # every record scored
                self.assertEqual((entry["paired"]["n"], entry["paired"]["mean_diff"]), (150, 0.0))
                self.assertEqual(entry["records_dropped"], 0)

    def test_the_summaries(self) -> None:
        root = self.dry.out / "report"
        md = (root / "report.md").read_text(encoding="utf-8")
        self.assertEqual(md.splitlines()[0], NO_MEASUREMENT_LINE)
        self.assertIn("#### Judge test: the verifier's narrow question, per model, beside the lexical judge", md)
        self.assertIn("| `a-4b` | `plumbing` | 6 of 6 |", md)
        self.assertIn("- record-blind control, every record: balanced accuracy", md)
        self.assertIn(f"- predicate-only bound, every record: {self.dry.report['j1']['prior_bound']['value']:.3f}", md)
        self.assertIn("| control balanced accuracy | predicate-only bound |", md)
        self.assertIn(J1_PRIOR_NOTE, md)
        sources = json.loads((root / "report.sources.json").read_text(encoding="utf-8"))["sources"]
        check_sources(self, md, sources, root)
        plan_md = (self.dry.out / "plan" / "summary.md").read_text(encoding="utf-8")
        plan_sources = json.loads((self.dry.out / "plan" / "summary.sources.json").read_text(encoding="utf-8"))
        check_sources(self, plan_md, plan_sources["sources"], self.dry.out / "plan")
        self.assertIn("records 150", plan_md)


class AggregateTests(unittest.TestCase):
    """DryTree copies of J001's dry run, changed and re-sealed as a real run would have left them."""

    def setUp(self) -> None:
        self.dry = _J001.get()
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-j1-agg-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tree = DryTree(self.dry.out, self.tmp / "T", manifest=LAB_MANIFEST)

    def model_shards(self, model: str) -> list[str]:
        return [s["shard"] for s in self.tree.plan["shards"] if s["model"] == model]

    def make_measured(self, model: str) -> None:
        """Every unit of ``model``: a verified model measurement whose run.json says ``measurement: true``."""
        for shard in self.model_shards(model):
            unit_ids = next(s["units"] for s in self.tree.plan["shards"] if s["shard"] == shard)
            for uid in unit_ids:
                unit = next(u for u in self.tree.plan["units"] if u["unit"] == uid)
                rel = f"runs/j1/{unit['run_id']}/run.json"
                doc = self.tree.read(shard, rel)
                doc["measurement"] = True
                self.tree.replace_unit_file(shard, uid, rel, canonical_bytes(doc) + b"\n")
            self.tree.make_real(shard, model_units=tuple(unit_ids))

    def drop_records(self, model: str, count: int) -> None:
        """Mark the first ``count`` records of the model's first part as transport failures."""
        shard = self.model_shards(model)[0]
        unit = next(u for u in self.tree.plan["units"] if u["unit"] == f"j1-{model}-p1")
        rel = f"runs/j1/{unit['run_id']}/verdicts.jsonl"
        lines = [json.loads(x) for x in self.tree.path(shard, rel).read_text(encoding="utf-8").splitlines()]
        refs = list(dict.fromkeys(line["record_ref"] for line in lines))[:count]
        for line in lines:
            if line["record_ref"] in refs and line["kind"] == "positive":
                line.update(mentions_entity=None, describes_predicate=None, verdict="unknown",
                            error_kind="timeout", scored=False)
        data = "".join(canonical_dumps(line) + "\n" for line in lines).encode("utf-8")
        self.tree.replace_unit_file(shard, unit["unit"], rel, data)
        run_rel = f"runs/j1/{unit['run_id']}/run.json"
        doc = self.tree.read(shard, run_rel)
        doc["verdicts_sha256"] = hashlib.sha256(data).hexdigest()
        self.tree.replace_unit_file(shard, unit["unit"], run_rel, canonical_bytes(doc) + b"\n")
        self.tree.reseal(shard)

    def aggregate(self) -> dict[str, Any]:
        code, _, err, report = self.tree.aggregate(self.tmp / "R")
        self.assertEqual(code, 0, err)
        return report["j1"]

    def test_a_complete_model_measurement_gets_its_headline(self) -> None:
        self.make_measured("a-4b")
        block = self.aggregate()
        entry = block["models"]["a-4b"]
        self.assertEqual((entry["display_class"], entry["measurement"], entry["complete"]), ("model", True, True))
        self.assertEqual((entry["headline"], entry["headline_reason"]), ("not_told_apart", None))
        self.assertEqual(block["models"]["a-1p5b"]["headline_reason"], J1_NOT_MEASURED)

    def test_a_missing_part_leaves_a_partial_reading(self) -> None:
        self.make_measured("a-4b")
        shutil.rmtree(self.tree.path(self.model_shards("a-4b")[1]))
        entry = self.aggregate()["models"]["a-4b"]
        self.assertEqual((entry["complete"], entry["partial"], entry["parts_ok"]), (False, True, [1, 2, 5, 6]))
        self.assertEqual((entry["headline"], entry["headline_reason"]), (None, J1_INCOMPLETE))
        self.assertEqual(entry["scores"]["records_scored"], 100)
        self.assertEqual(entry["lexical"]["records_scored"], 100)

    def test_transport_failures_on_more_than_one_record_in_a_hundred_withhold_it(self) -> None:
        self.make_measured("a-4b")
        self.drop_records("a-4b", 1)
        entry = self.aggregate()["models"]["a-4b"]
        self.assertEqual((entry["records_dropped"], entry["headline"], entry["withheld_reason"]),
                         (1, "not_told_apart", None))
        self.assertEqual(entry["lexical"]["records_scored"], 149)
        self.drop_records("a-4b", 2)
        entry = self.aggregate_again()["models"]["a-4b"]
        self.assertEqual((entry["records_dropped"], entry["headline"], entry["headline_reason"]),
                         (2, None, J1_WITHHELD))
        self.assertEqual(entry["withheld_reason"], J1_WITHHELD)

    def aggregate_again(self) -> dict[str, Any]:
        shutil.rmtree(self.tmp / "R", True)
        return self.aggregate()

    def test_a_changed_verdict_file_is_not_a_finished_part(self) -> None:
        shard = self.model_shards("a-0p5b")[0]
        unit = next(u for u in self.tree.plan["units"] if u["unit"] == "j1-a-0p5b-p2")
        rel = f"runs/j1/{unit['run_id']}/verdicts.jsonl"
        data = self.tree.path(shard, rel).read_bytes().replace(b'"confirm"', b'"refute"', 1)
        self.tree.replace_unit_file(shard, unit["unit"], rel, data)       # the record matches, run.json does not
        self.tree.reseal(shard)
        entry = self.aggregate()["models"]["a-0p5b"]
        self.assertEqual((entry["complete"], entry["parts_ok"]), (False, [1, 3, 4, 5, 6]))

    def restamp(self, model: str, part: int, *, lines: Callable[[list[dict[str, Any]]], None] | None = None,
                run: Callable[[dict[str, Any]], None] | None = None) -> None:
        """Change one part's verdict lines or run.json and stamp every hash again, as a hand edit that knew the
        hashes would: run.json's ``verdicts_sha256``, the unit record and the shard's seal."""
        unit = next(u for u in self.tree.plan["units"] if u["unit"] == f"j1-{model}-p{part}")
        shard = next(s["shard"] for s in self.tree.plan["shards"] if unit["unit"] in s["units"])
        rel = f"runs/j1/{unit['run_id']}/verdicts.jsonl"
        got = [json.loads(x) for x in self.tree.path(shard, rel).read_text(encoding="utf-8").splitlines()]
        if lines is not None:
            lines(got)
        data = "".join(canonical_dumps(line) + "\n" for line in got).encode("utf-8")
        self.tree.replace_unit_file(shard, unit["unit"], rel, data)
        run_rel = f"runs/j1/{unit['run_id']}/run.json"
        doc = self.tree.read(shard, run_rel)
        doc["verdicts_sha256"] = hashlib.sha256(data).hexdigest()
        if run is not None:
            run(doc)
        self.tree.replace_unit_file(shard, unit["unit"], run_rel, canonical_bytes(doc) + b"\n")
        self.tree.reseal(shard)

    def test_a_part_of_another_preregistration_is_not_finished(self) -> None:
        self.restamp("a-1p5b", 3, run=lambda doc: doc.update(prereg_sha256="0" * 64))
        entry = self.aggregate()["models"]["a-1p5b"]
        self.assertEqual((entry["complete"], entry["parts_ok"]), (False, [1, 2, 4, 5, 6]))
        self.assertEqual(entry["headline_reason"], J1_INCOMPLETE)

    def test_a_restamped_line_that_breaks_the_rule_is_not_finished(self) -> None:
        """Each line must be the runner's line for its question: its verdict recomputed from its two answers by the
        one verdict rule, its kind, predicate and record those of the preregistered question, ``scored`` from its
        error kind, each question once. Hashes stamped again do not save a line that differs."""

        def first(match: Callable[[dict[str, Any]], bool], change: dict[str, Any]) -> Callable[[list], None]:
            def edit(lines: list[dict[str, Any]]) -> None:
                next(line for line in lines if match(line)).update(change)
            return edit

        cases = {   # one part each, so one aggregate reads them all
            ("a-0p5b", 1): first(lambda x: x["verdict"] == "confirm", {"verdict": "refute"}),
            ("a-0p5b", 2): first(lambda x: x["kind"] == "positive", {"kind": "negative"}),
            ("a-0p5b", 3): first(lambda x: x["kind"] == "negative", {"predicate": "unknown_or_other"}),
            ("a-0p5b", 4): first(lambda x: True, {"record_ref": "999999"}),
            ("a-0p5b", 5): first(lambda x: x["mentions_entity"] == "yes", {"mentions_entity": "maybe"}),
            ("a-0p5b", 6): first(lambda x: True, {"scored": False}),
            ("a-1p5b", 1): first(lambda x: True, {"error_kind": "timeout"}),
            ("a-1p5b", 2): lambda lines: lines.append(dict(lines[0])),
        }
        for (model, part), edit in cases.items():
            self.restamp(model, part, lines=edit)
        models = self.aggregate()["models"]
        for (model, part) in cases:
            with self.subTest(model=model, part=part):
                row = next(p for p in models[model]["parts"] if p["part"] == part)
                self.assertEqual((row["status"], row["ok"]), ("ok", False))
        self.assertEqual((models["a-0p5b"]["parts_ok"], models["a-1p5b"]["parts_ok"]), ([], [3, 4, 5, 6]))
        self.assertEqual(models["a-4b"]["parts_ok"], [1, 2, 3, 4, 5, 6])

    def test_a_restamped_line_that_keeps_the_rule_is_scored_as_written(self) -> None:
        """The check is the rule, not the content: answers and verdict changed together still finish the part, and
        the scores follow them."""

        def refute_first_confirm(lines: list[dict[str, Any]]) -> None:
            line = next(line for line in lines if line["verdict"] == "confirm" and line["kind"] == "positive")
            line.update(describes_predicate="no", verdict="refute")

        before = self.aggregate()["models"]["a-0p5b"]["scores"]["sensitivity"]["value"]
        self.restamp("a-0p5b", 2, lines=refute_first_confirm)
        shutil.rmtree(self.tmp / "R", True)
        entry = self.aggregate()["models"]["a-0p5b"]
        self.assertEqual((entry["complete"], entry["parts_ok"]), (True, [1, 2, 3, 4, 5, 6]))
        self.assertAlmostEqual(entry["scores"]["sensitivity"]["value"], before - 1 / 150, places=12)

    def test_without_the_preregistration(self) -> None:
        (self.tree.root / "plan" / "prereg" / "prereg.json").unlink()
        block = self.aggregate()
        self.assertEqual((block["reason"], block["models"]), (lab_aggregate.PREREG_MISSING, {}))


# --------------------------------------------------------------------------------------------------- request, plan

class RequestTests(unittest.TestCase):
    def blocks(self, **j1_block: Any) -> dict[str, Any]:
        obj = plumbing_min()
        obj["experiments"] = {"j1": j1_block}
        return validate(obj, MANIFEST)["experiments"]

    def error(self, block: dict[str, Any], models: list[str] | None = None) -> tuple[str, str]:
        obj = plumbing_min()
        if models is not None:
            obj["models"] = models
        obj["experiments"] = {"j1": block}
        with self.assertRaises(RequestError) as caught:
            validate(obj, MANIFEST)
        return caught.exception.path, caught.exception.problem

    def test_defaults(self) -> None:
        got = self.blocks(minutes=5, labels=LABELS, parts=2, seed=3)["j1"]
        self.assertEqual(got, {"models": ["fake-a", "fake-b"], "minutes": 5, "labels": LABELS, "parts": 2, "seed": 3,
                               "bootstrap_b": 10000, "bootstrap_seed": 1})

    def test_every_block_error(self) -> None:
        base = {"minutes": 5, "labels": LABELS, "parts": 2, "seed": 1}
        p = "$.experiments.j1"
        cases = [
            ({**base, "extra": 1}, None, (f"{p}.extra", "unknown key")),
            ({k: v for k, v in base.items() if k != "parts"}, None, (f"{p}.parts", "required")),
            ({**base, "models": ["fake-a", "h-a"]}, ["fake-a", "h-a"], (f"{p}.models[1]", NHTSA_HOSTED)),
            (base, ["h-a", "fake-a"], ("$.models[0]", NHTSA_HOSTED)),
            ({**base, "models": ["nope"]}, None, (f"{p}.models[0]", "not one of $.models")),
            ({**base, "minutes": 21}, None,
             (f"{p}.minutes", "exceeds the shard capacity (job_minutes minus the shard overhead)")),
            ({**base, "labels": {"source": "generator", "pack": "device_quality", "n": 40, "seed": 1}}, None,
             (f"{p}.labels.source", J1_LABELS_PROBLEM)),
            ({**base, "labels": {**LABELS, "pack": "device_quality"}}, None,
             (f"{p}.labels.pack", f"nhtsa labels read the vehicle pack {NHTSA_PACK}")),
            ({**base, "labels": {**LABELS, "n": 39}}, None, (f"{p}.labels.n", "must be an int in [40, 2000]")),
            ({**base, "parts": 0}, None, (f"{p}.parts", "must be an int in [1, 30]")),
            ({**base, "parts": 31}, None, (f"{p}.parts", "must be an int in [1, 30]")),
            ({**base, "seed": -1}, None, (f"{p}.seed", "must be an int in [0, 2147483647]")),
            ({**base, "bootstrap_b": 999}, None, (f"{p}.bootstrap_b", "must be an int in [1000, 20000]")),
            ({**base, "bootstrap_seed": "1"}, None, (f"{p}.bootstrap_seed", "must be an int in [0, 2147483647]")),
        ]
        for block, models, want in cases:
            with self.subTest(case=want):
                self.assertEqual(self.error(block, models), want)

    def test_j001_s_settings_give_eighteen_units_in_nine_shards(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="lab-j1-plan-"))
        self.addCleanup(shutil.rmtree, root, True)
        path = write_json(root / "judge-001.json", J001_REQUEST)
        manifest = load_manifest(LAB_MANIFEST)
        plan = build_plan(load_request(path, manifest), manifest, "unknown")
        self.assertEqual((len(plan["units"]), len(plan["shards"])), (18, 9))
        for shard in plan["shards"]:
            self.assertEqual(len(shard["units"]), 2)
            self.assertEqual({u.split("-p")[0] for u in shard["units"]}, {f"j1-{shard['model']}"})
        unit = next(u for u in plan["units"] if u["unit"] == "j1-a-4b-p6")
        self.assertEqual(unit["params"], {"pack": NHTSA_PACK, "labels": {**LABELS, "n": 150}, "endpoint": "a-4b",
                                          "part": 6, "parts": 6, "seed": 1, "bootstrap_b": 10000,
                                          "bootstrap_seed": 1})
        self.assertEqual((unit["minutes"], unit["seeds"], unit["kind"]), (150, [1], "gguf"))


if __name__ == "__main__":
    unittest.main()
