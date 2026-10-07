# E1 model tags: configurable examples only

These are the in-boundary models STRATEGY section 11.2 lists for E1, written as the tags each server expects. They
are **examples you may pass on the command line or put in a routing file**, not defaults, not recommendations, and
**nothing was measured with them**. No E1 result exists until you run E1 yourself (`../RUNBOOK.md`, "E1").

| Model (STRATEGY E1) | Ollama tag | vLLM / llama-server model id |
|---|---|---|
| Qwen3-1.7B | `qwen3:1.7b` | `Qwen/Qwen3-1.7B` |
| Qwen3-4B | `qwen3:4b` | `Qwen/Qwen3-4B` |
| Qwen3-8B | `qwen3:8b` | `Qwen/Qwen3-8B` |
| Llama-3.2-3B | `llama3.2:3b` | `meta-llama/Llama-3.2-3B-Instruct` |
| Gemma-3-4B | `gemma3:4b` | `google/gemma-3-4b-it` |
| Phi-4-mini | `phi4-mini` | `microsoft/Phi-4-mini-instruct` |

Check each tag against the server's own model library on the day you pull it; tags change.

**The frontier reference.** STRATEGY names hosted models (Haiku 4.5 and Opus) as the comparator. Use the exact model
id your provider documents for its OpenAI-compatible endpoint, in an endpoint with `"boundary": "external"`. E1 sends
raw records to it only for public or synthetic data, and only with `--allow-external-raw` equal to the data label;
partner data never goes to an external endpoint, so for partner data the reference must be a larger model inside
the boundary.

One routing file can hold every candidate. Each candidate is one `--endpoint` at `prereg`, then three `run`
repeats.
