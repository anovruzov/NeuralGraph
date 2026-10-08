"""Pushdown verification inside a site's boundary (G6): answer one narrow question from the site's own raw records.

A question (``edge/egress.py``: the params ``{entity_type, entity_id, predicate}``, a window of closed ISO weeks)
comes in through the site's Boundary; :meth:`SiteVerifier.answer` answers it with the site's in-boundary model, or
with :func:`lexical_judge` when no runtime is configured, and the verdict goes out through the same Boundary. What
leaves is a verdict (``confirm``, ``refute`` or ``unknown``), count buckets, the newest confirming week and one opaque
``evidence_ref``; no text, no exact count and no per-record handle.

Rules, in the order :meth:`SiteVerifier.answer` applies them (all under one lock per verifier, on the calling
thread, with a :class:`~.records.RecordStore` opened for the call and closed after it, so no SQLite object crosses
threads; the orchestrator calls ``answer`` on worker threads):

1. ``boundary.accept``: the question must pass the closed spec and end at or before the site clock's closed week;
2. no secret (a missing secret file) gives ``unknown`` with the wire reason ``no_secret`` before anything is read;
3. a question answered before re-sends its stored bytes (an identical send is a Boundary no-op) and uses no budget;
4. the daily budgets, both ``unknown`` with the wire reason ``budget`` (not stored, so a later day answers): when
   ``question_budget_per_entity_per_day`` distinct questions about the entity were answered on the site-clock day,
   or when the question names an entity not yet answered that day and ``question_entities_per_site_per_day``
   distinct entities were (a cap on guessing many ids, audit round 2);
5. master data: with the pack's ``require_master_data``, an id that is not the site's master data (the rule the
   cells apply, ``site.in_master_data``) gets ``unknown`` without a single record read, stored like any answer (its
   local reason ``not_master_data``), so a question cannot test whether an id the cells would withhold, such as
   id-shaped person data, is in the site's narratives (audit round 2);
6. retrieval (:func:`retrieve`): the union of the site's own records (forwarded-in excluded) received in the window
   that hold any stored claim on the entity, whose structured values of the type resolve to it, or whose narrative
   names it (the canonicaliser's scan) other than as one of the record's person values or its reporter (the
   extractor's ``person_value`` rule; audit round 2); newest first, cut at ``verify_max_records`` (``truncated``);
7. the judge, per record: ``{mentions_entity, describes_predicate}``, each ``yes``, ``no`` or ``unclear``. The
   payload (:func:`judge_payload`) holds the question's entity, aliases and predicate and the record's language,
   codes, structured entity values and narrative (cut at ``max_input_chars``); never persons, the reporter or a record
   ref, and the ledger ``ref`` is ``j:<question id prefix>:<index>``. A boundary refusal propagates and nothing is
   stored or sent; any other inference error counts the record as a failure. When the judge route leaves the site
   under an exemption (a simulated endpoint, or an external one with ``allow_external_raw``), every retrieved record
   must carry its label (:func:`~.records.exempt_records_problem`: synthetic, or the public source's), else the verifier
   raises :class:`~..inference.errors.InferenceBoundaryError` before any call, and nothing is stored or sent. The
   judging stops early (audit round 2): as soon as failures are more than half the records (the verdict is then
   ``degraded`` whatever the rest say), and after :data:`~.extract.BREAKER_AFTER` consecutive failures that say the
   route's primary server is down (timeout, network, 5xx, after the client's retries; a failure on the escalation
   endpoint after the primary answered is not one, :func:`~.extract.server_down`), when the records not yet judged
   count as failures; so a dead server costs a question at most that many deadlines, not one per record under the
   lock;
8. the verdict: no record retrieved, ``unknown`` (``no_records``); failures on more than half, ``unknown`` with
   quality ``degraded``, which reflects the model server's health rather than the records, so it is sent but not
   stored (its question_log row is ``degraded``, which uses no budget) and the next ask re-judges; any yes/yes,
   ``confirm`` (support, distinct roots, distinct reporters with every unknown reporter one shared reporter, the
   newest week of the yes/yes records); a record that mentions the entity, none that describes the predicate and
   fewer than half unclear, ``refute`` (the mentioning records); else ``unknown`` (``unclear``). Counts leave only
   as :func:`~.egress.bucket_of` labels; the local reason stays in ``verdict_log``;
9. ``evidence_ref = HMAC-SHA256(secret, verdict_id)[:16]`` for a confirm or a refute, null otherwise; any verdict but
   a degraded one is stored with its ``answered`` question_log row in one transaction, then sent.

Secrets: a secret file of exactly 64 lowercase hex characters (one trailing newline allowed), read at construction
(``secret_mode`` ``file``; a missing file is ``none`` and fails closed), or a demo seed (``seeded-demo``:
``sha256("mycelic-seeded-demo-secret:<seed>:<site id>")``). Rotating the file (a new verifier) makes earlier
references unresolvable: :meth:`SiteVerifier.resolve` recomputes the HMAC with the current secret. ``resolve`` and
``audit`` are for the site's auditor; there is no module-level resolve.

Deterministic: clocks are injected, nothing here draws randomness, and every read is ordered.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from .. import schemacheck
from ..inference.errors import InferenceBoundaryError, InferenceError
from ..inference.tasks import TaskSpec
from ..jsonio import canonical_bytes, sha256_hex, strict_load
from ..packs.canonical import Canonicaliser, folded
from .egress import EVIDENCE_REF_RE, SCHEMA_VERSION, bucket_of, verdict_id_of
from .extract import BREAKER_AFTER, LexicalExtractor, codes_channel, pair, person_values, server_down, truncate
from .records import QuestionLogRow, RecordStore, VerdictRow, WindowRecord, exempt_records_problem
from .site import in_master_data
from .weeks import TS_RE, local_date

if TYPE_CHECKING:
    from ..inference.runtime import Runtime
    from ..packs.loader import FrozenPack
    from .site import EdgeSite

JUDGE_TASK = "judge_record"
JUDGE_MAX_TOKENS = 256
ANSWERS = ("yes", "no", "unclear")
DEMO_SECRET_PREFIX = "mycelic-seeded-demo-secret"
SECRET_FILE_RE = re.compile(rb"[0-9a-f]{64}\n?")
_UNKNOWN_REPORTER = object()


class VerifyError(ValueError):
    """A verifier that cannot be built; the text never holds a value."""


@dataclass(frozen=True)
class Resolution:
    """What the site's auditor gets for an evidence reference: the local record refs behind one verdict."""

    question_id: str
    verdict_id: str
    verdict: str
    window: tuple[str, str]
    record_refs: tuple[str, ...]


@dataclass(frozen=True)
class VerdictAudit:
    """The counts behind one verdict, for site use only; none of them leaves the site."""

    judged: int
    failures: int
    unclear: int
    entity_records: int
    confirming: int
    extraction_misses: int
    local_reason: str | None
    truncated: bool


@dataclass(frozen=True)
class _Outcome:
    verdict: str
    local_reason: str | None
    quality: str
    confirming: tuple[WindowRecord, ...]
    entity: tuple[WindowRecord, ...]
    unclear: int


# --------------------------------------------------------------------------------------------------- the judge

def judge_task() -> TaskSpec:
    instructions = (
        "Judge one record of this site against one question. The question names an entity (its type, its id and "
        "the aliases it may be written as) and a predicate. mentions_entity: yes when the record names that entity "
        "in its text or among its structured entities, no when it does not, unclear when you cannot tell. "
        "describes_predicate: yes when the record states that the predicate happened to that entity (the record's "
        "codes are statements too), no when it does not or says that it did not happen, unclear when you cannot "
        "tell. Judge only this record."
    )
    return TaskSpec(JUDGE_TASK, "raw", instructions, JUDGE_MAX_TOKENS)


def judge_schema() -> dict[str, Any]:
    answer = {"type": "string", "enum": list(ANSWERS)}
    return {"type": "object", "additionalProperties": False, "required": ["describes_predicate", "mentions_entity"],
            "properties": {"mentions_entity": answer, "describes_predicate": answer}}


def judge_question(pack: "FrozenPack", params: Mapping[str, str]) -> dict[str, Any]:
    """The question part of a judge payload: the entity (type, label, id, its alias phrases) and the predicate."""
    t, eid, predicate = params["entity_type"], params["entity_id"], params["predicate"]
    table = pack.aliases.get(t, {})
    return {"entity_type": t, "entity_type_label": pack.entity_types[t].label, "entity_id": eid,
            "aliases": sorted(alias for alias in table if table[alias] == eid), "predicate": predicate,
            "predicate_label": pack.predicates[predicate].label}


def judge_payload(pack: "FrozenPack", question: Mapping[str, Any], record: WindowRecord) -> dict[str, Any]:
    """What the judge sees: the question's entity and predicate, and the record's language, codes, structured entity
    values and narrative (cut at ``max_input_chars``). Never persons, the reporter or a record ref."""
    return {"question": judge_question(pack, question["params"]),
            "record": {"language": record.language, "codes": sorted(record.codes),
                       "entities": {name: list(record.structured[name]) for name in sorted(record.structured)},
                       "text": truncate(record.narrative, pack.extraction.max_input_chars)[0]}}


def lexical_judge(pack: "FrozenPack", canonicaliser: Canonicaliser) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """The deterministic judge, and the fake provider's judge handler. It rebuilds the record from the payload (no
    persons, no reporter) and runs the codes channel, the lexical extractor and :func:`~.extract.pair`:
    ``mentions_entity`` is yes when the entity is a codes-channel entity or in any text claim (negated and
    entity-only claims included); ``describes_predicate`` is yes when ``(type, id, predicate)`` is a paired claim.
    When the record's language is not a pack language the text yields no predicate, so a yes there can only come
    from the record's codes; every other answer is then ``unclear``. Every claim the site stored for a record makes
    this judge answer yes/yes on that record (the narrative is cut at ``max_input_chars``)."""
    extractor = LexicalExtractor(pack, canonicaliser)

    def handler(payload: Mapping[str, Any]) -> dict[str, Any]:
        q, r = payload["question"], payload["record"]
        record = {"language": r["language"], "codes": list(r["codes"]),
                  "entities": {name: list(r["entities"][name]) for name in sorted(r["entities"])},
                  "narrative": r["text"], "persons": {}, "reporter": None}
        codes = codes_channel(record, pack, canonicaliser)
        text = extractor.extract(record, codes)
        entity = (q["entity_type"], q["entity_id"])
        mentioned = (entity in {(e.entity_type, e.entity_id) for e in codes.entities}
                     or entity in {(c.entity_type, c.entity_id) for c in text.claims})
        described = (*entity, q["predicate"]) in {(c.entity_type, c.entity_id, c.predicate) for c in pair(codes, text)}
        if text.language_supported:
            return {"mentions_entity": "yes" if mentioned else "no",
                    "describes_predicate": "yes" if described else "no"}
        return {"mentions_entity": "yes" if mentioned else "unclear",
                "describes_predicate": "yes" if described else "unclear"}

    return handler


# --------------------------------------------------------------------------------------------------- retrieval

def retrieve(store: RecordStore, canonicaliser: Canonicaliser, *, entity_type: str, entity_id: str,
             window: Mapping[str, str], cap: int,
             cache: dict[tuple[str, str], frozenset[str]] | None = None) -> tuple[list[WindowRecord], bool]:
    """The site's own records (forwarded-in excluded) received in the window that hold any stored claim on the entity
    (any predicate or channel), whose structured values of the type resolve exactly to it, or whose narrative names
    it other than as one of the record's person values or its reporter (a patient reference in the lot format is not
    the lot, as the extractor's ``person_value`` rule already says); newest first, the first ``cap`` kept. Returns
    ``(records, truncated)``. E2's central_raw condition reads through this function too, so both read the same
    records.

    ``cache`` is an optional dict the caller keeps for one site store and one canonicaliser: a stored narrative never
    changes, so the ids its scan names per ``(record_ref, entity_type)`` are computed once."""
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise ValueError("cap must be an int >= 1") from None
    first, last = window["start_week"], window["end_week"]
    claimed = frozenset(store.claimed_refs(entity_type, entity_id, first, last))
    found = []
    for rec in store.window_records(first, last):
        if rec.record_ref in claimed:
            found.append(rec)
            continue
        resolved = (canonicaliser.resolve_exact(entity_type, v) for v in rec.structured.get(entity_type, ()))
        if any(m is not None and m.entity_id == entity_id for m in resolved):
            found.append(rec)
            continue
        key = (rec.record_ref, entity_type)
        named = cache.get(key) if cache is not None else None
        if named is None:
            persons = person_values({"persons": rec.persons or {}, "reporter": rec.reporter_id})
            named = frozenset(m.entity_id for m in canonicaliser.scan(rec.narrative, types=(entity_type,)).mentions
                              if folded(m.text) not in persons)
            if cache is not None:
                cache[key] = named
        if entity_id in named:
            found.append(rec)
    return found[:cap], len(found) > cap


# --------------------------------------------------------------------------------------------------- the rules

def decide(records: Sequence[WindowRecord], judged: Sequence[tuple[WindowRecord, Mapping[str, str]]],
           failures: int) -> _Outcome:
    """The verdict rules over the retrieved records, the judged ones with their replies and the failure count."""
    unclear = sum(1 for _, reply in judged if "unclear" in (reply["mentions_entity"], reply["describes_predicate"]))
    if not records:
        return _Outcome("unknown", "no_records", "ok", (), (), unclear)
    if 2 * failures > len(records):
        return _Outcome("unknown", "degraded", "degraded", (), (), unclear)
    yes = tuple(rec for rec, reply in judged
                if reply["mentions_entity"] == "yes" and reply["describes_predicate"] == "yes")
    if yes:
        return _Outcome("confirm", None, "ok", yes, (), unclear)
    mentioning = tuple(rec for rec, reply in judged if reply["mentions_entity"] == "yes")
    describing = any(reply["describes_predicate"] == "yes" for _, reply in judged)
    if mentioning and not describing and 2 * unclear < len(judged):
        return _Outcome("refute", None, "ok", (), mentioning, unclear)
    return _Outcome("unknown", "unclear", "ok", (), (), unclear)


# --------------------------------------------------------------------------------------------------- the verifier

class SiteVerifier:
    """Answers questions for one :class:`~.site.EdgeSite`. ``runtime`` (bound to ``site:<id>``) judges records; None
    judges them with :func:`lexical_judge`. Exactly one of ``secret_file`` and ``demo_seed``."""

    def __init__(self, site: "EdgeSite", *, runtime: "Runtime | None", clock: Callable[[], str],
                 secret_file: str | Path | None = None, demo_seed: int | None = None) -> None:
        if runtime is not None and runtime.boundary != f"site:{site.site_id}":
            raise VerifyError("the runtime is bound to another boundary") from None
        if (secret_file is None) == (demo_seed is None):
            raise VerifyError("give exactly one of secret_file and demo_seed") from None
        secret: bytes | None = None
        if demo_seed is not None:
            if isinstance(demo_seed, bool) or not isinstance(demo_seed, int) or demo_seed < 0:
                raise VerifyError("demo_seed must be an int >= 0") from None
            secret = hashlib.sha256(f"{DEMO_SECRET_PREFIX}:{demo_seed}:{site.site_id}".encode("utf-8")).digest()
            mode = "seeded-demo"
        else:
            path = Path(secret_file)
            mode = "none"
            if path.exists():
                data = None
                try:
                    data = path.read_bytes()
                except OSError:
                    data = None
                if data is None or SECRET_FILE_RE.fullmatch(data) is None:
                    raise VerifyError("the secret file must hold exactly 64 lowercase hex characters") from None
                secret, mode = bytes.fromhex(data[:64].decode("ascii")), "file"
        self.site = site
        self.pack = site.pack
        self.runtime = runtime
        self.secret_mode = mode
        self._secret = secret
        self._clock = clock
        self._lock = threading.Lock()
        self._task = judge_task()
        self._schema = schemacheck.compile(judge_schema())
        self._lexical = lexical_judge(site.pack, site.canonicaliser) if runtime is None else None
        self._mentions: dict[tuple[str, str], frozenset[str]] = {}

    def _now(self) -> str:
        ts = self._clock()
        if not isinstance(ts, str) or TS_RE.fullmatch(ts) is None or local_date(ts) is None:
            raise ValueError("the clock must return an ISO 8601 timestamp") from None
        return ts

    def _store(self) -> RecordStore:
        """A new connection on the site's store, opened in the calling thread."""
        return RecordStore(self.site.store.path, site_id=self.site.site_id, pack_id=self.pack.id,
                           config_hash=self.pack.config_hash)

    def _ref(self, verdict_id: str) -> str:
        return hmac.new(self._secret, verdict_id.encode("ascii"), hashlib.sha256).hexdigest()[:16]

    # ------------------------------------------------------------------ answer
    def answer(self, question: Any) -> dict[str, Any]:
        """The parsed verdict body that crossed the Boundary. An :class:`~.egress.EgressError` from the Boundary and an
        :class:`~..inference.errors.InferenceBoundaryError` from the runtime propagate."""
        with self._lock:
            parsed = self.site.boundary.accept("in", "question", question)
            store = self._store()
            try:
                return self._answer(store, parsed)
            finally:
                store.close()

    def _body(self, question: Mapping[str, Any], verdict: str, *, reason: str | None = None,
              outcome: _Outcome | None = None, truncated: bool = False) -> dict[str, Any]:
        support = roots = reporters = entity_records = newest = None
        if outcome is not None and outcome.verdict == "confirm":
            yes = outcome.confirming
            support = bucket_of(len(yes), self.pack)
            roots = bucket_of(len({r.root_ref for r in yes}), self.pack)
            reporters = bucket_of(len({r.reporter_id if r.reporter_id is not None else _UNKNOWN_REPORTER
                                       for r in yes}), self.pack)
            newest = max(r.iso_week for r in yes)
        if outcome is not None and outcome.verdict == "refute":
            entity_records = bucket_of(len(outcome.entity), self.pack)
        body: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION, "pack": self.pack.id, "pack_hash": self.pack.config_hash,
            "site": self.site.site_id, "question_id": question["question_id"], "verdict": verdict, "reason": reason,
            "window": dict(question["window"]), "support_bucket": support, "roots_bucket": roots,
            "reporters_bucket": reporters, "entity_records_bucket": entity_records, "newest_week": newest,
            "truncated": truncated, "quality": outcome.quality if outcome is not None else "ok",
            "secret_mode": self.secret_mode}
        body["verdict_id"] = verdict_id_of(body)
        body["evidence_ref"] = self._ref(body["verdict_id"]) if verdict in ("confirm", "refute") else None
        return body

    def _judge(self, question: Mapping[str, Any],
               records: Sequence[WindowRecord]) -> tuple[list[tuple[WindowRecord, dict[str, str]]], int]:
        judged: list[tuple[WindowRecord, dict[str, str]]] = []
        failures = 0
        prefix = question["question_id"][:12]
        if self.runtime is not None:
            label = self.runtime.exemption(self._task)
            if exempt_records_problem(label, self.site.site_id, sum(1 for r in records if not r.synthetic)):
                route = self.runtime.config.routes.get(JUDGE_TASK)
                raise InferenceBoundaryError(task=JUDGE_TASK, endpoint=route.endpoint if route else None) from None
        down = 0
        for i, rec in enumerate(records):
            payload = judge_payload(self.pack, question, rec)
            if self._lexical is not None:
                judged.append((rec, self._lexical(payload)))
                continue
            if 2 * failures > len(records):          # degraded whatever the rest say
                break
            if down >= BREAKER_AFTER:                # the server is down: the rest are not sent
                failures += len(records) - i
                break
            reply = None
            down_now = False
            try:
                reply = self.runtime.run(self._task, payload, self._schema, ref=f"j:{prefix}:{i}")
            except InferenceBoundaryError:
                raise
            except InferenceError as err:
                down_now = server_down(err, self.runtime, JUDGE_TASK)
            if reply is None:
                failures += 1
                down = down + 1 if down_now else 0
            else:
                judged.append((rec, reply))
                down = 0
        return judged, failures

    def _answer(self, store: RecordStore, question: dict[str, Any]) -> dict[str, Any]:
        boundary = self.site.boundary
        ts = self._now()
        day = local_date(ts)
        qid, params = question["question_id"], question["params"]
        t, eid = params["entity_type"], params["entity_id"]
        if self._secret is None:
            store.log_question(QuestionLogRow(0, qid, day, t, eid, "no_secret", ts))
            return boundary.send("out", "verdict", self._body(question, "unknown", reason="no_secret"))
        stored = store.verdict_for_question(qid)
        if stored is not None:
            return boundary.send("out", "verdict", strict_load(stored.body))
        answered = store.answered_count(t, eid, day)
        if answered >= self.pack.egress.question_budget_per_entity_per_day or (
                answered == 0 and store.answered_entities(day) >= self.pack.egress.question_entities_per_site_per_day):
            store.log_question(QuestionLogRow(0, qid, day, t, eid, "budget", ts))
            return boundary.send("out", "verdict", self._body(question, "unknown", reason="budget"))
        if in_master_data(self.pack, self.site.master, t, eid):
            records, truncated = retrieve(store, self.site.canonicaliser, entity_type=t, entity_id=eid,
                                          window=question["window"], cap=self.pack.egress.verify_max_records,
                                          cache=self._mentions)
            judged, failures = self._judge(question, records)
            outcome = decide(records, judged, failures)
        else:
            records, truncated, judged, failures = [], False, [], 0
            outcome = _Outcome("unknown", "not_master_data", "ok", (), (), 0)
        parsed = boundary.validate("out", "verdict", self._body(question, outcome.verdict, outcome=outcome,
                                                                truncated=truncated))
        if outcome.quality == "degraded":
            store.log_question(QuestionLogRow(0, qid, day, t, eid, "degraded", ts))
            return boundary.send("out", "verdict", parsed)
        data = canonical_bytes(parsed)
        misses = sum(1 for r in outcome.confirming if not store.has_claim(r.record_ref, t, eid, params["predicate"]))
        store.add_verdict(
            VerdictRow(verdict_id=parsed["verdict_id"], question_id=qid, evidence_ref=parsed["evidence_ref"],
                       body=data, sha256=sha256_hex(data), verdict=outcome.verdict,
                       confirming_refs=tuple(r.record_ref for r in outcome.confirming),
                       entity_refs=tuple(r.record_ref for r in outcome.entity), judged=len(judged),
                       failures=failures, unclear=outcome.unclear, extraction_misses=misses,
                       local_reason=outcome.local_reason, truncated=truncated, created_at=ts),
            QuestionLogRow(0, qid, day, t, eid, "answered", ts))
        return boundary.send("out", "verdict", parsed)

    # ------------------------------------------------------------------ the site's auditor
    def resolve(self, evidence_ref: Any) -> Resolution | None:
        """The local record refs behind a confirm (the confirming records) or a refute (the records that mention the
        entity); None for an unknown reference, without a secret, or when the reference does not verify with the
        current secret (a rotated secret)."""
        if not isinstance(evidence_ref, str) or EVIDENCE_REF_RE.fullmatch(evidence_ref) is None:
            return None
        if self._secret is None:
            return None
        with self._lock:
            store = self._store()
            try:
                row = store.verdict_for_ref(evidence_ref)
            finally:
                store.close()
        if row is None or not hmac.compare_digest(self._ref(row.verdict_id), evidence_ref):
            return None
        window = strict_load(row.body)["window"]
        refs = row.confirming_refs if row.verdict == "confirm" else row.entity_refs
        return Resolution(question_id=row.question_id, verdict_id=row.verdict_id, verdict=row.verdict,
                          window=(window["start_week"], window["end_week"]), record_refs=tuple(refs))

    def audit(self, verdict_id: Any) -> VerdictAudit | None:
        """The counts behind a stored verdict (site use only), or None."""
        if not isinstance(verdict_id, str):
            return None
        with self._lock:
            store = self._store()
            try:
                row = store.verdict_by_id(verdict_id)
            finally:
                store.close()
        if row is None:
            return None
        return VerdictAudit(judged=row.judged, failures=row.failures, unclear=row.unclear,
                            entity_records=len(row.entity_refs), confirming=len(row.confirming_refs),
                            extraction_misses=row.extraction_misses, local_reason=row.local_reason,
                            truncated=row.truncated)
