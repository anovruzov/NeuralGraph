#!/bin/zsh
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/s2_e56.log
lms unload google/gemma-4-e4b >> $LOG 2>&1; lms load qwen/qwen3.6-35b-a3b -y --context-length 8192 --parallel 4 >> $LOG 2>&1
for stem in lme_E56_plain_concise lme_E56_crateq1_concise_cascauto_v1n; do
  ctx=$R/${stem%%_concise*}_ctx.json
  python3 research/overnight/scripts/answer_eval.py --contexts $ctx --regrade-from $R/${stem}_acc.json --out $R/${stem}_j35_acc.json --judge-model qwen/qwen3.6-35b-a3b --judge-prefill --threads 4 >> $LOG 2>&1
done
echo "[s2] plain E56 vs plain base (35B judge)" >> $LOG
python3 research/overnight/scripts/compare.py $R/lme_base_vk15_concise_j35_acc.json $R/lme_E56_plain_concise_j35_acc.json --md --metric strict,lenient,lenient_nonidk >> $LOG 2>&1
echo "[s2] shipped E56 vs shipped base (35B judge)" >> $LOG
python3 research/overnight/scripts/compare.py $R/lme_base_crateq1_concise_cascauto_v1n_j35_acc.json $R/lme_E56_crateq1_concise_cascauto_v1n_j35_acc.json --md --metric strict,lenient,lenient_nonidk >> $LOG 2>&1
lms unload qwen/qwen3.6-35b-a3b >> $LOG 2>&1; lms load google/gemma-4-e4b -y --context-length 8192 --parallel 4 >> $LOG 2>&1
echo "[s2] $(date '+%F %T') done" >> $LOG; touch $LOG.done
