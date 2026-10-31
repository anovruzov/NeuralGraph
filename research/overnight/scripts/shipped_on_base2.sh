#!/bin/zsh
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/shipped_on_base2.log
NEURALGRAPH_EXP=context_quotes=1 ./target/release/chat-memory-context lme --db-dir .context/overnight/db/lme_base2 --n 50 --seed 1 --out $R/lme_base2_crateq1_ctx.json >> $LOG 2>&1
python3 research/overnight/scripts/answer_eval.py --contexts $R/lme_base2_crateq1_ctx.json --out $R/lme_base2_crateq1_concise_cascauto_v1n_acc.json --prompt concise --cascade auto_v1n --extra-judges --threads 4 --label "shipped on base2" >> $LOG 2>&1
echo "[b2] shipped (quotes+cascade) vs plain, both on the base2 ingest" >> $LOG
python3 research/overnight/scripts/compare.py $R/lme_base2_plain_concise_acc.json $R/lme_base2_crateq1_concise_cascauto_v1n_acc.json --md --metric strict,lenient,strict_v2,lenient_nonidk >> $LOG 2>&1
echo "[b2] $(date '+%F %T') done" >> $LOG; touch $LOG.done
