# Live check of the holder answer step

**Component-only live check; dev split; n=40.** Written 2026-10-09 by ENGINEER-C (model `claude-sonnet-5-5`).
This is NOT a live end-to-end run: only `answer_from_evidence` inside the holder (`EvidenceStore.answer_question`) was replaced by a real model.
Retrieval, policy, audience filtering, export ledger and everything the coordinator does with the answers are the unchanged deterministic product code, and n=40 pairs from one dev world (seed 1, size S) is a small sample, so read the numbers as indications, not rates.

## What was run

* **Data:** a copy of the dev run `runs/C1-S1/data` (coord.db + 136 holder stores) in `scratchpad/engC_live/data`; the original was never written. Gold (`tasks_dev.gold.json`) and the per-task views were read from the original run directory, dev material only.
* **Product code:** the revision the run was made with, `scratchpad/code/56f9832` (the only revision directory; `mycelic/holder/service.py` and `mycelic/evidence/service.py` are what `HolderService._dispatch` and `answer_question` run).
* **Call path:** per pair, the holder's `EvidenceStore` is opened exactly as `EmbeddedHolders._open` does (export policy, routable domains, owner ids from the `holders` row; hash embedder as in the run) and `await store.answer_question(question, idempotency_key=...)` is called, the same call `HolderService` makes for a `question` envelope. The question payload is the retained envelope from `transport_messages` (`msg_id = q:<question>:<holder>`), so text, policy, `candidate_domains`, validity window and the **audience from the run** are the original ones; `tenant_id` is added as `HolderService` does. Only `question_id` is replaced by a fresh id per call, so neither call replays the run's stored answer or exports.
* **Two routers per pair:** `fake` = `DefaultModelRouter` with `fake:mycelic-fake-*` tiers (the run's deterministic provider); `live` = `DefaultModelRouter.from_settings(load_settings())` from `live_env.sh` (Qwen3-4B-Instruct-2507 Q4_K_M on llama-server `:8082`, tiers `openai:qwen3-4b-instruct-2507`, 2 server threads, 4 slots). Live pass at **4 concurrent requests** (never more). Retrieval is identical for both (same store, same query), only the answer model differs.
* **Fidelity of the replay:** the fake re-run reproduced the run's recorded response for **40/40** pairs (same status, and for the answered ones the same answer text), so store, policy, audience and retrieval are reproduced.
* **Sample (seed 20261009, one pair per question, 40 distinct questions, `asker_type='user'`, forbidden holders excluded):**
  frame = 1098 (question, routed holder) pairs: deterministic `answered` on gold holders 243, `answered` on non-gold holders 13, `no_evidence` on gold holders **0**, `no_evidence` on non-gold holders 842.
  No gold holder had a deterministic `no_evidence`, so "20 answered + 20 no_evidence with half on gold holders" cannot be met inside the no_evidence stratum. Actual strata: 17 answered/gold-holder (classes round-robin), 3 answered/non-gold-holder (3 of the 13 that exist), 20 no_evidence/non-gold-holder (classes round-robin). Gold-holder share 17/40.
* **Gold labels** (`live_answer_check.py: label_excerpt`): a *gold holder* is one in the task's gold `holders` list. A cited or retrieved excerpt is **gold** if it is on a gold holder, carries the question's context phrase (e.g. "umber valve") and, when the task has an answer (10 of the 17 gold-holder pairs), the gold entity. Seven of the 17 gold-holder pairs are tasks whose gold answer is `abstain` (coincidence, single_domain, common_origin_copies): there the gold observation is the gold holder's record with the context phrase, and no entity-level check is possible. `gold-text/nongold` = context phrase and gold entity on a holder that is not in the gold list; `decoy` = context phrase and a decoy option; everything else `other`.

## Results

Agreement on "answered" (fake vs live), `data/answer_check_report.txt`:

| subset | n | both answer | only fake | only live | neither |
|---|---|---|---|---|---|
| all | 40 | 13 | 7 | 0 | 20 |
| answered/gold-holder | 17 | 13 | 4 | 0 | 0 |
| answered/non-gold-holder | 3 | 0 | 3 | 0 | 0 |
| no_evidence/non-gold-holder | 20 | 0 | 0 | 0 | 20 |

"neither" includes 4 pairs where retrieval returned nothing, so the model was not called at all; the other 16 had 2 to 8 retrieved excerpts, none of which carried the question's context phrase (all 89 labelled `other`). In all 13 "both answer" pairs the live and the fake answer cite the same single excerpt.

Gold-holder pairs (n=17; the gold observation was among the 8 retrieved excerpts in 17/17, so every miss is in the answer step, not in retrieval):

| metric | fake | live |
|---|---|---|
| answered | 17/17 | 13/17 |
| hit: answered and cites >= 1 gold observation | 17/17 | 13/17 (Wilson 95% interval about 53% to 90%) |
| every cited excerpt is gold | 17/17 | 13/13 of its answers |
| answer text names the gold entity (10 answerable tasks) | 10/10 | 7/10 |
| answer text names a decoy entity (10 answerable tasks) | 0/10 | 0/10 |

Live misses on gold-holder pairs (4): idx 9 (cross_domain), 10 (contradiction), 11 (coincidence, gold answer `abstain`), 12 (temporal). In each, the retrieved set contained the record that literally answers the question (for example "freightbridge-service: origin of the umber valve, 23." for "Which service sits behind the umber valve that Support Desk Americas and Rapid Response Cell flag?") and the live model returned `no_evidence`.

**Live citing a non-gold or wrong excerpt: none.** All 13 live answers cite exactly one excerpt and it is gold, and the 7 answers on answerable tasks name the gold entity. No live answer ever named a decoy entity, and the live model never answered where the fake did not (0 "only live").

The 3 deterministic answers on non-gold holders (idx 17, 18, 19) are records carrying the question's context phrase: idx 17 and 18 the gold entity (`gold-text/nongold`), idx 19 a decoy entity in an abstain task. In the main run the live model returned `no_evidence` for all three.

Follow-up diagnostics (`data/answer_check_diag.jsonl`, the 7 disagreeing pairs re-run alone, one request at a time, raw replies kept; not part of the n=40 tables): 6 of 7 gave the same result again; idx 17 flipped to `answered` (cited the `gold-text/nongold` record, entity `crategate-service`; in the main run its first reply was invalid and the repair reply said no_evidence). Greedy decoding is not bit-stable across batch compositions, so single flips like this are within run-to-run noise at this n. The raw refusals show the mechanism: either the bare `{"answer": "", "confidence": 0, "used_ref_ids": [], "no_evidence": true}` (idx 9, 11, 18, 19) or an explanation that "the evidence does not mention either Warehouse Operations or Treasury Planning, nor ... a waxen zipper in relation to either service" (idx 10, 12). The question text names two departments that no excerpt mentions, and the model treats that as missing evidence. The product prompt (`answer_from_evidence`) says to answer "using ONLY the evidence" and to set `no_evidence=true` if the evidence "does not address the question". This is a prompt/task-design interaction, not a parse or retrieval failure.

Other live observations:

* 3 of 36 model calls first returned JSON without the required `no_evidence` key and were repaired by the router's single repair retry (idx 13 and 15 then answered correctly; idx 17 ended `no_evidence`). No call failed outright, none fell back to the rule.
* Cost, 36 pairs with a model call, 4 concurrent requests on 2 shared server threads: latency per pair median 141.7 s, mean 149.6 s, range 55 to 318 s (alone, one request at a time: 31 to 79 s); prompt tokens median 826 (max 1750 with a repair retry), completion tokens median 26 (max 115); totals 28,889 prompt and 1,570 completion tokens; live pass wall time 1359 s. The fake router answers in about 10 ms or less per pair.
* Live answers are terse (`manifestbridge-service`, `The arctic bobbin is routelink-service.`) with confidence 0.9 to 0.95; the fake quotes the record verbatim.

## Limits

* n=40 in one dev world with synthetic template records; one live run per pair (plus 7 reruns); no repeated seeds. The four gold-holder misses and the three non-gold refusals are the whole disagreement signal.
* Labels are heuristic text rules over the template bank (see above). For the 7 abstain-class gold-holder pairs "hit" means citing the gold holder's context-phrase record, not producing a correct final answer (the coordinator's abstention is a different step).
* The holder policy is the end-of-run `holders` row (not a per-question snapshot); the 40/40 fake reproduction suggests it did not matter.
* Concurrency 4 and the shared machine (other benchmark jobs on the same cores) make latency numbers contended.

## Files

* `LIVE_ANSWER_CHECK.jsonl` raw rows (line 1 = meta; per pair: question, holder, class, gold flags, recorded deterministic response, `fake` and `live` blocks with status, answer text, cited refs with labels, all retrieved excerpts with labels, latency, per-model-call tokens and errors).
* `data/answer_check_pairs.json` the sampled pairs and frame counts; `data/answer_check_report.txt` the generated tables; `data/answer_check_diag.jsonl` the 7 rerun pairs with raw model replies.
* `live_answer_check.py` (`plan`, `run`, `report`, and `run --only <idx,...>` for the diagnostics). Reproduce: `python3 live_answer_check.py plan`, then `set -a; . live_env.sh; set +a; python3 live_answer_check.py run --live-concurrency 4`, then `python3 live_answer_check.py report`. The default paths point at the scratchpad copy of the run; the server must be up (`/root/mycelic-live/serve.sh 4b`).
