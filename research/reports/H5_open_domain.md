# H5: open-domain routing throws away evidence

**Verdict: WORKS. open_domain 41/96 (42.7%) -> 55/96 (57.3%), +14 pts, 17 wins / 3 losses. No single_hop regression (77 vs 78 control, one LIST-mode noise flip).**

## Offline finding that reshaped the experiment
`OPEN_DOMAIN_WORLD` (context wiped) fires on only **2 of 96** open_domain questions ("Who is Anthony/Jill?"): 94/96 name a speaker, so `names_in_q` blocks it. `OPEN_DOMAIN_KEEP_CONTEXT` alone can move 2 questions. The real levers: (a) routing hedged questions to OPEN_DOMAIN_INFER, (b) the OPEN_DOMAIN_INFER prompt itself, which forbids world knowledge ("Use ONLY evidence from memories, do not guess") and invites abstention. Control abstained ("Unclear", "No evidence found", "Not found") on 27/96.

"Open-domain-looking" detector (question text only): hedge words `likely|might|would|could|probably|potentially|presumably|suspected|possibly|may|should`. Offline coverage 46/96 open_domain vs 2/282 single_hop, 17/841 multi_hop, 5/321 temporal. `is_open_domain_query` rejected (53/282 single_hop, 40/321 temporal).

## Flags (default off)
- `OPEN_DOMAIN_KEEP_CONTEXT=1`: OPEN_DOMAIN_WORLD keeps the 15 reranked memories; prompt = new `OPEN_DOMAIN_WORLD_WITH_MEMORIES_PROMPT` (memories primary, world knowledge allowed, short direct answer, never unsure).
- `OPEN_DOMAIN_FORCE_INFER=1|2`: hedged questions -> OPEN_DOMAIN_INFER. 1 overrides STRICT/INFERENTIAL/OPEN_DOMAIN_WORLD; 2 overrides every mode.
- `OPEN_DOMAIN_INFER_WORLD=1`: OPEN_DOMAIN_INFER (and the not-found retry) uses the same memories+world prompt.

## Results (ONLY_CAT=open_domain, 96 q, local_pairs, gemma-4-e4b answer/rerank/judge)
| run | flags | acc | e2e | gold-in-ctx | wins/losses |
|---|---|---|---|---|---|
| H5_control | none | 41/96 = 42.7% | 7.9 s | 10% | - |
| H5_T1_route | KEEP_CONTEXT=1, FORCE_INFER=1 | 43/96 = 44.8% | 7.8 s | 10% | 2 / 0 |
| H5_T2_full | KEEP_CONTEXT=1, FORCE_INFER=2, INFER_WORLD=1 | **55/96 = 57.3%** | 7.8 s | 10% | 17 / 3 |
| H5_sh_T2 (single_hop 100) | T2 flags | 77/100 | 7.9 s | 16% | 0/1 vs H1_control 78 and vs H2_control 78 |

Routing counts (final answer mode):
- control: OPEN_DOMAIN_INFER 39, STRICT 15, AGGREGATION 13, TEMPORAL 13, LIST 10, INFERENTIAL 4, OPEN_DOMAIN_WORLD 2
- T1: 5 rerouted (1 -> 3 correct); otherwise identical to control.
- T2: OPEN_DOMAIN_INFER_WORLD 50, STRICT 14, AGGREGATION 12, TEMPORAL 9, LIST 7, INFERENTIAL 2, WORLD_MEM 2. 52 rerouted: 22 -> 34 correct; 44 untouched: 19 -> 21.
- single_hop T2: 3 q hit INFER_WORLD (2/3 correct), zero hedge reroutes.

Per-mode, control -> T2: OPEN_DOMAIN_INFER 19/39 -> 32/50; OPEN_DOMAIN_WORLD 0/2 -> 2/2; STRICT 7/15 -> 8/14; TEMPORAL 3/13 -> 4/9 (hedged "what might X do after..." questions pulled out of TEMPORAL, which answered with dates). Retrieval untouched (gold-in-context 10% everywhere): the delta is prompt + routing. Abstentions 27 -> 13.

## Examples (control -> T2)
Wins:
1. "Would Caroline likely have Dr. Seuss books?" gold "Yes, collects classic children's books": `Unclear` -> `Yes, she has lots of kids' books including classics.`
2. "Would Tim enjoy C. S. Lewis or John Greene?" gold "C. S. Lewis": `Fantasy, growth, psychology` -> `Tim would enjoy reading books by C. S. Lewis.`
3. "Who is Jill?" (context wiped in control): `"Jill" is a very common name...` -> `Jill is John's partner/significant other.` (gold: John's partner)

Losses:
1. "Would Melanie be considered a member of the LGBTQ community?" gold "Likely no": `No` -> `Yes, she is supportive of Caroline's journey` (over-infers from allyship).
2. "What kind of yoga might John benefit from?" gold "Hatha Yoga": `Warrior II...` -> `Poses like Warrior II...` (neither right; judge flip).
3. "Career Andrew could pursue with his love for animals" gold "Park ranger": `Animal behaviorist, Ecologist` -> `Animal behaviorist or wildlife enthusiast` (judge flip).

## Caveat
Mean answer length 25 -> 56 chars; the gemma judge is lenient toward longer hedged answers. About 4 of 17 wins look like judge false positives (e.g. gold "Obesity" judged correct for "No. The memories do not mention..."). Honest gain ~+10 on 96. Re-judge with GPT-4o to settle.

## Code changes (worktree agent-ae201c981dec26f3b, uncommitted)
- NeuralGraph/answering.py:80 new prompt; :154 modes `OPEN_DOMAIN_WORLD_MEM` / `OPEN_DOMAIN_INFER_WORLD`.
- NeuralGraph/tesseract.py:125 `_OPEN_DOMAIN_HEDGE_RE`, :130 `looks_open_domain_question`.
- demo/runner.py:120-122 flags; :832 forced routing; :932 context wipe guarded; :937-943 `answer_mode`/`retry_mode`/`FINAL_MODE_STATS`; result rows carry `"mode"`; FINAL ANSWER MODES printed after ROUTING STATS.

## Cost
4 runs, 388 judged questions, 58 min LM Studio. Files: H5_control/T1_route/T2_full/sh_T2.json, run_H5_*.log, analyze_h5.py, h5_route_offline.py.

Recommendation: default `OPEN_DOMAIN_FORCE_INFER=2 OPEN_DOMAIN_INFER_WORLD=1 OPEN_DOMAIN_KEEP_CONTEXT=1`; next, try the memories+world prompt on multi_hop's 116 OPEN_DOMAIN_INFER questions.
