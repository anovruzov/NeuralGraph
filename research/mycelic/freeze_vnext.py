"""Freeze the vNext knobs into calibration.json, with provenance.

Every knob written here was chosen on the calibration seeds (500-502) or
confirmed on the held-out evaluation seeds by a paired experiment whose rows
live in artifacts/quick_<tag>.jsonl; the tag is recorded next to the value
so the report can cite the measurement rather than the decision.

    python3 -m research.mycelic.freeze_vnext question_frac=0.65:qf_v3 \\
        batched_descent=true:rk2_batch link_time=hybrid:refit_hyb

A value is `<literal>:<quick tag>`; literals are parsed as JSON where they
can be (true/false/numbers) and kept as strings otherwise.
"""
from __future__ import annotations

import json
import os
import sys
import time

from .runner import ART

KNOBS = {"question_frac", "batched_descent", "local_reextract", "link_time",
         "strict_targeting", "triage_target_chains", "sketch_weak_bits",
         "merge_descent_evidence"}


def main(args) -> None:
    path = os.path.join(ART, "calibration.json")
    cal = json.load(open(path))
    prov = cal.get("vnext", {})
    for a in args:
        k, _, rest = a.partition("=")
        if k not in KNOBS:
            raise SystemExit(f"unknown knob {k!r}; known: {sorted(KNOBS)}")
        lit, _, tag = rest.partition(":")
        try:
            v = json.loads(lit)
        except json.JSONDecodeError:
            v = lit
        cal[k] = v
        prov[k] = {"value": v, "evidence": f"quick_{tag}.jsonl" if tag else "",
                   "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        print(f"  {k} = {v!r}   ({prov[k]['evidence'] or 'no tag'})")
    cal["vnext"] = prov
    with open(path, "w") as fh:
        json.dump(cal, fh, indent=1)
    print("wrote", path)


if __name__ == "__main__":
    main(sys.argv[1:])
