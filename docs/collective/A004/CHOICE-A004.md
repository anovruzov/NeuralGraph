# Accuracy test A004: the fourth cycle toward 86, a-4b with reasoning on, on a fresh draw

The founder's goal is "loop until we hit 86". What 86 means is pinned in `docs/strategy/LOOP-48H.md`, section 2
(2.1 to 2.7): balanced accuracy 0.860 of the site verifier's verdicts against the filed-category key, at record
level, with the codes hidden, on a fresh pre-registered draw of 150 real public NHTSA complaints (300 questions),
scored exactly as J001 was scored (`docs/collective/replay/vehicles/CHOICE-J001.md`), with the interval's lower end
above the lexical judge and above the predicate-only bound, one primary (model, arm) pair per cycle, and confirmation
on the next cycle's fresh draw. This file is the rule for the loop's fourth cycle, lever 6 of the pinned lever order
(section 2.6, item 6, quoted whole under "What this cycle tests"). It is committed alone, on 2026-10-14, at the
deployment branch's commit `831f424`, before any code is written for it, before A003's judging run has run, and
before any record of its draw exists. How the lab will run it (a model-only timing check of a new kind `jr`, then a
judging kind `j5` on A003's `j4` pattern) goes in `BUILD-A004.md`, written after this rule, its reviews and its
amendments.

Nothing here redefines the target, the metric, the data class, the draw, the comparators, the interval rule or the
confirmation rule. Site-level balanced accuracy stays rejected as primary. The lever is one server setting: the same
model, a-4b, served with the server's reasoning switched on, measured on the same records beside the same model served
as every earlier cycle served it. Where this rule adds a clause (the comparators of A003 when the drafted terms are in
the payload, the timing check before the run), the pass test gets stricter or the run safer, never looser.

**What the evidence says.** Every result is cited as written in its record and is never re-scored here.

- **J001 run 9** (lab run 37996675559; `docs/lab/RESULTS.md`, Run 9; labels sha256 `bc6d092d8bca…`), 150 NHTSA
  complaints, codes hidden: a-4b scored balanced accuracy 0.387 [0.340, 0.433] with the shipped instruction
  (sensitivity 0.647, specificity 0.127, unknown share 0.483), against the lexical judge's 0.670 [0.627, 0.713]
  (sensitivity 0.380, specificity 0.960), the record-blind control's 0.527 [0.467, 0.587] and the predicate-only
  bound's 0.580.
- **A001 run 1** (lab run 38114522160; `docs/collective/A001/CHOICE-A001.md`, "## Runs", Run 1; `docs/lab/RESULTS.md`,
  Run 11): the primary pair (a-4b, negation instruction) scored 0.577 [0.530, 0.623] against the lexical judge's
  0.663 [0.627, 0.703] on the same records, verdict word **failure**; a-4b with the shipped instruction read through
  the unclear-to-no mapping scored 0.667 [0.620, 0.710]; 12 (model, arm, cycle) comparisons.
- **A002 run 1** (lab run 38439612256; `docs/collective/A002/CHOICE-A002.md`, "## Runs", Run 1; `docs/lab/RESULTS.md`,
  Run 12): the primary pair (a-4b, evidence as answered) scored 0.540 [0.503, 0.577] against the lexical judge's
  0.680 [0.637, 0.723], verdict word **failure**; the negation task read through the mapping scored 0.687 [0.643,
  0.730], paired 0.007 [-0.050, 0.063] to the lexical judge, not told apart; 9 comparisons, 21 since the loop began.
- **A003** (`docs/collective/A003/CHOICE-A003.md`, amended before any build, C1 to C32, and after the term run, T1 to
  T11): lever 5, the drafted-terms payload, branch N, P = (a-4b, `negation_terms`); the term set is term run 1's
  (lab run 38612800578, sha256 `42c591ccbb66b81455ca19840a54e18cbb69b5613dd2eab8f3f8c70f9cea39cc`, artifact
  `lab-plan-38612800578-1`, expiring 2027-01-12T18:09:48Z). Its judging kind `j4` is being built as this rule is
  written; its judging run has not run. **A003's result is unknown, and this rule assumes nothing about it**: the base
  task of this cycle is chosen from A003's recorded result by the pre-registered adaptation rule of "The base task and
  the arms", written now with every branch complete, and the branch is fixed by a dated amendment that cites
  CHOICE-A003's Runs entry once it exists. A003 declares 4 comparisons, 25 since the loop began (CHOICE-A003, rule 12).
- **No a-4b call with reasoning on has ever run in this lab; no server started with `--reasoning on` has served a
  call; no warm-up probe has recorded reasoning content** (its stop, `lab/notes.py:43`, appears in no run record)
  (E14). a-4b has been served with `--reasoning off` since the lab's G2 commit `4945460` (2026-10-07), before the
  lab's first run (`git log -- lab/models.json`); every a-4b figure above and below is a reasoning-off figure. a-0p5b
  and a-1p5b run with no reasoning flag (`lab/models.json:31, 47`, empty `server_args`). The fourth gguf key of
  `lab/models.json`, served without a reasoning flag in RESULTS Run 1 (lab run 37834297419), stopped its warm-up at
  the token limit (RESULTS Run 1, "Units without a result"), was given `--reasoning off` in commit `132386d`, and
  stopped the same way with it in RESULTS Run 2 (lab run 37841045975, its fourth shard): no record attributes that
  stop to reasoning.

The pattern, read from the arms tables of A001 run 1 and A002 run 1 (a-4b, each figure as its record prints it):

| Cycle and arm | Record | Sensitivity | Specificity | Unknown share | Balanced accuracy [95% interval] |
|---|---|---|---|---|---|
| J001, shipped | RESULTS Run 9 | 0.647 | 0.127 | 0.483 | 0.387 [0.340, 0.433] |
| A001 A, shipped | CHOICE-A001 Run 1 | 0.560 | 0.053 | 0.560 | 0.307 [0.263, 0.350] |
| A001 B, shipped, mapped | CHOICE-A001 Run 1 | 0.560 | 0.773 | 0.000 | 0.667 [0.620, 0.710] |
| A001 C, negation (P) | CHOICE-A001 Run 1 | 0.833 | 0.320 | 0.137 | 0.577 [0.530, 0.623] |
| A001 D, negation, mapped | CHOICE-A001 Run 1 | 0.833 | 0.473 | 0.017 | 0.653 [0.610, 0.697] |
| A002 N, negation | CHOICE-A002 Run 1 | 0.907 | 0.360 | 0.090 | 0.633 [0.590, 0.677] |
| A002 D, negation, mapped | CHOICE-A002 Run 1 | 0.907 | 0.467 | 0.017 | 0.687 [0.643, 0.730] |
| A002 E, evidence (P) | CHOICE-A002 Run 1 | 0.900 | 0.180 | 0.130 | 0.540 [0.503, 0.577] |
| A002 R, rationale | CHOICE-A002 Run 1 | 0.980 | 0.200 | 0.143 | 0.590 [0.557, 0.623] |
| A002 Rm, rationale, mapped | CHOICE-A002 Run 1 | 0.980 | 0.240 | 0.120 | 0.610 [0.573, 0.647] |
| Lexical judge, J001's draw | RESULTS Run 9 | 0.380 | 0.960 | 0.000 | 0.670 [0.627, 0.713] |
| Lexical judge, A001's draw | CHOICE-A001 Run 1 | 0.333 | 0.993 | 0.000 | 0.663 [0.627, 0.703] |

Under every instruction that makes a-4b answer rather than abstain, it says yes too often: across A002's nine arms
sensitivity runs from 0.887 to 0.980 and specificity from 0.180 to 0.467 (CHOICE-A002 Run 1, the nine arms' table);
the lexical judge is the mirror image (sensitivity 0.333 to 0.380, specificity 0.960 to 0.993). The failure to fix is
a yes on a question about a component the record was not filed under. A reasoning pass could plausibly help there,
by checking whether the narrative reports a problem with the asked component rather than merely naming it, or it
could not. The nearest measured analogue is A002's rationale task, an analysis written in the reply before the two
answers (a median of 87.0 output tokens, CHOICE-A002 Run 1): it moved a-4b toward yes (sensitivity 0.907 to 0.980,
specificity 0.360 to 0.200 against the negation task), `R_minus_N` -0.043 [-0.090, 0.003], not told apart, and
`Rm_minus_D` -0.077 [-0.123, -0.030], hurts. A server-side reasoning pass is not that task: it is the model's trained
thinking mode, hidden from the reply and usually longer. No recorded figure predicts its direction.

**What this cycle tests.** Lever 6 of the pinned lever order, section 2.6, item 6, verbatim: "**a-4b reasoning on**
(`--reasoning on` is allowlisted, `lab/manifest.py:121`; a-4b runs with `--reasoning off`, `lab/models.json`; the
warm-up stops on any `reasoning_content`, `lab/warmup.py:44`, so this needs a reviewed lab change). Cost: about 1-2
min per call, 5-10 h per model for 300 calls, spread across parts (up to 30)." Each cited line, checked at `831f424`:

- `lab/manifest.py:121` holds `ARGS_ALLOWLIST`: `--reasoning on|off|auto`, `--reasoning-budget N`, `--jinja`,
  `--no-jinja`; `:126` holds `REASONING_VALUES` (`on`, `off`, `auto`) and `:127-128` the budget's form and its maximum
  32768; `parse_server_args` (`:208-231`) accepts each flag at most once. **Holds.**
- `lab/models.json`: a-4b's entry (`:49-64`) has `server_args` `["--reasoning", "off"]` at `:63`. **Holds.**
- `lab/warmup.py:44` **does not hold at this commit**: line 44 is part of the docstring's description of the warm-up's
  calls. The rule the lever names is stated at `:49-50` ("any `reasoning_content` stops the whole start (remedy:
  reasoning off)") and coded at `:427` (the probe reads `reasoning_content` from both of its replies, through `_reply`,
  `:316-324`) and `:433-434` (the stop). A second warm-up rule bears on the lever: `:46-47` and `:407-408`, a warm-up
  call that ends with `finish_reason` `length` skips its task's units (remedy: reasoning off or more tokens). A003
  cited the first rule at `:46-50` (CHOICE-A003, "What this cycle tests").
- "about 1-2 min per call, 5-10 h per model for 300 calls" is **the loop prompt's estimate, not a measurement**: this
  lab has measured no call with reasoning on. What it has measured is under "Why the units are this size".

Levers 1 and 2 are A001's, levers 3 and 4 A002's, lever 5 A003's; lever 7 needs the chief scientist's settings and
lever 8 a model rung: none of them this cycle. One primary pair is named below. By the pinned rule a pass in this
cycle is provisional unless it is the confirmation the adaptation rule's confirmation case names.

## The declaration

- The rule was written by the AI system that wrote this repository's code.
- It has seen J001's result (RESULTS run 9; CHOICE-J001, Runs), A001's (CHOICE-A001, Runs, Run 1; RESULTS run 11),
  A002's (CHOICE-A002, Runs, Run 1; RESULTS run 12), L001 runs 1 and 2 (RESULTS run 10; CHOICE-L001, Runs), RESULTS
  runs 1 to 12, A003's rule with both amendments and its term run 1 (CHOICE-A003, Runs, "Term run 1"), the loop prompt
  (`docs/strategy/LOOP-48H.md`) and the loop's status log (`docs/handoff/LOOP-48H-STATUS.md`).
- It has not seen A003's result: A003's judging kind is not merged at `831f424` (no `lab/j4.py` exists), and its
  judging run has not run. No figure of it is cited; the base task is left to the adaptation rule, every branch
  written now.
- It has not seen any record of this cycle's draw. The draw does not exist: it is made in the plan job on a runner,
  from the complaint file as published on the day of the run, after J001's, A001's, A002's and A003's records and
  A003's split removals are removed. The sandbox cannot reach NHTSA (standing rule 2).
- It has not seen any drafted term: the term set lives in term run 1's artifact, which the sandbox cannot download
  (CHOICE-A003, T10); this rule cites only its sha256 and counts.
- It has not read any stored reply of J001, A001 or A002, and no reasoning text exists: no reasoning-on call has run.
- No call on the handoff's decision 9 (how a site verifier's "unclear" counts) and no answer to A002's ask on the
  verbatim-substring check have been received: `docs/handoff/LOOP-48H-STATUS.md` lists both under "Open asks" and
  nothing on them under "Decisions received". The loop prompt's default applies (section 7: "shipped `decide`
  primary, declared mappings secondary"), read as A001's A7, A002's B1 and A003's declaration read it: the primary arm
  reads the answers as answered, and the mapped reading is the headline secondary. A call that counts unclear as no,
  received before the judging request is pushed, is adopted by a dated amendment with no other content, and installs
  exactly the pair "The base task and the arms" names for it (E2); any other call, and any call received later, bears
  on the next cycle.
- The founder ask A002's B6 posted on section 2.3's stop rule stands; this rule rests on the same reading (one
  verifying source per cycle suffices; a verified artifact's refs are the records that cycle judged) for all four
  earlier cycles.
- No hosted model is named or used. The founder has not supplied a key (`docs/handoff/LOOP-48H-STATUS.md`, "Open
  asks", 2026-10-11), and standing rule 2 forbids any other model-host egress. No inference request leaves the
  runner: the model server listens on loopback. The runs make these other requests (E34): the complaint archive's
  download from NHTSA (the judging run only); the lab's own artifact downloads and cache restores; in every gguf
  provision job, an anonymous query to the model hub's API for the pinned revision, and the weight file's download
  from the hub when the cache does not hold it (`lab/provision.py`, module docstring; the new key's first run
  downloads 2,497,280,256 bytes, E27).
- The models may have seen these public complaints in training. The answer key is the components each complaint was
  filed under, not labels anyone checked.

## The metric, pinned

Copied in substance from LOOP-48H sections 2.1 to 2.4; where this file and that section disagree, that section wins,
and the disagreement is a defect of this file.

- **86 is balanced accuracy 0.860** of the site verifier's verdicts against the filed-category key, on a scale where
  the key itself scores 1.000. Balanced accuracy is (sensitivity + specificity) / 2, scored as J001 scores: for each
  record one question about a filed category (positive) and one about a category the record was not filed under
  (negative, drawn with the frequency of the other records' positives, `lab/j1.py:130` `NEGATIVE_DRAW`); a `confirm`
  on a positive is correct, a `refute` on a negative is correct, `unknown` is never correct. Intervals are a cluster
  bootstrap over records, B 10,000, 95%.
- **The centralized baseline** is the filed-category key: the codes the filer attached to each record, which a
  centralized system already holds (HQ's `codes`-channel cells; `docs/collective/L001/CHOICE-L001.md`, K2). A
  centralized reader that sees the codes scores 1.000 by construction. The site verifier reads only its own record's
  text, codes hidden.
- **The data class is fixed** (section 2.3): real public NHTSA complaints of the vehicle pack, the makes `NHTSA_MAKES`
  and the window `NHTSA_WINDOW` of `lab/goldlabels.py:49-50` verbatim, the j1 eligibility rule, n 150, codes hidden,
  through `judge_task`, `judge_payload` and `decide` of `mycelic/collective/edge/verify.py` (`:135`, `:162-168`,
  `:243-259`; pinned, unchanged), or through `Runtime.run(task, payload, schema, ref=...)`
  (`mycelic/collective/inference/runtime.py:166`) with a lab-side TaskSpec scored by the same pinned `decide`. A
  server setting is not a change of data class: the records, the payload, the questions, the path and the scoring are
  those of every earlier cycle. No arm may place the judged record's codes, its filed categories or anything derived
  from them in the payload, the task text or a reply mapping. The reasoning is generated from the payload and the
  model's weights; no arm puts codes in the payload; what the weights hold of these public complaints is trap 5's
  caveat, and a reasoning pass may draw on it more than a direct answer does (trap 1; E32).
- **The comparators** (section 2.4), on the same records, paired, with 95% cluster-bootstrap intervals of the
  difference: the lexical judge (`lexical_judge`, `verify.py:171`; J001 0.670 [0.627, 0.713]; A001 0.663 [0.627,
  0.703]; A002 0.680 [0.637, 0.723]), the predicate-only bound (`lab/j1.py:346`, `prior_bound`; J001 0.580; A001 0.577;
  A002 0.553; a point value fitted to the draw's answers, no interval) and the record-blind control (`PRIOR_RULE`,
  `lab/j1.py:133-134`; J001 0.527 [0.467, 0.587]; A001 0.520 [0.463, 0.577]; A002 0.517 [0.460, 0.573]). When the
  drafted terms are in the payload (branch T and the confirmation case below), the comparators and the pass test are
  A003's (CHOICE-A003, rules 8 and 13, with C5 and C6): the lexical judge with the drafted terms in the pass test's
  lexical clause, the hand-pack lexical judge and the drafted term-presence judge in two added clauses.
- **Rejected as primary** (gameable): site-level balanced accuracy (the route-role baseline scores 0.808 [0.667,
  0.938] reading no record, L001 runs 1 and 2), codes visible to the judge, and a plain ratio to the lexical judge.
- **The secondary** (BA_site − bound) / (BA_central − bound) against a hosted central reader needs a key the lab does
  not have. It is not run and is never assumed.

## What "reasoning on" is in this lab

This section answers the brief's item (1). Every setting here is fixed now.

1. **The setting: one server flag, on a manifest key of its own.** The lab's model server takes its arguments from the
   shard's manifest entry and from nowhere else: `lab/shard.py:353` passes the manifest's `server.args` and the entry's
   `server_args` as the server's extra arguments, and `lab/server.py:131-135` (`server_argv`) appends them to the
   code-owned ones. `lab/shard.py` is pinned, and a shard serves one manifest key (`lab/plan.py:279-307`, shards packed
   per model key). So "reasoning on" is a manifest entry: a new key **`a-4b-r`** in `lab/models.json`, kind `gguf`,
   alias `lab-a-4b-r`, whose `gguf` block (repository, file, revision, licence) is a-4b's byte for byte, whose
   `response_format` (`json_schema`), `transport_schema` (`full`) and context sizes (`ctx_per_slot` 16,384,
   `e3_ctx_per_slot` 4,096, `e2_ctx_per_slot` 32,768) are a-4b's, and whose `server_args` are `["--reasoning", "on"]`
   where a-4b's are `["--reasoning", "off"]`. The key names a server setting of the same model, not another model: the
   model of this cycle is a-4b alone. `lab/models.lock.json` gains, under the new key, a copy of a-4b's lock entry (its
   commit, its sha256 `7485fe6f11af…` and its size, 2,497,280,256 bytes, as `lab/models.lock.json` holds them), so the
   provision step verifies the new key's file against a-4b's digest and never trusts it on first use
   (`lab/manifest.py:57-60`). A provision that verifies any other digest for the new key fails before any model runs.
   The new key has its own cache entry, `lab-gguf-a-4b-r-<sha16>` (`lab/manifest.py:62-64`, `cache_key`;
   `lab/plan.py:325-340`, `provision_entries`): a second copy of the same 2,497,280,256-byte file, counted against the
   repository's 10 GB of caches (`docs/lab/README.md:70-71`); the five model files of the lock would then hold
   8,148,584,608 bytes (derived here from `lab/models.lock.json`'s sizes), beside the server archive and every other
   cache. An entry evicted or unused for 7 days is downloaded again from the model hub by the provision job; a download
   that fails is an infrastructure stop before any model ran (rule 17) (E27). The manifest and lock entries are written
   by a test-pinned helper that copies a-4b's blocks, and `docs/lab/MODELS.md` gains a row that says "the same file as
   a-4b" instead of copying its cells; no document, commit message, BUILD note or console line carries the repository or
   file name (E36). a-4b's entry and lock entry are not changed. `--reasoning on` is the form the lever names, and it is
   allowlisted (`lab/manifest.py:121, 126`). What the pinned server release does with it is the release's behaviour;
   this rule does not read it from the release's source, and the timing check ("The timing check") observes it on a
   runner before any drawn record is judged.
2. **No reasoning budget.** `--reasoning-budget N` is allowlisted with N from -1 to 32768 (`lab/manifest.py:121,
   127-128`). It is not set. The repository records nothing about what the pinned release does with a positive
   budget, and two bounds on one generation (a budget and the cap of item 4) would make a cut reply ambiguous. The
   reasoning is bounded by the task's `max_tokens` alone (item 4). A budget is a later rule's question.
3. **Where the reasoning goes, and where the answer is read.** The request is the one every earlier arm sent:
   `client.chat` (`mycelic/collective/inference/client.py:493-541`) sends the alias, the messages, `temperature` 0
   (`:498`), `max_tokens`, the strict `json_schema` response format and nothing about reasoning (the endpoint's
   `reasoning_effort` and `chat_template_kwargs` stay unset, `:504-507`). A non-streamed reply is read by `_parse_body`
   (`:445-466`): the answer is `choices[0].message.content` and nothing else; a `reasoning_content` field, where the
   server separates the reasoning into one, is never read, kept or returned. A streamed reply, which only the timing
   check uses, keeps the content deltas and counts the reasoning deltas without keeping them (`:210-219`). Where a
   server leaves the reasoning inline in the content between `<think>` tags, `extract_object` removes every such block
   before it looks for the answer (`mycelic/collective/inference/jsonparse.py:9-11`; `strip_reasoning`, `:49`). The
   answer is therefore parsed from the final content only: the first JSON object of the content after any reasoning
   block, validated against the task's schema (`runtime.py:340` and the lines after it), and the pinned `decide`
   reads its two answers, `mentions_entity` and `describes_predicate`, and nothing else. This cycle requires the
   separated form: the warm-up's check for the reasoning key (item 7) and the timing check's clause S1 stop a server
   that reasons inline, so that no reasoning ever sits in the content that a repair's excerpt would carry back to the
   model (`mycelic/collective/inference/tasks.py:100-102`, the first 300 characters of the previous content).
4. **The cap: 2,048 tokens for the reasoning plus the answer, on the reasoning arms only.** This rule reads the server
   as counting every generated token, reasoning included, against the request's `max_tokens`; the timing check tests it
   on a runner, by clause S5 with its parts (i) to (iii) and by its cut call 19 (E10, E11). The reasoning arms' task is
   the base task with `max_tokens` 2,048 and nothing else changed (the same name, data class, instructions and schema),
   built as `dataclasses.replace(base, max_tokens=2048)`. The prompt does not carry `max_tokens` (`tasks.py:78-81`,
   `render_messages`: the system text, the instructions, the schema and the data block), so the messages a reasoning arm
   sends are the control's byte for byte, and the two requests differ in `max_tokens` alone, sent to servers that differ
   in one flag. The rendered prompt differs only as each server's chat template renders its thinking switch, which is
   the lever; each server's fit step counts its own rendered prompt (`/apply-template` then `/tokenize`,
   `lab/warmup.py:293-307`, applied at `:340-377`) (E16). The control arms keep the base task unchanged, with its cap of
   256 (`JUDGE_MAX_TOKENS`, `verify.py:87`; `lab/j2.py:134`, `negation_task`). With reasoning off, a-4b's negation
   replies took a median of 16.0 output tokens and a 95th percentile of 17.0, and none of 300 was cut at its cap
   (CHOICE-A002 Run 1), so the control's cap does not bind. That the larger cap alone, with reasoning off, would change
   no answer is an inference from those figures, not a measurement (the control's cut count and output tokens are
   reported, rule 14). Under branch T and the confirmation case the control is task D, whose output tokens on A003's
   draw are placeholder 7 (E18). Why 2,048: "Why the units are this size" fixes the planning figures (E6) and shows that
   2,048 fits a unit's budget with 30 parts at them, with about 21 minutes to spare under branch N and the hypothesis
   case and about 6 under branch T and the confirmation case; the largest cap that fits is about 2,234 and 2,098 tokens,
   and 2,560 and 3,072 do not fit (E7). The warm-up's fit check (the worst prompt plus `max_tokens` within the
   16,384-token slot, `lab/warmup.py:371-377`) has room at 2,048.
5. **The deadline: 900 s per HTTP try and one retry, for both keys.** j1's endpoint gives each HTTP try 600 s
   (`lab/j1.py:125`, `DEADLINE_S`) and one retry (`:126`, `MAX_RETRIES`), which the client spends on a 429, a 5xx or a
   network failure and never on a timeout (`client.py`'s module docstring). A timeout is a transport failure: the
   record leaves the set, and more than `WITHHOLD_SHARE` 0.01 of a set's records leaving (two of 150) withholds the set
   (`lab/j1.py:128`); two consecutive timeouts stop the unit (`SERVER_DOWN_KINDS` and `BREAKER_AFTER` 2,
   `mycelic/collective/edge/extract.py:85-86`). A long reasoning must therefore end as a reply cut at the cap (item 6),
   counted against the arm, and never as a timeout. At the planning figures a call at the cap takes at most 674.4 s
   under branch N and the hypothesis case and 714.4 s under branch T and the confirmation case ("Why the units are
   this size", E7), so the endpoints of this cycle give each try 900 s (the routing document allows
   up to 3,600 s, `mycelic/collective/inference/routing.py:262`). For the control this deadline is longer than the
   600 s that A001 to A003 gave (`lab/j1.py:125`; CHOICE-A003 rule 6), and no recorded control call came near either:
   the negation task's 95th percentile was at most 16.4 s on any runner (A001 run 1, 16427.0 ms). Under branch T and
   the confirmation case the control is task D, whose per-call latency on A003's draw is placeholder 7, copied from
   CHOICE-A003's Runs entry by the amendment that reads the branch; the negation figures do not apply to it (E18).
6. **A reply cut at the cap: unanswered, counted and reported; no lab-side repair; the runtime's one repair kept.** With
   the reasoning separated, a generation cut at 2,048 tokens is expected to return `finish_reason` `length` and a
   content that is empty or holds a partial object; `extract_object` then finds no object, and the attempt is
   `json_invalid` (`runtime.py:340` and below). That expectation rests on the release's behaviour, which the timing
   check's calls 17 to 19 and clause S7 observe on a runner, the non-streamed path included (E11). The pinned runtime
   validates the content, not the finish reason (`runtime.py:320-350`): a first attempt cut at the cap whose content
   still parses into a valid reply is scored as answered, with no repair, and is counted apart (rule 14; E11). The
   judging path is `Runtime.run` (section 2.3 pins it for every lab-side variant), and `Runtime.run` answers an invalid
   first attempt with one repair on the same endpoint, at the same cap, with the note that the reply was cut at the
   token limit (`runtime.py:175-180`; `tasks.py:97-98`). That repair is part of the pinned path and applies to every arm
   of every cycle alike; a lab that skipped it for one arm would judge the two arms by two paths, and a lab that
   replaced `Runtime.run` would leave section 2.3's path. So this rule keeps it, and reads a cut as follows.
   **Unanswered**: when the repair is cut or invalid too, `Runtime.run` raises an `InferenceError` of a validation kind,
   which the J runners count as a model failure (`e1_extract.failure_class`,
   `mycelic/collective/experiments/e1_extract.py:799-803`): the question's verdict is `unknown`, scored and never
   correct, never dropped as a transport failure. **Counted and reported**, per arm and per kind, from the ledgers: the
   first attempts cut at the cap, the first attempts cut at the cap whose content still gave a valid reply (scored as
   answered, E11), the repairs made, the repairs cut, the repairs that gave a valid reply, and the questions left
   unanswered; and, as a count and never as an arm, how many of the verdicts a repair after a cut produced were correct
   (rule 14). **Never repaired by the lab**: no lab code re-asks a question, raises the cap, retries a cut, reads the
   reasoning for an answer or replaces a cut reply with anything; the reasoning text is never available to it (item 8).
7. **The warm-up, changed for the reasoning key only, with its own check.** Today the warm-up stops a server start
   when either of the probe's two replies carries a non-empty `reasoning_content` (`lab/warmup.py:427, 433-434`), and a
   warm-up call cut at the token limit skips its task's units (`:407-408`). Both rules stay unchanged for every
   manifest entry whose `server_args` do not hold `--reasoning on`: a-4b keeps them, and a test pins that its start
   still stops on any reasoning content. For an entry whose `server_args` hold `--reasoning on`, and only for it:
   - the probe's rule is inverted: the start stops unless **both** probe replies carry a non-empty `reasoning_content`
     (a new note, the server returned no separated reasoning); a server that reasons inline, or not at all, fails it;
   - a warm-up call or probe reply that ends with `finish_reason` `length` does not skip any unit; it is recorded with
     its output token count. A cut on the generated record says nothing a drawn record's cut would not, and 30 shards
     each risking a skip would put P's completeness on chance (trap 9);
   - the probe's raw requests take the task's deadline, 900 s, where today they take the warm-up's 600 s
     (`lab/warmup.py:99, 424`), and the warm-up call takes the same (its `WarmTask.deadline_s`, `:115`);
   - the warm-up record keeps, per probe reply, a boolean (reasoning present) and the reply's output token count,
     never the reasoning text, as it keeps the content's sha256 and never the content (`:430-431`).
   The fake server never runs a warm-up (`lab/shard.py:286-384`, only `_ModelServing` calls it), so dry runs are
   unaffected, and `BUILD-A004.md` says so. The check's warm-up and every judging shard's warm-up build their
   payloads the same way, from `_longest_record(pack, 5)` (the requests' seed 5, `lab/warmup.py:120-123`) and
   `judge_example` with the pack's first sorted predicate (`:131-145`), under branch T and the confirmation case
   with T's list for that predicate (A003's C12), so the check's probe result carries over to every R1 shard's start,
   CPU nondeterminism aside (trap 11) (E28).
8. **The lab's code never reads, keeps or prints the reasoning text; the server's log is the release's** (E12). The
   reasoning can quote and paraphrase the narrative at length. No lab code writes it to a console line, step summary,
   report, document, commit, ledger, verdict line or run file. This holds by construction on the judging path: the
   pinned client never reads `reasoning_content` from a non-streamed reply (item 3), so the ledger rows (which hold no
   content of any kind, `runtime.py:298-310`), the verdict lines (which hold the parsed two-answer object) and the run
   files cannot carry it; the timing check's streamed calls count reasoning deltas without keeping them, and its call
   20 holds one reasoning in memory for clause S6 only; the warm-up keeps a boolean. **Not stored in the shard
   artifact by the lab, as the replies are**: the replies are two enum answers, public and needed to recompute every
   verdict; the reasoning is free text that no reading of this rule uses, storing it would need a raw HTTP call beside
   `Runtime.run` (a second path that section 2.3 does not name), and a stored reasoning would invite readings made
   after the result (trap 15). The cost is that no one can audit what the reasoning did; the lifts, the answer counts
   and the yes rates of rule 14 are the only readings. **The server's own log is written by the pinned release, not by
   the lab** (its stdout and stderr, `OUT/server/server-<key>-<index>.log`, `lab/server.py:10, 340-342`): what the
   release writes there when it reasons, cuts a reply or fails to parse one is the release's behaviour, which this
   rule does not read from its source. The log stays in the shard artifact, capped at 1 MiB (`lab/server.py:66`), as
   in every earlier cycle, and is printed nowhere: the console keeps only a failed start's fixed problem
   (`lab/shard.py:375-379`), and no summary prints `log_tail`; the build verifies that no console line, step summary
   or report block carries the log or its tail. The repository is public: anyone can read its run logs, step summaries
   and artifacts (`docs/lab/README.md`, "Everything is public"), and any signed-in GitHub user can download a shard
   artifact for its `retention_days`, 30; CHOICE-A003 states the same access rule for its own artifact ("The terms",
   item 7). The timing check's clause S6 counts the reasoning text the release writes to the check shard's log, and
   a count above 0 removes the reasoning key's server logs from the judging run's artifacts (S6).
9. **Why a key and not a per-arm request setting.** The routing document can send `chat_template_kwargs` with every
   request (`routing.py:17-18, 287-296`; `client.py:506-507`), and some server templates read a thinking switch from
   them. Declined: the lever names the server flag; what the pinned release does with a template switch on a server
   started with `--reasoning off` is recorded nowhere in this repository; and the control would then be a-4b on a
   server set differently from every earlier cycle's, so it would replicate nothing. A key keeps the control the
   configuration A001 to A003 measured, but for the HTTP deadline: 900 s here against their 600 s (item 5; E18).
10. **The lab change, in one list.** The manifest key and its lock entry (item 1); the warm-up's rule for an entry with
    `--reasoning on` (item 7); the endpoints' 900-s deadline in the new kinds (item 5); the timing check's kind `jr`;
    the judging kind `j5`; for a `j5` plan, the workflow's run job gated on every provision job's success (E9); and, if
    clause S6 finds reasoning text in the check's server log, the reasoning key's server logs left out of the judging
    run's uploaded shard artifacts (E12). No pinned file changes (`lab/shard.py`, `lab/j1.py` to `lab/j3labels.py`,
    `lab/jt.py`, anything under `mycelic/`). "The build plan" lists every file.

## The base task and the arms: the pre-registered adaptation rule

This section answers the brief's item (2). It is written now with every branch complete. The branch is read by a
dated amendment, before the judging build starts, from CHOICE-A003's Runs entry for A003's result run, from the
named cells below, quoted as the entry prints them. A003's result run is the first A003 judging run in which a model
judged a drawn record (LOOP-48H section 6.3: a stop before any model call is not a result and the same request is
re-pushed; CHOICE-A003 rule 17: when a plan job printed and the run then failed before any model judged, the re-run's
draw is the result). A run stopped before any model ran is never a trigger: while A003 has no result run, the judging
build of this cycle does not start and the timing check is not pushed (it runs the branch's base task). If A003 were
abandoned without a result run by a recorded decision, this cycle would need a new choice file, since its four-entry
exclusion presumes an A003 draw a model judged. The cells are those of the result run's first aggregate; if that
aggregate printed no arms table, they are those of the re-aggregation declared in CHOICE-A003's Runs entry before it
ran (LOOP-48H section 6.3), and the branch amendment waits for it (E21).

**What may change after this file is read against A003's result** (E1). From the commit of the amendment "Amended
before any build, 2026-10-14" (E1 to E38), no amendment changes any of the following: the four cases, their order
and the cells they read; P under any case, except as E2 fixes now for a decision-9 call; the four arms and their
keys; the readings; the comparators and the pass test by branch; the seeds, n and parts; the cap, the deadline, the
planning figures, the timing check's calls and its clauses; the R0-only request of rule 18. An amendment written
after A003's result is recorded may only read the branch and fill placeholders 1 to 7, quoting the cells, or correct
a citation to its source; it states that the branch the cells select is unchanged by it. An amendment after the
timing check may only fill placeholders 8 to 13. The amendment that adopts a decision-9 call has no other content.
An amendment that fixes the lab-side cause of a stop before any model ran, as LOOP-48H section 6.3 requires, changes
none of these. A review finding whose fix would change any of these after A003's result is recorded is declined, and
that change is a new choice file on a fresh draw. Rule 17 says the same.

**The cells read** (CHOICE-A003, "## Runs", the judging run's entry; its rule 5 names the four arms D
`negation_terms`, Dr `negation_terms_mapped`, B `negation` and Br `negation_mapped`): the verdict words of D and B as
A003's rule 13 computes them (`lab.j4.verdict_word`, CHOICE-A003 C19), wherever the Runs entry prints them (the
verdict-word column of its arms table, or its verdict paragraph); and the lifts table's row `D_minus_B` with its 95%
interval. **A set of A003 is complete exactly when the verdict word of its as-answered arm (D for task D, B for task
B) is not `incomplete`** (E4). That word is `incomplete` when a part of the set did not finish, the set's display
class is not `model` or its `measurement` is not true, more than 0.01 of its records were dropped by transport
failures, or the balanced accuracy, the drafted-judge difference, the bound, the hand-judge difference or the
term-presence difference is not a number (`lab/j2.py:264-273`; C19). The lower end of `D_minus_B` is read as the
Runs entry prints it, to three decimals, and a printed 0.000 is not above 0 (E20). Ties fail every strict clause
(A002's B17).

Cases C and H read A003's rule-13 word (`lab.j4.verdict_word`, the arms table's "Verdict word"), the verdict A003
declared before its run. The word under lever 5's text alone selects no case. A D or B whose word under lever 5's
text alone is `pass_provisional` while its rule-13 word is not is recorded as a hypothesis and changes nothing here
(E5). The four cases are tried in this order, and the first whose condition holds is the branch:

1. **The confirmation case (C).** Task D's set is complete and D's verdict word is `pass_provisional`. Section 2.5
   requires that pair, named as the primary before this cycle's run, to pass again on this cycle's fresh draw. Then
   the base task is A003's task D, `judge_record_negation_terms` (A003 rule 3: A001's negation instructions with the
   terms sentence before the last sentence, the shipped schema, 256 output tokens, the payload with
   `question.predicate_terms`), imported unchanged from A003's kind; **P = (a-4b, `negation_terms`)**, the control arm
   R0 read as answered, which is A003's P exactly (the same task, cap, server setting and reading); R1, the same task
   with reasoning on, is the headline secondary. Success in the sense of section 2.5 is reachable in this cycle for
   (a-4b, `negation_terms`) alone and only by this confirmation; a pass by R1 stays provisional. P's pass test is
   A003's rule 13 with its added clauses, the test A003's P passed, with A003's comparators (rules 8 and 13 below).
2. **The hypothesis case (H).** The confirmation case does not hold, task B's set is complete and B's verdict word is
   `pass_provisional`. Section 2.5 makes a secondary that meets the pass test "a hypothesis for the next cycle's
   primary"; B reads the answers as answered, so the standing default allows it as primary. Then the base task is A001's
   negation task N, `judge_record_negation` (`lab/j2.py:80`, `NEGATION_TASK`; `:82-93`, `NEGATION_INSTRUCTIONS`;
   `:134-137`, `negation_task`, the instructions and 256 output tokens; the shipped schema, `judge_schema`,
   `mycelic/collective/edge/verify.py:147-150`) (E31), imported unchanged; **P = (a-4b, `negation`)**, the control arm
   R0 read as answered, which is A003's B exactly; R1 is the headline secondary. A pass of P here is a first pass of P
   as a primary, provisional.
3. **Branch T (the drafted terms).** Neither case above holds, both task D's and task B's sets are complete, and the
   lifts table prints `D_minus_B` with a 95% lower end above 0. Then the base task is A003's task D, imported
   unchanged, with the terms of term run 1 in the payload; **P = (a-4b, `negation_terms_reasoning`)**, R1 read as
   answered; R1 through the mapping is the headline secondary.
4. **Branch N (the negation task).** Every other recorded case, with neither case above holding: task D's set or task
   B's set incomplete (a lift over an incomplete set is not read, CHOICE-A003 C2's reading); or `D_minus_B` not
   printed, or printed with its lower end at or below 0. Then the base task is A001's task N,
   imported unchanged; **P = (a-4b, `negation_reasoning`)**, R1 read as answered; R1 through the mapping is the
   headline secondary.

A secondary of A003 read through the mapping (Dr or Br) that meets the pass test is recorded as a hypothesis and
changes no setting here: under the standing default a mapped reading cannot be a primary, and the mapped control arm
R0m of whichever base is selected measures it again on this draw (Dr's pair under branch T and the confirmation case,
Br's under branch N and the hypothesis case). **A decision-9 call** (E2): a chief-scientist call on decision 9 that
counts unclear as no, received before the judging request is pushed, is adopted by a dated amendment with no other
content. Under branch N it makes P = (a-4b, `negation_reasoning_mapped`); under branch T, P = (a-4b,
`negation_terms_reasoning_mapped`). R1, read as answered, becomes the headline secondary. In the confirmation case
and the hypothesis case P does not change, because section 2.5 names the pair that must pass again as A003 recorded
it, and the hypothesis is the secondary as it met the test; R0m's word is printed beside it. Any other call bears on
the next cycle. A mapped arm of A003 (Dr or Br) that met its pass test is never this cycle's primary, with or without
a call.

**Why these bases and no other.** N is the instruction family every cycle since A001 has carried: under it a-4b
answers instead of abstaining (unknown share 0.137 and 0.090 against 0.560 under the shipped instruction, A001 and
A002), and its mapped reading is the best a-4b figure on a fresh draw so far (0.687 [0.643, 0.730], A002 run 1),
replicated within sampling across two draws (0.653 and 0.687; CHOICE-A002 Run 1, the between-draw table). A003's task
D is N with the drafted terms; it becomes the base only when the recorded lift of the terms over N, read as P is read
(as answered), has its lower end above 0, the condition A003 used for E over N (CHOICE-A003, C2). The shipped
instruction is not a base: as answered it abstains on more than half of its answers (A001 arm A), its mapped arm was
level with N's mapped arm (A001: 0.667 and 0.653, D − B -0.013 [-0.063, 0.037]), and a different base would make the
control replicate no earlier control. The evidence and rationale tasks are not bases: both lowered a-4b's balanced
accuracy against N (A002 run 1: `E_minus_N` -0.093 [-0.137, -0.053]; `Rm_minus_D` -0.077 [-0.123, -0.030]).

**Under every branch the arms are the same four, on a-4b alone** (rule 5): the base task with reasoning on, read as
answered (**R1**) and through the mapping M (**R1m**); the base task with reasoning off, the control, read as
answered (**R0**) and through M (**R0m**). Model calls are made for R1 and R0 only, each on its own key; R1m and R0m
are readings of the same replies. The four arms isolate the lever, up to runner differences (trap 11; E37): R1 and
R0 differ in the server flag and in a cap that does not bind with reasoning off (item 4 above), on the same records,
questions and messages, each key on runners whose CPU models vary.

**a-4b is the only model run.** It is the only model whose answers separated positives from negatives in J001 (yes on
97 of 150 positives and 37 of 150 negatives, RESULTS run 9) and the only one any lever moved in A001 (lifts 0.270
[0.217, 0.327] and 0.360 [0.323, 0.393], CHOICE-A001 Run 1); a-0p5b and a-1p5b scored 0.467 to 0.497 on every A001 arm.
Their servers run with no reasoning flag at all (`lab/models.json:31, 47`, empty `server_args`), and lever 6's text
names a-4b. No figure justifies another model.

## The timing check (before the judging run)

This section answers part of the brief's item (3). The rule's time budget rests on two planning figures that no
reasoning-on call has measured ("Why the units are this size"), and on the server doing what item 1 of the previous
section says. A small model-only lab run checks all three before the judging run, on generated records, drawing
nothing.

- **The kind.** A new lab kind `jr`, one unit on the reasoning key `a-4b-r`, in one shard. It downloads no complaint
  archive, reads no earlier cycle's artifact and draws nothing. Under branch T and the confirmation case its plan job
  downloads the term artifact (lab run 38612800578) and verifies it as A003's judging plan job does (the full sha256
  `42c591cc…`, P_D's `vocabulary_hash` `c0260f0e…`, CHOICE-A003 "The terms", item 10), because the check runs the
  branch's reasoning task with the branch's payload shape.
- **The calls.** 20 calls of R1's TaskSpec (the branch's base task with `max_tokens` 2,048; call 19 aside), at
  boundary `site:lab`, data label `synthetic`, HTTP deadline 900 s, and `max_retries` 0 on the check's endpoint, so
  that a call is never tried twice (E25). Calls 1 to 16 go through `Runtime.single(..., stream=True)`
  (`runtime.py:191`: one attempt, no repair, streamed so that the client records the time to the first token and
  counts reasoning deltas, `client.py:211-220`). The payloads come from the vehicle pack's generated records
  (`generate(pack, 5, 1, weeks)`, as `lab/warmup.py:120-123` takes them), ordered as `_longest_record` orders them
  (longest narrative first, ties by `record_ref`), each built as `lab.warmup.judge_example` builds a payload, asked
  about the record's first entity, with the codes emptied as `lab.j1.payload` empties them (`lab/j1.py:278-282`):
  call k, for k from 1 to 12, is the k-th record with its own narrative, asked about the pack's k-th sorted
  predicate; calls 13 to 16 are records 1 to 4 with their narratives repeated to the pack's 6,000-character cap
  (`max_input_chars`, `docs/collective/replay/vehicles/pack/vocabulary.json:295`; repeated and cut by `_worst_text`,
  `lab/warmup.py:126-128`) (E31), asked about the pack's 13th to 16th sorted predicates (`unknown_or_other`, never
  asked, is skipped in the count; the hand pack has 26 specific predicates, CHOICE-A003 "The terms", item 6). Under
  branch T and the confirmation case each payload carries `predicate_terms` for its asked predicate, and calls 13 to
  16 carry the longest list in T by characters instead (A003's C12). Calls 1 to 12 thus carry user turns of at most
  629 characters (call 1's, derived here; under branch T and the confirmation case with the asked predicate's terms
  added), well below the longest judging calls' (about 3,100 characters, or about 6,300 with terms), and calls 13 to
  16 longer ones than any judging call's (6,289 characters, and up to about 9,500 with the longest list; derived
  here), so the two groups bracket the judging run's prompts (E8). After call 16 come four
  non-streamed calls (E11, E12):
  - **call 17** is call 1's payload and **call 18** call 13's, each through `Runtime.single(..., stream=False)`, the
    judging path's non-streamed client;
  - **call 19** is call 1's payload sent as raw HTTP, as the warm-up's probe sends one (`lab/warmup.py:416-427`), with
    `max_tokens` 16, a constant of `lab/jr.py`: no more than the control's median answer alone (16.0 output tokens,
    CHOICE-A002 Run 1), so a reply that counts its reasoning against the cap cannot finish inside it; the unit reads
    its `finish_reason`, whether its `reasoning_content` is non-empty, its content's length, whether that content is
    empty or begins, after white space, with `{`, and whether `extract_object` finds an object in it, and keeps none
    of the texts;
  - **call 20** is call 1's payload sent the same raw way at `max_tokens` 2,048; its `reasoning_content` is held in
    memory for clause S6 and never written.
  No drawn record and no complaint narrative is sent.
- **Measured per call**, from the ledger and the attempt (from the raw reply for calls 19 and 20): HTTP status,
  `finish_reason`, output tokens, latency, time to the first token (streamed calls), the count of reasoning and of
  content deltas (streamed calls), the user turn's characters, for calls 13 to 16 the time to the first token per
  character of the user turn, and whether the content parsed into a reply valid under the schema. The unit stores
  the parsed two-answer objects (synthetic) and nothing else of a reply.
- **The stop rule.** Each clause below except S6 is a stop clause:
  - **S1, the flag works and the reasoning is separated:** each of the 20 calls answered with HTTP 200 on its one
    try (a non-200 call fails S1, E25); calls 1 to 16 each streamed at least one reasoning delta; calls 19 and 20
    each carried a non-empty `reasoning_content`; and the check shard's warm-up passed its probe (item 7 of the
    previous section).
  - **S2, the answer is read from the content:** every call of 1 to 18 that ended with `finish_reason` `stop`
    returned content that parsed into a reply valid under the task's schema, the non-streamed calls 17 and 18
    included (E11).
  - **S3, the cap leaves room:** at most 2 of calls 1 to 16 ended with `finish_reason` `length`. S3 is a smoke bound
    on generated records, not an estimate: with 16 calls it passes with a probability of about 0.35 when the true
    cut share is 0.2 and about 0.79 when it is 0.1 (derived, binomial); the judging run's cut share is read only from
    rule 14's counts, and the time budget is sized for every call cut (E17).
  - **S4, the time fits the design on this runner** (E8; each a failing condition):
    - **S4a, the prompt rate:** on each of calls 13 to 16, the time to the first token divided by the characters of
      the call's user turn (the data block; the system text is the one calls 1 to 12 sent) is at most 15 ms; and on
      each of calls 1 to 12 the time to the first token is at most 60 s;
    - **S4b, the decode rate:** the largest decode time per output token, (latency − time to the first token) /
      (output tokens − 1), over calls 1 to 12 with at least 256 output tokens (when none has 256, that of the call
      among them with the most output tokens), is at most 0.32 s under branch N and the hypothesis case and 0.30 s
      under branch T and the confirmation case, the design's tolerances ("Why the units are this size");
    - **S4c, the deadline at the longest prompts:** on each of calls 13 to 16, the time to the first token plus
      2,047 times the call's own decode time per output token (S4b's figure when the call has fewer than 256 output
      tokens) is at most 780 s, the 900-s deadline less 120 s;
    - **S4d, the latency:** every call's latency is at most 780 s.
    The planning figures (0.30 s per output token; 60 s and 100 s to the first token of a judging call) are printed
    beside the measured figures and stop nothing. A check whose calls all reason briefly measures the decode only at
    that length; the planning figure and the margins of "Why the units are this size" then carry the rest, and R1's
    per-call figures in Runs measure it. S4 bounds the check's CPU model only. For the judging run's other
    runners this file compares the design's tolerances (about 0.327 and 0.307 s per token) with the slowest recorded
    a-4b decode, 0.238 s per token at an E3 prompt of about 10,690 characters (RESULTS Run 5; derived), and with
    decoding 22 to 25% slower, in tokens per second, when that prompt doubled (RESULTS Runs 1 and 5): the tolerances
    are 37% and 29% above that figure (derived), room for contexts longer than any record measured, which R1's
    per-call figures in Runs then measure on every runner; and it bounds first tokens by the 15-ms rate, about 1.5
    times the rate derived for the slowest recorded prefill, the AMD EPYC 7763's ("Why the units are this size").
  - **S5, the cap binds the reasoning** (E10): no call reports more output tokens than its cap (2,048, or 16 for call
    19); and (i) on calls 1 to 16, `tokens_out` and `ttft_ms` are not null, and on calls 17 to 20 `tokens_out` is not
    null; (ii) on calls 1 to 16, `tokens_out` is at least the reasoning deltas plus the content deltas the client
    counted (`client.py:211-220`): each non-empty delta carries at least one generated token, and an event carrying
    both kinds, which a separated reply is not expected to send, counts in both and fails safe; (iii) every call that
    ended with `finish_reason` `length` reports `tokens_out` exactly its cap.
  - **S6, the reasoning in the server's log** (E12; counted, not a stop): after call 20 and before the shard seals,
    the unit searches the check shard's server log (`OUT/server/server-a-4b-r-<index>.log`, `lab/server.py:10, 245`)
    for every 40-character run of call 20's `reasoning_content`, and prints how many runs it found, never a run. If
    the count is above 0, build 2 makes the workflow leave `server/*.log` out of every `a-4b-r` shard's uploaded
    artifact in the judging run (`.github/workflows/mycelic-lab.yml` is not pinned), keeping the start record's
    printable lines (build information, backend lines, the first `system_info` line); the check's own log holds
    reasoning about generated records only.
  - **S7, a cut inside the reasoning is not an answer** (E11): call 19 ended with `finish_reason` `length`, carried a
    non-empty `reasoning_content`, and its content is empty or begins, after white space, with `{` and holds no JSON
    object (`extract_object` raises on it, `jsonparse.py:105-120`). A content that begins otherwise is reasoning text
    in the content, which a cut at the judging cap could carry into the parse (trap 6).
  What a failure does depends on the branch (rule 18; E3). Under branches N and T, if any stop clause fails, or the
  unit stops before its 20th call, the judging run of this rule is not pushed: the check is recorded in Runs with its
  figures, and lever 6 needs a new choice file, whose settings may use the check's figures, since they come from
  generated records and never from a draw. In the confirmation case and the hypothesis case the judging run does not
  depend on the check's outcome: if the check passes, the four arms run as declared; if it fails, or stops before
  its 20th call for any reason other than infrastructure, the judging run is pushed with R0 and R0m only (rule 18).
  A check stopped before any call for an infrastructure reason (a provision, a cache or a runner failure) may be
  pushed again unchanged once, as LOOP-48H section 6.3 allows; if the re-push also stops before its 20th call, for
  any reason, the check has failed (E3). A warm-up stop for missing reasoning is S1 failing, not infrastructure. A
  passing check is not repeated for better figures, and a failing one is not repeated to pass.
- **What it prints**: the counts of each clause and the quantiles of each measured figure (median, 95th percentile
  and maximum of the output tokens, the latency, the time to the first token, the decode time per token and, for
  calls 13 to 16, the time to the first token per character), call 19's content length and whether it was empty,
  S6's count, the runner CPU model beside every recorded a-4b figure for that CPU model (E8), the reasoning key's
  provision digest beside a-4b's lock digest, and each clause's outcome. Never a reply, never reasoning, never a
  term, never a narrative, never a payload (E38).
- **Its budget**: "Why the units are this size" (E7: `minutes` 300 and `job_minutes` 330). Its figures fill
  placeholders 8 to 13 of "Placeholders" by a dated amendment before the judging request is pushed, and that
  amendment may do nothing else (E1).

## The data and the draw

**NHTSA complaints, as J001, A001, A002 and A003 drew them, and nothing else in this cycle.** The reasons are
A001's to A003's: the pinned definition names them; 150 records give 300 questions and 150 clusters with a balanced
accuracy standard error near 0.035; J001's pipeline (`lab/j1.py`, `lab/goldlabels.py`) is pinned and importable, so
the questions, the comparators and the scoring are J001's code, called unchanged; A001's, A002's and A003's exclusion
machinery is importable unchanged (`lab/j2labels.py`, `lab/j3labels.py`, and A003's `lab/j4labels.py` once merged);
what is new (A003's layout, A003's rebuild with its split, the stop rules over four cycles) lives in a new module
`lab/j5labels.py`, pinned equal to the earlier checks on the earlier layouts by a test.

**A fresh, verifiable draw (section 2.3), with four earlier cycles excluded.** The lab draws
`random.Random("nhtsa:<seed>").sample(eligible, n)` from a complaint file downloaded live in the plan job
(`lab/goldlabels.py:90, 133`), so a new seed alone gives no disjointness, and record refs may never be committed. The
exclusion happens on the runner, in the j5 plan job, by eleven sources:

1. **J001: J1 (the artifact) and R1 (the rebuild)**, exactly as A003's item 1: `lab-plan-37996675559-1` (run
   37996675559; retention until 2026-11-08T22:00:03Z, CHOICE-A001:119), `prereg/j1/labels.jsonl` with sha256 prefix
   `bc6d092d8bca`, through `lab.j2labels.earlier_refs` unchanged; R1 is `lab.j2labels.rebuild_refs(fetch,
   "bc6d092d8bca")` unchanged, the seed-1 n-150 draw from the day's file.
2. **A001: A1, R2 and K2**, exactly as A003's item 2: `lab-plan-38114522160-1` (run 38114522160; retention until about
   2026-11-10), `prereg/j2/labels.jsonl` prefix `623780b8a1de` through `lab.j3labels.earlier_labels_refs` unchanged;
   the chain `prereg/j2/excluded.jsonl` prefix `9a948cbf2cd9` (300 refs) through `lab.j2labels.earlier_refs`; R2 the
   seed-2 rebuild over the eligible list less J001's set (`lab.j3labels.rebuild_a001_refs`), attempted only once
   J001's set is established.
3. **A002: B1, R3 and K3**, exactly as A003's item 3: `lab-plan-38439612256-1` (run 38439612256, artifact id
   11833749961, expiring 2026-11-12T17:34:56Z), `prereg/j3/labels.jsonl` prefix `0a64a62d94a7`, questions prefix
   `84486fa38568`, the chain `prereg/j3/excluded.jsonl` prefix `65d51f0fca51` with 450 lines (all from CHOICE-A003,
   C3, from A002 run 1); R3 the seed-3 rebuild over the eligible list less J001's and A001's sets, through A003's
   `lab.j4labels.rebuild_a002_refs` unchanged, attempted only once those two sets are established.
4. **A003: C1 (the artifact), R4 (the rebuild) and K4 (the chain).** The entry names A003's result run, its facts
   placeholders filled by the amendment that reads the branch, each labelled "from A003's result run": its run id
   and attempt, its plan artifact `lab-plan-<run id>-<attempt>` with the artifact's id and expiry, the labels file
   `prereg/j4/labels.jsonl` and its sha256 prefix, the questions file `prereg/j4/questions.jsonl` and its prefix, the
   chain `prereg/j4/excluded.jsonl` with its prefix and line count (A003's item 6: 600 lines plus A003's split
   removals), A003's two split counts (by record id and by text, its labels record's `excluded_runs` entry of kind
   `split`), and the complaint file's prefix on A003's day. The plan job downloads the artifact by a fourth step
   `earlier-4` of the earlier steps' shape. `lab.j5labels.earlier_labels_refs` checks A003's layout as A003's code
   checks A002's: the labels file must hash to A003's labels prefix, `labels.json` must say `source` `nhtsa`, `n` 150,
   `seed` 4, the questions file's refs must equal the labels' and the file must hash to A003's questions prefix (a
   code constant `A003_QUESTIONS_SHA256_PREFIX`, as A003 holds A002's), every file cross-checked against the
   artifact's manifest, nothing printed but counts and outcomes: that gives **C1**. `lab.j5labels.earlier_chain_refs`
   on `prereg/j4/excluded.jsonl` gives **K4**. **R4** rebuilds A003's draw on the day: A003's split removals
   recomputed exactly as A003's plan job computed them (CHOICE-A003, "The data and the draw", item 8: the drafting set
   rebuilt from the day's archive with term run 1's drafting files, removal by record id and by text, through A003's
   functions unchanged), then `lab.j2labels.draw(eligible, J001_set | A001_set | A002_set | split, 150, 4)`, verified
   when the bytes of the sampled label lines hash to A003's labels prefix; attempted only once J001's, A001's and
   A002's sets are established and the term artifact verifies (else `not attempted`).
5. **The stop rules, read for four cycles** (A003's item 4 generalised).
   - **The sources.** A cycle's set is established by its own sources, the union of its artifact's and its rebuild's
     refs when both verified (J1 or R1; A1 or R2; B1 or R3; C1 or R4). A cycle with no verifying source of its own is
     established from a verified chain less the sets of the other cycles that chain covers, each of those established
     without that chain (K2 covers J001 and A001; K3 covers J001 to A002; K4 covers J001 to A003 and A003's split
     removals, which are subtracted too when the chain is read for a cycle). A chain establishes at most one cycle,
     and a chain alone, with no other cycle established, establishes none. Several chains may each establish one
     cycle in the same plan job when each cycle a chain's reading draws on is established without that same chain
     (A003's C20); the by-chain reading is printed per cycle with the chain it used. A003 established by K4 alone (C1
     and R4 both unverified) is K4 less the other three sets, which holds A003's records and its split removals
     together: both are excluded, and the split-count consistency check is not computed and is printed as not
     computed (E22).
   - **The consistency counts**, each printed and stopping above 0: J1 and R1 equal when both verified; A1 and R2; B1
     and R3; C1 and R4; every verified source of a cycle inside every verified chain that covers it; each chain equal
     to the union of the sets it covers when all of them are established by their own sources, K4 with A003's split
     removals added: K4 less the four sets has exactly as many refs as A003's two split counts add up to.
   - **A003's split removals** are excluded when known: from K4 (K4 less the four sets) when K4 verifies, and from
     R4's recomputation when R4 ran. They are records no model ever judged, removed by A003 because the drafted terms
     were learned from them or from their text. Under branch N and the hypothesis case no payload carries terms, so
     their absence from the excluded set (K4 and R4 both unverified) is printed and is not a stop; under branch T and
     the confirmation case the plan job removes the drafting set again on the day (item 8), which removes them anyway.
   - **The excluded set is the union of every verified source's refs** and of A003's split removals when known: 600
     refs plus A003's split removals expected, the count printed.
   - **The plan job stops** before any draw and any model, and the loop asks, when any cycle's set is not established
     or any count is above 0.
   - The record ref is the complaint file's own id column (`mapping.json`, `record_ref`), the same in every version of
     the file. The complaint file's sha256 is printed beside A001's `a5a5de2d31f3…`, A002's `216ecb36f9f5…` and
     A003's day's prefix (a placeholder).
6. **The draw.** `lab.j2labels.eligible_records` builds the eligible list exactly as `lab.goldlabels.nhtsa_lines`
   builds it (the makes and the window of `goldlabels.py:49-50` verbatim; a narrative, one vehicle, at least one
   specific component code; codes removed; sorted by `record_ref`); `lab.j2labels.draw(eligible, excluded, 150, 5)`
   removes the excluded refs (and, under branch T and the confirmation case, this day's split removals of item 8) and
   samples `random.Random("nhtsa:5").sample` of 150, sorted by `record_ref`; `lab.j2labels.build_labels` unchanged
   writes the labels and their record (kind `lab_j2_labels`; `exclusion.excluded_runs` one entry per cycle in seed
   order with the outcomes and counts of its sources, and an entry of kind `split` for A003's removals and, under
   branch T, one for this day's, counts only), the complaint file's sha256, `available`, the excluded count, the
   eligible count after exclusion (on A001's and A002's days, `available` was 58,319, CHOICE-A001 Run 1 and
   CHOICE-A002 Run 1; the day's file decides) and the overlap (0, else the plan job stops).
7. **The chain, carried forward.** The plan job writes `prereg/j5/excluded.jsonl`: one `{"record_ref"}` line per ref,
   sorted, the union of the excluded set and this draw's 150 (750 lines plus the split removals expected), and prints
   its sha256 prefix and line count. The next cycle names this run's artifact, this file and that prefix. For this
   cycle the list of earlier cycles is: J001, run 37996675559, labels `bc6d092d8bca`, seed 1; A001, run 38114522160,
   labels `623780b8a1de`, seed 2, chain `9a948cbf2cd9`; A002, run 38439612256, labels `0a64a62d94a7`, seed 3, chain
   `65d51f0fca51`; A003, its result run, labels, seed 4 and chain (placeholders).
8. **Under branch T and the confirmation case, the received-date split, enforced on the day**, exactly as A003's
   item 8 enforces it, with term run 1's term set and drafting files: the drafting set rebuilt from the day's archive
   and compared with term run 1's (`J5_SPLIT_VIOLATED` when the archives are equal and the figures are not); removal
   by record id and by text before the draw; the two tripwires after it. Under branch N and the hypothesis case no
   term is in any payload and the split is not recomputed (item 5 covers A003's removals).
9. **What is printed**: one fixed line with `available`, the excluded count (and A003's split removals beside it),
   the eligible count after exclusion, the overlap (0 expected), the outcome of each of the eleven sources with any
   by-chain reading named and every consistency count computed, the new labels' sha256 prefix, the questions' prefix,
   the chain file's prefix and line count, the complaint file's prefix; under branch T and the confirmation case the
   split's line and the term check's line; then the comparators' figures, before any shard starts. Those figures are
   never a reason to redraw (LOOP-48H section 6.3). Never a ref, never a term, never a narrative. If fewer than 150
   eligible unexcluded records remain, the plan job stops, and no further fresh draw is pre-registered.
10. **The plan job's time** (E30). The j5 plan job does what A003's judging plan job does and adds R4 (A003's split
   recomputed and A003's draw rebuilt from the day's archive) and a fifth artifact download. A002's plan job ran from
   17:33:30 to 17:34:58 UTC (plan job 115442458382, lab run 38439612256, CHOICE-A002 Run 1), and term run 1's
   `prereg` step, which rebuilt the drafting set and drafted the terms, took 61.2 s (CHOICE-A003, T9). A few minutes
   are expected against the `prereg` step's 20 (`.github/workflows/mycelic-lab.yml:77`) and the plan job's 30
   (`:28`): expected, not measured; A003's judging plan job, once it has run, is the nearest figure, and the Runs
   entry records this one's.

## The rule

1. **Records: 150 fresh NHTSA complaints, labels seed 5.** `lab.j5labels` over `lab.j2labels`, `lab.j3labels` and
   A003's `lab.j4labels`, source `nhtsa`, the pack `docs/collective/replay/vehicles/pack`, the makes, the window and
   the eligibility rule of `lab.goldlabels` verbatim, the four earlier cycles' 600 records and A003's split removals
   excluded as "The data and the draw" says, `random.Random("nhtsa:5").sample` of 150. Each label record has its codes
   removed (`lab/j1.py:278-279` refuses one that still holds them).
2. **Questions: two per record, J001's code, seed 5, 30 parts.** `lab.j1.build_questions(pack, labels, seed=5,
   parts=30)` with the hand pack, unchanged (`lab/j1.py:218`; parts 1 to 30, `:222-223`): the positive is one filed
   predicate, the negative follows the other records' positives (`NEGATIVE_DRAW`, `EXCLUDED`, `TWINS` unchanged,
   `lab/j1.py:118-130`). Record `i` of 150 is in part `i * 30 // 150 + 1`: 5 records and 10 questions a part. The
   questions file's sha256 is preregistered. 150 positives and 150 negatives: 300 questions per key.
3. **The tasks: two TaskSpecs, one base.** Every task reads the shipped payload (`lab.j1.payload`, codes empty;
   under branch T and the confirmation case with `question.predicate_terms` added as A003's task D adds it) through
   `Runtime.run(task, payload, schema, ref=...)`, and every verdict is the pinned `verify.decide` over the one record
   (`lab.j1.verdict`) applied to the reply's two answers, exactly as J001 to A003.
   - **The control task, R0's**: the base task of the branch, imported unchanged: `lab.j2.negation_task()` under
     branch N and the hypothesis case; A003's task D, `judge_record_negation_terms`, under branch T and the
     confirmation case. Its cap is 256.
   - **The reasoning task, R1's**: the control task with `max_tokens` 2,048 and nothing else changed:
     `dataclasses.replace(control, max_tokens=2048)`; the same name, data class, instructions, schema and payload.
   - **Codes never reach the judge.** The payload's codes list is empty; the task texts name no code, category or
     filed label; the reasoning is generated from the payload and the model's weights, and no arm puts codes in the
     payload (trap 5's caveat; E32). Under branch T `predicate_terms` holds words of
     other records' narratives drafted before the cutoff, nothing of the judged record (CHOICE-A003, rule 3, C9).
4. **The reading, declared now: M** (`lab.j2.MAPPING`, `mapped_reply`, unchanged, `lab/j2.py:101, 162-169`: a reply's
   `describes_predicate` `unclear` is read as `no`; `mentions_entity` left as answered). It reads the reply only.
5. **The arms: four, declared now, all on a-4b; one primary pair.**

   | Letter | Arm under branch N and case H | Arm under branch T and case C | Task, key | Reading before the pinned `decide` |
   |---|---|---|---|---|
   | R1 | `negation_reasoning` | `negation_terms_reasoning` | the reasoning task on `a-4b-r` | as answered |
   | R1m | `negation_reasoning_mapped` | `negation_terms_reasoning_mapped` | the same replies | M |
   | R0 | `negation` | `negation_terms` | the control task on `a-4b` | as answered |
   | R0m | `negation_mapped` | `negation_terms_mapped` | the same replies | M |

   **The primary pair**: branch N, P = (a-4b, `negation_reasoning`); branch T, P = (a-4b, `negation_terms_reasoning`);
   the confirmation case, P = (a-4b, `negation_terms`); the hypothesis case, P = (a-4b, `negation`). A decision-9
   call adopted as "The base task and the arms" says makes P R1m under branches N and T, with R1 the headline
   secondary, and changes nothing in the two cases (E2). The model of
   every pair is a-4b; the key that serves an arm is the arm's server setting, held by the kind's constants and never
   by the request. Every other arm is a secondary, reported with the same statistics, including rule 13's pass test;
   a secondary that meets it is a hypothesis for the next cycle's primary, never a result. The headline secondary is
   R1m under branches N and T (its verdict word printed beside P's, labelled "the primary under the other reading of
   the section 7 default", A002's B1 form) and R1 in the two cases (labelled "the lever's arm"), with R0m's word printed
   beside as the other reading.
6. **The judging path: the product's, through the lab's pinned server.** As A003's rule 6, with the deadline of
   "What reasoning on is", item 5: `Runtime.run` with its one repair after an invalid reply and no escalation, at
   boundary `site:lab`, data label `public`, through the lab's pinned model server on one runner; HTTP deadline 900 s
   and 1 retry; temperature 0 on every call; the server seed is the unit's seed, 5, so one run per arm. The routing
   document routes the task name to the unit's one endpoint, without escalation; a unit refuses a routing that routes
   it elsewhere or escalates, or an endpoint whose key is not the arm's.
7. **The model and its two server settings.** a-4b alone: key `a-4b` with `--reasoning off` (`lab/models.json:63`,
   unchanged) for R0 and R0m; key `a-4b-r` with `--reasoning on` (the same file, verified by the same digest) for R1
   and R1m. One slot of 16,384 tokens per server, as every earlier J cycle.
8. **The comparators, in the plan job, before any model runs.** Under branch N and the hypothesis case, section 2.4's
   three: the lexical judge (`lab.j1.lexical_verdicts` over the hand pack, `lab/j1.py:314`), the record-blind control
   (`lab.j1.prior_verdicts`, `:335`, `PRIOR_TOP` unchanged) and the predicate-only bound (`lab.j1.prior_bound`,
   `:346`). Under branch T and the confirmation case, A003's five through A003's functions unchanged: the drafted
   lexical judge (over P_D rebuilt from term run 1's `terms.json`, refused unless P_D's `vocabulary_hash` is
   `c0260f0edab4638915b1fd0065e236ca20b497c0b217495c77adf018800fe579`), the hand-pack lexical judge, the drafted
   term-presence judge, the control and the bound. Each is scored on every record and, in the aggregate, on each arm's
   scored records; the plan job prints their figures, the lexical judges' with sensitivity, specificity and unknown
   count. Those figures are never a reason to redraw. The comparators are not arms and are not counted.
9. **Scoring, against the filed codes,** by `lab.j1.score` unchanged (`lab/j1.py:443`) on each arm's j1-shaped view:
   sensitivity, specificity, balanced accuracy and the unknown share, each with a percentile-bootstrap 95% interval
   over records, `stats.cluster_bootstrap_mean`, B 10,000, `bootstrap_seed` 5 (seed string `j1:5`), a record's two
   questions drawn together, the same draws for every judge and arm scored on the same records. A model failure (no
   valid reply after the repair, a cut reply among them) is `unknown` and counts. A transport failure leaves that
   record out of that key's set and of both arms on it; the comparators are compared on the same records. If more than
   `WITHHOLD_SHARE` 0.01 of a set's records leave, the set and its arms are withheld (`lab/j1.py:128`).
10. **Paired differences,** by `lab.j1.paired` unchanged (`lab/j1.py:518`; B 10,000, seed `j1:5`), over the records
    both sides score: each arm minus the lexical judge (four; the pass test's clause; under branch T and the
    confirmation case the drafted lexical judge, with each arm minus the hand judge and minus the term-presence judge
    beside, eight more); the lifts: `R1_minus_R0` (the lever: reasoning on, answers as answered; under branches N and
    T, P over its control), `R1m_minus_R0m` (the lever under the mapping), `R1m_minus_R1` (the mapping under
    reasoning) and `R0m_minus_R0` (the mapping without reasoning: A001's D − C, A002's `D_minus_N`, A003's Dr − D or
    Br − B, replicated).
11. **The primary pair, the headline arm, and the exploratory rest.**
    - **P** as rule 5 names it per branch. The 86 verdict of rule 13 is read on P alone. The aggregate computes the
      verdict word for every arm, and the Runs entry prints the headline secondary's word beside P's.
    - **How P meets "chosen from earlier recorded results before the run."** The model is the only one any lever
      moved. Under branches N and T the arm is lever 6, the next in the pinned order, on the base the recorded result
      of A003 selects by a rule fixed now, read by the shipped `decide` on the answers as answered, which the standing
      default allows as primary. In the confirmation case the pair is A003's P, as section 2.5 requires; in the
      hypothesis case it is A003's passing secondary read as answered, as section 2.5 allows.
    - **The headline arm is P's arm**: its balanced accuracy interval read against the lexical judge's balanced
      accuracy on the same records as J001's rule 7 reads it: **better** only when the interval's lower end is above
      it, **worse** only when its upper end is below it, otherwise **not told apart**; under branch T and the
      confirmation case the same words against the hand judge and the term-presence judge beside it.
    - **Every other arm is exploratory.** The lifts are read as "helps" when the lower end is above 0, "hurts" when the
      upper end is below 0, otherwise "not told apart"; they decide nothing toward 86.
12. **The comparison count.** This cycle makes 4 (model, arm, cycle) comparisons: 4 arms × 1 model × 1 cycle, the
    controls counted, under every branch. 25 were made or declared before it (12 after A001, 9 declared by A002, 4
    declared by A003, CHOICE-A003 rule 12). The running count since the loop began is therefore **29** after this
    cycle. Under rule 18's R0-only request (the two cases, after a failed timing check) this cycle makes 2 (R0 and
    R0m), and the count is **27** (E3). The request carries `comparisons_before` 25 in both forms; the Runs entry
    prints the count. The 25 is the rules' declared sum (12 + 9 + 4); if A003's aggregate prints another running
    count, that figure is recorded as a defect of A003's aggregate and changes neither `comparisons_before` nor the 29
    or 27 (E23). No multiplicity correction is
    applied, for A002's reason (B15): the 86 verdict is read on P alone and the one headline is P's arm, so there is
    one decision per cycle; the three other verdict words are exploratory and uncorrected, which is why a secondary
    that passes is a hypothesis and never a result, and the running count is printed so that a reader can discount a
    sampling peak across cycles. The comparators are not arms and are not counted.
13. **The verdict rule.** Copied verbatim from LOOP-48H section 2.5 (lines re-wrapped; the words are its). Its "§7"
    and "§9" are that prompt's sections 7 (what needs a human) and 9 (the final report).

    - **One primary pair.** Each CHOICE file names exactly one primary pair P = (model, arm), chosen from earlier
      recorded results before the run. Every other (model, arm) pair in the run is a secondary, reported with the
      same statistics; a secondary that meets the pass test is a hypothesis for the next cycle's primary, never a
      result.
    - **Complete.** A verdict is read only when every planned record of the draw is scored, `measurement` is true,
      and the share of records dropped by transport failures is at most `WITHHOLD_SHARE` 0.01
      (`lab/j1.py:128, 503-515`). Otherwise the cycle is **incomplete**: record it, and the next cycle needs a new
      rule on a fresh draw.
    - **Pass test on P:** BA point estimate ≥ 0.860 **and** the paired difference to the lexical judge has its 95%
      lower end above 0 **and** the model's BA 95% lower end is above the predicate-only bound's point value computed
      on the same draw **and** the unknown share is reported.
    - **Success (86 reached):** P passes in this cycle **and** P, named as the primary in the next cycle's CHOICE
      file before that run, passes again on that cycle's fresh draw. A first pass is **provisional**, shipped path
      included; only a confirmed success ends the loop.
    - **Partial:** BA ≥ 0.860 but an interval clause fails; or BA < 0.860 but P beats the lexical judge with the
      lower end above 0. Report both numbers and continue.
    - **Failure:** BA below the lexical judge, or the interval to lexical includes 0. Report plainly: "P scored BA b
      [lo, hi]; gap to 0.860 is g; lexical l [lo, hi]; bound p." Continue with the next lever.
    - **Multiplicity.** Every CHOICE Runs entry and the §9 report print the running count of (model, arm, cycle)
      comparisons made since the loop began. With 150 records the BA standard error is about 0.035, so a sampling
      peak can cross 0.860 on some draw; the confirmation rule exists for that reason.
    - **Secondary (report when available, never promote):** the chance-corrected share (BA_site − bound) /
      (BA_central − bound) against a hosted central reader, only when §7 allows one on real text.

    **How the words are computed.** Under branch N and the hypothesis case, by `lab.j2.verdict_word` and
    `lab.j2.pass_test` unchanged (`lab/j2.py:238, 264`), in the order **`incomplete`, `pass_provisional`, `partial`,
    `failure`** (A001's A5). Under branch T and the confirmation case, by A003's `lab.j4.verdict_word` and
    `lab.j4.pass_test` unchanged (CHOICE-A003, rule 13 and C19): "the lexical judge" in the pass test's second clause,
    in Partial and in Failure is the drafted lexical judge, and the hand clause and the term-presence clause are
    required as A003 requires them, so that a model given the drafted terms must beat word matching with the same
    terms, with and without the lexical judge's negation handling, and the hand judge; P's word under lever 5's text
    alone is printed beside, as A003 prints it. For this cycle, read as A001's A5 and A6 read it: "complete" is read
    per key's set (all 30 parts finished for that key, `measurement` true, `records_not_judged` 0, the dropped share at
    most 0.01); P's set is R1's under branches N and T and R0's in the two cases, and "the same draw" is the 150
    records P's set scored. A missing figure of any comparator, the bound or the interval makes the word `incomplete`.
    Equality fails every strict clause. Where the headline and the paired clause disagree, the word follows the paired
    clause. Success in the sense of the rule cannot be reached in this cycle, except for (a-4b, `negation_terms`) in
    the confirmation case.
14. **Reported beside the verdict, deciding nothing.** For every arm: sensitivity, specificity, accuracy and the
    unknown share with intervals; the count of each answer pair, for positives and for negatives, as answered and
    after M; the verdict counts; failures by kind; per predicate, how often each judge confirmed it, for positives and
    for negatives (`by_predicate`); the comparators on the same records. **For the lever**: the yes rate of
    `describes_predicate` on positives and on negatives for R1 and R0 (trap 2); the agreement of R1's and R0's verdicts
    per kind (the share of questions with equal verdicts) and the four-way table of their verdict changes per kind
    (confirm to refute, refute to confirm, either to unknown, unknown to either); the share of `unclear` answers per
    key. **For the cap**, per key and per kind, from the ledgers: first attempts cut at the cap, first attempts cut at
    the cap whose content still gave a valid reply (scored as answered: the pinned runtime validates the content, not
    the finish reason, `runtime.py:320-350`; E11), repairs made, repairs
    cut, repairs that gave a valid reply, questions unanswered after the repair, and how many of the verdicts that a
    repair after a cut produced were correct (a count, never an arm); output tokens per attempt (median, 95th
    percentile, maximum: reasoning plus answer for R1, the answer for R0). **For the time**: seconds per judge call per
    key (median, 95th percentile, maximum) with the runner CPU model, a runner number and not site hardware; for R1
    also seconds per output token per call; each shard's wall time and each unit's budget used; the warm-up's probe
    booleans and `repeat_identical` per shard. **Beside earlier cycles**: R0 and R0m beside the matching arms of A003's
    result run (B and Br under branch N and the hypothesis case; D and Dr under branch T and the confirmation case),
    and beside A001's and A002's negation arms, labelled a between-draw difference. Under branch T and the
    confirmation case: A003's agreement readings of R1 and R0 with the drafted lexical judge and the term-presence
    judge (CHOICE-A003, rule 14).
15. **Units and limits** (sized in "Why the units are this size"):
    - one unit per key and part: **60 units**, 30 on `a-4b` and 30 on `a-4b-r` (30 on `a-4b` alone under rule 18's
      R0-only request); a unit judges its part's 10 questions once, with its key's one task;
    - **P's key's shards first** (E19): build 2 orders the run job's matrix with P's key's shards first, the `a-4b-r`
      shards under branches N and T and the `a-4b` shards in the two cases, for A003's rule 15's reason: a late stop
      (a runner-pool problem, a cancelled workflow) then loses the control before P's set. Today `pack_shards`
      numbers shards by key in sorted order (`lab/plan.py:279-286`), which would put `a-4b` first; `lab/plan.py` is
      not pinned;
    - each unit has 300 minutes (the request's `minutes`) and runs alone in its shard: **60 shards**; `job_minutes`
      330 gives a shard capacity of 305 and a job timeout of 325 minutes (`lab/plan.py:304`, the planned minutes plus
      `SHARD_OVERHEAD_MINUTES` 25, `lab/request.py:238`), below the hosted runner's six-hour job limit, which
      `lab/shard.py:139` mirrors as `MAX_JOB_MINUTES` 360; `max_parallel` 16, the lab's cap (`lab/request.py:1148`);
    - the unit's budget is the smaller of 300 minutes and the shard's remaining time (the job start plus 315 minutes,
      the timeout less `JOB_TAIL_MINUTES` 10, `lab/shard.py:138`, less `DEADLINE_MARGIN_S` 120 s, `:133`) less
      `SIM_BUDGET_MARGIN_S` 30 s (`lab/units.py:205`, used at `:1250-1251`); the warm-up runs before it within the
      shard's time less 180 s (`lab/shard.py:370-374`);
    - each unit stops at its budget and keeps what it judged (`stopped: budget`); a call in flight at the budget is cut
      by the J runners' timer and gets no line, as in A001 to A003;
    - **a key's set is scored only when all 30 of its parts finished.** Otherwise its finished parts are a partial
      reading and decide nothing, and both arms on that key are incomplete.
16. **What is written, and the guards: A003's, unchanged in kind.** Each verdict line holds the question id, the record
    ref, the kind, the predicate, the arm's key, the task, the whole reply (the two answers), the error kind, the
    attempt that gave the reply (1 or the repair), whether the first attempt was cut at the cap, `scored`, and both
    readings' verdicts, each by the one pinned `decide`; never a narrative, never reasoning, never a term. The
    aggregate recomputes every reading's verdict from the stored reply with the pinned `decide`, checks the key of
    every line against its unit's, recomputes the cut and repair flags from the unit's ledger, and refuses a line that
    disagrees. The plan artifact holds the labels, questions, the comparators' files and the chain file (public
    complaint text and refs; the artifact, never the repository). Reports, step summaries, console lines and commits
    hold counts, scores and hashes only: no narrative, no reasoning, no term, no record ref, no VIN, no city, no
    dealer, no operator name. The warm-up calls each shard's task on a generated record about no drawn record
    (`lab/warmup.py:131-145`, `judge_example`), typical and worst case, with the reasoning key's rules of "What
    reasoning on is", item 7.
17. **The first run is the result.** A run that fails before any model judges, for an infrastructure reason, may run
    again unchanged, with the same seeds and n (LOOP-48H section 6.3); if the plan job printed and the run then failed
    before any model judged, the re-run's plan job draws from the complaint file of its own day under the same seed,
    both plan jobs' fixed lines are recorded and the second draw is the result (A002's B13). After model calls
    started, the run is the result for whatever finished; a set left incomplete gets no verdict from this run, and
    judging it again is a new choice file. Any change to a setting (the cap, the deadline, the flag, the base, the
    reading, the arms, the seeds, the parts) is a new choice file once the judging run has started, and, before it, a
    numbered, reviewed amendment only within the clause that follows (E1). **From the commit of the amendment "Amended
    before any build, 2026-10-14" (E1 to E38), no amendment changes any of the following:** the four cases, their
    order and the cells they read; P under any case, except as E2 fixes now for a decision-9 call; the four arms and
    their keys; the readings; the comparators and the pass test by branch; the seeds, n and parts; the cap, the
    deadline, the planning figures, the timing check's calls and its clauses; the R0-only request of rule 18. An
    amendment written after A003's result is recorded may only read the branch and fill placeholders 1 to 7, quoting
    the cells, or correct a citation to its source; it states that the branch the cells select is unchanged by it. An
    amendment after the timing check may only fill placeholders 8 to 13. The amendment that adopts a decision-9 call
    has no other content. An amendment that fixes the lab-side cause of a stop before any model ran, as LOOP-48H
    section 6.3 requires, changes none of these. A review finding whose fix would change any of these after A003's
    result is recorded is declined, and that change is a new choice file on a fresh draw. After the run, no reading
    other than the four arms and the reports of rule 14 is made of the replies.
18. **The timing check comes first** (E3). Under branches N and T the judging request is pushed only after the timing
    check has passed every stop clause of its stop rule ("The timing check") and a dated amendment has recorded its
    figures; if it fails, this rule ends and lever 6 needs a new choice file. In the confirmation case and the
    hypothesis case, the judging run does not depend on the timing check's outcome. If the check passes, the four
    arms run as declared. If it fails, or stops before its last call for any reason other than infrastructure (or
    stops before its last call again after one unchanged re-push for infrastructure), the judging run is pushed with
    R0 and R0m only: 30 units on a-4b in 30 shards, 2 (model, arm, cycle) comparisons this cycle, 27 since the loop
    began, `comparisons_before` 25 ("The request", the R0-only request). P, the pass test and the comparators stay as
    declared. The lever's arms go to a new choice file. In every case a dated amendment records the check's figures
    before the judging request is pushed.

**The settings, all fixed now.**

| Setting | Value |
|---|---|
| Model | a-4b; key `a-4b` with `--reasoning off` (unchanged) for R0 and R0m; key `a-4b-r` with `--reasoning on`, the same file verified by a-4b's lock digest `7485fe6f11af…`, for R1 and R1m; no reasoning budget; temperature 0 (the client); server seed 5 |
| Population | NHTSA complaints, pack `docs/collective/replay/vehicles/pack`, `NHTSA_MAKES` and `NHTSA_WINDOW` verbatim, j1 eligibility |
| Excluded | J001's 150 (J1, R1), A001's 150 (A1, R2, K2), A002's 150 (B1, R3, K3), A003's 150 (C1, R4, K4; placeholders) and A003's split removals; eleven sources; the stop rules over four cycles |
| Labels | n 150, seed 5 (`random.Random("nhtsa:5")`), codes removed |
| Questions | `lab.j1.build_questions`, seed 5, parts 30 (5 records, 10 questions a part) |
| Base task | by the adaptation rule from A003's result run: A001's task N (branch N, case H) or A003's task D (branch T, case C); placeholder until the amendment |
| Tasks | R0: the base task unchanged (cap 256); R1: the base task with `max_tokens` 2,048, nothing else changed |
| Reading | M, `lab.j2.MAPPING`, unchanged |
| Arms | R1, R1m on `a-4b-r`; R0, R0m on `a-4b` |
| Primary pair | branch N: (a-4b, `negation_reasoning`); branch T: (a-4b, `negation_terms_reasoning`); case C: (a-4b, `negation_terms`); case H: (a-4b, `negation`); placeholder until the amendment; a decision-9 call adopted by its own amendment makes it (a-4b, `negation_reasoning_mapped`) under branch N and (a-4b, `negation_terms_reasoning_mapped`) under branch T, and changes nothing in the two cases (E2) |
| Comparators | branch N and case H: section 2.4's three; branch T and case C: A003's five, with A003's added clauses |
| Bootstrap | over records, B 10,000, `bootstrap_seed` 5 (`j1:5`), 95% percentile; paired differences with the same draws |
| Endpoint | the lab's pinned model server on loopback at `site:lab`, data label `public`; HTTP deadline 900 s, 1 retry; `Runtime.run` with its one repair; no escalation |
| Units | 60 (2 keys × 30 parts), 10 calls each before repairs, 300 minutes, one per shard, P's key's shards first (E19); `job_minutes` 330; `max_parallel` 16; under rule 18's R0-only request, 30 on `a-4b` (E3) |
| Withhold | `WITHHOLD_SHARE` 0.01 per key's set |
| Timing check | kind `jr`, one unit of 20 calls on `a-4b-r` (16 streamed, 2 non-streamed, 2 raw), generated payloads, stop clauses S1 to S5 and S7, S6 counted (E8, E10, E11, E12); `minutes` 300, `job_minutes` 330 (E7); `max_retries` 0 (E25) |
| Planning figures | 0.30 s per output token; 60 s to a judging call's first token under branch N and case H, 100 s under branch T and case C; 15 ms per character of a user turn as the first-token bound; 35 minutes before the warm-up (E6, E7) |
| Requests | the check: `lab/templates/reasoning-check-004.json`, copied to `lab/requests/reasoning-check-004.json`, kind `jr`; the judging run: `lab/templates/accuracy-004.json` (four arms, `reasoning` true) or, under rule 18 only, `lab/templates/accuracy-004-control.json` (R0 and R0m, `reasoning` false), copied to `lab/requests/accuracy-004.json`, kind `j5`; `retention_days` 30 each; `provider` `llama-server`; `comparisons_before` 25; `decision_9` `default` unless E2's amendment sets it |

## Why these arms, and why this primary pair

**The yes on a negative is the failure to fix, and no cycle has let the model think before it answers.** Every
earlier lever changed what the model was told or how its answer was read; this one changes how much computation the
model spends before it answers, with the messages unchanged byte for byte (E16). The lexical judge refutes almost every
negative and confirms about a third of the positives (specificity 0.960 to 0.993, sensitivity 0.333 to 0.380, J001
and A001); a-4b confirms almost every positive and refutes at most about half of the negatives (A002 run 1). A model
that kept the sensitivity of A002's best arm, D (0.907; E15), would need a specificity of about 0.813 to reach 0.860
(derived here: (0.907 + 0.813) / 2 = 0.860), against the 0.467 that arm reached.

- **Arm R1 (the primary under branches N and T)** reads the reasoning arm's answers as answered. `R1_minus_R0` is the
  lever's reading: whether reasoning moves the answers at all, and in which direction, read with sensitivity and
  specificity and with the yes rates of rule 14 (trap 2). R1 minus the lexical judge is the question the loop needs
  answered.
- **Arm R1m (the headline secondary under branches N and T)** reads the same replies through M. The mapping's
  measured lift under the negation task is 0.077 [0.047, 0.107] (A001 run 1, D − C) and 0.053 [0.030, 0.080] (A002
  run 1, `D_minus_N`); whether reasoning leaves fewer `unclear` answers for it to convert is `R1m_minus_R1`.
- **Arms R0 and R0m** are the controls: the earlier cycle's arms on a fresh draw, served exactly as before. Without R0
  a lift could be a between-draw difference (A002's B14: two independent draws differ by sampling alone by up to
  about 0.10).
- **The two cases.** When A003's P passed provisionally, the pinned confirmation rule decides the primary; when A003's
  control B passed and P did not, section 2.5 names B a hypothesis for this cycle's primary. In both, R1 still
  measures the lever beside it on the same records.
- **Why a-4b alone**: "The base task and the arms".
- **Why no third task or key.** A reasoning arm with a reasoning budget, a larger cap, the shipped instruction, or
  the two smaller models would each cost 300 calls and a comparison; none has a recorded figure to justify it, and
  lever 6's text names a-4b with the flag.
- **The honest expectation for P, from the recorded figures: failure.** For P to pass under branch N, R1 must reach
  0.860 and beat the lexical judge (0.663 to 0.680 on the last three draws) with the lower end above 0: about 0.17
  above the best a-4b arm measured so far (0.687 [0.643, 0.730], A002 run 1, derived here as 0.860 − 0.687 = 0.173).
  The nearest measured analogue, visible analysis before the answer, moved a-4b toward yes. Replies cut at the cap
  become `unknown` and cost both sensitivity and specificity. Nothing recorded predicts a pass. The cycle still
  measures what the loop needs: the direction and size of `R1_minus_R0`, read with sensitivity and specificity, and
  the cost of reasoning per call on the lab's runners, which no run has measured.

## Why the units are this size

This section answers the rest of the brief's item (3). Its figures were re-derived by the amendment before any build
(E6, E7, E13); the rule's first text planned on 0.25 s per output token, 60 s to the first token and 30 minutes
before the warm-up.

**What the lab has measured** (a-4b, reasoning off, every figure as its record prints it, E6; one shared runner per
shard; compare only within a CPU model):

| Run and record | What | Figures |
|---|---|---|
| RESULTS Run 1, lab run 37834297419 | E3 latency, streamed, concurrency 1, AMD EPYC 9V74 | decode 7.0 tokens per second (extraction) and 9.0 (short), medians; first token 75.934 s and 35.597 s, medians |
| RESULTS Run 2, lab run 37841045975 | sim ledgers, median per call | judging 3,435 ms; extraction 4,274 ms |
| RESULTS Run 5, lab run 37866392839 | E3 latency, streamed, concurrency 1, Intel Xeon Platinum 8573C (E6) | decode 4.2 tokens per second (extraction) and 5.6 (short), medians; first token 25.681 s and 11.344 s, medians |
| RESULTS Run 7, lab run 37910989965 | E1 extraction on real complaints (E6) | median 25.8 s per call (Intel Xeon Platinum 8573C); repeats 1 and 2 timed out at the unit's 150-minute limit |
| RESULTS Run 9, lab run 37996675559 | J001, shipped task, median per call | 6.0 s (Intel Xeon Platinum 8573C), 9.0 s (AMD EPYC 7763) |
| RESULTS Run 10 and CHOICE-L001 Run 2, lab run 38102412415 | L001, judge calls on MSHA records | alert: median 3.792 s, 95th percentile 5.306 s (AMD EPYC 7763); five questions: 3.518 s and 5.484 s |
| CHOICE-A001 Run 1, lab run 38114522160 | A001, shipped / negation task | medians 9275.1 / 9431.5 ms (AMD EPYC 7763), 8211.6 / 8637.7 ms (Intel Xeon Platinum 8370C); 95th percentiles 16209.7 / 16427.0 and 11754.1 / 12020.0 ms; longest shard 1377.2 s |
| CHOICE-A002 Run 1, lab run 38439612256 | A002, evidence / rationale / negation task | medians 13646.9 / 18272.5 / 9297.1 ms (AMD EPYC 7763), 9466.4 / 14474.4 / 6645.3 ms (AMD EPYC 9V74), 8381.1 / 16777.5 / 4909.1 ms (Intel Xeon Platinum 8573C); output tokens medians 38.0 / 87.0 / 16.0, 95th percentiles 143.0 / 115.1 / 17.0, none cut; longest shard 2735.4 s |

**Derived here from those figures** (rough: a difference of medians is not a median of differences, and the tasks'
prompts differ by a sentence):

- **Decoding.** The marginal decode rate of a-4b, from the rationale and negation medians (71 more output tokens at
  the median), is 7.9 tokens per second on the AMD EPYC 7763, 9.1 on the AMD EPYC 9V74 and 6.0 on the Intel Xeon
  Platinum 8573C; from the evidence and negation medians (22 more), 5.1, 7.8 and 6.3. The slowest recorded decode is
  Run 5's 4.2 tokens per second, 0.238 s per token (1 / 4.2), on the 8573C (E6). The E3 user turn, as
  `render_messages` renders `e3_latency`'s payload, is about 5,370 characters for the short workload and about 10,690
  for the extraction workload (median of six seeds at `e6b9a45`; across seeds the lengths vary by about 1%): when that
  prompt doubled, decoding was 22% slower in tokens per second on the 9V74 (9.0 to 7.0, Run 1) and 25% slower on the
  8573C (5.6 to 4.2, Run 5). No record measures decoding over the longer context of a reasoning call.
- **The first token.** Run 1's medians are 6.6 and 7.1 ms per character of that user turn on the 9V74 (35.597 s /
  5,370 and 75.934 s / 10,690); Run 5's are 2.1 and 2.4 ms on the 8573C. No record times a first token on the AMD
  EPYC 7763, whose negation-call median was 1.40 times the 9V74's (9297.1 against 6645.3 ms, CHOICE-A002 Run 1): about
  9.3 to 9.9 ms per character there (rough: a whole-call ratio applied to the first token). The system text of a task
  is the same for every call and is held in the server's prompt cache after the first call, as E3's notes assume
  (`e3_latency.py`, `NOTES`), so the user turn is the part that is processed again.
- **The prompts this cycle sends.** The negation task's system text is 1,561 characters. The user turn of a judging
  call is the narrative plus the question and record fields, 293 characters for the check's call 1 (629 less its
  336-character narrative, at `e6b9a45`); with the longest narrative this repository records for NHTSA, 2,824
  characters (LOOP-48H section 2.6; HANDOFF-2026-10-09, Q9), that is about 3,100 characters, and under branch T and
  the confirmation case up to about 3,200 characters of terms more (CHOICE-A003, "The terms", item 8), about 6,300.
  The timing check's calls 13 to 16 carry 6,289 characters (the 6,000-character narrative), and up to about 9,500
  with the longest list. At Run 1's rates the 9V74 takes about 43 and 67 s to the first token of those calls, and the
  7763 about 60 and 94 s (interpolated on Run 1's two medians, then times 1.40).

**The loop prompt's estimate, labelled as such**: "about 1-2 min per call, 5-10 h per model for 300 calls". At the
derived rates of 5.1 to 9.1 tokens per second, one to two minutes of decoding is about 300 to 1,100 tokens of
reasoning and answer (derived here). The estimate is plausible and unmeasured; the timing check measures the first
reasoning-on calls.

**The planning figures, declared now, and why** (E6). **0.30 s per output token** (3.3 tokens per second): 26% above
the slowest recorded a-4b decode, 0.238 s per token (Run 5), which was measured at a prompt of about 10,690
characters and over at most 200 output tokens; a reasoning call at the cap decodes up to 2,048 tokens over a context
that grows with them, and decoding slowed by 22 to 25% when the E3 prompt doubled, so the room is for that growth,
which no record measures at this length. **The first token of a judging call: 60 s under branch N and the
hypothesis case, 100 s under branch T and the confirmation case.** At a bound of 15 ms per character of the user
turn, about 1.5 times the rate derived above for the 7763, the longest judging prompts take about 47 s (3,100
characters) and 95 s (6,300 characters). The 60 s applies to real narratives under branch N and the hypothesis case
only; the timing check's calls 13 to 16, with 6,000-character narratives, are bounded by rate (S4a), not by these
figures. **35 minutes before the warm-up** (E7): the sum of the step timeouts before it (two cache restores of 5
minutes, `.github/workflows/mycelic-lab.yml:214, 224`; the prepare step's 20, `:241`; the server's 300-s health
deadline, `lab/server.py:60`); the checkout, the Python set-up and the artifact downloads, which have no step timeout
of their own, are assumed to fit in what those timeouts leave unused. An allowance, not a measurement. The timing
check's clause S4 tests the decode and first-token figures on its runner, at the design's tolerances below.

**The worst case by design, at the planning figures** (derived here; E7). Every call at its cap, every first attempt
repaired and the repair at the cap too, every narrative and term list at its longest:

- a reasoning call at the cap: 2,048 × 0.30 + 60 = 674.4 s under branch N and the hypothesis case, and
  2,048 × 0.30 + 100 = 714.4 s under branch T and the confirmation case, inside the 900-s deadline with 225.6 and
  185.6 s to spare;
- a question: at most two attempts at the cap (the runtime's one repair): 1,348.8 s and 1,428.8 s;
- a part of 10 questions: 13,488 s (about 225 minutes) and 14,288 s (about 238 minutes);
- the warm-up of an R1 shard: one call and two probe calls at the cap on the generated record, whose user turn is 629
  characters (with up to about 3,200 characters of terms more under branch T and the confirmation case), at 15 ms per
  character: 3 × (614.4 + 10) = 1,873.2 s (about 31 minutes) and 3 × (614.4 + 58) = 2,017.2 s (about 34 minutes);
- the shard's time for calls: its deadline, the job start plus 313 minutes (the 325-minute timeout less
  `JOB_TAIL_MINUTES` 10 less `DEADLINE_MARGIN_S` 120 s), less the 35-minute allowance and `SIM_BUDGET_MARGIN_S` 30 s:
  16,650 s; the 23 calls take 15,361.2 s (about 21 minutes to spare) and 16,305.2 s (about 6 minutes to spare); the
  unit's budget is then about 246 and 244 minutes against about 225 and 238 needed;
- the design's tolerance in decoding: about 0.327 s per token under branch N and the hypothesis case ((16,650 −
  1,230) / 47,104) and 0.307 s under branch T and the confirmation case ((16,650 − 2,174) / 47,104), 23 calls of
  2,048 tokens with the first tokens above; S4b stops at 0.32 and 0.30; the deadline holds to about 0.41 and 0.39 s
  per token;
- the cap: the largest that fits at these figures is about 2,234 tokens under branch N and 2,098 under branch T
  ((16,650 − 1,230) / (23 × 0.30) and (16,650 − 2,174) / (23 × 0.30)); a cap of 2,560 takes 868 s a call under
  branch T and 23 calls 19,838 s against 16,650, and 3,072 less still, so neither fits and the cap stays 2,048 (E7);
- a control call at its cap of 256: 136.8 s or 176.8 s; a control part at most 2,736 s or 3,536 s, about 46 or 59
  minutes; an R0 shard at most about 85 or 101 minutes with its warm-up and the allowance.

So no shard can reach its job timeout by design: every unit's budget timer stops it first, and at the planning
figures the budget is not reached either. 30 parts is the most `lab.j1.build_questions` allows (`MAX_PARTS` 30,
`lab/j1.py:127`; `J1_MAX_PARTS`, `lab/request.py:283`), and it gives the smallest part. What is left (E7): the plan
job prints no narrative length, and the pack cuts narratives at 6,000 characters, not at 2,824; a part whose five
records all ran to 6,000 characters would add up to about 11 minutes under branch N and 14 under branch T to the
worst case (20 × (94 − 60) s and 20 × (142 − 100) s), more than branch T's 6 minutes of room. That needs every call
of the part at the cap and every repair at the cap as well, and is stated, not removed.

**What it costs** (E13), three figures, each derived here, none measured for reasoning:

- **Expected, from the loop prompt's estimate and the measured control latency.** R1: 300 calls at 1 to 2 minutes, 5
  to 10 hours of model time spread over 30 shards, 10 to 20 minutes of calls per shard plus 3 warm-up calls. R0: 300
  calls at the negation medians of A002 run 1 (4909.1 to 9297.1 ms), about 25 to 47 minutes over 30 shards; under
  branch T and the confirmation case the control's figures are task D's (placeholder 7). With 16 shards at a time
  and P's key first (rule 15), that is about 1 to 2 hours of model-call time end to end (two rounds of R1 shards of
  13 to 26 minutes, the R0 shards beside and after them), plus each round's preparation, which no record times and
  which the allowance puts at up to 35 minutes, four rounds in all; each repair after a cut adds one call.
- **At the planning figures, by design.** An R1 shard at most about 291 minutes (branch N, the hypothesis case) or 307
  (branch T, the confirmation case), an R0 shard at most about 85 or 101; two rounds of each: about 752 or 815
  minutes, about 12.5 or 13.6 hours (rough: the rounds overlap in practice).
- **By the job timeouts alone.** 60 shard jobs, 16 at a time, each at most 325 minutes (`lab/plan.py:304`): four
  rounds, up to 1,300 minutes, about 21.7 hours; plus the plan job (30 minutes at most,
  `.github/workflows/mycelic-lab.yml:28`), the provision jobs (60 minutes at most each, in parallel, `:115`) and the
  aggregate job (30 minutes at most, `:309`): up to about 23.7 hours.

Under rule 18's R0-only request: 30 shards, two rounds, at most about 2 × 85 or 2 × 101 minutes at the planning
figures and 2 × 325 = 650 minutes by the job timeouts, plus the same jobs. The jobs: the plan job, three provision
jobs (the server and the two keys; the new key's cache entry, E27), 60 shard jobs and the aggregate job. The runners
are GitHub-hosted; no figure in this repository prices them.

**The timing check's budget** (derived here; E7, E11, E12): calls 1 to 12 at the cap, 12 × (614.4 + 10) = 7,492.8 s
under branch N and the hypothesis case and 12 × (614.4 + 58) = 8,068.8 s under branch T and the confirmation case;
calls 13 to 16, 4 × (614.4 + 94.3) = 2,834.8 s and 4 × (614.4 + 142.3) = 3,026.8 s (6,289 and about 9,489 characters
at 15 ms); calls 17, 18 and 20 as calls 1, 13 and 1; call 19, 16 tokens, at most about 15 s or 63 s; in all about
12,300 s (205 minutes) and 13,260 s (221 minutes); the warm-up about 31 or 34 minutes. With `minutes` 300 and
`job_minutes` 330 (E7; with the rule's first 240 and 270 the unit would have had about 186 or 184 minutes, too few),
the shard's timeout is 325 minutes, its deadline 315 minutes after the job starts, and the unit's budget in the
worst case about 246 or 244 minutes, about 41 or 23 minutes over the need. At the loop prompt's estimate the check's
calls take about 22 to 44 minutes (22 calls at 1 to 2 minutes, call 19 aside).

**What happens if a part does not finish** (E9), per key. P's key is `a-4b-r` under branches N and T and `a-4b` in
the two cases (the only key under rule 18's R0-only request). A unit that stops at its budget, a shard whose server
stays down (two consecutive server-down failures stop the unit, `extract.BREAKER_AFTER`), a shard whose preparation
fails (a re-download from the hub that does not finish before the prepare step's deadline, `lab/provision.py`,
`prepare`), or a shard job that fails leaves its part's remaining questions unjudged: that key's set is incomplete,
and so are both arms on it (rule 15). If P's key's set is incomplete, the cycle is **incomplete** by section 2.5's
completeness clause: it is recorded, and the next cycle needs a new rule on a fresh draw. If only the other key's set
is incomplete, P's verdict is read, and the lifts and the other key's arms are not. A transport failure on a question
leaves its record out of that key's set; more than 0.01 of a set's records leaving withholds the set. **A provision
failure of either key stops the run before any model judges**: build 2 makes the run job of a `j5` plan require
every provision job's success (a reviewed workflow change; today the run job starts when the plan job succeeded
whatever the provision jobs did, `.github/workflows/mycelic-lab.yml:176-177`), so no shard of either key starts. That
stop, like one in the plan job or a lost runner before any model judged, is not a result: the same request is pushed
again unchanged once (rule 17). If shards of one key judged while every shard of the other failed before judging,
the run is the result for whatever finished, as rule 17 says, and the per-key reading above applies.

## How it is read

- **The verdict on P** is read by rule 13 and only there: `pass_provisional`, `partial`, `failure` or `incomplete`,
  printed by the aggregate with "P scored BA b [lo, hi]; gap to 0.860 is g; lexical l [lo, hi]; bound p" (under
  branch T and the confirmation case with every lexical figure, as A003 prints it).
- **The honest expectation** is failure on P ("Why these arms"). The lever's reading survives a failure:
  `R1_minus_R0` with sensitivity and specificity.
- **The lifts** say what the lever did. Read `R1_minus_R0` with the yes rates: reasoning that makes the model confirm
  more raises sensitivity and lowers specificity and moves balanced accuracy little; reasoning that lets it refute a
  component the record does not report raises specificity. A lift near 0 with many cut replies says the cap, not the
  reasoning, set the result; the cut counts of rule 14 say which.
- **The cut counts** are read before the lifts: a reasoning arm with many questions unanswered after the repair
  measures truncation as much as reasoning, and the first attempts cut, per kind, say whether the cap fell more on
  positives or on negatives (trap 7). If more than 0.10 of R1's 300 questions are unanswered after the repair (rule
  14's count), the Runs entry states that R1 is truncation-bound and labels `R1_minus_R0` and `R1m_minus_R0m` "not a
  reading of the lever at this cap"; the verdict words are unchanged (E24).
- **The time** is a measurement, not a test: R1's seconds per call and per token are the first reasoning-on figures
  of this lab, runner numbers, never site hardware.
- **150 records resolve little** (A002's B14): a balanced-accuracy interval is up to about 0.14 wide; a paired
  difference to a lexical judge is told from 0 only when it is about 0.05 or more (A001 run 1's paired half-width of a
  model arm against the lexical judge, 0.047 to 0.054, B14); an arm between is `failure` for want of power, not
  evidence that the lever does nothing.
- **0.5 is no judging, with J001's caveats.** A judge that confirms everything, or refutes everything, scores 0.5 on a
  balanced draw; one that answers unknown everywhere scores 0. The record-blind control and the predicate-only bound
  decide nothing; a model at or below the bound is not shown to read.
- **This is one public field,** complaints written to a regulator, with the model possibly having seen them in
  training. A pass here would be a pass on public text the model may have seen; the Runs entry says so.

## Traps

1. **The reasoning reading the codes.** Never from the payload: the payload's codes list is empty (`lab.j1.payload`;
   a label record that still holds codes is refused, `lab/j1.py:278-279`), and the task texts name no code. The
   reasoning is generated from the prompt and the model's weights; what the weights hold of these public complaints,
   their filed components possibly included, is trap 5's caveat, and a reasoning pass may draw on it more than a
   direct answer does (E32). The filed categories reach no arm through the payload, the task text or a reply mapping,
   as in J001 to A003.
2. **Reasoning that increases yes answers.** a-4b already says yes too often; a reasoning pass may argue itself into a
   yes on a component the narrative merely names, as the rationale task did (A002 run 1). The yes rates on positives
   and on negatives, R1 against R0, are printed (rule 14), and `R1_minus_R0` is read with sensitivity and
   specificity, never alone.
3. **A longer reply changing what `decide` reads.** It cannot: `decide` reads the two answers of the parsed object
   only (`verify.py:243-259`); the schema is the control's, strict, with no room for another key; the reasoning is
   outside the content (separated form, required by item 7 of "What reasoning on is" and clause S1) or, where a
   server put it inline, removed before the object is parsed (`jsonparse.py:9-11`). A content holding two objects
   gives the first (`jsonparse.py:13-19`); under the strict `json_schema` transport the content is one object.
4. **The reasoning echoing the predicate list.** The payload carries the asked predicate's label and, under branch T,
   its drafted terms; a reasoning pass may restate them and talk itself into a match. That is word matching with a
   model in the loop, which the comparators price: the lexical judge under branch N, and under branch T the drafted
   lexical judge and the term-presence judge with A003's agreement readings (A003's trap 6). The reasoning is never
   stored, so no one reads whether it echoed anything; the verdicts' agreement with the judges is the reading.
5. **Training-time exposure to public complaints.** The model may have seen these complaints, and a reasoning pass
   may recall more of what it saw than a direct answer does. That would help the model and not the lexical judge.
   Exclusion of earlier draws removes records the model judged, not records it may have read in training; the Runs
   entry carries the public-text sentence.
6. **A cut reply read as an answer.** A cut attempt whose content holds no valid object is `json_invalid`, the
   runtime's one repair follows, and a question with no valid reply after it is `unknown`, scored. The reasoning of a
   cut attempt is never read by the lab. That a cut inside the reasoning leaves the content empty or a partial object
   is the release's behaviour: the timing check's call 19 and clause S7 test it on the non-streamed path, and a
   release that puts reasoning text into the content of a cut reply stops the rule there. A first attempt cut at the
   cap whose content still parses into a valid reply is scored as answered, because the pinned runtime validates the
   content and not the finish reason (`runtime.py:320-350`); rule 14 counts such attempts apart (E11).
7. **The cap binding unevenly.** Long narratives, or questions whose answer is less obvious, may reason longer and be
   cut more; a cut costs a positive and a negative alike. The first attempts cut per kind and per predicate are
   reported (rule 14).
8. **The repair after a cut.** The runtime's repair note asks for a shorter object (`tasks.py:97-98`); the reasoning,
   not the object, was long, so a repaired attempt reasons again from the start and may be cut again. Its time is in
   the worst case of "Why the units are this size"; its effect is in the counts of rule 14.
9. **A warm-up cut skipping a shard.** Under today's rule a warm-up call cut at the token limit skips its task's units
   (`lab/warmup.py:407-408`); with 30 reasoning shards a chance cut on the generated record could leave a part
   unjudged and P incomplete. The reasoning key's warm-up records a cut and skips nothing (item 7 of "What reasoning
   on is"); the timing check's clause S3 is a smoke bound on cuts on generated records, not an estimate (E17).
10. **The flag doing nothing, or more than it says.** A server that ignores `--reasoning on` under the strict
    `json_schema` transport would make R1 the control at a larger cap; one that reasons inline would put reasoning in
    the content. The timing check's S1 and the warm-up's inverted probe stop both before any drawn record is judged.
    A provision that serves the reasoning key a different file is refused by the lock digest (item 1).
11. **Two keys on different runners.** The R0 and R1 shards run on runners whose CPU models vary; a-4b's answers at
    temperature 0 with a fixed seed may differ across CPU models and thread counts, as in every earlier cycle. The
    lift is paired by record and is a difference of the two keys' answers, each on its own runner; latency is
    compared only within a CPU model.
12. **Unclear answers and the mapping.** M reads `unclear` as `no` (A001 trap 10): it converts abstention into
    refutation. If reasoning leaves fewer `unclear` answers, `R1m_minus_R1` shrinks; the share of `unclear` per key is
    reported. M is decision 9's reading, pending, and secondary here.
13. **`mentions_entity` turning refutes into unknowns.** `decide` refutes only when the record mentions the entity and
    nothing describes the predicate (`verify.py:255-258`); a reasoning pass stricter about whether the vehicle is named
    could answer `mentions_entity` no more often and turn refutes into `unknown`. The answer-pair counts per kind show
    it.
14. **One model, four arms, one draw.** There is no reading of whether the lever generalises across model sizes. With
    a standard error near 0.035, the best of four readings on one draw is biased upward; the pass test is read on P
    alone, a secondary that passes is a hypothesis, and the running count (29 after this cycle) is printed.
15. **Post-hoc readings.** The replies are stored, the reasoning is not. Other readings of the replies (another
    mapping, the first attempt alone, a per-predicate cut) can be computed after the run; none counts. J001 to A003
    are never re-scored.
16. **Tuning on the draw.** The cap, the deadline, the planning figures, the flag, the base rule, the reading, the seeds
    and the parts are fixed in this file before the draw exists. The timing check's figures come from generated records
    and can only stop this rule (in the two cases, only its lever's arms, rule 18), never tune it: an amendment after
    the check may only fill placeholders 8 to 13 (rule 17; E1). The plan job prints the comparators before any shard
    starts; those figures are never a reason to redraw.
17. **Filed codes are not truth** (J001 trap 1). A narrative can describe a component it was not filed under, and the
    reverse. The key's noise ceiling is unmeasured, and 0.860 may sit above it for some predicates; reasoning cannot
    recover a filing the narrative does not support.
18. **Old and new names** (J001 trap 2). The pack keeps three old-and-new name pairs as separate predicates (`TWINS`);
    no reasoning can tell the two names apart from the text.
19. **Artifacts expire.** J001's on 2026-11-08T22:00:03Z, A001's about 2026-11-10, A002's on 2026-11-12T17:34:56Z,
    A003's 30 days after its result run (its `expires_at` a placeholder), the term artifact on 2027-01-12T18:09:48Z.
    After each expiry that cycle's freshness rests on its rebuild and on the chains; under branch T and the
    confirmation case the judging request must be pushed before the term artifact's expiry, else the plan job refuses
    with the terms unverified, a stop before any model.
20. **The complaint file changes.** The labels come from the file as published on the day; the exclusion works on
    refs, and a changed file is reported through the rebuilds' outcomes and the file's sha256 beside the earlier days'.
21. **The texts are written for this pack.** The negation instruction and the terms sentence fit vehicle complaints
    (A001 trap 15; CHOICE-A003 trap 20). They are lab TaskSpecs, not product texts.
22. **One record a question.** The product pools a window of records into one verdict; this cycle judges one record
    at a time, so the pooling, the routes, the gate and the alert's time to answer with reasoning on are not measured.
23. **Greedy decoding** (E24). The pinned client sends temperature 0 on every call (`client.py:498`), so R1 reasons
    by greedy decoding. No record shows how a-4b's reasoning behaves under greedy decoding. A reasoning pass that
    repeats itself runs to the cap and shows as first attempts cut, per kind (rule 14), and as unknowns; "How it is
    read" fixes when R1 is read as truncation-bound (more than 0.10 of its questions unanswered after the repair). A
    failure then says nothing about reasoning at another sampling setting ("What it does not show").

## What it does not show

- No site-level verdict: how pooling, routing and the commit gate turn record verdicts into an alert's answer. That
  is L001's measurement.
- No early warning: no outcomes, no detection, no alert; this is reading, not signal.
- No partner data: these are public complaints written to a regulator.
- Not the product's behaviour. The product runs the shipped instruction with its server's reasoning off; every arm
  here is a lab reading of a declared change, and none is shipped by this cycle. Not the product's latency with
  reasoning on: L001 measured a-4b's alert answered in 143.058 s with reasoning off (RESULTS run 10), and this cycle's
  per-call times bear on that only as runner numbers.
- Not a-4b with a reasoning budget, another cap, streamed replies, or another transport; not a-0p5b or a-1p5b; not a
  larger local model, the R003 settings or pack-v2 (levers 7 and 8).
- Not the hosted central reader of section 2.5's secondary; no key exists and none is assumed.
- Nothing about Tesseract or ingestion; latency is reported, not judged.
- Not the key's noise ceiling, nor whether any judge could reach 0.860 on these labels.
- Not a-4b's reasoning at any sampling setting other than temperature 0 (trap 23; E24).
- Not what the reasoning said: the lab's code never stores it. The server's log is the release's; it stays in each
  shard artifact, which any signed-in GitHub user can download for 30 days, and the timing check's clause S6 counts
  the reasoning text the release writes there, a count above 0 removing the reasoning key's logs from the judging
  run's artifacts (E12).

## Decisions this does not make

- **Shipping reasoning on.** The product's site verifier serves its model through settings this cycle does not
  touch; switching its reasoning on would change its answers and its time to answer, a product decision for the
  founder and the chief scientist, made from this cycle's figures and L001's if at all.
- **Decision 9** (how "unclear" counts) is the chief scientist's; M stays secondary under the standing default. A
  call that counts unclear as no, received before the judging request is pushed, is adopted by a dated amendment with
  no other content and installs exactly the pair "The base task and the arms" names (E2); any other call bears on the
  next cycle.
- **What 86 means** (E35). The founder has not confirmed that 86 is read as LOOP-48H sections 2.1 to 2.5; the ask has
  been open since 2026-10-11 (`docs/handoff/LOOP-48H-STATUS.md`, "Open asks"). This rule runs section 2 by default,
  as LOOP-48H section 7 sets it, and will not switch after a result.
- **Whether a lexical judge may be a primary** (E35): A003's ask is conditional on its own drafted lexical judge
  reaching 0.860 on its draw (CHOICE-A003, "Decisions this does not make") and has not been posted: the status log at
  `831f424` does not list it under "Open asks". A003's result run decides whether it is posted, under A003's rule;
  this rule posts nothing. No cycle counts a lexical judge's figure.
- **The R003 settings and pack-v2** are the chief scientist's; **decisions 1, 2 and 7** (Tesseract placement, routing,
  the embedder) are untouched; **the founder's alternative readings** of 86 (section 2.7) are not adopted; **the hosted
  central reader** needs the founder's secrets and a reviewed lab role.
- **Later cycles' questions**, left to their rules: a reasoning budget; a cap set from this cycle's measured reasoning
  lengths; the two smaller models with reasoning; reasoning under the shipped instruction; a larger local rung.

## The request

Two requests, each pushed alone after its code is merged (LOOP-48H section 6.4: the request push is the only thing
that starts a lab run), each only when no other lab run is in flight. The judging run has two forms, of which exactly
one is pushed (rule 18; E3). Their content, fixed now (the exact key names are the build's, recorded in
`BUILD-A004.md`):

**The timing check**, `lab/templates/reasoning-check-004.json`, copied to `lab/requests/reasoning-check-004.json`:

- top level: `provider` `llama-server`; `models` `["a-4b-r"]`; `job_minutes` 330; `max_parallel` 1; `retention_days`
  30; a `purpose` naming this file and the copy's name and no other digit (E7: 270 before);
- `experiments.jr`: `minutes` 300 (E7: 240 before); `pack` `docs/collective/replay/vehicles/pack`; `seed` 5;
  `base_task` `negation`
  or `negation_terms` (placeholder: by the adaptation rule); under branch T and the confirmation case `terms`, A003's
  term entry: `run_id` 38612800578, `artifact` `lab-plan-38612800578-1`, `file` `prereg/jt/terms.json`, `sha256`
  `42c591ccbb66b81455ca19840a54e18cbb69b5613dd2eab8f3f8c70f9cea39cc`, `vocabulary_hash`
  `c0260f0edab4638915b1fd0065e236ca20b497c0b217495c77adf018800fe579`, `cutoff` `2023-01-01`. The number of calls, the
  caps (2,048 and call 19's 16), the deadline, the endpoint's `max_retries` 0, the planning figures, the 15-ms rate,
  the tolerances, the 780-s bound, the 40-character run of S6 and the clauses are code constants of `lab/jr.py`, not
  request fields, so that no request can change them. Pushed after A003's result run has ended, its Runs entry is
  recorded and the amendment that reads the branch is committed.

**The judging run, four arms**, `lab/templates/accuracy-004.json`, copied to `lab/requests/accuracy-004.json`:

- top level: `provider` `llama-server`; `models` `["a-4b", "a-4b-r"]`; `job_minutes` 330; `max_parallel` 16;
  `retention_days` 30; a `purpose` naming this file and the copy's name and no other digit;
- `experiments.j5`: `minutes` 300; `labels` `{source: nhtsa, pack: docs/collective/replay/vehicles/pack, n: 150,
  seed: 5}`; `parts` 30; `seed` 5; `bootstrap_b` 10000; `bootstrap_seed` 5; `reasoning` true (E3); `base_task`
  (placeholder, as the check's); `primary` `{model: a-4b, arm: <the branch's P arm>}` (placeholder:
  `negation_reasoning`, `negation_terms_reasoning`, `negation_terms` or `negation`; `negation_reasoning_mapped` or
  `negation_terms_reasoning_mapped` only by E2's amendment); `decision_9` `default` (`unclear_as_no` only by E2's
  amendment); `comparisons_before` 25;
- `experiments.j5.exclude`, four entries in seed order: J001 (`run_id` 37996675559, `artifact`
  `lab-plan-37996675559-1`, `file` `prereg/j1/labels.jsonl`, `sha256_prefix` `bc6d092d8bca`, rebuild seed 1 with the
  same prefix); A001 (`run_id` 38114522160, `artifact` `lab-plan-38114522160-1`, `file` `prereg/j2/labels.jsonl`,
  `sha256_prefix` `623780b8a1de`, `chain_file` `prereg/j2/excluded.jsonl`, `chain_sha256_prefix` `9a948cbf2cd9`,
  rebuild seed 2 with prefix `623780b8a1de`); A002 (`run_id` 38439612256, `artifact` `lab-plan-38439612256-1`, `file`
  `prereg/j3/labels.jsonl`, `sha256_prefix` `0a64a62d94a7`, `chain_file` `prereg/j3/excluded.jsonl`,
  `chain_sha256_prefix` `65d51f0fca51`, rebuild seed 3 with prefix `0a64a62d94a7`); A003 (placeholders: `run_id`,
  `artifact`, `file` `prereg/j4/labels.jsonl`, `sha256_prefix`, `chain_file` `prereg/j4/excluded.jsonl`,
  `chain_sha256_prefix`, rebuild seed 4 with the labels prefix; A003's questions prefix is a code constant of
  `lab/j5labels.py`);
- `experiments.j5.terms`: term run 1's entry, as the check's, under every branch (R4 rebuilds A003's split with it;
  under branch T and the confirmation case it also feeds the payloads and the comparators).

**The judging run, R0 and R0m only** (rule 18, in the confirmation case or the hypothesis case after a failed timing
check only; E3), `lab/templates/accuracy-004-control.json`, copied to `lab/requests/accuracy-004.json` in place of
the four-arm template:

- top level: `provider` as the four-arm request's; `models` `["a-4b"]`; `job_minutes` 330; `max_parallel` 16;
  `retention_days` 30; a `purpose` naming this file and the copy's name and no other digit;
- `experiments.j5`: exactly the four-arm request's, but `reasoning` false, `base_task` the case's base
  (`negation_terms` in the confirmation case, `negation` in the hypothesis case) and `primary` `{model: a-4b, arm:
  negation_terms}` or `{model: a-4b, arm: negation}`; `decision_9` `default` or as E2's amendment set it (it changes
  no pair in the two cases); `comparisons_before` 25; `exclude` and `terms` as the four-arm request's.

`lab.request` validates every field: integers in range; `parts` exactly 30; with `reasoning` true the two models
exactly `a-4b` with `--reasoning off` and one key with `--reasoning on` whose manifest `gguf` block and lock entry
equal a-4b's, and with `reasoning` false `models` exactly `["a-4b"]` and the primary arm the case's (E3); artifact
names of the lab's shape; lower-case hex prefixes of 12 to 64 characters for the exclusions and exactly 64 for the
term entry; the files pinned per entry; rebuild seeds 1 to 4 in order; `base_task` one of the two; the primary arm
one of the branch's arms with `model` `a-4b`, and P's arm consistent with `base_task` (`negation_reasoning` or
`negation` with `negation`; `negation_terms_reasoning` or `negation_terms` with `negation_terms`); a mapped arm as
primary only `negation_reasoning_mapped` (with `negation`) or `negation_terms_reasoning_mapped` (with
`negation_terms`), only with `decision_9` `unclear_as_no`, and never a mapped R0 arm (E2). `lab.plan
--github-output` echoes the four excluded runs and artifacts and the term run; the plan job downloads all five.

## The build plan

The build starts only after this rule's reviews and amendments are committed, after A003's judging kind `j4` is
merged into the deployment branch (A004 imports its functions and edits none of its files), and in two builds with
the timing check between them. It changes no pinned file: `lab/j1.py` and `lab/goldlabels.py` (J001's `CODE_FILES`),
`lab/j2.py` and `lab/j2labels.py` (A001's), `lab/j3.py` and `lab/j3labels.py` (A002's), `lab/jt.py` (frozen by
CHOICE-A003 T7), A003's `lab/j4.py`, `lab/j4labels.py` and `lab/j4terms.py` once merged (A003's code hash),
`lab/shard.py`, nothing under `mycelic/`, `connectors/openfda.py`, `research/**`, the vehicle pack, any earlier
`CHOICE-*.md`, `run-*.json` or `*-settings.json`.

**The order.** (0) Before build 1, the dry-run outputs of A002's and A003's judging templates (plan summary,
`report.json`, `report.md`) are recorded on the build's base commit and pinned byte for byte after each build, as
A003's C30 did for A002's. (1) Build 1 on `wf/a004-check`: the reasoning key, its lock entry, the warm-up's rule for
it and the kind `jr`; review; fix; merge into the deployment branch only after A003's result run has ended (its
aggregate job finished), so that no A003 run, first or re-pushed, runs on a commit that holds build 1's manifest,
lock or warm-up, which A003's reviews never saw (CHOICE-A003 rule 7; a run uses the workflow at its own commit,
LOOP-48H section 2.3) (E33). (2) The amendment that reads the branch from A003's result run, with A003's exclusion
facts, and nothing else (rule 17; E1). (3) Push `lab/requests/reasoning-check-004.json` alone, with no other lab run
in flight: the timing check. (4) The amendment that records the check's figures and its outcome, and nothing else.
If the check failed, under branches N and T this rule ends here; in the two cases the R0-only request follows (rule
18; E3). (5) Build 2 on `wf/a004`: the judging kind `j5`, with both request forms; review; fix; merge. (6) Push
`lab/requests/accuracy-004.json` alone: the judging run. The order is serial: the check runs the branch's own base
task, so it waits for the branch; this is accepted, because a check on another task would measure prompts no
judging call of branch N sends and could not show that branch's answers parse (E29).

- **Why new kinds, not options on `j4`.** `lab/j4.py`'s constants (its tasks, arms, lifts, line keys) are read by the
  request validator, the unit adapter, the warm-up and the aggregate, as `lab/j3.py`'s were (CHOICE-A003, "The build
  plan"), and A003's modules are in its code hash, so extending them would change what a re-aggregation of A003
  computes. A004 is a judging kind `j5` whose own constants hold its two tasks per branch, its four arms per branch,
  the arm-to-key map, its lifts and its line keys, and which imports unchanged from `lab.j2` (`negation_task`,
  `MAPPING`, `mapped_reply`, `pass_test`, `verdict_word`, `lift_reading`), from A003's `lab.j4` (task D, its payload,
  the drafted judges, `pass_test`, `verdict_word`), from `lab.j2labels`, `lab.j3labels` and A003's `lab.j4labels`
  and `lab.j4terms`; and a check kind `jr` that judges no drawn record.
- **New modules.** `lab/jr.py` (the check: the 16 generated payloads, calls 1 to 16 through `Runtime.single`
  streamed, calls 17 and 18 through it non-streamed, calls 19 and 20 as raw HTTP, the endpoint's `max_retries` 0, the
  per-call figures, the clauses S1 to S7 as functions, S6's search of the server log holding call 20's reasoning in
  memory only, the fixed console line of counts and quantiles, the constants shared with `lab/j5.py`: the cap 2,048,
  call 19's 16, the deadline 900 s, the planning figures 0.30 s, 60 s and 100 s, the 15-ms rate, the tolerances 0.32
  and 0.30 s, the 780-s bound; E8, E10, E11, E12, E25);
  `lab/j5.py` (the two TaskSpecs per branch, R1's by `dataclasses.replace`, pinned equal to the control's in every
  field but `max_tokens`; the arms, the arm-to-key map, the lifts, the line keys; the verdict line with the attempt
  and the cut flag; the line check against the key and the ledger; the j1-shaped views; the cut and repair counts
  from the ledgers; the yes rates and the verdict-change table; the pins; the preregistration document; the runner:
  one pass, A001's budget timer and signals, `complete`); `lab/j5labels.py` (A003's layout check with
  `A003_QUESTIONS_SHA256_PREFIX`, pinned equal to the earlier layouts' checks by a test; the chain check over
  `prereg/j4/excluded.jsonl`; `rebuild_a003_refs` with A003's split recomputed by A003's functions; the eleven sources,
  the stop rules and the consistency counts of "The data and the draw", item 5; the union; the `excluded_runs`
  entries; the chain file); `tests/lab/test_lab_jr.py`, `tests/lab/test_lab_j5.py`, `tests/lab/j5_data.py` (a
  synthetic archive large enough for five disjoint draws of 150, with A003-shaped and earlier synthetic artifacts
  built by the pinned code and A003's, and stand-ins for the four entries and the term entry);
  `lab/templates/reasoning-check-004.json`, `lab/templates/accuracy-004.json`,
  `lab/templates/accuracy-004-control.json` (E3); `docs/collective/A004/BUILD-A004.md`. `lab.j5.CODE_FILES` is A003's
  `lab.j4.CODE_FILES` plus `lab/jr.py`, `lab/j5.py` and `lab/j5labels.py`; `lab/warmup.py` and `lab/models.json` are
  outside every code hash, as in earlier cycles, and the manifest's and the lock's sha256 are in each shard's
  provenance (`docs/lab/README.md`, "Everything is public") (E26).
- **Build 1's wiring.** `lab/models.json` (the key `a-4b-r`, item 1 of "What reasoning on is") and
  `lab/models.lock.json` (a-4b's entry copied under the new key), both written by a test-pinned helper that copies
  a-4b's blocks, so that no commit message, BUILD note or console line carries the repository or file name;
  `docs/lab/MODELS.md` (the key's row, saying "the same file as a-4b" and its `server_args`, copying no cell) (E36);
  `lab/warmup.py` (the rule for an entry with `--reasoning on`, item 7: the inverted probe, no skip on a cut, the
  probe's and the call's deadline from the task, the boolean and the token count; `warm_tasks` branches for `jr` and
  `j5` with the generated payloads, under branch T carrying T's lists as A003's C12 does; the docstring); `lab/notes.py`
  (the new warm-up sentence for missing reasoning, the check's notes; every sentence free of ASCII digits,
  `tests/lab/test_lab_guards.py`, `test_notes_hold_no_digits`); `lab/request.py` (`EXPERIMENTS` gains `jr`; `_jr`);
  `lab/plan.py` (`build_units`' `jr` branch; `plan_outputs` and `OUTPUT_KEYS` gaining the check's term run and artifact,
  which the workflow's `terms` step reads; `tests/lab/test_lab_plan.py`) (E26); `lab/prereg.py` (`_jr`: under branch T
  the term check through A003's `lab.j4terms`, the payloads' term lists, the pins); `lab/units.py` (`ADAPTERS["jr"]`,
  `required_tasks`, `write_routing` with the 900-s deadline and `max_retries` 0, `build_argv` and `run_unit`'s budget
  list, `harness_status`, `harness_measurement`, `harness_verdict` (E26), `participation`, `unit_notes`, `public_text`
  giving a `jr` unit the synthetic note, `PREREG_EXPERIMENTS`, and the path of the shard's server log handed to the `jr`
  unit for S6); `lab/aggregate.py` (`jr_block`), `lab/summary.py` (the check's lines), `lab/dryrun.py`,
  `lab/__init__.py`; `.github/workflows/mycelic-lab.yml` (the `terms` step keyed also on the check's term output);
  `docs/lab/REFERENCE.md`, `docs/lab/README.md` (the troubleshooting row for the new warm-up sentence),
  `lab/requests/README.md`, `docs/lab/INTEGRATION.md`.
- **Build 2's wiring, every function a j5 kind touches** (A003's list, with the additions): `lab/request.py`
  (`EXPERIMENTS` gains `j5`; `_J5_KEYS`, `_J5_REQUIRED`; `_j5_exclude_list` with four entries; the two-key check of "The
  request" and its `reasoning` false form (E3); `_primary` with the branch's arms and the `decision_9` field (E2));
  `lab/plan.py` (`build_units`' `j5` branch with each unit's key and the arm-to-key map, and the shard matrix ordered
  with P's key's shards first (E19); `exclude_entries` with four entries; `plan_outputs` and `OUTPUT_KEYS` gaining the
  fourth entry's run and artifact and j5's term outputs); `.github/workflows/mycelic-lab.yml` (the step `earlier-4`
  after `earlier-3`, of the same shape; for a `j5` plan, the run job's condition requiring every provision job's success
  (E9); and, only if the check's S6 count is above 0, `server/*.log` left out of every `a-4b-r` shard's uploaded
  artifact (E12)); `lab/prereg.py` (`_j5`: the eleven sources through `lab.j5labels`, the split under branch T, the
  draw, J001's questions with 30 parts, the comparators by branch, the chain file, the pins; `preregister`'s unit list
  and manifest key, `main()`'s `prereg:` line, `load_stand_in` with four entries); `lab/units.py` (`ADAPTERS["j5"]`;
  `required_tasks` and `write_routing` reading the unit's key and base task; `build_argv` and `run_unit`'s budget list
  gaining `j5`; `harness_status`, `participation`, `harness_measurement`, `harness_verdict`, `unit_notes`,
  `public_text`, `PREREG_EXPERIMENTS`, `REPLY_PARTICIPATION`, `run_unit`'s `with_pack` list); `lab/warmup.py` (the j5
  branch registering each shard's one task); `lab/responder.py` (nothing under branch N: the fake answers by task name,
  and R1 shares R0's; under branch T, A003's handler); `lab/aggregate.py` (`j5_block`: the two sets, the four arms with
  the comparators, the words by branch, the lifts, the counts of rule 14, `primary_result` with the headline secondary's
  word; the manifest cross-check); `lab/summary.py` (`J5_ARMS` per branch, `J5_LIFTS`; the plan summary's eleven sources
  and the report's sections); `lab/notes.py`; `lab/dryrun.py` (`--j5-data`, exclusive with the other data flags);
  `lab/__init__.py`; the four lab documents.
- **Tests, declared.** `tests/lab/test_lab_jr.py` pins: the 16 payloads (generated, codes empty, the worst-case four,
  the term lists under branch T) and calls 17 to 20; each clause S1 to S7 on synthetic figures, at and either side of
  each bound, S4's four parts, S5's parts (i) to (iii) and S7's empty and `{` contents included (E8, E10, E11); S6's
  count on a synthetic log with and without a 40-character run, the reasoning never written (E12); `max_retries` 0; the
  console line holding no reply, no reasoning, no term, no narrative and no payload (E38); the warm-up's inverted probe
  and its no-skip on a cut for an entry with `--reasoning on`, and today's rules unchanged for a-4b (the existing
  `tests/lab/test_lab_server.py:319-322` test kept); the probe's deadline from the task; the manifest's new key equal to
  a-4b's in every field but the alias and `server_args`, and its lock entry equal to a-4b's
  (`tests/lab/test_lab_shipped.py:27, 49` gain the key and keep a-4b's pin). `tests/lab/test_lab_j5.py` pins: R1's
  TaskSpec equal to R0's in every field but `max_tokens`, under both bases; the messages of R1 and R0 equal byte for
  byte (E16); every `rule N` of this file naming the item whose bold title the test lists; the four arms per branch and
  the arm-to-key map; the adaptation rule's four cases on synthetic A003 cells, in order, ties failing, completeness
  read from the rule-13 word, a printed 0.000 not above 0 (E4, E5, E20); the words by branch through
  `lab.j2.verdict_word` and `lab.j4.verdict_word` unchanged; a cut first attempt followed by a valid repair, and by a
  cut repair, giving the counts of rule 14 and an `unknown` verdict for the second, and a cut first attempt whose
  content is valid counted apart and scored as answered (E11); the request's two forms and the `decision_9` field (E2,
  E3); P's key's shards first (E19); the four layouts' checks, each pinned equal to the pinned function on the earlier
  layouts case for case; the eleven sources and the stop rules on synthetic artifacts (each cycle by own sources, by a
  chain with the others established, chains alone stopping, R2 to R4 `not attempted`, every consistency count stopping,
  K4 with A003's split removals); the draw over the eligible list less 600 refs and the removals, disjoint from four
  synthetic draws, the chain file of 750 lines plus the removals; `plan_outputs` on a j5 and every earlier plan; the
  template giving 60 units in 60 shards with 30 on each key; the runner through the fake server (one pass, the pins
  refused before any call, a stop mid-part); the small and the template dry runs with every number traced and no ref,
  term or reasoning anywhere. `tests/lab/test_lab_workflow.py`, `tests/lab/test_lab_plan.py`, `tests/lab/helpers.py`,
  `tests/lab/test_lab_docs.py`, `tests/lab/test_lab_sim.py` and `tests/lab/test_lab_request.py` change where they list
  the experiments, the templates, the output keys or the download steps.
- **The guards the build and this file obey.** No model-family name anywhere: `tests/mycelic/test_collective_guards.py`
  (`NoModelNamesTests`, `:734-761`, which scans `docs/collective` and the collective code) and
  `tests/lab/test_lab_guards.py` (which scans `lab/`, `tests/lab/` and `docs/lab/`); the models are a-0p5b, a-1p5b
  and a-4b, the new key names a server setting of a-4b, and the deployment branch is called that. No record ref, no
  term, no narrative and no reasoning in any report, summary, console line, commit or document. No ASCII digit in any
  `lab/notes.py` sentence. Lab Python imports only the standard library, `mycelic` and `lab`. The check makes no
  network call but the lab's artifact downloads, the cache restores and its provision jobs' query to the model hub
  (and the weight download on a cache miss); the judging run's plan job none but the archive's download and the five
  artifact downloads; no inference request leaves a runner (E34).
- **Before each merge** (section 2.3): `python -m pytest tests/lab -q -p no:cacheprovider` in the background, the
  onboard and guard suites, and the template dry runs: `python -m lab.dryrun --request
  lab/templates/reasoning-check-004.json --out OUT`, which must show one unit in one shard, every clause computed on
  the fake's figures and no reply or reasoning in any line; `python -m lab.dryrun --request
  lab/templates/accuracy-004.json --out OUT --j5-data DIR`, which must show 60 units in 60 shards, every one of the
  eleven sources verified on the stand-ins, the draw line with eligible after exclusion at least 150 and overlap 0,
  the comparators printed, every unit `ok` and `plumbing`, every word `incomplete`, the comparisons 29, and no ref,
  term or reasoning anywhere; and `python -m lab.dryrun --request lab/templates/accuracy-004-control.json --out OUT
  --j5-data DIR`, which must show 30 units in 30 shards, all on `a-4b`, and the comparisons 27 (E3). In a dry run the
  fake answers R1 as it answers R0 and never reasons, so nothing in a dry run measures the lever; `BUILD-A004.md` says
  so.

## Placeholders to be filled by dated amendments before the run

Renumbered by the amendment before any build (E1); rule 17 says what each amendment may do, and nothing else.

From A003's result run, by the amendment that reads the branch (before the timing check is pushed), each labelled
"from A003's result run":

1. The branch (confirmation case, hypothesis case, branch T or branch N), with the cells it was read from (the
   rule-13 verdict words of D and B and the printed `D_minus_B` row; E4, E5, E20), quoted as CHOICE-A003's Runs entry
   prints them; which aggregate's cells they are (E21); and whether A003's plan job's comparator figures were read.
2. The base task, P's arm and the headline secondary, read from the branch (the request's `base_task` and `primary`).
3. A003's result run's id and attempt, its plan artifact's name, id and `expires_at` as the Actions API lists it.
4. A003's labels sha256 prefix, questions sha256 prefix, chain file prefix and line count, and complaint file prefix.
5. A003's two split counts (by record id and by text), as its labels record carries them.
6. The comparison count as A003's aggregate printed it (25 expected; the declared sum governs, E23), and this
   cycle's (29, or 27 under rule 18's R0-only request).
7. The control task's recorded timing on A003's draw (E18): under branch T and the confirmation case task D's, under
   branch N and the hypothesis case task B's; per-call latency (median and 95th percentile) with the runner CPU model
   and output tokens (median and 95th percentile), as CHOICE-A003's Runs entry prints them. A timing, never an
   accuracy figure; it changes no setting.

From the timing check, by the amendment after it (before build 2):

8. The check's lab run id, its request's sha256 and the commits of build 1.
9. Each clause's outcome (S1 to S5 and S7) with its counts, and the warm-up's probe booleans and token counts.
10. The measured figures: output tokens, latency, time to the first token, decode time per token and, for calls 13
    to 16, the time to the first token per character (median, 95th percentile, maximum); call 19's content length
    and whether it was empty; the runner CPU model beside every recorded a-4b figure for that CPU model (E8); the
    reasoning key's provision digest beside a-4b's `7485fe6f11af…`.
11. The planning figures against the measured ones (0.30 s per output token; 60 s and 100 s to a judging call's
    first token), and the margin of "Why the units are this size" recomputed with the measured figures (reported,
    not used to change any setting).
12. S6's count, and whether build 2 therefore leaves the reasoning key's server logs out of the judging run's
    artifacts (a consequence fixed by S6, not a choice; E12).
13. Whether the check passed, and so which judging request is pushed: under branches N and T the four-arm request or
    none (lever 6 then needs a new choice file); in the two cases the four-arm request or rule 18's R0-only request
    (E3).

Conditional, and not a placeholder of this rule's figures: a chief-scientist call on decision 9 that counts unclear
as no, received before the judging request is pushed, is adopted by a dated amendment with no other content, and
installs exactly the pair "The base task and the arms" names for it (E2).

## Runs

None yet. No code exists for this cycle, the timing check has not run, and no record of this cycle's draw exists.

**The timing check's entry** will hold: the lab run id, the request file and its sha256, the commits of the rule, its
amendments and the request; the plan job's line (under branch T, the term artifact verified); the shard's runner CPU
model beside every recorded a-4b figure for it, the server start and the warm-up record (the probe's two booleans and
token counts, `repeat_identical`); the check's fixed line (each clause's counts and outcome; the quantiles of the
output tokens, the latency, the time to the first token, the decode time per token and the first token per
character; call 19's content length; S6's count) (E8, E11, E12); the reasoning key's provision digest beside a-4b's;
the report's marker line with its sha256 recomputed and matching; whether the check passed, and which judging request
follows (E3); and that no drawn record, no reply text, no term, no narrative and no reasoning appear anywhere (E38).

**The judging run's entry** will hold: the lab run id, the request file and its sha256 (and which of the two forms
it is, E3), the commits of the rule, its amendments and the request, and the branch in force with the A003 figures
that fixed it; under branch T and the confirmation case the request's push date beside the term artifact's
`expires_at`; the plan job's fixed draw line (`available`, the excluded count with A003's split removals, eligible
after exclusion, overlap 0, the outcome of each of the eleven sources with any by-chain reading and every consistency
count, the labels', questions', prereg's and chain file's prefixes with the chain's line count, the complaint file's
prefix beside the earlier days'; under branch T the split's and the term check's lines; on a re-run after a plan job
printed, both plan jobs' lines) and the plan job's time (E30); the comparators on the fresh draw with intervals; the
four arms' table (balanced accuracy with interval, sensitivity, specificity, unknown share, the paired difference to
each lexical judge of the branch, the bound on the same records, the pass test, the verdict word, parts finished of
30 and records left out per key); the verdict on P in rule 13's words, with the headline secondary's word beside it
labelled as rule 5 says; the headline; the lifts with their readings, and R1 labelled truncation-bound when more than
0.10 of its questions are unanswered after the repair (E24); the yes rates, the verdict-change table and the
`unclear` shares; the cut and repair counts per key and kind, the cut first attempts scored as answered among them
(E11); R0 and R0m beside A003's matching arms and A001's and A002's negation arms as a between-draw difference; the
model-call latency per key with the runner CPU model, seconds per output token for R1, each shard's wall time;
whether the reasoning key's server logs were left out of the artifacts (S6; E12); anything that stopped before a
model ran; the comparison count ("4 this cycle, 29 since the loop began", or "2 this cycle, 27 since the loop began"
under rule 18's R0-only request); and the public-text sentence: on public text the model may have seen in training,
scored against filed codes, which are not checked labels.

## Amended before any build, 2026-10-14

**A003's result was not known when this amendment was written, and this amendment assumes nothing about it.** It was
written on 2026-10-14, from 19:30 UTC, by the AI system that wrote the rule, on the rule as committed at `e6b9a45`
(18:54:16 UTC), which holds this file alone on top of the deployment branch's `831f424`. At that commit A003's
judging kind `j4` is not merged (no `lab/j4.py` exists) and A003's judging run has not run; no A004 code exists, no
A004 request has been pushed, the timing check has not run, and no record of this cycle's draw exists. No figure of
A003's judging run is cited, because none exists.

Reviews of the rule at `e6b9a45` against the lab's code at the same commit, the loop prompt and the earlier rules
found thirteen blocking gaps and thirty-nine smaller ones, several raised by two or three reviews. The changes are E1
to E38, numbered as this cycle's task asks (A001's A1 to A12, A002's B1 to B25 and A003's C1 to C32 and T1 to T11 live
in their own files and are cited with those files' names; where a review labelled its own findings B1 to B3 or M1 to
M13, the label is quoted once to say which finding an item answers, and is never one of A002's items). Each item
names its findings by where they point and says what the finding was, why it is right, what the text now says and
what it changes; "Declined" gives the reason for every finding, or clause of one, not adopted as written. Where the
text above and this section disagree, this section wins; the body above was edited to agree, and each edit carries
its item's number in parentheses.

Since the rule was committed, its author has read the files the items cite: `lab/manifest.py`, `lab/models.json` (its
keys and arguments; no repository or file name is copied here), `lab/models.lock.json` (sizes and digests),
`lab/warmup.py`, `lab/server.py`, `lab/shard.py`, `lab/plan.py`, `lab/request.py`, `lab/units.py`,
`lab/provision.py`, `lab/aggregate.py`, `lab/notes.py`, `lab/j1.py`, `lab/j2.py`, `.github/workflows/mycelic-lab.yml`,
`mycelic/collective/edge/verify.py`, `mycelic/collective/edge/extract.py`, `mycelic/collective/inference/client.py`,
`runtime.py`, `jsonparse.py`, `tasks.py` and `routing.py`, `mycelic/collective/experiments/e3_latency.py` and
`e1_extract.py`, the vehicle pack's `vocabulary.json`, `docs/lab/RESULTS.md` (Runs 1, 2, 5, 7 and 9 to 12),
`docs/lab/README.md`, `docs/lab/MODELS.md`, `docs/lab/REFERENCE.md`, CHOICE-A001's Run 1, CHOICE-A002's Run 1 and
its amendment, CHOICE-A003 whole (C1 to C32, T1 to T11 and its Runs templates), `docs/strategy/LOOP-48H.md` sections
2, 6 and 7, `docs/handoff/HANDOFF-2026-10-09.md` (Q9) and the status log at `831f424`. Figures computed here rather
than copied are labelled derived. The character counts of rendered prompts were computed at `e6b9a45` with the
repository's own `render_messages` (`mycelic/collective/inference/tasks.py:78-81`), `lab.warmup` and
`e3_latency.filler`, which read no record and call no model. No figure here comes from this cycle's draw or from
A003's judging run; no reasoning text exists; no stored reply of J001, A001 or A002 has been read; no term has been
seen.

**No finding asked to change the pinned metric, and none is changed.** 86 stays balanced accuracy 0.860 against the
filed-category key at record level with the codes hidden, on 150 fresh records scored as J001; rule 13's block stays
word for word LOOP-48H section 2.5; the comparators, the interval rule and the confirmation rule are untouched;
site-level balanced accuracy stays rejected; the comparison count stays 25 before this cycle. No floor is lowered:
the adaptation rule loses discretion (E1, E2, E4, E5, E20, E21); the confirmation obligation no longer depends on the
lever (E3); the design is re-sized on slower, sourced figures (E6, E7); the timing check gains calls and clauses
(E10, E11, E12) and stops where the re-sized design stops fitting rather than at its planning figures (E8); the rest
are figures traced to their sources, readings, the build plan and wording. "Floors after this amendment" lists every
floor.

### E1. No amendment re-chooses a setting after A003's result or the check's figures are seen (rule 17; "The base task and the arms", first paragraph; "The build plan", steps (2) and (4); trap 16; "Placeholders")

- **The findings** (one blocking, one smaller). Rule 17 let "a numbered, reviewed amendment" change any setting before
  the judging run (the base, the reading, the arms, the cap, the parts). The amendments the build plan schedules come
  after A003's result is recorded (step 2) and after the timing check's figures are seen (step 4), so the base, P and
  the arms could have been re-chosen after A003's D, B, Dr and Br figures and R1's behaviour on generated records were
  seen, which defeats a pre-registered adaptation rule. A003 bound its own post-result amendment with three
  constraints (CHOICE-A003, "Amended before any build", (a) to (c)); this rule had none. The smaller finding: rule 17
  against trap 16 ("can only stop this rule, never tune it") would let the cap rise after the check.
- **Why the findings are right:** rule 17's sentence, read alone, allowed exactly that, and trap 16 was a statement,
  not a rule.
- **Now:** rule 17 carries, and the paragraph "What may change after this file is read against A003's result" of
  "The base task and the arms" repeats, the clause: from the commit of this amendment, no amendment changes the four
  cases, their order and the cells they read; P under any case, except as E2 fixes now for a decision-9 call; the four
  arms and their keys; the readings; the comparators and the pass test by branch; the seeds, n and parts; the cap, the
  deadline, the planning figures, the timing check's calls and its clauses; or the R0-only request of rule 18. An
  amendment written after A003's result is recorded may only read the branch and fill placeholders 1 to 7, quoting the
  cells, or correct a citation to its source, and states that the branch the cells select is unchanged by it. An
  amendment after the timing check may only fill placeholders 8 to 13. The amendment that adopts a decision-9 call has
  no other content. An amendment that fixes the lab-side cause of a stop before any model ran, as LOOP-48H section 6.3
  requires, changes none of these. A review finding whose fix would change any of these after A003's result is
  recorded is declined, and that change is a new choice file on a fresh draw. Rule 17's phrase "a numbered, reviewed
  amendment before it" is subject to the clause; trap 16 and the build plan's steps (2) and (4) cite it.
- **What it changes:** discretion removed. The finding's text froze the settings "from this rule's commit"; this item
  freezes them from this amendment's commit, because the other findings of the same reviews require changes to the
  cells read (E4, E5), to P under a call (E2), to the planning figures (E6, E7) and to the check (E8, E10 to E12), and
  this amendment is written before A003's judging run has run and before any check exists, so nothing it changes was
  chosen after looking. The placeholder numbers are this amendment's (its list below: 1 to 7 from A003, 8 to 13 from
  the check), not the rule's first 1 to 6 and 7 to 10.

### E2. A decision-9 call installs one named pair, and nothing else ("The base task and the arms", the paragraph after case 4; the declaration; rule 5; the settings, row "Primary pair"; "Decisions this does not make"; "The request"; "Placeholders")

- **The finding** (blocking). "A call on decision 9 received before the judging request is pushed may make a mapped
  reading eligible, by a dated amendment" named neither the reading a call would install nor the resulting P per
  case, and "may" left the author a choice. A late call could arrive after A003's figures are read, or after the
  timing check shows how often R1 answers `unclear` on generated records; the author could then choose whether to
  promote R1m, or in case C swap A003's pair D for its mapped reading (R0m, which is Dr's pair). Section 2.5 requires
  the confirmed pair to be the same P, and A002's B1 had fixed which pair a call installs.
- **Why the finding is right:** "may" is discretion, and the clause named no pair.
- **Now:** the paragraph after case 4 reads: "A chief-scientist call on decision 9 that counts unclear as no,
  received before the judging request is pushed, is adopted by a dated amendment with no other content. Under branch
  N it makes P = (a-4b, `negation_reasoning_mapped`); under branch T, P = (a-4b, `negation_terms_reasoning_mapped`).
  R1, read as answered, becomes the headline secondary. In the confirmation case and the hypothesis case P does not
  change, because section 2.5 names the pair that must pass again as A003 recorded it, and the hypothesis is the
  secondary as it met the test; R0m's word is printed beside it. Any other call bears on the next cycle. A mapped arm
  of A003 (Dr or Br) that met its pass test is never this cycle's primary, with or without a call." The declaration's
  decision-9 bullet, rule 5, the settings row "Primary pair", "Decisions this does not make" and the closing
  paragraph of "Placeholders" say the same. "The request" carries a field `decision_9`, `default` unless that
  amendment sets it to `unclear_as_no`; `lab.request` accepts `negation_reasoning_mapped` (base `negation`) or
  `negation_terms_reasoning_mapped` (base `negation_terms`) as the primary arm only with `unclear_as_no`, and never a
  mapped R0 arm.
- **What it changes:** discretion removed; A002's B1 precedent restored. No call has been received (the status log at
  `831f424`, "Decisions received").

### E3. In the two cases the judging run does not wait on the lever (rule 18; "The timing check", the stop rule; "The request"; rule 12; rule 15; the settings, rows "Units" and "Requests"; the build plan)

- **The finding** (blocking). In case C, P = (a-4b, `negation_terms`) is R0, served with reasoning off; its
  confirmation is section 2.5's obligation and does not depend on reasoning. Rule 18 made the whole judging run, P
  included, wait on a check of the reasoning key: a failed check would have left A003's P unconfirmed, and who
  confirms it, and whether a later choice file still counts as "the next cycle's CHOICE file", would have been decided
  after both A003's result and the check's figures were seen. The same held for the hypothesis pair in case H.
- **Why the finding is right:** rule 18 and the check's stop rule said so, and no text said what follows.
- **Now:** rule 18 reads: under branches N and T the judging request is pushed only after the check passed, and a
  failed check ends the rule (lever 6 needs a new choice file), as declared. "In the confirmation case and the
  hypothesis case, the judging run does not depend on the timing check's outcome. If the check passes, the four arms
  run as declared. If it fails, or stops before its last call for any reason other than infrastructure, the judging
  run is pushed with R0 and R0m only: 30 units on a-4b in 30 shards, 2 (model, arm, cycle) comparisons this cycle, 27
  since the loop began, `comparisons_before` 25. P, the pass test and the comparators stay as declared. The lever's
  arms go to a new choice file." A check that stops before any call for an infrastructure reason is pushed again
  unchanged once; if the re-push also stops before its last call, for any reason, the check has failed. "The request"
  writes out the R0-only request now (`lab/templates/accuracy-004-control.json`, copied to
  `lab/requests/accuracy-004.json` in place of the four-arm template: `models` `["a-4b"]`, `reasoning` false, the
  case's `base_task` and P, everything else as the four-arm request's), and `lab.request` accepts it only with the
  case's P; the four-arm request carries `reasoning` true. Rule 12 adds the count (2 this cycle, 27 since the loop
  began), rule 15 the 30 units, the settings rows "Units" and "Requests" both forms, the build plan both templates and
  their dry runs.
- **What it changes:** section 2.5's confirmation (case C) and the hypothesis's test (case H) run whatever the
  reasoning key does; one outcome, written now, replaces a decision that would have been made after looking.

### E4. A003's sets are complete when their rule-13 word says so ("The base task and the arms", "The cells read"; cases 3 and 4)

- **The finding** (blocking). "The cells read" defined a complete set twice: as CHOICE-A003 rule 13 reads "complete"
  (the dropped share at most 0.01) and as the arms table's "Records left out" 0. One record dropped by a transport
  failure (1/150 = 0.0067, derived) is complete by rule 13 (`lab/j1.py:128`; `lab/j2.py:264-273` withholds only above
  0.01) and incomplete by the cell, and the difference flips branch T against branch N. `measurement` true was tied to
  no cell, and A003's Runs template does not commit to "Parts finished" and "Records left out" columns in its arms
  table: it lists "the parts finished per task and `records left out` per arm" separately (CHOICE-A003, "## Runs",
  the judging run's entry), so the named cells may not exist.
- **Why the finding is right:** checked at `e6b9a45` in `lab/j2.py:264-273` and CHOICE-A003's Runs template.
- **Now:** "A set of A003 is complete exactly when the verdict word of its as-answered arm (D for task D, B for task
  B), as A003's rule 13 computes it (`lab.j4.verdict_word`, CHOICE-A003 C19), is not `incomplete`", read wherever
  CHOICE-A003's Runs entry prints that word (the verdict-word column of its arms table, or its verdict paragraph).
  That word is `incomplete` when a part of the set did not finish, the set's display class is not `model` or its
  `measurement` is not true, more than 0.01 of its records were dropped by transport failures, or the balanced
  accuracy, the drafted-judge difference, the bound, the hand-judge difference or the term-presence difference is not
  a number (`lab/j2.py:264-273`; C19). The two named columns are gone from the cells.
- **What it changes:** one computable cell; the one-record case is complete; no branch depends on a column A003 has
  not committed to. A set whose word is `incomplete` only because a comparator figure is missing is read as
  incomplete, which can only send the rule to branch N, its default.

### E5. Cases C and H read A003's rule-13 word (cases 1 and 2)

- **The finding** (blocking). A003's Runs entry prints two words for each arm: its rule-13 word (`lab.j4.verdict_word`,
  with the hand clause and the term-presence clause) and "the word under lever 5's text alone" (CHOICE-A003 rule 13
  and C6). If D's word under lever 5's text alone were `pass_provisional` and its rule-13 word `partial`, the rule did
  not say whether case C, and with it section 2.5's confirmation obligation, applied: a choice left until after
  looking.
- **Why the finding is right:** CHOICE-A003 rule 13 prints both words, and cases 1 and 2 named neither.
- **Now:** before case 1: "Cases C and H read A003's rule-13 word (`lab.j4.verdict_word`, the arms table's 'Verdict
  word'), the verdict A003 declared before its run. The word under lever 5's text alone selects no case. A D or B whose
  word under lever 5's text alone is `pass_provisional` while its rule-13 word is not is recorded as a hypothesis and
  changes nothing here."
- **Why this reading and not the other:** A003's declared verdict is its rule-13 word, stricter than lever 5's text
  and allowed by it (section 2.6, item 5, requires the drafted judge beaten and forbids no further clause); a pair that
  did not pass A003's declared test did not pass in A003, so section 2.5 creates no confirmation obligation for it. The
  other reading would let the weaker of two printed words create the obligation.
- **What it changes:** one word named.

### E6. The recorded figures, complete, and planning figures derived from them ("Why the units are this size", the table, "Derived here" and "The planning figures"; "What reasoning on is", items 4 and 5)

- **The findings** (three blocking, one smaller: the review's B1, the finding on the planning figures, the planning
  part of the finding on S4's first-token clause, and the finding on the thousands separators). (a) The table, "what
  the lab has measured" for a-4b, left out RESULTS Run 5 (lab run 37866392839: E3 at concurrency 1 on an Intel Xeon
  Platinum 8573C, decode medians 4.2 and 5.6 tokens per second, first-token medians 25.681 and 11.344 s) and RESULTS
  Run 7 (lab run 37910989965: E1 extraction on real complaints, median 25.8 s per call, repeats 1 and 2 timed out at
  the unit's 150-minute limit). (b) "Below every recorded and derived figure, with a quarter's room over the slowest
  derived one (0.198 s) for decoding that slows as the context grows, which this lab has not measured" was wrong
  twice: the slowest recorded decode is 4.2 tokens per second, 0.238 s per token, so 0.25 s left about 5% of room; and
  Runs 1 and 5 recorded a-4b's decode at two prompt sizes, slower at the longer. (c) The 60-s first-token figure was
  justified by a whole-call 95th percentile of short negation prompts (16.4 s), while the table itself held first-
  token medians of 35.597 and 75.934 s on the AMD EPYC 9V74; "60 s leaves room for task D's longer payload" did not
  hold. (d) The table added thousands separators that CHOICE-A001 and CHOICE-A002 do not print (9,275.1 for 9275.1).
- **Why the findings are right:** RESULTS.md Run 5 ("Latency (E3, a-4b, Intel Xeon Platinum 8573C ...)") and Run 7
  print those figures; the E3 user turns are about 5,370 and 10,690 characters (derived here: `render_messages` over
  `e3_latency`'s payload, median of six seeds); 4.2 tokens per second is 0.238 s per token, so 0.25 s is 5% above it;
  CHOICE-A001 Run 1 and CHOICE-A002 Run 1 print their milliseconds without separators.
- **Now:** the table gains the two rows, cited as written, and every figure is copied as its record prints it.
  "Derived here" gains three paragraphs: decoding (the slowest recorded decode 0.238 s per token on the 8573C; 22% and
  25% slower in tokens per second when the E3 prompt doubled, Runs 1 and 5; no record over the longer context of a
  reasoning call); the first token (6.6 and 7.1 ms per character on the 9V74, 2.1 and 2.4 ms on the 8573C, about 9.3
  to 9.9 ms on the 7763 by the 1.40 ratio of negation medians, CHOICE-A002 Run 1; the system text is cached, the user
  turn processed again); and the prompts this cycle sends (a judging call's user turn up to about 3,100 characters,
  about 6,300 with terms; the check's calls 13 to 16 at 6,289, up to about 9,500 with terms). The planning figures
  are **0.30 s per output token**, 26% above the slowest recorded decode, the room being for a context that grows
  with the reasoning; **60 s to a judging call's first token under branch N and the hypothesis case and 100 s under
  branch T and the confirmation case**, from a bound of **15 ms per character of the user turn**, about 1.5 times the
  rate derived for the 7763, applied to the longest judging prompts (about 47 and 95 s); the 60 s for real narratives
  under branch N and the hypothesis case only; and **35 minutes before the warm-up** (E7). Item 4's "Why 2,048" and
  item 5's per-call figures cite them.
- **What it changes:** the planning figures are slower and sourced. The design is re-sized on them (E7), and the
  check stops at the design's limits (E8).

### E7. The design re-sized on the corrected figures; the cap kept at 2,048; the check given room ("Why the units are this size", "The worst case by design" and "The timing check's budget"; "What reasoning on is", items 4 and 5; the settings and "The request", the check's minutes)

- **The findings** (two blocking clauses, two smaller findings). The review's B1 asked that the worst case be
  recomputed with corrected figures and the cap changed if they do not fit. The finding on S4's first-token clause
  asked that the worst case be set from the counted worst prompt and the check's budget redone, raising the check to
  `job_minutes` 330 and `minutes` 300 if more margin is wanted. M3: the restriction of the cap to the form 1,024 × k
  was not explained, and at the old planning figures 2,560 would have fitted. M9: the step timeouts before the
  warm-up add to about 35 minutes (two cache restores of 5 minutes, `.github/workflows/mycelic-lab.yml:214, 224`;
  prepare 20, `:241`; the server's 300-s health deadline, `lab/server.py:60`), more than the 30-minute allowance.
- **Why the findings are right:** with E6's figures the old arithmetic no longer holds, and the step timeouts are as
  M9 says.
- **Now:** the allowance before the warm-up is 35 minutes. At 0.30 s per token and the first tokens of E6, a call at
  the cap takes at most 674.4 s (branch N, the hypothesis case) or 714.4 s (branch T, the confirmation case), 225.6 and
  185.6 s inside the 900-s deadline; 23 calls (20 in a part, 3 in the warm-up) take 15,361.2 s or 16,305.2 s against
  the 16,650 s a shard leaves for calls (313 minutes less 35 less 30 s), about 21 and 6 minutes to spare; the design's
  tolerances are about 0.327 and 0.307 s per token, the deadline's 0.41 and 0.39. The largest cap that fits is about
  2,234 tokens under branch N and 2,098 under branch T; 2,560 (868 s a call under branch T, 23 calls 19,838 s) and
  3,072 do not fit, so the cap stays 2,048, and M3's trade is answered by the arithmetic, not by a choice of form. The
  control's worst case is 136.8 or 176.8 s a call, an R0 shard at most about 85 or 101 minutes. The timing check's
  calls (20 with E11 and E12) take at most about 205 or 221 minutes and its warm-up about 31 or 34; with the rule's
  first `minutes` 240 and `job_minutes` 270 the unit would have had about 186 or 184 minutes, too few, so the check's
  request is `minutes` 300 and `job_minutes` 330 (the request validator's largest `job_minutes`,
  `lab/request.py:11`), leaving about 41 or 23 minutes. The residual is stated: the plan job prints no narrative
  length and the pack cuts at 6,000 characters, not at the 2,824 the repository records (LOOP-48H section 2.6;
  HANDOFF-2026-10-09, Q9); a part whose five records all ran to 6,000 characters would add up to about 11 or 14
  minutes, more than branch T's 6 minutes of room, and only with every call and every repair at the cap.
- **What it changes:** the allowance is 35 minutes; the check's request is `minutes` 300 and `job_minutes` 330; the
  cap stays 2,048 and the deadline 900 s. B1's condition "if the corrected figures do not fit, change the cap" did not
  arise.

### E8. S4 stops at the design's limits, by rate for the long calls ("The timing check", clause S4 and "What it prints"; rule 18; the settings, row "Timing check")

- **The findings** (three blocking: the finding on S4's first-token clause, the review's B2 and the clause of the
  planning-figures finding on S4; one smaller). S4 applied 60 s to the first token of every call, calls 13 to 16
  included, whose user turns are 6,289 characters under branch N and up to about 9,500 under branch T (derived here);
  at Run 1's rate the 9V74 takes about 43 and 67 s, and the 7763 about 60 and 94 s (derived, E6), so S4 failed by
  design under branch T and the confirmation case on every recorded CPU and under branch N on the 7763, ending the
  lever. S4 also stopped at the planning figures rather than at the design's tolerance, so whether lever 6 survived
  depended on which CPU model GitHub assigned the one check shard, not on feasibility; a pass on a fast runner bounds
  none of the 30 R1 shards' runners; and the decode clause, measured at whatever length each call reasoned to, could
  pass without showing that a call at the cap fits. The smaller finding: S4 is measured on one runner, and a-4b's
  negation medians differ 1.9 times between the 7763 and the 8573C (9297.1 against 4909.1 ms, CHOICE-A002 Run 1).
- **Why the findings are right:** the derivations hold at `e6b9a45` (the user-turn lengths are `render_messages` over
  `lab.warmup`'s worst payload for the negation task, 6,298 characters, 6,289 with the codes emptied, plus up to about
  3,200 characters of terms).
- **Now:** S4 has four parts, each a failing condition. **S4a**, the prompt rate: on each of calls 13 to 16 the time to
  the first token per character of the user turn is at most 15 ms, and on each of calls 1 to 12 the time to the first
  token is at most 60 s. **S4b**, the decode rate: the largest decode time per output token over calls 1 to 12 with at
  least 256 output tokens (when none has 256, that of the call among them with the most output tokens) is at most 0.32 s
  under branch N and the hypothesis case and 0.30 s under branch T and the confirmation case, the design's tolerances
  (E7). **S4c**, the deadline at the longest prompts: on each of calls 13 to 16, the time to the first token plus 2,047
  times the call's own decode time per token (S4b's figure when the call has fewer than 256 output tokens) is at most
  780 s, the 900-s deadline less 120 s. **S4d**: every call's latency is at most 780 s. Calls 1 to 12 carry user turns
  well below the longest judging calls' and calls 13 to 16 longer ones than any judging call's, so S4b reads the decode
  where it is lowest and S4c the deadline where it is tightest; a check whose calls all reason briefly measures the
  decode only at that length, which the text now says. The planning figures are printed beside the measured figures and
  stop nothing. S4 bounds the check's CPU model only; for the other runners the file compares the tolerances (about
  0.327 and 0.307 s per token) with the slowest recorded a-4b decode, 0.238 s per token (Run 5), which the tolerances
  exceed by 37% and 29%, and with the 22 to 25% slowdown recorded when the E3 prompt doubled; first tokens are bounded
  by the 15-ms rate, about 1.5 times the rate derived for the slowest recorded prefill. The check prints its runner's
  CPU model beside every recorded a-4b figure for that CPU model.
- **What it changes:** S4 stops where the design stops fitting, not where the planning figures are exceeded; the
  first-token bound for the longest calls is by rate. The review's per-call budget clause on every call is adopted in
  principle and replaced in form ("Declined").

### E9. A provision failure stops the run before any model judges; incompleteness read per key ("Why the units are this size", "What happens if a part does not finish"; "What reasoning on is", item 10; the build plan, build 2's wiring)

- **The finding** (blocking). This cycle has two model keys, each with its own cache entry
  (`lab-gguf-<key>-<sha16>`, `lab/manifest.py:62-64`), and the workflow's run job starts when the plan job succeeded
  whatever the provision jobs did (`.github/workflows/mycelic-lab.yml:176-177`: `!cancelled() &&
  needs.plan.result == 'success'`). If the reasoning key's provision failed, the 30 `a-4b` shards would judge while
  every R1 shard failed at prepare: "a stop before any model judged ... is not a result" and "after model calls
  started, the run is the result" would then contradict each other, and whether the draw is spent or the request
  re-pushed would be decided after R0's figures on that draw were seen.
- **Why the finding is right:** checked at `e6b9a45`.
- **Now:** the review's preferred option. Build 2 makes the run job of a `j5` plan require every provision job's
  success (a reviewed workflow change, listed in build 2's wiring), so a provision failure of either key is a stop
  before any model ran, re-pushed unchanged once (rule 17). "What happens if a part does not finish" is rewritten per
  key: P's key is `a-4b-r` under branches N and T and `a-4b` in the two cases; P's key's set incomplete makes the cycle
  incomplete; the other key's set incomplete leaves P's verdict read and the lifts unread; a shard whose preparation
  fails (a re-download that does not finish before the prepare step's deadline) is a part that did not finish; and if,
  despite the gate, one key judged while the other never did, the run is the result for whatever finished.
- **What it changes:** one outcome per case, written now.

### E10. S5 can fail in the case it exists for ("The timing check", clause S5; "What reasoning on is", item 4; the build plan's tests)

- **The findings** (one blocking, the review's B3, and two smaller). Item 4 rested on the server counting reasoning
  tokens against `max_tokens` and said S5 verifies it, but S5 ("no call reports more than 2,048 output tokens") could
  not fail when it mattered: a release that reports only the answer's tokens in `usage.completion_tokens` would report
  about 16 a call, pass S5, make S4's decode clause (calls with at least 32 output tokens) vacuous, and leave the
  reasoning bounded only by the 900-s deadline, where a timeout is a transport failure and two in a row stop the unit
  (`extract.py:85-86`). No clause required `tokens_out` or `ttft_ms` to be present, and no clause bounded a call's
  total latency.
- **Why the findings are right:** the streamed `tokens_out` is `usage.completion_tokens` as the server reports it
  (`client.py:530-539`), and the client counts reasoning and content deltas separately (`:211-220`).
- **Now:** S5 reads: no call reports more output tokens than its cap; (i) on calls 1 to 16 `tokens_out` and `ttft_ms`
  are not null, and on calls 17 to 20 `tokens_out` is not null; (ii) on calls 1 to 16 `tokens_out` is at least the
  reasoning deltas plus the content deltas the client counted (each non-empty delta carries at least one generated
  token; an event carrying both kinds counts in both and fails safe); (iii) every call that ended with
  `finish_reason` `length` reports `tokens_out` exactly its cap. S4d bounds every call's latency at 780 s (E8), the
  deadline less 120 s, where the smaller finding proposed the old planning worst case of 572 s ("Declined"). Call 19's
  cut (E11) also tests the cap on the reasoning. `tests/lab/test_lab_jr.py` pins each part at and either side of its
  bound.
- **What it changes:** S5 can fail on a server that leaves the reasoning out of its count.

### E11. The non-streamed path and a cut inside the reasoning are exercised; cut but valid first attempts counted ("The timing check", the calls, S1, S2 and S7; "What reasoning on is", items 3 and 6; rule 14; trap 6)

- **The findings** (three smaller: M1, the finding on items 3 and 6, and part of the finding on S4 and S5). All 16
  check calls were streamed, while the judging path is non-streamed `Runtime.run`; the check never observed a long
  non-streamed request (no bytes for up to about ten minutes), nor a reply cut before the reasoning closes, whose
  unclosed reasoning a release might put into the content, where `extract_object` scans for `{` (`jsonparse.py:105-
  120`) and could take an object drafted inside the reasoning. Trap 6's "Never" was unverified, and S2 looked only at
  calls that ended with `stop`. Separately, `Runtime._attempt` validates the content and not `finish_reason`
  (`runtime.py:320-350`): a first attempt that ends with `length` but parses a valid object is scored as an answer,
  with no repair.
- **Why the findings are right:** checked at `e6b9a45`.
- **Now:** the check makes four non-streamed calls after call 16: **call 17** (call 1's payload) and **call 18** (call
  13's) through `Runtime.single(..., stream=False)`, under S1, S2 and S5; **call 19**, call 1's payload as raw HTTP, as
  the warm-up's probe sends one (`lab/warmup.py:416-427`), with `max_tokens` 16, no more than the control's median
  answer (16.0 output tokens, CHOICE-A002 Run 1), so a reply that counts its reasoning against the cap cannot finish;
  and call 20 for S6 (E12). A new stop clause **S7** requires call 19 to end with `finish_reason` `length`, to carry a
  non-empty `reasoning_content`, and to have a content that is empty or begins, after white space, with `{` and holds
  no JSON object; its content's length is printed, never its content. Item 6 says the empty or partial content is the
  release's behaviour, tested by calls 17 to 19 and S7; trap 6 says what happens and where it is tested; rule 14 and
  item 6 count "first attempts cut at the cap whose content still gave a valid reply" apart and say they are scored as
  answered.
- **What it changes:** two more calls on the judging path's client, one cut call and one stop clause; one more count.

### E12. Who can read the server's log, and a count of reasoning in it ("What reasoning on is", items 8 and 10; "The timing check", S6 and call 20; "What it does not show"; the build plan; Runs)

- **The findings** (one blocking, one smaller, M4). Item 8 said the reasoning appears in no shard artifact, then kept
  the server's own log in the shard artifact, capped at 1 MiB (`lab/server.py:66`). That log is the server process's
  stdout and stderr (`lab/server.py:10, 340-342`), written by the pinned release, and item 1 says the rule does not
  read the release's behaviour from its source; nothing established what the release writes when it reasons, cuts a
  reply or fails to parse one, and no clause looked at the log. The console is safe (`lab/shard.py:375-379` keeps only
  a fixed problem, and no summary prints `log_tail`). The repository is public, so a shard artifact can be downloaded
  by any signed-in GitHub user for its 30 days; A003 states that access rule for its own artifact. Under branch T and
  the confirmation case the reasoning may restate the drafted terms (trap 4).
- **Why the findings are right:** checked at `e6b9a45`; `docs/lab/README.md`, "Everything is public", says the
  artifacts are public.
- **Now:** item 8 reads what holds: the lab's code never reads, keeps or prints reasoning text; the server's log is the
  release's, stays in the shard artifact, and any signed-in GitHub user can download it for 30 days; "What it does not
  show" says the same. The check's **call 20** is call 1's payload as raw HTTP at `max_tokens` 2,048, its
  `reasoning_content` held in memory only. **S6** (counted, not a stop): after call 20 and before the shard seals, the
  unit searches the check shard's server log for every 40-character run of that reasoning and prints the number of
  runs found, never a run. If the count is above 0, build 2 makes the workflow (`.github/workflows/mycelic-lab.yml`,
  not pinned) leave `server/*.log` out of every `a-4b-r` shard's uploaded artifact in the judging run, keeping the
  start record's printable lines; the Runs entries record the count and whether the logs were left out. Item 10 and
  build 2's wiring list the conditional change.
- **What it changes:** an unverified absolute claim replaced by what holds, a count of the leak route, and a removal of
  the route fixed now for the case the count finds it. M4's stop on a hit and its 64-character runs are taken in
  another form ("Declined").

### E13. What the run costs, three figures ("Why the units are this size", "What it costs")

- **The finding** (blocking). "By design ... cannot take more than about 13.4 hours" (2 × 77 + 2 × 325 = 804 minutes)
  used a 77-minute bound for an `a-4b` shard taken from the planning figures, not from the design; by design every
  shard is bounded only by its 325-minute job timeout (`lab/plan.py:304`), so 60 shards at 16 at a time can take four
  rounds of 325 minutes, 1,300 minutes, about 21.7 hours (derived), plus the plan, provision and aggregate jobs. "About
  1 to 2 hours in all" left out each shard's preparation, which the rule itself said no record times; and repairs
  after cuts add calls.
- **Why the finding is right:** the arithmetic and the job timeouts are as the finding says (`.github/workflows/
  mycelic-lab.yml:28, 115, 309`).
- **Now:** three figures, each labelled derived. Expected (the loop prompt's estimate and the measured control
  latency): about 1 to 2 hours of model-call time end to end, plus each round's preparation at up to 35 minutes, four
  rounds in all, each repair after a cut adding one call. At the planning figures, by design: an R1 shard at most about
  291 or 307 minutes, an R0 shard about 85 or 101, two rounds of each about 752 or 815 minutes (12.5 or 13.6 hours). By
  the job timeouts alone: up to 1,300 minutes of shard jobs, about 21.7 hours, plus the plan job (30), the provision
  jobs (60 each, in parallel) and the aggregate job (30): up to about 23.7 hours. The R0-only request's figures beside.
- **What it changes:** figures only.

### E14. What has run with reasoning on, stated as the record allows ("What the evidence says", last bullet)

- **The findings** (two smaller). "No call with reasoning on has ever run in this lab" was broader than the record.
  a-0p5b and a-1p5b run with no reasoning flag (`lab/models.json:31, 47`, empty `server_args`). The fourth gguf key of
  `lab/models.json` ran without a flag in RESULTS Run 1 and stopped its warm-up at the token limit; it was given
  `--reasoning off` in commit `132386d`, and the same stop recurred with it in RESULTS Run 2, so no record attributes
  that stop to reasoning.
- **Why the findings are right:** RESULTS Run 1, "Units without a result", and Run 2's shard table say so.
- **Now:** the bullet reads "No a-4b call with reasoning on has ever run in this lab; no server started with
  `--reasoning on` has served a call; no warm-up probe has recorded reasoning content" (its stop, `lab/notes.py:43`,
  appears in no run record), with the fourth key's note and its sources. The key is named by its place in the manifest,
  as the instructions for this loop's documents require.
- **What it changes:** a claim narrowed to its record.

### E15. "A002's best sensitivity" relabelled ("Why these arms, and why this primary pair", first paragraph)

- **The findings** (two smaller): A002's best sensitivity was 0.980 (arms R and Rm, CHOICE-A002 Run 1); 0.907 is the
  sensitivity of A002's best-scoring arm, D (and of N).
- **Now:** "A model that kept the sensitivity of A002's best arm, D (0.907) ...". The arithmetic is unchanged.
- **What it changes:** a label.

### E16. The messages are the control's byte for byte; the rendered prompt differs by the lever ("What reasoning on is", item 4; rule 3; "Why these arms"; the tests)

- **The findings** (two smaller: M7 and another): the request's messages are byte-identical, but each server renders
  them through its chat template, which applies its thinking switch differently under `--reasoning on` and `off`, so
  the tokens the model sees differ; the warm-up's fit step counts the rendered prompt per server (`/apply-template`
  then `/tokenize`, `lab/warmup.py:293-307`, applied at `:340-377`).
- **Now:** item 4 reads "the messages a reasoning arm sends are the control's byte for byte ... The rendered prompt
  differs only as each server's chat template renders its thinking switch, which is the lever; each server's fit step
  counts its own rendered prompt". "Why these arms" and the tests say "messages".
- **What it changes:** wording.

### E17. S3 is a smoke bound ("The timing check", S3; trap 9)

- **The finding** (smaller): S3 has little power (binomially it passes with a probability of about 0.35 when the true
  cut share is 0.2 and about 0.79 when it is 0.1, derived), runs on generated narratives, and could be read as bounding
  the judging run's cut rate.
- **Now:** S3 says it is a smoke bound on generated records, not an estimate; the judging run's cut share is read only
  from rule 14's counts, and the time budget is sized for every call cut (E7). Trap 9 says the same.
- **What it changes:** a reading.

### E18. The control under branch T is task D, not the negation task; the deadline difference stated ("What reasoning on is", items 4, 5 and 9; "What it costs"; "Placeholders")

- **The findings** (two smaller): under branch T and the confirmation case the control is A003's task D, with up to
  about 3,200 characters of terms, whose latency and output tokens are unmeasured until A003's run, so "the control's
  calls are unaffected" and "the 256 cap does not bind" rested on task N's figures only; and item 9's "exactly the
  configuration A001 to A003 measured" was not exact, since the endpoints give 900 s where A001 to A003 gave 600 s
  (`lab/j1.py:125`; CHOICE-A003 rule 6).
- **Now:** items 4, 5 and 9 say both; task D's (or, under branch N and the hypothesis case, task B's) per-call latency
  and output tokens on A003's draw are placeholder 7, copied from CHOICE-A003's Runs entry by the amendment that reads
  the branch: a timing, never an accuracy figure, changing no setting, read into "What it costs".
- **What it changes:** a placeholder; wording.

### E19. P's key's shards first (rule 15; "What it costs"; build 2's wiring)

- **The finding** (smaller): packing in sorted key order (`lab/plan.py:279-286`) puts the `a-4b` shards first, so under
  branches N and T a late stop (a runner-pool problem, a cancelled workflow) would lose P's set before the control;
  A003's rule 15 states the opposite principle.
- **Now:** build 2 orders the run job's matrix with P's key's shards first: `a-4b-r` under branches N and T, `a-4b` in
  the two cases (`lab/plan.py` is not pinned); rule 15 and "What it costs" say so; a test pins it.
- **What it changes:** an order.

### E20. A printed lower end of 0.000 is not above 0 ("The base task and the arms", "The cells read"; case 3)

- **The finding** (smaller): the rule did not say whether the printed three-decimal value of `D_minus_B`'s lower end or
  the unrounded `report.json` value is read; a printed 0.000 could hide +0.0004.
- **Now:** the lower end is read as CHOICE-A003's Runs entry prints it, to three decimals, and a printed 0.000 is not
  above 0 (A002's B17).
- **What it changes:** a reading named; ties fail, as everywhere.

### E21. Which aggregate's cells ("The base task and the arms", first paragraph)

- **The finding** (smaller): LOOP-48H section 6.3 allows a declared re-aggregation after model calls start, and the rule
  did not say whose cells are read if A003's Runs entry declares one.
- **Now:** the cells of the result run's first aggregate; if that aggregate printed no arms table, those of the
  re-aggregation declared in CHOICE-A003's Runs entry before it ran; the branch amendment waits for it.
- **What it changes:** one choice pre-registered.

### E22. A003 established by K4 alone ("The data and the draw", item 5)

- **The finding** (smaller): when A003 is established from K4 alone (C1 and R4 both unverified), its split removals
  cannot be subtracted: the reading yields A003's 150 records and its split removals together.
- **Now:** "A003 established by K4 alone is K4 less the other three sets, which holds A003's records and its split
  removals together: both are excluded, and the split-count consistency check is not computed and is printed as not
  computed."
- **What it changes:** a case named; the excluded set is never smaller.

### E23. Which comparison count governs (rule 12; "Placeholders", item 6)

- **The finding** (smaller): the rule did not say which count governs if A003's aggregate prints a running count other
  than 25.
- **Now:** the count is the rules' declared sum (12 + 9 + 4 = 25); a different printed figure is recorded as a defect
  of A003's aggregate and changes neither `comparisons_before` nor the 29 (or the 27 of E3).
- **What it changes:** one reading.

### E24. Greedy decoding, and when R1 is truncation-bound (traps; "How it is read"; "What it does not show"; Runs)

- **The findings** (two smaller: M2 and another). The pinned client sends temperature 0 on every call
  (`client.py:498`); thinking under greedy decoding is prone to repetitive loops that run to the cap, and R1's cut rate
  on drawn records is bounded only by S3 on 16 generated calls. "Many questions unanswered" left the truncation
  reading to be chosen after looking, and "What it does not show" did not exclude other sampling settings.
- **Now:** trap 23, "Greedy decoding", names the setting and points to rule 14's first attempts cut per kind. "How it
  is read": if more than 0.10 of R1's 300 questions are unanswered after the repair, the Runs entry states that R1 is
  truncation-bound and labels `R1_minus_R0` and `R1m_minus_R0m` "not a reading of the lever at this cap"; the verdict
  words are unchanged. "What it does not show" adds "not a-4b's reasoning at any sampling setting other than
  temperature 0".
- **What it changes:** a threshold pre-registered for a reading; no verdict changes.

### E25. The check's endpoint never retries ("The timing check", the calls and S1; the build plan)

- **The finding** (smaller, M5): the check's endpoint retries were not fixed; a 5xx retry after a long generation
  doubles a call's time and blurs what S1's "HTTP 200" means.
- **Now:** the check's endpoint has `max_retries` 0, a constant of `lab/jr.py`; a non-200 call fails S1. The judging
  run's endpoints keep 1 retry (rule 6).
- **What it changes:** one setting of the check.

### E26. The build plan's gaps (the build plan, build 1's wiring and "New modules")

- **The finding** (smaller, M6): build 1 listed only `build_units`' `jr` branch for `lab/plan.py`, though the
  workflow's `terms` step, keyed on the check's term output, needs `plan_outputs` and `OUTPUT_KEYS` to carry the
  check's term run and artifact; `harness_measurement` and `harness_verdict` for `jr` were not listed; `lab.j5`'s code
  hash was not declared.
- **Now:** build 1 adds `plan_outputs` and `OUTPUT_KEYS` (the check's term outputs) with `tests/lab/test_lab_plan.py`,
  and `harness_measurement` and `harness_verdict` for `jr` (`lab/units.py:760, 773`). `lab.j5.CODE_FILES` is A003's
  `lab.j4.CODE_FILES` plus `lab/jr.py`, `lab/j5.py` and `lab/j5labels.py`; `lab/warmup.py` and `lab/models.json` are
  outside every code hash, and the manifest's and the lock's sha256 are in each shard's provenance.
- **What it changes:** the build plan.

### E27. The new key's cache entry ("What reasoning on is", item 1; "What it costs")

- **The finding** (smaller, M8): the new key gets its own cache entry, `lab-gguf-a-4b-r-<sha16>` (`manifest.cache_key`;
  `plan.provision_entries`, `lab/plan.py:325-340`), a second copy of the same 2,497,280,256-byte file counted against
  the repository's 10 GB of caches (`docs/lab/README.md:70-71`).
- **Now:** item 1 says so: the five model files of the lock would hold 8,148,584,608 bytes (derived from
  `lab/models.lock.json`'s sizes) beside the server archive and every other cache; an entry evicted or unused for 7
  days is downloaded again by the provision job; a download that fails is rule 17's infrastructure stop.
- **What it changes:** a fact stated.

### E28. The check's warm-up carries over to every R1 shard ("What reasoning on is", item 7)

- **The finding** (smaller, M10): S1's probe result predicts every R1 shard's start only if the check's and the
  judging run's warm-ups use the same generated payload, which the rule did not say.
- **Now:** item 7 says both take `_longest_record(pack, 5)` (`lab/warmup.py:120-123`) and `judge_example` with the
  pack's first sorted predicate (`:131-145`), under branch T and the confirmation case with T's list for it, so the
  check's probe carries over, CPU nondeterminism aside (trap 11).
- **What it changes:** a statement.

### E29. The serial order is accepted ("The build plan", "The order")

- **The finding** (smaller, M11): the check runs the branch's base task, so it waits for A003's result and the branch
  amendment; running it on task D with branch T's payload shape under every branch would let it run earlier and
  bound branch N's timings conservatively.
- **Now:** "The order" says the serial order is accepted: a check on another task would measure prompts no judging
  call of branch N sends (another instruction text) and could not show that branch's own answers parse (S2, S7). The
  finding allowed this answer.
- **What it changes:** a statement.

### E30. The plan job's time ("The data and the draw", item 10; Runs)

- **The finding** (smaller, M12): the j5 plan job adds R4 and a fifth artifact download; no measured figure was cited
  against the `prereg` step's 20 minutes (`.github/workflows/mycelic-lab.yml:77`) and the plan job's 30 (`:28`).
- **Now:** item 10 cites A002's plan job (17:33:30 to 17:34:58 UTC, plan job 115442458382, lab run 38439612256,
  CHOICE-A002 Run 1) and term run 1's `prereg` step (61.2 s, CHOICE-A003 T9), expects a few minutes, says it is not
  measured, and the Runs entry records the plan job's time.
- **What it changes:** a figure sourced.

### E31. Three citations (the check's calls; case H)

- **The finding** (smaller, M13): the 6,000-character cap is the pack's `max_input_chars`
  (`docs/collective/replay/vehicles/pack/vocabulary.json:295`), not `lab/warmup.py:126-128`; `NEGATION_INSTRUCTIONS`
  starts at `lab/j2.py:82` (line 80 is `NEGATION_TASK`); `negation_task` (`:134-137`) carries the instructions and 256
  tokens, but the schema is `judge_schema`, `verify.py:147-150`.
- **Now:** corrected in "The timing check" and case H.
- **What it changes:** citations.

### E32. The reasoning is generated from the payload and the weights ("The metric, pinned", the data-class bullet; rule 3; trap 1)

- **The finding** (smaller): "the reasoning is generated from the payload and can hold nothing the payload does not"
  contradicted trap 5: the model's weights are also an input, and they may hold these public complaints, possibly with
  their filed components.
- **Now:** "The reasoning is generated from the payload and the model's weights; no arm puts codes in the payload;
  what the weights hold of these public complaints is trap 5's caveat, and a reasoning pass may draw on it more than a
  direct answer does." Rule 3 and trap 1 say the same.
- **What it changes:** wording.

### E33. Build 1 merges after A003's result run has ended ("The build plan", "The order")

- **The finding** (smaller): build 1 changes `lab/models.json`, `lab/models.lock.json` and `lab/warmup.py` and needs
  only `j4` merged first, so it could land on the deployment branch before A003's judging request is pushed, and
  A003's run would use a manifest, lock and warm-up its reviews never saw (CHOICE-A003 rule 7).
- **Now:** build 1 merges only after A003's result run has ended (its aggregate job finished), so that no A003 run,
  first or re-pushed, runs on a commit that holds build 1; this is stricter than the finding's "after A003's request
  is pushed", which would have left a re-push after a stop before any model on build 1's commit. It costs nothing on
  the critical path: the check waits for A003's result anyway.
- **What it changes:** an order.

### E34. The runs' requests beyond NHTSA and the artifacts, named (the declaration, hosted-model bullet; the guards)

- **The finding** (smaller): every gguf provision job queries the model hub's API and, on a cache miss, downloads the
  file (`lab/provision.py`, module docstring); the rule expected the new key's first download.
- **Now:** the bullet names the hub query on every run and the weight download on a cache miss, and says that no
  inference request leaves the runner (the model server listens on loopback). The build plan's guards say the same.
- **What it changes:** wording.

### E35. Two asks stated as they stand ("Decisions this does not make")

- **The findings** (smaller): the founder's confirmation of the 86 reading, open since 2026-10-11, was not listed; and
  "A003's ask stands, unanswered" cited an ask (CHOICE-A003, "Decisions this does not make") that does not appear under
  the status log's "Open asks".
- **Now:** a bullet says the founder has not confirmed that 86 means section 2, that this rule runs section 2 by
  default as LOOP-48H section 7 sets it, and that it will not switch after a result. The lexical-judge bullet says
  A003's ask is conditional on A003's drafted lexical judge reaching 0.860 on its draw, has not been posted, and is
  A003's to post under its own rule; this rule posts nothing.
- **What it changes:** two statements corrected.

### E36. No repository or file name copied ("What reasoning on is", item 1; build 1's wiring)

- **The finding** (smaller): the build copied a-4b's `gguf` block into a new manifest entry, a lock entry and a
  `docs/lab/MODELS.md` row "with a-4b's cells", writing the repository and file name again, which this loop's
  instructions forbid in documents and commits.
- **Now:** the `docs/lab/MODELS.md` row says "the same file as a-4b" and copies no cell; the manifest and lock entries
  are written by a test-pinned helper that copies a-4b's blocks; no commit message, BUILD note or console line carries
  the repository or file name.
- **What it changes:** the build plan.

### E37. "Isolate the lever", up to runner differences ("The base task and the arms", the arms paragraph)

- **The finding** (smaller): R0 and R1 run on different runners whose CPU models vary (trap 11).
- **Now:** "The four arms isolate the lever, up to runner differences (trap 11)".
- **What it changes:** wording.

### E38. What the check prints ("The timing check", "What it prints"; the tests; Runs)

- **The finding** (smaller): "Never a reply and never reasoning" did not exclude terms or narratives, which the
  check's payloads carry under branch T and the confirmation case.
- **Now:** "Never a reply, never reasoning, never a term, never a narrative, never a payload"; the tests pin it.
- **What it changes:** wording, and one more pinned refusal.

### Declined

No finding's fix would change anything after A003's result is recorded: no A003 result exists, so none is declined
for that reason. These clauses are declined or taken in another form, each for the reason given.

- **"From this rule's commit"** (the blocking finding on rule 17, one clause). Taken from this amendment's commit
  instead (E1): the same reviews' other findings require this amendment to change the cells read, P under a call, the
  planning figures and the check, and it is written before any A003 result or check figure exists.
- **The per-call budget clause on every call** (the review's B2, its first fix: the time at the cap, the call's own
  time to the first token plus 2,047 times its decode time, at most 736 s and 780 s on every call). The 780-s clause is
  adopted for calls 13 to 16 (S4c) and every call's latency (S4d). The budget clause is declined in form: on calls 13
  to 16, whose prompts are longer than any judging call's, it would stop on a design that fits, the fault the review
  found in the old S4; on calls 1 to 12 the call's own first token (a 629-character user turn) understates a judging
  call's. S4a (the rate on the long calls) and S4b (the decode against the design's tolerance, with the planning first
  tokens) carry the review's principle, stopping where the design stops fitting (E8). Its other fixes (decode from the
  call itself when it has 256 output tokens, else the call with the most; the planning figures reported, not stopping;
  the sentence on the check's CPU) are adopted.
- **A first-token planning figure of about 150 s for every call** (the blocking finding on S4's first-token clause, one
  clause). The 150 s is that of the check's 6,000-character calls, which S4a bounds by rate; judging calls carry real
  narratives, recorded up to 2,824 characters. Sizing every judging call at 150 s with the review's slower decode of
  0.30 s per token would not fit (20 × 764.4 + 1,873.2 = 17,161.2 s against 16,650 s under branch N, derived) and would
  force a smaller cap for a stack of worst cases no record shows. The planning figures are 60 and 100 s (E6), and the
  residual for 6,000-character narratives is stated (E7). The finding's other clauses (S4's first-token clause on calls
  1 to 12, a rate bound on calls 13 to 16 derived from Run 1 and the 7763's ratio, the 60 s for real narratives under
  branch N only, the worst case redone, the check at `job_minutes` 330 and `minutes` 300) are adopted.
- **Scaling S4's bounds by a CPU ratio** (the smaller finding on S4 and one runner, its first option). Declined for its
  second option, which the finding offered: S4 bounds only the check's CPU model, stated plainly, with the check's CPU
  printed beside every recorded figure for it, and the other runners covered by this file's comparison with the
  slowest recorded figures (E8). A ratio of whole-call medians applied to a clause's bound would itself be a rough
  derived figure stopping the lever.
- **A latency bound of 572 s** (the smaller finding on S5, its S6). 572 s was the old planning worst case, which E6
  replaced; the bound is 780 s, the deadline less 120 s (S4d), above the corrected worst case of every call (at most
  about 757 s, call 13's under branch T).
- **A cap of 64 tokens through `Runtime.single` for the cut call** (M1, call 17 as proposed). Taken as call 19, raw, at
  16 tokens: raw, because `Runtime.single` returns neither the content nor `reasoning_content`, and S7 must see the
  content's length and the reasoning's presence, as the other finding on items 3 and 6 asks; 16, because no more than
  the control's median answer, a reply that counts its reasoning cannot finish inside it whatever the reasoning's
  length, while 64 could finish after a short reasoning. M1's non-streamed calls on the judging client are calls 17 and
  18; its budget arithmetic is redone in E7.
- **A stop on any hit in the server log, with 64-character runs** (M4, two clauses; and the reading of the other
  finding's "must be 0" as a stop). S6 counts and does not stop: a hit says the release logs reasoning, and the fixed
  consequence, the reasoning key's logs left out of the judging run's artifacts, closes that route without ending a
  lever whose measurement is sound. The runs are 40 characters, as the other finding proposed: a shorter run finds more.
- **Running the check on task D under every branch** (M11, its option). Declined, and the serial order accepted (E29),
  for the reason given there.
- **Posting A003's ask** (the smaller finding on "Decisions this does not make", its second option). A003's ask is
  conditional on A003's own result and is A003's to post; this file changes only `docs/collective/A004/`, and the
  finding's first option, saying it has not been posted, is adopted (E35).

### Floors after this amendment

| Floor | Before this amendment (`e6b9a45`) | After | Direction |
|---|---|---|---|
| The target and the metric | balanced accuracy 0.860 against the filed-category key, record level, codes hidden, 150 fresh NHTSA complaints (300 questions), scored as J001 | unchanged | unchanged |
| The verdict rule | LOOP-48H section 2.5 verbatim; the words by branch through `lab.j2` or A003's `lab.j4` | unchanged | unchanged |
| The pass test and the comparators | section 2.4's three under branch N and case H; A003's five with its added clauses under branch T and case C | unchanged | unchanged |
| The interval rule | cluster bootstrap over records, B 10,000, 95%, seed `j1:5`, paired on the records both sides score; ties fail | unchanged; a printed lower end of 0.000 is not above 0 (E20) | unchanged |
| The confirmation rule | a first pass is provisional; success only for (a-4b, `negation_terms`) by confirmation in case C; the whole judging run waits on the check | the same; in cases C and H the judging run runs whatever the check shows, with R0 and R0m alone if it fails (E3) | raised |
| Completeness | per key's set: 30 of 30 parts, `measurement` true, dropped share at most 0.01; A003's sets read from two cells that could disagree | per key's set unchanged; A003's sets complete exactly when their rule-13 word is not `incomplete` (E4) | unchanged in kind; one reading |
| The primary pair | per branch and case; a decision-9 call "may" make a mapped reading eligible | the same pairs; a call installs exactly the pair E2 names under branches N and T and nothing in the cases; a mapped A003 arm never primary (E2) | discretion removed |
| The adaptation rule | four cases in order on named cells | the same cases and order on the rule-13 words (E5), the completeness word (E4), the printed lift (E20) and the first aggregate (E21) | discretion removed |
| Amendments after looking | any setting changed by a reviewed amendment before the judging run | from this amendment, the branch amendment fills placeholders 1 to 7, the check's amendment 8 to 13, the decision-9 amendment E2's content only; any other change is a new choice file (E1) | raised |
| The draw and the exclusion | seed 5, n 150, 30 parts; four cycles and A003's split removals excluded by eleven sources and the stop rules | unchanged; A003 by K4 alone excludes its records and split removals together (E22) | unchanged |
| The cap and the deadline | 2,048 on the reasoning arms, 256 on the controls; 900 s, 1 retry | unchanged (E7); the check's endpoint 0 retries (E25) | unchanged |
| The planning figures and the design | 0.25 s per token, 60 s to the first token, 30 minutes before the warm-up; 23 calls at the cap in about 191 of 254 minutes | 0.30 s per token; 60 s (branch N, case H) or 100 s (branch T, case C) to a judging call's first token, from 15 ms per character; 35 minutes before the warm-up; the same 23 calls fit with about 21 or 6 minutes to spare (E6, E7) | more conservative |
| The timing check | 16 streamed calls; S1 to S5, S4 at the planning figures on every call; `minutes` 240, `job_minutes` 270 | 20 calls (16 streamed, 2 non-streamed on the judging client, 1 cut, 1 for the log); stop clauses S1 to S5 and S7; S4 at the design's tolerances with a rate bound and latency bounds; S5 with three parts; S6 counted with a fixed consequence; `minutes` 300, `job_minutes` 330 (E7, E8, E10, E11, E12, E25) | raised: more clauses, and a pass now implies the re-sized design fits on the check's runner |
| The judging path | `Runtime.run` with its one repair | unchanged; cut first attempts whose content is valid counted apart, scored as answered (E11) | unchanged |
| Runs on code their reviews saw | build 1 after `j4` is merged | build 1 merged only after A003's result run has ended (E33) | raised |
| The comparison count | 4 this cycle, 29 since the loop began, `comparisons_before` 25 | the same; 2 and 27 under rule 18's R0-only request (E3); the declared sum governs (E23) | unchanged |
| What is stored and printed | no reasoning by any lab code; the server's log kept; the check prints no reply and no reasoning | the same; the log's readers named; S6 counts reasoning in the check's log, and a count above 0 leaves the reasoning key's logs out of the judging run's artifacts (E12); the check prints no term, narrative or payload either (E38) | raised |

### Placeholders to be filled by later amendments before the run

None is filled by this amendment. The list, as "Placeholders to be filled by dated amendments before the run" above
now holds it (E1):

From A003's result run, by the amendment that reads the branch (before the timing check is pushed), each labelled
"from A003's result run"; that amendment may do nothing else (rule 17):

1. The branch, with the cells it was read from (the rule-13 verdict words of D and B, the printed `D_minus_B` row),
   quoted as CHOICE-A003's Runs entry prints them; which aggregate's cells they are; whether A003's plan job's
   comparator figures were read.
2. The base task, P's arm and the headline secondary (the request's `base_task` and `primary`).
3. A003's result run's id and attempt, its plan artifact's name, id and `expires_at`.
4. A003's labels, questions, chain file (with its line count) and complaint file prefixes.
5. A003's two split counts (by record id and by text).
6. The comparison count as A003's aggregate printed it (25 expected; the declared sum governs), and this cycle's (29,
   or 27 under rule 18's R0-only request).
7. The control task's recorded timing on A003's draw (task D's under branch T and the confirmation case, task B's
   under branch N and the hypothesis case): per-call latency with the runner CPU model and output tokens.

From the timing check, by the amendment after it (before build 2); that amendment may do nothing else:

8. The check's lab run id, its request's sha256 and the commits of build 1.
9. Each clause's outcome (S1 to S5 and S7) with its counts; the warm-up's probe booleans and token counts.
10. The measured figures (output tokens, latency, time to the first token, decode time per token, the first token per
    character on calls 13 to 16; call 19's content length); the runner CPU model beside every recorded a-4b figure
    for it; the reasoning key's provision digest beside a-4b's `7485fe6f11af…`.
11. The planning figures against the measured ones, and the margin recomputed (reported, not used).
12. S6's count, and whether the reasoning key's server logs are therefore left out of the judging run's artifacts.
13. Whether the check passed, and so which judging request is pushed (E3).

Conditional, and not a placeholder of this rule's figures: a chief-scientist call on decision 9 that counts unclear
as no, received before the judging request is pushed, is adopted by a dated amendment with no other content and
installs exactly the pair E2 names; none is recorded under "Decisions received" in the status log at `831f424`.
