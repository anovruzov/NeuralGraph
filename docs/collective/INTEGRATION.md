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
raised no X alert; the single reporter and high base rate decoys alerted with their flags set; the unmarked copies
alerted in X. `claims_integrity` (built-in, seed 5): X found 2 of 3 (the rate-2 pattern below k=5 was not found), S 0
of 3, R (model-free) 0 of 3, U 3 of 3, single_site 3 of 3. The lifts over three patterns and one seed are not
interpretable (the scorecard warns), and none of these figures may be shown to anyone outside the team. Re-run with
the review fixes (a no-plant control per seed; single_site counted only at a planted site; "Review fixes" at the end
of this file), same labels: `device_quality` X 3 of 3 (control 0, net 3), S 0 of 3, R 0 of 3, U 3 of 3 (control 0,
net 3), single_site 3 of 3 (control 2, net 1); `claims_integrity` X 2 of 3 (control 0, net 2), S 0, R 0, U 3 of 3
(control 0, net 3), single_site 3 of 3 (control 1, net 2). Two of single_site's three device finds, and one of its
three claims finds, also happen in the world without the plant: they were chance finds.

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

**A fictional company, synthetic data and a constructed illustration; internal and YC use only; never a
measurement.** G8 runs the loop end to end for one fictional multi-site device maker (Halvern Medical, six plants in
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
| 2 | X1 credits "found" without the plant; single_site at any site | A no-plant control world per seed through the same pipeline; `control_found`, `found_net`, `recall_net` everywhere, lifts on net found; single_site counts only at a planted site | `test_single_site_matches_the_key_only_at_a_planted_site`, `test_a_find_in_the_no_plant_control_is_a_chance_find_and_not_net`, `test_the_no_plant_control_runs_per_seed_and_its_finds_are_not_net` |
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
