#!/bin/zsh
# When the full-50 E56 ingest (hyphen_ground + num_dedupe) finishes: contexts with quotes off (plain, comparable to lme_base plain)
# and quotes on (shipped), answer + judge, paired compares against the lme_base equivalents.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/after_e56.log
until [ -f .context/overnight/logs/lme_E56_ingest.done ]; do sleep 30; done
echo "[e56] $(date '+%F %T') ingest done" >> $LOG
# repair: re-ingest questions whose DB has dead/failed jobs or pending messages (same recipe as after_lme_base2.sh)
bad=""
for f in .context/overnight/db/lme_E56/*.db; do
  r=$(sqlite3 "$f" "select sum(status='dead') + sum(status='failed') + (select count(*) from messages where status='pending') from jobs" 2>/dev/null)
  if [ "${r:-0}" != "0" ]; then bad="$bad $(basename $f .db)"; fi
done
echo "[e56] damaged:$bad" >> $LOG
if [ -n "$bad" ]; then
  python3 - $bad <<'PY'
import json, sys, os
bad = set(sys.argv[1:]); p = '.context/overnight/results/lme_E56_retrieval.json'
d = json.load(open(p)); d['rows'] = [r for r in d['rows'] if r['question_id'] not in bad]; json.dump(d, open(p, 'w'), indent=2)
for q in bad:
    for ext in ('', '-wal', '-shm'):
        f = f'.context/overnight/db/lme_E56/{q}.db{ext}'
        if os.path.exists(f): os.remove(f)
print('removed', sorted(bad))
PY
  NEURALGRAPH_EXP=hyphen_ground,num_dedupe ./target/release/chat-memory-longmemeval --n 50 --seed 1 --db-dir .context/overnight/db/lme_E56 --out .context/overnight/results/lme_E56_retrieval.json --resume >> $LOG 2>&1
  echo "[e56] $(date '+%F %T') re-ingest rc=$?" >> $LOG
fi
python3 - >> $LOG <<'PY'
import json
for n in ('lme_base','lme_E56'):
    o=json.load(open(f'.context/overnight/results/{n}_retrieval.json'))['summary']['overall']; print(n, {k:o[k] for k in ('turn@k','ceiling_turn','total_memories','total_extract_s')})
PY
NEURALGRAPH_EXP=context_quotes=0 ./target/release/chat-memory-context lme --db-dir .context/overnight/db/lme_E56 --n 50 --seed 1 --out $R/lme_E56_plain_ctx.json >> $LOG 2>&1
NEURALGRAPH_EXP=context_quotes=1 ./target/release/chat-memory-context lme --db-dir .context/overnight/db/lme_E56 --n 50 --seed 1 --out $R/lme_E56_crateq1_ctx.json >> $LOG 2>&1
python3 research/overnight/scripts/answer_eval.py --contexts $R/lme_E56_plain_ctx.json --out $R/lme_E56_plain_concise_acc.json --prompt concise --extra-judges --threads 4 --label "E56 plain" >> $LOG 2>&1
python3 research/overnight/scripts/answer_eval.py --contexts $R/lme_E56_crateq1_ctx.json --out $R/lme_E56_crateq1_concise_cascauto_v1n_acc.json --prompt concise --cascade auto_v1n --extra-judges --threads 4 --label "E56 shipped" >> $LOG 2>&1
echo "[e56] plain E56 vs plain base" >> $LOG
python3 research/overnight/scripts/compare.py $R/lme_base_vk15_concise_acc.json $R/lme_E56_plain_concise_acc.json --md --metric strict,lenient,strict_v2,lenient_nonidk >> $LOG 2>&1
echo "[e56] shipped E56 vs shipped base" >> $LOG
python3 research/overnight/scripts/compare.py $R/lme_base_crateq1_concise_cascauto_v1n_acc.json $R/lme_E56_crateq1_concise_cascauto_v1n_acc.json --md --metric strict,lenient,strict_v2,lenient_nonidk >> $LOG 2>&1
echo "[e56] $(date '+%F %T') done" >> $LOG; touch $LOG.done
