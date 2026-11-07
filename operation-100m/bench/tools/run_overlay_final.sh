#!/bin/sh
# Final 100K runs after the index overlay + updated_at index: read-only (ablate.sh) then mixed read/write, on a
# quiet machine. Run from the repo root.
set -eu
W=operation-100m/work/100k; TK=operation-100m/bench/results/100k-real; Q=file:$W/queries.f32
R=operation-100m/bench/results/mixed-100k-real-final
mkdir -p $R; uptime > $R/load_before.txt
T=operation-100m/bench/tools/ablate.sh
$T overlay2_engine_default 100000 $W $TK $Q 1,8
$T overlay2_bounded_recall 100000 $W $TK $Q 1,8 O100M_VECTOR_INDEX=ivf O100M_IVF_PATH=$W/bank.nl316.ivf O100M_NPROBE=48 O100M_READ_POOL=1 O100M_KEYWORD_MAX_DF=0.01
for c in default bounded bounded_recall; do
  D=operation-100m/work/100k-mixed/final-$c; rm -rf $D; mkdir -p $D
  cp $W/bank.db $W/bank.db.ivf $W/bank.db.ivf.ids $W/bank.nl316.ivf $W/bank.nl316.ivf.ids $D/
  case $c in
    default) ENVS="X=1";;
    bounded) ENVS="O100M_VECTOR_INDEX=ivf O100M_NPROBE=32 O100M_READ_POOL=1 O100M_KEYWORD_MAX_DF=0.01";;
    bounded_recall) ENVS="O100M_VECTOR_INDEX=ivf O100M_IVF_PATH=$D/bank.nl316.ivf O100M_NPROBE=48 O100M_READ_POOL=1 O100M_KEYWORD_MAX_DF=0.01";;
  esac
  uptime > $R/load_before_$c.txt
  env $ENVS target/release/o100m mixed --n 100000 --db $D/bank.db --queries $TK/queries.jsonl --qvec $Q \
    --readers 4 --ops 2000 --write-rate 20 --out-dir $R/$c > /dev/null 2> $R/$c.err
  echo "mixed $c done"
done
uptime > $R/load_after.txt
