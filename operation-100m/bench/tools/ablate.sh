#!/bin/sh
# Run one engine variant on a bank and have the Critic judge it against the shipped-engine baseline.
# usage (from repo root): operation-100m/bench/tools/ablate.sh <name> <n> <work-dir> <task-results-dir> <qvec> <concurrency> [ENV=VAL ...]
# example: operation-100m/bench/tools/ablate.sh ivf32 100000 operation-100m/work/100k operation-100m/bench/results/100k-real \
#            file:operation-100m/work/100k/queries.f32 1,8 O100M_VECTOR_INDEX=ivf O100M_NPROBE=32
set -e
NAME=$1; N=$2; WORK=$(cd "$3" && pwd); TASK=$(cd "$4" && pwd); QVEC=$5; CONC=$6; shift 6
ROOT=$(pwd)
OUT="$TASK/../ablation-$(basename "$TASK")/$NAME"
mkdir -p "$OUT"
env "$@" "$ROOT/target/release/o100m" run --n "$N" --db "$WORK/bank.db" --queries "$TASK/queries.jsonl" --qvec "$QVEC" \
  --concurrency "$CONC" --out-dir "$OUT" > /dev/null 2> "$OUT/stderr.log"
printf '%s\n' "$*" > "$OUT/env.txt"
grep -i "warn" "$OUT/stderr.log" | head -3 || true
python3 -I "$ROOT/operation-100m/agents/judge_variant.py" "$OUT" "$WORK/bank.db" "$TASK" "$N"
