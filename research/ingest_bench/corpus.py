"""Seeded synthetic corpus for the ingestion benchmarks (docs/mycelic/INGESTION.md §15), labelled by construction.

Twelve top-level domains (the default tenant taxonomy) with Zipf-like sizes, so a few large domains are the natural split
candidates. Each domain owns a vocabulary: its taxonomy keywords plus a seeded set of words drawn from a shared lexicon;
a record mixes ~70% words of its own domain with ~30% common words, so retrieval is not trivially separable by domain and
a query can match records of several domains. One export file per (domain, app) pair, whose source header maps the
domain (the classifier's source-mapping stage), so every record's primary domain is known.

Queries are known-item queries: four content words of a sampled record (the record is the expected best hit), drawn with
the same seed.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DOMAINS = ["engineering", "infrastructure", "product", "sales", "finance", "legal", "operations", "research", "customer-support",
           "security", "hr", "executive-strategy"]
APPS = ["chat", "tracker", "mail"]

# a neutral lexicon; words are assigned to domains by the seed (real words keep BM25 and the hash embedder realistic)
LEXICON = """
abacus anchor apex arbor archive atlas aurora badge ballast banner barley basin beacon bellow binder birch blanket bolt bramble
bridge bronze buckle bundle cabin cactus caliper canal candle canvas canyon carbon cargo cascade cedar cellar chalk channel
chapter charter chisel cinder circuit citadel clamp clover cobalt comet compass copper coral corner cotton cradle crater crest
crystal current cushion dagger delta depot desert dial dome draft dune easel ember engine estuary falcon feather fender ferry
fiber fjord flint forge fossil fountain fresco funnel garnet gazebo geyser glacier granite gravel grove harbor harness hatch
hazel helmet hinge hollow horizon hydrant icicle indigo inlet iris island ivory jasper jetty juniper kernel kettle keystone
kiln ladder lagoon lantern larch lattice ledger lens lever lichen lighthouse linen lobby locket lumber magnet mantle maple
marble marsh meadow mesa meteor mica mill mirror moat mortar mosaic nectar needle nickel nozzle oasis obelisk ochre olive
onyx orbit orchard otter oven paddle palette panel parcel pebble pendant pepper pier pigment pillar pinion piston plank
plaza plinth pollen portal pottery prism pulley quarry quartz quill radar raft rampart rapids raven reef ribbon ridge rivet
rocket rudder saddle saffron salvo sandbar satchel scaffold scarab sconce sextant shale shutter signal silo silver skiff slate
sleeve socket sonar spindle spire spruce stencil summit sundial tabard talon tandem tapestry tassel temple tether thimble
thistle throttle timber torch tower trellis trident trolley tundra turbine tunnel turret umber valve vault velvet vessel
viaduct vine visor walnut warren wedge wharf whistle wicket willow winch window wren yarrow zenith zephyr zinc
""".split()
COMMON = """the a of to and in for on with after before during about from team week update note review plan issue status
question meeting follow follow-up next said says asked agreed noted change changes again still new old early late""".split()
VERBS = ["blocked", "delayed", "improved", "replaced", "reviewed", "approved", "rejected", "moved", "broke", "fixed", "escalated",
         "measured", "shipped", "paused", "restarted", "flagged", "audited", "migrated", "doubled", "halved"]


@dataclass
class Corpus:
    records: dict[str, list[dict[str, Any]]] = field(default_factory=dict)        # domain -> records
    vocab: dict[str, list[str]] = field(default_factory=dict)
    queries: list[dict[str, Any]] = field(default_factory=list)                  # {text, expected_object_id, domain}
    sizes: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.records.values())


def build_corpus(n_records: int, *, seed: int = 7, n_queries: int = 200, words_per_domain: int = 40) -> Corpus:
    rng = random.Random(seed)
    from mycelic.ingest.domains import default_taxonomy
    tax = default_taxonomy()
    lex = list(LEXICON)
    rng.shuffle(lex)
    corpus = Corpus()
    # Zipf-like domain sizes: the first domains are large (the split candidates)
    weights = [1.0 / (i + 1) ** 0.9 for i in range(len(DOMAINS))]
    total_w = sum(weights)
    for i, dom in enumerate(DOMAINS):
        corpus.sizes[dom] = max(5, int(n_records * weights[i] / total_w))
        kws = [w for k in tax.domains[dom].keywords for w in k.split() if len(w) > 2]
        own = lex[(i * words_per_domain) % len(lex):][:words_per_domain]
        if len(own) < words_per_domain:
            own += lex[:words_per_domain - len(own)]
        corpus.vocab[dom] = list(dict.fromkeys(kws + own))
    for dom in DOMAINS:
        vocab = corpus.vocab[dom]
        recs = []
        for j in range(corpus.sizes[dom]):
            n_sent = rng.randint(1, 3)
            sents = []
            for _ in range(n_sent):
                words = [rng.choice(vocab) if rng.random() < 0.7 else rng.choice(lex) for _ in range(rng.randint(5, 9))]
                filler = rng.sample(COMMON, 3)
                sent = f"{filler[0].capitalize()} {words[0]} {rng.choice(VERBS)} {filler[1]} {' '.join(words[1:4])} {filler[2]} {' '.join(words[4:])}."
                sents.append(sent)
            day = rng.randint(1, 360)
            ts = f"2026-{1 + (day // 31) % 12:02d}-{1 + day % 28:02d}T{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:00Z"
            recs.append({"type": "message", "id": f"{dom}-{j}", "text": " ".join(sents), "created_at": ts, "author": f"user{rng.randint(1, 40)}@acme.test",
                         "app": APPS[j % len(APPS)]})
        corpus.records[dom] = recs
    # known-item queries, sampled across domains in proportion to their size
    pool = [(dom, r) for dom in DOMAINS for r in corpus.records[dom]]
    for dom, rec in rng.sample(pool, min(n_queries, len(pool))):
        words = [w.strip(".,").lower() for w in rec["text"].split() if w.strip(".,").lower() not in COMMON and w.strip(".,").lower() not in VERBS]
        q = " ".join(rng.sample(words, min(4, len(words))))
        corpus.queries.append({"text": q, "expected_object_id": rec["id"], "domain": dom})
    return corpus


def write_exports(corpus: Corpus, out_dir: Path) -> list[dict[str, Any]]:
    """One JSONL export per (domain, app); returns ``{path, domain, app}`` per file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for dom, recs in corpus.records.items():
        for app in APPS:
            mine = [{k: v for k, v in r.items() if k != "app"} for r in recs if r["app"] == app]
            if not mine:
                continue
            p = out_dir / f"{dom}-{app}.jsonl"
            header = {"type": "source", "id": f"{dom}-{app}", "name": f"{dom} {app}", "source_type": "channel", "visibility": "public", "domains": [dom]}
            p.write_text("\n".join(json.dumps(x) for x in [header, *mine]) + "\n", encoding="utf-8")
            files.append({"path": p, "domain": dom, "app": app})
    return files
