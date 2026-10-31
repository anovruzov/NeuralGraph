# Overnight accuracy campaign — framework and protocol

Started 2026-10-28 01:47 CDT on this MacBook (Apple M5 Max, 18 cores, 48 GB, LM Studio on :1234).
Branch `PNurmanM/xr4y4` of the worktree `conductor/workspaces/neuralgraph-v1/raleigh`.
The orchestrator (Claude Fable 5.1, this session) runs until the user says **stop**.
A session cron (`*/20 * * * *`, job `7d9bb854`) re-enters the loop whenever the session is idle, so
a usage-limit pause or a crashed turn resumes from `STATE.md` as soon as the limit resets.

---

## 1. What "accuracy" means here, and the numbers we start from

The product is `crates/neuralgraph-memory` (the shipped assistant memory pipeline). Its accuracy is measured
end to end on **LongMemEval Oracle, 50-question stratified sample, seed 1** (the published Track D set in
`docs/BENCHMARKS.md`): ingest each question's chat history through the real extractor, build the shipped
context block, let a local model answer, grade with a local judge of another family.

| metric | what it measures | baseline (2026-10-22) | noise floor |
|---|---|---:|---|
| **strict** (Gemma-4-e4b strict judge) | every gold fact present, no hedging, IDK on answerable = wrong | **54.0** | 1 q = 2 pts; < 6 pts is noise at n=50 |
| **lenient** (Gemma-4-e4b lenient judge) | conveys the gold, extra detail ok | **80.0** | same |
| substring | every gold part is a substring of the answer | 44.0 | same |
| turn@15 (retrieval guard) | a memory in the context cites a has_answer turn | 93.5 | 1 q = 2.2 pts |
| LoCoMo conv0 e2e (secondary) | same pipeline on 66 LoCoMo questions, strict / lenient | 36.4 / 77.3 | 1 q = 1.5 pts |

**Primary objective: strict and lenient on LME-50, both up, retrieval turn@15 not down.**
Secondary: the same change must not lose on LoCoMo conv 0 e2e (different data, different style of questions).
Judge-robustness: a change that only helps under one judge prompt is a judge artifact (N5 lesson, 51-point spread);
proven changes are re-graded under a second judge model (`qwen/qwen3.6-35b-a3b`, strict prompt) before shipping.

Why these and not the research-track LoCoMo runner (`--bin runner`, 73.8 % single_hop): that engine is not what
ships, and one run costs 40 min to 3.5 h. The product harness answers 50 questions in ~4 min once databases exist.

## 2. Where the points are (from the baseline failure modes)

From the 2026-10-22 run (`ankara/.context/exp/acc_lme*.json`, details in `hypotheses/R1_failure_analysis.md`):

- retrieval delivered evidence on 93.5 %; given evidence, strict accuracy was 53.5 %
- 12 / 43 evidence-present questions were answered "I don't know" (needed arithmetic or unit conversion)
- ~5 of 20 strict misses are judge false negatives ("approximately 1300" vs "1300")
- the compute-style prompt fixed arithmetic (+2 strict) but lost 8 lenient (wrong sums instead of IDK)
- the 35B answerer did not help; so "bigger reader" is not the lever, "better instructions + better context" is

So the cheap levers are ordered: (1) reader prompt and context rendering, (2) retrieval parameters, (3) extraction.
Extraction changes cost a 1.5–2 h re-ingest each; they run last and only on a screened subset first.

## 3. Resources and hard constraints

| resource | fact | rule |
|---|---|---|
| GPU / unified memory | one M5 Max; LM Studio serialises generations per model | at most **2 GPU jobs** at once (one ingest + one reader eval); `scripts/gpu.sh` enforces slots via `.context/overnight/locks/` |
| models (LM Studio, JIT load, 60 min TTL) | `qwen3.8-9b-mlx` (extractor + answerer, 5 GB), `google/gemma-4-e4b` (judge, 7 GB), `qwen/qwen3.8-27b`, `qwen/qwen3.6-35b-a3b` (second judge / alt answerer), `text-embedding-nomic-embed-text-v1.5` | keep 9B + Gemma resident; load 27B/35B only for confirm stages |
| Qwen thinking | ignores `reasoning_effort`; only an empty `<think>` assistant prefill stops it | `answer_eval.py --prefill` default on; `--think` to allow reasoning deliberately |
| Gemma thinking | obeys `reasoning_effort: "none"` | always send it for the judge |
| ingest cost | LME-50: ~1,086 messages, 5.4 s/msg → ~1.5–2 h; LoCoMo conv0 s1–8: ~12 min | extraction variants screen on an 18-question stratified subset (`--n 18`, ~35 min) before any full run |
| reader eval cost | 50 answers + 100 judge calls ≈ 4 min with 4 threads | screen every reader idea on the full 50 |
| context dump cost | 50 queries, embedding only, < 1 min | retrieval variants are nearly free |
| Claude usage | this session has a usage limit | orchestrator reads summaries only; agents are Sonnet (code, research) and Haiku (judge audits, accountability); result JSONs never pasted into context |
| disk | 287 GB free | fine |

## 4. Framework layout

```
research/overnight/
  PLAN.md            this document
  STATE.md           live state, rewritten every loop tick (what runs, what is queued, next action)
  LEDGER.md          one row per experiment: id, stage, hypothesis, config, metrics, delta, p, verdict
  REPORT.md          written at the end: proven changes, evidence, what failed
  hypotheses/        R*_*.md research notes (ranked interventions) and per-hypothesis specs
  prompts/           answer-prompt variants (plain text, {date_line} {memories} {question})
  scripts/
    answer_eval.py   contexts JSON -> answers + 3-way grades (stdlib only; reproduces the 2026-10-22 harness)
    compare.py       paired stats: delta, wins/losses, exact McNemar p, bootstrap CI, flipped ids
    gpu.sh           runs a command under one of 2 GPU slots, logs it, stamps start/end in the ledger
    judge_audit.md   checklist the judge agent follows
.context/overnight/   (gitignored)
  db/<run>/          SQLite databases per question (LME) or per conversation (LoCoMo)
  results/           *_retrieval.json (harness), *_ctx.json (context dumps), *_acc.json (answer_eval)
  logs/              one log per job
  locks/             GPU slot directories
crates/neuralgraph-research/src/bin/chat-memory-context.rs   NEW: builds context blocks from existing DBs
                                                             with retrieval overrides (--k, --channels, --retrieval k=v)
```

Pipeline for one experiment:

```
[ingest variant]  chat-memory-longmemeval --db-dir db/X --out results/X_retrieval.json   (1.5–2 h, only for extraction ideas)
[context]         chat-memory-context lme --db-dir db/X [--retrieval ...] --out results/X_<r>_ctx.json   (< 1 min)
[answer+judge]    answer_eval.py --contexts results/X_<r>_ctx.json --prompt P --out results/X_<r>_<P>_acc.json (4 min)
[compare]         compare.py results/<baseline>_acc.json results/<variant>_acc.json
```

## 5. Experiment protocol: stages and promotion rules

| stage | what | sample | cost | promote if |
|---|---|---:|---|---|
| **S0 screen** | one variable changed vs the current best; reader/retrieval ideas | LME-50 | 4 min | strict ≥ +4 **or** lenient ≥ +4, and neither metric down by > 2 |
| **S0x screen (extraction)** | ingest variant | LME-18 stratified (`--n 18 --seed 1`) + conv0 LoCoMo | 35 min + 12 min | turn@15 not down; e2e strict up on the 18 |
| **S1 confirm** | full 50 (for extraction ideas) + LoCoMo conv0 e2e (66 q) | 50 + 66 | 2 h / 10 min | both sets up or flat; McNemar p < 0.1 on the pooled 116, or ≥ +6 on LME |
| **S2 judge-robust** | re-grade S1 winners' answers under second judge model (Qwen3.6-35B strict) and a substring check | same answers | 10 min | gain survives (same sign) under the second judge |
| **S3 refine** | ablate the winner's parts, tune its one knob (3 values), combine with other winners | LME-50 | 4 min each | keeps the gain; combination ≥ best single |
| **S4 ship** | code/prompt change in the crate, tests updated, docs/BENCHMARKS.md row, commit on this branch | — | — | independent judge agent signs off |

Rules that do not bend:
1. **One variable per S0 experiment.** Combinations only in S3.
2. **Same questions, same judge, same answerer settings** in any comparison. `compare.py` pairs by id.
3. **Judge prompts and judge model are frozen** for the comparable columns. A tighter strict prompt may be added as a *fourth* column, never substituted.
4. **Temperature 0, prefill on**, unless the experiment is about thinking.
5. Deltas under 4 points on 50 questions (2 questions) are noise; they are logged and not promoted.
6. Every result file is kept; nothing is overwritten (run names are unique: `<db>_<retrieval>_<prompt>[_<model>]`).
7. The GPU is never idle while the queue is non-empty; the queue is never empty while the night lasts.

## 6. Agents and their roles

| role | model | when | input | output |
|---|---|---|---|---|
| **orchestrator** (this session) | Fable 5.1 | every tick | STATE, LEDGER, summaries | decisions, queue, STATE rewrite |
| **research** | Sonnet | when the backlog has < 5 untested ideas | reports, code, failure rows | `hypotheses/R*.md` ranked ideas with exact changes |
| **implementer** | Sonnet | an idea needs code (Rust bin, prompt file, crate change) | spec from the orchestrator | code + build + smoke test, no benchmark runs |
| **runner** | shell via `gpu.sh` (no LLM) | always | queue entry | result JSON + log |
| **judge** | Haiku (stats) + Sonnet (audit of flips) | after each S1 and before S4 | baseline + variant JSON, `compare.py` output | verdict: confirm / reject / judge-artifact / needs-more-data, with the flipped questions audited |
| **accountability** | Haiku | every 2 h | STATE, LEDGER, logs, `ps` | consistency report: idle GPU, stale jobs, ledger rows missing results, time budget check |

The judge is *independent*: it receives the two result files and the hypothesis text, never the orchestrator's
opinion. It must (a) recompute the numbers, (b) read every flipped question and classify the flip (real fix / judge
noise / regression), (c) check the variant did not peek at gold (grep the prompt and code paths for `gold`,
`answer`, `has_answer`), (d) say whether the gain survives the second judge.

## 7. Session-limit and continuity handling

- Cron `7d9bb854` fires every 20 min while idle with a self-contained prompt pointing at `STATE.md`.
- All state is on disk: queue and running jobs in `STATE.md`, results in `.context/overnight/results`, logs in
  `.context/overnight/logs`. A fresh context can resume from those three.
- Long jobs run under `nohup` via `gpu.sh`; they survive the orchestrator's turn ending or the session pausing.
- If the session is paused by a usage limit, the cron prompt queues and fires once the limit resets; running jobs
  keep running meanwhile, so no GPU time is lost.
- `PushNotification` is sent at: baseline reproduced, first promoted change, each S4 ship, and any blocking error.
- The orchestrator never asks the user a question; ambiguous calls are made and logged in STATE under "decisions".

## 8. Hypothesis backlog (initial; research agents append to `hypotheses/`)

Reader (S0, 4 min each):
- P1 `auto`: compute only when the question needs it, otherwise one concise sentence; forbid IDK when any memory bears on the question
- P2 `fewshot_math`: P1 + three worked exemplars (duration add, date subtract, unit convert)
- P3 `strict_format`: answer must restate every entity/number asked; no hedges; lists complete
- P4 `quotes`: render the source message quotes under each memory (`--render quotes`), concise prompt
- P5 `nodate`: drop the "(from chat X, observed)" suffix to cut noise; or move the date to the front
- P6 `think`: Qwen thinking enabled with the concise prompt (cost: ~10x latency)
- P7 `two_pass`: pass 1 lists the relevant memory numbers and the needed computation; pass 2 answers
- P8 `self_consistency`: 3 samples at T=0.7, majority vote (cost 3x)
- P9 answerer model: `qwen/qwen3.8-27b` with the best prompt (expect: not the lever, verify once)
- P10 abstention: explicit "if the memories are about other topics say I don't know"

Retrieval (S0, 5 min each: dump + eval):
- Q1 `k` 10 / 20 / 25 (context_k) with context_max_chars raised
- Q2 `context_min_relative_score` 0.25 → 0.1 / 0.4; `context_min_vector_sim` 0.3 → 0.2 / 0.4
- Q3 keyword weight 0.8 → 1.0 / 0.5; rrf_k 60 → 20 / 120
- Q4 channels VKG (graph on) on the dense index
- Q5 subject/entity boosts 1.6/1.25 → 1.0/1.0 and 2.0/1.5
- Q6 recency weight 0.2 → 0 / 0.4 (knowledge-update questions)
- Q7 date-filtered search: parse a year/month in the question → since/until (needs a small code change)
- Q8 multi-query: question + a rewritten keyword query, union of contexts (needs code)

Extraction (S0x then S1; 35 min screen, 2 h confirm):
- E1 verbatim-numbers rule in `MEMORY_PROMPT` (never round or paraphrase numbers, dates, durations, names, titles)
- E2 derived-fact rule (store "X lasted N months" when computable from the window)
- E3 raw-turn fallback memories (every user turn stored verbatim at importance 0.3) → ceiling 100 %, context budget risk
- E4 batch_size 1; E5 context_messages 10; E6 dedupe_cosine 0.97 / reconcile off
- E7 extractor `qwen/qwen3.8-27b` (cost 3x)
- E8 assistant-turn rule rewrite for single-session-assistant

## 9. Timeline for the night (CDT)

| time | what |
|---|---|
| 01:47 | baseline LME-50 ingest started (`db/lme_base`) |
| 02:00–02:30 | tooling: `chat-memory-context` bin, `answer_eval.py`, `compare.py`, prompts; R1/R2 research notes |
| ~03:30 | baseline DBs done → reproduce 54/80 (`lme_base_vk15_concise_acc.json`); push notification |
| 03:40–05:30 | S0 reader sweep (P1–P10) and retrieval sweep (Q1–Q6), ~15 runs; LoCoMo conv0 baseline ingest in slot 2 |
| 05:30–07:30 | S1 confirm top 3 on LoCoMo; S2 second judge; S3 refine/combine; first extraction screen (E1) |
| 07:30– | extraction confirm runs; S4 ship proven changes (crate prompt/config, tests, BENCHMARKS.md); REPORT.md |
| until stop | keep screening the backlog; research agents refill it |

## 10. Deliverables when the user wakes

1. `LEDGER.md` with every experiment and its verdict.
2. `REPORT.md`: the proven changes with before/after tables under both judges and both datasets.
3. Commits on this branch for each proven change (prompt/config in the crate, tests green, BENCHMARKS.md updated).
4. The result files under `.context/overnight/results` for audit.
