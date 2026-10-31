"""Operation 100M recursive research loop: Planner -> Retriever -> Critic, at most four rounds per task.

    python3 -I operation-100m/agents/loop.py --task 100k-real [--max-rounds 4]

PASS publishes the Critic-verified metrics to state/verified/<task>.json. FAIL hands critique.json back to the
Planner, which revises the plan (another engine variant, a rebuild, or more queries) or stops with the failure left
UNRESOLVED. State persists in operation-100m/state/ between rounds and between invocations; plan.json,
evidence.json and critique.json at operation-100m/ always hold the latest round.

The agents are deterministic programs, not LLM calls: every decision is a rule over artifacts, so a run can be
audited and replayed. Code changes the Planner asks for (a variant that does not exist yet) stop the loop with
status NEEDS_IMPLEMENTATION; an engineer implements the variant and re-runs the loop.
"""
import argparse
import datetime
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)             # operation-100m/
REPO = os.path.dirname(ROOT)
STATE = os.path.join(ROOT, "state")
BIN = os.path.join(REPO, "target", "release", "o100m")
sys.path.insert(0, HERE)
from critic import Critic  # noqa: E402


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(path, default=None):
    return json.load(open(path)) if os.path.exists(path) else default


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(obj, f, indent=1, default=str)
    os.replace(path + ".tmp", path)


TASKS = load(os.path.join(ROOT, "bench", "configs", "tasks.json"))
VARIANTS = load(os.path.join(ROOT, "bench", "configs", "variants.json"))
GATES = load(os.path.join(ROOT, "bench", "configs", "gates_stage.json"))


# =============================================================================================== AGENT 01 PLANNER
class Planner:
    """Chooses the next measurable hypothesis from the task, prior rounds and the last critique."""

    DATA_FAILURES = {"DATASET_HASH_MISMATCH", "VECTOR_HASH_MISMATCH", "ROW_COUNT", "GENERATOR_SPEC_DIVERGENCE",
                     "GROUND_TRUTH_DIVERGENCE", "GROUND_TRUTH_UNGROUNDED"}

    def plan(self, task_name, history):
        task = TASKS[task_name]
        rnd = len(history) + 1
        tried = [h["plan"]["experiment"]["variant"] for h in history]
        last = (history[-1].get("critique_current_gates") or history[-1]["critique"]) if history else None
        exp = dict(task["experiment"])
        exp["variant"] = task.get("start_variant", "engine_default")
        actions, hypothesis = [], task["hypothesis"]
        status = "RUN"
        if last:
            codes = [f["code"] for f in last["failures"]]
            if set(codes) & self.DATA_FAILURES:
                actions.append({"action": "rebuild_bank", "because": sorted(set(codes) & self.DATA_FAILURES)})
                exp["rebuild"] = True
                exp["variant"] = history[-1]["plan"]["experiment"]["variant"]
                hypothesis = "The data failures came from a stale or divergent bank; a rebuild from the seed clears them."
            else:
                # the untried variant that addresses the most open failures, in registry order
                best, cover = None, 0
                for name, v in VARIANTS.items():
                    if name in tried or name.startswith("_"):
                        continue
                    k = len(set(v.get("addresses", [])) & set(codes))
                    if k > cover:
                        best, cover = name, k
                if best is None:
                    wanted = sorted(set(codes))
                    status = "NEEDS_IMPLEMENTATION" if any(c in task.get("implementable", []) for c in codes) else "UNRESOLVED"
                    actions.append({"action": "stop", "because": wanted,
                                    "note": "no untried variant addresses these failures"})
                    exp["variant"] = None
                else:
                    exp["variant"] = best
                    hypothesis = VARIANTS[best]["hypothesis"]
                    actions.append({"action": "try_variant", "variant": best, "addresses": VARIANTS[best]["addresses"],
                                    "open_failures": codes})
        return {"task": task_name, "round": rnd, "created_at": now(), "status": status, "hypothesis": hypothesis,
                "experiment": exp, "gates": GATES, "actions_from_critique": actions,
                "stop_conditions": ["Critic PASS", "round 4 reached", "no untried variant addresses the failures"],
                "prior_variants": tried}


# =============================================================================================== AGENT 02 RETRIEVER
class Retriever:
    """Executes the plan with the real engine, collects raw artifacts, and states candidate conclusions."""

    def __init__(self):
        self.log = []
        self.cache = load(os.path.join(STATE, "search_cache.json"), {})

    def sh(self, args, env_extra=None, stdout_path=None):
        env = dict(os.environ, **(env_extra or {}))
        t = time.time()
        with open(stdout_path, "w") if stdout_path else open(os.devnull, "w") as out:
            p = subprocess.run(args, cwd=REPO, env=env, stdout=out, stderr=subprocess.PIPE, text=True)
        self.log.append({"cmd": " ".join(shlex.quote(a) for a in args), "env": env_extra or {}, "exit": p.returncode,
                         "seconds": round(time.time() - t, 2), "stderr_tail": p.stderr[-600:]})
        if p.returncode != 0:
            raise RuntimeError(f"command failed: {args}\n{p.stderr[-2000:]}")

    # --- research tools (deduplicated, cached) -------------------------------------------------------------
    def _cached(self, key, fetch):
        if key not in self.cache:
            self.cache[key] = {"fetched_at": now(), "result": fetch()}
            save(os.path.join(STATE, "search_cache.json"), self.cache)
        return self.cache[key]["result"]

    def arxiv_search(self, query, n=5):
        def fetch():
            url = "http://export.arxiv.org/api/query?" + urllib.parse.urlencode(
                {"search_query": f"all:{query}", "max_results": n})
            xml = urllib.request.urlopen(url, timeout=30).read().decode()
            ids = [s.split("</id>")[0] for s in xml.split("<id>")[2:]]
            titles = [" ".join(s.split("</title>")[0].split()) for s in xml.split("<title>")[2:]]
            return [{"id": i, "title": t} for i, t in zip(ids, titles)]
        return self._cached(f"arxiv:{query}:{n}", fetch)

    def github_repo(self, repo):
        def fetch():
            d = json.load(urllib.request.urlopen(f"https://api.github.com/repos/{repo}", timeout=30))
            return {k: d.get(k) for k in ("full_name", "pushed_at", "default_branch", "license", "stargazers_count")}
        return self._cached(f"github:{repo}", fetch)

    # --- experiment -------------------------------------------------------------------------------------------
    def execute(self, plan):
        e = plan["experiment"]
        work = os.path.join(ROOT, "work", e["work"])
        rdir = os.path.join(ROOT, "bench", "results", plan["task"], f"round{plan['round']}-{e['variant']}")
        os.makedirs(rdir, exist_ok=True)
        db = os.path.join(work, "bank.db")
        manifest = os.path.join(rdir, "..", "manifest.json")
        base = ["--bank", "0", "--n", str(e["n"]), "--seed", str(e["seed"])]
        if e.get("rebuild") or not os.path.exists(db) or not os.path.exists(manifest):
            emb = e["emb"].replace("{work}", work)
            self.sh([BIN, "build", *base, "--db", db, "--emb", emb, "--out", manifest])
        variant = VARIANTS[e["variant"]]
        env = {k: v.replace("{work}", work) for k, v in variant.get("env", {}).items()}
        ivf = env.get("O100M_IVF_PATH", db + ".ivf")
        if variant.get("build_ivf") and (e.get("rebuild") or not os.path.exists(ivf)):
            self.sh([BIN, "build-ivf", "--db", db, *variant.get("ivf_build_args", []), "--out",
                     os.path.join(rdir, "ivf_build.json")], env_extra=env)
        qpath = os.path.join(rdir, "..", "queries.jsonl")
        self.sh([BIN, "queries", *base, "--nq", str(e["nq"]), "--out", qpath])
        self.sh([BIN, "run", *base, "--db", db, "--queries", qpath, "--qvec", e["qvec"].replace("{work}", work),
                 "--concurrency", e["concurrency"], "--out-dir", rdir], env_extra=env,
                stdout_path=os.path.join(rdir, "run_stdout.json"))
        self.sh([BIN, "profile", *base, "--db", db, "--queries", qpath, "--qvec", e["qvec"].replace("{work}", work),
                 "--limit", "300", "--out", os.path.join(rdir, "profile.json")], env_extra=env)
        summary = load(os.path.join(rdir, "run_summary.json"))
        man = load(manifest)
        claims = []
        for lv in summary["levels"]:
            for p in ("p50_ms", "p95_ms", "p99_ms"):
                claims.append({"id": f"c{lv['concurrency']}_{p}", "statement": f"{p} at concurrency {lv['concurrency']}",
                               "value": lv["latency"][p], "artifact": os.path.join(rdir, "run_summary.json"),
                               "pointer": ["levels", summary["levels"].index(lv), "latency", p]})
        claims.append({"id": "peak_rss", "statement": "peak RSS of the query process", "value": summary["rss_bytes"]["peak"],
                       "artifact": os.path.join(rdir, "run_summary.json"), "pointer": ["rss_bytes", "peak"]})
        claims.append({"id": "ingest_rate", "statement": "engine insert throughput (memories/s)",
                       "value": man["ingest_memories_per_s_engine_insert"], "artifact": manifest,
                       "pointer": ["ingest_memories_per_s_engine_insert"]})
        evidence = {"task": plan["task"], "round": plan["round"], "created_at": now(), "variant": e["variant"],
                    "variant_detail": variant, "commands": self.log, "artifacts": {
                        "db": db, "manifest": manifest, "queries": qpath, "run_dir": rdir,
                        "profile": os.path.join(rdir, "profile.json")},
                    "claims": claims, "label": "candidate (unverified until Critic PASS)",
                    "research": {"hindsight_sources": os.path.join(ROOT, "sources", "hindsight_primary_sources.md")}}
        if plan["round"] == 1:
            try:
                evidence["research"]["github"] = [self.github_repo("vectorize-io/hindsight"),
                                                  self.github_repo("anovruzov/NeuralGraph")]
                evidence["research"]["arxiv"] = {q: self.arxiv_search(q) for q in
                                                 ("RaBitQ quantization nearest neighbor", "LongMemEval long-term memory")}
            except Exception as ex:  # noqa: BLE001  research is evidence, not a gate
                evidence["research"]["error"] = repr(ex)
        exp = {"seed": e["seed"], "bank": 0, "n": e["n"], "db": db, "manifest": manifest, "queries": qpath,
               "run_dir": rdir, "evidence": os.path.join(rdir, "evidence.json")}
        save(os.path.join(rdir, "evidence.json"), evidence)
        return evidence, exp


# =============================================================================================== LOOP
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--max-rounds", type=int, default=4)
    a = ap.parse_args()
    spath = os.path.join(STATE, f"{a.task}.json")
    st = load(spath, {"task": a.task, "rounds": [], "status": "OPEN"})
    planner = Planner()
    critic_gates = dict(GATES, **TASKS[a.task].get("gates_override", {}))

    def baseline_quality():
        for r in st["rounds"]:
            if r["plan"]["experiment"]["variant"] == "engine_default":
                return r["critique"]["metrics"]["quality"]
        return None

    # gates can tighten between invocations (they are versioned in gates_stage.json); earlier rounds are re-judged
    # from their raw artifacts under the current gates, and the original critique is kept alongside
    for r in st["rounds"]:
        if r["plan"]["experiment"]["variant"] != "engine_default" and r["critique"].get("gates") != critic_gates:
            exp = dict(r["critique"]["experiment"], baseline_quality=baseline_quality())
            r["critique_current_gates"] = Critic(exp, critic_gates).run()
    while st["status"] in ("OPEN", "FAIL", "NEEDS_IMPLEMENTATION") and len(st["rounds"]) < a.max_rounds:
        plan = planner.plan(a.task, st["rounds"])
        save(os.path.join(ROOT, "plan.json"), plan)
        if plan["status"] != "RUN":
            st["status"] = plan["status"]
            st["stopped_because"] = plan["actions_from_critique"]
            break
        print(f"[round {plan['round']}] planner: variant={plan['experiment']['variant']} :: {plan['hypothesis']}")
        evidence, exp = Retriever().execute(plan)
        save(os.path.join(ROOT, "evidence.json"), evidence)
        if plan["experiment"]["variant"] != "engine_default":
            exp["baseline_quality"] = baseline_quality()
        critique = Critic(exp, critic_gates).run()
        critique["round"], critique["created_at"] = plan["round"], now()
        save(os.path.join(ROOT, "critique.json"), critique)
        save(os.path.join(exp["run_dir"], "critique.json"), critique)
        print(f"[round {plan['round']}] critic: {critique['verdict']} {[f['code'] for f in critique['failures']]}")
        st["rounds"].append({"plan": plan, "evidence_path": exp["evidence"], "critique": critique})
        st["status"] = "PASS" if critique["verdict"] == "PASS" else "FAIL"
        save(spath, st)
        if st["status"] == "PASS":
            save(os.path.join(STATE, "verified", f"{a.task}.json"),
                 {"task": a.task, "verified_at": now(), "label": "verified", "round": plan["round"],
                  "variant": plan["experiment"]["variant"], "metrics": critique["metrics"],
                  "evidence": exp["evidence"]})
    if st["status"] == "FAIL" and len(st["rounds"]) >= a.max_rounds:
        st["status"] = "UNRESOLVED_AFTER_MAX_ROUNDS"
    save(spath, st)
    print(f"task {a.task}: {st['status']}")


if __name__ == "__main__":
    main()
