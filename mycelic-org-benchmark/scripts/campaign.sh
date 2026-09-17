#!/usr/bin/env bash
# The campaign that produced results/: families in priority order with per-family seed counts,
# Tier 1 first, then the Tier-2 headline/aggregation/poisoning/scaling, then Tier-3 scaling.
# Every family call is idempotent (append-only CSVs); interrupt and re-run to continue.
#
#   scripts/campaign.sh                 # everything (many hours on a 4-core machine)
#   STAGE=tier1 scripts/campaign.sh     # only the Tier-1 families
#   STAGE=tier2 scripts/campaign.sh     # only the Tier-2 families
#   STAGE=tier3 scripts/campaign.sh     # only the Tier-3 scaling points
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
STAGE="${STAGE:-all}"
JOBS="${JOBS:-3}"
RESULTS="${RESULTS:-results}"
SEED_OFFSET="${SEED_OFFSET:-1}"   # seed 0 is the development seed (design decisions were checked on it); evaluation uses seeds >= 1
export RESULTS
T0=$SECONDS
mkdir -p "$RESULTS/logs"

stage() { [[ "$STAGE" == "all" || "$STAGE" == "$1" ]]; }
fam() {   # fam <seeds> <family> [extra args passed through EXTRA_ARGS]
  local seeds="$1"; local family="$2"; shift 2
  echo "=== $(date -u +%H:%M:%S) family=$family seeds=$seeds jobs=$JOBS tier=${TIER:-tier1} $*"
  SEEDS="$seeds" JOBS="$JOBS" FAMILIES="$family" EXTRA_ARGS="--seed-offset $SEED_OFFSET $*" TIER="${TIER:-tier1}" scripts/run_small.sh || echo "!!! family $family failed (continuing)"
}

if stage tier1; then
  export TIER=tier1
  fam 30 aggregation
  fam 10 hierarchy
  fam 10 contradictions
  fam 10 temporal
  fam 10 independent_support
  fam 5  poisoning
  fam 5  privacy
  fam 5  questioning
  fam 5  models
  fam 5  failures
  fam 5  routing
  fam 3  scaling
fi
if stage tier2; then
  export TIER=tier2
  JOBS="${JOBS2:-2}"
  fam 5 headline
  fam 5 aggregation
  fam 3 poisoning
  fam 2 scaling --sizes 10000
fi
if stage tier3; then
  export TIER=tier3
  JOBS=1
  fam 1 scaling --sizes 50000,100000
fi
echo "=== campaign ($STAGE) done in $(( (SECONDS - T0) / 60 )) min"
