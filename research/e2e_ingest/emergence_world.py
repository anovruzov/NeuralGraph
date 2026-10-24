"""The EMERGENCE world as the collective's load: the adapter from ``research.mycelic`` (``build_org``,
``build_corpus``, ``make_gold``; imported, never edited) to the routing spike's record and world interfaces
(``docs/collective/E2E-INGEST.md`` sections 3.1 to 3.3 and 3.5; M16, M21).

* **One pack record per EMERGENCE record** (section 3.3): ``record_ref`` ``em{seed}-{rid:07d}`` (``rid`` the row
  index in ``corpus.recs``); ``site`` the lower-cased name of ``org.ancestor_at(uid, SITE)``; ``received_date``
  ``2024-01-01 + t days``; ``language`` ``en``; ``codes`` ``[]``; ``entities`` ``{"entity": [the anchor's canonical
  id]}``; ``narrative`` ``corpus.text(rid)`` exactly; ``persons`` ``{}``; ``reporter`` ``u{uid}``; no origin;
  ``synthetic`` true.
* **One site per org SITE node** (section 3.2): site ids are the org node names lower-cased (``r00-site-00``).
* **The counts the brief fixes** (section 3.1, :data:`WORLD_TABLE`): :func:`check_world_counts` refuses a world whose
  users, sites, teams, records, patterns, Gold or decoy counts differ from the table's row for its size.
* **Canonical entity ids.** The pack's entity type is alias-only and its aliases are every EMERGENCE entity name. The
  loader refuses an alias whose folded form equals its target's folded form (``packs/loader.py`` ``_aliases``, "alias
  chain"), and folding lower-cases, so the id cannot be the upper-cased name alone: the canonical id is ``E-`` plus
  the upper-cased name (``E-NIMBUS-PAY``), which matches the alias-only id pattern (``loader.py`` ``ALIAS_ONLY_ID_RE``).
  This is the one place the build departs from the brief's wording "upper-cased names" and it is recorded in
  ``docs/collective/BUILD-E2E.md``.
* **The pack's predicates** (section 3.4): the 7 causal chains, ``chain_supply`` .. ``chain_network`` in the order of
  ``CAUSAL_CHAINS``, and ``revenue_miss`` on its own (it ends two chains). :func:`pack_predicate` maps an EMERGENCE
  predicate to the pack's; background predicates map to None.
* **The pipeline and the candidates** (``build_emergence_world``): the shipped ``run_pipeline`` ingests the records
  (M16: nothing here ingests first; a duplicate or rejection makes ``run_pipeline`` raise), ``detect`` runs at the
  pipeline's ``as_of`` on channel X with the tie salt, and the candidates are every candidate with a snapshot, ordered
  by (score descending, ``sha256(tie_salt|key)``), the first ``top_n`` (M21: no evaluation window).
* **Gold's labels**: the spike's labels shape (``world.candidate_label``): a routed candidate is *planted* when its
  key is a Gold-discoverable pattern's (canonical anchor, chain predicate) and its snapshot week lies in the
  pattern's window ``[iso_week(start + min(facet_times)), iso_week(start + max(facet_times)) + 4 weeks]``.
* **The 20 fixed queries** (section 3.5): HQ's question for each of the first 20 candidates in (``as_of``, key) order,
  built from the candidate alone with the pinned ``question_window`` and ``build_question``.

Harness side. No record text and no record ref leaves this module in any log or report; the record dicts are handed
to the pipeline and to the pack's fixtures only.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from mycelic.collective.detect.detectors import detect
from mycelic.collective.detect.rules import series_key
from mycelic.collective.edge.weeks import iso_week
from mycelic.collective.evaluate.baselines import closing_date, run_pipeline, world_weeks
from mycelic.collective.jsonio import sha256_hex
from mycelic.collective.packs.connector import RECORD_KEYS
from mycelic.collective.packs.loader import ALIAS_ONLY_ID_RE, FrozenPack, load_pack_dir
from mycelic.collective.pushdown.questions import build_question, question_window
from research.mycelic.corpus import (CAUSAL_CHAINS, CHAIN_OF_PRED, PRED_ID, PREDICATES, Corpus, Gold, build_corpus,
                                     make_gold)
from research.mycelic.org import SITE, Org, build_org
from research.routing_spike.hq import Candidate
from research.routing_spike.site_process import SiteConfig
from research.routing_spike.world import World, backup_sqlite, restore_store

__all__ = [
    "BENCHMARK", "CHAIN_PREDICATES", "EMBED_DIM", "ENTITY_TYPE", "GRACE_WEEKS", "ID_PREFIX", "LANGUAGE", "M",
    "MAX_RECORDS", "N_DAYS", "N_WEEKS", "NARRATIVE_MARKER_RE", "PACK_DIR", "PACK_ID", "RECORD_REF_RE", "REVENUE_MISS",
    "ROOT", "RUN_CHANNEL", "START", "TIE_SALT", "TOP_N", "WORLD_TABLE", "EmergenceWorld", "WorldError",
    "build_emergence_world", "build_records", "canonical_entity_id", "check_world_counts", "entity_ids",
    "fixed_questions", "generate", "gold_labels", "master_data", "narrative_prefixes", "ordered_candidates",
    "SEED_DRAWN_COUNTS", "SEED_INVARIANT_COUNTS", "table_row_diff",
    "pack_predicate", "question_for", "received_date", "record_dict", "record_ref", "records_per_site",
    "site_configs", "site_id_of", "site_ids", "world_counts", "backup_sqlite", "restore_store",
]

ROOT = Path(__file__).resolve().parents[2]
PACK_DIR = ROOT / "research" / "e2e_ingest" / "pack" / "emergence"
PACK_ID = "emergence"
BENCHMARK = "EMERGENCE"
START = "2024-01-01"                       # a Monday; day 0 of the world (corpus.py: t in 0..n_days-1)
N_DAYS = 180
N_WEEKS = 26                               # 2024-W01 .. 2024-W26 cover days 0..179 (2024-01-01 .. 2024-06-28)
LANGUAGE = "en"
ENTITY_TYPE = "entity"
ID_PREFIX = "E-"
REVENUE_MISS = "revenue_miss"
CHAIN_PREDICATES = ("chain_supply", "chain_auth", "chain_policy", "chain_attrition", "chain_firmware",
                    "chain_pricing", "chain_network")
TIE_SALT = "e2e-ingest-v1"
RUN_CHANNEL = "X"
TOP_N = 60
GRACE_WEEKS = 4
M = 3
MAX_RECORDS = 50
EMBED_DIM = 256
RECORD_REF_RE = re.compile(r"em[0-9]+-[0-9]{7}", re.ASCII)
NARRATIVE_MARKER_RE = re.compile(r"\[d[0-9]{3}\] R[0-9]{2}S[0-9]{2}-DEPT-", re.ASCII)
NARRATIVE_PREFIX = 24

#: Section 3.1's table (seed 0): the generator is deterministic, so a world whose counts differ is not the brief's.
WORLD_TABLE: Mapping[int, Mapping[str, int]] = {
    200: {"users": 309, "sites": 7, "teams": 35, "records": 12964, "patterns_real": 40, "gold_discoverable": 40,
          "gold_rare": 18, "decoys": 50, "entities": 150},
    600: {"users": 601, "sites": 7, "teams": 66, "records": 22464, "patterns_real": 40, "gold_discoverable": 40,
          "gold_rare": 15, "decoys": 50, "entities": 150},
    2000: {"users": 1999, "sites": 9, "teams": 213, "records": 68463, "patterns_real": 40, "gold_discoverable": 40,
           "gold_rare": 9, "decoys": 50, "entities": 150},
}
_TEAM = 1
assert len(CHAIN_PREDICATES) == len(CAUSAL_CHAINS)


class WorldError(ValueError):
    pass


# --------------------------------------------------------------------------------------------------- the mapping

def canonical_entity_id(name: str) -> str:
    """``E-`` plus the upper-cased EMERGENCE entity name; an alias-only id of the pack's ``entity`` type."""
    out = ID_PREFIX + name.upper()
    if ALIAS_ONLY_ID_RE.fullmatch(out) is None:
        raise WorldError("an entity name does not give an alias-only id") from None
    return out


def entity_ids(corpus: Corpus) -> list[str]:
    """Every entity's canonical id, in the corpus's entity order."""
    return [canonical_entity_id(name) for name in corpus.entities]


def pack_predicate(name: str) -> str | None:
    """The pack predicate of an EMERGENCE predicate name: ``revenue_miss`` for itself, the chain predicate of the one
    causal chain holding any other causal predicate, None for a background predicate."""
    if name == REVENUE_MISS:
        return REVENUE_MISS
    chains = CHAIN_OF_PRED.get(PRED_ID[name], [])
    if not chains:
        return None
    indexes = {ci for ci, _ in chains}
    if len(indexes) != 1:
        raise WorldError("a causal predicate other than revenue_miss belongs to one chain") from None
    return CHAIN_PREDICATES[indexes.pop()]


def record_ref(seed: int, rid: int) -> str:
    return f"em{seed}-{rid:07d}"


def received_date(t: int) -> str:
    """``START + t days`` as an ISO date."""
    if not 0 <= t < N_DAYS:
        raise WorldError("a record's day index is outside the world") from None
    return (date.fromisoformat(START) + timedelta(days=int(t))).isoformat()


def site_id_of(org: Org, nid: int) -> str:
    """The lower-cased name of a site node."""
    return org.nodes[nid].name.lower()


def site_ids(org: Org) -> list[str]:
    return sorted(site_id_of(org, nid) for nid in org.levels[SITE])


def _user_sites(org: Org) -> dict[int, str]:
    out = {}
    for uid in org.user_ids:
        anc = org.ancestor_at(uid, SITE)
        if anc is None:
            raise WorldError("a user has no site ancestor") from None
        out[int(uid)] = site_id_of(org, anc)
    return out


def record_dict(corpus: Corpus, rid: int, *, seed: int, sites: Mapping[int, str]) -> dict[str, Any]:
    """Section 3.3's record for row ``rid`` of ``corpus.recs``; exactly the connector's record keys."""
    r = corpus.recs[rid]
    uid = int(r["uid"])
    out = {
        "record_ref": record_ref(seed, rid),
        "site": sites[uid],
        "received_date": received_date(int(r["t"])),
        "language": LANGUAGE,
        "codes": [],
        "entities": {ENTITY_TYPE: [canonical_entity_id(corpus.entities[int(r["anchor"])])]},
        "narrative": corpus.text(rid),
        "persons": {},
        "reporter": f"u{uid}",
        "origin_ref": None,
        "origin_site": None,
        "synthetic": True,
    }
    assert tuple(sorted(out)) == tuple(sorted(RECORD_KEYS))
    return out


def build_records(corpus: Corpus, *, seed: int | None = None) -> list[dict[str, Any]]:
    """One record per row of ``corpus.recs``, in row order."""
    seed = corpus.seed if seed is None else seed
    sites = _user_sites(corpus.org)
    return [record_dict(corpus, rid, seed=seed, sites=sites) for rid in range(len(corpus.recs))]


def records_per_site(corpus: Corpus) -> dict[str, int]:
    """Records whose user's site ancestor is each site, from ``org.ancestor_at`` alone (criterion 8's expectation,
    computed without any site module)."""
    sites = _user_sites(corpus.org)
    out = dict.fromkeys(site_ids(corpus.org), 0)
    for uid in corpus.recs["uid"].tolist():
        out[sites[int(uid)]] += 1
    return out


def master_data(corpus: Corpus) -> dict[str, dict[str, list[str]]]:
    """Every site's master data is the whole entity list (section 3.3): no site's answer tells HQ what it holds."""
    ids = sorted(entity_ids(corpus))
    return {sid: {ENTITY_TYPE: list(ids)} for sid in site_ids(corpus.org)}


# --------------------------------------------------------------------------------------------------- counts

def world_counts(org: Org, corpus: Corpus, gold: Gold) -> dict[str, int]:
    return {"users": len(org.user_ids), "sites": len(org.levels[SITE]), "teams": len(org.levels[_TEAM]),
            "records": int(len(corpus.recs)), "patterns_real": sum(1 for p in corpus.patterns if p.real),
            "gold_discoverable": len(gold.discoverable), "gold_rare": len(gold.rare), "decoys": len(gold.decoys),
            "entities": len(corpus.entities)}


#: The counts section 3.1 fixes for a size whatever the seed draws: the org's user total, the planted pattern and
#: decoy totals, and the entity list.  The generator derives each from ``target_users`` and its own constants, so a
#: world that misses one of these is not this benchmark's world and must be refused at any seed.
SEED_INVARIANT_COUNTS: tuple[str, ...] = ("users", "patterns_real", "gold_discoverable", "decoys", "entities")

#: The counts the draw moves: how the org falls into sites and teams, how many records the users write, and how many
#: of the planted patterns come out rare.  Section 3.1's row records **seed 0's** draw of these, so requiring the row
#: exactly is a guard against the seed, not against a malformed world -- it refused every seed but 0, which is why
#: seeds 1-5 could only run as disclosed guard-bypass diagnostics.  They are checked as structure instead.
SEED_DRAWN_COUNTS: tuple[str, ...] = ("sites", "teams", "records", "gold_rare")


def table_row_diff(counts: Mapping[str, int], target_users: int) -> list[str]:
    """Which of section 3.1's numbers this world does not match, as ``name: got != row`` (empty when it matches).

    The historical table check, kept as a diagnostic: it says what a world's draw did, and it is reported rather
    than enforced, so a run on a seed other than 0 is self-describing instead of refused.
    """
    row = WORLD_TABLE.get(target_users)
    if row is None:
        raise WorldError(f"no row of section 3.1 for target_users {target_users}") from None
    return [f"{k}: {counts.get(k)} != {row[k]}" for k in sorted(row) if counts.get(k) != row[k]]


def check_world_counts(counts: Mapping[str, int], target_users: int) -> None:
    """Refuse a world that is not this size's EMERGENCE world, at any seed.

    The seed-invariant counts must equal section 3.1's row exactly.  The drawn counts are held to the structure the
    benchmark needs rather than to seed 0's numbers: at least the two sites the admission rule compares, a team for
    each site, records to carry the evidence, and no more rare patterns than planted ones.  Each is a property of
    the benchmark's definition, not a band fitted to the development seeds.

    What this no longer catches -- a world whose record total is off by one -- was never a safety property: the
    record total is a draw, and :func:`table_row_diff` still reports it.
    """
    row = WORLD_TABLE.get(target_users)
    if row is None:
        raise WorldError(f"no row of section 3.1 for target_users {target_users}") from None
    bad = sorted(k for k in SEED_INVARIANT_COUNTS if counts.get(k) != row[k])
    if bad:
        raise WorldError(f"world counts differ from section 3.1 at target_users {target_users}: {bad}") from None
    missing = sorted(k for k in SEED_DRAWN_COUNTS if counts.get(k) is None)
    if missing:
        raise WorldError(f"world counts lack {missing} at target_users {target_users}") from None
    if counts["sites"] < 2:
        raise WorldError(f"a world with {counts['sites']} site(s) cannot meet the two-site admission rule") from None
    if counts["teams"] < counts["sites"]:
        raise WorldError(f"{counts['teams']} teams across {counts['sites']} sites: a site without a team") from None
    if counts["records"] < counts["users"]:
        raise WorldError(f"{counts['records']} records for {counts['users']} users: a world without evidence") from None
    if not 0 <= counts["gold_rare"] <= counts["gold_discoverable"]:
        raise WorldError(f"{counts['gold_rare']} rare of {counts['gold_discoverable']} discoverable patterns") from None


def generate(target_users: int, seed: int) -> tuple[Org, Corpus, Gold]:
    """``build_org``, ``build_corpus``, ``make_gold`` with the generator's defaults (``runner.build_world``'s calls)."""
    org = build_org(target_users, seed=seed)
    corpus = build_corpus(org, seed=seed)
    return org, corpus, make_gold(corpus)


# --------------------------------------------------------------------------------------------------- Gold's labels

def _window(times: Sequence[int], *, grace_weeks: int = GRACE_WEEKS) -> tuple[str, str]:
    first = iso_week(received_date(min(times)))
    last = iso_week(date.fromisoformat(received_date(max(times))) + timedelta(weeks=grace_weeks))
    return first, last


def gold_labels(corpus: Corpus, gold: Gold, *, grace_weeks: int = GRACE_WEEKS) -> dict[str, Any]:
    """The labels doc in the spike's shape (``world.candidate_label``): every Gold-discoverable pattern with its key
    (canonical anchor, chain predicate), its window and its facet sites; every decoy with its class and its keys (each
    facet record's anchor and chain), so a routed candidate is true, decoy or background."""
    org = corpus.org
    sites = _user_sites(org)
    patterns = []
    for pid in gold.discoverable:
        p = corpus.patterns[pid]
        anchor = canonical_entity_id(corpus.entities[int(p.anchor)])
        chain = CHAIN_PREDICATES[p.chain]
        first, last = _window(p.facet_times, grace_weeks=grace_weeks)
        held = sorted({sites[int(corpus.recs[r]["uid"])] for fr in p.facet_records for r in fr})
        patterns.append({"id": f"p{pid:03d}", "pattern": pid, "key": series_key(ENTITY_TYPE, anchor, chain),
                         "anchor": anchor, "chain": chain, "found_from": first, "found_to": last, "sites": held,
                         "rare": bool(p.rare), "contradicted": bool(p.contradicted), "facets": len(p.preds),
                         "facet_weeks": [iso_week(received_date(t)) for t in p.facet_times]})
    decoys = []
    for pid in gold.decoys:
        p = corpus.patterns[pid]
        keys = set()
        for fr in p.facet_records:
            for r in fr:
                rec = corpus.recs[r]
                predicate = pack_predicate(PREDICATES[int(rec["pred"])])
                if predicate is not None:
                    keys.add(series_key(ENTITY_TYPE, canonical_entity_id(corpus.entities[int(rec["anchor"])]),
                                        predicate))
        first, last = _window(p.facet_times, grace_weeks=grace_weeks)
        decoys.append({"id": f"d{pid:03d}", "pattern": pid, "class": int(p.decoy_type), "keys": sorted(keys),
                       "found_from": first, "found_to": last})
    return {"schema_version": 1, "benchmark": BENCHMARK, "grace_weeks": grace_weeks, "patterns": patterns,
            "decoys": decoys}


# --------------------------------------------------------------------------------------------------- candidates

def ordered_candidates(result: Mapping[str, Any], *, tie_salt: str = TIE_SALT, top_n: int | None = TOP_N
                       ) -> tuple[list[dict[str, Any]], int]:
    """Every detector candidate with a snapshot, by (score descending, ``sha256(tie_salt|key)``), the first ``top_n``
    (M21: no evaluation window), and how many there were."""
    found = [c for c in result["candidates"] if c["snapshot"] is not None]
    found.sort(key=lambda c: (-c["snapshot"]["score"], sha256_hex(f"{tie_salt}|{c['key']}")))
    return (found if top_n is None else found[:top_n]), len(found)


def question_for(pack: FrozenPack, cand: Candidate) -> dict[str, Any]:
    """HQ's question for a candidate, from the candidate alone (``hq.plan`` builds the same question)."""
    window = question_window(pack, as_of=cand.as_of, candidate_window_start=cand.window_start)
    return build_question(pack, entity_type=cand.entity_type, entity_id=cand.entity_id, predicate=cand.predicate,
                          window=window, as_of=cand.as_of)


# --------------------------------------------------------------------------------------------------- the world

@dataclass
class EmergenceWorld(World):
    """The spike's ``World`` plus the generator's objects and the counts; ``planted`` is True (the benchmark plants its
    patterns by construction) and ``labels`` is Gold's labels doc."""

    target_users: int = 0
    org: Org | None = None
    corpus: Corpus | None = None
    gold: Gold | None = None
    counts: dict[str, int] = field(default_factory=dict)
    #: section 3.1's row minus this world's draw, as :func:`table_row_diff` renders it; empty at seed 0
    table_diff: list[str] = field(default_factory=list)
    per_site_records: dict[str, int] = field(default_factory=dict)
    entity_ids: list[str] = field(default_factory=list)
    tie_salt: str = TIE_SALT

    @property
    def name(self) -> str:
        return f"emergence-{self.target_users}-seed-{self.seed:02d}"

    @property
    def as_of(self) -> str:
        return self.pipeline.as_of


def build_emergence_world(target_users: int = 200, seed: int = 0, workdir: str | Path = "e2e-world", *,
                          pack_dir: str | Path = PACK_DIR, top_n: int | None = TOP_N, tie_salt: str = TIE_SALT,
                          enterprise: str = PACK_ID, timings: dict[str, float] | None = None) -> EmergenceWorld:
    """Generate the world, check its counts, run the shipped pipeline (ingest, lexical extraction, weekly cells
    through every site's Boundary, HQ's store) and detection, and take the candidates. The caller owns
    ``world.pipeline`` (``pipeline.close()``). ``timings``, when given, receives the wall seconds of the generator
    (``generate_seconds``), of the pipeline (``pipeline_seconds``) and of detection (``detection_seconds``): the
    scaling table of section 10 reports the pipeline's and detection's seconds apart."""
    t0 = time.perf_counter()
    org, corpus, gold = generate(target_users, seed)
    counts = world_counts(org, corpus, gold)
    check_world_counts(counts, target_users)
    table_diff = table_row_diff(counts, target_users)
    pack = load_pack_dir(Path(pack_dir), expected_id=PACK_ID)
    ids = entity_ids(corpus)
    if set(pack.entity_types[ENTITY_TYPE].ids or ()) != set(ids):
        raise WorldError("the pack's entity ids are not this world's") from None
    if set(pack.predicates) != set(CHAIN_PREDICATES) | {REVENUE_MISS}:
        raise WorldError("the pack's predicates are not the chains and revenue_miss") from None
    sites = site_ids(org)
    records = build_records(corpus, seed=seed)
    master = master_data(corpus)
    weeks = world_weeks(START, N_WEEKS)
    t1 = time.perf_counter()
    pipeline = run_pipeline(pack, records, site_ids=sites, master_data=master, weeks=weeks, workdir=workdir,
                            enterprise=enterprise)
    t2 = time.perf_counter()
    try:
        if pipeline.as_of != closing_date(pack, weeks[-1]):
            raise WorldError("the pipeline's as_of is not the closing date of the last week") from None
        result = detect(pipeline.store, as_of=pipeline.as_of, run_channel=RUN_CHANNEL, tie_salt=tie_salt)
        t3 = time.perf_counter()
        candidates, detected = ordered_candidates(result, tie_salt=tie_salt, top_n=top_n)
        labels = gold_labels(corpus, gold)
    except BaseException:
        pipeline.close()
        raise
    if timings is not None:
        timings.update({"generate_seconds": t1 - t0, "pipeline_seconds": t2 - t1, "detection_seconds": t3 - t2})
    return EmergenceWorld(seed=seed, planted=True, workdir=Path(workdir), pack=pack, pipeline=pipeline,
                          records=records, master_data=master, site_ids=sites, weeks=list(weeks),
                          candidates=candidates, detected=detected, labels=labels, target_users=target_users, org=org,
                          corpus=corpus, gold=gold, counts=counts, table_diff=table_diff,
                          per_site_records=records_per_site(corpus), entity_ids=ids, tie_salt=tie_salt)


def fixed_questions(world: World, n: int = 20) -> list[dict[str, Any]]:
    """Section 3.5's fixed queries: HQ's question for each of the first ``n`` candidates in (``as_of``, key) order;
    the same list at every site."""
    cands = sorted((Candidate.from_detector(c) for c in world.candidates), key=lambda c: (c.as_of, c.key))
    return [question_for(world.pack, c) for c in cands[:n]]


def site_configs(world: World, *, retrieval: str = "tesseract", max_records: int = MAX_RECORDS,
                 edge: Path | None = None, hq_dir: Path | None = None, embed_dim: int = EMBED_DIM,
                 routing: str | None = None) -> dict[str, SiteConfig]:
    """One ``SiteConfig`` per site, as the spike's ``run.site_configs`` builds them, with the pack named by its
    directory (the pack is not built in)."""
    edge = edge or world.workdir / "edge"
    hq_dir = hq_dir or world.workdir / "hq"
    return {sid: SiteConfig(pack=world.pack.directory, site_id=sid, edge_dir=str(edge), hq_dir=str(hq_dir),
                            master_data={t: list(v) for t, v in world.master_data[sid].items()},
                            demo_seed=world.seed, retrieval=retrieval, max_records=max_records,
                            graph_path=str(edge / f"site-{sid}.graph.sqlite3"),
                            clock_start=f"{world.pipeline.as_of}T12:00:00Z", embed_dim=embed_dim,
                            routing=None if routing is None else str(Path(routing) / f"{sid}.routing.json"))
            for sid in world.site_ids}


def narrative_prefixes(corpus: Corpus, n: int = NARRATIVE_PREFIX) -> list[str]:
    """The first ``n`` characters of every narrative (drill 5's third scan pattern); harness side only."""
    return sorted({corpus.text(rid)[:n] for rid in range(len(corpus.recs))})
