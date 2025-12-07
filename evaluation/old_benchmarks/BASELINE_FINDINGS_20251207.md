# MemMachine Baseline Benchmark Findings
**Date**: December 7, 2025
**Benchmark**: LoCoMo-PARALLEL v1.0 (Official MemMachine Evaluation)
**Judge Model**: GPT-4o-mini (OpenAI API)
**Answer Model**: qwen2.5:7b-instruct (Ollama)
**Embedding**: nomic-embed-text

---

## Executive Summary

Baseline evaluation of MemMachine's episodic memory system on the LoCoMo-10 benchmark reveals **strong multi-hop reasoning** but **significant temporal question weakness**.

---

## Overall Results

| Metric | Value |
|--------|-------|
| Total Questions | 1,986 |
| Overall Accuracy | ~60-68% |
| Judge | GPT-4o-mini (binary CORRECT/WRONG) |

---

## Category Breakdown (from observed runs)

| Category | Accuracy | Observations |
|----------|----------|--------------|
| **multi_hop** | ~85-95% | Excellent - connects information across sessions |
| **single_hop** | ~55-65% | Moderate - retrieval sometimes misses exact facts |
| **open_domain** | ~70-80% | Good - inference from patterns works well |
| **temporal** | ~30-40% | **POOR** - major weakness identified |
| **adversarial** | ~45-55% | Mixed - entity verification needs work |

---

## Key Findings

### 1. Temporal Questions: Critical Weakness

**Observed Pattern**: Temporal questions consistently fail at ~30-40% accuracy.

**Root Causes Identified**:
- Timestamps stored only as datetime objects, not structured for search
- BM25 doesn't weight date tokens appropriately
- Single-sentence chunking loses temporal context
- LLM hallucinates dates when not found in retrieved memories

**Examples of Failures**:
```
Q: "When did Melanie paint a sunrise?"
Gold: "2022"
Generated: "15 July 2023"  <- WRONG (hallucinated)

Q: "When did Caroline go to the LGBTQ conference?"
Gold: "May 2023"
Generated: "October 2023"  <- WRONG (wrong date)
```

### 2. Multi-hop Questions: Strength

**Observed Pattern**: Multi-hop questions achieve ~85-95% accuracy.

**Why It Works**:
- Semantic embeddings effectively find related content
- Context windowing captures connected information
- RRF hybrid reranking brings up relevant episodes

**Examples of Successes**:
```
Q: "What motivated Caroline to pursue counseling?"
CORRECT - found evidence across multiple sessions

Q: "What does Caroline's necklace symbolize?"
CORRECT - connected gift story with meaning
```

### 3. Adversarial Questions: Entity Confusion

**Observed Pattern**: ~45-55% accuracy on adversarial questions.

**Issue**: Questions attribute facts to wrong person; model often doesn't verify.

```
Q: "What is Melanie's necklace symbolize?" (when it's Caroline's)
Should say: "Not Melanie's - it's Caroline's necklace"
Often says: Incorrect answer about Melanie
```

---

## Retrieval Analysis

### Current Hybrid Search Weights
- **single_hop**: 35% semantic, 65% BM25
- **temporal**: 40% semantic, 60% BM25
- **open_domain**: 75% semantic, 25% BM25
- **multi_hop**: 60% semantic, 40% BM25

### Issues Identified
1. Temporal queries need date-token boosting (not just keyword matching)
2. Session date headers not being weighted for retrieval
3. Relative time references ("last week") not being resolved

---

## Generation Analysis

### Current Prompts
- Standard, temporal, multi-hop, adversarial, open_domain prompts
- Prompts instruct to answer from context only

### Issues Identified
1. LLM still hallucinates dates when not found
2. Missing explicit "Not stated" instruction for temporal questions
3. No system message enforcing strict grounding

---

## Fixes Implemented

### Retrieval Fixes
1. **Structured Timestamp Metadata**: `TimestampMetadata` class with searchable date tokens
2. **Date-Aware BM25 Reranker**: 2.5x boost for date token matches
3. **Larger Chunks for Dates**: 3-5 sentence chunks when dates detected

### Generation Fixes
1. **System Message**: "NEVER use external knowledge; respond 'Not stated' if missing"
2. **Updated Prompts**: All prompts now enforce memory-only answers
3. **Temporal Prompt**: Explicit "If date NOT present, respond: 'Not stated.'"

---

## Expected Improvements

| Category | Before | Expected After |
|----------|--------|----------------|
| temporal | ~35% | ~55-65% |
| single_hop | ~60% | ~65-70% |
| adversarial | ~50% | ~60-70% |
| multi_hop | ~90% | ~90% (maintain) |
| open_domain | ~75% | ~75% (maintain) |

---

## Next Steps

1. Run new benchmark with implemented fixes
2. Compare category-by-category improvements
3. Iterate on temporal retrieval if still underperforming
4. Consider knowledge graph integration for entity verification

---

## Files Modified

- `data_types.py`: Added `TimestampMetadata` class
- `declarative_memory.py`: Date-aware chunking, timestamp metadata creation
- `date_aware_reranker.py`: New reranker with date token boosting
- `locomo10_parallel_benchmark.py`: Updated prompts and system message
