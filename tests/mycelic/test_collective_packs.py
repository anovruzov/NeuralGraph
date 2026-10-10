"""Domain packs: loading, freezing and hashing, malformed packs, id formats, the canonicaliser, the disclaimer, the
seeded world generator and the connector. Everything runs on the built-in packs (every generic check on each of
them, B4) or on copies of them in a temporary directory; nothing touches the network.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable

from mycelic.collective import schemacheck
from mycelic.collective.edge.extract import LexicalExtractor, codes_channel
from mycelic.collective.jsonio import canonical_dumps, strict_load
from mycelic.collective.packs import canonical, connector, generator, loader
from mycelic.collective.packs.canonical import Canonicaliser, IdFormatError, compile_id_format, fold_phrase, normalise
from mycelic.collective.packs.connector import ConnectorError, check_record, map_rows, read_jsonl
from mycelic.collective.packs.generator import GeneratorError, generate, world_digest
from mycelic.collective.packs.loader import (BUILTIN_ROOT, FILES, HASH_SCOPES, PackError, RESERVED, compute_hashes,
                                             load_pack, load_pack_dir)
from tests.mycelic.test_collective_guards import BUILTIN_PACKS

ROOT = Path(__file__).resolve().parents[2]
PACKS = BUILTIN_PACKS
# per built-in pack: k and the verdict count buckets
EGRESS_EXPECTED = {"device_quality": (3, [3, 10, 50]), "claims_integrity": (5, [5, 10, 50]),
                   "it_incidents": (3, [3, 10, 50])}
HEX64 = re.compile(r"[0-9a-f]{64}")
ID = re.compile(r"[a-z][a-z0-9_]{1,40}")
_LOADED: dict[str, loader.FrozenPack] = {}


def pack(pid: str) -> loader.FrozenPack:
    if pid not in _LOADED:
        _LOADED[pid] = load_pack(pid)
    return _LOADED[pid]


class TempCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def copy(self, pid: str = "device_quality", name: str | None = None) -> Path:
        dest = self.dir / (name or pid)
        shutil.copytree(BUILTIN_ROOT / pid, dest)
        return dest

    @staticmethod
    def edit(directory: Path, name: str, fn: Callable[[Any], Any]) -> None:
        path = directory / name
        obj = json.loads(path.read_text(encoding="utf-8"))
        out = fn(obj)
        path.write_text(json.dumps(obj if out is None else out, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def edit_fixtures(directory: Path, fn: Callable[[list], Any]) -> None:
        path = directory / "fixtures" / "records.jsonl"
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        out = fn(lines)
        lines = lines if out is None else out
        path.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines), encoding="utf-8")


# =================================================================================================== loading

class LoadTests(TempCase):
    def test_builtins_load_with_four_hashes(self) -> None:
        for pid in PACKS:
            with self.subTest(pack=pid):
                p = pack(pid)
                self.assertEqual((p.id, p.source), (pid, "builtin"))
                self.assertEqual(set(p.hashes()), {"config_hash", "vocabulary_hash", "detector_hash", "fixtures_hash"})
                for value in p.hashes().values():
                    self.assertRegex(value, HEX64)
                self.assertEqual(len(set(p.hashes().values())), 4)

    def test_a_path_copy_has_source_path_and_equal_hashes(self) -> None:
        for pid in PACKS:
            with self.subTest(pack=pid):
                copied = load_pack(str(self.copy(pid)))
                self.assertEqual(copied.source, "path")
                self.assertEqual(copied.hashes(), pack(pid).hashes())
                self.assertEqual(load_pack_dir(self.copy(pid, name=f"{pid}-renamed")).hashes(), pack(pid).hashes())

    def test_unknown_builtin_and_wrong_expected_id(self) -> None:
        with self.assertRaises(PackError) as ctx:
            load_pack("no_such_pack")
        self.assertEqual((ctx.exception.file, ctx.exception.path), ("pack", "$"))
        self.assertIn("unknown built-in pack no_such_pack", str(ctx.exception))
        with self.assertRaises(PackError) as ctx:
            load_pack_dir(self.copy(), expected_id="other")
        self.assertEqual(str(ctx.exception), "pack.json: $.id: pack id differs from directory name")

    def test_dotfiles_are_ignored(self) -> None:
        d = self.copy()
        (d / ".DS_Store").write_bytes(b"\x00junk")
        (d / ".git").mkdir()
        (d / "fixtures" / ".hidden").write_text("x")
        self.assertEqual(load_pack(str(d)).hashes(), pack("device_quality").hashes())

    def test_every_cross_reference_resolves(self) -> None:
        for pid in PACKS:
            p = pack(pid)
            with self.subTest(pack=pid):
                for code in p.codes.values():
                    self.assertIn(code.predicate, p.predicates)
                for rule in p.rules.values():
                    self.assertIn(rule.entity_type, p.entity_types)
                    self.assertIn(rule.predicate, p.predicates)
                    self.assertGreaterEqual(rule.window_weeks, p.egress.min_window_weeks)
                for q in p.questions.values():
                    self.assertTrue(set(q.entity_types) <= set(p.entity_types))
                    self.assertTrue(set(q.predicates or ()) <= set(p.predicates))
                for ft in p.followups.values():
                    self.assertIn(ft.owner_role, p.roles)
                    self.assertIn(ft.escalate_to_role, (None, *p.roles))
                canon = Canonicaliser(p)
                for t, table in p.aliases.items():
                    for target in table.values():
                        self.assertTrue(canon.is_canonical(t, target))
                for name, m in p.mappings.items():
                    self.assertIn(m["primary_entity_type"], m["entities"])
                    for spec in m["codes"]:
                        self.assertTrue(set((spec["value_map"] or {}).values()) <= set(p.codes))
                self.assertEqual(set(p.egress.egress_entity_types),
                                 {t for t, et in p.entity_types.items() if et.egress})

    def test_egress_and_detector_values(self) -> None:
        for pid, (k, buckets) in EGRESS_EXPECTED.items():
            p = pack(pid)
            with self.subTest(pack=pid):
                self.assertEqual(p.egress.k, k)
                self.assertEqual(list(p.egress.verdict_count_buckets), buckets)
                self.assertEqual(p.egress.verdict_count_buckets[0], p.egress.k)
                self.assertEqual(p.egress.suppress_below_days, 31)
                self.assertGreaterEqual(p.egress.min_window_weeks, 5)
                self.assertTrue(set(p.egress.central_allowed_fields).isdisjoint(p.egress.never_fields))
                for field in ("narrative", "reporter", *(f"persons.{f}" for f in p.mapping()["persons"])):
                    self.assertIn(field, p.egress.never_fields)

    def test_no_t3_and_no_enabled_t2(self) -> None:
        for pid in PACKS:
            for ft in pack(pid).followups.values():
                with self.subTest(pack=pid, followup=ft.id):
                    self.assertIn(ft.tier, ("T0", "T1", "T2"))
                    self.assertFalse(ft.tier == "T2" and ft.enabled)

    def test_every_gold_fixture_claim_resolves_exactly(self) -> None:
        for pid in PACKS:
            p = pack(pid)
            canon = Canonicaliser(p)
            self.assertGreaterEqual(len(p.fixtures), 40)
            for fx in p.fixtures:
                for g in fx.gold:
                    with self.subTest(pack=pid, record=fx.record["record_ref"], claim=g):
                        m = canon.resolve_exact(g.entity_type, g.entity_id)
                        self.assertEqual((m.entity_id, m.method), (g.entity_id, "exact"))
                        self.assertIn(g.predicate, (None, *p.predicates))

    def test_pack_ids_match_the_id_regex_and_are_not_reserved(self) -> None:
        for pid in PACKS:
            p = pack(pid)
            ids = [p.id, *p.entity_types, *p.predicates, *p.roles, *p.questions, *p.followups, *p.rules]
            for value in ids:
                with self.subTest(pack=pid, id=value):
                    self.assertRegex(value, ID)
                    self.assertNotIn(value, RESERVED)

    def test_every_universe_id_resolves_to_exactly_one_type(self) -> None:
        for pid in PACKS:
            p = pack(pid)
            canon = Canonicaliser(p)
            for t, ids in p.generator["universe"].items():
                for eid in ids:
                    with self.subTest(pack=pid, id=eid):
                        hits = [u for u in p.entity_types if canon.resolve_exact(u, eid) is not None]
                        self.assertEqual(hits, [t])
                        self.assertEqual(canon.resolve_exact(t, eid).entity_id, eid)

    def test_reserved_holds_the_generic_vocabulary_and_is_the_same_without_site(self) -> None:
        for word in ("confirm", "text_only", "record_ref", "entity_id", "label", "alnum", "class", "match", "len",
                     "exit", "help", "id", "type", "codes", "narrative"):
            self.assertIn(word, RESERVED)
        r = subprocess.run([sys.executable, "-S", "-c", "from mycelic.collective.packs.loader import RESERVED;"
                            "print(len(RESERVED))"], cwd=ROOT, capture_output=True, text=True, timeout=60,
                           env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(int(r.stdout), len(RESERVED))


# =================================================================================================== frozen

def _walk(obj: Any, path: str = "pack"):
    yield path, obj
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        for f in dataclasses.fields(obj):
            yield from _walk(getattr(obj, f.name), f"{path}.{f.name}")
    elif isinstance(obj, MappingProxyType):
        for k in obj:
            yield from _walk(obj[k], f"{path}[{k!r}]")
    elif isinstance(obj, (tuple, frozenset)):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")


class FrozenTests(unittest.TestCase):
    def test_nothing_in_a_frozen_pack_is_mutable(self) -> None:
        allowed = (MappingProxyType, tuple, frozenset, str, int, float, bool, type(None), re.Pattern)
        for pid in PACKS:
            counts = {"dataclass": 0, "mapping": 0, "tuple": 0}
            for path, obj in _walk(pack(pid)):
                with self.subTest(pack=pid, path=path):
                    self.assertNotIsInstance(obj, (list, dict, set))
                    if dataclasses.is_dataclass(obj):
                        counts["dataclass"] += 1
                        name = dataclasses.fields(obj)[0].name
                        with self.assertRaises(dataclasses.FrozenInstanceError):
                            setattr(obj, name, None)
                        with self.assertRaises(dataclasses.FrozenInstanceError):
                            delattr(obj, name)
                    else:
                        self.assertIsInstance(obj, allowed)
                    if isinstance(obj, MappingProxyType):
                        counts["mapping"] += 1
                        with self.assertRaises(TypeError):
                            obj["new"] = 1
                        if obj:
                            with self.assertRaises(TypeError):
                                del obj[next(iter(obj))]
                    if isinstance(obj, tuple) and obj:
                        counts["tuple"] += 1
                        with self.assertRaises(TypeError):
                            obj[0] = None  # type: ignore[index]
            self.assertTrue(all(v > 10 for v in counts.values()), counts)

    def test_json_schemas_are_fresh_and_independent(self) -> None:
        for pid in PACKS:
            for ft in pack(pid).followups.values():
                with self.subTest(pack=pid, followup=ft.id):
                    a = ft.args_json_schema()
                    self.assertIsInstance(a, dict)
                    a["properties"].clear()
                    a["mutated"] = True
                    b = ft.args_json_schema()
                    self.assertNotIn("mutated", b)
                    self.assertTrue(b["properties"])
                    schemacheck.compile(b)
                    draft = ft.draft_json_schema()
                    if ft.executor == "draft":
                        draft["properties"].clear()
                        self.assertTrue(ft.draft_json_schema()["properties"])
                        schemacheck.compile(ft.draft_json_schema())
                    else:
                        self.assertIsNone(draft)

    def test_args_schema_accepts_canonical_ids_only(self) -> None:
        # G7 (D2): the built-in args are {conclusion} for evidence_packet, {conclusion, supplier_id} for scar_draft
        # and {conclusion, severity} for capa_initiation_draft; every arg kind stays covered
        dq = pack("device_quality")
        packet = schemacheck.compile(dq.followups["evidence_packet"].args_json_schema())
        self.assertEqual(packet.validate({"conclusion": "c-" + "0" * 32}), [])
        self.assertEqual(packet.validate({"conclusion": "c:site-a:42"}), [])
        for value in ("Has Space", "", "C-UPPER", 7, None):
            with self.subTest(arg="conclusion", value=value):
                self.assertTrue(packet.validate({"conclusion": value}))
        self.assertTrue(packet.validate({"conclusion": "c1", "product_id": "SD-9"}))           # an extra arg
        self.assertTrue(packet.validate({}))
        scar = schemacheck.compile(dq.followups["scar_draft"].args_json_schema())
        self.assertEqual(scar.validate({"conclusion": "c1", "supplier_id": "V1001"}), [])
        for value in ("v1001", "V 1001", "V-1001", "V10011", "V100", "V１００１"):
            with self.subTest(arg="supplier_id", value=value):
                self.assertTrue(scar.validate({"conclusion": "c1", "supplier_id": value}))
        self.assertTrue(scar.validate({"conclusion": "c1", "supplier_id": "V1001", "component_id": "BATTERY-DOOR"}))
        capa = schemacheck.compile(dq.followups["capa_initiation_draft"].args_json_schema())
        self.assertEqual(capa.validate({"conclusion": "c1", "severity": "high"}), [])
        for value in ("HIGH", "urgent", "", 1):
            with self.subTest(arg="severity", value=value):
                self.assertTrue(capa.validate({"conclusion": "c1", "severity": value}))
        # the predicate, integer and alias-only entity_id kinds, through a copy whose types regain the G6 args
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        directory = Path(tmp.name) / "device_quality"
        shutil.copytree(BUILTIN_ROOT / "device_quality", directory)

        def g6_args(obj: Any) -> None:
            obj["types"]["evidence_packet"]["args_schema"] = {
                "conclusion": {"kind": "conclusion_id"}, "product_id": {"kind": "entity_id", "entity_type": "product"},
                "failure_mode": {"kind": "predicate"}, "max_records": {"kind": "integer", "minimum": 1, "maximum": 200}}
            obj["types"]["scar_draft"]["args_schema"]["component_id"] = {"kind": "entity_id",
                                                                         "entity_type": "component"}

        TempCase.edit(directory, "followups.json", g6_args)
        copy = load_pack(directory)
        ft = copy.followups["evidence_packet"]
        schema = schemacheck.compile(ft.args_json_schema())
        good = {"conclusion": "c:site-a:42", "product_id": "SD-9", "failure_mode": "crack", "max_records": 10}
        self.assertEqual(schema.validate(good), [])
        self.assertEqual(schema.validate({**good, "max_records": 200}), [])
        self.assertEqual(schema.validate({**good, "max_records": 1}), [])
        for key, value in (("product_id", "sd-9"), ("product_id", "SD_9"), ("product_id", "SD 9"),
                           ("failure_mode", "free text"), ("failure_mode", "Crack"), ("max_records", 0),
                           ("max_records", 201), ("max_records", 10.0), ("max_records", True),
                           ("conclusion", "Has Space")):
            with self.subTest(key=key, value=value):
                self.assertTrue(schema.validate({**good, key: value}))
        scar = schemacheck.compile(copy.followups["scar_draft"].args_json_schema())
        self.assertEqual(scar.validate({"conclusion": "c1", "supplier_id": "V1001", "component_id": "BATTERY-DOOR"}),
                         [])
        self.assertTrue(scar.validate({"conclusion": "c1", "supplier_id": "V1001", "component_id": "battery door"}))


# =================================================================================================== hashes

def _leaves(obj: Any, path: tuple = ()) -> list[tuple]:
    if isinstance(obj, dict):
        return [leaf for k in obj for leaf in _leaves(obj[k], path + (k,))]
    if isinstance(obj, list):
        return [leaf for i, v in enumerate(obj) for leaf in _leaves(v, path + (i,))]
    return [path]


def _mutated(obj: Any, path: tuple) -> Any:
    obj = copy.deepcopy(obj)
    node = obj
    for key in path[:-1]:
        node = node[key]
    value = node[path[-1]]
    if isinstance(value, bool):
        node[path[-1]] = not value
    elif isinstance(value, (int, float)):
        node[path[-1]] = value + 1
    elif isinstance(value, str):
        node[path[-1]] = value + "~"
    else:
        node[path[-1]] = "was-null"
    return obj


def _parsed(pid: str) -> tuple[dict[str, Any], list[Any]]:
    d = BUILTIN_ROOT / pid
    files = {name: strict_load((d / name).read_bytes()) for name in FILES if (d / name).exists()}
    lines = [strict_load(line) for line in (d / "fixtures" / "records.jsonl").read_bytes().split(b"\n") if line.strip()]
    return files, lines


class HashTests(TempCase):
    def test_stable_across_processes_and_hash_seeds(self) -> None:
        code = ("import json; from mycelic.collective.packs.loader import load_pack;"
                f"print(json.dumps([load_pack(p).hashes() for p in {PACKS!r}]))")
        outputs = []
        for seed in ("0", "4242"):
            r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                               env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONHASHSEED": seed})
            self.assertEqual(r.returncode, 0, r.stderr)
            outputs.append(json.loads(r.stdout))
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0], [pack(p).hashes() for p in PACKS])
        self.assertEqual(load_pack("device_quality").hashes(), pack("device_quality").hashes())

    def test_invariant_to_key_order_whitespace_and_crlf(self) -> None:
        def reorder(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {k: reorder(obj[k]) for k in reversed(list(obj))}
            if isinstance(obj, list):
                return [reorder(v) for v in obj]
            return obj

        for pid in PACKS:
            d = self.copy(pid)
            for name in FILES:
                if (d / name).exists():
                    obj = json.loads((d / name).read_text(encoding="utf-8"))
                    (d / name).write_text(json.dumps(reorder(obj), indent=7, ensure_ascii=True) + "\n\n")
            lines = (d / "fixtures" / "records.jsonl").read_text(encoding="utf-8").splitlines()
            body = "\r\n\r\n".join(json.dumps(reorder(json.loads(line)), separators=(" ,", " : ")) for line in lines)
            (d / "fixtures" / "records.jsonl").write_bytes(body.encode("utf-8") + b"\r\n")
            with self.subTest(pack=pid):
                self.assertEqual(load_pack(str(d)).hashes(), pack(pid).hashes())

    def test_every_leaf_changes_exactly_its_scopes(self) -> None:
        total = 0
        for pid in PACKS:
            files, lines = _parsed(pid)
            base = compute_hashes(files, lines)
            self.assertEqual(base, pack(pid).hashes())
            for name in sorted(files):
                for path in _leaves(files[name]):
                    changed = compute_hashes({**files, name: _mutated(files[name], path)}, lines)
                    got = {scope for scope in base if changed[scope] != base[scope]}
                    want = {scope for scope, spec in HASH_SCOPES.items() if name in spec["files"]}
                    if name == "pack.json" and path == ("version",):
                        want = {"config_hash", "vocabulary_hash"}
                    if name == "egress.json" and path[0] in loader.DETECTOR_EGRESS_FIELDS:
                        want.add("detector_hash")
                    if got != want:
                        self.fail(f"{pid} {name} {path}: changed {sorted(got)}, expected {sorted(want)}")
                    total += 1
            for i, line in enumerate(lines):
                for path in _leaves(line):
                    mutated = [*lines[:i], _mutated(line, path), *lines[i + 1:]]
                    changed = compute_hashes(files, mutated)
                    got = {scope for scope in base if changed[scope] != base[scope]}
                    if got != {"fixtures_hash"}:
                        self.fail(f"{pid} fixture line {i} {path}: changed {sorted(got)}")
                    total += 1
        self.assertGreaterEqual(total, 500)

    def test_special_cases_named_in_the_brief(self) -> None:
        files, lines = _parsed("device_quality")
        base = compute_hashes(files, lines)

        def changed(name: str, path: tuple) -> set[str]:
            out = compute_hashes({**files, name: _mutated(files[name], path)}, lines)
            return {s for s in base if out[s] != base[s]}

        self.assertEqual(changed("pack.json", ("version",)), {"config_hash", "vocabulary_hash"})
        for field in ("k", "suppress_below_days", "count_granularity", "close_lag_days", "min_window_weeks"):
            self.assertEqual(changed("egress.json", (field,)), {"config_hash", "detector_hash"})
        self.assertEqual(changed("egress.json", ("verify_max_records",)), {"config_hash"})
        self.assertEqual(changed("vocabulary.json", ("predicates", "crack", "lexicon", "en", 0)),
                         {"config_hash", "vocabulary_hash"})
        self.assertEqual(changed("generator.json", ("forwarding_rate",)), {"fixtures_hash"})

    def test_compute_hashes_never_raises_on_arbitrary_json(self) -> None:
        files, lines = _parsed("claims_integrity")
        for name in sorted(files):
            for value in (None, [], "str", 0, {"version": [1, {"x": None}]}, float("nan"), {"k": float("inf")}):
                with self.subTest(file=name, value=repr(value)):
                    out = compute_hashes({**files, name: value}, lines)
                    self.assertTrue(all(HEX64.fullmatch(v) for v in out.values()))
        for weird_files, weird_lines in ((None, None), ([], "x"), ({1: 2}, [float("nan"), None]), ({}, [])):
            out = compute_hashes(weird_files, weird_lines)
            self.assertEqual(set(out), set(HASH_SCOPES))


# =================================================================================================== malformed packs

def _set(path: tuple, value: Any) -> Callable[[Any], None]:
    def fn(obj: Any) -> None:
        node = obj
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return fn


def _del(path: tuple) -> Callable[[Any], None]:
    def fn(obj: Any) -> None:
        node = obj
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]
    return fn


def _t2_enabled(obj: Any) -> None:
    obj["types"]["scar_draft"].update({"tier": "T2", "executor": "write", "draft_schema": None, "enabled": True})


def _never_without_person(obj: Any) -> None:
    obj["never_fields"].remove("persons.patient_ref")


def _reserved_predicate(obj: Any) -> None:
    obj["predicates"]["codes"] = {"label": "x", "lexicon": {"en": ["zzz"]}}


def _ambiguous_alias(obj: Any) -> None:
    obj["component"]["Battery  Door"] = "LUER-CONNECTOR"


def _fixture_gold_variant(lines: list) -> None:
    lines[1]["gold"][0]["entity_id"] = "sd-9"


def _fixture_duplicate(lines: list) -> list:
    return [*lines, lines[0]]


# (name, file to edit, edit, expected file, expected path, expected problem fragment)
MALFORMED = [
    ("unknown key", "pack.json", _set(("extra",), 1), "pack.json", "$.extra", "unknown key"),
    ("missing key", "pack.json", _del(("title",)), "pack.json", "$.title", "missing key"),
    ("array where an object was expected", "egress.json", lambda o: [], "egress.json", "$", "must be an object"),
    ("invalid semver", "pack.json", _set(("version",), "1.0"), "pack.json", "$.version", "pattern"),
    ("zero-length id_format", "vocabulary.json", _set(("entity_types", "product", "id_format"), []),
     "vocabulary.json", "$.entity_types.product.id_format", "non-empty"),
    ("unknown segment kind", "vocabulary.json", _set(("entity_types", "lot", "id_format"), [{"word": [1, 2]}]),
     "vocabulary.json", "$.entity_types.lot.id_format[0]", "unknown segment kind"),
    ("format admits the empty string", "vocabulary.json",
     _set(("entity_types", "lot", "id_format"), [{"digit": [0, 3]}, {"alpha": [0, 1]}]),
     "vocabulary.json", "$.entity_types.lot.id_format", "empty string"),
    ("ambiguous adjacent segments", "vocabulary.json",
     _set(("entity_types", "lot", "id_format"), [{"alpha": [1, 2]}, {"alnum": [1, 2]}]),
     "vocabulary.json", "$.entity_types.lot.id_format[1]", "ambiguous adjacent segments"),
    ("alias to an unknown id", "aliases.json", _set(("component", "flux capacitor"), "NOPE"), "aliases.json",
     "$.component", "unknown target"),
    ("ambiguous alias", "aliases.json", _ambiguous_alias, "aliases.json", "$.component", "ambiguous alias"),
    ("duplicate alias", "aliases.json", _set(("component", "Battery  door"), "BATTERY-DOOR"), "aliases.json",
     "$.component", "duplicate alias"),
    ("alias chain", "aliases.json", _set(("component", "battery-door"), "BATTERY-DOOR"), "aliases.json",
     "$.component", "alias chain"),
    ("alias that looks like an id", "aliases.json", _set(("product", "sd 12"), "SD-12"), "aliases.json",
     "$.product", "alias looks like an id of type product"),
    ("alias folding to nothing", "aliases.json", _set(("component", "\u200b"), "BATTERY-DOOR"), "aliases.json",
     "$.component", "empty"),
    ("dangling predicate in codes", "codes.json", _set(("ILL-0101", "predicate"), "nope"), "codes.json",
     "$.ILL-0101.predicate", "unknown predicate"),
    ("dangling predicate in rules", "rules.json", _set(("rules", "multi_site_lot_leak", "predicate"), "nope"),
     "rules.json", "$.rules.multi_site_lot_leak.predicate", "unknown predicate"),
    ("dangling predicate in questions", "questions.json",
     _set(("templates", "lot_scope_check", "predicates"), ["nope"]), "questions.json",
     "$.templates.lot_scope_check.predicates[0]", "unknown predicate"),
    ("T3", "followups.json", _set(("types", "evidence_packet", "tier"), "T3"), "followups.json",
     "$.types.evidence_packet.tier", "T3 is not an action type"),
    ("enabled T2", "followups.json", _t2_enabled, "followups.json", "$.types.scar_draft.enabled",
     "T2 writes are off"),
    ("k=1", "egress.json", _set(("k",), 1), "egress.json", "$.k", "minimum"),
    ("min_window_weeks=4", "egress.json", _set(("min_window_weeks",), 4), "egress.json", "$.min_window_weeks",
     "minimum"),
    ("buckets[0] != k", "egress.json", _set(("verdict_count_buckets",), [4, 5, 10]), "egress.json",
     "$.verdict_count_buckets[0]", "must equal k"),
    ("field in both lists", "egress.json", lambda o: o["central_allowed_fields"].append("reporter"), "egress.json",
     "$.central_allowed_fields[7]", "field in both egress and never lists"),
    ("person field missing from never_fields", "egress.json", _never_without_person, "egress.json",
     "$.never_fields", "must contain persons.patient_ref"),
    ("unknown question placeholder", "questions.json",
     _set(("templates", "lot_scope_check", "text"), "Tell me about {record_text}."), "questions.json",
     "$.templates.lot_scope_check.text", "unknown placeholder"),
    ("placeholder with a format spec", "questions.json",
     _set(("templates", "lot_scope_check", "text"), "In {window:>3}?"), "questions.json",
     "$.templates.lot_scope_check.text", "conversion or format spec"),
    ("placeholder with a conversion", "questions.json",
     _set(("templates", "lot_scope_check", "text"), "In {window!r}?"), "questions.json",
     "$.templates.lot_scope_check.text", "conversion or format spec"),
    ("unbalanced braces", "questions.json", _set(("templates", "lot_scope_check", "text"), "In {window?"),
     "questions.json", "$.templates.lot_scope_check.text", "unbalanced braces"),
    ("args_schema with free text", "followups.json",
     _set(("types", "evidence_packet", "args_schema", "note"), {"kind": "text"}), "followups.json",
     "$.types.evidence_packet.args_schema.note.kind", "free text or unsupported kind"),
    ("args_schema with an unsupported keyword", "followups.json",
     _set(("types", "evidence_packet", "args_schema", "failure_mode"), {"kind": "predicate", "pattern": ".*"}),
     "followups.json", "$.types.evidence_packet.args_schema.failure_mode.pattern", "unsupported keyword"),
    ("draft_schema string without maxLength", "followups.json",
     _set(("types", "capa_initiation_draft", "draft_schema", "properties", "title"), {"type": "string"}),
     "followups.json", "$.types.capa_initiation_draft.draft_schema.properties.title",
     "draft_schema string without maxLength"),
    ("an id containing ':'", "vocabulary.json",
     _set(("predicates", "bad:id"), {"label": "x", "lexicon": {"en": ["zzz"]}}), "vocabulary.json",
     "$.predicates[\"bad:id\"]", "invalid predicate id"),
    ("a reserved word as an id", "vocabulary.json", _reserved_predicate, "vocabulary.json", "$.predicates.codes",
     "reserved word"),
    ("generator rate outside [0, 1]", "generator.json", _set(("forwarding_rate",), 1.5), "generator.json",
     "$.forwarding_rate", "rate outside [0, 1]"),
    ("reporter pool < 1", "generator.json", _set(("sites", 0, "reporters"), 0), "generator.json",
     "$.sites[0].reporters", "reporter pool < 1"),
    ("template slot not on a word boundary", "generator.json",
     _set(("narratives", "en", "crack", "affirmed", 0), "The x{component} cracked."), "generator.json",
     "$.narratives.en.crack.affirmed[0]", "slot not on a word boundary"),
    ("generator start not a Monday", "generator.json", _set(("start",), "2024-01-02"), "generator.json",
     "$.start", "Monday"),
]


class MalformedPackTests(TempCase):
    def assert_pack_error(self, directory: Path, file: str, path: str, problem: str) -> None:
        try:
            load_pack(str(directory))
        except PackError as err:
            self.assertIs(type(err), PackError)
            self.assertEqual(err.file, file, str(err))
            self.assertTrue(err.path.startswith(path), str(err))
            self.assertIn(problem, err.problem, str(err))
            self.assertEqual(str(err), f"{err.file}: {err.path}: {err.problem}")
            return
        self.fail("no PackError")

    def test_constructed_malformed_packs(self) -> None:
        self.assertGreaterEqual(len(MALFORMED), 30)
        for i, (name, file, fn, efile, epath, problem) in enumerate(MALFORMED):
            with self.subTest(case=name):
                d = self.copy(name=f"case{i}")
                self.edit(d, file, fn)
                self.assert_pack_error(d, efile, epath, problem)

    def test_file_level_failures(self) -> None:
        cases = [
            ("missing file", lambda d: (d / "codes.json").unlink(), "codes.json", "$", "missing file"),
            ("unexpected file", lambda d: (d / "notes.txt").write_text("x"), "notes.txt", "$", "unexpected file"),
            ("unexpected fixture file", lambda d: (d / "fixtures" / "more.jsonl").write_text(""),
             "fixtures/more.jsonl", "$", "unexpected file"),
            ("duplicate key", lambda d: (d / "detectors.json").write_bytes(b'{"a": 1, "a": 2}'), "detectors.json",
             "$.a", "duplicate_key"),
            ("NaN", lambda d: (d / "rules.json").write_bytes(b'{"rules": NaN}'), "rules.json", "$", "non_finite"),
            ("BOM", lambda d: (d / "questions.json").write_bytes(b"\xef\xbb\xbf" + (d / "questions.json")
                                                                 .read_bytes()), "questions.json", "$", "bom"),
            ("non-UTF-8 bytes", lambda d: (d / "followups.json").write_bytes(b'{"roles": "\xff"}'), "followups.json",
             "byte", "encoding"),
            ("empty file", lambda d: (d / "aliases.json").write_bytes(b""), "aliases.json", "line 1", "syntax"),
            ("a file that is a directory", lambda d: ((d / "vocabulary.json").unlink(),
                                                      (d / "vocabulary.json").mkdir()),
             "vocabulary.json", "$", "cannot read (IsADirectoryError)"),
            ("a dangling symlink at the optional file", lambda d: ((d / "mapping_openfda.json").unlink(),
                                                                   (d / "mapping_openfda.json").symlink_to(
                                                                       d / "nowhere.json")),
             "mapping_openfda.json", "$", "cannot read (FileNotFoundError)"),
            ("fixtures with a bad line", lambda d: (d / "fixtures" / "records.jsonl").write_bytes(b"{}\n{oops\n"),
             "fixtures/records.jsonl", "line 2", "syntax"),
            ("a fixture with a non-canonical gold id", lambda d: self.edit_fixtures(d, _fixture_gold_variant),
             "fixtures/records.jsonl", "line 2 $.gold[0].entity_id", "gold id is not canonical"),
            ("a fixture with a duplicate record_ref", lambda d: self.edit_fixtures(d, _fixture_duplicate),
             "fixtures/records.jsonl", "line 54 $.record.record_ref", "duplicate record_ref"),
            ("too few fixtures", lambda d: self.edit_fixtures(d, lambda lines: lines[:39]),
             "fixtures/records.jsonl", "$", "at least 40"),
        ]
        for i, (name, fn, efile, epath, problem) in enumerate(cases):
            with self.subTest(case=name):
                d = self.copy(name=f"file{i}")
                fn(d)
                self.assert_pack_error(d, efile, epath, problem)

    def test_wrong_type_sweep_gives_a_load_or_a_pack_error_only(self) -> None:
        names = [n for n in FILES if (BUILTIN_ROOT / "device_quality" / n).exists()] + ["fixtures/records.jsonl"]
        outcomes = {"loaded": 0, "pack_error": 0}
        for name in names:
            for value in (None, [], 0, "x", True, {}):
                with self.subTest(file=name, value=value):
                    d = self.dir / "sweep"
                    if d.exists():
                        shutil.rmtree(d)
                    shutil.copytree(BUILTIN_ROOT / "device_quality", d)
                    (d / name).write_text(json.dumps(value) + "\n")
                    try:
                        load_pack(str(d))
                        outcomes["loaded"] += 1
                    except PackError as err:
                        self.assertIs(type(err), PackError)
                        self.assertIn(err.file, (*FILES, "fixtures/records.jsonl", "pack"))  # may be a cross-reference
                        outcomes["pack_error"] += 1
        self.assertEqual(sum(outcomes.values()), len(names) * 6)
        self.assertGreater(outcomes["pack_error"], 0)


# =================================================================================================== templates

_CAPA_T = ("types", "capa_initiation_draft", "template")
_CAPA_P = "$.types.capa_initiation_draft.template"
_MISFIT = "template source does not fit the property's type"


def _integer_property(obj: Any) -> None:
    schema = obj["types"]["capa_initiation_draft"]["draft_schema"]
    schema["properties"]["revision"] = {"type": "integer", "minimum": 0}
    schema["required"].append("revision")
    obj["types"]["capa_initiation_draft"]["template"]["revision"] = "headline"


# (name, edit, expected path, expected problem)
TEMPLATE_REFUSALS = [
    ("unknown key", _set(_CAPA_T + ("extra",), "headline"), _CAPA_P + ".extra", "unknown key"),
    ("missing key", _del(_CAPA_T + ("containment",)), _CAPA_P + ".containment", "missing key"),
    ("not an object", _set(_CAPA_T, "headline"), _CAPA_P, "must be an object"),
    ("unknown source", _set(_CAPA_T + ("title",), "narrative"), _CAPA_P + ".title", "unknown template source"),
    ("a null source", _set(_CAPA_T + ("title",), None), _CAPA_P + ".title", "unknown template source"),
    ("unknown list entry", _set(_CAPA_T + ("problem_statement",), ["summary", "for_owner"]),
     _CAPA_P + ".problem_statement[1]", "unknown template source"),
    ("duplicate list entry", _set(_CAPA_T + ("problem_statement",), ["summary", "evidence", "summary"]),
     _CAPA_P + ".problem_statement[2]", "duplicate template source"),
    ("a text source on an array", _set(_CAPA_T + ("affected_lots",), "headline"), _CAPA_P + ".affected_lots",
     _MISFIT),
    ("an entity source on a string", _set(_CAPA_T + ("title",), "entity_ids:lot"), _CAPA_P + ".title", _MISFIT),
    ("confirming sites on a string", _set(_CAPA_T + ("containment",), "confirming_sites"), _CAPA_P + ".containment",
     _MISFIT),
    ("a list on an array", _set(_CAPA_T + ("affected_lots",), ["summary"]), _CAPA_P + ".affected_lots", _MISFIT),
    ("an empty list", _set(_CAPA_T + ("problem_statement",), []), _CAPA_P + ".problem_statement", _MISFIT),
    ("a list of four", _set(_CAPA_T + ("problem_statement",), ["headline", "summary", "evidence", "summary"]),
     _CAPA_P + ".problem_statement", _MISFIT),
    ("a source on an integer property", _integer_property, _CAPA_P + ".revision", _MISFIT),
    ("a non-egress entity type", _set(_CAPA_T + ("affected_lots",), "entity_ids:patient_ref"),
     _CAPA_P + ".affected_lots", "not an egress entity type"),
    ("a template on a non-draft type", _set(("types", "evidence_packet", "template"), {"title": "headline"}),
     "$.types.evidence_packet.template", "template is set exactly when executor is draft"),
    ("a draft type without a template", _set(_CAPA_T, None), _CAPA_P,
     "template is set exactly when executor is draft"),
    ("a non-draft type without the key", _del(("types", "evidence_packet", "template")),
     "$.types.evidence_packet.template", "missing key"),
]


class TemplateLoaderTests(TempCase):
    """B1: ``followups.json``'s ``template``, one source per required property of a draft type's schema."""

    def test_each_refusal_names_its_path_and_a_fixed_problem(self) -> None:
        for i, (name, fn, path, problem) in enumerate(TEMPLATE_REFUSALS):
            with self.subTest(case=name):
                d = self.copy(name=f"template{i}")
                self.edit(d, "followups.json", fn)
                with self.assertRaises(PackError) as cm:
                    load_pack(str(d))
                self.assertEqual((cm.exception.file, cm.exception.path, cm.exception.problem),
                                 ("followups.json", path, problem))
                for word in ("narrative", "patient_ref"):
                    self.assertNotIn(word, cm.exception.problem)

    def test_every_built_in_type_carries_a_template_exactly_when_it_drafts(self) -> None:
        for pid in PACKS:
            raw = json.loads((BUILTIN_ROOT / pid / "followups.json").read_text(encoding="utf-8"))
            for name, ft in pack(pid).followups.items():
                with self.subTest(pack=pid, type=name):
                    self.assertIn("template", raw["types"][name])
                    self.assertEqual(list(raw["types"][name]).index("template"),
                                     list(raw["types"][name]).index("draft_schema") + 1)
                    self.assertEqual(ft.template is None, ft.executor != "draft")
                    if ft.template is not None:
                        self.assertEqual(sorted(ft.template), sorted(ft.draft_json_schema()["required"]))
                        self.assertEqual(loader.thaw(ft.template), raw["types"][name]["template"])

    def test_other_forms_load_and_change_only_the_config_hash(self) -> None:
        base = pack("device_quality")
        for i, template in enumerate((
                {"title": "summary", "problem_statement": ["evidence", "headline", "summary"],
                 "containment": "evidence", "affected_lots": "confirming_sites"},
                {"title": ["headline"], "problem_statement": "for_owner", "containment": ["summary", "evidence"],
                 "affected_lots": "for_owner"})):
            with self.subTest(template=i):
                d = self.copy(name=f"forms{i}")
                self.edit(d, "followups.json", _set(_CAPA_T, template))
                p = load_pack(str(d))
                self.assertEqual(loader.thaw(p.followups["capa_initiation_draft"].template), template)
                self.assertEqual({k for k in base.hashes() if base.hashes()[k] != p.hashes()[k]}, {"config_hash"})

    def test_template_is_reserved_and_names_no_built_in_id(self) -> None:
        self.assertIn("template", RESERVED)
        self.assertIn("template", loader._FOLLOWUP_KEYS)
        for pid in PACKS:
            p = pack(pid)
            ids = [p.id, *p.entity_types, *p.predicates, *p.roles, *p.questions, *p.followups, *p.rules, *p.codes]
            self.assertNotIn("template", ids)

    def test_only_the_config_hash_differs_from_g5(self) -> None:
        from tests.mycelic.test_collective_pushdown import B1_CONFIG_HASHES, G5_HASHES
        for pid in ("device_quality", "claims_integrity"):           # the G5 history; B4_HASHES pins it_incidents
            with self.subTest(pack=pid):
                hashes = pack(pid).hashes()
                self.assertEqual({k for k in hashes if hashes[k] != G5_HASHES[pid][k]}, {"config_hash"})
                self.assertEqual(hashes["config_hash"], B1_CONFIG_HASHES[pid])

    def test_every_builtin_pack_is_pinned(self) -> None:
        # B4: device and claims are pinned by their G5 hashes with B1's config hash, it_incidents by B4_HASHES
        from tests.mycelic.test_collective_pushdown import B1_CONFIG_HASHES, B4_HASHES, G5_HASHES
        pins = {pid: {**G5_HASHES[pid], "config_hash": B1_CONFIG_HASHES[pid]} for pid in G5_HASHES}
        pins.update(B4_HASHES)
        self.assertEqual(set(pins), set(PACKS))
        for pid in PACKS:
            with self.subTest(pack=pid):
                self.assertEqual(pack(pid).hashes(), pins[pid])


# =================================================================================================== id formats

def _fmt(segments: list, case: str = "upper", separator: str | None = None, strip: bool = False):
    return compile_id_format("t", segments, case, separator, strip)


class IdFormatTests(unittest.TestCase):
    def formats(self) -> list:
        out = [et.id_format for pid in PACKS for et in pack(pid).entity_types.values() if et.id_format]
        out.append(_fmt([{"literal": "TW"}, {"sep": "optional"}, {"digit": [4, 4]}], separator="-"))
        out.append(_fmt([{"alpha": [1, 3]}, {"sep": "required"}, {"alnum": [2, 2]}], case="lower", separator="/"))
        return out

    def test_pattern_text_is_ascii_without_shorthand_classes_or_lookaround(self) -> None:
        for fmt in self.formats():
            text = fmt.pattern.pattern
            with self.subTest(pattern=text):
                self.assertTrue(text.isascii())
                for banned in ("\\d", "\\w", "(?=", "(?!", "(?<=", "(?<!", "(?P=", "\\1"):
                    self.assertNotIn(banned, text)
                self.assertTrue(fmt.canonical.pattern.isascii())

    def test_bad_formats_are_rejected_with_a_segment_index(self) -> None:
        cases = [
            ([], None, None, False, None, "non-empty"),
            ([{"alpha": [0, 2]}, {"digit": [0, 1]}], "upper", None, False, None, "empty string"),
            ([{"word": [1, 2]}], "upper", None, False, 0, "unknown segment kind"),
            ([{"alpha": [1, 2]}, {"alnum": [1, 1]}], "upper", None, False, 1, "ambiguous adjacent segments"),
            ([{"alpha": [1, 2]}, {"sep": "optional"}, {"alpha": [1, 2]}], "upper", "-", False, 2, "ambiguous"),
            ([{"digit": [1, 2]}, {"alpha": [0, 1]}, {"digit": [1, 2]}], "upper", None, False, 2, "ambiguous"),
            ([{"literal": "ab"}], "upper", None, False, 0, "literal"),
            ([{"literal": "A"}, {"digit": [2, 3]}], "upper", None, True, 1, "strip_leading_zeros"),
            ([{"alpha": [20, 20]}, {"digit": [20, 20]}, {"alpha": [1, 1]}], "upper", None, False, None, "longer"),
            ([{"alpha": [1, 21]}], "upper", None, False, 0, "range"),
            ([{"alpha": [2, 1]}], "upper", None, False, 0, "range"),
            ([{"alpha": [1, 2]}, {"sep": "required"}], "upper", "-", False, 1, "edge"),
            ([{"alpha": [1, 2]}, {"sep": "optional"}, {"sep": "required"}, {"digit": [1, 1]}], "upper", "-", False,
             2, "adjacent seps"),
            ([{"alpha": [1, 2]}, {"sep": "required"}, {"digit": [1, 1]}], "upper", None, False, None, "separator"),
            ([{"alpha": [1, 2]}, {"digit": [1, 1]}], "upper", "-", False, None, "separator"),
        ]
        for segments, case, sep, strip, index, problem in cases:
            with self.subTest(segments=segments):
                with self.assertRaises(IdFormatError) as ctx:
                    compile_id_format("t", segments, case or "upper", sep, strip)
                self.assertIsInstance(ctx.exception, ValueError)
                self.assertEqual(ctx.exception.index, index)
                self.assertIn(problem, ctx.exception.problem)

    def test_canonical_form_inserts_the_separator_and_folds_case(self) -> None:
        fmt = _fmt([{"literal": "TW"}, {"sep": "optional"}, {"digit": [4, 4]}], separator="-")
        for text in ("TW0007", "tw0007", "TW-0007", "TW_0007", "TW 0007", "TW\u20130007"):
            with self.subTest(text=text):
                self.assertEqual(fmt.canonical_form(text), "TW-0007")
        self.assertTrue(fmt.is_canonical("TW-0007"))
        self.assertFalse(fmt.is_canonical("TW0007"))
        clinic = pack("claims_integrity").entity_types["clinic"].id_format
        self.assertEqual(clinic.canonical_form("cl-7q2k"), "CL-7Q2K")
        self.assertIsNone(clinic.canonical_form("CL-7Q2"))
        lower = _fmt([{"alpha": [1, 3]}, {"sep": "required"}, {"alnum": [2, 2]}], case="lower", separator="/")
        self.assertEqual(lower.canonical_form("AB-9X"), "ab/9x")

    def test_leading_zeros(self) -> None:
        lot = pack("device_quality").entity_types["lot"].id_format
        self.assertEqual((lot.canonical_form("L012345"), lot.canonical_form("L12345")), ("L012345", "L12345"))
        shop = pack("claims_integrity").entity_types["repair_shop"].id_format
        self.assertEqual(shop.canonical_form("RS-0042"), "RS-42")
        self.assertEqual(shop.canonical_form("RS-00000"), "RS-0")
        self.assertTrue(shop.is_canonical("RS-42"))
        self.assertFalse(shop.is_canonical("RS-0042"))
        canon = Canonicaliser(pack("claims_integrity"))
        self.assertEqual(canon.resolve_exact("repair_shop", "RS-0042").method, "variant")
        self.assertEqual(canon.resolve_exact("repair_shop", "RS-42").method, "exact")

    def test_canonical_regex_agrees_with_canonical_form(self) -> None:
        samples = ["SD-9", "SD-09", "sd-9", "SD_9", "L12345", "L012345", "L12345A", "V0001", "RS-42", "RS-042",
                   "RS-0", "RS-00", "CL-7Q2K", "CL-7q2k", "TW-0007", "TW0007", "X", ""]
        for fmt in self.formats():
            for s in samples:
                with self.subTest(pattern=fmt.canonical.pattern, sample=s):
                    self.assertEqual(fmt.canonical.fullmatch(s) is not None, fmt.is_canonical(s))

    def test_adversarial_input_scans_in_under_a_second(self) -> None:
        for pid in PACKS:
            canon = Canonicaliser(pack(pid))
            for text in ("A-" * 100000, "L" + "1" * 100000, "SD 9 " * 40000, "RS-" * 66667, "\u0405" * 200000):
                with self.subTest(pack=pid, text=text[:8]):
                    t0 = time.perf_counter()
                    canon.scan(text)
                    self.assertLess(time.perf_counter() - t0, 1.0)

    def test_constructed_format_keeps_eszett_and_double_s_apart(self) -> None:
        fmt = _fmt([{"literal": "LOT"}, {"sep": "required"}, {"alpha": [1, 2]}, {"digit": [1, 1]}], separator="-")
        self.assertEqual([fmt.canonical_of(m) for m in fmt.scanner.finditer("LOT-SS1")], ["LOT-SS1"])
        self.assertEqual(list(fmt.scanner.finditer("LOT-\u00df1")), [])
        shadow = list(fmt.shadow.finditer("LOT-\u00df1"))
        self.assertEqual(len(shadow), 1)
        self.assertEqual(fmt.non_ascii_kind(shadow[0]), "homoglyph")
        self.assertIsNone(fmt.canonical_form("LOT-\u00df1"))


# =================================================================================================== canonicaliser

S, V, A = "exact", "variant", "alias"
# (text, known or None, [(type, id, method, start, end)], {unresolved key: count})
DEVICE_TABLE = [
    ("SD-9", None, [("product", "SD-9", S, 0, 4)], {}),
    ("sd-9", None, [("product", "SD-9", V, 0, 4)], {}),
    ("SD_9", None, [("product", "SD-9", V, 0, 4)], {}),
    ("SD\u20139", None, [("product", "SD-9", V, 0, 4)], {}),
    ("SD\u20149", None, [("product", "SD-9", V, 0, 4)], {}),
    ("SD\u22129", None, [("product", "SD-9", V, 0, 4)], {}),
    ("\uff33\uff24\uff0d\uff19", None, [("product", "SD-9", V, 0, 4)], {}),
    ("SD\u200b-9", None, [("product", "SD-9", V, 0, 5)], {}),
    ("SD 9", None, [("product", "SD-9", V, 0, 4)], {}),
    ("SD\u00a09", None, [("product", "SD-9", V, 0, 4)], {}),
    ("SD 12", None, [], {"space_unknown": 1}),
    ("SD 12", {"product": ["SD-12"]}, [("product", "SD-12", V, 0, 5)], {}),
    ("SD-90", None, [("product", "SD-90", S, 0, 5)], {}),
    ("XSD-91", None, [], {}),
    ("SD-9-B", None, [], {}),
    ("SD-9\u00b2", None, [], {}),
    ("used SD 90 days", None, [], {"space_unknown": 1}),
    ("\u0405D-9", None, [], {"homoglyph": 1}),
    ("SD-\u0669", None, [], {"non_ascii_digit": 1}),
    ("SD-\u096f", None, [], {"non_ascii_digit": 1}),
    ("\u0130P-7", None, [], {"homoglyph": 1}),
    ("\u0131p-7", None, [], {"homoglyph": 1}),
    ("X\ufb01-9", None, [], {"homoglyph": 1}),
    ("SD-\u2468", None, [], {}),
    ("\u00dfD-9", None, [], {"homoglyph": 1}),
    ("(SD-9).", None, [("product", "SD-9", S, 1, 5)], {}),
    ("<b>SD-9</b>", None, [("product", "SD-9", S, 3, 7)], {}),
    ("SD-9- ", None, [("product", "SD-9", S, 0, 4)], {}),
    ("SD-9 broke", None, [("product", "SD-9", S, 0, 4)], {}),
    ("SD-9 and L10001 cracked", None, [("product", "SD-9", S, 0, 4), ("lot", "L10001", S, 9, 15)], {}),
    ("the pump housing leaked", None, [("component", "PUMP-HOUSING", A, 4, 16)], {}),
    ("No crack in the battery door", None, [("component", "BATTERY-DOOR", A, 16, 28)], {}),
    ("battery\u00a0 door", None, [("component", "BATTERY-DOOR", A, 0, 13)], {}),
    ("SalineDuo leaked", None, [("product", "SD-9", A, 0, 9)], {}),
    ("L12345 and L12345A", None, [("lot", "L12345", S, 0, 6), ("lot", "L12345A", S, 11, 18)], {}),
    ("l12345a", None, [("lot", "L12345A", V, 0, 7)], {}),
    ("L012345", None, [("lot", "L012345", S, 0, 7)], {}),
    ("L12345AB", None, [], {}),
    ("v1001 shipped", None, [("supplier", "V1001", V, 0, 5)], {}),
]
CLAIMS_TABLE = [
    ("RS-42", None, [("repair_shop", "RS-42", S, 0, 5)], {}),
    ("RS-0042", None, [("repair_shop", "RS-42", V, 0, 7)], {}),
    ("rs-42", None, [("repair_shop", "RS-42", V, 0, 5)], {}),
    ("RS_42", None, [("repair_shop", "RS-42", V, 0, 5)], {}),
    ("RS\u201342", None, [("repair_shop", "RS-42", V, 0, 5)], {}),
    ("\uff32\uff33\uff0d\uff14\uff12", None, [("repair_shop", "RS-42", V, 0, 5)], {}),
    ("RS 42", None, [("repair_shop", "RS-42", V, 0, 5)], {}),
    ("RS 77", None, [], {"space_unknown": 1}),
    ("RS 77", {"repair_shop": ["RS-77"]}, [("repair_shop", "RS-77", V, 0, 5)], {}),
    ("XRS-42", None, [], {}),
    ("RS-42-B", None, [], {}),
    ("RS-123456", None, [], {}),
    ("CL-7Q2K", None, [("clinic", "CL-7Q2K", S, 0, 7)], {}),
    ("cl-7q2k", None, [("clinic", "CL-7Q2K", V, 0, 7)], {}),
    ("CL-A1009", None, [], {}),
    ("CL-7Q2", None, [], {}),
    ("TW-0007", None, [("tow_operator", "TW-0007", S, 0, 7)], {}),
    ("TW0007", None, [("tow_operator", "TW-0007", V, 0, 6)], {}),
    ("tw 0007", None, [("tow_operator", "TW-0007", V, 0, 7)], {}),
    ("TW-007", None, [], {}),
    ("Kestrel Auto Body", None, [("repair_shop", "RS-42", A, 0, 17)], {}),
    ("kestrel  auto body", None, [("repair_shop", "RS-42", A, 0, 18)], {}),
    ("the front bumper", None, [("damage_area", "FRONT-BUMPER", A, 4, 16)], {}),
    ("The side panel and door panel", None,
     [("damage_area", "SIDE-PANEL", A, 4, 14), ("damage_area", "SIDE-PANEL", A, 19, 29)], {}),
    ("\u0420S-42", None, [], {"homoglyph": 1}),
    ("RS-\u0664\u0662", None, [], {"non_ascii_digit": 1}),
    ("CL-7Q\u0662K", None, [], {"non_ascii_digit": 1}),
    ("RS-42.", None, [("repair_shop", "RS-42", S, 0, 5)], {}),
]


class CanonicaliserTests(TempCase):
    def run_table(self, pid: str, table: list) -> None:
        p = pack(pid)
        conf = {S: p.extraction.confidence_exact, V: p.extraction.confidence_variant,
                A: p.extraction.confidence_alias}
        for text, known, mentions, unresolved in table:
            with self.subTest(pack=pid, text=text, known=known):
                result = Canonicaliser(p, known).scan(text)
                got = [(m.entity_type, m.entity_id, m.method, m.start, m.end) for m in result.mentions]
                self.assertEqual(got, mentions)
                for m in result.mentions:
                    self.assertEqual(m.text, text[m.start:m.end])
                    self.assertEqual(m.res_conf, conf[m.method])
                self.assertEqual(dict(result.unresolved), {"homoglyph": 0, "non_ascii_digit": 0, "space_unknown": 0,
                                                           **unresolved})

    def test_device_table(self) -> None:
        self.assertGreaterEqual(len(DEVICE_TABLE), 25)
        self.run_table("device_quality", DEVICE_TABLE)

    def test_claims_table(self) -> None:
        self.assertGreaterEqual(len(CLAIMS_TABLE), 25)
        self.run_table("claims_integrity", CLAIMS_TABLE)

    def test_every_hyphen_like_character_is_a_separator(self) -> None:
        canon = Canonicaliser(pack("device_quality"))
        for ch in canonical.HYPHEN_LIKES:
            with self.subTest(char=hex(ord(ch))):
                self.assertEqual([(m.entity_id, m.method) for m in canon.scan(f"SD{ch}9").mentions],
                                 [("SD-9", "variant")])

    def test_space_form_of_sd9_needs_sd9_to_be_known(self) -> None:
        d = self.copy()
        self.edit(d, "aliases.json", _del(("product", "SalineDuo")))
        self.edit_fixtures(d, lambda lines: None)
        p = load_pack(str(d))
        unknown = Canonicaliser(p).scan("SD 9")
        self.assertEqual((unknown.mentions, unknown.unresolved["space_unknown"]), ((), 1))
        known = Canonicaliser(p, {"product": ["SD-9"]}).scan("SD 9")
        self.assertEqual([(m.entity_id, m.method) for m in known.mentions], [("SD-9", "variant")])
        with self.assertRaises(ValueError):
            Canonicaliser(p, {"product": ["sd-9"]})
        with self.assertRaises(ValueError):
            Canonicaliser(p, {"nope": ["X"]})

    def test_the_same_alias_in_two_types_gives_one_mention_per_type(self) -> None:
        d = self.copy("claims_integrity")

        def both(obj: dict) -> None:
            obj["repair_shop"]["harbour"] = "RS-1077"
            obj["tow_operator"]["harbour"] = "TW-1200"

        self.edit(d, "aliases.json", both)
        result = Canonicaliser(load_pack(str(d))).scan("towed to harbour today")
        self.assertEqual([(m.entity_type, m.entity_id, m.start, m.end) for m in result.mentions],
                         [("repair_shop", "RS-1077", 9, 16), ("tow_operator", "TW-1200", 9, 16)])

    def test_resolve_exact(self) -> None:
        canon = Canonicaliser(pack("device_quality"))
        self.assertIsNone(canon.resolve_exact("product", "The SD-9 cracked."))
        self.assertIsNone(canon.resolve_exact("product", "Anna Berger"))
        self.assertIsNone(canon.resolve_exact("product", "SD-"))
        self.assertIsNone(canon.resolve_exact("product", "SD-9 SD-12"))
        self.assertIsNone(canon.resolve_exact("nope", "SD-9"))
        self.assertIsNone(canon.resolve_exact("product", None))
        m = canon.resolve_exact("component", "BATTERY-DOOR")
        self.assertEqual((m.entity_id, m.method, m.res_conf), ("BATTERY-DOOR", "exact", 1.0))
        self.assertEqual(canon.scan("BATTERY-DOOR failed").mentions, ())
        self.assertEqual(canon.resolve_exact("component", "Battery door").method, "alias")
        self.assertEqual(canon.resolve_exact("product", "  sd-9 ").entity_id, "SD-9")
        self.assertTrue(canon.is_known("product", "SD-9"))
        self.assertFalse(canon.is_known("product", "SD-12"))

    def test_scans_are_deterministic_and_spans_index_the_original(self) -> None:
        canon = Canonicaliser(pack("device_quality"))
        text = "\uff33\uff24\uff0d\uff19 and SD\u200b-12 near the pump housing, lot l10001, supplier V1001."
        a, b = canon.scan(text), canon.scan(text)
        self.assertEqual(a, b)
        self.assertEqual([m.text for m in a.mentions], [text[m.start:m.end] for m in a.mentions])
        self.assertEqual([m.entity_id for m in a.mentions], ["SD-9", "SD-12", "PUMP-HOUSING", "L10001", "V1001"])

    def test_folding_rules(self) -> None:
        self.assertEqual(normalise("A\u200bB\uff21")[0], "ABA")
        self.assertEqual(normalise("A\u200bB")[1], (0, 2, 3))
        folded, index = fold_phrase("\u00dc\u2013X_y  \u00a0Z \u0130")
        self.assertEqual(folded, "\u00fc-x-y z \u0130")
        self.assertEqual(len(index), len(folded) + 1)
        self.assertEqual(fold_phrase("\ufb01 \u00df")[0], "\ufb01 \u00df")


# =================================================================================================== disclaimer

OFFICIAL = re.compile(r"(official|authoritative) (FDA|IMDRF)", re.IGNORECASE)


INVISIBLE = "\u200b\u200c\u200d\u2060\ufeff\u00a0\u00ad"
G2_TEXT_FILES = ("docs/collective/**/*.md", "docs/collective/examples/*.json", "mycelic/collective/packs/**/*.py",
                 "mycelic/collective/packs/data/**/*.json", "mycelic/collective/packs/data/**/*.jsonl",
                 "mycelic/collective/edge/*.py", "mycelic/collective/experiments/e1_extract.py",
                 "tests/mycelic/test_collective_packs.py", "tests/mycelic/test_collective_extract.py",
                 "tests/mycelic/test_collective_e1.py")


class DisclaimerTests(unittest.TestCase):
    def test_docs_and_g2_files_hold_no_literal_invisible_character(self) -> None:
        """Zero-width characters, NBSP and soft hyphens are written as escapes (\\u200b), never literally: a literal
        one renders as nothing, so a reader cannot see what a doc or a fixture says."""
        files = sorted({p for pattern in G2_TEXT_FILES for p in ROOT.glob(pattern)})
        self.assertGreater(len(files), 40)
        for path in files:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                found = sorted({f"U+{ord(c):04X}" for c in line if c in INVISIBLE})
                self.assertEqual(found, [], f"{path.relative_to(ROOT)}:{number}")

    def test_device_pack_is_illustrative_with_both_phrases(self) -> None:
        p = pack("device_quality")
        self.assertTrue(p.illustrative)
        self.assertIn("illustrative subset", p.disclaimer)
        self.assertIn("not the official FDA or IMDRF code list", p.disclaimer)
        self.assertIn("invented placeholders", p.disclaimer)
        for code in p.codes:
            self.assertTrue(code.startswith("ILL-"), code)

    def test_official_wording_occurs_only_on_the_allow_listed_lines(self) -> None:
        disclaimer = pack("device_quality").disclaimer
        line = f"  \"disclaimer\": {json.dumps(disclaimer, ensure_ascii=False)},"
        allowed = {("mycelic/collective/packs/data/device_quality/pack.json", line),
                   ("docs/collective/PACKS.md", f"> {disclaimer}")}
        # D003's two hand copies (CHOICE-D003.md 6.2 and E1 (c)) keep device_quality's pack.json byte for byte, so
        # its disclaimer line, the same line and nothing else, occurs once in each
        allowed |= {(f"docs/collective/onboard/d003-hand/{name}/pack.json", line)
                    for name in ("device_quality", "device_quality_own")}
        found = set()
        files = [p for p in (ROOT / "mycelic/collective/packs/data/device_quality").rglob("*") if p.is_file()]
        files += [p for p in (ROOT / "docs" / "collective").rglob("*") if p.is_file()]
        for path in files:
            rel = path.relative_to(ROOT).as_posix()
            for line in path.read_text(encoding="utf-8").splitlines():
                if OFFICIAL.search(line):
                    with self.subTest(file=rel, line=line[:80]):
                        self.assertIn((rel, line), allowed)
                    found.add((rel, line))
        self.assertEqual(found, allowed)

    def test_claims_pack_states_it_is_same_author_and_not_x3_evidence(self) -> None:
        p = pack("claims_integrity")
        self.assertTrue(p.illustrative)
        self.assertTrue(p.same_author_as_code)
        self.assertTrue(pack("device_quality").same_author_as_code)
        self.assertIn("X3", p.disclaimer)
        self.assertIn("ictional", p.disclaimer)


# =================================================================================================== generator

def _digest_in_subprocess(pid: str, seed: int, hashseed: str) -> str:
    code = ("import sys; from mycelic.collective.packs.loader import load_pack;"
            "from mycelic.collective.packs.generator import generate, world_digest;"
            f"print(world_digest(generate(load_pack({pid!r}), seed={seed}, sites=6, weeks=52)))")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                       env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONHASHSEED": hashseed})
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    return r.stdout.strip()


class GeneratorTests(TempCase):
    worlds: dict[str, generator.World] = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.worlds = {pid: generate(pack(pid), seed=7, sites=6, weeks=52) for pid in PACKS}

    def test_digest_is_stable_across_hash_seeds_and_changes_with_the_seed(self) -> None:
        for pid in PACKS:
            with self.subTest(pack=pid):
                a = _digest_in_subprocess(pid, 7, "0")
                b = _digest_in_subprocess(pid, 7, "4242")
                self.assertEqual(a, b)
                self.assertEqual(a, world_digest(self.worlds[pid]))
                self.assertNotEqual(world_digest(generate(pack(pid), seed=8, sites=6, weeks=52)), a)

    def test_records_pass_check_record_and_are_synthetic(self) -> None:
        for pid, world in self.worlds.items():
            self.assertGreater(len(world.records), 500)
            for r in world.records:
                check_record(r, pack(pid))
                self.assertIs(r["synthetic"], True)
                self.assertEqual(list(r), sorted(connector.RECORD_KEYS))

    def test_every_non_english_lexicon_language_has_narratives(self) -> None:
        for pid, world in self.worlds.items():
            p = pack(pid)
            languages = {lang for pred in p.predicates.values() for lang in pred.lexicon} - {"en"}
            for lang in sorted(languages):
                with self.subTest(pack=pid, language=lang):
                    self.assertGreaterEqual(sum(1 for r in world.records if r["language"] == lang and r["narrative"]),
                                            1)
        self.assertTrue(any(r["language"] is None for r in self.worlds["device_quality"].records))

    def test_forwarded_share_and_unique_narratives(self) -> None:
        for pid, world in self.worlds.items():
            rate = pack(pid).generator["forwarding_rate"]
            forwards = [r for r in world.records if r["origin_ref"] is not None]
            with self.subTest(pack=pid):
                self.assertLessEqual(abs(len(forwards) / len(world.records) - rate), 0.02)
                independent = [r["narrative"] for r in world.records if r["origin_ref"] is None]
                self.assertEqual(len(independent), len(set(independent)))
                refs = {r["record_ref"]: r for r in world.records}
                for f in forwards:
                    origin = refs[f["origin_ref"]]
                    self.assertNotEqual(origin["site"], f["site"])
                    self.assertEqual(origin["site"], f["origin_site"])
                    self.assertGreaterEqual(f["received_date"], origin["received_date"])
                    self.assertEqual(origin["narrative"], f["narrative"])
                    self.assertEqual(world.gold[f["record_ref"]], world.gold[origin["record_ref"]])

    def test_person_values_and_reporters_hold_no_egress_id(self) -> None:
        for pid, world in self.worlds.items():
            p = pack(pid)
            canon = Canonicaliser(p, {t: v for t, v in p.generator["universe"].items()})
            egress = set(p.egress.egress_entity_types)
            for r in world.records:
                for value in [*r["persons"].values(), r["reporter"]]:
                    with self.subTest(pack=pid, value=value):
                        self.assertFalse([m for m in canon.scan(value).mentions if m.entity_type in egress])

    def test_gold_resolves_and_lexical_extraction_reproduces_it(self) -> None:
        for pid, world in self.worlds.items():
            p = pack(pid)
            canon = Canonicaliser(p)
            lexical = LexicalExtractor(p, canon)
            for r in world.records:
                for g in world.gold[r["record_ref"]]:
                    self.assertTrue(canon.is_canonical(g["entity_type"], g["entity_id"]))
                    self.assertIn(g["predicate"], (None, *p.predicates))
                    if g["entity_text"] is not None:
                        self.assertEqual(canon.resolve_exact(g["entity_type"], g["entity_text"]).entity_id,
                                         g["entity_id"])
                got = {(c.entity_type, c.entity_id, c.predicate, c.negated)
                       for c in lexical.extract(r, codes_channel(r, p, canon)).claims}
                want = {(g["entity_type"], g["entity_id"], g["predicate"], g["negated"])
                        for g in world.gold[r["record_ref"]]}
                with self.subTest(pack=pid, record=r["record_ref"]):
                    self.assertEqual(got, want)

    def test_argument_errors(self) -> None:
        p = pack("device_quality")
        need = p.detectors["baseline_weeks"] + p.detectors["window_weeks"]
        for kwargs in ({"seed": True}, {"seed": "7"}, {"seed": 7.0}, {"weeks": need - 1}, {"sites": 0},
                       {"sites": len(p.generator["sites"]) + 1}, {"start": "2024-01-02"}, {"start": "not a date"}):
            args = {"seed": 7, "sites": 2, "weeks": need, **kwargs}
            with self.subTest(kwargs=kwargs), self.assertRaises(GeneratorError):
                generate(p, **args)
        world = generate(p, seed=1, sites=2, weeks=need, start="2024-03-04")
        self.assertEqual(min(r["received_date"] for r in world.records)[:7], "2024-03")

    def test_master_data_covers_the_chosen_sites(self) -> None:
        p = pack("device_quality")
        world = generate(p, seed=3, sites=3, weeks=34)
        ids = [s["id"] for s in p.generator["sites"][:3]]
        self.assertEqual(list(world.master_data), ids)
        self.assertEqual(sorted({r["site"] for r in world.records}), sorted(ids))
        self.assertEqual(world.master_data[ids[0]]["product"], tuple(p.generator["universe"]["product"]))
        self.assertEqual(world.master_data[ids[1]]["lot"], ("L10001", "L10002", "L20045"))

    def test_id_shaped_person_generators(self) -> None:
        always = {"kind": "pattern", "segments": [{"alpha": [2, 2]}, {"sep": "required"}, {"digit": [1, 2]}],
                  "separator": "-", "case": "upper"}
        d = self.copy(name="always")
        self.edit(d, "generator.json", _set(("persons", "patient_ref"), always))
        with self.assertRaises(GeneratorError) as ctx:
            generate(load_pack(str(d)), seed=1, sites=6, weeks=34)
        self.assertIn("id-shaped", str(ctx.exception))
        d = self.copy(name="sometimes")
        self.edit(d, "generator.json", _set(("persons", "patient_ref"),
                                            {"kind": "choice", "values": ["SD-12", "PT000001", "lot L10001"]}))
        world = generate(load_pack(str(d)), seed=1, sites=6, weeks=34)
        self.assertEqual({r["persons"]["patient_ref"] for r in world.records}, {"PT000001"})

    def test_narrative_uniqueness_failure_is_an_error_not_a_duplicate(self) -> None:
        d = self.copy(name="dull")

        def dull(obj: dict) -> None:
            obj["zero_claim_rate"] = 1.0
            obj["filler"] = {lang: ["Same sentence one.", "Same sentence two.", "Same sentence three.",
                                    "Same sentence four.", "Same sentence five."] for lang in obj["filler"]}

        self.edit(d, "generator.json", dull)
        with self.assertRaises(GeneratorError) as ctx:
            generate(load_pack(str(d)), seed=1, sites=6, weeks=34)
        self.assertIn("uniqueness", str(ctx.exception))


# =================================================================================================== connector

DEVICE_ROW = {
    "complaint_no": "C-0001", "plant": "plant-ashvale", "received_on": "2024-03-04", "lang": "en",
    "problem_codes": ["ILL-0101", "ILL-0101", "ILL-9001"], "model": "SD-9", "lots": ["L10001", " L10002 "],
    "parts": [], "supplier_no": 1001, "description": "The SD-9 cracked.",
    "notes": [{"kind": "investigation", "body": "Housing replaced."}, {"kind": "billing", "body": "SECRET-BILL"}],
    "patient": {"name": "Jonas Okafor", "ref": "PT000001"}, "clinician": None, "reported_by": "RPT-0001",
    "internal_cost": 4711, "secret_note": "CANARY-VENDOR-SECRET",
}
OPENFDA_EVENT = {
    "mdr_report_key": 9876543, "date_received": "20240305", "event_type": "Malfunction",
    "device": [{"model_number": "SD-12", "lot_number": "L20045", "generic_name": "pump"},
               {"model_number": "SD-12", "lot_number": None}],
    "product_problems": ["Crack", "Unknown Problem Name"],
    "mdr_text": [{"text_type_code": "Description of Event or Problem", "text": "The housing cracked."},
                 {"text_type_code": "Additional Manufacturer Narrative", "text": "CANARY-MFR-NARRATIVE"},
                 {"text_type_code": "Description of Event or Problem", "text": "It also leaked."}],
    "manufacturer_g1_name": "CANARY-MANUFACTURER",
}


class ConnectorTests(TempCase):
    def test_vendor_rows_map_to_record_keys(self) -> None:
        p = pack("device_quality")
        mapped = map_rows([DEVICE_ROW], p)
        self.assertEqual((mapped.rows, mapped.rejected, mapped.unmapped_code_values), (1, {}, 0))
        record = mapped.records[0]
        self.assertEqual(list(record), list(connector.RECORD_KEYS))
        self.assertEqual(record["codes"], ["ILL-0101", "ILL-9001"])
        self.assertEqual(dict(record["entities"]), {"component": [], "lot": ["L10001", "L10002"],
                                                    "product": ["SD-9"], "supplier": ["1001"]})
        self.assertEqual(record["narrative"], "The SD-9 cracked.\n\nHousing replaced.")
        self.assertEqual(record["persons"], {"clinician_name": None, "patient_name": "Jonas Okafor",
                                             "patient_ref": "PT000001"})
        self.assertEqual((record["site"], record["reporter"], record["origin_ref"]), ("plant-ashvale", "RPT-0001",
                                                                                      None))
        text = canonical_dumps(list(mapped.records))
        for secret in ("CANARY-VENDOR-SECRET", "internal_cost", "4711", "SECRET-BILL", "secret_note"):
            self.assertNotIn(secret, text)
        check_record(record, p)

    def test_claims_vendor_rows(self) -> None:
        p = pack("claims_integrity")
        row = {"claim_id": "CLM-1", "line_of_business": "motor-north", "reported": "2024-05-06", "language": "en",
               "flags": ["FLAG-04"], "shop_code": "RS-0042", "clinic_code": None, "tow_ref": "TW0007",
               "damage": ["front bumper"], "adjuster_notes": "RS-42 billed twice.",
               "claimant": {"name": "Maria Novak", "policy": "POL1234567", "phone": "555-014-2231"},
               "vehicle": {"plate": "KX 41 BRD"}, "handler_id": "ADJ-NORTH-1",
               "copied_from": {"claim_id": "CLM-0", "line_of_business": "motor-south"}, "fraud_score": 0.97}
        record = map_rows([row], p).records[0]
        self.assertEqual(record["entities"]["repair_shop"], ["RS-0042"])
        self.assertEqual((record["origin_ref"], record["origin_site"]), ("CLM-0", "motor-south"))
        self.assertNotIn("fraud_score", canonical_dumps(record))
        self.assertNotIn("0.97", canonical_dumps(record))
        check_record(record, p)

    def test_openfda_event_maps_through_mapping_openfda(self) -> None:
        p = pack("device_quality")
        mapped = map_rows([OPENFDA_EVENT], p, mapping="mapping_openfda", site="public")
        self.assertEqual((mapped.rejected, mapped.unmapped_code_values), ({}, 1))
        record = mapped.records[0]
        self.assertEqual(list(record), list(connector.RECORD_KEYS))
        self.assertEqual((record["record_ref"], record["received_date"], record["site"]),
                         ("9876543", "2024-03-05", "public"))
        self.assertEqual(record["codes"], ["ILL-0101"])
        self.assertEqual(dict(record["entities"]), {"lot": ["L20045"], "product": ["SD-12"]})
        self.assertEqual(record["narrative"], "The housing cracked.\n\nIt also leaked.")
        self.assertEqual((record["persons"], record["language"], record["synthetic"]), ({}, None, False))
        text = canonical_dumps(record)
        for secret in ("CANARY-MFR-NARRATIVE", "CANARY-MANUFACTURER", "Malfunction", "pump"):
            self.assertNotIn(secret, text)
        check_record(record, p, "mapping_openfda")
        self.assertEqual(connector.record_mapping(record, p), "mapping_openfda")

    def test_rejections_are_counted_per_reason(self) -> None:
        p = pack("device_quality")
        rows = [
            DEVICE_ROW,
            {**DEVICE_ROW},                                                     # duplicate_record_ref
            "not an object",                                                    # not_object
            {k: v for k, v in DEVICE_ROW.items() if k != "complaint_no"},       # missing_record_ref
            {**DEVICE_ROW, "complaint_no": "C-2", "received_on": None},         # missing_received_date
            {**DEVICE_ROW, "complaint_no": "C-3", "received_on": "2024-02-30"},  # bad_date
            {**DEVICE_ROW, "complaint_no": "C-4", "model": ["SD-9", {"x": 1}]},  # bad_type
            {**DEVICE_ROW, "complaint_no": "C-5", "plant": "Bad Plant!"},       # bad_site
            {**DEVICE_ROW, "complaint_no": "C-6", "description": "", "notes": []},  # missing_narrative
            {**DEVICE_ROW, "complaint_no": "C-7", "plant": None},               # missing_site
            {**DEVICE_ROW, "complaint_no": "C-8", "complaint_no_extra": 1, "lots": "L10001"},  # bad_type ([] path)
        ]
        mapped = map_rows(rows, p)
        self.assertEqual(len(mapped.records), 1)
        self.assertEqual(mapped.rejected, {"bad_date": 1, "bad_site": 1, "bad_type": 2, "duplicate_record_ref": 1,
                                           "missing_narrative": 1, "missing_received_date": 1,
                                           "missing_record_ref": 1, "missing_site": 1, "not_object": 1})
        self.assertEqual(mapped.rows, len(rows))

    def test_a_ref_first_seen_on_a_rejected_row_is_not_a_duplicate(self) -> None:
        p = pack("device_quality")
        rows = [{**DEVICE_ROW, "received_on": "2024-02-30"},                   # bad_date: no record produced
                DEVICE_ROW,                                                     # the first record with that ref
                {**DEVICE_ROW, "description": "Another text."}]                 # duplicate_record_ref
        mapped = map_rows(rows, p)
        self.assertEqual([r["record_ref"] for r in mapped.records], [DEVICE_ROW["complaint_no"]])
        self.assertEqual(mapped.rejected, {"bad_date": 1, "duplicate_record_ref": 1})

    def test_read_jsonl_with_bom_invalid_lines_and_a_missing_site(self) -> None:
        p = pack("device_quality")
        path = self.dir / "export.jsonl"
        path.write_bytes(b"\xef\xbb\xbf" + json.dumps(DEVICE_ROW).encode() + b"\r\n\n{broken\n"
                         + json.dumps({**DEVICE_ROW, "complaint_no": "C-9"}).encode() + b"\n")
        mapped = read_jsonl(path, p)
        self.assertEqual((len(mapped.records), mapped.rejected, mapped.rows), (2, {"invalid_json": 1}, 3))
        with self.assertRaises(ConnectorError):
            read_jsonl(self.dir / "absent.jsonl", p)
        with self.assertRaises(ConnectorError):
            map_rows([OPENFDA_EVENT], p, mapping="mapping_openfda")
        with self.assertRaises(ConnectorError):
            map_rows([OPENFDA_EVENT], p, mapping="mapping_openfda", site="Not A Site")
        with self.assertRaises(ConnectorError):
            map_rows([], pack("claims_integrity"), mapping="mapping_openfda", site="public")

    def test_record_problems(self) -> None:
        p = pack("device_quality")
        good = dict(p.fixtures[0].record)
        good = json.loads(json.dumps({k: (dict(v) if isinstance(v, MappingProxyType) else v)
                                      for k, v in good.items()}, default=list))
        self.assertEqual(connector.record_problems(good, p), [])
        for change in ({"record_ref": ""}, {"site": "Bad"}, {"received_date": "2024-13-01"}, {"codes": ["A", "A"]},
                       {"entities": {}}, {"narrative": None}, {"persons": {}}, {"reporter": 3},
                       {"origin_ref": "x"}, {"synthetic": "yes"}, {"language": 1}):
            with self.subTest(change=change):
                self.assertTrue(connector.record_problems({**good, **change}, p))
                with self.assertRaises(ConnectorError):
                    check_record({**good, **change}, p)
        self.assertTrue(connector.record_problems([], p))
        self.assertTrue(connector.record_problems({**good, "extra": 1}, p))


if __name__ == "__main__":
    unittest.main()
