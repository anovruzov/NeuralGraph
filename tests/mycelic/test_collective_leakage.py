"""G0: canary planting, the text-leakage scanner and the G0 runner v1.

Every run here is synthetic and uses a fake model (in process, or a fake OpenAI-compatible server on loopback); no
number printed or asserted here measures a model. Temporary directories only.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from mycelic.collective.edge.extract import TASK_NAME, LexicalExtractor, codes_channel, lexical_handler, pair
from mycelic.collective.edge.verify import JUDGE_TASK, lexical_judge
from mycelic.collective.experiments import g0_canary
from mycelic.collective.inference.fakeserver import FakeOpenAIServer, request_payload
from mycelic.collective.leakage import (CANARY_PREFIX, CANARY_WINDOW, CORE_LETTERS, KNOWN_LIMITATION_NOTE,
                                        MAX_DRAWS, MIN_ID_CANARY, NOT_COVERED, SCOPE, Artifact, LeakageError,
                                        Manifest, collision_corpus, config_strings, plant_canaries, read_manifest,
                                        scan, unescape, write_manifest)
from mycelic.collective.packs.canonical import Canonicaliser, folded, term_regex
from mycelic.collective.packs.generator import generate
from mycelic.collective.packs.loader import BUILTIN_ROOT, FrozenPack, load_pack

ROOT = Path(__file__).resolve().parents[2]
PACK_IDS = ("device_quality", "claims_integrity")
PACKS = {pid: load_pack(pid) for pid in PACK_IDS}
DQ, CI = PACKS["device_quality"], PACKS["claims_integrity"]
LOOPBACK = ("127.0.0.1", "::1", "localhost")
_real_create_connection = socket.create_connection


def _loopback_only(address, *args, **kwargs):
    if address[0] not in LOOPBACK:
        raise OSError(f"test guard: refusing a non-loopback connection to {address[0]}")
    return _real_create_connection(address, *args, **kwargs)


def setUpModule() -> None:
    socket.create_connection = _loopback_only


def tearDownModule() -> None:
    socket.create_connection = _real_create_connection


_WORLDS: dict[str, Any] = {}


def world_records(pack: FrozenPack, n: int = 300) -> tuple[dict[str, Any], ...]:
    if pack.id not in _WORLDS:
        _WORLDS[pack.id] = generate(pack, 3, len(pack.generator["sites"]), 46)
    return _WORLDS[pack.id].records[:n]


def lexicon_regex(pack: FrozenPack) -> Any:
    terms = [folded(t) for p in pack.predicates.values() for lang in p.lexicon for t in p.lexicon[lang]]
    terms += [folded(c) for neg in pack.negation.values() for c in (*neg.pre, *neg.post)]
    terms += [folded(a) for table in pack.aliases.values() for a in table]
    return term_regex(terms)


class ScriptedRng(random.Random):
    """``random()`` and ``choice()`` return scripted values first (a choice must be in the sequence), then fall back
    to a seeded generator; ``cycle`` repeats its choices forever."""

    def __init__(self, floats: Any = (), choices: Any = (), cycle: bool = False) -> None:
        super().__init__(0)
        self.floats = list(floats)
        self.choices = list(choices)
        self.cycle = cycle
        self.i = 0

    def random(self) -> float:
        return self.floats.pop(0) if self.floats else super().random()

    def choice(self, seq: Any) -> Any:
        if self.cycle:
            value = self.choices[self.i % len(self.choices)]
            self.i += 1
            assert value in seq
            return value
        if self.choices:
            value = self.choices.pop(0)
            assert value in seq, (value, seq)
            return value
        return super().choice(seq)


# --------------------------------------------------------------------------------------------------- planting

class CanaryPlantTests(unittest.TestCase):
    def test_every_class_on_both_packs(self) -> None:
        for pack in PACKS.values():
            with self.subTest(pack=pack.id):
                records = world_records(pack)
                before = copy.deepcopy(records)
                planted, manifest = plant_canaries(records, random.Random("plant"), pack)
                self.assertEqual(records, before)
                self.assertEqual(len(planted), len(records))
                by_id = {c.id: c for c in manifest.canaries}
                self.assertEqual(len(by_id), len(manifest.canaries))
                tokens = [c.token.lower() for c in manifest.canaries]
                self.assertEqual(len(set(tokens)), len(tokens))
                for i, a in enumerate(tokens):
                    for j, b in enumerate(tokens):
                        if i != j:
                            self.assertNotIn(a, b)
                counts = {cls: sum(1 for c in manifest.canaries if c.canary_class == cls) for cls in "abc"}
                self.assertGreater(counts["b"], 0)
                self.assertGreater(counts["c"], 0)
                persons = sorted(pack.mapping()["persons"])
                a_by_record: dict[tuple[str, str], str] = {(c.record_ref, c.field): c.token
                                                           for c in manifest.canaries if c.canary_class == "a"}
                b_fields = {(c.record_ref, c.field): c.token for c in manifest.canaries if c.canary_class == "b"}
                reporter_token = {c.token for c in manifest.canaries if c.field == "reporter"}
                seen_reporters = set()
                for original, rec in zip(records, planted):
                    ref = rec["record_ref"]
                    self.assertTrue(rec["narrative"].endswith(a_by_record[(ref, "narrative")] + "."))
                    for f in persons:
                        if (ref, f"persons.{f}") in b_fields:
                            self.assertEqual(rec["persons"][f], b_fields[(ref, f"persons.{f}")])
                        else:
                            self.assertTrue(rec["persons"][f].endswith(a_by_record[(ref, f"persons.{f}")]))
                    if original["reporter"] is not None:
                        prefix, token = rec["reporter"].rsplit(" ", 1)
                        self.assertEqual(prefix, original["reporter"])
                        self.assertIn(token, reporter_token)
                        seen_reporters.add(original["reporter"])
                self.assertEqual(len(reporter_token), len(seen_reporters))
                originals = {}
                for original, rec in zip(records, planted):
                    if original["reporter"] is not None:
                        originals.setdefault(original["reporter"], set()).add(rec["reporter"])
                self.assertTrue(all(len(v) == 1 for v in originals.values()))  # one token per reporter value

    def test_class_a_tokens(self) -> None:
        for pack in PACKS.values():
            with self.subTest(pack=pack.id):
                _, manifest = plant_canaries(world_records(pack), random.Random("a"), pack)
                corpus, windows = collision_corpus(pack)
                canon, lex = Canonicaliser(pack), lexicon_regex(pack)
                used: set[str] = set()
                for c in (c for c in manifest.canaries if c.canary_class == "a"):
                    self.assertEqual(c.token, CANARY_PREFIX + c.core)
                    self.assertTrue(c.core.isalpha() and c.core.islower() and len(c.core) == CORE_LETTERS)
                    self.assertEqual(canon.scan(c.token).mentions, ())
                    self.assertIsNone(lex.search(folded(c.token)))
                    self.assertIsNone(c.entity_type)
                    for i in range(CORE_LETTERS - CANARY_WINDOW + 1):
                        w = c.core[i:i + CANARY_WINDOW]
                        self.assertNotIn(w, windows)
                        self.assertNotIn(w, used)
                        self.assertFalse(set(w) <= set("abcdef"))
                    used.update(c.core[i:i + CANARY_WINDOW] for i in range(CORE_LETTERS - CANARY_WINDOW + 1))

    def test_class_b_is_whole_person_value_mirrored_and_dropped(self) -> None:
        for pack in PACKS.values():
            with self.subTest(pack=pack.id):
                records = world_records(pack)
                planted, manifest = plant_canaries(records, random.Random("b"), pack)
                by_ref = {r["record_ref"]: r for r in planted}
                universe = {i for ids in pack.generator["universe"].values() for i in ids}
                targets = {i for ids in pack.alias_targets().values() for i in ids}
                canon = Canonicaliser(pack)
                lexical = LexicalExtractor(pack, canon)
                corpus, _ = collision_corpus(pack)
                for c in (c for c in manifest.canaries if c.canary_class == "b"):
                    fmt = pack.entity_types[c.entity_type].id_format
                    self.assertIsNotNone(fmt.canonical.fullmatch(c.token))
                    self.assertNotIn(c.token, universe | targets)
                    self.assertNotIn(c.token.lower(), corpus)
                    self.assertGreaterEqual(len(c.token), MIN_ID_CANARY)
                    self.assertRegex(c.token.lower(), "[g-z]")
                    rec = by_ref[c.record_ref]
                    self.assertEqual(rec["persons"][c.field.split(".", 1)[1]], c.token)
                    self.assertIn(f" {c.token}.", rec["narrative"])
                    result = lexical.extract(rec, codes_channel(rec, pack, canon))
                    self.assertNotIn(c.token, {x.entity_id for x in result.claims})
                    self.assertGreaterEqual(result.drops["person_value"], 1)
                    self.assertNotIn(c.token, {x.entity_id for x in pair(codes_channel(rec, pack, canon), result)})

    def test_class_c_is_a_narrative_only_mention_and_a_claim(self) -> None:
        for pack in PACKS.values():
            with self.subTest(pack=pack.id):
                planted, manifest = plant_canaries(world_records(pack), random.Random("c"), pack)
                by_ref = {r["record_ref"]: r for r in planted}
                canon = Canonicaliser(pack)
                lexical = LexicalExtractor(pack, canon)
                paired = 0
                for c in (c for c in manifest.canaries if c.canary_class == "c"):
                    rec = by_ref[c.record_ref]
                    self.assertEqual(c.field, "narrative")
                    for value in [*rec["persons"].values(), rec["reporter"]]:
                        self.assertNotIn(c.token, value or "")
                    (sentence,) = [s for s in rec["narrative"].split("\n") if c.token in s]
                    self.assertTrue(sentence.endswith(f" {c.token}."))
                    mentions = canon.scan(sentence).mentions
                    self.assertEqual([(m.entity_type, m.entity_id) for m in mentions], [(c.entity_type, c.token)])
                    codes = codes_channel(rec, pack, canon)
                    result = lexical.extract(rec, codes)
                    self.assertIn((c.entity_type, c.token, None), {(x.entity_type, x.entity_id, x.predicate)
                                                                   for x in result.claims})
                    paired += c.token in {x.entity_id for x in pair(codes, result)}
                self.assertGreater(paired, 0)

    def test_short_id_formats_are_never_used(self) -> None:
        _, manifest = plant_canaries(world_records(DQ), random.Random("s"), DQ)
        types = {c.entity_type for c in manifest.canaries if c.canary_class != "a"}
        self.assertEqual(types, {"lot", "product"})                # the supplier format (V + 4 digits) is too short

    def test_collision_redraw_keeps_the_second_draw(self) -> None:
        record = world_records(DQ, 1)
        clean = "qmwzrkvjxtpl"
        self.assertIn("investig", collision_corpus(DQ)[1])         # from the mapping's narrative filter
        rng = ScriptedRng(floats=[0.9], choices=list("investigatio") + list(clean))
        _, manifest = plant_canaries(record, rng, DQ)
        first = manifest.canaries[0]
        self.assertEqual((first.canary_class, first.core, first.field), ("a", clean, "persons.clinician_name"))
        choices = ["patient_ref", "lot", 5, *"10001", 0, 6, *"987654", 1, "Q"]
        _, manifest = plant_canaries(record, ScriptedRng(floats=[0.1], choices=choices), DQ)
        self.assertEqual((manifest.canaries[0].canary_class, manifest.canaries[0].token), ("b", "L987654Q"))

    def test_draw_exhaustion_raises(self) -> None:
        record = world_records(DQ, 1)
        with self.assertRaises(LeakageError):
            plant_canaries(record, ScriptedRng(floats=[0.9], choices=list("investigatio"), cycle=True), DQ)
        with self.assertRaises(LeakageError):
            plant_canaries(record, ScriptedRng(floats=[0.1], choices=["patient_ref", "lot"]
                                               + [5, *"10001", 0] * (MAX_DRAWS + 1)), DQ)

    def test_manifest_round_trip_and_determinism(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outs = []
            for i in range(2):
                _, manifest = plant_canaries(world_records(DQ, 50), random.Random("same"), DQ)
                written = write_manifest(manifest, Path(tmp) / f"m{i}" / "manifest.json")
                self.assertEqual(written.path, (Path(tmp) / f"m{i}" / "manifest.json").resolve())
                self.assertEqual(read_manifest(written.path), written)
                outs.append(written.path.read_bytes())
            self.assertEqual(outs[0], outs[1])
            obj = json.loads(outs[0])
            self.assertEqual((obj["kind"], obj["schema_version"], obj["prefix"]), ("g0_canary_manifest", 1, "Qzxv"))
            bad_cases = [b"", b"{}", b"[]", outs[0].replace(b'"g0_canary_manifest"', b'"other"')]
            changed = json.loads(outs[0])
            changed["canaries"][0]["id"] = "z-000001"
            bad_cases.append(json.dumps(changed).encode())
            changed = json.loads(outs[0])
            changed["canaries"].append(changed["canaries"][0])
            bad_cases.append(json.dumps(changed).encode())
            for i, data in enumerate(bad_cases):
                path = Path(tmp) / f"bad{i}.json"
                path.write_bytes(data)
                with self.subTest(case=i), self.assertRaises(LeakageError):
                    read_manifest(path)
            with self.assertRaises(LeakageError):
                read_manifest(Path(tmp) / "absent.json")


# --------------------------------------------------------------------------------------------------- scanning

NARRATIVE_EN = "The left housing seam opened while the unit stood on the trolley overnight."
NARRATIVE_DE = "Das Gehäuse öffnete sich über Nacht, während das Gerät auf dem Wagen stand."


class ScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.planted, cls.manifest = plant_canaries(world_records(DQ, 40), random.Random("scan"), DQ)
        cls.a = next(c for c in cls.manifest.canaries if c.canary_class == "a")
        cls.ids = [c for c in cls.manifest.canaries if c.canary_class != "a"]
        cls.narratives = [NARRATIVE_EN, NARRATIVE_DE, "Kurz."]

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def report(self, *items: Any, narratives: Any = None, pack: FrozenPack = DQ, manifest: Manifest | None = None,
               hygiene: Any = ()) -> dict[str, Any]:
        artifacts = [x if isinstance(x, Artifact) else
                     Artifact("probe", f"item{i}", data=x.encode("utf-8") if isinstance(x, str) else x)
                     for i, x in enumerate(items)]
        return scan(artifacts, manifest or self.manifest, self.narratives if narratives is None else narratives, pack,
                    hygiene=hygiene)

    def only_hit(self, report: dict[str, Any]) -> dict[str, Any]:
        self.assertEqual(len(report["hits"]), 1, report["hits"])
        return report["hits"][0]

    def test_tokens_are_found_in_any_case(self) -> None:
        token = self.a.token
        for variant in (token, token.upper(), token.title(), token.swapcase(), token[:6].upper() + token[6:]):
            with self.subTest(variant=variant):
                hit = self.only_hit(self.report(f"x {variant} y"))
                self.assertEqual((hit["canary_id"], hit["match"], hit["view"], hit["canary_class"]),
                                 (self.a.id, "token", "raw", "a"))

    def test_an_8_letter_core_window_is_found_and_7_are_not(self) -> None:
        core = self.a.core
        hit = self.only_hit(self.report("..." + core[2:2 + CANARY_WINDOW].upper() + "..."))
        self.assertEqual((hit["canary_id"], hit["match"]), (self.a.id, "core_window"))
        self.assertEqual(self.report("..." + core[2:2 + CANARY_WINDOW - 1] + "...")["hits"], [])

    def test_escaped_forms_are_found_unescaped(self) -> None:
        token = self.a.token
        lower = "".join(f"\\u{ord(ch):04x}" for ch in token)
        upper = "".join(f"\\u{ord(ch):04X}" for ch in token)
        mid = token[:5] + f"\\u{ord(token[5]):04x}" + token[6:]
        double = json.dumps(lower)
        for form in (lower, upper, mid, double):
            with self.subTest(form=form[:30]):
                hit = self.only_hit(self.report(form))
                self.assertEqual((hit["canary_id"], hit["match"], hit["view"]), (self.a.id, "token", "unescaped"))
        hit = self.only_hit(self.report(json.dumps({"note": "Größe " + token + " ü"}, ensure_ascii=True)))
        self.assertEqual((hit["canary_id"], hit["match"]), (self.a.id, "token"))
        self.assertEqual(unescape("\\ud83d\\ude00 \\ud800 \\\\u0041 \\n"), "\U0001F600 � \\u0041 \n")

    def test_id_tokens_are_found_case_insensitively(self) -> None:
        for c in self.ids[:4]:
            for variant in (c.token, c.token.lower(), c.token.swapcase()):
                with self.subTest(token=c.id, variant=variant):
                    hit = self.only_hit(self.report(f"id={variant};"))
                    self.assertEqual((hit["canary_id"], hit["canary_class"], hit["match"]), (c.id, c.canary_class,
                                                                                              "token"))

    def test_shingles(self) -> None:
        excerpt = NARRATIVE_EN[5:35]
        self.assertEqual(self.report(excerpt)["shingle_overlap_bytes"], 30)
        escaped = self.report("".join(f"\\u{ord(ch):04x}" for ch in excerpt))
        self.assertEqual((escaped["shingle_overlap_bytes"], escaped["shingle_hits"][0]["view"]), (30, "unescaped"))
        german = NARRATIVE_DE[0:34]
        report = self.report(json.dumps(german, ensure_ascii=True))
        self.assertEqual(report["shingle_overlap_bytes"], len(german.encode("utf-8")))
        self.assertGreater(len(german.encode("utf-8")), len(german))
        self.assertEqual(self.report(NARRATIVE_EN[5:28])["shingle_overlap_bytes"], 0)
        twice = self.report(excerpt + " and " + excerpt)
        self.assertEqual(twice["shingle_overlap_bytes"], 60)
        self.assertEqual(report["shingles"], {"window_chars": 24, "narratives": 3, "short_narratives": 1,
                                              "windows_indexed": report["shingles"]["windows_indexed"],
                                              "windows_excluded_as_vocabulary": 0})
        self.assertEqual(report["shingles"]["windows_indexed"], (len(NARRATIVE_EN) - 23) + (len(NARRATIVE_DE) - 23))

    def test_negative_control_c_and_vocabulary_exclusion(self) -> None:
        excerpt = NARRATIVE_EN[10:40]
        report = self.report(Artifact("probe", "leak.txt", data=f"note: {excerpt}".encode()))
        self.assertEqual(report["shingle_hits"], [{"artifact_class": "probe", "file": "leak.txt", "bytes": 30,
                                                   "view": "raw"}])
        phrase = DQ.disclaimer[:30]
        narratives = [f"Operator wrote: {phrase}, and nothing else of note."]
        report = self.report(f"<{phrase}>", narratives=narratives)     # only the config phrase itself is shared
        self.assertEqual(report["shingle_overlap_bytes"], 0)
        self.assertGreaterEqual(report["excluded_vocabulary_windows"], 7)
        self.assertGreaterEqual(report["shingles"]["windows_excluded_as_vocabulary"], 7)

    def test_the_manifest_is_never_scanned(self) -> None:
        manifest = write_manifest(self.manifest, self.tmp / "private" / "manifest.json")
        scanned = self.tmp / "scanned"
        scanned.mkdir()
        (scanned / "ok.txt").write_text("clean", encoding="utf-8")
        for path in (manifest.path, self.tmp / "private", self.tmp):
            with self.subTest(path=path), self.assertRaises(LeakageError):
                self.report(Artifact("probe", "p", path=path), manifest=manifest)
        self.report(Artifact("probe", "p", path=scanned), manifest=manifest)
        os.symlink(manifest.path, scanned / "link.json")
        with self.assertRaises(LeakageError):
            self.report(Artifact("probe", "p", path=scanned), manifest=manifest)
        with self.assertRaises(LeakageError):
            self.report(Artifact("probe", "p", path=self.tmp / "absent"), manifest=manifest)
        with self.assertRaises(ValueError):
            Artifact("Bad Class", "p", data=b"")
        with self.assertRaises(ValueError):
            Artifact("probe", "p", path=self.tmp, data=b"")
        with self.assertRaises(ValueError):
            Artifact("probe", "p")

    def test_negative_control_b_plain_and_escaped_files(self) -> None:
        d = self.tmp / "out"
        d.mkdir()
        c = self.ids[0]
        (d / "plain.txt").write_bytes(b"x " + self.a.token.encode() + b" y")
        (d / "esc.json").write_text(json.dumps({"v": "".join(f"\\u{ord(ch):04x}" for ch in c.token)}),
                                    encoding="utf-8")
        report = self.report(Artifact("probe", "out", path=d))
        found = {(h["file"], h["canary_id"]) for h in report["hits"]}
        self.assertEqual(found, {("out/plain.txt", self.a.id), ("out/esc.json", c.id)})
        self.assertEqual(report["artifact_classes"], {"probe": {"bytes": sum(p.stat().st_size for p in d.iterdir()),
                                                                "items": 2}})

    def test_binary_and_wal_files_are_scanned(self) -> None:
        garbage = bytes(range(256)) * 3
        report = self.report(garbage + self.a.token.encode() + b"\xff\xfe\x00" + garbage)
        self.assertEqual(self.only_hit(report)["canary_id"], self.a.id)
        d = self.tmp / "hq"
        d.mkdir()
        (d / "x.sqlite3-wal").write_bytes(garbage + self.ids[1].token.lower().encode() + garbage)
        report = self.report(Artifact("hq_receive_log", "hq", path=d))
        hit = self.only_hit(report)
        self.assertEqual((hit["file"], hit["canary_id"]), ("hq/x.sqlite3-wal", self.ids[1].id))

    def test_hygiene_artifacts_are_reported_apart(self) -> None:
        report = self.report("clean", hygiene=[Artifact("site_ledger_hygiene", "ledger",
                                                        data=f"{self.a.token} {NARRATIVE_EN}".encode())])
        self.assertEqual((report["hits"], report["shingle_overlap_bytes"]), ([], 0))
        hygiene = report["site_ledger_hygiene"]
        self.assertEqual([h["canary_id"] for h in hygiene["hits"]], [self.a.id])
        self.assertEqual(hygiene["shingle_overlap_bytes"], len(NARRATIVE_EN))
        self.assertNotIn("site_ledger_hygiene", report["artifact_classes"])

    def test_scope_and_not_covered(self) -> None:
        report = self.report("clean")
        self.assertEqual(report["scope"], SCOPE)
        self.assertEqual(SCOPE, "text-only")
        self.assertEqual(report["not_covered"], list(NOT_COVERED))
        self.assertGreaterEqual(sum("X5" in item for item in NOT_COVERED), 4)
        for needle in ("Attribute inference", "Membership inference", "'<k'", "base64", "SQLite pages"):
            self.assertTrue(any(needle in item for item in NOT_COVERED), needle)
        self.assertEqual((report["known_limitation"], report["known_limitation_note"]), ([], None))

    def test_known_limitation_only_for_class_c_without_master_data(self) -> None:
        planted, manifest = plant_canaries(world_records(CI, 40), random.Random("kl"), CI)
        a = next(c for c in manifest.canaries if c.canary_class == "a")
        b = next(c for c in manifest.canaries if c.canary_class == "b")
        c = next(c for c in manifest.canaries if c.canary_class == "c")
        text = f"{a.token} {b.token} {c.token}"
        report = scan([Artifact("cells", "cells#1", data=text.encode())], manifest, [], CI)
        self.assertEqual(sorted(h["canary_id"] for h in report["hits"]), sorted([a.id, b.id]))
        self.assertEqual(report["known_limitation"], [{"canary_id": c.id, "artifact_class": "cells",
                                                       "file": "cells#1"}])
        self.assertEqual(report["known_limitation_note"], KNOWN_LIMITATION_NOTE)
        dq_c = next(x for x in self.manifest.canaries if x.canary_class == "c")
        report = self.report(dq_c.token)
        self.assertEqual((self.only_hit(report)["canary_id"], report["known_limitation"]), (dq_c.id, []))

    def test_a_pack_edited_after_loading_is_refused(self) -> None:
        dest = self.tmp / "pack"
        shutil.copytree(BUILTIN_ROOT / "device_quality", dest)
        pack = load_pack(dest)
        self.assertTrue(config_strings(pack))
        egress = json.loads((dest / "egress.json").read_text(encoding="utf-8"))
        egress["k"] = 4
        (dest / "egress.json").write_text(json.dumps(egress), encoding="utf-8")
        with self.assertRaises(LeakageError):
            config_strings(pack)
        with self.assertRaises(LeakageError):
            scan([], self.manifest, [], pack)
        (dest / "egress.json").write_text("{", encoding="utf-8")
        with self.assertRaises(LeakageError):
            config_strings(pack)

    def test_a_manifest_of_another_pack_config_is_refused(self) -> None:
        with self.assertRaises(LeakageError):
            scan([], self.manifest, [], CI)

    def test_the_report_holds_no_token(self) -> None:
        tokens = [c.token for c in self.manifest.canaries[:30]]
        report = self.report(" ".join(tokens), hygiene=[Artifact("site_ledger_hygiene", "h",
                                                                  data=" ".join(tokens).encode())])
        self.assertGreater(report["hit_count"], 0)
        text = json.dumps(report).lower()
        for c in self.manifest.canaries:
            self.assertNotIn(c.token.lower(), text)


# --------------------------------------------------------------------------------------------------- the runner

def run_main(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = g0_canary.main(argv)
    return code, out.getvalue(), err.getvalue()


def args(pack: str, out: Path, *extra: str, records: int = 1000, seed: int = 11) -> list[str]:
    return ["--pack", pack, "--records", str(records), "--seed", str(seed), "--out", str(out), *extra]


class G0RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.runs: dict[str, tuple[int, str, Path]] = {}
        out = cls.tmp / "dq-on"
        r = subprocess.run([sys.executable, "-m", "mycelic.collective.experiments.g0_canary",
                            *args("device_quality", out, "--require-master-data", "on")], cwd=ROOT,
                           capture_output=True, text=True, timeout=600, env={**os.environ, "PYTHONPATH": str(ROOT)})
        cls.runs["dq-on"] = (r.returncode, r.stdout + r.stderr, out)
        for name, argv in (("ci-on", args("claims_integrity", cls.tmp / "ci-on", "--require-master-data", "on")),
                           ("dq-off", args("device_quality", cls.tmp / "dq-off", "--require-master-data", "off")),
                           ("ci-default", args("claims_integrity", cls.tmp / "ci-default", records=300)),
                           ("dq-lexical", args("device_quality", cls.tmp / "dq-lexical", "--mode", "lexical",
                                               records=300))):
            code, stdout, stderr = run_main(argv)
            cls.runs[name] = (code, stdout + stderr, cls.tmp / name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def result(self, name: str) -> dict[str, Any]:
        code, output, out = self.runs[name]
        self.assertEqual(code, 0, output)
        return json.loads((out / "leakage.json").read_text(encoding="utf-8"))

    def assert_no_token(self, name: str) -> None:
        code, output, out = self.runs[name]
        manifest = read_manifest(out / "private" / "manifest.json")
        text = ((out / "leakage.json").read_text(encoding="utf-8") + output).lower()
        for c in manifest.canaries:
            self.assertNotIn(c.token.lower(), text)

    def test_master_data_on_for_both_packs(self) -> None:
        for name, pid in (("dq-on", "device_quality"), ("ci-on", "claims_integrity")):
            with self.subTest(run=name):
                d = self.result(name)
                self.assertEqual((d["kind"], d["pack"], d["synthetic"], d["data_label"]),
                                 ("g0_leakage", pid, True, "synthetic"))
                self.assertGreaterEqual(d["canaries_planted"], 2000)
                self.assertTrue(all(d["canaries_by_class"][c] > 0 for c in "abc"))
                self.assertEqual((d["hits"], d["hit_count"], d["shingle_overlap_bytes"]), ([], 0, 0))
                self.assertEqual((d["scope"], d["known_limitation"], d["known_limitation_note"]),
                                 ("text-only", [], None))
                self.assertTrue(d["not_covered"])
                self.assertRegex(d["config_hash"], "^[0-9a-f]{64}$")
                for cls in ("cells", "site_egress_log", "hq_receive_log", "usage_summary"):
                    self.assertGreater(d["artifact_classes"][cls]["bytes"], 0, cls)
                self.assertEqual((d["site_ledger_hygiene"]["hits"], d["site_ledger_hygiene"]["shingle_overlap_bytes"]),
                                 ([], 0))
                self.assertGreater(d["site_ledger_hygiene"]["bytes"], 0)
                self.assertGreater(d["positive_control"]["canary_hits"], 0)
                self.assertGreater(d["positive_control"]["shingle_overlap_bytes"], 0)
                self.assertTrue(d["passed"])
                self.assertEqual((d["records"], d["mode"], d["models_fake"], d["require_master_data"]),
                                 (1000, "fake", True, True))
                self.assertEqual(d["edge_totals"]["records_ingested"], 1000)
                for item in d["scanned"]:
                    self.assertFalse(item["label"].startswith("private"), item)
                    # a site's own database never crosses; HQ's collective store (G6 pushdown stage) does
                    self.assertIsNone(re.search(r"edge/site-.*\.sqlite3", item["label"]), item)
                self.assertIn("hqdb/collective.sqlite3", {item["label"] for item in d["scanned"]})
                self.assert_no_token(name)
                code, output, _ = self.runs[name]
                self.assertIn(f"g0: pack={pid} canaries={d['canaries_planted']} hits=0 shingle_overlap_bytes=0 "
                              f"known_limitation=0 -> PASS", output)

    def test_master_data_off_lists_class_c_under_the_known_limitation(self) -> None:
        d = self.result("dq-off")
        self.assertTrue(d["known_limitation"])
        self.assertEqual({k["canary_id"][0] for k in d["known_limitation"]}, {"c"})
        self.assertEqual((d["hits"], d["shingle_overlap_bytes"], d["known_limitation_note"]),
                         ([], 0, KNOWN_LIMITATION_NOTE))
        self.assertNotEqual(d["base_config_hash"], d["config_hash"])
        self.assertEqual((d["require_master_data"], d["require_master_data_overridden"]), (False, True))
        self.assertEqual(d["base_config_hash"], DQ.config_hash)
        copied = load_pack(self.runs["dq-off"][2] / "pack")
        self.assertEqual(copied.config_hash, d["config_hash"])
        self.assertTrue(d["passed"])
        self.assert_no_token("dq-off")

    def test_the_default_follows_the_pack(self) -> None:
        ci, dq = self.result("ci-default"), self.result("dq-on")
        self.assertEqual((ci["require_master_data"], ci["require_master_data_overridden"], ci["config_hash"],
                          ci["base_config_hash"]), (False, False, CI.config_hash, CI.config_hash))
        self.assertTrue(ci["known_limitation"])
        self.assertEqual((dq["require_master_data"], dq["require_master_data_overridden"], dq["config_hash"]),
                         (True, False, DQ.config_hash))
        self.assertFalse((self.runs["dq-on"][2] / "pack").exists())
        on = self.result("ci-on")
        self.assertEqual((on["require_master_data_overridden"], on["base_config_hash"]), (True, CI.config_hash))

    def test_lexical_mode_sends_no_usage(self) -> None:
        d = self.result("dq-lexical")
        self.assertEqual(d["artifact_classes"]["usage_summary"], {"bytes": 0, "items": 0})
        self.assertEqual((d["mode"], d["models_fake"], d["site_ledger_hygiene"]["bytes"]), ("lexical", None, 0))
        self.assertFalse(list((self.runs["dq-lexical"][2] / "edge").glob("*.ledger.jsonl")))
        self.assertTrue(d["passed"])

    def test_routing_mode_against_a_fake_server(self) -> None:
        # one server answers both routed tasks (G6), dispatching on the payload's shape: an extraction payload has
        # "text", a judge payload has "question"
        canonicaliser = Canonicaliser(DQ)
        extract, judge = lexical_handler(DQ, canonicaliser), lexical_judge(DQ, canonicaliser)

        def respond(request: Any) -> Any:
            payload = request_payload(request)
            return judge(payload) if "question" in payload else extract(payload)

        srv = FakeOpenAIServer("valid", responder=respond).start()
        self.addCleanup(srv.stop)
        routing = self.tmp / "routing.json"
        routes = {TASK_NAME: {"endpoint": "sim"}, JUDGE_TASK: {"endpoint": "sim"}}
        routing.write_text(json.dumps({"schema_version": 1, "endpoints": {"sim": {
            "provider": "openai_compat", "boundary": "any-simulated", "base_url": srv.base_url, "model": "m-tag"}},
            "routes": routes}), encoding="utf-8")
        out = self.tmp / "routing-run"
        code, output, _ = run_main(args("device_quality", out, "--mode", "routing", "--routing", str(routing),
                                        records=60))
        self.assertEqual(code, 0, output)
        d = json.loads((out / "leakage.json").read_text(encoding="utf-8"))
        self.assertEqual((d["mode"], d["models_fake"], d["passed"]), ("routing", False, True))
        payloads = [request_payload(r["json"]) for r in srv.chat_requests]
        self.assertEqual(sum(1 for p in payloads if "text" in p and "question" not in p), 60)
        self.assertGreaterEqual(sum(1 for p in payloads if "question" in p), 1)
        self.assertEqual(len(payloads), sum(1 for p in payloads if ("question" in p) != ("text" in p)))
        self.assertGreater(d["artifact_classes"]["usage_summary"]["bytes"], 0)
        for boundary, provider in (("site:plant-ashvale", "openai_compat"), ("any-simulated", "fake")):
            spec = {"provider": provider, "boundary": boundary}
            if provider == "openai_compat":
                spec.update(base_url=srv.base_url, model="m-tag")
            routing.write_text(json.dumps({"schema_version": 1, "endpoints": {"sim": spec}, "routes": routes}),
                               encoding="utf-8")
            target = self.tmp / f"refused-{provider}"
            code, output, _ = run_main(args("device_quality", target, "--mode", "routing", "--routing",
                                            str(routing), records=10))
            self.assertEqual(code, 2, output)
            self.assertFalse(target.exists())

    def test_usage_errors_exit_2_and_write_nothing(self) -> None:
        busy = self.tmp / "busy"
        busy.mkdir()
        (busy / "keep.txt").write_text("x", encoding="utf-8")
        cases = [args("device_quality", busy), args("device_quality", self.tmp / "n0", records=0),
                 args("device_quality", self.tmp / "n1", records=100001),
                 args("device_quality", self.tmp / "s", seed=-1),
                 args("device_quality", self.tmp / "r", "--routing", str(self.tmp / "x.json")),
                 args("device_quality", self.tmp / "m", "--mode", "routing"),
                 args("no_such_pack", self.tmp / "p")]
        for argv in cases:
            with self.subTest(argv=argv[-4:]):
                before = sorted(p.name for p in self.tmp.iterdir())
                code, _, _ = run_main(argv)
                self.assertEqual(code, 2)
                self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), before)
        self.assertEqual(sorted(p.name for p in busy.iterdir()), ["keep.txt"])

    def test_dry_run_creates_nothing(self) -> None:
        before = sorted(str(p) for p in self.tmp.rglob("*"))
        for argv, expected in ((args("device_quality", self.tmp / "dry", "--dry-run"), 0),
                               (args(str(self.tmp / "missing-pack"), self.tmp / "dry", "--dry-run"), 0),
                               (args("device_quality", self.tmp / "dry", "--mode", "routing", "--routing",
                                     str(self.tmp / "missing.json"), "--dry-run"), 0),
                               (args("device_quality", self.tmp / "dq-on", "--dry-run"), 2)):
            with self.subTest(argv=argv):
                code, output, _ = run_main(argv)
                self.assertEqual(code, expected, output)
                if expected == 0:
                    self.assertTrue(output.startswith("dry-run: experiments.g0_canary"), output)
        self.assertEqual(sorted(str(p) for p in self.tmp.rglob("*")), before)

    def test_a_leaky_stage_fails_the_run(self) -> None:
        def leaky(ctx: g0_canary.G0Context) -> tuple[list, list]:
            narrative = ctx.records[0]["narrative"]
            (ctx.out / "hq" / "leak.txt").write_text(f"oops: {narrative}", encoding="utf-8")
            return [], []

        stages = g0_canary.STAGES + (g0_canary.Stage("leaky", leaky),)
        out = self.tmp / "leaky"
        with mock.patch.object(g0_canary, "STAGES", stages):
            code, output, _ = run_main(args("device_quality", out, records=200))
        self.assertEqual(code, 1, output)
        d = json.loads((out / "leakage.json").read_text(encoding="utf-8"))
        self.assertFalse(d["passed"])
        self.assertTrue(d["hits"])
        self.assertEqual({h["file"] for h in d["hits"]}, {"hq/leak.txt"})
        self.assertGreater(d["shingle_overlap_bytes"], 0)
        self.assertEqual(d["stages"], ["edge", "pushdown", "followup", "leaky"])          # G7 added followup
        self.assertIn("-> FAIL", output)


if __name__ == "__main__":
    unittest.main()
