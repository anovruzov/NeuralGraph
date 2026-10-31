#!/bin/zsh
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1 PATH="$HOME/.cargo/bin:$PATH"
LOG=.context/overnight/logs/after_lme_base.log
while pgrep -f "chat-memory-longmemeval --n 50 --seed 1 --db-dir .context/overnight/db/lme_base" > /dev/null; do sleep 15; done
echo "[after2] $(date '+%F %T') first repair finished; repairing remaining damaged DBs" >> $LOG
bad=""
for f in .context/overnight/db/lme_base/*.db; do
  r=$(sqlite3 "$f" "select sum(status='dead') + sum(status='failed') + (select count(*) from messages where status='pending') from jobs" 2>/dev/null)
  if [ "${r:-0}" != "0" ]; then bad="$bad $(basename $f .db)"; fi
done
echo "[after2] damaged:$bad" >> $LOG
if [ -n "$bad" ]; then
  python3 - $bad <<'PY'
import json, sys, os
bad = set(sys.argv[1:])
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
  echo "[after2] $(date '+%F %T') re-ingest rc=$?" >> $LOG
fi
research/overnight/scripts/sweep.sh lme50 .context/overnight/sweep_lme50.txt >> $LOG 2>&1
echo "[after2] $(date '+%F %T') lme50 sweep launched" >> $LOG
