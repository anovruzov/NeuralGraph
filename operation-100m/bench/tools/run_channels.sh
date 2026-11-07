#!/bin/sh
# Channel ablation on the 100K real-vector bank: V (vector only), K (BM25 only), VK (shipped hybrid),
# identical 2,000 queries, under the shipped engine and the bounded variant. Serial pass (concurrency 1).
set -eu
cd "$(dirname "$0")/../.."
BIN=../target/release/o100m
W=work/100k
R=bench/results/channels-100k-real
mkdir -p "$R"
for cfg in engine_default bounded; do
  for ch in V K VK; do
    if [ "$cfg" = bounded ]; then
      export O100M_VECTOR_INDEX=ivf O100M_NPROBE=32 O100M_READ_POOL=1 O100M_KEYWORD_MAX_DF=0.01
    else
      unset O100M_VECTOR_INDEX O100M_NPROBE O100M_READ_POOL O100M_KEYWORD_MAX_DF || true
    fi
    O100M_CHANNELS=$ch $BIN run --n 100000 --db $W/bank.db --queries bench/results/100k-real/queries.jsonl \
      --qvec file:$W/queries.f32 --concurrency 1 --out-dir "$R/$cfg-$ch" > /dev/null
    echo "done $cfg $ch"
  done
done
