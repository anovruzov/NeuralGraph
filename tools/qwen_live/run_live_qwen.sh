#!/usr/bin/env bash
# Live Qwen as the site verifiers' judge in the real EMERGENCE `run` job (process mode, real nats-server).
# Run on the machine that serves the Qwen model. Nothing here downloads a model or calls a paid/hosted API:
# only loopback endpoints are probed, and the routing file only admits a local base_url.
#
#   tools/qwen_live/run_live_qwen.sh [seed] [model-substring]
#
# Steps: (1) discover local OpenAI-compatible servers and the Qwen models they serve; (2) prove one real inference
# and save its request/response receipt; (3) write one routing file per site, each bound to exactly site:<id>;
# (4) run the job; every judge call is in edge/site-<id>.ledger.jsonl (model, tokens, latency) and
# edge/site-<id>.receipts.jsonl (payload and validated reply), both inside the site's own directory.
# Seed 0 runs the unmodified CLI. Any other seed needs the world-count guard bypassed: that is a DIAGNOSTIC run,
# labelled so in its run id and directory, never an official baseline.
set -euo pipefail
SEED="${1:-0}"; WANT="${2:-qwen}"
OUT="runs/live-qwen/seed${SEED}-$(date -u +%Y%m%dT%H%M%SZ)"; mkdir -p "$OUT"

# ---- 1. discover (Ollama 11434, llama.cpp 8080, vLLM 8000, LM Studio 1234)
BASE="${QWEN_BASE_URL:-}"; MODEL="${QWEN_MODEL:-}"     # set both to skip discovery (a non-standard port)
PORTS="11434 8080 8000 1234"; [ -n "$BASE" ] && PORTS=""
for port in $PORTS; do
  url="http://127.0.0.1:${port}/v1"
  models=$(curl -s -m 3 "${url}/models" || true)
  [ -z "$models" ] && continue
  echo "$models" > "$OUT/models-${port}.json"
  pick=$(python3 -c '
import json, sys
want = sys.argv[1].lower()
ids = [m.get("id", "") for m in json.load(open(sys.argv[2])).get("data", [])]
print("port", sys.argv[3], ids, file=sys.stderr)
hits = [i for i in ids if want in i.lower()]
print(hits[0] if hits else "")' "$WANT" "$OUT/models-${port}.json" "$port" || true)
  if [ -n "$pick" ] && [ -z "$MODEL" ]; then BASE="$url"; MODEL="$pick"; fi
done
[ -n "$PORTS" ] && command -v ollama >/dev/null && ollama list | tee "$OUT/ollama-list.txt" || true
[ -z "$MODEL" ] && { echo "no local model matching '$WANT' is being served; start one (e.g. 'ollama serve') and retry"; exit 2; }
echo "using $MODEL at $BASE"

# ---- 2. prove a real inference (thinking off: the judge's token budget is small); the receipt keeps request+response
python3 - "$BASE" "$MODEL" "$OUT/probe.json" <<'PY'
import json, sys, time, urllib.request
base, model, out = sys.argv[1:]
body = {"model": model, "messages": [{"role": "user", "content": "Reply with exactly: OK"}], "temperature": 0,
        "max_tokens": 16, "reasoning_effort": "none"}
t = time.perf_counter()
req = urllib.request.Request(base + "/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"})
resp = json.load(urllib.request.urlopen(req, timeout=300))
rec = {"request": body, "response": resp, "model_requested": model, "model_served": resp.get("model"), "latency_s": round(time.perf_counter() - t, 3),
       "usage": resp.get("usage"), "reply": resp["choices"][0]["message"]["content"],
       "system_fingerprint": resp.get("system_fingerprint")}
json.dump(rec, open(out, "w"), indent=1); print("probe:", rec)
PY

# ---- 3. routing: one file per site, each bound to exactly site:<id> (no wildcard). The simulated sites share this
# host's server, which the report must disclose; the boundary check itself is per site.
mkdir -p "$OUT/routing"
python3 - "$BASE" "$MODEL" "$OUT/routing" "$SEED" <<'PY'
import json, sys
from research.e2e_ingest import emergence_world as ew
base, model, out, seed = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
org, _, _ = ew.generate(200, seed)
for sid in ew.site_ids(org):
    json.dump({"schema_version": 1,
               "endpoints": {"qwen-local": {"provider": "openai_compat", "boundary": f"site:{sid}", "base_url": base,
                                            "model": model, "reasoning_effort": "none"}},
               "routes": {"judge_record": {"endpoint": "qwen-local"}}}, open(f"{out}/{sid}.routing.json", "w"), indent=1)
    print("routing", sid)
PY
shasum -a 256 "$OUT"/routing/*.json | tee "$OUT/routing.sha256"

# ---- 4. the real job (process mode); seeds other than 0 need the world-count guard bypass (disclosed)
git rev-parse HEAD > "$OUT/commit.txt"; git status --porcelain > "$OUT/dirty.txt"
export MYCELIC_NATS_SERVER_BIN="${MYCELIC_NATS_SERVER_BIN:-$(command -v nats-server)}"
start=$(date +%s)
if [ "$SEED" = "0" ]; then CLI=(python3 -m research.e2e_ingest); RID="live-qwen-s0";
else CLI=(python3 tools/qwen_live/run_seed.py); RID="DIAGNOSTIC-guard-bypass-live-qwen-s${SEED}"; fi
"${CLI[@]}" run --size 200 --seed "$SEED" --mode process --skip-drills \
  --routing "$OUT/routing" --out "$OUT/run" --run-id "$RID" 2>&1 | tee "$OUT/run.log" || true
echo "wall_seconds $(( $(date +%s) - start ))" | tee -a "$OUT/run.log"

# ---- 5. what the model actually did
python3 - "$OUT" <<'PY'
import glob, json, sys, statistics as st
out = sys.argv[1]
rows = [json.loads(l) for f in glob.glob(f"{out}/run/**/site-*.ledger.jsonl", recursive=True) for l in open(f)]
receipts = [json.loads(l) for f in glob.glob(f"{out}/run/**/site-*.receipts.jsonl", recursive=True) for l in open(f)]
lat = sorted(r.get("latency_ms") or 0 for r in rows)
ok = sum(1 for r in rows if r.get("ok"))
summary = {"receipts": len(receipts), "receipt_errors": sum(1 for r in receipts if r["error"]),
           "judge_calls": len(rows), "ok": ok, "fake_marker_rows": sum(1 for r in rows if r.get("fake_marker")),
           "tokens_in": sum(r.get("tokens_in") or 0 for r in rows),
           "tokens_out": sum(r.get("tokens_out") or 0 for r in rows),
           "latency_ms_p50": lat[len(lat) // 2] if lat else None,
           "latency_ms_p95": lat[int(len(lat) * 0.95)] if lat else None,
           "models_served": sorted({str(r.get("model_served")) for r in rows}),
           "boundary_modes": sorted({str(r.get("boundary_mode")) for r in rows})}
rep = glob.glob(f"{out}/run/e2e-ingest-report.json")
if rep:
    d = json.load(open(rep[0]))["discovery"]
    summary.update({k: d[k] for k in ("found", "gold_discoverable", "recall_all", "recall_rare",
                                      "decoy_accepted_by_type")})
json.dump(summary, open(f"{out}/summary.json", "w"), indent=1); print(json.dumps(summary, indent=1))
PY
