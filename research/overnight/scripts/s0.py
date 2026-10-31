#!/usr/bin/env python3
"""One S0 experiment in one command: context dump (cached) -> answer_eval -> compare -> ledger row.

  s0.py --id P1 --db lme_base --prompt auto_v1 [--render quotes] [--k 20] [--channels VKG]
        [--retrieval "weight_keyword=1.0"] [--model qwen3.8-9b-mlx] [--think] [--base lme_base_vk15_concise]
        [--set lme|locomo] [--note "..."] [--stage S0] [--official-judge]  (result stem gets "_off")
        [--route-regex tight|wide|<re>] (stem _rrtight/_rrc<hash>) [--route-llm] (_rllm) [--think-early-stop] [--route-compute-max-tokens-first N] (_estop_f<N>)
        [--concise-max-tokens N] (_cc<N>) [--verify verify_v1 [--verify-apply-unsupported idk]] (_ververify_v1[_vidk])

Names: context file  = <db>_<rtag>_ctx.json         (rtag = k<k>[_<channels>][_<retrieval slug>], default vk15)
       result file   = <db>_<rtag>_<prompt>[_<render>][_<modeltag>][_think]_acc.json
Everything lands in .context/overnight/results. The ledger row is appended to research/overnight/LEDGER.md with an
automatic verdict: baseline (no --base), promising (>= +4 on strict or lenient and nothing < -2), rejected
(<= -4 on either), else noise. The judge agent may overrule in the notes column later.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RES = ROOT / ".context/overnight/results"
BIN = ROOT / "target/release/chat-memory-context"
SCRIPTS = Path(__file__).resolve().parent
LEDGER = ROOT / "research/overnight/LEDGER.md"


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", s.replace("=", "").replace(".", "p"))[:40]


def model_slug(m: str) -> str:
    """Stem tag for a model id: part after the last '/', non-alphanumerics stripped, max 16 chars."""
    return re.sub(r"[^A-Za-z0-9]+", "", m.split("/")[-1])[:16]


def run(cmd: list[str], log: Path | None = None) -> str:
    print("$", " ".join(str(c) for c in cmd), flush=True)
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if log:
        log.write_text(p.stdout + "\n" + p.stderr)
    if p.returncode != 0:
        print(p.stdout[-3000:], p.stderr[-3000:], file=sys.stderr)
        raise SystemExit(f"command failed rc={p.returncode}")
    return p.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--db", required=True, help="db name under .context/overnight/db (dir for lme, file stem for locomo)")
    ap.add_argument("--set", default="lme", choices=["lme", "locomo"])
    ap.add_argument("--sessions", type=int, default=8)
    ap.add_argument("--conv", type=int, default=0)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--file", default="", help="LME dataset file override (e.g. the 18-question subset); implies --n 0")
    ap.add_argument("--prompt", default="concise")
    ap.add_argument("--render", default="plain")
    ap.add_argument("--k", type=int, default=0)
    ap.add_argument("--channels", default="")
    ap.add_argument("--retrieval", default="")
    ap.add_argument("--max-chars", type=int, default=0)
    ap.add_argument("--model", default="qwen3.8-9b-mlx")
    ap.add_argument("--judge-model", default="google/gemma-4-e4b")
    ap.add_argument("--think", action="store_true")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--base", default="", help="baseline result stem to compare against (no _acc.json)")
    ap.add_argument("--note", default="")
    ap.add_argument("--stage", default="S0")
    ap.add_argument("--force-ctx", action="store_true")
    ap.add_argument("--calendar", action="store_true")
    ap.add_argument("--cascade", default="")
    ap.add_argument("--route-pref", default="")
    ap.add_argument("--route-compute", default="")
    ap.add_argument("--route-compute-think", action="store_true")
    ap.add_argument("--route-compute-max-tokens", type=int, default=0, help="0 = answer_eval default (2500)")
    ap.add_argument("--route-regex", default="", help="tight | wide | <regex>; '' = answer_eval default (wide)")
    ap.add_argument("--route-llm", action="store_true", help="LLM router for the compute route (alone = LLM decides; with --route-regex = regex OR LLM); stem _rllm")
    ap.add_argument("--think-early-stop", action="store_true", help="two-stage thinking budget for the routed call (needs --route-compute-think)")
    ap.add_argument("--route-compute-max-tokens-first", type=int, default=0, help="0 = answer_eval default (900); implies --think-early-stop")
    ap.add_argument("--concise-max-tokens", type=int, default=0, help="0 = answer_eval default (160)")
    ap.add_argument("--verify", default="", help="verification prompt spec (e.g. verify_v1)")
    ap.add_argument("--verify-apply-unsupported", default="", choices=["", "keep", "idk"])
    ap.add_argument("--quotes-per-memory", type=int, default=0)
    ap.add_argument("--extra-judges", action="store_true")
    ap.add_argument("--official-judge", action="store_true", help="also grade the LongMemEval official-prompt `official` column")
    ap.add_argument("--k-limit", type=int, default=0)
    ap.add_argument("--max-tokens", type=int, default=0)
    ap.add_argument("--quote-chars", type=int, default=0)
    ap.add_argument("--route-model", default="", help="model id for the routed compute call (default: --model); stem _rm<slug>")
    ap.add_argument("--cascade-model", default="", help="model id for the cascade call (default: --model); stem _cm<slug>")
    ap.add_argument("--cascade-think", action="store_true")
    ap.add_argument("--cascade-max-tokens", type=int, default=0, help="0 = answer_eval default (3000)")
    ap.add_argument("--vote", type=int, default=1)
    ap.add_argument("--vote-temperature", type=float, default=0.0, help="0 = answer_eval default (0.7)")
    ap.add_argument("--answer-model-think", action="store_true")
    ap.add_argument("--agentic-search", action="store_true", help="routed thinking reader may request one extra `context` retrieval (db derived from --db)")
    ap.add_argument("--search-k", type=int, default=5)
    a = ap.parse_args()

    RES.mkdir(parents=True, exist_ok=True)
    rtag = f"k{a.k or 15}" + (f"_{a.channels}" if a.channels else "") + (f"_{slug(a.retrieval)}" if a.retrieval else "") \
        + (f"_mc{a.max_chars}" if a.max_chars else "")
    if rtag == "k15":
        rtag = "vk15"
    ctx = RES / f"{a.db}_{rtag}_ctx.json"
    if a.force_ctx or not ctx.exists():
        cmd = [BIN, a.set]
        if a.set == "lme":
            cmd += ["--db-dir", ROOT / ".context/overnight/db" / a.db, "--n", 0 if a.file else a.n, "--seed", a.seed]
            if a.file:
                cmd += ["--file", a.file]
        else:
            cmd += ["--db", ROOT / ".context/overnight/db" / f"{a.db}.db", "--conv", a.conv, "--sessions", a.sessions]
        cmd += ["--with-sources", "--out", ctx]
        if a.k:
            cmd += ["--k", a.k]
        if a.channels:
            cmd += ["--channels", a.channels]
        if a.retrieval:
            cmd += ["--retrieval", a.retrieval]
        if a.max_chars:
            cmd += ["--max-chars", a.max_chars]
        run(cmd, ROOT / ".context/overnight/logs" / f"{ctx.stem}.log")
    doc = json.loads(ctx.read_text())
    rows = doc["rows"]
    ev = [r for r in rows if r.get("evidence_retrieved") is not None and r.get("type") != "abstention"]
    turn_at_k = round(100 * sum(1 for r in ev if r["evidence_retrieved"]) / max(1, len(ev)), 1)

    mtag = "" if a.model == "qwen3.8-9b-mlx" else "_" + slug(a.model.split("/")[-1])
    stem = f"{a.db}_{rtag}_{Path(a.prompt).stem}" + ("" if a.render == "plain" else f"_{a.render}") + mtag \
        + ("_think" if a.think else "") + ("" if a.temperature == 0 else f"_t{a.temperature}") \
        + ("_cal" if a.calendar else "") + (f"_casc{Path(a.cascade).stem}" if a.cascade else "") \
        + (f"_pref{Path(a.route_pref).stem}" if a.route_pref else "") \
        + (f"_rc{Path(a.route_compute).stem}" if a.route_compute else "") \
        + (f"_rm{model_slug(a.route_model)}" if a.route_model else "") + (f"_cm{model_slug(a.cascade_model)}" if a.cascade_model else "") \
        + ("_rcthink" if a.route_compute_think else "") + (f"_rcmt{a.route_compute_max_tokens}" if a.route_compute_max_tokens else "") \
        + ("" if a.route_regex in ("", "wide") else "_rrtight" if a.route_regex == "tight" else f"_rrc{hashlib.sha1(a.route_regex.encode()).hexdigest()[:6]}") \
        + ("_rllm" if a.route_llm else "") \
        + ("_estop" if a.think_early_stop or a.route_compute_max_tokens_first else "") \
        + (f"_f{a.route_compute_max_tokens_first}" if a.route_compute_max_tokens_first else "") \
        + (f"_cc{a.concise_max_tokens}" if a.concise_max_tokens else "") \
        + (f"_ver{Path(a.verify).stem}" if a.verify else "") + ("_vidk" if a.verify and a.verify_apply_unsupported == "idk" else "") + (f"_kl{a.k_limit}" if a.k_limit else "") \
        + (f"_mt{a.max_tokens}" if a.max_tokens else "") + (f"_qc{a.quote_chars}" if a.quote_chars else "") \
        + (f"_qpm{a.quotes_per_memory}" if a.quotes_per_memory else "") + ("_amthink" if a.answer_model_think else "") \
        + ("_off" if a.official_judge else "") + ("_cthink" if a.cascade_think else "") + (f"_cmt{a.cascade_max_tokens}" if a.cascade_max_tokens else "") \
        + ("_agsrch" if a.agentic_search else "") + (f"_sk{a.search_k}" if a.agentic_search and a.search_k != 5 else "") \
        + (f"_vote{a.vote}" if a.vote > 1 else "") + (f"_vt{slug(str(a.vote_temperature))}" if a.vote > 1 and a.vote_temperature else "")
    out = RES / f"{stem}_acc.json"
    cmd = [sys.executable, SCRIPTS / "answer_eval.py", "--contexts", ctx, "--out", out, "--prompt", a.prompt,
           "--render", a.render, "--model", a.model, "--judge-model", a.judge_model, "--threads", a.threads,
           "--temperature", a.temperature, "--label", a.id]
    if a.think:
        cmd.append("--think")
    if a.calendar:
        cmd.append("--calendar")
    if a.extra_judges:
        cmd.append("--extra-judges")
    if a.official_judge:
        cmd.append("--official-judge")
    if a.cascade:
        cmd += ["--cascade", a.cascade]
    if a.route_pref:
        cmd += ["--route-pref", a.route_pref]
    if a.route_compute:
        cmd += ["--route-compute", a.route_compute]
    if a.route_model:
        cmd += ["--route-model", a.route_model]
    if a.cascade_model:
        cmd += ["--cascade-model", a.cascade_model]
    if a.route_compute_think:
        cmd.append("--route-compute-think")
    if a.route_compute_max_tokens:
        cmd += ["--route-compute-max-tokens", a.route_compute_max_tokens]
    if a.route_regex:
        cmd += ["--route-regex", a.route_regex]
    if a.route_llm:
        cmd.append("--route-llm")
    if a.think_early_stop:
        cmd.append("--think-early-stop")
    if a.route_compute_max_tokens_first:
        cmd += ["--route-compute-max-tokens-first", a.route_compute_max_tokens_first]
    if a.concise_max_tokens:
        cmd += ["--concise-max-tokens", a.concise_max_tokens]
    if a.verify:
        cmd += ["--verify", a.verify]
    if a.verify_apply_unsupported:
        cmd += ["--verify-apply-unsupported", a.verify_apply_unsupported]
    if a.quotes_per_memory:
        cmd += ["--quotes-per-memory", a.quotes_per_memory]
    if a.k_limit:
        cmd += ["--k-limit", a.k_limit]
    if a.max_tokens:
        cmd += ["--max-tokens", a.max_tokens]
    if a.quote_chars:
        cmd += ["--quote-chars", a.quote_chars]
    if a.answer_model_think:
        cmd.append("--answer-model-think")
    if a.cascade_think:
        cmd.append("--cascade-think")
    if a.agentic_search:
        cmd.append("--agentic-search")
        if a.set == "lme":
            cmd += ["--db-dir", ROOT / ".context/overnight/db" / a.db]
        else:
            cmd += ["--db", ROOT / ".context/overnight/db" / f"{a.db}.db"]
        if a.search_k != 5:
            cmd += ["--search-k", a.search_k]
    if a.cascade_max_tokens:
        cmd += ["--cascade-max-tokens", a.cascade_max_tokens]
    if a.vote > 1:
        cmd += ["--vote", a.vote]
        if a.vote_temperature:
            cmd += ["--vote-temperature", a.vote_temperature]
    run(cmd, ROOT / ".context/overnight/logs" / f"{stem}.log")
    s = json.loads(out.read_text())["summary"]
    o = s["overall"]
    line = f"| {a.id} | {a.stage} | {'LME-' + str(len(rows)) if a.set == 'lme' else 'LoCoMo c' + str(a.conv)} | {a.note or a.prompt} | " \
           f"{a.db} / {rtag} / {Path(a.prompt).stem}{'+' + a.render if a.render != 'plain' else ''}{' +cal' if a.calendar else ''}{' +casc:' + Path(a.cascade).stem if a.cascade else ''}{' +pref' if a.route_pref else ''}{' +rcomp:' + Path(a.route_compute).stem if a.route_compute else ''}{' rr:' + ('tight' if a.route_regex == 'tight' else 'wide' if a.route_regex == 'wide' else 'c' + hashlib.sha1(a.route_regex.encode()).hexdigest()[:6]) if a.route_regex else ''}{' estop' + str(a.route_compute_max_tokens_first or 900) if a.think_early_stop or a.route_compute_max_tokens_first else ''}{' cc' + str(a.concise_max_tokens) if a.concise_max_tokens else ''}{' +ver:' + Path(a.verify).stem + ('/idk' if a.verify_apply_unsupported == 'idk' else '') if a.verify else ''}{' +agsrch' + (str(a.search_k) if a.search_k != 5 else '') if a.agentic_search else ''}{' qpm' + str(a.quotes_per_memory) if a.quotes_per_memory else ''}{' kl' + str(a.k_limit) if a.k_limit else ''} / {a.model.split('/')[-1]}{' think' if a.think else ''}{' rm:' + a.route_model.split('/')[-1] if a.route_model else ''}{' cm:' + a.cascade_model.split('/')[-1] if a.cascade_model else ''} | " \
           f"{o['strict']} | {o['lenient']} | {o['substring']} | {turn_at_k} |"
    verdict, pcol, dS, dL = "baseline", "—", "—", "—"
    if a.base:
        base = RES / f"{a.base}_acc.json"
        table = run([sys.executable, SCRIPTS / "compare.py", base, out, "--md"])
        print(table)
        m = {r.split("|")[1].strip(): r.split("|") for r in table.splitlines() if r.startswith("| ") and not r.startswith("| metric")}
        ds, dl = float(m["strict"][4]), float(m["lenient"][4])
        ps, pl = m["strict"][7].strip(), m["lenient"][7].strip()
        dS, dL, pcol = f"{ds:+.1f}", f"{dl:+.1f}", f"{ps}/{pl}"
        if ds <= -4 or dl <= -4:
            verdict = "rejected"
        elif (ds >= 4 or dl >= 4) and ds > -2 and dl > -2:
            verdict = "promising"
        else:
            verdict = "noise"
    line += f" {dS} | {dL} | {pcol} | {verdict} | {out.name} |"
    with LEDGER.open("a") as f:
        f.write(line + "\n")
    print(line)
    print(json.dumps({"id": a.id, "strict": o["strict"], "lenient": o["lenient"], "substring": o["substring"],
                      "idk": o["idk"], "turn@k": turn_at_k, "verdict": verdict, "file": out.name}))


if __name__ == "__main__":
    main()
