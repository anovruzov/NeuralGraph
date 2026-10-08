"""Live edge agents: one real LLM call per user agent over its own notes.

``EdgeAgent.extract(notes)`` sends the agent's notes (and nothing else) to a
chat backend and asks for one compact line per note:

    <note number> <predicate name> <subject entity> <ok|not>

under a GBNF grammar (predicate names are a closed enum; the entity is any
lowercase token, optionally followed by a second one; the note number is one
of the agent's own indices; the polarity is an explicit word), so the output
is short (CPU decode is the bottleneck) and always parseable.

Format history (developed on LIVE seed 701 only): edge-v1 ended a line with an
optional `` not``.  Under the grammar, a model that wanted to go on copying
the note's second entity could only continue with `` not`` or by gluing the
names with ``-``; on the v1 smoke this produced false negations on 21% and
glued or wrong entity strings on 27% of notes.  edge-v2 makes polarity an
explicit word on every line and allows (and ignores) a second entity string.

The prompt gives the 50 predicate NAMES, each with a one-line plain-language
description written for this module.  It deliberately does NOT contain the
generator's synonym lists (``corpus.PRED_SURFACE``): pasting them would turn
classification into string lookup, which is what the observable lexical
index already does.

Everything after the model call is code and reads only observable things:

* entity string -> entity id by exact match against the enterprise catalog
  (``corpus.entities``): the subject is the first string the model wrote that
  is a catalog name; anything else is dropped (a near-miss name maps to that
  other entity, as it would in a deployment);
* predicate name -> id (an unknown name is dropped);
* note number -> record id through the NoteStore's code-side map (evidence
  pointer), an out-of-range or repeated number is dropped (first line wins);
* day ``t`` parsed from the note's ``[dNNN]`` header;
* echo signature from the note text (``notes.echo_signature``);
* ``spurious`` = False (the evaluator measures live precision directly).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from ..corpus import BACKGROUND_PREDICATES, N_CAUSAL_PRED, PREDICATES, PRED_ID
from ..ops import ExtractResult
from .backend import CallResult, Job, _Backend
from .notes import NoteStore, echo_signature, split_note

PROMPT_VERSION = "edge-v2"

# One line per predicate, written from the predicate names.  Plain language,
# no generator surface phrases.
PRED_DESCRIPTIONS: Dict[str, str] = {
    # supply chain / manufacturing
    "supplier_substitution": "a part or material was moved to a different or alternate supplier",
    "component_defect": "parts or components were found faulty or not meeting specification",
    "yield_drop": "manufacturing yield went down or more units were scrapped",
    "shipment_delay": "a shipment or delivery is late",
    "customer_escalation": "a customer's issue was escalated or made more severe (the customer has not left)",
    "revenue_miss": "sales or revenue came in under target",
    # security / platform
    "cert_rotation": "a security certificate or cryptographic key was replaced or renewed",
    "auth_failure": "logins or authentication checks are failing more than usual",
    "api_error_spike": "API or server error rates jumped",
    "checkout_drop": "fewer purchases or payments complete at checkout",
    # compliance
    "policy_change": "a policy, control or approval rule was introduced or modified",
    "manual_workaround": "staff bypassed the official process with a manual or unofficial workaround",
    "data_exposure_risk": "sensitive data may have left its protected boundary",
    "audit_finding": "an audit or assessment reported a deficiency",
    "regulatory_notice": "a regulator or supervisory authority sent an inquiry or notice",
    # service delivery
    "attrition_spike": "unusually many staff resigned or left the team",
    "backlog_growth": "the amount of pending, unfinished work is increasing",
    "sla_breach": "a service-level or response-time commitment was not met",
    "contract_penalty": "money became owed to a customer under a contract (a penalty, fine or credit)",
    "account_churn": "a customer was lost: stopped buying or did not renew",
    # operations
    "firmware_update": "new firmware or a new device image was installed on equipment",
    "sensor_drift": "sensor or telemetry readings became inaccurate or biased",
    "false_alarm": "an alarm or alert fired without a real problem",
    "unplanned_downtime": "equipment, a production line or a service stopped unexpectedly",
    "output_shortfall": "production output or throughput fell below plan",
    # commercial
    "price_increase": "prices were raised or an extra charge was added",
    "margin_compression": "profit margins shrank or the cost of goods rose (our own prices are price_increase)",
    "discount_escalation": "discounts or other concessions on price became larger or more frequent",
    "forecast_revision": "a forecast, plan or guidance figure was changed",
    # infrastructure / finance
    "network_change": "the network configuration, routing or firewall rules were changed",
    "latency_regression": "responses or queries became slower",
    "batch_job_overrun": "a scheduled batch, ETL or overnight job ran past its time window",
    "reporting_delay": "a report, dashboard or metric was late or out of date",
    "close_delay": "the accounting month-end or financial close finished late",
    # routine activity
    "status_update": "a routine progress or status update",
    "meeting_note": "notes taken at a meeting",
    "ticket_triage": "sorting and prioritising incoming tickets",
    "doc_review": "a document was reviewed",
    "onboarding": "a new person or team was onboarded",
    "budget_note": "a note about budget",
    "training_session": "a training session took place",
    "tool_upgrade": "an internal software tool was upgraded (software on devices or equipment is firmware_update)",
    "headcount_note": "a routine note about planned staffing numbers (staff leaving is attrition_spike)",
    "travel_note": "a note about business travel",
    "vendor_checkin": "a routine check-in with a vendor",
    "release_note": "notes about a software release",
    "backup_completed": "a scheduled backup finished",
    "access_request": "someone asked for access to a system",
    "inventory_count": "stock or inventory was counted",
    "patch_window": "a scheduled maintenance window for applying patches",
}
assert set(PRED_DESCRIPTIONS) == set(PREDICATES), "one description per predicate"


def system_prompt() -> str:
    ops_ = "\n".join(f"{p}: {PRED_DESCRIPTIONS[p]}" for p in PREDICATES[:N_CAUSAL_PRED])
    rout = "\n".join(f"{p}: {PRED_DESCRIPTIONS[p]}" for p in BACKGROUND_PREDICATES)
    return f"""You extract events from short enterprise work notes.

Each note reads: <event phrase> <entity> [<second entity>] <filler words> [not]
- The event phrase (one to five words) says what happened. Map it to exactly one predicate from the list below.
- Entity names come from the enterprise catalog: lowercase code names such as falcon, falcon-core, juniperx or redwood-ii (a name may carry a suffix like -pay, -core, -edge, -ii, x or a number). The SUBJECT entity is the first entity name after the event phrase. A second entity name, if present, is only context.
- The words after the entity names (for example "pending review", "no further action" or "see thread for details") are filler with no meaning.
- If the LAST word of a note is "not", the event is denied or retracted. Words like "no" or "nothing" inside the filler do not count.

Predicates (name: meaning)
Operational events:
{ops_}
Routine activity:
{rout}

Output one line per note, in note order:
<note number> <predicate> <subject entity> <ok or not>
End the line with "not" only when the note's last word is "not"; otherwise end it with "ok".
Skip a note only if it describes no recognisable event. Output nothing else.

Example
Notes:
0: vendor replaced by a backup supplier falcon-core redwood-ii see thread for details
1: alerts fired without any real fault juniperx per the runbook no further not
2: notes from the weekly sync redwood-ii owner assigned nothing blocking
Output:
0 supplier_substitution falcon-core ok
1 false_alarm juniperx not
2 meeting_note redwood-ii ok"""


SYSTEM_PROMPT = system_prompt()


def user_message(notes: Sequence[Tuple[int, str]]) -> str:
    """The agent's notes, numbered.  Only the body after the ``[dNNN] author
    ::`` header is shown: the day and the author are note metadata that the
    code reads from the same rendered text, and every note of one agent has
    the same author, so repeating them would only cost CPU tokens."""
    lines = [f"{i}: {split_note(t)[2]}" for i, t in notes]
    return "Notes:\n" + "\n".join(lines) + "\nOutput:"


def grammar(n_notes: int) -> str:
    """GBNF: at most one line per note, valid note numbers, closed predicate
    enum, a lowercase entity token (optionally a second one: a small model
    that wants to copy the note's second entity is not forced into a wrong
    token) and an explicit polarity word, ``ok`` or ``not``."""
    idx = " | ".join(f'"{i}"' for i in range(max(1, n_notes)))
    pred = " | ".join(f'"{p}"' for p in PREDICATES)
    return (f"root ::= (line \"\\n\"){{0,{max(1, n_notes)}}}\n"
            f"line ::= idx \" \" pred \" \" ent (\" \" ent)? \" \" pol\n"
            f"idx ::= {idx}\n"
            f"pred ::= {pred}\n"
            "ent ::= [a-z0-9] [a-z0-9-]{0,31}\n"
            "pol ::= \"ok\" | \"not\"\n")


_LINE = re.compile(r"^\s*(\d+)\s+([A-Za-z_]+)((?:\s+\S+)+?)\s*$")


def parse_lines(text: str) -> Tuple[List[Tuple[int, str, Tuple[str, ...], bool]], int]:
    """Lenient parse of the model's lines -> [(idx, pred, entity strings,
    negated)], plus the number of non-empty lines that did not parse.  The
    last word is the polarity (``not`` = negated, ``ok`` = asserted; a line
    with neither keeps every word as an entity string and is asserted)."""
    out: List[Tuple[int, str, Tuple[str, ...], bool]] = []
    bad = 0
    for ln in text.splitlines():
        if not ln.strip():
            continue
        m = _LINE.match(ln)
        if not m:
            bad += 1
            continue
        words = m.group(3).split()
        neg = False
        if len(words) >= 2 and words[-1] in ("ok", "not"):
            neg = words[-1] == "not"
            words = words[:-1]
        out.append((int(m.group(1)), m.group(2), tuple(words), neg))
    return out, bad


@dataclass
class AgentReply:
    agent_id: str
    lines: List[Tuple[int, str, Tuple[str, ...], bool]]
    n_malformed: int
    call: CallResult
    n_notes: int


class EdgeAgent:
    """A user agent: one model call over its own notes.

    ``role`` only labels the agent; the A2 central reader is the same class
    run on another backend over chunks of many users' notes."""

    def __init__(self, backend: _Backend, max_tokens_per_note: int = 16,
                 role: str = "edge"):
        self.backend = backend
        self.max_tokens_per_note = int(max_tokens_per_note)
        self.role = role

    def job(self, notes: Sequence[Tuple[int, str]], tag: str = "") -> Job:
        n = len(notes)
        return Job(messages=[{"role": "system", "content": SYSTEM_PROMPT},
                             {"role": "user", "content": user_message(notes)}],
                   max_tokens=self.max_tokens_per_note * n + 16,
                   grammar=grammar(n), tag=tag)

    def extract(self, notes: Sequence[Tuple[int, str]], agent_id: str = "") -> AgentReply:
        res = self.backend.chat(self.job(notes, agent_id))
        lines, bad = parse_lines(res.text)
        return AgentReply(agent_id, lines, bad, res, len(notes))

    def extract_many(self, items: Sequence[Tuple[str, Sequence[Tuple[int, str]]]],
                     progress=None) -> List[AgentReply]:
        jobs = [self.job(notes, aid) for aid, notes in items]
        calls = self.backend.map(jobs, progress=progress)
        out = []
        for (aid, notes), res in zip(items, calls):
            lines, bad = parse_lines(res.text)
            out.append(AgentReply(aid, lines, bad, res, len(notes)))
        return out


# ---------------------------------------------------------------------------
# Code-side canonicalisation -> ExtractResult
# ---------------------------------------------------------------------------

DIAG_KEYS = ("notes", "lines", "malformed", "bad_index", "dup_index",
             "bad_pred", "bad_entity", "kept", "truncated")


@dataclass
class Claims:
    """Columnar canonical claims plus parse/canonicalisation diagnostics."""
    rid: List[int] = field(default_factory=list)
    uid: List[int] = field(default_factory=list)
    pred: List[int] = field(default_factory=list)
    anchor: List[int] = field(default_factory=list)
    t: List[int] = field(default_factory=list)
    pol: List[int] = field(default_factory=list)
    sig: List[int] = field(default_factory=list)
    diag: Dict[str, int] = field(default_factory=lambda: {k: 0 for k in DIAG_KEYS})

    def to_extract_result(self) -> ExtractResult:
        n = len(self.rid)
        # the last column (ExtractResult.spurious, "the operator invented this
        # claim") is constant False: a live model's inventions are unknown to
        # the code, and the evaluator measures them against the notes instead
        return ExtractResult(
            np.array(self.rid, dtype=np.int64),
            np.array(self.uid, dtype=np.int64),
            np.array(self.pred, dtype=np.int16),
            np.array(self.anchor, dtype=np.int32),
            np.array(self.t, dtype=np.int32),
            np.array(self.pol, dtype=np.int8),
            np.array(self.sig, dtype=np.int64),
            np.zeros(n, dtype=bool))


class Canonicaliser:
    """Maps model lines onto catalog ids; reads only the store's text and its
    code-side note -> record / author map."""

    def __init__(self, store: NoteStore):
        self.store = store
        self.ent_id = {e: i for i, e in enumerate(store.catalog)}
        self.catalog = set(store.catalog)

    def add(self, claims: Claims, reply: AgentReply, note_rids: Sequence[int]) -> None:
        d = claims.diag
        d["notes"] += reply.n_notes
        d["lines"] += len(reply.lines) + reply.n_malformed
        d["malformed"] += reply.n_malformed
        d["truncated"] += int(reply.call.finish_reason == "length")
        seen = set()
        for idx, pname, ents, neg in reply.lines:
            if idx < 0 or idx >= len(note_rids):
                d["bad_index"] += 1
                continue
            if idx in seen:
                d["dup_index"] += 1
                continue
            seen.add(idx)
            pid = PRED_ID.get(pname)
            if pid is None:
                d["bad_pred"] += 1
                continue
            # the subject: the first entity string the model wrote that is a
            # catalog name (exact match); a second string is context
            eid = next((self.ent_id[e] for e in ents if e in self.ent_id), None)
            if eid is None:
                d["bad_entity"] += 1
                continue
            rid = int(note_rids[idx])
            text = self.store.text_of(rid)
            day, _, _ = split_note(text)
            claims.rid.append(rid)
            claims.uid.append(self.store.uid_of(self.store.author_of(rid)))
            claims.pred.append(int(pid))
            claims.anchor.append(int(eid))
            claims.t.append(int(day))
            claims.pol.append(-1 if neg else 1)
            claims.sig.append(echo_signature(text, self.catalog))
            d["kept"] += 1


def edge_claims(store: NoteStore, replies: Iterable[AgentReply]) -> Claims:
    can = Canonicaliser(store)
    cl = Claims()
    for r in replies:
        can.add(cl, r, store.rids_of(r.agent_id))
    return cl


def call_usage(replies: Iterable[AgentReply]) -> Dict[str, float]:
    tot = {"calls": 0, "replayed": 0, "prompt_tokens": 0, "cached_tokens": 0,
           "completion_tokens": 0, "latency_s": 0.0, "prompt_ms": 0.0,
           "decode_ms": 0.0}
    for r in replies:
        c = r.call
        tot["calls"] += 1
        tot["replayed"] += int(c.replayed)
        tot["prompt_tokens"] += c.prompt_tokens
        tot["cached_tokens"] += c.cached_tokens
        tot["completion_tokens"] += c.completion_tokens
        tot["latency_s"] += c.latency_s
        tot["prompt_ms"] += c.prompt_ms
        tot["decode_ms"] += c.decode_ms
    return tot
