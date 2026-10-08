# Mycelic collective: architecture (gates G1 to G8)

This document describes what gates G1 to G8 build under `mycelic/collective/` (and, for G8, `demo/collective/`). It
also places them in the loop that later gates complete (STRATEGY section 4.1). Sections 1 to 10 describe G1; section
11 describes G2 (domain packs and the sense step); section 12 describes G3 (the site boundary and the G0
text-leakage scan); section 13 describes G4 (detection at HQ over the cells that left the sites); section 14
describes G5 (measuring that detection on planted synthetic worlds against its baselines, and the openFDA public
replay); section 15 describes G6 (pushdown verification: narrow questions answered inside each site, bucketed
verdicts, the commit gate, and the E2 harness); section 16 describes G7 (approval-routed follow-up on supported
conclusions, built ahead of E2 and X4 and unvalidated); section 17 describes G8 (the end-to-end demo for a fictional
multi-site device maker: the run-file contract, the screen with per-number provenance, the console and the number
lint); section 18 describes B3 (X5: what the artifacts that leave reveal beyond text, measured on synthetic worlds by
a red team that holds only what HQ holds).

**No real-model number is produced in this sandbox.** Model weights and the openFDA API cannot be reached from it, so
every test runs against a deterministic in-process fake or a local fake HTTP server. Every harness output says so in
its `measurement` flag. The figures STRATEGY needs come from the founder's runs (`RUNBOOK.md`).

## 1. What G1 builds

| Part | Module | Purpose |
|---|---|---|
| Strict JSON | `jsonio.py` | Refuses byte-order marks, invalid UTF-8, NaN/Infinity (and `1e999`), duplicate keys and deep nesting; canonical serialisation for hashes, ledgers and run files |
| Schema subset | `schemacheck.py` | Local validation of every model reply; problems are `(json_path, keyword)` and never hold a value |
| Statistics | `stats.py` | Percentile, Wilson interval, exact sign test, paired bootstrap (of a mean and of a micro F1); seeded only |
| Model runtime | `inference/runtime.py` | The only way collective code calls a model; bound to one boundary; repair once, escalate at most once |
| HTTP client | `inference/client.py` | OpenAI-compatible chat over `http.client`; a deadline on every receive (status line, headers and body), size cap, no redirects, no stored text |
| Routing | `inference/routing.py` | Routing file: endpoints with their boundary, routes with optional single-hop escalation; no defaults ship |
| Tasks and prompts | `inference/tasks.py` | Task specs, the `<data>` block, the repair message |
| Reply parsing | `inference/jsonparse.py` | One JSON object out of a reply (reasoning blocks, fences, prose), with a depth cap |
| Usage ledger | `inference/ledger.py` | One JSON line per logical attempt; numbers and labels only |
| Fakes | `inference/fake.py`, `inference/fakeserver.py` | Deterministic in-process provider; local OpenAI-compatible server with 34 personas (audit round 2 added `thinks-by-default` and streamed thinking) |
| Founder tools | `experiments/e3_latency.py`, `connectors/openfda.py`, `experiments/n1_narratives.py` | E3, the openFDA cache, N1 sample and score |

Everything is standard library only and runs under `python -S`. No fabric file changed (`INTEGRATION.md`). After
G8 the layer has 60 modules on the stdlib-only list and twenty CLIs, plus the demo's two scripts (section 7; G4 added
no CLI, G5 added six, G6 one, G7 two, G8 adds `runfiles` and no module CLI); sections 11 to 17 list what G2 to G8
added.

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
are accepted; `simulation=True` needs `data_label` `synthetic` (audit round 2). Both exemptions rest on that label,
and the runtime sees payloads, not where their records came from, so a caller that sends records checks them first:
`Runtime.exemption(task)` names the label the records must carry when the runtime is simulated or the task's route
(primary or escalation) leaves the boundary as `simulated` or `external_raw_exempt`, and
`edge.records.exempt_records_problem` checks it (`synthetic`: every record synthetic; `public`: the site is the public
source, `public`, as E1 requires). Model extraction (section 12.2) and the site judge (section 15.4) refuse before
any call when a record does not carry it, and E2's central_raw refuses a record that is not synthetic. Up to round 2
only a simulated runtime's extraction checked, so the judge, and extraction under `allow_external_raw`, could send
real narratives off-site with ledger rows that called them synthetic. The guard runs before any I/O, on the primary
endpoint and then on the escalation endpoint. A refusal does three things:

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
| 2 | primary | the same two messages, the repair appended to the user turn (`### REPAIR`, problems as `path: keyword`, a 300-character excerpt of the bad reply as data, a truncation hint when `finish_reason` was `length`) | attempt 1 was `json_invalid` or `schema_invalid` |
| 3 | `escalate_to` | the attempt-1 messages only: no repair marker, no excerpt | attempt 2 was also `json_invalid` or `schema_invalid`, and the route names `escalate_to` (no `endpoint=` override) |

The repair is part of the one user turn (`tasks.with_repair`), never a second user message: chat templates that
require roles to alternate (user, assistant, user, ...) refuse `[system, user, user]` with a 400, which would turn
every repair on such a server into an `http_4xx`. The fake server refuses non-alternating roles the same way, so a
test of the repair path fails if the turn is ever split again.

**Escalation matrix.** Only `json_invalid` and `schema_invalid` lead to a repair or an escalation. These never reach
the escalation endpoint:

- `http_4xx`: a 400 for `json_schema` or for strict keywords, 401 or 403, or 429 after its retries;
- `http_5xx` after its retries;
- `timeout`, `network` and `too_large`;
- `boundary`, configuration errors and `no_handler`.

Transport retries (429, 5xx and connection failures, never timeouts or TLS-verification failures) happen inside one
logical attempt. They are counted in that attempt's row, not written as rows of their own.

**Thinking (audit round 2).** Current servers stream a hybrid-thinking model's thinking as deltas whose `content` is
empty: llama-server names the field `reasoning_content`, Ollama and vLLM `reasoning`. The streaming client counts a
delta with either field as a token for TTFT and counts them as `reasoning_chunks` (on `ChatResult` and `Attempt`,
not in the ledger); up to round 2 only `reasoning_content` counted, so against Ollama or vLLM TTFT waited for the end
of the thinking. Thinking also counts against `max_tokens` and the JSON format applies only after it, so a server's
default thinking can spend a task's whole budget: the reply is empty, `finish_reason` `length`, and the runtime
records `json_invalid`. A routing endpoint may therefore send `reasoning_effort` and `chat_template_kwargs`
(`routing.py`; nothing is sent unless the file names it), and E1 pins both in its prereg.

Errors are `InferenceError(task, endpoint, kind, http_status)`. Each has exactly those four values and no other
argument. Every raise uses `from None`, so no reason phrase, server body or socket text reaches a message, a
traceback or a log. Logging is at most one WARNING per failed attempt, naming the task, endpoint, attempt, kind and
status.

## 4. The usage ledger

Each runtime appends one canonical JSON line per logical attempt to its own ledger file. The row has exactly 28 keys:

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
| `proxy` | the request went through the environment's HTTP proxy (audit round 3). Only an endpoint that may use one does: an `external` endpoint by default, any other only with `"env_proxy": true` in the routing file; an endpoint inside a site's or HQ's boundary is otherwise connected to directly whatever `http_proxy` says, since its requests carry the data the boundary keeps in |
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
site. G3 sends it windowed and k-suppressed rather than whole (section 12.6); since audit round 2 a site sends only its
extraction task's groups, with complementary suppression that also covers the missing-token counts, token sums and
latency (LEAKAGE section 2).

## 5. Fakes and the measurement flag

- `FakeProvider` is in-process and deterministic. Each task has a handler; `fail_next` scripts the next replies to
  exercise repair and failure paths; it opens no socket. Its rows are priced `fake`.
- `FakeOpenAIServer` listens on 127.0.0.1 and implements 34 personas (`thinks-by-default` since audit round 2): valid, slow, trickling the status line, the
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
| Stdlib only | All 60 collective modules import (G8 adds `runfiles`), and the twenty CLIs (G2 adds the five E1 subcommands and `packs.loader check`; G3 adds `experiments.g0_canary`; G4 adds none; G5 adds `evaluate.harness` `prereg`, `check-plant` and `run` and `experiments.openfda_replay` `prereg`, `signals` and `score`; G6 adds `experiments.e2_pushdown run`; G7 adds `experiments.e5_injection` and `followup.ledger verify`) answer `--help`, under `python -S`; so do the demo's two scripts, `demo/collective/collective_demo.py` and `lint_numbers.py` (G8), and `demo/collective/{scenario,screen}.py` import there |
| No model names | No model-family name in collective code, docs or tests. The matcher holds sha256 digests only. Example tags live only in `docs/collective/examples/`. |
| Determinism | No wall clock or unseeded randomness in `jsonio`, `schemacheck`, `stats`, the runtime modules, the pack modules, `edge/{extract,weeks,records,egress,site,verify}.py`, `leakage.py`, every `detect/*.py` and every `pushdown/*.py` (`ClockEntropyTests` checks by glob that each detect and pushdown module is listed), every `evaluate/*.py`, `experiments/openfda_replay.py`, `experiments/e2_pushdown.py`, every `followup/*.py` and `edge/packets.py` (`ClockEntropyTests` checks the follow-up modules by glob too; the harnesses stamp `created_at` through `common.utc_clock` and time with `time.perf_counter`, both allowed), `runfiles.py` and the demo's `scenario.py`, `screen.py` and `lint_numbers.py` (G8) |
| Domain literals (G2, G8) | No pack term (entity type, predicate, code, rule, template, follow-up type or role id of any built-in pack) is an identifier or a whole string constant in generic collective code or in any `demo/collective/*.py` (G8: every domain value of the demo comes from `scenario.json` or the pack), no string constant there contains `ILL-`, and no openFDA field name is a string constant in the pack, extraction or E1 code (one documented exemption: `text`, the payload key the brief fixes) |
| Runbook | Every RUNBOOK command runs with `--dry-run`, with the network blocked, and creates nothing |
| HQ imports (G4, G6) | No `detect/*.py` and no `pushdown/*.py` imports `edge.records`, `edge.site`, `edge.extract`, `edge.verify` (the site verifier, G6), `packs.generator`, `evaluate`, `leakage`, `experiments`, any `inference` module or a model client (AST check with relative imports resolved); a fresh interpreter importing every detect and pushdown module loads none of them except `inference` and `inference.errors`, which the Boundary's validator pulls in. `test_collective_pushdown.py::PushdownImportGuardTests` repeats it for pushdown and pins `detect/{detectors,rules,org}.py` to their G5 bytes |
| Evaluation imports (G5) | No `evaluate/*.py` and not `experiments/openfda_replay.py` imports any `mycelic.collective.inference` module or a model client (`EvaluateImportGuardTests`, the same AST check; `edge.site` pulls in `inference.ledger` transitively, which is allowed). G5 makes no model call: X uses the lexical extractor |
| Follow-up imports (G7) | No `followup/*.py` imports `edge.records`, `edge.site`, `edge.extract`, `edge.verify`, `edge.packets`, `packs.generator`, `evaluate`, `leakage`, `experiments` or a model client; only `followup/drafts.py` imports inference (`tasks` and `errors` at run time, the runtime only for type checking); `edge/packets.py` imports no `followup`, `pushdown`, `detect` or inference module (`FollowupImportGuardTests`: the AST checks and two fresh interpreters) |
| Approval call sites (G7) | Outside `followup/service.py`, `experiments/g0_canary.py` (G0's simulated owner), `demo/collective/collective_demo.py` (the G8 console, whose real call the checker must see) and `tests/`, nothing under `mycelic/` or `demo/` calls `approve`, `edit` or `reject`, as a name, an attribute call or `getattr` with that name (`ApprovalCallSiteTests`) |
| Demo readers (G8) | `demo/collective/screen.py` and `lint_numbers.py` import no `collective_demo`, no `inference`, `edge`, `detect`, `pushdown` or `followup` module and no model client (`DemoImportGuardTests`: the AST check, an injected import in a copy of the lint, and a fresh interpreter); they read run files only, so neither a replayed screen nor the lint can compute a number of its own |

Each later gate extends the lists at the top of that module.

## 8. Where G1 sits in the loop (STRATEGY section 4.1)

| Stage | What it needs | Status after G6 |
|---|---|---|
| Sense | records become typed claims and per-site counts; structured codes (no model) and in-boundary extraction | **G1:** runtime (boundary-bound model calls, schema validation, ledger). **G2:** packs, the canonicaliser, the record connector, claim extraction from codes (S) and narratives (X), and the E1 harness. **G3:** each site's own record store, k-suppressed weekly count cells and windowed usage summaries that leave only through the Boundary, and the G0 text-leakage scan. The HQ counts store is G4 |
| Detect | statistical detectors over counts; rules as a second channel | **G4:** HQ's collective store of immutable cells, the model-free detectors D2 to D7 over the k-suppressed weekly cells, the rules channel over the same cells, run X (codes and text-derived cells) and baseline S (codes only); section 13. **G5 measures it** without changing it: planted patterns and decoys, the baselines S, R (model-free), U and single-site, the pre-registered X1/X2 harness and scorecard, and the openFDA replay; section 14 |
| Decide | candidate decided at the lowest unit spanning the evidence | **G4:** each candidate carries the decision unit of its supporting sites (their lowest common ancestor in the current org config); the fabric's rule conclusions are unchanged |
| Verify (pushdown) | narrow questions answered by each site's in-boundary model from its own records | **G6:** structured questions from pack templates routed to the contributing sites and up to two siblings; each site answers from its own raw records with its in-boundary model through `Runtime.run` (the boundary guard keeps raw text inside) or the lexical judge, and sends a bucketed verdict through the same Boundary as its cells; the commit gate ported from vr034p assigns `supported`, `hypothesis`, `contested`, `stale` or `rejected` and HQ keeps versioned conclusions with their lineage; E2 measures it against central reading; section 15 |
| Follow up | approval-routed T0/T1 tasks | **G7, built ahead of E2 and X4 and unvalidated:** allow-listed proposals from supported conclusions only, a named owner, approval scoped to the current approvers file, at-most-once execution, caps, the kill switch, escalation and a hash-chained ledger; T0 packets assembled inside the sites, T1 drafts at HQ from structured inputs only; T2 off, T3 not an action type; section 16 |
| Check the outcome | did the failure mode recur | **G7:** a measurement only (not causal, no counterfactual) of the key's cells in a post window against the baseline; section 16.10 |

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

G1 built no pack and no extractor; G2 adds them (section 11). Edge stores, counts, detectors, verification,
follow-up and the demo are later gates, and the E2 harness comes later. E1 (G2) calls the model through
`Runtime.run` with an `endpoint=` override, so the repair rule is the one production extraction uses.

## 11. G2: packs and the sense step

G2 makes generality a matter of data plus a small amount of generic code, and builds the sense step with the
experiment that measures it (E1). **No real-model number is produced here either**: E1 was rehearsed against local
fake servers, and its outputs say `measurement: false`.

| Part | Module | Purpose |
|---|---|---|
| Pack loader | `packs/loader.py` | Strict-JSON pack directory to a frozen `FrozenPack` with four sha256 hashes; every failure a `PackError(file, path, problem)`; `python -m mycelic.collective.packs.loader check` |
| Canonicaliser | `packs/canonical.py` | Folds, id formats compiled to ASCII patterns, deterministic resolution of entity mentions (never a model), sentence spans |
| Connector | `packs/connector.py` | Vendor rows (and openFDA events) to internal records with exactly the record keys; per-reason rejections |
| Generator | `packs/generator.py` | A seeded synthetic world (records, gold claims, master data) from a pack's world spec |
| Built-in packs | `packs/data/device_quality`, `packs/data/claims_integrity` | An illustrative device-quality pack and a fictional claims-integrity pack, both same-author (`PACKS.md`) |
| Extraction | `edge/extract.py` | The codes channel, the lexical and model extractors, post-processing, pairing into claims |
| E1 | `experiments/e1_extract.py` | prepare, label-check, prereg, run, compare; pinned values, paired statistics |
| Additions to G1 | `stats.py`, `inference/fakeserver.py` | `f1_from_counts` and `bootstrap_f1`; the fake server's `responder` hook and `request_payload` |

Import graph (no cycles): `packs` imports neither `edge` nor `experiments`; `edge` imports `packs.canonical`, the
inference errors and `TaskSpec` (the runtime and the pack type only for type hints); only `experiments` imports
`experiments`; the fabric core imports none of this (the import guard).

### 11.1 Two channels

| Channel | Source | Model | Claims |
|---|---|---|---|
| **S** (codes) | structured codes and structured entity fields | none | structured entities x code predicates |
| **X** = S + `text_only` | S plus the narrative, read inside the site's boundary | lexical, or a model behind the runtime with deterministic post-processing | S's claims plus the pairs only the narrative supports |

`codes_channel` maps each code to its predicate (unknown codes are counted) and resolves each structured value
with `resolve_exact` (failures are counted); the primary entity is the first resolved value of the mapping's
`primary_entity_type`. An extractor returns text claims `(entity, predicate, negated)` or entity-only mentions.

**Pairing** (`pair`, amendment A2). For a record with structured entities E_s, code predicates P_c and text claims:

| Pair | Channel | Example (codes ILL-0101 crack; product SD-9, lot L10001; "Battery door cracked. No leak observed.") |
|---|---|---|
| E_s x P_c | `codes` | (SD-9, crack), (L10001, crack) |
| the extractor's own non-negated pairs | `text_only` unless already a codes pair | (BATTERY-DOOR, crack) |
| non-negated text predicates x E_s | `text_only` unless already a codes pair | (SD-9, crack) is already a codes pair |
| P_c x entities named only in the text | `text_only` | (BATTERY-DOOR, crack) |
| a pair the text states only as negated | never output | (SD-9, leak) is dropped |

There is no record-level cross product of text predicates with every text entity, so a predicate is not attached to
an unrelated component named elsewhere in the record. Each (entity, predicate) appears once; `res_conf` is the
best evidence for the entity (text or structured). S is `pair(codes, None)`; X is `pair(codes, text)`.

### 11.2 The canonicaliser

Rules, in order: full-width fold; separators at separator positions (hyphen, underscore, every hyphen-like
character, space or NBSP, the type's separator); zero-width deletion; ASCII-only case (so `ß`, `İ` and `ı` are
never folded); leftmost-longest matches bounded by non-alphanumerics and never followed by a hard separator plus an
alphanumeric (`SD-9-B` is rejected, `SD-90` is never `SD-9`); a lookalike with a non-ASCII letter or digit is
unresolved and counted (`homoglyph`, `non_ascii_digit`); a space-separated form resolves only to an id the site
knows (alias targets plus its master data), else it is counted as `space_unknown`; then alias phrases. Every span
indexes the original text. Exact matches carry confidence 1.0, aliases 0.95 and variants 0.9 in every built-in pack.

### 11.3 Extraction and its post-processing

The model receives exactly `{language, text}`: the narrative cut at a whitespace boundary to the pack's input cap.
Persons, the reporter, the record ref and structured values are never sent. A reply item is dropped, in order, as
`empty` (all null), `not_canonical` (only one of entity type and text), `ungrounded` (its text is not in what was
sent), `person_value` (its text is a person value or the reporter), `not_canonical` (it does not resolve),
`ungrounded` again (the canonicaliser does not read that id anywhere the text occurs: a model that trims `SD-9-B` to
`SD-9` names an id the scanner rejects outright, so it is dropped, exactly as the lexical channel and label-check
find nothing there), `no_entity` (a predicate with no entity and no primary) or `duplicate`. A kept entity's
`res_conf` is that of the text's own occurrence, not of the model's spelling. An out-of-enum type or predicate is a
schema failure (one repair, then the lexical fallback, or an empty result in E1), not a drop. A boundary refusal
propagates. The fake provider's `lexical_handler` reproduces the lexical extractor's claims byte for byte, which
the tests check on every fixture of every built-in pack.

The lexical extractor pairs per distinct entity and distinct (predicate, negated) in a sentence, carrying their
multiplicities, so its work is linear in the narrative with a factor the pack bounds (at most two per predicate) and
its duplicate counts equal those of pairing every mention. The remaining cost is the output itself: one sentence
that names thousands of distinct ids next to every predicate yields that many claims (a 200,000-character worst
case for `device_quality` gives about 371,000 claims in about 1.2 s). The handler stops at `max_claims` items.

### 11.4 E1

| Step | Writes | Refuses |
|---|---|---|
| `prepare` | `runs/e1/<id>/records.jsonl`, `sheet.csv`, `prepare.json` (seeded sample; shortfall recorded) | a missing mapping, an unreadable source |
| `label-check` | `labels.jsonl` (canonical, sorted) | unlabelled cells, unknown types or predicates, ids that do not canonicalise, a predicate-only item without a primary entity, duplicate or missing rows |
| `prereg` | `runs/e1/<id>/prereg.json` | fewer than 3 runs or 2 endpoints, a reference not evaluated, the fake provider, raw records to an external endpoint without a matching public or synthetic exemption, a data label the records contradict (`public` needs records from a public source, site `public`), dirty code without `--allow-dirty` |
| `run` | `run.json` (with the sha256 of `predictions.jsonl` and `ledger.jsonl`), `predictions.jsonl`, `ledger.jsonl` | a malformed prereg, any change to the labels, the vocabulary hash, the scoring code hash or a pinned endpoint setting, a data label the labels contradict |
| `compare` | `runs/e1/<id>/e1.json` | a tampered prereg, predictions or ledger that differ from their run's stamped sha256, a run that never finished writing `run.json`, duplicate or missing repeats, a pre-registered endpoint without runs or incomplete runs without `--allow-incomplete` (stamped; e1.json lists the endpoints without runs), a changed reference or margin |

The scored output is the extractor's text claims (A3). Field-level micro F1 is primary; claim, entity and predicate
F1, JSON validity and lot and supplier exact match (the share of records matched in every run, with a Wilson
interval over records) are secondary. Intervals bootstrap records. The paired comparison, and the non-inferiority
verdict, is on the primary metric: the candidate's micro field F1 minus the reference's over the shared records, with
a paired percentile bootstrap over records (`stats.paired_bootstrap_f1`); the per-record mean field F1 difference and
an exact sign test are reported beside it as secondary (a claim-free record scores 1.0 on both sides there, so it
dilutes that difference; audit round 3 moved the verdict off it). With `measurement: false` the verdicts are
withheld (null).

### 11.5 B4: a third built-in pack, as data only

B4 added `it_incidents` (multi-site IT operations incidents at a fictional group; `PACKS.md` section 3) as a pack
directory and nothing else: **no file under `mycelic/` or `demo/` outside `mycelic/collective/packs/data/it_incidents/`
changed or was added**, so every code hash (X1, E1, E2, the openFDA replay, X5) is unchanged and the two earlier packs'
hashes are too. What the data could not express is recorded, not fixed, as generality gaps in
`docs/collective/x3/effort.json` (none blocked the loader, G0, the X1 smoke or the demo): an id separator other than
`-` or `/`, two-hop links, sub-day timestamps, record fields such as priority or a change window, locales as
languages, a varying generic-code rate or reporter storm in the background world, per-site id universes, and the
lexical extractor's contiguous terms, token-window negation, sentence-level pairing and sentence split. It is the
first built-in pack with an entity type that never leaves a site (`config_item`, `egress` false): its claims stay in
the site store and `build_cells` counts them as `non_egress_type`. Same author as the generic code: internal evidence
that a new field can be configuration, not X3.

## 12. G3: the site boundary

G3 stores each site's raw records and claims inside the site and lets exactly two artifact types out, both through
one `Boundary`: weekly count cells and usage summaries, each structurally closed, k-suppressed and never revised.
G0 (`LEAKAGE.md`) plants canaries and scans every byte that crossed. **Scope: text only.** Nothing in the fabric
changes, and no real-model number is produced: every run here is synthetic, with a fake model or none.

| Part | Module | Purpose |
|---|---|---|
| Weeks | `edge/weeks.py` | ISO weeks (`YYYY-Www`, 52 or 53 a year), closed weeks, the recorded local date of a timestamp |
| Record store | `edge/records.py` | One SQLite file per site (WAL); records, claims, extraction stats, late records and the emission log; the only SQL inside a site (HQ's is `detect/store.py`, section 13) |
| Boundary | `edge/egress.py` | The only path out of a site: a closed spec per artifact type, cross-field checks, the `after` sequence, two append-only logs |
| EdgeSite | `edge/site.py` | Ingest, extract, `emit_cells`, `emit_usage`; `build_cells` |
| Leakage | `leakage.py` | Canary planting, the manifest, the scan |
| G0 runner | `experiments/g0_canary.py` | `python -m mycelic.collective.experiments.g0_canary`; writes `leakage.json` |
| Addition to G1 | `inference/ledger.py` | `summarise(rows)`, the reduction `usage_summary(path)` now calls |

Import graph (no cycles): `weeks` imports only `packs.connector`; `records` imports `weeks`, `jsonio` and
`packs.canonical`; `egress` imports `weeks`, `jsonio`, `packs.connector` and the inference error kinds; `site`
imports `records`, `egress`, `weeks`, `extract`, `packs.canonical`, `packs.connector` and the ledger (the runtime and
the pack type only for type hints); `leakage` imports `jsonio`, `packs.canonical`, `packs.loader` and
`egress.schema_words`; only `g0_canary` imports `experiments.common`.

### 12.1 Data flow

```
 SITE <id> (inside its boundary)                                        HQ (simulated in G0)
 +-------------------------------------------------------------+
 | records ---ingest---> site-<id>.sqlite3                      |
 |   (date check,        records | claims | extraction_stats    |
 |    record check,      late_records | emitted_weeks           |
 |    own site only)        |   ^                              |
 |                  extract |   | claims (valid only)          |
 |    lexical or model -----+---+   model calls -> ledger.jsonl |
 |    (runtime bound to site:<id>)          (never leaves)      |
 |                                                              |
 | emit_cells / emit_usage: closed weeks, k-suppressed,         |
 |   stored in emitted_weeks (exact bytes) before sending       |
 |                          |                                   |
 |                     Boundary.send ---------------------------+--> hq/receive.jsonl   (1st append)
 |                          +--> site-<id>.egress.jsonl         |    (2nd append, same line)
 +-------------------------------------------------------------+
```

### 12.2 The site store (`site-<id>.sqlite3`)

Opened with `isolation_level=None`, `journal_mode=WAL` (refused when the file system cannot do WAL),
`synchronous=FULL`, foreign keys on and a 5 s busy timeout. Every write runs in one `BEGIN IMMEDIATE ... COMMIT`
and is rolled back on any exception; every `SELECT` carries `ORDER BY`. No table has a fabric table name.

| Table | Columns |
|---|---|
| `site_info` | `key`, `value`: `schema_version`, `site_id`, `pack_id`, `config_hash`; reopening with another value raises `StoreError('site_info mismatch: <key>')` |
| `records` | `record_ref` (key), `seq`, `received_date`, `iso_week`, `ingested_at`, `ingest_week`, `count_week`, `language`, `codes`, `structured`, `narrative`, `narrative_key`, `person`, `reporter_id`, `origin_ref`, `origin_site`, `root_ref`, `forwarded_in`, `synthetic` |
| `claims` | `record_ref`, `entity_type`, `entity_id`, `predicate` (together the key), `channel`, `extractor`, `res_conf` |
| `extraction_stats` | `record_ref` (key), `mode`, `extractor`, `error_kind`, `truncated`, `language_supported`, `drops`, `unresolved`, `unknown_codes`, `structured_unresolved`, `invalid_claims`, `extracted_at` |
| `late_records` | `record_ref` (key), `received_week`, `count_week`, `watermark`, `ingested_at` |
| `emitted_weeks` | `artifact_type`, `closed_through` (together the key), `as_of`, `after_week`, `ledger_rows`, `body` (the bytes sent), `sha256`, `cells`, `created_at`, `sent_at` |

Ingest: a known `record_ref` (stored, or earlier in the batch) is a duplicate and the first wins. `root_ref` is the
origin's ref when the record names one; else the root of the earliest record at this site with the same folded
narrative (`narrative_key`); else the record's own ref. An empty narrative has no key and never shares a root, and a
copy that reached another site without an origin marker counts as independent there (a documented limitation).
`forwarded_in` is 1 only when the origin is another site. A record whose received week is at or before the cells
watermark is late: it counts in `max(ingest week, the week after the watermark)` and gets a `late_records` row.

Extraction runs over records without stats, 100 per transaction; the first extraction of a record wins, except a
`not_sent` stand-in (below). A claim is
stored only when its type and predicate are the pack's, its id is canonical, its channel is `codes` or `text_only`
and its `res_conf` is one of the pack's three confidences; others are counted as `invalid_claims`. Model extraction
refuses to start while any pending record does not carry the label `Runtime.exemption` names (a simulated runtime, or
a route that leaves the site under an exemption; section 2); a boundary refusal propagates and saves nothing of the
batch. After two consecutive records whose call found the route's primary endpoint down (`timeout`, `network`,
`http_5xx`, after the client's retries), the breaker opens and records are sensed lexically without a call (extractor
`fallback`, error `not_sent`; audit round 2: every record used to wait out the full deadline before its fallback).
Audit round 3 made it recover: a failure on the escalation endpoint after the primary answered (its replies failed
validation) does not count (`extract.server_down`); while open, the breaker sends one record in the 1st, 3rd, 7th,
15th, ... batch after it opened, and an answer closes it, so a short outage costs at most about as many batches
again as it lasted and a dead server about 2 + log2(n / 100) deadlines per pass of n records; and a `not_sent`
stand-in counts as extracted (the cells can be emitted) but every later model pass sends the record again and
replaces it, until its count week is emitted (an emitted week is never revised). `ExtractSummary.resent` counts them.
Before, the breaker covered the whole backlog, never half-opened, counted a dead escalation server as the primary
being down, and stored the stand-ins as final, so a 3-second outage or two records slower than the deadline
downgraded the whole backlog to lexical for good.

### 12.3 The cell format

One cell per (entity type, entity id, predicate, ISO week, channel) with at least one of the site's own records:

```
{"channel":"text_only","entity_id":"ALARM-SPEAKER","entity_type":"component","iso_week":"2024-W02","n":3,
 "n_reporters":"<k","n_roots":3,"predicate":"detachment","res_conf_min":0.95}
{"channel":"codes","entity_id":"ALARM-SPEAKER","entity_type":"component","iso_week":"2024-W02","n":"<k",
 "n_reporters":"<k","n_roots":"<k","predicate":"alarm_failure"}
```

(Both are taken from a high-volume synthetic copy of `device_quality`, seed 7, site `plant-ashvale`; at the
pack's own volumes every cell looks like the second one, `LEAKAGE.md` section 1.)

- `n` counts distinct records, `n_roots` distinct roots and `n_reporters` distinct reporters (all unknown reporters
  are one shared reporter, so an unknown never inflates the count). Each is the int when it is at least k, else
  `'<k'`, independently. `res_conf_min` (the lowest resolution confidence) is present exactly when `n` is an int.
- Forwarded-in records are excluded; a record counts once per (entity, predicate), however many codes, structured
  values or mentions name the pair. The codes and text-only cells of a key count disjoint record sets.
- A row of a type outside `egress_entity_types` is counted as `non_egress_type`. With `require_master_data`, an id
  of a type with an id format outside the site's master data is counted as `not_master_data` (alias-only types
  always pass; a type without master data at the site drops all its ids).
- The bundle is `{schema_version, pack, config_hash, site, as_of, after, closed_through, k, cells}`, cells sorted by
  their key. It has no totals, no marginals, no forwarded count and no per-record rows; those stats stay at the site
  (`Emission.stats`).

### 12.4 The Boundary

`Boundary(pack, site_id, egress_log=..., receive_log=..., clock=..., tasks=..., endpoints=...)` is a site's only
way out (G6 adds the path in). `validate` and `send` check, in order, and raise the first problem as `EgressError`:

1. the direction (`out`) and the artifact type (`cells_bundle` or `usage_summary`);
2. canonical JSON (NaN, sets and other non-JSON values fail as `json`);
3. the closed structure: unknown keys first (reported at the parent object as `additionalProperties`, never named),
   then required keys in sorted order, then optional keys present; arrays by index. Counts are an int in
   [k, 10^9] or exactly `'<k'` (booleans, floats, `'3'` and `'<k '` fail); ids match a coarse ASCII pattern;
   enums compare type-strictly;
4. cross-field checks: `range` (`after < closed_through`, every cell week in `(after, closed_through]`),
   `id_format` (the type's canonical form, or a listed alias-only id), `consistency` (`res_conf_min` iff `n` is an
   int; `n_roots` and `n_reporters` are `'<k'` when `n` is, and never above `n`; no token or latency keys in a
   usage group whose `calls` is `'<k'`), `order` (cells, or usage groups, strictly ascending);
5. `sequence`: `after` equals the last `closed_through` sent for that type (None before the first), unless the body
   is byte-identical to the last one sent, which is an idempotent re-send.

An `EgressError` has `artifact_type`, `path` and `keyword` only; its `args` are empty and its text never holds a
value. A refused artifact writes nothing and `egress.py` logs nothing. `send` then appends one canonical line
`{artifact_type, body, bytes, direction, sha256, site, ts}` (with `ts` from the injected clock) to the HQ receive log
first and the site's egress log second, so after a crash between the two a re-send makes them agree; HQ may then hold
a duplicate line with the same `sha256`, which HQ (G4) drops. `read_log` refuses any line that is not exactly such a
row with a matching `sha256` and `bytes`.

### 12.5 Weeks

A week is closed at `as_of` when its Sunday plus `close_lag_days` is on or before `as_of`, so with a lag of 7 days
and `as_of` on a Wednesday, the current and previous weeks are still open. `emit_cells(as_of)` refuses an `as_of`
that is not a date, is later than the site clock or earlier than the last emission's; a repeated `as_of`, or one that
closes no new week, sends nothing. Before computing a new bundle it re-sends, byte for byte, any stored bundle a
failed send left unsent, and it refuses to emit while records counted in the window are not extracted. A window
without cells still sends a bundle: it advances the watermark. Received dates are read in the record's own calendar
(`2026-03-01T23:30:00-05:00` is 2026-03-01); a record dated in the future waits until its week closes.

### 12.6 Usage windowing

`emit_usage(as_of)` follows the same `as_of` rules and sends one `usage_summary` per newly closed span. It reads the
site's ledger (every row must belong to `site:<id>`), takes the rows after those already summarised (the
`ledger_rows` count of the last emission) up to the first row whose week is still open, and reduces the extraction
rows among them with `ledger.summarise` (audit round 2: the judge's rows are consumed but never summarised, since one
judge call per retrieved record made a week's judge calls the exact record counts behind its verdicts). Per group,
`calls` and each error kind are `'<k'` below k, `ok` and the missing-token counts may also be 0, and tokens and latency
percentiles are sent only when `calls` is at least k. Because `calls = ok + the error counts`, a group whose `calls`
is an int and that has any part below k (not 0) sends `ok`, every error kind and both missing-token counts as
`'suppressed'`, with no token sum and no latency (complementary suppression; the Boundary refuses a `'<k'` part beside
an int `calls`, and anything exact beside a withheld split). Since the review of audit round 2, the missing-token
counts, token sums and latency follow the parts: a transport failure carries no tokens, so an exact missing-token
count beside a withheld split gave the split back, and so would a token sum (divided by the prompt size) or the
interpolated p95 (moved by failures at the deadline). With no part withheld, a missing-token count is exact only when
within each part every row or none lacks the tokens (a sum of whole parts, which the Boundary checks), else
`'suppressed'` without its token sum (LEAKAGE section 2). Each ledger row is summarised exactly once. A site without a
runtime sends no usage.

## 13. G4: detection at HQ

G4 finds cross-site candidates that no rule was written for, using only the k-suppressed weekly cells that left the
sites through the G3 Boundary. It runs two channels over the same store: **X** reads `codes` and `text_only` cells;
**S**, the baseline, reads `codes` cells only, so nothing in an S run (history, eligibility, rules) depends on text.
The hand-written rules run as a second channel over the same cells and are merged by key. Every parameter comes from
the frozen pack (`detectors.json`, pinned by `detector_hash`). Detection uses no model, no clock and no entropy, and
its result is byte-identical under any `PYTHONHASHSEED`. **Nothing in G4 is a measurement:** every number its tests
print comes from synthetic, same-author data, and the sandbox timing in `INTEGRATION.md` is an engineering figure.

| Part | Module | Purpose |
|---|---|---|
| Org config | `detect/org.py` | Sites, their unit paths (2 to 5 segments, below the enterprise, none nested), country and display name; `org_hash`; the decision unit of a set of sites |
| Collective store | `detect/store.py` | HQ's own `collective.sqlite3`: immutable bundles and cells, rejections, the org log, saved runs; ingest of the receive log; the only SQL on the HQ side |
| Rules channel | `detect/rules.py` | A rule fires for an entity when enough sites count enough in the rule's own window; pure |
| Detectors | `detect/detectors.py` | `DetectorConfig`, the imputation, D2 to D7, the weekly walk, alerts and cooldown, the rules merge and the result JSON; `run_detection` (pure) and `detect(store, ...)` |
| Statistics | `stats.py` (extended) | `poisson_logsf`, `binom_logsf`, `poisson_binomial_logsf`, `smoothed_pmi`, `logistic`, each formula in the module docstring |
| Shared validator | `edge/egress.py` (additive) | `check_artifact` and `log_row_problem`, now public, so the Boundary and HQ run one validator |

Import graph (no cycles): `org` imports `mycelic.hierarchy`, `jsonio` and the connector's site-id pattern; `store`
imports `org`, `jsonio`, `edge.egress` and `edge.weeks` (the pack type only for type hints); `rules` imports no
collective module at run time; `detectors` imports `stats`, `store`, `org`, `rules`, `edge.weeks` and `jsonio`. No
detect module imports `edge.records`, `edge.site`, `edge.extract`, `packs.generator`, `leakage`, `experiments` or any
inference module (the guard in section 7).

### 13.1 Data flow

```
 sites (G3)                       HQ
 Boundary.send ---> hq/receive.jsonl
                         |
                         v  CollectiveStore.ingest_log: each line checked (log_row_problem), each cells_bundle
                         |  checked in a fixed order (duplicate, invalid, config_hash, unknown_site, the Boundary's
                         |  own validator, site_mismatch, conflict, sequence); usage summaries are counted, not kept
                         v
                 collective.sqlite3: bundles + cells (immutable) | rejections | org_sites + org_log
                         |
                         v  detection_inputs(as_of, X|S): bundles and cells with as_of <= D, weeks <= closed_through(D),
                         |  the run's channels, the current org's sites
                         v
                 run_detection: one pass builds prefix arrays; the weekly walk computes D2 to D7, the rules, the
                         |       alert budget and cooldown
                         v
                 result JSON: weeks[], alerts[], candidates[] (snapshot and/or rule part, lineage), rule_hits[]
                         |
                         v  save_run (idempotent): detection_runs, candidates, rule_hits
```

### 13.2 The collective store

Opened like the site store (`isolation_level=None`, WAL, so `':memory:'` is refused, `synchronous=FULL`, foreign
keys on, 5 s busy timeout); every write is one `BEGIN IMMEDIATE ... COMMIT`, every `SELECT` carries `ORDER BY`, and
timestamps come only from the injected clock. One writer per store.

| Table | Columns |
|---|---|
| `store_info` | `key`, `value`: `schema_version`, `pack_id`, `config_hash`, `enterprise`; reopening with another value raises `StoreError('store_info mismatch: <key>')` |
| `org_sites` | `site_id` (key), `unit_path`, `country`, `display_name`: the org config the store was last opened with |
| `org_log` | `seq`, `site_id`, `old_unit_path`, `new_unit_path`, `org_hash`, `at`: one row per added, moved or removed site |
| `bundles` | `sha256` (key), `seq`, `site`, `config_hash`, `as_of`, `after_week`, `closed_through`, `cells`, `received_at` |
| `cells` | `site`, `entity_type`, `entity_id`, `predicate`, `iso_week`, `channel` (together the key), `n`, `n_roots`, `n_reporters` (NULL is `'<k'`), `res_conf_min` (NULL is absent), `as_of`, `bundle` |
| `rejections` | `seq`, `sha256`, `site` (only a valid site id), `reason`, `path`, `keyword`, `line`, `received_at`; unique on `(sha256, reason)` |
| `detection_runs` | `run_id` (key), `run_channel`, `as_of`, `last_week`, `tie_salt`, `config_hash`, `detector_hash`, `org_hash`, `result_sha256`, `saved_at` |
| `candidates` | `run_id`, `key`, `first_candidate_week`, `detection_week`, `body` (canonical bytes) |
| `rule_hits` | `run_id`, `rule_id`, `key`, `first_week`, `body` (canonical bytes) |

Triggers abort any `UPDATE` or `DELETE` on `cells` and `bundles`; `store.py` has no such statement. No table has a
fabric table name. `cells` is the counts table of STRATEGY section 4.5.

**Ingest order and reasons** (`ingest_bundle(body, row_site=None)`), first failing check decides:

| Step | Check | Result |
|---|---|---|
| 0 | the body's sha256 is already stored | `duplicate`, nothing written |
| 1 | the body is not an object | `invalid` (`$`, `type`) |
| 2 | a str `config_hash` other than the pack's (a missing one falls through to step 4) | `config_hash` |
| 3 | a str `site` outside the current org | `unknown_site` |
| 4 | `edge.egress.check_artifact` finds a problem (structure, counts, ids, ranges, order; an extra key such as a marginal is `additionalProperties`) | `invalid`, with its path and keyword |
| 5 | the log row names another site than the body | `site_mismatch` |
| 6 | a stored cell with the same key and other `(n, n_roots, n_reporters, res_conf_min)`, compared NULL-safe | `conflict` |
| 7 | `after` other than the site's last accepted `closed_through` (None before the first), or `as_of` before the site's last accepted one | `sequence` |

A rejection is one `rejections` row and never a value from the body. A bundle refused for `sequence` is accepted by a
later ingest once its predecessor has arrived. `ingest_log` adds `bad_line` (a line that is not strict JSON or not a
well-formed log row, recorded with the sha256 of the raw line and its 1-based number) and counts `usage_summary` rows
as ignored; re-ingesting a file writes nothing new. **Org sync** on open compares `org_sites` with the config passed
in: an added site logs `(NULL, path)`, a moved one `(old, new)`, a removed one `(old, NULL)`; a changed country or
display name is updated without a log row; an identical org writes nothing. Detection always uses the config passed
in, and cells of sites no longer in it are not read.

### 13.3 The walk and the visibility model

A run at `as_of` D reads only bundles with `as_of <= D` and cells of weeks up to `last_week = closed_through(D,
close_lag_days)`; anything else handed to `run_detection` is counted in `ignored_cells` (`after_last_week`,
`invisible_bundle`, `not_in_org`, `other_channel`). The walk covers the contiguous ISO weeks (built with `next_week`,
so week 53 and year ends are handled) from the first week with a used cell to `last_week`. Step W uses every loaded
cell with a week at or before W and nothing later.

- **Reported.** A site has reported W when the largest `closed_through` among its loaded bundles is at or after W.
  This is the *on-time-reporting assumption*: a bundle that reached HQ after W closed is still used at step W, which
  is what makes a historical backfill (one bundle covering two years) testable. Lead times are only meaningful on
  worlds whose sites emit every week (G5), and the alerts a deployment actually raised are those each saved run holds.
- **History.** `history_weeks(s, W) = max(0, idx(W) - window_weeks - idx(site_start) + 1)`, where `site_start` is the
  site's first week with a used cell in the run's channels (for S, codes only). A site is `late` (not reported),
  `short_history` (reported, fewer than `min_history_weeks`) or `eligible`. A step with no eligible site is
  `insufficient_baseline`: D2 and D3 are skipped there, rules still run.
- **Windows.** `Window(W)` is the `window_weeks` weeks ending at W; `Baseline(W)` the `baseline_weeks` weeks ending
  at `W - window_weeks`; indices are clipped at 0. The step's own `as_of_W = min(D, sunday(W) + close_lag_days + 6
  days)` is used only for staleness and is reported per week.

### 13.4 Imputation

`bounds(v, k)` is `(v, v)` for an int count and `(1, k - 1)` for `'<k'`; an absent cell is `(0, 0)`. In run X a
series' week at a site adds the bounds of its `codes` and `text_only` cells (two `'<k'` cells give `(2, 2k - 2)`).
Each use takes the side that makes a detector less likely to fire:

| Use | Side taken for `'<k'` |
|---|---|
| D2 window count c (evidence) | lower bound |
| D2 baseline rate (null hypothesis) | upper bound |
| D2 past exceedance for `p_s` (A1) | window upper bound, baseline lower bound ("possible") |
| D3 window PMI: the key's own count `n_ep` | lower bound |
| D3 baseline PMI: the key's own count `n_ep` | upper bound |
| D3 nuisance counts (the entity's other predicates, the predicate's other entities, the rest of the type), window and baseline | one shared imputation: an int as is, `'<k'` as `k / 2` |
| D3 support (`n_ep >= k`) | lower bound |
| Rule count per site | lower bound |
| D5 independent roots | lower bound of `n_roots` |
| D5 `root_ratio_ub` | numerator: per cell `min(ub n_roots, ub n)`; denominator: lower bound of `n`; capped at 1.0 |
| D6 few reporters | an int `n` with `n_reporters` `'<k'` (at most k-1 reporters; it cannot tell 1 from k-1) |
| D4 `res_conf` | lowest `res_conf_min` over cells with an int `n`; null when there is none |

D3 does not take opposite bounds for its marginals. Up to G8 it did (window marginals at the upper bound, baseline
marginals at the lower bound), which on all-`'<k'` cells cost every key a large negative rise before any change in the
data: for a key alone in its type, `-4 log(k - 1)` before smoothing (about -2.8 at k = 3), which a real rise had to
overcome on top of `pmi_delta`. The nuisance cells are not evidence for or against the key: taking them at one
imputation on both sides lets them shift both PMIs alike, and the key's own count still takes its conservative side. A
steady key beside steady suppressed background does not rise (a test pins it); the price is that a rise is no longer
certain over every value consistent with the suppression, only over the key's own count.

### 13.5 Detectors and the ranker

Series are `(entity_type, entity_id, predicate)` with any used cell; a key is `<entity_type>:<entity_id>:<predicate>`.

- **D2, cross-site burst.** For each eligible site and each test: `B = min(baseline_weeks, history_weeks)`, `lambda
  = max(lambda_floor, baseline ub / B)`, `expected = lambda * window_weeks`, `c` = window lower bound, `logp =
  poisson_logsf(c, expected)`; a test **certainly exceeds** when `c >= 1` and `logp < log(alpha_site / tests)`. That
  holds for every value consistent with the suppression, because `P(X >= c)` falls in `c` and rises in `lambda`. S
  has one test, its codes count. In X, once the series has cells in both channels at the site, there are two tests,
  Bonferroni over two: the combined count (`codes` plus `text_only`), and S's own codes count against a rate of at
  least the series' certain rate over both channels (`combined baseline lb / B`); before that the two coincide and
  the one test runs at `alpha_site`. The site certainly exceeds when one test does, and the snapshot shows the test
  with the lowest `logp` (`test`: `combined` or `codes`). Up to G8, X ran the combined test alone, so steady
  `text_only` background raised its baseline and a codes burst that S caught could reach X weeks later or not at all
  (a test pins one that X missed through its last week). The codes test removes most of that, and the floor keeps
  records that move between channels from looking like a fresh series (without it, per-channel tests at
  `lambda_floor` roughly tripled X's false alarms in an engineering probe on synthetic plant worlds). X can still be
  later than S, by design and disclosed: its codes test runs at `alpha_site / 2` where S runs at `alpha_site` (the
  same test has the burst in S in week 36 and in X in week 37), and its rate is floored by the series' certain rate
  over both channels, so a key with steady text-only background needs a higher codes rate in X than in S
  (`rate_at_k_text_background`, 14.4). Closing the gap fully means running S's test at full `alpha_site` inside X,
  which raises X's false alarms; that is an owner's decision, not made here. `possible(w)` is the same test with the
  window upper bound against `max(lambda_floor, baseline lb / B)` (any test). `p_s = clip(#possible / |P|, p_min,
  p_max)` over `P = {w <= W - window_weeks : history_weeks(s, w) >= min_history_weeks}`
  (`p_max` when P is empty), so no past window overlaps the current one and `p_s` is an upper bound on the site's base
  rate (A1). With `m` certainly exceeding among `n` eligible sites, `surprise = -poisson_binomial_logsf([p_s in
  site order], m)`, finite because `p_s` lies inside (0, 1). A candidate when `m >= burst.min_sites`. A site whose
  history of the series is all `'<k'` stays in `n` with its conservative `p_s` and is listed in
  `flags.suppressed_history_sites` (A2): leaving out a trial that did not exceed would raise the surprise.
- **D3, co-occurrence lift.** For each eligible site, within the key's entity type: `n_ep` (the key), `n_e` (the
  entity over all predicates), `n_p` (the predicate over all entities of the type) and `N` (all cells of the type),
  all derived from cells (no emitted marginal exists; the Boundary refuses any extra key). With `own` the key's count
  (window lower bound, baseline upper bound) and `rest_x = imputed(x) - imputed(n_ep)` the nuisance part at the
  shared imputation (13.4), each PMI is `smoothed_pmi(own, own + rest_e, own + rest_p, own + rest_N,
  pmi_smoothing)`, over the window and over the site's baseline weeks; `rise = pmi_window - pmi_baseline`; rising
  when `rise > pmi_delta` and `n_ep lb >= k`. A candidate when `cooccurrence.min_sites` sites rise. D2 and D3 on one
  key give one candidate; `detectors` lists `d2` and/or `d3`.
- **D4, resolution.** `res_conf` as in the table; `low_res_conf` is 1.0 when it is known and below
  `resolution.res_conf_min`.
- **D5, independence.** `independent_roots` is the sum of the lineage cells' lower-bound roots (root-cells: a root
  that spans weeks or channels counts more than once, a documented limitation); `root_ratio_ub` as in the table.
- **D6, decoys.** `echo` when `root_ratio_ub < echo_min_ratio`, which is flagged only when certain: an int `n` of 10
  with 3 roots gives 0.3 (echo); at k = 3, `'<k'` roots with an int `n` of 4 give 2/4 = 0.5 (no echo) and with an int
  `n` of 10 give 0.2 (echo: fewer than k roots is certain); a `'<k'` `n` caps the ratio at 1.0. `few_reporters_sites`:
  contributing sites with a lineage cell of int `n` and `'<k'` reporters (with k = 5 in `claims_integrity` that means at
  most 4 reporters). `high_base_rate` (A3): the share of eligible sites where **another** series of the same predicate
  **and entity type** certainly exceeds is above `base_rate_site_fraction`; the key itself is left out, so a
  single-entity burst never flags itself. Up to G8 the flag was predicate-wide over every entity type, so the pattern's
  own co-mentioned keys (the product and the supplier of a lot burst, each bursting with it) set it on the lot. Per
  type, a co-mentioned key of another type no longer does; the limitation that stays is two co-mentioned entities of the
  **same** type (two lots in one narrative), which still count as each other's base rate. `short_history_sites`:
  contributing sites with status `short_history`. **Stale** is a hard filter: when the newest lineage week's Sunday is
  more than `stale_days` before `as_of_W`, the candidate is removed at that step (`weeks[].stale_removed`). Because
  `stale_days >= close_lag_days + 7` (checked at load), the newest closed week is never stale.
- **D7, ranker.** Features: `burst_surprise` (the D2 surprise, 0.0 when `m = 0`), `pmi_rise` (mean rise over rising
  sites), `log_independent_roots = log1p(independent_roots)`, `supporting_sites`, `low_res_conf`, `echo`,
  `few_reporters_share = |few_reporters_sites| / |contributing_sites|`, `high_base_rate`. `score = logistic(bias +
  fsum(weight_f * feature_f))`. These are default weights; nothing is fitted and there is no learning.

Per candidate: `contributing_sites` are reported sites with a window cell of the key, `lineage` those cells (site,
type, id, predicate, week, channel, bundle), `supporting_sites` the eligible sites that exceed or rise, and
`decision_unit = org.decision_unit(supporting_sites)`, from the org config passed in.

### 13.6 Alerts, cooldown and ties

Per week, the detector candidates left after the stale filter are split into cooling and eligible keys. A key that
alerted cools until it has been absent from the candidates for `cooldown_weeks` consecutive steps; a cooling key
that appears again restarts its count and uses no budget (a stale-removed step counts as absent). The eligible keys
are ordered by `(-score, sha256(tie_salt|key))` and the first `alert_budget_per_week` alert, ranked from 1; the
lexical key never decides a tie, and another salt can flip it. Budget 0 gives no alerts. `detection_week` is a key's
first alert week (null if it never alerted) and `alert_weeks` lists every alert.

### 13.7 The rules channel

At every step, each pack rule is evaluated over the reported sites' lower-bound window counts, in the rule's own
`window_weeks`, for its entity type and predicate in the run's channels. A hit needs `min_sites` sites each counting
at least `min_count_per_site`. There is no history requirement and no stale filter; late sites are left out. Rule
hits are never ranked and never use the alert budget. `rule_hits[]` holds `{rule_id, key, first_week, weeks,
sites_at_first}`; a candidate's `channels` is the sorted subset of `["detector", "rule"]`, and a rule-only key is a
candidate with a null snapshot and no alert.

### 13.8 The result JSON

```
{"schema_version": 1, "run_id": "det-<16 hex>", "pack", "config_hash", "detector_hash", "org_hash", "run_channel",
 "cell_channels", "as_of", "first_week", "last_week", "tie_salt", "bundles", "bundles_sha256", "cells",
 "ignored_cells": {"after_last_week", "invisible_bundle", "not_in_org", "other_channel"},
 "status": "ok" | "insufficient_baseline" | "empty", "insufficient_baseline_weeks",
 "weeks": [{"week", "as_of", "status", "reported_sites", "eligible_sites", "late_sites", "candidates",
            "stale_removed", "cooling", "rule_hits", "alerts"}],
 "alerts": [{"week", "rank", "key", "score"}],
 "candidates": [{"key", "entity_type", "entity_id", "predicate", "run_channel", "channels", "detectors",
                 "first_candidate_week", "candidate_weeks", "alert_weeks", "detection_week", "decision_unit",
                 "config_hash", "detector_hash", "org_hash", "snapshot", "rule"}],
 "rule_hits": [{"rule_id", "key", "first_week", "weeks", "sites_at_first"}]}
```

`run_id` is `det-` plus the first 16 hex characters of the sha256 of the canonical `{run_channel, as_of, tie_salt,
config_hash, detector_hash, org_hash, sorted visible bundle shas}`, so a later bundle never changes an earlier run's id.
`snapshot` is the detector part at the detection week (else the first candidate week): `week`, `as_of`, `window`,
`score`, `features`, `flags` (`echo`, `few_reporters_sites`, `high_base_rate`, `short_history_sites`,
`suppressed_history_sites`), `res_conf`, `independent_roots`, `root_ratio_ub`, `d2` (`m`, `n`, `surprise`, one entry per
org site with its status, history, `test` (the D2 test shown: `combined` or `codes`), `c`, rate, expected, `logp`,
`exceeded`, `p_s`, suppressed history), `d3` (`rising`, one entry per org site with `n_ep_lb`, both PMIs, `rise`,
`rising`), `supporting_sites`, `contributing_sites`, `decision_unit` and `lineage`; fields not defined for a site's
status are null. `rule` is the rule part at its first week: `rule_ids`, `first_week`, `weeks`, `window`, `sites`
(`site`, `rule_id`, `count_lb`), `decision_unit` and `lineage`. Every list is sorted and floats are as computed.

### 13.9 What detection can and cannot see when every cell is `'<k'`

At the built-in packs' synthetic volumes every weekly cell is `'<k'` (G3 merge note 2). Then:

- **D2 can still fire** on a fresh or low-background series: two `'<k'` window weeks give `c = 2` against
  `lambda_floor * window_weeks = 0.08`, and `P(X >= 2) = 0.003 < 0.01`. A series present every week cannot burst
  this way: its baseline rate is taken at the upper bound (k-1 per `'<k'` week) while its window counts 1 per week.
- **D3 needs `n_ep lb >= k`**, which `'<k'` cells reach only by summing over the window (k cells, each counting 1).
  It can rise on all-`'<k'` cells: with the nuisance cells at `k / 2` on both sides, a steady key whose type gains
  other series in the window becomes more specific to its entity and predicate, and rises (a test pins such a case,
  where the opposite-bound marginals of G8 gave a negative rise on the same cells).
- **Few reporters and `res_conf` are invisible**: both need an int `n`. Echo cannot be shown either, since a
  `'<k'` `n` caps `root_ratio_ub` at 1.0.

The end-to-end test runs generated worlds of every built-in pack through `EdgeSite`, the receive log, the store and both
runs. What it printed in this sandbox, quoted only as **synthetic, same-author world, not a measurement** (seed 4, 40
weeks, six sites): `device_quality` X 3,155 cells, 27 candidates (12 from the detectors), 12 alerts, 18 rule hits;
S 1,367 cells, 9 candidates (2), 2 alerts, 7 rule hits. `claims_integrity` X 2,854 cells, 20 candidates (11),
11 alerts, 9 rule hits; S 1,007 cells, 7 candidates (2), 2 alerts, 5 rule hits. These worlds plant nothing on
purpose; the counts say only that the pipeline runs end to end. Recall, lead time and false alarms come from G5's
harness, never from these lines.

Nothing is ported from `origin/claude/mycelic-implementation-vr034p` in G4 (`INTEGRATION.md`).

## 14. G5: evaluation

G5 measures the frozen G4 detector on synthetic worlds with planted patterns and decoys, and replays it on public
openFDA data, **without changing what is measured**: no file under `detect/` changes, and every setting, the pack
and the code are hashed in a pre-registration before a plant spec or an outcome is seen. Every result is reported
against baselines. **Nothing in G5 is a measurement:** a planted world is synthetic and same-author, its scorecard
says `synthetic: true`, `internal_only: true` and `measurement: false`, and a replay is a measurement only on public
caches run by the founder. G5 makes no model call (X uses the lexical extractor).

| Part | Module | Purpose |
|---|---|---|
| Plant specs | `evaluate/plant.py` | Strict-JSON plant specs (patterns and eight decoy classes), their pack and world checks, the seeded construction and the seed-independent labels |
| Baselines | `evaluate/baselines.py` | The real G3 to G4 pipeline over a world, and the channels X, S, R (model-free), U, single_site, rules and the k=1 ablation |
| Harness | `evaluate/harness.py` | `python -m mycelic.collective.evaluate.harness prereg|check-plant|run`: pins, metrics, diagnostics, the scorecard and its schema |
| Replay | `experiments/openfda_replay.py` | `python -m mycelic.collective.experiments.openfda_replay prereg|signals|score` (STRATEGY section 9.3) |
| Fixtures | `packs/data/<pack>/fixtures/plant_smoke.json` | A construction smoke per built-in pack: same-author, not blind, never a result |
| Additions | `stats.py`, `edge/site.py`, `packs/loader.py`, `packs/connector.py`, `experiments/common.py` | Tie-averaged AP and precision@k and the cluster bootstrap; `build_cells(k=)`; the loader's listing accepts `fixtures/plant_*.json` (never read or hashed); `field_values`; `code_files` and `code_dirty(paths)` |

Import graph (no cycles): `plant` imports `packs.{loader,canonical,connector,generator}`, `detect.rules.series_key` and
`jsonio`; `baselines` imports `edge.{site,records,extract,weeks}`, `detect.{detectors,store,org,rules}`,
`packs.canonical`, `jsonio` and `stats`; `harness` imports `plant`, `baselines`, `stats`, `schemacheck`, `jsonio`,
`detect.detectors` and `experiments.common`; `openfda_replay` imports `connectors.openfda`, `packs`,
`evaluate.{baselines,harness}` and `experiments.common`. `detect/` imports nothing from `evaluate` (section 7). There
is no SQL in `evaluate/` or the replay: site stores are read through `RecordStore` methods and HQ through
`CollectiveStore` and `detect`.

### 14.1 Data flow

```
 prereg.json (pack hashes, code hash, seeds, world, evaluation weeks, grace, tie salt, author, bootstrap)
      |                                    plant spec (planter; bound by prereg_sha256) -> check-plant
      v
 run, per seed:  generate(pack, seed) --> world.records + plant(world, spec).records
                      |
                      v  run_pipeline: EdgeSite.ingest + extract("lexical") per site, emit_cells at every week's
                      |  closing date through each Boundary, CollectiveStore.ingest_log at HQ (constant clock)
                      v
     HQ store --> X, S (detect)            site stores --> U (exact cells, k=1), single_site, k=1 ablation
     records  --> R (model-free): the allowed fields only, record level, unsuppressed
     X result --> rules (episode starts)
                      |
     control, per seed: the same world without the plant, through the same pipeline and channels
                      |
                      v  alerts in the evaluation weeks, matched to labels.json; a find the control makes as early is a chance find
 labels.json, scorecard.json (schema-checked, content_hash)
```

### 14.2 Plant specs and labels

A plant spec is strict JSON with every object closed: `kind`, `schema_version`, `pack`, `prereg_sha256` (null or the
sha256 of the prereg file the run is given), `planted_by`, `planter_saw_detector_code`, `notes`, 1 to 500 `patterns`
and 0 to 500 `decoys`. A **pattern** is `{id, entity_type, entity_id, predicate, sites (at least 2), start_week,
weeks, rate_per_week, visibility, language}`; `start_week` is a 0-based index into the world's weeks and
`rate_per_week` the exact number of records per site per week. A **decoy** has `{id, class, entity_type, predicate,
sites, start_week, weeks, rate_per_week, language}` plus, by class:

| Class | Extra keys and shape | What it imitates |
|---|---|---|
| `echo_marked` | `entity_id`, one origin site, `copy_sites` (at least 1, disjoint) | forwarded copies that name their origin |
| `cross_site_unmarked_copies` | as above | copies that reached other sites without an origin marker |
| `same_site_duplicates` | `entity_id`, one site | one narrative entered again and again (held quiet by `min_sites`, not by root collapse: see below) |
| `single_site_burst` | `entity_id`, one site | a real burst that only one site sees |
| `single_reporter` | `entity_id`, at least 2 sites, rate at least k | one person at each site |
| `stale_chain` | `entity_id`, at least 2 sites, stale at the first evaluation week and wholly inside its detection window | an old chain whose records still sit in the window |
| `high_base_rate_everywhere` | `entity_ids` (at least 2 of one type), more than `base_rate_site_fraction` of the sites | a predicate common everywhere |
| `near_miss_entity` | `entity_id` (A) at one site, `near_miss_id` (B, 1 or 2 edits from A) at another | two ids that look alike |

`parse_plant` raises the first problem as `PlantError(path, problem)`, whose text is `plant: <JSON path>:
<problem>` and never holds a value. The order is fixed: the top level (shape, keys, types), `kind` and
`schema_version`, `pack mismatch`, `prereg_sha256`, `planted_by`, `notes`, the list sizes; then each item in list
order (a decoy's `class` first, since its keys depend on it; then keys, id, entity type, ids, predicate, site lists,
week and rate ranges, visibility and language, what the construction needs, the class shape); then `duplicate id`
and `duplicate key` (a key planted twice, counting every key of a decoy). `check_plant` adds the world: `unknown
site`, `id not in master data of a counted site` (with `require_master_data`), `outside the world weeks`, `outside
the evaluation weeks`, for a stale chain `not stale at the first evaluation week` (unless `7 * (eval_from - end) +
close_lag_days > stale_days`: the first evaluation step's own as_of is at least its week's Sunday plus
`close_lag_days`, so the chain's newest week is stale there whatever the run's as_of) and `not inside the first
evaluation week's window` (unless `start >= eval_from - window_weeks + 1`), `too few sites for a high base rate` and
`rate below k` (a single reporter needs an int `n`, so G4 can see few reporters). The two stale-chain rules leave a
narrow band (device: chains in weeks `eval_from - 7` to `eval_from - 5`; claims: `eval_from - 7` to `eval_from - 6`),
and that is the point: the whole chain is in the first evaluation week's window and none of it in that step's D2
baseline, so without G4's stale filter the chain is as strong a candidate there as when it was fresh. Audit round 3:
the earlier rule (`7 * (eval_from - end) > stale_days`) put every legal claims chain, and the shipped device chain,
outside every window of the watch span, so window arithmetic and burn-in, not the stale filter, kept them quiet.

**Construction** (`plant`) uses `random.Random(f"plant:{spec sha256}:{world seed}")` only. Records are made in spec
order (patterns, then decoys), weeks ascending, then sites in the given order, then the week's records. Persons and
reporters come from each site's background records; a single reporter uses the same reporter at each site. **Planted
records carry only the planted mention**:

| Visibility | Codes | Structured entities | Narrative |
|---|---|---|---|
| `narrative_only` (and every decoy) | none | none | one affirmed template of (language, predicate) whose only non-person slot is the entity type, with the exact id (an alias for an alias-only type), plus 1 or 2 filler sentences |
| `codes_only` | one specific code of the predicate | the id | 1 to 3 filler sentences |
| `both` | one specific code | the id | the template and filler |

Narratives are unique against the world (re-drawn at most 20 times), except same-site duplicates (one narrative for
every record, so the site store collapses them to one root) and copies (the origin's narrative, persons, reporter,
codes and entities, in the same ISO week; marked copies name their origin and are forwarded-in at the copy site,
unmarked copies do not, so each is its own root there). The pipeline input is `world.records + planted.records`.

**Labels** (`labels.json`) are seed-independent; the harness adds each seed's planted record count. A pattern's
**found window** is `[start, min(end + grace, eval_to)]`. A decoy's **watch span** is `[start, min(end + grace,
eval_to)]`, or for a stale chain `[eval_from, min(end + window_weeks - 1 + grace, eval_to)]`. The
**quiet precondition** of the structural classes is checked on HQ's X cells: for echo, same-site duplicates, a
single-site burst and a near miss, over weeks `[start - window_weeks + 1, watch_to]`, every site that planted no
counted record of the key sums at most 1 (an int `n` counts `n`, `'<k'` counts 1); for a stale chain every site sums
0 over `(end, watch_to]`.

**What the decoys assert (amendment A1).** G4 as built treats echo, few reporters and a high base rate as ranker
penalties, removes only stale candidates, and alerts every candidate while the budget lasts (section 13.5-13.6).
So: for the structural classes (`echo_marked`, `same_site_duplicates`, `single_site_burst`, `near_miss_entity`,
`stale_chain`) the mechanism is noise-free and, given the quiet precondition, G4's rules leave no X alert in the
watch span (and, for a stale chain, no X candidate either). Which rule does it differs by class, and the classes are
not five different tests:

- `echo_marked`, `same_site_duplicates`, `single_site_burst` and `near_miss_entity` are held quiet by `min_sites`:
  each key counts at one site only (marked copies are forwarded-in and count nowhere; each near-miss id is at its
  own site). `same_site_duplicates` therefore tests `min_sites` exactly as `single_site_burst` does; it does **not**
  test duplicate (root) collapse in detection, because D2 and D3 count records (`n`), not roots (`n_roots`). The
  collapse to one root is asserted on the site's cells instead
  (`test_same_site_duplicates_share_one_root_and_their_cells_have_suppressed_roots`), and a duplicate decoy at two
  or more sites would alert.
- `stale_chain` tests the stale filter, which acts on candidates: the chain is a candidate while fresh (in
  burn-in), its whole chain is still in the first evaluation week's window, and with the filter disabled it is a
  candidate in the watch span. It would still raise no alert there: it alerted while fresh and stays a candidate at
  every step after, so the cooldown (13.6) is never released. An alert-level outcome alone therefore cannot show a
  broken filter, and X1 scores a stale chain on candidacy as well (audit round 3 review): on every channel with
  candidates (all but `rules` and `single_site`) a candidate week of its key inside the watch span fails it like an
  alert (`candidate`, `failed`; 14.4). With the filter disabled the shipped chains fail X and U and raise no alert in
  either (`assert_only_the_stale_filter_keeps_it_quiet` runs the harness both ways in both packs and pins X's
  outcome).

For the penalty classes
(`single_reporter`, `high_base_rate_everywhere`) the flag is set on the candidate, and whether they alert is reported,
not asserted; `cross_site_unmarked_copies` is a known hard case, reported in `known_hard_cases`.

### 14.3 Channels and baselines

| Channel | Input | Run | Notes |
|---|---|---|---|
| **X** | HQ's codes and text_only cells, k-suppressed | `detect(store, "X")` | exactly what HQ computes |
| **S** | HQ's codes cells | `detect(store, "S")` | no model, no narrative |
| **R_mf** | record-level, unsuppressed counts built only from `central_allowed_fields` | `run_detection(..., "S")` over one synthetic bundle per site | see below |
| **U** | every extracted claim of every non-forwarded record (`emission_inputs`), both channels, k=1, exact roots and reporters, no master-data rule | `run_detection(..., "X")` | a reference, not a deployable system |
| **single_site** | each site's exact weekly counts (codes plus text_only) | G4's D2 site test per site, cooldown per (site, key), one budget shared by all sites, ties by `sha256(salt|site|key)`, score `-logp` | each site alone; an event carries its site |
| **rules** | the rule hits of the X run | one event per episode start (a hit week whose previous ISO week has no hit) | unranked, no budget |
| **X_k1, S_k1** (`--ablation-k1`) | the sites' own cells with the master-data rule, k=1, bypassing the Boundary | `run_detection` | internal only; the detector's parameters (including D3's support k) unchanged |

The scorecard carries each channel's label verbatim. The R-mf label states the gap to STRATEGY's R:

> R (model-free): the same detectors over record-level, unsuppressed counts built only from the pack's
> central_allowed_fields (structured codes and structured ids, never narrative). Not STRATEGY section 6.1's R (the
> best central system, including a frontier model, reading the allowed fields); E2 (G6) approximates that with its
> central_allowed condition.

**What R-mf reads (amendment A5).** A record is read only through `record[field]` for an allowed top-level field and
`record["entities"][t]` for an allowed `entities.t`; it is never iterated and its narrative, persons, reporter and
origin are never read (a test uses a record that fails on any other access). Every built-in pack allows `codes`,
`entities.*` of its egress types, `received_date` and `site`, and the reporter is a mandatory never field, so R-mf cannot drop forwarded
copies and counts every record as its own root and reporter (`n_roots = n_reporters = n`). It applies exactly the
cells' egress-type and master-data rules, so it differs from S only by record level, no suppression and no forwarded
removal. **The exact channels (U, R-mf, the k=1 ablation) can never raise G4's `few_reporters` flag**, which is
defined on a suppressed reporter count.

### 14.4 Metrics

Alert events are `{week, rank, key, score, site}`; events before the evaluation weeks are burn-in and dropped.

- **Found.** A pattern is found by a channel when an event names its key inside its found window (single_site: at
  one of the pattern's planted sites; up to G8 any site counted, so a site that never saw the plant could "find" it
  on its own background). Repeated events of a found pattern use budget but count once. Every other event is a false
  alarm, including an event on a pattern key outside its window.
- **Control and net found.** Each seed runs a second time **without the plant** (the same world records through the
  same pipeline, `work/seed-<seed>/control/`), and every channel's events there are matched to the same labels. A
  planted-world find is a **chance find** when the control also finds the unit (pattern, seed) **in the same week or
  earlier**: the key would have alerted that early without a single planted record. A control alert that comes only
  later does not void the find (audit round 2): the planted world is the control's records plus the plant's, so the
  planted world's earlier alert came from the plant, and the cooldown after it is what hides the later background
  alert there. Counting any control alert in the window as chance removed single_site's plant-driven finds (it
  spends its whole budget, so its control world nearly always alerts somewhere later) and gave X a positive lift
  over a single_site that found more patterns, earlier. Every channel reports `control_found` (the control's finds,
  any week in the window), `control_recall`, `control_alerts` and `chance_found` next to `found` and `recall`, and
  `found_net` / `recall_net` count the planted finds that are not chance finds (pooled, by visibility and per seed;
  each pattern outcome carries `found_in_control` and `chance_find`). `found` and `recall` still include chance
  finds; the lifts and `by_construction` read the net values. The control's alerts are kept in
  `control_alerts` per seed and channel. Per pattern, seed and channel: `delay_weeks = first -
  start_index` and `lead_weeks = end_index - first` (negative when found in the grace weeks).
- **Recall** = found units / (patterns x seeds), pooled and by visibility; median delay and lead (`stats.percentile`
  at 50) over found units, null when none.
- **Ranking**, per seed and channel: items are the distinct keys with an event, scored by their best event, relevant
  when one of the key's events lies in its pattern's found window; `n_relevant` = the number of patterns. Ties are
  averaged exactly over every ordering (`stats.tie_averaged_ap`, `stats.tie_averaged_precision_at_k`, formulas in
  the module docstring): for a tie group of `n` items with `r` relevant after `s` items and `R_b` relevant ones,
  AP adds `sum_{j=1..n} (r / n)(R_b + 1 + (j - 1)(r - 1)/(n - 1)) / (s + j)` (or `r (R_b + 1)/(s + 1)` for `n = 1`),
  divided by `n_relevant`; precision@k adds the `r` of whole groups inside the top k and `r (k - s)/n` for the group
  straddling k, divided by k always. The channel value is the mean over seeds; rules are unranked (null).
- `false_alarms_per_week` = false alarms / (evaluation weeks x seeds); `decoys_alerted[class]` counts (decoy, seed)
  instances with an event on any of the decoy's keys inside its watch span. `decoys_failed[class]` counts the
  instances that failed: an event as above, or, for a `stale_chain` on a channel with candidates (X, S, R_mf, U, the
  ablation), a week inside the watch span at which one of its keys was a detector candidate (after the stale filter,
  cooling or not). Each decoy outcome carries `alerted`, `first_alert_week`, `candidate` (null where candidacy is not
  scored: every other class, and `rules` and `single_site`), `first_candidate_week` and `failed`. Decoy outcomes
  enter no X1 verdict; a decoy alert is a false alarm and so counts against precision.
- **Lifts** `X_minus_single_site` (the collective lift), `X_minus_S` and `X_minus_R_mf`: per pattern, the list over
  seeds of `net_found_a - net_found_b` (`basis`: "found in the planted world and not found as early or earlier in
  the same seed's no-plant control world"); the estimate is the pooled mean; the 95% interval is `stats.cluster_bootstrap_mean`
  (whole patterns resampled, B and seed from the prereg, seed string `x1:<seed>:<name>`). **B2:** each lift also
  carries `alpha_adjusted` (`0.05 / family_size`, from the prereg) and `ci_low_adjusted` / `ci_high_adjusted`, from a
  second call with the same seed string, so the same replicates: the adjusted interval nests the 95% one, and with
  `family_size` 1 it equals it. `x1.verdict` reads the unadjusted interval (STRATEGY section 11.2).
- **Channel intervals (B2).** `harness.interval_blocks` adds four 95% blocks to every channel block, the ablation's
  included (`channel_block` itself is unchanged), each null or `{estimate, ci_low, ci_high, B, seed, method,
  clusters, n_clusters}` from `stats.cluster_bootstrap_mean` with seed string `x1:<seed>:<channel>:<metric>`:
  `recall_net_ci` resamples whole patterns, each holding its per-seed net found (the lifts' input; `clusters:
  "patterns"`, null without patterns), and its estimate is `recall_net` exactly; `precision_at_40_ci`,
  `average_precision_ci` and `false_alarms_per_week_ci` resample the seeds whose per-seed value is not null
  (`clusters: "seeds"`), null when none has one (rules), and their estimates are the channel's values (false alarms
  per week to within 1e-12, a mean of per-seed rates against a pooled rate). With few seeds the seed intervals are
  coarse.
- **Rate strata (B2).** Each scorecard pattern carries its `rate_per_week` (the plant format's one rate: the exact
  records per counted site per week), and `by_rate_per_week` holds one row per distinct rate, ascending:
  `{rate_per_week, patterns, units, channels: {<channel>: {found_net, recall_net}}}` over `CHANNELS`, on net found.
- **Minimum detectable rate** (analytic, pack only): for a constant weekly background `b` in `0..2k` at a site with
  full history, the smallest weekly rate `r` for which G4's D2 site test certainly exceeds, with `c = window_weeks x
  lb(b + r)` against `max(lambda_floor, ub(b))`, at the pack's k and with k=1. For `device_quality` (k=3) the rates
  for `b = 0..6` are 1, 3, 2, 2, 2, 2, 3 (unsuppressed 1, 1, 2, 2, 2, 2, 3); for `claims_integrity` (k=5) `b = 0..4`
  gives 1, 5, 4, 3, 2 (unsuppressed 1, 1, 2, 2, 2). `rate_at_k_text_background` is X when the background sits in
  text-only cells and the plant in codes cells (two cells a week): the combined test (`window x (lb(b) + lb(r))`
  against `ub(b)`) or the codes test (`window x lb(r)` against the certain `lb(b)`), each at `alpha_site / 2`; for
  `device_quality` `b = 0..6` it is 1, 3, 3, 3, 3, 3, 3 and for `claims_integrity` `b = 0..4` 1, 5, 5, 5, 5, where S,
  which never sees the text-only background, needs 1 (13.5). This is arithmetic on the pack's settings, not a
  measurement.

### 14.5 Prereg and run checks

`prereg` writes `runs/x1/<id>/prereg.json`: the pack (its ref, id, version, flags, four hashes, k, budget and
window), `code_hash` over `EVAL_CODE_FILES` (every file of `detect/`, `edge/`, `packs/` and `evaluate/`, plus
`stats.py`, `jsonio.py`, `schemacheck.py` and `experiments/common.py`), the code files, commit and dirty state, the
seeds (1 to 100 unique ints, sorted), sites, weeks (at least `baseline_weeks + window_weeks`), the evaluation weeks
(`eval_from >= window_weeks + min_history_weeks - 1`, `eval_to <= weeks - 1`), the grace weeks, top_k 40, the tie salt,
the detector author and the bootstrap's B (at least 1000) and seed. Uncommitted or unknown code state under
`EVAL_DIRTY_PATHS` is refused without `--allow-dirty`, which is stamped.

**B2 prereg keys.** The prereg also pins `family_size` (1 to `MAX_FAMILY_SIZE` = 100, `--family-size`, default 1:
how many primary tests share the run's error rate) and `planter_relation` (one of `PLANTER_RELATIONS`:
`independent`, `same_system_procedural`, `unstated`; `--planter-relation`, default `unstated`). Both are required in
the closed `PREREG_SCHEMA`; `cmd_prereg` refuses other values with a `UsageError` (`error: ...`, exit 2, nothing
written; no argparse `choices`, so the refusal has the CLI's own form). The flags are optional, so earlier argv works;
a prereg made before B2 lacks the keys and `read_prereg`, which E2 shares, refuses it.

`run` checks, in this order, and exits 2 writing no scorecard: the run id and a new run directory; the prereg's
closed structure; `--seeds` equal to the prereg's; the pack; the pins (`config_hash`, `vocabulary_hash`,
`detector_hash`, `fixtures_hash` and the code hash; every differing name is listed); the dirty rule; the plant spec
(`parse_plant` and `check_plant`) and its binding: a non-null `prereg_sha256` must equal the sha256 of the prereg
file. `check-plant` prints that sha256 for the planter and writes nothing. Ctrl-C exits 130 and leaves a partial run
directory that a later run with the same id refuses.

**B2 check-plant.** `check-plant` refuses a foreign binding with `run`'s message (`check_binding`), so a planter who
pastes a wrong sha256 learns it at once. `--construct` then builds the plant into every prereg seed's world, in seed
order (`generate`, then `plant`; `construct_every_seed`), and prints a third line, `construction: ok (seeds=<n>)`; a
`PlantError` or `GeneratorError` exits 2 with its text plus ` (seed <seed>)`, and nothing is printed on stdout. It is
construction only (no pipeline, no detection, no outcome), so it is a check, not an evaluation. `plant`'s RNG is
seeded by the spec's sha256 and the world seed, so a failure such as `narrative uniqueness exhausted` can depend on the
seed and on the spec's exact bytes; without `--construct` it first appears in `run`. A dry run with `--construct`
checks everything but constructs nothing. Without `--construct` the output is unchanged.

### 14.6 Scorecard and content hash

`scorecard.json` is validated against `SCORECARD_SCHEMA` (schemacheck, every object closed) before it is written; the
one union schemacheck cannot express, `code_dirty` (true, false or `"unknown"`), is checked beside it. It holds the
stamps (`synthetic: true`, `internal_only: true`, `measurement: false`, `same_author_pack`, `blind` with its
self-declared basis, `plant_bound_to_prereg`, `ablation_k1`, `allow_dirty`, `extractor: lexical`), every hash (pack,
code, org, prereg, plant, labels), the world and plant summaries, the channel labels, every channel's metrics
(per seed too, with the control's finds and the net values), the ablation (or null), the three lifts on net found,
`by_construction` (S and R-mf cannot see `narrative_only` plants: "planted narrative_only records carry no codes and
no structured entities, so they add nothing to the cells this channel reads", labelled "by construction, not a
result", next to their measured recall, control recall and net recall), `known_hard_cases`, per-pattern outcomes
(each with `found_in_control`) and per-decoy outcomes (`alerted`, `candidate`, `failed`), the decoys' quiet
precondition and X flags at detection, suppression per seed, the minimum detectable rate, every alert in the
evaluation weeks and every control alert (`control_alerts`), warnings (fewer than 10 patterns; a decoy that was not
quiet elsewhere), the `x1` block and notes.

`x1.eligible` needs a blind run (self-declared: `planter_saw_detector_code` false and `planted_by` other than the
detector author, compared case-folded with whitespace collapsed), a plant bound to the prereg, and at least 20
patterns and 20 decoys; `reasons` lists every failing condition and `verdict` is null unless eligible
(`lift_ci_low_above_0` for `X_minus_single_site`, `precision_at_40_at_least_0_25`, `pass`).

**B2 additions.** `x1` also holds `family_size` and `planter_relation` (echoed from the prereg), `independent`
(`planter_relation == "independent"`), `counts_as_strategy_x1` (eligible, independent, and a verdict that passes) and
`caveats`: `SAME_SYSTEM_CAVEAT` ("Procedural blinding only: the planter and the detector author are the same AI
system.") for `same_system_procedural`, `UNSTATED_CAVEAT` ("The prereg does not state the planter's relation to the
detector author, so this run cannot count as STRATEGY's X1.") for `unstated`, none for `independent`. `eligible`,
`reasons` and `verdict` are computed exactly as before. Each pattern carries `rate_per_week`, every channel block the
four interval blocks, every lift its adjusted interval, and the top level `by_rate_per_week` (14.4). `NOTES` gains
two sentences: the seed intervals resample seeds and are coarse with few seeds, and each lift's adjusted interval
comes from the same replicates while `x1.verdict` reads the unadjusted 95% one. `SCORECARD_SCHEMA` and
`PREREG_SCHEMA` stay closed.

`content_hash` is the sha256 of the canonical JSON without `$.content_hash`, `$.created_at`, `$.run_id`, `$.paths`
and `$.timings` (`content_hash_excludes`, written in the file). The pipeline clock is constant, every RNG is seeded
and every iteration sorted, so two runs of the same prereg and plant under other run ids, run directories and
`PYTHONHASHSEED` values give the same hash (tested with two subprocesses).

### 14.7 The openFDA replay

Three commands that can only run in order (RUNBOOK section 12); every file carries the label **"public data,
artificial partitioning, not a confidentiality demonstration"** and the caches' data label.

1. **prereg** pins the pack's four hashes, the replay's code hash (`EVAL_CODE_FILES` plus `connectors/openfda.py` and
   the replay), the events cache's manifest sha256 and query, the manufacturer names (exact, 1 to 20), the
   manufacturer and partition field paths (`packs.loader.PATH_RE`), the recalling firms (default: the names), the
   minimum partition coverage, the date range (inside the cache's query, at least `window_weeks +
   min_history_weeks + 1` ISO weeks), the look-back and post weeks and the tie salt, and stamps the analyst's
   declaration whether recall outcomes were seen. There is no recall argument.
2. **signals** refuses any changed pin (listing the names) and a changed events manifest, then runs phase 1 on the
   events alone: page order, each event with its page's product code; a repeated record ref counted and the first
   kept; the manufacturer field's values equal to a name exactly (missing and malformed fields counted apart); a date
   out of range counted (missing and malformed dates through the connector's rejections); the partition field's
   first value as a site (`p-` plus a slug of 48 characters, a sha-based id on an empty slug or a collision,
   `unpartitioned` without a value, more than 999 partitions refused); `map_rows` with `mapping_openfda`; records
   re-keyed to the pack's own record fields (the site store checks the default mapping); master data from the
   structured ids seen at each site ("public data has no ERP; structured ids seen at the site stand in for it"); the
   real pipeline; X, S and R-mf (null with a reason when the pack does not allow `site` and `received_date`). Each
   alert in the evaluated weeks (from index `window_weeks + min_history_weeks - 1`) carries its available date (its
   week's closing date) and the product codes of the records whose claims, in the channel's cell channels, hold its
   key in its window. It writes `signals.json` (coverage per field over the matched events, the partition, every
   product code of the manufacturer, warnings `low_partition_coverage`, `fewer_sites_than_min_sites` and
   `low_resolution`) and then `phase1.json` with the file's sha256.
3. **score** re-checks the pins, refuses a `signals.json` whose bytes differ from `phase1.json` or that was made under
   another prereg, and only then opens the recall cache. Recalls are deduplicated; another firm, a bad
   `event_date_initiated` and an initiation outside the range are counted; a product code without events is listed;
   a recall whose look-back window misses the evaluated weeks is not evaluable. A recall is found when an alert on
   its product code is available in `[initiation - 7 x lookback_weeks, initiation)` (the earliest gives
   `lead_days`); alerts from the initiation through `post_weeks` after it are post-recall alerts, never found and
   never false alarms; false alarms are the evaluated weeks' alerts that match no in-scope recall's window, over
   every product code of the manufacturer. `measurement` is true only when both caches are public.

**Chance (the circular-shift null).** Matching on the product code alone credits a channel for alert volume: a
channel that alerts on a code every few weeks "finds" a recall of that code at almost any date, and the scorer does
not match on the predicate or the cause (`root_cause_description` is free text with no mapping to the pack's
predicates). So each channel's summary carries `alerts`, `alerts_per_week`, `found_minus_expected` and `chance`: the
channel's alert timeline rotated by each whole number of weeks `s` in `0 .. evaluated_weeks - 1` (an alert available
`t` days after the first evaluated closing date moves to `(t + 7 s) mod (7 x evaluated_weeks)`), which keeps every
code's alert count and clustering, against the recalls at their real dates and the same look-back rule.
`expected_found` is the mean found count over the shifts, `per_recall` each recall's share of shifts that find it,
`p_value` the share of shifts (shift 0, the observed alignment, included, so never below `1 / evaluated_weeks`) whose
found count is at least the observed one, and `median_lead_days` the median lead over every shift's finds. A
channel's `found` means something only as far as it exceeds its own `expected_found`. A first null that drew alert
dates uniformly over the evaluable dates was rejected: on a synthetic world with no signal it gave p = 0.016, because
it broke the clustering of real alerts; the circular shift gave p = 0.30 on the same world (an engineering probe on
synthetic data, not a measurement).

### 14.8 What G5 does not show

- **Synthetic, same-author worlds.** The pack, the world generator, the planted templates and the detector were
  written by one author; a planted pattern uses the pack's own templates, which the lexical extractor reads
  perfectly. A smoke run is not blind, and blindness is self-declared even when it is claimed. When the planter is
  the same AI system as the detector author (B2), blindness is procedural at best: `planter_relation` says so, and
  `counts_as_strategy_x1` stays false.
- **Penalty decoys alert when the budget is free.** G4 penalises echo, few reporters and a high base rate in the
  ranker; with budget to spare they alert, and the scorecard reports it.
- **R-mf is not R.** STRATEGY's R includes a frontier model reading the allowed fields; E2 (G6) approximates it.
- **The illustrative pack resolves few real ids.** On real openFDA data its id formats will not match most model and
  lot numbers until a frozen copy carries the manufacturer's id shapes (`low_resolution` says so).
- **Nothing here is a measurement.** The sandbox could not reach api.fda.gov; every replay in the tests ran on a
  synthetic cache served by a local stub.

Nothing is ported from `origin/claude/mycelic-implementation-vr034p` in G5 (`INTEGRATION.md`).

### 14.9 The sealed X1 run (B2)

`docs/collective/x1/` holds the sealed, procedurally blind X1 run on the two packs B2 names, `device_quality` and
`claims_integrity` (B4's `it_incidents` is outside its frozen scope): `PLANTER_BRIEF.md` (the
planter's whole prompt, frozen at B2a: what it may read and run, the world, the spec format, the rules check-plant
enforces and the brief's own rules) and `RESULTS_TEMPLATE.md` (frozen at B2a: the fixed sentences, the
interpretation sentences and the conditions that select them, and the pointer rows `RESULTS.md` will show). Later
commits add the seeds, the preregs, the wrapper template, the seal and the results. The planter and the detector
author are the same AI system, so the blinding is procedural only and STRATEGY 11.2's X1 is not met; the results are
synthetic and internal only. The protocol is RUNBOOK section 11.1; the commits and commands are in `INTEGRATION.md`,
section B2. `tests/mycelic/test_collective_x1_sealed.py` checks the brief, the brief's own rules (`brief_problems`,
also run by the sandbox's wrapper) and the template's helpers.

## 15. G6: pushdown verification

G6 closes the Verify step of the loop (STRATEGY section 6.3). For each candidate HQ wants checked it asks one narrow
structured question of the sites that contributed cells and of up to `max_sibling_sites` siblings. Each site answers
from its own raw records, inside its boundary, with its in-boundary model or, when none is configured, the lexical
judge. What leaves is a verdict (`confirm`, `refute` or `unknown`), count buckets and one opaque `evidence_ref` that
only that site's auditor can resolve: no text, no exact count and no per-record handle. Questions go in and verdicts
come out through the same Boundary as the cells, and G0 scans both. A deterministic commit gate, ported from vr034p,
turns the verdicts into a status, and HQ keeps versioned, append-only conclusions with the lineage candidate -> cells ->
question -> verdicts. E2 is the experiment that gates the architecture.

**Nothing in G6 is a measurement and no real-model number is produced.** Model weights cannot be downloaded in the
sandbox, so every test and rehearsal ran with the lexical judge, a scripted fake or a local fake server. Detection
(`detect/detectors.py`, `rules.py`, `org.py`) is byte-identical to G5.

| Part | Module | Purpose |
|---|---|---|
| Questions | `pushdown/questions.py` | the question window, the template choice, the params check, the body (`build_question`) and HQ's display text (`render_text`, never sent); `PushdownError(path, problem)` |
| Gate | `pushdown/gate.py` | the commit gate over bucketed verdicts: pure, deterministic, every reason a fixed sentence (section 15.6) |
| Orchestrator | `pushdown/orchestrator.py` | routing (D3), delivery on worker threads with a deadline, verdict intake, late verdicts, versioned conclusions |
| Site verify | `edge/verify.py` | `SiteVerifier`: secrets, the question budget, retrieval, the judge (`judge_record` task or `lexical_judge`), the verdict rules, `evidence_ref`, `resolve` and `audit` |
| Boundary | `edge/egress.py` | the `question` (in) and `verdict` (out) artifacts, `ARTIFACT_DIRECTION`, the bucket helpers, `question_id`, `verdict_id_of`, `Boundary.accept` |
| Site store | `edge/records.py` | `question_log`, `verdict_log` and the window reads (additive; `SCHEMA_VERSION` stays 1) |
| HQ store | `detect/store.py` | `pd_questions`, `pd_routes`, `pd_verdicts`, `pd_verdict_rejections`, `pd_conclusions` and the routing reads (additive; `SCHEMA_VERSION` stays 1) |
| Packs | `packs/loader.py`, both packs' `egress.json` and `questions.json` | the required `pushdown` block (`PushdownConfig`), template coverage, verdict buckets `[k, 10, 50]` (`PACKS.md` section 1.2) |
| Statistics | `stats.py` | `paired_ranking_bootstrap` |
| G0 | `experiments/g0_canary.py` | the `pushdown` stage |
| E2 | `experiments/e2_pushdown.py` | `python -m mycelic.collective.experiments.e2_pushdown run` (section 15.7) |
| Fixture | `packs/data/device_quality/fixtures/plant_e2_smoke.json` | the E2 smoke spec: same-author, not blind, never a result |

**Import graph (no cycles).** `questions` imports `detect.rules.series_key`, `edge.egress` and `edge.weeks`; `gate`
imports `edge.egress`, `edge.weeks` and `jsonio`; `orchestrator` imports `questions`, `gate`, `detect.{store,org,
rules}`, `edge.{egress,weeks}`, `jsonio` and `threading`. No `pushdown/*.py`
and no `detect/*.py` imports `edge.records`, `edge.site`, `edge.extract`, `edge.verify`, `packs.generator`,
`evaluate`, `leakage`, `experiments` or an inference module (`edge.egress` pulls in only `inference.errors`); the HQ
import guard checks it statically and in a fresh interpreter (section 7). `edge/verify.py` imports `edge.{egress,
extract,records,weeks}`, `packs.canonical`, `schemacheck`, `jsonio` and `inference.{errors,tasks}`; it names
`EdgeSite` and `Runtime` only for type checking, and `edge/site.py` imports only `JUDGE_TASK` from it, so there is no
cycle. `e2_pushdown` imports `evaluate.{baselines,harness,plant}`, `detect`, `edge.verify`, `pushdown`, `leakage`,
`stats` and the inference runtime.

### 15.1 Data flow

```
 HQ   candidate: a stored detection candidate (verify_stored) or one built from HQ's cells (verify_candidate)
        |  question_window(as_of), select_template, build_question                     (pushdown/questions.py)
        v
      pd_questions + pd_routes   (contributing per D3; siblings by entity volume, then type volume; the cap)
        |  one routed site at a time, in sorted site order, on a daemon thread joined against the deadline
        v
 site Boundary.accept("in", "question")      -> hq/questions.jsonl, then edge/site-<id>.ingress.jsonl
      SiteVerifier.answer (its own RecordStore connection, one lock):
        no secret? -> answered before? -> budget? -> retrieve -> judge each record -> rules -> buckets, evidence_ref
        -> verdict_log + question_log (one transaction)
      Boundary.send("out", "verdict")        -> hq/receive.jsonl, then edge/site-<id>.egress.jsonl
        |  the returned body (or an exception, or nothing by the deadline)
        v
 HQ   receive_verdict: the Boundary's validator, then unknown_question, unrouted, pack_hash, window
        -> pd_verdicts (seq = last + 1; an identical latest body is a duplicate) | pd_verdict_rejections
      timeout or error -> an HQ record in pd_verdicts
        v
      regate: gate.evaluate over the verdicts received at or before as_of -> pd_conclusions (a new version on change)
```

### 15.2 The question and the verdict

Both are closed specs in `edge/egress.py`, checked by `check_artifact` at the Boundary and again at HQ; `LEAKAGE.md`
section 9 lists what each may and may not hold.

**Question** (direction `in`): `schema_version` 1, `pack`, `pack_hash` (the pack's `config_hash`), `question_id`,
`candidate_key` (`<type>:<id>:<predicate>`), `template_id` (one of the pack's templates), `params {entity_type,
entity_id, predicate}`, `window {start_week, end_week}` and `as_of`. Cross-field checks, in order: `template` (the
type is among the template's types; the predicate among its predicates unless they are null), `id_format`,
`consistency` (the key is `series_key(params)`), `question_id` (it equals `sha256(canonical {candidate_key, params,
template_id, window})`, so `as_of` and the pack hash are not in it and re-asking later has the same id) and `range`
(start before end, at least `min_window_weeks` ISO weeks counted on Mondays, the end closed at `as_of`).

**Verdict** (direction `out`): `schema_version`, `pack`, `pack_hash`, `site` (the Boundary's own), `question_id`,
`verdict_id`, `verdict`, `reason` (null, `budget` or `no_secret`), `window`, `support_bucket`, `roots_bucket`,
`reporters_bucket`, `entity_records_bucket` (each null or a label of `verdict_buckets(pack)`), `newest_week`,
`evidence_ref` (null or 16 hex), `truncated`, `quality` (`ok`, `degraded`) and `secret_mode` (`file`,
`seeded-demo`, `none`). A confirm has support, roots and reporters buckets, no entity bucket, a newest week inside
the window, a reference, no reason, quality `ok`, and roots and reporters no larger than support; a refute has only
the entity-records bucket and a reference; an unknown has no bucket, week or reference. A wire reason implies
`truncated` false, `secret_mode` is `none` exactly when the reason is `no_secret`, and `verdict_id` is the sha256 of
the canonical body without `verdict_id` and `evidence_ref`.

**Buckets.** `verdict_buckets(pack)` is `('<k', 'k-9', '10-49', '50+')` for every built-in pack (`k` is 3 or 5),
`bucket_of(n)` maps an int `n >= 1` (a bool, a float or 0 is a `ValueError`) and `bucket_lower` gives 1 for `'<k'`
and the low end otherwise.

**The Boundary.** `send("out", "verdict")` appends one line to HQ's receive log and then to the site's egress log;
an identical verdict already sent is a no-op. `accept("in", "question")` refuses a window ending after the last week
closed by the site's own clock (`range`), appends one line to HQ's `questions.jsonl` and then to the site's
`site-<id>.ingress.jsonl`, and is a no-op for an identical question. A row's direction must be its type's
(`log_row_problem`); `cells_bundle` and `usage_summary` keep G3's `after` sequence byte for byte.

### 15.3 Routing, delivery, deadlines and versions

**Contributing sites (D3)** are computed at HQ from the visible cells: a site of the current org with a cell of the
key, visible at the verification `as_of`, in the run channel's cell channels (X: codes and text_only; S: codes),
inside the question window. Verified at the snapshot's own `as_of`, this is the snapshot's `contributing_sites`; G4
also drops late sites, so on a late site the two can differ. The candidate's own lineage is kept for the lineage
only, which makes re-verification at a later `as_of`, and `verify_candidate` on hand-made candidates, well defined.

**Siblings** come from the span `[window start - baseline_weeks, window end]`, contributing sites excluded: first the
sites with any cell of the entity (any predicate), by lower-bound volume descending and then site id; then the sites
with cells of the entity type, by type volume and site id; the first `max_sibling_sites` are kept. A volume is the
sum of the cells' `n`, a `'<k'` cell counting 1.

**The question window** ends at the last week closed at `as_of` and starts at the earlier of the candidate's window
start and the week `min_window_weeks - 1` weeks before the end (week 53 handled: an `as_of` closing 2026-W53 gives
2026-W48..2026-W53, one closing 2027-W01 gives 2026-W49..2027-W01 for the device pack).

**Entry points.** `verify_stored(run_id, key, as_of=None)` verifies a stored candidate; `as_of` defaults to the
snapshot's, or for a rule-only candidate to `min(run as_of, rule.first_week's Sunday + close_lag_days + 6)`, and an
earlier `as_of` is refused ("before the candidate existed"). `verify_candidate(candidate, as_of)` is the documented
test path for any well-formed candidate, and `constructed_candidate` builds one from HQ's cells (score null,
`constructed` true). A malformed candidate raises `PushdownError` naming its path and writes nothing. A question id
already stored is reused with its stored body and routes, so re-asking is idempotent: the sites return their stored
verdicts byte for byte, the Boundary writes nothing, and no conclusion version is added.

**Delivery.** The routed sites are asked one at a time, in sorted site order: each handler runs on a daemon thread
joined against the deadline (`deadline_seconds`, in (0, 3600], default 600), so the sites' log lines (the question
and verdict lines of HQ's shared logs) and HQ's rows come in a fixed order, and a hung site delays the others by at
most the deadline; only a site that timed out can write later. A missing handler or an exception is an HQ
record `{question_id, site, verdict: unknown, reason: error, source: hq}` (nothing of the exception is kept); a
handler still running at the deadline is an HQ record with reason `timeout`, and its thread is registered as late; a
returned body goes through intake, and a refused one also gets an HQ `error` record. No SQLite object is used off
the calling thread (the site verifier opens its own connection) and the clock is read only for `received_at`. A site
answers one question at a time (its verifier's lock), so a question sent to a site still busy with a late one waits
behind it, and that wait counts against the new question's deadline: this is documented behaviour, not changed.
`join_late(timeout_seconds)` waits a bounded time for every late thread and returns how many still run; it takes
nothing in (`late_deliveries` counts what `collect_late` has not taken yet). E2 uses it (15.7).

**Intake** (`receive_verdict`): non-canonical JSON raises `PushdownError`; then the first failing check decides,
written as one `pd_verdict_rejections` row (the sha256, the site only when it is a valid site id, the question id
only when it is 64 hex, the reason, and the validator's path and keyword; never a value): `invalid`,
`unknown_question`, `unrouted`, `pack_hash`, `window`. A body whose sha256 equals the site's latest for the question
is a `duplicate` (a Boundary re-send after a crash between its two writes is one); any other body is appended with
the next `seq` and supersedes the earlier one.

**Late verdicts and versions.** `collect_late(as_of)` takes in the body of every late thread that has finished,
received at that `as_of`, and re-gates those questions there. `regate(question_id, as_of)` evaluates the gate over
every verdict received at or before `as_of`, so a late verdict never reaches back: gating again at the original
`as_of` gives the original result. A version is appended only when `{status, reasons, used}` changed; an `as_of`
before the latest version's is refused. `pd_questions`, `pd_routes`, `pd_verdicts` and `pd_conclusions` are
append-only (triggers abort `UPDATE` and `DELETE`). A conclusion is `c-` plus the first 32 hex of the question id;
its body holds `schema_version`, `conclusion_id`, `version`, `question_id`, `candidate_key`, `as_of`, `status`,
`reasons`, the gate result, `pack_hash`, the gate parameters, the `decision_unit` (`OrgConfig.decision_unit` of the
counted confirming sites, else the contributing ones; null when both are empty) and the lineage `{candidate: {run_id,
key, constructed}, cells, question_id, verdicts: [{site, seq, sha256}]}`.

### 15.4 Site verify (`edge/verify.py`)

`SiteVerifier(site, runtime=..., clock=..., secret_file=... | demo_seed=...)` refuses a runtime bound to another
boundary than `site:<id>`, anything but exactly one of `secret_file` and `demo_seed`, a negative or non-int seed and
a malformed secret file (`VerifyError`, no value in its text). `answer(question)`, in order:

1. under the verifier's lock, `boundary.accept` (an `EgressError` propagates);
2. a new `RecordStore` connection on the site's file, opened in the calling thread and closed afterwards;
3. no secret (a missing secret file) gives `unknown`, reason `no_secret`, before any read or budget check;
4. a question answered before re-sends its stored bytes and uses no budget;
5. `answered_count(type, id, day) >= question_budget_per_entity_per_day`, or an entity not yet answered that day
   when `answered_entities(day) >= question_entities_per_site_per_day` (audit round 2: a cap on guessing many ids),
   gives `unknown`, reason `budget`, not stored, so a later day answers it;
6. **master data** (audit round 2): with `require_master_data`, an id outside the site's master data
   (`site.in_master_data`, the cells' rule) gets `unknown` without a record read, stored like any answer with the
   local reason `not_master_data`; its body is that of an id with no records. Up to round 2 a question could test
   whether an id the cells withhold (id-shaped person data in a narrative) was in the site's records;
7. **retrieval** (`retrieve`, shared with E2's central_raw): the union of the site's own records (forwarded-in
   excluded) received in the window that hold any stored claim on the entity, whose structured values of the type
   resolve exactly to it, or whose narrative names it (the canonicaliser's scan, which also finds extraction misses)
   other than as one of the record's person values or its reporter (the extractor's `person_value` rule, since audit
   round 2); newest first, the first `verify_max_records` kept (`truncated` when cut);
8. **the judge**, per record: the task `judge_record` (data class `raw`, 256 tokens, schema `{mentions_entity,
   describes_predicate}`, each `yes`, `no` or `unclear`) through the site's runtime with ledger ref
   `j:<question id prefix>:<index>` (never a record ref), or `lexical_judge`. The payload holds the question's
   entity type and label, id, alias phrases, predicate and label, and the record's language, codes, structured entity
   values (D7: inside the boundary, and without them a codes-only record could never confirm) and narrative cut at
   `max_input_chars`; never persons, the reporter or a record ref. When the judge route leaves the site under an
   exemption (section 2), every retrieved record must carry its label (`WindowRecord.synthetic`, since audit round 2),
   else `InferenceBoundaryError` before any call. A boundary refusal propagates and nothing is stored or sent; any
   other inference error counts the record as a failure. Since audit round 2 the judging stops early: once failures
   are more than half the records (degraded whatever the rest say), and after two consecutive failures that say the
   route's primary server is down (`timeout`, `network`, `http_5xx`, each after the client's retries;
   `extract.BREAKER_AFTER`; since audit round 3 a failure on the escalation endpoint after the primary answered is
   not one), when the records not yet judged count as failures. Up to round 2 a dead server cost one call per
   retrieved record, all under the verifier's lock (at the shipped `verify_max_records` 500 and the example's 300 s
   deadline, about 42 h for one question at a wedged site, and every other question queued behind it); now a question
   costs at most two deadlines;
9. **the rules**, in order: nothing retrieved, `unknown` (local reason `no_records`); failures on more than half,
   `unknown` with quality `degraded` (see below); any yes/yes, `confirm` (support = yes/yes records, roots = their
   distinct roots, reporters = their distinct reporters with every unknown reporter one shared reporter, newest week); a
   record that mentions the entity, none that describes the predicate and fewer than half unclear, `refute` (the
   mentioning records); otherwise `unknown` (`unclear`). Only `budget` and `no_secret` cross as reasons (D8);
10. counts leave only as buckets; `evidence_ref = HMAC-SHA256(secret, verdict_id)[:16]` for a confirm or a refute;
   the verdict and its `answered` question_log row are stored in one transaction, then sent. A **degraded** verdict
   is the exception: it reflects the model server's health, not the records, so it is sent but never stored (its
   question_log row is `degraded`, which uses no budget, and `audit` has nothing for it), and the next ask of the
   question judges again. Up to G8 it was stored like any verdict, so step 4 re-sent the outage's `unknown` for that
   question forever.

**The lexical judge** rebuilds the record from the payload (no persons, no reporter) and runs the codes channel, the
lexical extractor and `pair`: `mentions_entity` is yes when the entity is a codes-channel entity or in any text claim
(negated and entity-only claims included), `describes_predicate` when the triple is a paired claim; for a language
the pack does not cover, every answer that is not yes is `unclear`. Every claim a site stored for a record makes it
answer yes/yes on that record (tested on every built-in pack's fixtures and a generated world). A narrative that writes a JSON
answer without the predicate stays at no; with a real model that is an E5 item (prompt injection through record
text).

**Secrets.** A secret file holds exactly 64 lowercase hex characters (one trailing newline allowed): `secret_mode`
`file`; a missing file is `none` and fails closed. A demo seed gives `sha256("mycelic-seeded-demo-secret:<seed>:<site
id>")`, `seeded-demo`. `resolve(evidence_ref)` (a method only; there is no module-level resolve) looks the reference
up in `verdict_log` and recomputes the HMAC with the current secret, so a rotated secret or none gives None; it
returns the question id, verdict id, verdict, window and the local record refs (the confirming records of a confirm,
the mentioning records of a refute). `audit(verdict_id)` returns the judged, failure, unclear, entity, confirming and
extraction-miss counts, the local reason and `truncated`, for the site only.

**The evidence_ref deviation.** STRATEGY section 6.3 has a verdict carry `record_ids_local[]`. A list's length is an
exact count, and per-record identifiers let HQ link records across questions; G6 sends one HMAC reference per verdict
instead, which the site's auditor resolves to the same local ids and nobody else can.

### 15.5 Decisions that refine the brief (D1 to D10)

- **D1. Weak confirms are not counted.** A confirm counts toward support only when its support bucket is not `'<k'`
  (at least k yes/yes records); a weaker one is noted. Background noise puts single records of decoy keys at other
  sites inside a window, and counting them would carry decoys to `supported`. Contributing and sibling confirms are
  treated alike. This mirrors G4's conservative imputation of `'<k'`.
- **D2.** An echo's copies are forwarded-in and never retrieved, so only the origin confirms with k records: its
  reason names confirming sites, not roots. Same-site duplicates are one root (`'<k'` counts 1), so they name roots
  too, when the window holds no other record of the key at that site (section 15.8).
- **D3.** Contributing sites are computed from the visible cells (section 15.3).
- **D4.** Verdict buckets are `[k, 10, 50]` in both packs; only `config_hash` changed.
- **D5.** The gate's thresholds are pack data (`questions.json`'s `pushdown` block), in `config_hash` only.
- **D6.** HQ tables are prefixed `pd_`, so a later fabric table cannot collide with them.
- **D7.** The judge payload carries the record's structured entity values (inside the boundary).
- **D8.** Only `budget` and `no_secret` cross as unknown reasons; `timeout` and `error` are HQ's records.
- **D9.** With no runtime a site judges with the lexical judge, which the fake provider also wraps; G0's lexical mode
  writes no ledger.
- **D10.** G0 keeps HQ's store at `<out>/hqdb/collective.sqlite3`, outside `hq/`, so the edge stage's whole-directory
  artifact still covers only the transport logs.

### 15.6 The gate (`pushdown/gate.py`)

**Ported (adapted, not merged) from `origin/claude/mycelic-implementation-vr034p@388aa30`,
`mycelic/knowledge/gate.py` and `support.py`.** Kept: the five checks (authorization, schema, provenance, temporal
validity, support), each readable in `checks`; the precedence contested, then hypothesis without evidence, then
stale, then the support checks, with `rejected` when a question-level check fails; `age > freshness_days` for stale;
every reason a readable sentence. Deviations: no Authorizer or OrgService (authorisation is "routed for this question
id and the pack hash matches"); `as_of` is injected where vr034p's `freshness()` defaults to `utcnow()`; the inputs
are bucketed verdicts, not excerpts or evidence refs; a `'<k'` roots or reporters bucket counts its lower bound 1,
where vr034p never counts unknown independence (every bucket here has a known lower bound); weak confirms are not
counted (D1); a per-verdict failure excludes that verdict rather than rejecting the claim; contested comes only from
a contributing site's refute (a sibling's refute is scoped negative evidence).

`evaluate(pack, question, routes, records, as_of)` is pure: no clock, no I/O, every iteration sorted, byte-identical
`to_dict()` under any record order and `PYTHONHASHSEED`. `GateParams.from_pack` reads `k`, the labels, the four
thresholds and `close_lag_days`; `STATUS_RANK` is supported 4, hypothesis 3, stale 2, contested 1, rejected 0 (E2's
pushdown score). **The reasons, exactly:**

| When | Reason |
|---|---|
| question under another pack hash | `rejected: the question was made under another pack hash` |
| question fails the spec | `rejected: the question fails the schema at <path> (<keyword>)` |
| `as_of` before the question's, or its window not closed at `as_of` | `rejected: the question window ends after the last week closed at as_of (look-ahead)` |
| per verdict, first match: received after `as_of`; not routed; another pack hash; another question; fails the spec; another window | `excluded: <site> verdict arrived after as_of`, `excluded: <site> was not routed this question`, `excluded: <site> answered under another pack hash`, `excluded: <site> answered another question`, `excluded: <site> verdict fails the schema at <path> (<keyword>)`, `excluded: <site> answered for another window` (an HQ record gets only the first two) |
| a contributing site's used verdict refutes | `contested: contributing site <site> refutes` (one per site) |
| no confirm at all | `hypothesis: no evidence (no site confirms)` |
| the newest counted confirming week ended more than `freshness_days` before `as_of` | `stale: the newest confirming week <week> ended more than <freshness_days> days before <as_of>` |
| too few counted confirming sites | `hypothesis: <n> confirming site(s) with at least <k> records; min_confirming_sites is <m>` |
| too few roots | `hypothesis: independent roots, lower bound <r>; min_independent_roots is <m>` |
| too few reporters | `hypothesis: independent reporters, lower bound <p>; min_independent_reporters is <m>` |
| all three checks pass | `supported: <n> confirming sites; independent roots, lower bound <r>; independent reporters, lower bound <p>` |

The list is the exclusions (by site, then seq), then the status reasons, then the notes in this order of groups, each
sorted by site: `<site> answered again; the latest verdict is used` (valid records with more than one sha256; the
highest seq is used), `not observed at <site> (sibling refutes; scoped negative evidence)`, `sibling <site> confirms
(counted as support)`, `<site> confirms with fewer than <k> records (not counted)`, `<site> judged a truncated
subset (verify_max_records)`, and `<site> unknown (<reason>)` for timeout, error, budget or no_secret, `<site>
unknown (degraded)`, or `<site> unknown`. `GateResult` also carries `support` (confirming, weak, refuting and unknown
sites, the lower bounds, the newest week, truncated sites), `freshness`, `excluded` and `used`.

**Resolvability** (STRATEGY section 6.3 targets at least 95%): a supported conclusion is resolvable when every
counted confirm's `evidence_ref` resolves at its site, through that site's `SiteVerifier.resolve`, to records of that
site that are not forwarded-in, were received in the window and are judged yes/yes. On the device `plant_smoke`
world (seed 11; synthetic, same-author, lexical judge) all 7 supported conclusions are resolvable, with 0
extraction-miss confirmations (the lexical extractor and judge share their machinery; a test covers a miss with a
fake extractor).

### 15.7 E2 (`experiments/e2_pushdown.py`)

```
python -m mycelic.collective.experiments.e2_pushdown run --x1-prereg FILE --plant FILE --run-id ID
    [--top-n 60] [--min-candidates N] [--site-routing DIR] [--central-routing FILE --central-context-tokens N]
    [--allow-external-raw synthetic] [--data-label synthetic] [--deadline-seconds 600] [--bootstrap-b 10000]
    [--bootstrap-seed 1] [--runs-dir runs] [--allow-dirty] [--dry-run]
```

**Refusals** (exit 2, nothing written): an existing run id; the prereg and its pack (the four hashes and G5's evaluation
code hash, as X1 pins them); the pack's `central_allowed_fields` without `site` and `received_date`; dirty code under
the evaluation paths, `pushdown/` and E2 without `--allow-dirty` (stamped); the world and the plant spec with its
binding; `--allow-external-raw` and `--data-label` both required and both `synthetic` ("central_raw sends raw record
text across site boundaries: ..."); a site routing directory without a file per prereg site, or whose judge route or
escalation names a boundary other than `site:<id>` or `any-simulated`, or the fake provider ("the judge route of site
<sid> may leave the site"); a central routing on a site boundary or the fake provider; `--central-routing` without
`--central-context-tokens` (the context the central server gives one request: Ollama's `num_ctx`, llama-server's `-c`
divided by `--parallel`), a context of at most the central tasks' 64 output tokens, or `--central-context-tokens`
without `--central-routing`; `--bootstrap-b` below 1000. Without `--site-routing` every site judges with an in-process
fake (the lexical judge); without `--central-routing` the central conditions use fake handlers, rehearsal only: a record
counts when the site's lexical judge says yes/yes (raw) or a structured value resolves to the entity and a code maps to
the predicate (allowed), and `score = min(100, 30 * min(3, confirming sites) + min(10, confirming records))`.

**Per seed**, in prereg order: generate and plant the world, run G5's pipeline, detect run X at its `as_of` with the
prereg tie salt, and take the detector candidates whose first candidate week is an evaluation week, by `(-snapshot
score, sha256(tie_salt|key))`, the first `--top-n`. A candidate is `true` when its key is a pattern key and its
snapshot week lies in the pattern's found window, `decoy` for a decoy key, else `background`. Fewer than 300
candidates or 5 seeds exits 2 (the partial run directory is left) unless `--min-candidates` allows it, stamped
`below_protocol_minimum`.

**Scoring**, in `(as_of, seed, key)` order with the site clock at the item's `as_of` (the budget's day):
`stats_only` is the snapshot score; `central_raw` retrieves with the sites' own `retrieve` across all sites (forwarded
copies excluded) in the question window, at most 400 records (truncation recorded), and sends their codes, entities,
language and text to `judge_candidate_raw` (raw) at a `central` runtime with `allow_external_raw=synthetic`;
`central_allowed` reads only the allowed fields, by subscript (`allowed_view`), of every record, forwarded copies
included (origin fields are not allowed), whose allowed structured values resolve to the entity and whose received
week is in the window, and sends them to `judge_candidate_allowed` (structured); `pushdown` is
`verify_stored(run_id, key)` at the snapshot's `as_of`, scored `STATUS_RANK[status] * 1,000,000 + support lower
bound`.

**Late answers.** After each item's pushdown, every delivery still running at the deadline gets one more
`--deadline-seconds` (`join_late`) and is then collected at the item's own `as_of` (`collect_late`, which re-gates
the question there), and the item is scored on the conclusion read after that. Up to G8, E2 never collected late
answers: a slow site's verdict was scored as a timeout `unknown`, and since a site answers one question at a time,
its next questions queued behind the late one and timed out too. A route that still ends `unknown` with reason
`timeout` or `error`, or a `degraded` verdict, is **unanswered**; `pushdown.unanswered`, `unanswered_share` and
`late_collected` count them, and each item carries its `late_collected`.

**Central failures and the context.** A central call that fails after the runtime's retries, repair and escalation
(an `InferenceError`) no longer aborts the run: the item records its `central_errors` by condition and kind, its
central scores are null, `central.failures` counts every kind, and such items are left out of the statistics
(`bootstrap.excluded_central_failures`). Each central_raw call's server-reported prompt tokens are kept per item
(`central_raw_prompt_tokens`); a prompt whose tokens plus the task's 64 output tokens reach
`--central-context-tokens`, or whose tokens were not reported, is counted in `central.at_context_limit`, since a
server that truncates silently would have shown the central reference a cut prompt.

**Statistics.** `stats.paired_ranking_bootstrap` over the items without a central failure: AP (tie-averaged, null
without positives) and precision@40 per condition with paired percentile intervals (one resampling stream for all
conditions), resampled by **candidate key** (`bootstrap.clusters: "candidate key"`, `n_clusters`): the same key in
several seeds is one cluster, so its correlated items are drawn together. The ratio is pushdown AP / central_raw AP
(null when central_raw's AP is null or below 0.01). `chance` is the AP of a random order (its expectation, near the
prevalence of true items, with its interval), and the ratio block adds the **chance-corrected ratio** `lift_estimate`,
`(AP_pushdown - chance) / (AP_central_raw - chance)`, with its paired interval (`lift_undefined` when central_raw's lift
is below 0.01) and central_raw's own lift over chance (`denominator_lift` with its interval). The plain AP ratio alone
passes an uninformative verifier: on a pool where most candidates are true, a constant score has an AP near the
prevalence, and the ratio of that to central_raw's AP can exceed 0.90 while the verifier ranks nothing; its lift over
chance is 0 (a test pins it).

**Raw text.** `central_raw` reports the UTF-8 bytes of every text it sent; the in-memory payloads are also scanned
(their overlap must be above 0, a positive control); `central_allowed` reports the shingle overlap of its payloads (0
by construction); `pushdown` reports the overlap of every crossing transport artifact of each seed (the questions, the
verdict rows of the receive log, the site ingress and egress logs), from `leakage.scan` with an empty canary manifest
against that seed's narratives; it must be 0.

**Resolvability** as in section 15.6, and `extraction_miss_confirmations` from each counted confirm's `audit`.

**The bar.** `measurement` is `common.measurement_flag` over every site and central endpoint and every ledger row, so
any fake makes it false. The 0.90 bar (`{bar, ratio, ratio_at_least_bar, ci_low_at_least_bar, lift_ratio,
lift_ratio_at_least_bar, pushdown_raw_text_bytes_zero, pass}`) is withheld (`verdict` null, `verdicts_withheld` true,
`withheld_reason` says why), in this order, when: a fake took part; a central call failed; a central_raw prompt
reached the declared context or did not report its tokens; more than 5% of the pushdown routes were unanswered
(`settings.max_unanswered_share`); the ratio is undefined; or the pool is uninformative (central_raw's lift over
chance has a 95% interval reaching 0, so no ratio can show that pushdown keeps it). `pass` needs the AP ratio and
its interval's low end at 0.90, the chance-corrected ratio at 0.90 and zero pushdown raw-text bytes.

**The run file.** `e2.json` is validated against `E2_SCHEMA` (every object closed) before it is written: `kind`,
`schema_version`, `run_id`, `created_at`, `stamps` (`synthetic` and `internal_only` true, `measurement`,
`same_author_pack`, `below_protocol_minimum`, `allow_dirty`, `secret_mode: seeded-demo`, `data_label: synthetic`), the
four pack hashes, the E2 code hash, the prereg and plant sha256, `code`, `endpoints`, `settings` (with
`central_context_tokens` and `max_unanswered_share`), `candidates`, `condition_labels` (each says what the condition is:
`central_allowed` is "STRATEGY's R for this task", `central_raw` says raw text crosses and the data is synthetic only),
`bootstrap` (with `clusters`, `n_clusters` and `excluded_central_failures`), `conditions`, `chance`, `ratio`, `central`
(`failures` by condition and kind, `failed_items`, `context_tokens`, `prompt_tokens_max`, `prompt_tokens_median`,
`prompt_tokens_unreported`, `at_context_limit`), `raw_text_bytes`, `raw_text_scan`, `pushdown` (statuses, verdicts by
kind and reason, routes, budget unknowns, timeouts, errors, `late_collected`, `unanswered`, `unanswered_share`,
resolvability), `verdict`, `verdicts_withheld`, `withheld_reason`, `items`, `notes`, `paths`, `timings`,
`content_hash_excludes` and `content_hash` (without `content_hash`, `created_at`, `run_id`, `paths` and `timings`; two
runs under other run ids and `PYTHONHASHSEED` values give the same hash, tested). No record text, record ref or
narrative is written to `e2.json` or to `central.ledger.jsonl` beside it (ledger refs are `e2:<n>:raw|allowed`); the
site ledgers stay in the work directories.

**The smoke fixture.** `plant_e2_smoke.json` (85 narrative-only patterns at 2 or 3 sites, rate 2, 4 to 6 weeks inside
the evaluation weeks, ids in each counted site's master data, plus 10 decoys of the single-reporter, unmarked-copies
and high-base-rate classes) yields at least 60 candidates per seed under an X1 prereg of 6 sites, 52 weeks,
evaluation weeks 20 to 51 and seeds 1 to 5. Its recipe is in `INTEGRATION.md` (G6); no generator script is
committed. A rehearsal over it with this round's code (fakes everywhere, tie salt `r9-rehearsal`, top-n 60;
synthetic, same-author, **not a measurement**) gave 300 candidates over 5 seeds (106 distinct keys), raw-text bytes
of 2,573,511 for central_raw and 0 for central_allowed and pushdown, 276 of 276 supported conclusions resolvable, 0
unanswered routes, 0 late answers, 0 central failures, and a verdict withheld because fakes took part. Its AP figures
are not quoted: the fake site judge and the fake central judge are the same deterministic reader of the pack's own
templates, so their ratio says nothing about any model. What the rehearsal does say is about the **pool**: 247 of
the 300 candidates are true, so the chance AP is about 0.83 and central_raw's lift over chance has a 95% interval
reaching below 0. On such a pool the plain AP ratio and its interval's low end clear 0.90 for almost any verifier,
which is why the bar also needs the chance-corrected ratio, and why a run on this fixture is withheld as
uninformative unless the real judges separate far better than chance. A real E2 needs a plant spec whose candidate
pool holds many more decoys and background keys than this smoke does.

### 15.8 What G6 does not show

- **No model was measured.** Every judge was the lexical judge, a scripted fake or a local fake server. E2's bar can
  only be judged on the founder's runs (RUNBOOK section 13) with `measurement: true`.
- **Synthetic, same-author worlds.** The lexical judge reads the pack's own templates perfectly, so a fake rehearsal's
  resolvability and ratio are plumbing checks, not results.
- **Known hard cases reach `supported`.** Cross-site copies without an origin marker count as independent roots at
  each site, and a predicate common everywhere confirms everywhere (on the device `plant_smoke` world both decoy
  classes reach `supported`; the tests record this and assert only well-formedness). Background records add roots: on
  that world every 6-week window over the same-site-duplicate span also holds one or two background records of the
  key at that site, so the roots check passes there and only the sites check keeps the decoy a hypothesis; the roots
  reason appears in the window that holds only the duplicates.
- **Few reporters at k = 5.** A `'<k'` reporters bucket counts 1, so in `claims_integrity` (k = 5) a pattern planted at
  two sites with fewer than 5 reporters each stays a hypothesis on reporters until a third site confirms (seen on its
  `plant_smoke` world; the three-site pattern is supported).
- **Residual disclosure (X5).** A refute versus an unknown reveals presence in the window (the daily budget limits,
  not prevents, it), bucket transitions between overlapping windows narrow a count, and verdict buckets can be
  differenced against weekly cells (`LEAKAGE.md` sections 7 and 9).
- **No follow-up and no fabric wiring.** Only `supported` conclusions propose follow-ups (G7, section 16, built ahead
  of E2 and X4); fabric events and JetStream subjects for questions, verdicts and conclusions are integration notes
  (`INTEGRATION.md`, G6), not code.

## 16. G7: approval-routed follow-up

**Built ahead of E2 and X4 (STRATEGY sections 5.5 and 7): approval-routed follow-up is unvalidated; nothing here
measures it.** STRATEGY section 5.5 says E2 gates the architecture and runs before any action-layer work; section 7
says X4 (20 historical cases: time to an approved draft, edit distance, rejection and false-action rates) is measured
before the layer is shown. Neither has run. G7 builds the layer so that those measurements have something to measure;
every doc and run file of it carries the label above (`followup.policy.BUILT_AHEAD_LABEL`).

G7 closes the Follow-up and Check-the-outcome steps of the loop (STRATEGY section 4.1). A `supported` conclusion (G6's
`pd_conclusions`) becomes approval-routed work for a named owner: T0 assembles a read-only evidence packet inside each
target site and lets only a bucketed, suppressed, structured summary cross the Boundary; T1 writes a CAPA, SCAR or
SIU-referral style draft at HQ from structured inputs only; a human approves, edits or rejects it; T2 stays off and T3
is not an action type. Nothing in a record can propose, target or approve anything. Every step is an entry of an
append-only, hash-chained ledger (`followups.sqlite3`). Afterwards an outcome check measures, and only measures,
whether the failure mode recurred. **No real-model number is produced**: drafts come from a deterministic template
(or a fake provider replaying it), every approval in G0 and E5 is simulated and stamped so, and detection
(`detect/{detectors,rules,org}.py`) is byte-identical to G5.

| Part | Module | Purpose |
|---|---|---|
| Policy | `followup/policy.py` | the labels, `as_of` normalisation, `Principal` (`SYSTEM`, `human`), the approvers file (D1) and the kill switch |
| Ledger | `followup/ledger.py` | `followups.sqlite3`: entries, unique indexes, triggers, the hash chain, `verify_chain` and the `verify` CLI |
| Service | `followup/service.py` | `FollowupService`: propose, assign and escalate, draft, approve, edit, reject, execute, overdue, check_outcome; `FollowupState` and `replay` |
| Drafts | `followup/drafts.py` | `draft_payload`, `template_draft`, `draft_scope_problem`, `DraftWriter` (the only follow-up module that imports inference) |
| Executors | `followup/executors.py` | `PacketExecutor` (T0), `OutboxExecutor` (T1), `ExecContext` |
| Outcome | `followup/outcome.py` | `evaluate` (pure) and `check` (from HQ's store): recurrence after execution, measurement only |
| Packets | `edge/packets.py` | `PacketAssembler` inside the site; `code_distribution` and `co_mentions` (pure) |
| Boundary | `edge/egress.py` | the `packet_request` (in) and `packet` (out) artifacts, `packet_labels`, `FOLLOWUP_KEY_RE`, the `suppression` keyword, `Boundary.accept` for packet requests (additive; the cells, usage, question and verdict behaviour is byte-identical) |
| HQ reads | `detect/store.py` | `HqReader`: read-only, one `mode=ro` connection per call |
| Packs | both packs' `followups.json` | the args per D2 (`PACKS.md` section 1.3); only `config_hash` changed |
| G0 | `experiments/g0_canary.py` | the `followup` stage; `build_context` and `run_stages` |
| E5 | `experiments/e5_injection.py` | the injection smoke (section 16.11) |

**Import graph (no cycles).** `policy` imports `hierarchy`, `jsonio` and `packs.connector`; `ledger` imports
`edge.egress`, `edge.weeks`, `jsonio` and `policy` (and `service.replay` inside `FollowupLedger.open`); `drafts`
imports `schemacheck`, `jsonio`, `packs.canonical` and `inference.{errors,tasks}`; `executors` imports `edge.egress`,
`jsonio`, `packs.connector` and `policy`; `outcome` imports `edge.{egress,weeks}`, `stats` and `policy`; `service`
imports all of them, `detect.store` (`HqReader`) and `schemacheck`. No follow-up module imports `edge.records`,
`edge.site`, `edge.extract`, `edge.verify`, `edge.packets`, `packs.generator`, `evaluate`, `leakage`, `experiments` or
a model client; `edge/packets.py` imports `edge.{egress,records,weeks}` and `jsonio` and nothing of `followup`,
`pushdown`, `detect` or inference (section 7).

### 16.1 Tiers

| Tier | Here | Executor | Approval |
|---|---|---|---|
| T0 read-only | an evidence packet assembled inside each target site | `packet` | exactly one human approval (D3); its version is always 1 and it cannot be edited |
| T1 draft | a CAPA initiation, SCAR or SIU-referral draft written at HQ from structured inputs | `draft` (the outbox) | exactly one human approval of a draft version; edits make new versions |
| T2 write | (none enabled; the loader refuses an enabled T2) | none: a `write` executor is refused | — |
| T3 | regulatory submissions, recalls, holds | not an action type (the loader refuses `T3`) | human only |

### 16.2 Data flow

```
 HQ   pd_conclusions (G6), read through HqReader (mode=ro)
        |  propose(conclusion_id, type, args, principal=SYSTEM, as_of[, targets])   all checks in one ledger transaction
        v
      ledger: proposed -> assigned (owner from the CURRENT approvers file) [-> escalated | escalation_failed]
        |  T1: draft_payload (structured only) -> DraftWriter (central runtime or template) -> id-scope scan
        v                                         -> drafted | draft_failed   (own transaction, no retry)
      a named human: approve(version) | edit(base_version, fields) -> edited | reject
        |  execute(as_of): executing (committed) -> the executor -> executed | outcome_unknown | blocked
        v
 T0   PacketExecutor: per target site, in sorted order, on a daemon thread joined against the deadline
 site   Boundary.accept("in", "packet_request")  -> hq/packet_requests.jsonl, then site-<id>.ingress.jsonl
        PacketAssembler: the stored verdict for the question -> the confirming records -> codes, co-mentions
        full packet (narratives) -> <workdir>/packets/site-<id>/<digest>.json   (never leaves the site)
        Boundary.send("out", "packet")          -> hq/receive.jsonl, then site-<id>.egress.jsonl
 T1   OutboxExecutor: one canonical line -> outbox.jsonl
 HQ   check_outcome(as_of[, post_start]) -> outcome (measurement only)
```

### 16.3 Guardrails

- **Only the system proposes, and only on `supported`.** `propose` refuses any principal but `SYSTEM`
  (`not_system`), so no person and no record content can propose. A conclusion that is not `supported` at its latest
  version is refused.
- **Allow-listed and schema-validated.** The type must be a pack type of tier T0 or T1 and enabled; its args must
  pass the pack's compiled args schema (the closed DSL: entity id, predicate, conclusion id, enum, integer; no free
  text) after NFC normalisation, and lie inside the conclusion (the scope rule, `PACKS.md` section 1.3). Targets must
  be contributing sites of the conclusion.
- **Record content is data.** A packet reads only structured fields; a draft reads only `draft_payload`; refusal
  payloads hold codes, schema paths and arg names, never values. E5 checks it (section 16.11).
- **Scoped, current authority.** A decider is a human whose CURRENT approvers entry has the type's owner or
  escalation role at a unit covering every target site; a target no longer in the org is out of scope.
- **Version-pinned approval.** Approve names a version; an older one is `stale_version`. Rejects and approvals are
  terminal, once each.
- **At most once.** `executing` is committed before the executor runs; a stored result is returned, never recomputed;
  replay calls no executor.
- **Caps and the kill switch.** A per-type daily cap on proposals (by the UTC date of `as_of`); a kill switch read on
  every call (file plus environment), failing closed, which blocks proposals, approvals and execution.
- **Idempotent keys** `act:<conclusion>:<type>:<16 hex of sha256(canonical {args, targets})>` (D4).

### 16.4 Propose: check order and codes

In one `BEGIN IMMEDIATE` ledger transaction; the first failing check decides. A refusal writes one `refused` entry
(its key column is the computed key when computable, else `-`), commits, and raises `FollowupRefused(code)`. A
malformed call raises `FollowupError` and writes nothing: an `as_of` that is not a UTC date or a timestamp with `Z` or
`+00:00` (an impossible date or time, another offset), a principal that is not a `Principal`, targets that are not a
list of strings or hold duplicates, an `as_of` before the conclusion's, an `as_of` whose `ack_due` would pass
9999-12-31. The shape checks (`as_of`, the principal object, targets as a list of strings) run before the
transaction; the others sit at their step in the table, so an earlier refusal wins: duplicate targets from a human
principal are `not_system` with its `refused` entry, and the duplicate check is reached only at step 10. When HQ's
store is missing or damaged mid-call, `HqReader` raises its own `StoreError` (or `StrictJsonError`); the transaction
rolls back and nothing is written.

| # | Check | Code |
|---|---|---|
| 1 | the principal is `SYSTEM` | `not_system` |
| 2 | the id matches `c-[0-9a-f]{32}` and HQ holds a version | `unknown_conclusion` |
| 3 | the key (computable when the args canonicalise) already has `proposed`: return it, no entry, whatever its state | — |
| 4 | the latest version is `supported` (the payload names the status) | `conclusion_not_supported` |
| 5 | a pack follow-up type | `unknown_type` |
| 6 | tier T0 or T1 (before enabled: a disabled T2 is this) | `tier_not_allowed` |
| 7 | enabled | `type_disabled` |
| 8 | the compiled args schema over the NFC args (the payload names the path and keyword; a non-object is `$`/`type`, args that cannot be canonical JSON `$`/`json`) | `args_invalid` |
| 9 | each `conclusion_id` arg is the conclusion's id, each `predicate` arg its predicate, each `entity_id` arg in its scope ids (the payload names the arg) | `args_out_of_scope` |
| 10 | targets (default: the contributing sites, routes of role `contributing` in the org) are a non-empty subset of the contributing sites | `target_not_contributing` |
| 11 | the kill switch is off for the type | `kill_switch` |
| 12 | fewer than `daily_cap` `proposed` entries of the type on the UTC date of `as_of` (refusals do not count) | `daily_cap` |
| 13 | the approvers file loads | `approvers_unavailable` |

Then `proposed` `{conclusion_id, conclusion_version, question_id, candidate_key, type, tier, executor, args (NFC),
targets (sorted), as_of}`, then `assigned` (section 16.8), then, without an owner, the escalation entry. For T1 the
draft is written after the commit, outside any transaction (section 16.9), and recorded in its own transaction.

### 16.5 Decide: approve, edit, reject

Each in one ledger transaction under the service's lock; the first failing check decides:

| # | Check | Code |
|---|---|---|
| 1 | the principal is human | `system_cannot_decide` |
| 2 | the key exists (logged with key `-` when it is not key-shaped) | `unknown_key` |
| 3 | no terminal decision yet: approve after reject, reject after approve, edit after approve, approve twice | `terminal` |
| 4 | `as_of` not before the key's last entry's | `FollowupError`, no entry |
| 5 | the conclusion's latest version is still `supported` | `conclusion_no_longer_supported` |
| 6 | the approvers file loads (read now, never from the assignment) | `approvers_unavailable` |
| 7 | the principal holds the owner or escalation role at a unit covering every target (a target no longer in the org is out of scope) | `not_an_approver`, `role_not_allowed`, `out_of_scope` |
| 8 | approve only: the kill switch is off | `kill_switch` |
| 9 | approve and edit: an edit of T0, or a T1 without a draft | `no_draft` |
| 10 | approve and edit: the version is the latest | `stale_version` |

`approved` and `rejected` store `{version, role, unit_path, approvers_hash, conclusion_version, as_of}` from the
current approvers file (the first sorted entry that grants authority) and the latest conclusion version. **Edit**: a
field name that is not a draft property and is one of `args, conclusion_id, key, targets, tier, type` is
`immutable_field`; the merged draft must pass the type's `draft_schema` (`draft_invalid`, with the path and keyword;
an unknown field is `$`/`additionalProperties`) and the id-scope scan (`draft_out_of_scope`, with the path); an
unchanged draft writes nothing and returns the latest version; otherwise `edited` `{base_version, version = latest +
1, draft, diff: {changed: [{field, from, to}]}, as_of}`. `regenerate(key)` (the system, or a human with authority)
adds a draft attempt to an undecided T1 (`unknown_key`, `terminal`, `no_draft` for T0,
`conclusion_no_longer_supported`).

### 16.6 Execute at most once, and the crash window

`execute(key, as_of)`, in one transaction under the lock: an unknown key is refused; a stored `executed` returns its
result byte for byte with no entry and no executor call; a stored `outcome_unknown` returns it; an `executing`
without a closing entry is `in_progress` when this process runs it and otherwise gets one `outcome_unknown`
`{reason: interrupted}` without an executor call (a crash happened); then `rejected`, `not_approved`,
`conclusion_no_longer_supported`; a kill switch that is on appends `blocked` `{reason: kill_switch, source, as_of}` and
returns `blocked_kill_switch`; else `executing` is committed and the key joins the in-flight set. Outside the lock and
any transaction the executor runs with `ExecContext(view, state, as_of, draft)` (the approved version's draft for T1).
An `Exception` is `outcome_unknown` `{reason: executor_error}` (nothing of it is stored); a `BaseException` (a crash)
propagates and leaves `executing`, which the next call turns into `interrupted`. Otherwise `executed` `{result,
as_of}`. The in-flight set is per process (D12): **one executing service per ledger**.

### 16.7 The ledger (`followups.sqlite3`)

Opened like the other stores (WAL, refused without it; `synchronous=FULL`; foreign keys; a 5 s busy timeout); one
connection per ledger object, usable from any thread, always used under the ledger's lock; every write is one
`BEGIN IMMEDIATE ... COMMIT`, rolled back on anything (a crash included). `ledger_info` pins `schema_version`,
`pack_id`, `config_hash` and `enterprise`; `entries` holds `(seq, at, kind, key, actor, payload, prev_hash, hash)`;
triggers abort `UPDATE` and `DELETE` on both.

**Kinds and payloads** (exact key sets, `ledger.PAYLOAD_KEYS`): `proposed`, `refused` `{op, code, conclusion_id,
type, status, path, keyword, arg}`, `assigned` `{owner, role, unit, assignment_unit, ack_due, approvers_hash}`,
`drafted` `{attempt, version, draft, source}`, `draft_failed` `{attempt, reason}`, `approved` and `rejected`,
`edited`, `executing` `{as_of}`, `executed` `{result, as_of}`, `outcome_unknown` `{reason}`, `blocked`, `escalated`
`{reason, to_role, to, unit, as_of}`, `escalation_failed` `{reason, to_role, problem, as_of}` and `outcome` `{result,
as_of}`: the brief's thirteen plus `drafted` and `escalation_failed` (D9).

**Unique indexes** (partial, on `key`): one `proposed`, one `assigned`, one of `approved`/`rejected`, one
`executing`, one of `executed`/`outcome_unknown`, one of `escalated`/`escalation_failed`. A violation inside an
append is `LedgerConflict` and rolls its transaction back; the other kinds may repeat
(`LedgerChainTests::test_each_partial_unique_index_allows_one_entry_per_key`). Two service instances on one file still
produce exactly one terminal decision: every check re-reads the key's entries inside the `BEGIN IMMEDIATE` transaction
(D12). The indexes are the last line behind the service's state checks: they hold even if those checks are handed the
wrong entries.

**The chain.** `seq` is the last plus one (no autoincrement); `prev_hash` of the first entry is the sha256 of the
canonical `ledger_info`; `hash = sha256(prev_hash + canonical {seq, at, kind, key, actor, payload})`. `verify_chain`
(read-only) reports `seq_gap`, `prev_hash_mismatch`, `bad_entry` (a payload that is not strict canonical JSON with
its kind's keys) or `hash_mismatch` at the first seq it finds, and `unreadable` for any SQLite error, a failing
`PRAGMA integrity_check` or a file without a well-formed `ledger_info`. The chain walk reads the table with a full
scan, while every per-key read of the service goes through the `entries_key` index; `quick_check` (the brief's
choice) never compares an index with its table, so one flipped byte in an index page passed it and the chain walk and
handed the service a key's entries without, say, its `approved` row (the review of round 2 found an edit accepted on an
approved follow-up that way). `open` and `verify_chain` therefore run `integrity_check`, which compares every index
with the table; such a file is `unreadable`, not a traceback
(`LedgerChainTests::test_an_index_flip_that_passes_quick_check_is_unreadable`,
`test_page_and_header_corruption_is_unreadable_not_a_traceback`). Every ledger connection decodes TEXT cells
strictly to a value that is never a string when the bytes are not UTF-8, so a flipped byte inside an entry's `at`,
`key`, `actor` or `hash` cell is `bad_entry` at that seq (inside `prev_hash`, `prev_hash_mismatch`; inside `kind`,
whose value the partial indexes' `WHERE` reads, `integrity_check` finds the index out of step first: `unreadable`),
inside a `ledger_info` cell `unreadable`, and a SQLite error message that quotes a damaged schema name (which Python
would decode into a `UnicodeDecodeError`) is `unreadable` too; none is a traceback
(`LedgerChainTests::test_undecodable_or_retyped_cells_are_a_chain_problem_not_a_traceback`).

**After `open`.** A payload damaged since `open` is `LedgerError('chain:bad_entry', seq)` on the next read of that
row (a state, the entries, the daily-cap count), and so is a text cell that is no longer a string (a row, the head
hash, the hash an append chains to); a read or a write that meets a damaged page (`SQLITE_CORRUPT`, `SQLITE_NOTADB`)
is `LedgerError('unreadable')` and appends nothing; no sqlite3 or decoding error escapes. Any other SQLite error, such
as a busy file, propagates unchanged
(`LedgerChainTests::test_a_payload_damaged_after_open_is_bad_entry_not_a_decoding_error`,
`test_a_text_cell_damaged_after_open_is_bad_entry`,
`test_damage_met_after_open_is_a_ledger_error_and_a_busy_file_is_not`). The limit: damage made while a ledger is open
is found by the read that meets it or at the next `open`; until then a damaged index can still mislead a per-key
read, and the partial unique indexes are what keep one terminal decision and one execution per key.

**The anchoring limit:** the chain has no key, so without an anchor a truncated tail or a complete rewrite passes; G0
and E5 export the head hash and entry count, and `verify --expected-head --expected-entries` checks them
(`anchor_mismatch`). `FollowupLedger.open` refuses a missing file (creating none), an existing path at
`create` (an empty file included), `ledger_info` of another pack config or enterprise, a broken chain and an
impossible sequence of entries.

**Replay.** `replay(entries)` rebuilds every follow-up's state from the ledger alone: pure, no executor, no I/O; a gap
or an out-of-order seq is `chain:seq_gap`, an impossible transition (anything before `proposed`, a second
`proposed`, an executor that is not its tier's, `executed` without `executing`, a second terminal decision, a draft
after a decision, a `drafted`, `draft_failed` or `edited` entry on a T0 key) is `chain:bad_entry`; `refused` entries
never change a state. A state's status is, by precedence, `outcome_unknown`, `executed`, `executing`, `rejected`,
`approved`, then for T1 without a draft `draft_failed` (an attempt failed) or `awaiting_draft`, else
`awaiting_approval`.

### 16.8 Assignment and escalation

The assignment unit is the common unit-path prefix of the conclusion's decision unit and the targets' decision unit
(their lowest common ancestor; the targets' alone when the conclusion has none), so the owner can always approve, even
when a contributing site lies outside the confirming sites' subtree. The owner is the holder of the type's
`owner_role` at that unit or its nearest ancestor (the smallest label when several hold it there), and
`ack_due = as_of + ack_days`. Without a holder, `assigned` has a null owner and the escalation is written at once:
`escalated` (`unassigned`) to the `escalate_to_role` holder found the same way, or `escalation_failed`
(`no_escalation_role` when the type has none, `no_holder`). `overdue(as_of)` escalates, once each (the unique index),
every follow-up still awaiting a draft or a decision, not acknowledged (no approve, reject or edit) and without an
escalation entry, whose `ack_due` is strictly before `as_of`; a missing approvers file gives `escalation_failed`
(`approvers_unavailable`).

### 16.9 Packets and drafts

**Packet request** (in; `edge/egress.py`): `schema_version`, `pack`, `pack_hash`, `followup_key`, `question_id`,
`candidate_key`, `window`, `as_of`; the key's conclusion id must be the question's, the candidate key an egress type,
a canonical id and a pack predicate, the window ordered and closed at `as_of` (and, at the site, by the site's clock).

**Packet** (out): `schema_version`, `pack`, `pack_hash`, `site`, `followup_key`, `question_id`, `candidate_key`,
`window`, `status` (`ok`, `no_confirmed_records`, `no_verdict`), `verdict`, `support_bucket`, `roots_bucket`,
`reporters_bucket`, `evidence_ref`, `truncated`, `codes [{code, n}]` (`n` in `packet_labels`) and `co_mentions
[{entity_type, entity_id, n}]` (`n` a bucket from k up). No total, no record handle, no text. Cross-field checks, in
order: the key's conclusion id; the window; status against verdict (`ok` needs a confirm, `no_confirmed_records` a
refute or an unknown, `no_verdict` none); buckets and reference against verdict; no codes or co-mentions unless `ok`;
codes strictly sorted; never exactly one `'suppressed'` code among two or more (`suppression`); co-mentions strictly
sorted, canonical, and never the key's entity. `PacketAssembler` (`edge/packets.py`) answers from the verdict the site
stored for the question (refusing, after taking the request in, a verdict of another window or a question log naming
another entity), counts codes and co-mentions over the confirming records only (D6; LEAKAGE section 10), writes the
full packet inside the site, never overwriting one, and sends the summary. `PacketExecutor` asks the targets in
sorted order with a deadline each and returns `complete`, `partial` or `failed` with the unavailable sites' reasons
(`no_handler`, `error`, `timeout`, `invalid`). A late handler is left out of the result but not cancelled: it still
finishes, so its site keeps the full packet and its summary may still reach HQ's receive log (where G0 scans it) while
the ledger's result lists the site as `timeout`.

**Draft.** `draft_payload` has exactly `{followup_type, type_label, tier, args, conclusion, packets}`, the conclusion
`{conclusion_id, version, candidate_key, entity_type, entity_type_label, entity_id, predicate, predicate_label, status,
window, confirming_sites, decision_unit, support_lb, roots_lb, reporters_lb, newest_week}` and each packet `{site,
status, verdict, support_bucket, codes, co_mentions}`: structured only. `DraftWriter` runs the task `draft_followup`
(data class `structured`, 2,048 tokens) on a `central` runtime (a runtime bound elsewhere is refused) with one repair,
or fills `template_draft` without one; the draft must pass the type's `draft_schema` and the id-scope scan, else
`draft_failed` with a reason from `DRAFT_FAILURE_REASONS` (the inference error kinds and `out_of_scope_id`). The
ledger ref is `d:<last 16 of the key>:<attempt>`, never a record ref.

**The template drafter and source attribution (B1).** The template drafter fills each property from the source its
type's pack `template` names (`PACKS.md` section 1.3; `drafts.template_sources` gives one word per property, a list
joined with `+`, such as `summary+evidence`): `headline` is `'<type label>: <predicate label> on <entity type label>
<entity id>'`; `summary` is `'<predicate label> on <entity type label> <entity id>: supported, <n> confirming site(s),
weeks <start> to <end> (conclusion <id> v<version>)'`; `evidence` is one clause per packet with status `ok`, in payload
order (`'<site>: <verdict>, support <bucket>'`, then `', codes: <code label> (<n>), ...'` and `', named with: <entity
type label> <id> (<n>), ...'`), joined with `'; '`, or `drafts.EVIDENCE_NONE` without an ok packet (a refuting or
silent packet adds no clause); a list joins its parts with a space; `for_owner` is `drafts.FOR_OWNER_TEXT` for a
string and `[]` for an array; `entity_ids:<type>` is the sorted unique ids of that type among the conclusion's entity
and every ok packet's co-mentions; `confirming_sites` is the conclusion's confirming sites. A string longer than its
`maxLength` loses whole evidence clauses from the end, then the evidence part, then is cut at the last space (inside a
token only when it has none); an array is cut to `maxItems`. The schema check and the id-scope scan apply as before:
every id the evidence names is the conclusion's or a co-mention already in the scope. Up to B1 every required string
was `'<type label>: <summary>'` and every array `[]`, so the committed CAPA draft's title, problem statement and
containment were one sentence and its affected lots were empty, although the conclusion is about a lot (the
superseded G8 run, `docs/collective/evidence/superseded/collective-halvern-g10/`).

The demo's scorecard gives each draft field and list its **source** (`fields [{name, value, source}]`, `lists [{name,
items, source}]`; the block's own `source` stays the ledger's, `generated` or `edited`). `collective_demo.field_sources`
returns the template's sources for a generated draft whose provider label is the stand-in, the template or a local
test server (no model wrote it), `model` for a generated draft from a routed model (which ignores the template), and
`edited` for a human edit. The screen shows a field or list sourced `for_owner` as "left for the named owner to write"
(not its filler value), and an empty list from another source as "none in the conclusion or the packets"; up to B1 an
empty list read "none listed".

**Outbox** (T1 executor): one canonical line `{schema_version, key, conclusion_id, type, tier, version, draft, owner,
approved_by, targets, label}` (the built-ahead label), flushed and synced; the result is `{outbox, line_sha256,
version}`.

### 16.10 The outcome check

`outcome.evaluate` is pure: over the key's cells at the contributing sites, both channels, with `'<k'` counted as
`[1, k - 1]` (G4's imputation), it compares a post window of the original window's length (default the next week on;
one overlapping the original window is `OutcomeError`) against the `baseline_weeks` before the original window:
`expected_ub = max(lambda_floor * sites, baseline_ub / B) * L`, `expected_lb = baseline_lb / B * L`. First match:
the post window not closed at `as_of`, or a site's coverage ending before it, is `insufficient_data`
(`post_window_incomplete`); more than half the post cells `'<k'` is `insufficient_data` (`mostly_suppressed`);
`post_lb >= 1` and `poisson_sf(post_lb, expected_ub) < alpha_site` is `recurred`; `post_ub <= expected_lb` is
`not_recurred`; otherwise `insufficient_data` (`inconclusive`). Every result carries **"measurement only; not causal,
no counterfactual"**: a recurrence after a follow-up says nothing about the follow-up's effect. `check_outcome` is
refused before execution (`not_executed`) and records one `outcome` entry per distinct result.

### 16.11 E5: the injection smoke

`experiments/e5_injection.py` plants instruction-shaped English sentences naming a fresh id (the type's label, the
id, a predicate term, an imperative to open, approve or target a follow-up) in 1% of one site's narratives, adds the id
to that site's master data, runs G0's stages and counts the artifacts holding the id: supported conclusions,
proposals, drafts, outbox lines, ledger entries and packets must all be 0 while HQ's cells name it (the positive
control); a second variant at two sites is recorded, never asserted. **It is a plumbing smoke, not E5:** the
extractors here are the lexical extractor or a fake replaying it, so injected text is inert by construction, and a
coordinated campaign at two or more sites is indistinguishable from real records for any extractor; the defences
are independence and human approval. On the device pack (supplier `V9999`) and the claims pack (repair shop
`RS-99999`), seed 11, 1,000 records (synthetic, same-author, not a measurement), every hit is 0 and HQ holds 3 and 1
cells naming the id; the two-site variant's hits are also 0.

### 16.12 Decisions that refine the brief (D1 to D12)

- **D1. Approvers live in their own file** (`approvers.json`), validated against the org and the pack and read at
  every use; `detect/org.py` is unchanged (its sha256 is pinned, its hash is stored in every detection run, and every
  org file would otherwise need approvers).
- **D2. Pack args.** `evidence_packet` takes `{conclusion}`; `scar_draft` `{conclusion, supplier_id}`;
  `siu_referral_draft` `{conclusion, priority}`; only `config_hash` changed (`PACKS.md` section 1.3).
- **D3. Every tier needs one human approval**, T0 included (the requester is always the system, which never decides,
  and the T0 summary crosses the Boundary); T0's version is 1 and an edit of it is `no_draft`.
- **D4. The key covers the targets**: the same args sent to other sites are another follow-up; key order and Unicode
  normalisation never change a key.
- **D5. Targets** default to the contributing sites; given ones must be a non-empty, duplicate-free subset.
- **D6. Packet counts leave as buckets** (`'suppressed'` or k up), with complementary suppression; a co-mention needs k
  records, comes only from structured master-data fields, and an id below k is omitted.
- **D7. Packet requests and packets are Boundary artifacts**, logged in `hq/packet_requests.jsonl` and the receive
  log; `questions.jsonl` keeps questions only.
- **D8. The draft scope scan checks types with an id format only** (exact and variant mentions, mentions by one of
  the type's aliases, and unresolved lookalikes); the aliases of alias-only types are ordinary words (the device
  label "Display fault" reads as the component alias) and pass. Audit round 3: the scan used to skip every alias
  mention, so a draft or an edit naming an out-of-scope product, supplier, repair shop, clinic or tow operator by its
  name ("FlowLine Pro", "Harbourside Panel Works") passed while the same draft naming the id was refused; the
  aliases of a type with an id format are proper names, so they are now checked like the id.
- **D9. Ledger kinds** are the brief's thirteen plus `drafted` and `escalation_failed`.
- **D10. The approve, edit and reject call sites** are the service, G0's simulated owner, a later demo console and
  the tests (`ApprovalCallSiteTests`); E5 runs G0's stages and calls none of them.
- **D11. HQ is read only through `HqReader`**, one `mode=ro` connection per call; the service never writes HQ's store.
- **D12. One executing service per ledger.** Every check runs in a `BEGIN IMMEDIATE` transaction that re-reads the
  key, so decisions from two instances are safe; the in-flight set that tells `in_progress` from `interrupted` is per
  process. When a second instance executes a key while the first is running it, the executor still runs once, but
  the second sees a foreign `executing`, records `outcome_unknown` (`interrupted`) and closes the key, so the first's
  `executed` is refused and both return `outcome_unknown` while the side effect (a packet set, an outbox line)
  exists. A deployment with more than one replica therefore needs a lease or a single executing worker (INTEGRATION
  G7, merge note 7).

Deviations, each recorded in `INTEGRATION.md` (G7): the full packet is kept at `packets/site-<id>/` (sites can share a
work directory, as G0's do); the template's summary reads "supported, <n> confirming site(s)" (two letters, a space
and a number read as a device product id); the service takes no clock (the ledger stamps `at` with its own and every
decision uses the caller's `as_of`); a ledger refusal reason `wal` and `conflict` beside the brief's; `outcome.evaluate`
also takes the conclusion id and candidate key its result names.

### 16.13 What G7 does not show

- **No X4 and no E2.** Nothing here says that approval-routed follow-up saves time, that drafts are good or that
  pushdown with a small model works. The layer is built ahead of both and is unvalidated.
- **Synthetic only, simulated approvals.** Every G0 and E5 run uses synthetic, same-author worlds; its approvals are a
  simulated owner (`simulated_approvals: true`); its drafts are a template (or a fake replaying it). No figure here is
  a product number.
- **E5 is a smoke, not E5.** Injected text is inert for the lexical extractor by construction; a real in-boundary
  model may behave differently, and a coordinated campaign at two or more sites cannot be told from real records.
- **The text scan is text only** (`LEAKAGE.md` sections 7 and 10): a packet's codes and co-mentions disclose
  co-occurrence as buckets, and a drafting model's prose could restate a count or an id in another form.
- **No fabric wiring.** Fabric event kinds, JetStream subjects, auth scopes and anchoring the ledger head in the
  fabric's signed log are integration notes (`INTEGRATION.md`, G7), not code.

## 17. G8: the collective demo

**A fictional company, synthetic data and a constructed illustration. Internal use only; never a measurement**
(STRATEGY section 12; section 9.1, the YC demo's rules, puts synthetic-fixture results of any kind off the YC screen,
so showing this run to YC is the founder's decision, `demo/collective/README.md`). Every run file says `measurement: false`, and no number from the demo is
a product figure.

G8 runs the whole loop once, end to end, for one fictional multi-site device maker (Halvern Medical, six plants in
four countries) and shows it on a console: sense (each plant extracts claims inside its boundary), detect (X over the
k-suppressed cells, next to S and R), check (pushdown verification at the plants), follow-up (approval-routed, built
ahead of X4) and the outcome ("not yet checked"). The hero is the narrative-only case: complaints about one lot carry
only a generic malfunction code, and what failed is written only in the narratives, in English and German. Nothing in
the loop changed: `detect/`, `pushdown/`, `followup/`, `edge/`, `evaluate/`, `inference/` and `packs/` (pack data and
hashes included) are byte-identical to G7. G8 adds one module under `mycelic/` and a demo directory.

| Part | File | Purpose |
|---|---|---|
| Run-file contract | `mycelic/collective/runfiles.py` | the six names, digests (32 hex), ledger and approvals projections, portability checks, `content_hash`, RFC 6901 `src` resolution, atomic write (scorecard last) and strict read; shared by G0 and the demo (`mycelic/` never imports `demo/`) |
| Scenario | `demo/collective/scenario.json`, `scenario.py` | the fictional org, approvers, follow-ups and five items (hero, sibling, three decoys) over the pack generator's background world; validation in a fixed order; `build_world` (records, canaries, case keys, `by_construction`, structured fill) |
| Engine and server | `demo/collective/collective_demo.py` | `DemoEngine` (prepare, check, follow-up, finish), the closed run-file schemas and `validate_run`, `--record`, `--serve`, `--replay`, `--export`, the console server |
| Screen | `demo/collective/screen.py` | `build_screen`: every value an item read from a primary run file; the formats; the static texts |
| Console | `demo/collective/console.html` | renders `screen.json` only; live (`/screen`, `/events`, `/control`) or embedded (replay and export) |
| Number lint | `demo/collective/lint_numbers.py` | fails the build when a number on screen cannot be traced to a primary run file |
| Talk track | `demo/collective/SCRIPT.md`, `README.md` | the 60-second cut and the extended internal beat; what the demo is and is not |
| Committed run | `demo/collective/recorded/<run-id>/` | the six files of one fake-mode recording |
| G0 | `experiments/g0_canary.py` | the `run_files` stage (section 17.6) |

### 17.1 Data flow

```
 scenario.json ─► build_world: background (pack generator) + items, canaries planted; manifest to <workdir>/private
                                   │
 per plant (one process): EdgeSite ingest ─► extract (stand-in, or --routing) ─► weekly cells, usage ─► Boundary
                                   │                                                          │
 HQ: CollectiveStore ◄─────────────┴───── receive.jsonl ◄────────────────────────────────────┘
      X, S (detect) · R_mf, U, each site alone (evaluate.baselines) ─► hero detection block, decoys
      check: Orchestrator.verify_stored(hero key, as_of) ─► questions ─► SiteVerifier at each routed plant ─► verdicts
             ─► gate ─► scan "after_pushdown"
      follow-up (supported only): T0 evidence packet (approve, execute) ─► T1 CAPA draft (approve, execute)
      finish: scan "final" (every crossing + four run files) ─► documents ─► screen.json ─► self-check ─► write
                                   │
 run directory: scorecard.json trace.json ledger.jsonl leakage.json approvals.jsonl screen.json
                                   │
 console (live SSE, or one page with screen.json embedded) · lint_numbers.py · --replay · --export
```

The clock is simulated (as in G0): ingest and extraction run at the day after the last record, then the clock moves
to `as_of` (that day plus seven days plus the pack's close lag), where detection, the check and every follow-up step
happen. The wall clock reaches only `created_at`, `recorded_at`, the timings and the trace's `t`.

### 17.2 The run-file contract

A run directory holds exactly six files (`runfiles.RUN_FILES`): `scorecard.json` (the hero's detection, pushdown and
follow-up blocks, the decoys, the providers, the checks, the stamps and the content hash), `trace.json` (meta, the
org, the beats and every event), `ledger.jsonl` (HQ's own model calls only, at most twelve rows per task, projected
to `LEDGER_ROW_KEYS`; never a host, ref, timestamp or served model), `leakage.json` (the two scans, the positive
control, what is not covered), `approvals.jsonl` (every follow-up ledger entry, then one `ledger_head` line) and
`screen.json`. The first five are the **primary** files; `screen.json` is derived from them.

- **Closed schemas.** Every object has fixed keys (no free-key maps: per-site and per-task data are arrays), checked by
  `validate_run`. The baselines' blocks are exactly `{rank, caught, related}`: no count, feature, score, rate or
  log-probability of R, U or a single site appears in any run file, and an alert event carries only channel, key,
  week and rank.
- **Digests are 32 hex.** Every sha256 is written as its first 32 characters (`runfiles.shorten`, `digest`); a string
  holding a 64-hex run inside longer text is refused rather than kept. The screen shows 12 (`digest12`).
- **Portability.** `portability_problems` refuses an absolute path, the work and output directories, the repository
  root, the home directory, the host and user names (whole tokens), a 64-hex token, a credential-named key and the
  agent-key prefix. `write_run_files` checks every file first and writes nothing on any problem; it writes atomically,
  `scorecard.json` last, so a run directory with a scorecard is complete.
- **Content hash.** `content_hash` is 32 hex of the canonical scorecard without `/code`, `/content_hash`,
  `/created_at`, `/run_id` and `/timings`. A fresh recording of the committed scenario reproduces the committed
  run's hash under any `PYTHONHASHSEED`, work directory and run id; the tests check it, so the committed run is
  genuine and current.
- **A site's usage only as it crossed.** `runfiles.project_ledger` refuses any row of a `site:<id>` runtime: a
  site's per-call ledger never leaves it, and its exact call counts are site data below k (one judge call per judged
  record, so a site's per-call rows for one question give its exact judged count). A site's usage appears only as
  `scorecard.ledger.site_usage` (`runfiles.crossed_usage`): every group of every `usage_summary` in HQ's receive log,
  as the site sent it, a `'<k'` count written as null and named in `suppressed`. `ledger.scope` says so, and the
  providers block gives a site task's `calls` as null, keeping only booleans read from the site ledgers (any call,
  every call to an in-process fake, every call fake-marked) for its label. Up to G8 the run-file ledger copied up to
  twelve per-call rows per site and task, which leaked those sub-k counts into a committed file.
- **What never enters a run directory:** narrative text, record refs, person or reporter values, the canary manifest,
  the site databases, the site ledgers and the site `packets/` directories. The work directory holding them is
  removed at exit.

### 17.3 Screen items and provenance

`screen.json` is `{kind, schema_version, run_id, mode, presentation, phase, beats, cut_60s, controls, blocks,
items}`. `mode` is how the run was made (`record` or `live`; the footer item shows "scripted run" or "run driven live
in the console"); `presentation` is how the screen is shown now, and the console's badge reads only it: `live` while
the console is attached to the engine running the run (`--serve`), `recorded` for `--record` and always for
`--replay` and `--export` (`screen.presented`). Up to G8 the badge read the mode, so a replay or an export of a run
made with `--serve` showed a green LIVE badge with nothing running behind it. A block is a list of parts, each
exactly one of a static `text` or an `item` id. An item is `{id, beat, label, display, src, fmt}`:
`src` is `<primary file>#<RFC 6901 pointer>` (a `.jsonl` pointer starts with the line index), and `display` is
`screen.format_value(runfiles.resolve(docs, src), fmt)`. No other code formats a value: the formats (`int` with
thousands separators, `rank` with "not alerted" for null, `pct`, `dec2`, `text`, `digest12`, `yesno`, `mode`,
`approval`, `datetime`) live in `screen.py` with a fixed locale, and `parse_display` reads each display back. Every
run-derived string (display names, ids, labels, the question, reasons, draft text, the run id and time) is an item;
static texts are module constants with no digit and no number word. The console renders `item.display` with
`textContent` and computes or formats nothing.

Beats: the problem, the alert, the check with the sites, the approval-routed follow-up (`in_cut: false`; built ahead
of X4, not measured) and the real-data line. `cut_60s` is the four others.

### 17.4 Caught vs related

`caught(channel)` means the channel alerted the hero key in the hero window (from the hero's first week through the last
closed week); `related(channel)` lists its first alert on every other **case key** in that window. Case keys are the
hero's entities (the key's, every structured entity of a hero record and every entity the lexical extractor finds in a
hero narrative) times the hero's predicates (the key's and every hero code's), egress types only. A caption names the
hero key as "this failure mode", and each channel row's rank is for it; the S and R rows also name the **first other
case key** each flagged, with its own rank and week ("first other key of this case it flagged"), or say that it flagged
none. On the committed run both baselines rank the lot or the product under the generic malfunction code first: up to G8
their rows said only "not alerted", which read as if they had missed the lot, and the talk track said that "nobody
connects" the complaints. The screen says "The restricted central baseline also caught this case" (R) or "The codes-only
baseline also caught this case" (S) exactly when the flag is true, lists every related key with its own entity,
predicate, rank and week under "flagged a related key", and never calls a related key a miss. The by-construction
caption ("By construction, S and R cannot see this key: ...") is shown exactly when the scenario makes the hero
narrative-only: it is a property of the constructed case, not a result. A scenario copy with a specific code and the
structured lot always filled makes both baselines catch the hero, and the screen says so (`HonestyTests`).

Every channel's block carries `detection_week`, its first alert week on the hero key (audit round 2; up to then only
X's did). The references row shows each site alone with its rank and, when it caught the key, its week, and a warning
says when one plant alone caught it no later than X ("... so it shows no collective lift") or later. In the committed
run one plant alone flags the hero key in X's own week (2024-W35): STRATEGY 6.1 measures collective lift against
exactly that baseline, so this constructed case shows none, and it is not a hidden pattern in 6.1's sense. Up to round
2 the row gave only the rank, and the talk track never said it. Rebuilding the case so that no plant's own baseline
fires is a scenario decision left open (`INTEGRATION.md`, audit round 2).

### 17.5 The lint

`lint_numbers.py RUN_DIR` reads the six files, the console, `SCRIPT.md` and `README.md`, and prints one
`lint: <file>: <rule>: <token>` line per violation (exit 1), `lint: ok (...)` (exit 0), or one `error:` line (exit 2:
a missing directory or file, invalid JSON, another schema version). Rules: `schema` and `run_id_mismatch`;
`src_not_primary`, `src_unresolved`, `src_not_scalar`; `display_mismatch` (the display differs from the formatted
source, or does not read back to it); `digit_in_text` and `number_word` over screen text parts, item labels, beat and
control titles, the console's visible text, `title`, `alt`, `placeholder` and `aria-*` attributes, its script string
literals (escapes decoded; CSS lengths, durations, colours and colour or transform functions allowed) and every
`SCRIPT.md` line but the time column (a short list of identifiers such as X4, N1, Phase-1 and 60-second is removed
first); `console_markup` (an ordered list, a progress or meter element, a CSS counter or a decimal list style, which
render digits that are in no run file); `aggregate_metric_key` (a screen key or item id naming recall, precision,
lift, AP, F1, AUC or accuracy); `denylisted_phrase` and `benchmark_figure` (STRATEGY section 9.1) in all four files.

### 17.6 The two scans, the self-check and G0

`after_pushdown` scans what crossed when the check finished (cells, usage summaries, questions, verdicts, the site
egress and ingress logs, HQ's database); the check beat shows it, so a live run can too. `final` adds the follow-up
crossings (packet requests, packets, the follow-up ledger, the outbox, the draft ledger and every draft) and the four
primary run files that exist by then (class `run_files`). The positive control scans the first plant's own database
and must find canaries and narrative text. After `screen.json` is built, a self-check validates all six documents,
scans their final bytes for canaries and narrative shingles and runs the portability check; any hit stops the run
with nothing written. G0 gains a `run_files` stage (after `followup`): it writes a `run/` directory (`ledger.jsonl`,
`approvals.jsonl`, a `g0_trace` and a `g0_scorecard`) with `runfiles`, and the stage's four files are scanned as
class `run_files`; a stage that writes narrative text there fails the run (`LEAKAGE.md` section 11).

### 17.7 Modes, the server and failures

`--record` runs the loop headless with scripted approvals by the named owner (labelled recorded; each execute is
requested twice to show it runs once). `--serve` runs the same engine behind a `ThreadingHTTPServer`: `GET /`,
`/screen`, `/trace` and `/events` (SSE: a snapshot, every event with its `id`, a new snapshot when the screen
changes; a `Last-Event-ID` at or beyond the end restarts from the start), and `POST /control` with `next`, `check` or
`approve` (by follow-up key). Actions are queued to the main thread; a control at the wrong beat or after completion
answers 409, a bad body 400, a second press while one runs `busy`, and a repeated check or approval `done_before`
with no effect. `--serve --cut` gives the 60-second cut live (audit round 2): the server walks only `CUT_60S`
(problem, alert, check, real data), runs the follow-ups with scripted approval when the presenter presses Next after
the check (stamped `approval: recorded`, two execute requests each, as in `--record`), and `screen.json` says
`cut_only: true`, so the console shows only the cut and hides its full-version button. Without `--cut`, the console's
cut button only filters the view, and the beat the engine is on always stays visible: up to round 2 it hid the live
follow-up beat, which left an untitled page whose approve buttons and way on to real data could not be reached.
`--replay` serves a recorded run with no engine (every control 409) and `--export` writes it as one
standalone page (no network, under 2 MB). A routed endpoint that does not answer `GET /models`, an extraction that
falls back, or a judge that degrades stops the run with one line naming the endpoint and the replay command, and
nothing is written; in `--serve` the console shows the error and stays up. Ctrl-C, SIGTERM and SIGHUP exit 130 with
nothing written.

### 17.8 What G8 does not show

- **No measurement.** The world, the hero and the decoys are synthetic and written by the same author as the detectors
  and the pack; the stand-in model reads the pack's own sentences perfectly. The committed outcome (`INTEGRATION.md`,
  G8) is an illustration of the narrative-only case, not evidence that X beats S or R.
- **R here is model-free.** It is not STRATEGY's R, which includes a frontier model reading the allowed fields; E2's
  `central_allowed` condition approximates that R.
- **No X4 and no E2.** The follow-up beat is built ahead of X4 and unmeasured; pushdown with a real small model is E2,
  which has not run. With `--routing` to a model the plants share one model in one process (section 17.9).
- **Text only.** The scans show that planted text did not cross; they say nothing about what counts and buckets reveal
  (X5, `LEAKAGE.md` section 7).
- **No fabric wiring.** The console reads run files, not fabric events (`INTEGRATION.md`, G8 merge notes).

### 17.9 B1: the first scenario's run, re-recorded

The final review of phase 2 found that the committed G8 run shows no case the allowed fields miss: R (model-free) flags
the product under the generic code at the top rank in X's own week, and S flags the lot a week later (the run is kept,
byte-identical, as `docs/collective/evidence/superseded/collective-halvern-g10/`, with a README pointing into its
scorecard). B1 keeps the first scenario unchanged and adds, in its first commit (B1a), what the review found missing
around it. A second commit (B1b) was to add the constructed codes-miss scenario that `docs/collective/b1/PREREG.md`
fixed before any of its worlds existed; **B1's constructed codes-miss illustration has not been built**: no B1b
commit, codes-miss scenario, attempt or run exists (`INTEGRATION.md`, audit round 4). What B1a changed:

- **The draft** is filled from the conclusion and the ok packets through the pack template, each field with its source
  (section 16.9); the screen leaves the `for_owner` field to the owner.
- **X by construction.** `detection.by_construction` gains `X` (bool) and `x_reason`: `X` is true when the extract
  provider label is the stand-in, a local test server or the template (none of them a model), with `x_reason` =
  `collective_demo.X_BY_CONSTRUCTION` ("the hero narratives were written by the scenario author in the pack lexicon
  and read by the deterministic lexical handler; extraction is not tested here (see E1 and N1)"); otherwise false and
  null. The screen shows it as a caption beside the S and R one: "By construction, X reads this case perfectly: "
  followed by the reason as an item. It is a property of the stand-in, not a result: up to B1 the screen said why S and
  R cannot see the key and nothing about why X could.
- **One wording for the sites.** `collective_demo.sites_stamp(routed, providers)` gives `stamps.shared_model` and the
  trace's `sites_note`: a routed run whose extract or judge calls went to a model shares one model across the simulated
  sites (`SITES_NOTE`, "sites simulated in one process, one shared model"); a routed run whose site calls all went to a
  local test server shares none (`TEST_SERVER_SITES_NOTE`, "sites simulated in one process; every site's calls went to
  one local test server (fake marker)", and `shared_model` false); without routing there is no note. Up to B1 any
  routed run said "one shared model" while its provider labels said "no model".
- **The committed run** is `demo/collective/recorded/collective-halvern-b1a` (scenario digest unchanged); its
  detection blocks are the G8 run's, so R (model-free) still flags a key of this case in X's week and the screen still
  says so. That is why B1 planned a second scenario rather than re-reading this one; since it was not built, no
  committed run shows the case STRATEGY 9.2's cut needs.

## 18. B3: X5, leakage beyond text

**Synthetic, same-author and internal only.** The worlds, the red team and the defences come from the same AI system,
on the packs' synthetic generators, in fake mode; X5 here describes these artifacts on these worlds and is never a
buyer claim (`x5_attacks.STATEMENT`). The published figure is `LEAKAGE.md` section 12, filled from `x5.json`; the
commands are RUNBOOK section 18.

| Part | File | Purpose |
|---|---|---|
| Orchestration | `experiments/x5_inference.py` | the CLI (`prereg`, `run`), the variant pack copies, the worlds and the member split, one G0 pipeline per (pack, variant, seed), the A6 probe, the fact extractors, the targets and their truth, the shadow statistics, the derived variants and simulated transforms, the closed schemas, the self-scan and `leakage_section` |
| Attacks | `experiments/x5_attacks.py` | pure: `Fact` and `FactIndex`, what the attacker knows per target, A1 to A6, the applicability table, the labels, the per-target values, `summarise` and `incremental`; it imports `stats` only (no file, store, site, generator or model code: `X5AttacksImportGuardTests`) |

No other file under `mycelic/` changed: G0, the detectors, the Boundary, the packs and `stats.py` are byte-identical
to B2a, and X5 adds no SQL of its own (it reads stores through their own methods).

### 18.1 Threat model and holdings

The red team is HQ. It holds every artifact HQ holds after G0's stages on a world (edge, pushdown, follow-up, run
files), one artifact type each: `cells_codes` and `cells_text` (the cell bundles split by channel), `usage_summary`,
`verdicts_passive` (HQ's own questions and the verdicts that answered them), `packets`, `followup` (the follow-up
ledger's entries, the outbox and the central draft ledger), `hq_results` (the detection result, recomputed with the
run's channel and tie salt and checked equal to the stored candidates, and the latest conclusion row of every
conclusion) and `run_files` (`trace.json`, `approvals.jsonl`, `scorecard.json`, `ledger.jsonl`); `all` is the union.
It may also ask questions of its own through a real `SiteVerifier` per site, under the pack's budgets
(`verdicts_active`, A6). It knows the pack, the lexical extractor and every site's master data. It never sees a site
database, a narrative, a person or reporter value, a site's `packets/` directory or the generator's gold.

Two reference types are not HQ holdings: `allowed_fields_reference` is R (model-free)'s exact per-record cells, the
fields policy lets a central system read (`baselines.r_mf_cells`, codes channel), and `allowed_plus_all` adds HQ's
artifacts to it; the incremental entry measures what the artifacts add over the allowed fields on the same targets.

### 18.2 Data flow

```
prereg:  packs -> variant copies (hashed) -> worlds per seed (digested) -> prereg.json
run:     check pins (code, packs, copies, worlds)
         per pack: shadow worlds -> priors and A1 thresholds (lexical rows, build_cells; no pipeline)
                   per pipeline variant, per seed:
                     make_world(2n) -> split -> members -> build_context + run_stages (fake) -> boundary mark
                     -> passive facts (receive log, questions, packets, ledger, outbox, store, run files)
                     -> A6 probe (fresh EdgeSite + SiteVerifier per site, from as_of + 1 day) -> active facts
                     -> true rows (RecordStore.emission_inputs), R_mf cells -> delete the world directory
                     -> targets and truth -> attacks -> outcomes
                   default worlds also feed k1_reference, a5_injected and the simulated transforms
         summarise (Wilson, cluster bootstrap, labels) -> controls, bar -> schema, portability, self-scan
         -> x5.json, leakage_section.md
```

The work directory holds the variant copies and one world's pipeline at a time; it is removed at the end and on
every refusal. Every world is generated from the base pack, except volume worlds, which come from the volume copy
(the generator reads the volume and the egress types), so variants of one seed share members and `as_of`.

### 18.3 Facts

`Fact(source, site, first, last, entity_type, entity_id, predicate, channel, field, lo, hi, text)` says that an
artifact of type `source` puts the quantity `field` of (entity, predicate, channel) at `site` over the week indexes
`[first, last]` (weeks counted from the generator's start, so the attacks never parse weeks) in `[lo, hi]` (`hi`
null: unbounded). A predicate of null is an entity-level fact; a channel of null sums both channels (a record's claim
for a key is in exactly one channel). The extractors:

- **cells**: `n`, `n_roots` and `n_reporters` per cell (`'<k'` is `[1, k-1]`), and one `covered` fact per week and
  channel of each bundle's span, so a covered week without a cell for an egress-able key reads as 0;
- **usage summaries**: `calls` per group over the summary's span, with no entity;
- **verdicts** (joined to their question): a confirm's support, roots and reporters buckets over the window, support
  at least 1 in its newest week and 0 in every later week of the window; a refute's zero support and its entity-records
  bucket; an unknown without a wire reason, for an id that passes the site's master-data rule, as zero records (an
  `unclear` unknown is misread as absence, which counts against the attacker); `budget` and `no_secret` say nothing;
  a truncated verdict leaves every bucket open above;
- **packets**: the stored verdict's buckets, each pack code as a lower bound on its predicate, each co-mention;
- **follow-up**: the packets inside executed results, and every egress id a draft, outbox line or ledger payload
  names, as presence over the conclusion's window at its confirming sites;
- **HQ results**: per candidate snapshot each site's window count and the contributing sites; per conclusion its
  confirming sites, its newest week and its refuting siblings;
- **run files**: `trace.json`'s verdicts (no truncation flag there, so buckets are open above) and `approvals.jsonl`
  as follow-up;
- **strings**: every string leaf of every item, per (source, site, span), deduplicated; A5 reads only these.

### 18.4 Targets and truth

Truth is computed apart and meets a prediction only in `x5_attacks.outcome`; no attack function takes a truth
argument (a test inspects the signatures). Forwarded copies are never targets. Clusters are (seed, site, week), and
(seed, site, window start) for A6.

- **A1**: original records of each (site, week) stratum, every record of the smaller class and a seeded sample of
  the larger, so the baseline is 0.5 exactly; single-class strata are dropped and counted. The attacker's keys are its
  own lexical extraction of the record (egress types, the site's master-data rule).
- **A2**: member originals with an affirmed gold predicate and a canonical structured entity; truth is the set of
  affirmed predicates.
- **A3**: every true cell (site, key, week, channel) with 1 <= n < k, from the sites' own emission rows; truth is n
  (never `n_roots`).
- **A4**: every pair of records in a true cell with 2 <= n < k; truth is a shared reporter (an unknown reporter is one
  shared reporter, as `build_cells` counts it). At k = 2 a cell of 2 is exact, so A4 is not applicable there.
- **A5**: member originals times the pack's name fields (the generator's person fields of kind `name`); truth is the
  surname.
- **A6**: (site, entity, window) for a seeded sample of askable ids per site (master-data ids of types with an id
  format, every id of alias-only types); truth is a member record at the site in the window naming the entity in its
  gold claims or its structured entities.

### 18.5 Attacks

- **A1 membership**: the share of the record's keys with a positive fact at its site whose span holds its week,
  counting only the keys of the channels the attacked type can carry (`x5_attacks.A1_CHANNELS`: the codes channel for
  `cells_codes` and the allowed-fields reference, the text channel for `cells_text`, both otherwise); `A1_calibrated`
  predicts member at the threshold calibrated on that type's own shadow facts, so it applies only to the types with a
  shadow analogue (`A1_CALIBRATED_TYPES`: the cell types, `all` and the two reference types), and `A1_fixed` when every
  key is present; no such key takes a seeded coin (uncovered).
- **A2 predicate attribute inference**: per predicate, the number of the record's entities with a positive fact at its
  site and week; the argmax, ties and no support broken by the shadow prior chain.
- **A3 count inference**: interval bounds on the (site, key)'s weekly counts per channel over the connected span of its
  facts, propagated to a fixpoint (cell counts, covered zeros, verdict and packet sums, presence, the zeros after a
  newest week); the shadow mode inside the target's interval, cut to [1, k-1].
- **A4 reporter linkage**: from the target cell's `n` and `n_reporters` only (`n_reporters` at most 1: same; an exact
  `n` with as many reporters: different); otherwise the shadow majority.
- **A5 person names (negative control)**: the field's generator surnames found among the string facts at the site and
  week; on real artifacts nothing names a person.
- **A6 presence oracle**: a confirm or a refute says the site holds the entity in the window.

The applicability table (`x5_attacks.applicability`, copied into the prereg) marks every (variant, attack, artifact
type) as applicable, not applicable (with a reason: usage summaries name no entity; `A1_calibrated` needs a type with
shadow facts to calibrate on; A4 reads cell counts only; A6 reads only the attacker's own questions; the k1 reference
replaces only the cells; the injected control tests A5 only) or not run (A6 on the volume variant).

### 18.6 Shadow

Shadow worlds never run a pipeline. Their lexical rows (`lexical_rows`, equal to what a fake-mode site stores; a test
checks it) are cut into cells at each variant's settings (k, master-data rule, egress types; k = 1 for the reference;
each simulated transform), which give the A1 threshold per setting and artifact type (`a1_threshold_types`), each on the
type's own shadow facts and scoring only the keys of its channels: the two cell types, `all` (the shadow cells of both
channels, its only part with a shadow analogue) and, at a pipeline variant's own setting, the allowed-fields reference
(R (model-free)'s exact cells of the shadow members) and `allowed_plus_all` (both); the threshold is the score among the
sorted distinct shadow scores that maximises shadow accuracy, the smallest on ties. Up to audit round 4 one threshold,
calibrated on the union of the shadow cells, served every type. They also give the A2 prior chain, the distribution of n
below k (A3), the same-reporter share (A4), the surname counts per field (A5), the presence share per (site, entity)
(A6) and the A6 predicate per entity. `x5.json` carries only counts, thresholds and modes from them, never a prior
table.

### 18.7 Variants, controls and mitigations

| Variant | Kind | What changes |
|---|---|---|
| `default` | pipeline | nothing |
| `k2`, `k10` | pipeline | egress k and the verdict buckets (k, then the pack's higher edges) |
| `rmd_flipped` | pipeline | `require_master_data` negated |
| `minus_type` | pipeline | the pack's primary entity type (`mapping.primary_entity_type`) no longer leaves: vocabulary, egress and the question templates |
| `volume` | pipeline | every site's weekly volume times the factor; worlds of factor times 2n records |
| `k1_reference` | derived | the default worlds' cells rebuilt at k = 1 (reference, not deployable); the positive control of A1 to A4 |
| `a5_injected` | derived | a `'<k'` text cell naming each A5 target's surname added to its site's bundle; the positive control of A5 |

The copies are written as canonical JSON into the work directory and their four hashes are pinned in the prereg.
Pipelines run in the order packs, then default, the k variants, `rmd_flipped`, `minus_type`, `volume`, then seeds,
against `--max-pipelines`; a variant that cannot run all its seeds runs none, and a default that cannot run in full
stops the run.

**Controls.** Each positive control must be labelled `leak` on `all` (exit 2 otherwise); the negative control, A5 on
`all` for every pack and measured variant, must be `at_chance` or `inconclusive` with 0 inside its interval (exit 1
otherwise). The injected cells are refused by the Boundary (a test checks `check_artifact`).

**Mitigations.** The measured knobs are the pipeline variants. The simulated transforms of the default worlds,
`drop_lt_k` (cells below k removed, so a missing cell means at most k - 1) and `four_week` (cells rebuilt over
four-week periods at the pack's k), are labelled "simulated: not implemented in the Boundary; implementing it is a
code change"; they report the cells before and after and re-run A1 to A5 on the cell types and `all`, with every
other artifact the unmitigated run's (detection is not re-run).

**Designed disclosures** (STRATEGY 6.4) are reported apart and never labelled: the share of members whose every key
has a `'<k'` or exact cell at their site and week, and the share of exact cells whose count is right.

### 18.8 Statistics and labels

Per entry: the targets, the coverage (the share the attack decided from the artifact), the accuracy with a Wilson
interval, the matched baseline's accuracy, and the advantage: the mean per-target value (A1 `2c - 1`, the others the
attack's correctness minus the baseline's) with a cluster-bootstrap percentile interval (`stats.cluster_bootstrap_mean`,
seed `<bootstrap seed>:<pack>:<variant>:<attack>:<type>`). The label reads only that interval: `leak` when
`ci_low > 0`, `at_chance` when it lies within [-0.05, 0.05], otherwise `inconclusive` (and with no target). The eight
primary tests use B = 10,000 and carry a Bonferroni interval at 0.05 / 8 from the same seed; every other entry uses
B = 2,000 and is exploratory. The incremental entry bootstraps the per-target difference between `allowed_plus_all`
and `allowed_fields_reference`. The bar is `fails` when any primary 95% label is `leak`, `met` when every primary
Bonferroni label is `at_chance`, else `undecided`; its sentence and each primary sentence come from fixed templates.

### 18.9 Outputs

`prereg.json` (`x5_prereg`, closed schema): the packs and their copies' hashes, the code hash (`X5_CODE_FILES`, every
module the run loads; a test checks the import closure), the commit and dirty state, the seeds (derived from the
commit, every candidate recorded), every world digest, the selections, the applicability table, the rules, the
primary family, the bootstrap, the A6 settings, the cap and the NOT_COVERED mapping. `run` refuses, naming each,
any change of the code hash, a pack hash, a copy hash or a world digest.

`x5.json` (`x5_results`, a closed schema built from the prereg): the stamps and statement, the rules, the primary
family, per pack and variant the copies' hashes, per world the pipeline and A6 counts, facts and targets, the shadow
counts and the designed disclosures, the uniform results matrix (every pack, variant, attack and artifact type, with
`status` run, not_applicable or not_run), the primary pointers and sentences, the controls, the mitigations, the
NOT_COVERED mapping, the bar, the timings and a content hash (without `created_at`, `run_id` and `timings`). Every
digest is 32 hex. Before writing, the run checks the schema, portability (no path, host, user or 64-hex token) and a
self-scan (no narrative shingle of any generated world, no person, surname or reporter value as a whole token); a
failure writes nothing. `leakage_section.md` is rendered from the validated document only: the bar sentence, the
primary sentences, the statement and pointer-row tables whose every cell is `format_value` of the field its pointer
names.

### 18.10 What X5 here does not show

- **Fake, lexical extraction only.** The sites run the fake provider, which replays the lexical extractor and judge,
  and the attacker knows that extractor and the master data; a real model's errors are not modelled.
- **Usage covers one ingest week**, because every record is extracted on the simulated ingest day; weekly usage
  volumes are not attacked.
- **Tiny pack universes** (a handful of products and suppliers) make keys collide between records often, so
  membership is likely understated relative to a real catalogue.
- **A4 does not decompose window reporter buckets**; it reads cell-level counts only.
- **Volume is four times the built-in volume, not ten**: the generator runs out of unique narratives above that.
- **A6 is a small sample** of entities per site, not an exhaustive presence oracle.
- **Calibrated membership only where shadow worlds have an analogue.** Shadow worlds run no pipeline, so the verdict,
  packet, follow-up, HQ-results and run-file types are attacked for membership by `A1_fixed` (every key present) only,
  which can understate them, and `all` is calibrated on the shadow cells alone.
- **No detection re-run under the simulated transforms**: they replace the cells and keep every other artifact.
- **Same author.** The red team knows the defences it attacks because it wrote them; an independent red team may
  find more.
