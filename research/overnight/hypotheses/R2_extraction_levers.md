# R2: extraction / ingest-side levers for end-to-end strict accuracy (LongMemEval Oracle 50 q)

Scope: raise strict 54 / lenient 80 without lowering turn@15 (93.5). Read-only research; nothing here is implemented.
Sources read: `prompts.rs`, `extraction.rs` (`build_plan`, `ground`, `normalise`, `reconcile`, `apply_decision`), `worker.rs`
(`WorkerConfig`), `retrieval.rs` (`context_with_results`), `temporal.rs`, `tests/golden*`, `ankara/.context/exp/RESULTS.md`,
`acc_lme.json` (per-question answers), `lme_oracle_assistant_v2.json` (context previews), LongMemEval source turns, and the
two baseline DBs the overnight run had finished (`raleigh/.context/overnight/db/lme_base/cc6d1ec1.db`, copied to /tmp, audit log read).

## 1. What the 23 strict failures look like (my reading, not a rerun)

| bucket | n | questions | reachable from extraction side? |
|---|---:|---|---|
| judge false negative (answer is right) | 2 | 29f2956b, db467c8c | no (judge) |
| reader date/unit math on facts that ARE in the block | 5 | cc6d1ec1, c9f37c46, 1192316e, 7024f17c, 9aaed6a3 | partly: absolute dates, weekday, no relative words |
| a specific is missing or stale in the stored memory | 9 | 2ebe6c90 (start date), 08f4fc43 (St. Mary's mass on "January 2nd"), aae3761f (3rd trip), 0a995998 (3rd item), gpt4_372c3eed (UCLA span), 0f05491a (120 vs 125), gpt4_7f6b06db, gpt4_d6585ce9, a2f3aa27 (merged "approximately 1300") | yes |
| never extracted or not retrieved (turn@15 miss) | 3 | 41275add (0 memories for the whole history), 7161e7e2 (assistant table), 09ba9854 (taxi cost) | yes |
| preference questions the reader refuses | 3 | 1a1907b4, 1da05512, 1c0ddc50 | marginal |
| wrong evidence used | 1 | 0bc8ad93 | no |

Upper bound for extraction-side work is about 12 questions (24 strict points); a realistic haul is 3 to 6 questions (6 to 12 points).
Three facts found while reading the code and DBs matter for ranking:

1. **turn@15 hides stale values.** In `reconcile()` a candidate with cosine >= `dedupe_cosine` (0.94) to an existing memory of the
   same subject is dropped and only its source link is added (`plan.duplicate_sources`). "needs 120 stars" vs "needs 125 stars"
   embeds at far above 0.94 with nomic, so the newer value can vanish while turn@15 still counts a hit (0f05491a: both gold turns
   `has_answer`, the block shows 125). Unverified until the audit log of that DB is read (diagnostic 0b below).
2. **`ground()` rejects hyphenated proper nouns.** `CAP_NAME_RE` matches "Red" in "Red-headed", but `window_lower` is built with
   `norm_entity`, which keeps `-`, so `" red "` is never found. In `cc6d1ec1.db` 8 of 33 candidates (24%) were rejected `unknown_name`:
   Red-headed (x5), Perky-Pet (x2), Xeno-canto. 180 of 1086 messages in the 50 histories contain a Capitalised-hyphen token.
3. **The date shown for date math is weak.** The line is `- [2023-05-18, fact] text (from chat answer_353d3c6d_1, 2023-05-25)`.
   No weekday: in 9aaed6a3 the 9B called 2023-05-18 "a Tuesday" (it was a Thursday) and answered IDK. The trailing date is the
   conversation date but is unlabeled, and the chat id costs about 8 tokens per line. Memories without `when` carry only that
   trailing date. `event_time` for "started X about 3 months ago" was the message date (c9f37c46: `[2023-05-27, event] ...3 months ago`).
   `temporal.rs` annotates "N days ago", "N years ago", "a few weeks ago", "two weeks ago", "a week ago", **not "N weeks/months ago"**.

## 2. Ranked list

Gain = strict questions out of 50 (2 points each), my estimate with wide error. "Subset" = the 18-question set in section 4.
"Flag" = ship as a default-off config field so no golden fixture changes until the lever wins.

| # | lever | kind | est. gain | types helped | re-ingest? | subset first? | golden tests touched |
|--:|---|---|---|---|---|---|---|
| 1 | Source snippet under each memory in the context block (g) | retrieval | +3..6 | multi, temporal, KU, SSA | no | yes, on existing DBs | retrieval/mcp/rest json if default changes; flag = none |
| 2 | Weekday + labelled dates in context lines (h) | retrieval | +1..3 | temporal, multi | no | yes, existing DBs | same as #1 |
| 3 | Sentence-level verbatim fallback, triggered by uncovered numbers (b, narrowed) | ingest | +2..4 | multi, temporal, KU | yes | yes | none with flag |
| 4 | Relative-time elimination: "N months ago" resolver + rule 3 wording (h) | ingest | +1..3 | temporal | yes | yes | prompts + ingest + extraction json if promoted |
| 5 | Hyphenated-name grounding fix in `ground()` | ingest | +1..2 | SSA, multi | yes | yes | likely none |
| 6 | Numeric guard on DUPLICATE and tighter UPDATE (f) | ingest | +1..2 | KU, multi | yes | yes | extraction json (run to see); RECONCILE_PROMPT if edited |
| 7 | "Copy specifics verbatim" prompt rule (a) | ingest | +0..2 | all | yes | yes | prompts/ingest/extraction json if promoted |
| 8 | Assistant list/table/URL rules + zero-memory retry (e) | ingest | +1..2 | SSA | yes | yes | prompts/ingest/extraction json if promoted |
| 9 | batch_size 1 (needs a subject fix) and context_messages (c) | ingest | -1..+2 | all | yes | yes | `tests/worker.rs` pins 1 and 6 explicitly; ingest json sets batch size per variant |
| 10 | Bigger extractor, qwen/qwen3.8-27b (i) | ingest | 0..+4 (probe) | all | yes | yes, ~1.5-2 h | none |
| 11 | LLM "gleaning" second pass when numbers are uncovered | ingest | +1..3 | multi, temporal | yes | yes | none with flag (extra prompt text, new const) |
| 12 | Derived facts at extraction time (d) | ingest | 0..+1 | multi | yes | low priority | none with flag |

Verdicts on the named candidates: (a) keep but low; (b) as drafted (one raw memory per message) **no**, see #3; (c) test, do not
expect much; (d) no as an LLM step, only deterministic span annotation; (e) yes, #8 plus #5; (f) yes, #6; (g) yes, #1; (h) yes, #2 and #4; (i) a probe.

## 3. Detail per lever

### 1. Source snippet in the block (g). Cheapest, widest reach
- Mechanism: 93.5% of questions have a memory citing the answer turn, but the memory sentence often lost the specific (1 and 3 above).
  The raw user sentence is still in the `messages` table and linked by `memory_sources`. Showing it recovers numbers, hedges, list items.
- Change, `retrieval.rs::context_with_results`. Today:
  `opts.with_sources = false;` and
  `let line = format!("- [{when}{}] {} (from chat {}, {})", m.kind, m.text, m.chat_id, py_prefix(&m.observed_at, 10));`
  New: `opts.with_sources = true`; collect `m.source_message_ids` of the first `context_snippets` (default 0, try 5) lines, one
  `self.store.get_messages_by_ids(&ids)` call, pick the sentence of the first HUMAN source message with the highest token overlap with
  `m.text` (a table or a 2 000-char assistant turn must be reduced to the best sentence or row, <= 220 chars), skip when overlap > 0.9
  (snippet adds nothing), show each message once, and append `\n    said: "<snippet>"` under the line.
- Budget: user turns average 237 chars (median 226); assistant turns 2 079. Current block averages 2 149 chars (537 tokens), max 3 447,
  cap `context_max_chars` 3600. 5 snippets x <= 220 chars is about +1 100 chars (+275 tokens), so raise the cap to about 5 000 when on.
  Answer latency 4.6 s -> about 6 s.
- Risk: more tokens for a 9B reader can raise distraction (cashback: the assistant's `$0.47` calculation memory already distracts).
  Cap at 5 snippets and test against the 4 control questions.
- Test: no ingest. Re-run `chat-memory-longmemeval`-style retrieval + the answer eval on the existing DBs. ~10 min for 50 q.
  Existing 50 DBs are no longer in /tmp; the overnight `lme_base` set (same ids) is the natural target once complete.

### 2. Weekday and labelled dates (h). Retrieval-side, a few lines
- Change, same function. `when` becomes `2023-05-18 Thu, ` for day-precision `event_time` (parse the first 10 chars with chrono, format `%a`);
  tail becomes `(said 2023-05-25 Thu)`; drop `from chat <id>` when ids look auto-generated, or shorten to `chat <id>`. Optional: sort the
  included lines by `event_time`/`observed_at` when the query has an ordering cue ("first", "before", "order", "how many days"),
  since "most relevant first" hides chronology (gpt4_7f6b06db).
- Effect: removes weekday errors (9aaed6a3), makes the observation date readable as "when this was said" (needed for "last Thursday",
  "two months ago"). Saves about 100 tokens per block if the chat id goes.
- Tests: `tests/golden/retrieval.json` (7 context blocks), `mcp.json` (5), `rest.json` (1) compare blocks byte for byte; hand-written
  asserts in `tests/retrieval.rs:238-243`, `review_fixes.rs:527` (tight 260-char budget, still fits) and `mcp.rs:340` only use `contains`.
  Gate behind `RetrievalConfig.context_weekday: bool` (default false) and fixtures stay green.

### 3. Sentence-level verbatim fallback with a numeric-coverage trigger (replaces (b))
- Why not one raw memory per message: user turns are cheap (60 tokens) but would take slots from distilled memories at k=15; assistant
  turns (mean 520 tokens, 58 are over the 3 000-char clip) cannot fit; embeddings of whole turns are noisy for the vector channel; it
  roughly doubles stored rows (1 086 messages -> about 2 200). At importance 0.2 the multiplier `(1-0.35)+0.35*0.2 = 0.72` ranks them
  below distilled twins (0.9 for importance 0.7), but they still enter the fused top-k whenever BM25 hits a rare number or name.
- Mechanism instead: after `parse_memories`, for each HUMAN message split into sentences; take tokens matching
  `\$?\d[\d,.:/]*` (plus URLs); if a token's digits appear in no candidate text of the batch, store that one sentence as
  `"<Speaker> said: \"<sentence, <= 240 chars>\""`, kind `fact`, importance 0.35, confidence 0.6, source = that message, at most 2 per
  message. No extra LLM call. 132 of 543 user turns contain a digit (24%); expect +60..100 rows over 1 111 (+5..9%), about +40 tokens
  for each fallback line that reaches the block.
- Change: new `fn numeric_fallbacks(&self, worthy: &[ChatMessage], cands: &[CandidateMemory]) -> Vec<CandidateMemory>` in `extraction.rs`,
  called in `build_plan` between `let candidates = self.parse_memories(&mem_out, &worthy);` and `self.normalise(...)`, behind
  `ExtractionConfig.numeric_fallback: bool` (default false). Output goes through the normal normalise/reconcile path (dedupe still applies).
- Catches: 08f4fc43 ("January 2nd"), 9aaed6a3 / gpt4_2ba83207 style dollar amounts, hours/minutes. Does not catch 2ebe6c90
  ("started ... today" has no digit) or table cells (#8).
- Test: 18-question subset, flag on vs off.

### 4. Relative-time elimination (h)
- Mechanism: the 9B copies "about three months" and "one month ago" into memory text with the same anchor, then fails to subtract
  (cc6d1ec1, c9f37c46: both answered 3, gold 2). If the text carries absolute dates the subtraction is trivial. The workshop memory
  in the fresh baseline already reads "on 2023-04-25"; the duration memory has no anchor.
- Changes:
  1. `temporal.rs::resolve_relative_dates`: add `N months ago` (digits or words, plus "a month ago") -> `"[= February 2023]"`, via
     `NaiveDate::checked_sub_months`; same in `parse_when` path (it already calls `resolve_relative_dates`). Add weeks only after
     checking `tests/golden/temporal.json`: it has 44 "weeks ago" strings; "months ago" has none, so months is safe.
  2. `prompts.rs`, rule 3, append after "...not tied to a date.": `Never leave "ago", "last week", "recently" or "for N months" in the text: write the absolute date, e.g. "has watched stand-up since about 2023-02" for "about 3 months" said on 2023-05-27. A duration that began before the message gets "when" = its start.`
  3. `extraction.rs::parse_memories` currently strips `[= ...]` with `ANNOTATION_RE`; keep stripping, rule 3 does the work.
- Scale: only 11 user messages in the 50 histories say "N weeks/months ago", so the ceiling is small but they are the temporal questions.
- Prompt promotion needs fixture regeneration (section 5). Test first with the `memory_prompt_extra` suffix (section 5).

### 5. Hyphen grounding fix
- `extraction.rs::ground`: `let window_lower = format!(" {} ", norm_entity(&window_text));` and the filter
  `!known_names.contains(&n) && !window_lower.contains(&format!(" {n} "))`. Add `let window_flat = window_lower.replace('-', " ");`
  and `&& !window_flat.contains(&format!(" {n} "))`; also split `known_names` entries on `-`. Same pattern in `parse_relations` (`grounded`).
- Effect: restores brand/product memories (SSA recommendations, Perky-Pet, Xeno-canto). Pure recall gain, no new LLM cost, cannot add
  hallucinated names that are absent from the window. Add a unit test in `tests/extraction.rs`.
- Tests: `extraction.json` `hallucinated_name_in_entities` should stay green (no hyphens); run and see.

### 6. Numeric guard on DUPLICATE, tighter UPDATE
- Mechanism: see fact 1. Also `apply_decision` UPDATE replaces the memory text with the LLM merge: a2f3aa27 is the visible case
  ("approximately 1300 followers..., prefers #hashtags..., plans Instagram Live", kind flipped to preference, no "now", hedge added).
- Changes in `reconcile()`:
  `Some(s) if s >= cfg.dedupe_cosine => { ... "DUPLICATE" ...}` becomes
  `Some(s) if s >= cfg.dedupe_cosine && same_numbers(&c.text, &top[0].1.text) => ...`; the next arm (`s >= cfg.reconcile_cosine`) then sends
  differing numbers to the LLM (ADD/UPDATE/CONTRADICT). Intra-batch check
  `accepted.iter().any(|a| cos(...) >= cfg.dedupe_cosine)`: keep `(embedding, text)` in `accepted` and apply the same test.
  In `ask_reconcile`, downgrade a DUPLICATE whose numbers differ to UPDATE. `same_numbers` = sorted multiset of `\d+(?:[.,:]\d+)*`.
- `prompts.rs::RECONCILE_PROMPT` UPDATE line: `- UPDATE: NEW refines, extends or corrects the same fact as one existing memory...` ->
  `...the SAME attribute (same quantity, date, status or name) as one existing memory; "text" = one sentence with the NEW value, keeping an old detail only if it is still true. A different attribute of the same topic is ADD, never folded into the sentence.`
- Worker maintenance also merges at `merge_cosine` 0.96 (`worker.rs::maintain`, `store.supersede`) with no numeric check; the benchmark harness
  sets `maintenance_interval = 0`, so it does not affect these numbers, but production does. Apply the same guard there.
- Diagnostic 0b (free, before coding): on each finished DB run
  `select detail from audit_log where kind='duplicate'` and compare the logged text with the surviving memory; count pairs whose digits differ.

### 7. "Copy specifics verbatim" rule (a)
- Evidence of rounding/paraphrase is thin: the observed losses are omissions, plus one added hedge ("approximately"). Expect small gain.
- Change: new rule after rule 9 in `MEMORY_PROMPT`:
  `10. Copy numbers, quantities, durations, prices, dates, times, percentages, URLs and proper nouns EXACTLY as written ("30 minutes" stays "30 minutes", "$75" stays "$75"). Never round, convert, or add "about"/"approximately"; keep a hedge only if the speaker used it.`
- Rule 9 currently reads `Keep every specific detail about a participant as its own memory rather than merging several facts into one sentence.`
  The 9B emits about 2 memories per call whatever the rule says (RESULTS.md), so wording alone is unlikely to move recall; pair with #3.

### 8. Assistant-turn rules (e)
- Facts: 2 of 6 SSA questions fail. 41275add: 2 messages, 1 batch, **0 memories** (a 4-item list of videos with URLs; audit log not kept). 7161e7e2:
  a 7x4 markdown table, 4 memories for 14 messages. 58 of ~543 assistant turns exceed `max_assistant_message_chars` (3 000) and lose their tail.
- Diagnostic 0a: re-run only 41275add (5 s) and print `audit_log`: was it `[]` from the model, or rejected (`unknown_name`, `ungrounded`)?
- Changes: (1) `ASSISTANT_RULE_ON` add: `For a list, ranking or table in an (assistant) message write ONE memory per item or per table row, copying every title, name, number and URL verbatim ("The user was shown the Sunday shift rotation: 8 am-4 pm Admon, 12 pm-8 pm Magdy, ..."). The last item counts as much as the first.`
  (2) `build_plan`: if a batch with an assistant message of >= 3 list markers (`^\d+\.|^[-*] |\|`) yields zero candidates, retry once with the
  repair suffix. (3) `ExtractionConfig::max_assistant_message_chars` 3000 -> 6000 (prompt grows by up to 750 tokens on 10% of calls).
  (4) Optional deterministic table-to-sentences converter for `|`-tables, stored as fallback memories (no LLM).
- Clutter note: `cc6d1ec1` has 12 of 24 memories as "The assistant recommended ..." (the rule asks for the user as subject; the 9B
  ignores it). Per memory notes, prune with a sweep if it shows up in retrieval, do not gate.

### 9. batch_size and context_messages (c)
| setting | evidence | expectation | cost |
|---|---|---|---|
| batch 6 -> 2 | LoCoMo evidence 62.7 -> 77.6, 2.5x time | shipped | baseline (LME 6 493 s summed, 1.0 memory per message) |
| batch 1 | untested; model emits about 2 memories per call, now 1.0 per message | maybe 1.5-2x memories, more specifics | about 1.6-2x extraction time; subset about 55-65 min |
| batch 3 | trend says worse | fewer memories per message | faster |
| context_messages 6 -> 10 | untested | little: only resolves references | longer prompts |
- **Blocking bug for batch 1** (code reading, not run): for an assistant-only batch `humans` is empty, `subject_speaker` stays the
  assistant, `resolve_str("The user", Some("assistant"))` returns `"assistant"` (FIRST_PERSON hits before the registry), and `ignored("assistant")`
  rejects every such memory as `assistant_subject`. Fix: in `normalise`, when the primary source is an assistant turn and the batch has no human,
  take the speaker of the last human message in `context`. Without it batch 1 silently drops all assistant-sourced memories.
- Harness: `--batch-size 1` exists; `context_messages` has no flag (hard-coded in `cfg.extraction`).

### 10. Bigger extractor (i)
- `chat-memory-longmemeval --model qwen/qwen3.8-27b` already works (flag exists; check `LLM_NO_THINK_PREFILL=1` still applies to that template).
- Cost estimate (not measured): dense 27B is about 2.5-3x slower than 9B per token, so subset about 80-95 min, full run about 4.5-5.5 h;
  RAM for 27B plus the 9B answerer plus nomic embedder at once (a 4-bit 27B is roughly 16 GB, check the machine). LM Studio serialises generations,
  so there is no parallel speedup. Qwen3.6-35B-A3B (MoE, 3B active) would run near 9B speed and was loaded for the 2026-10-22 answerer run; try it too.
- Hypothesis: a bigger model follows "one memory per list item / copy numbers" better, which is the failure here. Counter-evidence:
  the 35B-A3B answerer scored 48 vs 54 (reader task, different). Run on the subset only; it tells whether prompt work can substitute for model size.

### 11. LLM gleaning when numbers are uncovered
- Same trigger as #3; instead of a verbatim sentence make one extra call `GLEAN_PROMPT` ("These specifics from the messages are not in any memory: 30 minutes,
  $75. Write one memory for each") with the uncovered tokens as the hint. About +1 call on the ~25% of batches that trigger, so +15-25% time.
  Better phrased memories than #3 at higher cost; compare against #3 on the subset. New const in `prompts.rs`; `prompts_match_python` enumerates
  existing consts, so a new one breaks nothing. The scripted responder returns `"{}"` for unknown prompts, but `ingest.json` records the prompt count,
  so keep it behind a flag.

### 12. Derived facts (d)
- Cross-session sums cannot be computed at ingest (each session is its own chat; the prompt only sees 12 known memories). A 9B computing in-message
  arithmetic bakes errors into permanent memories (the compute-prompt reader run converted IDK into wrong sums). Only deterministic annotations are safe:
  "from 2010 to 2014" -> append "(4 years)"; "30 minutes" -> "(0.5 hours)". Gain is at most gpt4_372c3eed / 7024f17c, and only if the other spans exist.
  `ground()` would pass them (overlap check needs >= 20% of tokens in the window).

## 4. Test plan: stratified 18-question subset (about 32 min of summed extraction vs 108 min full)

| type | ids | baseline strict |
|---|---|---|
| temporal-reasoning (4) | cc6d1ec1, c9f37c46, 2ebe6c90, 08f4fc43 | 0/4 |
| multi-session (4) | 9aaed6a3, 0a995998, aae3761f, 09ba9854 | 0/4 |
| knowledge-update (3) | 0f05491a, a2f3aa27, dad224aa (control) | 1/3 |
| single-session-assistant (4) | 41275add, 7161e7e2, 778164c6 (control), 65240037 (control) | 2/4 |
| single-session-user (1) / preference (2) | 37d43f65 (control) / 1da05512, 1a1907b4 | 1/3 |

Baseline on this set: strict 4/18 (22%), turn@15 15/18, 319 memories. It is deliberately failure-heavy (14 failures + 4 controls), so it measures lever
potential and regressions, **not** the unbiased gain: extrapolate to 50 q only through the failure buckets in section 1.
Gates to promote a lever: (1) no control flips to wrong, (2) turn@15 on the subset >= 15/18 and `ceiling_turn` not lower, (3) memories per message
<= 1.5x baseline, (4) mean context <= 800 tokens, (5) the winner then runs the full 50 q once. Judge noise: ~2-5 of 20 strict misses are judge false
negatives, so also report a hand-corrected or numeric-normalised strict and use the judge cache. `chat-memory-longmemeval` has no `--ids` flag; add one
(a 5-line filter after `stratified_sample`) or run `--types` per type and drop the rest. Retrieval-only levers (#1, #2) need no ingest at all.

Suggested order (cheapest information first): diagnostics 0a/0b (minutes) -> #1+#2 on existing DBs (minutes) -> #5 and #6 together on the subset (35 min,
both deterministic) -> #3 and #4 (35 min each) -> #7+#8 prompt suffix (35 min) -> #9 and #10 only if recall is still the bottleneck.

## 5. Golden fixtures and how to experiment without breaking them

Prompts are pinned three ways: `golden.rs::prompts_match_python` (each const in `prompts.json`), `ingest_matches_python` (3 variants, 20 recorded
MEMORY_PROMPTs in `ingest.json`, compared byte for byte), `extraction_scenarios_match_python` (17 scenarios in `extraction.json`: about 23 MEMORY prompts and 29 reconcile
mentions, counts include script keys; RELATION prompts are recorded too). `fake_llm.json` stores prompts only as inputs; the scripted responder keys on the prefixes `You maintain the long-term memory`,
`You are building a knowledge graph`, `You maintain a memory store` and on the `NEW MESSAGES (date: ..):\n<n>. [who]: text\n\nReturn ONLY JSON` layout, so
edits inside rules (not the prefix, not that layout) leave its responses unchanged. `generate.py` was removed with the Python; update fixtures by exact string
replacement of the old rule text (it has no `{placeholders}`, so rendered and template forms are identical apart from `{{`/`}}`) in `prompts.json`, `ingest.json`,
`extraction.json`, then run the three tests.

Zero-churn experiment path, recommended for every prompt lever (#4 text, #7, #8):
- `ExtractionConfig.memory_prompt_extra: String` (default `""`), appended in `build_plan` at `) + &repair;` -> `) + &cfg.memory_prompt_extra + &repair;`
  (the repair suffix already proves the 9B obeys trailing instructions). Promote into `MEMORY_PROMPT` and regenerate fixtures only for winners.
- New config fields need no test edits: tests build configs with `..Default::default()`; `golden.rs::extraction_config` only overrides three known keys.

| change | breaks (if default changes) | fix |
|---|---|---|
| MEMORY_PROMPT / ASSISTANT_RULE_ON text | `prompts_match_python`, `ingest_matches_python`, `extraction_scenarios_match_python` | string-replace in 3 json files |
| RECONCILE_PROMPT text | `prompts_match_python`, `extraction_scenarios_match_python` (29 prompts) | string-replace |
| `numeric_fallback`, `memory_prompt_extra`, `context_snippets`, `context_weekday` (default off) | nothing | none |
| context line format | `retrieval_matches_python`, `mcp_matches_python`, `rest_matches_python`; `tests/retrieval.rs`, `review_fixes.rs:527` use `contains`/length only | regenerate blocks |
| `dedupe_cosine`/`same_numbers` guard | possibly `extraction.json` duplicate/update scenarios (fake hash embeddings), `review_fixes.rs:241,269` | run; adjust only if digits differ in fixtures |
| `ground()` hyphen | none expected | add unit test |
| `temporal.rs` months pattern | none (`temporal.json` has no "months ago") | add cases |
| `WorkerConfig::batch_size` default | `tests/worker.rs:94,129` set it explicitly; `ingest.json` variants set it explicitly | none |
| `max_assistant_message_chars` default | `extraction.json` only if a fixture message exceeds 3 000 chars | run |

## 6. Things that are NOT worth doing (with reasons)
- Raw whole-turn memories (b as drafted): slot competition at k=15, assistant turns blow the 3 600-char cap, embeddings of long turns are noisy.
- LLM-computed derived facts across sessions: impossible at ingest and sticky when wrong.
- Raising `max_memories_per_batch` further: RESULTS.md shows the model emits about 2 per call regardless.
- Reader-side fixes (compute prompt, bigger answerer): outside this scope and already measured as neutral.
