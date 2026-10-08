"""T0 evidence packets inside a site's boundary (G7): assemble a read-only packet for one follow-up and let only a
bucketed, suppressed, structured summary cross.

Built ahead of E2 and X4 (STRATEGY sections 5.5 and 7): approval-routed follow-up is unvalidated; nothing here
measures it.

A packet request (``edge/egress.py``: the follow-up key, the question id, the candidate key, the question's window and
an ``as_of``) comes in through the site's Boundary; :meth:`PacketAssembler.handle` answers it from the verdict the site
stored for that question (``edge/verify.py``) and sends the ``packet`` artifact out through the same Boundary. Rules,
in the order :meth:`PacketAssembler.handle` applies them (all under one lock per assembler, on the calling thread,
with a :class:`~.records.RecordStore` opened for the call and closed after it, so no SQLite object crosses threads):

1. ``boundary.accept``: the request must pass the closed spec and end at or before the site clock's closed week;
2. no stored verdict for the question: status ``no_verdict``, every bucket, reference and the verdict null, no code
   and no co-mention, ``truncated`` false;
3. a stored verdict whose window is not the request's, or whose question log rows do not name the candidate key's
   entity, refuses the request (:class:`PacketError`; nothing is sent);
4. a refute or an unknown: status ``no_confirmed_records`` with the verdict, its buckets (all null for a refute or an
   unknown), its ``evidence_ref`` and its ``truncated`` flag copied; no code and no co-mention;
5. a confirm: status ``ok`` with the verdict's three buckets, reference and flag, and, over the confirming records
   only (the site's own records in the window whose refs the verdict log holds):

   * **codes** (:func:`code_distribution`): per pack code (unknown codes ignored) the number of records carrying it
     (one record counts a code once), sent as ``bucket_of(n)`` when ``n >= k`` and ``'suppressed'`` otherwise; when
     exactly one code is suppressed among two or more, the unsuppressed code with the smallest ``(n, code)`` is
     suppressed too (complementary suppression), so a suppressed count can never be recovered by subtraction from
     the support bucket and the others;
   * **co-mentions** (:func:`co_mentions`): the ``(entity type, id)`` pairs named in the records' STRUCTURED entity
     fields of an egress type, each value resolved exactly (``Canonicaliser.resolve_exact``), the key's own entity
     excluded, and an id of a type with an id format kept only when it is in the site's master data (whatever the
     pack's ``require_master_data`` says); a pair is listed only when at least k records name it, as a bucket label,
     and a pair below k is omitted entirely (listing it would disclose it). The narrative is never read, so text in a
     record (an injection included) cannot reach a packet;
6. the body is validated by the Boundary, then the FULL packet (the crossing body plus each confirming record's ref,
   week, codes, structured fields and narrative; never persons, never the reporter) is written atomically to
   ``<workdir>/packets/site-<id>/<sha256(followup key)[:16]>.json`` (the site's own directory, so sites that share a
   work directory never collide), never overwriting an existing file; it never leaves the site;
7. the body is sent; an identical body already sent is a Boundary no-op.

Nothing here imports ``followup``, ``pushdown``, ``detect`` or ``inference``. Deterministic: the clock is injected
(it stamps only the local file's ``created_at``), nothing draws randomness, and every iteration is sorted.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, Sequence

from ..jsonio import canonical_bytes, sha256_hex, strict_load
from .egress import PACKET_SUPPRESSED, SCHEMA_VERSION, bucket_of
from .records import RecordStore, WindowRecord
from .weeks import TS_RE, local_date

if TYPE_CHECKING:
    from ..packs.canonical import Canonicaliser
    from ..packs.loader import FrozenPack
    from .site import EdgeSite

LOCAL_LABEL = "site-local evidence packet; it never leaves the site"
PACKETS_DIR = "packets"


class PacketError(ValueError):
    """A request the site refuses after taking it in; the text is fixed and never holds a value."""


def code_distribution(pack: "FrozenPack", records: Iterable[WindowRecord]) -> list[dict[str, Any]]:
    """``[{code, n}]`` sorted by code: per pack code, the records carrying it as a bucket label, or ``'suppressed'``
    below k, with complementary suppression. Pure."""
    k = pack.egress.k
    counts: dict[str, int] = {}
    for record in records:
        for code in sorted(set(record.codes)):
            if code in pack.codes:
                counts[code] = counts.get(code, 0) + 1
    suppressed = {code for code, n in counts.items() if n < k}
    if len(suppressed) == 1 and len(counts) >= 2:
        smallest = min((n, code) for code, n in counts.items() if code not in suppressed)
        suppressed.add(smallest[1])
    return [{"code": code, "n": PACKET_SUPPRESSED if code in suppressed else bucket_of(counts[code], pack)}
            for code in sorted(counts)]


def co_mentions(pack: "FrozenPack", canonicaliser: "Canonicaliser", master: Mapping[str, Iterable[str]],
                records: Iterable[WindowRecord], exclude: tuple[str, str]) -> list[dict[str, Any]]:
    """``[{entity_type, entity_id, n}]`` sorted by (type, id): the pairs at least k records name in their structured
    entity fields (resolved exactly; master data required for types with an id format), as bucket labels. Pure; the
    narrative is never read."""
    k = pack.egress.k
    known = {t: frozenset(master.get(t, ())) for t in pack.egress.egress_entity_types}
    counts: dict[tuple[str, str], int] = {}
    for record in records:
        named: set[tuple[str, str]] = set()
        for entity_type in sorted(pack.egress.egress_entity_types):
            for value in record.structured.get(entity_type, ()):
                mention = canonicaliser.resolve_exact(entity_type, value)
                if mention is None:
                    continue
                pair = (entity_type, mention.entity_id)
                if pair == tuple(exclude):
                    continue
                if pack.entity_types[entity_type].id_format is not None and pair[1] not in known[entity_type]:
                    continue
                named.add(pair)
        for pair in sorted(named):
            counts[pair] = counts.get(pair, 0) + 1
    return [{"entity_type": t, "entity_id": eid, "n": bucket_of(counts[(t, eid)], pack)}
            for t, eid in sorted(counts) if counts[(t, eid)] >= k]


def _local_record(record: WindowRecord) -> dict[str, Any]:
    return {"record_ref": record.record_ref, "iso_week": record.iso_week, "codes": sorted(record.codes),
            "structured": {name: list(record.structured[name]) for name in sorted(record.structured)},
            "narrative": record.narrative}


def _write_once(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (a temporary file, then a hard link) unless ``path`` exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        os.link(tmp, path)
    except FileExistsError:
        pass
    finally:
        tmp.unlink()


class PacketAssembler:
    """Answers packet requests for one :class:`~.site.EdgeSite`; ``handle`` is the site's handler for HQ's packet
    executor."""

    def __init__(self, site: "EdgeSite", *, clock: Callable[[], str]) -> None:
        self.site = site
        self.pack = site.pack
        self._clock = clock
        self._lock = threading.Lock()

    def _now(self) -> str:
        ts = self._clock()
        if not isinstance(ts, str) or TS_RE.fullmatch(ts) is None or local_date(ts) is None:
            raise ValueError("the clock must return an ISO 8601 timestamp") from None
        return ts

    def _store(self) -> RecordStore:
        """A new connection on the site's store, opened in the calling thread."""
        return RecordStore(self.site.store.path, site_id=self.site.site_id, pack_id=self.pack.id,
                           config_hash=self.pack.config_hash)

    def local_path(self, followup_key: str) -> Path:
        """Where the full packet for ``followup_key`` is kept, inside the site."""
        return (self.site.store.path.parent / PACKETS_DIR / f"site-{self.site.site_id}"
                / f"{sha256_hex(followup_key)[:16]}.json")

    def handle(self, request: Any) -> dict[str, Any]:
        """The parsed packet body that crossed the Boundary. An :class:`~.egress.EgressError` from the Boundary and a
        :class:`PacketError` propagate; neither sends anything."""
        with self._lock:
            parsed = self.site.boundary.accept("in", "packet_request", request)
            store = self._store()
            try:
                body, records = self._assemble(store, parsed)
            finally:
                store.close()
            body = self.site.boundary.validate("out", "packet", body)
            local = {"label": LOCAL_LABEL, "crossing": body, "records": [_local_record(r) for r in records],
                     "created_at": self._now()}
            _write_once(self.local_path(parsed["followup_key"]), canonical_bytes(local))
            return self.site.boundary.send("out", "packet", body)

    def _assemble(self, store: RecordStore,
                  request: Mapping[str, Any]) -> tuple[dict[str, Any], Sequence[WindowRecord]]:
        qid, window = request["question_id"], request["window"]
        entity_type, entity_id, _ = request["candidate_key"].split(":")
        body: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION, "pack": self.pack.id, "pack_hash": self.pack.config_hash,
            "site": self.site.site_id, "followup_key": request["followup_key"], "question_id": qid,
            "candidate_key": request["candidate_key"], "window": dict(window), "status": "no_verdict",
            "verdict": None, "support_bucket": None, "roots_bucket": None, "reporters_bucket": None,
            "evidence_ref": None, "truncated": False, "codes": [], "co_mentions": []}
        row = store.verdict_for_question(qid)
        if row is None:
            return body, ()
        verdict = strict_load(row.body)
        logged = [(q.entity_type, q.entity_id) for q in store.question_log() if q.question_id == qid]
        if verdict["window"] != window or not logged or any(pair != (entity_type, entity_id) for pair in logged):
            raise PacketError("the request does not match this site's verdict") from None
        body.update({"verdict": verdict["verdict"], "support_bucket": verdict["support_bucket"],
                     "roots_bucket": verdict["roots_bucket"], "reporters_bucket": verdict["reporters_bucket"],
                     "evidence_ref": verdict["evidence_ref"], "truncated": verdict["truncated"]})
        if verdict["verdict"] != "confirm":
            body["status"] = "no_confirmed_records"
            return body, ()
        confirming = frozenset(row.confirming_refs)
        records = [r for r in store.window_records(window["start_week"], window["end_week"])
                   if r.record_ref in confirming]
        body.update({"status": "ok", "codes": code_distribution(self.pack, records),
                     "co_mentions": co_mentions(self.pack, self.site.canonicaliser, self.site.master, records,
                                                (entity_type, entity_id))})
        return body, records
