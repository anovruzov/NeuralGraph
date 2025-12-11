# Problematic Results Analysis - Root Cause Investigation

## Summary
- **Overall Accuracy**: 38.69% (77/199)
- **Retrieval Latency**: 54-130ms (avg ~90ms) - GOOD
- **Answer Generation**: 6-18 seconds per question

## Category Breakdown
| Category | Accuracy | Analysis |
|----------|----------|----------|
| temporal | 13.51% | **CRITICAL** - Worst performer |
| single_hop | 15.62% | **CRITICAL** - Basic retrieval failing |
| open_domain | 23.08% | Poor |
| multi_hop | 48.57% | Good |
| adversarial | 63.83% | Excellent |

---

## ROOT CAUSE ANALYSIS

### Problem 1: Temporal Questions Failing (13.51%)

**Examples of failures:**

1. **Q: "When did Melanie paint a sunrise?"**
   - Gold: "2022"
   - Generated: "Not mentioned in memories" but then calculates "May 8, 2022"
   - **ROOT CAUSE**: The LLM saw relevant info ("last year") but gave an ambiguous answer. The retrieval found the memory, but the answer generation failed to commit.

2. **Q: "When did Melanie run a charity race?"**
   - Gold: "The sunday before 25 May 2023"
   - Generated: "May 20, 2023" (calculated from "last Saturday")
   - **ROOT CAUSE**: Judge marked WRONG because gold says "sunday" but answer says "Saturday, May 20". Actually, May 20, 2023 WAS a Saturday. The gold answer is imprecise ("sunday before 25 May 2023" is May 21). This might be a DATA/JUDGE issue.

3. **Q: "When is Melanie planning on going camping?"**
   - Gold: "June 2023"
   - Generated: "Not mentioned in memories" (but mentions past camping in June)
   - **ROOT CAUSE**: The query asks for FUTURE plans but retrieval found PAST camping. Need better temporal reasoning.

4. **Q: "When did Caroline give a speech at a school?"**
   - Gold: "The week before 9 June 2023"
   - Generated: "May 30, 2023" (calculated correctly from "last week" relative to June 9)
   - **ROOT CAUSE**: Judge issue - "week before 9 June 2023" includes May 30. Answer is CORRECT but marked WRONG.

### Problem 2: Single-Hop Questions Failing (15.62%)

**Examples of failures:**

1. **Q: "What did Caroline research?"**
   - Gold: "Adoption agencies"
   - Generated: Long explanation about adoption process
   - **ROOT CAUSE**: Answer is verbose. Says "researched and prepared for adoption" but gold wants "Adoption agencies". The info is there, but judge is too strict.

2. **Q: "Where did Caroline move from 4 years ago?"**
   - Gold: "Sweden"
   - Generated: "Not mentioned in memories"
   - **ROOT CAUSE**: **RETRIEVAL FAILURE** - The memory about Sweden was not retrieved. This is a critical issue.

3. **Q: "What is Caroline's relationship status?"**
   - Gold: "Single"
   - Generated: "Not mentioned in memories"
   - **ROOT CAUSE**: **RETRIEVAL FAILURE** - The memory stating she's single was not retrieved.

---

## IDENTIFIED ROOT CAUSES (Priority Order)

### 1. RETRIEVAL FAILURE (Critical)
- Single-hop questions asking for specific facts get "Not mentioned in memories"
- The embedding search is not finding relevant memories
- Boost values are working (0.03-0.13) but base retrieval is weak

**Evidence**: Questions about Sweden, relationship status, etc. return nothing even though info exists in conversation.

### 2. TEMPORAL REASONING MISMATCH
- Gold answers use relative terms ("week before X", "sunday before Y")
- Generated answers use absolute dates ("May 30, 2023")
- Judge can't reconcile these formats

**Evidence**: Multiple temporal questions have CORRECT calculations but are marked WRONG.

### 3. ANSWER VERBOSITY
- LLM generates long explanations when short answers are needed
- Judge may penalize for not being concise enough

**Evidence**: Q4 asks "What did Caroline research?" - Gold: "Adoption agencies" vs. long paragraph.

### 4. JUDGE STRICTNESS
- Current judge requires exact match of key info
- Partial matches, synonyms, or format differences fail

---

## RECOMMENDED FIXES

### Fix 1: Improve Retrieval (Highest Priority)
- **Issue**: Top-k retrieval (k=10) may not be enough for single-hop questions
- **Fix**: Increase top_k to 15-20 OR use reranking
- **Alternative**: Add keyword-based fallback for simple entity queries

### Fix 2: Temporal Answer Normalization
- Convert all temporal answers to a standard format before judging
- Parse relative dates ("week before X") to absolute dates
- Compare date ranges, not strings

### Fix 3: Answer Compression
- Add post-processing to extract the KEY answer from verbose responses
- Pattern: "The answer is X" extraction

### Fix 4: Semantic Judge
- Instead of strict matching, use semantic similarity
- Allow synonyms and paraphrases

---

## CURRENT BENCHMARK STATUS

**Run 1/10 in progress**
- Progress: ~4% (77/1986 questions)
- Current accuracy: ~19.5%
- Latency: 5.0s/question
- ETA: ~158 minutes

The accuracy will likely improve as multi_hop (48.57%) and adversarial (63.83%) questions come later in the conversation.

---

## NEXT STEPS

1. Let current 10x benchmark complete for baseline statistics
2. Investigate why "Sweden" and "relationship status" memories aren't retrieved
3. Consider increasing top_k or adding keyword fallback
4. Consider relaxing judge for temporal/format variations
