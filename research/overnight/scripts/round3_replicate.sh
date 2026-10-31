#!/bin/zsh
# Replicate the round-3 winner (quotes + 3 raw user turns, 6000 chars, 9B thinking compute reader) on the first LME ingest and on LoCoMo.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/round3_replicate.log
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py
EXP=context_quotes=1,context_raw_messages=3,context_max_chars=6000
NEURALGRAPH_EXP=$EXP ./target/release/chat-memory-context lme --db-dir .context/overnight/db/lme_base --n 50 --seed 1 --out $R/lme_base_q1raw3_6k_ctx.json >> $LOG 2>&1
NEURALGRAPH_EXP=$EXP ./target/release/chat-memory-context locomo --db .context/overnight/db/locomo_c0.db --conv 0 --sessions 8 --out $R/locomo_c0_q1raw3_6k_ctx.json >> $LOG 2>&1
python3 $A --contexts $R/lme_base_q1raw3_6k_ctx.json --out $R/lme_base_q1raw3_6k_auto_v1n_think_acc.json --prompt auto_v1n --think --max-tokens 2500 --extra-judges --official-judge --threads 2 --label "lme_base winner" >> $LOG 2>&1
python3 $A --contexts $R/locomo_c0_q1raw3_6k_ctx.json --out $R/locomo_c0_q1raw3_6k_auto_v1n_think_acc.json --prompt auto_v1n --think --max-tokens 2500 --extra-judges --threads 2 --label "locomo winner" >> $LOG 2>&1
# references: shipped on each set, with extra judges (+ official for LME)
python3 $A --contexts $R/lme_base_crateq1_ctx.json --regrade-from $R/lme_base_crateq1_concise_cascauto_v1n_acc.json --out $R/lme_base_crateq1_concise_cascauto_v1n_off_acc.json --extra-judges --official-judge --threads 4 >> $LOG 2>&1
echo "[rep] LME first ingest: winner vs shipped" >> $LOG
python3 $C $R/lme_base_crateq1_concise_cascauto_v1n_off_acc.json $R/lme_base_q1raw3_6k_auto_v1n_think_acc.json --md --metric strict,strict_v2,lenient_nonidk,official >> $LOG 2>&1
echo "[rep] LoCoMo: winner vs shipped" >> $LOG
python3 $C $R/locomo_c0_crateq1_concise_cascauto_v1n_acc.json $R/locomo_c0_q1raw3_6k_auto_v1n_think_acc.json --md --metric strict,strict_v2,lenient_nonidk >> $LOG 2>&1
echo "[rep] $(date '+%F %T') done" >> $LOG; touch $LOG.done
