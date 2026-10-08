# Oracle audit of the Mycelic simulator (v5)

> **Status after adoption (protocol amendment A1):**
> * The three observable replacements are now the **default for every architecture** (`ops.OBSERVABLE` all on;
>   `MYCELIC_OBSERVABLE=none` restores the legacy paths for reproducing pre-v5 artifacts).
> * Final seeds are sealed at every scale (finding P1 closed), and `corpus.build_corpus` checks too.
> * `test_leakage` runs in CI next to `test_mycelic`.
>
> The audit text below describes the code as audited, before adoption.

Audited code: branch `claude/vnext-accuracy-70-percent-gsjg8m` at `2657ea5`, plus the uncommitted audit diff that adds
`ops.OBSERVABLE`, `systems.surface_pred`, `test_leakage.py` and `leakage_allowlist.json`. The diff does not change
any default behaviour: registers, compute and call counts are byte-identical to `2657ea5` for nine architectures
on two worlds (10k/590 and 400/591), and `test_mycelic` passes unchanged.

Every number below was measured on TUNE seeds 580–599 at 10,000 users, `back-loaded` allocation. No sealed world
was built.

**Hidden information** means anything the generator knows that a deployed system would not observe:
* the gold objects (`Pattern`, `corpus.patterns`, `Gold`, `p.real`, `decoy_type`, `rare`), planted chains, families
  and contradiction pairs;
* the per-record fields `kind`, `veracity`, `event`, `group`, `facet`, `superseded` and `salience`;
* the true `pred`, `anchor`, `t`, `polarity` and `aux` whenever they are read by anything other than a simulated
  operator that corrupts them;
* the extraction operator's `near_miss` table, the `hallucinated` and `spurious` flags, and anything derived from
  these.

## 1. Summary

* **Three shortcuts** (§2). These are places where a decision reads hidden information outside a disclosed operator.
  Each now has an observable replacement behind `ops.OBSERVABLE` (default off). Paired measurement on TUNE 580–599:
  * **S1**, the A/A2 lexical prefilter on the true `pred`, and **S3**, H's routing on the `hallucinated` flag, change
    **nothing**. The registers are byte-identical on 20/20 seeds for A, A2, H_mycelic_full and H_mycelic_prev, and on
    seed 585 for every other hierarchy variant. On this generator's text the observable rule computes exactly the
    same thing.
  * **S2**, local re-extraction finding notes by the true `anchor`, sits in a mechanism that is **off in every frozen
    configuration**. Its observable form reads 21% more notes. Its effect on found is not established
    (+0.030 [+0.003, +0.056], sign test 11/5, p = 0.21).
  * **No committed or v4 number depends on a shortcut.**
* **No architecture bypasses a simulated operator** (§5). Every system's claims come out of the same extraction
  operator at its tier, and every synthesising system runs the same verification coins at the kernel tier. The
  differences are the declared tier allocation, tuning (link timing, own ranker), and the three shortcuts.
* **The benchmark depends on oracle-assisted simulation through its operators, not through its decisions** (§4, §6).
  Every system is scored through an extraction operator with these properties:
  * it reads the true tuple, copies **days and polarity exactly at every tier**, and hands every system the **true
    event id as a near-duplicate signature** (92% of records);
  * its errors are independent coins;
  * hallucinations carry no evidence;
  * the kernel knows the causal schema verbatim, and the rankers are fitted on simulator gold.

  These carry the independence, D2/D4/D5, lineage and ranking results, and they are what a live run must earn. Two
  points follow:
  * they flatter multi-witness aggregation (H, B4) more than single-pass reading (A2);
  * A2's own coverage rests on an exact lexical prefilter.

  So **the sign of H vs A2 under live operators is not established by the simulator**.
* **Other findings** (§3):
  * **P1**: the final-seed seal can be bypassed. `build_world(9_999, s)` *is* the sealed `build_world(10_000, s)`.
  * **D1**: the published claim that "no system is ever handed the event id" is false as worded.
  * **L1**: two latent channels exist (event-id magnitude, record position). No decision uses them, and the new tests
    guard them.
* **New automated audit:** `python3 -m unittest research.mycelic.test_leakage`. It runs 32 tests in about 17 s. It
  passes on the current code, with S1, S2, S3 (×2) and P1 as expected failures. `test_mycelic` still passes
  (30 tests).

## 2. Shortcuts (decisions reading hidden information outside a disclosed operator)

Measured paired on TUNE seeds 580–599 at 10,000 users (`back-loaded`). For each architecture, *current* is the
committed behaviour and *observable* has every `ops.OBSERVABLE` switch on. Δ is observable minus current, with its
95% bootstrap CI.

| ID | site | what reads hidden information | affected | observable replacement (switch) |
|---|---|---|---|---|
| **S1** | `systems._lexical_index` (`systems.py:1618`) | The "records whose surface form matches a causal predicate" prefilter is built by sorting on the record's **true `pred`**. | A_flat_rag, A2_chunked_ctx. B computes the index but does not use it. | `systems.surface_pred`: find the `PRED_SURFACE` phrase in each note's rendered tokens, then build the same stable index (`lexical_index`). |
| **S2** | `Hierarchy._reextract` (`systems.py:876`) | "Notes that name this entity" is looked up by the record's **true `anchor`**. That misses notes naming the entity as the second (`aux`) mention, and under-charges the re-read. | H-family with `local_reextract=True`. This is off in every frozen configuration; it was rejected in vNext. | Exact token match of the entity name in the agent's own rendered notes. The extra notes are read and charged (`reextract_lookup`). |
| **S3** | `ops.unsupported` (`ops.py:106`), called from `_weak_targets`, `_question_round`, `_completion_round`, `_families`, `annotate_anchor_context` and `apply_ranker_to` | Descent, question and completion targets, family grouping and the ranker's gate skip candidates by the simulator's **`hallucinated`** flag. | Every hierarchy variant (C–E only in family grouping). A2 and B4 have no such stage. | "Unsupported = cites no evidence object" (`unsupported_by_evidence`). |

| architecture | registers identical | found current → observable | Δ found [95% CI] | decoy acc. Δ | AP Δ | compute ratio |
|---|---|---|---|---|---|---|
| A_flat_rag (S1) | 20/20 | 0.705 → 0.705 | 0 | 0 | 0 | 1.0000 |
| A2_chunked_ctx (S1) | 20/20 | 0.751 → 0.751 | 0 | 0 | 0 | 1.0000 |
| B4_central_triage (control; no site) | 20/20 | 0.550 → 0.550 | 0 | 0 | 0 | 1.0000 |
| H_mycelic_prev (S3) | 20/20 | 0.384 → 0.384 | 0 | 0 | 0 | 1.0000 |
| H_mycelic_full (S3) | 20/20 | 0.694 → 0.694 | 0 | 0 | 0 | 1.0000 |
| H_mycelic_full + `local_reextract` (S2, S3) | 0/20 | 0.674 → 0.704 | +0.030 [+0.003, +0.056]; sign 11/5, p = 0.21 | +0.018 [−0.014, +0.048] | −0.009 [−0.033, +0.014] | 1.0036 (re-read notes 2,364 → 2,866, +21%) |

On seed 585, the registers, compute and call counts are also identical with the switches on for C, D, E, F, G,
H_mycelic_lean, I, J, B, B2, Y and Z2.

**Why S1 and S3 measure zero.**
* *S1.* The rendered text is generated from `PRED_SURFACE[pred]`, and no filler, entity name or background phrase
  contains a causal phrase. So a surface-phrase match agrees with the true predicate on **100%** of records (checked
  on all ≈330,000 records of each of seeds 580–583). The prefilter is therefore an exact keyword retriever on this
  text: it is complete and has no false hits. That is the real assumption (§6, row 2).
* *S3.* The hallucination operator builds every invented candidate with no evidence objects. No real candidate has
  none, so "no evidence" and the flag agree on every synthesis output.

In both cases the shortcut gave the system information it could have computed itself. The issue is that the code read
the answer rather than computing it, so any change to the generator or the operator could silently turn it into a real
leak. Both cases are now guarded by test (b2) / (c).

**Why S2 is not exact, and what its Δ means.** The token match also returns notes in which the entity is the aux
mention. Re-extracting those yields claims about their *own* anchor, which are discarded unless an entity slip maps
them onto the queried entity. On seeds 590 and 591 the observable lookup re-read 20% more notes, but recovered only
1–2% more objects about the queried entity (1,107 → 1,119; 920 → 936). Most of the found shift is therefore the
re-seeded noise stream (more extraction draws), not information; the sign test does not separate it from zero. Either
way, re-extraction stays a rejected, off-by-default mechanism. If it is ever re-evaluated, it must use the observable
lookup, which pays for the extra reads.

## 3. Other findings

**P1. The final-seed seal can be bypassed through an alias scale (protocol, not oracle).**
* `protocol.check_world` refuses only the exact pairs (10,000, 3000–3029) and (50,000, 3000–3002).
* `org.build_org` uses its target only through `rint(w_region × target)`, and `build_corpus` depends only on the org
  and the seed. So target **9,999** builds the same org, and therefore the same world, as 10,000; **50,001** does the
  same for 50,000. Those calls are not refused and not logged.
* Verified on TUNE seed 585: `build_world(9_999, s)` reproduces every record field and every pattern of
  `build_world(10_000, s)`. No final seed was used.
* `live_tasks.build` also calls `build_org` / `build_corpus` directly, without `check_world`. Its default seed is
  900 at 2,000 users.
* Fix: refuse seeds 3000–3029 at **every** scale (or key the seal on the realised org) unless `MYCELIC_FINAL_EVAL=1`,
  and route `live_tasks` through `build_world`.
* `test_leakage.TestSealedSeeds.test_check_world_blocks_alias_scales` is an expectedFailure until this is fixed.

**D1. The published description of echo detection is inaccurate.** The `Corpus.tokens` docstring and
`docs/MYCELIC_ENTERPRISE.md` ("What is in a record") say that "no system is ever handed the event id". The extraction
operator's signature *is* the event id for 92% of records (O1c). Decisions use it only as an identity, which test (a)
checks. On this text a text signature recovers event identity exactly (§4), so the substance is not wrong, but the
sentence is. It should say that echo detection is simulated by an operator that sees the event id. In the same way,
the `ops.py` leakage contract cited an `assert_no_leak` in `eval.py` that does not exist, and it listed `event` among
the fields no operator output may expose. This diff corrects both code docstrings (`Corpus.tokens`, the `ops.py`
header) and points them here and at `test_leakage.py`. The sentence is still in `report_text.py:90`, and so in
`docs/MYCELIC_ENTERPRISE.md:456`. It is left for the next regeneration, so that no committed document changes.

**L1. Latent channels.** No decision uses these today:
* *Event-id magnitude.* Background events are numbered from 0. The background contradicting ("false") records get ids
  in [n, 2n), where n is the number of background records. Planted facet and decoy records get ids ≥ 2n + 10. Any
  decision that read a signature's *value*, rather than using it as an identity, would separate planted records from
  background. Test (a) fails on any such read.
* *Record position.* After the stable sort by user, every user's planted records sit **after** that user's background
  records. A decision that used record-id order inside a user's slice would separate them. No decision does: the
  evidence order is score order, chunk-shuffle order or user-major order. The static scan cannot see this channel; a
  generator revision should randomise both.

**M1. Unmetered sketch (cost, not oracle).** `user_extract` computes the `LocalBaseRate` / `ForeignScore` sketch size
(`ul.sketch_tokens`) and never meters it. It is LLM-free, but it is a global statistic broadcast to every user agent.
It feeds the importance score of H, B2, B4 and Y alike.

## 4. Simulated operators: what each reads and what it assumes

Every architecture reaches the corpus only through these operators. An operator may read the truth because it
*simulates* a model, and the noise it adds is the whole of the simulated model's fallibility. So the strength of each
assumption below sets the difficulty of the benchmark for every system. Tier values are for the `back-loaded`
allocation: users and teams `small-7b`, departments `mid-14b`, sites `mid-32b`, regions `frontier`, kernel
`frontier-plus`.

| # | operator (site) | reads | real capability it stands for | how strong the assumption is |
|---|---|---|---|---|
| O1 | **claim extraction**, `ops.extract` | true `pred`, `anchor`, `t`, `polarity`, `event`, `uid`; the `near_miss` table | reading a work note into (predicate, entity, day, polarity) over a closed 50-predicate schema | **Recall** is an independent coin per record per read (small-7b 0.70; frontier-plus 0.96), uncorrelated with how hard the note is, so a re-read is a fresh coin. **Predicate** errors (up to 22% at small-7b and 3% at frontier-plus, less at chain ends) only ever go to a neighbouring link of the same causal chain; background predicates never slip. **Entity** errors (small-7b 16%, frontier-plus 0.5%) are fixed per (author, entity) and only go to one lexically similar name (`near_miss`, same 5-letter prefix). **Spurious claims** (18% of records at small-7b, 1.5% at frontier-plus) get a uniformly random predicate and entity, polarity +, and a unique signature, so each counts as an independent witness. They form a flat noise floor (at 10k with small-7b, about 2 per (predicate, entity) pair) rather than the systematic misreadings that real models repeat across authors. Of the `aux` entity, only the anchor is ever extracted; the extractor always knows which named entity is the subject. |
| O1a | day | true `t`, copied exactly | reading the note's date | **Exact at every tier, including small-7b and the `lexical` pipeline.** The rendered note carries `[dNNN]`, so this matches the text. There are no relative dates, no time zones, no misdated notes. |
| O1b | polarity | true `polarity`, copied exactly | detecting negation and retraction | **Exact at every tier.** The rendered note ends in `not`. No tier misreads a negation, a hedge or a retraction. D5 (stale chain) and contradiction handling stand on this. |
| O1c | **echo signature** `sig` | true `event` (92%); a coarse key (extracted pred, extracted anchor, day÷3) for the other 8% | near-duplicate detection of echoed or forwarded reports | A near-perfect echo detector. Its only errors are the 8% coarse keys, which can merge independent reports on the same entity, predicate and 3-day window, or split an echo group across buckets. On this generator's template text, a deterministic text signature (surface phrase, first entity, filler words minus the paraphrase-noised last word) reproduces event identity **exactly**: 0 false splits over ≈52,000 echo records per world, and ≈50 of ≈303,000 events sharing a signature (seeds 580–583). So the operator is *noisier* than the text it stands for. The live question is whether real echoes (forwards, paraphrased restatements, copied tickets) can be told from independent reports at all. All independence, D4 and top-support results stand on this. |
| O2 | entity-slip table, `ops._near_miss_map` | entity names only | which names a model confuses | Confusions are within a 5-letter-prefix group, one fixed partner per entity. This is the same lexical neighbourhood the D3 decoys are drawn from. |
| O3 | **synthesis checks**, `ops.synthesize` | knowledge-object fields only (operator outputs) | the kernel verifying a candidate chain | Each check is a coin at the tier's probability; when the coin succeeds the check is executed **exactly**: **causal** (0.97 at the kernel) against the true schema `CAUSAL_CHAINS` with exact link order; **temporal** (0.99), exact ordering of exact days; **dedup** (0.97), set cardinality of O1c signatures; **entity linking** (0.96), where failing merges same-stem names; **contradiction** (0.95), a fixed balance rule. The kernel knows the generator's causal schema verbatim, which no real enterprise has. |
| O3h | **hallucination injection**, `synthesize` | none (random) | the kernel inventing an unsupported pattern | Binomial(candidates, p) random chains on random entities (frontier-plus p = 0.025), confidence U(0.3, 0.7). **They carry no evidence and no features**, so they are trivially recognisable as unsupported. Real hallucinations cite plausible evidence. The `hallucinated` flag exists for the evaluator, but H's routing also reads it (S3). |
| O4 | abstraction, `Hierarchy.build_user_layer` / `aggregate_level` | operator outputs | summarising claims upward | Salience noise σ, retention and merge distortion as coins. Hierarchy only (C–J), because only the hierarchy abstracts. |
| O5 | routing, `Hierarchy.descend` | sketch counts of extracted claims | choosing which branch to descend | With probability 1 − `route_quality` the best child is dropped. |
| O6 | question targeting, `_question_round` | candidate fields | asking the right question | With probability 1 − `question_quality` the question goes to a random entity. |
| O7 | provenance re-read, `_verify_evidence` (J only) | evidence records through O1 at the kernel tier, independent entity salt | the kernel re-reading cited notes | An entity-fidelity check of 0.995. |
| O8 | local re-extraction, `_reextract` (off) | O1 at the user tier | an agent re-reading its own notes | The fresh read is an independent recall coin, so it recovers about 70% of first-pass misses at small-7b. Its note lookup is shortcut S2. |
| O9 | fitted rankers, `ops.apply_ranker_to` | candidate features (operator outputs) | a learned judge of candidates | Fitted on **gold labels** of TUNE worlds from the same generator (SCORING), then applied as a decision on observable features. This is legitimate inside the simulator. A deployment has no gold labels for its own enterprise. |

Two further points:
* The statistics behind importance and triage (`BaseRate`, `LocalBaseRate`, `ForeignScore`, the site sketches,
  `central_triage`) read only extracted claims and the org chart.
* `ExtractResult.spurious` is set by O1 and read by no decision; only loss accounting and tests read it.

## 5. Architecture × operator

Rows are the registry in `runner.ARCHS`. The *natural* regime is `back-loaded`; in the capability-matched regime
every column runs at one tier. ✓ means the shared operator, applied at the tier shown. — means the architecture has no
such stage. In every row all synthesis checks (O3) are on unless stated.

| architecture | extraction O1 (tier; what is read) | echo sig O1c | day / polarity O1a/b | abstraction O4 | routing O5 | questions O6 | synthesis O3 (kernel f-plus) | hallucination O3h | verification O7 | ranker O9 | hidden reads outside operators |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A `flat_rag` | f-plus; ≤41.7k records sampled from the **causal-predicate index** | ✓ | exact | — | — | — | ✓ link_time `min` | ✓ | — | shared | **S1** (index on true `pred`) |
| B `long_context` | f-plus; random sample of all records | ✓ | exact | — | — | — | ✓ `min` | ✓ | — | hand logistic | none |
| A2 `chunked_ctx` | f-plus; **every** record in the causal-predicate index (≈74k of ≈328k at 10k) | ✓ | exact | — | — | — | ✓ `min` | ✓ | — | shared | **S1** |
| B2 `map_reduce` | small-7b; all records (shared user layer) | ✓ | exact | global top-K (salience noise) | — | — | ✓ `min` | ✓ | — | shared | none |
| B4 `central_triage` | small-7b; all records (shared user layer) | ✓ | exact | — | — | — | ✓ `min`; triage features | ✓ | — | shared | none |
| C `recursive_sum` | small-7b (shared) | ✓, but dedup **off** | exact | ✓ small budgets | — | — | ✓ temporal & dedup **off** | ✓ | — | hand | S3 (family grouping only) |
| D `hier_nolineage` | small-7b (shared) | ✓, dedup **off** | exact | ✓ | — | — | ✓ lineage & dedup off; `hybrid` | ✓ | — | shared | S3 (family grouping only) |
| E `hier_lineage` | small-7b (shared) | ✓ | exact | ✓ | — | — | ✓ `hybrid` | ✓ | — | shared | S3 (family grouping only) |
| F `hier_retrieval` | small-7b (shared); descents reuse first-pass claims | ✓ | exact | ✓ | ✓ | — | ✓ `hybrid` | ✓ | — | shared | S3 |
| G `hier_questions` | as F | ✓ | exact | ✓ | ✓ | ✓ | ✓ `hybrid` | ✓ | — | shared | S3 |
| H_mycelic_prev (v1 config) | as F | ✓ | exact | ✓ | ✓ | ✓ | ✓ `min` | ✓ | — | hand | S3 |
| H_mycelic_full | as F | ✓ | exact | ✓ | ✓ | ✓ | ✓ `hybrid`, `w_dispersion` 0.8 | ✓ | — | **own** (40 TUNE seeds) | S3 (+ S2 if `local_reextract`) |
| H_mycelic_lean | as F | ✓ | exact | ✓ | ✓ | ✓ strict | ✓ `hybrid` | ✓ | — | own | S3 |
| I `completion` | as F | ✓ | exact | ✓ | ✓ | ✓ | ✓ `hybrid` | ✓ | — | shared | S3 |
| J `verified` | as F | ✓ | exact | ✓ | ✓ | ✓ | ✓ `hybrid` | ✓ | ✓ (kernel) | shared | S3 |
| Y `oracle_retrieval` (declared control: unbounded claim pool) | small-7b (shared), every claim | ✓ | exact | — | — | — | ✓ `min` | ✓ | — | shared | none |
| Z `random_rank` | as B2 | ✓ | exact | as B2 | — | — | as B2 | ✓ | — | hand, then shuffled | none |
| Z2 `naive_enumerate` (control) | small-7b (shared) | unused | unused | — | — | — | **none** (by design) | none | — | — | none |

**No architecture bypasses an operator.** Every system's claims come out of O1 at its own tier, and every system that
synthesises runs O3 with the same check probabilities at the kernel tier. Descents reuse the user's first-pass claims;
they do not re-read truth. B2, B4, Y, Z2 and every hierarchy variant share one cached user layer per seed, so their
extraction noise is identical draw for draw.

The asymmetries are of three kinds:
* **Tier, by design of the natural regime.** A, B and A2 extract with the kernel model (recall 0.96, entity fidelity
  0.995). Everyone else extracts at small-7b (0.70, 0.84).
* **Tuning.** H uses the `hybrid` link timing, `w_dispersion` 0.8 and its own ranker fitted on 40 TUNE seeds. The
  centralised systems use `min`, 0.0 and the shared ranker fitted on 3 seeds. The protocol's *A2-tuned* comparator
  exists to remove this.
* **Oracle reads outside operators:**
  * A and A2 retrieve with the true predicate (S1). This is exact on this text, so it has no effect.
  * Every hierarchy variant groups families, and F–J also route descents and questions, around hallucinations by the
    simulator's flag (S3). This is exact, so it has no effect.
  * H's optional re-extraction looks up notes by the true anchor (S2). It is off in every frozen configuration.

## 6. Which conclusions depend on oracle-assisted simulation

Every v5 number is produced by the simulator (PROTOCOL_V5 §1). This section says which conclusions rest on an operator
that is more capable or more convenient than a live model is known to be. Those are what a live-LLM run would have to
earn. The shortcuts in §2 are not on this list: replacing them changes nothing measured.

| conclusion or metric | rests on | what a live run must earn |
|---|---|---|
| Absolute `found`, AP and precision for **every** system | O1 recall and precision as independent coins at *assumed* tier rates (`models.py`: "assumed placements"); O3 checks as coins | Extraction recall and precision on real prose, with errors that are correlated (hard notes stay hard, systematic misreadings repeat across authors). Independent errors reward multi-witness aggregation; correlated errors penalise it. Architectures that aggregate many cheap reads (the hierarchy, B4, Y at small-7b) are the most exposed. |
| A and A2 retrieval coverage (A2 is the strongest centralised baseline) | S1 and the closed surface lexicon: the "causal-predicate notes" prefilter is 100% precise and 100% complete on template text | Retrieval of relevant notes from open-vocabulary text, where keyword prefilters miss paraphrases and admit noise. A2's evidence coverage is therefore an upper bound. |
| Independent-support accuracy, D4 (duplicate inflation), top-support, `min_sup` / `echo_ratio` ranker features, the dedup check | O1c: signatures are true event ids for 92% of records | Echo and duplicate detection on real forwarded or paraphrased reports. |
| D2 (temporal scramble) and D5 (stale chain) rejection; contradiction F1; `conflict`, `neg_after_n`, lag and cluster ranker features; the hybrid link-time DP | O1a and O1b: exact days and exact polarity at every tier; O3 temporal coin | Date extraction (including relative dates), event ordering, and detection of negation and retraction. |
| D3 (near-miss entity) rejection, lineage and provenance accuracy, J's attribution | O1 entity slips confined to one same-prefix partner and fixed per author; O3 entity linking as a coin over a 5-letter stem; O7 at 0.995 | Entity resolution and linking on real names (aliases, abbreviations, renamed systems), and provenance checking by re-reading. |
| D1 (entity coincidence) rejection; the triage span test; B4's and H's sketch bitmasks | The kernel and every site know the generator's causal schema `CAUSAL_CHAINS` and the causal/background split exactly; O1 maps text onto that closed schema | Discovering or being given the causal structure, and mapping open text onto it. |
| `hallucination_rate` (guardrail G6) | O3h: hallucinations carry no evidence and no features | Detecting hallucinations that cite real but irrelevant evidence. |
| Every fitted ranker (v3/v4 shared ranker; H's own ranker) and every guardrail result produced with one | O9: supervised on simulator gold labels from worlds of the same generator | Labels, or a transfer argument, for live candidates whose feature distribution differs. |
| Rejected local re-extraction (vNext) | O8: re-read misses independent of first-pass misses; S2 lookup | Recall on a re-read of the same hard note. |
| Cost and wall time | The meter: token counts of serialised operator I/O; no retries or failures (protocol §3) | Real token counts, retries and failures. |

Paired comparisons in the capability-matched regime are less sensitive to these assumptions than absolute levels,
because both sides use the same operators at the same tier. They are not insensitive. The architectures lean on
different operators: A2 on the lexical prefilter and single-pass extraction at the kernel tier; H and B4 on
aggregating many independent cheap reads, on exact echo signatures and on exact days. So **the sign of the H-vs-A2
gap under live operators is not established by the simulator.** A staged live plan (PROTOCOL_V5 §8) should replace O1
(with O1a–c), O3h and O9 first, because they carry the most weight.

## 7. The automated audit (`research/mycelic/test_leakage.py`)

`python3 -m unittest research.mycelic.test_leakage` (32 tests, 17 s on this machine).

* **Static inventory.** An AST scan of `research/mycelic/*.py` lists every access to a hidden field or object:
  * string subscripts of hidden fields on record arrays (`field:` / `write:`);
  * attributes that only gold, generator or flag objects carry (`attr:`);
  * the gold types and constructors, `KIND_*`, and non-plumbing uses of the `near_miss` table (`name:`);
  * construction of the `hallucinated` / `spurious` flags (`kw:`).

  It compares them with `research/mycelic/leakage_allowlist.json`, which maps each site (file::function::token) to an
  expected count, a category and a one-line justification. A new site, or a higher count at a listed site, fails, and
  every SHORTCUT entry must cite its finding. A planted-leak test checks the scanner itself. The scanner is a tripwire
  and has known limits: it does not follow data through files (calibrator labels), containers or record positions
  (L1). The metamorphic tests cover those paths for the four architectures.
* **Metamorphic** (400 users, TUNE seed 590). Each test compares the whole decision record with an untransformed
  run: the ranked register (entity, links, confidence, chain, support, contradiction, regions, evidence pointers),
  compute units, call count, questions asked and kernel pool size.

| test | transform | why the invariance must hold | H_full | H_prev | A2 | B4 |
|---|---|---|---|---|---|---|
| (a) | every `sig` the extraction operator returns is mapped through an affine bijection of int64 | signatures are a simulated detector's *identities*; their values carry the planted-record channel (L1) | pass | pass | pass | pass |
| (b1) | `kind`, `veracity`, `group`, `facet`, `superseded`, `salience` permuted; `corpus.patterns`, `notable_records`, `contradiction_pairs`, `families`, `dup_events`, `entity_kind` and `world.gold` replaced by objects that raise on any use | no operator reads these fields, and no decision may | pass | pass | pass | pass (also H + re-extraction: pass) |
| (b2) | `pred`, `anchor`, `t`, `polarity`, `event`, `aux` permuted for decisions while `ops.extract` and the renderer (`Corpus.tokens` / `text`) read the pristine columns | operator outputs and the observable text are unchanged, so only a decision that reads a true field directly can change | pass | pass | **xfail (S1)**; observable index: pass | pass |
| (b2) | as above, H with `local_reextract` | as above | **xfail (S2)**; token lookup: pass | | | |
| (c) | `hallucinated` cleared on every candidate when synthesis returns | the flag is the evaluator's; decisions must not route on it | **xfail (S3)**; evidence rule: pass | **xfail (S3)**; evidence rule: pass | pass | pass |

  A companion test asserts that each expected failure fails *because the decisions differ*, not because of an error.
  Another asserts that the S1 and S3 replacements give decisions identical to the current defaults.
* **Sealed seeds.**
  * `check_world` refuses all 33 final worlds without `MYCELIC_FINAL_EVAL=1` and writes nothing to
    `final_access.log`.
  * `build_world` refuses before the generator is reached; this is checked with the generator mocked to fail.
  * Development seeds pass, and the declared final sets match PROTOCOL_V5 §2.
  * The alias case P1 is an expectedFailure.

## 8. Recommendations

1. **Make all three observable replacements the default, for every architecture at once**
   (`ops.OBSERVABLE = all True`).
   * S1 and S3 are exact on this generator, so no committed number moves, and the oracle reads leave the decision
     path.
   * S2 is honest only in its observable form: it reads, and is charged for, the notes that name the entity as an aux
     mention. Any future evaluation of local re-extraction must use the observable form.
   * After the switch, flip the expectedFailure cases in `test_leakage.py` to plain passes, and reclassify the three
     SHORTCUT allowlist rows.
2. **Close P1.** Refuse final seeds at every scale (or check the realised org), and route `live_tasks` through
   `build_world`. Add the alias case to the seal test as a plain test.
3. **Finish D1:** correct `report_text.py:90` and regenerate `MYCELIC_ENTERPRISE.md` (the code docstrings are fixed
   in this diff).
4. **Keep `test_leakage` in the acceptance gate.** Every v5 change that touches `systems.py` or `ops.py` must keep the
   static inventory and the metamorphic invariances green. A new row in `leakage_allowlist.json` needs a category and
   a justification, and a SHORTCUT row needs a finding in this document.
5. **Report operator sensitivity, not only point estimates.** The conclusions in §6 rest on O1, O1a–c, O3h and O9.
   Before any claim leaves the simulator, re-run the final comparison with harsher operators applied to every
   architecture alike:
   * extraction misses correlated per note, so a re-read repeats the miss;
   * a text-derived echo signature with paraphrase noise;
   * a date / polarity error rate at small tiers;
   * hallucinations that cite real evidence pointers.

   Generator-side fixes for L1 (randomised event ids and within-user record order) belong to a new world version,
   because they change every world.

## Appendix A. Every hidden-information site, classified

Grouped from `python3 -m research.mycelic.test_leakage --sites`, which prints one row per site. A site is a file, a
function and a token. The
tokens are `field:` (a record-array read), `write:` (a generator write), `attr:`, `name:` and `kw:`. The lines are
those at the audited diff. `test_leakage.py` is excluded from its own scan.

Categories:
* **SCORING**: evaluator, loss accounting, calibrator labelling, reports and tests. These read gold after the fact.
* **GENERATOR**: world construction and rendering.
* **OPERATOR-SIM**: a disclosed simulated model reads ground truth to produce a noisy output (see §4).
* **SHORTCUT**: hidden information reaches a decision outside an operator (see §2).

| category | file:function | tokens (lines) | justification |
|---|---|---|---|
| SHORTCUT | `ops.py` unsupported | `attr:hallucinated` 106 | S3 (ORACLE_AUDIT.md): descent / question / completion / family routing and the ranker gate skip candidates by the simulator's hallucination flag; observable replacement ops.OBSERVABLE['unsupported_by_evidence'] (no evidence object) is exact - identical registers |
| SHORTCUT | `systems.py` Hierarchy._reextract | `field:anchor` 876 | S2 (ORACLE_AUDIT.md): local re-extraction finds 'notes that name the entity' by the true anchor field (misses aux mentions, under-charges reads); observable replacement ops.OBSERVABLE['reextract_lookup'] (token match on the agent's own notes); local_reextract is off in every frozen configuration |
| SHORTCUT | `systems.py` _lexical_index | `field:pred` 1618 | S1 (ORACLE_AUDIT.md): the A / A2 lexical prefilter is keyed on the record's true predicate; observable replacement ops.OBSERVABLE['lexical_index'] (surface-phrase match on the rendered text) is exact on this generator - identical registers on TUNE 580-599 |
| OPERATOR-SIM | `ops.py` ExtractResult.take | `attr:spurious` 160 | marks claims the extraction operator invented; read only by loss accounting and tests |
| OPERATOR-SIM | `ops.py` extract | `name:_near_miss_map` 222; `name:near_miss` 221,254 | O2 entity-slip table of the extraction operator (built from entity names) |
| OPERATOR-SIM | `ops.py` extract | `field:anchor` 231; `field:polarity` 233; `field:pred` 230; `field:t` 232 | O1 claim extraction: reads the true tuple and corrupts it at tier rates (recall coin, chain-neighbour predicate slip, per-author near-miss entity slip, spurious claims); day and polarity copied exactly |
| OPERATOR-SIM | `ops.py` extract | `field:event` 234 | O1c echo detector: sig = true event id, replaced by a coarse (pred, anchor, day/3) key for 8% of records; decisions use sigs only as identities (test_leakage test a) |
| OPERATOR-SIM | `ops.py` extract | `attr:spurious` 297; `kw:spurious` 271,281 | marks claims the extraction operator invented; read only by loss accounting and tests |
| OPERATOR-SIM | `ops.py` synthesize | `kw:hallucinated` 1331 | O3h hallucination injection marks its inventions for the evaluator |
| OPERATOR-SIM | `runner.py` build_world | `name:_near_miss_map` 62 | builds the extraction operator's entity-slip table per world; passed only to ops.extract |
| GENERATOR | `corpus.py` <module> | `name:KIND_ANOMALY` 147; `name:KIND_DECOY` 146; `name:KIND_DUP` 146; `name:KIND_FACET` 146; `name:KIND_FALSE` 146; `name:KIND_NAMES` 148; `name:KIND_NOTABLE` 147; `name:KIND_ROUTINE` 146; `name:KIND_STALE` 146 | record-kind constants |
| GENERATOR | `corpus.py` Corpus | `name:Pattern` 184 | Corpus.patterns field annotation |
| GENERATOR | `corpus.py` Corpus.text | `field:t` 233 | renderer: realises the true tuple as the observable note text |
| GENERATOR | `corpus.py` Corpus.tokens | `field:anchor` 215; `field:aux` 216,217; `field:event` 210; `field:polarity` 226; `field:pred` 212 | renderer: realises the true tuple as the observable note text |
| GENERATOR | `corpus.py` build_corpus | `attr:contradicted` 581; `attr:facet_records` 705,706,707,716,730; `attr:facet_times` 579,586,676; `attr:facet_users` 578,675; `field:anchor` 447,478; `field:event` 448,479; `field:kind` 445,453,458,463,467; `field:polarity` 460; `field:pred` 446,477; `field:salience` 464,468; `field:superseded` 454; `field:t` 449,455; `field:veracity` 459; `name:KIND_ANOMALY` 463; `name:KIND_DECOY` 667,689; `name:KIND_DUP` 445,663; `name:KIND_FACET` 571,592; `name:KIND_FALSE` 458; `name:KIND_NOTABLE` 467; `name:KIND_ROUTINE` 408; `name:KIND_STALE` 453; `name:Pattern` 487,506,556,616; `name:_near_miss` 635; `write:anchor` 404,567,588,659,685; `write:aux` 405,568,589,660,686; `write:event` 407,569,590,662,666,687; `write:facet` 412,575,596,672,693; `write:group` 411,574,595,671,692; `write:kind` 408,571,592,663,667,689; `write:polarity` 410,573,594,670,691; `write:pred` 373,566,587,658,684; `write:salience` 413,576,597,673,694; `write:t` 369,565,586,657,683; `write:veracity` 409,572,593,669,690 | world construction: writes the hidden record fields, plants patterns and decoys |
| GENERATOR | `corpus.py` make_gold | `attr:contradicted` 778; `attr:facet_records` 769,773; `attr:families` 781; `attr:notable_records` 787; `attr:patterns` 766,783; `attr:rare` 776; `attr:real` 767,783; `field:event` 775; `name:Gold` 762,785 | answer-key construction; consumed only by the evaluator |
| GENERATOR | `live_tasks.py` build | `field:anchor` 62; `field:aux` 56; `field:pred` 61 | builds the live primitive-operator task file and its answer key from the true fields |
| GENERATOR | `runner.py` build_world | `name:make_gold` 61 | builds the answer key beside the world; World.gold is read only by evaluate() |
| SCORING | `arm_paired.py` run_arms | `attr:gold` 95 | paired runner row: evaluate() |
| SCORING | `calibrate.py` calibrate | `attr:gold` 58,82,102,120 | knob fitting: evaluate() on calibration seeds |
| SCORING | `calibrate.py` calibrate_ct | `attr:gold` 152 | knob fitting: evaluate() on calibration seeds |
| SCORING | `calibrate.py` calibrate_evidence | `attr:gold` 200 | knob fitting: evaluate() on calibration seeds |
| SCORING | `calibrator.py` _dump_arch | `attr:decoy_type` 663; `attr:discoverable` 640; `attr:gold` 640,651; `attr:patterns` 639,641; `attr:rare` 669; `attr:real` 641 | calibrator labelling: candidate dumps labelled against gold for ranker fitting on TUNE seeds |
| SCORING | `calibrator.py` dump | `attr:discoverable` 118; `attr:gold` 118; `attr:patterns` 117,119; `attr:rare` 149; `attr:real` 119 | calibrator labelling: candidate dumps labelled against gold for ranker fitting on TUNE seeds |
| SCORING | `calibrator.py` select_archs | `attr:gold` 432 | ranker adoption chosen from evaluated metrics |
| SCORING | `corpus.py` <module> | `attr:discoverable` 800; `attr:rare` 800; `name:make_gold` 798 | __main__ smoke print of gold sizes |
| SCORING | `corpus.py` Corpus.stats | `attr:contradiction_pairs` 256; `attr:decoy_type` 252; `attr:notable_records` 255; `attr:patterns` 241,242; `attr:rare` 250; `attr:real` 241,242; `field:kind` 240; `name:KIND_NAMES` 247 | world statistics for reports |
| SCORING | `evalm.py` _match_sets | `name:Pattern` 27 | evaluator: matches the register against gold after the run |
| SCORING | `evalm.py` evaluate | `attr:contradicted` 215; `attr:decoy_type` 112,113,153,155; `attr:discoverable` 107; `attr:evidence` 205; `attr:facet_records` 164,190,284; `attr:families` 244,255; `attr:hallucinated` 169; `attr:independent_sources` 203; `attr:patterns` 106,112,113,281; `attr:rare` 144,146; `attr:real` 112,113,282; `attr:top_supported` 234,240; `field:anchor` 287,291; `field:event` 163; `field:pred` 286,290; `name:Gold` 101 | evaluator: matches the register against gold after the run |
| SCORING | `experiments.py` _row | `attr:discoverable` 56; `attr:gold` 53,56 | experiment row: evaluate() and gold size |
| SCORING | `experiments.py` e12_provenance | `field:anchor` 680 | provenance metric: checks the true anchor of each cited evidence record after the run |
| SCORING | `live_rank.py` build | `attr:discoverable` 106; `attr:gold` 106; `attr:hallucinated` 116; `attr:patterns` 106 | builds the blind live-ranking task and its answer key (keys/); the hallucinated filter chooses which candidates are shown to the live model, it is not a system decision |
| SCORING | `loss_accounting.py` _best_match | `name:Pattern` 161 | loss funnel: traces each gold pattern through a finished run |
| SCORING | `loss_accounting.py` _facets_extracted | `attr:facet_records` 99; `attr:spurious` 94; `name:Pattern` 81 | loss funnel: traces each gold pattern through a finished run |
| SCORING | `loss_accounting.py` _flat_rows | `attr:discoverable` 282; `attr:facet_records` 286,294; `attr:gold` 282; `attr:patterns` 268; `attr:rare` 293 | loss funnel: traces each gold pattern through a finished run |
| SCORING | `loss_accounting.py` _hier_rows | `attr:discoverable` 188; `attr:facet_records` 198; `attr:facet_users` 209,213; `attr:gold` 188; `attr:patterns` 177; `attr:rare` 197 | loss funnel: traces each gold pattern through a finished run |
| SCORING | `quick_paired.py` run_pair | `attr:gold` 80 | paired runner row: evaluate() |
| SCORING | `runner.py` run_matrix | `attr:gold` 294 | experiment driver passes World.gold to evaluate() |
| SCORING | `test_mycelic.py` TestCorpus.setUp | `attr:gold` 63; `name:make_gold` 63 | test harness: checks generator and operator invariants against ground truth |
| SCORING | `test_mycelic.py` TestCorpus.test_all_five_decoy_classes_present | `attr:decoy_type` 91; `attr:patterns` 91; `attr:real` 91 | test harness: checks generator and operator invariants against ground truth |
| SCORING | `test_mycelic.py` TestCorpus.test_deterministic | `field:anchor` 68; `field:pred` 67 | test harness: checks generator and operator invariants against ground truth |
| SCORING | `test_mycelic.py` TestCorpus.test_echoes_repeat_wording_independents_do_not | `field:event` 98,112 | test harness: checks generator and operator invariants against ground truth |
| SCORING | `test_mycelic.py` TestCorpus.test_entity_locality | `attr:discoverable` 131; `attr:gold` 131; `attr:patterns` 131; `field:anchor` 126 | test harness: checks generator and operator invariants against ground truth |
| SCORING | `test_mycelic.py` TestCorpus.test_no_single_user_holds_a_whole_pattern | `attr:facet_records` 83; `attr:patterns` 79; `attr:real` 80 | test harness: checks generator and operator invariants against ground truth |
| SCORING | `test_mycelic.py` TestCorpus.test_patterns_span_multiple_regions | `attr:discoverable` 75; `attr:gold` 75; `attr:patterns` 74; `attr:real` 75 | test harness: checks generator and operator invariants against ground truth |
| SCORING | `test_mycelic.py` TestMetrics.test_empty_report_scores_zero_not_nan | `attr:gold` 301 | test harness: checks generator and operator invariants against ground truth |
| SCORING | `test_mycelic.py` TestMetrics.test_found_is_at_least_recall_at_100 | `attr:gold` 317 | test harness: checks generator and operator invariants against ground truth |
| SCORING | `test_mycelic.py` TestMetrics.test_strict_match_never_exceeds_primary | `attr:gold` 310 | test harness: checks generator and operator invariants against ground truth |
| SCORING | `test_mycelic.py` TestOperators.test_entity_errors_are_per_agent_not_per_mention | `attr:spurious` 171; `field:anchor` 173 | test harness: checks generator and operator invariants against ground truth |
| SCORING | `test_mycelic.py` TestSystems.test_ablating_lineage_destroys_lineage_accuracy | `attr:gold` 262 | test harness: checks generator and operator invariants against ground truth |
| SCORING | `test_mycelic.py` TestSystems.test_descent_does_not_broadcast | `attr:discoverable` 283; `attr:gold` 283; `attr:patterns` 283 | test harness: checks generator and operator invariants against ground truth |
| SCORING | `test_mycelic.py` TestSystems.test_kernel_context_is_comparable_across_architectures | `attr:gold` 251 | test harness: checks generator and operator invariants against ground truth |
| SCORING | `test_mycelic.py` TestSystems.test_oracle_has_near_total_evidence_coverage | `attr:gold` 234 | test harness: checks generator and operator invariants against ground truth |
| SCORING | `test_mycelic.py` TestSystems.test_random_rank_has_the_same_candidates_as_its_parent | `attr:gold` 227,228 | test harness: checks generator and operator invariants against ground truth |
| SCORING | `test_mycelic.py` TestSystems.test_runs_are_deterministic | `attr:gold` 208,209 | test harness: checks generator and operator invariants against ground truth |
