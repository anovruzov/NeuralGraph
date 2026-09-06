"""Two-agent memory probes on LoCoMo. Retrieval-only (no LLM calls).

Treats each LoCoMo speaker as an agent with its own memory store.

H9a  per-agent memory split: pooled vs local (route to the named agent) vs federated
     (top-25 from each agent store, merged) for tesseract and pure-embedding retrieval.
H9b  speaker-swap probe: rewrite single_hop questions naming exactly one speaker with the
     OTHER speaker's name and measure how much recall drops (identity sensitivity).

Usage:
    EMB_CACHE_DIR=... ONLY_CONV=0,1 .venv/bin/python demo/two_agent_eval.py
    OUT_JSON=/path/rows.json  -> per-question rows for further analysis
"""
import asyncio
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from datetime import datetime

from NeuralGraph import NeuralNode, NodeLayer
from NeuralGraph.storage import InMemoryNeuralGraphStorage
from NeuralGraph.tesseract import Tesseract
from retrieval_eval import ingest, cached_embeddings, cosine_topk, flatten_messages, TOP_K
from runner import check_gold_in_memories_substring, CATEGORIES

JUDGE_CATS = ("single_hop", "multi_hop", "open_domain")
ONLY_CONV = {int(c) for c in os.environ.get("ONLY_CONV", "").split(",") if c.strip()}
OUT_JSON = os.environ.get("OUT_JSON", "")
FED_K = 25          # per-agent slice merged in "federated"
PAIR_EXTRA = 30     # back-fill budget appended after a top-50 (same as embed_union / H3 pairs)
MODE = os.environ.get("MODE", "split")  # split = H9a/H9b ; combined = pair-node stack
SPEAKER_BOOST = 1.5  # same as retrieval_eval "tesseract+speaker"


# ----------------------------------------------------------------------------- helpers
def named_speakers(question: str, speakers: list[str]) -> list[str]:
    """Same rule as retrieval_eval 'tesseract+speaker': lowercase substring match."""
    q = question.lower()
    return [sp for sp in speakers if sp in q]


def swap_name(question: str, src: str, dst: str) -> str:
    """Case-preserving whole-word replace of speaker `src` by `dst` (handles possessives)."""
    def repl(m):
        w = m.group(0)
        if w.isupper():
            return dst.upper()
        if w[0].isupper():
            return dst[0].upper() + dst[1:].lower()
        return dst.lower()
    return re.sub(r"\b" + re.escape(src) + r"\b", repl, question, flags=re.IGNORECASE)


def gold_parts(gold: str) -> list[str]:
    g = str(gold).lower().strip()
    parts = [p.strip() for p in g.replace(",", "|").replace(" and ", "|").split("|")]
    parts = [p for p in parts if len(p) > 2]
    return parts or [g]


def hits(res, gold) -> tuple[bool, bool]:
    mems = [{"text": n.content} for n, _ in res]
    r10, _ = check_gold_in_memories_substring(gold, mems, top_k=10)
    r50, _ = check_gold_in_memories_substring(gold, mems, top_k=50)
    return r10, r50


def speaker_boost(res, speaker: str):
    out = [(n, c * (SPEAKER_BOOST if n.speaker_id.lower() == speaker else 1.0)) for n, c in res]
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def merge_by_charge(a, b, k=TOP_K):
    seen, out = set(), []
    for n, c in sorted(a + b, key=lambda x: x[1], reverse=True):
        if n.node_id not in seen:
            seen.add(n.node_id)
            out.append((n, c))
    return out[:k]


def merge_round_robin(a, b, k=TOP_K):
    seen, out = set(), []
    for i in range(max(len(a), len(b))):
        for lst in (a, b):
            if i < len(lst) and lst[i][0].node_id not in seen:
                seen.add(lst[i][0].node_id)
                out.append(lst[i])
    return out[:k]


async def build_agent_store(nodes) -> InMemoryNeuralGraphStorage:
    """Per-agent store: same node objects, no edges (Tesseract only needs nodes + session index)."""
    st = InMemoryNeuralGraphStorage()
    for n in nodes:
        await st.save_node(n)
    return st


async def tess(t: Tesseract, q: str, qvec, conv_idx: int, limit=TOP_K):
    return await t.retrieve(query_text=q, query_embedding=qvec, session_key=f"conv_{conv_idx}",
                            limit=limit, auto_expand_temporal=True)


def evidence_speakers(qa: dict, dia_speaker: dict[str, str]) -> set[str]:
    ev = qa.get("evidence", [])
    if isinstance(ev, str):
        ev = [ev]
    ids = re.findall(r"D\d+:\d+", " ".join(str(e) for e in ev))
    return {dia_speaker[i].lower() for i in ids if i in dia_speaker}


def substring_gold_speakers(gold: str, nodes) -> set[str]:
    parts = gold_parts(gold)
    g = str(gold).lower().strip()
    out = set()
    for n in nodes:
        t = n.content.lower()
        if any(p in t for p in parts) or g in t:
            out.add(n.speaker_id.lower())
    return out


# ----------------------------------------------------------------------------- main
async def main():
    data = json.load(open(Path(__file__).parent.parent / "evaluation" / "locomo" / "locomo10.json"))
    rows_a, rows_b = [], []
    t0 = time.time()
    async with aiohttp.ClientSession() as http:
        for conv_idx, conv in enumerate(data):
            if ONLY_CONV and conv_idx not in ONLY_CONV:
                continue
            storage, linker, nodes, speakers = await ingest(http, conv_idx, conv)
            c = conv["conversation"]
            sp_a, sp_b = c["speaker_a"], c["speaker_b"]
            assert {sp_a.lower(), sp_b.lower()} == set(speakers), (speakers, sp_a, sp_b)
            dia_speaker = {m["dia_id"]: m["speaker"] for k, v in c.items()
                           if k.startswith("session_") and isinstance(v, list) for m in v if "dia_id" in m}

            by_sp = {sp_a.lower(): [n for n in nodes if n.speaker_id.lower() == sp_a.lower()],
                     sp_b.lower(): [n for n in nodes if n.speaker_id.lower() == sp_b.lower()]}
            stores = {sp: await build_agent_store(ns) for sp, ns in by_sp.items()}
            t_pool = Tesseract(storage)
            t_ag = {sp: Tesseract(st) for sp, st in stores.items()}

            qas = [qa for qa in conv.get("qa", []) if CATEGORIES.get(qa.get("category")) in JUDGE_CATS]
            qvecs = await cached_embeddings(http, f"conv{conv_idx}_questions_all",
                                            [qa["question"] for qa in conv.get("qa", [])])
            qvec_by_text = {qa["question"]: v for qa, v in zip(conv.get("qa", []), qvecs)}

            pooled_t_cache: dict[str, list] = {}
            # ---------------- H9a
            for qa in qas:
                q, gold, cat = qa["question"], qa.get("answer", ""), CATEGORIES[qa["category"]]
                qvec = qvec_by_text[q]
                named = named_speakers(q, speakers)
                ev_sp = evidence_speakers(qa, dia_speaker)
                sub_sp = substring_gold_speakers(gold, nodes)

                pooled_t = await tess(t_pool, q, qvec, conv_idx)
                pooled_t_cache[q] = pooled_t
                ag_t = {sp: await tess(t_ag[sp], q, qvec, conv_idx) for sp in speakers}
                pooled_e = cosine_topk(nodes, qvec, TOP_K)
                ag_e = {sp: cosine_topk(by_sp[sp], qvec, TOP_K) for sp in speakers}

                a, b = speakers[0], speakers[1]
                variants = {
                    "tess_pooled": pooled_t,
                    "tess_local": ag_t[named[0]] if len(named) == 1 else pooled_t,
                    "tess_local_oracle": (ag_t[next(iter(ev_sp))] if len(ev_sp) == 1 else pooled_t),
                    "tess_federated": merge_by_charge(ag_t[a][:FED_K], ag_t[b][:FED_K]),
                    "tess_federated_rr": merge_round_robin(ag_t[a][:FED_K], ag_t[b][:FED_K]),
                    "embed_pooled": pooled_e,
                    "embed_local": ag_e[named[0]] if len(named) == 1 else pooled_e,
                    "embed_local_oracle": (ag_e[next(iter(ev_sp))] if len(ev_sp) == 1 else pooled_e),
                    "embed_federated": merge_by_charge(ag_e[a][:FED_K], ag_e[b][:FED_K]),
                }
                row = {"conv": conv_idx, "cat": cat, "q": q, "gold": gold, "named": named,
                       "evidence_speakers": sorted(ev_sp), "substring_gold_speakers": sorted(sub_sp)}
                for name, res in variants.items():
                    r10, r50 = hits(res, gold)
                    row[name] = [int(r10), int(r50)]
                rows_a.append(row)

            # ---------------- H9b (single_hop, exactly one named speaker)
            probe = [qa for qa in qas if CATEGORIES[qa["category"]] == "single_hop"
                     and len(named_speakers(qa["question"], speakers)) == 1]
            orig_name = {sp_a.lower(): sp_a, sp_b.lower(): sp_b}
            swapped_qs = []
            for qa in probe:
                nm = named_speakers(qa["question"], speakers)[0]
                other = sp_b if nm == sp_a.lower() else sp_a
                swapped_qs.append(swap_name(qa["question"], orig_name[nm], other))
            svecs = await cached_embeddings(http, f"conv{conv_idx}_questions_swapped", swapped_qs) if probe else []
            for qa, sq, svec in zip(probe, swapped_qs, svecs):
                q, gold = qa["question"], qa.get("answer", "")
                qvec = qvec_by_text[q]
                nm = named_speakers(q, speakers)[0]
                other = sp_b.lower() if nm == sp_a.lower() else sp_a.lower()
                o_t = pooled_t_cache[q]
                s_t = await tess(t_pool, sq, svec, conv_idx)
                o_e = cosine_topk(nodes, qvec, TOP_K)
                s_e = cosine_topk(nodes, svec, TOP_K)
                row = {"conv": conv_idx, "q": q, "swapped_q": sq, "gold": gold, "named": nm,
                       "evidence_speakers": sorted(evidence_speakers(qa, dia_speaker))}
                for name, (o, s) in {
                    "embed": (o_e, s_e),
                    "tesseract": (o_t, s_t),
                    "tesseract+speaker": (speaker_boost(o_t, nm), speaker_boost(s_t, other)),
                }.items():
                    row[name + "_orig"] = [int(x) for x in hits(o, gold)]
                    row[name + "_swap"] = [int(x) for x in hits(s, gold)]
                rows_b.append(row)
            print(f"conv {conv_idx}: {len(qas)} H9a questions, {len(probe)} H9b probes ({time.time() - t0:.0f}s)")

    report(rows_a, rows_b)
    if OUT_JSON:
        json.dump({"h9a": rows_a, "h9b": rows_b}, open(OUT_JSON, "w"))


def pct(num, den):
    return f"{100 * num / den:5.1f}" if den else "  n/a"


def report(rows_a, rows_b):
    variants = ["tess_pooled", "tess_local", "tess_local_oracle", "tess_federated", "tess_federated_rr",
                "embed_pooled", "embed_local", "embed_local_oracle", "embed_federated"]
    print("\n=== H9a per-agent memory split (recall@10 / recall@50) ===")
    print(f"{'variant':20}" + "".join(f"{c:>28}" for c in JUDGE_CATS) + f"{'ALL':>28}")
    for v in variants:
        line = f"{v:20}"
        for cat in list(JUDGE_CATS) + [None]:
            rs = [r for r in rows_a if cat is None or r["cat"] == cat]
            line += f"{pct(sum(r[v][0] for r in rs), len(rs))} / {pct(sum(r[v][1] for r in rs), len(rs))} (n={len(rs)})".rjust(28)
        print(line)

    print("\n--- routing statistics (questions naming exactly one speaker) ---")
    print(f"{'category':12}{'n':>6}{'names 1':>9}{'names 0':>9}{'names 2':>9}"
          f"{'gold by OTHER (evidence)':>26}{'gold only OTHER (substr)':>26}")
    for cat in list(JUDGE_CATS) + [None]:
        rs = [r for r in rows_a if cat is None or r["cat"] == cat]
        one = [r for r in rs if len(r["named"]) == 1]
        ev = [r for r in one if r["evidence_speakers"]]
        other_ev = sum(1 for r in ev if r["named"][0] not in r["evidence_speakers"])
        sub = [r for r in one if r["substring_gold_speakers"]]
        other_sub = sum(1 for r in sub if r["named"][0] not in r["substring_gold_speakers"])
        print(f"{(cat or 'ALL'):12}{len(rs):6d}{len(one):9d}{sum(1 for r in rs if not r['named']):9d}"
              f"{sum(1 for r in rs if len(r['named']) == 2):9d}"
              f"{pct(other_ev, len(ev)) + '% of ' + str(len(ev)):>26}{pct(other_sub, len(sub)) + '% of ' + str(len(sub)):>26}")

    print("\n--- H9a restricted to questions naming exactly one speaker ---")
    for v in variants:
        line = f"{v:20}"
        for cat in list(JUDGE_CATS) + [None]:
            rs = [r for r in rows_a if (cat is None or r["cat"] == cat) and len(r["named"]) == 1]
            line += f"{pct(sum(r[v][0] for r in rs), len(rs))} / {pct(sum(r[v][1] for r in rs), len(rs))} (n={len(rs)})".rjust(28)
        print(line)

    print("\n=== H9b speaker-swap probe (single_hop, exactly one named speaker) ===")
    n = len(rows_b)
    print(f"n = {n}")
    print(f"{'variant':20}{'orig r@10':>11}{'swap r@10':>11}{'gap@10':>9}{'orig r@50':>11}{'swap r@50':>11}{'gap@50':>9}"
          f"{'blind@10 (swap hit | orig hit)':>32}")
    for v in ("embed", "tesseract", "tesseract+speaker"):
        o10 = sum(r[v + "_orig"][0] for r in rows_b); s10 = sum(r[v + "_swap"][0] for r in rows_b)
        o50 = sum(r[v + "_orig"][1] for r in rows_b); s50 = sum(r[v + "_swap"][1] for r in rows_b)
        both = sum(1 for r in rows_b if r[v + "_orig"][0] and r[v + "_swap"][0])
        print(f"{v:20}{pct(o10, n):>11}{pct(s10, n):>11}{pct(o10 - s10, n):>9}{pct(o50, n):>11}{pct(s50, n):>11}"
              f"{pct(o50 - s50, n):>9}{pct(both, o10) + '% of ' + str(o10):>32}")
    ev_other = [r for r in rows_b if r["evidence_speakers"] and r["named"] not in r["evidence_speakers"]]
    print(f"probes whose evidence message is spoken by the OTHER (un-named) speaker: {len(ev_other)}/{n}")


# ============================================================================= combined stack
# Ported from worktree agent-a96e7782c6d2db9a2 demo/retrieval_eval.py (H3 pair nodes), window=2 only.
async def build_pair_nodes(http, conv_idx: int, messages: list[dict], nodes: list, window: int = 2):
    """PAIR nodes "[prev speaker] prev\n[speaker] this"; anchor (replier) = second message.
    Kept out of storage; a pair hit maps back to member messages via metadata["pair_members"] (reply first)."""
    texts, members = [], []
    for i in range(1, len(messages)):
        idxs, order = [i - 1, i], [i, i - 1]
        texts.append("\n".join(f"[{messages[j]['speaker']}] {messages[j]['text']}" for j in idxs))
        members.append([nodes[j].node_id for j in order])
    embs = await cached_embeddings(http, f"conv{conv_idx}_pairs{window}", texts)
    out = []
    for k, (text, emb, mem) in enumerate(zip(texts, embs, members)):
        anchor = nodes[int(mem[0].rsplit("_", 1)[1])]
        out.append(NeuralNode(
            node_id=f"pair{window}_{conv_idx}_{k}", session_key=f"conv_{conv_idx}", content=text,
            layer=NodeLayer.MESSAGE, embedding=emb, created_at=datetime.now(),
            metadata={"speaker": anchor.speaker_id, "datetime": anchor.metadata.get("datetime"),
                      "pair_members": mem}))
    return out


def unpair(ranked, node_by_id, seen=None):
    """Expand pair hits into member message nodes (reply first), dedupe, keep score order."""
    seen = set() if seen is None else seen
    out = []
    for n, c in ranked:
        for mid in n.metadata.get("pair_members") or [n.node_id]:
            if mid not in seen:
                seen.add(mid)
                out.append((node_by_id[mid], c))
    return out


def append_extra(base, extra_ranked, k=PAIR_EXTRA):
    have = {n.node_id for n, _ in base}
    extra = [(n, c) for n, c in extra_ranked if n.node_id not in have]
    return base + extra[:k]


def hits3(res, gold):
    mems = [{"text": n.content} for n, _ in res]
    return [int(check_gold_in_memories_substring(gold, mems, top_k=k)[0]) for k in (10, 50, len(mems))]


async def combined_main():
    data = json.load(open(Path(__file__).parent.parent / "evaluation" / "locomo" / "locomo10.json"))
    rows = []
    t0 = time.time()
    async with aiohttp.ClientSession() as http:
        for conv_idx, conv in enumerate(data):
            if ONLY_CONV and conv_idx not in ONLY_CONV:
                continue
            storage, linker, nodes, speakers = await ingest(http, conv_idx, conv)
            messages = flatten_messages(conv)
            pairs = await build_pair_nodes(http, conv_idx, messages, nodes, 2)
            node_by_id = {n.node_id: n for n in nodes}
            by_sp = {sp: [n for n in nodes if n.speaker_id.lower() == sp] for sp in speakers}
            pairs_by_sp = {sp: [p for p in pairs if p.speaker_id.lower() == sp] for sp in speakers}
            t_pool = Tesseract(storage)
            t_ag = {sp: Tesseract(await build_agent_store(by_sp[sp])) for sp in speakers}
            from NeuralGraph.mega_search import MegaIndex
            mega = MegaIndex(nodes, pairs)   # one index per conversation; speaker routing = pool filter
            kg_path = Path(__file__).parent / "results" / "kg_cache" / f"conv{conv_idx}.json"
            mega_kg = MegaIndex(nodes, pairs, kg=json.load(open(kg_path))) if kg_path.exists() else None
            mega_kgu = MegaIndex(nodes, pairs, kg=json.load(open(kg_path)) | {"_union_regex": True}) if kg_path.exists() else None

            qas = [qa for qa in conv.get("qa", []) if CATEGORIES.get(qa.get("category")) in JUDGE_CATS]
            qvecs = await cached_embeddings(http, f"conv{conv_idx}_questions_all",
                                            [qa["question"] for qa in conv.get("qa", [])])
            qvec_by_text = {qa["question"]: v for qa, v in zip(conv.get("qa", []), qvecs)}
            for qa in qas:
                q, gold, cat = qa["question"], qa.get("answer", ""), CATEGORIES[qa["category"]]
                qvec = qvec_by_text[q]
                named = named_speakers(q, speakers)
                if len(named) == 1:  # local pool
                    sp = named[0]
                    pool_nodes, pool_pairs, tsr = by_sp[sp], pairs_by_sp[sp], t_ag[sp]
                else:                # pooled fallback (0 or 2 speakers named)
                    pool_nodes, pool_pairs, tsr = nodes, pairs, t_pool
                embed_local = cosine_topk(pool_nodes, qvec, TOP_K)
                embed_pairs = unpair(cosine_topk(pool_nodes + pool_pairs, qvec, 2 * TOP_K), node_by_id)[:TOP_K]
                tess_local = (await tess(tsr, q, qvec, conv_idx))[:TOP_K]
                pair_backfill = unpair(cosine_topk(pool_pairs, qvec, 2 * PAIR_EXTRA), node_by_id,
                                       seen={n.node_id for n, _ in tess_local})
                pool_ids = {n.node_id for n in pool_nodes}
                W = TOP_K + PAIR_EXTRA
                tess_ids = [n.node_id for n, _ in tess_local]
                mega_VK = mega.retrieve(q, qvec, pool_ids, k=W, channels="VK")
                mega_VKG = mega.retrieve(q, qvec, pool_ids, k=W, channels="VKG")
                mega_VKG_tess = mega.retrieve(q, qvec, pool_ids, k=W, channels="VKG", extra_rankings=[tess_ids])
                mega_VG = mega.retrieve(q, qvec, pool_ids, k=W, channels="VG")
                mega_VKG_g2 = mega.retrieve(q, qvec, pool_ids, k=W, channels="VKG", weights={"G": 2.0})
                mega_VG50 = mega.retrieve(q, qvec, pool_ids, k=TOP_K, channels="VG")
                mega_VG_pairs = mega_VG50 + unpair(cosine_topk(pool_pairs, qvec, 2 * PAIR_EXTRA), node_by_id,
                                                   seen={n.node_id for n, _ in mega_VG50})[:PAIR_EXTRA]
                mega_VGT50 = mega.retrieve(q, qvec, pool_ids, k=TOP_K, channels="VG", extra_rankings=[tess_ids])
                mega_VGT_pairs = mega_VGT50 + unpair(cosine_topk(pool_pairs, qvec, 2 * PAIR_EXTRA), node_by_id,
                                                     seen={n.node_id for n, _ in mega_VGT50})[:PAIR_EXTRA]
                if mega_kg is not None:
                    kg_VGT50 = mega_kg.retrieve(q, qvec, pool_ids, k=TOP_K, channels="VG", extra_rankings=[tess_ids])
                    kg_VGT_pairs = kg_VGT50 + unpair(cosine_topk(pool_pairs, qvec, 2 * PAIR_EXTRA), node_by_id,
                                                     seen={n.node_id for n, _ in kg_VGT50})[:PAIR_EXTRA]
                    kg_G_only = mega_kg.retrieve(q, qvec, pool_ids, k=W, channels="G")
                    kg_VG_pairs = mega_kg.retrieve(q, qvec, pool_ids, k=TOP_K, channels="VG")
                    kg_VG_pairs = kg_VG_pairs + unpair(cosine_topk(pool_pairs, qvec, 2 * PAIR_EXTRA), node_by_id,
                                                       seen={n.node_id for n, _ in kg_VG_pairs})[:PAIR_EXTRA]
                else:
                    kg_VGT_pairs = kg_G_only = kg_VG_pairs = []
                if mega_kgu is not None:
                    kgu50 = mega_kgu.retrieve(q, qvec, pool_ids, k=TOP_K, channels="VG", extra_rankings=[tess_ids])
                    kgu_pairs = kgu50 + unpair(cosine_topk(pool_pairs, qvec, 2 * PAIR_EXTRA), node_by_id,
                                               seen={n.node_id for n, _ in kgu50})[:PAIR_EXTRA]
                else:
                    kgu_pairs = []
                variants = {
                    "KG+regex union: VG+tess50 + pairs": kgu_pairs,
                    "KG: graph channel only": kg_G_only,
                    "KG: mega_VG50 + pairs": kg_VG_pairs,
                    "KG: mega_VG+tess50 + pairs": kg_VGT_pairs,
                    "mega_VG50 + pair backfill": mega_VG_pairs,
                    "mega_VG+tess50 + pair backfill": mega_VGT_pairs,
                    "mega_VK (vec+bm25 rrf)": mega_VK,
                    "mega_VG (vec+graph rrf)": mega_VG,
                    "mega_VKG (vec+bm25+graph)": mega_VKG,
                    "mega_VKG graph x2": mega_VKG_g2,
                    "mega_VKG + tess channel": mega_VKG_tess,
                    "embed_local": embed_local,
                    "embed_local+pairs": embed_pairs,
                    "tess_local": tess_local,
                    "tess_local+pairs": tess_local + pair_backfill[:PAIR_EXTRA],
                    "stack_embedpairs_then_tess": append_extra(embed_pairs, tess_local),
                    "stack_tess_then_embedpairs": append_extra(tess_local, embed_pairs),
                    # controls for the 80-candidate window: same budget, no pair nodes
                    "tess_local+embed_union": append_extra(tess_local, embed_local),
                    "embed_local_top80": cosine_topk(pool_nodes, qvec, TOP_K + PAIR_EXTRA),
                }
                row = {"conv": conv_idx, "cat": cat, "q": q, "named": named}
                for name, res in variants.items():
                    row[name] = hits3(res, gold)
                rows.append(row)
            print(f"conv {conv_idx}: {len(qas)} questions ({time.time() - t0:.0f}s)")

    variants = ["KG+regex union: VG+tess50 + pairs", "KG: graph channel only", "KG: mega_VG50 + pairs", "KG: mega_VG+tess50 + pairs", "mega_VG50 + pair backfill", "mega_VG+tess50 + pair backfill", "mega_VK (vec+bm25 rrf)", "mega_VG (vec+graph rrf)", "mega_VKG (vec+bm25+graph)", "mega_VKG graph x2", "mega_VKG + tess channel",
                "embed_local", "embed_local+pairs", "tess_local", "tess_local+pairs",
                "stack_embedpairs_then_tess", "stack_tess_then_embedpairs",
                "tess_local+embed_union", "embed_local_top80"]
    print("\n=== Combined stack (recall@10 / @50 / @all; local pool if exactly one speaker named, else pooled) ===")
    print(f"{'variant':28}" + "".join(f"{c:>30}" for c in JUDGE_CATS) + f"{'ALL':>30}")
    for v in variants:
        line = f"{v:28}"
        for cat in list(JUDGE_CATS) + [None]:
            rs = [r for r in rows if cat is None or r["cat"] == cat]
            cell = " / ".join(pct(sum(r[v][i] for r in rs), len(rs)) for i in range(3)) + f" (n={len(rs)})"
            line += cell.rjust(30)
        print(line)
    if OUT_JSON:
        json.dump({"combined": rows}, open(OUT_JSON, "w"))


if __name__ == "__main__":
    asyncio.run(combined_main() if MODE == "combined" else main())
