#!/bin/zsh
# 27B extractor (deterministic) vs the 9B deterministic baselines: retrieval, plain e2e, routed e2e.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/after_det27b.log
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py
until [ -f .context/overnight/logs/lme_det27b_ingest.done ]; do sleep 60; done
lms unload qwen/qwen3.8-27b >> $LOG 2>&1
python3 - >> $LOG <<'PY'
import json
for n in ('lme_det1','lme_det2','lme_det27b'):
    o=json.load(open(f'.context/overnight/results/{n}_retrieval.json'))['summary']['overall']; print(n, {k:o[k] for k in ('turn@k','ceiling_turn','total_memories','total_extract_s')})
PY
NEURALGRAPH_EXP=context_quotes=0,context_raw_messages=0,context_max_chars=3600 ./target/release/chat-memory-context lme --db-dir .context/overnight/db/lme_det27b --n 50 --seed 1 --out $R/lme_det27b_plain_ctx.json >> $LOG 2>&1
./target/release/chat-memory-context lme --db-dir .context/overnight/db/lme_det27b --n 50 --seed 1 --out $R/lme_det27b_default_ctx.json >> $LOG 2>&1
python3 $A --contexts $R/lme_det27b_plain_ctx.json --out $R/lme_det27b_plain_concise_acc.json --prompt concise --extra-judges --official-judge --threads 4 --label "27b plain" >> $LOG 2>&1
python3 $A --contexts $R/lme_det27b_default_ctx.json --out $R/lme_det27b_routed_acc.json --prompt concise --cascade auto_v1n --route-compute auto_v1n --route-compute-think --extra-judges --official-judge --threads 2 --label "27b routed" >> $LOG 2>&1
for d in det1 det2; do echo "[27b] plain: 27B extractor vs $d (9B)" >> $LOG; python3 $C $R/lme_${d}_plain_concise_off_acc.json $R/lme_det27b_plain_concise_acc.json --md --metric strict,strict_v2,lenient_nonidk,official >> $LOG 2>&1; done
echo "[27b] routed: 27B extractor vs base2 (9B extractor, routed)" >> $LOG
python3 $C $R/lme_base2_routed_acc.json $R/lme_det27b_routed_acc.json --md --metric strict,strict_v2,lenient_nonidk,official >> $LOG 2>&1
echo "[27b] $(date '+%F %T') done" >> $LOG; touch $LOG.done
