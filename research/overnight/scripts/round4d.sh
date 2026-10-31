#!/bin/zsh
# Round 4d (wild, latency): Gemma-4-e4b as the CONCISE reader (non-routed questions) with the 9B thinking on routed ones;
# judged ONLY by the 35B (Gemma cannot judge itself). Compared with the 9B routed protocol under the same 35B judge.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/round4d.log
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py
until [ -f .context/overnight/logs/round4c.log.done ]; do sleep 20; done
# answer phase: Gemma concise primary (no prefill; reasoning_effort none is honoured by Gemma), 9B stays the routed/cascade model? answer_eval
# uses ONE --model for all calls, so this variant is Gemma for everything incl. the routed thinking call (Gemma thinks via reasoning_effort).
for s in lme_base2 locomo_c0; do
  ctx=$R/lme_base2_q1raw3_6k_ctx.json; [[ $s == locomo_c0 ]] && ctx=$R/locomo_c0_q1raw3_6k_ctx.json
  python3 $A --contexts $ctx --out $R/${s}_r4_gemma_acc.json --prompt concise --cascade auto_v1n --route-compute auto_v1n --route-compute-think --think-early-stop --model google/gemma-4-e4b --no-prefill --judge-model google/gemma-4-e4b --threads 2 --label "$s gemma reader" >> $LOG 2>&1
done
lms unload google/gemma-4-e4b >> $LOG 2>&1; lms load qwen/qwen3.6-35b-a3b -y --context-length 8192 --parallel 4 >> $LOG 2>&1
for s in lme_base2 locomo_c0; do
  ctx=$R/lme_base2_q1raw3_6k_ctx.json; [[ $s == locomo_c0 ]] && ctx=$R/locomo_c0_q1raw3_6k_ctx.json
  python3 $A --contexts $ctx --regrade-from $R/${s}_r4_gemma_acc.json --out $R/${s}_r4_gemma_j35_acc.json --judge-model qwen/qwen3.6-35b-a3b --judge-prefill --threads 4 >> $LOG 2>&1
  echo "[r4d] $s Gemma reader vs 9B routed (35B judge)" >> $LOG
  python3 $C $R/${s}_routed_j35_acc.json $R/${s}_r4_gemma_j35_acc.json --md --metric strict,lenient,lenient_nonidk >> $LOG 2>&1
  python3 - $R/${s}_routed_acc.json $R/${s}_r4_gemma_acc.json >> $LOG <<'PY'
import json,sys
for f in sys.argv[1:]:
    j=json.load(open(f)); o=j['summary']['overall']; rows=j['rows']; secs=sorted(r['answer_seconds'] for r in rows)
    print(f"   {f.split('/')[-1][:44]:<44} mean_s {o['mean_answer_s']:.2f} p50 {secs[len(secs)//2]:.2f} p95 {secs[int(len(secs)*0.95)]:.2f}")
PY
done
lms unload qwen/qwen3.6-35b-a3b >> $LOG 2>&1; lms load google/gemma-4-e4b -y --context-length 8192 --parallel 4 >> $LOG 2>&1
echo "[r4d] $(date '+%F %T') done" >> $LOG; touch $LOG.done
