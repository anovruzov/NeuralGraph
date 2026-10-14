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
- **No call with reasoning on has ever run in this lab.** a-4b has been served with `--reasoning off` since the lab's
  G2 commit `4945460` (2026-10-07), before the lab's first run (`git log -- lab/models.json`); every a-4b figure above
  and below is a reasoning-off figure.

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
  reads the answers as answered, and the mapped reading is the headline secondary. A call received before the judging
  request is pushed is adopted by a dated amendment before any run; one received after the run bears on the next cycle.
- The founder ask A002's B6 posted on section 2.3's stop rule stands; this rule rests on the same reading (one
  verifying source per cycle suffices; a verified artifact's refs are the records that cycle judged) for all four
  earlier cycles.
- No hosted model is named or used. The founder has not supplied a key (`docs/handoff/LOOP-48H-STATUS.md`, "Open
  asks", 2026-10-11), and standing rule 2 forbids any other model-host egress. Both runs of this cycle make no egress
  but the complaint archive's download from NHTSA (the judging run only) and the lab's own artifact downloads.
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
  from them in the payload, the task text or a reply mapping; the reasoning is generated from the payload and can hold
  nothing the payload does not (trap 1).
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

1. **The setting: one server flag, on a manifest key of its own.** The lab's model server takes its arguments from
   the shard's manifest entry and from nowhere else: `lab/shard.py:353` passes the manifest's `server.args` and the
   entry's `server_args` as the server's extra arguments, and `lab/server.py:131-135` (`server_argv`) appends them to
   the code-owned ones. `lab/shard.py` is pinned, and a shard serves one manifest key (`lab/plan.py:279-307`, shards
   packed per model key). So "reasoning on" is a manifest entry: a new key **`a-4b-r`** in `lab/models.json`, kind
   `gguf`, alias `lab-a-4b-r`, whose `gguf` block (repository, file, revision, licence) is a-4b's byte for byte, whose
   `response_format` (`json_schema`), `transport_schema` (`full`) and context sizes (`ctx_per_slot` 16,384,
   `e3_ctx_per_slot` 4,096, `e2_ctx_per_slot` 32,768) are a-4b's, and whose `server_args` are `["--reasoning", "on"]`
   where a-4b's are `["--reasoning", "off"]`. The key names a server setting of the same model, not another model:
   the model of this cycle is a-4b alone. `lab/models.lock.json` gains, under the new key, a copy of a-4b's lock entry
   (its commit, its sha256 `7485fe6f11af…` and its size, 2,497,280,256 bytes, as `lab/models.lock.json` holds them), so
   the provision step verifies the new key's file against a-4b's digest and never trusts it on first use
   (`lab/manifest.py:57-60`). A provision that verifies any other digest for the new key fails before any model runs.
   a-4b's entry and lock entry are not changed. `--reasoning on` is the form the lever names, and it is allowlisted
   (`lab/manifest.py:121, 126`). What the pinned server release does with it is the release's behaviour; this rule
   does not read it from the release's source, and the timing check ("The timing check") observes it on a runner
   before any drawn record is judged.
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
4. **The cap: 2,048 tokens for the reasoning plus the answer, on the reasoning arms only.** This rule reads the
   server as counting every generated token, reasoning included, against the request's `max_tokens`; the timing
   check's clause S5 verifies it on a runner. The reasoning arms' task is the base task with `max_tokens` 2,048 and
   nothing else changed (the same name, data class, instructions and schema), built as
   `dataclasses.replace(base, max_tokens=2048)`. The prompt does not carry `max_tokens` (`tasks.py:78-81`,
   `render_messages`: the system text, the instructions, the schema and the data block), so a reasoning arm's prompt
   is the control arm's byte for byte, and the two requests differ in `max_tokens` alone, sent to servers that differ
   in one flag. The control arms keep the base task unchanged, with its cap of 256 (`JUDGE_MAX_TOKENS`,
   `verify.py:87`; `lab/j2.py:134`, `negation_task`). With reasoning off, a-4b's negation replies took a median of
   16.0 output tokens and a 95th percentile of 17.0, and none of 300 was cut at its cap (CHOICE-A002 Run 1), so the
   control's cap does not bind. That the larger cap alone, with reasoning off, would change no answer is an inference
   from those figures, not a measurement (the control's cut count and output tokens are reported, rule 14). Why 2,048:
   "Why the units are this size" fixes the planning figures and shows that 2,048 is the largest cap of the form
   1,024 × k whose worst case fits a unit's budget with 30 parts; 3,072 does not. The warm-up's fit check (the worst
   prompt plus `max_tokens` within the 16,384-token slot, `lab/warmup.py:371-377`) has room at 2,048.
5. **The deadline: 900 s per HTTP try and one retry, for both keys.** j1's endpoint gives each HTTP try 600 s
   (`lab/j1.py:125`, `DEADLINE_S`) and one retry (`:126`, `MAX_RETRIES`), which the client spends on a 429, a 5xx or a
   network failure and never on a timeout (`client.py`'s module docstring). A timeout is a transport failure: the
   record leaves the set, and more than `WITHHOLD_SHARE` 0.01 of a set's records leaving (two of 150) withholds the set
   (`lab/j1.py:128`); two consecutive timeouts stop the unit (`SERVER_DOWN_KINDS` and `BREAKER_AFTER` 2,
   `mycelic/collective/edge/extract.py:85-86`). A long reasoning must therefore end as a reply cut at the cap (item 6),
   counted against the arm, and never as a timeout. At the planning figures a call at the cap takes at most 572 s
   ("Why the units are this size"), so the endpoints of this cycle give each try 900 s (the routing document allows
   up to 3,600 s, `mycelic/collective/inference/routing.py:262`). The control's calls are unaffected: their 95th
   percentile was at most 16.4 s on any runner (A001 run 1, 16,427.0 ms).
6. **A reply cut at the cap: unanswered, counted and reported; no lab-side repair; the runtime's one repair kept.**
   With the reasoning separated, a generation cut at 2,048 tokens returns `finish_reason` `length` and a content that
   is empty or holds a partial object; `extract_object` finds no object, and the attempt is `json_invalid`
   (`runtime.py:340` and below). The judging path is `Runtime.run` (section 2.3 pins it for every lab-side variant),
   and `Runtime.run` answers an invalid first attempt with one repair on the same endpoint, at the same cap, with the
   note that the reply was cut at the token limit (`runtime.py:175-180`; `tasks.py:97-98`). That repair is part of the
   pinned path and applies to every arm of every cycle alike; a lab that skipped it for one arm would judge the two
   arms by two paths, and a lab that replaced `Runtime.run` would leave section 2.3's path. So this rule keeps it, and
   reads a cut as follows. **Unanswered**: when the repair is cut or invalid too, `Runtime.run` raises an
   `InferenceError` of a validation kind, which the J runners count as a model failure (`e1_extract.failure_class`,
   `mycelic/collective/experiments/e1_extract.py:799-803`): the question's verdict is `unknown`, scored and never
   correct, never dropped as a transport failure. **Counted and reported**, per arm and per kind, from the ledgers:
   the first attempts cut at the cap, the repairs made, the repairs cut, the repairs that gave a valid reply, and the
   questions left unanswered; and, as a count and never as an arm, how many of the verdicts a repair after a cut
   produced were correct (rule 14). **Never repaired by the lab**: no lab code re-asks a question, raises the cap,
   retries a cut, reads the reasoning for an answer or replaces a cut reply with anything; the reasoning text is
   never available to it (item 8).
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
   unaffected, and `BUILD-A004.md` says so.
8. **The reasoning text is never stored, printed or written.** It can quote and paraphrase the narrative at length.
   It appears in no console line, step summary, report, document, commit, ledger, verdict line, run file or shard
   artifact. This holds by construction on the judging path: the pinned client never reads `reasoning_content` from a
   non-streamed reply (item 3), so the ledger rows (which hold no content of any kind, `runtime.py:298-310`), the
   verdict lines (which hold the parsed two-answer object) and the run files cannot carry it; the timing check's
   streamed calls count reasoning deltas without keeping them; the warm-up keeps a boolean. **Not stored in the shard
   artifact, as the replies are**: the replies are two enum answers, public and needed to recompute every verdict;
   the reasoning is free text that no reading of this rule uses, storing it would need a raw HTTP call beside
   `Runtime.run` (a second path that section 2.3 does not name), and a stored reasoning would invite readings made
   after the result (trap 15). The cost is that no one can audit what the reasoning did; the lifts, the answer counts
   and the yes rates of rule 14 are the only readings. The server's own log stays in the shard artifact, capped at
   1 MiB (`lab/server.py:66`), as in every earlier cycle, and is printed nowhere; the build verifies that no console
   line, step summary or report block carries it or its tail.
9. **Why a key and not a per-arm request setting.** The routing document can send `chat_template_kwargs` with every
   request (`routing.py:17-18, 287-296`; `client.py:506-507`), and some server templates read a thinking switch from
   them. Declined: the lever names the server flag; what the pinned release does with a template switch on a server
   started with `--reasoning off` is recorded nowhere in this repository; and the control would then be a-4b on a
   server set differently from every earlier cycle's, so it would replicate nothing. A key keeps the control exactly
   the configuration A001 to A003 measured.
10. **The lab change, in one list.** The manifest key and its lock entry (item 1); the warm-up's rule for an entry
    with `--reasoning on` (item 7); the endpoints' 900-s deadline in the new kinds (item 5); the timing check's kind
    `jr`; the judging kind `j5`. No pinned file changes (`lab/shard.py`, `lab/j1.py` to `lab/j3labels.py`, `lab/jt.py`,
    anything under `mycelic/`). "The build plan" lists every file.

## The base task and the arms: the pre-registered adaptation rule

This section answers the brief's item (2). It is written now with every branch complete. The branch is read by a
dated amendment, before the judging build starts, from CHOICE-A003's Runs entry for A003's result run, from the
named cells below, quoted as the entry prints them. A003's result run is the first A003 judging run in which a model
judged a drawn record (LOOP-48H section 6.3: a stop before any model call is not a result and the same request is
re-pushed; CHOICE-A003 rule 17: when a plan job printed and the run then failed before any model judged, the re-run's
draw is the result). A run stopped before any model ran is never a trigger: while A003 has no result run, the judging
build of this cycle does not start and the timing check is not pushed (it runs the branch's base task). If A003 were
abandoned without a result run by a recorded decision, this cycle would need a new choice file, since its four-entry
exclusion presumes an A003 draw a model judged.

**The cells read** (CHOICE-A003, "## Runs", the judging run's entry; its rule 5 names the four arms D
`negation_terms`, Dr `negation_terms_mapped`, B `negation` and Br `negation_mapped`): in the arms table, the rows of D
and B, columns "Verdict word", "Parts finished" and "Records left out"; in the lifts table, the row `D_minus_B` with
its 95% interval. A set is complete as CHOICE-A003 rule 13 reads it: every part of the set finished for that task,
`measurement` true, `records_not_judged` 0, the dropped share at most 0.01; in the arms table, "Parts finished" 5 of
5 and "Records left out" 0. Ties fail every strict clause (A002's B17). The four cases are tried in this order, and
the first whose condition holds is the branch:

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
   primary"; B reads the answers as answered, so the standing default allows it as primary. Then the base task is
   A001's negation task N, `judge_record_negation` (`lab/j2.py:80-93`, `NEGATION_INSTRUCTIONS`; `:134-137`, the
   shipped schema and 256 output tokens), imported unchanged; **P = (a-4b, `negation`)**, the control arm R0 read as
   answered, which is A003's B exactly; R1 is the headline secondary. A pass of P here is a first pass of P as a
   primary, provisional.
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
Br's under branch N and the hypothesis case). A call on decision 9 received before the judging request is pushed may
make a mapped reading eligible, by a dated amendment.

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
are readings of the same replies. The four arms isolate the lever: R1 and R0 differ in the server flag and in a cap
that does not bind with reasoning off (item 4 above), on the same records, questions and prompts.

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
- **The calls.** 16 calls of R1's TaskSpec (the branch's base task with `max_tokens` 2,048) through
  `Runtime.single(..., stream=True)` (`runtime.py:191`: one attempt, no repair, streamed so that the client records
  the time to the first token and counts reasoning deltas, `client.py:216-219`), at boundary `site:lab`, data label
  `synthetic`, HTTP deadline 900 s. The payloads come from the vehicle pack's generated records (`generate(pack, 5, 1,
  weeks)`, as `lab/warmup.py:120-123` takes them), ordered as `_longest_record` orders them (longest narrative first,
  ties by `record_ref`), each built as `lab.warmup.judge_example` builds a payload, asked about the record's first
  entity, with the codes emptied as `lab.j1.payload` empties them (`lab/j1.py:278-282`): call k, for k from 1 to 12,
  is the k-th record with its own narrative, asked about the pack's k-th sorted predicate; calls 13 to 16 are records
  1 to 4 with their narratives repeated to the 6,000-character cap (`_worst_text`, `lab/warmup.py:126-128`), asked
  about the pack's 13th to 16th sorted predicates (`unknown_or_other`, never asked, is skipped in the count; the hand
  pack has 26 specific predicates, CHOICE-A003 "The terms", item 6). Under branch T and the confirmation case each
  payload carries `predicate_terms` for its asked predicate, and calls 13 to 16 carry the longest list in T by
  characters instead (A003's C12). No drawn record and no complaint narrative is sent.
- **Measured per call**, from the ledger and the attempt: HTTP status, `finish_reason`, output tokens, latency, time
  to the first token, the count of reasoning deltas, and whether the content parsed into a reply valid under the
  schema. The unit stores the parsed two-answer objects (synthetic) and nothing else of a reply.
- **The stop rule.** The judging request of this rule is pushed only if every clause holds:
  - **S1, the flag works and the reasoning is separated:** each of the 16 calls answered with HTTP 200 and streamed
    at least one reasoning delta; and the check shard's warm-up passed its probe (item 7 of the previous section).
  - **S2, the answer is read from the content:** every call that ended with `finish_reason` `stop` returned content
    that parsed into a reply valid under the task's schema.
  - **S3, the cap leaves room:** at most 2 of the 16 calls ended with `finish_reason` `length`.
  - **S4, the planning figures hold:** on every call the time to the first token is at most 60 s, and on every call
    with at least 32 output tokens the decode time per output token, (latency − time to the first token) / (output
    tokens − 1), is at most 0.25 s.
  - **S5, the cap binds the reasoning:** no call reports more than 2,048 output tokens.
  If any clause fails, or the unit stops before its 16th call, the judging run of this rule is not pushed: the check
  is recorded in Runs with its figures, and lever 6 needs a new choice file, whose settings may use the check's
  figures, since they come from generated records and never from a draw. A check stopped before any call for an
  infrastructure reason (a provision, a cache or a runner failure) may be pushed again unchanged once, as LOOP-48H
  section 6.3 allows; a warm-up stop for missing reasoning is S1 failing, not infrastructure. A passing check is not
  repeated for better figures, and a failing one is not repeated to pass.
- **What it prints**: the counts of each clause and the quantiles of each measured figure (median, 95th percentile
  and maximum of the output tokens, the latency, the time to the first token and the decode time per token), the
  runner CPU model, the reasoning key's provision digest beside a-4b's lock digest, and each clause's outcome. Never a
  reply and never reasoning.
- **Its budget**: "Why the units are this size". Its figures fill the placeholders of "Placeholders" by a dated
  amendment before the judging request is pushed.

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
     (A003's C20); the by-chain reading is printed per cycle with the chain it used.
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
     filed label; the reasoning is generated from the payload alone. Under branch T `predicate_terms` holds words of
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
   the confirmation case, P = (a-4b, `negation_terms`); the hypothesis case, P = (a-4b, `negation`). The model of
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
    cycle. The request carries `comparisons_before` 25; the Runs entry prints the count. No multiplicity correction is
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
    key. **For the cap**, per key and per kind, from the ledgers: first attempts cut at the cap, repairs made, repairs
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
    - one unit per key and part: **60 units**, 30 on `a-4b` and 30 on `a-4b-r`; a unit judges its part's 10 questions
      once, with its key's one task;
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
    reading, the arms, the seeds, the parts) is a new choice file once the judging run has started, and a numbered,
    reviewed amendment before it. After the run, no reading other than the four arms and the reports of rule 14 is
    made of the replies.
18. **The timing check comes first.** The judging request is pushed only after the timing check has passed every
    clause of its stop rule ("The timing check") and a dated amendment has recorded its figures.

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
| Primary pair | branch N: (a-4b, `negation_reasoning`); branch T: (a-4b, `negation_terms_reasoning`); case C: (a-4b, `negation_terms`); case H: (a-4b, `negation`); placeholder until the amendment |
| Comparators | branch N and case H: section 2.4's three; branch T and case C: A003's five, with A003's added clauses |
| Bootstrap | over records, B 10,000, `bootstrap_seed` 5 (`j1:5`), 95% percentile; paired differences with the same draws |
| Endpoint | the lab's pinned model server on loopback at `site:lab`, data label `public`; HTTP deadline 900 s, 1 retry; `Runtime.run` with its one repair; no escalation |
| Units | 60 (2 keys × 30 parts), 10 calls each before repairs, 300 minutes, one per shard; `job_minutes` 330; `max_parallel` 16 |
| Withhold | `WITHHOLD_SHARE` 0.01 per key's set |
| Timing check | kind `jr`, one unit of 16 calls on `a-4b-r`, generated payloads, stop rule S1 to S5; `minutes` 240, `job_minutes` 270 |
| Requests | the check: `lab/templates/reasoning-check-004.json`, copied to `lab/requests/reasoning-check-004.json`, kind `jr`; the judging run: `lab/templates/accuracy-004.json`, copied to `lab/requests/accuracy-004.json`, kind `j5`; `retention_days` 30 each; `provider` `llama-server`; `comparisons_before` 25 |

## Why these arms, and why this primary pair

**The yes on a negative is the failure to fix, and no cycle has let the model think before it answers.** Every
earlier lever changed what the model was told or how its answer was read; this one changes how much computation the
model spends before it answers, with the prompt unchanged byte for byte. The lexical judge refutes almost every
negative and confirms about a third of the positives (specificity 0.960 to 0.993, sensitivity 0.333 to 0.380, J001
and A001); a-4b confirms almost every positive and refutes at most about half of the negatives (A002 run 1). A model
that kept A002's best sensitivity, 0.907, would need a specificity of about 0.813 to reach 0.860 (derived here:
(0.907 + 0.813) / 2 = 0.860), against the 0.467 it reached.

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

This section answers the rest of the brief's item (3).

**What the lab has measured** (a-4b, reasoning off, every figure as its record prints it; one shared runner per shard;
compare only within a CPU model):

| Run and record | What | Figures |
|---|---|---|
| RESULTS Run 1, lab run 37834297419 | E3 latency, streamed, concurrency 1, AMD EPYC 9V74 | decode 7.0 tokens per second (extraction) and 9.0 (short), medians; first token 75.934 s and 35.597 s, medians |
| RESULTS Run 2, lab run 37841045975 | sim ledgers, median per call | judging 3,435 ms; extraction 4,274 ms |
| RESULTS Run 9, lab run 37996675559 | J001, shipped task, median per call | 6.0 s (Intel Xeon Platinum 8573C), 9.0 s (AMD EPYC 7763) |
| RESULTS Run 10 and CHOICE-L001 Run 2, lab run 38102412415 | L001, judge calls on MSHA records | alert: median 3.792 s, 95th percentile 5.306 s (AMD EPYC 7763); five questions: 3.518 s and 5.484 s |
| CHOICE-A001 Run 1, lab run 38114522160 | A001, shipped / negation task | medians 9,275.1 / 9,431.5 ms (AMD EPYC 7763), 8,211.6 / 8,637.7 ms (Intel Xeon Platinum 8370C); 95th percentiles 16,209.7 / 16,427.0 and 11,754.1 / 12,020.0 ms; longest shard 1,377.2 s |
| CHOICE-A002 Run 1, lab run 38439612256 | A002, evidence / rationale / negation task | medians 13,646.9 / 18,272.5 / 9,297.1 ms (AMD EPYC 7763), 9,466.4 / 14,474.4 / 6,645.3 ms (AMD EPYC 9V74), 8,381.1 / 16,777.5 / 4,909.1 ms (Intel Xeon Platinum 8573C); output tokens medians 38.0 / 87.0 / 16.0, 95th percentiles 143.0 / 115.1 / 17.0, none cut; longest shard 2,735.4 s |

**Derived here from those figures** (rough: a difference of medians is not a median of differences, and the tasks'
prompts differ by a sentence): the marginal decode rate of a-4b, from the rationale and negation medians (71 more
output tokens at the median), is 7.9 tokens per second on the AMD EPYC 7763, 9.1 on the AMD EPYC 9V74 and 6.0 on the
Intel Xeon Platinum 8573C; from the evidence and negation medians (22 more), 5.1, 7.8 and 6.3. The slowest, 5.1
tokens per second, is 0.198 s per token.

**The loop prompt's estimate, labelled as such**: "about 1-2 min per call, 5-10 h per model for 300 calls". At the
derived rates of 5.1 to 9.1 tokens per second, one to two minutes of decoding is about 300 to 1,100 tokens of
reasoning and answer (derived here). The estimate is plausible and unmeasured; the timing check measures the first
reasoning-on calls.

**The planning figures, declared now, and why.** **0.25 s per output token** (4.0 tokens per second): below every
recorded and derived figure, with a quarter's room over the slowest derived one (0.198 s) for decoding that slows as
the context grows, which this lab has not measured. **60 s for the prompt and the first token**: the slowest recorded
95th percentile of a whole negation call is 16.4 s (A001 run 1), which includes its decoded answer; 60 s leaves room
for task D's longer payload under branch T (at most about 3,200 characters of terms more, CHOICE-A003, "The terms",
item 8) and for a slower runner. **30 minutes before the warm-up** for the job's checkout, downloads, preparation and
the server's start: an allowance, not a measurement (the server's health deadline alone is 300 s,
`lab/server.py:60`). The timing check's clause S4 verifies the first two on a runner.

**The worst case by design, at the planning figures** (derived here):

- a reasoning call at the cap: 2,048 × 0.25 + 60 = 572 s, inside the 900-s deadline with 328 s to spare;
- a question: at most two attempts at the cap (the runtime's one repair): 1,144 s;
- a part of 10 questions: 11,440 s, about 191 minutes;
- the warm-up of an R1 shard: one call and two probe calls at the cap, 1,716 s, about 29 minutes;
- the unit's budget in the worst case: the smaller of 300 minutes and 315 − 30 − 29 − 2 minutes, less 30 s: about
  254 minutes, against about 191 needed, with about 63 minutes to spare;
- the design holds while the decode time per token stays at or below about 0.33 s (about 3.0 tokens per second):
  23 calls (20 in the part, 3 in the warm-up) at the cap, the 30-minute allowance and 150 s of margins must fit 315
  minutes; the deadline holds to about 0.41 s per token;
- at a cap of 3,072 the same arithmetic gives 828 s a call and 23 calls of 19,044 s against 16,950 s available, and
  828 s against the 900-s deadline leaves 72 s: it does not fit, which is why the cap is 2,048;
- a control call at its cap of 256: 124 s; a control part: at most 2,480 s, about 41 minutes.

So no shard can reach its job timeout by design: every unit's budget timer stops it first, and at the planning
figures the budget is not reached either. 30 parts is the most `lab.j1.build_questions` allows (`MAX_PARTS` 30,
`lab/j1.py:127`; `J1_MAX_PARTS`, `lab/request.py:283`), and it gives the smallest part.

**What it costs, expected** (derived here from the loop prompt's estimate and the measured control latency, not
measured for reasoning): R1, 300 calls at 1 to 2 minutes: 5 to 10 hours of model time, spread over 30 shards; R0, 300
calls at the negation medians of A002 run 1 (4.9 to 9.3 s): about 25 to 47 minutes. An R1 shard makes 13 calls (3 in
the warm-up, 10 in its part), about 13 to 26 minutes plus its preparation, which no record times. The 60 shards run
16 at a time, the 30 `a-4b` shards first (shards are numbered by key in sorted order, `lab/plan.py:279-307`): the
shards are expected to take about 1 to 2 hours in all (derived here: two waves of R1 shards of 13 to 26 minutes of
calls each, plus their preparation, after the short R0 shards). By design they cannot take more than about 13.4
hours (derived here: an R0 shard takes at most about 77 minutes at the planning figures, its 41-minute part, its
6-minute warm-up and the 30-minute allowance; an R1 shard at most its 325-minute job timeout; in matrix order with
16 at a time that is two waves of each, 2 × 77 + 2 × 325 = 804 minutes). The jobs: the plan job (30 minutes at most,
`.github/workflows/mycelic-lab.yml:28`), three provision jobs (the server and the two keys; the new key's first
download fills its cache, 2,497,280,256 bytes), 60 shard jobs and the aggregate job (30 minutes at most, `:309`). The
runners are GitHub-hosted; no figure in this repository prices them.

**The timing check's budget** (derived here): 16 calls at the cap, 9,152 s, about 153 minutes; its warm-up about 29
minutes; with `minutes` 240 and `job_minutes` 270 the shard's timeout is 265 minutes, its deadline 255 minutes after
the job starts, and the unit's budget in the worst case about 194 minutes, about 41 minutes over the need. At the
loop prompt's estimate the check takes about 30 to 60 minutes.

**What happens if a part does not finish.** A unit that stops at its budget, a shard whose server stays down
(two consecutive server-down failures stop the unit, `extract.BREAKER_AFTER`), or a shard job that fails leaves its
part's remaining questions unjudged: that key's set is incomplete, and so are both arms on it (rule 15). If P's set is
incomplete, the cycle is **incomplete** by section 2.5's completeness clause: it is recorded, and the next cycle needs
a new rule on a fresh draw. If only the other key's set is incomplete, P's verdict is read, and the lifts and the
other arms are not. A transport failure on a question leaves its record out of that key's set; more than 0.01 of a
set's records leaving withholds the set. A stop before any model judged (the plan job, a provision, a cache miss, a
lost runner) is not a result: the same request is pushed again unchanged once (rule 17).

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
  positives or on negatives (trap 7).
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

1. **The reasoning reading the codes.** Never: the payload's codes list is empty (`lab.j1.payload`; a label record
   that still holds codes is refused, `lab/j1.py:278-279`), the task texts name no code, and the reasoning is
   generated from the prompt alone, so it can hold nothing the prompt does not. The filed categories reach no arm by
   any path, as in J001 to A003.
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
6. **A cut reply read as an answer.** Never: a cut attempt is `json_invalid`, the runtime's one repair follows, and a
   question with no valid reply after it is `unknown`, scored. The reasoning of a cut attempt is never read, and its
   content (empty, or a partial object) yields no answer.
7. **The cap binding unevenly.** Long narratives, or questions whose answer is less obvious, may reason longer and be
   cut more; a cut costs a positive and a negative alike. The first attempts cut per kind and per predicate are
   reported (rule 14).
8. **The repair after a cut.** The runtime's repair note asks for a shorter object (`tasks.py:97-98`); the reasoning,
   not the object, was long, so a repaired attempt reasons again from the start and may be cut again. Its time is in
   the worst case of "Why the units are this size"; its effect is in the counts of rule 14.
9. **A warm-up cut skipping a shard.** Under today's rule a warm-up call cut at the token limit skips its task's units
   (`lab/warmup.py:407-408`); with 30 reasoning shards a chance cut on the generated record could leave a part
   unjudged and P incomplete. The reasoning key's warm-up records a cut and skips nothing (item 7 of "What reasoning
   on is"); the timing check's clause S3 bounds how often a cut happens on generated records.
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
16. **Tuning on the draw.** The cap, the deadline, the planning figures, the flag, the base rule, the reading, the
    seeds and the parts are fixed in this file before the draw exists. The timing check's figures come from generated
    records and can only stop this rule, never tune it. The plan job prints the comparators before any shard starts;
    those figures are never a reason to redraw.
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
- Not what the reasoning said: it is never stored.

## Decisions this does not make

- **Shipping reasoning on.** The product's site verifier serves its model through settings this cycle does not
  touch; switching its reasoning on would change its answers and its time to answer, a product decision for the
  founder and the chief scientist, made from this cycle's figures and L001's if at all.
- **Decision 9** (how "unclear" counts) is the chief scientist's; M stays secondary under the standing default, and a
  call received before the judging request is pushed is adopted by a dated amendment.
- **Whether a lexical judge may be a primary**: A003's ask stands, unanswered; no cycle counts a lexical judge's figure.
- **The R003 settings and pack-v2** are the chief scientist's; **decisions 1, 2 and 7** (Tesseract placement, routing,
  the embedder) are untouched; **the founder's alternative readings** of 86 (section 2.7) are not adopted; **the hosted
  central reader** needs the founder's secrets and a reviewed lab role.
- **Later cycles' questions**, left to their rules: a reasoning budget; a cap set from this cycle's measured reasoning
  lengths; the two smaller models with reasoning; reasoning under the shipped instruction; a larger local rung.

## The request

Two requests, each pushed alone after its code is merged (LOOP-48H section 6.4: the request push is the only thing
that starts a lab run), each only when no other lab run is in flight. Their content, fixed now (the exact key names
are the build's, recorded in `BUILD-A004.md`):

**The timing check**, `lab/templates/reasoning-check-004.json`, copied to `lab/requests/reasoning-check-004.json`:

- top level: `provider` `llama-server`; `models` `["a-4b-r"]`; `job_minutes` 270; `max_parallel` 1; `retention_days`
  30; a `purpose` naming this file and the copy's name and no other digit;
- `experiments.jr`: `minutes` 240; `pack` `docs/collective/replay/vehicles/pack`; `seed` 5; `base_task` `negation`
  or `negation_terms` (placeholder: by the adaptation rule); under branch T and the confirmation case `terms`, A003's
  term entry: `run_id` 38612800578, `artifact` `lab-plan-38612800578-1`, `file` `prereg/jt/terms.json`, `sha256`
  `42c591ccbb66b81455ca19840a54e18cbb69b5613dd2eab8f3f8c70f9cea39cc`, `vocabulary_hash`
  `c0260f0edab4638915b1fd0065e236ca20b497c0b217495c77adf018800fe579`, `cutoff` `2023-01-01`. The number of calls, the
  cap, the deadline, the planning figures and the stop clauses are code constants of `lab/jr.py`, not request
  fields, so that no request can change them. Pushed after A003's result run has ended, its Runs entry is recorded and
  the amendment that reads the branch is committed.

**The judging run**, `lab/templates/accuracy-004.json`, copied to `lab/requests/accuracy-004.json`:

- top level: `provider` `llama-server`; `models` `["a-4b", "a-4b-r"]`; `job_minutes` 330; `max_parallel` 16;
  `retention_days` 30; a `purpose` naming this file and the copy's name and no other digit;
- `experiments.j5`: `minutes` 300; `labels` `{source: nhtsa, pack: docs/collective/replay/vehicles/pack, n: 150,
  seed: 5}`; `parts` 30; `seed` 5; `bootstrap_b` 10000; `bootstrap_seed` 5; `base_task` (placeholder, as the check's);
  `primary` `{model: a-4b, arm: <the branch's P arm>}` (placeholder: `negation_reasoning`,
  `negation_terms_reasoning`, `negation_terms` or `negation`); `comparisons_before` 25;
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

`lab.request` validates every field: integers in range; `parts` exactly 30; the two models exactly `a-4b` with
`--reasoning off` and one key with `--reasoning on` whose manifest `gguf` block and lock entry equal a-4b's; artifact
names of the lab's shape; lower-case hex prefixes of 12 to 64 characters for the exclusions and exactly 64 for the
term entry; the files pinned per entry; rebuild seeds 1 to 4 in order; `base_task` one of the two; the primary arm
one of the branch's four with `model` `a-4b`, and P's arm consistent with `base_task` (`negation_reasoning` or
`negation` with `negation`; `negation_terms_reasoning` or `negation_terms` with `negation_terms`). `lab.plan
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
it and the kind `jr`; review; fix; merge. (2) The amendment that reads the branch from A003's result run, with A003's
exclusion facts. (3) Push `lab/requests/reasoning-check-004.json` alone, with no other lab run in flight: the timing
check. (4) The amendment that records the check's figures and its outcome. If the check failed, this rule ends here
(rule 18). (5) Build 2 on `wf/a004`: the judging kind `j5`; review; fix; merge. (6) Push
`lab/requests/accuracy-004.json` alone: the judging run.

- **Why new kinds, not options on `j4`.** `lab/j4.py`'s constants (its tasks, arms, lifts, line keys) are read by the
  request validator, the unit adapter, the warm-up and the aggregate, as `lab/j3.py`'s were (CHOICE-A003, "The build
  plan"), and A003's modules are in its code hash, so extending them would change what a re-aggregation of A003
  computes. A004 is a judging kind `j5` whose own constants hold its two tasks per branch, its four arms per branch,
  the arm-to-key map, its lifts and its line keys, and which imports unchanged from `lab.j2` (`negation_task`,
  `MAPPING`, `mapped_reply`, `pass_test`, `verdict_word`, `lift_reading`), from A003's `lab.j4` (task D, its payload,
  the drafted judges, `pass_test`, `verdict_word`), from `lab.j2labels`, `lab.j3labels` and A003's `lab.j4labels`
  and `lab.j4terms`; and a check kind `jr` that judges no drawn record.
- **New modules.** `lab/jr.py` (the check: the 16 generated payloads, the calls through `Runtime.single` streamed,
  the per-call figures, the clauses S1 to S5 as functions, the fixed console line of counts and quantiles, the
  constants shared with `lab/j5.py`: the cap 2,048, the deadline 900 s, the planning figures 0.25 s and 60 s);
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
  `lab/templates/reasoning-check-004.json`, `lab/templates/accuracy-004.json`; `docs/collective/A004/BUILD-A004.md`.
- **Build 1's wiring.** `lab/models.json` (the key `a-4b-r`, item 1 of "What reasoning on is"); `lab/models.lock.json`
  (a-4b's entry copied under the new key); `docs/lab/MODELS.md` (the key's row, with a-4b's cells and its
  `server_args`); `lab/warmup.py` (the rule for an entry with `--reasoning on`, item 7: the inverted probe, no skip on
  a cut, the probe's and the call's deadline from the task, the boolean and the token count; `warm_tasks` branches
  for `jr` and `j5` with the generated payloads, under branch T carrying T's lists as A003's C12 does; the docstring);
  `lab/notes.py` (the new warm-up sentence for missing reasoning, the check's notes; every sentence free of ASCII
  digits, `tests/lab/test_lab_guards.py`, `test_notes_hold_no_digits`); `lab/request.py` (`EXPERIMENTS` gains `jr`;
  `_jr`); `lab/plan.py` (`build_units`' `jr` branch); `lab/prereg.py` (`_jr`: under branch T the term check through
  A003's `lab.j4terms`, the payloads' term lists, the pins); `lab/units.py` (`ADAPTERS["jr"]`, `required_tasks`,
  `write_routing` with the 900-s deadline, `build_argv` and `run_unit`'s budget list, `harness_status`,
  `participation`, `unit_notes`, `public_text` giving a `jr` unit the synthetic note, `PREREG_EXPERIMENTS`);
  `lab/aggregate.py` (`jr_block`), `lab/summary.py` (the check's lines), `lab/dryrun.py`, `lab/__init__.py`;
  `.github/workflows/mycelic-lab.yml` (the `terms` step keyed also on the check's term output); `docs/lab/REFERENCE.md`,
  `docs/lab/README.md` (the troubleshooting row for the new warm-up sentence), `lab/requests/README.md`,
  `docs/lab/INTEGRATION.md`.
- **Build 2's wiring, every function a j5 kind touches** (A003's list, with the additions): `lab/request.py`
  (`EXPERIMENTS` gains `j5`; `_J5_KEYS`, `_J5_REQUIRED`; `_j5_exclude_list` with four entries; the two-key check of
  "The request"; `_primary` with the branch's arms); `lab/plan.py` (`build_units`' `j5` branch with each unit's key
  and the arm-to-key map; `exclude_entries` with four entries; `plan_outputs` and `OUTPUT_KEYS` gaining the fourth
  entry's run and artifact and j5's term outputs); `.github/workflows/mycelic-lab.yml` (the step `earlier-4` after
  `earlier-3`, of the same shape); `lab/prereg.py` (`_j5`: the eleven sources through `lab.j5labels`, the split under
  branch T, the draw, J001's questions with 30 parts, the comparators by branch, the chain file, the pins;
  `preregister`'s unit list and manifest key, `main()`'s `prereg:` line, `load_stand_in` with four entries);
  `lab/units.py` (`ADAPTERS["j5"]`; `required_tasks` and `write_routing` reading the unit's key and base task;
  `build_argv` and `run_unit`'s budget list gaining `j5`; `harness_status`, `participation`, `harness_measurement`,
  `harness_verdict`, `unit_notes`, `public_text`, `PREREG_EXPERIMENTS`, `REPLY_PARTICIPATION`, `run_unit`'s
  `with_pack` list); `lab/warmup.py` (the j5 branch registering each shard's one task); `lab/responder.py` (nothing
  under branch N: the fake answers by task name, and R1 shares R0's; under branch T, A003's handler); `lab/aggregate.py`
  (`j5_block`: the two sets, the four arms with the comparators, the words by branch, the lifts, the counts of rule
  14, `primary_result` with the headline secondary's word; the manifest cross-check); `lab/summary.py` (`J5_ARMS` per
  branch, `J5_LIFTS`; the plan summary's eleven sources and the report's sections); `lab/notes.py`; `lab/dryrun.py`
  (`--j5-data`, exclusive with the other data flags); `lab/__init__.py`; the four lab documents.
- **Tests, declared.** `tests/lab/test_lab_jr.py` pins: the 16 payloads (generated, codes empty, the worst-case four,
  the term lists under branch T); each clause S1 to S5 on synthetic figures, at and either side of each bound; the
  console line holding no reply and no reasoning; the warm-up's inverted probe and its no-skip on a cut for an entry
  with `--reasoning on`, and today's rules unchanged for a-4b (the existing `tests/lab/test_lab_server.py:319-322`
  test kept); the probe's deadline from the task; the manifest's new key equal to a-4b's in every field but the alias
  and `server_args`, and its lock entry equal to a-4b's (`tests/lab/test_lab_shipped.py:27, 49` gain the key and keep
  a-4b's pin). `tests/lab/test_lab_j5.py` pins: R1's TaskSpec equal to R0's in every field but `max_tokens`, under
  both bases; the prompts of R1 and R0 equal byte for byte; every `rule N` of this file naming the item whose bold
  title the test lists; the four arms per branch and the arm-to-key map; the adaptation rule's four cases on synthetic
  A003 cells, in order, ties failing; the words by branch through `lab.j2.verdict_word` and `lab.j4.verdict_word`
  unchanged; a cut first attempt followed by a valid repair, and by a cut repair, giving the counts of rule 14 and an
  `unknown` verdict for the second; the four layouts' checks, each pinned equal to the pinned function on the earlier
  layouts case for case; the eleven sources and the stop rules on synthetic artifacts (each cycle by own sources, by a
  chain with the others established, chains alone stopping, R2 to R4 `not attempted`, every consistency count
  stopping, K4 with A003's split removals); the draw over the eligible list less 600 refs and the removals, disjoint
  from four synthetic draws, the chain file of 750 lines plus the removals; `plan_outputs` on a j5 and every earlier
  plan; the template giving 60 units in 60 shards with 30 on each key; the runner through the fake server (one pass,
  the pins refused before any call, a stop mid-part); the small and the template dry runs with every number traced
  and no ref, term or reasoning anywhere. `tests/lab/test_lab_workflow.py`, `tests/lab/test_lab_plan.py`,
  `tests/lab/helpers.py`, `tests/lab/test_lab_docs.py`, `tests/lab/test_lab_sim.py` and
  `tests/lab/test_lab_request.py` change where they list the experiments, the templates, the output keys or the
  download steps.
- **The guards the build and this file obey.** No model-family name anywhere: `tests/mycelic/test_collective_guards.py`
  (`NoModelNamesTests`, `:734-761`, which scans `docs/collective` and the collective code) and
  `tests/lab/test_lab_guards.py` (which scans `lab/`, `tests/lab/` and `docs/lab/`); the models are a-0p5b, a-1p5b
  and a-4b, the new key names a server setting of a-4b, and the deployment branch is called that. No record ref, no
  term, no narrative and no reasoning in any report, summary, console line, commit or document. No ASCII digit in any
  `lab/notes.py` sentence. Lab Python imports only the standard library, `mycelic` and `lab`. The check makes no
  network call but the lab's artifact downloads; the judging run's plan job none but the archive's download and the
  five artifact downloads.
- **Before each merge** (section 2.3): `python -m pytest tests/lab -q -p no:cacheprovider` in the background, the
  onboard and guard suites, and the template dry runs: `python -m lab.dryrun --request
  lab/templates/reasoning-check-004.json --out OUT`, which must show one unit in one shard, every clause computed on
  the fake's figures and no reply or reasoning in any line; `python -m lab.dryrun --request
  lab/templates/accuracy-004.json --out OUT --j5-data DIR`, which must show 60 units in 60 shards, every one of the
  eleven sources verified on the stand-ins, the draw line with eligible after exclusion at least 150 and overlap 0,
  the comparators printed, every unit `ok` and `plumbing`, every word `incomplete`, the comparisons 29, and no ref,
  term or reasoning anywhere. In a dry run the fake answers R1 as it answers R0 and never reasons, so nothing in a dry
  run measures the lever; `BUILD-A004.md` says so.

## Placeholders to be filled by dated amendments before the run

From A003's result run, by the amendment that reads the branch (before the timing check is pushed):

1. The branch (confirmation case, hypothesis case, branch T or branch N), with the cells it was read from, quoted as
   CHOICE-A003's Runs entry prints them, and whether A003's plan job's comparator figures were read.
2. The base task, P's arm and the headline secondary, read from the branch (the request's `base_task` and `primary`).
3. A003's result run's id and attempt, its plan artifact's name, id and `expires_at` as the Actions API lists it.
4. A003's labels sha256 prefix, questions sha256 prefix, chain file prefix and line count, and complaint file prefix.
5. A003's two split counts (by record id and by text), as its labels record carries them.
6. The comparison count as A003's aggregate printed it (25 expected), and this cycle's (29 after it).

From the timing check, by the amendment after it (before build 2):

7. The check's lab run id, its request's sha256 and the commits of build 1.
8. Each clause's outcome (S1 to S5) with its counts, and the warm-up's probe booleans.
9. The measured figures: output tokens, latency, time to the first token and decode time per token (median, 95th
   percentile, maximum), the runner CPU model, and the reasoning key's provision digest beside a-4b's `7485fe6f11af…`.
10. Whether the planning figures held (S4), and the margin the measured maxima leave in the arithmetic of "Why the
    units are this size", recomputed with the measured figures in place of the planned ones (reported, not used to
    change any setting).

Conditional, and not a placeholder of this rule's figures: a chief-scientist call on decision 9 received before the
judging request is pushed is adopted by a dated amendment (the declaration).

## Runs

None yet. No code exists for this cycle, the timing check has not run, and no record of this cycle's draw exists.

**The timing check's entry** will hold: the lab run id, the request file and its sha256, the commits of the rule, its
amendments and the request; the plan job's line (under branch T, the term artifact verified); the shard's runner CPU
model, the server start and the warm-up record (the probe's two booleans and token counts, `repeat_identical`); the
check's fixed line (each clause's counts and outcome; the quantiles of the output tokens, the latency, the time to the
first token and the decode time per token); the reasoning key's provision digest beside a-4b's; the report's marker
line with its sha256 recomputed and matching; whether the check passed; and that no drawn record, no reply text and
no reasoning appear anywhere.

**The judging run's entry** will hold: the lab run id, the request file and its sha256, the commits of the rule, its
amendments and the request, and the branch in force with the A003 figures that fixed it; under branch T and the
confirmation case the request's push date beside the term artifact's `expires_at`; the plan job's fixed draw line
(`available`, the excluded count with A003's split removals, eligible after exclusion, overlap 0, the outcome of each
of the eleven sources with any by-chain reading and every consistency count, the labels', questions', prereg's and
chain file's prefixes with the chain's line count, the complaint file's prefix beside the earlier days'; under branch
T the split's and the term check's lines; on a re-run after a plan job printed, both plan jobs' lines); the
comparators on the fresh draw with intervals; the four arms' table (balanced accuracy with interval, sensitivity,
specificity, unknown share, the paired difference to each lexical judge of the branch, the bound on the same records,
the pass test, the verdict word, parts finished of 30 and records left out per key); the verdict on P in rule 13's
words, with the headline secondary's word beside it labelled as rule 5 says; the headline; the lifts with their
readings; the yes rates, the verdict-change table and the `unclear` shares; the cut and repair counts per key and
kind; R0 and R0m beside A003's matching arms and A001's and A002's negation arms as a between-draw difference; the
model-call latency per key with the runner CPU model, seconds per output token for R1, each shard's wall time; anything
that stopped before a model ran; the comparison count ("4 this cycle, 29 since the loop began"); and the public-text
sentence: on public text the model may have seen in training, scored against filed codes, which are not checked labels.
