# Examples

This directory holds templates, never defaults:

- `routing.example.json`: one site's routing file (below);
- `central_routing.example.json`: the central comparator's routing file for E2 (G6; below);
- `e1_models.example.md`: the model tags STRATEGY lists for E1, as configurable examples;
- `e1.prereg.example.json`: the exact key set E1's `prereg` writes, with placeholder values (the E1 tests check that
  `prereg` writes exactly these keys).

## Routing

`routing.example.json` is a template for one site's routing file. Copy it, set every `boundary` to your own site
(`site:<site-id>`), delete the endpoints you do not run, and point `base_url` at your servers. The runtime never
adds or removes `/v1`, so the URL must be exactly what your server serves (see `../RUNBOOK.md`, section 2).

**This directory is the only place in the repository where model tags appear.** The tags in the example
(`qwen3:8b`, `Qwen/Qwen3-32B`, `local-gguf`) are placeholders that show the shape each server expects:

- Ollama wants its own tag (`name:size`);
- vLLM wants the model id it was started with;
- llama-server serves whatever file it loaded and accepts any model string.

They are **not recommendations** and **nothing was measured with them**. Use whatever your hardware runs.

## Endpoints in the example

| Name | Server | Boundary |
|---|---|---|
| `site-a-llamaserver` | llama-server on port 8080 | `site:a` |
| `site-a-ollama` | Ollama on port 11434 | `site:a` |
| `site-a-vllm` | vLLM on port 8000 | `site:a` |
| `external-comparator` | a hosted, OpenAI-compatible API, for comparison runs on public or synthetic data only | `external` |

`external-comparator` uses a placeholder host (`api.provider.example`). Replace it with your provider's
OpenAI-compatible base URL, and put the key in the environment variable named by `api_key_env`. Never put a key in
the file. The runtime refuses to send raw records to an `external` endpoint unless the runtime was built for public
or synthetic data (E3 does this; nothing that reads partner records can).

The route `extract_claims` shows single-hop escalation. A reply that still fails the schema after one repair is
retried once on `escalate_to`, with the original prompt only. The route `judge_record` (G6) is the site's
pushdown judge: it answers one question about one of the site's own records, so it must stay at `site:<site-id>`
(E2 refuses a judge route, or an escalation, that names another boundary).

## The central comparator (E2)

`central_routing.example.json` routes E2's two central tasks, `judge_candidate_raw` (raw record text, which E2 sends
only from synthetic worlds) and `judge_candidate_allowed` (only the fields policy allows to leave), to one endpoint
with boundary `central`. `central-model-tag` and the port are placeholders: point it at the server and model you
compare against. E2 refuses a central route that names a `site:` boundary or the fake provider.

## Prices

No prices ship, and an endpoint without one is recorded as `unpriced` (cost `null`), never as free. To add one,
read it from the provider's own pricing page on the day you run, and add **one** of:

```json
"price": {"per_mtok_in": 0.0, "per_mtok_out": 0.0}
```

(US dollars per million input and output tokens), or, for hardware you run yourself:

```json
"price": {"usd_per_hour": 0.0}
```

(your own amortised cost per hour, charged by measured latency). The zeros above are placeholders, not prices.
Record in your notes where each figure came from.
