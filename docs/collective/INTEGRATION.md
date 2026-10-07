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
