# Candidate E — retrieval design and result

**Status: REJECTED.** Fails the >=5 net-recovery gate. No answer generation was run,
per the standing rule that generation follows only if retrieval passes.

## What was built

Immutable raw-turn index over `evaluation/locomo/locomo10.json`:

| | |
|---|---|
| turns indexed | 5,882 |
| conversations | 10 |
| turns with image captions (`blip_caption`) | 1,226 |
| turns with original timestamp | 5,882 / 5,882 |
| index digest | `993cf7e3749b8f00` |

Each turn preserves speaker, conversation, session, `dia_id`, and the original
session timestamp -- never ingestion time. Image captions are indexed alongside
the utterance.

**Entity incidence.** `ENTITY` means *this string is mentioned in this turn*.
Extraction covers proper-noun runs (people, places, organisations), quoted
phrases, compound topics, technical identifiers, and speaker aliases.
First-person pronouns resolve to the turn's own speaker, within its conversation
only. Same-speaker proximity is **not** used as an entity edge. Profile facts are
**not** used. Entity tables are built per conversation, so **0** entity links
cross a conversation boundary (verified in `entity_integrity.json`).

**Union generation with RRF.** BM25 + entity incidence (+ optional semantic),
fused by Reciprocal Rank Fusion (K=60), order-independent and deterministic, ties
broken on uid. Entity and BM25 results can introduce candidates absent from
semantic top-k. Final ordering is relevance-first; timestamps annotate but never
reorder.

## Measurement correction that changed the conclusion

Two defects in the prior measurement were found and fixed, and both mattered more
than the candidate itself.

**1. The evidence heuristic mislabels half the set.** The raw corpus carries gold
evidence turn ids (`dia_id`), and all 120 frozen questions join to it on
(conversation, question). Against that ground truth the substring heuristic
disagrees on **30/60** questions: 8 called retrieval misses that had gold
evidence, and **22** called recoverable that had none.

**2. The baseline was being scored unfairly.** The recorded pipeline injects
resolved dates into excerpt text (`"yesterday [= 7 May 2023]"`), so 24% of its
excerpts failed to map back to a turn. Stripping the annotation and adding prefix
matching raised mapping coverage from 76.2% to **97.3%**.

After both fixes the recorded retrieval is far better than previously believed:
**46/60 = 76.7%** of questions have gold evidence in their retrieved set, and
**36/60 = 60%** have *all* of it.

## Result

### Replacement (Candidate E instead of recorded retrieval)

| metric | Candidate E | recorded baseline |
|---|---|---|
| Recall@1 | 0.0917 | **0.3450** |
| Recall@5 | 0.1658 | **0.5094** |
| Recall@10 | 0.2144 | **0.6122** |
| Recall@20 | 0.3311 | **0.6678** |
| Recall@50 | 0.5122 | 0.6678 |
| MRR | 0.1738 | **0.5154** |
| any gold@20 | 24/60 (40.0%) | **46/60 (76.7%)** |
| complete@20 | 16/60 (26.7%) | **36/60 (60.0%)** |

**Recovered 2, lost 24, net -22.** BM25 + entity incidence without a dense
generator does not approach a tuned embedding-plus-rerank pipeline.

### Additive (recorded retrieval UNION entity/BM25 top-N)

| variant | any gold | complete | newly covered |
|---|---|---|---|
| baseline alone | 46/60 (76.7%) | 36/60 (60.0%) | -- |
| + top 5 | **48/60 (80.0%)** | 36/60 (60.0%) | 2 (`1282`, `1404`) |
| + top 10 | 48/60 (80.0%) | 37/60 (61.7%) | 2 |
| + top 20 | 48/60 (80.0%) | 37/60 (61.7%) | 2 |

Additive by construction, so nothing is lost. It recovers **+2**, against a gate
requiring **+5**, and against the **12 of 15** needed for an 80% run to stay
arithmetically plausible.

## Why this is the wrong lever

Of the **46** questions where the recorded retrieval already holds gold evidence,
only **21** are answered correctly. That is **25 generation-recoverable
failures** -- the dominant pool by a wide margin. The earlier conclusion that
retrieval was the binding constraint rested on the substring heuristic, which
mislabels 30/60.

Even with the additive layer, evidence availability is exactly 48/60 = 80.0%, so
80% judged accuracy would require converting **every** evidence-present question.
Current conversion is 21/46 = 45.7%.
