"""EdgeSite: one site inside its boundary. Records and claims stay in the site's own store; only what the Boundary
accepts leaves.

Rules:

* **Ingest.** A record must be a dict with a readable received date (:func:`~.weeks.local_date`: a date, or a
  timestamp read in its own calendar), pass the connector's record check and belong to this site; otherwise it is
  counted under one of :data:`REJECT_REASONS` and never stored.
* **Extract.** Records without extraction are sensed in batches of :data:`EXTRACT_BATCH`, each saved in one
  transaction; the first extraction of a record wins, except a ``not_sent`` stand-in (below). A claim is stored only
  when its type, predicate, canonical id, channel and confidence are the pack's (:func:`valid_claim`); others are
  counted as ``invalid_claims``. A model sees a record only through the runtime, which is bound to this site. When
  the extraction route leaves the site under an exemption (a simulated endpoint, or an external one with
  ``allow_external_raw``), model extraction refuses to start unless every record it would send carries the
  exemption's label (:func:`~.records.exempt_records_problem`): all synthetic for ``synthetic``, this site the
  public source for ``public``.
* **The extraction breaker.** After :data:`~.extract.BREAKER_AFTER` consecutive records whose call found the
  route's primary endpoint down (timeout, network, 5xx, after the client's retries; a failure on the escalation
  endpoint after the primary answered does not count, :func:`~.extract.server_down`), the breaker opens: records
  are sensed lexically without a call (extractor ``fallback``, error ``not_sent``), so a dead server costs no
  deadline per record. While open it half-opens on a doubling schedule (in the 1st, 3rd, 7th, 15th, ... batch
  after it opened): that batch's first record is sent, and an answer closes the breaker, so a dead server costs a
  pass of n records about 2 + log2(n / EXTRACT_BATCH) deadlines, and a short outage at most about as many batches
  again as it lasted. A ``not_sent`` stand-in counts as extracted (the cells can be emitted), and every later model
  pass sends the record to the model again and replaces it, until its count week is emitted in a cells bundle,
  which is never revised (audit round 3: the stand-ins used to be final, so a 3-second blip, two records slower than
  the deadline, or a dead escalation server downgraded the whole backlog to lexical for good).
  :class:`ExtractSummary` counts them in ``resent``.
* **Cells.** :meth:`EdgeSite.emit_cells` sends one ``cells_bundle`` per newly closed span of weeks
  (:func:`~.weeks.closed_through` with the pack's ``close_lag_days``). Cells count the site's own records
  (forwarded-in records excluded) per (entity, predicate, week, channel): records, distinct roots and distinct
  reporters (every unknown reporter is one shared reporter). Each count is the int when it is at least k, else
  ``'<k'``; ``res_conf_min`` is sent only with an int ``n``. With ``require_master_data``, an id of a type with an id
  format must be in the site's master data. A bundle without cells is still sent: it advances the watermark.
* **Usage.** :meth:`EdgeSite.emit_usage` sends ``usage_summary`` over the extraction rows of the ledger in closed
  weeks not yet summarised, suppressed per field like the cells; tokens and latency only when ``calls`` is at least
  k. ``calls = ok + the error counts``, so with an int ``calls`` and any part below k, ``ok``, every error kind and
  both missing-token counts go as ``'suppressed'``, with no token sum and no latency (complementary suppression;
  :func:`_usage_group`); a missing-token count is otherwise exact only as a sum of whole parts, else
  ``'suppressed'`` without its token sum, so every row count HQ can work out is 0 or at least k (review of audit
  round 2). Each ledger row is summarised exactly once; the per-call ledger never leaves. The judge's rows are passed
  over and never leave: the judge makes one call per retrieved record, so its calls and tokens would count the
  records behind a verdict that the verdict's buckets hide (audit round 2).
* **Never revised.** An emission is stored (with its exact bytes) before it is sent and re-sent unchanged after a
  failed send; ``as_of`` may not move backwards or past the site clock.
* **Questions (G6).** The Boundary also takes questions in (``site-<id>.ingress.jsonl`` at the site, HQ's
  ``questions.jsonl`` beside the receive log); ``edge/verify.py``'s ``SiteVerifier`` answers them. Since G7 it also
  takes packet requests in (HQ's ``packet_requests.jsonl``), which ``edge/packets.py``'s ``PacketAssembler``
  answers. A usage summary names only the extraction task (the Boundary refuses a judge group).

Timestamps (``ingested_at``, ``extracted_at``, emission rows, log rows) come only from the injected clock.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, Sequence

from ..inference.ledger import read_ledger, summarise
from ..jsonio import canonical_bytes, sha256_hex, strict_load
from ..packs.canonical import Canonicaliser
from ..packs.connector import SITE_ID_RE, record_problems, valid_date
from .egress import CHANNELS, PACKET_SUPPRESSED, SCHEMA_VERSION, SUPPRESSED, Boundary
from .extract import (BREAKER_AFTER, FALLBACK_EXTRACTOR, NOT_SENT, TASK_NAME, Claim, LexicalExtractor,
                      ModelExtractor, extraction_task, sense)
from .records import EmissionRow, ExtractionRow, InputRow, RecordStore, exempt_records_problem
from .weeks import TS_RE, closed_through, iso_week, local_date

if TYPE_CHECKING:
    from ..inference.runtime import Runtime
    from ..packs.loader import FrozenPack

EXTRACT_MODES = ("lexical", "model")
EXTRACT_BATCH = 100
REJECT_REASONS = ("bad_record", "bad_date", "wrong_site")
CELL_STATS = ("cells", "cells_n_ge_k", "suppressed_fields", "not_master_data", "non_egress_type")
TOKEN_SUMS = ("tokens_in", "tokens_out")
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
    resent: int = 0              # records the breaker had not sent in an earlier pass, extracted again in this one


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


def in_master_data(pack: "FrozenPack", master: Mapping[str, Iterable[str]], entity_type: str, entity_id: str, *,
                   require: bool | None = None) -> bool:
    """With ``require`` (default: the pack's ``require_master_data``), an id of a type with an id format must be in
    ``master``; alias-only types are closed pack vocabularies and always pass, and a type absent from ``master``
    passes none of its ids. The cells (:func:`build_cells`) and the site judge (``edge/verify.py``) both apply it."""
    require = pack.egress.require_master_data if require is None else require
    return not require or pack.entity_types[entity_type].ids is not None or entity_id in master.get(entity_type, ())


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
        if not in_master_data(pack, master, row.entity_type, row.entity_id, require=require):
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


def _usage_group(rows: Sequence[Mapping[str, Any]], k: int) -> dict[str, Any]:
    """One (task, endpoint) group of ledger rows, summarised as it leaves. Its parts are ``ok`` and each error kind,
    and ``calls`` is their sum, so when ``calls`` is an int and any part is below k (and not 0), every part goes as
    ``'suppressed'`` (audit round 2), and so do both missing-token counts, with no token sum and no latency (its
    review): ``calls`` minus the others would give the ``'<k'`` part; a missing-token count is the transport
    failures, which carry no tokens; a known prompt size divides a token sum into rows; and a failure's latency at the
    deadline moves the percentiles. Otherwise a missing-token count is exact only when within each part every row or
    none lacks the tokens, so that it is a sum of whole parts (each 0 or at least k); else it goes as
    ``'suppressed'`` without its token sum."""
    (group,) = summarise(rows)["groups"]
    out = {"task": group["task"], "endpoint": group["endpoint"], "calls": _sup(group["calls"], k),
           "ok": _sup0(group["ok"], k), "errors": {kind: _sup(n, k) for kind, n in sorted(group["errors"].items())},
           "fake": group["fake"]}
    if group["calls"] < k:
        return {**out, **{f"{field}_missing": _sup0(group[f"{field}_missing"], k) for field in TOKEN_SUMS}}
    if SUPPRESSED in (out["ok"], *out["errors"].values()):
        return {**out, "ok": PACKET_SUPPRESSED, "errors": dict.fromkeys(out["errors"], PACKET_SUPPRESSED),
                **{f"{field}_missing": PACKET_SUPPRESSED for field in TOKEN_SUMS}}
    for field in TOKEN_SUMS:
        lacking: dict[str, set[bool]] = {}
        for row in rows:
            lacking.setdefault("ok" if row["ok"] else row["error_kind"], set()).add(row[field] is None)
        if all(len(seen) == 1 for seen in lacking.values()):
            out[f"{field}_missing"], out[field] = group[f"{field}_missing"], group[field]
        else:
            out[f"{field}_missing"] = PACKET_SUPPRESSED
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
                                 tasks=(TASK_NAME,) if runtime is not None else (),
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
        redo = NOT_SENT if mode == "model" else None       # a model pass also sends what the breaker held back
        if mode == "model":
            if self.runtime is None:
                raise SiteError("model extraction needs a runtime") from None
            problem = exempt_records_problem(self.runtime.exemption(extraction_task(self.pack)), self.site_id,
                                             self.store.pending_non_synthetic(redo))
            if problem is not None:
                raise SiteError(problem) from None
            extractor: LexicalExtractor | ModelExtractor = ModelExtractor(self.pack, self.canonicaliser,
                                                                         self.runtime, fallback=True)
        else:
            extractor = LexicalExtractor(self.pack, self.canonicaliser)
        lexical = LexicalExtractor(self.pack, self.canonicaliser)
        down = 0                    # consecutive records whose call found the primary endpoint down
        gap = wait = 0              # while open: batches between probes (doubling) and batches left to the next
        cursor = 0
        n_records = n_claims = invalid = resent = 0
        extractors: dict[str, int] = {}
        errors: dict[str, int] = {}
        drops: dict[str, int] = {}
        while True:
            batch = self.store.unextracted(EXTRACT_BATCH, after_seq=cursor, redo_kind=redo)
            if not batch:
                break
            cursor = batch[-1][0]
            probe = down >= BREAKER_AFTER and wait == 0
            if down >= BREAKER_AFTER and wait > 0:
                wait -= 1
            rows = []
            for i, (seq, record) in enumerate(batch):
                probing = probe and i == 0
                if down >= BREAKER_AFTER and not probing:    # open: no call, no deadline
                    codes, text, claims = sense(record, self.pack, self.canonicaliser, lexical, ref=f"x:{seq}")
                    text = replace(text, extractor=FALLBACK_EXTRACTOR, error_kind=NOT_SENT)
                else:
                    codes, text, claims = sense(record, self.pack, self.canonicaliser, extractor, ref=f"x:{seq}")
                    if not text.server_down:
                        down = 0                             # an answer (valid or not) closes it
                    else:
                        down += 1
                        if probing:                          # a failed probe: twice as many batches to the next
                            gap *= 2
                            wait = gap - 1
                        elif down == BREAKER_AFTER:          # it opens: the next batch sends one probe
                            gap, wait = 1, 0
                kept = tuple(c for c in claims if valid_claim(c, self.pack, self.canonicaliser))
                rows.append(ExtractionRow(
                    record_ref=record["record_ref"], mode=mode, extractor=text.extractor, error_kind=text.error_kind,
                    truncated=text.truncated, language_supported=text.language_supported, drops=dict(text.drops),
                    unresolved=dict(text.unresolved), unknown_codes=codes.unknown_codes,
                    structured_unresolved=codes.structured_unresolved, invalid_claims=len(claims) - len(kept),
                    extracted_at=self._now(), claims=kept))
            resent += self.store.save_extractions(rows, redo_kind=redo)
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
                              drops=MappingProxyType(dict(sorted(drops.items()))), invalid_claims=invalid,
                              resent=resent)

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
        by_endpoint: dict[str, list[dict[str, Any]]] = {}
        for row in prefix:
            if row["task"] == TASK_NAME:
                by_endpoint.setdefault(row["endpoint"], []).append(row)
        groups = [_usage_group(by_endpoint[endpoint], k) for endpoint in sorted(by_endpoint)]
        return self._emit("usage_summary", as_of, after, through, {"groups": groups},
                          ledger_rows=start + len(prefix), items=len(groups), stats={"ledger_rows": len(prefix)})
