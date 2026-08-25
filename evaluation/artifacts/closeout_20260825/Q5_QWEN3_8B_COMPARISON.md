# Q5 — Qwen3 8B vs Qwen2.5 7B-Instruct (unpaid, deterministic)

**Verdict: FAIL. Replacing `qwen2.5:7b-instruct` with `qwen3:8b` is not an
improvement.** Zero paid calls. No judge was consulted, so **nothing here is a
judged accuracy score**.

## Provenance

| | |
|---|---|
| model | `qwen3:8b`, digest `500a1f067a9f`, Q4_K_M, 8.2B params |
| ollama | 0.32.13 · context 40,960 · embedding 4,096 |
| mode | `/no_think`, frozen on the smoke test below |
| temperature | 0 · seed: not exposed by the Ollama chat endpoint |
| prompt hash | `5e923dc613887b80` (v1, unchanged) |
| retrieval / evidence / ordering / membership / schema | all frozen, identical to baseline |

Everything except the model is held constant, so any difference is model
capability — **not a NeuralGraph retrieval mechanism**.

## Mode selection (5 development questions, both modes)

| | think | `/no_think` |
|---|---|---|
| item F1 | 0.8667 | 0.8667 |
| exact-set match | 0.600 | 0.600 |
| unsupported items | 1 | 1 |
| parse success | 1.00 | 1.00 |
| latency p50 | 30.6 s | **24.5 s** |
| mean output tokens | 557 | **435** |

Quality is identical; `/no_think` is 20% faster. **Frozen on latency**, not on
any judged result.

## Result

Reported on the rows completed at time of writing. Generation is slow (median
34 s/question, one outlier at 229 s) and the run is resumable; the cache is
atomic and re-running continues from where it stopped.

| metric | qwen2.5:7b | qwen3:8b | delta |
|---|---|---|---|
| item F1 | 0.4944 | 0.4961 | **+0.0017** |
| item precision | 0.5588 | 0.5451 | −0.0137 |
| item recall | 0.4647 | 0.4961 | **+0.0314** |
| exact-set match | 6 | **5** | **−1** |
| unsupported items | **1** | **15** | **+14** |
| unsupported rate | 0.048 | **0.536** | +0.489 |
| latency p50 | ~25 s | 34 s | slower |
| operational errors | 0 | **0** | — |

Evidence-present conversion falls 33.3% → 26.7%. Incomplete lists unchanged at 7.

### Passing gate

| criterion | result |
|---|---|
| item F1 improves | PASS (+0.0017, marginal) |
| exact-set match improves | **FAIL** (6 → 5) |
| unsupported-item rate not materially worse | **FAIL** (0.048 → 0.536) |
| operational errors remain zero | PASS |
| **overall** | **FAIL** |

## The unsupported-item spike is real, not a measurement artifact

Checked, because the same trap caught an earlier round:

| measure | qwen2.5 | qwen3 |
|---|---|---|
| strict substring | 1 | 15 |
| **token-wise** | **1** | **12** |
| median item length | 15 chars | 16 chars |
| total items emitted | 21 | 28 |

Both measures agree and item length is unchanged, so this is not longer items
failing a substring test.

The mechanism is visible in `q48` — *"Who supports Caroline when she has a
negative experience?"*:

* qwen2.5 → `['Melanie']`
* qwen3 → `['close friends', 'Melanie', "people in her LGBTQ+ community group…"]`

Qwen3 is **more expansive**. It gains recall (+0.031) by proposing additional
plausible items, and most of them are not in the evidence. That is a worse
trade than it appears: a grounded system that omits is recoverable, one that
invents is not.

## Does the Q4 support filter rescue it?

No. Applying `containment(1.0)` to Qwen3's output:

| config | F1 | precision | recall | ESM | unsupported |
|---|---|---|---|---|---|
| qwen2.5 raw (baseline) | **0.4944** | **0.5588** | 0.4647 | **6** | **1** |
| qwen3 raw | 0.4961 | 0.5451 | **0.4961** | 5 | 15 |
| qwen3 + containment filter | 0.4471 | 0.4961 | 0.4373 | 4 | 6 |

The filter does its job — unsupported 15 → 6 — but Qwen3 + filter is **worse than
the qwen2.5 baseline on every metric**. The filter cannot recover recall it has
removed, and Qwen3's extra recall was largely ungrounded to begin with.

## Conclusion

**Replacing Qwen2.5 7B with Qwen3 8B is insufficient**, and the paid judging step
is **not** justified. The frozen paid-judge command in
`Q5_STRONGER_ANSWERER_PLAN.md` remains prepared and unexecuted.

This also sharpens Q6: the generation gap is not simply "the model is too small".
A newer, larger local model traded grounding for coverage and came out behind.
The gap is more specific than parameter count.
