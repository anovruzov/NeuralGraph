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

## G2

**Base.** Branch `mycelic-collective-phase2` at c2d33b7 (G1). **Nothing in G2 is ported** from
`origin/claude/mycelic-implementation-vr034p`: that branch has no domain pack, no canonicaliser and no claim
extractor (its `knowledge/`, `holder/` and `inquiry/` modules serve later gates).

**Scope: fabric files changed: none.** G2 adds `mycelic/collective/packs/` (with `data/device_quality` and
`data/claims_integrity`), `mycelic/collective/edge/`, `mycelic/collective/experiments/e1_extract.py`, three test
files and `docs/collective/PACKS.md` plus two examples. It changes G1 files additively only:

- `stats.py`: `f1_from_counts` and `bootstrap_f1`;
- `inference/fakeserver.py`: the `responder` hook and `request_payload` (personas unchanged, still 33);
- `collective/__init__.py`: docstring only;
- `tests/mycelic/test_collective_guards.py`: the guard lists (extended, never weakened; the stdlib count moves from
  20 to 28) and the new `DomainLiteralTests`;
- `tests/mycelic/test_collective_stats.py`: new tests;
- `docs/collective/{ARCHITECTURE,RUNBOOK,INTEGRATION}.md` and `docs/collective/examples/README.md`.

No G1 behaviour changed, and every G1 test passes unmodified (only the guard lists grew).

### S1 test baseline

| Suite | Before G2 (c2d33b7) | After G2 |
|---|---|---|
| `python -m pytest tests/mycelic -q -p no:warnings` | 253 passed (700 subtests) | 399 passed (19882 subtests) = 253 + 146 new, 0 skipped |
| `python -m pytest NeuralGraph/tests -q -p no:warnings` | 222 passed, 1 skipped | unchanged: 222 passed, 1 skipped |

The 146 new tests (after round 2) are in five files:

- `test_collective_packs.py`: 58 (loading, freezing, hashes, malformed packs, id formats, the canonicaliser, the
  disclaimer and invisible-character check, the generator, the connector);
- `test_collective_extract.py`: 40 (codes channel, lexical and model extractors, negation, pairing, the fake
  server's responder);
- `test_collective_e1.py`: 35 (metrics, verdicts, prepare, labels, prereg and pinned values, run-file digests,
  smoke runs, external raw);
- `test_collective_guards.py`: 8 more (28 in all; `DomainLiteralTests` and the G2 runbook coverage);
- `test_collective_stats.py`: 5 more (17 in all; `f1_from_counts` and `bootstrap_f1`).

They use no skip decorator and connect only to loopback. The acceptance command (the five collective test files)
passes: 178 passed (19655 subtests) in about 28 s, under the 120 s budget. The full `tests/mycelic` run took 135 s
and `NeuralGraph/tests` 5 s on the shared machine.

### S4: what the suites leave behind (revised)

The two packs' `fixtures/records.jsonl` are committed, tracked JSONL, not run output. The suites create no
untracked file: every new test writes only to temporary directories (`runs` directories included), `runs/` still
holds only its `.gitignore`, and the ignored leftovers are those G1 listed (`__pycache__/`, `.pytest_cache/`,
`demo/results/mycelic-strategic/`).

### Merge note

STRATEGY section 4.5 lists "a pack loader and canonicaliser" among the fabric changes. They live in
`mycelic/collective/packs/` instead, so **the fabric needs no G2 change**. The fabric changes later gates still need
(the `claims_only` share mode, a counts table, the candidate/question/verdict/follow-up event kinds) are unchanged
from the G1 note and remain integration notes until a gate needs them.

### Amendments A1 to A15 (as implemented)

- **A1.** No `out_of_enum` drop. The extraction schema carries the type and predicate enums (with null), the
  runtime validates replies locally, and an out-of-enum value is `schema_invalid`: one repair, then the lexical
  fallback (`extractor: fallback`, `error_kind: schema_invalid`), or an empty result in E1 (`fallback=False`).
  `DROP_REASONS` is exactly `empty, not_canonical, ungrounded, person_value, duplicate, no_entity`.
- **A2.** `pair()` pairs text predicates with the extractor's own entity pairs and with every structured entity, and
  code predicates with entities named only in the text; there is no record-level cross product of text predicates
  with text entities. See the refinement below for negated pairs.
- **A3.** E1 scores the extractor's text claims, not `pair()` output.
- **A4.** The model schema's entity and predicate fields are nullable; a predicate-only item attaches to the primary
  structured entity in post-processing, and an entity-only item feeds "code predicates x text-only entities".
- **A5.** `prereg` pins model, provider, boundary, response format and transport schema per endpoint, the reference,
  data label, external-raw exemption and bootstrap B; `e1_code_hash` covers `E1_CODE_FILES` (the extractor, the
  canonicaliser, loader and connector, every inference module, `schemacheck`, `jsonio`, `stats` and the harness).
  `prereg`, `run` and `compare` refuse dirty code (or no git) without `--allow-dirty`, which is stamped.
- **A6.** `args_schema` is the restricted DSL (entity id, predicate, conclusion id, enum, integer), compiled to a
  schemacheck schema; free text and extra keywords are refused at load.
- **A7.** Map keys are checked in code against the id regexes; map values and closed objects are validated with
  compiled schemacheck schemas, and a schema problem is reported as `PackError(file, absolute path, keyword)`.
- **A8.** Case is per type; the `alnum` segment exists (the clinic id uses it); adjacency, empty-string and
  40-character rules are enforced.
- **A9.** A built-in pack's id must equal its directory name; a path-loaded pack may live anywhere.
- **A10.** `python -m mycelic.collective.packs.loader check <pack>` prints the four hashes.
- **A11.** `stats.f1_from_counts` and `stats.bootstrap_f1`.
- **A12.** `prepare --source records` reads internal records for synthetic rehearsals.
- **A13.** Known ids are alias targets plus the site's master data (world data, passed to `Canonicaliser`); the
  generator writes space forms only for alias targets.
- **A14.** `FakeOpenAIServer(responder=...)` and `request_payload`.
- **A15.** Duplicate claims keep the maximum `res_conf`; person-value filtering happens after pairing in both
  extractors.

### Other decisions and deviations, for the reviewer

- **Negated pairs never reach `pair()` output, from any source.** A2 read literally would let "code predicates x
  text-only entities" or "text predicates x structured entities" produce a pair the narrative states only as
  negated (codes say leak; the text says "the SD-9 did not leak"). `pair()` leaves such pairs out. A codes claim
  stays even when the text negates it (the brief's edge case).
- **`predictions.jsonl` lines carry per-record scores** (`counts`, `exact`, `field_f1`) beside the brief's keys,
  computed by `run` against the pinned labels. `compare` takes no labels argument, so it scores from these; the
  labels' sha256 was checked against the prereg before they were written.
- **`IdFormat` has four fields beyond the brief**: `canonical` (a pattern that matches canonical ids only; follow-up
  `entity_id` arguments compile to it), `case`, `separator` and `strip_leading_zeros` (so the format can compute
  canonical forms by itself). A test checks that `canonical` agrees with `canonical_form`.
- **The adjacency rule is generalised**: two segments that can touch through optional seps *or possibly-empty
  segments* must not share a character class (`[digit, alpha 0..1, digit]` is ambiguous too).
- **The shadow pattern counts only non-ASCII characters in segment positions**, never in separator positions, so a
  non-ASCII hyphen or an NBSP is not a homoglyph. A ligature or a circled digit that leaves no shadow match is
  neither resolved nor counted.
- **Term boundaries apply only at alphanumeric term edges**, so `,` works as a negation terminator in both packs.
  Only the nearest cue on each side of a predicate is tested (a farther one cannot be closer or less blocked).
- **For records with language None, on a lexicon clash between languages the first pack language wins.**
- **`RESERVED` adds `copyright, credits, exit, help, license, quit`**, which `python -S` does not install, so the
  set (and therefore what loads) is the same with and without site-packages.
- **The loader calls `connector.record_problems`** (the list form of `check_record`), so a fixture problem becomes a
  `PackError` without an exception handler.
- **`split_sentences` lives in `packs/canonical.py`**, so `edge/extract.py` imports only the canonicaliser module from
  `packs` (the brief's import graph).
- **`claims_integrity` has an alias-only type (`damage_area`) and `tow_operator` has an optional separator**
  (`TW0007` and `TW-0007` both resolve to `TW-0007`), so both packs exercise every generic path: alias-only types,
  optional-separator insertion, leading-zero stripping and `alnum`.
- **Generator rules the brief leaves open**: a link's parent may not itself be linked (one level); every alias-only
  universe id needs an alias; templates must be one sentence ending in `.`, `!` or `?`, and a slot may not be
  followed by a hard separator plus an alphanumeric (the scanner would reject it); `mixed_language_rate > 0` needs a
  second language with templates; forwards draw from independent records of other sites in the four previous
  weeks; filler-only records draw 1 to 3 distinct filler sentences. The person re-draw check knows the whole id
  universe, so a space form of any id counts as id-shaped. The generator's gold equals the lexical extractor's
  output on every record of three worlds per pack, and a test checks this for one world per pack. Forward
  candidates are bucketed by week, so generation stays linear in the number of records (about 3 s for 20,000).
  When a world asks for more distinct narratives than the templates can make, generation fails with
  `GeneratorError` rather than duplicate one.
- **`unmapped_code_values` counts over the records produced**, not over rejected rows.
- **`format_gold_cell` writes a claim whose `entity_text` is None as `@pred`** (the generator's attached claims).
- **`prepare --source jsonl` labels its output `partner`; `--source records` labels it `synthetic` only when every
  record is synthetic.** `prepare.json` carries `measurement: false` (it measures nothing).
- **`compare --allow-incomplete` still requires repeats exactly 1..runs**; an incomplete run fills its repeat.
  `e1.json` also records `incomplete_runs`, `run_dirs` and `created_at`.
- **`exact_match` in `e1.json` counts records** (round 2): a record matches when it matches in every run, and the
  Wilson interval is over records; a note in the file says so.
- **In a dry run, uncommitted code is a `would need` line** ("committed collective code (or --allow-dirty)"), not an
  error, like any other missing precondition; the real command refuses it.
- **`request_payload` skips repair messages**: a repair message's data block holds the previous reply's excerpt, not
  the payload, so the last user message that is not a repair is used.
- **The openFDA-literal check has one exact exemption, `text`**: it is both an openFDA field name
  (`mdr_text[].text`) and the model payload key the brief fixes (`{language, text}`). Every other openFDA path
  segment is still flagged (`test_openfda_check_is_exact`), as G1 handled its one provenance exemption.
- **Fixtures are written with ASCII escapes** (`\u200b`, `\u00a0`, full-width forms), so no invisible character
  hides in a G2 file; `test_docs_and_g2_files_hold_no_literal_invisible_character` checks the collective docs and
  every G2 code, pack and test file (G1's `jsonio.py` and two G1 test files hold literal ones and stay untouched). Each pack has one hand-labelled paraphrase its lexicon does not hold (`dq-053`,
  `ci-043`), plus one deliberately hard pairing case (`dq-034`), so the printed lexical fixture scores are not 1.0.
  Those scores are same-author, hand-labelled fixtures: they are printed by the test, never asserted, and are not a
  measurement.

### Round 2: the reviewer's findings

Every blocking finding is fixed, with a regression test; the outputs below were measured in the worktree.

- **The model channel no longer merges near-miss ids.** Grounding now goes through the canonicaliser: after
  `resolve_exact`, an item is kept only if the scan of the sent text has a mention of that type and id whose span
  folds equal to `entity_text`; otherwise it is dropped as `ungrounded`. `SD-9-B`, `SD-9_B`, `SD-9` + en dash +
  `B`, `L12345-A` and `RS-42-A` now give no claim in either channel (the reviewer's probe prints `[]` for all four
  of its cases), while `The SD-9-B pump and the SD-9 cracked.` still gives SD-9 from the second mention. A
  canonical alias-only id written literally in a narrative (`BATTERY-DOOR`) is now dropped too, as the scanner
  never reads one; the lexical channel already gave nothing there. With the grounding check removed, 6 of the 7
  near-miss subtests fail. A kept entity's `res_conf` now comes from the text's occurrence (the one written exactly
  as `entity_text`, else the best-written one), which also settles the reviewer's note that `sd-9` for a narrative
  `SD-9` scored 0.9.
- **Lexical pairing is linear in a sentence.** Pairs are formed per distinct entity and distinct
  (predicate, negated) with their multiplicities; `lexical_handler` yields items lazily and stops at `max_claims`.
  Claims, `res_conf`, every drop count and `pair()` output are byte-identical to round 1 on both packs' fixtures,
  three generated worlds per pack and crafted cases (one digest before and after), and a test compares them with a
  mention-by-mention reference on every fixture. 4,000 mentions x 4,000 predicates in one 56,000-character sentence:
  the reviewer measured 38.06 s and 1,629 MB in round 1; the same probe gives 0.11 s and 27 MB now. What remains is the output
  itself: distinct ids x distinct (predicate, negated) pairs per sentence, at most two per predicate in the pack. A
  200,000-character `device_quality` worst case (about 28,000 distinct lots in one sentence with every predicate)
  gives about 371,000 claims in 1.2 s and about 200 MB. Capping claims per record is a policy question for the
  detection gates, not something G2 decides.
- **compare refuses a pre-registered endpoint without runs.** With `--allow-incomplete` it proceeds, stamps that
  and lists the endpoint under `endpoints_without_runs` in `e1.json` (always present, normally empty).
- **Run files are pinned.** `run.json` stamps `predictions_sha256` and `ledger_sha256` when the run ends (null in the
  first `run.json`, written before any request). `compare` refuses a run whose files differ, and a run whose
  `run.json` was never finished (`finished_at` null: the process was killed). The reviewer's tamper probe now exits 2:
  `predictions.jsonl sha256 of run ... differs from its run.json`.
- **exact_match counts records.** `n` is the number of records whose labels name an id of the type, `matches` those
  matched in every run, and the Wilson interval is over records, so identical repeats add no precision (a test
  checks that three identical runs give the same interval as one). Each `run.json` keeps that run's own rate.
  Requiring a match in every run is stricter than pooling for a model that varies between repeats; the note in
  `e1.json` says so.
- **label-check reads sheets with narratives above the csv module's 131,072-character field limit**: `read_sheet`
  raises the limit to the file's length while it reads and restores it afterwards. The reviewer's 152,264-character
  case exits 0.
- **INTEGRATION.md** no longer holds literal invisible characters (the line above is plain ASCII now), and a test
  keeps the collective docs and G2 files free of them.

Non-blocking notes acted on:

- **Data label provenance.** `--data-label public` now requires every labelled record to have site `public` (what
  `prepare --source openfda` writes); other records are partner data. `run` re-checks the data label against the
  labels before creating anything, so a hand-edited prereg cannot relabel them either.
- **Malformed prereg and run files exit 2.** `read_prereg` checks the types `run` and `compare` rely on (pack,
  labels, code hash, endpoints, reference, margin, runs, seed, B, boundary, data label, exemption, and that the
  exemption equals the data label), so a tampered prereg exits 2 before a run directory exists and before any
  request. `compare` checks `run.json` fields and prediction lines the same way.
- **Connector**: a record_ref first seen on a rejected row no longer makes a later valid row a duplicate.
- **Loader**: a dangling symlink at `mapping_openfda.json` is read, and fails as `cannot read (FileNotFoundError)`,
  instead of counting as an absent optional file.
- **PACKS.md** says that only a hard separator plus an alphanumeric rejects a match, so `SD-9.5`, `SD-9/5` and
  `SD-9,5` read as `SD-9`, and how a pack author keeps such suffixes apart.

Round-2 test counts: `tests/mycelic` 399 passed (19882 subtests), up from 387 by 12 new tests; `NeuralGraph/tests`
222 passed, 1 skipped. After both suites `git status --porcelain` is unchanged and `runs/` holds only `.gitignore`.

## G3

**Base.** Branch `mycelic-collective-phase2` at 62f088b (G2). **Nothing is ported** from
`origin/claude/mycelic-implementation-vr034p` in G3. That branch's `holder/` is an async NATS consumer around an
embedding evidence store with HMAC envelopes, and its `inquiry/` serves questions; both belong to G6 (pushdown). It
has no per-site count store, no egress validator and no leakage scanner, which is what G3 builds.

**Scope: fabric files changed: none.** `git diff --stat 62f088b` touches only:

- new: `mycelic/collective/edge/{weeks,records,egress,site}.py`, `mycelic/collective/leakage.py`,
  `mycelic/collective/experiments/g0_canary.py`, `docs/collective/LEAKAGE.md`,
  `tests/mycelic/test_collective_{edge,leakage}.py`;
- changed additively: `mycelic/collective/inference/ledger.py` (`summarise`, which `usage_summary` now calls; the
  output is byte-identical and the G1 test passes unchanged), the docstrings of `mycelic/collective/__init__.py` and
  `mycelic/collective/edge/__init__.py`, `tests/mycelic/test_collective_guards.py` (lists, the stdlib count 28 to
  34, and `test_commands_cover_the_g3_cli`), `tests/mycelic/test_collective_inference.py` (one equivalence test),
  and `docs/collective/{ARCHITECTURE,RUNBOOK,INTEGRATION,PACKS}.md`.

`mycelic/{service,store,aggregation,transport,api,lineage,config}.py`, `deploy/`, `SECURITY.md`, `DEPLOYMENT.md`,
`NeuralGraph/` and `research/` have no diff. The site store is its own SQLite file per site with its own tables; no
table has a fabric table name (a test parses `mycelic/store.py`'s DDL, plus `memories`, `events` and `outbox`).

### S1 test baseline

| Suite | Before G3 (62f088b, clean worktree) | After G3 |
|---|---|---|
| `python -m pytest tests/mycelic -q -p no:warnings` | 399 passed in 162 s | 499 passed (20,095 subtests) = 399 + 100 new, 0 skipped, in 187 s |
| `python -m pytest NeuralGraph/tests -q -p no:warnings` | 222 passed, 1 skipped (119 subtests) | unchanged: 222 passed, 1 skipped (119 subtests) |

The before run was verbose and did not print its subtest count. The one skip is the same as in G1 and G2
(`test_single_hop_regression.py`, "Requires full NeuralGraph setup with data"). The 100 new tests:

- `test_collective_edge.py`: 67 (weeks; the record store; cell suppression, including the property tests on
  high-volume and built-in worlds of both packs; closed weeks, late arrivals, send failures and random interleavings;
  master data; forwarded records; usage windowing; the Boundary and its rejections; G0 determinism across hash seeds);
- `test_collective_leakage.py`: 31 (canary planting; the scanner's views, windows, shingles, manifest guard,
  hygiene class and known limitation; the G0 runner in fake, lexical and routing modes, overrides, usage errors,
  dry runs and a leaky stage);
- `test_collective_guards.py`: 1 (`test_commands_cover_the_g3_cli`; the lists grew by six stdlib modules, one CLI
  and five deterministic modules);
- `test_collective_inference.py`: 1 (`usage_summary(p) == summarise(read_ledger(p))`; `test_usage_summary` is
  unchanged).

No skip decorator, loopback only, temporary directories only. The acceptance command for the two new files,
`python -m pytest tests/mycelic/test_collective_edge.py tests/mycelic/test_collective_leakage.py -q -p no:warnings`,
passed 98 tests (204 subtests) in 25 s on the shared machine.

### S4: what the suites leave behind

After both suites `git status --porcelain` is identical to before them (only the G3 files listed above), `runs/`
holds only its `.gitignore`, and there is no untracked `*.db`, `*.sqlite3` or `*.jsonl` file in the worktree (the
JSONL files under `research/` and the pack fixtures are tracked). The ignored leftovers are those G1 listed
(`__pycache__/`, `.pytest_cache/`, `demo/results/`). Every G3 test and every G0 run in this gate wrote only to
temporary or scratch directories outside the worktree.

### Merge notes

1. **The HQ cells store is G4's own database**, not the fabric's counts table and not the site store. G4 reads
   `hq/receive.jsonl` (or its successor transport) and must:
   - drop a line whose `sha256` it already holds (a re-send after a crash between the HQ and the site append repeats
     the line, by design);
   - check `config_hash` against the pack it runs, and refuse a bundle of another config;
   - treat every week at or before a site's `closed_through` as final, including weeks without cells.
2. **At the built-in synthetic volumes every weekly cell is `'<k'`** (seed 11, 1,000 records: 3,080 cells for
   `device_quality` and 3,374 for `claims_integrity`, none with `n` >= k; synthetic). G4's detectors and G5's
   baselines must work with presence-only weekly series, or the packs' volumes or time granularity must change by a
   frozen, hashed config decision, not in code.
3. **Fabric changes later gates still need** are unchanged (G1 merge note 3): the `claims_only` share mode, a counts
   table, and the candidate, question, verdict and follow-up event kinds. G3 needs none of them.
4. **One writer per site.** `EdgeSite` computes a bundle and stores it in two transactions; one process per site
   store is the supported deployment (two writers could interleave an ingest between them). A multi-process site
   needs the emission computed inside the store's write transaction (G4 or later, if needed).

### Decisions and deviations (brief section 13, as implemented)

- **Overriding `require_master_data` writes a pack copy** (`OUT/pack`, `egress.json` changed in that one field), so
  it gets its own `config_hash`; `EdgeSite` has no override flag. The world is generated from the base pack (the
  override touches only `egress.json`, which generation never reads), and `leakage.json` records `base_config_hash`
  and `require_master_data_overridden`.
- **`usage_summary` is windowed by ledger-row week and suppressed per field**, instead of the raw G1 summary.
- **`'<k'` is the literal for suppressed fields.** A suppressed cell still reveals presence; `not_covered` says so.
- **`emitted_weeks` is a watermark log**, one row per emission per artifact type, holding the bytes sent, so a crash
  before or during a send is re-sent byte-identically before anything new.
- **The Boundary enforces `sequence`**, appends to HQ first and then the site log, and treats an identical re-send as
  a no-op.
- **`edge/weeks.py` was added; `EdgeSite` takes `hq_dir`.**
- **`ledger.summarise` was added.**
- **Reporter identities carry class-a canaries**, one token per reporter value, so reporter counts are unchanged.
- **Id canaries need a length of at least 6 and a letter `g` to `z`**, so short formats (the illustrative supplier
  format) are skipped and no hex digest can contain one.
- **The scan decodes JSON escapes in the artifact** (twice), instead of encoding narrative windows; this catches
  mixed, upper-case, surrogate-pair and double escaping.
- **The vocabulary exclusion is the `config_hash` file set only**; the world spec's templates are scanned for.
- **A positive control proves the scanner is live** on each run's own data (the first site's database).
- **G0 exit codes**: 0 passed, 1 leakage found (or a positive control that found nothing), 2 usage or configuration.

Further decisions the brief left open:

- **Counts have an upper bound**: an int in [k, 10^9]. Without it a crafted count could carry an arbitrarily large
  integer (an encoded text) through the Boundary.
- **A stored claim's `res_conf` must be one of the pack's three confidences**, not just in (0, 1]: the Boundary only
  accepts those values for `res_conf_min`, so a claim with another value would make every later bundle unsendable.
- **`RecordStore.pending_non_synthetic()`** was added for the simulated-runtime check, so that SQL stays in
  `records.py`.
- **`G0Context` carries `emit_at`, `seed`, `routing` and `totals`** beside the outline's fields; `ctx.runtimes` is
  filled by the edge stage, which closes the sites and runtimes it opened.
- **`--seed` is an int in [0, 10^12]**, so the runtime's `run_id` (`g0-<seed>`) is always valid.
- **The positive control counts known-limitation hits as canary hits**, so it also works when master data is off.
- **`windows_indexed` counts the narrative windows searched**, after the vocabulary exclusion.
- **`models_fake` is null in lexical mode** (no model at all), true with the fake provider and false with routing.
- **`Boundary.read_log` also checks** the artifact type, direction, site, timestamp and a valid `closed_through` of
  every row, and a Boundary refuses an egress log holding another site's rows.
- **The `ledger.py` module docstring** now says that what crosses is the windowed form (its old text said the raw
  summary was the artifact meant to cross).

## G4

**Base.** Branch `mycelic-collective-phase2` at e5575ba (G3), a clean worktree. **Nothing is ported** from
`origin/claude/mycelic-implementation-vr034p` in G4. That branch's `discovery/engine.py` is an async, model-driven
goal loop over `CoordDB` (jobs that route questions, evaluate model answers and commit claims); it has no count
store and no count detectors, which is what G4 builds.

**Scope: fabric files changed: none.** `git diff --stat e5575ba` touches only:

- new: `mycelic/collective/detect/{__init__,org,store,rules,detectors}.py` and
  `tests/mycelic/test_collective_detect.py`;
- changed additively: `mycelic/collective/stats.py` (the detector tails, PMI and logistic, each formula in the
  docstring), `mycelic/collective/edge/egress.py` (public `check_artifact` and `log_row_problem`; behaviour
  byte-identical and every G3 test unchanged), `mycelic/collective/edge/records.py` (one docstring sentence),
  `mycelic/collective/packs/loader.py` (the `detectors.json` schema, its checks and the reserved key lists), both
  packs' `detectors.json`, the docstring of `mycelic/collective/__init__.py`, `tests/mycelic/test_collective_stats.py`
  (four new classes), `tests/mycelic/test_collective_guards.py` (lists grown, the stdlib count 34 to 39, an optional
  `prefixes` argument for `forbidden_imports`, `HqImportGuardTests` and `ClockEntropyTests`) and
  `docs/collective/{ARCHITECTURE,PACKS,INTEGRATION}.md`.

`mycelic/{service,store,aggregation,transport,api,lineage,config}.py`, `deploy/`, `SECURITY.md`, `DEPLOYMENT.md`,
`NeuralGraph/` and `research/` have no diff (S2: `git diff --stat e5575ba -- <those paths>` is empty). There is no
new CLI and `RUNBOOK.md` is unchanged. HQ's store is its own SQLite file (`collective.sqlite3`, any path the caller
gives) with its own tables; no table has a fabric table name.

### S1 test baseline

| Suite | Before G4 (e5575ba, clean worktree) | After G4 |
|---|---|---|
| `python -m pytest tests/mycelic -q -p no:warnings` | 499 passed (20,095 subtests) in 198 s | 597 passed (23,358 subtests) = 499 + 98 new, 0 skipped, in 214 s |
| `python -m pytest NeuralGraph/tests -q -p no:warnings` | 222 passed, 1 skipped (119 subtests) in 6 s | unchanged: 222 passed, 1 skipped (119 subtests) in 5 s |

The one skip is the same as in G1 to G3 (`test_single_hop_regression.py`, "Requires full NeuralGraph setup with
data"). The 98 new tests:

- `test_collective_detect.py`: 77 (config; org and decision units; the store, its ingest order, rejections,
  immutability, visibility and org sync; imputation; D2; D3; D4 to D6; the ranker; channels; the alert walk; no
  look-ahead; determinism across hash seeds and `save_run`; the rules channel; org changes; end to end over generated
  worlds of both packs; performance);
- `test_collective_stats.py`: 14 (`PoissonBinomialTests`, `TailTests`, `PMITests`, `LogisticTests`);
- `test_collective_guards.py`: 7 (`HqImportGuardTests` 5, `ClockEntropyTests` 2; the lists grew by five stdlib
  modules and five deterministic modules).

No G1 to G3 test was modified except the guard lists (and the count they assert) in `test_collective_guards.py`. No
skip decorator, no network, temporary directories only. The acceptance command `python -m pytest
tests/mycelic/test_collective_detect.py tests/mycelic/test_collective_stats.py tests/mycelic/test_collective_guards.py
-q -p no:warnings` passed 144 tests (3,903 subtests) in 26 s on the shared machine.

### S4: what the suites leave behind

After both suites `git status --porcelain` lists only the G4 files above, `runs/` holds only its `.gitignore`, and
there is no untracked `*.sqlite3`, `*.db` or `*.jsonl` in the worktree. The ignored leftovers are those G1 listed
(`__pycache__/`, `.pytest_cache/`, `demo/results/`). Every G4 test writes only to temporary directories; the
determinism subprocesses work in a temporary directory of the test.

### Pack hashes (G4 changed `detectors.json`, amendment A5)

| Pack | Hash | Before (G3) | After (G4) |
|---|---|---|---|
| `device_quality` | `config_hash` | `6e442cc37c7ecf16d6380fc95206aacb1fbe8ada5f022ec1981ea9cae1386d86` | `83c094ffee1f157203f7265a59bdbfc35b7d8f8ed07c35304743377dd301d723` |
| `device_quality` | `detector_hash` | `21fae9365549617917ccd8d133d2975a37102706cacc8c750dcb524488686625` | `c9462f62aa90245f2c7cee50078d337554bded58c4630cda7becbf7a8048c7ec` |
| `claims_integrity` | `config_hash` | `6d7e410d21ae45394f84455959d964fc9da80eb82542f3018d01481e2275b0bd` | `130b8396eb92e5af060f0dc7b645fb80f00be1e041c1f0d224bcb49566f0cb01` |
| `claims_integrity` | `detector_hash` | `a46e83df4f68fa60aaf98d5149d251e010b4f8a9a1dd426122c2406018cb7b75` | `2041fe9b3e141a5603836d893c97d5671eb21cbb3da611969efbd0c2c4514b84` |

`vocabulary_hash` and `fixtures_hash` are unchanged (`device_quality` `e46f521154ce...` and `dc4b70b7044b...`;
`claims_integrity` `028b7603f2b6...` and `a2e8936b4be2...`). Printed by `python -m mycelic.collective.packs.loader
check <pack>`. No hash was pinned anywhere in the repository.

### Merge notes

1. **`collective.sqlite3`'s `cells` is the HQ counts table** of STRATEGY section 4.5, so the fabric needs no counts
   table for Phase 1. The fabric's `candidate` event kind (and the question, verdict and follow-up kinds) stays an
   integration note for the gates that publish candidates into the event log.
2. **`detect/org.py` imports `mycelic.hierarchy`** (`split_path`, `validate_segment`, `is_ancestor_or_self`,
   `HierarchyError`), which is not on the other team's list of files they are changing. If the merge changes those
   functions, `OrgTests` and `DecisionUnitTests` catch it.
3. **The pack hashes changed**, so site stores made with the old packs refuse to reopen (`site_info mismatch:
   config_hash`), as designed; a new HQ store is needed as well (`store_info mismatch: config_hash`).
4. **One writer per HQ store.** Ingest reads and writes inside one `BEGIN IMMEDIATE` transaction per bundle, but a
   multi-process HQ is not a supported deployment.

### Performance (sandbox engineering timing, not a product figure)

`PerformanceTests` ingests 6 sites x 52 weekly bundles (312 bundles) over 2,002 series (182 lot ids x 11
predicates) with a seeded density: 205,939 cells (about three quarters `codes`, a quarter `text_only`; 85 % of
them `'<k'`), every bundle through `ingest_bundle` with the full validator. On this sandbox (4 vCPU, Intel Xeon
2.10 GHz, Python 3.11, shared with another team's suites) ingest took 7.0 to 7.3 s and the X detection run 7.1 to
7.6 s (two runs), against the 180 s bound the test asserts. The cells are synthetic and random; the figure says only
that this size runs well inside the bound here, nothing about any partner's data or volume.

### Decisions and deviations: amendments A1 to A12 (as implemented)

- **A1** `p_s` uses only past windows ending at or before `W - window_weeks`, each with at least
  `min_history_weeks` of history, and counts a window when its exceedance was *possible* (window `'<k'` = k-1,
  baseline `'<k'` = 1): an upper bound on the site's base rate. Tests: hand `p_s` (p_max, 1/14), overlapping and
  current windows never counted.
- **A2** A fully suppressed history stays in `n` with its conservative `p_s` and is listed in
  `flags.suppressed_history_sites`; a test shows the surprise with it kept is at most the surprise without it.
- **A3** `high_base_rate` counts eligible sites where *another* series of the predicate (any entity type) certainly
  exceeds; a single-entity burst at 3 or at 4 of 6 sites does not flag itself.
- **A4** Visibility is set at run level (bundles with `as_of <= D`, weeks up to `last_week`); step W uses every loaded
  cell with a week at or before W; a site has reported W when its loaded bundles cover W (the on-time-reporting
  assumption); `as_of_W` is used only for staleness and is reported per week (`ARCHITECTURE.md` section 13.3).
- **A5** `detectors.json` has the new closed shape in both packs; both packs' `config_hash` and `detector_hash`
  changed (table above). G2/G3 code and tests read only `baseline_weeks` and `window_weeks`, which are kept, and G3's
  high-volume copy (`baseline_weeks` 4) still loads.
- **A6** Rules name a predicate, never a code.
- **A7** Rule hits are never ranked and never use the alert budget; they are merged into candidates by key.
- **A8** The store enforces each site's sequence (`after` equals the last accepted `closed_through`, `as_of` never
  moves backwards); the cell `conflict` check runs first, so a re-send that changes a stored cell is a `conflict`.
- **A9** `edge/egress.py` exposes `check_artifact` and `log_row_problem`; the Boundary calls `check_artifact` (it now
  keeps its tasks and endpoints and builds the spec per call instead of holding prebuilt specs; output identical).
- **A10** A site's unit path has 2 to 5 segments, starts with the enterprise, is unique and is no ancestor of another.
- **A11** The rejection reasons are `bad_line`, `config_hash`, `unknown_site`, `invalid`, `site_mismatch`, `conflict`
  and `sequence`, all recorded the same way.
- **A12** No CLI in G4.

### Further decisions and deviations, for the reviewer

- **`RUN_CHANNELS` lives in `detect/store.py`** (`X`: codes and text_only, `S`: codes) and `detectors` imports it:
  the store filters by channel and cannot import `detectors`.
- **Shape details the brief left open:** `insufficient_baseline_weeks` is a count; `weeks[].reported_sites`,
  `eligible_sites`, `candidates`, `stale_removed`, `cooling` and `rule_hits` are counts, `late_sites` and `alerts`
  lists; a candidate's `detectors` is the union over its candidate weeks; for a status other than `eligible`, the
  `d2` entry keeps `site`, `status` and `history_weeks` and the `d3` entry `site` and `status`, every other field null.
- **The rule part is taken at the key's first rule week:** `rule_ids`, `sites` and `lineage` come from the rules
  that hit the key that week (lineage: the key's cells at each satisfying site inside that rule's window), and
  `window` spans the widest of them; `weeks` lists every week any rule hit the key. `rule_hits[]` keeps every
  `(rule_id, key)`.
- **Two exact shortcuts in the walk:** a series is examined at week W only when at least `min(burst.min_sites,
  cooccurrence.min_sites)` eligible sites have cells of it, and the PMI is computed only at sites whose window lower
  bound reaches k; neither changes a result, since a site without cells never exceeds and a site below k never rises.
  The snapshot recomputes D2 and D3 at every org site, so it shows rises at sites below k as not rising.
- **`ingest_log` of an absent file** returns an empty report (as `read_log` returns no rows). A `bad_line` row has a
  NULL site. A clock is read only when the store writes.
- **A body whose `site` is not a string** is validated against the site id `""`, so the Boundary's `const` check
  refuses it (`invalid`, `$.site`); a string site outside the org is `unknown_site` first.
- **`OrgError` never names an unknown key** (it reports `unknown key` at the parent object); the size of `sites` is
  checked with the top-level shape, before `schema_version`. An unreadable org file is `OrgError("$", "cannot read
  (<exception class>)")`.
- **The tails return at most 0.0** (`min(0.0, x)`), which also turns a `-0.0` into `0.0`, so no `-0.0` reaches the
  result JSON; the D2 surprise is computed as `0.0 - logsf` for the same reason.
- **The loader keeps a private copy of the eight weight names** (`_RANKER_WEIGHTS`, for the schema and the reserved
  words) and `detectors.FEATURES` is the ordered list; `DetectorConfigTests` asserts they are the same set. It also
  checks `p_min > 0` explicitly, as part of `0 < p_min`.
- **The tests import helpers** (`Clock`, `coded`, `pack_copy`, `universe_master`, `string_constants` and the SQL
  matcher) from `test_collective_edge.py`, functions only, so no test class is collected twice.
- **The engineer ran the brief's thirteen mutation probes** (and nine more) on a scratch copy of the worktree, outside
  the repository; each made at least one test in `test_collective_detect.py` fail. Two of the extra probes (the
  `n_ep >= k` support check and echo's upper-bound roots) first survived and got a test each
  (`test_a_site_below_k_is_shown_not_rising_in_the_snapshot`, `test_suppressed_roots_count_their_upper_bound`).

## G5

**Base.** Branch `mycelic-collective-phase2` at 5b67dfe (G4), a clean worktree. **Nothing is ported** from
`origin/claude/mycelic-implementation-vr034p` in G5: that branch has no planted-pattern evaluation, no baselines and
no replay; its `discovery/` goal loop is model-driven, and G5 makes no model call.

**Scope: fabric files changed: none.** `git diff --stat 5b67dfe` touches only:

- new: `mycelic/collective/evaluate/{__init__,plant,baselines,harness}.py`,
  `mycelic/collective/experiments/openfda_replay.py`, `packs/data/{device_quality,claims_integrity}/fixtures/
  plant_smoke.json`, `tests/mycelic/test_collective_evaluate.py` and `tests/mycelic/test_collective_replay.py`;
- changed additively: `stats.py` (tie-averaged AP and precision@k, the cluster bootstrap; formulas in the docstring),
  `edge/site.py` (`build_cells(k=None)`; the default output is byte-identical, which every G3 cell test still
  checks), `packs/loader.py` (the listing accepts `fixtures/plant_*.json`; never read or hashed), `packs/connector.py`
  (`field_values`), `experiments/common.py` (`code_files`, `code_dirty(paths)`; the default unchanged), the
  docstrings of `mycelic/collective/__init__.py` and `experiments/__init__.py`, `tests/mycelic/test_collective_stats.py`
  (two new classes), `tests/mycelic/test_collective_guards.py` (the lists, the stdlib count 39 to 44, the G5 runbook
  coverage test and `EvaluateImportGuardTests`) and `docs/collective/{ARCHITECTURE,RUNBOOK,PACKS,INTEGRATION}.md`.

S2: `git diff --stat 5b67dfe -- mycelic/service.py mycelic/store.py mycelic/aggregation.py mycelic/transport.py
mycelic/api.py mycelic/lineage.py mycelic/config.py deploy SECURITY.md DEPLOYMENT.md NeuralGraph research
mycelic/collective/detect` is empty: **no file under `detect/` changed**; G5 measures the frozen G4 detector.

### S1 test baseline

| Suite | Before G5 (5b67dfe) | After G5 |
|---|---|---|
| `python -m pytest tests/mycelic -q -p no:warnings` | 597 passed (as recorded in the G4 section and the G5 brief) | 689 passed (23,696 subtests) = 597 + 92 new, 0 skipped, in 252 s |
| `python -m pytest NeuralGraph/tests -q -p no:warnings` | 222 passed, 1 skipped | unchanged: 222 passed, 1 skipped (119 subtests) in 6 s |

The 92 new tests, by class:

- `test_collective_evaluate.py` (59): `PlantSpecTests` 12, `PlantedConstructionTests` 12,
  `ClaimsIntegrityConstructionTests` 4, `BaselineInputTests` 8, `SingleSiteAndRulesTests` 4, `MetricTests` 8,
  `HarnessTests` 8, `ScorecardSchemaTests` 2, `DeterminismTests` 1 (two subprocesses);
- `test_collective_replay.py` (15): `OpenFDAReplayTests`;
- `test_collective_stats.py` (12): `TieAveragedRankingTests` 6, `ClusterBootstrapTests` 6;
- `test_collective_guards.py` (6): `EvaluateImportGuardTests` 5 and `RunbookCommandTests::test_commands_cover_the_g5_clis`.

The brief's `HarnessTests` items are split over `HarnessTests`, `MetricTests` and `ScorecardSchemaTests`, and its
single-site and rules items are in `SingleSiteAndRulesTests`. No G1 to G4 test was modified except the guard lists
(and the count they assert). No skip decorator, no network beyond loopback stubs, temporary directories only. The
acceptance command `python -m pytest tests/mycelic/test_collective_evaluate.py tests/mycelic/test_collective_replay.py
tests/mycelic/test_collective_stats.py tests/mycelic/test_collective_guards.py -q -p no:warnings` passed 159 tests (4,119 subtests) in 47 s on the shared machine.

### S4: what the suites leave behind

After both suites `git status --porcelain` lists only the G5 files above, `runs/` holds only its `.gitignore`, and
there is no untracked `*.sqlite3`, `*.db` or `*.jsonl` in the worktree. Every G5 test writes under a temporary
directory (its runs directory, work directories, pack copies and openFDA caches); the determinism subprocesses work
in a temporary directory of the test.

### Pack hashes (unchanged from the G4 table)

`python -m mycelic.collective.packs.loader check <pack>` prints the G4 values: `device_quality` 83c094ffee1f,
e46f521154ce, c9462f62aa90, dc4b70b7044b; `claims_integrity` 130b8396eb92, 028b7603f2b6, 2041fe9b3e14, a2e8936b4be2
(config, vocabulary, detector, fixtures). The plant fixtures are in no hash scope; `PlantSpecTests` asserts the full
values and that adding or editing a plant file changes none of them.

### Merge notes

1. **No fabric integration point is needed.** The harness reads HQ only through `CollectiveStore` and `detect`, and
   site stores only through `RecordStore.emission_inputs`; nothing publishes into the fabric's event log.
2. **`runs/x1/<id>/work/` holds a synthetic world's site stores and HQ store** (git-ignored with the rest of
   `runs/`); the founder sends back `prereg.json`, the plant spec, `labels.json` and `scorecard.json` only.
3. **The replay depends on openFDA field paths the founder passes** (`--manufacturer-field`, `--partition-field`)
   and on the four recall fields STRATEGY section 9.3 names; their contents are unverified until a run from an open
   network. A pack copy frozen with the manufacturer's id shapes should precede any replay (`low_resolution` warns).

### Amendments A1 to A8 (as implemented)

- **A1** The device smoke asserts, for the structural classes, the mechanism, the quiet precondition on HQ's X cells
  and then 0 X alerts in the watch span; for the penalty classes the flag on the candidate's snapshot (alerts
  reported, not asserted); the unmarked copies are a known hard case. A smoke that is noisy elsewhere is warned, not
  hidden: the claims_integrity smoke has several decoys that are not quiet, and the test checks the warnings.
- **A2** Decoys are always planted `narrative_only`, and planted records carry only the planted mention (the
  `by_construction` statement, next to the measured S and R-mf recall).
- **A3** `code_hash` covers `EVAL_CODE_FILES` (every file of `detect/`, `edge/`, `packs/`, `evaluate/`, plus `stats`,
  `jsonio`, `schemacheck` and `experiments/common`); the world is pinned through `fixtures_hash`; the dirty check
  covers `EVAL_DIRTY_PATHS`.
- **A4** Plant fixtures live at `packs/data/<pack>/fixtures/plant_*.json`; the listing accepts them, the loader never
  reads or hashes them, and a spec is hashed on its own (`plant_sha256`).
- **A5** R-mf reads only the allowed fields by subscript (a recording record fails on anything else), cannot drop
  forwarded copies, counts `n_roots = n_reporters = n`, and applies the cells' egress-type and master-data rules.
  The exact channels can never raise `few_reporters` (documented in ARCHITECTURE section 14.3 and the scorecard).
- **A6** A plant spec's non-null `prereg_sha256` must equal the sha256 of the prereg file; `x1.eligible` requires it;
  `check-plant` prints it.
- **A7** The replay is three subcommands; `signals` has no recall argument; `score` re-hashes `signals.json` against
  `phase1.json` (and its prereg) before it opens the recall cache; `--saw-recall-outcomes` is stamped.
- **A8** `--manufacturer-field` and `--partition-field` are required paths checked against `packs.loader.PATH_RE`
  and recorded; recalls use the four fixed fields.

### Decisions and deviations, for the reviewer

- **The device smoke's near-miss pair is `L20045`/`L20046` (contamination) at plant-ashvale and werk-dornhagen**, not
  the outline's `L10001`/`L10002`: as the planner's prototype warned, `lot:L10001:contamination` was not quiet
  elsewhere at seed 11 (plant-corrowfield summed 2 over the quiet weeks). The new pair is one edit apart and both keys
  are quiet. Every other device key, site, week and rate is the outline's. The claims_integrity smoke was chosen
  here: patterns `tow_operator:TW-0310:tow_without_dispatch` (rate 5 at three motor sites),
  `repair_shop:RS-2290:duplicate_invoice` (rate 2, below k) and `clinic:CL-B7X9:treatment_pattern_mismatch` (rate 5 at
  both injury sites), and one decoy of each class (`ClaimsIntegrityConstructionTests` asserts only what the brief
  asks: a pattern found by X, a valid scorecard and the minimum detectable rate rows).
- **Construction tests run on a device pack copy without rules** (as the brief's construction says); its config and
  detector hashes differ from the built-in pack's, which the prereg pins like any path pack.
- **Problems the brief left open are fixed strings:** `wrong type` (a top-level type), `must be an object`,
  `unknown key` at the item, `must be a list of site ids`, the range messages, `no filler for the language` (a pack
  language without filler), `site has no background records` and `planted record fails the record check` (a bug). A
  decoy's `class` is read before its keys, because the key set depends on it.
- **The quiet span ends at the watch span's end** (`min(end + grace, eval_to)`): later cells cannot affect an alert
  inside the watch span, since step W reads only weeks up to W. For each key the quiet rule excludes only the sites
  whose planted records count in that key's cells (an echo's origin, a near miss's own site), so an echo's copy sites
  are checked too.
- **`run_pipeline` takes `enterprise`** (`eval` for X1, `replay` for the replay) and also raises on a site that
  rejects or duplicates a record; `Pipeline.close()` closes HQ and every site.
- **`flags_at_detection`** is, per seed, a list of `{key, detection_week, flags}` (a high base rate has several keys);
  `labels.json`'s `seeds[].planted_records` is the total planted per seed, and `Planted.counts` keeps it per item.
- **`code_dirty` is true, false or "unknown"**, a union schemacheck cannot express: the schemas declare
  boolean-or-null and `union_problems` checks "unknown" (and refuses null) beside them. The harness's schema helpers
  (`obj_schema`, `arr_schema`, `typed_schema`, `union_problems`) are public because the replay reuses them.
- **`connector.field_values` validates its path with the loader's `PATH_RE`, imported inside the function** (the
  loader imports the connector), and takes an optional narrative-style `where`, so the replay's narrative coverage
  follows the mapping exactly.
- **Replay records are re-keyed to the pack's own record fields** (`site_record`): the site store checks records
  against the pack's default mapping, and the openFDA mapping names a subset of its entity types; a pack whose
  openFDA mapping names a type its default mapping lacks is refused.
- **Replay product codes per alert come from the claims in the channel's own cell channels** (codes only for S and
  R-mf). A partition field of the wrong shape counts as no value (unpartitioned). Manufacturer values are compared
  after the connector's stripping, case-sensitively. `coverage.manufacturer_field` is over the matched events (so
  1.0); the informative counts are the manufacturer block's.
- **Replay scoring details:** a recall that is not a JSON object (or not canonical JSON) is counted as `bad_record`;
  a recall whose product code has no event is listed and is not in scope; a channel's `post_recall_alerts` counts
  distinct alerts in any in-scope recall's post window; items that are not evaluable carry `by_channel: null`; an
  unavailable R-mf is `{alerts: null, candidates: null, status: null, reason}`; `data_label` is `public` only when both
  caches are public.
- **`check-plant` reads master data from the first seed's world** (master data does not depend on the seed), and
  `run` re-checks the prereg's world settings against the pack before planting.
- **The engineer ran the brief's twenty mutation probes** (24 variants, on a scratch copy outside the repository);
  each made at least one new test fail.

### Synthetic prototype figures (synthetic, same-author, not a measurement)

The construction smokes printed, in this sandbox, quoted only as **synthetic, same-author, not a measurement**:
`device_quality` (rules removed, seed 11, 6 sites, 52 weeks, evaluation weeks 26 to 51, grace 4): X found 3 of 3
patterns, S 0 of 3, R (model-free) 0 of 3, U 3 of 3, single_site 3 of 3; the five structural decoys were quiet and
raised no X alert; the single reporter and high base rate decoys alerted with their flags set; the unmarked copies
alerted in X. `claims_integrity` (built-in, seed 5): X found 2 of 3 (the rate-2 pattern below k=5 was not found), S 0
of 3, R (model-free) 0 of 3, U 3 of 3, single_site 3 of 3. The lifts over three patterns and one seed are not
interpretable (the scorecard warns), and none of these figures may be shown to anyone outside the team.

## G6

**Base.** Branch `mycelic-collective-phase2` at 51f3b09 (G5), a clean worktree.

**The port.** `pushdown/gate.py` is **ported (adapted, not merged) from
`origin/claude/mycelic-implementation-vr034p@388aa30`, `mycelic/knowledge/gate.py` and `support.py`**: the five
checks, the status precedence (contested, then hypothesis without evidence, then stale, then the support checks;
`rejected` for a question-level failure), `age > freshness_days` for stale and readable reasons are kept; the
deviations are listed in its header and in ARCHITECTURE section 15.6 (no Authorizer or OrgService, `as_of`
injected, bucketed verdicts as input, `'<k'` roots and reporters counting their lower bound 1, weak confirms not
counted, per-verdict exclusion instead of rejection, contested only from a contributing refute). Nothing else is
ported. vr034p's `holder/` and `inquiry/` carry free-text questions and per-record evidence ref ids over its own
transport and coordinator database, which G6's closed question and verdict formats exist to avoid;
`edge/verify.py` and `pushdown/orchestrator.py` are written fresh. Its `models/` is superseded by G1's runtime.

**Scope: fabric files changed: none.** `git diff --stat 51f3b09` touches only:

- new: `mycelic/collective/pushdown/{__init__,questions,gate,orchestrator}.py`, `mycelic/collective/edge/verify.py`,
  `mycelic/collective/experiments/e2_pushdown.py`, `packs/data/device_quality/fixtures/plant_e2_smoke.json`,
  `docs/collective/examples/central_routing.example.json`, `tests/mycelic/test_collective_{pushdown,gate,e2}.py`;
- changed additively: `edge/egress.py` (the `question` and `verdict` artifacts, `ARTIFACT_DIRECTION`,
  `SEQUENCED_TYPES`, the bucket helpers, `question_id`, `verdict_id_of`, `Boundary.accept`, the ingress and question
  logs; the cells and usage sequence is byte-identical and every G3 test passes), `edge/records.py`
  (`question_log`, `verdict_log`, the window reads), `edge/site.py` (the Boundary's ingress and question logs; the
  judge task among the usage tasks), `detect/store.py` (five `pd_` tables, their triggers and readers, the routing
  reads), `packs/loader.py` (the `pushdown` block, `PushdownConfig`, template coverage), both packs' `egress.json`
  and `questions.json`, `stats.py` (`paired_ranking_bootstrap`), `leakage.py` (two `NOT_COVERED` items),
  `experiments/g0_canary.py` (the pushdown stage), `evaluate/harness.py` (`_prereg_pack`, `_check_world`, `_plant` and
  `_dirty_state` renamed `prereg_pack`, `check_world`, `load_checked_plant` and `dirty_state`, no behaviour change),
  the docstrings of `mycelic/collective/__init__.py`, `edge/__init__.py` and `experiments/__init__.py`,
  `docs/collective/{ARCHITECTURE,LEAKAGE,RUNBOOK,PACKS,INTEGRATION}.md`, `docs/collective/examples/
  {README.md,routing.example.json}` and the earlier tests listed below.

**S2:** `git diff --stat 51f3b09 -- mycelic/service.py mycelic/store.py mycelic/aggregation.py mycelic/transport.py
mycelic/api.py mycelic/lineage.py mycelic/config.py deploy SECURITY.md DEPLOYMENT.md NeuralGraph research
mycelic/collective/detect/detectors.py mycelic/collective/detect/rules.py mycelic/collective/detect/org.py` is empty.
Detection is unchanged; `PushdownImportGuardTests` pins the three detect files to their G5 sha256.

### S1 test baseline

| Suite | Before G6 (51f3b09) | After G6 |
|---|---|---|
| `python -m pytest tests/mycelic -q -p no:warnings` | 689 passed (23,696 subtests), 0 skipped, in 246 s | 802 passed (25,509 subtests) = 689 + 113 new, 0 skipped, in 369 s |
| `python -m pytest NeuralGraph/tests -q -p no:warnings` | 222 passed, 1 skipped (119 subtests) in 6 s | unchanged: 222 passed, 1 skipped (119 subtests) in 5 s |

The 113 new tests, by class:

- `test_collective_pushdown.py` (72): `PackPushdownConfigTests` 3, `BucketTests` 3, `QuestionTests` 6,
  `EgressQuestionVerdictTests` 12, `VerifyTests` 15, `RoutingTests` 6, `OrchestratorTests` 10,
  `EndToEndPlantedTests` 7, `LeakageStageTests` 5, `PushdownImportGuardTests` 5;
- `test_collective_gate.py` (13): `GateTableTests` 7 (26 table cases as subtests: 23 verdict cases and 3
  question-level ones, plus the orchestrator-backed duplicate and conflict versions), `GateDeterminismTests` 3,
  `GatePortTests` 3;
- `test_collective_e2.py` (18): `E2SmokeTests` 8, `E2UnitTests` 3, `E2RefusalTests` 6, `E2DeterminismTests` 1;
- `test_collective_stats.py` (7): `PairedRankingBootstrapTests`;
- `test_collective_guards.py` (3): `HqImportGuardTests::test_no_pushdown_module_imports_a_forbidden_module`,
  `ClockEntropyTests::test_every_pushdown_module_is_on_the_determinism_list_with_no_hits` and
  `RunbookCommandTests::test_commands_cover_the_g6_clis`.

The acceptance command `python -m pytest tests/mycelic/test_collective_pushdown.py tests/mycelic/test_collective_gate.py
tests/mycelic/test_collective_e2.py -q -p no:warnings` passed 103 tests (1,753 subtests) in 131 s on the shared machine.

**Earlier tests changed, and why** (each at least as strong as before; nothing skipped, deleted or weakened):

- `test_collective_packs.py::LoadTests::test_egress_and_detector_values`: the expected verdict buckets (D4),
  `[3, 10, 50]` and `[5, 10, 50]`;
- `test_collective_evaluate.py::PlantSpecTests::test_the_loader_accepts_plant_files_and_hashes_none_of_them`: the
  two `config_hash` pins only (D4, D5); the vocabulary, detector and fixtures pins are unchanged;
- **outside the brief's list, and necessary:** `test_collective_evaluate.py::PlantSpecTests::
  test_refusals_that_need_a_pack_copy`, case "not a structured entity type". Its pack copy adds an egress entity type
  (`fastener`) to show that a type the record mapping does not carry cannot be planted as a structured code; G6's
  mandated loader rule (every egress entity type needs a template with `predicates` null) now refuses that copy at
  load, before the plant check runs. The copy's edits gain one line, adding the new type to the pack's template, so
  the pack loads again; the asserted path and problem are unchanged and the test is exactly as strong. The full suite
  found this; the alternative was to weaken the loader rule the brief fixes;
- `test_collective_leakage.py::G0RunnerTests` (G0 now runs the pushdown stage): (a) "no scanned label contains
  `.sqlite3`" became "no scanned label matches `edge/site-.*\.sqlite3`" (a site database, or its `-wal`, never
  crosses) plus "`hqdb/collective.sqlite3` is scanned"; (b) the leaky-stage test expects the stages `["edge",
  "pushdown", "leaky"]`; (c) the routing-mode test's file also routes `judge_record` to the same fake server, whose
  responder dispatches on the payload's shape (`text` to the lexical extraction handler, `question` to the lexical
  judge), and it asserts exactly 60 extraction requests, at least one judge request and no other request, instead of
  exactly 60 requests in total; its two refusal cases now route both tasks, so they are still refused for their
  boundary and provider rather than for a missing route;
- `test_collective_guards.py`: the lists (50 stdlib modules, the E2 CLI, the deterministic modules, `HQ_FORBIDDEN`
  plus `edge.verify`, two RUNBOOK placeholders) and the three new tests above;
- `test_collective_stats.py`: one additive class.

### S4: what the suites leave behind

After both suites `git status --porcelain` lists only the G6 files above, `runs/` holds only its `.gitignore`, and
there is no untracked `*.sqlite3`, `*.db` or `*.jsonl` in the worktree (every G6 test writes under a temporary
directory, including E2's runs directories and the G0 outputs).

### Pack hashes (only `config_hash` changed)

| Pack | Hash | G5 | G6 |
|---|---|---|---|
| `device_quality` | config | 83c094ffee1f157203f7265a59bdbfc35b7d8f8ed07c35304743377dd301d723 | b9e03c14d88100dac6849ba37525059dfb65e681397c15e59000f5cfbdeeb56f |
| | vocabulary | e46f521154ce94f42136319ade62cebb5c65515ab90a057470495a606f225901 | unchanged |
| | detector | c9462f62aa90245f2c7cee50078d337554bded58c4630cda7becbf7a8048c7ec | unchanged |
| | fixtures | dc4b70b7044b1094baaa669fb5a3582eeae91af290213e13b94180791db5656d | unchanged |
| `claims_integrity` | config | 130b8396eb92e5af060f0dc7b645fb80f00be1e041c1f0d224bcb49566f0cb01 | aa422ef844b583d7d7c0f78afac9147400c0b378b6e193a2347a031330fa329d |
| | vocabulary | 028b7603f2b6ef3bba203dee299ea8b001880ef89cd4b276de8513d2a1a301f3 | unchanged |
| | detector | 2041fe9b3e141a5603836d893c97d5671eb21cbb3da611969efbd0c2c4514b84 | unchanged |
| | fixtures | a2e8936b4be26683f0860c8c8799e701f9aedfb78ea167d5420ac891111b8d80 | unchanged |

`PackPushdownConfigTests::test_only_config_hash_changed_against_g5` asserts all sixteen values. The new
`plant_e2_smoke.json` is in no hash scope (G5's rule for `fixtures/plant_*.json`).

### Merge notes

1. **Nothing in the fabric changed and no fabric integration point is needed for G6 to run.** The orchestrator calls
   each site through an injected handler (`site id -> callable(question) -> verdict`); in-process here.
2. **Fabric event kinds for the merge.** When pushdown runs over the fabric, three event kinds would carry it:
   `question` (HQ to a site: the closed question body, nothing else), `verdict` (a site to HQ: the closed verdict
   body) and `conclusion` (HQ's versioned conclusion, for the fabric's own consumers). Both bodies are already
   canonical JSON with a content id (`question_id`, `verdict_id`); a fabric event should carry them unchanged, and
   HQ should still run `check_artifact` and `receive_verdict`'s checks on intake.
3. **JetStream subjects (proposal).** One subject per direction and site, so a site's credentials can be scoped to
   its own subjects: `mycelic.collective.<enterprise>.site.<site_id>.question` (HQ publishes, the site consumes) and
   `mycelic.collective.<enterprise>.site.<site_id>.verdict` (the site publishes, HQ consumes); message ids
   `q:<question_id>:<site_id>` and `v:<verdict_id>` make redelivery idempotent, matching the Boundary's sha256 no-op
   and HQ's duplicate rule. vr034p's holder signed every envelope with a per-holder key and committed a
   processed-message row with each effect; the fabric's own signing should play that role. The orchestrator's
   thread-per-site delivery and deadline become a publish and a timed wait on the verdict subject; late verdicts
   become ordinary consumption followed by `collect_late`-style re-gating.
4. **Who may verify.** Only HQ's orchestrator (the detection owner's principal) should publish questions; a site
   answers only questions that pass its Boundary (closed spec, its own clock's closed week) and its daily budget,
   and only that site's auditor holds the secret that resolves `evidence_ref`. A fabric permission that lets any
   other principal publish questions would bypass the budget's intent.
5. **HQ tables are prefixed `pd_`** (D6), so a later fabric table named `questions` or `verdicts` cannot collide in
   the no-fabric-name test after the merge. The site store's new tables are `question_log` and `verdict_log`.
6. **`runs/e2/<id>/work/`** holds every simulated site's store, ledgers and logs (git-ignored with the rest of
   `runs/`); the founder sends back `e2.json` and `central.ledger.jsonl` only (RUNBOOK section 10).

### Decisions D1 to D10 (as implemented)

All ten are implemented as the brief states them; ARCHITECTURE section 15.5 summarises each. Two observations from
the planted worlds refine how D2 reads in practice:

- **Same-site duplicates and background roots.** On the device `plant_smoke` world (seed 11) the duplicate decoy's
  site holds background records of `component:ALARM-SPEAKER:detachment` in 2024-W30, W35, W38 and W45, so every
  6-week window over the duplicate span (W33 to W40) holds 2 or 3 distinct roots and the roots check passes there;
  verified at the span's end (`as_of` 2024-10-20) the decoy is a hypothesis on the sites check alone. In the window
  W39 to W44 (`as_of` 2024-11-17), which holds only duplicates at that site, the reasons are exactly the sites reason
  and `hypothesis: independent roots, lower bound 1; min_independent_roots is 3`. `EndToEndPlantedTests` asserts both.
- **Few reporters at k = 5.** In `claims_integrity`, a pattern planted at two sites with fewer than 5 reporters each
  stays a hypothesis on reporters (lower bound 2) until a third site confirms: on its `plant_smoke` world (seed 5)
  `clinic:CL-B7X9:treatment_pattern_mismatch` is a hypothesis from 2024-W43 to W49 and supported at W50, while the
  three-site `tow_operator:TW-0310:tow_without_dispatch` is supported throughout. The end-to-end claims test verifies
  the pattern planted at the most sites.

### Further decisions and deviations, for the reviewer

- **The look-ahead window-end branch of `gate.evaluate` is defence in depth.** A question that passes the schema has a
  window closed at its own `as_of`, so with `as_of` not before the question's the window is always closed; the test
  shows the schema refusing a question whose `as_of` does not close its window.
- **E2 refuses a pack whose `central_allowed_fields` lack `site` or `received_date`** (as R (model-free) does), since
  `central_allowed`'s payload names both. `allowed_view` (E2) reads a record only by subscript for allowed fields; a
  test uses G5's recording record.
- **`bar_verdict` is a pure function** in E2, so the withheld and pass paths are unit-tested apart from a run.
- **Delivery is one site at a time, in sorted site order, each joined against the deadline** (as the brief's
  "join(timeout=deadline_seconds)" per handler reads). The engineer's first version started every site's thread at
  once and joined them against one shared deadline; the G3 test `test_g0_runs_are_byte_identical_across_hash_seeds`
  then failed in a full-suite run, because the sites' verdict and question lines landed in HQ's shared logs in thread
  scheduling order. Sequential delivery fixes that (a hung site delays the others by at most the deadline; only a
  timed-out site can write later), and `LeakageStageTests` now also pins the questions, verdict rows, ingress logs and
  conclusions of two G0 runs under different hash seeds. The clock is read only for the `received_at` and
  `created_at` columns, never for `as_of`.
- **Every SELECT is one literal with `ORDER BY`.** The first version composed five HQ queries from shared fragments
  (`"SELECT " + columns + ...`) and one site query by `str.format`; the G4 and G3 guards
  (`test_sql_only_in_store_and_every_select_is_ordered`, `test_sql_lives_only_in_records_and_every_select_is_ordered`)
  check every string constant that starts with `SELECT`, so each query is now spelled out (the site's verdict reads
  are three explicit queries).
- **`verify_stored` on a rule-only candidate without a run `as_of`** cannot happen (a stored candidate has a run);
  `verify_candidate` uses the rule's first week's closing date as the earliest `as_of`.
- **G0's pushdown stage verifies at the run's `as_of`**, not each snapshot's: every site emits once, at that `as_of`,
  so no cell is visible earlier.

### The E2 smoke fixture recipe (no generator script is committed)

`packs/data/device_quality/fixtures/plant_e2_smoke.json` (sha256
42f868eb2e3127a024b0c66457ae17edeba751f6fd1a57500a726e34501c85c4) was generated once and is reproduced byte for byte by
this recipe: load `device_quality`; `rng =
random.Random("plant_e2_smoke:1")`; sites = the first 6 generator sites; master data from `generate(DQ, 1, 6, 52)`;
evaluation weeks 20 to 51. List every `(type, id, predicate)` for each predicate (sorted) and egress type (sorted)
with an English single-slot template (`plant.single_slot_templates`), over the generator universe's ids (alias-only
ids only when they have an alias), and shuffle the list with `rng`. Then take, in list order: 4 `single_reporter`
decoys on products (2 sites by `rng.sample`, 4 to 6 weeks, rate 3), 3 `cross_site_unmarked_copies` decoys on
products (an origin and two copy sites, rate 2) and 3 `high_base_rate_everywhere` decoys on two components sharing a
predicate (4 sites, rate 2), each starting at `rng.randint(20, 51 - weeks + 1)`; then 85 `narrative_only` patterns
from the remaining combinations whose id is in at least 2 sites' master data (components count at every site), each
at `min(available, rng.choice((2, 2, 3)))` sites drawn from those, 4 to 6 weeks, rate 2, ids `p01` to `p85`. The
spec is `planted_by: "mycelic engineering (same author as the detector code)"`, `planter_saw_detector_code: true`,
`prereg_sha256: null`, written with `json.dumps(indent=2, ensure_ascii=False)` and a final newline. The planner's
probe of the same shape gave about 79 candidates per seed and about 4 s per seed for the pipeline; on this fixture the
five seeds gave 94, 90, 85, 86 and 91 detector candidates in the evaluation weeks (synthetic, same-author).

### Mutation probes run

On a scratch copy of the worktree outside the repository (`git` metadata excluded), each mutation was applied alone
and the named G6 tests were run; every one failed at least one test, and the unmutated copy passed before and after:

| # | Mutation | Caught by |
|---|---|---|
| 1 | count `'<k'` confirms | `GateTableTests` |
| 2 | drop the forwarded-in exclusion (`window_records`, `claimed_refs`) | `VerifyTests` retrieval |
| 3 | let a sibling refute contest | `GateTableTests` |
| 4 | send the exact support count (and, as 4b, with the verdict spec widened to accept it) | `VerifyTests` confirm; `EgressQuestionVerdictTests` |
| 5 | skip the HMAC check in `resolve` | `VerifyTests` secrets and rotation |
| 6 | `>=` in the freshness test | `GateTableTests` |
| 7 | `'<k'` roots count 0 | `GateTableTests` |
| 8 | skip the budget | `VerifyTests` budget |
| 9 | `as_of` in `question_id` (consistently in the Boundary and in `build_question`) | `QuestionTests` |
| 10 | the gate sees verdicts received after `as_of` (10a); the orchestrator passes them to the gate (10b) | `GateTableTests`; `OrchestratorTests` |
| 11 | drop the look-ahead rejection | `GateTableTests` |
| 12 | bucket off by one at the edges (12a) or in the labels (12b) | `BucketTests` |
| 13 | let central_allowed read the narrative | `E2UnitTests` |
| 14 | leave the bar verdict un-withheld under fakes | `E2UnitTests` |
| 15 | check the budget before no_secret | `VerifyTests` missing secret |
| 16 | use the site's main `RecordStore` from the worker thread | `VerifyTests` worker thread |
| 17 | intake skips the unrouted check (extra) | `OrchestratorTests` intake |
| 18 | contributing sites ignore cell visibility (D3; extra) | `RoutingTests` |
| 19 | the Boundary accepts an unclosed window (extra) | `EgressQuestionVerdictTests` |
| 20 | a re-delivered question is answered again (extra) | `VerifyTests` budget |

### Synthetic figures (synthetic, same-author, not a measurement)

Printed or recorded in this sandbox, quoted only as **synthetic, same-author, not a measurement** (every judge was
the lexical judge or a fake replaying it): on the device `plant_smoke` world (seed 11) the three planted patterns are
supported at the first candidate week inside their found windows, with every contributing site confirming and at
least one sibling routed; the echo, same-site-duplicate, single-reporter and stale decoys are hypotheses or stale as
the brief states; the unmarked copies and all three high-base-rate keys reach `supported` (the known hard cases); all
7 supported conclusions are resolvable, with 0 extraction-miss confirmations. G0 with the pushdown stage (seed 11,
1,000 records) passes for both packs with no hit and no narrative overlap (`LEAKAGE.md` section 9). An E2 rehearsal on
`plant_e2_smoke.json` (seeds 1 to 5, top-n 60, fakes everywhere) wrote 300 candidates over 5 seeds, raw-text bytes of
2,487,381 for central_raw and 0 for central_allowed and pushdown, 265 of 265 supported conclusions resolvable, and a
withheld verdict; its AP figures are not quoted because both sides used the same deterministic reader.
