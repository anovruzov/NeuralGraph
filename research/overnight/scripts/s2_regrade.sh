#!/bin/zsh
# After the cascade-only rerun: rerun the winners with the neutral cascade prompt (auto_v1n), then S2 = second judge MODEL
# (Qwen3.6-35B-A3B, same judge prompts, think-prefill) on baseline + clean winners, both sets.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
LOG=.context/overnight/logs/s2_regrade.log
until [ -f .context/overnight/logs/sweep_lme50_r1.log.done ]; do sleep 15; done
research/overnight/scripts/sweep.sh clean .context/overnight/sweep_clean.txt >> $LOG 2>&1
until [ -f .context/overnight/logs/sweep_clean.log.done ]; do sleep 15; done
echo "[s2] $(date '+%F %T') clean reruns done; loading 35B judge" >> $LOG
lms ps | grep -q "qwen3.6-35b" || lms load qwen/qwen3.6-35b-a3b -y --context-length 8192 --parallel 4 >> $LOG 2>&1
for stem in lme_base_vk15_concise lme_base_vk15_concise_quotes_user_cascauto_v1n lme_base_vk15_concise_quotes_user_cascauto_v1n_qpm2 locomo_c0_vk15_concise locomo_c0_vk15_concise_quotes_user_cascauto_v1n_qpm2; do
  ctx=.context/overnight/results/${stem%%_concise*}_ctx.json
  python3 research/overnight/scripts/answer_eval.py --contexts $ctx --regrade-from .context/overnight/results/${stem}_acc.json \
    --out .context/overnight/results/${stem}_j35_acc.json --judge-model qwen/qwen3.6-35b-a3b --judge-prefill --threads 4 --label "$stem j35" >> $LOG 2>&1
  echo "[s2] $(date '+%F %T') $stem rc=$?" >> $LOG
done
echo "[s2] $(date '+%F %T') done" >> $LOG; touch $LOG.done
