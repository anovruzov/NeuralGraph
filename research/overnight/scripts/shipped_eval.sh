#!/bin/zsh
# Measure the SHIPPED behaviour: contexts rendered by the crate itself (NEURALGRAPH_EXP) under the default 3600-char budget,
# then the concise prompt + IDK cascade (auto_v1n), both judge pairs. Paired against the LME-50 / LoCoMo baselines.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/shipped_eval.log
for q in 1 2; do
  NEURALGRAPH_EXP=context_quotes=$q ./target/release/chat-memory-context lme --db-dir .context/overnight/db/lme_base --n 50 --seed 1 --out $R/lme_base_crateq${q}_ctx.json >> $LOG 2>&1
  NEURALGRAPH_EXP=context_quotes=$q ./target/release/chat-memory-context locomo --db .context/overnight/db/locomo_c0.db --conv 0 --sessions 8 --out $R/locomo_c0_crateq${q}_ctx.json >> $LOG 2>&1
  for set in lme_base locomo_c0; do
    python3 research/overnight/scripts/answer_eval.py --contexts $R/${set}_crateq${q}_ctx.json --out $R/${set}_crateq${q}_concise_cascauto_v1n_acc.json \
      --prompt concise --cascade auto_v1n --extra-judges --threads 4 --label "shipped q$q $set" >> $LOG 2>&1
    python3 research/overnight/scripts/compare.py $R/${set}_vk15_concise_acc.json $R/${set}_crateq${q}_concise_cascauto_v1n_acc.json --md --metric strict,lenient,strict_v2,lenient_v2,lenient_nonidk >> $LOG 2>&1
  done
done
echo "[shipped] $(date '+%F %T') done" >> $LOG; touch $LOG.done
