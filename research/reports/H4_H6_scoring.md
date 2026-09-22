# H4 / H6 scoring experiments (retrieval_eval, all 10 convs, 1540 questions)

Baseline reproduced exactly (main repo's uncommitted `detect_list_question_universal` regex tweak and `llm_backend.py` were copied into the worktree first). Numbers are recall@10 / recall@50 (recall@all for the union variant in brackets). Temporal is substring-meaningless, shown for completeness.

## 1. Results

| config | flags | single_hop | multi_hop | open_domain | temporal |
|---|---|---|---|---|---|
| **tesseract** baseline | – | 39.4 / 61.7 | 46.8 / 61.4 | 13.5 / 20.8 | 6.2 / 9.0 |
| H4 (orig-keyword denominator, morph+thesaurus) | NG_KEYWORD_FIX=1 | 39.0 / 60.3 | 47.1 / 60.0 | 13.5 / 21.9 | 6.2 / 9.3 |
| H4 (morph only) | NG_KEYWORD_FIX=2 | 39.0 / 61.3 | 47.9 / 60.6 | 11.5 / 20.8 | 6.2 / 9.3 |
| H6a soft penalties (-1.0/-1.5 -> x0.5) | NG_SOFT_TEMPORAL=1 | 39.4 / 61.7 | 46.8 / 61.5 | 13.5 / 20.8 | 6.2 / 9.0 |
| H6a + soft month/year (-0.3 -> x0.85) | NG_SOFT_TEMPORAL=2 | 39.4 / 61.7 | 47.0 / 61.6 | 13.5 / 20.8 | 6.2 / 9.0 |
| H6b no min_charge cutoff | NG_MIN_CHARGE=-1 | 39.4 / 61.3 | 46.8 / 61.2 | 12.5 / 20.8 | 6.2 / 9.0 |
| H6c embed top-50 always fused (+0.15*cos) | NG_EMBED_RESCUE=0.15 | 36.5 / 61.0 | 48.2 / 62.8 | 14.6 / 20.8 | 5.6 / 8.7 |
| H4+H6 | KF=2, SOFT=2 | 39.0 / 61.3 | 48.0 / 60.9 | 11.5 / 20.8 | 6.5 / 9.3 |
| H4+H6+rescue | KF=2, SOFT=2, RESCUE=0.15 | 36.5 / 59.9 | 47.9 / 62.0 | 13.5 / 20.8 | 6.2 / 8.7 |
| **tesseract+speaker+embed_union** baseline | – | 44.7 / 61.7 [65.2] | 49.2 / 61.4 [65.6] | 13.5 / 20.8 [22.9] | 6.2 / 9.0 |
| + H4 KF=1 | | 42.2 / 60.3 [64.2] | 48.9 / 60.0 [65.4] | 14.6 / 21.9 [24.0] | 6.2 / 9.3 |
| + H4 KF=2 | | 43.3 / 61.3 [64.9] | 49.8 / 60.6 [65.3] | 11.5 / 20.8 [22.9] | 6.2 / 9.3 |
| + H6a SOFT=1 / SOFT=2 | | 44.7 / 61.7 [65.2] | 49.2 / 61.5 [65.6] | 13.5 / 20.8 [22.9] | 6.2 / 9.0 |
| + H6b MIN_CHARGE=-1 | | 44.3 / 61.3 [64.9] | 49.2 / 61.2 [65.6] | 13.5 / 20.8 [22.9] | 6.2 / 9.0 |
| + H6c RESCUE=0.15 | | 44.0 / 61.0 [63.8] | 50.8 / 62.8 [65.0] | 14.6 / 20.8 [22.9] | 5.6 / 8.7 |
| + H4+H6 (KF=2, SOFT=2) | | 43.3 / 61.3 [64.9] | 49.9 / 60.9 [65.4] | 11.5 / 20.8 [22.9] | 6.5 / 9.3 |
| + H4+H6+rescue | | 45.4 / 59.9 [63.8] | 51.0 / 62.0 [65.3] | 13.5 / 20.8 [22.9] | 6.2 / 8.7 |

Gold-drop diagnosis (single_hop, full ranking limit=len(nodes)): 72/282 questions have **no** message containing any gold substring (ceiling 74.5%). Of the remaining 210, baseline drops 18 entirely; H6a/H6b: 18 (unchanged); H6c: 15; H4: **26** (worse). Median rank of first gold node: 8 baseline, 7 with H4, 9 with H6c.

## 2. Verdicts

- **H4 – DOES NOT WORK.** Dilution is real (mean expanded set 60 keywords vs 5.5 original, max 148; gold-node keyword_charge rises 0.023 -> 0.19 with the fix) but recall is flat/negative: single_hop -0.4/-1.4 (KF=1), multi_hop +0.3..+1.1 @10 but -0.8..-1.4 @50, open_domain -2.0 (KF=2). It also increases fully-dropped gold nodes 18 -> 26 (a stronger keyword term makes the wrong nodes rank above min_charge crowd).
- **H6a/H6b – DOES NOT WORK (null).** Softening the -1.0/-1.5/-0.3 penalties or removing min_charge entirely changes nothing beyond +-0.2. The hard penalties are not what drops gold: nodes penalised out of the TemporalStore top-40 are still present via the Entity/Reasoning/Adversarial lists.
- **H6c – MIXED.** Embedding rescue helps multi_hop (+1.4/+1.4 plain; +1.6 @10 with union) but hurts single_hop @10 (-2.9 plain). Only H4+H6+rescue on the union variant beats baseline on single_hop @10 (45.4 vs 44.7) and multi_hop @10 (51.0 vs 49.2), at the cost of @50 (-1.8) and recall@all (-1.4). Not a clean win.

## 3. Code changes (all in NeuralGraph/tesseract.py, worktree agent-acaa31a9e4a3231bf; patch script: scratchpad/overnight/patch_tesseract.py, full diff: h4_h6.diff)

Flags are read from `os.environ` at call time (`_flag_int`, `_flag_float`, lines 63-105), so one process can toggle them.

- L43: `import os`.
- L63-105: helpers `_flag_int`, `_flag_float`, `_keyword_groups(keywords, expand_fn, use_synonyms) -> list[set]`, `_group_keyword_charge(groups, content) = #groups with any variant in content / #groups`.
- **H4** EntityStore L1387-1389 / ReasoningStore L1611-1614 (in `retrieve`):
  `keyword_groups = _keyword_groups(self._original_keywords(query_text), self._expand_morphology, _kf == 1) if _kf else None`; new `_original_keywords()` (L1422 / L1644) = same tokenizer/stopwords as `_extract_keywords` without expansion; `_compute_*_charge` gains `keyword_groups=None` kwarg and
  before: `keyword_charge = min(1.0, matches / max(1, len(query_keywords)))`
  after: `if keyword_groups is not None: keyword_charge = _group_keyword_charge(keyword_groups, content_lower)` (L1541, L1736).
- **H6a** TemporalStore `_compute_temporal_charge` L1174: `_soft = _flag_int("NG_SOFT_TEMPORAL"); dampen = 1.0`. L1212: `topic_charge = -1.0` -> `if _soft >= 1: dampen *= 0.5 else: topic_charge = -1.0`; L1225 same for `-1.5`; L1198 wrong month `-0.3` -> `elif _soft >= 2: dampen *= 0.85`; L1309 wrong year `-0.3` -> same. L1345: `return total` -> `return total * dampen`.
- **H6b** all four stores (L635, L1408, L1632, L1810): `if charge >= self._config.min_charge` -> `if charge >= _flag_float("NG_MIN_CHARGE", self._config.min_charge)`.
- **H6c** `Tesseract.retrieve` L2128-2147, after `_apply_temporal_momentum`: cosine over `all_nodes`, top-50 each get `+ w * cos/max_cos` (created if absent), `w = NG_EMBED_RESCUE`.

## 4. Surprises

- **Fusion weights are almost uniform, dominated by OPEN.** `detect_query_type` returns open ~0.26 and temporal/entity/multi_hop ~0.05 for single_hop, multi_hop and open_domain alike, so every store gets ~0.2 weight plus the open bonus; no store "owns" a category. In the fused top-10 the TemporalStore is the most frequent member for single_hop (639 of 10x84 slots) and multi_hop, despite temporal type score ~0.05 -- because its charges include big topic/entity boosts and normalisation to max=1.
- Nodes are almost never unique to one store (only 22 of 840 single_hop top-10 slots are entity-only), which is why H6a/H6b are no-ops: the four lists overlap heavily and min_charge/penalties only reorder inside them.
- H4's raw signal improvement (8x on gold nodes) does not translate: keyword_boost is 0.4 and the semantic + speaker terms still dominate; a real fix needs keyword rank normalisation (BM25-like) or a larger keyword weight, not just the denominator.
- Hard ceiling: 25.5% of single_hop gold answers are not a substring of any message, so 74.5% is the max any retrieval can score here.
- Note for the maintainer: the committed `demo/runner.py` (HEAD) contains a hard-coded OpenAI API key; the main working tree already replaces it with `os.environ`, but the key is still in git history.
