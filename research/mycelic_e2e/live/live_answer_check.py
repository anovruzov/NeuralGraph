#!/usr/bin/env python3
"""Component-only live check of the holder answer step (dev split; n=40). See LIVE_ANSWER_CHECK.md.

Calls the product's real holder answer path, ``EvidenceStore.answer_question`` (what ``HolderService`` calls for a ``question``
envelope), on a fixed seeded sample of (user-asked question, routed holder) pairs of the dev run C1-S1, twice per pair:
once with the deterministic ``fake`` router and once with the live router (Qwen3-4B-Instruct-2507 on llama-server, built from the
OPENAI_BASE_URL / MYCELIC_MODEL_* variables in live_env.sh).

    python3 live_answer_check.py plan                 # writes pairs.json (seeded sample + labels), no model call
    set -a; . live_env.sh; set +a
    python3 live_answer_check.py run [--live-concurrency 4]   # writes LIVE_ANSWER_CHECK.jsonl
    python3 live_answer_check.py report               # tables from the JSONL (printed as Markdown)

Inputs are read-only copies/paths: the run data is a COPY of the dev run (``--data``), the gold and the views are read from the
original run directory (dev material only), the product code is the revision the run was made with (``--code``).
The copy's holder stores receive export-ledger rows for the check's own question ids; nothing else is written there.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import glob
import json
import random
import re
import sqlite3
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

SCRATCH = Path("/tmp/claude-0/-home-user-NeuralGraph/cb55ecbb-9144-58cb-a5af-0b006a90960a/scratchpad")
HERE = Path(__file__).resolve().parent
DEFAULTS = {
    "code": SCRATCH / "code" / "56f9832",           # product revision the dev run was made with
    "run": SCRATCH / "runs" / "C1-S1",               # original run dir: gold + views, READ ONLY
    "data": SCRATCH / "engC_live" / "data",          # copy of <run>/data (coord.db + holders/); the only thing written to
}
SEED = 20261009
N_NONGOLD_ANSWERED, N_GOLD_ANSWERED, N_NOEV = 3, 17, 20      # 20 answered (17 on gold holders) + 20 no_evidence
JSONL = HERE / "LIVE_ANSWER_CHECK.jsonl"
DIAG = HERE / "data" / "answer_check_diag.jsonl"      # follow-up: raw model replies for the pairs where live and fake disagreed
PAIRS = HERE / "data" / "answer_check_pairs.json"


# ----------------------------------------------------------------------------------------------- labelling
def ctx_phrase(question_text: str, templates_py: Path) -> str | None:
    """The question's context phrase = '<adjective> <noun>' from the dev template bank's word lists."""
    src = templates_py.read_text()
    def words(name: str) -> set[str]:
        m = re.search(name + r'=tuple\("""(.*?)""".split\(\)\)', src, re.S)
        return set(m.group(1).split()) if m else set()
    adjs, nouns = words("ctx_adjectives"), words("ctx_nouns")
    toks = re.findall(r"[a-z]+", question_text.lower())
    for a, b in zip(toks, toks[1:]):
        if a in adjs and b in nouns:
            return f"{a} {b}"
    return None


def label_excerpt(text: str, *, ctx: str | None, gold_entity: str | None, decoys: list[str], holder_in_gold: bool) -> str:
    """gold            = a gold holder's record that carries the question's context phrase (and, when the task has an answer, the gold entity)
       gold-text/nongold = carries the context phrase and the gold entity but sits on a holder that is not on the task's gold-holder list
       decoy           = carries the context phrase and a decoy option
       other           = anything else (filler, renewal/window/seats records, other contexts)"""
    t = (text or "").lower()
    has_ctx = bool(ctx) and ctx in t
    has_gold = bool(gold_entity) and gold_entity.lower() in t
    if holder_in_gold and has_ctx and (has_gold or not gold_entity):
        return "gold"
    if has_ctx and has_gold:
        return "gold-text/nongold"
    if has_ctx and any(d.lower() in t for d in decoys):
        return "decoy"
    return "other"


# ----------------------------------------------------------------------------------------------- plan
def build_frame(paths: dict) -> list[dict]:
    run, data = Path(paths["run"]), Path(paths["data"])
    gold = json.loads((run / "tasks_dev.gold.json").read_text())["gold"]
    q2t = {}
    for f in sorted(glob.glob(str(run / "views" / "dev-*.json"))):
        v = json.loads(Path(f).read_text())
        qid = (v.get("ids") or {}).get("question_id")
        if qid:
            q2t[qid] = v["task_id"]
    c = sqlite3.connect(f"file:{data / 'coord.db'}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT r.question_id, r.holder_id, r.status, r.content, r.evidence_ref_ids, q.text, r.route_id "
                     "FROM responses r JOIN questions q USING(question_id) WHERE q.asker_type='user' ORDER BY r.question_id, r.holder_id").fetchall()
    out = []
    for r in rows:
        t = q2t.get(r["question_id"])
        if t is None or r["status"] not in ("answered", "no_evidence"):
            continue
        g = gold[t]
        if r["holder_id"] in g["forbidden_holder_ids"]:
            continue
        out.append({"question_id": r["question_id"], "holder_id": r["holder_id"], "task_id": t, "cls": g["cls"], "det_status": r["status"],
                    "det_content": r["content"], "det_n_refs": len(json.loads(r["evidence_ref_ids"] or "[]")), "route_id": r["route_id"],
                    "question_text": r["text"], "gold_holder": r["holder_id"] in g["holders"], "gold_answer": g["answer"],
                    "expected_abstain": bool(g["expected_abstain"]), "decoys": list(g["decoy_options"]),
                    "entity_id": g.get("entity_id")})
    return out


def pick(frame: list[dict], rng: random.Random, n: int, used_q: set[str], *, by_class: bool) -> list[dict]:
    """One pair per question (random holder among the candidates), questions not in ``used_q``, classes taken round-robin."""
    per_q: dict[str, list[dict]] = collections.defaultdict(list)
    for p in frame:
        if p["question_id"] not in used_q:
            per_q[p["question_id"]].append(p)
    cands = [rng.choice(sorted(v, key=lambda p: p["holder_id"])) for _, v in sorted(per_q.items())]
    if not by_class:
        rng.shuffle(cands)
        return cands[:n]
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for p in cands:
        groups[p["cls"]].append(p)
    classes = sorted(groups)
    rng.shuffle(classes)
    for k in classes:
        rng.shuffle(groups[k])
    chosen: list[dict] = []
    while len(chosen) < n and any(groups.values()):
        for k in classes:
            if groups[k] and len(chosen) < n:
                chosen.append(groups[k].pop())
    return chosen


def plan(paths: dict) -> list[dict]:
    frame = build_frame(paths)
    rng = random.Random(SEED)
    used: set[str] = set()
    ng_ans = pick([p for p in frame if p["det_status"] == "answered" and not p["gold_holder"]], rng, N_NONGOLD_ANSWERED, used, by_class=False)
    used |= {p["question_id"] for p in ng_ans}
    g_ans = pick([p for p in frame if p["det_status"] == "answered" and p["gold_holder"]], rng, N_GOLD_ANSWERED, used, by_class=True)
    used |= {p["question_id"] for p in g_ans}
    noev = pick([p for p in frame if p["det_status"] == "no_evidence"], rng, N_NOEV, used, by_class=True)
    sample = []
    for stratum, items in (("answered/gold-holder", g_ans), ("answered/non-gold-holder", ng_ans), ("no_evidence/non-gold-holder", noev)):
        for p in items:
            sample.append({**p, "stratum": stratum})
    for i, p in enumerate(sample):
        p["idx"] = i
    stats = {"seed": SEED, "frame_pairs": len(frame),
             "frame_counts": {f"{s}/{'gold' if g else 'non-gold'}": sum(1 for p in frame if p["det_status"] == s and p["gold_holder"] == g)
                              for s in ("answered", "no_evidence") for g in (True, False)},
             "n": len(sample)}
    PAIRS.parent.mkdir(parents=True, exist_ok=True)
    PAIRS.write_text(json.dumps({"stats": stats, "pairs": sample}, indent=1))
    return sample


# ----------------------------------------------------------------------------------------------- run
async def run(paths: dict, live_conc: int, only: set[int] | None = None) -> None:
    sys.path.insert(0, str(paths["code"]))
    from mycelic.evidence.service import EvidenceStore                     # noqa: E402
    from mycelic.models.router import DefaultModelRouter                  # noqa: E402
    from mycelic.config import load_settings                              # noqa: E402
    from mycelic.org import routable_domains                              # noqa: E402

    pairs = json.loads(PAIRS.read_text())["pairs"]
    if only:
        pairs = [p for p in pairs if p["idx"] in only]
    out_path = DIAG if only else JSONL
    data = Path(paths["data"])
    c = sqlite3.connect(f"file:{data / 'coord.db'}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    envs = {}
    for r in c.execute("SELECT msg_id, payload FROM transport_messages WHERE msg_id LIKE 'q:%'"):
        envs[r["msg_id"]] = json.loads(r["payload"])

    s = load_settings()
    assert all(v.startswith("openai:") for v in s.model_tiers.values()), "source live_env.sh first (live tiers must be openai:*)"
    live = DefaultModelRouter.from_settings(s)
    fake = DefaultModelRouter.from_settings(SimpleNamespace(model_tiers={t: f"fake:mycelic-fake-{t}" for t in ("light", "standard", "heavy")}))
    print("live router:", json.dumps(live.describe()["tiers"]), "| base:", s.openai_base_url, "| timeout:", s.model_timeout_seconds, flush=True)

    stores: dict[str, EvidenceStore] = {}
    captured: dict[str, list] = {}
    raw_log: list[str] = []
    if only:                                                     # diagnostics: keep the model's raw reply text (run sequentially)
        prov = live.providers["openai"]
        orig_complete = prov.complete

        async def spy_complete(*a, **kw):
            comp = await orig_complete(*a, **kw)
            raw_log.append(comp.text)
            return comp
        prov.complete = spy_complete

    def open_store(hid: str) -> EvidenceStore:
        if hid in stores:
            return stores[hid]
        row = c.execute("SELECT * FROM holders WHERE holder_id=?", (hid,)).fetchone()
        h = {**dict(row), "domains": json.loads(row["domains"] or "[]"), "published_domains": json.loads(row["published_domains"] or "[]")}
        owner_ids = [row["owner_id"]] if row["owner_type"] == "user" and row["owner_id"] else []
        st = EvidenceStore(data / "holders" / hid / "evidence.db", holder_id=hid, tenant_id=row["tenant_id"], llm=None, router=None,
                           export_policy=json.loads(row["export_policy"] or "{}"), domains=routable_domains(h), extract=False, owner_ids=owner_ids)
        orig = st._run_answer

        async def spy(question, evidence, *, question_id, provenance, force_rule=False):
            captured[question_id] = [{"ref_id": e["ref_id"], "excerpt": e["excerpt"], "observed_at": e.get("observed_at")} for e in evidence]
            return await orig(question, evidence, question_id=question_id, provenance=provenance, force_rule=force_rule)
        st._run_answer = spy                                    # observation only: same arguments, same result
        stores[hid] = st
        return st

    templates_py = Path(paths["code"]) / "research/mycelic_e2e/bench/templates_dev.py"

    async def one(p: dict, mode: str, router) -> dict:
        st = open_store(p["holder_id"])
        env = envs[f"q:{p['question_id']}:{p['holder_id']}"]
        question = dict(env["payload"])
        question.setdefault("tenant_id", env["tenant_id"])      # HolderService._dispatch does exactly this
        qid = f"{p['question_id']}~check-{mode}"
        question["question_id"] = qid                           # fresh id: no replay of the run's stored answer or exports
        st.router = router
        t0 = time.perf_counter()
        try:
            resp = await st.answer_question(question, idempotency_key=f"chk:{mode}:{p['question_id']}:{p['holder_id']}")
            err = None
        except Exception as exc:                                # recorded, never hidden
            resp, err = None, f"{exc.__class__.__name__}: {exc}"
        lat = time.perf_counter() - t0
        ctx = ctx_phrase(p["question_text"], templates_py)
        gold_entity = None if p["expected_abstain"] else p["gold_answer"]
        ev = captured.get(qid, [])
        def lab(text: str) -> str:
            return label_excerpt(text, ctx=ctx, gold_entity=gold_entity, decoys=p["decoys"], holder_in_gold=p["gold_holder"])
        by_ref = {e["ref_id"]: e for e in ev}
        cited = []
        for r in (resp or {}).get("evidence_refs", []):
            full = by_ref.get(r["ref_id"], {}).get("excerpt") or r.get("disclosed_excerpt") or ""
            cited.append({"ref_id": r["ref_id"], "source_root_id": r.get("source_root_id"), "excerpt": full, "label": lab(full)})
        calls = [x for x in router.ledger.calls if x.question_id == qid]
        content = (resp or {}).get("content", "")
        low = content.lower()
        return {
            "status": (resp or {}).get("status"), "error": err, "answer_method": ((resp or {}).get("provenance") or {}).get("answer_method"),
            "content": content, "confidence": (resp or {}).get("confidence"), "cited": cited,
            "mentions_gold_entity": bool(gold_entity) and gold_entity.lower() in low,
            "mentions_decoy_entity": any(d.lower() in low for d in p["decoys"]),
            "evidence_n": len(ev), "evidence_gold_n": sum(1 for e in ev if lab(e["excerpt"]) == "gold"),
            "evidence": [{"ref_id": e["ref_id"], "excerpt": e["excerpt"], "label": lab(e["excerpt"])} for e in ev],
            "latency_s": round(lat, 2), "model_calls": [{"tier": x.tier, "in": x.input_tokens, "out": x.output_tokens, "latency_ms": x.latency_ms,
                                                       "ok": x.ok, "error": x.error} for x in calls],
            "tokens_in": sum(x.input_tokens for x in calls), "tokens_out": sum(x.output_tokens for x in calls),
        }, ctx

    rows: dict[int, dict] = {p["idx"]: {**p} for p in pairs}
    t_all = time.perf_counter()
    for p in pairs:                                              # deterministic router: cheap, sequential
        rows[p["idx"]]["fake"], ctx = await one(p, "fake", fake)
        rows[p["idx"]]["ctx_phrase"] = ctx
    print(f"fake pass done in {time.perf_counter() - t_all:.1f}s", flush=True)

    sem = asyncio.Semaphore(live_conc)
    done = 0

    async def live_one(p: dict) -> None:
        nonlocal done
        async with sem:
            rows[p["idx"]]["live"], _ = await one(p, "live-diag" if only else "live", live)
            if only:
                rows[p["idx"]]["live"]["raw_replies"] = list(raw_log)
                raw_log.clear()
            done += 1
            lv = rows[p["idx"]]["live"]
            print(f"[{done}/{len(pairs)}] idx={p['idx']} {p['stratum']:28s} fake={rows[p['idx']]['fake']['status']:11s} live={lv['status']} "
                  f"method={lv['answer_method']} {lv['latency_s']}s in={lv['tokens_in']} out={lv['tokens_out']}", flush=True)
    t_live = time.perf_counter()
    await asyncio.gather(*(live_one(p) for p in pairs))
    wall = time.perf_counter() - t_live
    with out_path.open("w") as fh:
        meta = {"meta": {"title": "component-only live check; dev split; n=40", "seed": SEED, "run": str(paths["run"]), "code_rev": Path(paths["code"]).name,
                         "live_router": live.describe()["tiers"], "live_base_url": s.openai_base_url, "live_concurrency": live_conc,
                         "live_wall_s": round(wall, 1), "fake_router": "fake (deterministic)"}}
        fh.write(json.dumps(meta) + "\n")
        for i in sorted(rows):
            fh.write(json.dumps(rows[i], ensure_ascii=False) + "\n")
    print(f"live pass wall {wall:.0f}s; wrote {out_path}", flush=True)
    for st in stores.values():
        await st.close()
    await live.close()
    await fake.close()


# ----------------------------------------------------------------------------------------------- report
def report() -> str:
    lines = [json.loads(l) for l in JSONL.read_text().splitlines()]
    meta, rows = lines[0]["meta"], lines[1:]
    out: list[str] = []
    for r in rows:                                   # labels are a pure function of the stored texts: recompute and keep the file in step
        gold_entity = None if r["expected_abstain"] else r["gold_answer"]
        for mode in ("fake", "live"):
            for e in r[mode]["evidence"] + r[mode]["cited"]:
                e["label"] = label_excerpt(e["excerpt"], ctx=r["ctx_phrase"], gold_entity=gold_entity, decoys=r["decoys"], holder_in_gold=r["gold_holder"])
            r[mode]["evidence_gold_n"] = sum(1 for e in r[mode]["evidence"] if e["label"] == "gold")
    JSONL.write_text("\n".join([json.dumps(lines[0])] + [json.dumps(r, ensure_ascii=False) for r in rows]) + "\n")
    def ok_live(r): return r["live"]["error"] is None and (r["live"]["answer_method"] == "model" or r["live"]["evidence_n"] == 0)   # evidence_n 0: nothing retrieved, no model call by design
    n_nocall = sum(1 for r in rows if r["live"]["evidence_n"] == 0)
    out.append(f"pairs where retrieval returned nothing (the model is not called, both routers say no_evidence): {n_nocall}")
    def ans(x): return x["status"] == "answered"
    out.append(f"pairs: {len(rows)}; live model calls made: {sum(r['live']['answer_method'] == 'model' for r in rows)}; "
               f"live errors / rule fallbacks: {sum(not ok_live(r) for r in rows)}")
    # fake reproduces the run's recorded deterministic response?
    rep = sum(1 for r in rows if r["fake"]["status"] == r["det_status"])
    rep_c = sum(1 for r in rows if r["fake"]["status"] == r["det_status"] and (r["det_status"] != "answered" or r["fake"]["content"] == r["det_content"]))
    out.append(f"fake re-run reproduces the recorded status: {rep}/{len(rows)}; status and answer text: {rep_c}/{len(rows)}")

    def agree(subset):
        a = collections.Counter()
        for r in subset:
            if not ok_live(r):
                a["live unavailable"] += 1
                continue
            f, l = ans(r["fake"]), ans(r["live"])
            a["both answer" if f and l else "only fake" if f else "only live" if l else "neither"] += 1
        return a
    strata = sorted({r["stratum"] for r in rows})
    out.append("\nagreement (fake vs live answered):")
    out.append("| subset | n | both answer | only fake | only live | neither | live unavailable |")
    out.append("|---|---|---|---|---|---|---|")
    for name, sub in [("all", rows)] + [(s, [r for r in rows if r["stratum"] == s]) for s in strata]:
        a = agree(sub)
        out.append(f"| {name} | {len(sub)} | {a['both answer']} | {a['only fake']} | {a['only live']} | {a['neither']} | {a['live unavailable']} |")

    gold_rows = [r for r in rows if r["gold_holder"]]
    out.append(f"\ngold-holder pairs: {len(gold_rows)}")
    out.append("| metric | fake | live |")
    out.append("|---|---|---|")
    def hit(x): return ans(x) and any(c["label"] == "gold" for c in x["cited"])
    def clean(x): return ans(x) and x["cited"] and all(c["label"] == "gold" for c in x["cited"])
    def ent(x): return ans(x) and x["mentions_gold_entity"]
    def dec(x): return ans(x) and x["mentions_decoy_entity"]
    ge = [r for r in gold_rows if not r["expected_abstain"]]
    out.append(f"| answered | {sum(ans(r['fake']) for r in gold_rows)}/{len(gold_rows)} | {sum(ans(r['live']) for r in gold_rows if ok_live(r))}/{sum(ok_live(r) for r in gold_rows)} |")
    out.append(f"| hit (answered and cites >=1 gold observation) | {sum(hit(r['fake']) for r in gold_rows)}/{len(gold_rows)} | {sum(hit(r['live']) for r in gold_rows if ok_live(r))}/{sum(ok_live(r) for r in gold_rows)} |")
    out.append(f"| clean hit (every cited excerpt is gold) | {sum(bool(clean(r['fake'])) for r in gold_rows)}/{len(gold_rows)} | {sum(bool(clean(r['live'])) for r in gold_rows if ok_live(r))}/{sum(ok_live(r) for r in gold_rows)} |")
    out.append(f"| answer text names the gold entity (answerable tasks) | {sum(ent(r['fake']) for r in ge)}/{len(ge)} | {sum(ent(r['live']) for r in ge if ok_live(r))}/{sum(ok_live(r) for r in ge)} |")
    out.append(f"| answer text names a decoy entity (answerable tasks) | {sum(dec(r['fake']) for r in ge)}/{len(ge)} | {sum(dec(r['live']) for r in ge if ok_live(r))}/{sum(ok_live(r) for r in ge)} |")
    out.append(f"| gold observation among the retrieved 8 (same retrieval for both) | {sum(r['fake']['evidence_gold_n'] > 0 for r in gold_rows)}/{len(gold_rows)} | same |")

    out.append("\nlive citations that are not gold, and live disagreements:")
    for r in rows:
        if not ok_live(r):
            out.append(f"- idx {r['idx']} {r['stratum']}: LIVE UNAVAILABLE status={r['live']['status']} method={r['live']['answer_method']} error={r['live']['error']}")
            continue
        bad = [c for c in r["live"]["cited"] if c["label"] != "gold"]
        flag = (ans(r["live"]) and bad) or (ans(r["live"]) != ans(r["fake"])) or (r["gold_holder"] and not hit(r["live"]))
        if flag:
            out.append(f"- idx {r['idx']} {r['task_id']} ({r['cls']}) {r['stratum']}: fake={r['fake']['status']} live={r['live']['status']}; "
                       f"live answer={r['live']['content']!r}; live cited={[(c['label'], c['excerpt']) for c in r['live']['cited']]}")
    lv = [r["live"] for r in rows if r["live"]["answer_method"] == "model"]
    if lv:
        out.append("\nlive cost per pair (answer_from_evidence incl. any repair retries):")
        for k in ("latency_s", "tokens_in", "tokens_out"):
            xs = [x[k] for x in lv]
            out.append(f"- {k}: median {statistics.median(xs):.1f}, mean {statistics.mean(xs):.1f}, min {min(xs):.1f}, max {max(xs):.1f}")
        out.append(f"- model calls per pair: {collections.Counter(len(x['model_calls']) for x in lv)}; invalid-output retries: {sum(max(0, len(x['model_calls']) - 1) for x in lv)}")
        out.append(f"- live pass wall time: {meta['live_wall_s']} s at concurrency {meta['live_concurrency']}")
    return "\n".join(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "run", "report"])
    ap.add_argument("--live-concurrency", type=int, default=4)
    ap.add_argument("--only", default="", help="comma-separated pair idx: diagnostics run (live only, raw replies kept, written to data/answer_check_diag.jsonl)")
    for k, v in DEFAULTS.items():
        ap.add_argument(f"--{k}", default=str(v))
    a = ap.parse_args()
    paths = {k: Path(getattr(a, k)) for k in DEFAULTS}
    if a.cmd == "plan":
        sample = plan(paths)
        print(json.dumps(json.loads(PAIRS.read_text())["stats"], indent=1))
        print(collections.Counter((p["stratum"], p["cls"]) for p in sample))
    elif a.cmd == "run":
        if a.live_concurrency > 4:
            raise SystemExit("at most 4 concurrent live requests")
        asyncio.run(run(paths, a.live_concurrency, {int(x) for x in a.only.split(',') if x} or None))
    else:
        print(report())
