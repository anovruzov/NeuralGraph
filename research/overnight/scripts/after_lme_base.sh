#!/bin/zsh
# Runs once the LME-50 baseline ingest is done: reload Qwen with a 16k context (no TTL), re-ingest questions whose
# databases carry dead extraction jobs (transient 400s during the run), then launch the full-50 reader sweep.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1 PATH="$HOME/.cargo/bin:$PATH"
LOG=.context/overnight/logs/after_lme_base.log
until [ -f .context/overnight/logs/lme_base_ingest.done ]; do sleep 30; done
echo "[after] $(date '+%F %T') ingest done; reloading qwen with 16k context" >> $LOG
lms unload qwen3.8-9b-mlx >> $LOG 2>&1; lms load qwen3.8-9b-mlx -y --context-length 16384 --parallel 4 --identifier qwen3.8-9b-mlx >> $LOG 2>&1
bad=$(for f in .context/overnight/db/lme_base/*.db; do r=$(sqlite3 $f "select sum(status='dead') + sum(status='failed') + (select count(*) from messages where status='pending') from jobs"); [ "${r:-0}" != "0" ] && basename $f .db; done)
echo "[after] damaged: $bad" >> $LOG
if [ -n "$bad" ]; then
  python3 - "$bad" <<'PY'
import json, sys, os
bad = set(sys.argv[1].split())
p = '.context/overnight/results/lme_base_retrieval.json'
d = json.load(open(p)); d['rows'] = [r for r in d['rows'] if r['question_id'] not in bad]
json.dump(d, open(p, 'w'), indent=2)
for q in bad:
    for ext in ('', '-wal', '-shm'):
        f = f'.context/overnight/db/lme_base/{q}.db{ext}'
        if os.path.exists(f): os.remove(f)
print('removed', sorted(bad))
PY
  ./target/release/chat-memory-longmemeval --n 50 --seed 1 --db-dir .context/overnight/db/lme_base --out .context/overnight/results/lme_base_retrieval.json --resume >> $LOG 2>&1
  echo "[after] $(date '+%F %T') re-ingest rc=$?" >> $LOG
fi
research/overnight/scripts/sweep.sh lme50 .context/overnight/sweep_lme50.txt >> $LOG 2>&1
echo "[after] $(date '+%F %T') lme50 sweep launched" >> $LOG
