#!/bin/zsh
# Held-out check: a NEW stratified LME sample (seed 2) never used for tuning. Old shipped protocol vs routed protocol vs accuracy mode.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/after_seed4.log
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py
until [ -f .context/overnight/logs/lme_seed4_ingest.done ]; do sleep 30; done
python3 -c "import json; o=json.load(open('$R/lme_seed4_retrieval.json'))['summary']['overall']; print('seed4', {k:o[k] for k in ('turn@k','ceiling_turn','total_memories','total_extract_s')})" >> $LOG
# contexts: morning-shipped (quotes 1, 3600, no raw) and new defaults (quotes 1, 6000, raw 3); the original plain block for reference
NEURALGRAPH_EXP=context_quotes=0,context_raw_messages=0,context_max_chars=3600 ./target/release/chat-memory-context lme --db-dir .context/overnight/db/lme_seed4 --n 50 --seed 4 --out $R/lme_seed4_plain_ctx.json >> $LOG 2>&1
NEURALGRAPH_EXP=context_quotes=1,context_raw_messages=0,context_max_chars=3600 ./target/release/chat-memory-context lme --db-dir .context/overnight/db/lme_seed4 --n 50 --seed 4 --out $R/lme_seed4_crateq1_ctx.json >> $LOG 2>&1
./target/release/chat-memory-context lme --db-dir .context/overnight/db/lme_seed4 --n 50 --seed 4 --out $R/lme_seed4_default_ctx.json >> $LOG 2>&1
python3 $A --contexts $R/lme_seed4_plain_ctx.json --out $R/lme_seed4_plain_concise_acc.json --prompt concise --extra-judges --official-judge --threads 4 --label "seed4 original" >> $LOG 2>&1
python3 $A --contexts $R/lme_seed4_crateq1_ctx.json --out $R/lme_seed4_crateq1_concise_cascauto_v1n_acc.json --prompt concise --cascade auto_v1n --extra-judges --official-judge --threads 4 --label "seed4 morning shipped" >> $LOG 2>&1
python3 $A --contexts $R/lme_seed4_default_ctx.json --out $R/lme_seed4_routed_acc.json --prompt concise --cascade auto_v1n --route-compute auto_v1n --route-compute-think --extra-judges --official-judge --threads 2 --label "seed4 routed" >> $LOG 2>&1
echo "[seed4] morning shipped vs ORIGINAL (plain concise)" >> $LOG
python3 $C $R/lme_seed4_plain_concise_acc.json $R/lme_seed4_crateq1_concise_cascauto_v1n_acc.json --md --metric strict,strict_v2,lenient_nonidk,official >> $LOG 2>&1
echo "[seed4] ROUTED (final) vs ORIGINAL (plain concise)" >> $LOG
python3 $C $R/lme_seed4_plain_concise_acc.json $R/lme_seed4_routed_acc.json --md --metric strict,strict_v2,lenient_nonidk,official >> $LOG 2>&1
echo "[seed4] ROUTED vs morning shipped" >> $LOG
python3 $C $R/lme_seed4_crateq1_concise_cascauto_v1n_acc.json $R/lme_seed4_routed_acc.json --md --metric strict,strict_v2,lenient_nonidk,official >> $LOG 2>&1
echo "[seed4] $(date '+%F %T') done" >> $LOG; touch $LOG.done
