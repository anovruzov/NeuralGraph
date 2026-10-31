#!/bin/zsh
# Round 2 reader variants on the shipped context of the clean ingest (lme_base2, crate quotes=1), vs shipped (concise + cascade).
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/round2_reader.log; CTX=$R/lme_base2_crateq1_ctx.json
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py; BASE=$R/lme_base2_crateq1_concise_cascauto_v1n_acc.json
until [ -f .context/overnight/logs/reader_ceiling.log.done ]; do sleep 20; done
python3 $A --contexts $CTX --out $R/lme_base2_crateq1_concise_casc_think_acc.json --prompt concise --cascade auto_v1n --cascade-think --extra-judges --threads 2 --label "cascade-think" >> $LOG 2>&1
python3 $A --contexts $CTX --out $R/lme_base2_crateq1_concise_vote3_casc_acc.json --prompt concise --vote 3 --cascade auto_v1n --extra-judges --threads 4 --label "vote3+cascade" >> $LOG 2>&1
for v in concise_casc_think concise_vote3_casc; do
  echo "[r2] $v vs shipped (concise+cascade), same context" >> $LOG
  python3 $C $BASE $R/lme_base2_crateq1_${v}_acc.json --md --metric strict,lenient,strict_v2,lenient_nonidk >> $LOG 2>&1
done
echo "[r2] $(date '+%F %T') done" >> $LOG; touch $LOG.done
