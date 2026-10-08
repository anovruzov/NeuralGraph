"""Domains: taxonomy, matching, classification and memberships (docs/mycelic/INGESTION.md §6, product decision 3).

* **Taxonomy.** A tenant-wide tree of stable dot-path ids (``infrastructure.ci-cd``) with renamable names, plus
  **personal domains** ``personal.<holder_id>.<slug>`` that exist only in their holder, are never published (heartbeats,
  ingest batches) and never route questions across the organization. Aliases map today's flat holder domains
  (``deployments``, ``support`` ...) onto taxonomy ids so existing routing keeps working.
* **Matching.** :func:`domains_overlap` — equal, or one is an ancestor of the other, after alias resolution.
* **Classification.** :class:`DomainClassifier`: deterministic rules first (source mapping, labels, keyword/regex/container/
  sender rules), then embedding centroids, then the ``classify_domains`` model task only for ambiguous records, choosing from
  a closed candidate list (an id outside the list is dropped). Content is data: at worst it mislabels a record.
* **Memberships.** A record belongs to up to 3 domains — memberships, never copies. Each carries confidence, method,
  model_version and taxonomy_version. Human corrections are sticky (automatic reclassification never removes or overrides
  them, and never re-adds a domain a person removed). History is append-only (enforced by triggers in the schema).
"""
from __future__ import annotations

import fnmatch
import math
import re
import sqlite3
import struct
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from NeuralGraph.chat_memory.textutil import fold, stem

from ..util import j, jl, now_iso
from .contract import get_logger
from .normalize import mask_secrets

logger = get_logger(__name__)

UNCLASSIFIED = "unclassified"
PERSONAL_PREFIX = "personal."
TAXONOMY_VERSION_DEFAULT = 1
_WORD_RE = re.compile(r"[a-z0-9]+")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


# ---------------------------------------------------------------------------------------------- taxonomy
@dataclass(frozen=True)
class Domain:
    domain_id: str
    name: str
    parent_id: str | None = None
    description: str = ""
    keywords: tuple[str, ...] = ()
    rules: Mapping[str, Any] = field(default_factory=dict)
    scope: str = "tenant"                 # tenant | personal
    status: str = "active"                # active | deprecated


def is_personal(domain_id: str) -> bool:
    return str(domain_id).startswith(PERSONAL_PREFIX)


def personal_domain_id(holder_id: str, slug: str) -> str:
    if not _SLUG_RE.match(slug or ""):
        raise ValueError("personal domain slug must be lower-case letters, digits and dashes")
    return f"{PERSONAL_PREFIX}{holder_id}.{slug}"


def publishable(domain_ids: Iterable[str]) -> list[str]:
    """Domain ids that may leave the holder (personal domains never do)."""
    return [d for d in domain_ids if d and not is_personal(d)]


class Taxonomy:
    def __init__(self, domains: Iterable[Domain], aliases: Mapping[str, str] | None = None, *, version: int = TAXONOMY_VERSION_DEFAULT) -> None:
        self.version = int(version)
        self.domains: dict[str, Domain] = {d.domain_id: d for d in domains}
        self.aliases: dict[str, str] = {str(k).lower(): v for k, v in (aliases or {}).items()}
        self._children: dict[str | None, list[str]] = {}
        for d in self.domains.values():
            self._children.setdefault(d.parent_id, []).append(d.domain_id)

    def resolve(self, d: str) -> str:
        """alias -> id; unknown strings stay as they are (legacy flat domains keep matching by equality)."""
        s = str(d)
        return self.aliases.get(s.lower(), s)

    def get(self, d: str) -> Domain | None:
        return self.domains.get(self.resolve(d))

    def ancestors(self, d: str) -> list[str]:
        out: list[str] = []
        cur = self.domains.get(self.resolve(d))
        seen: set[str] = set()
        while cur is not None and cur.parent_id and cur.parent_id not in seen:
            seen.add(cur.parent_id)
            out.insert(0, cur.parent_id)
            cur = self.domains.get(cur.parent_id)
        return out

    def children(self, d: str) -> list[str]:
        return [c for c in self._children.get(self.resolve(d), []) if self.domains[c].status == "active"]

    def top_level(self, *, scope: str | None = "tenant") -> list[str]:
        return [c for c in self._children.get(None, []) if self.domains[c].status == "active" and c != UNCLASSIFIED
                and (scope is None or self.domains[c].scope == scope)]

    def personal(self) -> list[str]:
        return [d for d, v in self.domains.items() if v.scope == "personal" and v.status == "active"]

    def path(self, d: str) -> str:
        rid = self.resolve(d)
        names = [self.domains[a].name for a in self.ancestors(rid) if a in self.domains]
        if rid in self.domains:
            names.append(self.domains[rid].name)
        return "/".join(names) or rid

    def with_domains(self, extra: Iterable[Domain]) -> "Taxonomy":
        return Taxonomy([*self.domains.values(), *extra], self.aliases, version=self.version)


def domains_overlap(a: Iterable[str], b: Iterable[str], tax: Taxonomy) -> bool:
    """True when some x in a and y in b are equal or one is an ancestor of the other (after alias resolution).
    ``*`` on either side matches everything; an empty side means no constraint (the behaviour before taxonomies)."""
    A0 = {tax.resolve(str(x).strip().lower()) for x in a if x and str(x).strip()}
    B0 = {tax.resolve(str(y).strip().lower()) for y in b if y and str(y).strip()}
    if not A0 or not B0 or "*" in A0 or "*" in B0:
        return True
    # a deprecated domain keeps its members but routes nothing new; a side left with only deprecated domains matches nothing
    A = {x for x in A0 if not _deprecated(tax, x)}
    B = {y for y in B0 if not _deprecated(tax, y)}
    return any(x == y or x in tax.ancestors(y) or y in tax.ancestors(x) for x in A for y in B)


def _deprecated(tax: Taxonomy, d: str) -> bool:
    dom = tax.domains.get(d)
    return dom is not None and dom.status != "active"


# (id suffix, name, keywords); top level: (id, name, description, keywords, subdomains)
_DEFAULTS: tuple[tuple[str, str, str, tuple[str, ...], tuple[tuple[str, str, tuple[str, ...]], ...]], ...] = (
    ("engineering", "Engineering", "Building and maintaining software.",
     ("code", "bug", "refactor", "api", "library", "dependency", "version", "release", "pull request", "test"),
     (("backend", "Backend", ("backend", "server", "endpoint", "microservice", "service")),
      ("frontend", "Frontend", ("frontend", "css", "react", "browser", "javascript", "ui")),
      ("mobile", "Mobile", ("ios", "android", "mobile app")),
      ("data-platform", "Data Platform", ("etl", "data pipeline", "warehouse", "kafka", "spark")),
      ("architecture", "Architecture", ("architecture", "design doc", "rfc", "adr")),
      ("dependencies", "Dependencies & Releases", ("dependency", "upgrade", "library", "version bump", "release notes", "package")),
      ("developer-tooling", "Developer Tooling", ("tooling", "linter", "ide", "build tool")),
      ("qa", "Testing & QA", ("test", "qa", "regression", "flaky")))),
    ("infrastructure", "Infrastructure", "Running systems: cloud, networks, deploys, reliability.",
     ("deploy", "deployment", "rollout", "rollback", "outage", "latency", "timeout", "kubernetes", "terraform", "pager", "incident"),
     (("cloud", "Cloud", ("aws", "gcp", "azure", "cloud", "region")),
      ("networking", "Networking", ("network", "dns", "vpn", "firewall", "load balancer")),
      ("ci-cd", "Build & Deploy", ("deploy", "deployment", "rollout", "rollback", "pipeline", "build", "ci")),
      ("observability", "Observability", ("monitoring", "metrics", "alert", "dashboard", "logging", "tracing", "latency")),
      ("reliability", "Incidents & SRE", ("outage", "incident", "pager", "on-call", "sre", "postmortem", "downtime")),
      ("databases", "Databases", ("database", "postgres", "mysql", "replica", "schema migration")),
      ("capacity", "Capacity", ("capacity", "scaling", "autoscaling", "quota")))),
    ("product", "Product", "What is built and why.",
     ("roadmap", "spec", "prd", "feature", "user story", "mockup", "adoption"),
     (("roadmap", "Roadmap", ("roadmap", "milestone", "quarter plan")),
      ("requirements", "Requirements", ("spec", "prd", "requirement", "user story")),
      ("design", "UX & Design", ("mockup", "ux", "wireframe", "prototype design")),
      ("analytics", "Analytics", ("adoption", "funnel", "analytics", "retention")),
      ("feedback", "Feedback", ("feedback", "survey", "nps")))),
    ("sales", "Sales", "Selling, accounts and revenue.",
     ("deal", "opportunity", "quote", "renewal", "churn", "account", "pricing", "contract value"),
     (("pipeline", "Pipeline", ("deal", "opportunity", "sales pipeline")),
      ("accounts", "Accounts", ("account", "customer account")),
      ("renewals", "Renewals", ("renewal", "churn")),
      ("pricing", "Pricing", ("pricing", "discount", "quote")),
      ("partnerships", "Partnerships", ("partner", "partnership", "reseller")))),
    ("finance", "Finance", "Money in and out.",
     ("budget", "invoice", "po", "purchase order", "forecast", "spend", "accrual"),
     (("budgeting", "Budgeting", ("budget",)),
      ("accounting", "Accounting", ("accrual", "ledger", "accounting")),
      ("procurement", "Procurement", ("purchase order", "po", "procurement")),
      ("billing", "Billing", ("billing", "invoice", "payment")),
      ("forecasting", "Forecasting", ("forecast",)))),
    ("legal", "Legal", "Contracts, compliance and disputes.",
     ("contract", "nda", "msa", "dpa", "clause", "gdpr", "compliance", "counsel"),
     (("contracts", "Contracts", ("contract", "nda", "msa", "dpa", "clause")),
      ("compliance", "Compliance", ("compliance", "audit", "regulation")),
      ("privacy", "Privacy", ("gdpr", "privacy", "personal data")),
      ("ip", "Intellectual Property", ("patent", "trademark", "copyright")),
      ("litigation", "Litigation", ("lawsuit", "litigation", "counsel")))),
    ("operations", "Operations", "Running the business: logistics, facilities, vendors, processes.",
     ("shipment", "warehouse", "vendor", "sop", "dispatch", "customs"),
     (("logistics", "Logistics", ("shipment", "dispatch", "freight", "delivery")),
      ("facilities", "Facilities", ("office", "facility", "maintenance")),
      ("vendors", "Vendors", ("vendor", "supplier")),
      ("processes", "Processes", ("sop", "process", "approval")),
      ("supply-chain", "Supply Chain", ("warehouse", "inventory", "customs", "supply")))),
    ("research", "Research", "Experiments, literature and prototypes.",
     ("experiment", "hypothesis", "paper", "benchmark", "prototype", "model"),
     (("experiments", "Experiments", ("experiment", "hypothesis", "ab test")),
      ("literature", "Literature", ("paper", "literature")),
      ("prototypes", "Prototypes", ("prototype", "proof of concept")),
      ("data-science", "Data Science", ("model", "dataset", "benchmark")))),
    ("customer-support", "Customer Support", "Helping customers.",
     ("ticket", "customer", "escalation", "sla", "complaint", "support case"),
     (("tickets", "Tickets", ("ticket", "support case")),
      ("escalations", "Escalations", ("escalation", "escalate")),
      ("customer-impact", "Customer Impact", ("customer impact", "affected customers")),
      ("knowledge-base", "Knowledge Base", ("faq", "kb", "how-to")),
      ("sla", "SLA", ("sla", "response time")))),
    ("security", "Security", "Protecting systems and data.",
     ("cve", "vulnerability", "phishing", "access review", "breach", "mfa", "secret"),
     (("vulnerabilities", "Vulnerabilities", ("cve", "vulnerability", "patch")),
      ("access-control", "Access Control", ("access review", "permission", "mfa", "sso")),
      ("incident-response", "Incident Response", ("breach", "phishing", "compromise")),
      ("threat-intel", "Threat Intelligence", ("threat", "malware")),
      ("secrets", "Secrets", ("secret", "credential", "key rotation")))),
    ("hr", "HR", "People: hiring, onboarding, performance, pay.",
     ("candidate", "interview", "offer", "onboarding", "review cycle", "payroll"),
     (("hiring", "Hiring", ("candidate", "interview", "offer", "hiring")),
      ("onboarding", "Onboarding", ("onboarding", "new hire")),
      ("performance", "Performance", ("review cycle", "performance review")),
      ("compensation", "Compensation", ("payroll", "salary", "compensation")),
      ("people-policies", "People Policies", ("leave", "pto", "handbook")))),
    ("executive-strategy", "Executive Strategy", "Direction, board and organization.",
     ("okr", "board", "strategy", "acquisition", "competitor", "reorg"),
     (("okrs", "OKRs", ("okr", "objective", "key result")),
      ("board", "Board", ("board", "board meeting")),
      ("m-and-a", "M&A", ("acquisition", "merger")),
      ("competitive", "Competitive", ("competitor", "competitive")),
      ("org-design", "Org Design", ("reorg", "org chart")))),
)

# today's flat holder domains (seed, tests, demo) -> taxonomy ids
DEFAULT_ALIASES: dict[str, str] = {
    "general": UNCLASSIFIED, "engineering.infrastructure": "infrastructure",
    "deployments": "infrastructure.ci-cd", "deploys": "infrastructure.ci-cd", "on-call": "infrastructure.reliability",
    "incidents": "infrastructure.reliability", "monitoring": "infrastructure.observability", "metrics": "infrastructure.observability",
    "latency": "infrastructure.observability", "vpn": "infrastructure.networking", "ops": "operations",
    "dispatch": "operations.logistics", "customs": "operations.supply-chain", "approvals": "operations.processes",
    "maintenance": "operations.facilities", "support": "customer-support", "escalation": "customer-support.escalations",
}


def default_taxonomy() -> Taxonomy:
    """The 12 default domains with their subdomains, the reserved ``unclassified`` domain and the legacy aliases."""
    out: list[Domain] = [Domain(UNCLASSIFIED, "Unclassified", None, "Nothing matched; needs review.")]
    for did, name, desc, kws, subs in _DEFAULTS:
        out.append(Domain(did, name, None, desc, kws))
        for suffix, sub_name, sub_kws in subs:
            out.append(Domain(f"{did}.{suffix}", sub_name, did, f"{name}: {sub_name}", sub_kws))
    return Taxonomy(out, DEFAULT_ALIASES, version=TAXONOMY_VERSION_DEFAULT)


# ---------------------------------------------------------------------------------------------- taxonomy persistence (holder copy)
def install_taxonomy_sync(c: sqlite3.Connection, tax: Taxonomy) -> None:
    """Replace the tenant rows of the holder's taxonomy copy (personal domains are kept)."""
    now = now_iso()
    c.execute("DELETE FROM domains WHERE scope='tenant'")
    c.execute("DELETE FROM domain_aliases")
    for d in tax.domains.values():
        if d.scope != "tenant":
            continue
        rules = dict(d.rules or {})
        rules.setdefault("keywords", list(d.keywords))
        c.execute("""INSERT OR REPLACE INTO domains(domain_id, parent_id, name, path, ancestors, description, rules, scope, status,
                                                    taxonomy_version, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'tenant', ?, ?, ?)""",
                  (d.domain_id, d.parent_id, d.name, tax.path(d.domain_id), j(tax.ancestors(d.domain_id)), d.description, j(rules),
                   d.status, tax.version, now))
    for alias, did in tax.aliases.items():
        c.execute("INSERT OR REPLACE INTO domain_aliases(alias, domain_id) VALUES (?, ?)", (alias, did))


def load_taxonomy_sync(c: sqlite3.Connection) -> Taxonomy:
    rows = c.execute("SELECT * FROM domains").fetchall()
    domains = []
    version = TAXONOMY_VERSION_DEFAULT
    for r in rows:
        rules = jl(r["rules"], {})
        domains.append(Domain(r["domain_id"], r["name"], r["parent_id"], r["description"], tuple(rules.get("keywords") or ()), rules,
                              r["scope"], r["status"]))
        if r["scope"] == "tenant":
            version = max(version, int(r["taxonomy_version"]))
    aliases = {r["alias"]: r["domain_id"] for r in c.execute("SELECT alias, domain_id FROM domain_aliases")}
    return Taxonomy(domains, aliases, version=version)


def add_personal_domain_sync(c: sqlite3.Connection, tax: Taxonomy, holder_id: str, slug: str, name: str, *,
                             keywords: Iterable[str] = (), description: str = "", parent_slug: str | None = None) -> Domain:
    did = personal_domain_id(holder_id, slug)
    parent = personal_domain_id(holder_id, parent_slug) if parent_slug else None
    if parent and parent not in tax.domains:
        raise KeyError(parent)
    d = Domain(did, name, parent, description, tuple(keywords), {"keywords": list(keywords)}, "personal")
    c.execute("""INSERT OR REPLACE INTO domains(domain_id, parent_id, name, path, ancestors, description, rules, scope, status, taxonomy_version,
                                                updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'personal', 'active', ?, ?)""",
              (did, parent, name, name, j([parent] if parent else []), description, j(d.rules), tax.version, now_iso()))
    return d


# ---------------------------------------------------------------------------------------------- features and results
@dataclass
class RecordFeatures:
    record_id: str
    title: str
    text: str
    source_app: str
    container_kind: str = ""
    container_name: str = ""
    labels: tuple[str, ...] = ()
    author: str = ""
    default_domain_ids: tuple[str, ...] = ()
    domain_hints: tuple[str, ...] = ()
    conversation_distribution: Mapping[str, float] = field(default_factory=dict)
    conversation_size: int = 0
    sensitivity: str = "internal"
    flags: tuple[str, ...] = ()
    allow_llm: bool = True
    vector: list[float] | None = None
    embed_model: str = ""


@dataclass
class Membership:
    domain_id: str
    confidence: float
    method: str
    model_version: str
    taxonomy_version: int
    is_primary: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)
    needs_review: bool = False


@dataclass(frozen=True)
class DomainClassifierConfig:
    SOURCE_MAPPING_CONF: float = 0.95
    LABEL_CONF: float = 0.90
    RULE_ACCEPT: float = 0.80
    EMB_ACCEPT: float = 0.60
    EMB_ADD: float = 0.75
    EMB_ACCEPT_SUB: float = 0.55
    MARGIN: float = 0.10
    LLM_FLOOR: float = 0.45
    CONV_PRIOR_MIN_N: int = 20
    CONV_PRIOR_SHARE: float = 0.7
    CONV_PRIOR_WEIGHT: float = 0.5
    MAX_DOMAINS: int = 3
    SHORT_TEXT_CHARS: int = 80
    KEYWORD_WEIGHT: float = 0.3
    TITLE_KEYWORD_WEIGHT: float = 0.45
    LLM_TEXT_CHARS: int = 4000
    LLM_DAILY_CALLS: int = 2000


# method priority for ties (§6.3): source_mapping > label > rule > llm > embedding > conversation_prior
_METHOD_RANK = {"human": 0, "source_mapping": 1, "label": 2, "rule": 3, "llm": 4, "embedding": 5, "conversation_prior": 6, "fallback": 7}


@dataclass
class _Score:
    conf: float = 0.0
    method: str = "embedding"
    evidence: dict[str, Any] = field(default_factory=dict)


class _Candidates(dict):
    def bump(self, d: str, conf: float, method: str, **evidence: Any) -> None:
        conf = max(0.0, min(1.0, float(conf)))
        cur = self.get(d)
        if cur is None or conf > cur.conf + 1e-9 or (abs(conf - cur.conf) <= 1e-9 and _METHOD_RANK[method] < _METHOD_RANK[cur.method]):
            ev = dict(cur.evidence) if cur else {}
            ev.update({k: v for k, v in evidence.items() if v is not None})
            self[d] = _Score(conf, method, ev)
        elif evidence:
            cur.evidence.update({k: v for k, v in evidence.items() if v is not None and k not in cur.evidence})

    def set(self, d: str, conf: float, method: str, **evidence: Any) -> None:
        self[d] = _Score(max(0.0, min(1.0, float(conf))), method, {k: v for k, v in evidence.items() if v is not None})


# ---------------------------------------------------------------------------------------------- text matching
def _tokens(text: str) -> list[str]:
    return [stem(w) for w in _WORD_RE.findall(fold(text or ""))]


def _grams(tokens: list[str], n: int = 4) -> set[tuple[str, ...]]:
    out: set[tuple[str, ...]] = set()
    for k in range(1, n + 1):
        for i in range(0, len(tokens) - k + 1):
            out.add(tuple(tokens[i:i + k]))
    return out


def _kw(keyword: str) -> tuple[str, ...]:
    return tuple(_tokens(keyword))


# ---------------------------------------------------------------------------------------------- centroids
def _norm(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _cos(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / ((math.sqrt(sum(x * x for x in a)) or 1.0) * (math.sqrt(sum(y * y for y in b)) or 1.0))


def calibration_for(dim: int, model: str) -> tuple[float, float]:
    """(floor_sim, ceil_sim): shipped defaults until ``mycelic ingest calibrate`` measures them per model."""
    if dim == 256 or "hash" in model or "fake" in model:
        return 0.05, 0.45
    return 0.20, 0.55


def embed_model_name(embedder: Any) -> str:
    return f"{getattr(embedder, 'name', 'embed')}:{getattr(embedder, 'model', '') or ''}"


class CentroidIndex:
    """Domain centroids per embedding model: the L2-normalized mean of the seed text (name, description, keywords) and
    the vectors of positive examples, minus half the mean of negative examples. Persisted in ``domain_centroids``."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], tuple[list[float], float, float]] = {}

    def invalidate(self, domain_ids: Iterable[str] | None = None) -> None:
        if domain_ids is None:
            self._cache.clear()
            return
        for key in [k for k in self._cache if k[0] in set(domain_ids)]:
            self._cache.pop(key, None)

    async def get(self, c: sqlite3.Connection, tax: Taxonomy, domain_id: str, embedder: Any, *,
                  examples: Mapping[str, tuple[list[list[float]], list[list[float]]]] | None = None) -> tuple[list[float], float, float] | None:
        model = embed_model_name(embedder)
        key = (domain_id, model)
        if key in self._cache:
            return self._cache[key]
        row = c.execute("SELECT vector, dim, floor_sim, ceil_sim FROM domain_centroids WHERE domain_id=? AND embed_model=?", (domain_id, model)).fetchone()
        if row is not None:
            vec = list(struct.unpack(f"{row['dim']}f", row["vector"]))
            self._cache[key] = (vec, float(row["floor_sim"]), float(row["ceil_sim"]))
            return self._cache[key]
        d = tax.domains.get(domain_id)
        if d is None:
            return None
        seed = " ".join([d.name, d.description, *d.keywords, *d.keywords])
        try:
            v = [float(x) for x in await embedder.embed(seed)]
        except Exception:
            logger.warning("centroid embedding failed for domain %s", domain_id)
            return None
        pos, neg = (examples or {}).get(domain_id, ([], []))
        n_seed = 3
        acc = [x * n_seed for x in _norm(v)]
        for p in pos:
            if len(p) == len(acc):
                acc = [a + b for a, b in zip(acc, _norm(p))]
        vec = _norm(acc)
        if neg:
            mean_neg = _norm([sum(col) / len(neg) for col in zip(*[_norm(n) for n in neg if len(n) == len(vec)])]) if any(len(n) == len(vec) for n in neg) else None
            if mean_neg:
                vec = _norm([a - 0.5 * b for a, b in zip(vec, mean_neg)])
        floor, ceil = calibration_for(len(vec), model)
        c.execute("""INSERT OR REPLACE INTO domain_centroids(domain_id, embed_model, dim, vector, n_seed, n_examples, floor_sim, ceil_sim, version, updated_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                  (domain_id, model, len(vec), struct.pack(f"{len(vec)}f", *vec), n_seed, len(pos) + len(neg), floor, ceil, now_iso()))
        self._cache[key] = (vec, floor, ceil)
        return self._cache[key]


# ---------------------------------------------------------------------------------------------- the classifier
class DomainClassifier:
    """Rules -> embedding centroids -> ``classify_domains`` (closed candidate list), §6.3.

    ``router`` is a ``ModelRouter`` (``run_task``) or ``None`` (no LLM stage); ``conn`` is the holder connection used for
    centroids and labelled examples (read in the classify stage, before the write transaction).
    """

    def __init__(self, taxonomy: Taxonomy, *, conn: sqlite3.Connection | None = None, router: Any | None = None, tenant_id: str | None = None,
                 config: DomainClassifierConfig | None = None, centroids: CentroidIndex | None = None) -> None:
        self.taxonomy = taxonomy
        self.conn = conn
        self.router = router
        self.tenant_id = tenant_id
        self.config = config or DomainClassifierConfig()
        self.centroids = centroids or CentroidIndex()
        self.llm_calls_today = 0
        self.llm_day = ""
        self.counters: dict[str, int] = {}

    def _count(self, key: str) -> None:
        self.counters[key] = self.counters.get(key, 0) + 1

    # ---- stage 1
    def rule_candidates(self, f: RecordFeatures) -> _Candidates:
        cfg, tax = self.config, self.taxonomy
        cand = _Candidates()
        rules_v = f"rules@t{tax.version}"
        for d in f.default_domain_ids:
            rid = tax.resolve(d)
            if rid in tax.domains:
                cand.bump(rid, cfg.SOURCE_MAPPING_CONF, "source_mapping", rules=["source"], model_version=rules_v)
        for d in f.domain_hints:
            rid = tax.resolve(d)
            if rid in tax.domains:
                cand.bump(rid, cfg.LABEL_CONF - 0.1, "rule", rules=["hint"], model_version=rules_v)
        body_tokens = _tokens(f.text[:20000])
        title_tokens = _tokens(f.title)
        body_grams, title_grams = _grams(body_tokens), _grams(title_tokens)
        label_set = {str(x).lower() for x in f.labels}
        for did, d in tax.domains.items():
            if d.status != "active" or did == UNCLASSIFIED:
                continue
            rules = d.rules or {}
            for lab in rules.get("labels") or ():
                apps = lab.get("apps")
                if str(lab.get("value", "")).lower() in label_set and (not apps or f.source_app in apps):
                    cand.bump(did, max(float(lab.get("weight", cfg.LABEL_CONF)), cfg.LABEL_CONF), "label", rules=[f"label:{lab.get('value')}"],
                              model_version=rules_v)
            weights: list[float] = []
            matched: list[str] = []
            kw_weight = float(rules.get("keyword_weight") or cfg.KEYWORD_WEIGHT)
            for kw in d.keywords:
                g = _kw(kw)
                if not g:
                    continue
                if g in title_grams:
                    weights.append(cfg.TITLE_KEYWORD_WEIGHT)
                    matched.append(kw)
                elif g in body_grams:
                    weights.append(kw_weight)
                    matched.append(kw)
            for rx in rules.get("regex") or ():
                pat = str(rx.get("pattern") or "")
                if not pat or len(pat) > 256:
                    continue
                try:
                    hay = {"title": f.title, "body": f.text[:20000]}.get(rx.get("field") or "any", f"{f.title}\n{f.text[:20000]}")
                    if re.search(pat, hay, re.IGNORECASE):
                        weights.append(float(rx.get("weight", 0.6)))
                        matched.append(f"regex:{rx.get('id', '')}")
                except re.error:
                    continue
            for ct in rules.get("containers") or ():
                apps = ct.get("apps")
                if f.container_name and fnmatch.fnmatch(f.container_name.lower(), str(ct.get("pattern", "")).lower()) and (not apps or f.source_app in apps):
                    weights.append(float(ct.get("weight", 0.9)))
                    matched.append(f"container:{ct.get('pattern')}")
            for sd in rules.get("senders") or ():
                if f.author and fnmatch.fnmatch(f.author.lower(), str(sd.get("pattern", "")).lower()):
                    weights.append(float(sd.get("weight", 0.7)))
                    matched.append(f"sender:{sd.get('pattern')}")
            if not weights:
                continue
            score = 1.0
            for w in weights:
                score *= (1.0 - max(0.0, min(0.99, w)))
            score = 1.0 - score
            negatives = [n for n in rules.get("negative") or () if _kw(n) and (_kw(n) in body_grams or _kw(n) in title_grams)]
            if negatives:
                score /= 2.0
            cand.bump(did, score, "rule", rules=matched[:8], model_version=rules_v)
        return cand

    # ---- stage 2
    async def _embedding_stage(self, f: RecordFeatures, cand: _Candidates, accepted: set[str], embedder: Any | None) -> None:
        cfg, tax = self.config, self.taxonomy
        if f.vector is None or embedder is None or self.conn is None:
            return
        examples = self._examples(f.vector) if self.conn is not None else {}
        model = embed_model_name(embedder)

        async def score(d: str) -> float:
            got = await self.centroids.get(self.conn, tax, d, embedder, examples=examples)
            if got is None or len(got[0]) != len(f.vector or []):
                return 0.0
            vec, floor, ceil = got
            return max(0.0, min(1.0, (_cos(f.vector or [], vec) - floor) / max(1e-6, ceil - floor)))

        level1 = tax.top_level() + [d for d in tax.personal() if not tax.domains[d].parent_id]
        for d in level1:
            s = await score(d)
            if s > 0:
                cand.bump(d, s, "embedding", similarity=round(s, 3), model_version=f"{model}@c1")
        parents = {d for d, sc in cand.items() if sc.conf >= cfg.EMB_ACCEPT and not tax.domains.get(d, Domain(d, d)).parent_id} | \
                  {d for d in accepted if not tax.domains.get(d, Domain(d, d)).parent_id}
        for parent in sorted(parents):
            for child in tax.children(parent):
                s = await score(child)
                if s >= cfg.EMB_ACCEPT_SUB:
                    cand.bump(child, s, "embedding", similarity=round(s, 3), model_version=f"{model}@c1")
        if len(f.text) < cfg.SHORT_TEXT_CHARS and f.conversation_size >= cfg.CONV_PRIOR_MIN_N:
            for d, share in f.conversation_distribution.items():
                if share >= cfg.CONV_PRIOR_SHARE and d in tax.domains:
                    cand.bump(d, cfg.CONV_PRIOR_WEIGHT * share + 0.4, "conversation_prior", share=round(share, 3), model_version=f"conv@t{tax.version}")

    def _examples(self, _vector: list[float]) -> dict[str, tuple[list[list[float]], list[list[float]]]]:
        """Labelled examples (from human corrections) as record vectors: the mean of the record's active chunk embeddings."""
        out: dict[str, tuple[list[list[float]], list[list[float]]]] = {}
        if self.conn is None:
            return out
        rows = self.conn.execute("SELECT record_id, domain_id, label FROM domain_examples").fetchall()
        for r in rows:
            embs = [e["embedding"] for e in self.conn.execute(
                "SELECT m.embedding FROM record_memories rm JOIN memories m ON m.memory_id = rm.memory_id "
                "WHERE rm.record_id=? AND m.status='active' AND m.embedding IS NOT NULL", (r["record_id"],)).fetchall()]
            vecs = [list(struct.unpack(f"{len(b) // 4}f", b)) for b in embs if b]
            if not vecs:
                continue
            dim = len(vecs[0])
            mean = [sum(v[i] for v in vecs if len(v) == dim) / len(vecs) for i in range(dim)]
            pos, neg = out.setdefault(r["domain_id"], ([], []))
            (pos if int(r["label"]) > 0 else neg).append(mean)
        return out

    # ---- stage 3
    def _llm_allowed(self, f: RecordFeatures) -> bool:
        if self.router is None or not f.allow_llm or f.sensitivity == "restricted":
            return False
        if "suspicious_instructions" in f.flags or "contains_secret" in f.flags:
            return False
        today = now_iso()[:10]
        if today != self.llm_day:
            self.llm_day, self.llm_calls_today = today, 0
        if self.llm_calls_today >= self.config.LLM_DAILY_CALLS:
            self._count("llm_budget_exhausted")
            return False
        return True

    async def _llm_stage(self, f: RecordFeatures, cand: _Candidates, top5: list[str]) -> None:
        tax = self.taxonomy
        text, _ = mask_secrets(f.text[: self.config.LLM_TEXT_CHARS])
        candidates = []
        for d in top5:
            dom = tax.domains.get(d)
            if dom is None:
                continue
            candidates.append({"domain_id": d, "name": dom.name, "path": tax.path(d), "description": dom.description,
                               "keywords": list(dom.keywords), "similarity": round(cand[d].conf, 3) if d in cand else 0.0})
        inp = {"record": {"title": f.title, "text": text, "source_app": f.source_app, "container_kind": f.container_kind, "labels": list(f.labels)},
               "candidates": candidates, "max_domains": self.config.MAX_DOMAINS}
        self.llm_calls_today += 1
        try:
            out = await self.router.run_task("classify_domains", inp, tenant_id=self.tenant_id, tier="light")
        except Exception as exc:
            self._count("llm_error")
            logger.warning("classify_domains failed (%s); keeping rule and embedding results", type(exc).__name__)
            return
        allowed = {c["domain_id"] for c in candidates}
        for item in out.get("domains") or []:
            if not isinstance(item, dict):
                continue
            did = str(item.get("domain_id") or "")
            if did not in allowed:
                self._count("llm_out_of_set")      # closed set: anything else is dropped
                continue
            try:
                conf = float(item.get("confidence"))
            except (TypeError, ValueError):
                conf = 0.5
            cand.set(did, conf, "llm", rationale=str(item.get("rationale") or "")[:200], model_version="llm:light@classify_domains/1")

    # ---- pick
    def _pick(self, cand: _Candidates) -> list[Membership]:
        cfg, tax = self.config, self.taxonomy
        ranked = sorted(cand.items(), key=lambda kv: (-kv[1].conf, _METHOD_RANK[kv[1].method], kv[0]))

        def usable(d: str) -> bool:
            return d in tax.domains and tax.domains[d].status == "active" and d != UNCLASSIFIED

        def emb_child(d: str, sc: _Score) -> bool:
            return sc.method == "embedding" and bool(tax.domains[d].parent_id)

        chosen: list[str] = []
        for d, sc in ranked:
            if not usable(d) or emb_child(d, sc):
                continue
            if sc.method in ("source_mapping", "label", "human"):
                accept = True
            elif not chosen:
                accept = sc.conf >= (cfg.RULE_ACCEPT if sc.method == "rule" else cfg.EMB_ACCEPT)
            else:
                accept = sc.conf >= max(cfg.EMB_ADD, cfg.RULE_ACCEPT if sc.method == "rule" else 0.0)
            if accept:
                chosen.append(d)
        # subdomains found by embeddings are accepted inside an accepted parent, at the parent's position
        for d, sc in ranked:
            if usable(d) and emb_child(d, sc) and d not in chosen and sc.conf >= cfg.EMB_ACCEPT_SUB:
                anc = [a for a in tax.ancestors(d) if a in chosen]
                if anc:
                    chosen.insert(min(chosen.index(a) for a in anc), d)
        # the most specific domain wins over its ancestors (ancestry already matches through domains_overlap)
        pruned = [d for d in dict.fromkeys(chosen) if not any(d in tax.ancestors(o) for o in chosen if o != d)]
        out = []
        for d in pruned[: cfg.MAX_DOMAINS]:
            sc = cand[d]
            mv = sc.evidence.get("model_version") or f"rules@t{tax.version}"
            ev = {k: v for k, v in sc.evidence.items() if k != "model_version"}
            out.append(Membership(d, round(sc.conf, 4), sc.method, mv, tax.version, evidence=ev))
        if out:
            out[0].is_primary = True
        return out

    async def classify(self, f: RecordFeatures, *, embedder: Any | None = None) -> list[Membership]:
        cfg = self.config
        cand = self.rule_candidates(f)
        accepted = {d for d, sc in cand.items() if sc.conf >= cfg.RULE_ACCEPT}
        if len(accepted) < cfg.MAX_DOMAINS:
            await self._embedding_stage(f, cand, accepted, embedder)
        ranked = sorted(cand.items(), key=lambda kv: (-kv[1].conf, _METHOD_RANK[kv[1].method]))
        top1 = ranked[0][1].conf if ranked else 0.0
        top2 = ranked[1][1].conf if len(ranked) > 1 else 0.0
        strong = ranked and ranked[0][1].method in ("source_mapping", "label")
        ambiguous = not strong and (top1 < cfg.EMB_ACCEPT or (top2 >= cfg.LLM_FLOOR and top1 - top2 < cfg.MARGIN))
        if ambiguous and top1 >= cfg.LLM_FLOOR and self._llm_allowed(f):
            await self._llm_stage(f, cand, [d for d, _ in ranked[:5]])
        chosen = self._pick(cand)
        for m in chosen:
            self._count(f"method:{m.method}")
        if chosen:
            return chosen
        self._count("method:fallback")
        return [Membership(UNCLASSIFIED, 0.0, "fallback", f"rules@t{self.taxonomy.version}", self.taxonomy.version, is_primary=True,
                           evidence={"top": round(top1, 3)}, needs_review=True)]


# ---------------------------------------------------------------------------------------------- memberships (sticky, append-only history)
def _history_sync(c: sqlite3.Connection, record_id: str, domain_id: str, action: str, before: Mapping[str, Any], after: Mapping[str, Any], *,
                  method: str, actor_type: str, actor_id: str | None, reason: str, at: str) -> None:
    # no record content and no model rationale in history: it outlives a purge
    c.execute("""INSERT INTO domain_membership_history(record_id, domain_id, action, before, after, method, actor_type, actor_id, reason, at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (record_id, domain_id, action, j(dict(before)), j(dict(after)), method, actor_type,
                                                         actor_id, reason, at))


def _row_state(r: Mapping[str, Any] | None) -> dict[str, Any]:
    if r is None:
        return {}
    return {"status": r["status"], "confidence": r["confidence"], "method": r["method"], "is_primary": bool(r["is_primary"]),
            "model_version": r["model_version"], "taxonomy_version": r["taxonomy_version"]}


def active_memberships_sync(c: sqlite3.Connection, record_id: str) -> list[dict[str, Any]]:
    rows = c.execute("SELECT * FROM domain_memberships WHERE record_id=? AND status='active' ORDER BY is_primary DESC, confidence DESC, domain_id",
                     (record_id,)).fetchall()
    return [dict(r, evidence=jl(r["evidence"], {})) for r in rows]


def apply_memberships_sync(c: sqlite3.Connection, record_id: str, proposed: list[Membership], *, reason: str = "classify",
                           max_domains: int = 3, now: str | None = None) -> list[dict[str, Any]]:
    """Write the classifier's proposal for one record. Human rows (active *or* removed) are never touched by this path."""
    now = now or now_iso()
    existing = {r["domain_id"]: r for r in c.execute("SELECT * FROM domain_memberships WHERE record_id=?", (record_id,)).fetchall()}
    human = {d for d, r in existing.items() if r["method"] == "human"}
    human_active = [d for d in human if existing[d]["status"] == "active"]
    slots = max(0, max_domains - len(human_active))
    auto = [m for m in proposed if m.domain_id not in human][:slots]
    keep = {m.domain_id for m in auto}
    for m in auto:
        r = existing.get(m.domain_id)
        after = {"status": "active", "confidence": m.confidence, "method": m.method, "model_version": m.model_version,
                 "taxonomy_version": m.taxonomy_version}
        if r is None:
            c.execute("""INSERT INTO domain_memberships(record_id, domain_id, confidence, method, is_primary, status, model_version, taxonomy_version,
                                                        evidence, corrected_by, created_at, updated_at)
                         VALUES (?, ?, ?, ?, 0, 'active', ?, ?, ?, NULL, ?, ?)""",
                      (record_id, m.domain_id, m.confidence, m.method, m.model_version, m.taxonomy_version, j(m.evidence), now, now))
            _history_sync(c, record_id, m.domain_id, "add", {}, after, method=m.method, actor_type="system", actor_id=None, reason=reason, at=now)
        elif r["status"] != "active" or abs(float(r["confidence"]) - m.confidence) > 1e-6 or r["method"] != m.method or r["model_version"] != m.model_version:
            c.execute("""UPDATE domain_memberships SET status='active', confidence=?, method=?, model_version=?, taxonomy_version=?, evidence=?, updated_at=?
                         WHERE record_id=? AND domain_id=?""",
                      (m.confidence, m.method, m.model_version, m.taxonomy_version, j(m.evidence), now, record_id, m.domain_id))
            action = "add" if r["status"] != "active" else ("reclassify" if r["method"] != m.method else "confidence_change")
            _history_sync(c, record_id, m.domain_id, action, _row_state(r), after, method=m.method, actor_type="system", actor_id=None,
                          reason=reason, at=now)
    for d, r in existing.items():
        if d in human or d in keep or r["status"] != "active":
            continue
        c.execute("UPDATE domain_memberships SET status='removed', is_primary=0, updated_at=? WHERE record_id=? AND domain_id=?", (now, record_id, d))
        _history_sync(c, record_id, d, "remove", _row_state(r), {"status": "removed"}, method=r["method"], actor_type="system", actor_id=None,
                      reason=reason, at=now)
    _settle_primary_sync(c, record_id, preferred=[m.domain_id for m in auto if m.is_primary] + [m.domain_id for m in auto], now=now,
                         actor_type="system", actor_id=None, reason=reason)
    return active_memberships_sync(c, record_id)


def _settle_primary_sync(c: sqlite3.Connection, record_id: str, *, preferred: list[str], now: str, actor_type: str, actor_id: str | None,
                         reason: str, force: str | None = None) -> None:
    rows = {r["domain_id"]: r for r in c.execute("SELECT * FROM domain_memberships WHERE record_id=? AND status='active'", (record_id,)).fetchall()}
    current = next((d for d, r in rows.items() if r["is_primary"]), None)
    if force:
        target = force
    elif current and rows[current]["method"] == "human":
        target = current                       # a primary chosen by a person is sticky
    else:
        target = next((d for d in preferred if d in rows), None) or current or next(iter(sorted(rows, key=lambda d: -float(rows[d]["confidence"]))), None)
    if target == current:
        return
    if current:
        c.execute("UPDATE domain_memberships SET is_primary=0, updated_at=? WHERE record_id=? AND domain_id=?", (now, record_id, current))
    if target:
        c.execute("UPDATE domain_memberships SET is_primary=1, updated_at=? WHERE record_id=? AND domain_id=?", (now, record_id, target))
        _history_sync(c, record_id, target, "primary_change", {"primary": current}, {"primary": target}, method=rows[target]["method"] if target in rows else "human",
                      actor_type=actor_type, actor_id=actor_id, reason=reason, at=now)


def correct_domains_sync(c: sqlite3.Connection, record_id: str, *, add: Iterable[str] = (), remove: Iterable[str] = (), primary: str | None = None,
                         actor_id: str, reason: str = "", taxonomy: Taxonomy, max_domains: int = 3, now: str | None = None) -> list[dict[str, Any]]:
    """A person's correction: rows become ``method='human'`` with confidence 1.0 (added) or a sticky removal; labelled
    examples are recorded for centroid learning; the change is appended to the history."""
    now = now or now_iso()
    add_ids = [taxonomy.resolve(d) for d in add]
    remove_ids = [taxonomy.resolve(d) for d in remove]
    if primary:
        primary = taxonomy.resolve(primary)
        if primary not in add_ids:
            add_ids.append(primary)
    for d in add_ids + remove_ids:
        if d not in taxonomy.domains:
            raise KeyError(d)
    existing = {r["domain_id"]: r for r in c.execute("SELECT * FROM domain_memberships WHERE record_id=?", (record_id,)).fetchall()}
    for d in add_ids:
        r = existing.get(d)
        after = {"status": "active", "confidence": 1.0, "method": "human", "model_version": "human", "taxonomy_version": taxonomy.version}
        if r is None:
            c.execute("""INSERT INTO domain_memberships(record_id, domain_id, confidence, method, is_primary, status, model_version, taxonomy_version,
                                                        evidence, corrected_by, created_at, updated_at)
                         VALUES (?, ?, 1.0, 'human', 0, 'active', 'human', ?, '{}', ?, ?, ?)""", (record_id, d, taxonomy.version, actor_id, now, now))
        else:
            c.execute("""UPDATE domain_memberships SET status='active', confidence=1.0, method='human', model_version='human', taxonomy_version=?,
                         corrected_by=?, updated_at=? WHERE record_id=? AND domain_id=?""", (taxonomy.version, actor_id, now, record_id, d))
        _history_sync(c, record_id, d, "add", _row_state(r), after, method="human", actor_type="user", actor_id=actor_id, reason=reason, at=now)
        c.execute("INSERT OR REPLACE INTO domain_examples(record_id, domain_id, label, source, created_at) VALUES (?, ?, 1, 'human_correction', ?)",
                  (record_id, d, now))
    for d in remove_ids:
        r = existing.get(d)
        if r is None:
            c.execute("""INSERT INTO domain_memberships(record_id, domain_id, confidence, method, is_primary, status, model_version, taxonomy_version,
                                                        evidence, corrected_by, created_at, updated_at)
                         VALUES (?, ?, 1.0, 'human', 0, 'removed', 'human', ?, '{}', ?, ?, ?)""", (record_id, d, taxonomy.version, actor_id, now, now))
        else:
            c.execute("""UPDATE domain_memberships SET status='removed', is_primary=0, method='human', model_version='human', corrected_by=?, updated_at=?
                         WHERE record_id=? AND domain_id=?""", (actor_id, now, record_id, d))
        _history_sync(c, record_id, d, "remove", _row_state(r), {"status": "removed", "method": "human"}, method="human", actor_type="user",
                      actor_id=actor_id, reason=reason, at=now)
        c.execute("INSERT OR REPLACE INTO domain_examples(record_id, domain_id, label, source, created_at) VALUES (?, ?, -1, 'human_correction', ?)",
                  (record_id, d, now))
    active = c.execute("SELECT domain_id, method, confidence FROM domain_memberships WHERE record_id=? AND status='active' "
                       "ORDER BY (method='human') DESC, confidence DESC", (record_id,)).fetchall()
    if len(active) > max_domains:
        # people outrank the classifier: drop the weakest automatic rows; more human rows than the cap is an error
        humans = [r for r in active if r["method"] == "human"]
        if len(humans) > max_domains:
            raise ValueError(f"a record belongs to at most {max_domains} domains")
        for r in active[max_domains:]:
            c.execute("UPDATE domain_memberships SET status='removed', is_primary=0, updated_at=? WHERE record_id=? AND domain_id=?", (now, record_id, r["domain_id"]))
            _history_sync(c, record_id, r["domain_id"], "remove", {"status": "active", "method": r["method"]}, {"status": "removed"}, method=r["method"],
                          actor_type="user", actor_id=actor_id, reason="cap after correction", at=now)
    if primary:
        c.execute("UPDATE domain_memberships SET is_primary=0 WHERE record_id=? AND domain_id<>?", (record_id, primary))
        cur = c.execute("SELECT is_primary FROM domain_memberships WHERE record_id=? AND domain_id=?", (record_id, primary)).fetchone()
        if not (cur and cur["is_primary"]):
            c.execute("UPDATE domain_memberships SET is_primary=1, updated_at=? WHERE record_id=? AND domain_id=?", (now, record_id, primary))
            _history_sync(c, record_id, primary, "primary_change", {}, {"primary": primary}, method="human", actor_type="user", actor_id=actor_id,
                          reason=reason, at=now)
    else:
        _settle_primary_sync(c, record_id, preferred=add_ids, now=now, actor_type="user", actor_id=actor_id, reason=reason)
    return active_memberships_sync(c, record_id)


def membership_history_sync(c: sqlite3.Connection, record_id: str) -> list[dict[str, Any]]:
    return [dict(r, before=jl(r["before"], {}), after=jl(r["after"], {}))
            for r in c.execute("SELECT * FROM domain_membership_history WHERE record_id=? ORDER BY id", (record_id,)).fetchall()]


def domain_counts_sync(c: sqlite3.Connection, *, include_personal: bool = False) -> dict[str, int]:
    rows = c.execute("""SELECT dm.domain_id, COUNT(*) AS n FROM domain_memberships dm JOIN ingest_records r ON r.record_id = dm.record_id
                        WHERE dm.status='active' AND r.deletion_status='live' GROUP BY dm.domain_id""").fetchall()
    return {r["domain_id"]: int(r["n"]) for r in rows if include_personal or not is_personal(r["domain_id"])}
