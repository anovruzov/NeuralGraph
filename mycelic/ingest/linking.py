"""One connected graph across apps inside a holder (docs/mycelic/INGESTION.md §8.1–§8.4, the deterministic part).

Every ingested record is scanned for:

* **references** — GitHub issue and PR URLs, ``owner/repo#n`` (and bare ``#n`` when the record's own repository is
  known), Slack permalinks, and tracker keys whose prefix the holder knows;
* **mentions** — components and their versions near dependency vocabulary (``httpclient 4.2``), services
  (``checkout-service``, "deployment of checkout", the record's own repository), symptoms (``timeout regression``,
  "timing out"), and organizations by e-mail domain;
* **typed relations** from a closed vocabulary, each with a *modality*: ``X failed after Y`` is temporal (never
  causal), ``X introduced Y`` is asserted by its author, a hedged "might be caused by" is hypothesized, and "not caused
  by" or "ruled out" is negated.

The results become canonical entity ids (``component:httpclient``, ``version:httpclient@4.2``,
``issue:github:acme/checkout#482`` ...). The same id in a Slack message and in a GitHub issue links both records'
memories, so NeuralGraph's graph channel connects them with no special casing at retrieval time. Edges are NeuralGraph
``relations`` rows backed by ``relation_evidence`` (one row per supporting record, reference-counted): deleting a
record removes its evidence and recomputes the edges it supported.

Disclosure rule (§8.7, conservative): an edge is *traversable* (``status='active'``) only while at least one supporting
record comes from a public source. Edges known only from member-restricted or private records stay ``restricted``, so
neither their existence nor their ranking influence reaches an audience outside the source. Mentions are safe as they
are, because they attach entities to a record's own memories, and retrieval filters those memories by audience first.

No model is involved; this is the extractor the deterministic tests and the fake ``extract_org_relations`` rely on.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

PREDICATES = frozenset({"part_of", "reply_to", "quotes", "forward_of", "attachment_of", "authored_by", "references", "mentions", "depends_on",
                        "upgraded_to", "introduced_regression", "followed", "caused", "contributed_to", "affects", "at_risk", "same_event_candidate"})
MODALITIES = ("structural", "asserted", "hypothesized", "negated", "temporal")

HEDGES = frozenset({"might", "may", "could", "possibly", "probably", "likely", "perhaps", "suspect", "suspected", "seems", "appears", "maybe"})
NEGATIONS = ("not caused by", "not due to", "not related to", "unrelated to", "ruled out", "isn't caused by", "wasn't caused by", "is not caused by")
DEP_CUES = re.compile(r"\b(upgrad\w*|bump\w*|dependenc\w*|version|introduc\w*|release[ds]?|pin(?:ned)?|downgrad\w*)\b", re.I)
_GH_URL = re.compile(r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/(issues|pull)/(\d+)")
_GH_SHORT = re.compile(r"(?<![\w/])([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#(\d+)\b")
_GH_BARE = re.compile(r"(?<![\w/&])#(\d{1,7})\b")
_SLACK_LINK = re.compile(r"https?://([a-z0-9-]+)\.slack\.com/archives/([A-Z0-9]+)/p(\d{10})(\d{6})")
_TRACKER_KEY = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-(\d+)\b")
_EMAIL = re.compile(r"\b[\w.+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)\b")
_COMPONENT_VERSION = re.compile(r"\b([a-z][a-z0-9_.-]{1,40}?)\s+(?:v(?:ersion)?\s*)?(\d+\.\d+(?:\.\d+)?)\b", re.I)
_SERVICE_SUFFIX = re.compile(r"\b([a-z][a-z0-9-]{1,40})-service\b", re.I)
_SERVICE_PHRASE = re.compile(r"\b(?:deploy(?:ment)?s?|rollouts?|releases?)\s+(?:of|for|to)\s+(?:the\s+)?([a-z][a-z0-9-]{2,40})\b", re.I)
_SYMPTOM_REGRESSION = re.compile(r"\b(timeout|latency|memory|performance|throughput|error[- ]rate|crash|connection)\s+regression\b", re.I)
_TIMEOUT = re.compile(r"\b(time[sd]?\s+out|timing\s+out|timeouts?)\b", re.I)
_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")
_STOP = frozenset({"the", "a", "an", "to", "of", "and", "or", "in", "on", "at", "for", "with", "from", "by", "is", "was", "be", "it", "this", "that",
                   "after", "before", "version", "release", "v", "about", "around", "since", "than", "until", "up", "via", "per"})


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:60]


@dataclass(frozen=True)
class Ref:
    """An entity a record names. ``aliases`` are the phrases a query may use to reach it (graph channel)."""
    kind: str
    entity_id: str
    name: str
    aliases: tuple[str, ...] = ()
    role: str = "mention"                 # mention | reference
    method: str = "pattern"


@dataclass(frozen=True)
class Edge:
    subject: str
    predicate: str
    object: str
    modality: str
    confidence: float
    span: tuple[int, int] | None = None


@dataclass
class LinkResult:
    refs: list[Ref] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def entities(self) -> list[tuple[str, str, str]]:
        """``(entity_id, name, type)`` for NeuralGraph: linked to every chunk memory of the record."""
        return [(r.entity_id, r.name, r.kind) for r in self.refs]


# ---------------------------------------------------------------------------------------------- references (§8.2)
def extract_references(text: str, *, own_repo: str | None = None, known_keys: Iterable[str] = ()) -> list[Ref]:
    out: list[Ref] = []
    seen: set[str] = set()

    def add(ref: Ref) -> None:
        if ref.entity_id not in seen:
            seen.add(ref.entity_id)
            out.append(ref)
    for m in _GH_URL.finditer(text):
        o, r, _, n = m.groups()
        name = f"{o}/{r}#{n}"
        add(Ref("issue", f"issue:github:{name.lower()}", name, (name,), role="reference", method="url"))
        add(Ref("repo", f"repo:github:{o.lower()}/{r.lower()}", f"{o}/{r}", (f"{o}/{r}",), role="reference", method="url"))
    for m in _GH_SHORT.finditer(text):
        o, r, n = m.groups()
        if "." in o and o.lower().endswith((".com", ".org", ".net", ".example")):
            continue                                            # a host, not an owner
        name = f"{o}/{r}#{n}"
        add(Ref("issue", f"issue:github:{name.lower()}", name, (name,), role="reference", method="key"))
    if own_repo:
        for m in _GH_BARE.finditer(text):
            name = f"{own_repo}#{m.group(1)}"
            add(Ref("issue", f"issue:github:{name.lower()}", name, (name,), role="reference", method="key"))
    for m in _SLACK_LINK.finditer(text):
        _, channel, sec, micro = m.groups()
        add(Ref("message", f"slack:{channel}:{sec}.{micro}", f"Slack message in {channel}", (), role="reference", method="url"))
    keys = {k.upper() for k in known_keys}
    if keys:
        for m in _TRACKER_KEY.finditer(text):
            if m.group(1) in keys:
                key = f"{m.group(1)}-{m.group(2)}"
                add(Ref("issue", f"issue:tracker:{key.lower()}", key, (key,), role="reference", method="key"))
    return out


# ---------------------------------------------------------------------------------------------- mentions (§8.1)
def extract_mentions(text: str, *, own_repo: str | None = None, services: Mapping[str, Iterable[str]] | None = None) -> list[Ref]:
    out: list[Ref] = []
    seen: set[str] = set()

    def add(ref: Ref) -> None:
        if ref.entity_id not in seen:
            seen.add(ref.entity_id)
            out.append(ref)
    # components with versions, only in dependency vocabulary (so "Q3 2026" or "page 4.2" is not a component)
    for sent in _sentences(text):
        if not DEP_CUES.search(sent):
            continue
        for m in _COMPONENT_VERSION.finditer(sent):
            name, ver = m.group(1).lower().strip("._-"), m.group(2)
            if name in _STOP or len(name) < 2 or name.isdigit():
                continue
            add(Ref("component", f"component:{slug(name)}", name, (name,)))
            add(Ref("version", f"version:{slug(name)}@{ver}", f"{name} {ver}", (f"{name} {ver}", f"{name}@{ver}")))
    # services: '<name>-service', 'deployment of <name>', a catalog, the record's own repository
    for m in _SERVICE_SUFFIX.finditer(text):
        name = m.group(1).lower()
        add(Ref("service", f"service:{slug(name)}", name, (name, f"{name}-service", f"{name} service")))
    for m in _SERVICE_PHRASE.finditer(text):
        name = m.group(1).lower()
        if name in _STOP or name in ("production", "staging", "prod"):
            continue
        add(Ref("service", f"service:{slug(name)}", name, (name, f"{name}-service", f"{name} service")))
    for sid, aliases in (services or {}).items():
        names = [sid, *aliases]
        if any(re.search(rf"\b{re.escape(a.lower())}\b", text.lower()) for a in names if a):
            add(Ref("service", f"service:{slug(sid)}", sid, tuple(a.lower() for a in names if a), method="catalog"))
    if own_repo and "/" in own_repo:
        repo = own_repo.split("/", 1)[1].lower()
        add(Ref("service", f"service:{slug(repo)}", repo, (repo, f"{repo}-service", f"{repo} service"), method="structural"))
    # symptoms
    for m in _SYMPTOM_REGRESSION.finditer(text):
        kind = slug(m.group(1).replace("time out", "timeout"))
        add(Ref("symptom", f"symptom:{kind}-regression", f"{m.group(1).lower()} regression", (f"{m.group(1).lower()} regression",)))
        if kind == "timeout":
            add(Ref("symptom", "symptom:timeout", "timeouts", ("timeout", "timeouts", "time out", "timing out", "timed out")))
    if _TIMEOUT.search(text):
        add(Ref("symptom", "symptom:timeout", "timeouts", ("timeout", "timeouts", "time out", "timing out", "timed out")))
    # organizations by e-mail domain (a customer, a vendor); the domain only, never the address
    for m in _EMAIL.finditer(text):
        dom = m.group(1).lower()
        add(Ref("org", f"org:{dom}", dom, (dom,), method="email_domain"))
    return out


# ---------------------------------------------------------------------------------------------- relations (§8.3)
def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text or "") if s.strip()]


def _modality(sentence: str, default: str) -> str:
    low = sentence.lower()
    if any(n in low for n in NEGATIONS):
        return "negated"
    if default in ("asserted",) and set(re.findall(r"[a-z']+", low)) & HEDGES:
        return "hypothesized"
    return default


_CONF = {"asserted": 0.7, "hypothesized": 0.5, "temporal": 0.6, "negated": 0.6, "structural": 0.95}


def pattern_relations(text: str, refs: list[Ref], *, record_id: str, observed_at: str | None = None) -> tuple[list[Edge], list[Ref]]:
    """Edges between the record's entities, sentence by sentence. Events (a failed deployment, an upgrade) are new
    entities local to this record; they are never merged automatically across records (§8.1)."""
    by_kind: dict[str, list[Ref]] = {}
    for r in refs:
        by_kind.setdefault(r.kind, []).append(r)
    services = by_kind.get("service", [])
    service = services[0] if services else None
    day = (observed_at or "")[:10] or "undated"
    edges: list[Edge] = []
    events: list[Ref] = []

    def event(kind: str) -> Ref:
        sid = service.name if service else "none"
        ref = Ref("event", f"event:{kind}:{slug(sid)}:{day}:{record_id[-8:]}", f"{kind.replace('-', ' ')} ({sid}, {day})", ())
        if ref not in events:
            events.append(ref)
        return ref

    def edge(s: Ref | str, p: str, o: Ref | str, modality: str, sent: str) -> None:
        sid = s.entity_id if isinstance(s, Ref) else s
        oid = o.entity_id if isinstance(o, Ref) else o
        if p not in PREDICATES or sid == oid:
            return
        e = Edge(sid, p, oid, modality, _CONF[modality], _span(text, sent))
        if e not in edges:
            edges.append(e)

    for sent in _sentences(text):
        low = sent.lower()
        comps = [r for r in by_kind.get("component", []) if r.name in low]
        vers = [r for r in by_kind.get("version", []) if r.name.split(" ")[0] in low and r.name.split(" ")[-1] in low]
        symptoms = [r for r in by_kind.get("symptom", []) if any(a in low for a in r.aliases)]
        failed = re.search(r"\b(fail(?:ed|s|ing|ure)?|broke|crash(?:ed|es)?|abort(?:ed)?|rolled back|timing out|timed out)\b", low)
        deploy = re.search(r"\b(deploy\w*|rollout|release)\b", low)
        upgrade = re.search(r"\b(upgrad\w*|bump\w*|dependenc\w*)\b", low)
        if failed and deploy:
            fail_ev = event("deploy-failure")
            if service:
                edge(fail_ev, "affects", service, _modality(sent, "asserted"), sent)
            if upgrade and re.search(r"\bafter\b", low):
                up_ev = event("dependency-upgrade")
                edge(fail_ev, "followed", up_ev, "temporal", sent)          # "after" is temporal, never causal
                for v in vers:
                    edge(up_ev, "upgraded_to", v, "asserted", sent)
            for s in symptoms:
                edge(fail_ev, "affects", s, _modality(sent, "asserted"), sent)
        if re.search(r"\bintroduc\w*\b", low):
            src = vers[0] if vers else (comps[0] if comps else None)
            for s in symptoms or []:
                if src is not None:
                    edge(src, "introduced_regression", s, _modality(sent, "asserted"), sent)
        if re.search(r"\b(caused by|due to|because of|as a result of)\b", low):
            causes = vers or comps
            effects = symptoms or ([event("deploy-failure")] if failed else [])
            for c in causes:
                for e in effects:
                    edge(c, "caused", e, _modality(sent, "asserted"), sent)
        if re.search(r"\bat risk\b", low):
            for org in by_kind.get("org", []):
                edge(org, "at_risk", "topic:renewal" if "renewal" in low else "topic:account", _modality(sent, "asserted"), sent)
        if re.search(r"\b(uses|depends on|relies on|built on)\b", low) and service:
            for c in comps:
                edge(service, "depends_on", c, _modality(sent, "asserted"), sent)
        if upgrade and service and not failed:
            for v in vers:
                edge(service, "upgraded_to", v, _modality(sent, "asserted"), sent)
    for r in by_kind.get("issue", []) + by_kind.get("message", []):
        edge(f"record:{record_id}", "references", r, "structural", r.name)
    return edges, events


def _span(text: str, sent: str) -> tuple[int, int] | None:
    i = text.find(sent)
    return (i, i + len(sent)) if i >= 0 else None


def link_record(title: str, body: str, *, record_id: str, observed_at: str | None = None, own_repo: str | None = None,
                known_keys: Iterable[str] = (), services: Mapping[str, Iterable[str]] | None = None) -> LinkResult:
    text = f"{title}\n{body}" if title and title not in body else (body or title or "")
    refs = extract_references(text, own_repo=own_repo, known_keys=known_keys) + extract_mentions(text, own_repo=own_repo, services=services)
    uniq: dict[str, Ref] = {}
    for r in refs:
        uniq.setdefault(r.entity_id, r)
    edges, events = pattern_relations(text, list(uniq.values()), record_id=record_id, observed_at=observed_at)
    for e in events:
        uniq.setdefault(e.entity_id, e)
    return LinkResult(refs=list(uniq.values()), edges=edges)


# ---------------------------------------------------------------------------------------------- storage (§8.4)
def relation_id(s: str, p: str, o: str) -> str:
    return "rel_" + hashlib.sha256(f"{s}|{p}|{o}".encode("utf-8")).hexdigest()[:24]


def write_links_sync(c: sqlite3.Connection, store: Any, record_id: str, link: LinkResult, *, visibility: str, chat_id: str, seen_at: str,
                     now: str) -> None:
    """In the record's write transaction: aliases for its entities, ``record_entities`` mention rows, and the record's
    evidence for each edge (replacing what an earlier version of the record supported)."""
    for r in link.refs:
        store._upsert_entity_sync(c, r.entity_id, r.name, r.kind, seen_at, mentions=0, aliases=r.aliases)
    c.execute("DELETE FROM record_entities WHERE record_id=? AND method<>'structural'", (record_id,))
    c.executemany("INSERT OR IGNORE INTO record_entities(record_id, entity_id, role, confidence, method) VALUES (?, ?, ?, ?, ?)",
                  [(record_id, r.entity_id, r.role, 0.9 if r.role == "reference" else 0.7, r.method) for r in link.refs])
    touched = {row["relation_id"] for row in c.execute("SELECT relation_id FROM relation_evidence WHERE record_id=?", (record_id,))}
    c.execute("DELETE FROM relation_evidence WHERE record_id=?", (record_id,))
    for e in link.edges:
        rid = relation_id(e.subject, e.predicate, e.object)
        c.execute("INSERT INTO relations(relation_id, subject_id, predicate, object_id, confidence, chat_id, status, observation_count, observed_at, created_at, updated_at, metadata) "
                  "VALUES (?, ?, ?, ?, ?, ?, 'restricted', 0, ?, ?, ?, '{}') ON CONFLICT(subject_id, predicate, object_id) DO NOTHING",
                  (rid, e.subject, e.predicate, e.object, e.confidence, chat_id, seen_at, now, now))
        real = c.execute("SELECT relation_id FROM relations WHERE subject_id=? AND predicate=? AND object_id=?", (e.subject, e.predicate, e.object)).fetchone()
        rid = real["relation_id"] if real else rid
        c.execute("INSERT OR REPLACE INTO relation_evidence(relation_id, record_id, memory_id, confidence, modality, method, span_start, span_end, visibility, created_at) "
                  "VALUES (?, ?, NULL, ?, ?, 'pattern', ?, ?, ?, ?)",
                  (rid, record_id, e.confidence, e.modality, e.span[0] if e.span else None, e.span[1] if e.span else None, visibility, now))
        touched.add(rid)
    for rid in touched:
        recompute_relation_sync(c, rid, now)


def drop_record_sync(c: sqlite3.Connection, record_id: str, now: str) -> None:
    """A deleted or redacted record supports nothing any more: its evidence goes, and every edge it touched is recomputed."""
    touched = {row["relation_id"] for row in c.execute("SELECT relation_id FROM relation_evidence WHERE record_id=?", (record_id,))}
    c.execute("DELETE FROM relation_evidence WHERE record_id=?", (record_id,))
    c.execute("DELETE FROM record_entities WHERE record_id=? AND method<>'structural'", (record_id,))
    for rid in touched:
        recompute_relation_sync(c, rid, now)


def recompute_relation_sync(c: sqlite3.Connection, rid: str, now: str) -> None:
    """confidence = max over evidence; modalities summarized; ``active`` (traversable) only with public evidence that is
    not negated, ``restricted`` with only restricted evidence, ``retracted`` with none."""
    import json
    rows = c.execute("SELECT confidence, modality, visibility FROM relation_evidence WHERE relation_id=?", (rid,)).fetchall()
    if not rows:
        c.execute("UPDATE relations SET status='retracted', observation_count=0, updated_at=? WHERE relation_id=?", (now, rid))
        return
    modalities: dict[str, int] = {}
    for r in rows:
        modalities[r["modality"]] = modalities.get(r["modality"], 0) + 1
    positive = [r for r in rows if r["modality"] != "negated"]
    public = [r for r in positive if r["visibility"] == "public"]
    status = "active" if public else ("restricted" if positive else "negated")
    conf = max((r["confidence"] for r in (positive or rows)), default=0.0)
    c.execute("UPDATE relations SET status=?, confidence=?, observation_count=?, updated_at=?, metadata=json_set(COALESCE(metadata, '{}'), '$.modalities', json(?)) "
              "WHERE relation_id=?", (status, conf, len(positive), now, json.dumps(modalities, sort_keys=True), rid))
