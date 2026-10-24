#!/bin/bash
# usage: ARMS_SCRATCH=DIR [WT=TREE] arms.sh ARM [seeds...]; diagnostic ablations on dev seeds.
# ARMS_SCRATCH holds nats/nats-server, tools/ablate.py and em/ (outputs); WT is the code tree to run (default: cwd)
S=${ARMS_SCRATCH:?set ARMS_SCRATCH}
WT=${WT:-$PWD}; TAGP=${TAGP:-}
cd $WT && export MYCELIC_NATS_SERVER_BIN=$S/nats/nats-server PATH=$S/nats:$PATH PYTHONPATH=$WT
ARM=$1; shift
ND='"egress": {"near_duplicate_roots": {"jaccard": 0.65, "days": 14}}'
N1='"questions": {"pushdown": {"weak_corroborating_sites": 1}}'
N2='"questions": {"pushdown": {"weak_corroborating_sites": 1, "roots_from_k_sites_only": true}}'
H1='"detectors": {"min_history_weeks": 1}'
RS='"detectors": {"min_history_weeks": 1, "rule_snapshots": true}'
RL='"detectors": {"min_history_weeks": 1, "rule_snapshots": true, "reask": {"every_weeks": 2}}'
case $ARM in
  nd)        OV="{$ND}"; EXTRA="";;
  n1)        OV="{$N1}"; EXTRA="";;
  h1n_nd)    OV="{$H1, $ND}"; EXTRA="--top-n 1000";;
  h1n_nd_n1) OV="{$H1, $ND, $N1}"; EXTRA="--top-n 1000";;
  h1n_nd_n1_all) OV="{$H1, $ND, $N1}"; EXTRA="--top-n 1000"; CAND="--m 0";;
  h1n_nd_n1_n2_all) OV="{$H1, $ND, $N1, $N2}"; EXTRA="--top-n 1000"; CAND="--m 0";;
  h1n_nd_n1_n2) OV="{$H1, $ND, $N1, $N2}"; EXTRA="--top-n 1000";;
  rs_nd_n1_n2) OV="{$RS, $ND, $N2}"; EXTRA="--top-n 100000";;
  roll_rs_nd_n1_n2) OV="{$RL, $ND, $N2}"; EXTRA="--top-n 100000";;
  rs_nd_n1_n2_edges) OV="{$RS, $ND, $N2}"; EXTRA="--top-n 100000 --graph-edges";;
  b60_rs_nd_n2) OV="{$RS, $ND, $N2}"; EXTRA="--top-n 60";;
  b60_rs_nd_n2_rulefirst) OV="{$RS, $ND, $N2}"; EXTRA="--top-n 60"; CAND="--order rulefirst";;
  s10k_b60_rulefirst) OV="{$RS, $ND, $N2}"; EXTRA="--top-n 60"; CAND="--order rulefirst --extra-size 10000"; SIZE=10000; MODE=in_process;;
  s10k_b60) OV="{$RS, $ND, $N2}"; EXTRA="--top-n 60"; CAND="--extra-size 10000"; SIZE=10000; MODE=in_process;;
  rule_nd_n1) OV="{$ND, $N1}"; EXTRA="--top-n 100000"; CAND="--candidates persist+rule";;
  rule_nd)   OV="{$ND}"; EXTRA="--top-n 100000"; CAND="--candidates persist+rule";;
  *) echo unknown arm; exit 2;;
esac
for seed in "$@"; do
  t=$(date +%s)
  python3.11 $S/tools/ablate.py --tag $ARM-s$seed --pack-override "$OV" $CAND -- run --size ${SIZE:-200} --seed $seed --mode ${MODE:-process} --skip-drills $EXTRA --out $S/em/abl-$TAGP$ARM-s$seed --run-id ABLATION-$TAGP$ARM-s$seed-$(git rev-parse --short HEAD) > $S/em/abl-$TAGP$ARM-s$seed.log 2>&1
  echo "$TAGP$ARM seed $seed exit $? secs $(( $(date +%s)-t ))"
done
