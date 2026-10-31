#!/bin/zsh
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/locomo_c1_ablate.log
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py; BASE=$R/locomo_c1_crateq1_concise_cascauto_v1n_acc.json
python3 $A --contexts $R/locomo_c1_default_ctx.json --out $R/locomo_c1_default_concise_cascauto_v1n_acc.json --prompt concise --cascade auto_v1n --extra-judges --threads 4 --label "c1 new ctx, old reader" >> $LOG 2>&1
python3 $A --contexts $R/locomo_c1_crateq1_ctx.json --out $R/locomo_c1_crateq1_auto_v1n_think_acc.json --prompt auto_v1n --think --max-tokens 2500 --extra-judges --threads 2 --label "c1 old ctx, thinking reader" >> $LOG 2>&1
echo "[c1a] new ctx (6000+raw3) + OLD reader vs old shipped" >> $LOG
python3 $C $BASE $R/locomo_c1_default_concise_cascauto_v1n_acc.json --md --metric strict,strict_v2,lenient_nonidk >> $LOG 2>&1
echo "[c1a] old ctx + THINKING reader vs old shipped" >> $LOG
python3 $C $BASE $R/locomo_c1_crateq1_auto_v1n_think_acc.json --md --metric strict,strict_v2,lenient_nonidk >> $LOG 2>&1
echo "[c1a] $(date '+%F %T') done" >> $LOG; touch $LOG.done
