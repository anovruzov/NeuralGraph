#!/bin/zsh
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/seed2_rm35.log
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py
lms unload google/gemma-4-e4b >> $LOG 2>&1; lms load qwen/qwen3.6-35b-a3b -y --context-length 8192 --parallel 4 >> $LOG 2>&1
python3 $A --contexts $R/lme_seed2_default_ctx.json --out $R/lme_seed2_rm35_acc.json --prompt concise --cascade auto_v1n --route-compute auto_v1n --route-compute-think --route-model qwen/qwen3.6-35b-a3b --judge-model qwen3.8-9b-mlx --threads 2 --label "seed2 rm35" >> $LOG 2>&1
lms unload qwen/qwen3.6-35b-a3b >> $LOG 2>&1; lms load google/gemma-4-e4b -y --context-length 8192 --parallel 4 >> $LOG 2>&1
python3 $A --contexts $R/lme_seed2_default_ctx.json --regrade-from $R/lme_seed2_rm35_acc.json --out $R/lme_seed2_rm35_g_acc.json --extra-judges --official-judge --threads 4 >> $LOG 2>&1
echo "[seed2] ACCURACY MODE (35B thinker) vs ROUTED" >> $LOG
python3 $C $R/lme_seed2_routed_acc.json $R/lme_seed2_rm35_g_acc.json --md --metric strict,strict_v2,lenient_nonidk,official >> $LOG 2>&1
echo "[seed2] ACCURACY MODE vs ORIGINAL" >> $LOG
python3 $C $R/lme_seed2_plain_concise_acc.json $R/lme_seed2_rm35_g_acc.json --md --metric strict,strict_v2,lenient_nonidk,official >> $LOG 2>&1
echo "[seed2] $(date '+%F %T') done" >> $LOG; touch $LOG.done
