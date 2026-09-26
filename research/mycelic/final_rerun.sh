#!/bin/sh
# Final rerun of the whole Mycelic benchmark on the frozen vNext configuration.
#
#   research/mycelic/final_rerun.sh <freeze args for freeze_vnext.py...>
#
# 1. archives every v1 artifact under artifacts/v1/ (the report reads the
#    FIRST row per world, so a rerun has to start from empty files); when the
#    v1 archive already exists, the current rows move to artifacts/previous/;
# 2. freezes the vNext knobs into calibration.json with their provenance;
# 3. reruns every experiment in three parallel streams, then the 100k scale
#    trend, the loss accounting and the candidate dump + ranker evaluation;
# 4. regenerates every table, figure, document and PDF.
# Each stage appends to its own log under logs/final_*.log and leaves a
# marker line so an interrupted run can be resumed by hand.
set -u
cd "$(dirname "$0")/../.." || exit 1   # the repository root, wherever it is checked out
A=research/mycelic/artifacts
L=research/mycelic/logs
mkdir -p "$A/v1" "$L"

# v1/calibration.json is written right after this loop, so it marks an archive
# that already exists (a fresh clone, or a second run): then the current rows
# are set aside in previous/ and v1/ is never touched again.
if [ -f "$A/v1/calibration.json" ]; then ARCHIVE="$A/previous"; else ARCHIVE="$A/v1"; fi
mkdir -p "$ARCHIVE"
echo "=== archive to $ARCHIVE $(date -Is)"
for f in e1_baselines e1b_extra e2_ablations e3_allocation e3b_level_marginal \
         e3c_q_sweep e4_fanin e4b_dept_fanin e5_adversarial e6_frontier e7_shape \
         e8_crosslinks e9_privacy e10_scale_trend e12_provenance e1d_b4_capped \
         loss_funnel; do
  if [ -f "$A/$f.jsonl" ]; then mv "$A/$f.jsonl" "$ARCHIVE/$f.jsonl"; fi
done
if [ ! -f "$A/v1/calibration.json" ]; then
  python3 - <<'PY'
import json
cal = json.load(open("research/mycelic/artifacts/calibration.json"))
for k in ("ranker", "ranker_archs", "ranker_arch_table", "vnext"):
    cal.pop(k, None)
json.dump(cal, open("research/mycelic/artifacts/v1/calibration.json", "w"), indent=1)
print("v1 calibration archived (hand ranker, v1 knobs)")
PY
fi

echo "=== freeze $(date -Is)"
# the ranker fitted on the calibration seeds under the frozen pipeline
RANKER=${RANKER:-research/mycelic/artifacts/calibration.hyb.json}
python3 - "$RANKER" <<'PY'
import json, sys
src = json.load(open(sys.argv[1]))
path = "research/mycelic/artifacts/calibration.json"
cal = json.load(open(path))
cal["ranker"] = src["ranker"]
cal["ranker"]["source_file"] = sys.argv[1]
json.dump(cal, open(path, "w"), indent=1)
print("ranker installed from", sys.argv[1], "l2", src["ranker"]["l2"], "interactions", src["ranker"]["interactions"], "n_train", src["ranker"]["n_train"])
PY
python3 -m research.mycelic.freeze_vnext "$@" || exit 1
# per-architecture ranker adoption, decided on the calibration seeds under
# the frozen knobs (every system keeps whichever ranker is not worse for it)
echo "=== ranker adoption $(date -Is)"
python3 -m research.mycelic.calibrator archs > "$L/final_ranker_archs.log" 2>&1
tail -12 "$L/final_ranker_archs.log"

run() {  # run <log> <experiment...>
  log=$1; shift
  for e in "$@"; do
    echo "=== $e start $(date -Is)" >> "$L/$log"
    timeout 7200 python3 -m research.mycelic.experiments "$e" >> "$L/$log" 2>&1
    rc=$?   # before any command substitution: under bash $(date) would reset $?
    echo "=== $e end $(date -Is) rc=$rc" >> "$L/$log"
  done
}

echo "=== experiments $(date -Is)"
: > "$L/final_s1.log"; : > "$L/final_s2.log"; : > "$L/final_s3.log"
run final_s1.log e1 e11 e1c &
P1=$!
run final_s2.log e1b e2 e3 e3b e3c e6 e1d &
P2=$!
run final_s3.log e5 e4 e4b e7 e8 e9 e12 &
P3=$!
wait $P1 $P2 $P3
echo "=== experiments done $(date -Is)"

echo "=== scale trend + loss accounting $(date -Is)"
: > "$L/final_s4.log"
run final_s4.log e10 &
P4=$!
python3 -m research.mycelic.loss_accounting 10000 0,1,2,3,4 > "$L/final_loss10k.log" 2>&1 &
P5=$!
python3 -m research.mycelic.loss_accounting 50000 0,1,2 > "$L/final_loss50k.log" 2>&1 &
P6=$!
wait $P4 $P5 $P6
echo "=== scale trend + loss accounting done $(date -Is)"

echo "=== candidate dump + ranker evaluation $(date -Is)"
# The previous run's evaluation and dumps go first: if this step fails, the loss
# document then says the dump is incomplete instead of printing stale numbers.
rm -f "$A/ranker_eval_final.json" "$A"/hyp_features_final*.jsonl
python3 - > "$L/final_ranker_eval.log" 2>&1 <<'PY'
import json
from research.mycelic.calibrator import dump, evaluate_calibrator
from research.mycelic.runner import CAL
knobs = {k: CAL[k] for k in ("question_frac", "batched_descent", "local_reextract",
                             "link_time", "strict_targeting", "triage_target_chains")
         if k in CAL}
# the hierarchy is dumped in its frozen configuration; the controls at theirs
dump(archs=("H_mycelic_full",), cfg_over=knobs, tag="_finalH")
dump(archs=("A2_chunked_ctx", "B4_central_triage", "Y_oracle_retrieval"), tag="_finalC")
out = {}
for tag in ("_finalH", "_finalC"):
    out[tag] = evaluate_calibrator(tag=tag)
    print(tag, json.dumps({k: v for k, v in out[tag].items() if k != "weights"}, default=float))
json.dump(out, open("research/mycelic/artifacts/ranker_eval_final.json", "w"), indent=1, default=float)
PY

echo "=== reports $(date -Is)"
python3 -m research.mycelic.plots > "$L/final_reports.log" 2>&1
python3 -m research.mycelic.report >> "$L/final_reports.log" 2>&1
python3 -m research.mycelic.loss_doc >> "$L/final_reports.log" 2>&1
python3 -m research.mycelic.vnext_docs >> "$L/final_reports.log" 2>&1
python3 -m research.mycelic.make_pdf docs/MYCELIC_ENTERPRISE.md docs/MYCELIC_ENTERPRISE.pdf >> "$L/final_reports.log" 2>&1
python3 -m research.mycelic.make_pdf docs/MYCELIC_LOSS_ACCOUNTING.md docs/MYCELIC_LOSS_ACCOUNTING.pdf \
  "Mycelic: where the hidden patterns go" \
  "Loss accounting, gap decomposition and the vNext results on the frozen configuration" >> "$L/final_reports.log" 2>&1
python3 -m research.mycelic.make_pdf docs/mycelic_vnext/ALL.md docs/MYCELIC_VNEXT.pdf \
  "Mycelic vNext" \
  "Research report, architecture, experiment matrix, loss accounting and adversarial review" >> "$L/final_reports.log" 2>&1
echo "=== FINAL_RERUN_DONE $(date -Is)"
