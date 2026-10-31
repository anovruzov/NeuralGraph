#!/bin/zsh
# Reader-ceiling probes on the SHIPPED context (crate quotes=1) of the clean ingest lme_base2: bigger reader, thinking readers.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/reader_ceiling.log; CTX=$R/lme_base2_crateq1_ctx.json
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py
python3 $A --contexts $CTX --out $R/lme_base2_crateq1_concise_acc.json --prompt concise --extra-judges --threads 4 --label "9B concise on shipped ctx" >> $LOG 2>&1
python3 $A --contexts $CTX --out $R/lme_base2_crateq1_concise_27b_acc.json --prompt concise --model qwen/qwen3.8-27b --extra-judges --threads 4 --label "27B concise" >> $LOG 2>&1
python3 $A --contexts $CTX --out $R/lme_base2_crateq1_auto_v1n_think_acc.json --prompt auto_v1n --think --max-tokens 2500 --extra-judges --threads 2 --label "9B think" >> $LOG 2>&1
python3 $A --contexts $CTX --out $R/lme_base2_crateq1_auto_v1n_27b_think_acc.json --prompt auto_v1n --model qwen/qwen3.8-27b --think --max-tokens 2500 --extra-judges --threads 2 --label "27B think" >> $LOG 2>&1
for v in concise_27b auto_v1n_think auto_v1n_27b_think; do
  echo "[ceiling] $v vs 9B concise (same shipped context)" >> $LOG
  python3 $C $R/lme_base2_crateq1_concise_acc.json $R/lme_base2_crateq1_${v}_acc.json --md --metric strict,lenient,strict_v2,lenient_nonidk >> $LOG 2>&1
done
echo "[ceiling] $(date '+%F %T') done" >> $LOG; touch $LOG.done
