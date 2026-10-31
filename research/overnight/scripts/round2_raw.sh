#!/bin/zsh
# Episodic recall (raw matching turns) on the clean ingest, with a budget that lets raw lines in. vs shipped (quotes=1 + cascade).
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/round2_raw.log
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py; BASE=$R/lme_base2_crateq1_concise_cascauto_v1n_acc.json
until [ -f .context/overnight/logs/round2_reader.log.done ]; do sleep 20; done
NEURALGRAPH_EXP=context_quotes=1,context_raw_messages=3,context_max_chars=6000 ./target/release/chat-memory-context lme --db-dir .context/overnight/db/lme_base2 --n 50 --seed 1 --out $R/lme_base2_q1raw3_6k_ctx.json >> $LOG 2>&1
NEURALGRAPH_EXP=context_quotes=1,context_raw_messages=3,context_raw_include_assistant=1,context_max_chars=6000 ./target/release/chat-memory-context lme --db-dir .context/overnight/db/lme_base2 --n 50 --seed 1 --out $R/lme_base2_q1raw3a_6k_ctx.json >> $LOG 2>&1
NEURALGRAPH_EXP=context_quotes=1,context_max_chars=6000 ./target/release/chat-memory-context lme --db-dir .context/overnight/db/lme_base2 --n 50 --seed 1 --out $R/lme_base2_q1_6k_ctx.json >> $LOG 2>&1
for v in q1_6k q1raw3_6k q1raw3a_6k; do
  python3 $A --contexts $R/lme_base2_${v}_ctx.json --out $R/lme_base2_${v}_concise_cascauto_v1n_acc.json --prompt concise --cascade auto_v1n --extra-judges --threads 4 --label "$v" >> $LOG 2>&1
  echo "[raw] $v + cascade vs shipped (q1 3600 + cascade)" >> $LOG
  python3 $C $BASE $R/lme_base2_${v}_concise_cascauto_v1n_acc.json --md --metric strict,lenient,strict_v2,lenient_nonidk >> $LOG 2>&1
done
echo "[raw] $(date '+%F %T') done" >> $LOG; touch $LOG.done
