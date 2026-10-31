#!/bin/zsh
# Round 5: retrieval variants under the new defaults, routed reader, vs routed. LME base2 + seed2 (held-out) for each.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/round5.log
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py; B=./target/release/chat-memory-context
ROUTED=(--prompt concise --cascade auto_v1n --route-compute auto_v1n --route-compute-think --extra-judges --official-judge --threads 2)
typeset -A SEED; SEED=(lme_base2 1 lme_seed2 2)
for s in lme_base2 lme_seed2; do
  for v in "k20:context_k=20" "qpm2:context_quotes=2" "mqtp:context_multi_query=1,temporal_prior=0.5"; do
    name=${v%%:*}; exp=${v#*:}
    NEURALGRAPH_EXP=$exp $B lme --db-dir .context/overnight/db/$s --n 50 --seed ${SEED[$s]} --out $R/${s}_r5_${name}_ctx.json >> $LOG 2>&1
    python3 $A --contexts $R/${s}_r5_${name}_ctx.json --out $R/${s}_r5_${name}_routed_acc.json $ROUTED --label "$s $name" >> $LOG 2>&1
    echo "[r5] $s $name + routed vs routed" >> $LOG
    python3 $C $R/${s}_routed_acc.json $R/${s}_r5_${name}_routed_acc.json --md --metric strict,strict_v2,lenient_nonidk,official >> $LOG 2>&1
  done
done
echo "[r5] $(date '+%F %T') done" >> $LOG; touch $LOG.done
