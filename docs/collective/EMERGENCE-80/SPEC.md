# EMERGENCE 80%: the frozen benchmark specification and seed policy

Committed alone, before any design, code or development run for the 80% work, on the deployment branch at
`ce79909`. It changes only by a dated amendment below the last line, never after a held-out evaluation has started,
and never in a way that lowers a bar. Everything here answers the founder's directive of 2026-10-14 ("EMERGENCE 80%
architectural directive"), sections 1, 2, 5 and 6 and deliverable 5.

## 1. The metric

**The one metric is `recall_all` of the end-to-end benchmark**, exactly as `research/e2e_ingest/harness.py`
computes it in `discovery()` (`harness.py:1250-1345` at `ce79909`):

- `recall_all = found / gold_discoverable` for one world (`harness.py:1336`).
- A pattern is **found** when the fabric holds an active note with status `supported`, whose lineage check held
  (`lineage_ok`), whose metadata key (entity, chain predicate) and snapshot week map to that pattern under the labels
  doc (`candidate_label`, `research/routing_spike/world.py:136`; `harness.py:1288-1294`).
- `gold_discoverable` is the world's own count of Gold-discoverable patterns (`make_gold`,
  `research/mycelic/corpus.py:759`). At 200 users with seed 0 it is 40: 22 common and 18 rare.

`recall_common`, `recall_rare`, the decoys accepted by class, `background_supported` and the candidate counts are
reported beside it, as `discovery()` writes them. None of them replaces `recall_all`.

**What may not change during this work (the ground truth and the score).** These stay byte for byte as they are at
`ce79909`:

- the generator: `research/mycelic/corpus.py` and `research/mycelic/org.py`;
- the labels: `research/e2e_ingest/emergence_world.py`'s `generate`, `gold_labels` and the constants they read;
- the scoring: `Harness.discovery`, `candidate_label`, and `report.py`'s recomputation of `recall_all`
  (`report.py:486`, `report.py:816`).

The build adds a test that pins the sha256 of these sources. Any architectural change goes through the pipeline that
produces the fabric's notes, never through what scores them.

**Which path counts.** Only the real `run` job counts: `python -m research.e2e_ingest run`, in process mode as CI
runs it, with every leg real:

- one NeuralGraph per site;
- Tesseract routing;
- NATS JetStream;
- the site verifiers;
- the Mycelic fabric.

The simulator's columns (`simulated: true`) and modelled latencies never count toward the gate. Every reported
figure says whether it comes from real processes, simulated model behaviour or modelled latency.

## 2. The acceptance gate

The gate is applied once, to the frozen candidate, on the **20 held-out worlds at 200 users** (section 3). Let
`r_i` be `recall_all` of held-out world `i`. A world counts with weight 1 whatever its `gold_discoverable`.

1. **Recall.** The mean `r̄` of the 20 values is at least 0.80. The lower end of the two-sided 95% Student t
   interval, `r̄ − t(0.975, 19) · s / √20` with `t(0.975, 19) = 2.093` and `s` the sample standard deviation of the
   `r_i`, is also at least 0.80.
2. **Privacy.** On every one of the 20 worlds, every privacy check of the run passes. That covers the existing ones
   and the checks the amended architecture adds for its own guarantees (section 5). One failure on one world fails
   the gate, whatever the recall.
3. **No degeneracy.** The candidate's decoy acceptance, all five classes together, is no higher than the baseline
   code's on the same 20 worlds; both are run and both are reported. A recall bought by accepting decoys does not
   pass.

The following are reported beside the gate and never replace it:

- a world-level percentile bootstrap of `r̄`: 10,000 resamples of the 20 worlds, generator `numpy.random.default_rng`
  with seed 20261014;
- the pooled Clopper-Pearson interval on found over discoverable. It ignores the clustering by world and is shown
  for reference only;
- `recall_common` and `recall_rare`, precision (supported notes on true patterns over all supported notes), decoy
  acceptance by class, latency, compute and the privacy exposure measures of section 5.

**Failed worlds.** A world whose run fails counts as `r_i = 0` and is reported; it is never dropped and never
replaced by another seed. There is one exception: if the runner was lost before the world's run began (checkout,
install, runner loss), the same seed may be re-run once on the same commit. That is said in the report.

## 3. Seeds

Every world is the generator's world at a seed. A seed belongs to exactly one of three sets.

| Set | Seeds | Use |
|---|---|---|
| **Development** | 0 to 19, at any size | Free for diagnosis, ablations and tuning. Seed 0 at 200 users is the recorded baseline (CI run 38425113336; 13 of 40). |
| **Preliminary** | 5 seeds derived from the candidate-freeze commit | One preliminary validation of a frozen candidate |
| **Held-out** | 20 seeds derived from the final-freeze commit | The one evaluation the gate reads |

**Derivation.** For a set named `S` and a commit `C`, take
`int(sha256("EMERGENCE-80 " + S + "|" + C + "|" + str(j)).hexdigest()[:8], 16)` for `j = 0, 1, 2, …`. Skip any
value that is a development seed or already in an earlier set, until the set is full. `S` is `preliminary` or
`held-out`; `C` is the full 40-character sha of the freeze commit. Nobody can know these seeds before that commit
exists. A script that prints them is part of the build, and the report prints its output.

**Rules.**

- No preliminary or held-out seed is generated, inspected or run before its freeze commit is pushed.
- Each freeze commit is evaluated once; the first run is the result.
- A candidate that fails is recorded with its seeds and figures. A new candidate needs a new freeze commit, which
  gives new seeds, and the earlier result stays in the record.
- After a failed preliminary validation, its 5 seeds become development seeds.
- Freezing several commits in a row to pick among their seeds is forbidden. Every freeze commit pushed is listed in
  the report.

## 4. Sizes and the scaling tracks

- **200 users** is the gating size (section 2).
- **600 and 2,000 users.** The frozen candidate runs the 5 preliminary worlds at each size through the same real
  `run` job. The results are reported with their intervals as non-gating evidence. If CI's limits stop a size, the
  report says which limit and proposes a separately approved plan (directive section 6).
- **10,000 and 50,000 users** stay a separate track: the simulator, labelled `simulated: true`. They are never
  described as a live end-to-end run.

## 5. What else the evaluation must carry

- **Traceability.** For every run: the source revision, the configuration (k = 3, the question budget, `top_n`, `m`,
  the grace weeks), the commands, the logs and the report with its sha256.
  - The CI artifact host is not reachable from the agent's sandbox. Reports are read from the job log's report block
    (the `get_job_logs` tool) and checked against their sha256. The founder can download the artifacts from the run
    page.
- **Privacy exposure.** The amended architecture states what each site discloses and adds a check for each guarantee
  it claims: what leaves a site, what HQ learns, and the attacks it resists (differencing, repeated thresholds,
  adaptive queries, intermediate summaries).
  - k = 3 is the minimum disclosure threshold and is never lowered.
  - Raw records never leave their site, and nothing reads raw notes centrally.
  - No externally hosted model is used.
- **Process.** Diagnosis → architecture proposal → three independent adversarial reviews → amended design →
  implementation → tests → development-seed ablations (each lever alone, then combined) → frozen candidate →
  preliminary validation → final freeze → one held-out evaluation → report.
  - A held-out outcome never tunes anything.
- **If 80% cannot be reached.** If the diagnosis shows that `recall_all` ≥ 0.80 cannot be reached under these
  invariants (for example, because too few rare patterns have enough witnesses anywhere for any protocol to disclose
  them at k = 3), that is written down with its proof. The invariants are not relaxed.
