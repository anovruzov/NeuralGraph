#!/bin/zsh
# Extraction fixes A/B in DETERMINISTIC mode: lme_detE56 (hyphen_ground+num_dedupe, concurrency 1) vs det1 and det2 (same mode, no fixes).
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/after_detE56.log
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py
until [ -f .context/overnight/logs/lme_detE56_ingest.done ]; do sleep 30; done
python3 - >> $LOG <<'PY'
import json
for n in ('lme_det1','lme_det2','lme_detE56'):
    o=json.load(open(f'.context/overnight/results/{n}_retrieval.json'))['summary']['overall']; print(n, {k:o[k] for k in ('turn@k','ceiling_turn','total_memories','total_extract_s')})
PY
NEURALGRAPH_EXP=context_quotes=0,context_raw_messages=0,context_max_chars=3600 ./target/release/chat-memory-context lme --db-dir .context/overnight/db/lme_detE56 --n 50 --seed 1 --out $R/lme_detE56_plain_ctx.json >> $LOG 2>&1
python3 $A --contexts $R/lme_detE56_plain_ctx.json --out $R/lme_detE56_plain_concise_acc.json --prompt concise --extra-judges --official-judge --threads 4 --label "detE56 plain" >> $LOG 2>&1
for d in det1 det2; do
  python3 $A --contexts $R/lme_${d}_plain_ctx.json --regrade-from $R/lme_${d}_plain_concise_acc.json --out $R/lme_${d}_plain_concise_off_acc.json --extra-judges --official-judge --threads 4 >> $LOG 2>&1
  echo "[detE56] fixes vs $d (deterministic mode, plain context, concise)" >> $LOG
  python3 $C $R/lme_${d}_plain_concise_off_acc.json $R/lme_detE56_plain_concise_acc.json --md --metric strict,strict_v2,lenient_nonidk,official >> $LOG 2>&1
done
echo "[detE56] $(date '+%F %T') done" >> $LOG; touch $LOG.done
