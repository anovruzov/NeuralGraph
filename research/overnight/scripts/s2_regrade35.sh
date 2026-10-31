#!/bin/zsh
# S2 under a second judge MODEL (Qwen3.6-35B-A3B). Needs memory: unload Gemma first, reload it after.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/s2_regrade35.log
lms unload google/gemma-4-e4b >> $LOG 2>&1
lms load qwen/qwen3.6-35b-a3b -y --context-length 8192 --parallel 4 >> $LOG 2>&1
echo "[s2] $(date '+%F %T') 35B loaded: $(lms ps | grep -c 35b)" >> $LOG
for stem in lme_base_vk15_concise lme_base_crateq1_concise_cascauto_v1n lme_base_crateq2_concise_cascauto_v1n locomo_c0_vk15_concise locomo_c0_crateq1_concise_cascauto_v1n locomo_c0_crateq2_concise_cascauto_v1n; do
  case $stem in lme_base_vk15*) ctx=$R/lme_base_vk15_ctx.json;; lme_base_crateq1*) ctx=$R/lme_base_crateq1_ctx.json;; lme_base_crateq2*) ctx=$R/lme_base_crateq2_ctx.json;;
    locomo_c0_vk15*) ctx=$R/locomo_c0_vk15_ctx.json;; locomo_c0_crateq1*) ctx=$R/locomo_c0_crateq1_ctx.json;; *) ctx=$R/locomo_c0_crateq2_ctx.json;; esac
  python3 research/overnight/scripts/answer_eval.py --contexts $ctx --regrade-from $R/${stem}_acc.json --out $R/${stem}_j35_acc.json \
    --judge-model qwen/qwen3.6-35b-a3b --judge-prefill --threads 4 --label "$stem j35" >> $LOG 2>&1
  echo "[s2] $(date '+%F %T') $stem rc=$?" >> $LOG
done
for set in lme_base locomo_c0; do for q in 1 2; do
  python3 research/overnight/scripts/compare.py $R/${set}_vk15_concise_j35_acc.json $R/${set}_crateq${q}_concise_cascauto_v1n_j35_acc.json --md --metric strict,lenient,lenient_nonidk >> $LOG 2>&1
done; done
lms unload qwen/qwen3.6-35b-a3b >> $LOG 2>&1
lms load google/gemma-4-e4b -y --context-length 8192 --parallel 4 >> $LOG 2>&1
echo "[s2] $(date '+%F %T') done" >> $LOG; touch $LOG.done
