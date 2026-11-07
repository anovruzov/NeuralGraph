#!/bin/sh
# Independent before/after reproduction of the mixed read/write workload (Critic side).
# usage (repo root): verify_mixed.sh <old o100m binary> <new o100m binary> <out dir>
# Every run uses a fresh copy of the 100K bank, deleted afterwards. "old" copies have idx_memories_updated
# dropped, so the before numbers reflect the committed engine's schema.
set -eu
OLD=$1; NEW=$2; OUT=$3
ROOT=$(pwd); W=$ROOT/operation-100m/work/100k; TK=$ROOT/operation-100m/bench/results/100k-real
SCR=$ROOT/operation-100m/work/verify-mixed
mkdir -p "$OUT" "$SCR"
run() { # label binary engine ops rate
  label=$1; bin=$2; eng=$3; ops=$4; rate=$5
  D=$SCR/$label; rm -rf "${D:?}"; mkdir -p "$D"
  cp "$W/bank.db" "$W/bank.nl316.ivf" "$W/bank.nl316.ivf.ids" "$D/"
  case $label in old_*) python3 -I -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('DROP INDEX IF EXISTS idx_memories_updated'); c.commit()" "$D/bank.db";; esac
  if [ "$eng" = bounded_recall ]; then
    set -- O100M_VECTOR_INDEX=ivf O100M_IVF_PATH=$D/bank.nl316.ivf O100M_NPROBE=48 O100M_READ_POOL=1 O100M_KEYWORD_MAX_DF=0.01
  else
    set -- O100M_NONE=1
  fi
  uptime > "$OUT/$label.load_before.txt"
  env "$@" "$bin" mixed --n 100000 --db "$D/bank.db" --queries "$TK/queries.jsonl" --qvec "file:$W/queries.f32" \
    --readers 4 --ops "$ops" --write-rate "$rate" --out-dir "$OUT/$label" > /dev/null 2> "$OUT/$label.err"
  uptime > "$OUT/$label.load_after.txt"
  rm -rf "${D:?}"
  echo "done $label"
}
for rep in 1 2 3; do
  for eng in engine_default bounded_recall; do
    run old_${eng}_r$rep "$OLD" $eng 2000 20
    run new_${eng}_r$rep "$NEW" $eng 2000 20
  done
done
# compaction under load: ~15,000 changed rows > max(2048, 100000/16) = 6,250, so the overlay compacts mid-run
run new_bounded_recall_compaction "$NEW" bounded_recall 12000 200
run new_engine_default_compaction "$NEW" engine_default 12000 200
echo "all done"
