"""G3: the site boundary. ISO weeks, the per-site record store, k-suppressed closed-week count cells, the Boundary
(the only path out of a site), late arrivals, master data, forwarded records and windowed usage summaries.

Every test works in a temporary directory; nothing connects anywhere. Numbers printed by the property tests are
synthetic and are printed, never asserted.
"""
from __future__ import annotations

import ast
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping
from unittest import mock

from mycelic.collective.edge import site as site_mod
from mycelic.collective.edge.egress import (ARTIFACT_TYPES, CHANNELS, KEYWORDS, LOG_KEYS, SUPPRESSED, Boundary,
                                            EgressError, artifact_keys, read_log, schema_words)
from mycelic.collective.edge.extract import TASK_NAME, Claim, lexical_handler
from mycelic.collective.edge.records import TABLES, InputRow, RecordStore, StoreError
from mycelic.collective.edge.site import (EXTRACT_BATCH, REJECT_REASONS, EdgeSite, SiteError, build_cells,
                                          valid_claim)
from mycelic.collective.edge.weeks import (closed_through, iso_week, local_date, next_week, valid_week, week_monday,
                                           week_sunday)
from mycelic.collective.inference.errors import KINDS, InferenceBoundaryError
from mycelic.collective.inference.fake import FakeProvider
from mycelic.collective.inference.ledger import read_ledger, summarise, usage_summary
from mycelic.collective.inference.routing import parse_routing
from mycelic.collective.inference.runtime import Runtime
from mycelic.collective.jsonio import canonical_bytes, canonical_dumps, sha256_hex
from mycelic.collective.packs.canonical import Canonicaliser
from mycelic.collective.packs.connector import RECORD_KEYS
from mycelic.collective.packs.generator import generate
from mycelic.collective.packs.loader import BUILTIN_ROOT, FrozenPack, load_pack
from tests.mycelic.test_collective_guards import path_snapshot

ROOT = Path(__file__).resolve().parents[2]
PACK_IDS = ("device_quality", "claims_integrity")
PACKS = {pid: load_pack(pid) for pid in PACK_IDS}
DQ = PACKS["device_quality"]
# per pack: (a specific code, its predicate, an id-format type, five ids of that type in the universe)
KEY = {
    "device_quality": ("ILL-0101", "crack", "product", ("SD-9", "SD-12", "SD-40", "IP-7", "IP-21")),
    "claims_integrity": ("FLAG-01", "supplement_after_teardown", "repair_shop",
                         ("RS-42", "RS-5", "RS-310", "RS-1077", "RS-2290")),
}
T_MID_W11 = "2026-03-11T08:00:00.000Z"                 # a Wednesday in 2026-W11
PLANTED = "Qzxvmrbl hinge split, wobbly"               # narrative text pushed at the Boundary
_SQL_STATEMENT = re.compile(r"\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|PRAGMA|BEGIN|COMMIT|ROLLBACK|REPLACE|"
                            r"WITH|VACUUM|ATTACH)\b")


class Clock:
    def __init__(self, value: str = T_MID_W11) -> None:
        self.value = value

    def __call__(self) -> str:
        return self.value


def pack_copy(root: Path, pid: str, edits: Mapping[tuple[Any, ...], Any] | None = None) -> FrozenPack:
    """A copy of a built-in pack under ``root`` with JSON edits ``{(file, key, ...): value}``, loaded by path."""
    dest = Path(tempfile.mkdtemp(dir=root, prefix=f"pack-{pid}-"))
    shutil.rmtree(dest)
    shutil.copytree(BUILTIN_ROOT / pid, dest)
    for (file, *path), value in (edits or {}).items():
        obj = json.loads((dest / file).read_text(encoding="utf-8"))
        node = obj
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        (dest / file).write_text(json.dumps(obj, indent=2), encoding="utf-8")
    return load_pack(dest)


def record(pack: FrozenPack, ref: str, site: str, day: Any, codes: Any = (), entities: dict | None = None,
           narrative: str = "", persons: dict | None = None, reporter: str | None = "R1",
           origin: tuple[str, str] | None = None, synthetic: bool = True) -> dict[str, Any]:
    m = pack.mapping()
    ents = {t: [] for t in m["entities"]}
    ents.update(entities or {})
    people = {f: None for f in m["persons"]}
    people.update(persons or {})
    out = {"record_ref": ref, "site": site, "received_date": day, "language": pack.languages[0],
           "codes": list(codes), "entities": ents, "narrative": narrative, "persons": people, "reporter": reporter,
           "origin_ref": origin[0] if origin else None, "origin_site": origin[1] if origin else None,
           "synthetic": synthetic}
    assert sorted(out) == sorted(RECORD_KEYS)
    return out


def coded(pack: FrozenPack, ref: str, site: str, day: str, entity_id: str, **kw: Any) -> dict[str, Any]:
    """A record whose only claim is (KEY type, entity_id, KEY predicate) through the codes channel."""
    code, _, t, _ = KEY[pack.id]
    return record(pack, ref, site, day, codes=[code], entities={t: [entity_id]}, **kw)


def universe_master(pack: FrozenPack) -> dict[str, tuple[str, ...]]:
    return {t: tuple(ids) for t, ids in pack.generator["universe"].items()
            if pack.entity_types[t].id_format is not None}


def fake_runtime(pack: FrozenPack, site_id: str, ledger: Path, clock: Callable[[], str], *,
                 provider: FakeProvider | None = None, simulation: bool = False) -> Runtime:
    config = parse_routing({"schema_version": 1,
                            "endpoints": {"site-fake": {"provider": "fake", "boundary": f"site:{site_id}"}},
                            "routes": {TASK_NAME: {"endpoint": "site-fake"}}}, allow_fake=True)
    if provider is None:
        provider = FakeProvider()
        provider.register(TASK_NAME, lexical_handler(pack, Canonicaliser(pack)))
    return Runtime(config, boundary=f"site:{site_id}", ledger_path=ledger, run_id="g3-test", clock=clock,
                   data_label="synthetic", allow_fake=True, fake=provider, sleep=lambda s: None, environ={},
                   simulation=simulation)


def db_rows(path: Path, sql: str, args: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def string_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]


class SiteCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.clock = Clock()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def site(self, pack: FrozenPack = DQ, site_id: str = "s1", *, master: Mapping[str, Any] | None = None,
             runtime: Runtime | None = None, root: Path | None = None) -> EdgeSite:
        root = self.tmp if root is None else root
        s = EdgeSite(pack, site_id, root / "edge", runtime=runtime, clock=self.clock,
                     master_data=universe_master(pack) if master is None else master, hq_dir=root / "hq")
        self.addCleanup(s.close)
        return s

    def runtime(self, pack: FrozenPack = DQ, site_id: str = "s1", **kw: Any) -> Runtime:
        rt = fake_runtime(pack, site_id, self.tmp / "edge" / f"site-{site_id}.ledger.jsonl", self.clock, **kw)
        self.addCleanup(rt.close)
        return rt

    def hq_lines(self) -> list[bytes]:
        path = self.tmp / "hq" / "receive.jsonl"
        return path.read_bytes().splitlines(keepends=True) if path.exists() else []

    def cells(self, emission: Any) -> dict[tuple[str, ...], dict[str, Any]]:
        return {(c["entity_type"], c["entity_id"], c["predicate"], c["iso_week"], c["channel"]): c
                for c in emission.body["cells"]}


# --------------------------------------------------------------------------------------------------- weeks

class WeekTests(unittest.TestCase):
    def test_iso_week_year_edges(self) -> None:
        for day, week in (("2026-12-28", "2026-W53"), ("2027-01-01", "2026-W53"), ("2027-01-03", "2026-W53"),
                          ("2027-01-04", "2027-W01"), ("2024-12-30", "2025-W01"), ("2026-03-11", "2026-W11")):
            with self.subTest(day=day):
                self.assertEqual(iso_week(day), week)
                self.assertEqual(iso_week(date.fromisoformat(day)), week)
        with self.assertRaises(ValueError):
            iso_week("2026-02-30")

    def test_valid_week(self) -> None:
        for text in ("2026-W53", "2027-W01", "2020-W53", "2025-W52"):
            self.assertTrue(valid_week(text), text)
        for text in ("2025-W53", "2026-W00", "2026-W5", "2026-W54", "２０２６-W53", "2026-W53\n",
                     "2026-w53", "", None, 202653, "0000-W01"):
            with self.subTest(text=text):
                self.assertFalse(valid_week(text))

    def test_next_week_and_bounds(self) -> None:
        self.assertEqual(next_week("2026-W53"), "2027-W01")
        self.assertEqual(next_week("2025-W52"), "2026-W01")
        self.assertEqual(next_week("2026-W52"), "2026-W53")
        self.assertEqual(next_week("2026-W09"), "2026-W10")
        self.assertEqual(week_monday("2026-W53"), date(2026, 12, 28))
        self.assertEqual(week_sunday("2026-W53"), date(2027, 1, 3))
        for bad in ("2025-W53", "x"):
            with self.assertRaises(ValueError):
                next_week(bad)

    def test_closed_through(self) -> None:
        self.assertEqual(closed_through("2026-03-11", 7), "2026-W09")       # Wednesday: W11 and W10 are open
        self.assertEqual(closed_through("2026-03-08", 0), "2026-W10")       # a Sunday closes its own week at lag 0
        self.assertEqual(closed_through("2026-03-07", 0), "2026-W09")
        self.assertEqual(closed_through("2027-01-11", 7), "2026-W53")
        self.assertEqual(closed_through(date(2027, 1, 10), 7), "2026-W53")      # Sunday 2027-01-03 plus 7 days
        self.assertEqual(closed_through(date(2027, 1, 9), 7), "2026-W52")
        for lag in (-1, True, 1.0):
            with self.assertRaises(ValueError):
                closed_through("2026-03-11", lag)

    def test_week_strings_compare_as_strings(self) -> None:
        weeks = [iso_week(date(2024, 1, 1) + timedelta(days=d)) for d in range(0, 1200, 3)]
        self.assertEqual(weeks, sorted(weeks))
        self.assertLess("2026-W53", "2027-W01")

    def test_local_date(self) -> None:
        cases = {
            "2026-03-01": "2026-03-01",
            "2026-03-01T23:30:00-05:00": "2026-03-01",      # never converted to UTC
            "2026-03-02T00:30:00+05:00": "2026-03-02",
            "2026-03-01T23:30Z": "2026-03-01",
            "2026-03-01T23:30": "2026-03-01",
            "2026-03-01T23:59:59.123456789+14:59": "2026-03-01",
            "2026-03-01T10:00:00+15:00": None,
            "2026-03-01T10:00:00+14:60": None,
            "2026-02-30": None,
            "2026-02-30T10:00Z": None,
            "2026-03-01T25:00Z": None,
            "2026-03-01T24:00Z": None,
            "2026-03-01T23:60Z": None,
            "2026-03-01T23:59:60Z": None,
            "2026-03-01 10:00": None,
            "2026-03-01T10:00:00.Z": None,
            "２０２６-03-01": None,
            "2026-03-0١": None,
            "": None,
            " 2026-03-01": None,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(local_date(value), expected)
        for value in (None, 20260301, date(2026, 3, 1), b"2026-03-01"):
            with self.subTest(value=value):
                self.assertIsNone(local_date(value))


# --------------------------------------------------------------------------------------------------- record store

class RecordStoreTests(SiteCase):
    def stored(self, site: EdgeSite, columns: str = "record_ref, root_ref, narrative_key, forwarded_in") -> dict:
        return {row[0]: row[1:] for row in db_rows(site.store.path, f"SELECT {columns} FROM records ORDER BY seq")}

    def test_duplicate_ref_first_wins(self) -> None:
        s = self.site()
        first = record(DQ, "r1", "s1", "2026-03-02", narrative="First text.")
        got = s.ingest([first])
        self.assertEqual((got.ingested, got.duplicates), (1, 0))
        again = s.ingest([{**first, "narrative": "Other text."}, record(DQ, "r2", "s1", "2026-03-02"),
                          record(DQ, "r2", "s1", "2026-03-03", narrative="later copy")])
        self.assertEqual((again.ingested, again.duplicates), (1, 2))
        rows = dict(db_rows(s.store.path, "SELECT record_ref, narrative FROM records ORDER BY seq"))
        self.assertEqual(rows, {"r1": "First text.", "r2": ""})
        self.assertEqual(db_rows(s.store.path, "SELECT seq FROM records ORDER BY seq"), [(1,), (2,)])

    def test_same_site_folded_narrative_shares_root(self) -> None:
        s = self.site()
        s.ingest([record(DQ, "r1", "s1", "2026-03-02", narrative="Pump  CRACKED near hinge.")])
        s.ingest([record(DQ, "r2", "s1", "2026-03-03", narrative="pump cracked near hinge."),
                  record(DQ, "r3", "s1", "2026-03-03", narrative="Another text."),
                  record(DQ, "r4", "s1", "2026-03-03", narrative="another   TEXT.")])
        rows = self.stored(s)
        self.assertEqual(rows["r2"][0], "r1")
        self.assertEqual(rows["r3"][0], "r3")
        self.assertEqual(rows["r4"][0], "r3")                     # earlier record of the same batch
        self.assertEqual(rows["r1"][1], rows["r2"][1])

    def test_cross_site_copy_without_origin_is_independent(self) -> None:
        a, b = self.site(site_id="a"), self.site(site_id="b")
        a.ingest([record(DQ, "r1", "a", "2026-03-02", narrative="Pump cracked.")])
        b.ingest([record(DQ, "r2", "b", "2026-03-02", narrative="Pump cracked.")])
        self.assertEqual(self.stored(b)["r2"][0], "r2")           # a documented limitation (not_covered, X5)

    def test_empty_narratives_never_share_a_root(self) -> None:
        s = self.site()
        s.ingest([record(DQ, f"r{i}", "s1", "2026-03-02", narrative=text) for i, text in enumerate(("", "  ", ""))])
        rows = self.stored(s)
        self.assertEqual({ref: v[0] for ref, v in rows.items()}, {"r0": "r0", "r1": "r1", "r2": "r2"})
        self.assertEqual({v[1] for v in rows.values()}, {None})

    def test_origin_sets_root_and_forwarded_in_only_for_another_site(self) -> None:
        s = self.site()
        got = s.ingest([record(DQ, "f1", "s1", "2026-03-02", narrative="x", origin=("o1", "other")),
                        record(DQ, "f2", "s1", "2026-03-02", origin=("o2", "s1")),
                        record(DQ, "f3", "s1", "2026-03-02", narrative="x")])
        self.assertEqual(got.forwarded_in, 1)
        rows = self.stored(s)
        self.assertEqual(rows["f1"][0], "o1")
        self.assertEqual(rows["f1"][2], 1)
        self.assertEqual((rows["f2"][0], rows["f2"][2]), ("o2", 0))
        self.assertEqual(rows["f3"][0], "o1")                     # earliest record with the same narrative

    def test_rejections_are_counted_and_never_stored(self) -> None:
        s = self.site()
        base = record(DQ, "r", "s1", "2026-03-02")
        missing = dict(base)
        del missing["received_date"]
        bad_dates = [missing] + [{**base, "received_date": v} for v in (
            None, "", "2026-02-30", "2026-03-01T25:00Z", "2026-03-01 10:00", "２０２６-03-02",
            20260302, "2026-03-01T10:00:00+15:00")]
        bad_records = ["not a record", ["r"], None, {**base, "note": "x"}, {**base, "codes": "ILL-0101"},
                       {**base, "synthetic": "yes"}, {**base, "site": "Bad Site"}]
        got = s.ingest(bad_dates + bad_records + [{**base, "site": "s2"}])
        self.assertEqual(dict(got.rejected), {"bad_record": len(bad_records), "bad_date": len(bad_dates),
                                              "wrong_site": 1})
        self.assertEqual(tuple(got.rejected), REJECT_REASONS)
        self.assertEqual(got.ingested, 0)
        self.assertEqual(db_rows(s.store.path, "SELECT count(*) FROM records ORDER BY 1"), [(0,)])

    def test_timestamp_received_date_uses_the_recorded_local_date(self) -> None:
        s = self.site()
        s.ingest([record(DQ, "r1", "s1", "2026-03-01T23:30:00-05:00"),
                  record(DQ, "r2", "s1", "2026-03-02T00:30:00+05:00")])
        rows = db_rows(s.store.path, "SELECT record_ref, received_date, iso_week FROM records ORDER BY seq")
        self.assertEqual(rows, [("r1", "2026-03-01", "2026-W09"), ("r2", "2026-03-02", "2026-W10")])

    def test_one_wal_file_per_site(self) -> None:
        a, b = self.site(site_id="a"), self.site(site_id="b")
        for s, sid in ((a, "a"), (b, "b")):
            s.ingest([record(DQ, "r1", sid, "2026-03-02")])
        names = sorted(p.name for p in (self.tmp / "edge").iterdir() if p.suffix == ".sqlite3")
        self.assertEqual(names, ["site-a.sqlite3", "site-b.sqlite3"])
        for name in names:
            self.assertEqual(db_rows(self.tmp / "edge" / name, "PRAGMA journal_mode"), [("wal",)])

    def test_wal_unavailable_raises(self) -> None:
        with self.assertRaises(StoreError):
            RecordStore(":memory:", site_id="a", pack_id=DQ.id, config_hash=DQ.config_hash)

    def test_no_fabric_table_name(self) -> None:
        source = (ROOT / "mycelic" / "store.py").read_text(encoding="utf-8")
        fabric = set(re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?([A-Za-z_][A-Za-z0-9_]*)", source))
        self.assertGreaterEqual(fabric, {"meta", "orgs", "agents", "memories", "lineage_edges", "events", "rules",
                                         "audit_log"})
        fabric |= {"memories", "events", "outbox"}
        self.assertEqual(set(TABLES) & fabric, set())
        s = self.site()
        names = {n for (n,) in db_rows(s.store.path, "SELECT name FROM sqlite_master WHERE type = 'table' "
                                                       "ORDER BY name")}
        self.assertEqual(names, set(TABLES))

    def test_no_fabric_database_and_the_worktree_is_untouched(self) -> None:
        before = path_snapshot(ROOT)
        s = self.site(runtime=self.runtime())
        s.ingest([coded(DQ, f"r{i}", "s1", "2026-03-02", "SD-9", reporter=f"R{i}") for i in range(3)])
        s.extract("model")
        self.clock.value = "2026-03-29T08:00:00.000Z"
        self.assertIsNotNone(s.emit_cells("2026-03-29"))
        self.assertIsNotNone(s.emit_usage("2026-03-29"))
        self.assertEqual(list(self.tmp.rglob("mycelic.db")), [])
        self.assertEqual(path_snapshot(ROOT), before)

    def test_sql_lives_only_in_records_and_every_select_is_ordered(self) -> None:
        collective = ROOT / "mycelic" / "collective"
        selects = [s for s in string_constants(collective / "edge" / "records.py") if s.lstrip().startswith("SELECT")]
        self.assertGreaterEqual(len(selects), 10)
        for s in selects:
            self.assertIn("ORDER BY", s, s)
        for rel in ("edge/site.py", "edge/egress.py", "edge/weeks.py", "leakage.py", "experiments/g0_canary.py"):
            with self.subTest(module=rel):
                source = (collective / rel).read_text(encoding="utf-8")
                self.assertEqual([s for s in string_constants(collective / rel) if _SQL_STATEMENT.match(s)], [])
                self.assertNotIn("import sqlite3", source)

    def test_reopen_with_another_site_pack_or_config_raises(self) -> None:
        path = self.tmp / "store.sqlite3"
        RecordStore(path, site_id="a", pack_id=DQ.id, config_hash=DQ.config_hash).close()
        for kwargs, key in (({"site_id": "b"}, "site_id"), ({"pack_id": "other_pack"}, "pack_id"),
                            ({"config_hash": "0" * 64}, "config_hash")):
            args = {"site_id": "a", "pack_id": DQ.id, "config_hash": DQ.config_hash, **kwargs}
            with self.subTest(key=key):
                with self.assertRaises(StoreError) as cm:
                    RecordStore(path, **args)
                self.assertEqual(str(cm.exception), f"site_info mismatch: {key}")
        RecordStore(path, site_id="a", pack_id=DQ.id, config_hash=DQ.config_hash).close()

    def test_invalid_claims_are_dropped_and_counted(self) -> None:
        canon = Canonicaliser(DQ)
        good = Claim("product", "SD-9", "crack", "codes", "codes", 1.0)
        bad = [Claim("vehicle", "SD-9", "crack", "codes", "codes", 1.0),
               Claim("product", "SD-9", "melted", "codes", "codes", 1.0),
               Claim("product", "sd-9", "crack", "codes", "codes", 1.0),
               Claim("product", "SD-9", "crack", "rumour", "codes", 1.0),
               Claim("product", "SD-9", "crack", "codes", "codes", 0.5),
               Claim("product", "SD-9", "crack", "codes", "codes", True),
               Claim("component", "BATTERY-LID", "crack", "codes", "codes", 1.0)]
        self.assertTrue(valid_claim(good, DQ, canon))
        for c in bad:
            with self.subTest(claim=c):
                self.assertFalse(valid_claim(c, DQ, canon))
        real_sense = site_mod.sense

        def sense(*args: Any, **kwargs: Any) -> Any:
            codes, text, _ = real_sense(*args, **kwargs)
            return codes, text, (good, *bad)

        s = self.site()
        s.ingest([record(DQ, "r1", "s1", "2026-03-02")])
        with mock.patch.object(site_mod, "sense", sense):
            summary = s.extract("lexical")
        self.assertEqual((summary.claims, summary.invalid_claims), (1, len(bad)))
        self.assertEqual(db_rows(s.store.path, "SELECT entity_id FROM claims ORDER BY entity_id"), [("SD-9",)])
        self.assertEqual(db_rows(s.store.path, "SELECT invalid_claims FROM extraction_stats ORDER BY 1"),
                         [(len(bad),)])

    def test_a_model_repeating_a_claim_is_stored_once_and_counted_once(self) -> None:
        provider = FakeProvider()
        item = {"entity_type": "product", "entity_text": "SD-9", "predicate": "crack", "negated": False}
        provider.register(TASK_NAME, lambda payload: {"claims": [item, item]})
        s = self.site(runtime=self.runtime(provider=provider))
        s.ingest([record(DQ, f"r{i}", "s1", "2026-03-02", narrative="The SD-9 cracked.", reporter=f"R{i}")
                  for i in range(3)])
        summary = s.extract("model")
        self.assertEqual((summary.claims, summary.drops["duplicate"]), (3, 3))
        self.assertEqual(db_rows(s.store.path, "SELECT count(*) FROM claims ORDER BY 1"), [(3,)])
        self.clock.value = "2026-03-29T08:00:00.000Z"
        cell = self.cells(s.emit_cells("2026-03-29"))[("product", "SD-9", "crack", "2026-W10", "text_only")]
        self.assertEqual((cell["n"], cell["n_roots"], cell["n_reporters"]), (3, "<k", 3))  # one narrative root

    def test_extraction_runs_in_batches_and_the_first_wins(self) -> None:
        s = self.site()
        s.ingest([record(DQ, f"r{i:04d}", "s1", "2026-03-02") for i in range(EXTRACT_BATCH * 2 + 5)])
        self.assertEqual(s.extract("lexical").records, EXTRACT_BATCH * 2 + 5)
        self.assertEqual(s.extract("lexical").records, 0)
        s.ingest([record(DQ, "late", "s1", "2026-03-03")])
        self.assertEqual(s.extract("lexical").records, 1)
        with self.assertRaises(SiteError):
            s.extract("regex")
        with self.assertRaises(SiteError):
            s.extract("model")                                      # no runtime

    def test_a_boundary_refusal_propagates_and_saves_nothing(self) -> None:
        config = parse_routing({"schema_version": 1,
                                "endpoints": {"hq-fake": {"provider": "fake", "boundary": "central"}},
                                "routes": {TASK_NAME: {"endpoint": "hq-fake"}}}, allow_fake=True)
        provider = FakeProvider()
        provider.register(TASK_NAME, lexical_handler(DQ, Canonicaliser(DQ)))
        rt = Runtime(config, boundary="site:s1", ledger_path=self.tmp / "ledger.jsonl", run_id="g3-test",
                     clock=self.clock, data_label="synthetic", allow_fake=True, fake=provider, environ={})
        self.addCleanup(rt.close)
        s = self.site(runtime=rt)
        s.ingest([record(DQ, f"r{i}", "s1", "2026-03-02", narrative="The SD-9 cracked.") for i in range(3)])
        with self.assertRaises(InferenceBoundaryError):
            s.extract("model")
        self.assertEqual(db_rows(s.store.path, "SELECT count(*) FROM extraction_stats ORDER BY 1"), [(0,)])
        self.assertEqual(s.store.pending_through("2026-W10"), 3)

    def test_constructor_checks(self) -> None:
        with self.assertRaises(SiteError):
            EdgeSite(DQ, "Bad Site", self.tmp, runtime=None, clock=self.clock, master_data={}, hq_dir=self.tmp)
        with self.assertRaises(SiteError):
            EdgeSite(DQ, "s1", self.tmp, runtime=self.runtime(site_id="s2"), clock=self.clock, master_data={},
                     hq_dir=self.tmp)
        for master in ({"vehicle": ["X"]}, {"product": ["sd-9"]}):
            with self.subTest(master=master), self.assertRaises(SiteError):
                EdgeSite(DQ, "s1", self.tmp, runtime=None, clock=self.clock, master_data=master, hq_dir=self.tmp)
        s = self.site()
        self.clock.value = "2026-03-11 08:00"
        with self.assertRaises(ValueError):
            s.ingest([record(DQ, "r1", "s1", "2026-03-02")])


# --------------------------------------------------------------------------------------------------- cells

def high_volume_pack(root: Path, pid: str) -> FrozenPack:
    return pack_copy(root, pid, {("detectors.json", "baseline_weeks"): 4, ("detectors.json", "window_weeks"): 6,
                                 ("generator.json", "sites", 0, "weekly_volume"): [120, 160],
                                 ("generator.json", "sites", 1, "weekly_volume"): [120, 160]})


_UNKNOWN = object()


def true_cells(db: Path, pack: FrozenPack, master: Mapping[str, Any]) -> dict[tuple[str, ...], dict[str, Any]]:
    """The cells recomputed from the site database with sqlite3 directly (not through RecordStore)."""
    rows = db_rows(db, "SELECT r.count_week, r.record_ref, r.root_ref, r.reporter_id, c.entity_type, c.entity_id, "
                       "c.predicate, c.channel, c.res_conf FROM claims c JOIN records r ON r.record_ref = c.record_ref "
                       "WHERE r.forwarded_in = 0 ORDER BY r.seq")
    out: dict[tuple[str, ...], dict[str, Any]] = {}
    for week, ref, root, reporter, t, i, p, channel, conf in rows:
        if t not in pack.egress.egress_entity_types:
            continue
        if pack.egress.require_master_data and pack.entity_types[t].ids is None and i not in master.get(t, ()):
            continue
        cell = out.setdefault((t, i, p, week, channel), {"records": set(), "roots": set(), "reporters": set(),
                                                         "conf": []})
        cell["records"].add(ref)
        cell["roots"].add(root)
        cell["reporters"].add(_UNKNOWN if reporter is None else reporter)
        cell["conf"].append(conf)
    return out


class CellSuppressionTests(SiteCase):
    def run_world(self, pack: FrozenPack, world: Any, records: Any) -> list[tuple[EdgeSite, Any]]:
        last = max(r["received_date"] for r in records)
        as_of = (date.fromisoformat(last) + timedelta(days=8 + pack.egress.close_lag_days)).isoformat()
        out = []
        for sid in world.params["site_ids"]:
            s = self.site(pack, sid, master=world.master_data[sid])
            self.clock.value = last + "T08:00:00.000Z"
            s.ingest([r for r in records if r["site"] == sid])
            s.extract("lexical")
            out.append((s, as_of))
        result = []
        for s, as_of in out:
            self.clock.value = as_of + "T08:00:00.000Z"
            emission = s.emit_cells(as_of)
            self.assertIsNone(emission.after)
            self.assertGreaterEqual(emission.closed_through, iso_week(last))
            result.append((s, emission))
        return result

    def check_invariants(self, pack: FrozenPack, s: EdgeSite, emission: Any) -> dict[str, int]:
        k = pack.egress.k
        confs = (pack.extraction.confidence_exact, pack.extraction.confidence_alias,
                 pack.extraction.confidence_variant)
        truth = true_cells(s.store.path, pack, s.master)
        cells = emission.body["cells"]
        keys = [(c["entity_type"], c["entity_id"], c["predicate"], c["iso_week"], c["channel"]) for c in cells]
        self.assertEqual(keys, sorted(set(keys)))                       # unique and sorted
        self.assertEqual(set(keys), set(truth))                         # complete: every non-empty true key
        coverage = {"n_int_reporters_suppressed": 0, "roots_suppressed_with_n_int": 0}
        for key, c in zip(keys, cells):
            t = truth[key]
            true = {"n": len(t["records"]), "n_roots": len(t["roots"]), "n_reporters": len(t["reporters"])}
            self.assertGreaterEqual(true["n"], 1)                       # no zero cells
            for f, v in true.items():
                if isinstance(c[f], int) and not isinstance(c[f], bool):
                    self.assertGreaterEqual(c[f], k)
                    self.assertEqual(c[f], v)
                else:
                    self.assertEqual(c[f], SUPPRESSED)
                    self.assertTrue(1 <= v < k, (key, f, v))
            self.assertEqual("res_conf_min" in c, c["n"] != SUPPRESSED)
            if "res_conf_min" in c:
                self.assertEqual(c["res_conf_min"], min(t["conf"]))
                self.assertIn(c["res_conf_min"], confs)
            if c["n"] != SUPPRESSED and c["n_reporters"] == SUPPRESSED:
                coverage["n_int_reporters_suppressed"] += 1
            if c["n"] != SUPPRESSED and c["n_roots"] == SUPPRESSED:
                coverage["roots_suppressed_with_n_int"] += 1
        for (et, ei, p, w, channel), t in truth.items():
            other = truth.get((et, ei, p, w, "text_only" if channel == "codes" else "codes"))
            if other is not None:
                self.assertEqual(t["records"] & other["records"], set())
        self.assertEqual(emission.stats["cells"], len(cells))
        return coverage

    def test_property_on_high_volume_worlds(self) -> None:
        for pid in PACK_IDS:
            with self.subTest(pack=pid):
                pack = high_volume_pack(self.tmp, pid)
                world = generate(pack, 7, 2, 10)
                totals = {"cells": 0, "cells_n_ge_k": 0, "n_int_reporters_suppressed": 0}
                for s, emission in self.run_world(pack, world, world.records):
                    coverage = self.check_invariants(pack, s, emission)
                    totals["cells"] += emission.stats["cells"]
                    totals["cells_n_ge_k"] += emission.stats["cells_n_ge_k"]
                    totals["n_int_reporters_suppressed"] += coverage["n_int_reporters_suppressed"]
                print(f"\n[synthetic high-volume world, {pid}, {len(world.records)} records] {totals}")
                self.assertGreater(totals["n_int_reporters_suppressed"], 0)

    def test_property_on_the_built_in_volumes(self) -> None:
        for pid in PACK_IDS:
            with self.subTest(pack=pid):
                pack = PACKS[pid]
                world = generate(pack, 11, len(pack.generator["sites"]), 46)
                records = world.records[:1000]
                cells = ge_k = 0
                for s, emission in self.run_world(pack, world, records):
                    self.check_invariants(pack, s, emission)
                    cells += emission.stats["cells"]
                    ge_k += emission.stats["cells_n_ge_k"]
                print(f"\n[synthetic built-in world, {pid}, 1000 records] cells={cells} cells_n_ge_k={ge_k}")

    def test_k_boundaries_per_field(self) -> None:
        for pid in PACK_IDS:
            with self.subTest(pack=pid):
                pack = PACKS[pid]
                k = pack.egress.k
                code, pred, t, ids = KEY[pid]
                exact, below, roots, reporters, absent = ids
                recs = [coded(pack, f"a{i}", "s1", "2026-03-02", exact, reporter=f"R{i}") for i in range(k)]
                recs += [coded(pack, f"b{i}", "s1", "2026-03-02", below, reporter=f"R{i}") for i in range(k - 1)]
                recs += [coded(pack, f"c{i}", "s1", "2026-03-02", roots, reporter=f"R{i}",
                               narrative="See attached." if i < 2 else "") for i in range(k)]
                recs += [coded(pack, f"d{i}", "s1", "2026-03-02", reporters, reporter=f"R{min(i, k - 2)}")
                         for i in range(k)]
                s = self.site(pack, master=universe_master(pack), root=Path(tempfile.mkdtemp(dir=self.tmp)))
                s.ingest(recs)
                s.extract("lexical")
                self.clock.value = "2026-04-30T08:00:00.000Z"
                cells = self.cells(s.emit_cells("2026-04-30"))
                at = lambda i: cells.get((t, i, pred, "2026-W10", "codes"))  # noqa: E731
                self.assertEqual({f: at(exact)[f] for f in ("n", "n_roots", "n_reporters")},
                                 {"n": k, "n_roots": k, "n_reporters": k})
                self.assertEqual({f: at(below)[f] for f in ("n", "n_roots", "n_reporters")},
                                 {"n": "<k", "n_roots": "<k", "n_reporters": "<k"})
                self.assertNotIn("res_conf_min", at(below))
                self.assertEqual((at(roots)["n"], at(roots)["n_roots"], at(roots)["n_reporters"]), (k, "<k", k))
                self.assertEqual((at(reporters)["n"], at(reporters)["n_roots"], at(reporters)["n_reporters"]),
                                 (k, k, "<k"))
                self.assertIsNone(at(absent))
                self.assertEqual(at(exact)["res_conf_min"], 1.0)

    def test_unknown_reporters_count_as_one(self) -> None:
        for pid in PACK_IDS:
            with self.subTest(pack=pid):
                pack = PACKS[pid]
                k = pack.egress.k
                _, pred, t, ids = KEY[pid]
                recs = [coded(pack, f"u{i}", "s1", "2026-03-02", ids[0], reporter=None) for i in range(k)]
                recs += [coded(pack, f"m{i}", "s1", "2026-03-02", ids[1], reporter=None if i >= k - 2 else f"R{i}")
                         for i in range(k)]
                recs += [coded(pack, f"x{i}", "s1", "2026-03-02", ids[2], reporter=None if i >= k - 1 else f"R{i}")
                         for i in range(k + 1)]
                s = self.site(pack, root=Path(tempfile.mkdtemp(dir=self.tmp)))
                s.ingest(recs)
                s.extract("lexical")
                self.clock.value = "2026-04-30T08:00:00.000Z"
                cells = self.cells(s.emit_cells("2026-04-30"))
                only_unknown, mixed, enough = (cells[(t, i, pred, "2026-W10", "codes")] for i in ids[:3])
                self.assertEqual((only_unknown["n"], only_unknown["n_reporters"]), (k, "<k"))
                self.assertEqual((mixed["n"], mixed["n_reporters"]), (k, "<k"))      # k-2 known + one unknown
                self.assertEqual((enough["n"], enough["n_reporters"]), (k + 1, k))  # k-1 known + one unknown

    def test_pairs_count_once_per_record(self) -> None:
        cases = {"device_quality": (("ILL-0101", "ILL-0102"), "crack", {"product": ["SD-9", "sd-9"], "lot": ["L10001"]},
                                    "The SD-9 cracked. SD-9 cracked."),
                 "claims_integrity": (("FLAG-98", "FLAG-99"), "irregularity_unspecified",
                                      {"repair_shop": ["RS-42", "RS-042"], "tow_operator": ["TW-0007"]},
                                      "RS-42 irregularity noted. RS-42 irregularity again.")}
        for pid, (codes, pred, entities, narrative) in cases.items():
            with self.subTest(pack=pid):
                pack = PACKS[pid]
                k = pack.egress.k
                s = self.site(pack, root=Path(tempfile.mkdtemp(dir=self.tmp)))
                s.ingest([record(pack, f"r{i}", "s1", "2026-03-02", codes=codes, entities=entities,
                                 narrative=narrative + f" Ref {i}.", reporter=f"R{i}") for i in range(k)])
                s.extract("lexical")
                self.clock.value = "2026-04-30T08:00:00.000Z"
                cells = self.cells(s.emit_cells("2026-04-30"))
                for t, ids in entities.items():
                    cell = cells[(t, ids[0], pred, "2026-W10", "codes")]
                    self.assertEqual((cell["n"], cell["n_roots"], cell["n_reporters"]), (k, k, k))
                self.assertEqual({key[4] for key in cells}, {"codes"})

    def test_artifact_keys_are_exactly_the_schema(self) -> None:
        envelope = ("after", "as_of", "closed_through", "config_hash", "k", "pack", "schema_version", "site")
        for pack in PACKS.values():
            self.assertEqual(artifact_keys(pack, "cells_bundle"), {
                "$": (tuple(sorted(envelope + ("cells",))), ()),
                "$.cells[]": (("channel", "entity_id", "entity_type", "iso_week", "n", "n_reporters", "n_roots",
                               "predicate"), ("res_conf_min",)),
            })
            self.assertEqual(artifact_keys(pack, "usage_summary"), {
                "$": (tuple(sorted(envelope + ("groups",))), ()),
                "$.groups[]": (("calls", "endpoint", "errors", "fake", "ok", "task", "tokens_in_missing",
                                "tokens_out_missing"),
                               ("latency_ms_p50", "latency_ms_p95", "tokens_in", "tokens_out")),
                "$.groups[].errors": ((), tuple(sorted(KINDS))),
            })
        every = {key for t in ARTIFACT_TYPES for req, opt in artifact_keys(DQ, t).values() for key in req + opt}
        for word in ("total", "totals", "marginal", "count", "n_forwarded", "forwarded", "records", "narrative",
                     "reporter", "ts", "ref", "host", "run_id", "model_requested", "model_served"):
            self.assertNotIn(word, every)
        self.assertLessEqual(every, set(schema_words()))
        with self.assertRaises(ValueError):
            artifact_keys(DQ, "verdicts")

    def test_week_53(self) -> None:
        pack = pack_copy(self.tmp, "device_quality", {("egress.json", "close_lag_days"): 7})
        s = self.site(pack)
        days = ("2026-12-28", "2027-01-01", "2027-01-03", "2027-01-04")
        s.ingest([coded(pack, f"r{i}", "s1", d, "SD-9", reporter=f"R{i}") for i, d in enumerate(days)])
        s.extract("lexical")
        self.clock.value = "2027-01-11T08:00:00.000Z"
        emission = s.emit_cells("2027-01-11")
        self.assertEqual(emission.closed_through, "2026-W53")
        self.assertEqual({c["iso_week"] for c in emission.body["cells"]}, {"2026-W53"})
        self.assertEqual(self.cells(emission)[("product", "SD-9", "crack", "2026-W53", "codes")]["n"], 3)
        self.clock.value = "2027-01-18T08:00:00.000Z"
        second = s.emit_cells("2027-01-18")
        self.assertEqual((second.after, second.closed_through), ("2026-W53", "2027-W01"))
        self.assertEqual({c["iso_week"] for c in second.body["cells"]}, {"2027-W01"})

    def test_build_cells_counts_non_egress_types(self) -> None:
        rows = [InputRow("2026-W10", "r1", "r1", "R1", "vehicle", "X-1", "crack", "codes", 1.0),
                InputRow("2026-W10", "r1", "r1", "R1", "product", "SD-9", "crack", "codes", 1.0),
                InputRow("2026-W10", "r2", "r2", None, "product", "SD-12", "crack", "codes", 1.0)]
        cells, stats = build_cells(rows, DQ, master={"product": frozenset({"SD-9"})})
        self.assertEqual([c["entity_id"] for c in cells], ["SD-9"])
        self.assertEqual((stats["non_egress_type"], stats["not_master_data"], stats["cells"]), (1, 1, 1))
        cells, stats = build_cells(rows, DQ, master={}, require_master_data=False)
        self.assertEqual([c["entity_id"] for c in cells], ["SD-12", "SD-9"])
        self.assertEqual(stats["suppressed_fields"], 6)


# --------------------------------------------------------------------------------------------------- closed weeks

class ClosedWeekAndLateArrivalTests(SiteCase):
    def setUp(self) -> None:
        super().setUp()
        self.pack = pack_copy(self.tmp, "device_quality", {("egress.json", "close_lag_days"): 7})

    def three(self, prefix: str, day: str, entity_id: str = "SD-9") -> list[dict[str, Any]]:
        return [coded(self.pack, f"{prefix}{i}", "s1", day, entity_id, reporter=f"R{i}") for i in range(3)]

    def ready(self, s: EdgeSite, records: list[dict[str, Any]]) -> None:
        s.ingest(records)
        s.extract("lexical")

    def test_mid_week_leaves_the_current_and_previous_weeks_open(self) -> None:
        s = self.site(self.pack)
        self.ready(s, self.three("a", "2026-02-25") + self.three("b", "2026-03-04") + self.three("c", "2026-03-10"))
        emission = s.emit_cells("2026-03-11")
        self.assertEqual((emission.after, emission.closed_through), (None, "2026-W09"))
        self.assertEqual({c["iso_week"] for c in emission.body["cells"]}, {"2026-W09"})

    def test_repeated_earlier_and_future_as_of(self) -> None:
        s = self.site(self.pack)
        self.ready(s, self.three("a", "2026-02-25"))
        self.assertIsNotNone(s.emit_cells("2026-03-11"))
        logs = ((self.tmp / "hq" / "receive.jsonl").read_bytes(),
                (self.tmp / "edge" / "site-s1.egress.jsonl").read_bytes())
        self.assertIsNone(s.emit_cells("2026-03-11"))
        self.assertIsNone(s.emit_cells("2026-03-11"))
        for bad in ("2026-03-10", "2026-03-12", "2026-02-30", None, "2026-W11", "2026-03-11T00:00Z"):
            with self.subTest(as_of=bad), self.assertRaises(SiteError):
                s.emit_cells(bad)
        self.assertEqual(((self.tmp / "hq" / "receive.jsonl").read_bytes(),
                          (self.tmp / "edge" / "site-s1.egress.jsonl").read_bytes()), logs)

    def test_a_late_record_counts_in_its_ingest_week(self) -> None:
        s = self.site(self.pack)
        self.ready(s, self.three("a", "2026-02-25"))
        first = s.emit_cells("2026-03-11")
        self.assertEqual(self.cells(first)[("product", "SD-9", "crack", "2026-W09", "codes")]["n"], 3)
        line = self.hq_lines()[0]
        self.clock.value = "2026-03-18T08:00:00.000Z"                     # ingest in 2026-W12
        got = s.ingest([coded(self.pack, "late1", "s1", "2026-02-26", "SD-9", reporter="R9")])
        self.assertEqual((got.ingested, got.late), (1, 1))
        s.extract("lexical")
        self.assertEqual(db_rows(s.store.path, "SELECT record_ref, received_week, count_week, watermark FROM "
                                               "late_records ORDER BY record_ref"),
                         [("late1", "2026-W09", "2026-W12", "2026-W09")])
        self.clock.value = "2026-03-29T08:00:00.000Z"
        second = s.emit_cells("2026-03-29")
        self.assertEqual((second.after, second.closed_through), ("2026-W09", "2026-W12"))
        weeks = {c["iso_week"] for c in second.body["cells"]}
        self.assertEqual(weeks, {"2026-W12"})
        self.assertEqual(second.stats["late"], 1)
        self.assertEqual(self.hq_lines()[0], line)

    def test_a_late_record_with_the_clock_behind_the_watermark(self) -> None:
        s = self.site(self.pack)
        self.ready(s, self.three("a", "2026-02-25"))
        s.emit_cells("2026-03-11")
        self.clock.value = "2026-02-27T08:00:00.000Z"                     # the site clock went back into 2026-W09
        got = s.ingest([coded(self.pack, "late1", "s1", "2026-02-26", "SD-9")])
        self.assertEqual(got.late, 1)
        self.assertEqual(db_rows(s.store.path, "SELECT count_week FROM records WHERE record_ref = 'late1' "
                                               "ORDER BY 1"), [("2026-W10",)])
        s.extract("lexical")
        self.clock.value = "2026-03-18T08:00:00.000Z"
        second = s.emit_cells("2026-03-18")
        self.assertEqual({c["iso_week"] for c in second.body["cells"]}, {"2026-W10"})

    def test_a_late_record_before_any_emission_counts_normally(self) -> None:
        s = self.site(self.pack)
        self.clock.value = "2026-03-25T08:00:00.000Z"
        got = s.ingest([coded(self.pack, "old", "s1", "2026-02-25", "SD-9")])
        self.assertEqual(got.late, 0)
        self.assertEqual(db_rows(s.store.path, "SELECT count_week FROM records ORDER BY seq"), [("2026-W09",)])

    def test_a_future_received_date_waits_for_its_week_to_close(self) -> None:
        s = self.site(self.pack)
        self.ready(s, self.three("a", "2026-02-25") + self.three("f", "2026-03-20", "SD-12"))
        first = s.emit_cells("2026-03-11")
        self.assertNotIn("SD-12", {c["entity_id"] for c in first.body["cells"]})
        self.clock.value = "2026-03-29T08:00:00.000Z"
        second = s.emit_cells("2026-03-29")
        self.assertIn(("product", "SD-12", "crack", "2026-W12", "codes"), self.cells(second))

    def test_successive_bundles_chain_and_their_weeks_are_disjoint(self) -> None:
        s = self.site(self.pack)
        days = ["2026-02-25", "2026-03-04", "2026-03-10", "2026-03-17", "2026-03-24", "2026-04-02"]
        self.ready(s, [coded(self.pack, f"r{i}", "s1", d, "SD-9") for i, d in enumerate(days)])
        for as_of in ("2026-03-11", "2026-03-16", "2026-03-23", "2026-03-23", "2026-04-20"):
            self.clock.value = as_of + "T08:00:00.000Z"
            s.emit_cells(as_of)
        bodies = [row["body"] for row in read_log(self.tmp / "hq" / "receive.jsonl")]
        self.assertEqual([b["closed_through"] for b in bodies], ["2026-W09", "2026-W10", "2026-W11", "2026-W15"])
        self.assertEqual([b["after"] for b in bodies], [None, "2026-W09", "2026-W10", "2026-W11"])
        seen: set[str] = set()
        for b in bodies:
            weeks = {c["iso_week"] for c in b["cells"]}
            self.assertEqual(weeks & seen, set())
            seen |= weeks
        self.assertEqual(seen, {"2026-W09", "2026-W10", "2026-W11", "2026-W12", "2026-W13", "2026-W14"})

    def test_unextracted_records_block_emission(self) -> None:
        s = self.site(self.pack)
        s.ingest(self.three("a", "2026-02-25"))
        with self.assertRaises(SiteError) as cm:
            s.emit_cells("2026-03-11")
        self.assertIn("3 records", str(cm.exception))
        self.assertEqual(self.hq_lines(), [])
        self.assertFalse((self.tmp / "edge" / "site-s1.egress.jsonl").exists())
        s.extract("lexical")
        self.assertIsNotNone(s.emit_cells("2026-03-11"))

    def test_an_empty_window_is_sent_and_advances_the_watermark(self) -> None:
        s = self.site(self.pack)
        first = s.emit_cells("2026-03-11")
        self.assertEqual((first.body["cells"], first.closed_through), ([], "2026-W09"))
        self.assertEqual(len(self.hq_lines()), 1)
        self.clock.value = "2026-03-18T08:00:00.000Z"
        self.assertEqual(s.emit_cells("2026-03-18").after, "2026-W09")

    def failed_send(self, failing_call: int) -> None:
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        s = self.site(self.pack, root=root)
        calls = {"n": 0}

        class Flaky(Boundary):
            def append(self, path: Path, line: str) -> None:
                calls["n"] += 1
                if calls["n"] == failing_call:
                    raise OSError("disk full")
                super().append(path, line)

        s.boundary = Flaky(self.pack, "s1", egress_log=s.boundary.egress_log, receive_log=s.boundary.receive_log,
                           clock=self.clock)
        self.ready(s, self.three("a", "2026-02-25"))
        with self.assertRaises(OSError):
            s.emit_cells("2026-03-11")
        stored = db_rows(s.store.path, "SELECT body, sent_at FROM emitted_weeks ORDER BY closed_through")
        self.assertEqual(len(stored), 1)
        self.assertIsNone(stored[0][1])
        self.clock.value = "2026-03-18T08:00:00.000Z"
        second = s.emit_cells("2026-03-18")
        self.assertEqual(second.after, "2026-W09")
        hq = read_log(root / "hq" / "receive.jsonl")
        site_log = read_log(root / "edge" / "site-s1.egress.jsonl")
        self.assertEqual(canonical_bytes(hq[0]["body"]), bytes(stored[0][0]))
        self.assertEqual([r["sha256"] for r in site_log], [hq[0]["sha256"], second.sha256])
        expected = [hq[0]["sha256"], second.sha256]
        if failing_call == 2:                     # HQ got the line before the site log failed: HQ drops it (G4)
            expected.insert(0, hq[0]["sha256"])
        self.assertEqual([r["sha256"] for r in hq], expected)
        self.assertEqual(db_rows(s.store.path, "SELECT count(*) FROM emitted_weeks WHERE sent_at IS NULL "
                                               "ORDER BY 1"), [(0,)])

    def test_a_failed_hq_append_is_resent_byte_identically_first(self) -> None:
        self.failed_send(1)

    def test_random_interleavings_never_revise_an_emitted_week(self) -> None:
        coverage = {"late": 0, "failed_sends": 0, "bundles": 0}
        for seed in range(8):
            with self.subTest(seed=seed):
                for key, value in self.interleave(seed).items():
                    coverage[key] += value
        self.assertTrue(all(v > 0 for v in coverage.values()), coverage)

    def interleave(self, seed: int) -> dict[str, int]:
        rng = random.Random(f"g3-interleave:{seed}")
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        s = self.site(self.pack, root=root)
        fail = {"next": False}

        class Flaky(Boundary):
            def append(self, path: Path, line: str) -> None:
                if fail["next"]:
                    fail["next"] = False
                    raise OSError("injected")
                super().append(path, line)

        s.boundary = Flaky(self.pack, "s1", egress_log=s.boundary.egress_log, receive_log=s.boundary.receive_log,
                           clock=self.clock)
        hq_path = root / "hq" / "receive.jsonl"
        day, n, hq = date(2026, 1, 5), 0, b""
        late = failed = 0
        for _ in range(70):
            op = rng.choice(("ingest", "ingest", "extract", "emit", "emit", "clock", "clock", "fail"))
            if op == "clock":
                day += timedelta(days=rng.randint(-6, 9))
                self.clock.value = day.isoformat() + "T08:00:00.000Z"
            elif op == "ingest":
                batch = []
                for _ in range(rng.randint(1, 4)):
                    n += 1
                    received = (day + timedelta(days=rng.randint(-30, 3))).isoformat()
                    batch.append(coded(self.pack, f"r{n}", "s1", received, rng.choice(("SD-9", "SD-12")),
                                       reporter=f"R{rng.randint(1, 3)}"))
                late += s.ingest(batch).late
            elif op == "extract":
                s.extract("lexical")
            elif op == "fail":
                fail["next"] = True
            else:
                as_of = (day - timedelta(days=rng.randint(0, 3))).isoformat()
                try:
                    s.emit_cells(as_of)
                except SiteError:
                    pass
                except OSError:
                    failed += 1
            now = hq_path.read_bytes() if hq_path.exists() else b""
            self.assertTrue(now.startswith(hq), "an earlier HQ line changed")
            hq = now
        fail["next"] = False
        s.extract("lexical")
        day += timedelta(days=60)
        self.clock.value = day.isoformat() + "T08:00:00.000Z"
        s.emit_cells(day.isoformat())
        rows = read_log(hq_path)
        bodies, seen_sha = [], set()
        for row in rows:                                  # a re-send after a failed site-log write repeats a line
            if row["sha256"] not in seen_sha:
                seen_sha.add(row["sha256"])
                bodies.append(row["body"])
        self.assertEqual([b["after"] for b in bodies], [None] + [b["closed_through"] for b in bodies[:-1]])
        self.assertEqual([r["sha256"] for r in read_log(root / "edge" / "site-s1.egress.jsonl")],
                         [sha256_hex(canonical_bytes(b)) for b in bodies])
        emitted: dict[tuple[str, ...], int] = {}
        for b in bodies:
            for c in b["cells"]:
                key = (c["entity_type"], c["entity_id"], c["predicate"], c["iso_week"], c["channel"])
                emitted[key] = emitted.get(key, 0) + 1
        self.assertEqual(set(emitted.values()) - {1}, set())   # no cell key, so no week, in two bundles
        truth = {key for key in true_cells(s.store.path, self.pack, s.master) if key[3] <= bodies[-1]["closed_through"]}
        self.assertEqual(set(emitted), truth)                  # every counted claim is in exactly one bundle
        return {"late": late, "failed_sends": failed, "bundles": len(bodies)}

    def test_a_failed_site_log_append_is_resent_and_hq_may_hold_a_duplicate(self) -> None:
        self.failed_send(2)


# --------------------------------------------------------------------------------------------------- master data

class MasterDataTests(SiteCase):
    def emit(self, s: EdgeSite) -> Any:
        s.extract("lexical")
        self.clock.value = "2026-04-30T08:00:00.000Z"
        return s.emit_cells("2026-04-30")

    def test_ids_outside_master_data_are_absent_and_counted(self) -> None:
        s = self.site(master={"product": ["SD-9"], "lot": ["L10001"], "supplier": []})
        s.ingest([coded(DQ, "in", "s1", "2026-03-02", "SD-9"), coded(DQ, "out", "s1", "2026-03-02", "SD-12"),
                  record(DQ, "text", "s1", "2026-03-02", narrative="The SD-40 cracked.")])
        emission = self.emit(s)
        ids = {c["entity_id"] for c in emission.body["cells"]}
        self.assertEqual(ids, {"SD-9"})
        self.assertEqual(emission.stats["not_master_data"], 2)

    def test_a_type_absent_from_master_data_is_dropped(self) -> None:
        s = self.site(master={"product": ["SD-9"]})
        s.ingest([record(DQ, "r1", "s1", "2026-03-02", codes=["ILL-0101"],
                         entities={"product": ["SD-9"], "lot": ["L10001"], "supplier": ["V1001"]})])
        emission = self.emit(s)
        self.assertEqual({c["entity_type"] for c in emission.body["cells"]}, {"product"})
        self.assertEqual(emission.stats["not_master_data"], 2)

    def test_alias_only_types_always_pass(self) -> None:
        s = self.site(master={})
        s.ingest([record(DQ, "r1", "s1", "2026-03-02", codes=["ILL-0101"], entities={"component": ["BATTERY-DOOR"]}),
                  record(DQ, "r2", "s1", "2026-03-02", narrative="The battery cover cracked.")])
        emission = self.emit(s)
        self.assertEqual({(c["entity_id"], c["channel"]) for c in emission.body["cells"]},
                         {("BATTERY-DOOR", "codes"), ("BATTERY-DOOR", "text_only")})

    def test_claims_integrity_without_master_data_emits_a_narrative_only_id(self) -> None:
        pack = PACKS["claims_integrity"]
        self.assertFalse(pack.egress.require_master_data)
        s = self.site(pack, master={"repair_shop": ["RS-42"]})
        s.ingest([record(pack, "r1", "s1", "2026-03-02", narrative="RS-9999 sent a supplemental estimate.")])
        emission = self.emit(s)
        self.assertIn(("repair_shop", "RS-9999", "supplement_after_teardown", "2026-W10", "text_only"),
                      self.cells(emission))

    def test_an_id_shaped_person_value_is_dropped_without_master_data(self) -> None:
        pack = PACKS["claims_integrity"]
        s = self.site(pack, master={})
        s.ingest([record(pack, "r1", "s1", "2026-03-02", persons={"policy_number": "RS-7777"},
                         narrative="Policy RS-7777 sent a supplemental estimate.")])
        s.extract("lexical")
        drops = json.loads(db_rows(s.store.path, "SELECT drops FROM extraction_stats ORDER BY 1")[0][0])
        self.assertGreaterEqual(drops["person_value"], 1)
        self.clock.value = "2026-04-30T08:00:00.000Z"
        emission = s.emit_cells("2026-04-30")
        self.assertNotIn("RS-7777", {c["entity_id"] for c in emission.body["cells"]})
        self.assertNotIn(b"RS-7777", (self.tmp / "hq" / "receive.jsonl").read_bytes())


# --------------------------------------------------------------------------------------------------- forwarded

class ForwardedTests(SiteCase):
    def emit(self, s: EdgeSite) -> Any:
        s.extract("lexical")
        self.clock.value = "2026-04-30T08:00:00.000Z"
        return s.emit_cells("2026-04-30")

    def test_forwarded_in_records_are_excluded(self) -> None:
        s = self.site()
        own = [coded(DQ, f"o{i}", "s1", "2026-03-02", "SD-9", reporter=f"R{i}") for i in range(3)]
        fwd = [coded(DQ, f"f{i}", "s1", "2026-03-02", "SD-9", reporter=f"X{i}", origin=(f"z{i}", "s2"))
               for i in range(3)]
        fwd += [coded(DQ, f"g{i}", "s1", "2026-03-02", "SD-12", origin=(f"y{i}", "s2")) for i in range(3)]
        self.assertEqual(s.ingest(own + fwd).forwarded_in, 6)
        emission = self.emit(s)
        cells = self.cells(emission)
        cell = cells[("product", "SD-9", "crack", "2026-W10", "codes")]
        self.assertEqual((cell["n"], cell["n_roots"], cell["n_reporters"]), (3, 3, 3))
        self.assertNotIn("SD-12", {key[1] for key in cells})
        self.assertEqual((emission.stats["forwarded_excluded"], emission.stats["records"]), (6, 3))

    def test_a_same_site_forward_is_counted_with_its_origin_as_root(self) -> None:
        s = self.site()
        own = [coded(DQ, f"o{i}", "s1", "2026-03-02", "SD-9", reporter=f"R{i}") for i in range(3)]
        s.ingest(own + [coded(DQ, "f1", "s1", "2026-03-03", "SD-9", reporter="R7", origin=("o0", "s1"))])
        cell = self.cells(self.emit(s))[("product", "SD-9", "crack", "2026-W10", "codes")]
        self.assertEqual((cell["n"], cell["n_roots"], cell["n_reporters"]), (4, 3, 4))


# --------------------------------------------------------------------------------------------------- usage

class UsageTests(SiteCase):
    def model_site(self, n: int, *, provider: FakeProvider | None = None, day: str = "2026-03-02") -> EdgeSite:
        s = self.site(runtime=self.runtime(provider=provider))
        s.ingest([coded(DQ, f"r{i}", "s1", day, "SD-9", reporter=f"R{i}") for i in range(n)])
        return s

    def test_errors_are_suppressed_and_tokens_sent_when_calls_reach_k(self) -> None:
        provider = FakeProvider()
        provider.register(TASK_NAME, lexical_handler(DQ, Canonicaliser(DQ)))
        provider.fail_next(TASK_NAME, ["http_5xx"])
        s = self.model_site(6, provider=provider)
        summary = s.extract("model")
        self.assertEqual(dict(summary.errors), {"http_5xx": 1})
        self.clock.value = "2026-03-29T08:00:00.000Z"
        usage = s.emit_usage("2026-03-29")
        (group,) = usage.body["groups"]
        self.assertEqual({k: group[k] for k in ("task", "endpoint", "calls", "ok", "errors", "fake",
                                                "tokens_in_missing", "tokens_out_missing")},
                         {"task": TASK_NAME, "endpoint": "site-fake", "calls": 6, "ok": 5,
                          "errors": {"http_5xx": "<k"}, "fake": True, "tokens_in_missing": "<k",
                          "tokens_out_missing": "<k"})
        for key in ("tokens_in", "tokens_out", "latency_ms_p50", "latency_ms_p95"):
            self.assertIn(key, group)
        raw = summarise(read_ledger(s.runtime.ledger.path))["groups"][0]
        self.assertEqual((group["tokens_in"], group["tokens_out"]), (raw["tokens_in"], raw["tokens_out"]))
        text = canonical_dumps(usage.body)
        for leaked in ('"ts"', '"ref"', '"host"', '"run_id"', '"model_requested"', '"model_served"', "g3-test",
                       "in-process", "x:1"):
            self.assertNotIn(leaked, text)

    def test_fewer_than_k_calls_withholds_tokens_and_latency(self) -> None:
        s = self.model_site(2)
        s.extract("model")
        self.clock.value = "2026-03-29T08:00:00.000Z"
        (group,) = s.emit_usage("2026-03-29").body["groups"]
        self.assertEqual((group["calls"], group["ok"], group["errors"]), ("<k", "<k", {}))
        self.assertEqual((group["tokens_in_missing"], group["tokens_out_missing"]), (0, 0))
        for key in ("tokens_in", "tokens_out", "latency_ms_p50", "latency_ms_p95"):
            self.assertNotIn(key, group)

    def test_each_ledger_row_is_summarised_once(self) -> None:
        s = self.model_site(3)
        s.extract("model")                                              # 3 rows dated in 2026-W11
        self.clock.value = "2026-03-18T08:00:00.000Z"
        s.ingest([coded(DQ, f"n{i}", "s1", "2026-03-16", "SD-9", reporter=f"R{i}") for i in range(4)])
        s.extract("model")                                              # 4 rows dated in 2026-W12
        self.assertIsNone(s.emit_usage("2026-03-18"))                   # only 2026-W09 has closed: no row yet
        self.clock.value = "2026-03-29T08:00:00.000Z"
        first = s.emit_usage("2026-03-29")
        self.assertEqual((first.after, first.closed_through, first.body["groups"][0]["calls"]),
                         (None, "2026-W11", 3))
        self.clock.value = "2026-04-05T08:00:00.000Z"
        second = s.emit_usage("2026-04-05")
        self.assertEqual((second.after, second.closed_through, second.body["groups"][0]["calls"]),
                         ("2026-W11", "2026-W12", 4))
        self.assertEqual(db_rows(s.store.path, "SELECT closed_through, ledger_rows FROM emitted_weeks WHERE "
                                               "artifact_type = 'usage_summary' ORDER BY closed_through"),
                         [("2026-W11", 3), ("2026-W12", 7)])
        self.assertIsNone(s.emit_usage("2026-04-05"))

    def test_a_lexical_site_sends_no_usage(self) -> None:
        s = self.site()
        self.clock.value = "2026-03-29T08:00:00.000Z"
        self.assertIsNone(s.emit_usage("2026-03-29"))

    def test_a_ledger_row_of_another_boundary_raises(self) -> None:
        s = self.model_site(3)
        s.extract("model")
        path = s.runtime.ledger.path
        row = read_ledger(path)[0]
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(canonical_dumps({**row, "boundary": "site:s2"}) + "\n")
        self.clock.value = "2026-03-29T08:00:00.000Z"
        with self.assertRaises(SiteError):
            s.emit_usage("2026-03-29")

    def test_usage_summary_equals_summarise_of_the_ledger(self) -> None:
        s = self.model_site(4)
        s.extract("model")
        path = s.runtime.ledger.path
        self.assertEqual(usage_summary(path), summarise(read_ledger(path)))

    def test_a_simulated_runtime_refuses_non_synthetic_records_before_any_call(self) -> None:
        calls = []
        provider = FakeProvider()
        handler = lexical_handler(DQ, Canonicaliser(DQ))
        provider.register(TASK_NAME, lambda payload: calls.append(1) or handler(payload))
        s = self.site(runtime=self.runtime(provider=provider, simulation=True))
        s.ingest([coded(DQ, "syn", "s1", "2026-03-02", "SD-9"),
                  coded(DQ, "real", "s1", "2026-03-02", "SD-9", synthetic=False)])
        with self.assertRaises(SiteError):
            s.extract("model")
        self.assertEqual((calls, read_ledger(s.runtime.ledger.path)), ([], []))


# --------------------------------------------------------------------------------------------------- the Boundary

VALID_CELL = {"entity_type": "product", "entity_id": "SD-9", "predicate": "crack", "iso_week": "2026-W09",
              "channel": "codes", "n": 3, "n_roots": 3, "n_reporters": "<k", "res_conf_min": 1.0}


def cells_body(**changes: Any) -> dict[str, Any]:
    body = {"schema_version": 1, "pack": DQ.id, "config_hash": DQ.config_hash, "site": "s1", "as_of": "2026-03-11",
            "after": None, "closed_through": "2026-W09", "k": 3, "cells": [dict(VALID_CELL)]}
    body.update(changes)
    return body


def with_cell(**changes: Any) -> dict[str, Any]:
    cell = {**VALID_CELL, **changes}
    return cells_body(cells=[{k: v for k, v in cell.items() if v is not _DROP}])


_DROP = object()


class BoundaryTests(SiteCase):
    def boundary(self, cls: type[Boundary] = Boundary, **kw: Any) -> Boundary:
        return cls(DQ, "s1", egress_log=self.tmp / "edge" / "egress.jsonl", receive_log=self.tmp / "hq" / "r.jsonl",
                   clock=self.clock, **kw)

    def files(self) -> tuple[bytes | None, bytes | None]:
        return tuple(p.read_bytes() if p.exists() else None
                     for p in (self.tmp / "edge" / "egress.jsonl", self.tmp / "hq" / "r.jsonl"))

    def assert_refused(self, b: Boundary, body: Any, keyword: str, *, artifact_type: str = "cells_bundle",
                       direction: str = "out", planted: str | None = None) -> EgressError:
        before = self.files()
        for call in (b.validate, b.send):
            with self.assertNoLogs(level="DEBUG"), self.assertRaises(EgressError) as cm:
                call(direction, artifact_type, body)
            err = cm.exception
            self.assertEqual(err.keyword, keyword, (str(err), keyword))
            self.assertEqual(err.args, ())
            self.assertIsNone(err.__cause__)
            self.assertTrue(err.__suppress_context__)
            self.assertIn(err.artifact_type, ARTIFACT_TYPES + ("unknown",))
            self.assertEqual(str(err), f"egress refused: artifact_type={err.artifact_type} path={err.path} "
                                       f"keyword={err.keyword}")
            if planted is not None:
                for i in range(len(planted) - 3):
                    self.assertNotIn(planted[i:i + 4], str(err) + repr(err))
            self.assertEqual(self.files(), before)
        return err

    def test_rejections_carry_no_value_and_write_nothing(self) -> None:
        b = self.boundary()
        b.validate("out", "cells_bundle", cells_body())
        for field in ("entity_id", "predicate", "channel", "iso_week"):
            with self.subTest(field=field):
                keyword = {"entity_id": "pattern", "predicate": "enum", "channel": "enum", "iso_week": "week"}[field]
                self.assert_refused(b, with_cell(**{field: PLANTED}), keyword, planted=PLANTED)
        for field, keyword in (("site", "const"), ("as_of", "date"), ("pack", "const"), ("closed_through", "week"),
                               ("after", "week"), ("config_hash", "const")):
            with self.subTest(field=field):
                self.assert_refused(b, cells_body(**{field: PLANTED}), keyword, planted=PLANTED)
        cases = [
            (cells_body(note=PLANTED), "additionalProperties", "$"),
            (with_cell(note=PLANTED), "additionalProperties", "$.cells[0]"),
            (cells_body(**{PLANTED: 1}), "additionalProperties", "$"),
            (with_cell(entity_id="sd-9"), "id_format", "$.cells[0].entity_id"),
            (with_cell(entity_id="SD-0009"), "id_format", "$.cells[0].entity_id"),
            (with_cell(entity_type="component", entity_id="BATTERY-LID"), "id_format", "$.cells[0].entity_id"),
            (with_cell(entity_id="A" * 41), "maxLength", "$.cells[0].entity_id"),
            (with_cell(res_conf_min=0.5), "enum", "$.cells[0].res_conf_min"),
            (with_cell(res_conf_min=_DROP), "consistency", "$.cells[0].res_conf_min"),
            (with_cell(n="<k", n_roots="<k"), "consistency", "$.cells[0].res_conf_min"),
            (with_cell(n="<k", n_roots=3, res_conf_min=_DROP), "consistency", "$.cells[0].n_roots"),
            (with_cell(n=3, n_roots=4), "consistency", "$.cells[0].n_roots"),
            (with_cell(n=3, n_reporters=5), "consistency", "$.cells[0].n_reporters"),
            (cells_body(cells=[dict(VALID_CELL), dict(VALID_CELL)]), "order", "$.cells[1]"),
            (cells_body(cells=[{**VALID_CELL, "entity_id": "SD-90"}, dict(VALID_CELL)]), "order", "$.cells[1]"),
            (with_cell(iso_week="2026-W10"), "range", "$.cells[0].iso_week"),
            (cells_body(after="2026-W09"), "range", "$.after"),
            (cells_body(after="2026-W10"), "range", "$.after"),
            (cells_body(after="2026-W05"), "sequence", "$.after"),
            (cells_body(k=2), "const", "$.k"),
            (cells_body(schema_version=True), "const", "$.schema_version"),
            (cells_body(cells={"0": VALID_CELL}), "type", "$.cells"),
            ({**cells_body(), "cells": None}, "type", "$.cells"),
            ({k: v for k, v in cells_body().items() if k != "as_of"}, "required", "$.as_of"),
            (cells_body(as_of="2026-02-30"), "date", "$.as_of"),
            (cells_body(closed_through="2025-W53", cells=[]), "week", "$.closed_through"),
        ]
        for value in (1, 2, 0, -3, "<k ", " <k", "3", "<K", True, False, 2.0, 3.0, None, 10 ** 30, [3]):
            cases.append((with_cell(n_reporters=value), "count", "$.cells[0].n_reporters"))
        for body, keyword, path in cases:
            with self.subTest(keyword=keyword, path=path):
                err = self.assert_refused(b, body, keyword, planted=PLANTED)
                self.assertEqual(err.path, path)
        self.assert_refused(b, cells_body(), "artifact_type", artifact_type="verdicts")
        self.assert_refused(b, cells_body(), "direction", direction="in")
        self.assert_refused(b, with_cell(n=float("nan")), "json")
        self.assert_refused(b, cells_body(cells={1, 2}), "json")
        self.assert_refused(b, cells_body(as_of=date(2026, 3, 11)), "json")
        self.assert_refused(b, [cells_body()], "type")
        err = self.assert_refused(b, cells_body(), "artifact_type", artifact_type=PLANTED, planted=PLANTED)
        self.assertEqual(err.artifact_type, "unknown")

    def test_usage_rejections(self) -> None:
        b = self.boundary(tasks=(TASK_NAME,), endpoints=("site-fake",))
        group = {"task": TASK_NAME, "endpoint": "site-fake", "calls": 3, "ok": 3, "errors": {}, "fake": True,
                 "tokens_in": 10, "tokens_out": 10, "tokens_in_missing": 0, "tokens_out_missing": 0,
                 "latency_ms_p50": 0.0, "latency_ms_p95": 1.5}
        body = {**cells_body(), "groups": [group]}
        del body["cells"]
        b.validate("out", "usage_summary", body)
        cases = [
            ({**group, "calls": "<k"}, "consistency"),
            ({**group, "ok": 1}, "count"),
            ({**group, "errors": {"http_5xx": 1}}, "count"),
            ({**group, "errors": {PLANTED: 3}}, "additionalProperties"),
            ({**group, "task": PLANTED}, "enum"),
            ({**group, "endpoint": "elsewhere"}, "enum"),
            ({**group, "fake": 1}, "type"),
            ({**group, "tokens_in": -1}, "minimum"),
            ({**group, "tokens_in": 1.5}, "type"),
            ({**group, "latency_ms_p95": 10.0 ** 10}, "maximum"),
            ({**group, "ts": "2026-03-11T08:00:00.000Z"}, "additionalProperties"),
            ({**group, "model_served": "x"}, "additionalProperties"),
        ]
        for g, keyword in cases:
            with self.subTest(keyword=keyword, group=sorted(g)):
                self.assert_refused(b, {**body, "groups": [g]}, keyword, artifact_type="usage_summary",
                                    planted=PLANTED)
        self.assert_refused(b, {**body, "groups": [group, group]}, "order", artifact_type="usage_summary")
        self.assert_refused(b, cells_body(), "additionalProperties", artifact_type="usage_summary")

    def leaky(self, hook: str) -> None:
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        s = self.site(root=root)
        s.ingest([coded(DQ, f"r{i}", "s1", "2026-03-02", "SD-9", narrative=PLANTED, reporter=f"R{i}")
                  for i in range(3)])
        s.extract("lexical")

        def leak(body: Any) -> Any:
            return {**body, "cells": [{**c, "entity_id": PLANTED} for c in body["cells"]]}

        class Leaky(Boundary):
            def validate(self, direction: str, artifact_type: str, body: Any) -> dict[str, Any]:
                return super().validate(direction, artifact_type, leak(body) if hook == "validate" else body)

            def send(self, direction: str, artifact_type: str, body: Any) -> dict[str, Any]:
                return super().send(direction, artifact_type, leak(body) if hook == "send" else body)

        s.boundary = Leaky(DQ, "s1", egress_log=s.boundary.egress_log, receive_log=s.boundary.receive_log,
                           clock=self.clock)
        self.clock.value = "2026-03-29T08:00:00.000Z"
        with self.assertNoLogs(level="DEBUG"), self.assertRaises(EgressError) as cm:
            s.emit_cells("2026-03-29")
        err = cm.exception
        self.assertEqual((err.keyword, err.path, err.args), ("pattern", "$.cells[0].entity_id", ()))
        for i in range(len(PLANTED) - 3):
            self.assertNotIn(PLANTED[i:i + 4], str(err) + repr(err))
        self.assertFalse(s.boundary.egress_log.exists())
        self.assertFalse(s.boundary.receive_log.exists())

    def test_negative_control_a_a_leaky_validate_is_refused(self) -> None:
        self.leaky("validate")

    def test_negative_control_a_a_leaky_send_is_refused(self) -> None:
        self.leaky("send")

    def test_logs_agree_and_the_returned_copy_is_isolated(self) -> None:
        b = self.boundary()
        body = cells_body()
        returned = b.send("out", "cells_bundle", body)
        self.assertEqual(returned, body)
        hq, site_log = self.files()[1], self.files()[0]
        self.assertEqual(hq, site_log)
        (row,) = read_log(self.tmp / "hq" / "r.jsonl")
        self.assertEqual(sorted(row), sorted(LOG_KEYS))
        data = canonical_bytes(body)
        self.assertEqual((row["sha256"], row["bytes"], row["body"]), (sha256_hex(data), len(data), body))
        self.assertEqual((row["ts"], row["site"], row["direction"], row["artifact_type"]),
                         (self.clock.value, "s1", "out", "cells_bundle"))
        returned["cells"][0]["n"] = 99
        body["cells"][0]["n"] = 98
        self.assertEqual(self.files(), (site_log, hq))
        self.assertEqual(b.send("out", "cells_bundle", cells_body()), cells_body())   # identical re-send
        self.assertEqual(self.files(), (site_log, hq))
        reopened = self.boundary()
        self.assertEqual(reopened.send("out", "cells_bundle", cells_body()), cells_body())
        self.assertEqual(self.files(), (site_log, hq))
        self.assert_refused(reopened, cells_body(after=None, closed_through="2026-W10", cells=[]), "sequence")
        reopened.send("out", "cells_bundle", cells_body(after="2026-W09", closed_through="2026-W10", cells=[]))
        self.assertEqual(len(read_log(self.tmp / "hq" / "r.jsonl")), 2)

    def test_a_clock_that_is_not_a_timestamp_is_a_value_error(self) -> None:
        b = self.boundary()
        self.clock.value = "yesterday"
        with self.assertRaises(ValueError):
            b.send("out", "cells_bundle", cells_body())
        self.assertEqual(self.files(), (None, None))

    def test_read_log_refuses_a_tampered_line_without_its_value(self) -> None:
        b = self.boundary()
        b.send("out", "cells_bundle", cells_body())
        path = self.tmp / "hq" / "r.jsonl"
        row = read_log(path)[0]
        for bad in ({**row, "bytes": row["bytes"] + 1}, {**row, "sha256": "0" * 64}, {**row, "note": PLANTED},
                    {**row, "site": PLANTED}, {**row, "artifact_type": "verdicts"}, {**row, "ts": "now"}):
            path.write_text(canonical_dumps(bad) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError) as cm:
                read_log(path)
            self.assertIn("line 1", str(cm.exception))
            for i in range(len(PLANTED) - 3):
                self.assertNotIn(PLANTED[i:i + 4], str(cm.exception))
        path.write_text("{not json\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            read_log(path)
        self.assertEqual(read_log(self.tmp / "absent.jsonl"), [])
        self.assertEqual(sorted(KEYWORDS), sorted(set(KEYWORDS)))
        self.assertEqual(CHANNELS, ("codes", "text_only"))

    def test_a_site_run_writes_only_the_expected_files(self) -> None:
        s = self.site(runtime=self.runtime())
        s.ingest([coded(DQ, f"r{i}", "s1", "2026-03-02", "SD-9", reporter=f"R{i}") for i in range(3)])
        s.extract("model")
        self.clock.value = "2026-03-29T08:00:00.000Z"
        s.emit_cells("2026-03-29")
        s.emit_usage("2026-03-29")
        s.close()
        s.runtime.close()
        names = sorted(p.name for p in (self.tmp / "edge").iterdir())
        allowed = {"site-s1.sqlite3", "site-s1.sqlite3-wal", "site-s1.sqlite3-shm", "site-s1.egress.jsonl",
                   "site-s1.ledger.jsonl"}
        self.assertLessEqual(set(names), allowed)
        self.assertLessEqual({"site-s1.sqlite3", "site-s1.egress.jsonl", "site-s1.ledger.jsonl"}, set(names))
        self.assertEqual(sorted(p.name for p in (self.tmp / "hq").iterdir()), ["receive.jsonl"])
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["edge", "hq"])

    def test_g0_runs_are_byte_identical_across_hash_seeds(self) -> None:
        outs = []
        for seed in ("0", "4242"):
            out = self.tmp / f"g0-{seed}"
            r = subprocess.run([sys.executable, "-m", "mycelic.collective.experiments.g0_canary", "--pack",
                                "device_quality", "--records", "300", "--seed", "5", "--mode", "fake", "--out",
                                str(out)], cwd=ROOT, capture_output=True, text=True, timeout=300,
                               env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT)})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            outs.append(out)
        files = sorted(p.relative_to(outs[0]).as_posix() for p in (outs[0] / "edge").glob("site-*.egress.jsonl"))
        self.assertEqual(len(files), 6)
        for rel in files + ["hq/receive.jsonl", "private/manifest.json"]:
            with self.subTest(file=rel):
                self.assertEqual((outs[0] / rel).read_bytes(), (outs[1] / rel).read_bytes())


if __name__ == "__main__":
    unittest.main()
