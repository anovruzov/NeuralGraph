"""EdgeSite: one site inside its boundary. Records and claims stay in the site's own store; only what the Boundary
accepts leaves.

Rules:

* **Ingest.** A record must be a dict with a readable received date (:func:`~.weeks.local_date`: a date, or a
  timestamp read in its own calendar), pass the connector's record check and belong to this site; otherwise it is
  counted under one of :data:`REJECT_REASONS` and never stored.
* **Extract.** Records without extraction are sensed in batches of :data:`EXTRACT_BATCH`, each saved in one
  transaction; the first extraction of a record wins. A claim is stored only when its type, predicate, canonical
  id, channel and confidence are the pack's (:func:`valid_claim`); others are counted as ``invalid_claims``. A model
  sees a record only through the runtime, which is bound to this site; a simulated runtime refuses to start on
  records that are not synthetic.
* **Cells.** :meth:`EdgeSite.emit_cells` sends one ``cells_bundle`` per newly closed span of weeks
  (:func:`~.weeks.closed_through` with the pack's ``close_lag_days``). Cells count the site's own records
  (forwarded-in records excluded) per (entity, predicate, week, channel): records, distinct roots and distinct
  reporters (every unknown reporter is one shared reporter). Each count is the int when it is at least k, else
  ``'<k'``; ``res_conf_min`` is sent only with an int ``n``. With ``require_master_data``, an id of a type with an id
  format must be in the site's master data. A bundle without cells is still sent: it advances the watermark.
* **Usage.** :meth:`EdgeSite.emit_usage` sends ``usage_summary`` over the ledger rows of closed weeks not yet
  summarised, suppressed per field like the cells; tokens and latency only when ``calls`` is at least k. Each
  ledger row is summarised exactly once; the per-call ledger never leaves.
* **Never revised.** An emission is stored (with its exact bytes) before it is sent and re-sent unchanged after a
  failed send; ``as_of`` may not move backwards or past the site clock.
* **Questions (G6).** The Boundary also takes questions in (``site-<id>.ingress.jsonl`` at the site, HQ's
  ``questions.jsonl`` beside the receive log); ``edge/verify.py``'s ``SiteVerifier`` answers them. Since G7 it also
  takes packet requests in (HQ's ``packet_requests.jsonl``), which ``edge/packets.py``'s ``PacketAssembler``
  answers. With a runtime, a usage summary may name the judge task as well as extraction, so judge rows in the site's
  ledger can be summarised.

Timestamps (``ingested_at``, ``extracted_at``, emission rows, log rows) come only from the injected clock.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping

from ..inference.ledger import read_ledger, summarise
from ..jsonio import canonical_bytes, sha256_hex, strict_load
from ..packs.canonical import Canonicaliser
from ..packs.connector import SITE_ID_RE, record_problems, valid_date
from .egress import CHANNELS, SCHEMA_VERSION, SUPPRESSED, Boundary
from .extract import TASK_NAME, Claim, LexicalExtractor, ModelExtractor, sense
from .records import EmissionRow, ExtractionRow, InputRow, RecordStore
from .verify import JUDGE_TASK
from .weeks import TS_RE, closed_through, iso_week, local_date

if TYPE_CHECKING:
    from ..inference.runtime import Runtime
    from ..packs.loader import FrozenPack

EXTRACT_MODES = ("lexical", "model")
EXTRACT_BATCH = 100
REJECT_REASONS = ("bad_record", "bad_date", "wrong_site")
CELL_STATS = ("cells", "cells_n_ge_k", "suppressed_fields", "not_master_data", "non_egress_type")
_UNKNOWN_REPORTER = object()


class SiteError(ValueError):
    pass


@dataclass(frozen=True)
class IngestResult:
    ingested: int
    duplicates: int
    rejected: Mapping[str, int]
    late: int
    forwarded_in: int


@dataclass(frozen=True)
class ExtractSummary:
    records: int
    claims: int
    extractors: Mapping[str, int]
    errors: Mapping[str, int]
    drops: Mapping[str, int]
    invalid_claims: int


@dataclass(frozen=True)
class Emission:
    """What one emission sent: ``body`` is a private parsed copy of the bytes sent; ``stats`` stay at the site."""

    artifact_type: str
    as_of: str
    after: str | None
    closed_through: str
    sha256: str
    bytes: int
    body: dict[str, Any]
    stats: Mapping[str, int]


def valid_claim(claim: Claim, pack: "FrozenPack", canonicaliser: Canonicaliser) -> bool:
    """A claim the store may hold: a pack type and predicate, a canonical id, a known channel and one of the pack's
    three confidences (so every stored ``res_conf`` is a value the Boundary accepts)."""
    conf = pack.extraction
    return (claim.entity_type in pack.entity_types and claim.predicate in pack.predicates
            and canonicaliser.is_canonical(claim.entity_type, claim.entity_id) and claim.channel in CHANNELS
            and isinstance(claim.res_conf, (int, float)) and not isinstance(claim.res_conf, bool)
            and claim.res_conf in (conf.confidence_exact, conf.confidence_alias, conf.confidence_variant))


def _sup(value: int, k: int) -> int | str:
    return value if value >= k else SUPPRESSED


def _sup0(value: int, k: int) -> int | str:
    return 0 if value == 0 else _sup(value, k)


def build_cells(rows: Iterable[InputRow], pack: "FrozenPack", *, master: Mapping[str, Iterable[str]],
                require_master_data: bool | None = None,
                k: int | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Count cells from emission input rows. Pure. Rows of a type that may not leave are counted as
    ``non_egress_type``; with ``require_master_data`` (default: the pack's), an id of a type with an id format that
    is not in ``master`` is counted as ``not_master_data`` (alias-only types are closed pack vocabularies and always
    pass; a type absent from ``master`` drops all its ids). Each record, root and reporter counts once per cell.

    ``k`` (default: the pack's) is the suppression threshold and the ``res_conf_min`` presence rule. k=1 counts
    without suppression; only the evaluation harness passes it, and nothing it builds goes through the Boundary."""
    if k is None:
        k = pack.egress.k
    elif isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("k must be an int >= 1") from None
    require = pack.egress.require_master_data if require_master_data is None else require_master_data
    egress_types = frozenset(pack.egress.egress_entity_types)
    stats = dict.fromkeys(CELL_STATS, 0)
    acc: dict[tuple[str, str, str, str, str], tuple[set[str], set[str], set[Any], list[float]]] = {}
    for row in rows:
        if row.entity_type not in egress_types:
            stats["non_egress_type"] += 1
            continue
        if require and pack.entity_types[row.entity_type].ids is None \
                and row.entity_id not in master.get(row.entity_type, ()):
            stats["not_master_data"] += 1
            continue
        key = (row.entity_type, row.entity_id, row.predicate, row.count_week, row.channel)
        records, roots, reporters, conf = acc.setdefault(key, (set(), set(), set(), []))
        records.add(row.record_ref)
        roots.add(row.root_ref)
        reporters.add(row.reporter_id if row.reporter_id is not None else _UNKNOWN_REPORTER)
        conf.append(row.res_conf)
    cells = []
    for key in sorted(acc):
        records, roots, reporters, conf = acc[key]
        counts = {"n": len(records), "n_roots": len(roots), "n_reporters": len(reporters)}
        cell: dict[str, Any] = {"entity_type": key[0], "entity_id": key[1], "predicate": key[2], "iso_week": key[3],
                                "channel": key[4], **{f: _sup(v, k) for f, v in counts.items()}}
        if counts["n"] >= k:
            cell["res_conf_min"] = min(conf)
            stats["cells_n_ge_k"] += 1
        stats["suppressed_fields"] += sum(1 for v in counts.values() if v < k)
        cells.append(cell)
    stats["cells"] = len(cells)
    return cells, stats


def _usage_group(group: Mapping[str, Any], k: int) -> dict[str, Any]:
    out = {"task": group["task"], "endpoint": group["endpoint"], "calls": _sup(group["calls"], k),
           "ok": _sup0(group["ok"], k), "tokens_in_missing": _sup0(group["tokens_in_missing"], k),
           "tokens_out_missing": _sup0(group["tokens_out_missing"], k),
           "errors": {kind: _sup(n, k) for kind, n in sorted(group["errors"].items())}, "fake": group["fake"]}
    if group["calls"] >= k:
        out["tokens_in"] = group["tokens_in"]
        out["tokens_out"] = group["tokens_out"]
        for key in ("latency_ms_p50", "latency_ms_p95"):
            if group[key] is not None:
                out[key] = group[key]
    return out


class EdgeSite:
    """One site: its record store, its Boundary and its canonicaliser. ``master_data`` maps entity types to the
    site's known ids; ``hq_dir`` is where the simulated HQ receive log lives. One EdgeSite per site per process; the
    caller owns (and closes) the runtime."""

    def __init__(self, pack: "FrozenPack", site_id: str, workdir: str | Path, *, runtime: "Runtime | None",
                 clock: Callable[[], str], master_data: Mapping[str, Iterable[str]], hq_dir: str | Path) -> None:
        if not isinstance(site_id, str) or SITE_ID_RE.fullmatch(site_id) is None:
            raise SiteError("site_id must match [a-z0-9][a-z0-9_.-]{0,63}") from None
        if runtime is not None and runtime.boundary != f"site:{site_id}":
            raise SiteError("the runtime is bound to another boundary") from None
        canonicaliser = None
        try:
            canonicaliser = Canonicaliser(pack, known=master_data)
        except ValueError:
            canonicaliser = None
        if canonicaliser is None:
            raise SiteError("master data holds an unknown entity type or a non-canonical id") from None
        self.pack = pack
        self.site_id = site_id
        self.runtime = runtime
        self._clock = clock
        self.canonicaliser = canonicaliser
        self.master: Mapping[str, frozenset[str]] = MappingProxyType(
            {t: frozenset(master_data[t]) for t in sorted(master_data)})
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        self.boundary = Boundary(pack, site_id, egress_log=workdir / f"site-{site_id}.egress.jsonl",
                                 receive_log=Path(hq_dir) / "receive.jsonl", clock=clock,
                                 tasks=(TASK_NAME, JUDGE_TASK) if runtime is not None else (),
                                 endpoints=tuple(sorted(runtime.config.endpoints)) if runtime is not None else (),
                                 ingress_log=workdir / f"site-{site_id}.ingress.jsonl",
                                 question_log=Path(hq_dir) / "questions.jsonl",
                                 packet_request_log=Path(hq_dir) / "packet_requests.jsonl")
        self.store = RecordStore(workdir / f"site-{site_id}.sqlite3", site_id=site_id, pack_id=pack.id,
                                 config_hash=pack.config_hash)

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "EdgeSite":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _now(self) -> str:
        ts = self._clock()
        if not isinstance(ts, str) or TS_RE.fullmatch(ts) is None or local_date(ts) is None:
            raise ValueError("the clock must return an ISO 8601 timestamp") from None
        return ts

    # ------------------------------------------------------------------ ingest
    def ingest(self, records: Iterable[Any]) -> IngestResult:
        ingested_at = self._now()
        rejected = dict.fromkeys(REJECT_REASONS, 0)
        accepted = []
        for record in records:
            if not isinstance(record, dict):
                rejected["bad_record"] += 1
                continue
            day = local_date(record.get("received_date"))
            if day is None:
                rejected["bad_date"] += 1
                continue
            rec = {**record, "received_date": day}
            if record_problems(rec, self.pack):
                rejected["bad_record"] += 1
            elif rec["site"] != self.site_id:
                rejected["wrong_site"] += 1
            else:
                accepted.append(rec)
        stored = self.store.ingest(accepted, ingested_at=ingested_at)
        return IngestResult(ingested=stored.ingested, duplicates=stored.duplicates,
                            rejected=MappingProxyType(rejected), late=stored.late, forwarded_in=stored.forwarded_in)

    # ------------------------------------------------------------------ extract
    def extract(self, mode: str) -> ExtractSummary:
        if mode not in EXTRACT_MODES:
            raise SiteError("unknown extraction mode") from None
        if mode == "model":
            if self.runtime is None:
                raise SiteError("model extraction needs a runtime") from None
            if self.runtime.simulation and self.store.pending_non_synthetic():
                raise SiteError("a simulated runtime only reads synthetic records") from None
            extractor: LexicalExtractor | ModelExtractor = ModelExtractor(self.pack, self.canonicaliser,
                                                                         self.runtime, fallback=True)
        else:
            extractor = LexicalExtractor(self.pack, self.canonicaliser)
        n_records = n_claims = invalid = 0
        extractors: dict[str, int] = {}
        errors: dict[str, int] = {}
        drops: dict[str, int] = {}
        while True:
            batch = self.store.unextracted(EXTRACT_BATCH)
            if not batch:
                break
            rows = []
            for seq, record in batch:
                codes, text, claims = sense(record, self.pack, self.canonicaliser, extractor, ref=f"x:{seq}")
                kept = tuple(c for c in claims if valid_claim(c, self.pack, self.canonicaliser))
                rows.append(ExtractionRow(
                    record_ref=record["record_ref"], mode=mode, extractor=text.extractor, error_kind=text.error_kind,
                    truncated=text.truncated, language_supported=text.language_supported, drops=dict(text.drops),
                    unresolved=dict(text.unresolved), unknown_codes=codes.unknown_codes,
                    structured_unresolved=codes.structured_unresolved, invalid_claims=len(claims) - len(kept),
                    extracted_at=self._now(), claims=kept))
            self.store.save_extractions(rows)
            for r in rows:
                n_records += 1
                n_claims += len(r.claims)
                invalid += r.invalid_claims
                extractors[r.extractor] = extractors.get(r.extractor, 0) + 1
                if r.error_kind is not None:
                    errors[r.error_kind] = errors.get(r.error_kind, 0) + 1
                for reason, n in r.drops.items():
                    drops[reason] = drops.get(reason, 0) + n
        return ExtractSummary(records=n_records, claims=n_claims,
                              extractors=MappingProxyType(dict(sorted(extractors.items()))),
                              errors=MappingProxyType(dict(sorted(errors.items()))),
                              drops=MappingProxyType(dict(sorted(drops.items()))), invalid_claims=invalid)

    # ------------------------------------------------------------------ emit
    def _open_window(self, artifact_type: str, as_of: Any) -> tuple[str | None, str, EmissionRow | None] | None:
        """Checks ``as_of``, re-sends what a failed send left unsent, and returns ``(after, through, last)`` or None
        when no new week has closed."""
        last = self.store.last_emission(artifact_type)
        today = local_date(self._now())
        if not valid_date(as_of) or as_of > today:
            raise SiteError("as_of must be a calendar date YYYY-MM-DD, not after the site clock") from None
        if last is not None and as_of < last.as_of:
            raise SiteError("as_of is before the last emission") from None
        for row in self.store.unsent(artifact_type):
            self.boundary.send("out", artifact_type, strict_load(row.body))
            self.store.mark_sent(artifact_type, row.closed_through, self._now())
        through = closed_through(as_of, self.pack.egress.close_lag_days)
        after = last.closed_through if last is not None else None
        if after is not None and through <= after:
            return None
        return after, through, last

    def _emit(self, artifact_type: str, as_of: str, after: str | None, through: str, payload: dict[str, Any], *,
              ledger_rows: int | None, items: int, stats: Mapping[str, int]) -> Emission:
        body = {"schema_version": SCHEMA_VERSION, "pack": self.pack.id, "config_hash": self.pack.config_hash,
                "site": self.site_id, "as_of": as_of, "after": after, "closed_through": through,
                "k": self.pack.egress.k, **payload}
        parsed = self.boundary.validate("out", artifact_type, body)
        data = canonical_bytes(parsed)
        sha = sha256_hex(data)
        self.store.add_emission(EmissionRow(artifact_type=artifact_type, closed_through=through, as_of=as_of,
                                            after_week=after, ledger_rows=ledger_rows, body=data, sha256=sha,
                                            cells=items, created_at=self._now(), sent_at=None))
        self.boundary.send("out", artifact_type, parsed)
        self.store.mark_sent(artifact_type, through, self._now())
        return Emission(artifact_type=artifact_type, as_of=as_of, after=after, closed_through=through, sha256=sha,
                        bytes=len(data), body=strict_load(data), stats=MappingProxyType(dict(stats)))

    def emit_cells(self, as_of: str) -> Emission | None:
        window = self._open_window("cells_bundle", as_of)
        if window is None:
            return None
        after, through, _ = window
        pending = self.store.pending_through(through)
        if pending:
            raise SiteError(f"{pending} records in the window are not extracted yet") from None
        cells, stats = build_cells(self.store.emission_inputs(after, through), self.pack, master=self.master)
        stats.update(self.store.window_stats(after, through))
        return self._emit("cells_bundle", as_of, after, through, {"cells": cells}, ledger_rows=None,
                          items=len(cells), stats=stats)

    def emit_usage(self, as_of: str) -> Emission | None:
        if self.runtime is None:
            return None
        window = self._open_window("usage_summary", as_of)
        if window is None:
            return None
        after, through, last = window
        rows = read_ledger(self.runtime.ledger.path)
        boundary = f"site:{self.site_id}"
        if any(row["boundary"] != boundary for row in rows):
            raise SiteError("the ledger holds a row of another boundary") from None
        start = last.ledger_rows if last is not None and last.ledger_rows is not None else 0
        prefix: list[dict[str, Any]] = []
        for row in rows[start:]:
            day = local_date(row["ts"])
            if day is None:
                raise SiteError("a ledger row has no readable timestamp") from None
            if iso_week(day) > through:
                break
            prefix.append(row)
        if not prefix:
            return None
        k = self.pack.egress.k
        groups = [_usage_group(g, k) for g in summarise(prefix)["groups"]]
        return self._emit("usage_summary", as_of, after, through, {"groups": groups},
                          ledger_rows=start + len(prefix), items=len(groups), stats={"ledger_rows": len(prefix)})
