# H3: index question-and-reply PAIRS as extra nodes

Setup: demo/retrieval_eval.py, all 10 LoCoMo conversations, single_hop / multi_hop / open_domain
(n = 282 / 841 / 96 = 1219). Substring recall of gold in retrieved message text. The worktree was synced to the
main working tree (untracked retrieval_eval.py, llm_backend.py, runner.py and the uncommitted tesseract.py
regex tweak copied in); every variant ran on the same code in one pass.

## 1. Results (recall@10 / @50 / @all, %)

| variant | single_hop | multi_hop | open_domain | ALL |
|---|---|---|---|---|
| tesseract | 39.4 / 61.7 / 61.7 | 46.8 / 61.4 / 61.4 | 13.5 / 20.8 / 20.8 | 42.5 / 58.2 / 58.2 |
| tesseract+embed_union (control) | 39.4 / 61.7 / 65.2 | 46.8 / 61.4 / 65.6 | 13.5 / 20.8 / 22.9 | 42.5 / 58.2 / 62.2 |
| embed | 25.9 / 52.1 / 52.1 | 35.1 / 54.8 / 54.8 | 13.5 / 19.8 / 19.8 | 31.3 / 51.4 / 51.4 |
| **embed+pairs** | 41.1 / 59.6 / 59.6 | 54.1 / 65.2 / 65.2 | 12.5 / 19.8 / 19.8 | **47.8 / 60.3 / 60.3** |
| **tesseract+pairs** | 39.4 / 61.7 / 68.4 | 46.8 / 61.4 / 69.1 | 13.5 / 20.8 / 24.0 | 42.5 / 58.2 / **65.4** |
| embed+pairs3 | 36.2 / 58.9 / 58.9 | 53.7 / 65.2 / 65.2 | 11.5 / 18.8 / 18.8 | 46.3 / 60.0 / 60.0 |
| tesseract+pairs3 | 39.4 / 61.7 / 68.1 | 46.8 / 61.4 / 69.6 | 13.5 / 20.8 / 20.8 | 42.5 / 58.2 / 65.4 |

tesseract+* keep the flat top-50 intact, so @10/@50 are identical by construction; the back-fill only
changes @all (the 80-candidate rerank window).

## 2. Verdict

**WORKS.** tesseract+pairs beats the control by **+3.2 pts recall@all** (62.2 -> 65.4; single_hop +3.2,
multi_hop +3.5, open_domain +1.1) at the same 30-candidate budget: 44 questions recovered, 5 lost.
Pure embedding gains far more: embed+pairs is **+16.5 recall@10 / +8.9 recall@50 over embed**
(31.3 -> 47.8, 51.4 -> 60.3) and beats Tesseract's own flat top-10/50 (42.5 / 58.2).
The 3-window (prev/this/next) adds nothing over the 2-window and is slightly worse on single_hop; skip it.

## 3. Examples

Helped (gold sits in a short reply that only makes sense with the question before it):
- conv0 "What are Caroline's plans for the summer?" gold "researching adoption agencies" -> reply
  "Researching adoption agencies - it's been a dream..." control missed, +pairs rank 51 (embed 0 -> 3).
- conv2 "What food item did Maria drop off at the homeless shelter?" gold "Cakes" -> "...I'm off to bake some
  cakes." control missed, +pairs rank 52.
- conv0 "What book did Caroline recommend to Melanie?" gold "Becoming Nicole" -> "I loved 'Becoming Nicole'..."
  control missed, +pairs rank 51 (embed 0 -> 1).
- conv1 "What is Jon's attitude towards being part of the dance festival?" gold "Glad" -> "Yeah, awesome! Glad
  to be part of it." embed 36 -> embed+pairs 5.

Hurt (all 5 tesseract+pairs losses have one shape: plain-embedding back-fill had the gold at rank 60-72, the
pair pool's 30 slots went elsewhere):
- conv5 "Where did Audrey get Pixie from?" gold "breeder": control 68, +pairs missed.
- conv7 "What does yoga on the beach provide for Deborah?" gold "a peaceful atmosphere": control 69, +pairs missed.
- embed+pairs @10 loses 35 questions where a neighbour's context outranks the gold, e.g. conv1 "How does Jon
  feel about the opening night?" gold "excited": embed 8 -> 39.

Follow-up worth one run: back-fill 15 message-embedding + 15 pair hits so both pools survive.

## 4. Code changes (all in demo/retrieval_eval.py, worktree agent-a96e7782c6d2db9a2)

- :46-48 `PAIR_EXTRA=30`, `PAIR_WINDOWS` (derived from VARIANTS), `DUMP_PATH` (per-question rank dump).
- :147-148 `ingest()` builds pair nodes, returns 5th value `pairs: {window: [NeuralNode]}`.
- :151-193 `build_pair_nodes()`: text "[spk_{i-1}] t_{i-1}\n[spk_i] t_i" (window 3 adds next), cache key
  `conv{idx}_pairs{window}`; nodes NOT saved to storage; metadata `speaker`=replier,
  `pair_members`=[msg_i, msg_{i-1}(, msg_{i+1})] reply-first.
- :196-205 `unpair()`: expands pair hits to member messages, dedupes, keeps score order.
- :208-216 `retrieve()` gains `pairs`; "embed+pairs[3]" = cosine over nodes+pairs, unpaired, top-50.
- :246-252 "tesseract+pairs[3]" = Tesseract top-50 + unpaired pair-cosine hits not already present, 30 appended.
- :274, :291-293, :301-302 main-loop plumbing + dump. tesseract.py untouched.

## 5. Cost

- Extra embeddings: 5,872 for pairs2 (one per message minus the first per conv; 5,882 messages), +5,862 for
  pairs3. Cached as emb_cache/conv{i}_pairs2.json / _pairs3.json (6-11 MB each); first-run embedding of both
  windows took roughly 2-3 min of LM Studio time in batches of 64. Full eval wall time 421 s including that.
- Retrieval: one extra numpy cosine over ~600 rows per query, sub-millisecond; no extra LLM calls.
- RAM: node count per conversation doubles (pair nodes live outside storage).
