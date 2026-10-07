# Mycelic collective: architecture (gate G1)

This document describes what gate G1 builds under `mycelic/collective/`. It also places G1 in the loop that later
gates complete (STRATEGY section 4.1).

**No real-model number is produced in this sandbox.** Model weights and the openFDA API cannot be reached from it, so
every test runs against a deterministic in-process fake or a local fake HTTP server. Every harness output says so in
its `measurement` flag. The figures STRATEGY needs come from the founder's runs (`RUNBOOK.md`).

## 1. What G1 builds

| Part | Module | Purpose |
|---|---|---|
| Strict JSON | `jsonio.py` | Refuses byte-order marks, invalid UTF-8, NaN/Infinity (and `1e999`), duplicate keys and deep nesting; canonical serialisation for hashes, ledgers and run files |
| Schema subset | `schemacheck.py` | Local validation of every model reply; problems are `(json_path, keyword)` and never hold a value |
| Statistics | `stats.py` | Percentile, Wilson interval, exact sign test, paired bootstrap; seeded only |
| Model runtime | `inference/runtime.py` | The only way collective code calls a model; bound to one boundary; repair once, escalate at most once |
| HTTP client | `inference/client.py` | OpenAI-compatible chat over `http.client`; a deadline on every receive (status line, headers and body), size cap, no redirects, no stored text |
| Routing | `inference/routing.py` | Routing file: endpoints with their boundary, routes with optional single-hop escalation; no defaults ship |
| Tasks and prompts | `inference/tasks.py` | Task specs, the `<data>` block, the repair message |
| Reply parsing | `inference/jsonparse.py` | One JSON object out of a reply (reasoning blocks, fences, prose), with a depth cap |
| Usage ledger | `inference/ledger.py` | One JSON line per logical attempt; numbers and labels only |
| Fakes | `inference/fake.py`, `inference/fakeserver.py` | Deterministic in-process provider; local OpenAI-compatible server with 33 personas |
| Founder tools | `experiments/e3_latency.py`, `connectors/openfda.py`, `experiments/n1_narratives.py` | E3, the openFDA cache, N1 sample and score |

Everything is standard library only and runs under `python -S`. No fabric file changed (`INTEGRATION.md`).

## 2. The boundary guard

A `Runtime` is built for one boundary, either `site:<id>` or `central`, and keeps it for its lifetime. `run` and
`single` have no boundary parameter. Every endpoint in the routing file declares its own boundary: `site:<id>`,
`central`, `external` or `any-simulated`. Every task declares its data class: `raw` means the payload holds record
text; `structured` means it holds only fields policy allows to leave a site.

`boundary_mode()` is the single source of truth. Rules apply top to bottom:

| Endpoint boundary | Condition | Mode |
|---|---|---|
| `any-simulated` | provider is `fake`, or the runtime was built with `simulation=True` | `simulated` |
| `any-simulated` | otherwise (raw and structured alike) | refused |
| same as the runtime's | | `own` |
| any other | task is `structured` | `structured_egress` |
| any other | runtime has `allow_external_raw` (only `public` or `synthetic`, equal to its `data_label`) | `external_raw_exempt` |
| any other | otherwise | refused |

Partner data can never be exempted: `allow_external_raw` must equal `data_label`, and only `public` and `synthetic`
are accepted. The guard runs before any I/O, on the primary endpoint and then on the escalation endpoint. A refusal
does three things:

- writes one ledger row (attempt 0, mode `refused`, error `boundary`);
- raises `InferenceBoundaryError`;
- sends no request to any endpoint, even when only the escalation endpoint is out of bounds.

## 3. One call: attempts, repair, escalation

Pre-flight checks run in this order, before any I/O:

1. the `ref` handle (opaque, ASCII, at most 96 characters; never a record id);
2. the payload (a dict that canonical JSON accepts);
3. the schema (compiles in the subset, with a top-level object);
4. the route, or the `endpoint=` override;
5. the api-key environment variables of every endpoint that may be contacted;
6. the boundary guard.

| Attempt | Endpoint | Messages | When |
|---|---|---|---|
| 1 | primary | system (task, schema) + user `<data>` block | always |
| 2 | primary | the same + one repair message (`### REPAIR`, problems as `path: keyword`, a 300-character excerpt of the bad reply as data, a truncation hint when `finish_reason` was `length`) | attempt 1 was `json_invalid` or `schema_invalid` |
| 3 | `escalate_to` | the attempt-1 messages only: no repair marker, no excerpt | attempt 2 was also `json_invalid` or `schema_invalid`, and the route names `escalate_to` (no `endpoint=` override) |

**Escalation matrix.** Only `json_invalid` and `schema_invalid` lead to a repair or an escalation. These never reach
the escalation endpoint:

- `http_4xx`: a 400 for `json_schema` or for strict keywords, 401 or 403, or 429 after its retries;
- `http_5xx` after its retries;
- `timeout`, `network` and `too_large`;
- `boundary`, configuration errors and `no_handler`.

Transport retries (429, 5xx and connection failures, never timeouts or TLS-verification failures) happen inside one
logical attempt. They are counted in that attempt's row, not written as rows of their own.

Errors are `InferenceError(task, endpoint, kind, http_status)`. Each has exactly those four values and no other
argument. Every raise uses `from None`, so no reason phrase, server body or socket text reaches a message, a
traceback or a log. Logging is at most one WARNING per failed attempt, naming the task, endpoint, attempt, kind and
status.

## 4. The usage ledger

Each runtime appends one canonical JSON line per logical attempt to its own ledger file. The row has exactly 27 keys:

| Key | Meaning |
|---|---|
| `ts` | from the runtime's injected clock |
| `run_id` | the run this call belongs to |
| `task` | task name |
| `ref` | the caller's opaque handle (never a record id) |
| `attempt` | 0 refused before any I/O, 1 primary, 2 repair, 3 escalation |
| `escalated_from` | the primary's name on attempt 3, else null |
| `endpoint`, `provider` | which endpoint served the attempt |
| `model_requested`, `model_served` | the configured tag, and the id the server reported (kept only if it is a short plain identifier) |
| `host` | `host:port`, or `in-process` for the fake |
| `boundary`, `endpoint_boundary` | the runtime's boundary and the endpoint's |
| `boundary_mode` | `own`, `simulated`, `external_raw_exempt`, `structured_egress` or `refused` |
| `data_label` | `synthetic`, `public` or `partner` |
| `ok`, `error_kind` | outcome; the kind is null when ok |
| `http_status`, `finish_reason`, `transport_retries` | transport facts of the final HTTP try |
| `tokens_in`, `tokens_out` | as reported by the server; null when not reported, never 0 |
| `latency_ms`, `ttft_ms` | the final try, measured with a monotonic timer |
| `cost_usd`, `cost_basis` | `per_token` or `per_hour` from a configured price; `unpriced` (null cost) without one; `fake` for the fake provider |
| `fake_marker` | the response carried `X-Mycelic-Fake: 1`, or the provider is the fake |

A row never holds a prompt, a completion, an error body, a header, a record id or record text.

**Only `usage_summary` crosses a boundary.** A site's ledger stays at the site. `usage_summary()` reduces it to
counts per (task, endpoint): calls, ok, errors by kind, token sums with missing counts, latency p50/p95 and a fake
flag. It has no `ts`, `ref`, `host`, `run_id` or model name. It is the only ledger-derived artifact meant to leave a
site.

## 5. Fakes and the measurement flag

- `FakeProvider` is in-process and deterministic. Each task has a handler; `fail_next` scripts the next replies to
  exercise repair and failure paths; it opens no socket. Its rows are priced `fake`.
- `FakeOpenAIServer` listens on 127.0.0.1 and implements 33 personas: valid, slow, trickling the status line, the
  headers or the body, oversized, deeply nested, echoing the prompt into its error, rejecting `json_schema`,
  streaming, stalling, resetting mid-body, and others. Every response carries `X-Mycelic-Fake: 1`, and its model
  listing says `"fake": true`.
- Every harness output carries `measurement`. It is false whenever a fake was involved: a fake provider in the
  routing, a ledger row with `fake_marker`, or a model listing that said fake. It is never inferred from the host
  name. A run with `measurement: false` is a rehearsal and is never quoted.

## 6. The schema subset

Replies are validated locally against the full schema, even when the wire carries a reduced one.

- **Allowed keywords:** `type`, `properties`, `required`, `additionalProperties`, `enum`, `const`, `pattern`,
  `minLength`, `maxLength`, `minimum`, `maximum`, `items`, `minItems` and `maxItems`. Anything else fails at compile
  time.
- **Objects:** every object sets `additionalProperties: false` and lists all its properties in `required`; an
  optional field is `[T, "null"]`.
- **Strict typing:** `integer` excludes `bool` and `3.0`; `number` excludes `bool`, NaN and Infinity; `enum` and
  `const` compare type-strictly.
- **Patterns:** matched with `re.fullmatch` under `re.ASCII`, so a trailing newline or a non-ASCII digit cannot
  satisfy `[0-9]`.
- **No values in problems:** problems name JSON paths and keywords from the schema only. An extra key is reported
  without its name.
- **Reduced transport schema:** `transport_schema: reduced` drops the seven range and length keywords from the wire
  for servers that reject them.

## 7. The guards (`tests/mycelic/test_collective_guards.py`)

| Guard | What it enforces |
|---|---|
| Import guard | `mycelic/{service,aggregation,store,transport,lineage}.py` import no model client and nothing from `mycelic.collective`. An AST check covers plain, relative and dynamic imports; a fresh-interpreter check confirms it. A missing core file fails loudly. |
| Stdlib only | All 20 collective modules import, and the four CLIs answer `--help`, under `python -S` |
| No model names | No model-family name in collective code, docs or tests. The matcher holds sha256 digests only. Example tags live only in `docs/collective/examples/`. |
| Determinism | No wall clock or unseeded randomness in `jsonio`, `schemacheck`, `stats` and the runtime modules |
| Runbook | Every RUNBOOK command runs with `--dry-run`, with the network blocked, and creates nothing |

Each later gate extends the lists at the top of that module.

## 8. Where G1 sits in the loop (STRATEGY section 4.1)

| Stage | What it needs | Status after G1 |
|---|---|---|
| Sense | records become typed claims and per-site counts; structured codes (no model) and in-boundary extraction | **Runtime built** (boundary-bound model calls, schema validation, ledger); packs, extraction and counts are later gates |
| Detect | statistical detectors over counts; rules as a second channel | Later (no model is involved) |
| Decide | candidate decided at the lowest unit spanning the evidence | Exists in the fabric for rule conclusions; candidates are later |
| Verify (pushdown) | narrow questions answered by each site's in-boundary model from its own records | Later; it will call `Runtime.run` at each site, where the guard keeps raw text inside |
| Follow up | approval-routed T0/T1 tasks | Later |
| Check the outcome | did the failure mode recur | Later |

No stage "learns"; nothing in G1 claims learning.

## 9. Routing defaults as policy (STRATEGY section 5.6)

No routing file ships. Each site writes its own (`examples/routing.example.json` is a template, not a default). The
policy a site's file should express, until a measurement says otherwise:

- **Extract claims from one record:** a model inside the boundary, sized to the hardware. Escalate after a
  schema-invalid reply survives one repair (`escalate_to`, also inside the boundary). Gate: E1.
- **Canonicalise entities:** deterministic code (patterns, alias tables). Never a generative model by default.
- **Detect cross-site patterns:** no model.
- **Rank candidates:** a logistic over explicit features. A model reads raw text only at the evidence holder
  (pushdown). Gate: E2.
- **Verify downward:** the holder's in-boundary model over its own records, escalating to a larger in-boundary model.
  Gate: E2.
- **Draft a follow-up:** an in-boundary model, or a larger model on structured claims only (a `structured` task,
  which the guard lets leave the site). Gate: X4.

## 10. What G1 does not do

Packs, extraction, edge stores, detectors, verification, follow-up and the demo are gates G2 to G8. The E1 and E2
harnesses come later. They will use `Runtime.single` and `endpoint=`, as E3 does.
