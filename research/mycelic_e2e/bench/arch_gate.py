"""Architecture-coverage gate G1-G10 (PLAN_v1 §B.7, BENCHMARK_CONTRACT §8).

A scored run is **valid** only when every gate passes (``pass`` or ``na``); ``fail``, ``missing`` and ``error`` all
invalidate it. The gate looks at the databases the run produced and never at gold. Every connection is a read-only SQLite URI
(``file:...?mode=ro``) with ``PRAGMA query_only``; nothing here writes to ``coord.db`` or a holder file. The one replay that
needs the coordinator's authorization code (G4) runs on a *copy* of ``coord.db`` made through a read-only connection and
deleted afterwards.

Inputs: ``check(run_dir, coord_db=..., holders_dir=..., expect_hypergraph=...)``.

Optional run-directory files (all non-gold, written by the runner; absent files make the dependent assertion ``na``):

``run_manifest.json``    ``provider_label`` (G6), ``n_tasks`` (G7: a run with tasks must have ``views/``), ``harness_db_opens`` (G7)
``views/<task>.json``    the asker views the scorer reads; G7 compares each claim / reference / discovery / question in them with ``coord.db``
``sources_manifest.json`` + ``sources/``  the raw files the harness wrote; G10 derives the injected faults from them (duplicate lines,
                         malformed lines, delete records and the deleted record's own text, replay / restart flags), never from the
                         system's own reports
``fault_plan.json``      optional additions: ``{"restart": [{"holder_id", "pages_before"}], "deleted_markers": [...], "faults": {...}}``
``harness_db_opens.json``  ``[{"path", "uri"}]`` every database the harness opened itself (G7 requires ``mode=ro``)

Layout assumptions: the coordinator's ``holders`` table lists every holder; holder ``H`` stores its control shard at
``<holders_dir>/H/evidence.db`` and data shards at ``<holders_dir>/H/shd_*.db``.

``expect_hypergraph=True`` (the default) means the hypergraph tables (``hyperedges``, ``hyperedge_members``) must exist and be
consistent; when they are absent G3-hypergraph, G4-ranker and G8 report ``missing`` (fail). With ``expect_hypergraph=False`` those
parts are ``na`` (pre-hypergraph or an ablation that removes it). ``expect_ranker`` defaults to ``expect_hypergraph``.
"""
from __future__ import annotations

import contextlib
import dataclasses
import glob
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path and (_REPO_ROOT / "mycelic").is_dir():
    sys.path.append(str(_REPO_ROOT))             # the gate imports pure helpers (support.compute_support) and the authorizer for the G4 replay

try:                                               # the product's own definition of a publishable entity id (kinds + shape)
    from mycelic.entities import is_publishable_entity as _ENTITY_OK
except Exception:      # noqa: BLE001 - G8 then reports ERROR instead of guessing
    _ENTITY_OK = None

PASS, FAIL, MISSING, NA, ERROR = "pass", "fail", "missing", "na", "error"
VALID_STATUSES = frozenset({PASS, NA})
LIVE_DOC = ("active", "revised")
BENCH_CONNECTOR_TYPES = ("local_export", "slack", "github")
CLAIM_CREATOR_TYPES = ("loop", "agent")
DEFAULT_MIN_ROOTS = 2
DEFAULT_ROUTE_BUDGET = 10
DEFAULT_PROVIDERS = ("fake",)
LABEL_PROVIDER = {"deterministic-provider": "fake"}
MAX_PROBLEMS = 20

_OPENED: list[dict[str, str]] = []           # every database opened through ro_connect in this process (G7 evidence)


# ---------------------------------------------------------------------------------------------- connections
def ro_connect(path: str | os.PathLike[str]) -> sqlite3.Connection:
    """The only way this module (and the harness, if it wants G7 evidence) opens a database: read-only URI."""
    p = os.path.abspath(str(path))
    uri = f"file:{p}?mode=ro"
    _OPENED.append({"path": p, "uri": uri})
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def opened_databases() -> list[dict[str, str]]:
    return list(_OPENED)


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _jl(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _scalar(conn: sqlite3.Connection, sql: str, args: Sequence[Any] = (), default: Any = 0) -> Any:
    r = conn.execute(sql, tuple(args)).fetchone()
    return default if r is None or r[0] is None else r[0]


# ---------------------------------------------------------------------------------------------- report
@dataclasses.dataclass
class GateResult:
    gate: str
    title: str
    status: str = PASS
    detail: str = ""
    problems: list[str] = dataclasses.field(default_factory=list)
    data: dict[str, Any] = dataclasses.field(default_factory=dict)

    def fail(self, msg: str, status: str = FAIL) -> None:
        if self.status in (PASS, NA):
            self.status = status
        if len(self.problems) < MAX_PROBLEMS:
            self.problems.append(msg)
        else:
            self.data["problems_truncated"] = self.data.get("problems_truncated", 0) + 1


@dataclasses.dataclass
class GateReport:
    results: dict[str, GateResult]
    counts: dict[str, Any]
    expect_hypergraph: bool
    expect_ranker: bool
    notes: list[str] = dataclasses.field(default_factory=list)

    @property
    def failed_ids(self) -> list[str]:
        return [g for g, r in self.results.items() if r.status not in VALID_STATUSES]

    @property
    def valid(self) -> bool:
        return not self.failed_ids

    def to_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "failed": self.failed_ids, "expect_hypergraph": self.expect_hypergraph, "expect_ranker": self.expect_ranker,
                "gates": {g: dataclasses.asdict(r) for g, r in self.results.items()}, "counts": self.counts, "notes": self.notes}

    def summary(self) -> str:
        lines = [f"architecture gate: {'VALID' if self.valid else 'INVALID (' + ', '.join(self.failed_ids) + ')'}"]
        for g, r in self.results.items():
            lines.append(f"  {g:<4}{r.status:<8}{r.title}" + (f" - {r.detail}" if r.detail else ""))
            for p in r.problems[:3]:
                lines.append(f"        . {p}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------------------------- context
@dataclasses.dataclass
class HolderFacts:
    holder_id: str
    owner_type: str
    owner_id: str
    tenant_id: str
    files: list[Path]
    exists: bool = False
    records_live: int = 0
    records_total: int = 0
    documents_live: int = 0
    connector_ids: set[str] = dataclasses.field(default_factory=set)             # connectors table (s0)
    record_connector_ids: set[str] = dataclasses.field(default_factory=set)
    connector_types: dict[str, str] = dataclasses.field(default_factory=dict)
    applied: Counter = dataclasses.field(default_factory=Counter)
    queue_attempts_gt1: int = 0
    queue_open: int = 0                           # ingest_queue rows still queued / leased
    max_incr_checkpoint_version: int = 0          # highest committed page count of an incremental stream
    stage_duplicates: int = 0
    stage_normalize_errors: int = 0
    object_ids: set[str] = dataclasses.field(default_factory=set)               # provider object ids with an ingest_records row (any status)
    rejections: int | None = None                 # ingest_rejections rows (sum of count); None when the holder schema predates the table
    inode: tuple[int, int] | None = None
    problems: dict[str, list[str]] = dataclasses.field(default_factory=lambda: defaultdict(list))
    entity_ids: set[str] = dataclasses.field(default_factory=set)                # publishable entity ids (public/members records)
    export_policy: dict = dataclasses.field(default_factory=dict)                # the holder row's export_policy (authoritative: copied at registration)
    sources: dict = dataclasses.field(default_factory=dict)                      # source_id -> connector_sources row of the control shard
    pub_entities: Counter = dataclasses.field(default_factory=Counter)           # entity id -> distinct effectively-public records mentioning it
    pub_records: int = 0                                                         # live records that are effectively public (the term index's source)


def _marker_of(text: str) -> str:
    """A distinctive, whitespace-free-safe prefix of a record's text (first line, 70 chars) used to look for it in a holder's tables."""
    first = next((ln.strip() for ln in str(text).splitlines() if ln.strip()), "")
    return first[:70]


def derive_faults(run_dir: Path | None, fault_plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """What the run's own inputs say was injected, per holder id, read from the raw source files the harness wrote before the system saw
    them (``sources/`` + ``sources_manifest.json``), never from the system's reports: duplicate lines, malformed lines, delete records
    (with the deleted record's own text as the marker that must not survive), edits, and the restart / replay flags. ``fault_plan.json``
    may add ``restart: [{"holder_id", "pages_before"}]`` and ``deleted_markers``. Returns ``{holder_id: {...}}``."""
    out: dict[str, dict[str, Any]] = {}
    if run_dir is None:
        return out
    try:
        entries = json.loads((run_dir / "sources_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return out
    src_dir = run_dir / "sources"
    for e in entries:
        hid = e.get("holder_id")
        if not hid:
            continue
        f = out.setdefault(hid, {"dup_lines": 0, "malformed": 0, "deletes": 0, "edits": 0, "markers": [], "n_base": 0, "n_timed": 0, "replay": False, "restart": False,
                                 "pages_before": None, "object_ids": set(), "files": []})
        flags = e.get("flags") or {}
        f["replay"] = f["replay"] or bool(flags.get("replay"))
        f["restart"] = f["restart"] or bool(flags.get("restart"))
        f["n_base"] += int(e.get("n_base") or 0)
        f["files"].append(e.get("file"))
        texts: dict[str, list[str]] = defaultdict(list)
        deleted_ids: list[str] = []
        seen_lines: Counter = Counter()
        timed_lines: set[str] = set()
        timed_base: set[str] = set()
        for name in (e.get("file"), e.get("late_file")):
            if not name or not (src_dir / name).is_file():
                continue
            lines = (src_dir / name).read_text(encoding="utf-8").splitlines()
            for i, ln in enumerate(lines):
                if not ln.strip():
                    continue
                try:
                    rec = json.loads(ln)
                except ValueError:
                    f["malformed"] += 1
                    continue
                if not isinstance(rec, dict):
                    f["malformed"] += 1
                    continue
                if rec.get("type") == "source":
                    continue
                if rec.get("id") in (None, ""):
                    f["malformed"] += 1
                    continue
                f["object_ids"].add(str(rec["id"]))
                seen_lines[ln] += 1
                if any(rec.get(k) for k in ("created_at", "updated_at", "deleted_at")):
                    timed_lines.add(ln)                 # a re-delivery (backfill) reads only records that carry a time
                    if name == e.get("file"):
                        timed_base.add(ln)              # the replay happens in the base phase, before the late records are appended
                typ = str(rec.get("type") or "message").lower()
                if typ in ("delete", "deletion"):
                    f["deletes"] += 1
                    deleted_ids.append(str(rec["id"]))
                elif typ == "edit":
                    f["edits"] += 1
                if rec.get("text"):
                    texts[str(rec["id"])].append(_marker_of(rec["text"]))
        f["dup_lines"] += sum(n - 1 for n in seen_lines.values() if n > 1)
        if flags.get("replay"):
            f["n_timed"] += len(timed_base)             # only the re-synced connector's records come back as duplicates
        for rid in deleted_ids:
            f["markers"].extend(m for m in texts.get(rid, []) if m)
    for item in (fault_plan.get("restart") if isinstance(fault_plan.get("restart"), list) else []):
        if isinstance(item, Mapping) and item.get("holder_id"):
            f = out.setdefault(item["holder_id"], {"dup_lines": 0, "malformed": 0, "deletes": 0, "edits": 0, "markers": [], "n_base": 0, "n_timed": 0, "replay": False,
                                                   "restart": True, "pages_before": None, "object_ids": set(), "files": []})
            f["restart"] = True
            if item.get("pages_before") is not None:
                f["pages_before"] = int(item["pages_before"])
    for f in out.values():
        f["markers"] = list(dict.fromkeys(f["markers"]))
    return out


class _Ctx:
    def __init__(self, run_dir: Path | None, coord_db: Path, holders_dir: Path, expect_hypergraph: bool, expect_ranker: bool,
                 allowed_providers: Sequence[str], connector_types: Sequence[str], strict_rank: bool, strict_domains: bool,
                 required_entities: Iterable[str] | None, entity_coverage_min: float, deleted_markers: Sequence[str] | None,
                 expect_term_index: bool | None = None) -> None:
        self.run_dir = run_dir
        self.coord_path = Path(coord_db)
        self.holders_dir = Path(holders_dir)
        self.expect_hypergraph = expect_hypergraph
        self.expect_ranker = expect_ranker
        self.allowed_providers = tuple(allowed_providers)
        self.connector_types = tuple(connector_types)
        self.strict_rank = strict_rank
        self.strict_domains = strict_domains
        self.required_entities = set(required_entities) if required_entities is not None else None      # legacy, ignored: G8 derives the expected set
        self.expect_term_index = expect_term_index
        self.entity_coverage_min = entity_coverage_min
        self.deleted_markers = list(deleted_markers or [])
        self.manifest: dict[str, Any] = {}
        self.fault_plan: dict[str, Any] = {}
        if run_dir is not None:
            for name, attr in (("run_manifest.json", "manifest"), ("fault_plan.json", "fault_plan")):
                p = run_dir / name
                if p.exists():
                    with contextlib.suppress(ValueError, OSError):
                        setattr(self, attr, json.loads(p.read_text(encoding="utf-8")))
        if not self.deleted_markers:
            self.deleted_markers = [str(x) for x in (self.fault_plan.get("deleted_markers") or []) if str(x).strip()]
        self.coord = ro_connect(self.coord_path)
        self.tables = _tables(self.coord)
        self.has_hypergraph = {"hyperedges", "hyperedge_members"} <= self.tables
        self.faults = derive_faults(self.run_dir, self.fault_plan)
        self.holders: list[HolderFacts] = []
        for r in self.coord.execute("SELECT holder_id, tenant_id, owner_type, owner_id, export_policy FROM holders ORDER BY holder_id"):
            hdir = self.holders_dir / r["holder_id"]
            files = [hdir / "evidence.db"] + sorted(Path(p) for p in glob.glob(str(hdir / "shd_*.db")))
            hf = HolderFacts(r["holder_id"], r["owner_type"], r["owner_id"], r["tenant_id"], files)
            hf.export_policy = _jl(r["export_policy"], {}) or {}
            self.holders.append(hf)
        self._scan_holders()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.coord.close()

    # ------------------------------------------------------------------ what a holder may publish (G8)
    def _scan_publication(self, h: HolderFacts, c: sqlite3.Connection, t: set[str]) -> None:
        """Mirror of the product's publication filter: a live record counts when it is effectively public (its own visibility AND its source's
        current visibility are public, ``members`` too only for entities and only when the owner opted in), its source is exportable, its
        disclosure is not ``none``, access is ok, sensitivity is not restricted / confidential, it carries no injection / secret flag and
        sits in no personal domain. Entities are counted in distinct such records; the term index needs only ``pub_records``."""
        if "ingest_records" not in t:
            return
        include_members = (h.export_policy or {}).get("entity_include_members") is True
        recs = c.execute("""SELECT r.record_id, r.source_id, r.visibility, r.sensitivity, r.flags,
                                   EXISTS (SELECT 1 FROM domain_memberships dm WHERE dm.record_id = r.record_id AND dm.status='active'
                                           AND dm.domain_id LIKE 'personal.%') AS personal
                            FROM ingest_records r WHERE r.deletion_status='live'""").fetchall() if "domain_memberships" in t else []
        ent_ok: set[str] = set()
        for r in recs:
            if r["personal"] or r["sensitivity"] in ("restricted", "confidential") or "suspicious_instructions" in (r["flags"] or "") or "contains_secret" in (r["flags"] or ""):
                continue
            src = h.sources.get(r["source_id"]) if r["source_id"] else None
            allowed = ("public", "members") if include_members else ("public",)
            if r["visibility"] not in allowed:
                continue
            if src is not None:
                exportable = src["exportable"] if src["exportable"] is not None else (src["visibility"] != "private")
                if src["visibility"] not in allowed or not exportable or src["disclosure"] == "none" or (src["access_state"] or "ok") != "ok":
                    continue
            ent_ok.add(r["record_id"])
            if r["visibility"] == "public" and (src is None or src["visibility"] == "public"):
                h.pub_records += 1
        if ent_ok and "record_entities" in t and _ENTITY_OK is not None:
            seen: set[tuple[str, str]] = set()
            for e in c.execute("SELECT record_id, entity_id FROM record_entities WHERE role IN ('mention','reference','topic')"):
                if e["record_id"] in ent_ok and _ENTITY_OK(e["entity_id"]) and (e["record_id"], e["entity_id"]) not in seen:
                    seen.add((e["record_id"], e["entity_id"]))
                    h.pub_entities[e["entity_id"]] += 1

    # ------------------------------------------------------------------ per-holder scan (G1, G2, G10, counts)
    def _scan_holders(self) -> None:
        for h in self.holders:
            if not h.files[0].exists():
                continue
            h.exists = True
            st = h.files[0].stat()
            h.inode = (st.st_dev, st.st_ino)
            try:
                sc = ro_connect(h.files[0])
                try:
                    if "connector_sources" in _tables(sc):
                        h.sources = {r["source_id"]: dict(r) for r in sc.execute("SELECT source_id, visibility, exportable, disclosure, access_state FROM connector_sources")}
                finally:
                    sc.close()
            except sqlite3.Error as exc:
                h.problems["scan"].append(f"{h.files[0].name}: sources unreadable: {exc}")
            for fp in h.files:
                if not fp.exists():
                    continue
                conn = ro_connect(fp)
                try:
                    self._scan_file(h, fp, conn)
                except sqlite3.Error as exc:
                    h.problems["scan"].append(f"{fp.name}: {type(exc).__name__}: {exc}")
                finally:
                    conn.close()

    def _scan_file(self, h: HolderFacts, fp: Path, c: sqlite3.Connection) -> None:
        t = _tables(c)
        if "ingest_records" not in t or "documents" not in t:
            h.problems["scan"].append(f"{fp.name}: missing ingest tables")
            return
        h.records_total += _scalar(c, "SELECT COUNT(*) FROM ingest_records")
        h.records_live += _scalar(c, "SELECT COUNT(*) FROM ingest_records WHERE deletion_status='live'")
        h.documents_live += _scalar(c, "SELECT COUNT(*) FROM documents WHERE status IN ('active','revised')")
        if "connectors" in t:
            for r in c.execute("SELECT connector_id, connector_type FROM connectors"):
                h.connector_ids.add(r["connector_id"])
                h.connector_types[r["connector_id"]] = r["connector_type"]
        for r in c.execute("SELECT DISTINCT connector_id FROM ingest_records"):
            h.record_connector_ids.add(r["connector_id"])
        h.object_ids.update(str(r[0]) for r in c.execute("SELECT DISTINCT source_object_id FROM ingest_records"))
        if "applied_events" in t:
            for r in c.execute("SELECT outcome, COUNT(*) AS n FROM applied_events GROUP BY outcome"):
                h.applied[r["outcome"]] += r["n"]
        # G1: live documents without a connector-path ingest_records row / applied_events marker
        orphan = [r[0] for r in c.execute(
            "SELECT d.doc_id FROM documents d LEFT JOIN ingest_records r ON r.record_id = d.doc_id WHERE d.status IN ('active','revised') AND r.record_id IS NULL LIMIT 50")]
        if orphan:
            h.problems["G1.no_ingest_record"].extend(orphan)
        if "applied_events" in t:
            unmarked = [r[0] for r in c.execute(
                "SELECT r.record_id FROM ingest_records r WHERE r.deletion_status='live' AND NOT EXISTS (SELECT 1 FROM applied_events a WHERE a.record_id = r.record_id) LIMIT 50")]
            if unmarked:
                h.problems["G1.no_applied_event"].extend(unmarked)
        else:
            h.problems["G1.no_applied_event"].append(f"{fp.name}: no applied_events table")
        # G2: private records live only with their owner
        for r in c.execute("SELECT record_id, visibility, permissions FROM ingest_records WHERE visibility='private'"):
            perms = _jl(r["permissions"], {})
            members = {str(m) for m in (perms.get("member_ids") or [])}
            if h.owner_type != "user":
                h.problems["G2.private_in_unit_holder"].append(r["record_id"])
            elif h.owner_id not in members:
                h.problems["G2.private_not_owned"].append(r["record_id"])
        # G10
        if "ingest_queue" in t:
            h.queue_attempts_gt1 += _scalar(c, "SELECT COUNT(*) FROM ingest_queue WHERE attempts > 1")
        if "ingest_queue" in t:
            h.queue_open += _scalar(c, "SELECT COUNT(*) FROM ingest_queue WHERE status IN ('queued','leased')")
        if "connector_checkpoints" in t:
            h.max_incr_checkpoint_version = max(h.max_incr_checkpoint_version, int(_scalar(c, "SELECT MAX(version) FROM connector_checkpoints WHERE phase='incremental'", default=0) or 0))
        if "ingest_rejections" in t:
            h.rejections = (h.rejections or 0) + int(_scalar(c, "SELECT COALESCE(SUM(count),0) FROM ingest_rejections", default=0) or 0)
        if "ingest_stage_metrics" in t:
            h.stage_normalize_errors += int(_scalar(c, "SELECT COALESCE(SUM(count),0) FROM ingest_stage_metrics WHERE stage='normalize' AND outcome='error'", default=0) or 0)
            h.stage_duplicates += int(_scalar(c, "SELECT COALESCE(SUM(count),0) FROM ingest_stage_metrics WHERE outcome='duplicate'", default=0) or 0)
        for r in c.execute("""SELECT r.record_id, r.deletion_status FROM ingest_records r WHERE r.deletion_status <> 'live'"""):
            rid = r["record_id"]
            d = c.execute("SELECT text, summary, chars FROM documents WHERE doc_id=?", (rid,)).fetchone()
            if d is not None and ((d["text"] or "") != "" or (d["summary"] or "") != ""):
                h.problems["G10.text_survives_in_documents"].append(rid)
            if "document_versions" in t and _scalar(c, "SELECT COUNT(*) FROM document_versions WHERE doc_id=?", (rid,)):
                h.problems["G10.versions_survive"].append(rid)
            if "memories" in t and _scalar(c, "SELECT COUNT(*) FROM memories WHERE chat_id=? AND (text <> '' OR status = 'active')", (rid,)):
                h.problems["G10.memories_survive"].append(rid)
            if "record_entities" in t and _scalar(c, "SELECT COUNT(*) FROM record_entities WHERE record_id=?", (rid,)):
                h.problems["G10.entities_survive"].append(rid)
        markers = list(dict.fromkeys(self.deleted_markers + self.faults.get(h.holder_id, {}).get("markers", [])))
        if markers:
            for name in sorted(t):
                if name.startswith("sqlite_"):
                    continue
                try:
                    cols = [r[1] for r in c.execute(f"PRAGMA table_info('{name}')")]
                except sqlite3.Error:
                    continue
                for col in cols:
                    for m in markers:
                        try:
                            n = c.execute(f'SELECT COUNT(*) FROM "{name}" WHERE CAST("{col}" AS TEXT) LIKE ?', (f"%{m}%",)).fetchone()[0]
                        except sqlite3.Error:
                            continue
                        if n:
                            h.problems["G10.deleted_marker_survives"].append(f"{fp.name}:{name}.{col}")
        self._scan_publication(h, c, t)
        # G8 entity coverage input (legacy, informational)
        if "record_entities" in t:
            for r in c.execute("""SELECT DISTINCT e.entity_id FROM record_entities e JOIN ingest_records r ON r.record_id = e.record_id
                                  WHERE r.deletion_status='live' AND r.visibility IN ('public','members') AND e.role IN ('mention','reference','topic')"""):
                h.entity_ids.add(r["entity_id"])


# ---------------------------------------------------------------------------------------------- gates
def _g1(x: _Ctx) -> GateResult:
    r = GateResult("G1", "every live record came through the connector path (ingest_records + applied_events + benchmark connector)")
    live = 0
    for h in x.holders:
        if not h.exists:
            continue
        live += h.documents_live
        for key in ("G1.no_ingest_record", "G1.no_applied_event"):
            for rid in h.problems.get(key, []):
                r.fail(f"{h.holder_id}: {key.split('.', 1)[1]}: {rid}")
        for msg in h.problems.get("scan", []):
            r.fail(f"{h.holder_id}: {msg}", ERROR)
        for cid in sorted(h.record_connector_ids):
            ctype = h.connector_types.get(cid)
            if ctype is None:
                r.fail(f"{h.holder_id}: records reference connector {cid} which is not registered in the holder")
            elif ctype not in x.connector_types:
                r.fail(f"{h.holder_id}: connector {cid} has type {ctype!r}, not a benchmark connector {list(x.connector_types)}")
        if h.documents_live and not h.record_connector_ids:
            r.fail(f"{h.holder_id}: documents without any connector")
    r.data = {"live_documents": live, "holders_checked": sum(1 for h in x.holders if h.exists)}
    if live == 0:
        r.fail("no live records in any holder: nothing was ingested through a connector")
    r.detail = f"{live} live documents across {r.data['holders_checked']} holder files"
    return r


def _g2(x: _Ctx) -> GateResult:
    r = GateResult("G2", "holder stores are distinct and records live in the right holder")
    seen_files: dict[tuple[int, int], str] = {}
    conn_owner: dict[str, str] = {}
    reg = {}
    if "connector_registry" in x.tables:
        reg = {row["connector_id"]: row["holder_id"] for row in x.coord.execute("SELECT connector_id, holder_id FROM connector_registry")}
    missing = []
    for h in x.holders:
        if not h.exists:
            missing.append(h.holder_id)
            continue
        if h.inode in seen_files:
            r.fail(f"{h.holder_id} and {seen_files[h.inode]} share one holder file")
        seen_files[h.inode] = h.holder_id        # type: ignore[index]
        for key, rows in sorted(h.problems.items()):
            if key.startswith("G2."):
                for rid in rows:
                    r.fail(f"{h.holder_id}: {key.split('.', 1)[1]}: {rid}")
        for cid in h.connector_ids | h.record_connector_ids:
            if cid in conn_owner and conn_owner[cid] != h.holder_id:
                r.fail(f"connector {cid} appears in holders {conn_owner[cid]} and {h.holder_id}")
            conn_owner[cid] = h.holder_id
            if cid in reg and reg[cid] != h.holder_id:
                r.fail(f"connector {cid} is registered to {reg[cid]} but lives in {h.holder_id}")
    # a holder that holds records must be registered; a registered holder without a file has simply received nothing (reported)
    r.data = {"holders": len(x.holders), "holders_without_file": len(missing), "connectors": len(conn_owner)}
    r.detail = f"{len(seen_files)} distinct holder files, {len(missing)} registered holders without a file"
    return r


def _policy_roots(x: _Ctx, tenant_id: str) -> int:
    row = x.coord.execute("SELECT value FROM tenant_policies WHERE tenant_id=? AND key='min_independent_roots'", (tenant_id,)).fetchone()
    try:
        return int(json.loads(row["value"])) if row else DEFAULT_MIN_ROOTS
    except (TypeError, ValueError):
        return DEFAULT_MIN_ROOTS


def _g3(x: _Ctx) -> GateResult:
    from importlib import import_module
    r = GateResult("G3", "every supported claim has independent roots >= policy, resolvable root-known refs, and a matching hypergraph support edge")
    compute_support = None
    try:
        compute_support = import_module("mycelic.knowledge.support").compute_support
    except Exception as exc:                                       # noqa: BLE001 - recorded, not hidden
        r.data["recompute"] = f"unavailable ({type(exc).__name__})"
    claims = x.coord.execute("SELECT claim_id, tenant_id, support FROM claims WHERE status='supported'").fetchall()
    checked = 0
    for cl in claims:
        checked += 1
        cid = cl["claim_id"]
        support = _jl(cl["support"], {})
        need = _policy_roots(x, cl["tenant_id"])
        stored = int(support.get("independent_roots") or 0)
        if stored < need:
            r.fail(f"{cid}: independent_roots {stored} < policy {need}")
        refs = [dict(row) for row in x.coord.execute(
            """SELECT ce.ref_id, ce.role, e.holder_id, e.source_root_id, e.root_known, e.status, e.meta, e.observed_at
               FROM claim_evidence ce LEFT JOIN evidence_refs e ON e.ref_id = ce.ref_id WHERE ce.claim_id=?""", (cid,))]
        unresolved = [q["ref_id"] for q in refs if q["holder_id"] is None]
        if unresolved:
            r.fail(f"{cid}: claim_evidence refs not in evidence_refs: {unresolved[:3]}")
        supports = [q for q in refs if (q["role"] or "supports") == "supports" and q["holder_id"] is not None]
        unknown = [q["ref_id"] for q in supports if not q["root_known"] or not q["source_root_id"]]
        if len(unknown) == len(supports) and supports:
            r.fail(f"{cid}: no supporting ref has a known root")
        if compute_support is not None:
            rows = [{**q, "meta": _jl(q.get("meta"), {})} for q in refs if q["holder_id"] is not None]
            recomputed = int(compute_support(rows)["independent_roots"])
            if recomputed < need:
                r.fail(f"{cid}: recomputed independent roots {recomputed} < policy {need}")
            if recomputed != stored:
                r.fail(f"{cid}: stored independent_roots {stored} != recomputed {recomputed}")
        if x.expect_hypergraph:
            if not x.has_hypergraph:
                r.fail(f"{cid}: hypergraph tables absent", MISSING)
                continue
            edge = x.coord.execute("SELECT edge_id, independent_roots FROM hyperedges WHERE kind='support' AND anchor_type='claim' AND anchor_id=? AND status='active' "
                                   "ORDER BY version DESC LIMIT 1", (cid,)).fetchone()
            if edge is None:
                r.fail(f"{cid}: no active support edge", MISSING)
                continue
            members = x.coord.execute("SELECT member_type, member_id, role FROM hyperedge_members WHERE edge_id=?", (edge["edge_id"],)).fetchall()
            edge_roles: dict[str, set[str]] = defaultdict(set)
            for m in members:
                if m["member_type"] == "evidence_ref":
                    edge_roles[m["member_id"]].add(m["role"])
            claim_roles = {q["ref_id"]: (q["role"] or "supports") for q in refs}
            if set(edge_roles) != set(claim_roles):
                r.fail(f"{cid}: support edge refs differ from claim_evidence (edge-only {sorted(set(edge_roles) - set(claim_roles))[:2]}, claim-only {sorted(set(claim_roles) - set(edge_roles))[:2]})")
            else:
                wrong = [rid for rid, role in claim_roles.items() if role not in edge_roles[rid] and "copy_of" not in edge_roles[rid]]
                if wrong:
                    r.fail(f"{cid}: support edge roles differ from claim_evidence for {wrong[:2]}")
            if int(edge["independent_roots"] or 0) != stored:
                r.fail(f"{cid}: support edge independent_roots {edge['independent_roots']} != claim {stored}")
            roots_edge = {m["member_id"] for m in members if m["member_type"] == "source_root" and m["role"] == "origin"}
            if len(roots_edge) != stored:
                r.fail(f"{cid}: support edge lists {len(roots_edge)} origin roots, claim has {stored}")
            holders_edge = {m["member_id"] for m in members if m["member_type"] == "holder"}
            holders_claim = {q["holder_id"] for q in supports}
            if not holders_claim <= holders_edge:
                r.fail(f"{cid}: support edge misses holders {sorted(holders_claim - holders_edge)[:3]}")
    r.data = {"supported_claims": checked, "hypergraph_checked": bool(x.expect_hypergraph and x.has_hypergraph)}
    r.detail = f"{checked} supported claims"
    if checked == 0:
        r.detail += " (none to check)"
    return r


def _domain_history(x: _Ctx) -> dict[str, list[tuple[int, set[str], set[str]]]]:
    """holder id -> [(audit id, added, removed)] from the ``holder.domains_published`` audit rows, in audit order: the only record of which
    domains a holder had published when a question was routed."""
    hist: dict[str, list[tuple[int, set[str], set[str]]]] = defaultdict(list)
    for a in x.coord.execute("SELECT id, resource_id, detail FROM audit_log WHERE action='holder.domains_published' ORDER BY id"):
        d = _jl(a["detail"], {})
        hist[a["resource_id"]].append((int(a["id"]), set(d.get("added") or []), set(d.get("removed") or [])))
    return hist


def _published_at(hist: Mapping[str, Sequence[tuple[int, set[str], set[str]]]], holder_id: str, upto_audit_id: int | None) -> set[str]:
    cur: set[str] = set()
    for aid, added, removed in hist.get(holder_id, []):
        if upto_audit_id is not None and aid >= upto_audit_id:
            break
        cur = (cur | added) - removed
    return cur


def _g4(x: _Ctx) -> GateResult:
    """Routing is audited, authorized as of the moment it happened, within budget, and ranked. The authorization replay runs ``can_route`` for
    every routed holder with the domains that holder had published at the route's audit row (rebuilt from the ``holder.domains_published``
    history); no denial is excused as "drift". A holder whose current published domains differ from that history changed outside the audit
    trail, which fails too."""
    r = GateResult("G4", "routing is audited, authorized, within budget and ranked")
    qs = x.coord.execute("SELECT * FROM questions WHERE status NOT IN ('draft','cancelled')").fetchall()
    audits: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for a in x.coord.execute("SELECT id, resource_id, outcome, detail, actor_type FROM audit_log WHERE action='question.route' ORDER BY id"):
        audits[a["resource_id"]].append(a)
    routes: dict[str, list[str]] = defaultdict(list)
    for rt in x.coord.execute("SELECT question_id, holder_id FROM question_routes"):
        routes[rt["question_id"]].append(rt["holder_id"])
    methods: Counter = Counter()
    routed_questions = 0
    replay_rows: list[tuple[sqlite3.Row, list[tuple[str, int]]]] = []
    for q in qs:
        qid = q["question_id"]
        rows = [a for a in audits.get(qid, []) if a["outcome"] in ("ok", "allow")]
        hs = routes.get(qid, [])
        if not hs:
            # a question that never found an authorized holder is audited as a deny and has no routes: allowed only if audited
            if any(a["outcome"] == "deny" for a in audits.get(qid, [])):
                continue
            r.fail(f"{qid}: no question_routes rows and no route audit (status {q['status']})")
            continue
        routed_questions += 1
        if not rows:
            r.fail(f"{qid}: routed but no audit_log row 'question.route'")
        budget = _jl(q["budget"], {})
        cap = int(budget.get("holders") or DEFAULT_ROUTE_BUDGET)
        if len(set(hs)) > cap:
            r.fail(f"{qid}: routed to {len(set(hs))} holders, budget {cap}")
        audited: dict[str, int] = {}
        for a in rows:
            d = _jl(a["detail"], {})
            methods[d.get("rank_method") or "missing"] += 1
            for hid in d.get("holders") or []:
                audited.setdefault(hid, int(a["id"]))
        unaudited = sorted(set(hs) - set(audited))
        if rows and unaudited:
            r.fail(f"{qid}: {len(unaudited)} routed holders are not named in the question.route audit row (e.g. {unaudited[:2]})")
        replay_rows.append((q, [(hid, audited.get(hid, max([int(a["id"]) for a in rows] or [0]) or 0)) for hid in sorted(set(hs))]))
    # ranker assertion
    if x.expect_ranker:
        if not x.has_hypergraph:
            r.fail("ranker expected but the hypergraph tables are absent", MISSING)
        if routed_questions and methods.get("hypergraph", 0) == 0:
            r.fail("ranker expected but no route audit row has rank_method 'hypergraph'", MISSING if not x.has_hypergraph else FAIL)
        if methods.get("missing", 0):
            r.fail(f"{methods['missing']} route audit rows carry no rank_method", MISSING if not x.has_hypergraph else FAIL)
        if x.strict_rank and (sum(methods.values()) - methods.get("hypergraph", 0)):
            r.fail("strict ranking: some routes were not ranked by the hypergraph")
    # domain history must explain the holders' current published domains (a change with no audit row is an unaudited write)
    hist = _domain_history(x)
    drift = 0
    for row in x.coord.execute("SELECT holder_id, published_domains FROM holders"):
        cur = set(_jl(row["published_domains"], []))
        want = _published_at(hist, row["holder_id"], None)
        if cur != want:
            r.fail(f"holder {row['holder_id']}: published domains {sorted(cur)} differ from the audited history {sorted(want)} (changed outside the audit trail)")
        if hist.get(row["holder_id"]) and len(hist[row["holder_id"]]) > 1:
            drift += 1
    # offline replay of can_route, as of each route, on a copy of the coordinator database
    replay = _replay_can_route(x, replay_rows, hist)
    r.data["replay"] = {k: v for k, v in replay.items() if k not in ("denials", "moved")}
    reasons: Counter = Counter()
    for d in replay.get("denials", []):
        reasons[d["reason"]] += 1
        r.fail(f"{d['question_id']} -> {d['holder_id']}: can_route denies as of the route ({d['reason']}; domains then: {d.get('domains_then')})")
    r.data["replay_denial_reasons"] = dict(reasons)
    r.data["holders_with_changing_domains"] = drift
    r.data["routes_where_domains_changed_since"] = replay.get("moved", 0)
    if replay.get("error"):
        r.fail(f"can_route replay could not run: {replay['error']}", ERROR)
    r.data.update({"questions": len(qs), "routed_questions": routed_questions, "rank_methods": dict(methods)})
    r.detail = (f"{routed_questions}/{len(qs)} questions routed; rank_method {dict(methods)}; as-of replay denials {replay.get('denied', 0)}"
                f" (current domains differ from route-time domains on {replay.get('moved', 0)} of {replay.get('checked', 0)} routes)")
    return r


def _replay_can_route(x: _Ctx, rows: Sequence[tuple[sqlite3.Row, Sequence[tuple[str, int]]]], hist: Mapping[str, Sequence[tuple[int, set[str], set[str]]]]) -> dict[str, Any]:
    if not rows:
        return {"checked": 0, "denied": 0}
    tmp = tempfile.mkdtemp(prefix="gate-replay-")
    try:
        copy = Path(tmp) / "coord_copy.db"
        dest = sqlite3.connect(str(copy))
        try:
            x.coord.backup(dest)
        finally:
            dest.close()
        from importlib import import_module
        coord_mod = import_module("mycelic.db.coord")
        org_mod = import_module("mycelic.org")
        authz_mod = import_module("mycelic.authz")
        db = coord_mod.CoordDB(copy, migrate=False)
        try:
            org = org_mod.OrgService(db)
            authz = authz_mod.Authorizer(db, org)
            checked = denied = moved = 0
            denials = []
            for q, hs in rows:
                qd = {"tenant_id": q["tenant_id"], "scope_unit_id": q["scope_unit_id"], "policy": _jl(q["policy"], {}),
                      "candidate_domains": _jl(q["candidate_domains"], [])}
                for hid, audit_id in hs:
                    holder = org.get_holder(hid)
                    checked += 1
                    if holder is None:
                        denied += 1
                        denials.append({"question_id": q["question_id"], "holder_id": hid, "reason": "holder not found"})
                        continue
                    then = _published_at(hist, hid, audit_id)
                    if then != set(holder.get("published_domains") or []):
                        moved += 1
                    holder = dict(holder, published_domains=sorted(then))          # the holder as it was when the route was written
                    ok, why = authz.can_route(qd, holder)
                    if not ok:
                        denied += 1
                        denials.append({"question_id": q["question_id"], "holder_id": hid, "reason": why, "domains_then": sorted(then)})
            return {"checked": checked, "denied": denied, "denials": denials, "moved": moved}
        finally:
            db._conn.close()
    except Exception as exc:                                       # noqa: BLE001
        return {"checked": 0, "denied": 0, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _g5(x: _Ctx) -> GateResult:
    r = GateResult("G5", "every claim has a claim.gate audit row and a revision; every discovery is idempotent")
    gate_keys = {a["resource_id"] for a in x.coord.execute("SELECT resource_id FROM audit_log WHERE action='claim.gate'")}
    claims = x.coord.execute("SELECT claim_id FROM claims").fetchall()
    for cl in claims:
        rev = x.coord.execute("SELECT after FROM revisions WHERE object_type='claim' AND object_id=? AND version=1", (cl["claim_id"],)).fetchone()
        if rev is None:
            r.fail(f"{cl['claim_id']}: no revision row")
            continue
        key = _jl(rev["after"], {}).get("idempotency_key")
        if not key:
            r.fail(f"{cl['claim_id']}: revision has no idempotency key (not committed through the gate)")
        elif key not in gate_keys:
            r.fail(f"{cl['claim_id']}: no claim.gate audit row for {key}")
    keys: Counter = Counter()
    discs = x.coord.execute("SELECT discovery_id, question_id FROM discoveries").fetchall()
    known = {d["discovery_id"] for d in discs}
    for d in discs:
        rev = x.coord.execute("SELECT actor_type, after FROM revisions WHERE object_type='discovery' AND object_id=? AND version=1", (d["discovery_id"],)).fetchone()
        after = _jl(rev["after"], {}) if rev else {}
        key = after.get("idempotency_key")
        if not rev:
            r.fail(f"{d['discovery_id']}: no creation revision")
        elif key:
            keys[key] += 1
            if d["question_id"] and key != f"disc:{d['question_id']}":
                r.fail(f"{d['discovery_id']}: idempotency key {key!r} != 'disc:{d['question_id']}'")
        elif after.get("source") in known and rev["actor_type"] != "user":
            pass                                   # an escalated copy of another discovery (aggregate level), written by the worker
        else:
            r.fail(f"{d['discovery_id']}: creation revision has neither an idempotency key nor an escalation source")
    for k, n in keys.items():
        if n > 1:
            r.fail(f"discovery key {k} used by {n} discoveries")
    r.data = {"claims": len(claims), "discoveries": len(discs)}
    r.detail = f"{len(claims)} claims, {len(discs)} discoveries"
    return r


def _g6(x: _Ctx) -> GateResult:
    r = GateResult("G6", "all model usage names the declared provider and the run carries its label")
    rows = x.coord.execute("SELECT provider, COUNT(*) AS n, SUM(input_tokens) AS i, SUM(output_tokens) AS o FROM model_usage GROUP BY provider").fetchall()
    by = {row["provider"]: row["n"] for row in rows}
    allowed = set(x.allowed_providers)
    label = x.manifest.get("provider_label") or x.manifest.get("provider")
    if label in LABEL_PROVIDER:
        allowed |= {LABEL_PROVIDER[label]}
    for p, n in sorted(by.items()):
        if p not in allowed:
            r.fail(f"{n} model_usage rows name provider {p!r}, allowed {sorted(allowed)}")
    if not label:
        r.fail("run_manifest carries no provider_label" if x.run_dir is not None else "no provider label supplied", MISSING if x.run_dir is None else FAIL)
    claims = _scalar(x.coord, "SELECT COUNT(*) FROM claims")
    if claims and not by:
        r.fail("claims exist but model_usage is empty: the evaluation did not go through the model router")
    r.data = {"providers": by, "calls": sum(by.values()), "input_tokens": int(sum(row["i"] or 0 for row in rows)), "output_tokens": int(sum(row["o"] or 0 for row in rows)),
              "label": label}
    r.detail = f"label={label!r}, providers={by}"
    return r


def _view_claim_items(view: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Every claim payload the scorer (or the disclosure check) can read in a view: question claims, discovery claims, goal-level claims."""
    items: list[Mapping[str, Any]] = []
    for key in ("claims", "goal_claims"):
        items += [i for i in (view.get(key) or []) if isinstance(i, Mapping)]
    for key in ("discoveries", "goal_discoveries"):
        for d in view.get(key) or []:
            if isinstance(d, Mapping):
                items += [i for i in (d.get("claims") or []) if isinstance(i, Mapping)]
    return items


def _cross_check_views(x: _Ctx, r: GateResult) -> int:
    """Every view the scorer reads must agree with ``coord.db``: each claim's id, status, text and independent roots, each reference's holder
    and root, each discovery's title / summary / claim ids, and the question's terminal status. A later legitimate revision is explained by a
    revision row with a higher version; a difference with no such row, a claim the database does not know, or a question claim missing from
    the view means the view was not read from the system."""
    if x.run_dir is None:
        return 0
    vdir = x.run_dir / "views"
    if not vdir.is_dir():
        if x.manifest.get("n_tasks"):
            r.fail("the run recorded tasks but has no views directory: the scorer's input cannot be cross-checked against coord.db", MISSING)
        return 0
    c = x.coord
    n = 0
    for vp in sorted(vdir.glob("*.json")):
        try:
            view = json.loads(vp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            r.fail(f"{vp.name}: unreadable view")
            continue
        if not isinstance(view, Mapping):
            continue
        n += 1
        tag = view.get("task_id") or vp.stem
        in_view: set[str] = set()
        for item in _view_claim_items(view):
            cl = item.get("claim") if isinstance(item.get("claim"), Mapping) else item
            cid = str(cl.get("claim_id") or "")
            if not cid:
                continue
            in_view.add(cid)
            row = c.execute("SELECT status, text, version, support, question_id FROM claims WHERE claim_id=?", (cid,)).fetchone()
            if row is None:
                r.fail(f"{tag}: view claim {cid} does not exist in coord.db")
                continue
            vsup = item.get("support") if isinstance(item.get("support"), Mapping) else (cl.get("support") if isinstance(cl.get("support"), Mapping) else {})
            dsup = _jl(row["support"], {})
            same = (str(cl.get("status")) == row["status"] and str(cl.get("text") or "").strip() == str(row["text"]).strip()
                    and int(vsup.get("independent_roots") or 0) == int(dsup.get("independent_roots") or 0))
            vver = cl.get("version")
            if not same:
                later = vver is not None and int(row["version"]) > int(vver) and _scalar(
                    c, "SELECT COUNT(*) FROM revisions WHERE object_type='claim' AND object_id=? AND version>?", (cid, int(vver)))
                if not later:
                    r.fail(f"{tag}: view claim {cid} differs from coord.db (view {cl.get('status')!r}/{vsup.get('independent_roots')} roots, "
                           f"database {row['status']!r}/{dsup.get('independent_roots')} roots) with no later revision to explain it")
            if cl.get("question_id") not in (None, row["question_id"]):
                r.fail(f"{tag}: view claim {cid} names question {cl.get('question_id')} but the database says {row['question_id']}")
            for ref in item.get("evidence") or []:
                if not isinstance(ref, Mapping) or not ref.get("ref_id"):
                    continue
                er = c.execute("SELECT holder_id, source_root_id FROM evidence_refs WHERE ref_id=?", (ref["ref_id"],)).fetchone()
                if er is None:
                    r.fail(f"{tag}: view reference {ref['ref_id']} does not exist in coord.db")
                elif (ref.get("holder_id") not in (None, er["holder_id"])) or (ref.get("source_root_id") not in (None, er["source_root_id"])):
                    r.fail(f"{tag}: view reference {ref['ref_id']} differs from coord.db (holder / root)")
        for key in ("discoveries", "goal_discoveries"):
            for d in view.get(key) or []:
                disc = d.get("discovery") if isinstance(d, Mapping) and isinstance(d.get("discovery"), Mapping) else d
                if not isinstance(disc, Mapping) or not disc.get("discovery_id"):
                    continue
                row = c.execute("SELECT title, summary, claim_ids FROM discoveries WHERE discovery_id=?", (disc["discovery_id"],)).fetchone()
                if row is None:
                    r.fail(f"{tag}: view discovery {disc['discovery_id']} does not exist in coord.db")
                    continue
                differs = (str(disc.get("title") or "") != row["title"] or str(disc.get("summary") or "") != row["summary"]
                           or (disc.get("claim_ids") is not None and list(disc.get("claim_ids")) != _jl(row["claim_ids"], [])))
                if differs and not _scalar(c, "SELECT COUNT(*) FROM revisions WHERE object_type='discovery' AND object_id=? AND version>1", (disc["discovery_id"],)):
                    r.fail(f"{tag}: view discovery {disc['discovery_id']} differs from coord.db with no later revision to explain it")
        q = view.get("question")
        if isinstance(q, Mapping) and q.get("question_id"):
            qrow = c.execute("SELECT status, resolved_at FROM questions WHERE question_id=?", (q["question_id"],)).fetchone()
            if qrow is None:
                r.fail(f"{tag}: view question {q['question_id']} does not exist in coord.db")
                continue
            terminal = ("committed", "retained_uncertain", "expired", "failed", "cancelled")
            if str(q.get("status")) in terminal and qrow["status"] != q.get("status"):
                r.fail(f"{tag}: view says question {q['question_id']} is {q.get('status')!r}, coord.db says {qrow['status']!r}")
            resolved = q.get("resolved_at") or qrow["resolved_at"]
            if resolved and str(q.get("status")) in terminal:
                hidden = [row["claim_id"] for row in c.execute("SELECT claim_id FROM claims WHERE question_id=? AND created_at<=?", (q["question_id"], resolved))
                          if row["claim_id"] not in in_view]
                if hidden:
                    r.fail(f"{tag}: coord.db holds {len(hidden)} claims of question {q['question_id']} created before it resolved that the view omits (e.g. {hidden[:2]})")
    return n


def _g7(x: _Ctx) -> GateResult:
    r = GateResult("G7", "claims, discoveries and evidence were produced by the system, not written by the harness")
    for cl in x.coord.execute("SELECT claim_id, created_by_type FROM claims").fetchall():
        if cl["created_by_type"] not in CLAIM_CREATOR_TYPES:
            r.fail(f"{cl['claim_id']}: created_by_type {cl['created_by_type']!r} (expected {list(CLAIM_CREATOR_TYPES)})")
            continue
        d = x.coord.execute("SELECT response_ids, input_claim_ids FROM derivations WHERE claim_id=?", (cl["claim_id"],)).fetchall()
        if not d:
            r.fail(f"{cl['claim_id']}: no derivation row")
            continue
        resp_ids: list[str] = []
        inputs: list[str] = []
        for row in d:
            resp_ids += _jl(row["response_ids"], [])
            inputs += _jl(row["input_claim_ids"], [])
        if not resp_ids and not inputs:
            r.fail(f"{cl['claim_id']}: derivation names neither holder responses nor input claims")
        elif resp_ids:
            have = {row[0] for row in x.coord.execute(f"SELECT response_id FROM responses WHERE response_id IN ({','.join('?' * len(resp_ids))})", resp_ids)}
            if set(resp_ids) - have:
                r.fail(f"{cl['claim_id']}: derivation names responses that do not exist ({sorted(set(resp_ids) - have)[:2]})")
    # every evidence ref arrived in a holder response of its own holder
    in_responses: dict[str, set[str]] = defaultdict(set)
    human = 0
    for row in x.coord.execute("SELECT holder_id, evidence_ref_ids, provenance FROM responses"):
        for rid in _jl(row["evidence_ref_ids"], []):
            in_responses[rid].add(row["holder_id"])
        if str(_jl(row["provenance"], {}).get("retrieval_operator") or "") in ("human", "manual"):
            human += 1
    if human:
        r.fail(f"{human} responses were written by a person/harness (retrieval_operator human), not retrieved by a holder")
    refs = x.coord.execute("SELECT ref_id, holder_id FROM evidence_refs").fetchall()
    for e in refs:
        if e["holder_id"] not in in_responses.get(e["ref_id"], set()):
            r.fail(f"evidence_ref {e['ref_id']} is not in any response of holder {e['holder_id']}")
    # discoveries created through the loop's audit trail; no user principal on system writes
    for d in x.coord.execute("SELECT discovery_id FROM discoveries").fetchall():
        a = x.coord.execute("SELECT actor_type FROM audit_log WHERE action='discovery.create' AND resource_id=? LIMIT 1", (d["discovery_id"],)).fetchone()
        if a is None:
            rev = x.coord.execute("SELECT actor_type, after FROM revisions WHERE object_type='discovery' AND object_id=? AND version=1", (d["discovery_id"],)).fetchone()
            if rev is not None and _jl(rev["after"], {}).get("source") and rev["actor_type"] in ("worker", "system", "loop"):
                continue                           # an escalated copy: written by the worker, audited on its source
            r.fail(f"{d['discovery_id']}: no discovery.create audit row and no worker escalation revision")
        elif a["actor_type"] == "user":
            r.fail(f"{d['discovery_id']}: created by a user principal")
    n_user = _scalar(x.coord, """SELECT COUNT(*) FROM audit_log WHERE actor_type='user' AND (action IN ('claim.gate','discovery.create','response.accept','response.reject')
                                    OR action LIKE 'claim.commit%')""")
    if n_user:
        r.fail(f"{n_user} audit rows record a user principal writing claims/discoveries/responses")
    opens = x.manifest.get("harness_db_opens")
    if opens is None and x.run_dir is not None and (x.run_dir / "harness_db_opens.json").exists():
        with contextlib.suppress(ValueError, OSError):
            opens = json.loads((x.run_dir / "harness_db_opens.json").read_text(encoding="utf-8"))
    if opens:
        for o in opens:
            uri = str(o.get("uri") or "")
            if "mode=ro" not in uri:
                r.fail(f"harness opened {o.get('path')} without mode=ro")
    checked = _cross_check_views(x, r)
    r.data = {"claims": _scalar(x.coord, "SELECT COUNT(*) FROM claims"), "refs": len(refs), "harness_opens_declared": len(opens or []), "views_cross_checked": checked}
    r.detail = f"{r.data['claims']} claims, {len(refs)} evidence refs traced to holder responses"
    return r


def _g8(x: _Ctx) -> GateResult:
    r = GateResult("G8", "hypergraph consistent; entity and term indexes hold exactly what holders may publish (privacy) and cover it")
    if not x.expect_hypergraph:
        r.status, r.detail = NA, "hypergraph not expected in this run"
        return r
    if not x.has_hypergraph:
        r.fail("tables hyperedges / hyperedge_members are absent", MISSING)
        r.detail = "pre-hypergraph run"
        return r
    c = x.coord
    # I1 tenant scoping
    bad = c.execute("SELECT COUNT(*) FROM hyperedge_members m JOIN hyperedges e ON e.edge_id = m.edge_id WHERE m.tenant_id <> e.tenant_id").fetchone()[0]
    if bad:
        r.fail(f"I1: {bad} members carry a tenant different from their edge")
    # I2 claim edges
    for cl in c.execute("SELECT claim_id FROM claims").fetchall():
        k = {row["kind"] for row in c.execute("SELECT kind FROM hyperedges WHERE anchor_type='claim' AND anchor_id=? AND status IN ('active','retracted')", (cl["claim_id"],))}
        for need in ("support", "lineage"):
            if need not in k:
                r.fail(f"I2: claim {cl['claim_id']} has no {need} edge")
    # I3 versions: at most one active per (kind, anchor); superseded edges have a successor
    dup = c.execute("SELECT tenant_id, kind, anchor_type, anchor_id, COUNT(*) AS n FROM hyperedges WHERE status='active' "
                    "GROUP BY tenant_id, kind, anchor_type, anchor_id HAVING n > 1 LIMIT 5").fetchall()
    for d in dup:
        r.fail(f"I3: {d['n']} active versions of {d['kind']} edge on {d['anchor_type']} {d['anchor_id']}")
    # every edge has members; members of one type reference existing rows where cheap to check
    empty = c.execute("SELECT COUNT(*) FROM hyperedges e WHERE e.status='active' AND NOT EXISTS (SELECT 1 FROM hyperedge_members m WHERE m.edge_id = e.edge_id)").fetchone()[0]
    if empty:
        r.fail(f"{empty} active edges have no members")
    orphan = c.execute("""SELECT COUNT(*) FROM hyperedges e WHERE e.status='superseded' AND e.kind <> 'conflict'
                        AND NOT EXISTS (SELECT 1 FROM hyperedges n WHERE n.supersedes_edge_id = e.edge_id)""").fetchone()[0]
    if orphan:
        r.fail(f"I3: {orphan} superseded edges have no successor edge")
    miss = c.execute("""SELECT COUNT(*) FROM hyperedge_members m WHERE m.member_type='claim' AND NOT EXISTS (SELECT 1 FROM claims cl WHERE cl.claim_id = m.member_id)""").fetchone()[0]
    if miss:
        r.fail(f"{miss} claim members reference unknown claims")
    miss = c.execute("""SELECT COUNT(*) FROM hyperedge_members m WHERE m.member_type='evidence_ref' AND NOT EXISTS (SELECT 1 FROM evidence_refs e WHERE e.ref_id = m.member_id)""").fetchone()[0]
    if miss:
        r.fail(f"{miss} evidence_ref members reference unknown refs")
    # I5: no supported claim with an open conflict
    inc = c.execute("""SELECT COUNT(*) FROM claims cl WHERE cl.status='supported' AND EXISTS
                       (SELECT 1 FROM conflicts k WHERE k.status IN ('open','investigating') AND (k.claim_a_id = cl.claim_id OR k.claim_b_id = cl.claim_id))""").fetchone()[0]
    if inc:
        r.fail(f"I5: {inc} supported claims have an open conflict")
    # ---- entity index (privacy invariant + coverage), judged against what each holder is allowed to publish
    if _ENTITY_OK is None:
        r.fail("mycelic.entities could not be imported: the expected entity set cannot be computed", ERROR)
        return r
    expected: set[tuple[str, str]] = set()
    below = 0
    by_id = {h.holder_id: h for h in x.holders}
    for h in x.holders:
        pol = h.export_policy or {}
        if pol.get("auto_entities") is False:
            continue
        try:
            need = max(1, int(pol.get("entity_min_records") or 5))
        except (TypeError, ValueError):
            need = 5
        qualifying = sorted(((e, n) for e, n in h.pub_entities.items() if n >= need), key=lambda kv: (-kv[1], kv[0]))[:500]
        below += sum(1 for n in h.pub_entities.values() if n < need)
        expected.update((e, h.holder_id) for e, _ in qualifying)
    indexed: set[tuple[str, str]] = set()
    for row in c.execute("""SELECT e.anchor_id, e.tenant_id, m.member_id FROM hyperedges e JOIN hyperedge_members m ON m.edge_id = e.edge_id
                            WHERE e.kind='entity_index' AND e.status='active' AND m.member_type='holder' AND m.role='holds'"""):
        h = by_id.get(row["member_id"])
        if h is None or h.tenant_id != row["tenant_id"]:
            r.fail(f"entity index: {row['anchor_id']} is attributed to unknown or other-tenant holder {row['member_id']}")
            continue
        indexed.add((row["anchor_id"], row["member_id"]))
    covered = len(expected & indexed)
    extras = sorted(indexed - expected)
    ent_cov = (covered / len(expected)) if expected else None
    if expected and ent_cov < x.entity_coverage_min:
        r.fail(f"entity index covers {covered}/{len(expected)} = {ent_cov:.2f} of the entities the holders may publish (< {x.entity_coverage_min:.2f})")
    for e, hid in extras[:MAX_PROBLEMS]:
        r.fail(f"privacy: entity {e} is indexed for holder {hid}, which may not publish it (not in enough effectively-public records, or auto_entities off)")
    if len(extras) > MAX_PROBLEMS:
        r.data["entity_extras_truncated"] = len(extras) - MAX_PROBLEMS
    # ---- term index (hashed rare words of public records; the HMACs cannot be recomputed here, so the checks are structural)
    term_note = None
    term_exp = term_cov_n = term_bad = 0
    term_cov: float | None = None
    if "term_index" not in x.tables:
        term_note = "term_index table absent"
        if x.expect_term_index:
            r.fail("term_index table is absent", MISSING)
    elif x.expect_term_index is False:
        term_note = "term index not expected in this run"
    else:
        rows_by_holder: Counter = Counter()
        for row in c.execute("SELECT tenant_id, holder_id, COUNT(*) AS n FROM term_index GROUP BY tenant_id, holder_id"):
            h = by_id.get(row["holder_id"])
            if h is None or h.tenant_id != row["tenant_id"]:
                r.fail(f"term index: {row['n']} rows for unknown or other-tenant holder {row['holder_id']}")
                term_bad += 1
                continue
            rows_by_holder[row["holder_id"]] += row["n"]
        for hid, n in sorted(rows_by_holder.items()):
            h = by_id[hid]
            if h.pub_records < 1 or (h.export_policy or {}).get("auto_terms") is False:
                term_bad += 1
                if term_bad <= MAX_PROBLEMS:
                    r.fail(f"privacy: holder {hid} has {n} term rows but "
                           + ("auto_terms is off" if (h.export_policy or {}).get("auto_terms") is False else "no live effectively-public record to draw them from"))
        wanted = [h.holder_id for h in x.holders if h.exists and h.pub_records >= 1 and (h.export_policy or {}).get("auto_terms") is not False]
        term_exp = len(wanted)
        term_cov_n = sum(1 for hid in wanted if rows_by_holder.get(hid))
        term_cov = (term_cov_n / term_exp) if term_exp else None
        if term_exp and term_cov < x.entity_coverage_min:
            r.fail(f"term index covers {term_cov_n}/{term_exp} = {term_cov:.2f} of the holders with public records (< {x.entity_coverage_min:.2f})")
    r.data.update({"entity_index_pairs": len(indexed), "entity_expected_pairs": len(expected), "entity_covered": covered, "entity_coverage": ent_cov,
                   "entity_extras": len(extras), "entity_pairs_below_threshold": below, "coverage_basis": "per-holder expected set from the holder stores under the effective export policy",
                   "term_index_rows_ok": term_bad == 0, "term_holders_expected": term_exp, "term_holders_covered": term_cov_n, "term_coverage": term_cov,
                   "term_holders_violating": term_bad, "term_note": term_note})
    pct = lambda v: "n/a" if v is None else f"{v:.0%}"      # noqa: E731
    r.detail = (f"entities {covered}/{len(expected)} ({pct(ent_cov)}; {below} holder-entity pairs below entity_min_records, correctly unpublished), "
                f"{len(extras)} outside the expected set; "
                + (f"terms {term_cov_n}/{term_exp} holders ({pct(term_cov)}), {term_bad} violating" if term_note is None else f"terms: {term_note}"))
    return r


def _g9(x: _Ctx) -> GateResult:
    r = GateResult("G9", "lineage resolves for every discovery: discovery -> claims -> refs -> holders (and question/response)")
    n = 0
    holders_known = {row[0] for row in x.coord.execute("SELECT holder_id FROM holders")}
    for d in x.coord.execute("SELECT discovery_id, claim_ids, question_id, tenant_id FROM discoveries").fetchall():
        n += 1
        did = d["discovery_id"]
        cids = _jl(d["claim_ids"], [])
        if not cids:
            r.fail(f"{did}: discovery has no claims")
            continue
        for cid in cids:
            cl = x.coord.execute("SELECT claim_id, tenant_id, question_id, status FROM claims WHERE claim_id=?", (cid,)).fetchone()
            if cl is None:
                r.fail(f"{did}: claim {cid} does not exist")
                continue
            cl_status = cl["status"]
            if cl["tenant_id"] != d["tenant_id"]:
                r.fail(f"{did}: claim {cid} is in another tenant")
            refs = x.coord.execute("SELECT e.ref_id, e.holder_id, e.tenant_id FROM claim_evidence ce JOIN evidence_refs e ON e.ref_id = ce.ref_id WHERE ce.claim_id=?", (cid,)).fetchall()
            inputs = x.coord.execute("SELECT input_claim_ids FROM derivations WHERE claim_id=?", (cid,)).fetchall()
            has_inputs = any(_jl(i["input_claim_ids"], []) for i in inputs)
            if not refs and not has_inputs:
                r.fail(f"{did}: claim {cid} has neither evidence refs nor input claims")
            for e in refs:
                if e["holder_id"] not in holders_known:
                    r.fail(f"{did}: ref {e['ref_id']} names unknown holder {e['holder_id']}")
                if e["tenant_id"] != d["tenant_id"]:
                    r.fail(f"{did}: ref {e['ref_id']} is in another tenant")
            if x.expect_hypergraph and x.has_hypergraph:
                allowed = ("active", "retracted") if cl_status == "retracted" else ("active",)
                if not x.coord.execute(f"SELECT 1 FROM hyperedges WHERE kind='lineage' AND anchor_type='claim' AND anchor_id=? AND status IN ({','.join('?' * len(allowed))}) LIMIT 1",
                                       (cid, *allowed)).fetchone():
                    r.fail(f"{did}: claim {cid} ({cl_status}) has no {'/'.join(allowed)} lineage edge")
        if d["question_id"] and not x.coord.execute("SELECT 1 FROM questions WHERE question_id=?", (d["question_id"],)).fetchone():
            r.fail(f"{did}: question {d['question_id']} is missing")
    r.data = {"discoveries": n}
    r.detail = f"{n} discoveries"
    return r


def _g10(x: _Ctx) -> GateResult:
    """Fault dispositions, derived from the run's own raw inputs (``derive_faults``), not from what the system reports about itself. For every
    holder whose source files carry a fault, the evidence of its disposition must be in that holder's store; a fault without evidence fails."""
    r = GateResult("G10", "fault dispositions: duplicates, malformed lines, deletions, replay and restart are visible in the stores")
    for h in x.holders:
        for key, rows in sorted(h.problems.items()):
            if key.startswith("G10."):
                for item in rows:
                    r.fail(f"{h.holder_id}: {key.split('.', 1)[1]}: {item}")
    # one record each: no two live ingest_records share a provider object inside a holder
    for h in x.holders:
        if not h.exists:
            continue
        for fp in h.files:
            if not fp.exists():
                continue
            conn = ro_connect(fp)
            try:
                if "ingest_records" in _tables(conn):
                    for row in conn.execute("""SELECT connector_id, source_object_type, source_object_id, COUNT(*) AS n FROM ingest_records WHERE deletion_status='live'
                                               GROUP BY connector_id, source_object_type, source_object_id HAVING n > 1 LIMIT 5"""):
                        r.fail(f"{h.holder_id}: {row['n']} live records for one provider object {row['source_object_id']}")
            finally:
                conn.close()
    by_id = {h.holder_id: h for h in x.holders}
    totals: Counter = Counter()
    checked_markers = 0
    for hid, f in sorted(x.faults.items()):
        h = by_id.get(hid)
        if h is None or not h.exists:
            if f["dup_lines"] or f["malformed"] or f["deletes"] or f["restart"] or f["replay"]:
                r.fail(f"{hid}: faults were injected into its sources but the holder has no store")
            continue
        dup_evidence = h.applied.get("duplicate", 0) + h.stage_duplicates
        expected_dup = f["dup_lines"] + (f["n_timed"] if f["replay"] else 0)
        totals["dup_lines"] += f["dup_lines"]
        totals["malformed"] += f["malformed"]
        totals["deletes"] += f["deletes"]
        totals["restart"] += 1 if f["restart"] else 0
        totals["replay"] += 1 if f["replay"] else 0
        if expected_dup and dup_evidence < expected_dup:
            r.fail(f"{hid}: {f['dup_lines']} duplicate lines" + (f" and a replay of {f['n_timed']} timed records" if f["replay"] else "")
                   + f" were injected; applied_events/dedupe rows show {dup_evidence} duplicate dispositions (need >= {expected_dup})")
        if f["malformed"]:
            seen = h.rejections if h.rejections is not None else h.stage_normalize_errors
            if seen < f["malformed"]:
                r.fail(f"{hid}: {f['malformed']} malformed lines were injected; the store accounts for {seen} (ingest_rejections / normalize errors)")
        if f["deletes"]:
            if not f["markers"]:
                r.fail(f"{hid}: {f['deletes']} delete records were injected but no deleted string could be derived to check")
            checked_markers += len(f["markers"])
            if h.applied.get("delete", 0) < 1:
                r.fail(f"{hid}: {f['deletes']} delete records were injected but applied_events holds no 'delete' disposition")
        if f["restart"]:
            before = f["pages_before"] if f["pages_before"] is not None else 1
            if not (h.queue_attempts_gt1 or h.max_incr_checkpoint_version > before):
                r.fail(f"restart mid-ingest was injected into {hid} but its incremental cursor never advanced past {before} page(s) and no queue row was leased again")
            elif h.queue_open:
                r.fail(f"restart holder {hid} still has {h.queue_open} unprocessed queue rows")
            missing = sorted(f["object_ids"] - h.object_ids)
            if missing:
                r.fail(f"restart holder {hid} never ingested {len(missing)} of its source records after resuming (e.g. {missing[:2]})")
    # a run-level declaration (fault_plan.json / manifest) with no per-holder derivation behind it must still leave evidence
    declared = dict(x.manifest.get("faults") or x.fault_plan.get("faults") or {})
    dup_applied = sum(h.applied.get("duplicate", 0) for h in x.holders)
    dup_stage = sum(h.stage_duplicates for h in x.holders)
    if not x.faults:
        if (declared.get("duplicate") or declared.get("duplicate_delivery") or declared.get("replay")) and dup_applied + dup_stage < int(declared.get("duplicate") or 1):
            r.fail("duplicate delivery was injected but applied_events / ingest_stage_metrics record too few duplicates")
        rej0 = [h.rejections for h in x.holders if h.rejections is not None]
        if declared.get("malformed") and rej0 and sum(rej0) == 0:
            r.fail("malformed lines were injected but ingest_rejections records none")
        if (declared.get("restart") or x.fault_plan.get("restart")) and sum(h.queue_attempts_gt1 for h in x.holders) == 0:
            r.fail("restart mid-ingest was injected but no ingest_queue row was leased twice")
    rej = [h.rejections for h in x.holders if h.rejections is not None]
    r.data = {"derived_faults": dict(totals), "declared_faults": declared, "duplicates_applied_events": dup_applied, "duplicates_stage_metrics": dup_stage,
              "queue_rows_leased_more_than_once": sum(h.queue_attempts_gt1 for h in x.holders),
              "deleted_markers_checked": checked_markers + len(x.deleted_markers), "ingest_rejections": sum(rej) if rej else None}
    if not totals and not declared and not x.deleted_markers:
        r.detail = "no faults found in the run's sources; structural purge/uniqueness checks only"
    else:
        r.detail = f"injected {dict(totals)}; duplicate dispositions seen {dup_applied + dup_stage}; {r.data['deleted_markers_checked']} deleted strings checked"
    return r


# ---------------------------------------------------------------------------------------------- counts
def _counts(x: _Ctx) -> dict[str, Any]:
    c = x.coord
    applied: Counter = Counter()
    for h in x.holders:
        applied.update(h.applied)
    activated: set[str] = set()
    for row in c.execute("SELECT holder_id, evidence_ref_ids FROM responses WHERE status='answered'"):
        if _jl(row["evidence_ref_ids"], []):
            activated.add(row["holder_id"])
    routed = {row[0] for row in c.execute("SELECT DISTINCT holder_id FROM question_routes")}
    counts: dict[str, Any] = {
        "holders_created": len(x.holders),
        "holders_with_files": sum(1 for h in x.holders if h.exists),
        "holders_with_records": sum(1 for h in x.holders if h.records_live >= 1),
        "holders_activated": len(activated),
        "holders_routed": len(routed),
        "routes": _scalar(c, "SELECT COUNT(*) FROM question_routes"),
        "questions": _scalar(c, "SELECT COUNT(*) FROM questions"),
        "responses_answered": _scalar(c, "SELECT COUNT(*) FROM responses WHERE status='answered'"),
        "records_ingested": sum(h.records_total for h in x.holders),
        "ingest_rejections": (sum(h.rejections for h in x.holders if h.rejections is not None) if any(h.rejections is not None for h in x.holders) else None),
        "records_live": sum(h.records_live for h in x.holders),
        "applied_events_outcomes": dict(sorted(applied.items())),
        "claims_by_status": {row["status"]: row["n"] for row in c.execute("SELECT status, COUNT(*) AS n FROM claims GROUP BY status")},
        "discoveries": _scalar(c, "SELECT COUNT(*) FROM discoveries"),
        "hyperedges_by_kind": ({row["kind"]: row["n"] for row in c.execute("SELECT kind, COUNT(*) AS n FROM hyperedges WHERE status='active' GROUP BY kind")}
                               if x.has_hypergraph else None),
        "hyperedges_by_kind_status": ({f"{row['kind']}/{row['status']}": row["n"] for row in c.execute("SELECT kind, status, COUNT(*) AS n FROM hyperedges GROUP BY kind, status")}
                                      if x.has_hypergraph else None),
        "model_calls": _scalar(c, "SELECT COUNT(*) FROM model_usage"),
        "coord_db_bytes": x.coord_path.stat().st_size if x.coord_path.exists() else None,
        "holder_files_bytes": sum(fp.stat().st_size for h in x.holders for fp in h.files if fp.exists()),
    }
    return counts


GATES = (("G1", _g1), ("G2", _g2), ("G3", _g3), ("G4", _g4), ("G5", _g5), ("G6", _g6), ("G7", _g7), ("G8", _g8), ("G9", _g9), ("G10", _g10))


def check(run_dir: str | os.PathLike[str] | None, *, coord_db: str | os.PathLike[str], holders_dir: str | os.PathLike[str],
          expect_hypergraph: bool = True, expect_ranker: bool | None = None, allowed_providers: Sequence[str] = DEFAULT_PROVIDERS,
          connector_types: Sequence[str] = BENCH_CONNECTOR_TYPES, strict_rank: bool = False, strict_domains: bool = False,
          required_entities: Iterable[str] | None = None, entity_coverage_min: float = 0.95,
          deleted_markers: Sequence[str] | None = None, expect_term_index: bool | None = None) -> GateReport:
    """Run G1-G10. ``run_dir`` may be None (then no manifest / fault plan is read and G6 needs ``allowed_providers`` only)."""
    try:
        x = _Ctx(Path(run_dir) if run_dir is not None else None, Path(coord_db), Path(holders_dir), expect_hypergraph,
                 expect_hypergraph if expect_ranker is None else expect_ranker, allowed_providers, connector_types, strict_rank, strict_domains,
                 required_entities, entity_coverage_min, deleted_markers, expect_term_index)
    except (sqlite3.Error, OSError) as exc:        # no readable coordinator database: every gate is unverifiable, hence failed
        msg = f"cannot open {coord_db} read-only: {type(exc).__name__}: {exc}"
        return GateReport(results={gid: GateResult(gid, "unverifiable", ERROR, msg) for gid, _ in GATES}, counts={}, expect_hypergraph=expect_hypergraph,
                          expect_ranker=expect_hypergraph if expect_ranker is None else expect_ranker, notes=[msg])
    try:
        results: dict[str, GateResult] = {}
        for gid, fn in GATES:
            try:
                results[gid] = fn(x)
            except Exception as exc:                                # noqa: BLE001 - a crashing gate is a failed gate
                results[gid] = GateResult(gid, "gate crashed", ERROR, f"{type(exc).__name__}: {exc}")
        counts = _counts(x)
        notes = []
        if not x.has_hypergraph:
            notes.append("pre-hypergraph: the coordinator database has no hyperedges tables")
        return GateReport(results=results, counts=counts, expect_hypergraph=expect_hypergraph, expect_ranker=x.expect_ranker, notes=notes)
    finally:
        x.close()


def resolve_run_paths(run_dir: str | os.PathLike[str]) -> tuple[Path, Path]:
    """``(coord.db, holders dir)`` of a run directory: the manifest's ``coord_db`` / ``holders_dir`` when present, else the layout
    ``run.py`` writes (``<run>/data/coord.db``, ``<run>/data/holders``), else ``<run>/coord.db`` / ``<run>/holders``."""
    run = Path(run_dir)
    manifest: dict[str, Any] = {}
    with contextlib.suppress(ValueError, OSError):
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    coord = Path(manifest["coord_db"]) if manifest.get("coord_db") else next((p for p in (run / "data" / "coord.db", run / "coord.db") if p.exists()), run / "data" / "coord.db")
    holders = Path(manifest["holders_dir"]) if manifest.get("holders_dir") else next((p for p in (run / "data" / "holders", run / "holders") if p.exists()), run / "data" / "holders")
    return coord, holders


def main(argv: Iterable[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Architecture-coverage gate G1-G10")
    ap.add_argument("run_dir")
    ap.add_argument("--coord-db")
    ap.add_argument("--holders-dir")
    ap.add_argument("--no-hypergraph", action="store_true", help="the run is pre-hypergraph or an ablation that removes it")
    ap.add_argument("--no-ranker", action="store_true")
    ap.add_argument("--no-term-index", action="store_true", help="the term index is not expected (pre-0007 schema or an ablation that removes it)")
    ap.add_argument("--write", action="store_true", help="write arch_gate.json into the run directory")
    ns = ap.parse_args(list(argv) if argv is not None else None)
    run = Path(ns.run_dir)
    d_coord, d_holders = resolve_run_paths(run)
    coord = ns.coord_db or str(d_coord)
    holders = ns.holders_dir or str(d_holders)
    rep = check(run, coord_db=coord, holders_dir=holders, expect_hypergraph=not ns.no_hypergraph, expect_ranker=False if ns.no_ranker else None,
                    expect_term_index=False if ns.no_term_index else None)
    print(rep.summary())
    print(json.dumps(rep.counts, indent=2, default=str))
    if ns.write:
        (run / "arch_gate.json").write_text(json.dumps(rep.to_dict(), indent=2, default=str))
    return 0 if rep.valid else 2


if __name__ == "__main__":      # pragma: no cover
    raise SystemExit(main())
