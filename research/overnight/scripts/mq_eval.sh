#!/bin/zsh
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/mq_eval.log
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py
for s in lme_base2 lme_base locomo_c0 locomo_c1 locomo_c2; do
  extra=""; met=""; [[ $s == lme_* ]] && extra="--official-judge" && met=",official"
  python3 $A --contexts $R/${s}_mq_ctx.json --out $R/${s}_mq_routed_acc.json --prompt concise --cascade auto_v1n --route-compute auto_v1n --route-compute-think --extra-judges $extra --threads 2 --label "$s mq routed" >> $LOG 2>&1
  echo "[mq] $s: multi-query + routed vs routed" >> $LOG
  python3 $C $R/${s}_routed_acc.json $R/${s}_mq_routed_acc.json --md --metric strict,strict_v2,lenient_nonidk$met >> $LOG 2>&1
done
echo "[mq] $(date '+%F %T') done" >> $LOG; touch $LOG.done
