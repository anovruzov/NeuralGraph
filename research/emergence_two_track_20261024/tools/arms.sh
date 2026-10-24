#!/bin/bash
# usage: arms.sh ARM [seeds...]; runs from /home/user/emergence (committed HEAD), diagnostic ablations on dev seeds
S=/tmp/claude-0/-home-user-NeuralGraph/2707d1a8-0286-570b-bef6-de01f8113a8b/scratchpad
cd /home/user/emergence && export MYCELIC_NATS_SERVER_BIN=$S/nats/nats-server PATH=$S/nats:$PATH PYTHONPATH=/home/user/emergence
ARM=$1; shift
ND='"egress": {"near_duplicate_roots": {"jaccard": 0.65, "days": 14}}'
N1='"questions": {"pushdown": {"weak_corroborating_sites": 1}}'
H1='"detectors": {"min_history_weeks": 1}'
case $ARM in
  nd)        OV="{$ND}"; EXTRA="";;
  n1)        OV="{$N1}"; EXTRA="";;
  h1n_nd)    OV="{$H1, $ND}"; EXTRA="--top-n 1000";;
  h1n_nd_n1) OV="{$H1, $ND, $N1}"; EXTRA="--top-n 1000";;
  *) echo unknown arm; exit 2;;
esac
for seed in "$@"; do
  t=$(date +%s)
  python3.11 $S/tools/ablate.py --tag $ARM-s$seed --pack-override "$OV" -- run --size 200 --seed $seed --mode process --skip-drills $EXTRA --out $S/em/abl-$ARM-s$seed --run-id ABLATION-$ARM-s$seed-$(git rev-parse --short HEAD) > $S/em/abl-$ARM-s$seed.log 2>&1
  echo "$ARM seed $seed exit $? secs $(( $(date +%s)-t ))"
done
