"""Harness-only calibrated emulator; event identity and polarity never leave this module."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

from research.e2e_ingest.emergence_world import canonical_entity_id, pack_predicate
from research.mycelic.corpus import PREDICATES, PRED_SURFACE
from research.mycelic.org import SITE

from .protocol import handle, validate_summary

CALIBRATION = Path(__file__).resolve().parents[1] / "docs/research/hierarchy-sim/edge-calibration-seed0.json"


def uniform(seed, rid, purpose):
    value = hashlib.sha256(f"rollup-001|{seed}|{rid}|{purpose}".encode()).digest()
    return int.from_bytes(value[:8], "big") / 2**64


def emulate(corpus, *, company_key, perfect=False):
    rates = json.loads(CALIBRATION.read_text())["recall_by_chain"]
    sites = {uid: corpus.org.nodes[corpus.org.ancestor_at(uid, SITE)].name.lower() for uid in corpus.org.user_ids}
    groups, seen, newest, roots = defaultdict(list), set(), {}, {}
    counts = {"records": len(corpus.recs), "lexicon_events": 0, "kept": 0, "true_copies": 0,
              "marked_copy": 0, "marked_negated": 0, "false_memories": 0}
    for rid in sorted(range(len(corpus.recs)), key=lambda i: (int(corpus.recs[i]["t"]), i)):
        r = corpus.recs[rid]
        kind = PREDICATES[int(r["pred"])]
        chain = pack_predicate(kind)
        if chain is None:
            continue
        # Verify the lexicon phrase exists in the site's surface text, rather than exposing structured kinds alone.
        text = " ".join(corpus.tokens(rid))
        if not any(phrase in text for phrase in PRED_SURFACE[kind]):
            continue
        counts["lexicon_events"] += 1
        if uniform(corpus.seed, rid, "keep") >= rates[chain]["recall"]:
            continue
        counts["kept"] += 1
        site = sites[int(r["uid"])]
        entity = canonical_entity_id(corpus.entities[int(r["anchor"])])
        event = int(r["event"])
        is_copy = (site, event) in seen
        seen.add((site, event))
        marked = is_copy or (not perfect and uniform(corpus.seed, rid, "copy") < 615 / 2329)
        negated = bool(r["polarity"] < 0 and (perfect or uniform(corpus.seed, rid, "negation") < 42 / 95))
        # The emulator's event surrogate stands for the content fingerprint. No record ref or uid is hashed.
        fingerprint = hashlib.sha256(f"synthetic-content-event:{event}".encode()).hexdigest()
        lineage = handle(company_key, fingerprint)
        if is_copy:
            lineage = roots[(site, event)]
        nk = (site, entity, kind)
        if marked and not is_copy and nk in newest:
            lineage = newest[nk]
        newest[nk] = lineage
        roots[(site, event)] = lineage
        counts["true_copies"] += int(is_copy)
        counts["marked_copy"] += int(marked)
        counts["marked_negated"] += int(negated)
        week = int(r["t"]) // 7 + 1
        groups[(site, week, entity, kind, negated)].append((lineage, marked))
    weeks = {w: [] for w in range(1, 27)}
    indexed = defaultdict(list)
    for (site, week, ent, kind, neg), memories in sorted(groups.items()):
        hs = sorted({h for h, _ in memories})
        indexed[(site, week)].append({"entity": ent, "kind": kind, "negated": neg, "distinct": len(hs),
                                     "copies": sum(int(m) for _, m in memories), "handles": hs})
    for site in sorted(set(sites.values())):
        for week in weeks:
            entries = indexed[(site, week)]
            summary = {"site": site, "week": week, "entries": entries}
            validate_summary(summary)
            weeks[week].append(summary)
    return weeks, counts
