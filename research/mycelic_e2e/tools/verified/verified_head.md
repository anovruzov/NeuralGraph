# Verified results: Mycelic-E2E v1 (2026-10-09)

**How these numbers were produced.**
- Every accuracy figure below comes from `python -m research.mycelic_e2e.tools.results_table <run dirs> --json`. The tool re-runs
  the isolated scorer (`bench/score.py`) on each run's asker views and reads that run's architecture-gate report
  (`arch_gate.json`, written by `bench/arch_gate.py`).
- `tools/verified/gen_verified.py` composed the tables in this file from that JSON. The narrative notes between the tables
  quote further figures from the same runs' manifests and gate reports.
- The dev runs' `score.json`, `arch_gate.json`, `run_manifest.json` and `report.md` are kept under `results/runs/<run>/`.
- Holdout tasks, gold and views are **not** committed, so no holdout content enters the repository. Only aggregate numbers and
  gate verdicts are reported for the holdout.

**Labels that apply to every row.**
- **Provider:** `deterministic-provider`, i.e. `mycelic/models/fake.py` (sha256 `0dad30d6…`), lexical rules. No language model.
  The record templates were written to satisfy those rules (BENCHMARK_CONTRACT §2). These numbers therefore measure:
  - routing, authorization and isolation;
  - independence and lineage;
  - fault handling;
  - the commit gate;
  
  not language understanding.
- **Execution:** single process, SQLite transport (no NATS), one in-process API server.
- **Host:** cloud container, 4 vCPU, 15 GB RAM. The MacBook bridge environment never came up.
- **Primary metric (contract §5):** correct / all 120 tasks of the world, with a 95 % Wilson interval. An API error, timeout or
  missing view counts as wrong.
  - A positive task is correct only if the single option extracted from the asker's own supported claims is the gold entity.
  - An expected-abstain task is correct only if nothing is extracted **and** the asker's output has no reference the asker
    may not reach, **and** raw access to every visible reference is refused where the asker lacks it.
- **"disclosures"** is the scorer's count of such references: foreign refs, forbidden markers, open raw access, and visible
  references whose raw access was never checked. The last kind is counted conservatively as a disclosure.
- **Gate (§8):**
  - The scored number of a system run is published as full-system accuracy only if G1–G10 pass.
  - Ablation rows are expected to fail the gate their ablation targets.
  - Central-baseline runs have no coordinator, so the gate does not apply.
