# Mycelic collective: integration notes

Each gate adds a section. These notes are for merging this phase on top of the fabric work that is happening in
parallel (`mycelic/service.py` and its siblings, `deploy/`, `SECURITY.md`, `DEPLOYMENT.md`).

## G1

**Base.** Branch `mycelic-collective-phase2` at base sha 29307f2 (`origin/main`).

**Scope: fabric files changed: none.** G1 only adds files: `mycelic/collective/`,
`tests/mycelic/test_collective_*.py`, `docs/collective/` and `runs/.gitignore`.

### S1 test baseline

| Suite | Before G1 (29307f2) | After G1 |
|---|---|---|
| `python -m pytest tests/mycelic -q -p no:warnings` | 73 passed (7 subtests) | 253 passed = 73 + 180 new, 0 skipped |
| `python -m pytest NeuralGraph/tests -q -p no:warnings -rs` | 222 passed, 1 skipped | unchanged |
| `python -m pytest research/mycelic/test_mycelic.py -q -p no:warnings` | 30 passed | unchanged |

The one skip is
`NeuralGraph/tests/test_single_hop_regression.py::TestSingleHopIntegration::test_precision_at_1_meets_baseline`
("Requires full NeuralGraph setup with data").

The 180 new tests are in four files:

- `test_collective_inference.py`: 114;
- `test_collective_week1.py`: 34;
- `test_collective_guards.py`: 20;
- `test_collective_stats.py`: 12.

They do not use skip decorators, and they connect only to loopback.

### S4: what the suites leave behind (ignored, not committed)

- every `__pycache__/` directory;
- `.pytest_cache/`;
- `demo/results/mycelic-strategic/{lineage.json, lineage.mmd, summary.json}`.

The new tests write only to temporary directories. Nothing under `runs/` is created except `runs/.gitignore`, and no
`*.db` or `*.jsonl` file appears in the worktree.

### The fabric's import chain (checked in a fresh interpreter)

- `mycelic.store`, `mycelic.transport` and `mycelic.lineage` load no NeuralGraph module.
- `mycelic.aggregation` loads only `NeuralGraph.research.coordination.{adapters, contracts, core}`.
- `mycelic.service` loads `mycelic.retrieval`, which imports `NeuralGraph.chat_memory.textutil`. That runs the
  `NeuralGraph/chat_memory/__init__`, which loads:
  - `.extraction`, `.llm`, `.models`, `.retrieval`, `.service`, `.store`, `.worker` and `.jsonutil`;
  - numpy and prometheus_client.
- `mycelic.service` never loads aiohttp or `NeuralGraph.llm_backend`. `chat_memory/llm.py` imports `llm_backend`
  lazily, inside `BackendLLMClient.__init__`.
- **Correction to an earlier note:** importing `NeuralGraph.chat_memory.jsonutil` alone loads numpy and
  `NeuralGraph.chat_memory.llm`. It does not load aiohttp. That is why the collective layer has its own reply parser
  (`inference/jsonparse.py`) instead of reusing `jsonutil`.

The import guard encodes this chain:

- after importing the five core modules, no `mycelic.collective*`, `NeuralGraph.llm_backend` or model-client module
  is loaded;
- a `NeuralGraph.chat_memory` module is present only if `mycelic.retrieval` is;
- the core without `service` loads no `chat_memory` module at all.

### Merge notes

1. **CORE_FILES names the five core files by path:** `mycelic/{service, aggregation, store, transport,
   lineage}.py`, in `tests/mycelic/test_collective_guards.py`. If the fabric merge renames or splits any of them,
   `ImportGuardTests.test_core_files_exist` fails loudly. Update `CORE_FILES` so the guard keeps covering the core;
   never drop a file to make it pass.
2. **G1 needs no fabric change.** The runtime is called only from collective code. Nothing in the fabric imports it,
   and the guard forbids that.
3. **Fabric changes that later gates still need** (STRATEGY section 4.5). None is made in G1:
   - the `claims_only` share mode, so that shared items carry structured fields and a `local_ref` but no free text;
   - a counts table, separate from `Memory` rows;
   - the event kinds `candidate`, `question`, `verdict`, `followup.proposed`, `followup.approved` and
     `followup.result`.

   Later gates write each as an integration note before touching the fabric.
4. **`runs/` is git-ignored** except its `.gitignore`. Run files are never committed.

### Decisions and deviations from the G1 brief, for the reviewer

- **Model-name matcher: one exact provenance exemption.** The brief requires two things that conflict: the port
  headers must cite the source ref, and the scan must find no hits. The ref's second path segment is a model-family
  word. Step 1 of the matcher therefore also removes that one ref string (stored rot13-encoded in the guard). Any
  other occurrence of the word is still flagged, which `test_provenance_exemption_is_exact` proves.
- **The openFDA connector has its own UTC clock** (`openfda.utc_now`, used for `fetched_at` and injectable).
  Connectors may not import the experiments package, so it cannot use `common.utc_clock`. These two are the only
  wall clocks in G1. Neither module is on the determinism list.
- **The import graph differs from the outline in four small ways. None makes a cycle, and nothing outside
  `experiments` imports `experiments`:**
  - `tasks` imports `routing.TASK_RE`, the single definition of the task-name pattern;
  - `tasks` and `ledger` import `schemacheck.Schema` and `routing.Endpoint` for type hints only (under
    `TYPE_CHECKING`);
  - `openfda` also imports `client.proxy_for`, so that proxies are decided the same way in both places;
  - `runtime` imports `jsonio` for the payload check.
- **`Runtime.single` returns `Attempt(row, output, content_chunks)`.** `content_chunks` is not a ledger key. E3
  needs it for the decode-rate rule (null when a reply arrived in one chunk or none). `single` makes one attempt, so
  only the primary endpoint goes through the key and boundary checks.
- **Labels the server supplies are sanitised before they reach the ledger.** A reply's `model` and
  `finish_reason`, and listed model ids, are kept only when they are short plain identifiers. A server that echoes
  prompt text into those fields cannot put it into the ledger that way.
- **Key handling.** A `Runtime` keeps a reference to the environment mapping it reads api keys from at call time
  (`os.environ` unless one is injected). It never copies a key onto an object. Routing and the runtime's pre-flight
  also reject a key that is not a printable ASCII token, so a malformed key cannot reach an HTTP header error
  message.
- **Proxy credentials are not supported by the model client**: a proxy that needs them answers 407, which is
  recorded as `http_4xx`. The openFDA connector uses urllib's proxy handler, which does support them.
- **`jsonio` duplicate-key paths name the keys** (e.g. `$.endpoints.local`). That is document text, so code that
  parses untrusted text (model replies) maps every strict-JSON error to its reason only.
- **Harness outputs carry a few extra fields beyond the outline:**
  - E3 cells: `task`, `warmup` and `energy_note`. `energy` is null and `energy_note` gives the reason, e.g.
    `not requested (pass --energy rapl)`.
  - N1 `sample.json`: `eligible`, the sampled `rows` (key and code, which `score` uses for its key-set check),
    `schema_version` and `warning`.
  - N1 exclusions: also count `no_report_key`.
  - openFDA `--max-records`: applies per product code.
- **The fake server's `usage` default is a sentinel** rather than the literal `(123, 45)`. A non-streamed reply
  reports `(123, 45)`. A stream reports 123 prompt tokens and as many completion tokens as content chunks it sent:
  `n_chunks`, or fewer when the reply is shorter than `n_chunks` characters. The outline's `(123, n_chunks)` would
  overstate the rehearsal decode rate for short replies.
- **Fake server: two personas beyond the outline**, `slow-status` and `slow-headers`. They trickle the status line,
  or the headers after it, one byte every `trickle_s`, and prove that the deadline covers the whole response.
- **The client's deadline covers every receive.** `http.client` reads the status line and headers through
  `sock.makefile()`; the client hands it a reader that gives each `recv` only the time left and none after the
  deadline. The connect phase is bounded per step: the TCP connect and the TLS handshake each by
  `min(connect_timeout_s, remaining)`, and a proxy's `CONNECT` reply by the deadline. DNS resolution has no timeout in
  the standard library and is not bounded by the client.
- **A stream that ends with neither `[DONE]` nor a `finish_reason`** was cut short. It is a `network` failure, retried
  like a connection reset, not a complete reply.
- **`jsonparse` goes beyond the outline in two ways**, both so that an object nested inside a bad candidate is never
  taken for the answer:
  - when a balanced candidate fails strict parsing (NaN, a duplicate key, `1e999`), the search resumes after its
    end, not at the next `{`;
  - a candidate deeper than 64 ends the search with `too_deep`, because its end is unknown.
- **E3 cells also count transport retries**: `retried` (measured requests that needed one) and `transport_retries`
  (in all). `ttft_s` and `e2e_s` time only the final HTTP try, and a note in `e3.json` says so.
