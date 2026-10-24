# Pin ledger: which intentional change invalidated which pin

Founder decision (2026-10-24):

- Do not re-pin yet. Preserve the failing evidence, and record which intentional change invalidated each pin.
- `research/report-integrity` owns pin governance and required-gate reporting.

This file records evidence only. **No pin was changed for it.** My local re-pin commits are held back (§3).

How it was measured: each failing test was run at every commit in the local series (`28275759` and the six commits
on top of it), plus a walk back through history for the x3 pin. The test runs found 8 genuine failures. A further 16
errors came from running the live tests without `MYCELIC_NATS_SERVER_BIN`, which was my mistake; those are being
re-run with the broker.

## 1. The three overlapping digests — they check different things

| pin | where | what it checks |
|---|---|---|
| `LIVE_CODE_HASHES_R002` | `tests/mycelic/test_collective_x3.py:94` | one digest per experiment file set (X1 = `EVAL_CODE_FILES` = `detect,edge,packs,evaluate/*.py`; E1; E2 and `openfda_replay` add `pushdown/*.py` and more; X5). The X3 evidence record (`effort.json`) says which code produced which experiment's results. |
| `SPIKE_CODE_HASH` + `SPIKE_CODE_FILES` | `research/e2e_ingest/report.py`, narrated in the M13 section of the e2e settings doc | c7 guard: the e2e run's code (142 files, `CODE_PATTERNS`) equals the reviewed tree. `test_workflow::test_m13` also requires the value to appear in the M13 narration. |
| `DETECT_SHA256` | `tests/mycelic/test_collective_pushdown.py:100` | per-file digest of `detect/*.py`, guarding the pushdown experiment's detector |

They overlap on files but not in purpose. R002 ties *experiment records* to code. SPIKE ties *the e2e run* to code.
DETECT ties *one experiment's detector* to code. Merging them would erase which record each one vouches for. My
recommendation: keep all three, and make every re-pin commit list all three digests together.

## 2. Ledger

| commit | change (intentional) | pins / contract tests it broke |
|---|---|---|
| `fa609166` | per-site model judge | — (last commit where the x3 pin passes) |
| `22cbd0ed` | N1 weak-site corroboration (`pushdown/*.py`) | **R002** (E2, `openfda_replay`) — first red; never re-pinned since |
| `7bbaad1d`, `494650c7` | near-duplicate roots and N1 hardening (`edge/*.py`, `pushdown/*.py`) | R002 again (X1, E2, …); SPIKE re-pinned in `b3025fb6` and `f830a57c` |
| `7eabc4c3` | N2 (`gate.py`, `loader.py`) | R002; SPIKE re-pinned in `b4eeb7bb` |
| `73a58bdc` | `rule_snapshots` (`detectors.py`, `loader.py`) | R002; SPIKE re-pinned in `28275759` without updating the M13 narration → **`test_m13` red at `28275759`**; **DETECT red from here** (`test_detection_is_byte_identical_to_its_pin`: passes at `22cbd0ed`, fails at `73a58bdc`, `28275759`, `26ab44f2`) |
| `d50bee56` (patch 0014) | world guard independent of seed | fixes `test_m13` (narration updated to `0e05bc00…`) |
| `d1019af0` (patch 0015) | c8 adds `world_draw_is_reported` | **contract tests:** `test_harness::CheckTests` ×3, `test_measure::C5Tests`, `test_measure::SummaryTests`. Their "green report" fixture fails c8's new item. This is a fixture/contract mismatch, not a digest. |
| `26ab44f2` (local) | v5 re-ask (`detectors.py`, `loader.py`) | DETECT, SPIKE, R002 (X1, E2, `openfda_replay`) |
| `ae97d92e` (local, **held**) | re-pins DETECT (covering `rule_snapshots` and v5) and SPIKE | DETECT green again; SPIKE re-pinned without the M13 narration, so **`test_m13` red again** |
| `8005853f` (local) | opt-in site edges (`site_graph.py`, `site_process.py`, 6 tests) | SPIKE; **e2e collected-count floor** (159 → 165, `test_workflow::…floor…may_only_rise`) |
| `3d4c3b07` (local, **held**) | re-pins SPIKE for edges | — (`test_m13` still red; floor still red) |

## 3. What is held

The four local commits (`26ab44f2`, `ae97d92e`, `8005853f`, `3d4c3b07`) are not exported. When they go to
`report-integrity`:

- the code commits (v5, edges) go as they are;
- the re-pin commits are dropped;
- `report-integrity` re-pins once, on the validated tree: R002, SPIKE together with its M13 narration, DETECT, and
  the e2e floor, naming every commit above.
