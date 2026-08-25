# Closeout report — 2026-08-25

Autonomous close of the free scientific questions and the paper package.
**Zero paid API calls.** Nothing pushed.

| | |
|---|---|
| starting commit | `91fa991a393a5b85596604b90c2e68791d5de318` |
| ending commit | `705254d` |
| branch | `locomo-six-fix-loop` |
| dirty tree | clean apart from gitignored `demo/.cache/` and one user edit to `HANDOFF.md` |
| tests | **346 passed, 1 skipped, 3382 subtests** (Python 3.11.14) |
| paid API calls this session | **0** |

## Executed commands

```bash
/opt/homebrew/bin/python3.11 -m pytest -q
/opt/homebrew/bin/python3.11 -m tools.build_paper_tables --check
/opt/homebrew/bin/python3.11 -m tools.build_paper_figures --check
/opt/homebrew/bin/python3.11 -m tools.build_paper_figures --outdir docs/paper/figures
/opt/homebrew/bin/python3.11 -m tools.render_paper
(cd NeuralGraph/coordination/artifacts && shasum -a 256 -c SHA256SUMS)   # 4/4 OK
(cd evaluation/artifacts && shasum -a 256 -c SHA256SUMS)                 # 3/3 OK
/opt/homebrew/bin/python3.11 -m evaluation.improvement_loop.qwen3_compare --model qwen3:8b --mode {think,no_think} --set smoke
/opt/homebrew/bin/python3.11 -m evaluation.improvement_loop.qwen3_compare --model qwen3:8b --mode no_think --set diagnostic60
ollama show qwen3:8b ; ollama --version   # 0.32.13
```

Tables and figures both regenerate **byte-identically**.

## Artifact digests (SHA-256, first 16)

| artifact | digest |
|---|---|
| `q1_gold_format_audit.json` | `f88f7630a45bfb94` |
| `q2_temporal_annotations.json` | `55e771218e71af95` |
| `q3_wrong_selection_audit.json` | `43d498ab2c27285f` |
| `q4_support_filter.json` | `7f1542307d267f8e` |
| `q6_honest_ceiling.json` | `632a31ae1b91a184` |
| `q7_protocol_audit.json` | `26faeeb436194abd` |
| `q8_power_analysis.json` | `d37ea250840d9521` |
| `docs/paper/capability_survival.md` | `4bb2e1d3376d4a39` |
| `docs/paper/tables.md` | `c49fe941ce365f9f` |
| `docs/paper/capability_survival.html` | `05f00de6bfa93b80` |
| `figures/figure1_fixture.svg` | `a4a240b5f7036e77` |
| `figures/figure3_pareto.svg` | `a558b0d40e53c53f` |
| `benchmark_sweep30_seed20260813.json` | `3e45ebc05d653507` |
| `scale_seed20260813.json` | `61be1f24d6ef4808` |
| `benchmark_seed20260813.json` | `2d8564e7cd717a23` |
| `experiment_seed20260813.json` | `f712e12d85f8b63a` |

## Answers, Q1–Q8

**Q1 — gold format.** Costs exactly **4 questions**, all wrong: 2 disjunctive,
1 range, 1 justified yes/no. A preregistered grading rule (R1–R3) is **specified
and not applied**; expected recovery 2 questions. `date_relative` golds score
4/4, so relative form is not inherently a problem.

**Q2 — temporal annotations.** Delivered to **15/15** temporal prompts (mean 4.0
per prompt, 54/60 prompts overall). There is no annotation-absent group. The
rendering hypothesis is **falsified**; residual failure is reasoning. Five
present-but-ignored rows are heterogeneous, so **no prompt change is warranted**.

**Q3 — `wrong_selection`.** **A residual bucket, not a mechanism.** Ten rows
resolve into **seven** mechanisms; only **2** are genuinely wrong selection and
**4/10** are fixable by generation. Two were taxonomy errors, one of which
(`q470`) is a case where the model is right and the grading contract is wrong.

**Q4 — support filter. PASS.** `containment(1.0)`, decided from evidence alone,
beats the v1 baseline on all three gated metrics: unsupported **16 → 13**,
precision **0.5254 → 0.5972**, recall **0.5361 → 0.6056**, **0 gold-matching
items dropped**. 21 tests; 4/4 injected mutations caught after strengthening.

**Q5 — stronger answerer. FAIL.** Qwen3 8B gains recall (+0.031) but unsupported
items go **1 → 15** (token-wise 1 → 12, item length unchanged, so real) and
exact-set match falls. The Q4 filter cuts unsupported to 6 but leaves Qwen3 worse
than baseline on every metric. Paid judging **not justified**; frozen command
prepared, unexecuted.

**Q6 — honest ceiling.** Accounting closes: **22 evidence-present correct + 2
correct without retrieved evidence = 24**. Evidence-supported ceiling **48/60 =
80.0%** (a bound on the answerer, **not a score**); empirical ceiling **50/60 =
83.3%**. Conversion is **45.8%**; 80% would need **95.8%** — a 50-point uplift.
**80% is not a defensible target.**

**Q7 — protocol. Resolved.** All **446** category-5 questions carry an
`adversarial_answer` field with `answer = "None"`; **445** are the excluded set.
The 1,540 subset is exactly *the answerable questions*. **Recommendation: adopt
the 1,540 four-category subset as canonical and label it explicitly.** A 1,540
score and a 1,986 score must never be compared.

**Q8 — is n=60 adequate? NO.** Minimum detectable effect **12 questions = 20
points** at 80% power. A true 5-point gain is detected **17.5%** of the time.
Judge instability of 4.5% raises the sample needed for an 8-point effect from
**71 to 242**. **Further scored rounds on this set are diagnostic, not
confirmatory.**

## Integration decision

### `IMPLEMENT_SUPPORT_FILTER`

Implemented in `RunConfig` behind `support_filter` / `support_threshold`, **off
by default**. Filter off keeps config hash `cd9098dfbdd0ad06` so every cached v1
answer stays valid; filter on gives `be8b411e35ac4c2a`, correctly invalidating
the cache. 21 adversarial tests, mutation-verified.

**No judged score improvement is claimed.** Q8 establishes the evaluation cannot
detect an effect below 12 questions, and the filter's judged effect is unmeasured.

## Score

**Current judged score remains 24/60 = 40.0%** on the frozen diagnostic set. **No
new judged evaluation was run.** Every number added this session is deterministic
or unpaid.

## What the coordination paper truly proves

Replica count does not buy survival: full replication at 8 records scores 0.00
against a lineage-root failure. Lineage-aware repair reaches 0.778 at 4 records
and 5,448 bytes against full replication's 0.667 at 9,877 — higher survival at
roughly half the storage. Minimum failure-domain cut predicts survival: only
`oracle_min_cut` survives a single worst-domain failure. Every verdict is
invariant across K ∈ {2,3,5,8}, H ∈ {2,3}, and the headline reproduces on real
SQLite with lineage derived rather than declared.

Reported against interest and retained: no distributed strategy survives network
partition (0.00 across all six); lineage-aware repair loses to `source_count` in
the `node_failure` I=1 cell.

**In process only.** No NATS, no JetStream, no transport, no wall-clock latency.
The eight strategies are placement/repair policies, **not** architecture
families.

## Unimplemented

Transport; organizational abstraction hierarchy; cross-cutting scope hyperedges;
learned routing; hyperbolic embeddings; active lineage diversification; the
matched architecture comparison. All marked *proposed* in the manuscript.

## Remaining paid experiment

Judging the 60 cached Qwen3 answers: **60 requests, ~12,600 tokens, ~$0.036
maximum**. **Not recommended** — Qwen3 failed its unpaid gate. Blocked regardless
by the ledger (48 consumed against a 40-request ceiling,
`paid_calls_permitted: false`).

## Human decisions required

1. **Authorship / ownership** — flagged open in `state.md`. **Not guessed.**
2. **PDF render** — no toolchain installed; open
   `docs/paper/capability_survival.html` and print to PDF.
3. **Grading contract (Q1 R1–R3)** — specified, not applied; adopting it requires
   re-scoring the baseline identically.
4. **Budget ceiling** — only a human may raise it.
5. **Canonical protocol** — recommendation is the 1,540 subset; needs sign-off.

## Paper outputs

| | |
|---|---|
| manuscript | `docs/paper/capability_survival.md` |
| tables | `docs/paper/tables.md` |
| figures | `docs/paper/figures/figure1_fixture.svg`, `figure3_pareto.svg` |
| rendered | `docs/paper/capability_survival.html` |
| PDF | **BLOCKED** — no renderer installed |

## Submission readiness: **BLOCKED**

Not on evidence — the coordination result is complete, reproducible and
figure-backed. Blocked on: authorship undecided, and PDF unrenderable without a
toolchain. Both are human actions, neither is a scientific gap.
