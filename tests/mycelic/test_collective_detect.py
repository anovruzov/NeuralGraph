"""G4: detection at HQ over the k-suppressed weekly cells: the org config, the collective store, the detectors D2 to
D7, the rules channel, the alert walk, no look-ahead and determinism.

Every world here is hand-built or generated, synthetic and same-author; every test works in a temporary directory and
nothing connects anywhere. Counts printed by the end-to-end and performance tests are synthetic engineering figures,
printed and never asserted; they are not measurements of anything.
"""
from __future__ import annotations

import itertools
import json
import math
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from mycelic.collective import stats
from mycelic.collective.detect.detectors import (FEATURES, IGNORED_KEYS, DetectionError, DetectorConfig, bounds,
                                                 detect, result_bytes, run_detection)
from mycelic.collective.detect.org import OrgConfig, OrgError, load_org, parse_org
from mycelic.collective.detect.rules import RuleHit, evaluate_rule, series_key
from mycelic.collective.detect.store import (REJECT_REASONS, RUN_CHANNELS, TABLES, BundleRow, CellRow,
                                             CollectiveStore, StoreError)
from mycelic.collective.edge.egress import CHANNELS, SUPPRESSED
from mycelic.collective.edge.site import EdgeSite
from mycelic.collective.edge.weeks import next_week, week_sunday
from mycelic.collective.jsonio import canonical_bytes, canonical_dumps, sha256_hex
from mycelic.collective.packs.generator import generate
from mycelic.collective.packs.loader import FrozenPack, PackError, load_pack
from tests.mycelic.test_collective_edge import (_SQL_STATEMENT, Clock, coded, pack_copy, string_constants,
                                               universe_master)
from tests.mycelic.test_collective_guards import BUILTIN_PACKS

ROOT = Path(__file__).resolve().parents[2]
DETECT = ROOT / "mycelic" / "collective" / "detect"
DQ = load_pack("device_quality")
CI = load_pack("claims_integrity")
PACKS = {pid: load_pack(pid) for pid in BUILTIN_PACKS}
NOW = "2028-01-03T08:00:00Z"
ENTERPRISE = "acme"
SIX = (("s1", "acme/emea/de"), ("s2", "acme/emea/fr"), ("s3", "acme/amer/us"), ("s4", "acme/amer/ca"),
       ("s5", "acme/apac/jp"), ("s6", "acme/apac/au"))
SITES = tuple(s for s, _ in SIX)
# per pack: one id-format type with ids, predicates without a rule on that type, and a background series of
# another type that gives every site an early first week
VOCAB = {
    "device_quality": {"type": "product", "ids": ("SD-9", "IP-7", "CM-5", "IP-21", "IP-300", "SD-12", "SD-40"),
                       "preds": ("overheat", "leak", "occlusion", "power_loss", "alarm_failure"),
                       "bg": ("component", "BATTERY-DOOR", "display_fault")},
    "claims_integrity": {"type": "repair_shop", "ids": ("RS-42", "RS-5", "RS-310", "RS-1077", "RS-2290", "RS-7",
                                                        "RS-8"),
                         "preds": ("staged_collision", "duplicate_invoice", "inflated_labour_hours",
                                   "prior_damage_concealed", "tow_without_dispatch"),
                         "bg": ("damage_area", "FRONT-BUMPER", "irregularity_unspecified")},
    "it_incidents": {"type": "software_release", "ids": ("REL/2401/07", "REL/2402/03", "REL/2403/12", "REL/2404/21",
                                                         "REL/2405/02", "REL/2406/15", "REL/2407/09"),
                     "preds": ("memory_leak", "crash_after_update", "license_exhausted", "latency_degradation",
                               "data_sync_failure"),
                     "bg": ("it_service", "FILE-SHARE", "incident_unspecified")},
}
# per built-in pack: the documented detector defaults that differ between packs (PACKS.md section 1.1) and the egress
# values the detectors read: (alert budget, stale_days, k, close_lag_days)
DETECTOR_DEFAULTS = {"device_quality": (5, 42, 3, 14), "claims_integrity": (4, 56, 5, 21),
                     "it_incidents": (5, 42, 3, 14)}


def org_dict(sites: Sequence[tuple[str, str]] = SIX, enterprise: str = ENTERPRISE) -> dict[str, Any]:
    return {"schema_version": 1, "enterprise": enterprise,
            "sites": [{"site_id": s, "unit_path": p, "country": "DE", "display_name": f"Plant {s}"} for s, p in sites]}


ORG6 = parse_org(org_dict())


def weeks_from(first: str, n: int) -> list[str]:
    out = [first]
    while len(out) < n:
        out.append(next_week(out[-1]))
    return out


W = weeks_from("2026-W01", 60)


def closing(pack: FrozenPack, week: str) -> str:
    """The first date at which ``week`` is closed for the pack."""
    return (week_sunday(week) + timedelta(days=pack.egress.close_lag_days)).isoformat()


def ser(pack: FrozenPack, i_id: int, i_pred: int) -> tuple[str, str, str]:
    v = VOCAB[pack.id]
    return v["type"], v["ids"][i_id], v["preds"][i_pred]


def key(s: tuple[str, str, str]) -> str:
    return series_key(*s)


def cell(t: str, eid: str, p: str, week: str, n: Any = SUPPRESSED, *, roots: Any = None, reporters: Any = None,
         channel: str = "codes", conf: float = 1.0) -> dict[str, Any]:
    out = {"entity_type": t, "entity_id": eid, "predicate": p, "iso_week": week, "channel": channel, "n": n,
           "n_roots": n if roots is None else roots, "n_reporters": n if reporters is None else reporters}
    if n != SUPPRESSED:
        out["res_conf_min"] = conf
    return out


def bundle(pack: FrozenPack, site: str, cells: Iterable[dict[str, Any]], *, after: str | None, through: str,
           as_of: str | None = None) -> dict[str, Any]:
    ordered = sorted(cells, key=lambda c: (c["entity_type"], c["entity_id"], c["predicate"], c["iso_week"],
                                           c["channel"]))
    return {"schema_version": 1, "pack": pack.id, "config_hash": pack.config_hash, "site": site,
            "as_of": as_of or closing(pack, through), "after": after, "closed_through": through, "k": pack.egress.k,
            "cells": ordered}


def log_line(body: dict[str, Any], *, site: str | None = None, artifact_type: str = "cells_bundle",
             ts: str = "2026-06-01T08:00:00Z") -> bytes:
    data = canonical_bytes(body)
    row = {"artifact_type": artifact_type, "body": body, "bytes": len(data), "direction": "out",
           "sha256": sha256_hex(data), "site": site or body["site"], "ts": ts}
    return (canonical_dumps(row) + "\n").encode("utf-8")


def cand(result: dict[str, Any], s: tuple[str, str, str]) -> dict[str, Any] | None:
    return next((c for c in result["candidates"] if c["key"] == key(s)), None)


def entry(part: dict[str, Any], site: str) -> dict[str, Any]:
    return next(e for e in part["sites"] if e["site"] == site)


def detector_keys(result: dict[str, Any]) -> list[str]:
    return [c["key"] for c in result["candidates"] if "detector" in c["channels"]]


def brute_tail(ps: Sequence[float], m: int) -> float:
    total = 0.0
    for bits in itertools.product((0, 1), repeat=len(ps)):
        if sum(bits) >= m:
            pr = 1.0
            for b, p in zip(bits, ps):
                pr *= p if b else 1 - p
            total += pr
    return total


def table_count(path: Path, table: str) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


class Hq:
    """A hand-built HQ: cells per site, sent as chained bundles and ingested into a CollectiveStore."""

    def __init__(self, root: Path, pack: FrozenPack = DQ, org: OrgConfig = ORG6, *, weeks: Sequence[str] = W,
                 name: str = "collective.sqlite3") -> None:
        self.root = Path(root)
        self.pack = pack
        self.org = org
        self.weeks = list(weeks)
        self.path = self.root / name
        self.cells: dict[str, dict[tuple[str, ...], dict[str, Any]]] = {s: {} for s in SITES}
        self.sent: dict[str, str | None] = {s: None for s in SITES}
        self.last_index = -1
        self._store: CollectiveStore | None = None

    @property
    def store(self) -> CollectiveStore:
        if self._store is None:
            self._store = CollectiveStore(self.path, self.pack, self.org, clock=Clock(NOW))
        return self._store

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None

    def put(self, sites: str | Sequence[str], s: tuple[str, str, str], idxs: Iterable[int], n: Any = SUPPRESSED,
            **kw: Any) -> "Hq":
        for site in ([sites] if isinstance(sites, str) else sites):
            for i in idxs:
                c = cell(*s, self.weeks[i], n, **kw)
                self.cells[site][(c["entity_type"], c["entity_id"], c["predicate"], c["iso_week"], c["channel"])] = c
        return self

    def background(self, sites: Sequence[str] = SITES, idx: int = 0, *, channel: str = "codes") -> "Hq":
        return self.put(sites, VOCAB[self.pack.id]["bg"], (idx,), channel=channel)

    def body(self, site: str, through_idx: int, *, as_of: str | None = None) -> dict[str, Any]:
        after, through = self.sent[site], self.weeks[through_idx]
        cells = [c for c in self.cells[site].values()
                 if c["iso_week"] <= through and (after is None or c["iso_week"] > after)]
        return bundle(self.pack, site, cells, after=after, through=through, as_of=as_of)

    def send(self, through_idx: int, sites: Sequence[str] = SITES, *, as_of: str | None = None) -> "Hq":
        for site in sites:
            result = self.store.ingest_bundle(self.body(site, through_idx, as_of=as_of))
            assert result == "accepted", result
            self.sent[site] = self.weeks[through_idx]
        self.last_index = max(self.last_index, through_idx)
        return self

    def as_of(self, idx: int | None = None) -> str:
        return closing(self.pack, self.weeks[self.last_index if idx is None else idx])

    def run(self, channel: str = "X", *, as_of: str | None = None, salt: str = "t") -> dict[str, Any]:
        return detect(self.store, as_of=as_of or self.as_of(), run_channel=channel, tie_salt=salt)


class HqCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._hqs: list[Hq] = []

    def tearDown(self) -> None:
        for hq in self._hqs:
            hq.close()
        self._tmp.cleanup()

    def hq(self, pack: FrozenPack = DQ, org: OrgConfig = ORG6, **kw: Any) -> Hq:
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        hq = Hq(root, pack, org, **kw)
        self._hqs.append(hq)
        return hq


# =================================================================================================== config

class DetectorConfigTests(HqCase):
    def test_every_builtin_pack_loads_with_the_documented_values(self) -> None:
        common = {"cooldown_weeks": 4, "baseline_weeks": 26, "window_weeks": 8, "min_history_weeks": 12,
                  "alpha_site": 0.01, "lambda_floor": 0.01, "p_min": 0.01, "p_max": 0.25, "burst_min_sites": 2,
                  "pmi_smoothing": 0.5, "pmi_delta": 1.0, "cooccurrence_min_sites": 2, "res_conf_threshold": 0.95,
                  "echo_min_ratio": 0.5, "base_rate_site_fraction": 0.5, "bias": -4.0}
        weights = {"burst_surprise": 0.35, "pmi_rise": 0.5, "log_independent_roots": 0.4, "supporting_sites": 0.3,
                   "low_res_conf": -1.0, "echo": -1.5, "few_reporters_share": -1.0, "high_base_rate": -1.5}
        for pid, (budget, stale, k, lag) in DETECTOR_DEFAULTS.items():
            pack = PACKS[pid]
            with self.subTest(pack=pack.id):
                cfg = DetectorConfig.from_pack(pack)
                for name, value in {**common, "alert_budget_per_week": budget, "stale_days": stale, "k": k,
                                    "close_lag_days": lag}.items():
                    self.assertEqual(getattr(cfg, name), value, name)
                self.assertEqual(tuple(cfg.weights), FEATURES)
                self.assertEqual(dict(cfg.weights), weights)
                self.assertEqual(set(pack.detectors["ranker"]["weights"]), set(FEATURES))
                with self.assertRaises(TypeError):
                    cfg.weights["echo"] = 0.0        # type: ignore[index]

    def test_each_refused_value_raises_a_pack_error_with_its_path(self) -> None:
        cases = (
            (DQ, ("burst", "lambda_floor"), 0, "$.burst.lambda_floor"),
            (DQ, ("burst", "alpha_site"), 0, "$.burst.alpha_site"),
            (DQ, ("burst", "alpha_site"), 0.6, "$.burst.alpha_site"),
            (DQ, ("burst", "p_min"), 0.3, "$.burst.p_max"),
            (DQ, ("burst", "p_max"), 1, "$.burst.p_max"),
            (DQ, ("burst", "p_min"), 0, "$.burst.p_min"),
            (DQ, ("decoy", "stale_days"), 20, "$.decoy.stale_days"),
            (CI, ("decoy", "stale_days"), 27, "$.decoy.stale_days"),
            (DQ, ("cooccurrence", "pmi_smoothing"), 0, "$.cooccurrence.pmi_smoothing"),
            (DQ, ("burst", "min_sites"), 1, "$.burst.min_sites"),
            (DQ, ("cooccurrence", "min_sites"), 1, "$.cooccurrence.min_sites"),
            (DQ, ("resolution", "res_conf_min"), 0, "$.resolution.res_conf_min"),
            (DQ, ("independence", "echo_min_ratio"), 1.5, "$.independence.echo_min_ratio"),
            (DQ, ("decoy", "base_rate_site_fraction"), 0, "$.decoy.base_rate_site_fraction"),
            (DQ, ("alert_budget_per_week",), -1, "$.alert_budget_per_week"),
            (DQ, ("cooldown_weeks",), 53, "$.cooldown_weeks"),
            (DQ, ("min_history_weeks",), 0, "$.min_history_weeks"),
            (DQ, ("ranker", "bias"), "x", "$.ranker.bias"),
            (DQ, ("window_weeks",), 5, "$.window_weeks"),
        )
        for pack, path, value, where in cases:
            with self.subTest(pack=pack.id, path=path, value=value):
                with self.assertRaises(PackError) as cm:
                    pack_copy(self.tmp, pack.id, {("detectors.json", *path): value})
                self.assertEqual((cm.exception.file, cm.exception.path), ("detectors.json", where))

    def test_unknown_key_and_missing_weight(self) -> None:
        for name, edit, where in (("unknown", lambda o: o.update(extra=1), "$"),
                                  ("missing", lambda o: o["ranker"]["weights"].pop("echo"), "$.ranker.weights.echo"),
                                  ("nested", lambda o: o["burst"].update(rate_floor=0.1), "$.burst")):
            with self.subTest(case=name):
                d = Path(tempfile.mkdtemp(dir=self.tmp))
                pack_dir = d / "p"
                shutil.copytree(DQ.directory, pack_dir)
                obj = json.loads((pack_dir / "detectors.json").read_text(encoding="utf-8"))
                edit(obj)
                (pack_dir / "detectors.json").write_text(json.dumps(obj), encoding="utf-8")
                with self.assertRaises(PackError) as cm:
                    load_pack(pack_dir)
                self.assertEqual((cm.exception.file, cm.exception.path), ("detectors.json", where))

    def test_claims_integrity_stale_days_rule_against_its_lag(self) -> None:
        self.assertEqual(CI.egress.close_lag_days, 21)
        pack_copy(self.tmp, CI.id, {("detectors.json", "decoy", "stale_days"): 28})
        with self.assertRaises(PackError):
            pack_copy(self.tmp, CI.id, {("detectors.json", "decoy", "stale_days"): 27})
        # no relation between min_history_weeks and baseline_weeks: G3's high-volume copy still loads
        pack_copy(self.tmp, DQ.id, {("detectors.json", "baseline_weeks"): 4, ("detectors.json", "window_weeks"): 6})


# =================================================================================================== org

class OrgTests(unittest.TestCase):
    def test_parse_and_hash_invariance(self) -> None:
        org = parse_org(org_dict())
        self.assertEqual(org.enterprise, ENTERPRISE)
        self.assertEqual(list(org.sites), sorted(SITES))
        self.assertEqual(org.unit_path("s3"), "acme/amer/us")
        self.assertRegex(org.org_hash, r"^[0-9a-f]{64}$")
        reordered = org_dict(tuple(reversed(SIX)))
        reordered = {k: reordered[k] for k in reversed(list(reordered))}
        reordered["sites"] = [{k: s[k] for k in reversed(list(s))} for s in reordered["sites"]]
        self.assertEqual(parse_org(reordered).org_hash, org.org_hash)
        changed = org_dict()
        changed["sites"][0]["display_name"] = "Other"
        self.assertNotEqual(parse_org(changed).org_hash, org.org_hash)

    def test_non_ascii_display_name_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "org.json"
            obj = org_dict()
            obj["sites"][0]["display_name"] = "Werk Dörnhagen 東京"
            path.write_text(json.dumps(obj, ensure_ascii=True), encoding="utf-8")
            self.assertIn("\\u00f6", path.read_text(encoding="utf-8"))
            self.assertEqual(load_org(path).sites["s1"].display_name, "Werk Dörnhagen 東京")

    def refused(self, edit: Any, path: str, secret: str | None = None) -> None:
        obj = org_dict()
        edit(obj)
        with self.assertRaises(OrgError) as cm:
            parse_org(obj)
        self.assertEqual(cm.exception.path, path)
        self.assertTrue(str(cm.exception).startswith(f"org: {path}: "))
        if secret is not None:
            self.assertNotIn(secret, str(cm.exception))
            self.assertNotIn(secret, repr(cm.exception))

    def test_every_refusal(self) -> None:
        def site(i: int, **kw: Any) -> Any:
            return lambda o: o["sites"][i].update(kw)
        cases = (
            ("duplicate site id", site(2, site_id="s1"), "$.sites[2].site_id", None),
            ("bad site id", site(1, site_id="Bad Site"), "$.sites[1].site_id", "Bad Site"),
            ("bad segment", site(1, unit_path="acme/EMEA-X/fr"), "$.sites[1].unit_path", "EMEA-X"),
            ("empty segment", site(1, unit_path="acme//fr"), "$.sites[1].unit_path", "acme//fr"),
            ("one segment", site(1, unit_path="acme"), "$.sites[1].unit_path", None),
            ("six segments", site(1, unit_path="acme/a/b/c/d/zzfr"), "$.sites[1].unit_path", "zzfr"),
            ("seven segments", site(1, unit_path="acme/a/b/c/d/e/zzfr"), "$.sites[1].unit_path", "zzfr"),
            ("another root", site(1, unit_path="globex/emea/fr"), "$.sites[1].unit_path", "globex"),
            ("not a string", site(1, unit_path=7), "$.sites[1].unit_path", None),
            ("duplicate unit path", site(1, unit_path="acme/emea/de"), "$.sites[1].unit_path", "acme/emea/de"),
            ("nested unit path", site(1, unit_path="acme/emea/de/sub"), "$.sites[1].unit_path", "sub"),
            ("ancestor unit path", site(1, unit_path="acme/emea"), "$.sites[1].unit_path", None),
            ("bad country", site(0, country="de"), "$.sites[0].country", None),
            ("long country", site(0, country="DEU"), "$.sites[0].country", "DEU"),
            ("empty display name", site(0, display_name=""), "$.sites[0].display_name", None),
            ("long display name", site(0, display_name="Q" * 81), "$.sites[0].display_name", "Q" * 81),
            ("control character", site(0, display_name="Plant\x07Zed"), "$.sites[0].display_name", "Zed"),
            ("format character", site(0, display_name="Plant​Zed"), "$.sites[0].display_name", "Zed"),
            ("line separator", site(0, display_name="Plant Zed"), "$.sites[0].display_name", "Zed"),
            ("not a string name", site(0, display_name=5), "$.sites[0].display_name", None),
            ("unknown site key", site(0, secret_key="Zed"), "$.sites[0]", "secret_key"),
            ("missing site key", lambda o: o["sites"][0].pop("country"), "$.sites[0].country", None),
            ("site not an object", lambda o: o["sites"].__setitem__(0, "s1"), "$.sites[0]", None),
            ("unknown top key", lambda o: o.update(secret_key=1), "$", "secret_key"),
            ("missing top key", lambda o: o.pop("enterprise"), "$.enterprise", None),
            ("schema version", lambda o: o.update(schema_version=2), "$.schema_version", None),
            ("bool schema version", lambda o: o.update(schema_version=True), "$.schema_version", None),
            ("empty sites", lambda o: o.update(sites=[]), "$.sites", None),
            ("too many sites", lambda o: o.update(sites=o["sites"] * 200), "$.sites", None),
            ("bad enterprise", lambda o: o.update(enterprise="Acme Corp"), "$.enterprise", "Acme Corp"),
        )
        for name, edit, path, secret in cases:
            with self.subTest(case=name):
                self.refused(edit, path, secret)
        with self.assertRaises(OrgError) as cm:
            parse_org([1, 2])
        self.assertEqual(cm.exception.path, "$")

    def test_load_org_on_a_non_strict_or_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for name, data, reason in (("dup.json", b'{"schema_version": 1, "schema_version": 1}', "duplicate_key"),
                                       ("nan.json", b'{"schema_version": NaN}', "non_finite"),
                                       ("bom.json", b"\xef\xbb\xbf{}", "bom"), ("bad.json", b"{oops", "syntax")):
                with self.subTest(file=name):
                    (Path(tmp) / name).write_bytes(data)
                    with self.assertRaises(OrgError) as cm:
                        load_org(Path(tmp) / name)
                    self.assertEqual(str(cm.exception), f"org: $: not strict JSON ({reason})")
            with self.assertRaises(OrgError):
                load_org(Path(tmp) / "absent.json")


class DecisionUnitTests(unittest.TestCase):
    ORG = parse_org(org_dict((("a1", "acme/emea/sub-a/plant-1"), ("a2", "acme/emea/sub-a/plant-2"),
                              ("b1", "acme/emea/sub-b"), ("c1", "acme/amer/sub-c/plant-9"), ("c2", "acme/amer/x"))))

    def test_table(self) -> None:
        for sites, unit in ((["a1"], "acme/emea/sub-a/plant-1"), (["b1"], "acme/emea/sub-b"),
                            (["a1", "a2"], "acme/emea/sub-a"), (["a1", "b1"], "acme/emea"),
                            (["a2", "b1", "a1"], "acme/emea"), (["a1", "c1"], "acme"), (["c1", "c2"], "acme/amer"),
                            (["a1", "a1", "a2", "a2"], "acme/emea/sub-a"), (("b1", "c2", "a1"), "acme")):
            with self.subTest(sites=sites):
                self.assertEqual(self.ORG.decision_unit(sites), unit)
                self.assertEqual(self.ORG.decision_unit(reversed(list(sites))), unit)
                self.assertEqual(self.ORG.decision_unit(iter(sites)), unit)

    def test_empty_or_unknown_input_raises(self) -> None:
        for sites in ([], ["zz"], ["a1", "zz"], [5]):
            with self.subTest(sites=sites), self.assertRaises(OrgError) as cm:
                self.ORG.decision_unit(sites)
            self.assertNotIn("zz", str(cm.exception))
        with self.assertRaises(OrgError):
            self.ORG.unit_path("zz")


# =================================================================================================== store

S1 = ser(DQ, 0, 0)


class StoreTests(HqCase):
    def open(self, pack: FrozenPack = DQ, org: OrgConfig = ORG6, name: str = "c.sqlite3",
             clock: Any = None) -> CollectiveStore:
        st = CollectiveStore(self.tmp / name, pack, org, clock=clock or Clock(NOW))
        self.addCleanup(st.close)
        return st

    def b(self, site: str = "s1", weeks: Sequence[int] = (5,), *, after: int | None = None, through: int = 9,
          n: Any = SUPPRESSED, as_of: str | None = None) -> dict[str, Any]:
        cells = [cell(*S1, W[i], n) for i in weeks]
        return bundle(DQ, site, cells, after=None if after is None else W[after], through=W[through], as_of=as_of)

    def counts(self, st: CollectiveStore) -> tuple[int, int, int]:
        return (table_count(st.path, "cells"), table_count(st.path, "bundles"), table_count(st.path, "rejections"))

    def test_tables_and_no_fabric_table_name(self) -> None:
        source = (ROOT / "mycelic" / "store.py").read_text(encoding="utf-8")
        fabric = set(re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?([A-Za-z_][A-Za-z0-9_]*)", source))
        self.assertGreaterEqual(fabric, {"meta", "orgs", "agents", "memories", "lineage_edges", "events", "rules"})
        fabric |= {"memories", "events", "outbox"}
        self.assertEqual(set(TABLES) & fabric, set())
        st = self.open()
        conn = sqlite3.connect(str(st.path))
        try:
            names = {n for (n,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")}
            mode = conn.execute("PRAGMA journal_mode").fetchone()
        finally:
            conn.close()
        self.assertEqual(names, set(TABLES))
        self.assertEqual(mode, ("wal",))
        self.assertEqual(REJECT_REASONS, ("bad_line", "config_hash", "unknown_site", "invalid", "site_mismatch",
                                          "conflict", "sequence"))
        self.assertEqual(dict(RUN_CHANNELS), {"X": CHANNELS, "S": ("codes",)})

    def test_memory_is_refused_and_the_clock_must_be_a_timestamp(self) -> None:
        with self.assertRaises(StoreError):
            CollectiveStore(":memory:", DQ, ORG6, clock=Clock(NOW))
        with self.assertRaises(ValueError):
            CollectiveStore(self.tmp / "bad-clock.sqlite3", DQ, ORG6, clock=lambda: "yesterday")
        st = self.open(name="ok.sqlite3")
        st._clock = lambda: 5                                   # a later bad clock fails the write, writes nothing
        with self.assertRaises(ValueError):
            st.ingest_bundle(self.b())
        self.assertEqual(self.counts(st), (0, 0, 0))

    def test_store_info_mismatch(self) -> None:
        self.open().close()
        other_config = pack_copy(self.tmp, DQ.id, {("detectors.json", "cooldown_weeks"): 3})
        other_enterprise = parse_org(org_dict(tuple((s, p.replace("acme", "globex")) for s, p in SIX), "globex"))
        for pack, org, what in ((other_config, ORG6, "config_hash"), (CI, ORG6, "pack_id"),
                                (DQ, other_enterprise, "enterprise")):
            with self.subTest(key=what):
                with self.assertRaises(StoreError) as cm:
                    CollectiveStore(self.tmp / "c.sqlite3", pack, org, clock=Clock(NOW))
                self.assertEqual(str(cm.exception), f"store_info mismatch: {what}")
        self.open().close()

    def test_accept_and_identical_resend(self) -> None:
        st = self.open()
        body = self.b(weeks=(2, 5), n=4)
        self.assertEqual(st.ingest_bundle(body, row_site="s1"), "accepted")
        before = self.counts(st)
        self.assertEqual(before, (2, 1, 0))
        self.assertEqual(st.ingest_bundle(json.loads(json.dumps(body))), "duplicate")
        self.assertEqual(self.counts(st), before)
        sha = sha256_hex(canonical_bytes(body))
        self.assertEqual(st.bundles(), (BundleRow(sha, "s1", closing(DQ, W[9]), None, W[9]),))
        conn = sqlite3.connect(str(st.path))
        try:
            rows = conn.execute("SELECT site, entity_type, entity_id, predicate, iso_week, channel, n, n_roots, "
                                "n_reporters, res_conf_min, as_of, bundle FROM cells ORDER BY iso_week").fetchall()
        finally:
            conn.close()
        self.assertEqual(rows[0], ("s1", *S1, W[2], "codes", 4, 4, 4, 1.0, closing(DQ, W[9]), sha))
        suppressed = st.ingest_bundle(self.b("s2"))
        self.assertEqual(suppressed, "accepted")
        _, cells = st.detection_inputs(closing(DQ, W[9]), "X")
        self.assertIn(CellRow("s2", *S1, W[5], "codes", None, None, None, None,
                              sha256_hex(canonical_bytes(self.b("s2"))), closing(DQ, W[9])), cells)

    def test_each_rejection_writes_one_row_and_no_cells(self) -> None:
        st = self.open()
        extra_cell = self.b()
        extra_cell["cells"][0]["n_e"] = 5
        extra_top = self.b()
        extra_top["marginals"] = {"n_p": 7}
        other_config = self.b()
        other_config["config_hash"] = "0" * 64
        cases = (
            ("config_hash", other_config, None, None, None),
            ("unknown_site", self.b("zz9"), None, None, None),
            ("invalid", self.b(n=2), None, "$.cells[0].n", "count"),
            ("invalid", extra_cell, None, "$.cells[0]", "additionalProperties"),
            ("invalid", extra_top, None, "$", "additionalProperties"),
            ("invalid", [1, 2], None, "$", "type"),
            ("invalid", {**self.b(), "site": 5}, None, "$.site", "const"),
            ("invalid", {k: v for k, v in self.b().items() if k != "config_hash"}, None, "$.config_hash", "required"),
            ("invalid", {**self.b(), "pack": CI.id}, None, "$.pack", "const"),
            ("site_mismatch", self.b(), "s2", None, None),
        )
        for reason, body, row_site, path, keyword in cases:
            with self.subTest(reason=reason, path=path):
                before = self.counts(st)
                self.assertEqual(st.ingest_bundle(body, row_site=row_site), reason)
                self.assertEqual(self.counts(st), (before[0], before[1], before[2] + 1))
                row = st.rejections()[-1]
                self.assertEqual((row.sha256, row.reason, row.path, row.keyword, row.line, row.received_at),
                                 (sha256_hex(canonical_bytes(body)), reason, path, keyword, None, NOW))
                self.assertEqual(st.ingest_bundle(body, row_site=row_site), reason)
                self.assertEqual(self.counts(st)[2], before[2] + 1)      # UNIQUE (sha256, reason)
        self.assertEqual(st.rejections()[1].site, "zz9")
        self.assertEqual(st.rejections()[0].site, "s1")
        self.assertIsNone(st.rejections()[5].site)
        self.assertEqual(self.counts(st)[0], 0)

    def test_conflict_is_checked_before_sequence(self) -> None:
        st = self.open()
        self.assertEqual(st.ingest_bundle(self.b(weeks=(2, 5))), "accepted")
        changed = self.b(weeks=(2, 5))
        changed["cells"][1] = cell(*S1, W[5], 4)
        self.assertEqual(st.ingest_bundle(changed), "conflict")
        changed_roots = self.b(weeks=(2, 5), n=4)
        self.assertEqual(st.ingest_bundle(changed_roots), "conflict")
        self.assertEqual(table_count(st.path, "cells"), 2)
        st2 = self.open(name="c2.sqlite3")
        self.assertEqual(st2.ingest_bundle(self.b(weeks=(2,), n=4)), "accepted")
        conf = self.b(weeks=(2,), n=4)
        conf["cells"][0]["res_conf_min"] = 0.9
        self.assertEqual(st2.ingest_bundle(conf), "conflict")
        self.assertEqual([r.reason for r in st2.rejections()], ["conflict"])

    def test_sequence_gap_overlap_and_backwards_as_of(self) -> None:
        st = self.open()
        self.assertEqual(st.ingest_bundle(self.b(weeks=(5,), through=9)), "accepted")
        overlap = bundle(DQ, "s1", [cell(*S1, W[5]), cell(*S1, W[11])], after=W[3], through=W[12])
        gap = self.b(weeks=(13,), after=11, through=14)
        backwards = self.b(weeks=(11,), after=9, through=12, as_of=(date.fromisoformat(closing(DQ, W[9]))
                                                                     - timedelta(days=1)).isoformat())
        first_again = self.b(weeks=(5, 7), through=9)
        for name, body in (("value-preserving overlap", overlap), ("gap", gap), ("backwards as_of", backwards),
                           ("a second first bundle", first_again)):
            with self.subTest(case=name):
                self.assertEqual(st.ingest_bundle(body), "sequence")
        self.assertEqual(table_count(st.path, "cells"), 1)
        self.assertEqual(st.ingest_bundle(self.b(weeks=(11,), after=9, through=12)), "accepted")

    def test_a_sequence_rejected_bundle_is_accepted_after_its_predecessor(self) -> None:
        st = self.open()
        first, second = self.b("s2", weeks=(5,), through=9), self.b("s2", weeks=(11,), after=9, through=12)
        self.assertEqual(st.ingest_bundle(second), "sequence")
        self.assertEqual(st.ingest_bundle(second), "sequence")
        self.assertEqual(st.ingest_bundle(first), "accepted")
        self.assertEqual(st.ingest_bundle(second), "accepted")
        self.assertEqual([b.closed_through for b in st.bundles()], [W[9], W[12]])
        self.assertEqual([r.reason for r in st.rejections()], ["sequence"])

    def write_log(self, lines: Sequence[bytes]) -> Path:
        path = Path(tempfile.mkdtemp(dir=self.tmp)) / "receive.jsonl"
        path.write_bytes(b"".join(lines))
        return path

    def test_log_bad_line_usage_and_idempotent_reingest(self) -> None:
        st = self.open()
        usage = {"schema_version": 1, "closed_through": W[9], "groups": []}
        good1, good2 = log_line(self.b("s1")), log_line(self.b("s2"))
        path = self.write_log([good1, b"this is not json\n", good2, b"\n", log_line(usage, site="s1",
                              artifact_type="usage_summary"), good1])
        report = st.ingest_log(path)
        self.assertEqual((report.accepted, report.duplicates, report.ignored), (2, 1, 1))
        self.assertEqual(dict(report.rejected), {**dict.fromkeys(REJECT_REASONS, 0), "bad_line": 1})
        self.assertEqual(list(report.rejected), list(REJECT_REASONS))
        bad = st.rejections()[0]
        self.assertEqual((bad.reason, bad.line, bad.sha256, bad.site, bad.path, bad.keyword),
                         ("bad_line", 2, sha256_hex(b"this is not json"), None, None, None))
        before = self.counts(st)
        again = st.ingest_log(path)
        self.assertEqual((again.accepted, again.duplicates, again.ignored, again.rejected["bad_line"]), (0, 3, 1, 1))
        self.assertEqual(self.counts(st), before)
        empty = st.ingest_log(self.tmp / "absent.jsonl")
        self.assertEqual((empty.accepted, empty.duplicates, empty.ignored, sum(empty.rejected.values())), (0, 0, 0, 0))

    def test_forged_lines_never_raise_or_write_cells(self) -> None:
        st = self.open()
        good = json.loads(log_line(self.b("s1")))
        wrong_sha = dict(good, sha256="0" * 64)
        wrong_bytes = dict(good, bytes=good["bytes"] + 1)
        extra = self.b("s1")
        extra["cells"][0]["n_e"] = 3
        lines = [
            (canonical_dumps(wrong_sha) + "\n").encode(),
            (canonical_dumps(wrong_bytes) + "\n").encode(),
            log_line(self.b("s1"), site="s2"),
            log_line(extra),
            b'{"artifact_type":"cells_bundle","body":{"closed_through":"2026-W10","x":"\\ud800"},"bytes":1,'
            b'"direction":"out","sha256":"00","site":"s1","ts":"2026-06-01T08:00:00Z"}\n',
            (canonical_dumps(dict(good, body=[1])) + "\n").encode(),
            (canonical_dumps(dict(good, extra=1)) + "\n").encode(),
            b"\xff\xfe\n",
            b'{"a": 1, "a": 2}\n',
        ]
        report = st.ingest_log(self.write_log(lines))
        self.assertEqual(report.accepted, 0)
        self.assertEqual(dict(report.rejected), {**dict.fromkeys(REJECT_REASONS, 0), "bad_line": 7,
                                                 "site_mismatch": 1, "invalid": 1})
        self.assertEqual(table_count(st.path, "cells"), 0)
        self.assertEqual([r.path for r in st.rejections() if r.reason == "invalid"], ["$.cells[0]"])

    def test_immutability_triggers(self) -> None:
        st = self.open()
        st.ingest_bundle(self.b(weeks=(2, 5)))
        conn = sqlite3.connect(str(st.path))
        try:
            for statement in ("UPDATE cells SET n = 5", "DELETE FROM cells", "UPDATE bundles SET as_of = '2030-01-01'",
                              "DELETE FROM bundles", "UPDATE cells SET bundle = 'x' WHERE iso_week = '2026-W03'"):
                with self.subTest(statement=statement):
                    with self.assertRaises(sqlite3.DatabaseError) as cm:
                        conn.execute(statement)
                    self.assertIn("immutable", str(cm.exception))
        finally:
            conn.close()
        self.assertEqual(self.counts(st)[:2], (2, 1))

    def test_as_of_visibility_and_channels(self) -> None:
        st = self.open()
        st.ingest_bundle(self.b("s1", weeks=(5,), through=9))
        later = bundle(DQ, "s1", [cell(*S1, W[11]), cell(*S1, W[11], channel="text_only")], after=W[9],
                       through=W[12])
        st.ingest_bundle(later)
        early, late = closing(DQ, W[9]), closing(DQ, W[12])
        bundles, cells = st.detection_inputs(early, "X")
        self.assertEqual([b.closed_through for b in bundles], [W[9]])
        self.assertEqual([c.iso_week for c in cells], [W[5]])
        bundles, cells = st.detection_inputs(late, "X")
        self.assertEqual([b.closed_through for b in bundles], [W[9], W[12]])
        self.assertEqual([(c.iso_week, c.channel) for c in cells], [(W[5], "codes"), (W[11], "codes"),
                                                                  (W[11], "text_only")])
        _, cells = st.detection_inputs(late, "S")
        self.assertEqual({c.channel for c in cells}, {"codes"})
        for bad in (("2026-13-01", "X"), ("2026-06-01T00:00:00Z", "X"), (late, "Y"), (late, None)):
            with self.subTest(args=bad), self.assertRaises(StoreError):
                st.detection_inputs(*bad)
        result = detect(st, as_of=early, run_channel="X", tie_salt="t")
        self.assertEqual((result["bundles"], result["cells"], result["last_week"]), (1, 1, W[9]))
        self.assertEqual(result["ignored_cells"], dict.fromkeys(IGNORED_KEYS, 0))

    def test_sql_only_in_store_and_every_select_is_ordered(self) -> None:
        files = sorted(DETECT.glob("*.py"))
        self.assertEqual([p.name for p in files], ["__init__.py", "detectors.py", "org.py", "rules.py", "store.py"])
        for path in files:
            source = path.read_text(encoding="utf-8")
            sql = [s for s in string_constants(path) if _SQL_STATEMENT.match(s)]
            with self.subTest(module=path.name):
                if path.name == "store.py":
                    selects = [s for s in string_constants(path) if s.lstrip().startswith("SELECT")]
                    self.assertGreaterEqual(len(selects), 10)
                    for s in selects:
                        self.assertIn("ORDER BY", s, s)
                    self.assertIn("import sqlite3", source)
                    for table in ("cells", "bundles"):
                        self.assertNotRegex(source, rf"(UPDATE|DELETE FROM)\s+{table}\b")
                else:
                    self.assertEqual(sql, [])
                    self.assertNotRegex(source, r"(import|from) sqlite3")

    def test_org_sync_and_org_log(self) -> None:
        st = self.open()
        log = st.org_log()
        self.assertEqual([(r.site_id, r.old_unit_path, r.new_unit_path) for r in log],
                         [(s, None, p) for s, p in SIX])
        self.assertEqual({(r.org_hash, r.at) for r in log}, {(ORG6.org_hash, NOW)})
        st.close()
        self.open().close()
        self.assertEqual(len(self.open().org_log()), 6)
        changed = [("s1", "acme/emea/de"), ("s2", "acme/amer/fr2"), ("s4", "acme/amer/ca"), ("s5", "acme/apac/jp"),
                   ("s6", "acme/apac/au"), ("s7", "acme/apac/nz")]
        obj = org_dict(changed)
        obj["sites"][2]["display_name"] = "Renamed"
        obj["sites"][2]["country"] = "CA"
        new_org = parse_org(obj)
        st = self.open(org=new_org, clock=Clock("2028-02-01T00:00:00Z"))
        rows = st.org_log()[6:]
        self.assertEqual([(r.site_id, r.old_unit_path, r.new_unit_path) for r in rows],
                         [("s2", "acme/emea/fr", "acme/amer/fr2"), ("s3", "acme/amer/us", None),
                          ("s7", None, "acme/apac/nz")])
        self.assertEqual({(r.org_hash, r.at) for r in rows}, {(new_org.org_hash, "2028-02-01T00:00:00Z")})
        conn = sqlite3.connect(str(st.path))
        try:
            sites = conn.execute("SELECT site_id, unit_path, country, display_name FROM org_sites "
                                 "ORDER BY site_id").fetchall()
        finally:
            conn.close()
        self.assertEqual([s[0] for s in sites], ["s1", "s2", "s4", "s5", "s6", "s7"])
        self.assertEqual(sites[2][2:], ("CA", "Renamed"))
        st.close()
        self.assertEqual(len(self.open(org=new_org).org_log()), 9)

    def test_edge_site_round_trip_and_a_crash_duplicate_line(self) -> None:
        clock = Clock("2026-03-11T08:00:00.000Z")
        hq_dir = self.tmp / "hq"
        for sid in ("s1", "s2"):
            site = EdgeSite(DQ, sid, self.tmp / "edge", runtime=None, clock=clock, master_data=universe_master(DQ),
                            hq_dir=hq_dir)
            self.addCleanup(site.close)
            site.ingest([coded(DQ, f"{sid}-r{i}", sid, "2026-03-02", "SD-9", reporter=f"R{i}") for i in range(4)])
            site.extract("lexical")
            clock.value = "2026-03-29T08:00:00.000Z"
            emission = site.emit_cells("2026-03-29")
            clock.value = "2026-03-11T08:00:00.000Z"
            self.assertIsNotNone(emission)
        st = self.open()
        receive = hq_dir / "receive.jsonl"
        report = st.ingest_log(receive)
        self.assertEqual((report.accepted, report.duplicates, sum(report.rejected.values())), (2, 0, 0))
        _, cells = st.detection_inputs("2026-03-29", "X")
        self.assertEqual({(c.site, c.entity_id, c.n) for c in cells}, {("s1", "SD-9", 4), ("s2", "SD-9", 4)})
        with open(receive, "ab") as fh:
            fh.write(receive.read_bytes().splitlines(keepends=True)[0])
        again = st.ingest_log(receive)
        self.assertEqual((again.accepted, again.duplicates, sum(again.rejected.values())), (0, 3, 0))
        self.assertEqual(table_count(st.path, "rejections"), 0)

    def test_rejections_and_errors_hold_no_value(self) -> None:
        st = self.open()
        secret_id, secret_name = "SD-777", "Plant Zebulon"
        body = bundle(DQ, "s1", [cell("product", secret_id, "overheat", W[5], 2)], after=None, through=W[9])
        self.assertEqual(st.ingest_bundle(body), "invalid")
        self.assertEqual(st.ingest_bundle({**self.b(), "site": "Bad Site!"}), "unknown_site")
        self.assertIsNone(st.rejections()[-1].site)
        self.assertEqual(st.ingest_bundle({**self.b(), "as_of": secret_name}), "invalid")
        for row in st.rejections():
            for value in row:
                self.assertNotIn(secret_id, str(value))
                self.assertNotIn(secret_name, str(value))
                self.assertNotIn("Bad Site", str(value))
        with self.assertRaises(StoreError) as cm:
            st.ingest_bundle({"note": secret_name, "x": float("nan")})
        self.assertNotIn(secret_name, str(cm.exception))
        self.assertEqual(table_count(st.path, "rejections"), 3)


# =================================================================================================== detectors

Y = ("product", "IP-7", "occlusion")


class ImputationTests(HqCase):
    def test_bounds_at_k3_and_k5(self) -> None:
        for k in (3, 5):
            with self.subTest(k=k):
                self.assertEqual(bounds(None, k), (1, k - 1))
                for v in (k, 7, 10 ** 9):
                    self.assertEqual(bounds(v, k), (v, v))

    def world(self, pack: FrozenPack) -> tuple[dict[str, Any], tuple[str, str, str]]:
        hq = self.hq(pack)
        x, x_other, y, z = ser(pack, 0, 0), ser(pack, 0, 1), ser(pack, 1, 0), ser(pack, 2, 1)
        hq.background()
        # site s1: the key and its marginals, every cell '<k'; baseline weeks 0..22, window 23..30 at step 30
        hq.put("s1", x, (2, 5, 9, 25, 28)).put("s1", x_other, (3, 27)).put("s1", y, (4, 6, 26)).put("s1", z, (7, 24))
        hq.put(["s2", "s3"], x, (29, 30))
        hq.send(30)
        return hq.run(), x

    def test_all_suppressed_windows_and_baselines_use_the_hand_bounds(self) -> None:
        for pack in PACKS.values():
            with self.subTest(pack=pack.id):
                k = pack.egress.k
                result, x = self.world(pack)
                snap = cand(result, x)["snapshot"]
                self.assertEqual(snap["week"], W[30])
                self.assertEqual(snap["window"], [W[23], W[30]])
                d2 = entry(snap["d2"], "s1")
                lam = max(0.01, (k - 1) * 3 / 23)               # 3 baseline cells, upper bound k-1 each, B_eff 23
                self.assertEqual((d2["status"], d2["history_weeks"], d2["test"], d2["c"]),
                                 ("eligible", 23, "combined", 2))
                self.assertEqual(d2["baseline_rate"], lam)
                self.assertEqual(d2["expected"], lam * 8)
                self.assertEqual(d2["logp"], stats.poisson_logsf(2, lam * 8))
                self.assertFalse(d2["exceeded"])
                d3 = entry(snap["d3"], "s1")
                self.assertEqual(d3["n_ep_lb"], 2)
                # window: the key's own 2 cells at their lower bound, in all four counts; every other cell at k/2
                # (one of the entity's other predicates, one of the predicate's other entities, one other)
                self.assertEqual(d3["pmi_window"], stats.smoothed_pmi(2, 2 + k / 2, 2 + k / 2, 2 + 3 * k / 2, 0.5))
                # baseline: the key's own 3 cells at their upper bound; one entity cell, two predicate cells and four
                # other cells of the type at k/2
                own = (k - 1) * 3
                self.assertEqual(d3["pmi_baseline"], stats.smoothed_pmi(own, own + k / 2, own + 2 * k / 2,
                                                                        own + 4 * k / 2, 0.5))
                self.assertEqual(d3["rise"], d3["pmi_window"] - d3["pmi_baseline"])
                self.assertFalse(d3["rising"])
                self.assertEqual(len(snap["lineage"]), 6)
                self.assertEqual(snap["independent_roots"], 6)
                self.assertEqual(snap["root_ratio_ub"], 1.0)
                self.assertFalse(snap["flags"]["echo"])
                self.assertIsNone(snap["res_conf"])

    def test_a_suppressed_window_exceeds_a_near_zero_baseline(self) -> None:
        result, x = self.world(DQ)
        d2 = entry(cand(result, x)["snapshot"]["d2"], "s2")
        self.assertEqual((d2["c"], d2["baseline_rate"], d2["expected"]), (2, 0.01, 0.01 * 8))
        self.assertEqual(d2["logp"], stats.poisson_logsf(2, 0.08))
        self.assertLess(math.exp(d2["logp"]), 0.01)
        self.assertTrue(d2["exceeded"])


class D2Tests(HqCase):
    def scenario(self, extra: Any = None) -> dict[str, Any]:
        hq = self.hq()
        hq.background(["s1", "s2", "s3", "s5", "s6"], 0).background(["s4"], 21)
        hq.put(["s1", "s2", "s3"], Y, (39, 40)).put("s4", Y, (40,)).put("s5", Y, (12,))
        if extra is not None:
            extra(hq)
        hq.send(40)
        return hq.run()

    def test_three_of_six_against_brute_force_with_hand_p_s(self) -> None:
        result = self.scenario()
        snap = cand(result, Y)["snapshot"]
        self.assertEqual(snap["week"], W[40])
        d2 = snap["d2"]
        self.assertEqual((d2["m"], d2["n"]), (3, 6))
        self.assertEqual([entry(d2, s)["exceeded"] for s in SITES], [True, True, True, False, False, False])
        ps = [entry(d2, s)["p_s"] for s in SITES]
        # s4 has 12 weeks of history and no past window (p_max); s5 has exactly one possible past window of 14
        self.assertEqual(ps, [0.01, 0.01, 0.01, 0.25, 1 / 14, 0.01])
        self.assertEqual(entry(d2, "s4")["history_weeks"], 12)
        self.assertAlmostEqual(d2["surprise"], -math.log(brute_tail(ps, 3)), delta=1e-9)
        self.assertEqual(snap["features"]["burst_surprise"], d2["surprise"])
        self.assertEqual(snap["supporting_sites"], ["s1", "s2", "s3"])
        self.assertEqual(snap["contributing_sites"], ["s1", "s2", "s3", "s4"])
        self.assertEqual((snap["decision_unit"], cand(result, Y)["decision_unit"]), ("acme", "acme"))
        self.assertEqual(cand(result, Y)["detectors"], ["d2"])

    def test_short_history_is_excluded_and_suppressed_history_is_kept(self) -> None:
        hq = self.hq()
        hq.background(["s1", "s2", "s3", "s4", "s5"], 0).background(["s6"], 25)
        hq.put(["s1", "s2", "s3", "s6"], Y, (39, 40)).put("s5", Y, (10, 20, 28))
        hq.put("s4", Y, (15,), n=3).put("s4", Y, (20,))
        hq.send(40)
        snap = cand(hq.run(), Y)["snapshot"]
        d2 = snap["d2"]
        self.assertEqual((d2["m"], d2["n"]), (3, 5))
        s6 = entry(d2, "s6")
        self.assertEqual((s6["status"], s6["history_weeks"], s6["c"], s6["p_s"], s6["exceeded"]),
                         ("short_history", 8, None, None, None))
        self.assertEqual(snap["flags"]["short_history_sites"], ["s6"])
        self.assertIn("s6", snap["contributing_sites"])
        self.assertNotIn("s6", snap["supporting_sites"])
        self.assertEqual((entry(d2, "s5")["status"], entry(d2, "s5")["suppressed_history"]), ("eligible", True))
        self.assertFalse(entry(d2, "s4")["suppressed_history"])
        self.assertEqual(snap["flags"]["suppressed_history_sites"], ["s5"])
        eligible = [e for e in d2["sites"] if e["status"] == "eligible"]
        ps = [e["p_s"] for e in eligible]
        without = [e["p_s"] for e in eligible if e["site"] != "s5"]
        self.assertAlmostEqual(d2["surprise"], -math.log(brute_tail(ps, 3)), delta=1e-9)
        self.assertLessEqual(d2["surprise"], -math.log(brute_tail(without, 3)))

    def test_p_s_never_uses_the_current_or_a_later_window(self) -> None:
        base = cand(self.scenario(), Y)["snapshot"]["d2"]
        changed = cand(self.scenario(lambda hq: hq.put("s5", Y, (34,)).put("s4", Y, (35, 36), n=3)
                                     .put("s6", Y, (36, 37, 38))), Y)["snapshot"]["d2"]
        self.assertEqual({e["site"]: e["p_s"] for e in base["sites"]},
                         {e["site"]: e["p_s"] for e in changed["sites"]})
        self.assertEqual(entry(changed, "s5")["p_s"], 1 / 14)
        self.assertNotEqual((base["m"], base["surprise"]), (changed["m"], changed["surprise"]))

    def test_a_never_seen_series_and_huge_counts_stay_finite(self) -> None:
        hq = self.hq()
        hq.background().put(["s1", "s2", "s3"], Y, (40,), n=10 ** 9)
        hq.send(40)
        snap = cand(hq.run(), Y)["snapshot"]
        for s in ("s1", "s2", "s3"):
            e = entry(snap["d2"], s)
            self.assertEqual((e["c"], e["baseline_rate"], e["exceeded"]), (10 ** 9, 0.01, True))
            self.assertEqual(e["logp"], stats.poisson_logsf(10 ** 9, 0.08))
            self.assertTrue(math.isfinite(e["logp"]))
        for value in (snap["d2"]["surprise"], snap["score"], *snap["features"].values()):
            self.assertTrue(math.isfinite(value))
        self.assertEqual(snap["independent_roots"], 3 * 10 ** 9)

    def test_too_few_exceeding_sites_or_no_eligible_site_gives_no_candidate(self) -> None:
        hq = self.hq()
        hq.background().put("s1", Y, (39, 40)).put("s2", Y, (40,))
        hq.send(40)
        self.assertEqual(detector_keys(hq.run()), [])
        hq = self.hq()
        hq.background(SITES, 25).put(["s1", "s2", "s3"], Y, (39, 40))
        hq.send(40)
        result = hq.run()
        self.assertEqual(detector_keys(result), [])
        self.assertEqual(result["status"], "insufficient_baseline")
        self.assertEqual(result["insufficient_baseline_weeks"], len(result["weeks"]))
        self.assertEqual({w["status"] for w in result["weeks"]}, {"insufficient_baseline"})

    def test_a_late_site_is_excluded_and_listed(self) -> None:
        hq = self.hq()
        hq.background().put(["s1", "s2", "s3"], Y, (39, 40)).put("s6", Y, (34, 35))
        hq.send(35, ["s6"]).send(40, ["s1", "s2", "s3", "s4", "s5"])
        result = hq.run()
        snap = cand(result, Y)["snapshot"]
        late = entry(snap["d2"], "s6")
        self.assertEqual((late["status"], late["c"]), ("late", None))
        self.assertEqual(snap["d2"]["n"], 5)
        self.assertNotIn("s6", snap["contributing_sites"])
        self.assertNotIn("s6", {item["site"] for item in snap["lineage"]})
        self.assertEqual(result["weeks"][40]["late_sites"], ["s6"])
        self.assertEqual(result["weeks"][35]["late_sites"], [])
        self.assertEqual((result["weeks"][40]["reported_sites"], result["weeks"][40]["eligible_sites"]), (5, 5))

    def test_windows_across_the_year_end_and_week_53(self) -> None:
        weeks = weeks_from("2026-W20", 41)
        self.assertEqual(weeks[32:35], ["2026-W52", "2026-W53", "2027-W01"])
        hq = self.hq(weeks=weeks)
        y2 = ("product", "CM-5", "occlusion")
        hq.background().put(["s1", "s2", "s3"], Y, (33, 34)).put(["s1", "s2", "s3"], y2, (32,))
        hq.put(["s1", "s2", "s3"], y2, (34,), n=3)
        hq.send(34)
        result = hq.run()
        self.assertEqual([w["week"] for w in result["weeks"]], weeks[:35])
        for s, c in ((Y, 2), (y2, 4)):
            snap = cand(result, s)["snapshot"]
            self.assertEqual((snap["week"], snap["window"]), ("2027-W01", [weeks[27], "2027-W01"]))
            self.assertEqual(entry(snap["d2"], "s1")["c"], c)

    def test_insufficient_baseline_still_runs_rules_and_no_cells_is_empty(self) -> None:
        hq = self.hq()
        crack = ("product", "SD-9", "crack")
        hq.background().put(["s1", "s2"], crack, (10, 12))
        hq.send(15)
        result = hq.run()
        self.assertEqual((result["status"], result["insufficient_baseline_weeks"], len(result["weeks"])),
                         ("insufficient_baseline", 16, 16))
        self.assertEqual(detector_keys(result), [])
        self.assertEqual([(h["rule_id"], h["first_week"]) for h in result["rule_hits"]],
                         [("multi_site_product_crack", W[12])])
        self.assertEqual(cand(result, crack)["channels"], ["rule"])
        empty = self.hq()
        empty.send(15)
        result = empty.run()
        self.assertEqual((result["status"], result["weeks"], result["first_week"], result["candidates"],
                          result["bundles"], result["cells"]), ("empty", [], None, [], 6, 0))


Z = ("product", "CM-5", "occlusion")
MASS = ("product", "SD-9", "leak")


class D3Tests(HqCase):
    def world(self, *, z_sites: Sequence[str] = ("s1", "s2"), mass_sites: Sequence[str] = ("s1", "s2"),
              extra: Any = None) -> dict[str, Any]:
        hq = self.hq()
        hq.background().put(list(z_sites), Z, range(0, 41), n=5).put(list(mass_sites), MASS, (40,), n=200)
        if extra is not None:
            extra(hq)
        hq.send(40)
        return hq.run()

    def test_two_rising_sites_fire_without_a_burst(self) -> None:
        result = self.world()
        c = cand(result, Z)
        self.assertEqual((c["detectors"], c["first_candidate_week"]), (["d3"], W[40]))
        snap = c["snapshot"]
        self.assertEqual((snap["d2"]["m"], snap["d2"]["surprise"], snap["d3"]["rising"]), (0, 0.0, 2))
        rises = []
        for s in ("s1", "s2"):
            e = entry(snap["d3"], s)
            self.assertEqual(e["n_ep_lb"], 40)
            self.assertEqual(e["pmi_window"], stats.smoothed_pmi(40, 40, 40, 240, 0.5))
            self.assertEqual(e["pmi_baseline"], stats.smoothed_pmi(130, 130, 130, 130, 0.5))
            self.assertEqual(e["rise"], e["pmi_window"] - e["pmi_baseline"])
            self.assertTrue(e["rising"])
            self.assertFalse(entry(snap["d2"], s)["exceeded"])
            rises.append(e["rise"])
        self.assertEqual(snap["features"]["pmi_rise"], math.fsum(rises) / 2)
        self.assertEqual((snap["supporting_sites"], snap["decision_unit"]), (["s1", "s2"], "acme/emea"))

    def test_one_rising_site_does_not_fire(self) -> None:
        result = self.world(mass_sites=("s1",))
        self.assertIsNone(cand(result, Z))

    def test_a_second_rising_site_below_k_does_not_fire(self) -> None:
        # s2: the key once ('<k', lower bound 1) beside a large mass, so its rise is large but n_ep_lb < k
        self.assertGreater(stats.smoothed_pmi(1, 2, 2, 201, 0.5) - stats.smoothed_pmi(0, 0, 0, 0, 0.5), 1.0)
        result = self.world(z_sites=("s1",), extra=lambda hq: hq.put("s2", Z, (40,)))
        self.assertIsNone(cand(result, Z))

    def test_a_site_below_k_is_shown_not_rising_in_the_snapshot(self) -> None:
        result = self.world(mass_sites=("s1", "s2", "s3"), extra=lambda hq: hq.put("s3", Z, (40,)))
        snap = cand(result, Z)["snapshot"]
        s3 = entry(snap["d3"], "s3")
        self.assertEqual(s3["n_ep_lb"], 1)
        self.assertGreater(s3["rise"], 1.0)
        self.assertFalse(s3["rising"])
        self.assertEqual((snap["d3"]["rising"], snap["supporting_sites"]), (2, ["s1", "s2"]))

    def test_marginals_are_derived_from_cells(self) -> None:
        result = self.world(extra=lambda hq: hq.put("s1", ("product", "CM-5", "leak"), (38,), n=7))
        snap = cand(result, Z)["snapshot"]
        self.assertEqual(entry(snap["d3"], "s1")["pmi_window"], stats.smoothed_pmi(40, 47, 40, 247, 0.5))
        self.assertEqual(entry(snap["d3"], "s2")["pmi_window"], stats.smoothed_pmi(40, 40, 40, 240, 0.5))

    def test_a_marginal_field_is_refused_at_ingest(self) -> None:
        hq = self.hq()
        body = bundle(DQ, "s1", [cell(*Z, W[3], 5)], after=None, through=W[9])
        body["cells"][0]["n_e"] = 40
        self.assertEqual(hq.store.ingest_bundle(body), "invalid")
        top = bundle(DQ, "s1", [cell(*Z, W[3], 5)], after=None, through=W[9])
        top["n_p"] = 40
        self.assertEqual(hq.store.ingest_bundle(top), "invalid")
        self.assertEqual([(r.path, r.keyword) for r in hq.store.rejections()],
                         [("$.cells[0]", "additionalProperties"), ("$", "additionalProperties")])
        self.assertEqual(table_count(hq.path, "cells"), 0)

    def suppressed_world(self, other_weeks: Iterable[int]) -> dict[str, Any]:
        """Every cell '<k': the key every week 0..33 at s1 and s2, and twenty other product series (other ids, other
        predicates) at ``other_weeks``."""
        others = [("product", eid, p) for eid in ("SD-9", "IP-7", "IP-21", "IP-300", "SD-12", "SD-40")
                  for p in ("overheat", "leak", "power_loss", "alarm_failure")][:20]
        hq = self.hq()
        hq.background().put(["s1", "s2"], Z, range(0, 34))
        for o in others:
            hq.put(["s1", "s2"], o, other_weeks)
        hq.send(33)
        return hq.run()

    def test_d3_fires_on_all_suppressed_cells_where_opposite_marginal_bounds_could_not(self) -> None:
        # the key is steady (no burst) and twenty other series of the type appear in week 33, so the key's share of
        # its type falls and its PMI rises; every cell is '<k'
        k = DQ.egress.k
        result = self.suppressed_world((33,))
        c = cand(result, Z)
        self.assertEqual((c["detectors"], c["first_candidate_week"]), (["d3"], W[33]))
        snap = c["snapshot"]
        for s in ("s1", "s2"):
            e = entry(snap["d3"], s)
            self.assertEqual(e["n_ep_lb"], 8)
            # window: the key's 8 cells at their lower bound, the twenty other cells at k/2
            self.assertEqual(e["pmi_window"], stats.smoothed_pmi(8, 8, 8, 8 + 20 * k / 2, 0.5))
            # baseline: weeks 0..25, the key's 26 cells at their upper bound, nothing else of the type
            own = 26 * (k - 1)
            self.assertEqual(e["pmi_baseline"], stats.smoothed_pmi(own, own, own, own, 0.5))
            self.assertTrue(e["rising"])
            self.assertFalse(entry(snap["d2"], s)["exceeded"])
            # the earlier rule (marginals at the opposite bounds) gave a negative rise on these same cells
            earlier = (stats.smoothed_pmi(8, 8 * (k - 1), 8 * (k - 1), 8 + 20, 0.5)
                       - stats.smoothed_pmi(own, 26, 26, own, 0.5))
            self.assertLess(earlier, 0.0)
        self.assertGreater(snap["features"]["pmi_rise"], 1.0)

    def test_a_steady_suppressed_key_beside_steady_suppressed_cells_does_not_rise(self) -> None:
        # the same twenty other series every week: the key's own count still takes its lower bound in the window and
        # its upper bound in the baseline, so a steady key never rises
        k = DQ.egress.k
        result = self.suppressed_world(range(0, 34))
        self.assertIsNone(cand(result, Z))
        own = 26 * (k - 1)
        rise = (stats.smoothed_pmi(8, 8, 8, 8 + 20 * 8 * k / 2, 0.5)
                - stats.smoothed_pmi(own, own, own, own + 20 * 26 * k / 2, 0.5))
        self.assertLess(rise, DQ.detectors["cooccurrence"]["pmi_delta"])

    def test_d2_and_d3_on_one_key_give_one_candidate(self) -> None:
        hq = self.hq()
        hq.background().put(["s1", "s2"], Z, (40,), n=5).put(["s1", "s2"], MASS, (40,), n=200)
        hq.send(40)
        result = hq.run()
        self.assertEqual([c["key"] for c in result["candidates"]].count(key(Z)), 1)
        c = cand(result, Z)
        self.assertEqual(c["detectors"], ["d2", "d3"])
        self.assertEqual((c["snapshot"]["d2"]["m"], c["snapshot"]["d3"]["rising"]), (2, 2))


E_ECHO = ("product", "IP-21", "power_loss")
E_PLAIN = ("product", "IP-300", "alarm_failure")
FEW = ("product", "SD-12", "occlusion")
LOW = ("product", "IP-7", "overheat")
HIGH = (("product", "IP-7", "leak"), ("product", "CM-5", "leak"), ("product", "SD-9", "leak"))


class D5D6Tests(HqCase):
    def test_echo_from_int_roots_and_not_from_suppressed_roots(self) -> None:
        hq = self.hq()
        hq.background().put(["s1", "s2"], E_ECHO, (40,), n=10, roots=3).put(["s1", "s2"], E_PLAIN, (39, 40))
        hq.send(40)
        result = hq.run()
        echo = cand(result, E_ECHO)["snapshot"]
        self.assertEqual((echo["root_ratio_ub"], echo["flags"]["echo"], echo["features"]["echo"]), (0.3, True, 1.0))
        self.assertEqual(echo["independent_roots"], 6)
        plain = cand(result, E_PLAIN)["snapshot"]
        self.assertEqual((plain["root_ratio_ub"], plain["flags"]["echo"], plain["features"]["echo"]),
                         (1.0, False, 0.0))
        self.assertEqual(plain["independent_roots"], 4)

    def test_suppressed_roots_count_their_upper_bound(self) -> None:
        # '<k' roots of an int n count k-1: n=4 gives 2/4 = 0.5 (not below 0.5, no echo); n=10 gives 0.2 (certain echo)
        small, large = ("product", "CM-5", "power_loss"), ("product", "SD-9", "alarm_failure")
        hq = self.hq()
        hq.background().put(["s1", "s2"], small, (40,), n=4, roots=SUPPRESSED)
        hq.put(["s1", "s2"], large, (40,), n=10, roots=SUPPRESSED)
        hq.send(40)
        result = hq.run()
        snap = cand(result, small)["snapshot"]
        self.assertEqual((snap["root_ratio_ub"], snap["flags"]["echo"], snap["independent_roots"]), (0.5, False, 2))
        snap = cand(result, large)["snapshot"]
        self.assertEqual((snap["root_ratio_ub"], snap["flags"]["echo"]), (0.2, True))

    def test_few_reporters_per_site_and_its_share(self) -> None:
        hq = self.hq()
        hq.background().put("s1", FEW, (40,), n=10, reporters=SUPPRESSED).put("s2", FEW, (40,), n=10)
        hq.put("s3", FEW, (40,))
        hq.send(40)
        snap = cand(hq.run(), FEW)["snapshot"]
        self.assertEqual(snap["contributing_sites"], ["s1", "s2", "s3"])
        self.assertEqual(snap["flags"]["few_reporters_sites"], ["s1"])
        self.assertEqual(snap["features"]["few_reporters_share"], 1 / 3)

    def test_stale_is_judged_against_the_step_as_of(self) -> None:
        g = ("product", "SD-40", "overheat")
        hq = self.hq()
        hq.background().put(["s1", "s2"], g, (30, 31))
        hq.send(34).send(40)
        result = hq.run()
        self.assertEqual(cand(result, g)["candidate_weeks"], W[31:35])
        self.assertEqual([result["weeks"][i]["stale_removed"] for i in range(30, 39)], [0, 0, 0, 0, 0, 1, 1, 1, 0])
        self.assertEqual(result["weeks"][35]["candidates"], 0)
        self.assertEqual(result["weeks"][31]["as_of"], (week_sunday(W[31]) + timedelta(days=20)).isoformat())
        self.assertEqual(result["weeks"][40]["as_of"], result["as_of"])
        earlier = hq.run(as_of=closing(DQ, W[34]))
        self.assertEqual(earlier["last_week"], W[34])
        self.assertEqual(cand(earlier, g)["candidate_weeks"], W[31:35])
        self.assertEqual(earlier["weeks"][-1]["stale_removed"], 0)

    def test_a_co_mentioned_key_of_another_type_does_not_flag_a_genuine_burst(self) -> None:
        # the same records name a lot and its product: (lot, leak) and (product, leak) burst together at 4 of 6 sites;
        # only series of the key's own entity type count toward its high base rate
        lot, product = ("lot", "L20046", "leak"), ("product", "IP-21", "leak")
        hq = self.hq()
        hq.background()
        for s in (lot, product):
            hq.put(["s1", "s2", "s3", "s4"], s, range(33, 41), n=5)
        hq.send(40)
        result = hq.run()
        for s in (lot, product):
            snap = cand(result, s)["snapshot"]
            self.assertGreaterEqual(snap["d2"]["m"], 4)
            self.assertEqual((snap["flags"]["high_base_rate"], snap["features"]["high_base_rate"]), (False, 0.0))

    def test_high_base_rate_is_type_wide_and_never_flags_itself(self) -> None:
        single3, single4 = ("product", "IP-21", "occlusion"), ("product", "IP-300", "overheat")
        hq = self.hq()
        hq.background()
        for s in HIGH:
            hq.put(["s1", "s2", "s3", "s4"], s, (39, 40))
        hq.put(["s1", "s2", "s3"], single3, (39, 40)).put(["s1", "s2", "s3", "s4"], single4, (39, 40))
        hq.send(40)
        result = hq.run()
        for s in HIGH:
            snap = cand(result, s)["snapshot"]
            self.assertEqual((snap["flags"]["high_base_rate"], snap["features"]["high_base_rate"]), (True, 1.0))
        for s in (single3, single4):
            snap = cand(result, s)["snapshot"]
            self.assertEqual((snap["flags"]["high_base_rate"], snap["features"]["high_base_rate"]), (False, 0.0))
            self.assertGreaterEqual(snap["d2"]["m"], 3)

    def test_res_conf_is_the_lowest_known_confidence(self) -> None:
        mid, unknown = ("product", "CM-5", "overheat"), ("product", "SD-9", "overheat")
        hq = self.hq()
        hq.background().put("s1", LOW, (40,), n=4, conf=0.9).put("s2", LOW, (40,), n=5, conf=1.0)
        hq.put("s1", mid, (40,), n=4, conf=0.95).put("s2", mid, (40,), n=4).put(["s1", "s2"], unknown, (39, 40))
        hq.send(40)
        result = hq.run()
        for s, conf, low in ((LOW, 0.9, True), (mid, 0.95, False), (unknown, None, False)):
            snap = cand(result, s)["snapshot"]
            self.assertEqual((snap["res_conf"], snap["features"]["low_res_conf"]), (conf, 1.0 if low else 0.0))


class RankerTests(HqCase):
    def penalty_world(self) -> dict[str, Any]:
        hq = self.hq()
        hq.background()
        hq.put(["s1", "s2"], E_ECHO, (40,), n=10, roots=3)
        hq.put("s1", FEW, (40,), n=10, reporters=SUPPRESSED).put("s2", FEW, (40,), n=10).put("s3", FEW, (40,))
        hq.put("s1", LOW, (40,), n=4, conf=0.9).put("s2", LOW, (40,), n=5)
        for s in HIGH:
            hq.put(["s1", "s2", "s3", "s4"], s, (39, 40))
        hq.send(40)
        return hq.run()

    def test_score_is_the_logistic_of_the_weighted_features(self) -> None:
        result = self.penalty_world()
        cfg = DetectorConfig.from_pack(DQ)
        snaps = [c["snapshot"] for c in result["candidates"] if c["snapshot"] is not None]
        self.assertGreaterEqual(len(snaps), 6)
        for snap in snaps:
            f = snap["features"]
            self.assertEqual(sorted(f), sorted(FEATURES))
            expected = stats.logistic(-4.0 + math.fsum(cfg.weights[name] * f[name] for name in FEATURES))
            self.assertAlmostEqual(snap["score"], expected, delta=1e-15)
            self.assertEqual(f["log_independent_roots"], math.log1p(snap["independent_roots"]))
            self.assertEqual(f["supporting_sites"], float(len(snap["supporting_sites"])))

    def test_each_penalty_lowers_the_score(self) -> None:
        result = self.penalty_world()
        cfg = DetectorConfig.from_pack(DQ)
        for s, feature in ((E_ECHO, "echo"), (FEW, "few_reporters_share"), (LOW, "low_res_conf"),
                           (HIGH[0], "high_base_rate")):
            with self.subTest(feature=feature):
                snap = cand(result, s)["snapshot"]
                self.assertGreater(snap["features"][feature], 0.0)
                without = {**snap["features"], feature: 0.0}
                clean = stats.logistic(cfg.bias + math.fsum(cfg.weights[f] * without[f] for f in FEATURES))
                self.assertLess(snap["score"], clean)
                self.assertLess(cfg.weights[feature], 0.0)

    def test_features_order_and_closed_weights(self) -> None:
        self.assertEqual(FEATURES, ("burst_surprise", "pmi_rise", "log_independent_roots", "supporting_sites",
                                    "low_res_conf", "echo", "few_reporters_share", "high_base_rate"))
        for pack in PACKS.values():
            self.assertEqual(tuple(DetectorConfig.from_pack(pack).weights), FEATURES)
            self.assertEqual(sorted(pack.detectors["ranker"]["weights"]), sorted(FEATURES))


TEXT_KEY = ("product", "CM-5", "leak")


class ChannelTests(HqCase):
    def world(self) -> Hq:
        hq = self.hq()
        hq.background(["s1", "s2", "s3", "s5", "s6"], 0)
        hq.background(["s4"], 0, channel="text_only").background(["s4"], 25)
        hq.put(["s1", "s2", "s3"], LOW, (39, 40)).put(["s1", "s2", "s3"], LOW, (40,), channel="text_only")
        hq.put("s1", LOW, (20,)).put("s1", LOW, (20,), channel="text_only")
        hq.put(["s1", "s2", "s3"], TEXT_KEY, (39, 40), channel="text_only")
        hq.send(40)
        return hq

    def test_x_combines_codes_and_text_only_bounds(self) -> None:
        hq = self.world()
        x, s = hq.run("X"), hq.run("S")
        # s2: no history; the combined count (3) beats the codes count (2) against the same floor
        e2 = entry(cand(x, LOW)["snapshot"]["d2"], "s2")
        self.assertEqual((e2["test"], e2["c"], e2["baseline_rate"]), ("combined", 3, 0.01))
        self.assertEqual(e2["logp"], stats.poisson_logsf(3, 0.08))
        # s1: the combined test (3 against (2 + 2) / 26) and S's codes test (2 against 2 / 26, at least the certain
        # 2 / 26 of both channels); the snapshot shows the lower logp, here the codes test
        ex, es = entry(cand(x, LOW)["snapshot"]["d2"], "s1"), entry(cand(s, LOW)["snapshot"]["d2"], "s1")
        combined = stats.poisson_logsf(3, max(0.01, (2 + 2) / 26) * 8)
        self.assertEqual((ex["test"], ex["c"], ex["baseline_rate"]), ("codes", 2, max(0.01, 2 / 26)))
        self.assertEqual(ex["logp"], stats.poisson_logsf(2, 2 / 26 * 8))
        self.assertLess(ex["logp"], combined)
        self.assertEqual((es["test"], es["c"], es["baseline_rate"]), ("combined", 2, max(0.01, 2 / 26)))
        self.assertEqual((x["cell_channels"], s["cell_channels"]), (["codes", "text_only"], ["codes"]))

    def test_text_only_background_does_not_hide_a_codes_burst_that_s_sees(self) -> None:
        # s1 and s2: the key's codes cells '<k' in weeks 10 and 17, text-only cells '<k' in weeks 21 and 24, then a
        # codes burst ('<k' each week) from week 32
        y2 = ("product", "IP-21", "power_loss")
        hq = self.hq()
        hq.background()
        hq.put(["s1", "s2"], y2, (10, 17)).put(["s1", "s2"], y2, (21, 24), channel="text_only")
        hq.put(["s1", "s2"], y2, range(32, 39))
        hq.send(38)
        x, s = cand(hq.run("X"), y2), cand(hq.run("S"), y2)
        # S: 5 codes weeks against 2 * 2 / 26 at alpha; X's codes test runs at alpha / 2 and needs a sixth week
        self.assertEqual((s["first_candidate_week"], x["first_candidate_week"]), (W[36], W[37]))
        snap = x["snapshot"]
        for site in ("s1", "s2"):
            e = entry(snap["d2"], site)
            self.assertEqual((e["test"], e["c"], e["baseline_rate"], e["exceeded"]), ("codes", 6, 4 / 26, True))
            self.assertLess(e["logp"], math.log(0.01 / 2))
        # the combined test alone (6 against (2 + 2) * 2 / 26) does not exceed at alpha, at any week through 38
        self.assertGreater(stats.poisson_logsf(7, 8 / 26 * 8), math.log(0.01))

    def test_a_text_only_burst_is_in_x_and_absent_in_s(self) -> None:
        hq = self.world()
        self.assertIn("detector", cand(hq.run("X"), TEXT_KEY)["channels"])
        self.assertIsNone(cand(hq.run("S"), TEXT_KEY))

    def test_s_lineage_holds_only_codes_and_s_history_ignores_text(self) -> None:
        hq = self.world()
        x, s = hq.run("X"), hq.run("S")
        for c in s["candidates"]:
            for part in (c["snapshot"], c["rule"]):
                for item in (part or {}).get("lineage", []):
                    self.assertEqual(item["channel"], "codes")
        self.assertIn("text_only", {i["channel"] for i in cand(x, LOW)["snapshot"]["lineage"]})
        ex, es = entry(cand(x, LOW)["snapshot"]["d2"], "s4"), entry(cand(s, LOW)["snapshot"]["d2"], "s4")
        self.assertEqual((ex["status"], ex["history_weeks"]), ("eligible", 33))
        self.assertEqual((es["status"], es["history_weeks"]), ("short_history", 8))
        self.assertEqual(s["ignored_cells"], dict.fromkeys(IGNORED_KEYS, 0))
        bundles, cells = hq.store.detection_inputs(hq.as_of(), "X")
        direct = run_detection(DQ, ORG6, bundles=bundles, cells=cells, as_of=hq.as_of(), run_channel="S",
                               tie_salt="t")
        text_cells = sum(1 for c in cells if c.channel == "text_only")
        self.assertEqual(direct["ignored_cells"], {**dict.fromkeys(IGNORED_KEYS, 0), "other_channel": text_cells})
        self.assertEqual(result_bytes({**direct, "ignored_cells": s["ignored_cells"]}), result_bytes(s))


# =================================================================================================== alerts

K = ("product", "IP-7", "overheat")
L = ("product", "CM-5", "leak")


class AlertWalkTests(HqCase):
    def packed(self, **edits: Any) -> FrozenPack:
        return pack_copy(self.tmp, DQ.id, {("detectors.json", name): value for name, value in edits.items()})

    def cooldown_world(self, pack: FrozenPack, *, with_l: bool = True) -> dict[str, Any]:
        hq = self.hq(pack)
        hq.background().put(["s1", "s2", "s3"], K, (30, 36), n=10).put(["s1", "s2", "s3"], K, (47,), n=40)
        if with_l:
            hq.put(["s1", "s2"], L, (36,), n=10)
        hq.send(47)
        return hq.run()

    def test_cooldown_passes_the_budget_on_and_releases_after_quiet_steps(self) -> None:
        result = self.cooldown_world(self.packed(alert_budget_per_week=1))
        k, l = cand(result, K), cand(result, L)
        self.assertEqual(k["candidate_weeks"], W[30:34] + W[36:40] + [W[47]])
        self.assertEqual((k["alert_weeks"], k["detection_week"]), ([W[30], W[47]], W[30]))
        self.assertEqual(k["snapshot"]["week"], W[30])
        self.assertEqual((l["alert_weeks"], l["detection_week"]), ([W[36]], W[36]))
        self.assertEqual((result["weeks"][36]["alerts"], result["weeks"][36]["cooling"]), ([key(L)], 1))
        self.assertEqual([w["alerts"] for w in result["weeks"][31:34]], [[], [], []])
        self.assertEqual([w["cooling"] for w in result["weeks"][31:34]], [1, 1, 1])
        self.assertEqual([a["week"] for a in result["alerts"]], [W[30], W[36], W[47]])
        self.assertEqual({a["rank"] for a in result["alerts"]}, {1})

    def test_cooldown_zero_allows_an_alert_every_week(self) -> None:
        result = self.cooldown_world(self.packed(alert_budget_per_week=1, cooldown_weeks=0), with_l=False)
        k = cand(result, K)
        self.assertEqual(k["candidate_weeks"], W[30:34] + W[36:40] + [W[47]])
        self.assertEqual(k["alert_weeks"], k["candidate_weeks"])
        self.assertEqual(sum(w["cooling"] for w in result["weeks"]), 0)

    def test_budget_zero_gives_no_alerts(self) -> None:
        result = self.cooldown_world(self.packed(alert_budget_per_week=0))
        self.assertEqual(result["alerts"], [])
        self.assertTrue(result["candidates"])
        for w in result["weeks"]:
            self.assertEqual(w["alerts"], [])
        for c in result["candidates"]:
            self.assertEqual((c["alert_weeks"], c["detection_week"]), ([], None))
            self.assertEqual(c["snapshot"]["week"], c["first_candidate_week"])

    def test_a_budget_above_the_candidate_count_alerts_every_candidate(self) -> None:
        p1, p2, p3 = ("product", "IP-7", "overheat"), ("product", "CM-5", "leak"), ("product", "SD-9", "occlusion")
        hq = self.hq()
        hq.background().put(["s1", "s2", "s3", "s4"], p1, (40,), n=10).put(["s1", "s2", "s3"], p2, (40,), n=10)
        hq.put(["s1", "s2"], p3, (39, 40))
        hq.send(40)
        result = hq.run()
        week = result["weeks"][40]
        self.assertEqual((week["candidates"], len(week["alerts"])), (3, 3))
        alerts = [a for a in result["alerts"] if a["week"] == W[40]]
        self.assertEqual([a["rank"] for a in alerts], [1, 2, 3])
        self.assertEqual([a["key"] for a in alerts], week["alerts"])
        scores = [a["score"] for a in alerts]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(scores, [cand(result, s)["snapshot"]["score"] for s in (p1, p2, p3)])

    def test_ties_resolve_by_the_salted_sha256_never_by_the_key(self) -> None:
        pack = self.packed(alert_budget_per_week=1)
        t1, t2 = ("product", "IP-7", "overheat"), ("product", "CM-5", "overheat")
        hq = self.hq(pack)
        hq.background().put(["s1", "s2", "s3"], t1, (39, 40)).put(["s1", "s2", "s3"], t2, (39, 40))
        hq.send(40)
        bundles, cells = hq.store.detection_inputs(hq.as_of(), "X")
        winners = {}
        for i in range(64):
            salt = f"salt-{i}"
            result = run_detection(pack, ORG6, bundles=bundles, cells=cells, as_of=hq.as_of(), run_channel="X",
                                   tie_salt=salt)
            a, b = cand(result, t1)["snapshot"], cand(result, t2)["snapshot"]
            self.assertEqual((a["score"], a["week"]), (b["score"], b["week"]))
            self.assertEqual(len(result["weeks"][40]["alerts"]), 1)
            winners[salt] = result["weeks"][40]["alerts"][0]
            self.assertEqual(winners[salt], min((key(t1), key(t2)), key=lambda k: sha256_hex(f"{salt}|{k}")))
        self.assertEqual(set(winners.values()), {key(t1), key(t2)})
        lexical_first = min(key(t1), key(t2))
        self.assertTrue(any(w != lexical_first for w in winners.values()))


# =================================================================================================== look-ahead

class NoLookAheadTests(HqCase):
    def world(self) -> Hq:
        hq = self.hq()
        hq.background().put(["s1", "s2", "s3"], K, (38, 39)).put(["s1", "s2"], ("product", "SD-9", "crack"), (37, 40))
        hq.put("s6", K, (38, 39))
        hq.send(35, ["s6"]).send(40, ["s1", "s2", "s3", "s4", "s5"])
        return hq

    def test_later_bundles_leave_the_result_byte_identical(self) -> None:
        hq = self.world()
        as_of = hq.as_of(40)
        before = hq.run(as_of=as_of)
        self.assertTrue(before["alerts"] and before["rule_hits"])
        hq.send(40, ["s6"], as_of=closing(DQ, W[44]))           # s6's late bundle arrives after the run's as_of
        hq.put(SITES, K, (41, 42, 43), n=50).put(["s4", "s5"], L, (41, 42))
        hq.send(44)
        after = hq.run(as_of=as_of)
        self.assertEqual(result_bytes(after), result_bytes(before))
        self.assertEqual(after["run_id"], before["run_id"])
        bundles, cells = hq.store.detection_inputs(as_of, "X")
        self.assertTrue(cells)
        self.assertTrue(all(b.as_of <= as_of for b in bundles) and all(c.as_of <= as_of for c in cells))
        self.assertTrue(all(c.iso_week <= W[40] for c in cells))
        self.assertNotIn(("s6", W[38]), {(c.site, c.iso_week) for c in cells})
        later = hq.run()
        self.assertNotEqual(later["run_id"], before["run_id"])
        self.assertIn("s6", cand(later, K)["snapshot"]["contributing_sites"])

    def test_an_injected_cell_after_the_last_week_is_ignored(self) -> None:
        hq = self.world()
        as_of = hq.as_of(40)
        before = hq.run(as_of=as_of)
        sha = next(b.sha256 for b in hq.store.bundles() if b.site == "s1")
        conn = sqlite3.connect(str(hq.path))
        try:
            conn.execute("INSERT INTO cells (site, entity_type, entity_id, predicate, iso_week, channel, n, n_roots, "
                         "n_reporters, res_conf_min, as_of, bundle) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         ("s1", *K, W[41], "codes", 50, 50, 50, 1.0, as_of, sha))
            conn.execute("INSERT INTO cells (site, entity_type, entity_id, predicate, iso_week, channel, n, n_roots, "
                         "n_reporters, res_conf_min, as_of, bundle) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         ("s2", *L, W[40], "codes", 50, 50, 50, 1.0, closing(DQ, W[44]), sha))
            conn.commit()
        finally:
            conn.close()
        after = hq.run(as_of=as_of)
        self.assertEqual(result_bytes(after), result_bytes(before))
        _, cells = hq.store.detection_inputs(as_of, "X")
        self.assertNotIn(W[41], {c.iso_week for c in cells})
        self.assertNotIn(L[1], {c.entity_id for c in cells})

    def test_step_w_reads_no_later_week(self) -> None:
        results = []
        for variant in (0, 1):
            hq = self.hq()
            hq.background().put(["s1", "s2", "s3"], K, (30, 31)).put(["s1", "s2"], ("product", "SD-9", "crack"),
                                                                      (29, 33))
            hq.send(34)
            if variant:
                hq.put(["s1", "s2", "s3", "s4"], K, (36, 37), n=20).put(["s3", "s4", "s5"], L, (39, 40))
                hq.put(["s1", "s2"], ("product", "SD-9", "crack"), (35, 36))
            hq.send(44)
            results.append(hq.run())
        a, b = results
        self.assertNotEqual(result_bytes(a), result_bytes(b))
        self.assertEqual(a["weeks"][:35], b["weeks"][:35])
        early = [x for x in a["alerts"] if x["week"] <= W[34]]
        self.assertTrue(early)
        self.assertEqual(early, [x for x in b["alerts"] if x["week"] <= W[34]])
        for c in a["candidates"]:
            if c["detection_week"] is not None and c["detection_week"] <= W[34]:
                self.assertEqual(c["snapshot"], next(x for x in b["candidates"] if x["key"] == c["key"])["snapshot"])
        for h in a["rule_hits"]:
            other = next(x for x in b["rule_hits"] if (x["rule_id"], x["key"]) == (h["rule_id"], h["key"]))
            self.assertEqual([w for w in h["weeks"] if w <= W[34]], [w for w in other["weeks"] if w <= W[34]])


# =================================================================================================== determinism

def seeded_result(root: Path) -> dict[str, Any]:
    """A seeded synthetic HQ (random background, two identical planted keys for a score tie, a stronger burst and a
    rule hit), detected in run X. Used in-process and by the PYTHONHASHSEED subprocesses."""
    rng = random.Random("g4-determinism-v1")
    hq = Hq(Path(root))
    try:
        hq.background()
        v = VOCAB["device_quality"]
        for site in SITES:
            for _ in range(25):
                s = (v["type"], rng.choice(v["ids"]), rng.choice(v["preds"]))
                weeks = [i for i in range(1, 44) if rng.random() < 0.15]
                hq.put(site, s, weeks, n=rng.choice((SUPPRESSED, SUPPRESSED, SUPPRESSED, 3, 4, 7)))
        for s in (("lot", "L10001", "overheat"), ("lot", "L10002", "overheat")):
            hq.put(["s1", "s2", "s3"], s, (41, 42))
        hq.put(["s1", "s2", "s4", "s5"], ("supplier", "V1001", "power_loss"), (40, 41, 42), n=6)
        hq.put(["s1", "s2"], ("product", "SD-9", "crack"), (40, 42))
        hq.send(43)
        return hq.run(salt="determinism")
    finally:
        hq.close()


def has_tie(result: dict[str, Any]) -> bool:
    seen = [(a["week"], a["score"]) for a in result["alerts"]]
    return len(seen) != len(set(seen))


class DeterminismTests(HqCase):
    def test_two_hash_seeds_give_identical_result_bytes(self) -> None:
        code = ("import hashlib, json, sys\nfrom pathlib import Path\n"
                "from tests.mycelic.test_collective_detect import has_tie, result_bytes, seeded_result\n"
                "r = seeded_result(Path(sys.argv[1]))\n"
                "print(json.dumps({'sha': hashlib.sha256(result_bytes(r)).hexdigest(), "
                "'candidates': len(r['candidates']), 'alerts': len(r['alerts']), 'tie': has_tie(r)}))\n")
        outs = []
        for seed in ("0", "4242"):
            work = Path(tempfile.mkdtemp(dir=self.tmp))
            r = subprocess.run([sys.executable, "-c", code, str(work)], cwd=ROOT, capture_output=True, text=True,
                               timeout=300, env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT)})
            self.assertEqual(r.returncode, 0, r.stderr)
            outs.append(json.loads(r.stdout.strip().splitlines()[-1]))
        self.assertEqual(outs[0], outs[1])
        self.assertGreaterEqual(outs[0]["candidates"], 1)
        self.assertGreaterEqual(outs[0]["alerts"], 1)
        self.assertTrue(outs[0]["tie"])
        local = seeded_result(Path(tempfile.mkdtemp(dir=self.tmp)))
        self.assertEqual(sha256_hex(result_bytes(local)), outs[0]["sha"])

    def test_save_run_is_idempotent_and_a_changed_result_conflicts(self) -> None:
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        result = seeded_result(root)
        st = CollectiveStore(root / "collective.sqlite3", DQ, ORG6, clock=Clock(NOW))
        self.addCleanup(st.close)
        st.save_run(result)
        st.save_run(json.loads(result_bytes(result)))
        self.assertEqual((table_count(st.path, "detection_runs"), table_count(st.path, "candidates"),
                          table_count(st.path, "rule_hits")), (1, len(result["candidates"]), len(result["rule_hits"])))
        self.assertTrue(result["rule_hits"])
        conn = sqlite3.connect(str(st.path))
        try:
            row = conn.execute("SELECT run_id, run_channel, as_of, last_week, tie_salt, config_hash, detector_hash, "
                               "org_hash, result_sha256, saved_at FROM detection_runs ORDER BY run_id").fetchone()
            bodies = conn.execute("SELECT key, body FROM candidates ORDER BY key").fetchall()
        finally:
            conn.close()
        self.assertEqual(row, (result["run_id"], "X", result["as_of"], result["last_week"], "determinism",
                               DQ.config_hash, DQ.detector_hash, ORG6.org_hash, sha256_hex(result_bytes(result)), NOW))
        self.assertRegex(result["run_id"], r"^det-[0-9a-f]{16}$")
        self.assertEqual([(k, bytes(b)) for k, b in bodies],
                         [(c["key"], canonical_bytes(c)) for c in result["candidates"]])
        changed = json.loads(result_bytes(result))
        changed["weeks"][0]["candidates"] += 1
        with self.assertRaises(StoreError) as cm:
            st.save_run(changed)
        self.assertEqual(str(cm.exception), "run conflict")
        self.assertEqual(table_count(st.path, "detection_runs"), 1)

    def test_argument_errors(self) -> None:
        hq = self.hq()
        hq.background().send(20)
        for kwargs in ({"as_of": "2026-02-30"}, {"as_of": "2026-06-01T00:00:00Z"}, {"as_of": 20260601},
                       {"run_channel": "R"}, {"run_channel": "x"}, {"tie_salt": ""}, {"tie_salt": "x" * 129},
                       {"tie_salt": "café"}, {"tie_salt": "a\nb"}, {"tie_salt": 7}):
            args = {"as_of": hq.as_of(), "run_channel": "X", "tie_salt": "t", **kwargs}
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(DetectionError):
                    detect(hq.store, **args)
                with self.assertRaises(DetectionError):
                    run_detection(DQ, ORG6, bundles=(), cells=(), **args)
        self.assertEqual(detect(hq.store, as_of=hq.as_of(), run_channel="S", tie_salt="x" * 128)["status"], "ok")


# =================================================================================================== rules

CRACK = ("product", "SD-9", "crack")
DETACH = ("supplier", "V1001", "detachment")


class RulesChannelTests(HqCase):
    def test_evaluate_rule_directly(self) -> None:
        rule = DQ.rules["multi_site_product_crack"]
        counts = {"s2": {"SD-9": 3, "SD-12": 2}, "s1": {"SD-9": 2, "IP-7": 5, "SD-12": 9}, "s3": {"IP-7": 1}}
        self.assertEqual(evaluate_rule(rule, counts, week=W[5]), [
            RuleHit("multi_site_product_crack", "product:SD-12:crack", W[5], (("s1", 9), ("s2", 2))),
            RuleHit("multi_site_product_crack", "product:SD-9:crack", W[5], (("s1", 2), ("s2", 3)))])
        self.assertEqual(evaluate_rule(rule, {"s1": {"SD-9": 1}, "s2": {"SD-9": 2}}, week=W[5]), [])
        self.assertEqual(evaluate_rule(rule, {}, week=W[5]), [])

    def test_a_hit_counts_lower_bounds_over_its_own_window(self) -> None:
        hq = self.hq()
        hq.background().put("s1", CRACK, (33, 40)).put("s2", CRACK, (32, 40)).put("s3", CRACK, (40,), n=4)
        hq.put("s4", CRACK, (40,)).put("s1", DETACH, (29,)).put("s2", DETACH, (30,))
        hq.send(40)
        result = hq.run()
        hits = {(h["rule_id"], h["key"]): h for h in result["rule_hits"]}
        self.assertEqual(sorted(hits), [("multi_site_product_crack", key(CRACK)),
                                        ("supplier_part_detachment", key(DETACH))])
        crack = hits[("multi_site_product_crack", key(CRACK))]
        self.assertEqual((crack["first_week"], crack["weeks"]), (W[40], [W[40]]))
        self.assertEqual(crack["sites_at_first"], [{"site": "s1", "count_lb": 2}, {"site": "s3", "count_lb": 4}])
        detach = hits[("supplier_part_detachment", key(DETACH))]
        self.assertEqual((detach["first_week"], detach["weeks"]), (W[30], W[30:41]))
        c = cand(result, DETACH)
        self.assertEqual((c["channels"], c["snapshot"], c["alert_weeks"], c["detection_week"], c["detectors"]),
                         (["rule"], None, [], None, []))
        rule = c["rule"]
        self.assertEqual((rule["rule_ids"], rule["first_week"], rule["window"]),
                         (["supplier_part_detachment"], W[30], [W[19], W[30]]))
        self.assertEqual(rule["sites"], [{"site": "s1", "rule_id": "supplier_part_detachment", "count_lb": 1},
                                         {"site": "s2", "rule_id": "supplier_part_detachment", "count_lb": 1}])
        self.assertEqual((rule["decision_unit"], c["decision_unit"]), ("acme/emea", "acme/emea"))
        self.assertEqual([(i["site"], i["iso_week"]) for i in rule["lineage"]], [("s1", W[29]), ("s2", W[30])])
        self.assertEqual([w["rule_hits"] for w in result["weeks"]][30], 1)

    def test_late_sites_are_excluded(self) -> None:
        hq = self.hq()
        hq.background().put(["s5", "s6"], CRACK, (35, 37))
        hq.send(38, ["s5", "s6"]).send(40, ["s1", "s2", "s3", "s4"])
        hits = hq.run()["rule_hits"]
        self.assertEqual([(h["key"], h["weeks"]) for h in hits], [(key(CRACK), [W[37], W[38]])])

    def test_a_detector_and_a_rule_on_one_key_merge(self) -> None:
        hq = self.hq()
        hq.background().put(["s1", "s2", "s3"], CRACK, (39, 40))
        hq.send(40)
        result = hq.run()
        self.assertEqual([c["key"] for c in result["candidates"]].count(key(CRACK)), 1)
        c = cand(result, CRACK)
        self.assertEqual((c["channels"], c["detectors"]), (["detector", "rule"], ["d2"]))
        self.assertIsNotNone(c["snapshot"])
        rule = c["rule"]
        self.assertEqual((rule["rule_ids"], rule["first_week"], rule["decision_unit"]),
                         (["multi_site_product_crack"], W[40], "acme"))
        self.assertEqual([s["count_lb"] for s in rule["sites"]], [2, 2, 2])
        self.assertEqual(len(rule["lineage"]), 6)
        self.assertEqual(c["decision_unit"], c["snapshot"]["decision_unit"])

    def test_a_rule_only_key_gets_no_alert_and_uses_no_budget(self) -> None:
        pack = pack_copy(self.tmp, DQ.id, {("detectors.json", "alert_budget_per_week"): 1})
        hq = self.hq(pack)
        hq.background().put(["s1", "s2"], CRACK, range(0, 41, 2)).put(["s1", "s2", "s3"], K, (39, 40))
        hq.send(40)
        result = hq.run()
        crack = cand(result, CRACK)
        self.assertEqual((crack["channels"], crack["snapshot"], crack["alert_weeks"], crack["detection_week"]),
                         (["rule"], None, [], None))
        self.assertEqual(crack["rule"]["first_week"], W[2])          # cells at weeks 0 and 2, window clipped at 0
        self.assertEqual(cand(result, K)["alert_weeks"], [W[40]])
        self.assertNotIn(key(CRACK), [a["key"] for a in result["alerts"]])

    def test_s_rules_ignore_text_only_cells(self) -> None:
        hq = self.hq()
        hq.background().put(["s1", "s2"], CRACK, (39, 40), channel="text_only")
        hq.send(40)
        self.assertEqual([h["key"] for h in hq.run("X")["rule_hits"]], [key(CRACK)])
        self.assertEqual(hq.run("S")["rule_hits"], [])


# =================================================================================================== org change

class DecisionUnitOrgChangeTests(HqCase):
    def reopen(self, hq: Hq, org: OrgConfig) -> Hq:
        hq.close()
        again = Hq(hq.root, DQ, org)
        again.last_index = hq.last_index
        self._hqs.append(again)
        return again

    def test_a_unit_path_change_is_logged_once_and_followed(self) -> None:
        hq = self.hq()
        hq.background().put(["s1", "s2"], K, (39, 40))
        hq.send(40)
        self.assertEqual(cand(hq.run(), K)["decision_unit"], "acme/emea")
        moved = parse_org(org_dict(tuple((s, "acme/amer/fr2" if s == "s2" else p) for s, p in SIX)))
        hq2 = self.reopen(hq, moved)
        changes = [(r.site_id, r.old_unit_path, r.new_unit_path) for r in hq2.store.org_log()
                   if r.old_unit_path is not None and r.new_unit_path is not None]
        self.assertEqual(changes, [("s2", "acme/emea/fr", "acme/amer/fr2")])
        result = hq2.run()
        self.assertEqual((cand(result, K)["decision_unit"], cand(result, K)["snapshot"]["decision_unit"]),
                         ("acme", "acme"))
        self.assertEqual(result["org_hash"], moved.org_hash)
        before = len(hq2.store.org_log())
        hq3 = self.reopen(hq2, moved)
        self.assertEqual(len(hq3.store.org_log()), before)

    def test_a_removed_site_is_ignored(self) -> None:
        hq = self.hq()
        hq.background().put(["s1", "s2", "s3"], K, (39, 40))
        hq.send(40)
        bundles, cells = hq.store.detection_inputs(hq.as_of(), "X")
        self.assertEqual(cand(hq.run(), K)["snapshot"]["d2"]["m"], 3)
        smaller = parse_org(org_dict(tuple(x for x in SIX if x[0] != "s3")))
        hq2 = self.reopen(hq, smaller)
        self.assertIn(("s3", "acme/amer/us", None), [(r.site_id, r.old_unit_path, r.new_unit_path)
                                                     for r in hq2.store.org_log()])
        result = hq2.run()
        snap = cand(result, K)["snapshot"]
        self.assertEqual((snap["d2"]["m"], snap["d2"]["n"]), (2, 5))
        self.assertNotIn("s3", json.dumps(result))
        direct = run_detection(DQ, smaller, bundles=bundles, cells=cells, as_of=hq.as_of(), run_channel="X",
                               tie_salt="t")
        self.assertEqual(direct["ignored_cells"]["not_in_org"], sum(1 for c in cells if c.site == "s3"))
        self.assertEqual(direct["candidates"], result["candidates"])


# =================================================================================================== end to end

class EndToEndTests(HqCase):
    def test_generated_worlds_of_every_builtin_pack(self) -> None:
        for pack in PACKS.values():
            with self.subTest(pack=pack.id):
                root = Path(tempfile.mkdtemp(dir=self.tmp))
                world = generate(pack, 4, 6, 40)
                site_ids = list(world.params["site_ids"])
                org = parse_org({"schema_version": 1, "enterprise": "acme", "sites": [
                    {"site_id": s, "unit_path": f"acme/region-{i // 2}/{s}", "country": "DE", "display_name": s}
                    for i, s in enumerate(site_ids)]})
                start = date.fromisoformat(world.params["start"])
                lag = pack.egress.close_lag_days
                clock = Clock((start + timedelta(days=7 * 40)).isoformat() + "T08:00:00Z")
                sites = []
                for sid in site_ids:
                    es = EdgeSite(pack, sid, root / "edge", runtime=None, clock=clock,
                                  master_data=world.master_data[sid], hq_dir=root / "hq")
                    self.addCleanup(es.close)
                    es.ingest([r for r in world.records if r["site"] == sid])
                    es.extract("lexical")
                    sites.append(es)
                for weeks in range(8, 41, 4):
                    as_of = (start + timedelta(days=7 * weeks - 1 + lag)).isoformat()
                    clock.value = as_of + "T08:00:00Z"
                    for es in sites:
                        self.assertIsNotNone(es.emit_cells(as_of))
                lines = (root / "hq" / "receive.jsonl").read_bytes().splitlines()
                store = CollectiveStore(root / "hq" / "collective.sqlite3", pack, org, clock=clock)
                self.addCleanup(store.close)
                report = store.ingest_log(root / "hq" / "receive.jsonl")
                self.assertEqual((report.accepted, sum(report.rejected.values())), (len(lines), 0))
                conn = sqlite3.connect(str(store.path))
                self.addCleanup(conn.close)
                for channel in ("X", "S"):
                    result = detect(store, as_of=as_of, run_channel=channel, tie_salt="e2e")
                    self.assertEqual(result["status"], "ok")
                    self.assertEqual(result["bundles"], len(lines))
                    for c in result["candidates"]:
                        for part in (c["snapshot"], c["rule"]):
                            if part is None:
                                continue
                            for item in part["lineage"]:
                                self.assertTrue(part["window"][0] <= item["iso_week"] <= part["window"][1])
                                found = conn.execute(
                                    "SELECT bundle FROM cells WHERE site = ? AND entity_type = ? AND entity_id = ? "
                                    "AND predicate = ? AND iso_week = ? AND channel = ? ORDER BY bundle",
                                    (item["site"], item["entity_type"], item["entity_id"], item["predicate"],
                                     item["iso_week"], item["channel"])).fetchall()
                                self.assertEqual(found, [(item["bundle"],)])
                                if channel == "S":
                                    self.assertEqual(item["channel"], "codes")
                    print(f"\n[synthetic same-author world, {pack.id}, seed 4, run {channel}; not a measurement] "
                          f"cells={result['cells']} candidates={len(result['candidates'])} "
                          f"detector_candidates={len(detector_keys(result))} alerts={len(result['alerts'])} "
                          f"rule_hits={len(result['rule_hits'])}")
                again = store.ingest_log(root / "hq" / "receive.jsonl")
                self.assertEqual((again.accepted, again.duplicates, sum(again.rejected.values())), (0, len(lines), 0))


# =================================================================================================== performance

class PerformanceTests(HqCase):
    def test_detection_over_six_sites_52_weeks_and_2002_series(self) -> None:
        rng = random.Random("g4-performance-v1")
        lots = [f"L{10000 + j}" for j in range(182)]
        preds = sorted(DQ.predicates)
        self.assertEqual(len(lots) * len(preds), 2002)
        store = CollectiveStore(self.tmp / "perf.sqlite3", DQ, ORG6, clock=Clock(NOW))
        self.addCleanup(store.close)
        cells = 0
        start = time.perf_counter()
        for site in SITES:
            after = None
            for week in W[:52]:
                body_cells = []
                for lot in lots:
                    for p in preds:
                        x = rng.random()
                        if x >= 0.33:
                            continue
                        channel = "codes" if x < 0.25 else "text_only"
                        if rng.random() < 0.85:
                            body_cells.append(cell("lot", lot, p, week, channel=channel))
                        else:
                            n = rng.randint(3, 12)
                            body_cells.append(cell("lot", lot, p, week, n, roots=rng.choice((n, SUPPRESSED)),
                                                   reporters=rng.choice((n, SUPPRESSED)), channel=channel,
                                                   conf=rng.choice((1.0, 0.95, 0.9))))
                self.assertEqual(store.ingest_bundle(bundle(DQ, site, body_cells, after=after, through=week)),
                                 "accepted")
                cells += len(body_cells)
                after = week
        ingest_s = time.perf_counter() - start
        start = time.perf_counter()
        result = detect(store, as_of=closing(DQ, W[51]), run_channel="X", tie_salt="perf")
        detect_s = time.perf_counter() - start
        print(f"\n[sandbox engineering timing, synthetic cells; not a product figure] cells={cells} bundles=312 "
              f"ingest_s={ingest_s:.1f} detect_x_s={detect_s:.1f} candidates={len(result['candidates'])} "
              f"alerts={len(result['alerts'])}")
        self.assertGreaterEqual(cells, 200_000)
        self.assertEqual((result["cells"], result["bundles"]), (cells, 312))
        self.assertLess(detect_s, 180.0)
