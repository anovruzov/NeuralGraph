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
- the core without `service` loads no `chat_memory` module at all;
- since the review fixes: `NeuralGraph.chat_memory.llm`, which the chain above does load, may be loaded only from
  inside the `NeuralGraph.chat_memory` package, and that package only by `mycelic.retrieval` (a meta-path recorder
  in a fresh interpreter names each importer); no global of a core module is defined in a forbidden module; and the
  static check resolves every name taken from a package (`from pkg import name`, or `import pkg as m` then
  `m.name`) to the module that defines it, so `from NeuralGraph.chat_memory import BackendLLMClient`, a re-export of
  `chat_memory.llm`, is a hit. Before, the guard said "no model-client module is loaded" while one was, and both a
  re-exported import and a module-alias attribute passed it.

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
raised no X alert (audit round 3: those runs' stale chain ended outside every detection window of its watch span, so it
could not alert whatever G4's stale filter did, and the one-site classes test `min_sites`, not five different rules; the
fixtures were re-planted, "Audit round 3" at the end of this file; and a stale chain's alert cannot show a broken stale
filter at all, because the cooldown after its fresh alert holds it, so X1 now also scores it on candidacy, "Audit round
3, review follow-up"); the single reporter and high base rate decoys alerted with their flags set; the unmarked copies
alerted in X. `claims_integrity` (built-in, seed 5): X found 2 of 3 (the rate-2 pattern below k=5 was not found), S 0 of
3, R (model-free) 0 of 3, U 3 of 3, single_site 3 of 3. The lifts over three patterns and one seed are not interpretable
(the scorecard warns), and none of these figures may be shown to anyone outside the team. Re-run with the review fixes
(a no-plant control per seed; single_site counted only at a planted site; "Review fixes" at the end of this file), same
labels: `device_quality` X 3 of 3 (control 0, net 3), S 0 of 3, R 0 of 3, U 3 of 3 (control 0, net 3), single_site 3 of
3 (control 2, chance 0, net 3); `claims_integrity` X 2 of 3 (control 0, net 2), S 0, R 0, U 3 of 3 (control 0, net 3),
single_site 3 of 3 (control 1, chance 0, net 3). In the world without the plant, single_site alerts on two of the device
pattern keys and one of the claims pattern keys at a planted site, but always strictly later than its planted-world find
(device seed 11: planted W31 and W39, control W40 and W41). Those were plant-driven finds, not chance finds: the planted
world's earlier alert came from the plant, and its cooldown hid the later background alert. An earlier version of this
paragraph called them chance finds and reported single_site net 1 and 2 (audit round 2 corrected the rule; "Audit round
2" at the end of this file).

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

## G7

**Base.** Branch `mycelic-collective-phase2` at b861362 (G6), a clean worktree. Nothing is committed by the engineer.

**Built ahead of E2 and X4 (STRATEGY sections 5.5 and 7): approval-routed follow-up is unvalidated; nothing here
measures it.** G7 builds tiers T0 (a site-local evidence packet) and T1 (an HQ draft written to an outbox), each
behind one human approval, over a hash-chained ledger; T2 (a write into a system of record) has no executor. Nothing
is ported: vr034p has no follow-up layer to port.

**Scope: fabric files changed: none.** `git diff --stat b861362` touches only:

- new: `mycelic/collective/followup/{__init__,policy,ledger,service,drafts,executors,outcome}.py`,
  `mycelic/collective/edge/packets.py`, `mycelic/collective/experiments/e5_injection.py`,
  `docs/collective/examples/{approvers,kill_switch}.example.json`, `tests/mycelic/test_collective_followup.py`;
- changed additively: both packs' `followups.json` (D2: the args only), `edge/egress.py` (the `packet_request` and
  `packet` artifacts, their specs and validators, `FOLLOWUP_KEY_RE`, `packet_labels`, the Boundary's packet-request
  log; the cells, usage, question and verdict behaviour is byte-identical and every G3 and G6 test passes),
  `edge/site.py` (the Boundary's packet-request log and one docstring sentence), `detect/store.py` (`HqReader`, one
  literal SELECT for site coverage, a docstring paragraph; no table, trigger or write path changed),
  `experiments/g0_canary.py` (the follow-up stage; `_world` renamed `make_world`; `build_context` and `run_stages`
  split out of `run` so E5 runs the same stages, no behaviour change), `leakage.py` (two `NOT_COVERED` items), the
  docstrings of `mycelic/collective/__init__.py`, `edge/__init__.py` and `experiments/__init__.py`,
  `docs/collective/{ARCHITECTURE,LEAKAGE,RUNBOOK,PACKS,INTEGRATION}.md`, `docs/collective/examples/README.md` and the
  earlier tests listed below.

The follow-up ledger is its own SQLite file (`followups.sqlite3`, schema in `followup/ledger.py`); it shares no table,
connection or schema with the fabric store or HQ's collective store, which follow-up reads only through `HqReader`
(`mode=ro`, one connection per call).

**S2:** `git diff --stat b861362 -- mycelic/service.py mycelic/store.py mycelic/aggregation.py mycelic/transport.py
mycelic/api.py mycelic/lineage.py mycelic/config.py deploy SECURITY.md DEPLOYMENT.md NeuralGraph research
mycelic/collective/detect/detectors.py mycelic/collective/detect/rules.py mycelic/collective/detect/org.py` is empty.
The three detect files keep their G5 sha256 (`7d4ca86b...`, `f0f5caa1...`, `33972beb...`), which
`PushdownImportGuardTests` pins.

### S1 test baseline

Both columns were measured in this sandbox on the shared machine (another team runs suites concurrently, so times are
indicative); "before" is an isolated copy of b861362.

| Suite | Before G7 (b861362) | After G7 |
|---|---|---|
| `python -m pytest tests/mycelic -q -p no:warnings` | 802 passed (25,509 subtests), 0 skipped, in 347 s | 890 passed (25,838 subtests) = 802 + 88 new, 0 skipped, in 417 s (after round 3) |
| `python -m pytest NeuralGraph/tests -q -p no:warnings` | 222 passed, 1 skipped (119 subtests) in 5 s | unchanged: 222 passed, 1 skipped (119 subtests) in 5 s |

The 88 new tests, by class:

- `test_collective_followup.py` (76): `PackFollowupTests` 3, `ProposeGuardTests` 8, `ApproversFileTests` 3,
  `IdempotencyTests` 5, `ConcurrencyTests` 6, `ApprovalScopeTests` 7, `KillSwitchCapTests` 4, `LedgerChainTests` 14
  (two added in round 2, four in round 3), `AssignmentEscalationTests` 4, `PacketDraftTests` 13, `OutcomeTests` 4,
  `InjectionSmokeTests` 3, `LeakageStageTests` 2 (`ApproversFileTests::test_the_example_files_are_valid` keeps the
  two example files valid against an example org);
- `test_collective_guards.py` (12): `FollowupImportGuardTests` 6 (static import checks of every follow-up module and
  of `edge/packets.py`, only `drafts.py` importing inference and only `tasks` and `errors` at run time, an injected
  import flagged, and two fresh-interpreter checks), `ApprovalCallSiteTests` 4 (D10),
  `ClockEntropyTests::test_every_followup_module_is_on_the_determinism_list_with_no_hits` and
  `RunbookCommandTests::test_commands_cover_the_g7_clis`.

The acceptance command `python -m pytest tests/mycelic/test_collective_followup.py -q -p no:warnings` passed
76 tests (340 subtests) in 41 s after round 3 (the bound is about 180 s).

**Earlier tests changed, and why** (each at least as strong as before; nothing skipped, deleted or weakened):

- `test_collective_pushdown.py::PackPushdownConfigTests::test_only_config_hash_changed_against_g5`: `G5_HASHES` and
  `G6_CONFIG_HASHES` stay as history; `G7_CONFIG_HASHES` is added and the test asserts the current `config_hash` is
  not G6's and is G7's (D2); the other three hashes still equal G5's;
- `test_collective_pushdown.py::EgressQuestionVerdictTests::test_constants_and_keys`: `ARTIFACT_TYPES` and
  `ARTIFACT_DIRECTION` gain `packet_request` (in) and `packet` (out); every G6 assertion is unchanged;
- `test_collective_pushdown.py::LeakageStageTests::test_both_packs_pass_with_every_pushdown_artifact_scanned`: the
  stages are `["edge", "pushdown", "followup"]`; every pushdown assertion is unchanged;
- `test_collective_leakage.py::G0RunnerTests::test_a_leaky_stage_fails_the_run`: the stages are `["edge",
  "pushdown", "followup", "leaky"]`;
- `test_collective_evaluate.py::PlantSpecTests::test_the_loader_accepts_plant_files_and_hashes_none_of_them`: the
  two `config_hash` pins only; the vocabulary, detector and fixtures pins are unchanged;
- `test_args_schema_accepts_canonical_ids_only` (the brief places it in `LoadTests`; it lives in
  `test_collective_packs.py::FrozenTests`): rewritten to the D2 args and stronger than before. The conclusion pattern
  through `evidence_packet` (accepted forms, five refused values, an extra arg, a missing arg); the entity-id canonical
  form through `scar_draft`'s `supplier_id` (`V1001` accepted; `v1001`, `V 1001`, `V-1001`, `V10011`, `V100` and a
  full-width form refused; an extra arg refused); the enum through `capa_initiation_draft`'s `severity` (four refused
  values); and the predicate, integer and alias-only entity kinds through a pack copy whose types regain the G6 args,
  with every old case plus the integer bounds (1 and 200 accepted; 201, `10.0` and `true` refused) and a
  wrong-case predicate;
- `test_collective_guards.py`: the lists (`STDLIB_ONLY_MODULES` 50 to 59, `CLI_MODULES`, `DETERMINISTIC_MODULES`,
  four RUNBOOK placeholders) and the twelve new tests above.

### S4: what the suites leave behind

After both suites `git status --porcelain` lists only the G7 files above, `runs/` holds only its `.gitignore`, and
there is no untracked `*.sqlite3`, `*.db` or `*.jsonl` in the worktree: every G7 test writes its ledgers, outboxes,
packets, G0 outputs and E5 runs under a temporary directory. The only ignored files the suite rewrites are the
fabric strategic demo's `demo/results/mycelic-strategic/` outputs, which the base suite writes too.

### Pack hashes (only `config_hash` changed)

| Pack | Hash | G6 | G7 |
|---|---|---|---|
| `device_quality` | config | b9e03c14d88100dac6849ba37525059dfb65e681397c15e59000f5cfbdeeb56f | 285935198ba34f2e194dc175cffd1f8f62c494d03d8fb7400ca1aef709058f2a |
| | vocabulary | e46f521154ce94f42136319ade62cebb5c65515ab90a057470495a606f225901 | unchanged |
| | detector | c9462f62aa90245f2c7cee50078d337554bded58c4630cda7becbf7a8048c7ec | unchanged |
| | fixtures | dc4b70b7044b1094baaa669fb5a3582eeae91af290213e13b94180791db5656d | unchanged |
| `claims_integrity` | config | aa422ef844b583d7d7c0f78afac9147400c0b378b6e193a2347a031330fa329d | a3a042943452f6ef781f171cf879f3ba5f594f6c4dae5ffef47bfa241bb392da |
| | vocabulary | 028b7603f2b6ef3bba203dee299ea8b001880ef89cd4b276de8513d2a1a301f3 | unchanged |
| | detector | 2041fe9b3e141a5603836d893c97d5671eb21cbb3da611969efbd0c2c4514b84 | unchanged |
| | fixtures | a2e8936b4be26683f0860c8c8799e701f9aedfb78ea167d5420ac891111b8d80 | unchanged |

Only the follow-up types' `args_schema` changed (D2); owner and escalation roles, caps, acknowledgement days, tiers and
`enabled` are G6's (`PackFollowupTests::test_the_new_args_shapes_and_the_unchanged_settings`). A follow-up ledger
records the pack's `config_hash` in `ledger_info`, so a ledger written under G6's config would not open under G7's
(`info_mismatch:config_hash`); no G6 ledger exists.

### Merge notes

1. **Nothing in the fabric changed and no fabric integration point is needed for G7 to run.** Packet requests go to
   each site through an injected handler (`site id -> callable(request) -> packet`), in-process here, exactly as G6's
   questions do; the approvers and kill-switch files and the ledger are plain files the caller names.
2. **Fabric event kinds for the merge.** Three event kinds would carry follow-up over the fabric:
   `followup.proposed` (the `proposed` entry's payload, the key, and the ledger seq and hash), `followup.approved`
   (the `approved` entry: version, role, unit path, `approvers_hash`, conclusion version, `as_of` and the person
   label) and `followup.result` (the `executed`, `outcome_unknown` or `blocked` entry). The ledger stays the record
   of truth; an event carries the entry hash so a consumer can check it against `verify --expected-head`. Rejections,
   edits and refusals stay in the ledger; publish them only if a consumer needs them.
3. **JetStream subjects (proposal).** One subject per direction and site, as G6's questions and verdicts:
   `mycelic.collective.<enterprise>.site.<site_id>.packet_request` (HQ publishes, the site consumes) and
   `mycelic.collective.<enterprise>.site.<site_id>.packet` (the site publishes, HQ consumes). Message ids
   `pr:<sha256 of the canonical request>` and `p:<sha256 of the canonical packet>` make redelivery idempotent,
   matching the Boundary's sha256 no-op. The executor's per-site deadline becomes a timed wait on the packet subject;
   a site that misses it is listed as unavailable with reason `timeout` in the result, as now.
4. **Approver authority onto fabric auth scopes.** The approvers file grants `(person_label, role, unit_path)`;
   authority is the follow-up type's owner or escalation role held on a unit that is, or is an ancestor of, every
   target site's unit (D1). In the fabric this maps onto a principal scope such as
   `followup:decide:<role>@<unit_path>` over the same agent, team, department, subsidiary, region and enterprise
   hierarchy, with the file generated from the fabric's auth store (or the service reading the store). The service
   must still re-check authority at every decision from the current table, never from the assignment, and record
   that table's hash (`approvers_hash`) in the decision. Person labels are pseudonymous; the mapping to identities
   stays in the fabric's auth store.
5. **Anchor the ledger head in the fabric's signed event log.** The chain detects edits, reordering and deletion
   inside the file, but a truncated tail is a valid shorter chain
   (`LedgerChainTests::test_tail_truncation_passes_without_an_anchor_and_fails_with_one`). Publish the head hash and
   entry count (`ledger_head_hash` in G0's `followup_totals`; `summary()` in the service) as a signed fabric event
   after each `followup.result`, and check with `python -m mycelic.collective.followup.ledger verify --ledger
   <ledger-file> --expected-head <head-hash>` (RUNBOOK section 14).
6. **The approval call-site guard covers the fabric.** `ApprovalCallSiteTests` scans every `.py` under `mycelic/`
   and `demo/` for calls of `approve`, `edit` or `reject` (by name, as an attribute, or through `getattr` with a
   constant name) outside the allow-list (`followup/service.py`, G0's simulated owner, `demo/collective/
   collective_demo.py`, and `tests/`). A merge that adds an API route or MCP tool calling the service's decisions
   fails the guard until the route is added to the allow-list deliberately; a fabric method that happens to be named
   `edit` or `reject` would be flagged too (none exists at b861362). The scan cannot see a decision method bound to
   another name first (`f = svc.approve; f(...)`) or reached through `getattr` with a computed name.
7. **One executing service per ledger (D12): a lease is required, not optional.** Decisions from any number of
   instances are safe (each check re-reads the key inside `BEGIN IMMEDIATE`); execution must run in one process,
   because the in-flight set that tells `in_progress` from `interrupted` is per process. The reviewer's probe confirms
   what happens otherwise: when a second instance executes a key while the first runs it, the executor runs once, but
   the second records `outcome_unknown` (`interrupted`), the first's `executed` is then refused by the unique index,
   and both return `outcome_unknown` while the side effect (packets, an outbox line) exists. In a multi-replica
   deployment, give execution a fabric lease or a single worker.
8. **Deployment (not done here; `deploy/` is out of scope).** The ledger, the outbox, `approvers.json` and
   `kill_switch.json` need a persistent volume at HQ; the kill file must exist (a missing or unreadable file is ON), and
   `MYCELIC_FOLLOWUP_KILL` (`all` or a comma-separated list of type ids) is the operator's override. Sites need a
   writable `packets/` directory in their work directory, which is site-local and never shipped.

### Decisions D1 to D12 (as implemented)

All twelve are implemented as the brief states them; ARCHITECTURE section 16.12 summarises each: D1 approvers in
their own file, read at every use; D2 the pack args; D3 every tier, T0 included, needs one human approval; D4 the key
covers the targets; D5 targets default to the contributing sites; D6 packet counts as buckets with complementary
suppression, co-mentions from structured master-data fields at k or more; D7 packet requests and packets as Boundary
artifacts with their own HQ log; D8 the draft scope scan over types with an id format only; D9 fifteen ledger kinds;
D10 the approve, edit and reject call sites; D11 HQ read only through `HqReader`; D12 one executing service per
ledger.

### Further decisions and deviations, for the reviewer

- **The full packet is kept at `<workdir>/packets/site-<id>/<sha256(key)[:16]>.json`**, not directly under
  `packets/`: G0's sites share one work directory, so two sites answering the same follow-up would otherwise collide
  (and the write never overwrites).
- **The template summary reads "supported, <n> confirming site(s)"**, not "supported at <n> site(s)": in the device
  pack two letters, a space and a number read as a space-separated product id, and the D8 scan rightly refuses that
  as an unresolved lookalike. The scan is conservative by design; the template avoids the form.
- **The service takes no clock.** The ledger stamps `at` with its own injected clock; every decision, cap and due date
  uses the caller's `as_of` (mutation 7 shows the cap ignores `at`).
- **Ledger refusal reasons** add `wal` (a file that cannot be put in WAL mode) and `conflict` (`LedgerConflict`, a
  unique-index violation inside `append`) to the brief's `missing`, `exists` and `unreadable`.
- **`PRAGMA integrity_check`, not the brief's `quick_check`** (round 3), in `FollowupLedger.open` and `verify_chain`.
  Strictly stronger: every file `integrity_check` accepts, `quick_check` accepts too, and `integrity_check` also
  compares every index with its table, which the service's per-key reads depend on (round 3 below). The cost on a
  20,000-entry, 11 MB ledger, measured by the reviewer: 0.032 s against 0.011 s, while the whole open took 0.633 s.
- **`outcome.evaluate` also takes `conclusion_id` and `candidate_key`**, which its result names.
- **The ledger's lock is reentrant** (`threading.RLock`), so a read on the thread that holds a transaction does not
  deadlock; the service has its own reentrant lock around each check-and-append.
- **`HqReader` never writes HQ's store**, but on a WAL file whose writer has closed SQLite itself may create the empty
  `-wal` and `-shm` files a reader needs; a missing store is `StoreError('missing')` and creates nothing.
- **A regenerated draft's `drafted` entry names the principal who asked** (`system` or the person); the first draft's
  is `system`.
- **The approval call-site scan's limit** is stated in merge note 6.
- **No model drafts here.** Every draft in the tests and in G0 is the deterministic template or a fake provider
  replaying it; `DraftWriter` with a `central` runtime is exercised only against fakes.

### Mutation probes run

On a scratch copy of the worktree outside the repository (`git` metadata excluded), each mutation was applied alone
and the named test classes were run; every one failed at least one test, and the unmutated copy passed before and
after (62 tests, 242 subtests). The engineer's first run found one gap: no test fed the drafter a model draft that
names an out-of-scope id, so a drafter that skipped the scope scan (19a) survived the earlier tests; G7 adds
`PacketDraftTests::test_a_model_draft_naming_an_id_outside_its_scope_is_refused_and_never_stored`, which catches it.

| # | Mutation | Caught by |
|---|---|---|
| 1 | skip the supported check | `ProposeGuardTests::test_conclusion_not_supported_for_every_other_status` (all four statuses) |
| 2 | skip the args scope check | `ProposeGuardTests::test_each_refusal_code_writes_exactly_one_refused_entry`, `::test_supplier_keys_scar_scope_and_the_canonical_form`, `::test_pack_copy_refusals_tier_disabled_integer_and_predicate` |
| 3 | skip the targets check (given targets outside the contributing sites accepted) | `ProposeGuardTests::test_each_refusal_code_writes_exactly_one_refused_entry` |
| 4 | a missing kill file treated as OFF | `KillSwitchCapTests::test_between_propose_and_approve_and_per_type_versus_global`, `::test_on_after_approval_blocks_execute_and_off_runs_it` |
| 5 | the env ON ignored when the file is OFF | `KillSwitchCapTests::test_the_state_table`, `::test_between_propose_and_approve_and_per_type_versus_global` |
| 6 | the daily cap compares with `>` instead of `>=` | `KillSwitchCapTests::test_the_daily_cap_counts_proposals_per_type_and_utc_date` |
| 7 | the daily cap counts by the entry's `at` instead of the UTC date of `as_of` | `KillSwitchCapTests::test_the_daily_cap_counts_proposals_per_type_and_utc_date` |
| 8a | `executing` omitted | `IdempotencyTests::test_execute_twice_runs_the_executor_once_and_returns_the_stored_bytes`, `::test_replay_into_a_fresh_service_calls_no_executor`; `ConcurrencyTests::test_two_executes_with_a_blocking_executor_run_it_once`, `::test_a_crash_at_the_executed_append_leaves_executing_then_outcome_unknown`, `::test_an_executor_exception_is_outcome_unknown_with_nothing_of_it_kept` |
| 8b | `executed` written before the executor runs | `IdempotencyTests::test_execute_twice_runs_the_executor_once_and_returns_the_stored_bytes`, `::test_re_proposing_after_reject_or_execute_appends_nothing`; the same three `ConcurrencyTests` |
| 9 | replay calls the executor (a service built on an existing ledger runs its approved follow-ups) | `IdempotencyTests::test_replay_into_a_fresh_service_calls_no_executor` |
| 10 | approve accepted on a stale version (`>` instead of `!=`) | `ApprovalScopeTests::test_edit_limits_the_diff_and_version_pinning`; `ConcurrencyTests::test_edit_versus_approve_never_approves_a_superseded_version` |
| 11 | authority read from the assignment (the assigned owner may decide) instead of the current file | `ApprovalScopeTests::test_authority_is_read_from_the_current_approvers_file`, `::test_a_target_no_longer_in_the_org_is_out_of_scope` |
| 12 | the system principal allowed to approve, edit and reject | `ApprovalScopeTests::test_the_system_principal_never_decides` (all three operations) |
| 13 | complementary suppression removed | `PacketDraftTests::test_the_code_distribution_table`, `::test_packets_from_every_contributing_site_pass_the_validator_and_hold_no_total`, `::test_the_full_packet_stays_in_the_site_workdir`, `::test_packet_requests_are_logged_apart_and_an_unclosed_window_is_refused` (the Boundary's `suppression` check then refuses the packets) |
| 14 | co-mentions include narrative claims | `PacketDraftTests::test_co_mentions_come_only_from_structured_master_data_fields`, `::test_a_narrative_naming_a_master_data_supplier_reaches_no_packet_and_no_draft` |
| 15 | the entry hash omits the payload | `LedgerChainTests::test_payload_and_prev_hash_tampering_and_deletion_are_found_at_their_seq` |
| 16 | `verify_chain` ignores seq gaps | `LedgerChainTests::test_payload_and_prev_hash_tampering_and_deletion_are_found_at_their_seq` |
| 17 | the outcome accepts an overlapping post window | `OutcomeTests::test_an_overlapping_or_malformed_post_window_is_refused`, `::test_check_outcome_is_refused_before_execution_and_records_each_distinct_result` |
| 18 | the outcome imputes a `'<k'` cell as 0 | `OutcomeTests::test_suppressed_cells_are_imputed_and_other_sites_ignored` |
| 19a | the drafter skips the draft scope scan | `PacketDraftTests::test_a_model_draft_naming_an_id_outside_its_scope_is_refused_and_never_stored` (added after this probe survived the first run) |
| 19b | the scope scan finds nothing (drafter and edit) | the same test; `PacketDraftTests::test_drafts_from_structured_inputs_only`; `ApprovalScopeTests::test_edit_limits_the_diff_and_version_pinning` |
| 20 | edit accepts an unknown field (dropped silently instead of `draft_invalid`) | `ApprovalScopeTests::test_edit_limits_the_diff_and_version_pinning` |
| 21 | overdue escalates on every call | `AssignmentEscalationTests::test_ack_due_and_overdue_escalation` |
| 22 | open on a missing ledger creates it | `LedgerChainTests::test_missing_existing_and_mismatched_files` |

### Round 2: review fixes

**Blocking finding: a byte flip inside a TEXT cell crashed `open`, `verify_chain` and the `verify` CLI with a raw
`UnicodeDecodeError`.** It is fixed in `followup/ledger.py`, and nothing else changed for it:

- **Strict decoding.** Every ledger connection (`create`, `open`, `verify_chain`) now sets a `text_factory` that
  decodes TEXT strictly as UTF-8. Bytes that are not UTF-8 read as a marker that is never a string, so every check
  sees a bad value instead of raising. BLOB cells still read as bytes, so a payload retyped to TEXT is still caught.
  The results are:
  - an entry's `at`, `kind`, `key`, `actor` or `hash` cell gives `bad_entry` at its seq;
  - its `prev_hash` gives `prev_hash_mismatch`;
  - a `ledger_info` cell gives `unreadable`.
- **SQLite error messages.** SQLite's own error message can quote a schema name whose bytes were flipped, and Python
  decodes that message into a `UnicodeDecodeError`. This happens, for example, when a byte of the index name
  `entries_one_assigned` in `sqlite_master` is flipped; the reviewer's repro landed there first. `verify_chain` and
  `open` now catch it next to `sqlite3.DatabaseError` and map it to `unreadable`.
- **`ledger_info` key check.** `_info_ok` compares key sets instead of sorting them. A key retyped as a BLOB used to
  raise `TypeError` from `sorted`.
- **Reads after `open`.** Hardening beyond the finding: a payload damaged after `open` is
  `LedgerError('chain:bad_entry', seq)` on the next read of that row, never a JSON or decoding error. This covers
  `FollowupLedger.entries`, `LedgerTx.entries` and `entries_for_conclusion`, which share `_entry`, and the daily-cap
  count.
- **New tests:**
  - `LedgerChainTests::test_undecodable_or_retyped_cells_are_a_chain_problem_not_a_traceback`. It has seven cases
    (eight since round 3, which changed the first):
    - an entry's `kind`, byte-flipped in the closed file: `bad_entry` at seq 2, CLI exit 1 (since round 3
      `unreadable`, CLI exit 2: see round 3);
    - `actor` forged as `X'FF'`: `bad_entry` at seq 4;
    - `prev_hash` forged as invalid UTF-8: `prev_hash_mismatch` at seq 3;
    - a payload retyped as TEXT: `bad_entry` at seq 2;
    - a `ledger_info` value, byte-flipped: `unreadable`, CLI exit 2;
    - a `ledger_info` key retyped as a BLOB: `unreadable`;
    - a schema name, byte-flipped: `unreadable`, CLI exit 2.

    Every case asserts the `open` text, the chain report, one JSON line on stdout and an empty stderr.
  - `LedgerChainTests::test_a_payload_damaged_after_open_is_bad_entry_not_a_decoding_error`.
- **Fuzz results** (scratch probes outside the repository):
  - The reviewer's `p3_ledger_fuzz.py` (1,577 flips) now gives no raw exception; it gave 32 before. The breakdown is
    110 `bad_entry`, 13 `prev_hash_mismatch`, 1 `seq_gap`, 104 `unreadable`, and 1,348 flips that opened unchanged.
  - The reviewer's `p4_text_flip.py` now gives `ledger corrupt: unreadable` and CLI exit 2 with a JSON line.
  - An exhaustive run flipped every byte of a closed 9-entry, 57,344-byte ledger with XOR 0xFF and with XOR 0x01:
    114,688 files in all. Every one either opened with all 9 entries or raised `LedgerError`, and every
    `verify_chain` returned a report. None raised anything else. That run compared only the full scan; it did not
    compare the per-key reads, which go through the `entries_key` index, and the review of round 2 found index
    damage it could not see (round 3 below).

**Non-blocking notes:**

- **Fixed:**
  - **`plus_days`.** A day past 9999-12-31 is now `FollowupError` with a fixed text; it was a raw `OverflowError`. A
    proposal whose `ack_due` would pass it writes nothing
    (`ProposeGuardTests::test_as_of_normalisation_and_malformed_calls_write_nothing`).
  - **Replay** now refuses the following as `chain:bad_entry`:
    - a `drafted`, `draft_failed` or `edited` entry on a T0 key;
    - a `proposed` entry whose executor is not its tier's (T0 `packet`, T1 `draft`).

    Five cases cover this in `LedgerChainTests::test_replay_is_pure_and_refuses_gaps_and_impossible_transitions`.
- **Docs:**
  - **Draft scope scan.** The `drafts.py` docstring and LEAKAGE section 10 now say which restatements the scan does
    not see: a separator inserted into a format that has none (device `V-1001`, `V 1001`), or removed from one that
    has one (device `SD10`, claims `RS12345`). The scan catches a space for a hyphen (`SD 10`, `RS 12345`), case
    variants, homoglyphs and non-ASCII digits.
  - **Duplicate targets.** ARCHITECTURE section 16.4 states that duplicate targets are checked at step 10, so an
    earlier refusal wins. It also states that a damaged HQ store mid-call raises `HqReader`'s own error and writes
    nothing.
  - **Kill switch.** RUNBOOK section 14 says proposing and approving are refused, and executing records `blocked`.
  - **Late packets.** The `executors.py` docstring and ARCHITECTURE section 16.9 say a late packet handler is not
    cancelled: its packet may still reach HQ's receive log, where G0 scans it, while the result lists the site as
    `timeout`.
  - **Lease.** D12 (ARCHITECTURE section 16.12) and merge note 7 state that a lease is required and what happens
    without one.
- **Left as documented:**
  - `ApprovalCallSiteTests` scanning the fabric (merge note 6);
  - `HqReader`'s `-wal`/`-shm` side files (the deviation list above).

**Mutation probes for round 2** were run on a fresh scratch copy with the runner above. The runner's mutant 22 was
updated because `open` now connects through `_connect`, and mutant 7 because the cap query now selects `seq`. Each
probe was applied alone:

- all 32 variants were caught: mutants 1 to 22 (24 variants, rerun) and the eight below;
- the unmutated copy passed before and after (64 tests, 258 subtests).

| # | Mutation | Caught by |
|---|---|---|
| r2a | TEXT cells decoded by Python's default (no strict text factory) | `LedgerChainTests::test_undecodable_or_retyped_cells_are_a_chain_problem_not_a_traceback` (the `kind`, `actor` and `prev_hash` cases) |
| r2b | an undecodable SQLite error message not caught | the same test (the schema-name case) |
| r2c | `ledger_info` keys sorted (mixed types raise) | the same test (the BLOB-key case) |
| r2d | replay accepts `drafted`, `draft_failed` and `edited` on a T0 key | `LedgerChainTests::test_replay_is_pure_and_refuses_gaps_and_impossible_transitions` (the three T0 cases) |
| r2e | replay accepts an executor that is not its tier's | the same test (the two executor cases) |
| r2f | `plus_days` lets `OverflowError` escape | `ProposeGuardTests::test_as_of_normalisation_and_malformed_calls_write_nothing` |
| r2g | a row read after `open` parses its payload unchecked | `LedgerChainTests::test_a_payload_damaged_after_open_is_bad_entry_not_a_decoding_error` |
| r2h | the daily-cap count parses payloads unchecked | the same test |

### Round 3: review fixes

**Blocking finding: one byte flipped in the `entries_key` index passed `quick_check`, `open` and `verify_chain`, and
the service then acted on wrong per-key state.** The review of round 2 found an edit accepted on an approved T1
follow-up (`edited` after `approved`), after which every `open` refused the ledger (`bad_entry at seq 5`) and the
append-only triggers kept the bad entry for good. The cause: `quick_check` never compares an index with its table;
the chain walk and `replay` read the table with a full scan; every check of the service reads a key's entries through
`entries_key` (`LedgerTx.entries`, `entries_for_conclusion`). Fixed in `followup/ledger.py`:

- **`PRAGMA integrity_check`** in `FollowupLedger.open` and `verify_chain` (a deviation from the brief, strictly
  stronger; see the deviation list above). A file whose index disagrees with its table is `unreadable`: `open` raises
  `ledger corrupt: unreadable` before it compares `ledger_info`, `verify_chain` reports `unreadable`, and the CLI
  exits 2 with one JSON line.
- **Reads and writes after `open`** (the reviewer's optional fix, and non-blocking notes 2 and 3). Every read and
  write after `open` (`FollowupLedger.entries`, `head_hash`, `transaction`'s `BEGIN` and `COMMIT`, and in `LedgerTx`
  `entries`, `entries_for_conclusion`, `proposed_count` and `append`) maps a damaged page (`SQLITE_CORRUPT` or
  `SQLITE_NOTADB`, by the error's SQLite result code) to `LedgerError('unreadable')`. Any other SQLite error
  propagates unchanged: a busy file, and the unique-index violation that `append` turns into `LedgerConflict`. A row
  whose `at`, `key`, `actor`, `prev_hash` or `hash` cell is no longer a string, and a last hash that is no longer 64
  hex characters (the one `head_hash` returns and `append` chains to), are `LedgerError('chain:bad_entry', seq)`.
- **Docs.** The ARCHITECTURE section 16 sentence that claimed "a byte flip in a page or the header is
  `unreadable`" is corrected and now names the check, the index and the limit below. The ledger module's docstring
  says the same, and the round 2 fuzz bullet above now states that the run compared the full scan only.

**The limit that remains** (ARCHITECTURE section 16 and the ledger docstring): damage made while a ledger is open is
found by the read that meets it or at the next `open`. Until then a damaged index can still mislead a per-key read.
The partial unique indexes are what keep one terminal decision and one execution per key, and in the reviewer's
probes they held in every case.

**New tests** (`LedgerChainTests`, four):

- `test_an_index_flip_that_passes_quick_check_is_unreadable`. It scans the cell area of the `entries_key` root page
  (found through `sqlite_master`, so `dbstat` is not needed) for the first XOR 0x01 flip that `quick_check` accepts
  and that changes or breaks a per-key read through the index. It then asserts:
  - `quick_check` passes and `integrity_check` does not;
  - the `verify_chain` report;
  - the `open` text, also under another pack: `unreadable` comes before the `ledger_info` comparison;
  - CLI exit 2, one JSON line and an empty stderr.

  In the probe for this test, 643 of the 666 bytes in that cell area were such flips (synthetic).
- `test_damage_met_after_open_is_a_ledger_error_and_a_busy_file_is_not`. The `entries_key` page type is damaged
  under a ledger as `open` left it. `entries(key)`, `tx.entries`, `tx.entries_for_conclusion`, `tx.append`, and the
  service's `state`, `execute`, `approve` and `reject` each raise `LedgerError` with text `ledger corrupt: unreadable`
  and no cause. Nothing is appended and no executor runs. A file held by another writer raises
  `sqlite3.OperationalError`, not `LedgerError`.
- `test_a_text_cell_damaged_after_open_is_bad_entry`. A `hash` cell forged as invalid UTF-8 gives `chain:bad_entry`
  at its seq from `head_hash`, `entries` and the next append, which appends nothing. A forged `actor` cell gives the
  same from `state`.
- `test_each_partial_unique_index_allows_one_entry_per_key` (non-blocking note 1). For each of the six partial unique
  indexes, every ordered pair within its group (15 pairs) appended directly on one key raises `LedgerConflict`, rolls
  back and leaves the entry count unchanged; the same kind on another key is accepted. The six other kinds
  (`refused`, `drafted`, `draft_failed`, `edited`, `blocked`, `outcome`) are accepted twice on one key.

**An earlier G7 test changed, and is stronger:**
`test_undecodable_or_retyped_cells_are_a_chain_problem_not_a_traceback`.
- Its byte-flipped `kind` case now expects `unreadable` and CLI exit 2. The flipped value no longer matches
  `entries_one_assigned`'s `WHERE`, so that partial index is out of step with its table, and `integrity_check`
  reports it before the chain walk reads the cell.
- A new case forges the `kind` by SQL, which keeps the indexes in step. It keeps the undecodable-`kind` path covered
  at `bad_entry` at seq 2, CLI exit 1.

The test now has eight cases instead of seven.

**Fuzz** (scratch probes outside the repository, synthetic ledgers):

- **The reviewer's `p10_index_repro.py`:** "no such flip found". Before the fix, an edit of an approved follow-up was
  accepted.
- **The reviewer's `p8_index_service.py`** (3,000 random flips): 2,340 files opened, every one with per-key reads
  equal to the table and `integrity_check` ok. Before: 60 opened with differing per-key reads and 4 raised a raw
  `DatabaseError`.
- **The reviewer's `p9_index_edit.py`** at seeds 1 to 4, 4,000 flips each: 3,141, 3,159, 3,138 and 3,131 files
  opened, every one with per-key reads equal to the table. No index-damaged file opened, so no service call ran on
  one.
- **An exhaustive run with per-key reads.** It flipped every byte of a closed 13-entry, 61,440-byte ledger with three
  keys (an executed T0, a rejected T1 and an approved T1) with XOR 0x01 and with XOR 0xFF: 122,880 files.
  - Every file either raised `LedgerError` or opened. For each that opened, the following were all compared with the
    table and all equal: the full scan, every key's per-key read, the conclusion's range read and the daily-cap
    counts (48,019 files under 0x01, 47,974 under 0xFF).
  - `verify_chain` returned a report for every file.
  - Nothing else was raised, and no read after a clean `open` failed.

**Non-blocking notes:**

1. **The partial unique indexes are now pinned** by the test above, and every index has its own mutant (below).
2. **A `sqlite3.DatabaseError` from a read after `open`** is mapped to `LedgerError('unreadable')` (above).
3. **Text cells damaged after `open`** (`_entry`, `head_hash`, `append`) give `chain:bad_entry` (above).
4. **The round 2 fuzz wording** now says the exhaustive run compared the full scan only.
5. **The notes accepted as documented** stay as they are.

**Mutation probes for round 3** were run on a fresh scratch copy with the same runner. Mutant 7 and mutant r2g were
updated for the new code: the cap loop reads its rows first, and `_entry` checks the text cells. Results:

- all 49 variants were caught: mutants 1 to 22 (24 variants), r2a to r2h (8) and the 17 below;
- the unmutated copy passed before and after (68 tests, 291 subtests);
- r2a is now caught first by `test_a_text_cell_damaged_after_open_is_bad_entry`, and still by the undecodable-cell
  test when that one runs alone.

| # | Mutation | Caught by |
|---|---|---|
| r3a | the file check is `quick_check` (in `open` and `verify_chain`) | `LedgerChainTests::test_an_index_flip_that_passes_quick_check_is_unreadable` |
| r3b | `open`'s own file check is `quick_check` (`verify_chain`, which `open` also calls, keeps `integrity_check`) | the same test: under another pack the mutant reports `ledger_info mismatch: pack_id` instead of `unreadable` |
| r3c | `verify_chain`'s file check is `quick_check` | the same test (the report and the CLI exit) |
| r3d | no file check at all (the reviewer's `if True:` in both places) | the same test |
| r3e | damage met after `open` propagates as a raw sqlite3 error | `LedgerChainTests::test_damage_met_after_open_is_a_ledger_error_and_a_busy_file_is_not` |
| r3f | every sqlite3 error after `open` is called damage, a busy file included | the same test (the busy case) |
| r3g | a row read after `open` keeps an undecodable text cell | `LedgerChainTests::test_a_text_cell_damaged_after_open_is_bad_entry` |
| r3h | the last hash read after `open` is used unchecked | the same test |
| r3i | `LedgerTx.entries` reads unguarded | `LedgerChainTests::test_damage_met_after_open_is_a_ledger_error_and_a_busy_file_is_not` |
| r3j | `LedgerTx.append`'s insert unguarded | the same test |
| r3k | the decision index covers `approved` only | `LedgerChainTests::test_each_partial_unique_index_allows_one_entry_per_key` |
| r3-proposed, r3-assigned, r3-decision, r3-executing, r3-closing, r3-escalation | each partial unique index made non-unique, one at a time | the same test |

### Synthetic figures (synthetic, same-author, not a measurement)

Printed or recorded in this sandbox, quoted only as **synthetic, same-author, not a measurement** (every extractor,
judge and drafter was lexical, the template or a fake replaying them; approvals were simulated): G0 with the
follow-up stage (seed 11, 1,000 records) passes for both packs with no hit and no narrative overlap; the device pack
proposes, approves and executes 8 follow-ups on 4 supported conclusions (21 packets, 4 drafts, 4 outbox lines, 44
ledger entries) and the claims pack 2 on 1 (6 packets, 1 draft, 1 outbox line, 11 ledger entries); `LEAKAGE.md`
section 10 has the table. The E5 plumbing smoke (ARCHITECTURE section 16.11) gives 0 hits in every follow-up artifact
for both packs and both variants, with HQ holding 3 (device, `V9999`) and 1 (claims, `RS-99999`) cells naming the
injected id. Neither is evidence about a model or about E5 itself.

## G8

**Base.** Branch `mycelic-collective-phase2` at 3a4c786 (G7), a clean worktree. Nothing is committed by the engineer.

**A fictional company, synthetic data and a constructed illustration; internal use only; never a measurement.**
(G8 said "internal and YC use only"; audit round 2 corrected it: STRATEGY 9.1, the YC demo's own rules, puts
synthetic-fixture results of any kind off the YC screen, and showing this run to YC is the founder's decision.) G8 runs the loop end to end for one fictional multi-site device maker (Halvern Medical, six plants in
four countries) and shows it on a console whose every number is read from run files (ARCHITECTURE section 17).
Nothing in the loop changed: `detect/`, `pushdown/`, `followup/`, `edge/`, `evaluate/`, `inference/` and `packs/`
(pack data and the four hashes per pack) are byte-identical to G7.

**Scope: fabric files changed: none.** `git status` against 3a4c786 lists only:

- new: `mycelic/collective/runfiles.py`; `demo/collective/{collective_demo,scenario,screen,lint_numbers}.py`,
  `scenario.json`, `console.html`, `SCRIPT.md`, `README.md`; the committed run, the six run files under
  `demo/collective/recorded/collective-halvern-g8/`; `tests/mycelic/test_collective_demo.py`;
- changed additively: `mycelic/collective/experiments/g0_canary.py` (the `run_files` stage, `run_files` in
  `CROSSING_CLASSES`, `G0Context.run_files_totals`, a docstring paragraph and the dry-run line for `run/`),
  one docstring line in `mycelic/collective/__init__.py`,
  `docs/collective/{RUNBOOK,ARCHITECTURE,LEAKAGE,INTEGRATION}.md`, and the earlier tests listed below.

**S2:** `git diff --stat 3a4c786 -- mycelic/service.py mycelic/store.py mycelic/aggregation.py mycelic/transport.py
mycelic/api.py mycelic/lineage.py mycelic/config.py deploy SECURITY.md DEPLOYMENT.md NeuralGraph research demo/live
demo/README.md mycelic/collective/detect/detectors.py mycelic/collective/detect/rules.py
mycelic/collective/detect/org.py` is empty (the three detect files keep their G5 sha256, `7d4ca86b...`,
`f0f5caa1...`, `33972beb...`); so is the same diff over `mycelic/collective/{detect,pushdown,followup,edge,evaluate,
inference,packs}` and `leakage.py`. `git diff --stat 3a4c786 -- mycelic` lists only `mycelic/collective/__init__.py`
(one docstring line) and `mycelic/collective/experiments/g0_canary.py`.

**S3, S5 to S8.** The guards pass (`test_collective_guards.py`, in the suite below). Every figure G8 adds to the docs
and every run file is labelled synthetic, same-author or not a measurement (`measurement: false` in every run file).
Everything is standard library only: the 60 modules import and the demo's two scripts answer `--help` under
`python -S`. Pack hashes are unchanged (table below). Nothing is committed.

### S1 test baseline

Both columns were measured in this sandbox on the shared machine (another team runs suites concurrently, so times are
indicative); "before" is an isolated copy of 3a4c786.

| Suite | Before G8 (3a4c786) | After G8 |
|---|---|---|
| `python -m pytest tests/mycelic -q -p no:warnings` | 890 passed (25,838 subtests), 0 skipped, in 403 s | 947 passed (26,080 subtests) = 890 + 57 new, 0 skipped, in 514 s |
| `python -m pytest NeuralGraph/tests -q -p no:warnings` | 222 passed, 1 skipped (119 subtests) in 5 s | unchanged: 222 passed, 1 skipped (119 subtests) in 5 s |

The 57 new tests, by file:

- `test_collective_demo.py` (48): `RunFilesTests` 9, `RecordTests` 10, `LintTests` 5, `HonestyTests` 8,
  `DeterminismTests` 2, `ExportReplayTests` 11, `CommittedRunTests` 3. `setUpModule` records the committed scenario
  twice (`PYTHONHASHSEED` 0 and 4242, different work and output directories) for `RecordTests` and
  `DeterminismTests`; `HonestyTests` records three scenario variants (visibility both; a two-week hero, which the
  gate keeps a hypothesis; a one-week hero, which X does not alert). The file ran in
  94 s on its own.
- `test_collective_guards.py` (7): `StdlibOnlyTests::test_demo_scripts_answer_help_without_site_packages` (also
  imports `scenario.py` and `screen.py` under `-S` and builds a static screen), `DemoImportGuardTests` 5 (the AST check
  over `screen.py` and `lint_numbers.py`, positive and negative snippets, an injected import in a copy of the lint, a
  fresh interpreter) and `RunbookCommandTests::test_commands_cover_the_g8_clis`.
- `test_collective_leakage.py` (2): `G0RunnerTests::test_run_files_stage_both_packs` and
  `G0RunnerTests::test_narrative_written_into_run_files_fails_the_run`.

The acceptance command `python -m pytest tests/mycelic/test_collective_demo.py tests/mycelic/test_collective_guards.py
-q -p no:warnings` passed with nothing skipped: 112 tests (732 subtests) in 104 s.

**Earlier tests changed, and why** (each at least as strong as before; nothing skipped, deleted or weakened):

- `test_collective_pushdown.py::LeakageStageTests::test_both_packs_pass_with_every_pushdown_artifact_scanned` and
  `test_collective_followup.py::LeakageStageTests::test_both_packs_pass_with_every_follow_up_artifact_scanned`: the
  stages are `["edge", "pushdown", "followup", "run_files"]` and each also asserts that the `run_files` class scanned
  four items; every other assertion is unchanged;
- `test_collective_leakage.py::G0RunnerTests::test_a_leaky_stage_fails_the_run`: the stages are `["edge",
  "pushdown", "followup", "run_files", "leaky"]`, and it asserts the four `run_files` items;
- `test_collective_guards.py`: the lists (`STDLIB_ONLY_MODULES` 59 to 60, `DETERMINISTIC_MODULES` plus `runfiles.py`
  and the demo's `scenario.py`, `screen.py` and `lint_numbers.py`, two RUNBOOK placeholders), `generic_code_files()`
  now covering `demo/collective/*.py` (so the domain-literal test covers the demo, and asserts that it does),
  `ApprovalCallSiteTests` asserting that the checker sees the real call in `demo/collective/collective_demo.py` (the
  allow-list is unchanged), and the seven new tests above.

### S4: what the suites leave behind

After both suites `git status --porcelain` lists only the G8 files above, `runs/` holds only its `.gitignore`, and
there is no untracked `*.sqlite3`, `*.db` or `*.jsonl` outside the committed run: every G8 test records, serves,
exports and lints under a temporary directory, and every recording removes its work directory. The only ignored files
the suite rewrites are the fabric strategic demo's `demo/results/mycelic-strategic/` outputs, which the base suite
writes too.

### Pack hashes (unchanged from G7)

| Pack | config | vocabulary | detector | fixtures |
|---|---|---|---|---|
| `device_quality` | 285935198ba34f2e194dc175cffd1f8f62c494d03d8fb7400ca1aef709058f2a | e46f521154ce94f42136319ade62cebb5c65515ab90a057470495a606f225901 | c9462f62aa90245f2c7cee50078d337554bded58c4630cda7becbf7a8048c7ec | dc4b70b7044b1094baaa669fb5a3582eeae91af290213e13b94180791db5656d |
| `claims_integrity` | a3a042943452f6ef781f171cf879f3ba5f594f6c4dae5ffef47bfa241bb392da | 028b7603f2b6ef3bba203dee299ea8b001880ef89cd4b276de8513d2a1a301f3 | 2041fe9b3e141a5603836d893c97d5671eb21cbb3da611969efbd0c2c4514b84 | a2e8936b4be26683f0860c8c8799e701f9aedfb78ea167d5420ac891111b8d80 |

### Merge notes

1. **No fabric integration is needed for G8 to run.** The demo runs every plant in one process, reads and writes
   plain files under a temporary work directory, and imports nothing from the fabric; `mycelic/` never imports
   `demo/`.
2. **A later console could read the fabric.** The console reads `screen.json`, which is built from run files. Once
   G6's conclusions and G7's follow-ups travel as fabric events (G6 and G7 merge notes: `followup.proposed`,
   `followup.approved`, `followup.result`, the conclusion events and the signed ledger head), a console could build
   the same items from those events instead; the item contract (`src` pointing at a primary document, `display` from
   `screen.format_value`) and the lint carry over unchanged if the events are first written as run files.
3. **`demo/README.md` could link `demo/collective/`.** It is left byte-identical here (scope); one line in its index
   would do.
4. **The committed run's code stamp names the base commit.** `scorecard.code` says commit 3a4c786 with `dirty: yes`,
   because G8 is uncommitted when it records. `code` is excluded from the content hash, so the run stays current after
   the commit; re-record after committing only if a clean stamp is wanted (`demo/collective/README.md`).

### Amendments A1 to A12 (as implemented)

- **A1 caught vs related:** as specified, with case keys from `scenario.build_world` (the hero's entity, every
  structured entity of a hero record and every entity the lexical extractor finds in a hero narrative, times the hero
  predicate and every hero code's predicate, egress types only). The committed scenario has `caught: false` for S and
  R and a non-empty related list for both: R and S each flag the lot's and the product's generic-malfunction keys.
- **A2 digests:** every sha256 is 32 hex in a run file (`runfiles.shorten`, `digest`); the screen shows 12.
- **A3 one shared module:** `mycelic/collective/runfiles.py`, used by G0's `run_files` stage and by the demo.
- **A4 the demo's own item builder:** `scenario.py` builds the items over the pack generator's background world and
  reuses the generator, the pack's filler sentences, `connector.record_problems` and `edge.extract` for its checks.
- **A5 the demo's own pipeline:** its own `OrgConfig` for the cells; `evaluate.baselines` for R (model-free), U and
  each site alone. No `evaluate/` file changed.
- **A6 pushdown at the run's `as_of`:** the scorecard reports both the detection week and the `as_of`.
- **A7 decoys:** every decoy key X alerted is verified at `as_of`; a decoy X did not alert is `verified: false`,
  `reason: not_alerted`. Hero, sibling and decoy ids are fresh and added to master data.
- **A8 follow-up order and approval:** T0 proposed, approved, executed; then T1 (its draft written from structured
  inputs including the packet summaries), approved, executed. Live: approval by key from the console. Record:
  scripted approvals by the named owner, each execute requested twice (`execute_requests` 2, `executor_calls` 1).
- **A9 no silent fallback:** `list_models` preflight on every routed endpoint; a fallback extraction, an extraction
  error kind, a degraded judge or a judge failure stops the run with exit 2, naming the endpoint, and writes nothing
  (`ExportReplayTests::test_routing_unreachable_endpoint_fails_visibly`, `test_routing_schema_invalid_fails_visibly`).
- **A10 content hash:** excludes `/code`, `/content_hash`, `/created_at`, `/run_id` and `/timings`; two fresh
  recordings (`PYTHONHASHSEED` 0 and 4242, different work and output directories and run ids) reproduce the committed
  hash (`DeterminismTests`).
- **A11 two scans:** `after_pushdown` and `final`, both in `leakage.json`; the check beat shows the first.
- **A12 lint strengthening:** number words, the console markup rule (ordered lists, progress and meter, CSS counters,
  decimal list styles) and the digit rule over screen text parts, as specified.

### Further decisions and deviations, for the reviewer

- **The hero may be `narrative_only` or `both`** (the brief allows only `narrative_only`). `HonestyTests` must record
  a scenario copy whose hero has a specific code and the structured lot always filled, which the brief's validation
  would refuse. The committed scenario is `narrative_only`, and `HonestyTests::test_committed_scenario_shape` pins it.
- **An alert event carries channel, key, week and rank only.** The brief's trace list also has a `hero` flag, but its
  test allows only rank, caught, related, key, week and channel in a baseline's alert event; the flag added nothing a
  reader cannot get by comparing the key with `scorecard.hero.key.key`, so it was dropped.
- **`--run-id` defaults to the run directory's name** when that name is a valid run id (`--record DIR` or `--serve
  --out DIR`), else `collective-` plus random hex as the brief says. `--record demo/collective/recorded/<run-id>`
  (the brief's re-recording command) then gives a directory and a run id that agree, which
  `CommittedRunTests::test_exactly_one_committed_run_with_six_small_files` checks. An explicit `--run-id` wins.
- **`followup` when nothing was proposed** is the closed object `{proposed: false, reason, label: null, ...}` with
  every other field null (the brief says "followup = null" when X did not alert, and `{proposed: false, reason}`
  otherwise); one shape keeps `validate_run` closed. The reasons are `not_alerted`, `not_yet_checked` (live, before
  the check), `awaiting_followup` and `conclusion_not_supported`. `pushdown` is null when X did not alert.
- **The final self-check scans all six files**, not only `screen.json` and `leakage.json`, and also re-validates
  every document against its schema.
- **The live server shows every failure.** Besides a `DemoError` (an endpoint, a fallback), any unexpected exception
  in the engine becomes an error event naming only its class, the phase goes `failed`, the console stays up and
  nothing is written. A probe found the case: the live follow-up screen tried to format the ledger head before the
  follow-ups had finished; the screen now leaves out the ledger line, and an owner, role or acknowledgement date, until
  they exist.
- **The inference runtime's per-attempt warnings are silenced in the demo** (a `NullHandler` on the inference
  logger). Every attempt is still a ledger row, and a run that needed a fallback stops with one line naming the
  endpoint and the error kinds instead of hundreds of warnings.
- **The lint also checks beat titles and control labels**, and the `any field(s)` denylist entry matches "any field"
  and "any fields".
- **Decoys in the committed scenario.** The generic-code rise (three new product models) does alert X here (the
  probe's did not), and the gate keeps it a hypothesis; the echo flood is not alerted by X (`verified: false`); the
  single-reporter burst on a fresh lot at two plants is alerted and kept a hypothesis by the reporters bound. So
  `no_decoy_supported` is not vacuous.

### Mutation probes run

Each mutation was applied alone to the worktree, the named test run, and the file restored
(a scratch script outside the repository); every one is killed.

| # | Mutation | Killed by |
|---|---|---|
| m1 | `format_value` drops the thousands separator for `int` | `LintTests::test_format_value_and_parse_display` |
| m2 | the screen says R "also caught this case" whatever the flag | `HonestyTests::test_screen_sentences_follow_flags` |
| m3 | the by-construction caption shown whatever the flag | the same test |
| m4 | the lint never reports `display_mismatch` | `LintTests::test_each_injection_fails_naming_the_token` |
| m5 | the lint stops decoding escapes in script literals | the same test (`t\u0068ree`, `\u006fl`; it survived the first version of the test, whose only escape case decoded to a digit that the raw escape also shows, so the two cases were added) |
| m6 | `shorten` keeps a 64-hex run inside a longer string | `RunFilesTests::test_shorten_maps_64_hex_to_32_and_refuses_an_embedded_one` |
| m7 | `write_run_files` ignores portability problems | `RunFilesTests::test_write_run_files_writes_nothing_when_any_file_has_a_problem` |
| m8 | `portability_problems` drops the forbidden-string rule | `RunFilesTests::test_portability_problems_flags_each_rule` |
| m9 | record mode requests each execution once | `RecordTests::test_followup_block` |
| m10 | an alert event carries a score | `RecordTests::test_no_baseline_counts_or_features` (the event schema refuses it, so the recording fails) |

### The committed scenario and its outcome (synthetic, same-author, not a measurement)

Quoted only as **synthetic, same-author, not a measurement**: the world and the detectors have one author, and the
stand-in model reads the pack's own sentences perfectly.

- **Settings.** `device_quality`, seed 7, 40 weeks, tie salt `collective-demo`; six plants in four countries (GB, IE,
  DE, US; one display name is non-ASCII, one is longer than 40 characters), 1,116 records. Hero:
  `lot:L10099:detachment`, narrative-only, at plant-ashvale and plant-corrowfield (English) and werk-dornhagen (German), one record per plant
  per week for 6 weeks from week index 33 (18 records), code ILL-9001 (a generic malfunction code), structured product
  SD-9 at fill 1.0 (18 of 18 records) and lot L10099 at fill 0.6 (11 of 18). Sibling: plant-fennick holds L10099 with
  a quarantine sentence and code ILL-9002. Decoys: a generic-code rise on three new product models at five plants, an
  echo flood (marked copies from one plant to three), a single-reporter burst on a fresh lot at two plants. Follow-ups:
  `evidence_packet`, then `capa_initiation_draft` (severity medium); approvers at unit `halvern`.
- **Detection.** X alerted the hero key at rank 2 in 2024-W35, its first week (the lot's generic-malfunction key was
  rank 1 that week); no rule names it. S and R did not alert the hero key (`caught: false`, by construction: the
  predicate is never coded); each flagged related keys: S `lot:L10099:malfunction_unspecified` (rank 1, 2024-W36) and
  `product:SD-9:malfunction_unspecified` (rank 1, 2024-W39); R `product:SD-9:malfunction_unspecified` (rank 1,
  2024-W35) and `lot:L10099:malfunction_unspecified` (rank 1, 2024-W36). The references: U rank 2, each site alone
  rank 4.
- **Check.** At `as_of` 2024-10-28 over 2024-W28 to 2024-W41: the three contributing plants confirm (support, roots
  and reporters `3-9` each), plant-fennick refutes (its entity bucket `3-9`), plant-brindlemoor (sibling) answers
  unknown; the gate says supported ("3 confirming sites; independent roots, lower bound 9; independent reporters,
  lower bound 9"), and every counted confirm resolves at its own plant.
- **Follow-up.** The evidence packet is complete with one ok packet per contributing plant; the CAPA draft for
  `qe-owner.halvern` (Quality engineer) is approved and executed; each ran once though requested twice; 11 ledger
  entries; the outcome is "not yet checked".
- **Leakage.** Both scans: no canary hit and no narrative overlap; the positive control finds canaries and text.
- **Time of one `--record`** (fake mode, this sandbox, shared machine): 9.7 s for the committed run; 9.6 to 11.3 s for
  the other recordings in this session, and 12.8 s against a local fake server with `--routing`. The bound is 300 s.

## Review fixes (sixteen confirmed findings)

**Base.** Branch `mycelic-collective-phase2` at 2d2f54a (G8), a clean worktree. Nothing is committed by the engineer.

**Scope: fabric files changed: none.** `git diff --stat 2d2f54a -- mycelic/service.py mycelic/store.py
mycelic/aggregation.py mycelic/transport.py mycelic/api.py mycelic/lineage.py mycelic/config.py deploy SECURITY.md
DEPLOYMENT.md NeuralGraph research` is empty. Changed: `mycelic/collective/{stats,runfiles}.py`,
`detect/detectors.py`, `evaluate/harness.py`, `edge/{verify,records}.py`, `pushdown/orchestrator.py`,
`inference/{tasks,runtime,fakeserver}.py`, `experiments/{openfda_replay,e1_extract,e2_pushdown,g0_canary}.py`,
`demo/collective/{collective_demo,screen}.py`, `console.html`, `SCRIPT.md`, `README.md`, the committed run (re-recorded
as `demo/collective/recorded/collective-halvern-g9/`, the G8 directory removed, as the README's re-recording steps
say), `docs/collective/{ARCHITECTURE,RUNBOOK,LEAKAGE,INTEGRATION}.md` and nine test files. No new SQLite schema. Pack
data and the four hashes per pack are unchanged.

| # | Finding | Fix | Regression test |
|---|---|---|---|
| 1 | The openFDA replay credits recalls by alert volume | Every channel reports `alerts`, `alerts_per_week`, `found_minus_expected` and a circular-shift null (`chance`: expected found, per-recall share, p-value, median lead); ARCHITECTURE 14.7, RUNBOOK 12 | `OpenFDAReplayTests::test_every_channel_reports_its_alerts_and_a_circular_shift_null`, `test_the_circular_shift_null_by_hand`, `test_alert_volume_alone_is_not_credited_above_chance` |
| 2 | X1 credits "found" without the plant; single_site at any site | A no-plant control world per seed through the same pipeline; `control_found`, `found_net`, `recall_net` everywhere, lifts on net found; single_site counts only at a planted site | `test_single_site_matches_the_key_only_at_a_planted_site`, `test_a_find_the_control_makes_as_early_or_earlier_is_a_chance_find_and_not_net`, `test_the_no_plant_control_runs_per_seed_and_only_its_earlier_finds_are_not_net` (renamed in audit round 2, which made the rule timing-aware) |
| 3 | D3 PMI cannot fire on k-suppressed cells | The key's own count keeps its conservative bounds; the nuisance cells take one shared imputation (`'<k'` as k/2) in window and baseline | `test_d3_fires_on_all_suppressed_cells_where_opposite_marginal_bounds_could_not`, `test_a_steady_suppressed_key_beside_steady_suppressed_cells_does_not_rise` |
| 4 | X detects later than S on code-visible patterns | X runs S's codes test beside the combined test (Bonferroni over two, a certain-rate floor), shown as `test` in the snapshot | `test_text_only_background_does_not_hide_a_codes_burst_that_s_sees` |
| 5 | The E2 bar passes an uninformative verifier | Chance AP, the chance-corrected ratio and central_raw's lift over chance; the bar needs the corrected ratio and withholds an uninformative pool; key-cluster bootstrap | `test_a_constant_numerator_keeps_none_of_the_lift_over_chance`, `test_the_lift_ratio_by_hand_and_its_epsilon`, `test_cluster_replicates_draw_whole_labels`, `test_the_bar_needs_the_chance_corrected_ratio_and_an_informative_pool` |
| 6 | high_base_rate set by a pattern's own co-mentioned keys | A3 counts other series of the same predicate and entity type | `test_a_co_mentioned_key_of_another_type_does_not_flag_a_genuine_burst` (and the renamed `test_high_base_rate_is_type_wide_and_never_flags_itself`) |
| 7 | The run-file ledger copies per-call site rows (sub-k counts) | `project_ledger` refuses a site row; a site's usage is `site_usage` from the crossed summaries | `test_a_site_ledger_row_never_enters_a_run_file`, `test_crossed_usage_keeps_the_sites_suppression`, `RecordTests::test_ledger_capped_and_clean` |
| 8 | The import guard is bypassed by re-exports from `NeuralGraph.chat_memory` | Names resolved to their defining module; fresh-interpreter globals and importer-chain checks | the re-export snippets of `POSITIVE_IMPORTS` (`test_checker_flags_every_positive_snippet`) and `test_checker_flags_an_injected_import_in_a_copy_of_a_core_file` (now `lineage` and `service`), `test_no_core_global_is_defined_in_a_model_client_module`, `test_the_known_model_client_load_follows_exactly_its_documented_chain` |
| 9 | E2 aborts on the first central InferenceError | Recorded per item by kind; the run completes; the bar is withheld | `test_a_failing_central_judge_is_recorded_and_the_run_completes` |
| 10 | A degraded verdict is cached and re-sent forever | Sent, not stored (question_log `degraded`, no budget); the next ask judges again | `test_a_judge_outage_is_not_pinned_the_next_ask_re_judges` |
| 11 | E1 scores transport failures as model errors | Transport failures unscored, out of the validity denominators, counted by kind; a verdict withheld above 1% | `test_a_flaky_server_is_not_scored_as_model_errors_and_withholds_the_verdict` |
| 12 | E2 judges despite timeouts; late verdicts never collected | `join_late` then `collect_late` at the item's `as_of`; unanswered share above 5% withholds the bar | `test_late_pushdown_answers_are_collected_and_scored`, `test_failures_context_and_unanswered_routes_withhold_the_bar` |
| 13 | The repair turn sends [system, user, user] | The repair is appended to the one user turn; the fake server refuses non-alternating roles with 400 | `test_the_fake_server_refuses_roles_that_do_not_alternate_like_chat_templates` and the updated repair test |
| 14 | No context-length check on central_raw prompts | `--central-context-tokens` required with `--central-routing`; prompts at the context (or unreported) withhold the bar | `test_central_routing_needs_the_central_context`, `test_central_prompts_at_the_declared_context_are_counted`, `test_prompt_tokens_per_central_raw_ref` |
| 15 | S/R rows say "not alerted" while ranking the lot first; "nobody connects them" | A failure-mode caption; S and R rows name their first other case key with rank and week; the SCRIPT line dropped | `_HeroAssertions.assert_detection` (record and committed run) |
| 16 | Replay/export of a `--serve` run shows LIVE | `screen.presentation`; replay and export present `recorded`; the badge reads only it | `ExportReplayTests::test_serve_controls_are_idempotent` (exports and replays the live run), `HonestyTests::test_labels_present` |

### Earlier tests whose expectation encoded a finding

Changed, never weakened: each now asserts the fixed behaviour.

- `test_collective_evaluate.py`: `test_single_site_matches_the_key_at_any_site` became
  `..._only_at_a_planted_site` (finding 2); the construction smokes read `work/seed-N/planted`; `channel_block` takes
  the control events; the minimum-rate rows gained `rate_at_k_text_background`.
- `test_collective_detect.py`: the hand-bounds test uses the new D3 formulas and the snapshot's `test` field;
  `test_high_base_rate_is_predicate_wide_and_never_flags_itself` became `..._type_wide_...` (finding 6).
- `test_collective_inference.py`: the repair test asserts `[system, user]` on both requests (finding 13).
- `test_collective_pushdown.py`: the degrade test asserts nothing is stored and `audit` is None (finding 10).
  `DETECT_SHA256` re-pins `detectors.py` (`9d23000e...`; G5 to G8 `7d4ca86b...`; `rules.py` and `org.py` unchanged),
  and `test_detection_is_byte_identical_to_g5` is renamed `..._to_its_pin`: it is a tripwire for unintended changes
  to detection, and this one is intended (findings 3, 4 and 6).
- `test_collective_e1.py`, `test_collective_e2.py`: the smokes assert the new fields.
- `test_collective_demo.py`: the `mode` format reads "scripted run" / "run driven live in the console" (the badge
  text moved to `presentation`); the ledger test asserts HQ-only rows (`rows_written <= rows_total`, since HQ's draft
  ledger has fewer rows than the cap).

### Merge notes

1. **For the fabric (not changed here, out of scope): `mycelic/retrieval.py` loads a model-client module.** Its
   `from NeuralGraph.chat_memory.textutil import tokenize` runs `NeuralGraph/chat_memory/__init__`, which imports
   `.llm` (and numpy and prometheus_client). Nothing calls a model, and the guard now pins that exact chain, but
   the core would load no model-client module at all if `tokenize` lived outside the package `__init__`'s reach
   (for example a module of `mycelic/` or a `chat_memory` package `__init__` that imports `.llm` lazily). Once
   fixed, drop the `KNOWN_CHAIN` exemption in `test_collective_guards.py`.
2. **E2 needs a more informative pool than the shipped smoke.** On `plant_e2_smoke.json` 247 of 300 candidates are
   true (chance AP about 0.83), so a run on it is withheld as uninformative unless the judges separate far better
   than chance. A real E2 needs a plant spec with many more decoys and background candidates (ARCHITECTURE 15.7).
3. **A site answers one question at a time.** The verifier's lock queues a question behind a late one, and that wait
   counts against the new question's deadline (orchestrator docstring, ARCHITECTURE 15.3). E2 now waits for late
   answers itself; a deployment that sends many questions to a slow site should size `deadline_seconds` for it.

### Disagreements and residuals, for the reviewer

- **Finding 4 is mitigated, not closed.** A per-channel test at `lambda_floor` without a floor roughly tripled X's
  false alarms in an engineering probe on synthetic plant worlds, because records that move between channels look
  like a fresh series; the shipped fix is two tests at `alpha_site / 2` with a certain-rate floor. X can still be one
  week behind S at S's threshold (the regression test pins W36 for S and W37 for X), and with steady text-only
  background X needs a higher codes rate than S (`rate_at_k_text_background`). Full parity means running S's test at
  full alpha inside X; that trades false alarms and is the owner's call.
- **Finding 3 as worded ("can never fire") was stronger than the code.** On all-`'<k'` cells the opposite-bound
  marginals cost every key a large negative rise before any change in the data (`-4 log(k - 1)` before smoothing for a
  key alone in its type), so D3 could fire there only on a change large enough to overcome it; the regression test pins
  a case it could not reach. The fix also gives up certainty over the nuisance cells (ARCHITECTURE 13.4).
- **Finding 6's same-type case stays.** Two co-mentioned entities of the same type (two lots in one narrative) still
  count as each other's base rate.
- **Finding 12's queueing is left as documented behaviour** (merge note 3), not changed: E2 waits for the late answer
  instead of letting the next question time out behind it.

### Synthetic figures (synthetic, same-author, not a measurement)

Printed in this sandbox, quoted only as **synthetic, same-author, not a measurement**:

- The detection end-to-end test (seed 4, 40 weeks, six sites): `device_quality` X 3,155 cells, 27 candidates (12 from
  the detectors), 12 alerts, 18 rule hits (G8: 26, 10, 10, 18); S and `claims_integrity` unchanged (ARCHITECTURE 13.9).
- The X1 construction smokes: as quoted at the end of the G5 section above, with the control columns.
- G0 with the run-files stage (seed 11, 1,000 records, both packs): exit 0, no hit, no overlap; HQ's ledger rows 4
  and 1, six crossed usage groups each (LEAKAGE section 11).
- An E2 rehearsal on `plant_e2_smoke.json` (fakes everywhere, tie salt `r9-rehearsal`): 300 candidates, 106 keys,
  2,573,511 raw-text bytes for central_raw and 0 for the others, 276 of 276 supported conclusions resolvable, 0
  unanswered routes, a withheld verdict; the pool is uninformative (merge note 2).
- The re-recorded demo run: checks 12 of 12, hero X rank 2 in 2024-W35, gate supported; S's first other case key is
  the lot's generic-malfunction key (rank 1, 2024-W36) and R's the product's (rank 1, 2024-W35), as in G8;
  `ledger.jsonl` holds one HQ row; the final scan's run files are 49,452 bytes.

## Audit round 2 (ten confirmed findings)

**Base.** Branch `mycelic-collective-phase2` at 1fe6cff (audit round 1), a clean worktree. Nothing is committed by the
engineer.

**Scope: fabric files changed: none.** `git diff --stat HEAD -- mycelic/service.py mycelic/store.py
mycelic/aggregation.py mycelic/transport.py mycelic/api.py mycelic/lineage.py mycelic/config.py deploy SECURITY.md
DEPLOYMENT.md NeuralGraph research` is empty. Changed: `mycelic/collective/leakage.py`, `edge/{egress,extract,
records,site,verify}.py`, `evaluate/harness.py`, `experiments/{e1_extract,e2_pushdown,e3_latency}.py`,
`inference/{client,fakeserver,routing,runtime}.py`, `packs/{connector,loader}.py`, both packs' `egress.json`,
`demo/collective/{collective_demo,screen}.py`, `console.html`, `SCRIPT.md`, `README.md`, the committed run
(re-recorded as `demo/collective/recorded/collective-halvern-g10/`, the g9 directory removed),
`docs/collective/{ARCHITECTURE,INTEGRATION,LEAKAGE,PACKS,RUNBOOK}.md`, `docs/collective/examples/{README.md,
routing.example.json,e1.prereg.example.json,e1_models.example.md}` and nine test files. No new SQLite schema.

| # | Finding | Fix | Regression test |
|---|---|---|---|
| 1 | X1's net found voided single_site's plant-driven finds when the control alerted weeks later, so `X_minus_single_site` came out positive although single_site found more patterns, earlier | A find is a chance find only when the control's first in-window alert (single_site: at a planted site) is in the same week as the planted world's first or earlier (`harness.chance_find`); `chance_found` beside `control_found` in every block, `chance_find` per pattern outcome; `NET_BASIS` says so; ARCHITECTURE 14.4, RUNBOOK 11; the G5 figures above corrected | `MetricTests::test_a_control_alert_only_after_the_planted_find_does_not_void_it`, `test_a_find_the_control_makes_as_early_or_earlier_is_a_chance_find_and_not_net`, `PlantedConstructionTests::test_the_no_plant_control_runs_per_seed_and_only_its_earlier_finds_are_not_net` |
| 2 | Real narratives could reach off-site endpoints under the synthetic exemptions: the judge never checked, extraction checked only `simulation` | `simulation=True` needs `data_label='synthetic'`; `Runtime.exemption(task)` names the label records must carry when the route (primary or escalation) leaves the boundary as `simulated` or `external_raw_exempt`; `records.exempt_records_problem` checks it (synthetic: every record; public: the site is `public`); extraction and the judge refuse before any call; `WindowRecord` carries `synthetic`; E2's central_raw refuses a record that is not synthetic; ARCHITECTURE 2 | `BoundaryGuardTests::test_exemption_names_the_label_records_must_carry`, `BoundaryGuardTests::test_construction_rules` (simulated partner or public data), `UsageTests::test_an_exempt_external_extraction_only_reads_records_that_carry_the_label`, `VerifyTests::test_an_exempt_judge_only_reads_records_that_carry_the_label`, `VerifyTests::test_e2_central_raw_never_sends_a_record_that_is_not_synthetic` |
| 3 | `usage_summary` sent the judge's calls (one per retrieved record, so the counts behind a verdict), and `calls - ok` recovered `'<k'` error counts | A site summarises only its extraction rows (the judge's are consumed, never summarised) and its Boundary lists only the extraction task; complementary suppression: with an int `calls` and any part below k, `ok` and every error kind go as `'suppressed'`; the Boundary refuses a `'<k'` part beside an int `calls` and a partial `'suppressed'`; LEAKAGE 2 and 7 | `UsageTests::test_judge_rows_stay_at_the_site`, `test_no_part_below_k_can_be_recovered_by_subtraction`, `test_the_boundary_refuses_a_subtractable_or_judge_usage_group` |
| 4 | Pushdown was a membership oracle on guessed ids, person data included; `require_master_data` and the person-value drop did not apply; the per-entity budget did not limit guessing | With `require_master_data`, an id outside master data gets `unknown` without a record read (stored, local reason `not_master_data`, body identical to no records; `site.in_master_data` shared with the cells); retrieval skips a narrative mention written as a person value or the reporter (`extract.person_values`); a per-site budget on distinct entities a day (`question_entities_per_site_per_day`, 50 in both packs); LEAKAGE 7 and 9, ARCHITECTURE 15.4 | `VerifyTests::test_an_id_outside_master_data_is_answered_without_reading_a_record`, `test_a_mention_written_as_a_person_value_does_not_retrieve_the_record`, `test_the_daily_entity_budget_per_site` |
| 5 | The streaming client ignored the `reasoning` field of Ollama and vLLM, so E3's TTFT waited for the end of the thinking and `decode_tok_s` came out about 20 times too high | A delta with `reasoning` or `reasoning_content` starts the clock and counts in `reasoning_chunks`; E3 cells report `thinking_requests` and `length_cut`; the fake server streams thinking under either name; RUNBOOK 4, ARCHITECTURE 3 | `FakeServerRuntimeTests::test_thinking_deltas_start_the_clock_under_either_field_name`, `E3SmokeTests::test_a_thinking_server_is_timed_from_its_first_thinking_token_and_flagged` |
| 6 | Server-default thinking could not be turned off and spent the small token budgets | Routing endpoints take `reasoning_effort` and `chat_template_kwargs` (checked, sent only when named); E1 pins both in its prereg; the example routing turns thinking off for Ollama and vLLM; RUNBOOK 2 ("Thinking") and 13 (Ollama's `num_ctx` cannot be set per request); fake persona `thinks-by-default` | `FakeServerRuntimeTests::test_thinking_controls_are_sent_only_when_the_routing_file_names_them`, `test_thinking_control_keys_are_checked`, `E3SmokeTests::test_thinking_that_spends_the_token_cap_is_counted`, `E1PreregTests::test_pinned_values_are_enforced_by_run_and_compare` (the two thinking fields) |
| 7 | No circuit breaker: during an outage every record paid the full deadline, the verifier kept judging after degraded was certain, all under its lock | The judge stops as soon as failures exceed half the records, and after `BREAKER_AFTER` (2) consecutive server-down failures (`timeout`, `network`, `http_5xx`) counts the rest as failures; extraction senses the rest of the pass lexically without a call (extractor `fallback`, error `not_sent`); ARCHITECTURE 12.2 and 15.4 | `VerifyTests::test_a_dead_judge_server_costs_a_question_two_calls_not_one_per_record`, `UsageTests::test_a_dead_model_server_costs_an_extraction_pass_two_calls` |
| 8 | The each-site-alone baseline flagged the hero key in X's week and the screen hid that week | Every channel's detection block has `detection_week`; the references row shows the single-site week and a warning when one plant alone was no later than X (or later); README, SCRIPT and ARCHITECTURE 17.4 say this case shows no collective lift | `HonestyTests::test_the_single_site_reference_shows_its_week_and_says_when_it_was_no_later` |
| 9 | The live 60-second cut was broken: Next after the check landed on the hidden follow-up beat, and real data stayed locked | The console always shows the beat the engine is on; `--serve --cut` walks only the cut's beats and runs the follow-ups with scripted approval after the check (`approval: recorded`), with `cut_only` in `screen.json`; RUNBOOK 15, ARCHITECTURE 17.7 | `ExportReplayTests::test_the_sixty_second_cut_can_be_given_live` |
| 10 | The demo was labelled for YC use although STRATEGY 9.1 (the YC demo's rules) puts synthetic-fixture results of any kind off the screen | Every label reads "Internal use only"; README, SCRIPT, RUNBOOK, ARCHITECTURE and the G8 header above cite 9.1 correctly and leave the decision to the founder | `HonestyTests::test_labels_present` (the screen and the export carry "Internal use only") |

### Earlier tests whose expectation encoded a finding

Changed, never weakened: each now asserts the fixed behaviour.

- `test_collective_evaluate.py`: `test_a_find_in_the_no_plant_control_is_a_chance_find_and_not_net` counted a W32
  find against a later W35 control alert as chance; renamed `test_a_find_the_control_makes_as_early_or_earlier_...`,
  it now plants same-week and earlier control alerts. `test_the_no_plant_control_runs_per_seed_and_its_finds_are_not_net`
  pinned single_site `(found, control, net) = (3, 2, 1)` and the lift `2/3`; renamed `..._only_its_earlier_finds_...`,
  it pins `(3, 2, 0, 3)` with `chance_found`, the lift 0 and that both control alerts come strictly later (finding 1).
- `test_collective_edge.py`: the usage test's group with `ok 5, errors {http_5xx: '<k'}` beside `calls 6` was the
  subtraction the finding names; it now expects `'suppressed'` (finding 3).
- `test_collective_pushdown.py`: the degrade tests scripted one 5xx per record; with the breaker two in a row
  degrade, and the half-failure case uses 4xx, which does not trip it (finding 7). The NOT_COVERED pin quoted the
  per-entity budget alone (finding 4).
- `test_collective_extract.py`: the persona count is 34 (finding 6's `thinks-by-default`).
- `test_collective_demo.py`: a detection block has `detection_week`; the baseline-numbers check accepts that week as a
  week; the audience label reads "Internal use only" (findings 8, 10).
- Pack pins (`test_collective_pushdown.py`, `test_collective_evaluate.py`, `test_collective_followup.py`): `config_hash`
  is `R2_CONFIG_HASHES` (`device_quality` `ac59c4cb...`, `claims_integrity` `12d62cdf...`); the vocabulary, detector
  and fixtures hashes are unchanged.

### Merge notes

1. **No fabric change and no fabric integration point.** Everything is under `mycelic/collective/`, `demo/collective/`,
   `docs/collective/` and `tests/mycelic/test_collective_*.py`.
2. **The committed demo run moved from `collective-halvern-g9` to `collective-halvern-g10`.** The g9 removal is staged
   and the g10 directory is staged as new (README's re-recording steps). A fresh `--record` reproduces its
   `content_hash` (`CommittedRunTests`).
3. **Pack `config_hash` changed** in both packs (`question_entities_per_site_per_day`). Any run file or prereg that
   pins the G7 `config_hash` must be re-made.

### Disagreements and residuals, for the reviewer

- **Finding 1: the timing rule, not an aggregate correction.** A control alert in the same week as the planted world's
  first alert still voids the find (conservative); a control alert only later does not. The rule cannot tell a
  plant-driven find from a chance find when the background alone would have alerted later anyway; it treats the
  earlier, plant-driven alert as the find, which is what the planted world shows.
- **Finding 2: the public exemption is tied to the site id `public`** (`connector.PUBLIC_SITE`, the site E1 and the
  replay give openFDA records), since records carry no public flag. No in-repo caller sent real data before the fix;
  this closes the API gap the reviewer described.
- **Finding 3: HQ no longer sees the judge's health in usage.** A degraded verdict still tells HQ that a site's judge
  is failing; per-kind judge errors stay in the site's ledger. Complementary suppression still sends which error kinds
  occurred (the keys).
- **Finding 4: without `require_master_data` (`claims_integrity`)** an id-shaped value that is not written in a person
  field is still an id to the site; the per-site budget (50 a day, a pack value chosen here) limits how many such ids
  one site answers about per day, and the owner may tune it. A not-master-data question uses budget like any answer.
- **Findings 5 and 6 are rehearsed against the fake server only.** No real server ran here; the field names and the
  thinking defaults come from the reviewers' reading of upstream Ollama and vLLM source. RUNBOOK 2 tells the founder to
  check what their server version accepts and how to confirm thinking is off (`finish_reason` `stop`, `thinking_requests`
  and `length_cut` 0).
- **Finding 7: the breaker is per pass, not remembered across passes.** A wedged judge server costs each question up to
  two deadlines (at the example routing's `deadline_s` 300, 600 s, which reaches the orchestrator's default 600 s
  deadline, so HQ may record a timeout); a degraded verdict is not stored, so a re-ask pays it again. A judge endpoint
  with a deadline under half the orchestrator's keeps a dead server's cost inside one question's deadline.
- **Finding 8: the screen is honest; the case is unchanged.** The finding's better fix, a hero case that no plant's own
  baseline flags, is a scenario decision for the owner: on the committed case the collective view is not earlier than
  one plant, and the screen, README and talk track now say so.
- **Finding 10 is the founder's decision.** STRATEGY is outside this worktree and was not edited; the docs say what 9.1
  says and label the run internal until the founder decides.

### Synthetic figures (synthetic, same-author, not a measurement)

Printed in this sandbox, quoted only as **synthetic, same-author, not a measurement**:

- The X1 construction smokes (as at the end of the G5 section above): `device_quality` single_site 3 of 3 (control 2,
  chance 0, net 3), X 3 of 3 (net 3); `claims_integrity` single_site 3 of 3 (control 1, chance 0, net 3), X 2 of 3
  (net 2). The collective lift on each is 0 or below, not the 2/3 round 1 reported for the device smoke.
- The reviewer's 20-seed device repro (seeds 11 to 30, `plant_smoke.json`, tie salt `r2probe`), re-run with the fix:
  single_site 60 of 60 (control 7, chance 0, net 60, median delay 0), X 57 of 60 (control 0, net 57, median delay 2);
  `X_minus_single_site` -0.05, interval [-0.05, -0.05] (round 1's rule gave +0.0667, [0.05, 0.10]).
- G0 (seed 11, 1,000 records, both packs): exit 0, no hit, no overlap; LEAKAGE section 11's table is unchanged (G0's
  usage summaries are emitted before any question, so they never held judge rows).
- The re-recorded demo run (`collective-halvern-g10`): checks 12 of 12, gate supported; hero key X rank 2 in 2024-W35,
  U rank 3 in 2024-W35, each site alone rank 4 in 2024-W35, S and R not alerted on it (each flags a related key at
  rank 1).

## Audit round 2, review follow-up (finding 3's missing-token counts)

The review of the round-2 fixes found finding 3 only partly fixed. Complementary suppression withheld `ok` and the
error kinds, but `tokens_in_missing` and `tokens_out_missing` still crossed as exact ints. They count the rows without
token counts, which are the transport failures, so HQ read them back: three extraction passes against a dead server
(two timeouts each before the breaker) and one pass with 2 replies sent `calls 8, ok 'suppressed', errors {timeout:
'suppressed'}, tokens_in_missing 6`, which is timeout 6 and ok 2, below k. Two further fields gave the same split
back and are fixed with it: the token sums (here over the 2 replies; divided by the known prompt size they count the
rows) and the latency percentiles (a failure's latency sits at the deadline, and the interpolated p95 moves with the
number of failures).

| Fix | Regression test |
|---|---|
| `site._usage_group` summarises one (task, endpoint) group from its ledger rows. With an int `calls` and a part below k it sends `ok`, every error kind and both missing-token counts as `'suppressed'`, with no token sum and no latency. With no part withheld, a missing-token count is exact only when within each part every row or none lacks the tokens (a sum of whole parts), else `'suppressed'` without its token sum. `egress._usage_problem` enforces the same rules at the Boundary, plus `calls` equal to the sum of the parts. LEAKAGE 2, ARCHITECTURE 4 and 12.6 | `UsageTests::test_failures_cannot_be_read_from_the_missing_token_counts` (the finder's probe, then the same week with 4 replies), `test_no_part_below_k_can_be_recovered_by_subtraction` (the finder's `calls 8, ok 2, timeout 6`, a reply without usage, and every mix of up to three parts with and without rows lacking tokens), `test_the_boundary_refuses_a_subtractable_or_judge_usage_group` (ten new refused groups, six accepted) |

Earlier test whose expectation encoded the finding:
`UsageTests::test_errors_are_suppressed_and_tokens_sent_when_calls_reach_k` asserted that `calls 6, ok 'suppressed',
errors {http_5xx: 'suppressed'}` crossed with `tokens_in_missing '<k'`, the token sums and the latency, which is the
leak. The same shape (one 5xx among the calls) is now a case of the subtraction test, with everything but `calls`
withheld, and the test, renamed `test_errors_are_counted_and_tokens_sent_when_calls_reach_k`, sends tokens for a split
with no part below k (`calls 8, ok 5, errors {json_invalid: 3}`).

Mutation checks, each restored: sending the missing-token counts exact beside a withheld split, dropping the
whole-part rule at the site, and, at the Boundary, letting the missing-token counts, a token sum or latency cross
beside a withheld split, dropping the whole-part check, a token sum beside a withheld missing-token count, and a
`calls` that is not the sum of its parts each fail at least one of the tests above.

Residuals: with no part withheld, HQ still reads exact token sums (over no call or at least k calls) and latency
percentiles over the week's calls (the NOT_COVERED item on usage is unchanged), and which error kinds occurred. The
committed demo run and G0 are unchanged: their sites' extraction makes no failed call, so every group was already
exact with no rows lacking tokens. Re-run here (seed 11, 1,000 records, both packs), G0 passes and HQ's receive logs
are byte-identical to round 2's.

## Audit round 3 (eight confirmed findings)

**Base.** Branch `mycelic-collective-phase2` at 83bef36 (audit round 2), a clean worktree. Nothing is committed by the
engineer.

**Scope: fabric files changed: none.** `git diff --stat HEAD -- mycelic/service.py mycelic/store.py
mycelic/aggregation.py mycelic/transport.py mycelic/api.py mycelic/lineage.py mycelic/config.py deploy SECURITY.md
DEPLOYMENT.md NeuralGraph research` is empty. Changed: `mycelic/collective/stats.py`, `edge/{extract,records,site,
verify}.py`, `evaluate/plant.py`, `experiments/{e1_extract,g0_canary,n1_narratives}.py`, `followup/drafts.py`,
`inference/{client,ledger,routing,runtime}.py`, both packs' `fixtures/plant_smoke.json`, `demo/collective/screen.py`,
`docs/collective/{ARCHITECTURE,INTEGRATION,LEAKAGE,RUNBOOK}.md`, `docs/collective/examples/README.md` and ten test
files. No new SQLite table or column: the site store gained queries only.

| # | Finding | Fix | Regression test |
|---|---|---|---|
| 1 | `stale_chain` decoys could not fail: `check_plant` (`7 * (eval_from - end) > stale_days`) put every legal claims chain, and the shipped device chain, outside every detection window of the watch span, so window arithmetic and burn-in, not G4's stale filter, kept them quiet; `same_site_duplicates` tests `min_sites` only | A stale chain must be stale at the first evaluation step whatever the run's as_of (`7 * (eval_from - end) + close_lag_days > stale_days`) and wholly inside that step's window (`start >= eval_from - window_weeks + 1`); both fixtures re-planted in that band (device weeks 19 to 21; claims weeks 19 and 20 on `TW-0042`, an id with no background, since every tow-operator series had background after week 21); the watch span drops the dead `max`; ARCHITECTURE 14.2 says which rule keeps each structural class quiet, and that same-site duplicates test `min_sites`, not root collapse; RUNBOOK 11 tells the planner | `PlantSpecTests::test_a_stale_chain_is_stale_at_the_first_evaluation_week_and_inside_its_window` (every placement in weeks 14 to 25, both packs), `test_every_refusal_names_its_path_and_a_fixed_problem` (three stale cases), `PlantedConstructionTests::test_only_the_stale_filter_keeps_the_stale_chain_out_of_its_watch_span` and `ClaimsIntegrityConstructionTests::...` (the same: a candidate while fresh, none in the watch span, and with `detectors._stale` disabled a candidate there) |
| 2 | E1's non-inferiority was read from the per-record mean field F1 difference, where every claim-free record both sides leave empty adds an exact 0, so a model more than the margin below the reference in field F1 could pass | `paired.<name>.field_f1` is the decision: the micro field F1 of each side over the shared records, their difference and a paired percentile bootstrap over records (`stats.paired_bootstrap_f1`); the per-record mean and the sign test move to `per_record_field_f1`, secondary; NOTES, RUNBOOK 8, ARCHITECTURE 11.4 | `PrimaryMetricVerdictTests::test_a_model_beyond_the_margin_in_field_f1_is_not_non_inferior_however_many_records_are_claim_free` (the finder's 600-record scenario, measured runs written directly; 50% and 0% claim-free), `test_claim_free_records_leave_the_decision_unchanged`, `F1Tests::test_paired_bootstrap_f1_*` (stats) |
| 3 | N1's headline share pooled an equal-per-code sample unweighted, so low-volume codes dominated the go/no-go verdict | `sample` records each code's `strata` (openFDA total, fetched, eligible; `--n` at least the number of codes); `score` reports the event-level share `sum W_h k_h / n_h` with `W_h` the code's eligible volume (`total * eligible / fetched`, extrapolated when truncated), its interval (`stats.stratified_share`: Wilson at Kish's effective n, finite-population corrected, capped at n), the verdict from that share in exact fractions, and the pooled share as `unweighted_sample`; a sample.json without strata is refused (re-sample: same sheet); RUNBOOK 6 | `N1Tests::test_the_headline_share_is_event_level_not_an_equal_weight_mix_of_codes` (the finder's 9000/300 case), `test_weights_follow_volume_end_to_end_and_a_truncated_code_is_extrapolated` (a flip from supports to ambiguous through the CLI; a cache cut at 60 records), `test_a_sample_without_strata_and_too_small_an_n_are_refused`, `StratifiedShareTests` |
| 4 | The T1 draft scope scan skipped every alias mention, so a draft or an edit naming an out-of-scope product, supplier, repair shop, clinic or tow operator by name passed while the id was refused | Alias mentions of a type with an id format are checked like the id (their aliases are proper names); alias-only types' ordinary words still pass (D8); ARCHITECTURE D8, LEAKAGE 10 | `PacketDraftTests::test_a_name_of_an_out_of_scope_product_or_business_is_refused_like_its_id` (scan cases in both packs, a model draft refused `out_of_scope_id`, a human edit refused `draft_out_of_scope`, an in-scope alias accepted) |
| 5 | The extraction breaker turned a 3-second outage, two records slower than the deadline, or a dead escalation server into a permanent lexical downgrade of the whole backlog | Only the route's primary endpoint being down counts (`extract.server_down`; the verifier too); while open the breaker sends one record in the 1st, 3rd, 7th, ... batch after it opened, and an answer closes it; a `not_sent` stand-in counts as extracted but every later model pass sends the record again and replaces it until its count week is emitted (`RecordStore.unextracted` / `save_extractions` / `pending_non_synthetic` with a redo kind; the exemption check counts those records too); `ExtractSummary.resent`; ARCHITECTURE 12.2 and 15.4 | `UsageTests::test_a_short_outage_does_not_downgrade_the_rest_of_the_backlog`, `test_a_dead_server_is_probed_on_a_doubling_schedule`, `test_a_down_escalation_server_does_not_trip_the_breaker`, `test_a_not_sent_record_is_final_once_its_week_is_emitted`, `VerifyTests::test_a_down_escalation_server_is_not_the_judge_server_being_down` |
| 6 | G0 `--mode routing` reported PASS through a real server when no model call succeeded | `leakage.json` gains `model_path` (extraction records by extractor and error kind, judge attempts, answered and failed by kind, degraded verdicts) and `problems`; with a model, any problem fails the run (exit 1, one `g0: model path: ...` line each); a reply that failed validation is an answer, not a problem; RUNBOOK 9, LEAKAGE 5 and 6 | `G0RunnerTests::test_routing_mode_fails_when_the_models_did_not_answer` (a closed port, and a server whose every reply fails validation; the fake and lexical runs' `model_path`) |
| 7 | Site-boundary model calls went through the environment's `http_proxy` when the server was addressed by a host name, while the ledger said `boundary_mode: own` | An endpoint uses the environment's proxy only when `Endpoint.uses_env_proxy`: routing key `env_proxy` (boolean, `openai_compat` only), default true only for `external`; `client.endpoint_proxy` decides per endpoint; the ledger row gains `proxy` (28 keys); RUNBOOK 3, ARCHITECTURE 4, examples/README | `ClientTransportTests::test_an_endpoint_inside_a_boundary_never_uses_the_environment_proxy` (a site endpoint dials its own host with the proxy set, the ledger says `proxy: false`; opt-in and opt-out; refused values), `test_public_host_goes_through_the_proxy_in_absolute_form` (now an external endpoint, `proxy: true`) |
| 8 | Live, the alert beat read "X alerted: yes · not checked with the sites: X did not alert it" for decoys X alerted | An alerted decoy not yet verified reads "not checked with the sites yet: the check comes next" (`screen.DECOY_NOT_CHECKED_YET`); the not-alerted wording is unchanged; the committed recorded screen is unchanged | `HonestyTests::test_an_alerted_decoy_before_the_check_is_not_said_to_be_unalerted`, `ExportReplayTests::test_the_sixty_second_cut_can_be_given_live` (the live alert beat's decoy lines) |

### Earlier tests whose expectation encoded a finding

Changed, never weakened: each now asserts the fixed behaviour.

- `test_collective_evaluate.py`: the refusal "stale chain not stale" planted `start 14, weeks 8` and expected `not
  stale before the evaluation weeks`; the rule and its two messages changed (finding 1), so the table now has three
  stale cases, one of them the old fixture's placement. The labels test pinned the old chain's watch span (W26 to
  W30); it pins the new one (W26 to W32). `test_the_stale_chain_has_no_candidate_in_the_evaluation_weeks`, whose
  comment claimed "proof the filter, not absence, works" and which passed with the filter disabled, is replaced by
  `test_only_the_stale_filter_keeps_the_stale_chain_out_of_its_watch_span`, which fails with it disabled.
- `test_collective_e1.py`: the smoke test pinned the paired entry's keys (`mean_diff`, `ci95`, `sign`, `sign_p` at
  the top) and the transport test `mean_diff == 0.0`; both now read `field_f1` and `per_record_field_f1` (finding 2).
- `test_collective_week1.py`: `test_score_bars_and_wilson_interval` asserted the pooled share `k / n` with a plain
  Wilson interval; renamed `test_score_bars_and_the_stratified_interval`, it asserts the volume-weighted share, the
  stratified interval and the pooled share beside it. `test_verdict_bars_at_300` calls `verdict` with an exact share
  and gives its hand-built sample `strata` (finding 3).
- `test_collective_edge.py`: `test_a_dead_model_server_costs_an_extraction_pass_two_calls` asserted that the next
  pass sends only the newly ingested record; it now sends the 8 `not_sent` records as well (finding 5).
- `test_collective_inference.py`: the two proxy-mechanics tests used a `site:a` endpoint, which no longer uses the
  environment's proxy; they use an `external` one. `LEDGER_KEYS` has 28 keys, not 27 (finding 7).

### Merge notes

1. **No fabric change and no fabric integration point.** Everything is under `mycelic/collective/`, `demo/collective/`,
   `docs/collective/` and `tests/mycelic/test_collective_*.py`.
2. **Pack hashes are unchanged**: the plant fixtures are in no hash scope. The X1 and E1 code hashes change (`stats.py`,
   `edge/`, `evaluate/`, `experiments/e1_extract.py`, `inference/`), so an X1 or E1 prereg made before this round is
   refused by `run` and `compare` and must be re-made.
3. **Ledger rows have 28 keys** (`proxy`). `read_ledger` refuses a ledger written before; E1 run directories from an
   earlier round must be re-run (their prereg is refused anyway). Run files project the ledger to their own keys, so
   the committed demo run is unchanged and `CommittedRunTests` reproduce it.
4. **`e1.json`'s `paired.<name>` changed shape** (`field_f1`, `per_record_field_f1`) and **`narrative_gain.json`'s
   `share`, `ci95` and `verdict` are now the event-level estimate** (`unweighted_sample` holds the pooled one). Any
   consumer elsewhere (another team's tooling reading these files) must read the new keys.
5. **A routing file's endpoint may carry `env_proxy`**; files without it behave as before for `external` endpoints
   and connect directly for every other boundary, which is the fix.

### Disagreements and residuals, for the reviewer

- **Finding 1: the stale-chain band is narrow, and placement is all `check_plant` can guarantee.** Whether the chain is
  a candidate while fresh, and would be one without the filter, depends on the world (background of the key); the
  construction tests check it for the shipped fixtures, for one seed each. (The review of this round found the
  alert-level outcome still could not fail through the filter; see the follow-up below.) The one-site decoy classes
  still count toward X1's 20-decoy eligibility; whether they should is the planner's call (RUNBOOK 11 advises spreading
  the decoys over the classes). A duplicate decoy at two or more sites would alert, because D2 and D3 count records, not
  roots; testing root collapse in detection would need a detector change, which this round did not make.
- **Finding 2: the 600-record `underpowered` bar** was sized from a per-record SD (STRATEGY item 10); it is unchanged
  and is now a bar on the number of paired records for the micro-F1 bootstrap.
- **Finding 3: the weights assume the fetched records are typical of a truncated code** (`basis: extrapolated`). Equal
  allocation is kept so each code's own share stays estimable; the interval is capped at the sample size, so it is
  never narrower than a simple random sample of the same size would give.
- **Finding 4:** a business or product named by a name that is not in the pack's alias table still passes the scan;
  the second G7 `NOT_COVERED` item covers it, and LEAKAGE 10 now says so.
- **Finding 5: only records the breaker never sent are re-sent.** A record whose own call failed (after the client's
  retries) keeps its lexical fallback, as before; so do stand-ins whose count week was emitted (cells are never
  revised). The judge's breaker stays per question, as round 2 left it.
- **Finding 6 is strict by design:** one transport failure fails a routing G0. A founder whose server times out on
  one long record raises `deadline_s` and re-runs; a validation failure is reported but does not fail the run.
- **Finding 7:** proxy credentials are still unsupported, and the openFDA connector keeps using the environment's
  proxy (it fetches public data). The ledger's `host` remains the final destination; `proxy` says whether a proxy
  stood between.

### Synthetic figures (synthetic, same-author, not a measurement)

Printed in this sandbox, quoted only as **synthetic, same-author, not a measurement**:

- The X1 construction smokes on the re-planted fixtures (device: rules removed, seed 11; claims: built-in, seed 5;
  6 sites, 52 weeks, evaluation weeks 26 to 51): device X 3 of 3 (control 0, net 3), S 0 of 3, R (model-free) 0 of
  3, U 3 of 3, single_site 3 of 3 (control 2, chance 0, net 3); claims X 2 of 3 (net 2), S 0, R 0, U 3 of 3,
  single_site 3 of 3 (control 1, chance 0, net 3), the same as round 2's on the old fixtures.
- The stale chains: device (weeks 19 to 21, watch 2024-W27 to W33) is a candidate in 2024-W20 to W25 and alerts in
  2024-W20 (burn-in); with the stale filter disabled it is also a candidate in W27 and W28. Claims (weeks 19 and 20,
  watch W27 to W32): candidate W20 to W25 and, filter disabled, W27. On the old fixtures neither had a candidate in its
  watch span with the filter disabled (the finding). With the filter disabled neither alerts in its watch span
  either (the follow-up below).
- The pushdown figures quoted for the device `plant_smoke` world (seed 11) still hold on the re-planted fixture: 7 of
  14 conclusions supported (the three patterns, the unmarked copies and the three high-base-rate keys), the stale
  chain `stale`, the other decoys hypotheses; on the claims world (seed 5) `clinic:CL-B7X9:treatment_pattern_mismatch`
  is a hypothesis from 2024-W43 to W49 and supported at W50, and `tow_operator:TW-0310:tow_without_dispatch` is
  supported throughout.
- G0 in fake mode (`device_quality`, seed 11, 1,000 records, as `G0RunnerTests` runs it): `model_path` lists no
  problem, with 1,000 of 1,000 extraction records answered by the fake model and every judge attempt answered.

## Audit round 3, review follow-up (finding 1's stale-chain outcome)

The review of the round-3 fixes found finding 1 only partly fixed. The re-planted stale chains are candidates in
their watch span when G4's stale filter is disabled, but X1 did not score candidates: `harness.decoy_outcome` failed
a decoy only on an **alert** in its watch span, and that is what fed `decoys_alerted.stale_chain` and the X decoy
card. With the filter disabled the chain still never alerted there. It alerts while fresh (burn-in), and because its
whole chain lies inside every window from that alert to `eval_from`, it is a candidate at every step after, so the
cooldown ("a cooling key present now restarts its quiet count") is never released. So the cooldown, not the stale
filter, held the alert-level outcome, while ARCHITECTURE 14.2, RUNBOOK 11 and `plant.py` said the filter alone kept
the chain quiet. With the filter disabled globally, `test_structural_decoys_are_quiet_elsewhere_and_raise_no_x_alert`
still passed.

**Choice.** The review left the planner a choice: (a) score a stale chain on candidacy, or (b) say in the docs that
its alert-level outcome is held by the cooldown and stop presenting it as an alert-level test. The engineer took (a),
because only (a) lets a broken filter show in the scorecard. The planner may still prefer (b).

| Fix | Regression test |
|---|---|
| `baselines.detector_candidates` gives a G4 result's `{week, key}` candidate events (after the stale filter, cooling or not). `harness._channel_events` returns them for every channel that runs G4's detection (X, S, R_mf, U and the k=1 ablation; not `rules` or `single_site`), and `_seed_run` keeps those of stale-chain keys in the evaluation weeks. `decoy_outcome` adds `candidate` (null unless a stale chain on a channel with candidates), `first_candidate_week` and `failed` (an alert in the watch span, or for a stale chain also a candidate week there). Channel blocks add `decoys_failed` beside `decoys_alerted` (unchanged: alerts only). The scorecard schema has the new fields. Decoy outcomes still enter no X1 verdict. ARCHITECTURE 14.2, 14.4 and 14.6, RUNBOOK 11, the `plant.py` and `harness.py` docstrings | `MetricTests::test_a_stale_chain_fails_a_channel_that_keeps_it_a_candidate_in_its_watch_span` (outcomes with and without candidates, a channel without candidates, `decoys_failed` against `decoys_alerted`). `assert_only_the_stale_filter_keeps_it_quiet` (both packs' `test_only_the_stale_filter_keeps_the_stale_chain_out_of_its_watch_span`) now also pins the shipped X outcome (`candidate: false`, `failed: false`, null for `rules` and `single_site`), then runs the harness again with `detectors._stale` disabled: the X outcome is `alerted: false`, `candidate: true`, `failed: true`, its first candidate week is the unfiltered detection's, and `decoys_failed.stale_chain` is 1 with `decoys_alerted.stale_chain` 0. `test_structural_decoys_are_quiet_elsewhere_and_raise_no_x_alert` also asserts `failed` is false |

Earlier tests whose expectation encoded the finding:
- `MetricTests::test_a_decoy_counts_only_inside_its_watch_span` compared `decoy_outcome` with
  `{alerted, first_alert_week}`. It now compares the full outcome, gives its label a `class` (labels always carry
  one), and adds a check that candidacy is not scored for a class other than `stale_chain`.
- `assert_only_the_stale_filter_keeps_it_quiet` claimed in its docstring that the filter keeps the chain quiet; it
  now says the filter keeps it from being a candidate and that the cooldown holds the alert.

With the reviewer's plugin that disables `detectors._stale` for the whole session (`-p stale_off`), the construction
tests now give 3 failed, 16 passed: both packs' stale-filter test and the `d-stale` subtest of
`test_structural_decoys_are_quiet_elsewhere_and_raise_no_x_alert` (it was 2 failed, 16 passed before, with the X
decoy card test passing). Mutation checks, each restored: never scoring candidacy in `decoy_outcome`, dropping the
candidate events from `_seed_run`, and counting `decoys_failed` from `alerted`. Each makes the new unit test or both
construction tests fail.

Merge notes: X1 scorecards gain `decoys_failed` per channel block and `candidate`, `first_candidate_week` and
`failed` per decoy outcome. `evaluate/harness.py` and `evaluate/baselines.py` are in the X1 code hash, so a prereg
made before this change is refused by `run`. No fabric file, pack file or SQLite schema changed.

Synthetic figures (synthetic, same-author, not a measurement), from the construction smokes in this sandbox (device:
built-in, seed 11; claims: built-in, seed 5; 6 sites, 52 weeks, evaluation weeks 26 to 51). As shipped, the stale
chain's outcome is `alerted: false`, `candidate: false`, `failed: false` on X, S, R_mf and U in both packs. With the
stale filter disabled, X and U give `alerted: false`, `candidate: true`, first candidate week 2024-W27, `failed: true`
(device watch 2024-W27 to W33, claims W27 to W32), and S and R_mf, which cannot see a narrative-only plant, stay
`candidate: false`. X's recall is unchanged (device 3 of 3, claims 2 of 3).

## Audit round 3, review follow-up (finding 5's re-send and the exemption check)

The review found that the guard on the new re-send path had no test. A model pass now also sends the breaker's
`not_sent` stand-ins, which are stored already, so `EdgeSite.extract` passes `pending_non_synthetic(redo)` to the
exemption check; counting only records without an extraction (`pending_non_synthetic()`) would let the next pass send
non-synthetic narratives to an external endpoint under `allow_external_raw='synthetic'`. The shipped code counted
them, but that mutation passed every suite: the one related test read `RecordStore.pending_non_synthetic(NOT_SENT)`
directly and never went through `extract`. No code changed.

| Fix | Regression test |
|---|---|
| None needed in the code; a site-level test now pins the check | `UsageTests::test_an_exempt_endpoint_is_refused_the_stand_ins_a_model_pass_would_send_again`: the site's own server fails twice, so 3 of 5 records become stand-ins and none is left without an extraction; the same store is reopened with an `external` extraction endpoint under `allow_external_raw='synthetic'`. With non-synthetic records, `extract('model')` raises `SiteError`, the server receives no request, the exempt runtime's ledger stays empty and the stored rows are unchanged. The control, with synthetic records, sends the 3 stand-ins to that server and replaces them (`resent` 3, ledger `external_raw_exempt`, `synthetic`) |

Mutation check, on a scratch copy outside the worktree: with `pending_non_synthetic(redo)` changed to
`pending_non_synthetic()` in `edge/site.py`, the new test's non-synthetic case fails ("SiteError not raised"); the
unmutated code passes it. No fabric file, pack file or SQLite schema changed; only
`tests/mycelic/test_collective_edge.py` and this file.

## B1 (round 4, gate 1 of 4): an honest codes-miss illustration in the demo

The final review found that the committed demo case is not one the restricted central baseline misses: R
(model-free) flags the product under the generic code at the top rank in X's own week, and S flags the lot a week
later. STRATEGY 9.2 names a case that R misses or mis-ranks and pushdown resolves as the cut's centre; this run is not
one, and the draft it ends with repeated one line and listed no lots. B1 keeps that run as evidence, fixes the draft,
the attribution and the wording around the first scenario, and adds a second, constructed scenario in two commits:
**B1a** (sections 1 to 4 of the brief: everything before any codes-miss world exists, including the pre-registration)
and **B1b** (the scenario, the engine's attribution rule and its screen, the attempts and the re-recordings).
**Status (audit round 4): only B1a was committed. B1's constructed codes-miss illustration has not been built**: no
B1b commit, no `demo/collective/scenario_codes_miss.json`, no `docs/collective/b1/attempts/` and no run of it exist,
and the only recorded run is `collective-halvern-b1a`, which shows no case the allowed fields miss.

### B1a: the draft template, source attribution, one sites wording, the superseded run and the pre-registration

**Base.** Branch `mycelic-collective-phase2` at b0af5b8 (audit round 3), a clean worktree. Nothing is committed by
the engineer. No codes-miss world was built, previewed or detected in B1a: `scenario_codes_miss.json` and
`docs/collective/b1/attempts/` do not exist yet. To write the cast into the pre-registration the engineer read the pack
data only (the generator's sites, universe, links, fill rates and master data, and `window_weeks`), never a world.

**Scope: fabric files changed: none.** Frozen and unchanged (`git diff --name-only b0af5b8` empty):
`mycelic/collective/{detect,edge,evaluate}/`, `stats.py`, `jsonio.py`, `schemacheck.py`, `experiments/common.py`;
`DETECT_SHA256` is unchanged. Changed: `packs/loader.py`, both packs' `followups.json`, `followup/drafts.py`,
`demo/collective/{collective_demo.py,screen.py,README.md,SCRIPT.md}`, `docs/collective/{ARCHITECTURE,INTEGRATION,
LEAKAGE,PACKS,RUNBOOK}.md` and five test files. Added: `docs/collective/b1/PREREG.md`,
`docs/collective/evidence/superseded/collective-halvern-g10/README.md` and the run
`demo/collective/recorded/collective-halvern-b1a/`. Moved (`git mv`, byte-identical; the six b0af5b8 sha256 values are
pinned in `SupersededEvidenceTests`): `demo/collective/recorded/collective-halvern-g10/` to
`docs/collective/evidence/superseded/collective-halvern-g10/`.

| Part | What changed | Tests |
|---|---|---|
| Draft template (pack data, loader) | Every follow-up type gains the required key `template`, after `draft_schema`: null unless the executor is `draft`; for a draft type one source per required property (`PACKS.md` 1.3). `FollowupType.template`; `template` joins `RESERVED`. The loader refuses, each with a fixed problem at the template's path: the key set or not against the executor, unknown and missing properties, an unknown source, a duplicate list entry, a source that does not fit the property's type (a list on an array, an empty or too-long list included), and `entity_ids:` of a type that may not leave a site | `TemplateLoaderTests` (every refusal, the built-in templates, other legal forms changing only `config_hash`, `RESERVED`, only `config_hash` differs from G5) |
| Template drafter (`drafts.py`) | `template_draft` fills `headline`, `summary`, `evidence` (one clause per ok packet), `for_owner` (`FOR_OWNER_TEXT` or `[]`), `entity_ids:<type>` and `confirming_sites`; cuts strings at whole clauses, then the evidence, then the last space, and arrays at `maxItems`; `template_sources`, `SOURCES`, `EVIDENCE_NONE`. `DraftWriter`, the schema check and the scope scan are unchanged; no clause format tripped the scan in either pack | `TemplateDraftTests` (every pack under `BUILTIN_ROOT`, every enabled draft type, every egress entity type, ok, partial and failed packet sets with the packs' own site ids and every bucket label: schema-valid, scope-clean, distinct non-empty strings, the entity-ids rule, confirming sites, the clause-boundary cut on a 120-packet payload, dropping the evidence, the space cut, `EVIDENCE_NONE`, a model draft ignoring the template and still scope-checked) |
| Source attribution (`collective_demo.py`) | The scorecard's draft block gives each field and list a `source`; `field_sources(ft, label, ledger_source)`: the template's sources when no model wrote the draft (stand-in, template, local test server), `model` for a routed model, else the ledger's (`edited`) | `DraftSourceAndStampTests`; `_HeroAssertions.assert_draft` on the fixture recordings and the committed run (affected lots recomputed from the hero key and the ok packets' co-mentions, distinct title, problem statement and containment, every source) |
| One sites wording | `sites_stamp(routed, providers)` sets `stamps.shared_model` and the trace's `sites_note`: `SITES_NOTE` only when an extract or judge call went to a model; `TEST_SERVER_SITES_NOTE` for a run routed to a local test server; none without routing | `DraftSourceAndStampTests::test_sites_stamp_*`; `ExportReplayTests::test_routing_fake_server_end_to_end` rewritten |
| X by construction | `detection.by_construction` gains `X` and `x_reason` (`X_BY_CONSTRUCTION` when the extract label is not a model's); the screen shows "By construction, X reads this case perfectly: " with the reason as an item | `_HeroAssertions.assert_detection`; `HonestyTests::test_screen_sentences_follow_flags` now toggles it too |
| Screen | A `for_owner` field or list reads "left for the named owner to write"; an empty list from another source "none in the conclusion or the packets"; "none listed" is gone | `DraftSourceAndStampTests::test_the_screen_shows_each_draft_field_by_its_source` |
| Evidence | The G8 run moved to `docs/collective/evidence/superseded/collective-halvern-g10/` with a README naming why it is superseded and pointing into its scorecard | `SupersededEvidenceTests` (sha256 pins, portability, README pointers resolve, the README's claims checked against the scorecard) |
| Pre-registration | `docs/collective/b1/PREREG.md`: the Tarnwick cast, the shift, the realism constraints, `CODES_MISS_RULE`, `STATEMENT` and `AUTHOR_NOTE` verbatim, seed 29, the eight robustness seeds, the grid, the attempt rules and the disclosure. Never edited after B1a | `SupersededEvidenceTests::test_the_prereg_is_committed_with_the_cast_and_the_rule` |

**Pack hashes (only `config_hash` changed).** `B1_CONFIG_HASHES` (`test_collective_pushdown.py`, after
`R2_CONFIG_HASHES`); the vocabulary, detector and fixtures hashes are G5's.

| Pack | `config_hash` (R2) | `config_hash` (B1) |
|---|---|---|
| `device_quality` | `ac59c4cbb418509f02c8cef3cfb738fa65849f659844f6286e90a676f8c2276f` | `9de50cd4f690ba8876c7e1abb62f6fca886d1c5b96c00b048fa54d0c0c8a9d2d` |
| `claims_integrity` | `12d62cdfcab0c3fab0a0f1f11f1809df08aacce691b7dee41d3a86c7085deb7a` | `0ddf016d5bdb7f345528db831b9d174831fa15327518c3e9ddbd5781008c19a6` |

**Code hashes that change** (`packs/loader.py` is in each file list; first 32 hex, b0af5b8 then B1a):

| Hash | b0af5b8 | B1a |
|---|---|---|
| X1 (`harness.EVAL_CODE_FILES`) | `e41939185b7f9e4dfc60d83460bac6a5` | `58be1775321f1ee74abdff055b71a09f` |
| E1 (`e1_extract.E1_CODE_FILES`) | `37073e7a005d4cf2977466d0b8b6b322` | `07c08295d7eeeff6eadcaa1db80ee500` |
| E2 (`e2_pushdown.E2_CODE_FILES`) | `50199a9d42b29a5df0947c127da3ab37` | `73183f3e994195d990ae494748194ce8` |
| openFDA replay (`openfda_replay.REPLAY_CODE_FILES`) | `a5283e561bf97889135fac08ce1de0a0` | `2a0125d962aeead5b8693f3ac1d6f8c8` |

**The committed run.** `demo/collective/recorded/collective-halvern-b1a` is the only run under `recorded/`:
`content_hash` `8e851b4b796fc72165880791866ad11e`, scenario digest `63533d7ef51943361aa3d6f4ecd9d40e` (unchanged from
G8), every check ok, the lint green. Its detection blocks are the G8 run's: R (model-free) still flags the product
under the generic code at the top rank in X's week, and the screen and README say so. `--record` took 15.2 s (the
command's own `record:` line; wall clock 15.5 s with interpreter start-up), sandbox engineering timing, not a product
figure.

### Test counts (sandbox timing, not a product figure)

Both suites with `TMPDIR` on tmpfs, `nats-server` on the path; the b0af5b8 column is the same command on an export of
that commit, run in this sandbox the same day.

| Command | b0af5b8 | B1a |
|---|---|---|
| `python -m pytest tests/mycelic -q -p no:warnings` | 1017 passed (27,378 subtests), 0 skipped, in 806 s | 1033 passed (27,480 subtests) = 1017 + 16 new, 0 skipped, in 798 s |
| `python -m pytest NeuralGraph/tests -q -p no:warnings` | not re-run (no file under `NeuralGraph/` changed) | 222 passed, 1 skipped (119 subtests) in 5 s; the skip is the pre-existing one every earlier round lists |

The added suite time is within the run-to-run noise (the B1a run was 8 s faster). The 16 new tests take under a second
on their own (`TemplateLoaderTests` and `TemplateDraftTests`: 10 tests in 0.7 s); `DraftSourceAndStampTests` and
`SupersededEvidenceTests` add no recording (their 30 s on their own is the module's two shared recordings in
`setUpModule`, which the full suite already runs). G0 (`--records 1000 --seed 11`) exits 0 for both packs with `hits`
empty and `shingle_overlap_bytes` 0 (8.3 s and 8.2 s); the E5 tests pass.

### Earlier tests whose expectation changed

Changed, never weakened:

- `test_collective_followup.py`: `PacketDraftTests::test_drafts_from_structured_inputs_only` checked the template
  draft against the conclusion's own scope; the evidence now names the packets' co-mentions, so it checks the
  service's scope (the conclusion's plus the packets' co-mentions, `FollowupService._scope`) and also asserts that the
  conclusion's scope alone refuses the evidence field (`out_of_scope_id`). Its claims-pack title assertion is the new
  headline, and the summary is asserted at the start of `pattern_summary`, with `EVIDENCE_NONE` at its end (no packet)
  and `requested_checks` empty. `PackFollowupTests` pins `B1_CONFIG_HASHES` and asserts the hash differs from G6, G7
  and R2. The T2 type of the `DQX_EDITS` pack copy gains `"template": null` (the key is required).
- `test_collective_pushdown.py` (`test_only_config_hash_changed_against_g5`) and `test_collective_evaluate.py`
  (`test_the_loader_accepts_plant_files_and_hashes_none_of_them`): the pinned config hash is B1's, and each asserts it
  differs from R2's and G7's.
- `test_collective_demo.py`: `test_routing_fake_server_end_to_end` asserted "one shared model" for a run routed to the
  fake server; it now asserts `shared_model` false, `TEST_SERVER_SITES_NOTE`, no "shared model" in any screen text or
  item, template sources and `by_construction.X`. `HonestyTests::test_screen_sentences_follow_flags` also toggles
  `by_construction.X`.

### Merge notes

1. **No fabric change and no fabric integration point.**
2. **Only `config_hash` changed in both packs.** A site store, HQ store or follow-up ledger created with the R2 hash
   is refused (`config_hash` / `ledger_info mismatch`); rebuild them. A pack copy elsewhere (the lab's fixtures, the
   third pack of B4) needs the `template` key on every follow-up type, `null` unless it drafts.
3. **Every X1, E1, E2 and openFDA-replay prereg made before B1 is refused** (the code hashes above): the lab team must
   re-make its preregs on this commit before a run.
4. **Scorecard and trace schema.** The demo scorecard's draft fields and lists carry `source`, `by_construction` carries
   `X` and `x_reason`, and the trace's `sites_note` may be `TEST_SERVER_SITES_NOTE`. The trace's `draft` events are
   unchanged (no source). The superseded G8 run does not validate against the new scorecard schema and is not linted
   as a run; its `screen.json` still validates, so `--replay` of that directory works.
5. **G0 byte figures** in `LEAKAGE.md` sections 10 and 11 are re-measured (the drafts restate the packets' evidence);
   G0 still exits 0 with `hits` empty and `shingle_overlap_bytes` 0 for both packs.

### Disagreements and residuals, for the reviewer

- **The evidence follows the summary after one space**, as the brief's list rule says, so the problem statement reads
  "... (conclusion c-... v1) plant-ashvale: confirm, support 3-9, ...". A separator such as " Evidence: " would read
  better but is not in the brief.
- **The clause format was not changed** for the scope scan: with the packs' own site ids, every bucket label and
  every code label, no clause trips it. Synthetic site ids such as `site-ok-000` do (the device pack reads `ok-000` as
  a product id), so the tests use the packs' site ids or letter-only ids; a real site id of that shape would make the
  draft fail `out_of_scope_id`, visibly, never pass.
- **`LEAKAGE.md` section 11's committed-run figure** said 49,452 run-file bytes while the G8 run's own `leakage.json`
  says 49,571; it now quotes the B1a run's own value.

## B2 (round 4, gate 2 of 4): sealed, procedurally blind X1

B2 runs X1 (STRATEGY 11.2) on both built-in packs as properly as is honest in this sandbox, in four local commits the
orchestrator authorises in order: **B2a** freezes the evaluation code, the planter brief and the results template;
**B2b** derives ten fresh seeds and makes one prereg per pack on the clean B2a tree; a fresh planter agent, given only
the brief, writes the specs in a tmpfs sandbox; **B2c** seals them; **B2d** runs the harness once per pack and writes
`RESULTS.md` from the template. **The planter and the detector author are the same AI system: the blinding is
procedural only, STRATEGY 11.2's X1 is not met, and every result is synthetic and internal only.**

### B2a: the freeze (harness, brief, template, tests, docs)

**Base.** Branch `mycelic-collective-phase2` at d8feab1 (B1a), a clean worktree. Nothing is committed by the
engineer. No seed, prereg or sealed spec of B2 exists in this tree, no harness `run` used any of them, and the
engineer has not opened any planter file (see "A planter run before B2a" below).

**Scope.** Under `mycelic/` only `mycelic/collective/evaluate/harness.py` changed (`git diff --name-only d8feab1 --
mycelic/`). `plant.py`, `baselines.py`, `detect/`, `edge/`, `packs/*.py`, `stats.py`, `jsonio.py`,
`schemacheck.py` and `experiments/common.py` are unchanged, and `DETECT_SHA256` still matches. Also changed:
`tests/mycelic/test_collective_evaluate.py`, `docs/collective/{RUNBOOK,ARCHITECTURE,INTEGRATION}.md`. Added:
`tests/mycelic/test_collective_x1_sealed.py`, `docs/collective/x1/PLANTER_BRIEF.md`,
`docs/collective/x1/RESULTS_TEMPLATE.md`. No RUNBOOK placeholder was added, so `test_collective_guards.py` is unchanged.

| Part | What changed | Tests |
|---|---|---|
| Channel intervals | `interval_blocks` merges `recall_net_ci` (patterns resampled; the lifts' per-seed net found), `precision_at_40_ci`, `average_precision_ci` and `false_alarms_per_week_ci` (seeds with a value resampled) into every channel block, the ablation's included; seed string `x1:<seed>:<channel>:<metric>`; null without a cluster; `channel_block`'s signature is unchanged | `MetricTests::test_interval_blocks_on_hand_built_inputs`, `ScorecardSchemaTests::test_every_channel_carries_its_intervals_and_the_rate_strata`, the ablation in `PlantedConstructionTests`, `assert_interval_blocks` |
| Adjusted lifts | `lift(family_size=1)`: `alpha_adjusted = 0.05 / family_size`, `ci_low_adjusted`, `ci_high_adjusted` from a second call with the same seed string (same replicates, nested); `x1.verdict` reads the unadjusted interval | `MetricTests::test_lifts_carry_an_adjusted_interval_from_the_same_replicates` |
| Prereg keys | `family_size` (1 to `MAX_FAMILY_SIZE` = 100, `--family-size`, default 1) and `planter_relation` (`PLANTER_RELATIONS`, `--planter-relation`, default `unstated`), required in the closed `PREREG_SCHEMA`, validated in `cmd_prereg` with `UsageError` | `HarnessTests::test_prereg_pins_the_family_size_and_the_planter_relation`, `test_read_prereg_refuses_a_prereg_without_or_with_a_bad_family_size_or_relation` |
| x1 block | `family_size`, `planter_relation`, `independent`, `counts_as_strategy_x1`, `caveats` (`SAME_SYSTEM_CAVEAT`, `UNSTATED_CAVEAT`); `eligible`, `reasons` and `verdict` unchanged; keyword defaults keep earlier callers working | `MetricTests::test_x1_relation_family_size_and_caveats` (the full truth table) |
| Rate strata | each scorecard pattern carries `rate_per_week`; `by_rate_per_week` rows (ascending) of `found_net` and `recall_net` per channel | `MetricTests::test_rate_strata_split_net_recall_by_planted_rate` |
| check-plant | refuses a non-null `prereg_sha256` that is not the prereg file's sha256 (`run`'s message, `check_binding`); `--construct` builds every prereg seed's world and plant (`construct_every_seed`) and prints `construction: ok (seeds=N)`, or exits 2 with the error plus ` (seed <s>)` and no stdout; a dry run constructs nothing; without the flag the output is unchanged | `HarnessTests::test_check_plant_refuses_a_foreign_binding_and_accepts_a_matching_or_null_one`, `test_check_plant_construct_builds_every_prereg_seed_and_writes_nothing`, `test_check_plant_construct_names_the_seed_of_a_construction_failure`; `test_check_plant_prints_the_prereg_sha_and_writes_nothing` unchanged |
| Schemas, notes | `SCORECARD_SCHEMA` and `PREREG_SCHEMA` stay closed; `NOTES` gains the two B2 sentences; docstring and CLI help | `ScorecardSchemaTests::test_the_schema_rejects_tampering` (new cases), `DeterminismTests` (B2 prereg flags; the new blocks equal across processes) |
| Planter brief | `docs/collective/x1/PLANTER_BRIEF.md`: the planter's whole prompt (role, permitted reads, forbidden reads, commands, outputs, world, format with a skeleton, decoy classes, the rules check-plant enforces, the brief's own rules, procedure, names and paths) | `PlanterBriefTests` (forbidden words, classes, `PLANTED_BY`, `PERMITTED_READS`, bands, k, the 4-of-6 rule against the packs and against `check_plant`, portability, model names) |
| Brief rules | `brief_problems` and `brief_main` in `test_collective_x1_sealed.py` (fixed messages, never a value) | `BriefRuleTests` (smoke specs, a passing spec, each rule alone, `brief_main`) |
| Results template | `docs/collective/x1/RESULTS_TEMPLATE.md`: the catalog (six `always` sentences with the corrected blind sentence, the governing-interval and multiple-comparisons texts, ten interpretations in order with their conditions, ten row groups, the format) and the sections `RESULTS.md` follows | `TemplateTests` (closed shape, pinned texts, evaluator, expander, renderer, formatter), `TemplateOnAScorecardTests` (every scorecard row and condition pointer resolves on a one-seed smoke scorecard) |

**The brief audit (for the reviewer).** The brief states only the world shape (six sites, 104 weeks, `start`,
evaluation weeks 26 to 103), k (3 and 5), the stale bands (19 to 21 and 19 to 20), the 4-of-6 sites rule, master
data, template, alias and code availability, and the class shapes, all of which check-plant reveals. It names no
prereg as readable (the wrapper hands the prereg to check-plant, which prints its sha256), and adds `mapping.json`
to the permitted reads (structured entity types). `PLANTED_BY` is 77 characters.

**Pre-stated for B2b to B2d** (written before any seed, prereg or spec of B2 exists):

- **Seed rule.** `seed_i = 1 + int(sha256(f'x1-sealed:{B2a}:{i}'.encode()).hexdigest()[:8], 16) % 100000` for
  `i = 0, 1, ...`, where `{B2a}` is the B2a commit's 40-hex sha. A candidate is excluded when it repeats an earlier
  candidate (`duplicate`) or when this prints anything (`found: <path>:<line>`, the first hit):

  ```
  git -C <worktree> grep -n -w -e <value> <B2a> -- tests docs demo mycelic/collective
  ```

  The first 10 accepted values, sorted, are the seeds; `docs/collective/x1/seeds.json` records every candidate up to
  the tenth accepted one.
- **B2b prereg command**, per pack, on the clean B2a tree, with `TMPDIR=/dev/shm/b2/tmp`, then `cp` byte-identical to
  `docs/collective/x1/<pack>/prereg.json`:

  ```
  python -m mycelic.collective.evaluate.harness prereg --pack <pack> --seeds <seeds> --weeks 104 --eval-from 26 --eval-to 103 --grace-weeks 4 --tie-salt x1-sealed-<pack> --detector-author 'mycelic collective engineer agent' --family-size 2 --planter-relation same_system_procedural --bootstrap-b 10000 --bootstrap-seed 1 --run-id x1-sealed-<pack>-prereg --runs-dir /dev/shm/b2/runs
  ```

- **Sandbox** (built from the committed B2b tree; nobody changes the worktree from the B2b commit until the planter
  finishes): `/dev/shm/b2/planter/BRIEF.md` (byte-identical to `PLANTER_BRIEF.md`), `packs/<pack>/<the seven pack
  files>` (byte-identical), an empty `specs/`, and `check-plant` (mode 755) made from
  `docs/collective/x1/check-plant.sh.in` with `@REPO@` set to the worktree and `@LOG@` to
  `/dev/shm/b2/check_plant_calls.jsonl`, outside the sandbox. Every sandbox file's sha256 goes into SEAL.
- **Prompt form.** Exactly the bytes of `PLANTER_BRIEF.md` followed by `\nSandbox: /dev/shm/b2/planter\n`;
  `prompt_sha256` is the sha256 of the exact text the orchestrator passed to the spawn call.
- **B2d run command**, per pack, at HEAD == B2c on a clean tree, never `--allow-dirty`, with `TMPDIR=/dev/shm/b2/tmp`:

  ```
  python -m mycelic.collective.evaluate.harness run --prereg docs/collective/x1/<pack>/prereg.json --plant mycelic/collective/packs/data/<pack>/fixtures/plant_x1_sealed.json --seeds <the ten seeds> --run-id x1-sealed-<pack> --runs-dir /dev/shm/b2/runs
  ```

**Code hashes that change** (`harness.py` is in each file list; first 32 hex, d8feab1 then this worktree; the B2a
values are recomputed on the committed tree by the reviewer, since any later edit of `harness.py` changes them):

| Hash | d8feab1 | B2a |
|---|---|---|
| X1 (`harness.EVAL_CODE_FILES`) | `58be1775321f1ee74abdff055b71a09f` | `e7d5d82e3b9805358718b5927b6cca89` |
| E2 (`e2_pushdown.E2_CODE_FILES`) | `73183f3e994195d990ae494748194ce8` | `7428e57c6c9e2b5f4e7d7a93d80a45bc` |
| openFDA replay (`openfda_replay.REPLAY_CODE_FILES`) | `2a0125d962aeead5b8693f3ac1d6f8c8` | `452219e81aa711fb97387d8bc5da9663` |
| E1 (`e1_extract.E1_CODE_FILES`, no `harness.py`) | `07c08295d7eeeff6eadcaa1db80ee500` | unchanged |

No pack file changed: every pack hash is B1's.

**Test counts (sandbox timing, not a product figure).** Both suites with `TMPDIR` on tmpfs and `nats-server` on the
path; the d8feab1 column is B1a's recorded run.

| Command | d8feab1 | B2a (this worktree) |
|---|---|---|
| `python -m pytest tests/mycelic -q -p no:warnings` | 1033 passed (27,480 subtests), 0 skipped, in 798 s | 1061 passed (27,620 subtests) = 1033 + 28 new, 0 skipped, in 761 s |
| `python -m pytest NeuralGraph/tests -q -p no:warnings` | 222 passed, 1 skipped (119 subtests) | 222 passed, 1 skipped (119 subtests) in 5 s; the skip is the pre-existing one |

The 28 new tests are 10 in `test_collective_evaluate.py` (the module: 75 tests in 80 s on its own) and 18 in
`test_collective_x1_sealed.py` (9 s on its own, most of it the one-seed smoke scorecard of
`TemplateOnAScorecardTests`).

**Earlier tests whose expectation changed** (changed, never weakened):

- `test_collective_evaluate.py`: `MetricTests::test_x1_eligibility_and_verdict` compared the whole x1 block; it now
  includes the five new keys at their defaults (`family_size` 1, `unstated`, `independent` false,
  `counts_as_strategy_x1` false, `[UNSTATED_CAVEAT]`). `DeterminismTests` makes its prereg with `--family-size 2
  --planter-relation same_system_procedural` and also asserts the x1 additions, the adjusted lifts, the interval blocks
  and `by_rate_per_week` equal across the two processes. `ScorecardSchemaTests::test_the_schema_rejects_tampering` gains
  seven cases; `PlantedConstructionTests::test_the_x1_block_lifts_and_ablation` also checks the ablation's interval
  blocks. `HarnessTests.prereg` gained a `seeds` keyword (a helper, no expectation).
- `test_collective_e2.py` is unchanged and passes: its preregs come from the harness CLI, so the defaults fill the new
  keys.

**Merge notes.**

1. **No fabric change and no fabric integration point.**
2. **Every X1, E2 and openFDA-replay prereg made before B2a is refused** (the code hashes above; an X1 or E2 prereg
   also lacks the two required keys). Re-make them on this commit; E1 preregs are unaffected.
3. **Prereg and scorecard keys are added** (both schemas closed): the prereg's `family_size` and `planter_relation`; the
   scorecard's four channel interval blocks, the lifts' adjusted fields, the x1 additions, `patterns[].rate_per_week`
   and `by_rate_per_week`. A scorecard written before B2a fails the new schema; a consumer that copies the schema needs
   the new keys.
4. **check-plant refuses a mismatched binding** (before, only `run` did) and has `--construct`. The lab's argv keeps
   working (both prereg flags are optional); a lab-made prereg gets `unstated` and family size 1, so its scorecard
   carries `UNSTATED_CAVEAT` and `counts_as_strategy_x1` false.

**A planter run before B2a (out of procedure; recorded, not used).** Before this freeze existed, the orchestrator
spawned a planter that wrote one spec per pack and a declaration under `<scratchpad>/sealed_plants/` (outside the
repository). As the orchestrator relayed the planter's report: it was given the full B2 design brief rather than
`PLANTER_BRIEF.md` (which did not exist yet), and that text names detector mechanisms (through the forbidden-word
list), the alert cut, the channels and the grace weeks; a listing of `loader.py` showed it detector setting names; so
it set `planter_saw_detector_code` to true in both specs. There was no sandbox, no wrapper and no call log, and
`prereg_sha256` is null in both specs because no prereg existed. The engineer has not opened these files; it only
checked their sha256 against the relayed values (`sha256sum`, equal):

| File | Bytes | sha256 |
|---|---|---|
| `specs/device_quality.json` | 19407 | `b34159af684607d836bd4b5379f0f8b6a94eec6f480de33cc9bd27d9e183db3f` |
| `specs/claims_integrity.json` | 20035 | `24b8d490ddca5134c8279eb20daed21a20026952bfafa204c27c25d072223c6a` |
| `declaration.json` | 4119 | `904bae9b16fc55059198399f698fe56627b050ddee2ef001af7d1386d5b8fe25` |

They cannot be sealed as they are: an unbound spec fails the brief's rules, binding means changing `prereg_sha256`,
which only a planter may do (after B2b, and it changes the bytes and so `plant`'s RNG, so construction must be
re-checked at the prereg seeds), and a run on them would be ineligible (`planter_saw_detector_code` true). An
eligible run needs the designed procedure: a fresh agent given exactly `PLANTER_BRIEF.md` in the B2b sandbox. Either
way the run is recorded in `SEAL.json` `history` at B2c.

### Disagreements and residuals, for the reviewer

- **The construction-failure test uses five `de` filler sentences, not one.** The loader refuses fewer than five
  (`S_FILLER` `minItems` 5), so the design's one-sentence pack copy cannot load. With five there are 5 + 20 + 60 = 85
  filler-only texts, fewer than the 100 records of the test's `codes_only` `de` pattern, so construction fails at the
  first seed whatever the draw.
- **A dry run with `--construct` constructs nothing.** It checks the prereg, the pins, the world, the spec and its
  binding (a foreign binding still exits 2), then emits; construction is the expensive part and writes nothing, so
  the contract ("creates nothing") holds either way.
- **On a construction failure check-plant prints nothing on stdout**, so `prereg_sha256:` appears only on success,
  as the brief's procedure says.
- **`passing_spec` is a brief-rule fixture**, built from pack ids and predicates; it meets every brief rule with the
  least slack the single-rule cases need but is not a spec check-plant would accept. The sealed specs themselves are
  checked against check-plant at B2c.
- **`x1.caveats` is an enum list** in the schema (only the two caveat sentences), stricter than a free string list.
- **The template's catalog writes every pointer out** (`/channels/{channel}/recall`, `/channels/{channel}/recall_net`,
  ...) instead of the design's brace lists, so that only the five named placeholders and `{i}` need expanding. A
  pointer that passes through null (an ineligible run's `/x1/verdict/pass`, rules' `precision_at_40_ci`) shows `null`;
  a missing key is an error. Each SEAL row appears once: the packs' prereg and fixture rows are in `Lab handoff`, their
  pack, pattern and decoy counts in `Protocol`. `By construction and warnings` and `Cost` are row groups of their own
  in each pack's section.
- **`==` and `!=` compare a bool only with a bool** in the condition evaluator, and an ordering op on a bool is false,
  so `true` never equals `1`.

## B3 (round 4, gate 3 of 4): X5, leakage beyond text, measured by an HQ-level red team

B3 builds X5 (STRATEGY 6.4 and 11.2) and publishes its result as `LEAKAGE.md` section 12, "the leakage figure", in
whatever direction it falls, in three local commits the orchestrator authorises in order: **B3a** the code, tests and
docs; **B3b** the prereg (`docs/collective/x5/prereg.json`), made on the clean B3a tree before any `x5.json` exists;
**B3c** one `run` of that prereg at HEAD = B3b, its `x5.json` and the filled section 12. **Everything is synthetic,
same-author (the worlds, the red team and the defences come from the same AI system) and internal only; it is never a
buyer claim.**

### B3a: the code, tests and docs

**Base.** Branch `mycelic-collective-phase2` at 202dd531 (B2a), a clean worktree. B2 committed only B2a (no seeds,
seal or results) and B1 only B1a, so the base of every B3 check is 202dd531 and "the X1 code hash" is B2a's.

**Scope.** Under `mycelic/` exactly three files differ from 202dd531 (`git diff --name-only 202dd531 -- mycelic/`):
`experiments/__init__.py` (one docstring entry, "X5 (leakage beyond text, B3)") and the new
`experiments/x5_inference.py` and `experiments/x5_attacks.py`. Also changed: `tests/mycelic/test_collective_guards.py`
and `docs/collective/{LEAKAGE,RUNBOOK,ARCHITECTURE,INTEGRATION}.md`; added: `tests/mycelic/test_collective_x5.py`.
`g0_canary.py`, `common.py`, `stats.py`, `leakage.py` (so `NOT_COVERED` is byte-unchanged), every file of every
code-hash list of X1, E2, E1 and the openFDA replay, every pack file, `demo/` and `docs/collective/evidence/` are
unchanged (`git diff --quiet 202dd531 -- demo docs/collective/evidence mycelic/collective/packs
mycelic/collective/experiments/g0_canary.py mycelic/collective/experiments/common.py mycelic/collective/stats.py
mycelic/collective/leakage.py`), so the demo's committed `leakage.json` files are byte-identical. No RUNBOOK
placeholder was added.

| Part | What | Tests (`test_collective_x5.py` unless named) |
|---|---|---|
| Split and strata | `split_members` (seeded coin per original, a copy follows its origin); `a1_targets` (balanced (site, week) strata, single-class strata dropped and counted, copies never targets) | `SplitTests` |
| Variant copies | `write_variant`: `k<K>` (k and `bucket_edges`), `rmd_flipped`, `minus_type` (`mapping.primary_entity_type`: vocabulary, egress, question templates; a copy that does not load is a `UsageError` naming pack and type), `volume`; canonical JSON plus a newline; `a5_fields` (person fields of kind `name`) | `VariantPackTests` (buckets, hash deltas, the own-k rewrite changes nothing, device `lot` refused) |
| Lexical rows | `lexical_rows` equals what a fake-mode site stores (`RecordStore.emission_inputs`) | `LexicalRowsTests` (both packs, a real pipeline) |
| Facts | `Extractor`: cells by channel with covered weeks and the four-week span, usage, verdicts (confirm, newest week and the zeros after it, refute, unknown by master data, budget and no_secret silent, truncated open), packets, follow-up, HQ results, run files, deduplicated strings, week indexes | `FactExtractorTests` (hand-built artifact bodies, a year boundary) |
| Attacks | `x5_attacks`: `FactIndex`, A1 score, `calibrate` and the coin, A2 with the prior chain, A3 bound propagation, A4, A5, A6; no attack function takes truth | `AttackTests` (constructed mini-worlds; signatures inspected) |
| A6 probe | `a6_probe`: fresh `EdgeSite` and `SiteVerifier` per site from `as_of + 1` day, the per-entity budget per day, a `budget` answer asked again and counted, never read as absence | `A6ProbeTests` (a real site and verifier; HQ's own questions spend the day's budget first) |
| Statistics | `label`, `value`, `summarise` (Wilson, cluster bootstrap, nested Bonferroni from the same seed), `incremental`; primary B versus exploratory B | `StatsLabelTests` |
| Prereg and run | the closed prereg schema, the seed rule, the pins (code, packs, copies, worlds), dirty and existing-directory refusals, the dry runs and the estimate line, the cap | `PreregRunTests` |
| x5.json | the closed results schema built from the prereg, portability, the self-scan, `content_hash`, the full matrix | `SchemaScanTests` (one tiny run made in `setUpModule`) |
| Determinism | one tiny prereg run twice in subprocesses under `PYTHONHASHSEED` 0 and 1 | `DeterminismTests` |
| Controls | `controls_block`, `control_exit`; injected cells pass the extractor and are refused by `check_artifact` | `ControlTests` |
| Section 12 | the skeleton's fixed texts; `leakage_section` rows resolved by an independent parser with `format_value` and `lookup` from `test_collective_x1_sealed`; the bar; the NOT_COVERED mapping | `LeakageSectionTests` |
| Code hash | every `mycelic.collective` module loaded with `x5_inference` is in `X5_CODE_FILES` | `ImportClosureTests` |
| Guards | `STDLIB_ONLY_MODULES` 60 to 62, `CLI_MODULES` (`prereg`, `run`), `DETERMINISTIC_MODULES`, `X5AttacksImportGuardTests` (static guard, snippets, an injected import, `TYPE_CHECKING` only for the pack type, the fresh-interpreter module set), the RUNBOOK commands | `test_collective_guards.py` |

**Hashes that do not change** (recomputed on this worktree; first 32 hex): X1 (`harness.eval_code_hash()`)
`e7d5d82e3b9805358718b5927b6cca89` (B2a's; there is no SEAL); E2 `7428e57c6c9e2b5f4e7d7a93d80a45bc`; openFDA replay
`452219e81aa711fb97387d8bc5da9663`; E1 `07c08295d7eeeff6eadcaa1db80ee500`; `device_quality` config `9de50cd4`,
vocabulary `e46f5211`, detector `c9462f62`, fixtures `dc4b70b7`; `claims_integrity` config `0ddf016d`, vocabulary
`028b7603`, detector `2041fe9b`, fixtures `a2e8936b` (B1's). **The X5 code hash** (`x5_inference.x5_code_hash()`,
`X5_CODE_FILES`) is `dea481ca619129e1f42f685256834e81` on this worktree; the reviewer recomputes it on the committed B3a tree.

**Test counts (sandbox timing, not a product figure).** Both suites with `TMPDIR` on tmpfs and `nats-server` on the
path; the 202dd531 column is B2a's recorded run.

| Command | 202dd531 | B3a (this worktree) |
|---|---|---|
| `python -m pytest tests/mycelic -q -p no:warnings` | 1061 passed (27,620 subtests), 0 skipped, in 761 s | 1122 passed (27,940 subtests) = 1061 + 61 new, 0 skipped, in 765 s |
| `python -m pytest NeuralGraph/tests -q -p no:warnings` | 222 passed, 1 skipped (119 subtests) | 222 passed, 1 skipped (119 subtests) in 4 s; the skip is the pre-existing one |

The 61 new tests are 54 in `test_collective_x5.py` (22 s on its own, most of it the tiny run of `setUpModule` and the
two determinism subprocesses) and 7 in `test_collective_guards.py` (`X5AttacksImportGuardTests`, six, and
`RunbookCommandTests::test_commands_cover_the_b3_clis`). No earlier test's expectation changed except
`StdlibOnlyTests` (the module count, 60 to 62).

**Rehearsals (sandbox, dirty tree, `--allow-dirty`; deleted afterwards; not results).** The B3b prereg command with
`--allow-dirty` (seeds derived from 202dd531, not from B3a) wrote its prereg in 41 s, needed 54 of 60 pipelines and
excluded one `claims_integrity` volume shadow world by the generator-capacity rule; `run --dry-run` on it printed the
estimate line (54 pipelines, 14 worlds, 11 shadow worlds). A one-seed run of both packs with every variant, attack, type
and simulated transform at the default B (explicit seeds 101 and shadow 102, n = 1000) exited 0 after 632 s with every
control holding. No attack outcome of a rehearsal is reported anywhere: the result is B3c's. **Correction (audit round
4):** that run was the whole experiment (every variant, attack, type and transform) at the pre-registered n, on the one
seed 101 (B3b's five seeds derive from the B3a commit), and a run exits 0 only after writing `x5.json` and
`leakage_section.md` (every label and the bar outcome); B3a's code also prints the bar outcome on its last line. The
preregs and the run's two output files were deleted without a sha256, the outcome was not recorded, and whether code or
rules changed after it was not recorded, so the outcome counts as seen and B3a's code, label rules, primary family and
bar were fixed after it. The B3a commit message's "No X5 prereg was made and no x5.json written" is wrong. LEAKAGE.md
section 12 ("Rehearsal disclosure") and the audit round 4 section below carry the disclosure.

**Merge notes.**

1. **No fabric change and no fabric integration point.** X5 reads G0's artifacts through their own modules.
2. **A new CLI**, `python -m mycelic.collective.experiments.x5_inference` (`prereg`, `run`), and a new run kind,
   `runs/x5/<id>/`.
3. **No prereg is invalidated.** No file of the X1, E2, E1 or openFDA-replay code-hash lists changed, so every B2a
   value holds and every earlier prereg still pins.
4. **The lab runs no X5.** Nothing under `lab/` or `.github/` changed and no lab request names X5.
5. **The B2b note.** B2b's pre-stated prereg is "made on the clean B2a tree", and the harness's `prereg` stamps
   `code_commit` = HEAD. After B3 lands HEAD is no longer B2a: `eval_code_hash` is unchanged, so a B2b prereg made
   later still pins the same evaluation code, but its `code_commit` would name a later commit than the one B2a's text
   states. B3 does not resolve this; the orchestrator decides.

**Deviations from the gate text and the design, all checked by reading the tree or by probes in `/dev/shm`
(sandbox, same-day; not product figures).**

- **Volume is four times, not ten.** The generator refuses ("narrative uniqueness exhausted") `claims_integrity` at
  ten times at its 34-week minimum and at five times at 93 weeks for 3 of 5 seeds, and `device_quality` at ten times at
  83 weeks; four times at the default world's weeks generated for 5 of 5 seeds in both packs (about 8,000 to 8,300
  records and 4,000 members per world). The prereg's `--volume-factor` defaults to 4; a seed whose volume world does
  not generate in every pack is skipped and recorded in `volume_excluded` ("generator capacity").
- **No domain literals in code, so rules instead of names.** The removed type is `mapping.primary_entity_type`
  (`device_quality` product, `claims_integrity` repair_shop; device `lot` is refused because the capa template uses
  `entity_ids:lot`); the A5 fields are the generator's person fields of kind `name`. `POSITIVE_EXPECTED` is the
  label constant, since the label word is also a device predicate.
- **Six sentence templates, not five.** The Bonferroni interval contains the 95% one, but a 95% interval above 0 can
  have a Bonferroni interval within [-0.05, 0.05], so (leak, at_chance) can occur; it has its own sentence, and it
  still fails the bar (the bar reads the 95% label).
- **The bar sentences do not quote the phrase.** They say "the claim that nothing leaks beyond text may not be made"
  instead of quoting "no leakage", so the docs never hold that phrase in an X5 result.
- **`x5.json` additions.** Each entry carries `family` (`primary` or `exploratory`); each world block carries
  `records` and `originals` beside members and non-members; `rules.selection` carries `packs`, because canonical JSON
  sorts object keys and `leakage_section` takes every order from a list; the prereg also records `stamps`, each pack's
  `min_window_weeks` and `k_settings`, and `cap.needed`. A covered fact is one per week and channel of a bundle's span.
- **Seeds.** The volume seeds are the first two seeds, in ascending order, whose volume world generates in every pack
  (so both packs share them); explicit `--volume-seeds` must be among `--seeds`.
- **A3 reads a verdict's support as a sum over both channels** (a record's claim for a key is in exactly one channel,
  `edge.extract.pair`). Where a site's judge and its extraction disagree that constraint can be off: it can make the
  attack less accurate (an empty interval falls back to the prior), never the evaluation wrong, since truth is the
  sites' own rows.
- **The tiny test fixture uses n = 400.** At n = 200 the device world had too few A4 pairs for the `k1_reference` A4
  control to be labelled `leak` at B = 100 (the run exits 2, as it should); 400 gives a fixture that holds every
  control.
- **A harness self-check that fails** (a recomputed detection that differs from the stored run, an A6 probe past its
  day cap, a world that no longer matches its digest during the run) exits 2 with `harness error:` and writes
  nothing.

**Pre-stated for B3b and B3c** (written after the rehearsals above, whose prereg and `x5.json` were deleted, and before
the B3b prereg; corrected in audit round 4, which also changed the X5 code and therefore its hash):

- **B3b**, on the clean B3a tree, with `TMPDIR=/dev/shm/b3/tmp`, then `cp` byte-identical to
  `docs/collective/x5/prereg.json`:

  ```
  python -m mycelic.collective.experiments.x5_inference prereg --packs device_quality,claims_integrity --n 1000 --bootstrap-seed x5-b3 --run-id x5-b3-prereg --runs-dir /dev/shm/b3/runs
  ```

  The defaults give five seeds and three shadow seeds from the B3a commit, volume factor 4, k settings 2 and 10
  (device buckets k2 [2, 10, 50] and k10 [10, 50]; claims k2 [2, 10, 50] and k10 [10, 50]), every variant, attack,
  artifact type and simulated transform, four A6 targets per site, B 10,000 (primary) and 2,000, and a cap of 60
  pipelines, of which 54 are needed.
- **B3c**, at HEAD = B3b on a clean tree, never `--allow-dirty`, in the background with a log in `/dev/shm/b3`, then
  `cp` of `x5.json` to `docs/collective/x5/x5.json` and `leakage_section.md` spliced between LEAKAGE's markers:

  ```
  python -m mycelic.collective.experiments.x5_inference run --prereg docs/collective/x5/prereg.json --run-id x5-b3 --runs-dir /dev/shm/b3/runs --work-dir /dev/shm/b3/work
  ```

  The first run is the result. If it exits non-zero or shows a harness bug: stop, record the exit code, the `x5.json`
  sha256 if written and the controls block here, and report; never re-run with changed code or settings and present
  that as the result. `/dev/shm/b3` is removed afterwards.

### B3b and B3c: what ran, and the result

- **B3b** (commit `a91fd21`): the prereg command above, made on `0856dde` rather than on the B3a tree, since audit
  round 4 had changed the X5 code (its merge note says the prereg must be made on a commit that contains it); clean
  tree, `allow_dirty` false; 54 of 60 pipelines needed. `docs/collective/x5/prereg.json`, sha256 `1836ce69...bc7f2f`.
- **B3c**: the `run` command above, once, on a clean tree without `--allow-dirty`, in the background with its log in
  `/dev/shm/b3`. Started 2026-10-08 22:35:38 UTC, finished 23:15:09 UTC (`timings.total_s` 2,232.8), **exit 0**.
  `x5.json` sha256 `00d9d1a264553d67c2b3b3f3a3eb23760bbc9a606fb8ed43b6f62b85266c5dcb` (839,230 bytes), content hash
  `a5d2f9f341bfc025ef6f24f9e3b0f545`, copied byte-identical to `docs/collective/x5/x5.json`; `leakage_section.md`
  sha256 `0cf03248eda78aa021edd9f2d1e71f51f8ab6030d2209b08ae18b809488cdd83`, spliced byte-identical between
  LEAKAGE's markers. `/dev/shm/b3` was removed afterwards.
- **A commit landed during the run.** HEAD was `a91fd21` when the run started; `d9d65c4` (a public-data probe under
  `tools/market/`, its test and the market workflow; nothing under `mycelic/`, `git diff a91fd21 d9d65c4 -- mycelic/`
  is empty) was committed at 22:38:16 UTC. The run reads `code_commit` when it writes `x5.json`, so `x5.json` names
  `d9d65c4` (its `prereg_code_commit` is `0856dde`); `code_dirty` is false and `code_hash` equals the prereg's, which
  the run checks before building any world. No other commit was made until the run had finished.
- **The result: the bar fails.** On both packs, membership inference (A1, calibrated) and predicate attribute
  inference (A2) leak at the 95% and the Bonferroni level: A1 advantage 0.971 (device_quality) and 0.956
  (claims_integrity), A2 0.701 and 0.676. Count inference inside the protected range (A3) and reporter linkage (A4)
  are at chance on both. Every control holds (10 positive, 12 negative). LEAKAGE section 12 carries every row.
- **Where the leak comes from** (exploratory rows of the same run): the cells. The two cell types alone give A1
  0.764 and 0.744 (device_quality, codes and text channels) and 0.657 and 0.800 (claims_integrity); verdicts,
  packets, follow-up, HQ results and run files are each at chance for A1. The allowed-fields reference (exact counts
  of the fields a central system may already receive) gives A1 0.763 and 0.656 and A2 0.362 and 0.277; the cells add
  A1 0.208 and 0.300 and A2 0.339 and 0.397 beyond it. The `'<k'` presence cells, STRATEGY 6.4's designed
  disclosure, are what section 12's design note said would be the cause if A1 or A2 leaked.
- **The measured knobs do not close it**: A1 on `all` is 0.957 at k2 and 0.975 at k10 (device_quality), 0.895 and
  0.965 (claims_integrity); `minus_type` 0.853 and 0.851; `volume` 0.899 and 0.916.
- **The simulated transforms** (not implemented in the Boundary): removing every cell below k (`drop_lt_k`) brings A1
  on the cells to chance and A1 on `all` to 0.073 (device_quality, still `leak`) and to chance (claims_integrity),
  with A2 on `all` at 0.018 and 0.026 (`leak`); but in these worlds it removes every cell but 3 of 16,323 and all
  16,292, so it would remove the detection signal with them. Four-week cells remove 4 to 5% of the cells and leave
  A1 on `all` at 0.958 and 0.941.
- **What may be said.** Text and person values did not leave (sections 1 to 11; A5 is at chance on every artifact type
  of every measured variant, 162 entries, and leaks only in the injected positive control, as designed). The
  counts that leave tell an attacker who already holds a candidate record whether that record is in a site's data,
  and which failure mode it carries, with high accuracy on these synthetic, same-author worlds. "Nothing leaks
  beyond text" may not be said. Closing this would be a code change (for example noise on presence, or a threshold
  that keeps detection), measured by a new X5 prereg; none is made here.

### Disagreements and residuals, for the reviewer

- **An unexpected exception exits 1** (Python's default), which is also the negative control's code; the harness
  catches its own refusals (exit 2) but not a crash. A crash prints a traceback and writes nothing.
- **The detection result is recomputed** with `detect(...)` on HQ's store and checked equal to the stored candidates;
  a mismatch is a harness error, never a result.
- **`run` names pin differences in two steps**: the code and pack hashes first (no world is built), then the copies'
  and the worlds' (after building them in the work directory).

## B4 (round 4, gate 4 of 4): a third field as a data-only pack (it_incidents)

B4 builds `it_incidents`, a built-in pack for the multi-site IT operations incidents of a fictional group (six
subsidiaries, English and German), **as data only**, and records what the data could and could not express. It is the
internal generality proxy of STRATEGY section 4.4: **same author as the generic code; internal only; not X3 by a
non-author; not a buyer claim.** The AI system that wrote the generic code wrote the requirements, the pack and these
tests, having read that code, so nothing here measures X3 (STRATEGY section 11.2) and no figure of it goes to a buyer.

### B4a and B4b

**B4a** (`b2bf466`, parent `1cee431`, the B3a commit) adds only `docs/collective/x3/REQUIREMENTS.md`: requirements
R01 to R27 of the field, written before any file of the pack existed (`git ls-tree -r b2bf466 --
mycelic/collective/packs/data/it_incidents` is empty). It is not edited afterwards; no requirement was added later
(`effort.json` `requirements_added_after_b4a` is empty).

**B4b** (the commit after B4a) adds the pack directory
`mycelic/collective/packs/data/it_incidents/` (copied from `device_quality`, then every file rewritten; the openFDA
mapping and the E2 plant spec deleted), `tests/mycelic/test_collective_x3.py`, the evidence under `docs/collective/x3/`
(`scenario.json`, `effort.json`, `g0/leakage.json`, `x1_smoke/{prereg,labels,scorecard}.json` and the six demo run
files under `demo/x3-it-incidents-demo/`), generalised loops in ten collective test modules, and this section, `PACKS.md`
sections 1 to 4 and `ARCHITECTURE.md` section 11.5.

**Zero code change.** `git diff --numstat 1cee431 -- mycelic demo ':(exclude)mycelic/collective/packs/data/it_incidents'`
is empty and so is `git ls-files --others --exclude-standard` over the same pathspec: no file under `mycelic/` or
`demo/` outside the pack directory was changed or added. No gap blocked the loader check, G0, the X1 smoke or the demo,
so none was fixed. `EffortTests` recomputes this.

**The it_incidents hashes** (`B4_HASHES` in `tests/mycelic/test_collective_pushdown.py`; `PACKS.md` section 2):

| Hash | Value |
|---|---|
| `config_hash` | `946becb0adece13f2274bf70eb33af81541a98e0587efbd43377c51b52cb29ac` |
| `vocabulary_hash` | `95f71002cfe225ff5e7254c7d3dec34d9aa0aad351fdc1312ea62d5152afc6b5` |
| `detector_hash` | `8dfb97e3ddc76743dc217da9e4cbd712e90f54322616780107f13f4f03b99ffb` |
| `fixtures_hash` | `f562c907631d25d9a7ccd64752df69d1bb5cbffcdac488f8a335818af45ac14e` |

**Unchanged hashes.** The code hashes equal B3a's: X1 (`harness.eval_code_hash()`)
`e7d5d82e3b9805358718b5927b6cca89bb4912c733fccd7f14c89f61a9234161`, E1 (`e1_extract.e1_code_hash()`)
`07c08295d7eeeff6eadcaa1db80ee5004b3dacf3ceb415c1e3ae126ca67ec229`, E2 (`e2_pushdown.e2_code_hash()`)
`7428e57c6c9e2b5f4e7d7a93d80a45bc4a06694a3d01b274c12617e69b1d32be`, the openFDA replay
(`openfda_replay.replay_code_hash()`) `452219e81aa711fb97387d8bc5da9663e7765af8f8f5b17ae6617e8acb0b9525` and X5
(`x5_inference.x5_code_hash()`) `dea481ca619129e1f42f685256834e81ec76e27995287fb21b54c33025d01816`; none of them hashes
pack data. `device_quality` and `claims_integrity` keep their G5 hashes with B1's config hash
(`test_every_builtin_pack_is_pinned`). The X1 smoke prereg's `code_hash` equals `harness.eval_code_hash()`.

**The pack, in short.** Entity types `it_service` (alias-only, eight services, English and German phrasings and
acronyms as aliases), `config_item` (host names `aaa-bb(bb)-nnnn`, lower case, **the first built-in type that never
leaves a site**: `egress` false, `entities.config_item` a never field, counted by `build_cells` as `non_egress_type`),
`software_release` (`REL/nnnn/nn`, with slashes), `vendor` (`VND-nnnn`, names as aliases) and `change_request`
(`CHG` plus seven digits); eleven failure-mode predicates and the generic `incident_unspecified`; codes `ITC-*` mapped
from tool labels; an export mapping with a journal filter (work notes and comments only); k 3, close lag 14 days;
`detectors.json` a byte copy of the template's; three rules, none on (`software_release`, `crash_after_update`);
follow-ups `evidence_packet`, `problem_record_draft` (owner `problem_manager`) and `vendor_escalation_draft` (owner
`vendor_manager`, proposable only on vendor keys); 46 hand-labelled fixtures; `fixtures/plant_smoke.json` (three
patterns, one decoy per class, a construction smoke, not blind, never a result). No pack term had to be renamed
(`effort.json` `term_renames` is empty); the data choices made to fit the generic code are `fit_to_code_choices`.

### B4 effort and evidence (pointers)

Every effort and evidence figure is in the committed files; this table only points at them (`PointerTests` resolves
each row).

| Pointer | What it holds |
|---|---|
| `docs/collective/x3/effort.json#/totals` | files, lines and bytes of the pack directory, and its lines changed against the template |
| `docs/collective/x3/effort.json#/files` | the same per file |
| `docs/collective/x3/effort.json#/code_lines_changed` | code lines changed outside the pack directory |
| `docs/collective/x3/effort.json#/code_numstat` | the numstat those lines come from |
| `docs/collective/x3/effort.json#/test_lines_added` | test lines added and deleted, per test module |
| `docs/collective/x3/effort.json#/docs_lines_added` | doc lines added and deleted under `docs/collective/*.md` |
| `docs/collective/x3/effort.json#/requirements` | per requirement: status and how |
| `docs/collective/x3/effort.json#/gaps` | the generality gaps |
| `docs/collective/x3/effort.json#/presentation_gaps` | device nouns in the demo's screen texts |
| `docs/collective/x3/effort.json#/logic_gaps` | demo checks whose meaning does not fit the field |
| `docs/collective/x3/effort.json#/fixture_lexical_f1` | the lexical extractor on the fixtures (hand-labelled by the same author; not a measurement) |
| `docs/collective/x3/effort.json#/fixture_disagreements` | the fixtures where it differs from the labels |
| `docs/collective/x3/effort.json#/attempts` | every G0, X1 smoke and demo attempt |
| `docs/collective/x3/effort.json#/wall_clock` | the AI agent's wall clock per milestone, not engineer-hours |
| `docs/collective/x3/g0/leakage.json#/hits` | G0's canary hits (1,000 records, seed 11) |
| `docs/collective/x3/g0/leakage.json#/shingle_overlap_bytes` | G0's narrative overlap |
| `docs/collective/x3/g0/leakage.json#/positive_control` | the scanner's positive control |
| `docs/collective/x3/x1_smoke/scorecard.json#/channels` | the X1 construction smoke per channel (synthetic, same author, not a measurement) |
| `docs/collective/x3/x1_smoke/scorecard.json#/x1` | why the smoke does not count as X1 |
| `docs/collective/x3/demo/x3-it-incidents-demo/scorecard.json#/checks` | the demo's checks |
| `docs/collective/x3/demo/x3-it-incidents-demo/scorecard.json#/hero/detection` | X, S, R-mf, U and single site on the demo's hero key |

### Gaps, by name

**Generality gaps** (`effort.json` `gaps`; each symptom is reproduced by `GapTests` on a copy of the pack; none
blocking, none fixed): G4-01 a separator other than `-` or `/` (addresses, FQDNs, host:port; R03); G4-02 dotted
versions (R04); G4-03 two-hop links (R06); G4-04 sub-day timestamps (R10); G4-05 priority as a field or a code (R09);
G4-06 an alias-only id as its own acronym (R05); G4-07 change windows (R07); G4-08 alert storms in the background world
(R11); G4-09 a rising generic-code rate in the background world (R15); G4-10 locales as languages (R26); G4-11 HTML
paragraphs as one sentence (R18); G4-12 German separable verbs, G4-13 unlisted inflections and compounds, G4-14
negation beyond the token window (all R13); G4-15 sentence-level pairing (R17); G4-16 per-subsidiary host universes
(R02); G4-17 the generator's ticket-number form (R01); G4-18 forwards re-keyed without their origin (R12).

**Presentation gaps** (`effort.json` `presentation_gaps`, recomputed by `EffortTests` from the files): device-field
nouns ("plant", "complaint") in `demo/collective/screen.py` (`SINGLE_NO_LATER`, `SINGLE_LATER`, `NOT_ASKED`,
`PREPARING`, `TASK_TEXTS`, `ROLE_TEXTS` and texts of `_problem`, `_check` and `_followup`) and in
`collective_demo.CHECK_TEXTS`; `console.html` holds none. The IT demo's screen therefore says "plants" for
subsidiaries and "complaints" for tickets. Not fixed in this gate (the brief: never fixed here); the number lint passes.

**Logic gaps**: none; every check of the demo is ok.

### Evidence runs, allow-dirty and attempts

- **Loader check**: `python -m mycelic.collective.packs.loader check it_incidents` printed `pack: it_incidents 0.1.0
  (source builtin)` and the four hashes above.
- **G0**: `--pack it_incidents --records 1000 --seed 11` passed; `leakage.json` is copied byte-identical. Its
  `known_limitation` is empty (the pack has `require_master_data` on).
- **X1 smoke**: `prereg`, `check-plant --construct` and `run` (run ids `x3-smoke-prereg`, `x3-smoke-run`, one seed,
  104 weeks, evaluation weeks 26 to 103) each exited 0. **The pack was untracked when it ran, so the harness needed
  `--allow-dirty`**: the prereg and the scorecard stamp it (`allow_dirty` true, `code.code_dirty` true). It is a
  construction smoke on a same-author plant, not an evaluation and not X1. `prereg.json` and `labels.json` are
  byte-identical copies; in `scorecard.json` only `paths` was rewritten to repository-relative or `<runs-dir>/...`
  strings (`paths` is outside `content_hash`, which recomputes unchanged). **In this smoke X did not beat single-site
  or U**: X recalled fewer of the planted patterns than single-site and U, which recalled all of them, and S and R-mf
  recalled none (`/channels`). On a same-author plant that says nothing about X1 either way.
- **Demo**: `docs/collective/x3/scenario.json` (schema version 1 keys only) puts a desktop-client release
  (`software_release`) crashing after an update, written only in ticket narratives in English and German, at three
  subsidiaries, with a sibling, a generic-category rise, a marked echo and an alert storm as decoys. Its illustration
  says it is a constructed illustration for an internal generality check of a third field, not evidence that codes
  miss such cases. `--record` passed every check and `lint_numbers.py` passes on the committed copy; a fresh
  `--record` reproduces its `content_hash` (`DemoTests`).
- **Attempts**: one G0 trial at 200 records before the evidence run and two evidence runs; two X1 smoke attempts;
  four demo recordings, every check ok each time: a trial from a scratch copy of the scenario, the same bytes from
  `docs/collective/x3/scenario.json` (the same `content_hash`), a run after two decoy labels were reworded to hold no
  number word (the lint had passed, since decoy labels are run-file values, not static screen text), and the committed
  run. The second G0 evidence run, the second X1 smoke attempt and the fourth recording were made in review round 1,
  after fixture `INC0100045` was relabelled (below): the fixtures hash changed, the X1 prereg and scorecard and the
  demo scorecard carry it, and a generated world's parameters carry it too, so G0's `world_digest` changed. G0's
  result, the X1 channel results and the demo's checks are the same as before. All are listed in `effort.json`
  `attempts`.
- **Review round 1 changes to the evidence**: fixture `INC0100045` (a certificate expired on a host overnight, "with
  no impact on the mail service") had labelled the mail service with a negated certificate expiry; the text negates
  an impact, not an expiry, so the mail service is now an entity-only claim (predicate null). The lexical extractor
  still disagrees (it gives the mail service the expiry), so the disagreement stays, now under R17 (G4-15). R03 is
  `not_expressible` (addresses, host:port pairs and fully qualified host names cannot be recognised at all; they stay
  in only because the narrative never leaves) and R05 `approximated` (the ids are descriptive because an id cannot be
  its own acronym, G4-06). `wall_clock` `end` is the end of the review round's revision, so the span from
  `evidence_done` to `end` includes the time the work waited for review.

### Scope and the shallow-clone behaviour

**B2's sealed X1 stays scoped to `device_quality` and `claims_integrity`.** `docs/collective/x1/PLANTER_BRIEF.md`
(frozen at B2a) names "the two built-in packs" and those two; so do `RESULTS_TEMPLATE.md`, `b1/PREREG.md`, LEAKAGE
section 12 and the B2 and B3 RUNBOOK sections. None was edited. `it_incidents` is outside B2's and B3's frozen scope;
`LoopCoverageTests` excludes `test_collective_x1_sealed.py` and `test_collective_x5.py` by name for that reason.

**The git checks of `EffortTests`** compare base `1cee431` with the commit that added `docs/collective/x3/effort.json`
(`git log --diff-filter=A --format=%H -- docs/collective/x3/effort.json`), or with the worktree plus its untracked
files while that commit does not exist yet, so later commits never change them. In a shallow clone without the base
commit (the main CI's `checkout@v4` fetches depth 1), they assert that `git rev-parse --is-shallow-repository` prints
`true` and then check only the internal consistency of the recorded numbers.

### Test counts (sandbox timing, not a product figure)

Both suites with `TMPDIR` on tmpfs and `nats-server` on the path; the 1cee431 column is the same command on an export
of that commit (B3a), run in this sandbox the same day, directly after the B4b run. Both runs in the table had the
sandbox to themselves and are review round 1's.

| Command | 1cee431 | B4b (this worktree) |
|---|---|---|
| `python -m pytest tests/mycelic -q -p no:warnings` | 1122 passed (27,940 subtests), 0 skipped, in 754 s | 1158 passed (39,772 subtests) = 1122 + 36 new, 0 skipped, in 806 s |
| `python -m pytest NeuralGraph/tests -q -p no:warnings` | 222 passed, 1 skipped (119 subtests) in 5 s | 222 passed, 1 skipped (119 subtests) in 4 s; the skip is the pre-existing one |

The suite takes 53 s longer (806 s against 754 s). Run-to-run noise here is of the same order: the first round's runs
gave 752 s and 728 s for 1cee431 (the second while other work shared the sandbox) and 814 s for B4b, so the added time
is known only to within that noise. The 36 new tests are 32 in `test_collective_x3.py` (18 s on its own, 11 s of it
the fresh demo recording of `DemoTests` and 2 s the small G0 run of `PipelineTests`), the three `LoopCoverageTests` in
`test_collective_guards.py` (2 s) and `test_every_builtin_pack_is_pinned` in `test_collective_packs.py`. The ten
renamed tests (below) are counted once each, not as new. The 11,832 further subtests are the generalised loops running
on `it_incidents` too and the subtests of the new tests. No test was skipped, removed or weakened.

In the first round, a full run on this worktree ended with 1 failed and 1157 passed:
`EffortTests::test_closed_header_statement_and_labels` failed on a provisional `effort.json` whose `end` had been logged
before a re-logged `evidence_done`. The test was right; `end` was logged again and `effort.json` regenerated. In
review round 1 the full runs above were made before the last edits, which were a docstring in
`test_collective_guards.py` and this section's figures; `effort.json` was regenerated after them, and
`test_collective_x3.py` and `test_collective_guards.py` were run again (both pass).

### Earlier tests whose expectation changed

- `test_collective_guards.py`: `test_terms_cover_both_packs` became `test_terms_cover_every_builtin_pack` (each
  built-in pack's own terms are non-empty and in the scanned set, plus the old assertions); `pack_terms` reads every
  built-in pack; new `BUILTIN_PACKS` and `LoopCoverageTests` (the generalised collections and tables equal the built-in
  packs; an AST scan finds every for loop or comprehension over a literal tuple, list or set whose elements name two
  or more distinct built-in packs anywhere inside them, as bare ids, table rows, call arguments or module-level names
  bound to a pack, and each one must be in `PACK_SPECIFIC_LOOPS`, keyed by file and the qualified name of its
  enclosing function, with its reason). Review round 1 found that the scan's first version looked only at a row's
  first element, so it missed two literal loops of `G0RunnerTests` in `test_collective_leakage.py`; the scan now
  walks every element, and its self-test flags rows like `("dq-on", "device_quality")` and calls like
  `args("claims_integrity", ...)`.
- Generalised to every built-in pack (each loop or table now also runs on `it_incidents`): `test_collective_packs.py`
  (`PACKS`, `EGRESS_EXPECTED`, the hash subprocess, new `test_every_builtin_pack_is_pinned`),
  `test_collective_extract.py` and `test_collective_e1.py` (`PACKS`, `WORLDS`; `test_claims_integrity_reduced_smoke`
  became `test_reduced_smoke_on_every_other_builtin_pack`, with the exact-match types read from each pack and still
  `[clinic, repair_shop]` for the claims pack), `test_collective_edge.py` and `test_collective_leakage.py` (`PACK_IDS`,
  `KEY`; `test_every_class_on_both_packs` renamed), `test_collective_pushdown.py` (`PACKS`, template coverage, the
  judge on fixtures and on a world, the G0 pushdown-stage runs;
  `test_both_packs_pass_with_every_pushdown_artifact_scanned` renamed), `test_collective_detect.py` (`PACKS`, `VOCAB`,
  new `DETECTOR_DEFAULTS`; `test_both_packs_load_with_the_documented_values` and `test_generated_worlds_of_both_packs`
  renamed), `test_collective_followup.py` (the G0 follow-up-stage runs and the E5 smoke over `E5_INJECTIONS`;
  `test_both_packs_pass_with_every_follow_up_artifact_scanned` renamed), `test_collective_evaluate.py`
  (`SMOKE_FIXTURES`; `test_both_smoke_fixtures_parse_and_check` became
  `test_every_builtin_smoke_fixture_parses_and_checks`; the stale-chain test and the plant-file listing test),
  `test_collective_leakage.py` `G0RunnerTests` (the master-data-on G0 run of every built-in pack, `ON_RUNS`, one more
  1,000-record run for `it_incidents`; `test_master_data_on_for_both_packs` became
  `test_master_data_on_for_every_builtin_pack` and `test_run_files_stage_both_packs` became
  `test_run_files_stage_every_builtin_pack`).
- Unchanged and pack-specific (`PACK_SPECIFIC_LOOPS`): the hash-history tests of the two original packs, the G6
  pushdown values and follow-up settings, the device pack's supplier-keyed draft, the store-mismatch cases, and
  `G0RunnerTests`' single-pack option cases (master data off on the device pack, the claims pack's default, lexical
  mode).

### Merge notes

1. **The lab is untouched.** Nothing under `lab/` or `.github/` changed; the new pack is simply available to any
   harness that takes `--pack`, and the cloud lab can run its fixtures as they are.
2. **Existing hashes are unchanged**, code and pack (above), so no prereg is invalidated and no recorded run changes;
   `demo/collective/recorded/**` and `demo/collective/scenario.json` were not touched.
3. **Suites take longer**: every generic per-pack test now also runs on `it_incidents` (one more G0 run in each of the
   pushdown and follow-up stage classes and in `G0RunnerTests`, one more E5 smoke, one more E1 reduced smoke, more
   worlds), plus
   `test_collective_x3.py` (one demo recording, one small G0 run, an in-test prereg). The measured time is above.
4. **A merge that adds a fourth built-in pack** fails `LoopCoverageTests` until the pack's rows are added to the
   generalised tables, and `test_every_builtin_pack_is_pinned` until its hashes are pinned.

### Disagreements and residuals, for the reviewer

- **No numeric floor on the fixtures' lexical F1.** A floor would pull labels toward the extractor; the fixtures are
  labelled from the text and the five disagreements are recorded with their gaps (`fixture_disagreements`).
- **The background world cannot show storms or a rising generic category** (G4-08, G4-09); the X1 plant and the demo
  scenario construct them. The generator's ticket numbers are not INC numbers (G4-17), while the fixtures and the
  export test use INC numbers.
- **Presentation gaps are left as they are**: the demo's screen speaks of plants and complaints for an IT group. A
  generic fix (pack-supplied site and record nouns) would change `screen.py`, which this gate may not.
- **The demo's background is the pack's generic world**: R-mf does not catch the hero key, but its related alerts hold
  a generic-code key of one of the hero's services at rank 1 (`hero/detection` in the demo scorecard). The IT scenario
  is an illustration for this generality check, not B1's codes-miss case.
- **`effort.json` is evidence of the same author's work**; the wall clock is an AI agent's, not engineer-hours, and
  says nothing about X3.

## Audit round 4 (three confirmed findings)

**Base.** Branch `mycelic-collective-phase2` at 499557a (B4b), a clean worktree. Nothing is committed by the engineer.
Gate B1's second step (the codes-miss scenario) and B3's B3b and B3c are not part of this round.

**Scope: fabric files changed: none.** Changed: `mycelic/collective/experiments/{x5_attacks,x5_inference}.py` (the
only code), `docs/collective/{ARCHITECTURE,INTEGRATION,LEAKAGE,RUNBOOK}.md`,
`docs/collective/evidence/superseded/collective-halvern-g10/README.md`, `demo/collective/{README,SCRIPT}.md` and
`tests/mycelic/test_collective_{x5,demo,x3}.py`. Unchanged: every other file under `mycelic/` and `demo/`, the
recorded run, every pack, `docs/collective/b1/PREREG.md` and the B4 evidence under `docs/collective/x3/` (its
`effort.json` keeps the code hashes B4b recorded).

| # | Finding | Fix | Regression test |
|---|---|---|---|
| 1 | B1's constructed codes-miss illustration does not exist (only B1a was committed), but the superseded run's README, ARCHITECTURE 17.9, the demo README and the B1 plan described it as present or as being added | Every page a reader sees says so: "B1's constructed codes-miss illustration has not been built" (the superseded README names only the B1a run, whose own scorecard still shows R (model-free) flagging a key of the case in X's week; the demo README and SCRIPT say no committed run shows a case the allowed fields miss, and SCRIPT tells the presenter never to refer to one; ARCHITECTURE 17.9 and INTEGRATION B1 carry the status) | `CodesMissStatusTests::test_the_pages_say_whether_the_codes_miss_illustration_exists` (while no `scenario_codes_miss.json` or `b1/attempts/` exists: the five pages carry the sentence, none carries the four overclaiming phrases, and `recorded/` holds only `collective-halvern-b1a`; once one exists, the pages other than this log must drop the sentence), `test_every_recorded_run_a_page_names_exists`; it fails on HEAD's pages |
| 2 | A full X5 rehearsal (both packs, every variant, attack, type and transform, n = 1000, seed 101) exited 0 before B3a froze the code and the bar, so it wrote `x5.json` with every label and the bar outcome; the commit message said no `x5.json` was written, the plan said it was written before any `x5.json` existed, LEAKAGE said the bar was fixed before running, and no sha256 or outcome was kept | LEAKAGE section 12 gains the "Rehearsal disclosure" above the result markers (what ran, that the outcome counts as seen, that nothing was recorded, that the result is pre-registered only with respect to the B3b prereg's own seeds, that the commit message is wrong, and this round's own code change) and drops "fixed before running"; INTEGRATION B3a gains a correction and the plan's heading no longer claims to precede every `x5.json`; the prereg's note "frozen ... before any attack outcome exists" is replaced by `REHEARSAL_NOTE`; RUNBOOK 18 gains "A rehearsal is an outcome" (record sha256 values and the outcome before deleting, and disclose) | `LeakageSectionTests::test_the_rehearsal_before_the_freeze_is_disclosed` (the disclosure's facts, its place before the markers, the prereg notes of a fresh prereg, the INTEGRATION correction, the RUNBOOK rule, the removed phrases) |
| 3 | A1's per-artifact-type and reference entries used one threshold calibrated on the union of the shadow cells and divided by keys of a channel the type cannot carry, so a single-channel type could never score a mixed-channel member 1: the per-type and reference entries understated membership, the incremental entry overstated what HQ's artifacts add | A score counts only the keys of the channels the attacked type carries (`x5_attacks.A1_CHANNELS`: codes for `cells_codes` and the allowed-fields reference, text for `cells_text`); `shadow_stats` calibrates one threshold per (setting, type) on that type's own shadow facts (`a1_threshold_types`: the cell types, `all` from the shadow cells, and at a pipeline variant's own setting the reference from R (model-free)'s cells of the shadow members and `allowed_plus_all`); `evaluate` takes the per-type thresholds; `A1_calibrated` is not applicable to a type with no shadow analogue (`A1_SHADOW_REASON`: verdicts, packets, follow-up, HQ results and run files, which only `A1_fixed` reads); `x5.json`'s `shadow.a1_thresholds` is per setting and type; the prereg's `a1_threshold_rule` says all of it; LEAKAGE 12, ARCHITECTURE 18.5, 18.6 and 18.10 | `A1PerTypeTests` (five tests: the channels and a mixed-channel member scoring 1 only with them; the applicability of both A1 forms on every type; `evaluate` applying each type's own threshold and channels and refusing a borrowed one; `shadow_stats`' calibrate inputs, call by call, equal to scores recomputed on each type's own shadow facts for both packs, with every keyed member scoring 1 on its own codes cells; the tiny run's per-type threshold block, the not-applicable entries and the schema refusing a single number) |

**Hashes.** The X5 code hash (`x5_inference.x5_code_hash()`) changes from
`dea481ca619129e1f42f685256834e81ec76e27995287fb21b54c33025d01816` (B3a, B4b) to
`73a4e487727a7e665301d499a7d5d9c66f7a9b90c81e1445a72ce9bd49d03516`. X1 `e7d5d82e...`, E1 `07c08295...`, E2
`7428e57c...`, the openFDA replay `452219e8...` and every pack hash are unchanged (`EffortTests` and
`test_every_builtin_pack_is_pinned`).

**Test-fixture probe (sandbox, test settings; a diagnostic, not a result).** The tiny run of `test_collective_x5.py`
(`device_quality`, n = 400, seed 11, shadow seed 12, variants default, `k1_reference` and `a5_injected`, B = 100) was
run once on HEAD's code (an export of 499557a under `/dev/shm`) and once on this round's tree; both run directories
are deleted. Of the 252 results entries, 234 are identical. The 18 that differ are `A1_calibrated` and `A1_fixed` on
`cells_codes`, `cells_text`, `allowed_fields_reference` and `allowed_plus_all` (default), the same two on the two
cell types of `k1_reference`, and six `A1_calibrated` entries now not applicable; the two simulated transforms differ
only in their A1 cell-type entries. Every `all` entry, the primary entries, the controls and the bar are identical. On
the default variant (474 A1 targets), `A1_calibrated` accuracy on `cells_codes` moved from 0.608 to 0.892 (coverage
0.985 to 0.785: records with no codes key now take the coin), on `cells_text` from 0.593 to 0.859 (coverage 0.749), on
the reference from 0.608 to 0.892, and the incremental entry of `allowed_plus_all` from 0.755 (95% CI 0.699 to 0.817)
to 0.186 (0.128 to 0.247). Every calibrated threshold of that run is 1.0, except 0.0 at `drop_lt_k`, the same values
the union gave: once a type scores only its own channel's keys, a member's keys are all present in its own cells, so
the channel rule removes the cap and the per-type calibration keeps any type from borrowing another's threshold.
These figures come from the probe's own `x5.json` files; they are a test fixture's, quoted only to show the fix.

### Earlier tests whose expectation changed

Changed, never weakened:

- `test_collective_demo.py`: `SupersededEvidenceTests::test_the_readme_says_why_and_points_into_the_scorecard`
  required the phrase "superseded by the B1 runs", which named runs that do not exist (finding 1); it requires
  "superseded by the B1a run".
- `test_collective_x3.py`: `EvidenceTests::test_the_code_hashes_are_the_pinned_ones` required the live X5 hash to
  equal B4b's; it now requires `X5_CODE_HASH_R4`, asserts that it differs from B4b's, and still requires `effort.json`
  to hold B4b's hashes, which this round does not touch.

### Merge notes

1. **No fabric change and no fabric integration point.**
2. **The X5 code hash changes** (above). No X5 prereg exists in the repository, so none is invalidated, but the
   pre-stated B3b ("on the clean B3a tree") can no longer use the B3a tree: the B3b prereg must be made on a commit
   that contains this round, and its seeds then derive from that commit (`SEED_RULE` reads `code_commit`). That is
   the orchestrator's call.
3. **`x5.json` and prereg shape.** `shadow.a1_thresholds` maps each cell setting to an object of per-type
   thresholds; `A1_calibrated` is `not_applicable` on six types; the prereg's `a1_threshold_rule` and `notes` changed.
4. **No recorded run changes**: the demo's committed run, its lint and the superseded evidence's six files are
   byte-identical; only Markdown around them changed.
5. **The lab is untouched.**

### Disagreements and residuals, for the reviewer

- **Finding 1: `docs/collective/b1/PREREG.md` is not edited.** It says it is never edited after B1a; its "written in
  B1b" is the plan it fixed in advance. Every page that points to it now says the illustration has not been built.
  The b1a run's screen and scorecard were not re-recorded: they describe that run truthfully (R flags the product in
  X's week) and name no B1b. Building the illustration itself is the rest of gate B1, not this fix.
- **Finding 2: the B3a commit message is not rewritten** (no history rewriting); INTEGRATION B3a and LEAKAGE 12
  correct it. Whether anyone read the rehearsal's bar line cannot be established now, so the disclosure treats the
  outcome as seen. Nothing in code stops a future rehearsal; the RUNBOOK rule makes one a recorded, disclosed event.
  This round's own X5 runs were the two tiny-fixture runs above (test settings, one device world, B = 100), recorded
  here; no X5 run at the pre-registered size was made.
- **Finding 3: channels and calibration together.** Calibration alone (the review's probe: thresholds 0.167 and 0.25)
  would leave `A1_fixed` capped, since it needs a score of 1. With the channel rule, `cells_text` gives 0.859 on the
  fixture where the review's unrestricted per-type threshold gave 0.863: records with no text key take the coin here,
  while a low threshold over every key still reads some of them, so the restricted form is not stronger on every type
  (the difference is within the fixture's noise). The reference's incremental entry is 0.186, against the 0.245 the
  review estimated with calibration alone.
- **Finding 3: membership on the non-cell types is measured by `A1_fixed` only**, which can understate it. Calibrating
  them would need a pipeline per shadow world and variant; the prereg's cap is 60 pipelines and B3b needs 54, so this
  round does not add them. `all` is still calibrated on the shadow cells, its only part with a shadow analogue; the
  review found the primary family unaffected, and the fixture's `all` entries are unchanged.

## B1b: the constructed codes-miss scenario (attempt 1)

Built directly by the orchestrating session (the planner, engineer and reviewer agents were unavailable: their weekly
limit was reached), exactly as `docs/collective/b1/PREREG.md` fixed it before any codes-miss world existed. The PREREG
is not edited. ARCHITECTURE 17.10 describes the mechanism.

**What was added.** `demo/collective/scenario_codes_miss.json` (the PREREG section 3 cast); in `scenario.py` the
optional `codes_miss` block, the background shift, the realism checks R1 to R8 and `build_world(..., without=)`;
`demo/collective/codes_miss.py` (the rule verbatim, the robustness seeds, the grid, the cooldown replay, the gate);
the engine evaluates the rule when it prepares and applies the gate when it writes the scorecard; the scorecard's
`codes_miss` block on its own closed schema; the screen's statement, author note, shift and verdict blocks; the CLI's
`codes miss:` lines; `tests/mycelic/test_collective_codes_miss.py`.

**Nothing computed alerts on the codes-miss digest before attempt 1.** The tests parse and build the scenario and its
in-test copies (each breaking one constraint) without detection, run the rule on synthetic detection results, and
read the committed attempt files. The real `detect_only` path was exercised once, before the attempt, on the first
(Halvern) scenario, which is not a codes-miss digest.

### Attempts

| # | Scenario digest | What changed, and why | Outcome, from its own scorecard |
|---|---|---|---|
| 1 | `c534c9b76d956daad5bea2b274f41e66` | Nothing: the pre-registered cast | **Does not hold.** X caught the hero (`x_caught` true, first alert `2024-W32`, the hero's first week is `2024-W31`), gate `supported`; R (model-free) and S both have an attributed alert of `lot:L10002:malfunction_unspecified` in `2024-W31` (rank 1 and 2), so `holds_main` false and `holds` false. `robust_holding` 4 of 8 (`holds_robust` true), `grid_holding` 1 of 6, `holds_strict` false. |
| 2 | `5a92c52827c79f43174fbd268402db3d` | The hero lot, `L10002` to `L20045` (product `SD-12` by the generator's link), by a rule stated in `attempt-2/PLAN.md` before any alert: the lot R3 allows at all three hero plants with the most background records there (42 against 12 and 10). Why: attempt 1's R and S flagged the low-volume lot under the generic code in the hero's first week. Plan pushed in `5a15a99` before recording. | **Does not hold, and attempt-fatal.** X caught the hero (rank 1, `2024-W32`), but the gate is `stale` ("the newest confirming week 2024-W36 ended more than 42 days before 2024-10-28"); R has an attributed alert of `product:SD-12:malfunction_unspecified` in `2024-W31`; S's first attributed alert is `2024-W34`. `robust_holding` 4 of 8, `grid_holding` 1 of 6; 8 of 12 run checks pass (the gate and the three follow-up checks fail). |
| 3 | `5578b5500d2edc4001d9a635b10a61df` | Against the pre-registered cast, the hero start week 30 to 32 (the shift follows by R8, 24), by a rule stated in `attempt-3/PLAN.md` before any alert: the earliest start week whose last week ends within the gate's 42 days of the as-of date (50, 43, then 36 days). Why: attempt 2's gate went stale on the hero's own records. The lot returns to `L10002`, since attempt 2's lot change did not hide the case from R. Plan pushed in `5f85e21` before recording. | **Does not hold (final).** X caught the hero (rank 1, `2024-W34`, a week after its first week), gate `supported`, 12 of 12 checks; neither R nor S has an attributed alert by X's week (`holds_main` true), but R held the hero's lot and product cooling in X's week (`strict_no_later` true for both), and `robust_holding` is **0 of 8** (`holds_robust` false), so `holds` and `holds_strict` are false. `grid_holding` 1 of 6. |

Each attempt is in `docs/collective/b1/attempts/attempt-<n>/` (scenario bytes, the six run files of its first
recording and a README read from its scorecard; attempts 2 and 3 also a `PLAN.md` committed and pushed before any
alert on their digest). The three attempts the PREREG allows are used, and **none illustrates the case**. The committed
scenario is attempt 3's, byte-identical. **What every page now says:** the scenario exists, no attempt illustrates the
case, and no committed run shows a case the fields allowed to leave miss; on this constructed world the structured
fields, filled at the generator's rates, carry the case about as early as X does (across the 42 logged worlds, R or S
flags a key of the case within about two weeks of X in most of them).

**Why it fails, as far as the scorecard shows.** R reads record-level counts of the allowed fields with no
k-suppression, and the hero's lot field is filled in 60% of the hero records (R4 fixes the generator's rate), so the
hero's lot under the generic code rises visibly in the case's first week despite the intake form writing that code on
every complaint. X's own key first alerted a week later.

### Checks

- The first scenario's recorded run re-records with the same content hash (`8e851b4b796fc72165880791866ad11e`); its
  screen, trace and leakage files differ only in the recording time and the timings' byte counts.
- `python demo/collective/lint_numbers.py docs/collective/b1/attempts/attempt-1`: ok.
- `tests/mycelic/test_collective_codes_miss.py` (new), `test_collective_demo.py` and `test_collective_guards.py` pass;
  `codes_miss.py` is on the determinism list.

### Earlier tests whose expectation changed

- `CodesMissStatusTests::test_the_pages_say_whether_the_codes_miss_illustration_exists` (audit round 4) now takes its
  "built" branch: the scenario file and `b1/attempts/` exist, so the pages other than this log drop "has not been
  built", as that test was written to require.

### Disagreements and residuals, for the reviewer

- **Agent review is owed.** This gate was built without the reviewer agent; the review runs when the agents are
  available again.
- **The illustration does not exist, and the PREREG allows no further attempt.** A new codes-miss illustration would
  be a new pre-registration, not an edit of this one; the evidence from the 42 logged worlds says a case the allowed
  fields miss needs structured fields that are absent or wrong in the case's records, which R4 (the generator's own
  fill rates) rules out here. Whether real cases look like that is N1's question.
- **Attempt 2's lot rule looked only at background volume.** It did not foresee R flagging the product instead; that
  is recorded, not corrected, since the attempt is logged as made.
- **The robustness and grid variants run with the main run's routing.** With the stand-in (no `--routing`) that is the
  lexical handler everywhere, as here; with a model every variant calls it too (26 detection-only prepares).
