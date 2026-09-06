#!/usr/bin/env bash
# Large-Scale Agentic Web Stress Test -- full reproduction from one command.
#
#   ./run_all.sh              # main scales (N=1e2,1e3,1e4) + all ablations + analysis
#   FULL=1 ./run_all.sh       # additionally runs N=1e5 (~1 h, ~6 GB RSS)
#   QUICK=1 ./run_all.sh      # smoke configuration, a few minutes, for checking the pipeline
#
# Raw outputs are append-only: each invocation creates a new timestamped
# directory under results/ and appends one line to results/index.jsonl.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
cd "$REPO"
mkdir -p "$HERE/logs"
LOG="$HERE/logs/run_all_$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG") 2>&1
echo "run_all.sh starting $(date -u +%FT%TZ); log -> $LOG"

RUN="python3 -m experiments.large_scale_agentic_web.src.runner"
ANALYZE="python3 -m experiments.large_scale_agentic_web.src.analyze"
REPORT="python3 -m experiments.large_scale_agentic_web.src.report"
PAPER="python3 -m experiments.large_scale_agentic_web.src.paper_section"
PDF="python3 -m experiments.large_scale_agentic_web.src.make_pdf"
CFG="experiments/large_scale_agentic_web/configs"

python3 -c "import numpy, scipy, matplotlib" || {
  echo "installing dependencies"; pip install -r "$HERE/requirements.txt"; }
python3 - <<'PY' > "$HERE/environment.txt"
import platform, sys, os, subprocess
import numpy, scipy, matplotlib
print("python", sys.version.replace("\n", " "))
print("numpy", numpy.__version__)
print("scipy", scipy.__version__)
print("matplotlib", matplotlib.__version__)
print("platform", platform.platform())
print("machine", platform.machine())
print("cpu_count", os.cpu_count())
try:
    print("git_commit", subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip())
except Exception:
    print("git_commit unknown")
PY

if [[ "${QUICK:-0}" == "1" ]]; then
  $RUN --config "$CFG/smoke.json" --tag quick --index-name quick
  $ANALYZE --index quick --out-tag quick
  exit 0
fi

# ---------------------------------------------------------------- main scales
$RUN --config "$CFG/n100.json"    --tag main --index-name main_n100
$RUN --config "$CFG/n1000.json"   --tag main --index-name main_n1000
$RUN --config "$CFG/n10000.json"  --tag main --index-name main_n10000
if [[ "${FULL:-0}" == "1" ]]; then
  $RUN --config "$CFG/n100000.json" --tag main --index-name main_n100000
fi

# --------------------------------------------------- storage-budget frontier
for K in 1 2 4 8 16 32; do
  $RUN --config "$CFG/budget_n1000.json"  --tag "k$K" --index-name "budget_n1000"  --set budget_k=$K
  $RUN --config "$CFG/budget_n10000.json" --tag "k$K" --index-name "budget_n10000" --set budget_k=$K
done

# ----------------------------------------------- continual-questioning ablation
for B in 0.05 0.1 0.2 0.4 0.6 1.0 2.0; do
  $RUN --config "$CFG/questioning_n10000.json" --tag "budget$B" --index-name questioning_budget \
       --set question_budget=$B
done
for S in strategic temporal hybrid random; do
  $RUN --config "$CFG/questioning_n10000.json" --tag "strategy_$S" --index-name questioning_strategy \
       --set question_strategy=\"$S\"
done
# question-type decomposition: reverify only / add-support only / resolve only
$RUN --config "$CFG/questioning_n10000.json" --tag mix_reverify --index-name questioning_mix \
     --set question_mix=[1.0,0.0,0.0]
$RUN --config "$CFG/questioning_n10000.json" --tag mix_add --index-name questioning_mix \
     --set question_mix=[0.0,1.0,0.0]
$RUN --config "$CFG/questioning_n10000.json" --tag mix_resolve --index-name questioning_mix \
     --set question_mix=[0.0,0.0,1.0]
# storage parity on/off
$RUN --config "$CFG/questioning_n10000.json" --tag noequalize --index-name questioning_equalize \
     --set question_equalize_storage=false

# ------------------------------------------- placement x aggregation factorial
$RUN --config "$CFG/factorial_n10000.json" --tag main --index-name factorial
# the same cells in a world where evidence really is dominated by correlated copies
$RUN --config "$CFG/factorial_n10000.json" --tag heavy_correlation --index-name factorial_heavy \
     --set family_alpha=0.4 --set frac_class_correlated=0.25

# ------------------------ adversarial search for regimes where lineage fails
NEG="$CFG/negative_n10000.json"
$RUN --config "$NEG" --tag all_single_root      --index-name negative --set root_mix=[1.0,0.0,0.0] --set frac_class_independent=0.0
$RUN --config "$NEG" --tag heavy_correlation    --index-name negative --set family_alpha=0.4 --set frac_class_correlated=0.25
$RUN --config "$NEG" --tag high_revision        --index-name negative --set revision_fraction=0.75
$RUN --config "$NEG" --tag low_revision         --index-name negative --set revision_fraction=0.05
$RUN --config "$NEG" --tag question_flood       --index-name negative --set question_budget=8.0 --set question_strategy=\"random\"
$RUN --config "$NEG" --tag origin_corruption_50 --index-name negative --set corruption_levels=[0.5] --set corruption_types=[\"root\"]

# ------------------------------------------------- analysis, report, paper, PDF
$ANALYZE --all
$REPORT
$PAPER
$PDF
python3 -m experiments.large_scale_agentic_web.src.manifest
