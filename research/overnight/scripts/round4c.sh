#!/bin/zsh
# Round 4c: temporal prior 0.5 contexts + routed protocol vs routed (LME both ingests; cheap, judge-cached for unchanged rows).
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/round4c.log
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py
until [ -f .context/overnight/logs/round4b.log.done ]; do sleep 20; done
for s in lme_base2 lme_base; do
  python3 $A --contexts $R/${s}_tp05_ctx.json --out $R/${s}_r4_tp05_routed_acc.json --prompt concise --cascade auto_v1n --route-compute auto_v1n --route-compute-think --think-early-stop --extra-judges --official-judge --threads 2 --label "$s tp05" >> $LOG 2>&1
  echo "[r4c] $s temporal_prior=0.5 + routed vs routed" >> $LOG
  python3 $C $R/${s}_routed_acc.json $R/${s}_r4_tp05_routed_acc.json --md --metric strict,strict_v2,lenient_nonidk,official >> $LOG 2>&1
done
echo "[r4c] $(date '+%F %T') done" >> $LOG; touch $LOG.done
