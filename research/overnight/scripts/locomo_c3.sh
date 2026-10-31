#!/bin/zsh
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/locomo_c3.log
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py
./target/release/chat-memory-locomo ingest --db .context/overnight/db/locomo_c3.db --conv 3 --sessions 8 --batch-size 2 --concurrency 1 --parallel 1 >> $LOG 2>&1
NEURALGRAPH_EXP=context_quotes=1,context_raw_messages=0,context_max_chars=3600 ./target/release/chat-memory-context locomo --db .context/overnight/db/locomo_c3.db --conv 3 --sessions 8 --out $R/locomo_c3_crateq1_ctx.json >> $LOG 2>&1
./target/release/chat-memory-context locomo --db .context/overnight/db/locomo_c3.db --conv 3 --sessions 8 --out $R/locomo_c3_default_ctx.json >> $LOG 2>&1
python3 $A --contexts $R/locomo_c3_crateq1_ctx.json --out $R/locomo_c3_crateq1_concise_cascauto_v1n_acc.json --prompt concise --cascade auto_v1n --extra-judges --threads 4 --label "c1 old shipped" >> $LOG 2>&1
python3 $A --contexts $R/locomo_c3_default_ctx.json --out $R/locomo_c3_default_auto_v1n_think_acc.json --prompt auto_v1n --think --max-tokens 2500 --extra-judges --threads 2 --label "c1 new" >> $LOG 2>&1
echo "[c3] ROUTED vs old shipped (quotes 3600 + cascade), LoCoMo conv 1" >> $LOG
python3 $C $R/locomo_c3_crateq1_concise_cascauto_v1n_acc.json $R/locomo_c3_routed_acc.json --md --metric strict,strict_v2,lenient_nonidk >> $LOG 2>&1
echo "[c3] $(date '+%F %T') done" >> $LOG; touch $LOG.done
