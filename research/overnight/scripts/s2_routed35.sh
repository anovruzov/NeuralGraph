#!/bin/zsh
# 35B second-judge-model regrade of the FINAL routed protocol vs the old shipped protocol (LME both ingests + LoCoMo c0).
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/s2_routed35.log
lms unload google/gemma-4-e4b >> $LOG 2>&1; lms load qwen/qwen3.6-35b-a3b -y --context-length 8192 --parallel 4 >> $LOG 2>&1
rg() { python3 research/overnight/scripts/answer_eval.py --contexts $R/$2 --regrade-from $R/$1_acc.json --out $R/$1_j35_acc.json --judge-model qwen/qwen3.6-35b-a3b --judge-prefill --threads 4 >> $LOG 2>&1; }
rg lme_base2_routed lme_base2_q1raw3_6k_ctx.json
rg lme_base_routed lme_base_q1raw3_6k_ctx.json
rg locomo_c0_routed locomo_c0_q1raw3_6k_ctx.json
rg lme_base2_crateq1_concise_cascauto_v1n lme_base2_crateq1_ctx.json
for p in "lme_base2_crateq1_concise_cascauto_v1n lme_base2_routed" "lme_base_crateq1_concise_cascauto_v1n lme_base_routed" "locomo_c0_crateq1_concise_cascauto_v1n locomo_c0_routed"; do
  set -- $p; echo "[s2r] $2 vs $1 (35B judge)" >> $LOG
  python3 research/overnight/scripts/compare.py $R/$1_j35_acc.json $R/$2_j35_acc.json --md --metric strict,lenient,lenient_nonidk >> $LOG 2>&1
done
lms unload qwen/qwen3.6-35b-a3b >> $LOG 2>&1; lms load google/gemma-4-e4b -y --context-length 8192 --parallel 4 >> $LOG 2>&1
echo "[s2r] $(date '+%F %T') done" >> $LOG; touch $LOG.done
