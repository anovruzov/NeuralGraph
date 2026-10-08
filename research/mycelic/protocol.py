"""Seed governance for the v5 optimisation programme (see PROTOCOL_V5.md).

The final evaluation set is sealed: building one of its worlds raises unless
the caller deliberately opens it with MYCELIC_FINAL_EVAL=1, and every opened
build is appended to artifacts/final_access.log, which is committed, so the
number of times the final set was read is part of the record rather than a
claim.  Nothing here changes how any world is generated.
"""
from __future__ import annotations

import datetime
import os
import subprocess
from typing import Dict, FrozenSet

# Sealed final sets, by scale.  Disjoint from every seed used before v5
# (10k: 0-29 and 500-548; 50k: 0-4 and 500-509; 900 in ad-hoc profiling).
FINAL_SEEDS: Dict[int, FrozenSet[int]] = {
    10_000: frozenset(range(3000, 3030)),
    50_000: frozenset(range(3000, 3003)),
}

# Development roles (informational; enforced by review, not by code).
TUNE_SEEDS = tuple(range(500, 600))          # fitting, screening, LOSO
DEVVAL_SEEDS_10K = tuple(range(600, 650))    # one paired read per accepted candidate
DEVVAL_SEEDS_50K = tuple(range(600, 603))
TRANSFER_SEEDS = tuple(range(650, 680))      # org shapes, noise, difficulty
LEGACY_SEEDS_10K = tuple(range(0, 30))       # historical panels, now development data

_LOG = os.path.join(os.path.dirname(__file__), "artifacts", "final_access.log")


def is_final(scale: int, seed: int) -> bool:
    return int(seed) in FINAL_SEEDS.get(int(scale), frozenset())


def check_world(scale: int, seed: int) -> None:
    """Called by runner.build_world before a world is generated."""
    if not is_final(scale, seed):
        return
    if os.environ.get("MYCELIC_FINAL_EVAL") != "1":
        raise PermissionError(
            f"seed {seed} at scale {scale} belongs to the sealed final set "
            f"(research/mycelic/protocol.py). Set MYCELIC_FINAL_EVAL=1 only for "
            f"the pre-registered one-shot final evaluation.")
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True,
                             cwd=os.path.dirname(__file__)).stdout.strip()
    except OSError:
        rev = "?"
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with open(_LOG, "a") as fh:
        fh.write(f"{stamp}\tscale={scale}\tseed={seed}\tcommit={rev}\t"
                 f"reason={os.environ.get('MYCELIC_FINAL_REASON', '')}\n")
