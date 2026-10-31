#!/bin/sh
# Sample a concurrency-8 run with macOS `sample` and print the heaviest frames.
# usage: sample_hotspots.sh <db> <queries> <qvec> <out_dir> [ENV=VAL ...]
set -e
DB=$1; Q=$2; QV=$3; OUT=$4; shift 4
mkdir -p "$OUT"
env "$@" target/release/o100m run --n 100000 --db "$DB" --queries "$Q" --qvec "$QV" --concurrency 8 --passes 3 --out-dir "$OUT/run" > /dev/null 2>&1 &
PID=$!
sleep 8
sample $PID 6 -mayDie -file "$OUT/sample.txt" > /dev/null 2>&1 || true
wait $PID
grep -E "^ +[0-9]+ " "$OUT/sample.txt" | head -0
# "Sort by top of stack" section lists self time per symbol
awk '/Sort by top of stack/{f=1;next} f&&NF' "$OUT/sample.txt" | head -25
