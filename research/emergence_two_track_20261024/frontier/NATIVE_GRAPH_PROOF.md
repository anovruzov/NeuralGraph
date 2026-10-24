# Does the EMERGENCE path use NeuralGraph's graph? Proof, and the A/B

Question asked: does the real EMERGENCE e2e path call NeuralGraph's own edge builder, persist relationships and
traverse them over multiple hops? Then rerun the same benchmark with those capabilities on and off.

All numbers below are from dev seed 0 at 200 users, labelled **DIAGNOSTIC**. Sealed seeds were not touched.
NeuralGraph's files (`service.py`, `tesseract.py`, `retriever.py`, `temporal.py`) were called, never edited.

## 1. What the benchmarked path calls (instrumented, not read off the code)

`tools/native/prove_path.py` patches call counters onto storage, `TemporalChainManager`, `NeuralGraphService`,
`NeuralRetriever` and `Tesseract`. It builds the largest site graph (r00-site-00, 2,574 records) with the harness's own
`E2ESiteProcess`, then ranks 20 fixed questions. Raw output: `raw/native/prove_path_seed0.txt`.

| Call | default (as benchmarked) | `--graph-edges` (EMERGENCE-Next) |
|---|---|---|
| `TemporalChainManager.build_temporal_chain` / `create_causal_edge` | 0 | 0 |
| `NeuralGraphService.process_extraction_result` | 0 | 0 |
| `NeuralRetriever.retrieve` / `_expand_stage` / `retrieve_with_expansion` | 0 | 0 |
| `storage.save_edge` / `save_edges_batch` | 0 / 0 | 0 / 6 |
| edges persisted | **0** | 4,936 (TEMPORAL + ENTITY, per-entity chains) |
| `Tesseract.retrieve` | 20 | 20 |
| `get_neighbors` calls / non-empty | 120 / **0** | 120 / 120 (235 neighbours) |
| questions whose top-50 set changed | — | 4 of 20 |

**The benchmarked path persists no edges, never calls NeuralGraph's edge builders, and never runs multi-hop
retrieval.** Its only graph call is Tesseract's 1-hop temporal momentum. That call runs 6 times per question and
finds nothing, because there are no edges. The `--graph-edges` arm persists edges with our own deterministic builder
(`site_graph.chain_edges`) and Tesseract traverses them, but only 1 hop.

## 2. The native capabilities do run on this graph (offline probe)

`tools/native/native_probe.py` (raw: `raw/native/native_probe_seed0.txt`) does two things:

- **Edges:** builds per-entity chains with NeuralGraph's own `TemporalChainManager.build_temporal_chain`. That gives
  1,290 TEMPORAL edges; its 24-hour gap rule drops links between records more than a day apart.
- **Ranking:** ranks with `NeuralRetriever.retrieve` (default stages: `entity_expansion` and `temporal_chain` at
  `max_hops=3`, CA3 pattern completion).

The probe reads through a **fail-closed as-of view** (`StrictAsOf`):

- `get_node`, `get_edges_from`, `get_edges_to`, `get_edges_batch`, `find_nodes_by_entities` and `vector_search` all
  drop records received after the question's `as_of`.
- Any other storage call raises.
- Writes during ranking are dropped and counted: 247 LTP edge activations. Applying them would make each ranking
  depend on the questions asked before it.

This matters: the harness's `AsOfStorage` filters only `get_nodes_by_session` and `get_neighbors` and passes every
other call through. That is enough for Tesseract, but **it would leak future records into `NeuralRetriever`'s
multi-hop**. Do not plug `NeuralRetriever` behind `AsOfStorage`.

Results:

- All six stages ran on all 20 questions, at 3.5 s per question.
- Rankings are deterministic across rebuilds (20/20 identical).
- Native edges on vs off changed the top-50 set on 6 of 20 questions, by 0.3 records on average.

## 3. Why no ranker can raise recall under the lexical judge (structural)

What a site reads for a question (`TesseractRetrieval.union`):

1. First, the first L = 50 of the records the shipped entity retrieval matches (`edge.verify.retrieve`).
2. Then the ranker's records fill whatever room is left.

What a confirm needs (`edge.verify.decide`): one read record where the judge answers `mentions_entity = yes` **and**
`describes_predicate = yes`.

`tools/native/mention_subset.py` checked every in-window record at all 7 sites against 55 fixed questions: 184,561
record–question pairs. The judge said "mentions" on 2,940 of them. **0** of those 2,940 were outside the shipped
retrieval's match (raw: `raw/native/mention_subset_seed0.txt`). So:

- If 50 or fewer records match, every record that could confirm is already read, whatever the ranker returns.
- If more than 50 match, the ranker has **no room**.

Either way, no ranker can add a confirm: Tesseract, Tesseract with edges, or `NeuralRetriever`'s 3-hop expansion.
Only refute ↔ unknown can move, because ranker-added records in an unsupported language count as "unclear" in the
refute rule. **Recall (confirm-based) is invariant to the site graph by construction.** The gap the frontier work found
is upstream of the site ranker: which questions get asked, the per-site k-floor, and count-gate specificity at scale.

Caveat: this holds for the lexical judge. An LLM judge (the Qwen track) may answer "mentions" on records the shipped
retrieval does not match, such as aliases or paraphrases. There the ranker, and so edges and multi-hop, could matter.
That is the only setting where enabling NeuralGraph's graph can change accuracy.

## 4. The rerun: same benchmark, edges off vs on

Arms (v4 diagnostic config `rs_nd_n1_n2` at the current code, seed 0, process mode):

- control: no edges
- treatment: `--graph-edges`

Run IDs `ABLATION-v6-rs_nd_n1_n2-s0-*` and `ABLATION-v6-rs_nd_n1_n2_edges-s0-*`. Raw: `raw/native/edges_ab_seed0.txt`.

| | edges off | edges on |
|---|---|---|
| questions asked | 539 | 539 (same keys) |
| verdicts that differ | — | **0 of 539** |
| patterns found (rare) | 25 (4) | 25 (4), same set |
| decoys accepted / background-supported | 28 / 16 | 28 / 16 |
| records read, summed over site rankings | 59,886 | 61,937 (+3.4%) |
| Tesseract latency p50 / p95 | 0.656 / 1.220 s | 0.676 / 1.222 s |

As §3 predicts, edges change what the ranker returns, but not one verdict. The control also reproduces the earlier v4
seed-0 run exactly (0 of 539 verdicts differ), so the e2e path is deterministic at this config.

**Verdict:** enabling site edges changes nothing measurable except +3% read volume and latency. The native
`NeuralRetriever` arm was not run end to end: §3 shows that under the lexical judge no ranker can change a confirm.
It becomes worth running only together with an LLM judge.
