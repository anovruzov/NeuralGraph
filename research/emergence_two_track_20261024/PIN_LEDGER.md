# PIN_LEDGER.md — every live-code digest, what invalidated it, and what a re-pin would write

*The Gate-0 session's independent evidence ledger is `frontier/PIN_LEDGER.md`; it reaches the same conclusion on the
three digests (different records, do not merge) and classifies the eight `e2e_ingest` failures as the c8 fixture
mismatch. The two were written separately and agree.*

**Status: `LIVE_CODE_HASHES_R002` is stale and `test_the_code_hashes_are_the_pinned_ones` is red, on purpose.**
Per the founders' decision of 2026-10-24: preserve the failing evidence, document which intentional change
invalidated each digest, and re-pin only on a validated tree under review. Nothing in this file has been
applied to the code.

I had already re-pinned it before that decision arrived. **The re-pin has been reverted**; what follows is the
documentation it should have been, and the values a reviewed re-pin would write.

## 1. The two kinds of pin, which is the whole reason this keeps happening

| kind | example | reads | can it drift? |
|---|---|---|---|
| **recorded** | `CODE_HASHES` | `effort.json`, a committed artifact | no — it is a provenance record of what B4b measured |
| **live-code** | `LIVE_CODE_HASHES_R002`, `DETECT_SHA256`, `report.SPIKE_CODE_HASH` | the working tree, hashed at test time | **yes, by design** — that is the alarm |

A live-code pin is a tripwire: it fires whenever the code under it moves, so that a deliberate change is forced
to be declared. It only works if *every* such pin is re-pinned in the commit that moves the code. Three of them
exist and no commit has ever touched more than one.

## 2. `LIVE_CODE_HASHES_R002` — stale, preserved

Five digests, each over a different glob set, all invalidated by the same three deliberate Track-B v4 changes:

| digest | glob set | which changed files fall inside it | current tree value (a re-pin would write this) |
|---|---|---|---|
| X1 | `EVAL_CODE_FILES` = `detect/*.py`, `edge/*.py`, `packs/*.py`, `evaluate/*.py`, `stats.py`, `jsonio.py`, `schemacheck.py`, `experiments/common.py` | `detect/detectors.py`, `packs/loader.py` | `150ae5fa432a05d53a64fbfcef8adbbc3b859f3873ec22630e36a13cb7231c49` |
| E1 | `edge/extract.py`, `packs/{canonical,loader,connector}.py`, `inference/*.py`, … | `packs/loader.py` | `172231c8bfa6abd538a12f1297729b92ce9dfd5680768310094876d97e1700ff` |
| E2 | `EVAL_CODE_FILES` + `pushdown/*.py` + `inference/*.py` + `leakage.py` | `detect/detectors.py`, `packs/loader.py`, `pushdown/gate.py` | `f8e866ae87f89c1fbb1dbe6035b43ad6e44bcfefd1abe12ecaa5b6808748ee8d` |
| openfda_replay | `EVAL_CODE_FILES` + `connectors/openfda.py` | `detect/detectors.py`, `packs/loader.py` | `643011d7b327ed612e743df0df8a011e735746bf5f83219c29653b9e4ea053eb` |
| X5 | `collective/*.py`, `detect/*.py`, `edge/*.py`, `evaluate/*.py`, `followup/*.py`, `inference/*.py`, `packs/*.py`, `pushdown/*.py`, … | `detect/detectors.py`, `packs/loader.py`, `pushdown/gate.py` | `9ee8fa5b0aea7bd0a07731feff30affc371c265518b548a405b756310a07e18b` |

The three deliberate changes:

| commit | change | files |
|---|---|---|
| `324b9d2` | EMERGENCE-Next **N2** — roots only from sites whose own roots reach k (opt-in) | `pushdown/gate.py`, `packs/loader.py` |
| `5264760` | **N1** hardening — near-duplicate roots and weak-site corroboration after adversarial review | `edge/`, `pushdown/gate.py` |
| `9857556` | opt-in **`rule_snapshots`** — the rule-layer candidate branch | `detect/detectors.py`, `packs/loader.py` |

So this pin has been stale since `9857556`, and `tests/mycelic` red for the whole of the Track-B v4 effort.

## 3. The other two live-code pins

| pin | state | history |
|---|---|---|
| `DETECT_SHA256` (`tests/mycelic/test_collective_pushdown.py`) | **current** | was stale from `9857556`; re-pinned at `ce0e357` with the reason beside the constant |
| `report.SPIKE_CODE_HASH` | **current** | re-pinned three times — `b7b1a18` (`40f2d713`→`4352c2cf`), `8981505` (N2), `d88c150` (`rule_snapshots` → `0e05bc00`) |

`SPIKE_CODE_HASH` is the only one that was maintained, and the two commits that maintained it are precisely the
commits that should also have touched the other two.

## 4. The open question: do the overlapping digests enforce distinct requirements?

Not to be answered by deleting one. What is actually known:

- **They cover overlapping but non-identical file sets.** `SPIKE_CODE_HASH` covers 142 files of the routing
  spike. `LIVE_CODE_HASHES_R002`'s five globs cover `mycelic/collective/` broadly, including `pushdown/`,
  `inference/`, `followup/`, `leakage.py` and `connectors/openfda.py`, which the spike set does not.
  Neither is a subset of the other.
- **They answer different questions.** `SPIKE_CODE_HASH` is stamped into every e2e report (`run.spike_code_hash`)
  and checked by `c7_guards`, so it ties a *result* to the code that produced it. `LIVE_CODE_HASHES_R002` is
  asserted only in the test suite, so it ties the *tree* to a declared state. A result-provenance stamp and a
  tree tripwire are not the same requirement.
- **Therefore the likely right answer is to keep both and derive neither** — but to make the obligation
  enforceable rather than remembered.

The enforcement now exists and does not require anyone to remember: `LiveCodePinTests` names all three, asserts
the ones that should be current *are* current, and asserts the stale set is exactly what this ledger names. A
fourth pin going stale fails. A stale pin being repaired without updating this file also fails.

## 5. What a reviewed re-pin should do, when it is authorised

1. On a tree whose other suites are green (`tests/e2e_ingest`'s eight failures are fixed on
   `research/report-integrity`; see `BENCHMARK_INTEGRITY.md`).
2. Write the five values from section 2 above, in a commit that does nothing else.
3. Record in that commit message the three changes from section 2 and that they were intentional.
4. Decide the naming convention: the constant's own history tags it with the change that last moved it
   (`_R002`), so the successor would be `LIVE_CODE_HASHES_TB_V4`. This is cosmetic and should be decided with
   the re-pin, not before it.
5. Remove its row from `KNOWN_STALE` and from section 2 of this file in the same commit, or
   `test_exactly_the_pins_the_ledger_names_are_stale` will fail — which is the intended behaviour.

## 6. What is *not* in this ledger

`c8`'s broken fixture is not a pin and is not covered here. `tests/e2e_ingest`'s eight failures were the
unfinished half of `ce0e357`'s own c8 rework — the shared `green_report()` fixture never grew a
`table_row_diff`, and the mutation table still named the assertion the rework removed. Those are repaired, not
preserved: a broken test fixture is a bug, not evidence of anything.
