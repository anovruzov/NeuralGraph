#!/bin/zsh
# Determinism test: two sequential same-config ingests at --concurrency 1 --parallel 1 (lme_det1, lme_det2).
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/after_det.log
until [ -f .context/overnight/logs/lme_det2_ingest.done ]; do sleep 30; done
echo "[det] $(date '+%F %T') both ingests done" >> $LOG
python3 - >> $LOG <<'PY'
import json
rs={}
for n in ('lme_det1','lme_det2','lme_base','lme_base2'):
    d=json.load(open(f'.context/overnight/results/{n}_retrieval.json')); rs[n]={r['question_id']:r for r in d['rows']}
    o=d['summary']['overall']; print(n, {k:o[k] for k in ('turn@k','ceiling_turn','total_memories','total_extract_s')})
a,b=rs['lme_det1'],rs['lme_det2']
diff=[(q,a[q]['memories'],b[q]['memories']) for q in a if q in b and a[q]['memories']!=b[q]['memories']]
print('det1 vs det2: questions with different memory counts:', len(diff), 'max abs diff:', max((abs(x-y) for _,x,y in diff), default=0))
print('det1 vs det2 turn@15 flips:', sum(1 for q in a if q in b and (a[q]['ctx_turn_rank'] is None)!=(b[q]['ctx_turn_rank'] is None)))
PY
for n in lme_det1 lme_det2; do
  NEURALGRAPH_EXP=context_quotes=0 ./target/release/chat-memory-context lme --db-dir .context/overnight/db/$n --n 50 --seed 1 --out $R/${n}_plain_ctx.json >> $LOG 2>&1
  python3 research/overnight/scripts/answer_eval.py --contexts $R/${n}_plain_ctx.json --out $R/${n}_plain_concise_acc.json --prompt concise --extra-judges --threads 4 --label "$n plain" >> $LOG 2>&1
done
echo "[det] plain det1 vs det2 (same config, concurrency 1): the deterministic-mode noise floor" >> $LOG
python3 research/overnight/scripts/compare.py $R/lme_det1_plain_concise_acc.json $R/lme_det2_plain_concise_acc.json --md --metric strict,lenient,strict_v2,lenient_nonidk >> $LOG 2>&1
echo "[det] $(date '+%F %T') done" >> $LOG; touch $LOG.done
