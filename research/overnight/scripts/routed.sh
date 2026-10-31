#!/bin/zsh
# Routed reader: new context (6000 + raw3 user turns) + concise + IDK cascade, THINKING only on count/duration/date questions.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/routed.log
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py
until [ -f .context/overnight/logs/locomo_c1_ablate.log.done ]; do sleep 15; done
# contexts: LME base2/base have q1raw3_6k; locomo c0 has q1raw3_6k; locomo c1 has default (new defaults = same thing)
run() { # name ctx base extra-metrics
  python3 $A --contexts $R/$2 --out $R/$1_routed_acc.json --prompt concise --cascade auto_v1n --route-compute auto_v1n --route-compute-think --extra-judges $4 --threads 2 --label "$1 routed" >> $LOG 2>&1
  echo "[routed] $1: routed vs old shipped" >> $LOG
  python3 $C $R/$3 $R/$1_routed_acc.json --md --metric strict,strict_v2,lenient_nonidk$5 >> $LOG 2>&1
}
run lme_base2 lme_base2_q1raw3_6k_ctx.json lme_base2_crateq1_concise_cascauto_v1n_official_acc.json --official-judge ,official
run lme_base lme_base_q1raw3_6k_ctx.json lme_base_crateq1_concise_cascauto_v1n_off_acc.json --official-judge ,official
run locomo_c0 locomo_c0_q1raw3_6k_ctx.json locomo_c0_crateq1_concise_cascauto_v1n_acc.json "" ""
run locomo_c1 locomo_c1_default_ctx.json locomo_c1_crateq1_concise_cascauto_v1n_acc.json "" ""
echo "[routed] $(date '+%F %T') done" >> $LOG; touch $LOG.done
