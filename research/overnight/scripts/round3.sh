#!/bin/zsh
# Round 3: thinking 9B reader on the richer contexts (6k budget, episodic user turns), official judge column on everything.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/round3.log
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py
for v in q1_6k q1raw3_6k; do
  python3 $A --contexts $R/lme_base2_${v}_ctx.json --out $R/lme_base2_${v}_auto_v1n_think_acc.json --prompt auto_v1n --think --max-tokens 2500 --extra-judges --official-judge --threads 2 --label "$v think" >> $LOG 2>&1
done
python3 $A --contexts $R/lme_base2_q1raw3_6k_ctx.json --out $R/lme_base2_q1raw3_6k_concise_casc_think_acc.json --prompt concise --cascade auto_v1n --cascade-think --extra-judges --official-judge --threads 2 --label "raw3 cascade-think" >> $LOG 2>&1
# official-judge columns for the earlier reference files (regrade only)
python3 $A --contexts $R/lme_base2_crateq1_ctx.json --regrade-from $R/lme_base2_crateq1_auto_v1n_think_acc.json --out $R/lme_base2_crateq1_auto_v1n_think_off_acc.json --extra-judges --official-judge --threads 4 >> $LOG 2>&1
python3 $A --contexts $R/lme_base2_q1raw3_6k_ctx.json --regrade-from $R/lme_base2_q1raw3_6k_concise_cascauto_v1n_acc.json --out $R/lme_base2_q1raw3_6k_concise_cascauto_v1n_off_acc.json --extra-judges --official-judge --threads 4 >> $LOG 2>&1
SHIP=$R/lme_base2_crateq1_concise_cascauto_v1n_official_acc.json
for v in crateq1_auto_v1n_think_off q1_6k_auto_v1n_think q1raw3_6k_auto_v1n_think q1raw3_6k_concise_casc_think q1raw3_6k_concise_cascauto_v1n_off; do
  echo "[r3] $v vs SHIPPED (crateq1 3600 + concise + cascade)" >> $LOG
  python3 $C $SHIP $R/lme_base2_${v}_acc.json --md --metric strict,strict_v2,lenient_nonidk,official >> $LOG 2>&1
done
echo "[r3] $(date '+%F %T') done" >> $LOG; touch $LOG.done
