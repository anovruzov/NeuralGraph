# LoCoMo — full 1,540-question Mem0-methodology evaluation

> **83.2% answer accuracy (1,281/1,540)** with a local 9B extractor and local 35B answerer/judge at top-200 retrieval. **31 ms median native retrieval** in the same evaluation environment.
>
> Run ID: `595ffd62` · 2–3 November 2026 · Source: *Mycelic on LoCoMo under Mem0's official methodology, v2*, generated 3 November 2026, based on audited evaluation results.

## Result

| Retrieval limit | Correct / 1,540 | Judged accuracy |
|---|---:|---:|
| Top 10 | 1,160 | 75.3% |
| Top 20 | 1,211 | 78.6% |
| Top 50 | 1,278 | 83.0% |
| **Top 200** | **1,281** | **83.2%** |

All **1,540** questions from LoCoMo categories 1–4 were evaluated across ten conversations; category 5's 446 adversarial questions were excluded in accordance with the Mem0 evaluation used for this run. There were 1,540/1,540 question IDs, ten ingested databases, zero failed extraction jobs, zero empty answers, and zero malformed judge labels. Top-200 corresponds to the full candidate set returned by this memory backend (100–177 memories per question, median 146).

### Accuracy by category, top-200

| Category | Questions | NeuralGraph (local) |
|---|---:|---:|
| Single-hop | 841 | 82.3% |
| Multi-hop | 282 | 90.4% |
| Temporal | 321 | 84.4% |
| Open-domain | 96 | 65.6% |
| **Overall** | **1,540** | **83.2%** |

## Protocol and model stack

- **Runner and prompts:** `mem0ai/memory-benchmarks` at `4b61c5d31b9c`; `run.py`, `prompts.py`, and `llm_client.py` unchanged apart from a recorded timing-only patch.
- **Dataset:** Snap Research LoCoMo `locomo10.json`, 10 conversations, 1,540 non-adversarial questions.
- **Extraction:** `Qwen3.8-9B` quantized 4-bit MLX plus `nomic-embed-text v1.5`, deterministic mode, 5,882 turns ingested on local hardware.
- **Answers and grading:** `Qwen3.6-35B-A3B` via local LM Studio for both reader and LLM judge; original Mem0 answer and judge prompts/scoring used.
- **Hardware:** Apple M5 Max, 48 GB memory, macOS 26.4.
- **Model substitution:** the local 35B judge was used **instead of the original GPT-4o judge**. LM Studio replaced Ollama; an adapter supplied source-turn times as equivalent creation times. These are material differences from Mem0's published platform run.
- **Code provenance:** NeuralGraph build `b130b233917b`, source clean at evaluation.

This is a **full-run, audited local-model result under Mem0's open-source evaluation structure**, not a claim that the exact same models, judge, and infrastructure were used in Mem0's commercial platform.

## Latency, measured on the full-run configuration

| Metric | Native retrieval latency |
|---|---:|
| p50 | **31 ms** |
| p95 | **38 ms** |
| p99 | **41 ms** |

These figures include CLI process startup and exclude LLM answer generation (local answer p50 was 19 seconds, judge p50 was 1.3 seconds). They were measured in the full-run environment; other README latency figures use different configurations and cohorts.

## What the Mem0 protocol does and doesn't measure

NeuralGraph also implements adaptive, query-type-specific prompting and associative graph mechanisms (Flash resonance, optional Electron propagation, and Hebbian co-activation in the multi-stage path). **The 83.2% LoCoMo run used the official Mem0 prompt code for protocol compatibility.** It therefore does **not isolate the performance of NeuralGraph's adaptive prompting strategy**, nor does it measure the incremental accuracy contribution of Electron or Hebbian mechanisms. A fixed-vs-adaptive prompt ablation on the same questions, reader, judge, and retrieval outputs is needed to quantify any change—positive or negative.

**Reader effect:** an auxiliary Sonnet 5.5 analysis estimated **92.5% (1,425/1,540)** by re-answering only the 259 local errors while retaining the 1,281 originally correct answers. Because it is a one-sided partial re-score using subagents, **92.5% is an estimate, not an independently completed full-run Sonnet benchmark**.

**External reference:** [Mem0's public benchmark and methodology](https://mem0.ai/research) reports **92.5%** for the Mem0 platform on LoCoMo, under its own answerer and judge. Different models and judges mean these percentages are not a controlled head-to-head.

## Audit provenance

This document summarizes the team-provided four-page audit *Mycelic_LoCoMo_Mem0_Methodology_Results_v2_2026-11-03.pdf*, run `595ffd62`. The report identifies the evidence bundle as `.work/Mycelic_Mem0_Official_Methodology_LOCALJUDGE_595ffd62.zip` and the unchanged upstream runner at the commit above. The raw evidence bundle is referenced by that report; it is **not reproduced in this Markdown document**.

Earlier September experiments, including a 744-question end-to-end campaign and 1,540-question retrieval-only experiments, used **different evaluation pipelines**. They are separately documented in [BENCHMARKS.md](BENCHMARKS.md) and [RESULTS_ALL.md](../research/reports/RESULTS_ALL.md).
