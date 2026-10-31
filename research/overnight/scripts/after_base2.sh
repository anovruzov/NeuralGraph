#!/bin/zsh
# Noise floor: an UNCHANGED-config re-ingest of LME-50 (lme_base2) vs the first baseline ingest (lme_base), plain context, concise.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/after_base2.log
until [ -f .context/overnight/logs/lme_base2_ingest.done ]; do sleep 30; done
echo "[base2] $(date '+%F %T') ingest done" >> $LOG
python3 - >> $LOG <<'PY'
import json
for n in ('lme_base','lme_base2','lme_E56'):
    o=json.load(open(f'.context/overnight/results/{n}_retrieval.json'))['summary']['overall']; print(n, {k:o[k] for k in ('turn@k','ceiling_turn','total_memories','total_extract_s')})
b={r['question_id']:r for r in json.load(open('.context/overnight/results/lme_base_retrieval.json'))['rows']}
b2={r['question_id']:r for r in json.load(open('.context/overnight/results/lme_base2_retrieval.json'))['rows']}
diff=[(q, b[q]['memories'], b2[q]['memories']) for q in b if q in b2 and b[q]['memories']!=b2[q]['memories']]
print('questions with different memory counts:', len(diff), 'max abs diff:', max((abs(x-y) for _,x,y in diff), default=0))
print('turn@15 flips:', sum(1 for q in b if q in b2 and (b[q]['ctx_turn_rank'] is None)!=(b2[q]['ctx_turn_rank'] is None)))
PY
NEURALGRAPH_EXP=context_quotes=0 ./target/release/chat-memory-context lme --db-dir .context/overnight/db/lme_base2 --n 50 --seed 1 --out $R/lme_base2_plain_ctx.json >> $LOG 2>&1
python3 research/overnight/scripts/answer_eval.py --contexts $R/lme_base2_plain_ctx.json --out $R/lme_base2_plain_concise_acc.json --prompt concise --extra-judges --threads 4 --label "base2 plain" >> $LOG 2>&1
echo "[base2] plain base2 vs plain base (same config: this is the noise floor)" >> $LOG
python3 research/overnight/scripts/compare.py $R/lme_base_vk15_concise_acc.json $R/lme_base2_plain_concise_acc.json --md --metric strict,lenient,strict_v2,lenient_nonidk >> $LOG 2>&1
echo "[base2] plain E56 vs plain base2" >> $LOG
python3 research/overnight/scripts/compare.py $R/lme_base2_plain_concise_acc.json $R/lme_E56_plain_concise_acc.json --md --metric strict,lenient,strict_v2,lenient_nonidk >> $LOG 2>&1
echo "[base2] $(date '+%F %T') done" >> $LOG; touch $LOG.done
