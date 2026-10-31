#!/bin/zsh
# Round 5b: LLM router (alone, and OR-ed with the regex) vs regex routing. LME base2 + seed2 + LoCoMo c0 (c0 has 4 regex-routed; LLM may route more).
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/round5b.log
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py
until [ -f .context/overnight/logs/locomo_c3_chain.done ]; do sleep 30; done
typeset -A CTX; CTX=(lme_base2 lme_base2_q1raw3_6k_ctx.json lme_seed2 lme_seed2_default_ctx.json locomo_c0 locomo_c0_q1raw3_6k_ctx.json)
for s in lme_base2 lme_seed2 locomo_c0; do
  off=""; met="strict,strict_v2,lenient_nonidk"; [[ $s == lme_* ]] && off="--official-judge" && met="$met,official"
  for v in "rllm:--route-llm" "rboth:--route-llm --route-regex wide"; do
    name=${v%%:*}; flags=${v#*:}
    python3 $A --contexts $R/${CTX[$s]} --out $R/${s}_r5_${name}_acc.json --prompt concise --cascade auto_v1n --route-compute auto_v1n --route-compute-think ${=flags} --extra-judges $off --threads 2 --label "$s $name" >> $LOG 2>&1
    echo "[r5b] $s $name vs regex-routed" >> $LOG
    python3 $C $R/${s}_routed_acc.json $R/${s}_r5_${name}_acc.json --md --metric $met >> $LOG 2>&1
    python3 - $R/${s}_r5_${name}_acc.json >> $LOG <<'PY'
import json,sys
j=json.load(open(sys.argv[1])); rows=j['rows']; o=j['summary']['overall']
print(f"   routed {sum(1 for r in rows if r.get('routed')=='compute')} rows; mean_s {o['mean_answer_s']:.2f}")
PY
  done
done
echo "[r5b] $(date '+%F %T') done" >> $LOG; touch $LOG.done
