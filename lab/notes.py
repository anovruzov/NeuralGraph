"""Every fixed sentence the lab writes into a record, a status line or a workflow command.

No constant here holds an ASCII digit (a guard test checks it): numbers and identifiers that contain digits (unit
ids, run ids, thresholds, counts) are inserted where a sentence is rendered, and G3 summaries put identifiers in
backticks. The keys of :data:`CLASS_REASONS` and :data:`NOTES` are what records store; the sentences are what a
reader sees.
"""
from __future__ import annotations

PLUMBING_BANNER = "Plumbing check: a fake model answered every call. These records test the lab, not any model."
PLUMBING_HOSTED_BANNER = ("Plumbing check: a fake model answered every call except any hosted unit's calls, which "
                          "go to the configured host and are billed by it. These records test the lab, not any model.")
PLACEHOLDER = "placeholder: fill in"

# unit and shard outcomes
BUDGET_EXHAUSTED = "shard budget exhausted"
SHARD_INTERRUPTED = "shard interrupted"
HARNESS_INTERRUPTED = "harness interrupted by a signal"
LOW_PARTICIPATION = "the model answered too few calls"
NO_MODEL_CALLS = "no model call was recorded for a required task"
E3_FAILURES = "too many failed requests"
RESULT_MISSING = "harness result file missing or unreadable"
RESULT_CONTRADICTS_EXIT = "harness result contradicts its exit code"
UNEXPECTED_EXIT = "unexpected harness exit code"
HARNESS_USAGE = "the harness refused its configuration"
KILLED_BY_SIGNAL = "harness killed by a signal"
TIMED_OUT = "unit timed out"
LAB_ROUTING = "the lab wrote an invalid routing file"
NOT_PREPARED = ("not prepared: run lab.shard prepare for this shard first, or run with --provider fake for a plumbing "
                "check")

# the model server (server.py, warmup.py, the shard's serving session)
SERVER_START_FAILED = "the model server failed to start"
SERVER_NOT_HEALTHY = "the model server did not become healthy in time"
SERVER_SETTINGS = "server settings differ from requested"
ALIAS_MISMATCH = "alias mismatch: the server does not list or return the requested alias"
SERVER_EXITED = "server exited with"
OOM_HINT = "SIGKILL usually means the runner ran out of memory"
SERVER_UNAVAILABLE = "server unavailable"
SERVER_UNHEALTHY_AFTER = "the model server was unhealthy after the unit"
MEMORY_AFTER_LOAD = "memory: {available} MiB available after the model loaded, {needed} MiB needed"
WARMUP_LENGTH = "warm-up: {task} stopped at the token limit"
WARMUP_REASONING = "warm-up: the server returned reasoning content"
WARMUP_HTTP = "warm-up: {task} failed ({detail})"
CONTEXT_TOO_SMALL = ("context too small: {task} needs {needed} tokens (worst-case prompt {prompt} plus max_tokens "
                     "{max_tokens}) but a slot holds {slot}")
REMEDY_REASONING = "set server_args --reasoning off or raise max_tokens"
REMEDY_HTTP = "raise ctx_per_slot, or set transport_schema reduced"
REMEDY_CONTEXT = "raise {field}"
SERVER_NOTE_MODEL = ("pinned model server {program} {tag} (archive digest {server_digest}, verified by {verified_by}) "
                     "serving model {key} (file digest {model_digest}) with {slots} slots of {ctx} tokens, threads "
                     "{threads} and {threads_batch}, on this shared GitHub-hosted runner")

# provisioning (provision.py) and the prepare step
NOT_A_PLAN = "not a lab plan"
PLAN_CHANGED = "the manifest or lock changed since the plan"
MODEL_NOT_IN_PLAN = "the model is not a gguf model of the plan"
NO_GGUF_IN_PLAN = "the plan uses no gguf model, so it needs no server"
FORBIDDEN_ROOT = "must not lie inside mycelic/, research/, NeuralGraph/ or .github/"
CACHE_INSIDE_OUT = "--cache-root must not lie inside --out"
RELEASE_TAG_MISSING = "the release tag does not exist"
ASSET_MISSING = "the release has no asset with the pinned name"
ASSET_NOT_UPLOADED = "the release asset is not fully uploaded yet; retry later"
DIGEST_UNAVAILABLE = "digest unavailable"
NO_DIGEST = "no digest published"
DIGEST_MISMATCH = "the downloaded file does not match the pinned digest"
LOCK_MISMATCH = "the downloaded file does not match the lock"
NOT_GZIP_TAR = "not a gzip tar archive"
UNSAFE_MEMBER = "the server archive holds an unsafe member"
BINARY_MISSING = "the server binary is missing or not executable"
VERSION_FAILED = "the server binary failed --version"
HUB_REFUSED = "the hub refused the metadata request: private, gated, missing or renamed repository"
HUB_REDIRECTED = "the hub redirected the metadata request (renamed repository?): update the manifest"
HUB_STATUS = "the hub answered the metadata request with an unexpected status"
HUB_UNAVAILABLE = "the hub metadata API stayed unavailable"
HUB_BAD_COMMIT = "the hub returned no valid commit for the revision"
HUB_COMMIT_DIFFERS = "the hub's commit differs from the lock"
REPO_GATED = "the repository is gated"
LICENSE_DIFFERS = "the hub's licence differs from the manifest's or is not stated"
FILE_MISSING = "the file is not in the repository at this revision; available .gguf files:"
NO_HUB_DIGEST = "the hub lists no {field} for the file"
FILE_TOO_LARGE = "the file is larger than {limit} GiB"
HUB_DIFFERS_FROM_LOCK = "the hub's file for the locked commit differs from the lock"
NOT_GGUF = "not a GGUF file"
NOT_ENOUGH_DISK = "not enough disk: {free} MiB free, {need} MiB needed"
NOT_ENOUGH_MEMORY = "not enough memory: {free} MiB available, {need} MiB needed"
DOWNLOAD_REFUSED = "the download was refused"
DOWNLOAD_INSECURE = "the download was refused: a hop is neither https nor loopback"
DOWNLOAD_REDIRECTS = "the download was refused: too many redirects"
DOWNLOAD_NETWORK = "the download failed on the network"
DOWNLOAD_DEADLINE = "the download did not finish before its deadline"
DOWNLOAD_SIZE = "the download's size differs from the expected size"
DOWNLOAD_DISK = "the disk filled up during the download"
LOCK_CONFLICT = "lock candidates disagree"
AMBIGUOUS_RECORDS = "ambiguous provision records"
PROVISION_FAILED_SERVER = "provisioning failed for the server"
PROVISION_FAILED_MODEL = "provisioning failed for model"
RECORD_OTHER_PLAN = "the provision record is for another plan"
OUT_NOT_PREPARABLE = "--out may hold only provision/ and server/ before prepare"

# push discovery
DELETE_ONLY = "delete-only push: nothing to run"
BRANCH_DELETED = "branch deleted: nothing to run"
DEFAULT_BRANCH = "pushes to the default branch run nothing; dispatch the workflow instead"
NOT_A_BRANCH = "not a branch push (a tag?): nothing to run"
MERGE_SEVERAL = "a merge brought several requests; run each one by dispatch"
SEVERAL_REQUESTS = "a push may add or change only one request"
NO_REQUEST_CHANGE = "the workflow ran but the push changed no request file"
NO_BASE = "no base commit could be found for this push"
CHECKOUT_MISMATCH = "the checked-out commit is not the pushed commit"
BAD_REQUEST_NAME = "a request file name must match the request name pattern"
DISPATCH_HINT = "to run a request by hand:"
GIT_FAILED = "a git command failed unexpectedly"

# what the harness is told about the server it measures
FAKE_SERVER_NOTE = "fake OpenAI-compatible server inside the shard process; plumbing only"

# the multi-site simulation (sim.py) and its unit outcomes
SIM_PROJECTED = "projected {projected} min > budget {budget} min"
SIM_LOW_PARTICIPATION = "model participation below threshold"
SIM_CHANNEL_LABELS = {
    "X_model": "X with the model: each site's in-boundary model extracted claims from its own narratives; HQ ran the "
               "detectors over the codes and text-only cells that left the sites, k-suppressed",
    "X_lexical": "X with the lexical extractor: the same pipeline with the pack's lexicon in place of the model; "
                 "exact on generator text by construction",
    "S": "S: codes cells only (no model, no narrative); the same detectors over the same k-suppressed cells",
    "R_mf": "R, model-free: the same detectors over record-level, unsuppressed counts of the fields the pack allows "
            "to leave (codes and structured ids, never narrative); not the strategy's R, which may read those fields "
            "with a frontier model",
    "U": "U, a reference: every claim of the model channel at record level, unsuppressed, with exact roots and "
         "reporters; not a deployable system",
    "single_site": "single site: each site alone, a per-site exceedance test over its own exact counts, sharing the "
                   "same weekly alert budget",
    "rules": "rules: the pack's hand-written rules over the model channel's cells (episode starts, unranked)",
}
SIM_LIFT_LABELS = {
    "X_model_minus_S": "patterns X with the model found minus patterns S found, bootstrapped over patterns",
    "X_model_minus_R_mf": "patterns X with the model found minus patterns model-free R found, bootstrapped over "
                          "patterns",
    "X_model_minus_X_lexical": "patterns X with the model found minus patterns X with the lexical extractor found, "
                               "bootstrapped over patterns; the lexical extractor is exact on generator text, so the "
                               "model's extraction errors decide every pattern only one of them found: below zero "
                               "they lost more patterns than they found, above zero they found more than they lost, "
                               "and none of it is reading better",
}
SIM_NOTES = {
    "synthetic_internal": "Simulation: a seeded synthetic world with planted patterns, written by the same author as "
                          "the detectors; internal only, never a result to show buyers.",
    "lexical_exact": "The lexical extractor is exact on generator text by construction, so wherever X with the model "
                     "and X with the lexical extractor differ, the model's extraction errors made the difference, in "
                     "either direction: errors can lose a pattern, and they can also find one X with the lexical "
                     "extractor missed, as when a wrong predicate on an entity the text does name adds counts to a "
                     "planted key. A find of that kind counts for the model in its lifts over S and over model-free R "
                     "as well. The difference measures extraction fidelity on synthetic text, not the value of "
                     "reading real narratives.",
    "model_beyond_exact": "In the lifts' count, X with the model found a pattern X with the lexical extractor missed: "
                          "the lexical extractor is exact here, so the model's extraction errors made that find. It is "
                          "no sign that the model reads better, yet it raises the model's lifts over S and over "
                          "model-free R as well as its lift over the lexical extractor; the scorecard's patterns "
                          "block gives each channel's raw outcome per pattern, before any chance correction.",
    "few_patterns": "Few planted patterns: recall, average precision and the lift intervals rest on a handful of "
                    "patterns and one seed, so they are not interpretable as estimates.",
    "r_model_free": "R here is model-free: the same detectors over the fields allowed to leave, with no model; it is "
                    "not the strategy's R.",
    "no_control": "No no-plant control ran: a pattern the channel would also have found without the plant counts as "
                  "found, and the lifts compare raw finds.",
    "chance_control": "A no-plant control ran: the same seed's world without the plant went through the same "
                      "pipelines, and a find the control made as early is a chance find; the net counts and the lifts "
                      "leave chance finds out. The control's model extractions were replayed from the planted run for "
                      "the records both worlds share.",
}
SIM_MEASUREMENT_REASONS = {
    "fake_model": "a fake model answered: a fake endpoint, a fake model listing or a fake-server marker",
    "low_participation": "the share of records the lexical fallback extracted was above the threshold",
    "skipped_projection": "the run stopped after the projection check, before any result",
}
SIZING_NOTE = ("Sizing: the per-call medians measured on this runner and the minutes they suggest for the next "
               "request's sim block, a quarter above the estimate and rounded up; a skipped or timed-out unit's "
               "estimate is projected, not measured. One minutes value serves every model of a block, so the block "
               "needs the largest suggestion among its models.")
# the harness's label for a baseline a plant blinds (harness.BY_CONSTRUCTION_LABEL), and what it means for the lifts
BY_CONSTRUCTION_LABEL = "by construction, not a result"
BY_CONSTRUCTION_NOTE = ("By construction, not a result: planted narrative_only records carry no codes and no "
                        "structured entities, so they add nothing to the cells S and model-free R read, and a find of "
                        "theirs on a narrative_only pattern is chance, from background records. Every such pattern X "
                        "found and they missed counts for X in the lifts over S and over model-free R, so that share "
                        "of those lifts is fixed by the plant, not measured; only the plant's other patterns compare "
                        "the channels.")
SIM_WORLD_SAME = "Simulation units of the same plant, seed and weeks saw the same synthetic world."
SIM_WORLD_DIFFERS = ("Simulation units of the same plant, seed and weeks saw different synthetic worlds; their results "
                     "are not comparable.")

# the experiment adapters (prereg.py, units.py, openfda.py) and their unit outcomes; experiment ids are the
# placeholders e_one, e_two, x_one, n_one and j_one (in braces), filled with code spans where a summary renders the
# sentence
PREREG_MISSING = "the preregistration is missing or differs from the plan's"
E2_ABORTED = ("the pushdown harness stopped with an uncaught error, often a central or site call that failed after "
              "its retries; the files it kept are partial")
OPENFDA_UNREACHABLE = "openFDA stayed unreachable from this runner after the connector's retries"
OPENFDA_RATE_LIMITED = ("openFDA kept refusing requests as too many after the connector's retries: add the "
                        "MYCELIC_LAB_OPENFDA_API_KEY repository secret, lower max_records_per_code or product_codes, "
                        "or run again later")
OPENFDA_FETCH_REFUSED = "the openFDA fetch was refused: an HTTP error the connector does not retry, or a bad query"
STEP_SKIPPED = "skipped: a step it needs did not succeed"
E1_NO_REFERENCE = "the reference model has no complete set of valid repeats, so no comparison was run"
E1_COMPARE_FAILED = "the comparison harness refused the runs; its log is in the report artifact beside the copied runs"
E1_ENDPOINT_EXCLUDED = ("A model without a complete set of valid repeats was left out: the comparison is stamped "
                        "incomplete and lists it among the endpoints without runs.")
E1_VERDICTS_WITHHELD = ("Verdicts withheld: non-inferiority and the kill flag are shown only for a model measurement, "
                        "beneath the label above.")
E1_DROPS_NOTE = ("Reasons that start with reattached count predicates kept on the record's structured entity after "
                 "the model named an entity that did not resolve: they are not losses. Every other reason counts "
                 "reply items that post-processing dropped.")
E1_SCORES_NOTE = ("Each model's own scores, pooled over its valid repeats only and scored by the extraction harness's "
                  "own code: predicate F one with its percentile-bootstrap interval over records, beside the lexical "
                  "extractor's predicate F one on the same records when the labels carry it. In the drops column, "
                  "reasons that start with reattached count predicates kept on the record's structured entity, not "
                  "losses.")
E1_SCORES_ONLY = ("No reference comparison was run: these are each model's own scores, with no difference from the "
                  "reference, no non-inferiority and no kill flag.")
E1_SCORES_REFUSED = ("the extraction harness's reader refused a valid repeat of this model: a run file differs from "
                     "its run.json, or the run names another preregistration, model or repeat")
E1_SCORES_UNPINNED = ("no model's own scores: the preregistration, its pack or the scoring code no longer match what "
                      "the extraction harness pinned")
E1_LABELS = {
    "generator_text": "{e_one} here scores extraction against generator ground truth on template text. It is not the "
                      "STRATEGY {e_one} decision, which needs human-labelled public narratives.",
    "fixtures": "{e_one} here scores extraction against the pack's author-written fixture records. It is not the "
                "STRATEGY {e_one} decision, which needs human-labelled public narratives.",
    "public_nhtsa": "{e_one} here scores extraction on real public NHTSA complaint narratives, each complaint's codes "
                    "hidden, against the components it was filed under: filed codes, not human-checked labels. The "
                    "lexical extractor's scores on the same records are in report.json, in the extraction block's labels, "
                    "under public and lexical.",
}
E2_LABELS = {
    "synthetic": "{e_two} here runs on planted synthetic worlds; the pack, the detectors and the plant were written by "
                 "one author, so the plant is not blind. Internal only, never a result to show buyers.",
    "below_protocol": "Below the protocol minimum of candidates and seeds: the conditions and the ratio are not "
                      "interpretable as estimates.",
    "self_central": "The central comparator is the model under test itself, so the ratio compares the model with "
                    "itself; the bar verdict is not shown.",
}
X1_LABEL = ("{x_one} here runs the model-free evaluation harness on a same-author plant fixture that is not blind; the "
            "extractor is lexical and no model runs. It is not STRATEGY's blind {x_one} test.")
OPENFDA_LABEL = "Public data, artificial partitioning, not a confidentiality demonstration; no model in this pipeline."
OPENFDA_PUBLIC_FLAG = ("The replay's measurement flag says only that both caches came from the public openFDA host; it "
                       "measures no model.")
OPENFDA_SAW_RECALLS = ("The requester declared that they saw recall outcomes before the codes, manufacturers and "
                       "settings were fixed, so the vocabulary and detector settings were not frozen blind: the "
                       "recall figures below may reflect hindsight and are not a preregistered result.")
OPENFDA_WARNED = ("The replay warned: a warning can make a channel's zero structural, as when too few sites leave no "
                  "cross-site candidate, rather than a negative result.")
OPENFDA_FALSE_ALARM_SCOPE = ("False alarms per week count alerts on the request's product codes only, those in the "
                             "fetch table, not on every product code of the manufacturer: the lab fetches only the "
                             "requested codes, whatever the replay's own denominator note says.")
SHEETS_LABEL = ("No labels were generated: {n_one} and openFDA {e_one} have no result until a human labels and "
                "commits the sheet.")
G0_MODEL_PATH = ("the canary scan found no leak, but its model path had problems, so it did not test the model in "
                 "the loop")
G0_BELOW_PROTOCOL = ("Some canary scans used fewer records than the protocol scan, whose size the protocol records "
                     "column gives: they are smaller checks, not the protocol scan.")
J1_STOPPED = ("the judge run stopped before its last question, at its budget or after the server stayed down; the "
              "verdicts it wrote are kept, and the part did not finish")
J1_LABEL = ("{j_one} here asks each model the site verifier's narrow question about real public NHTSA complaint "
            "narratives, one record at a time with its codes hidden, and scores each verdict against the components "
            "the complaint was filed under: filed codes, not human-checked labels.")
J1_HEADLINE_NOTE = ("The headline compares each model's balanced accuracy interval with the lexical judge's balanced "
                    "accuracy on the same records: better only when the interval's low end is above it, worse only "
                    "when its high end is below it, otherwise not told apart. Each model is compared once, with no "
                    "correction for several comparisons; sensitivity, specificity and the unknown share decide "
                    "nothing.")
J1_LEXICAL_NOTE = ("The lexical judge is the verifier's judge when no model runs: it confirms a question only when one "
                   "of the pack's phrases for the component is in the narrative and not negated. It judged every "
                   "question in the plan job, before any model ran.")
J1_INCOMPLETE = ("not every part of this model finished, so it gets no headline: its finished parts are a partial "
                 "reading and decide nothing")
J1_WITHHELD = ("transport failures left out more than one in a hundred of this model's records, so its headline is "
               "withheld")
J1_NOT_MEASURED = "not every part of this model is a model measurement, so it gets no headline"
E2_SIZING_NOTE = ("Pushdown sizing: the model-free rehearsal's call counts times this runner's warm-up latencies, "
                  "against the share of the unit's budget the projection may use; a projection above it skips the "
                  "unit, and the suggested minutes are a quarter above the projection, rounded up.")

# the optional hosted provider (hosted.py, plan.py, shard.py): one OpenAI-compatible host named by two secrets
HOSTED_SECRETS_MISSING = "secret MYCELIC_LAB_HOSTED_API_KEY or MYCELIC_LAB_HOSTED_BASE_URL not set"
E1_WITHOUT_HOSTED = ("the extraction comparison keeps fewer than two models or loses its reference without its hosted "
                     "models; secret MYCELIC_LAB_HOSTED_API_KEY or MYCELIC_LAB_HOSTED_BASE_URL not set")
E2_CENTRAL_HOSTED_SKIPPED = ("the hosted central comparator needs secrets MYCELIC_LAB_HOSTED_API_KEY and "
                             "MYCELIC_LAB_HOSTED_BASE_URL, which are not set; the lab never replaces it with the model "
                             "itself")
HOSTED_NOTICE_TITLE = "lab hosted skipped"
HOSTED_SECRET_NOT_SET = "secret {name} is not set"
HOSTED_SECRET_BLANK = "secret {name} is blank"
HOSTED_KEY_NOT_TOKEN = "secret MYCELIC_LAB_HOSTED_API_KEY is not a printable ASCII token"
HOSTED_BASE_URL_INVALID = ("secret MYCELIC_LAB_HOSTED_BASE_URL is not a base URL the lab accepts: https to a public "
                           "host (http only to a loopback address), with no credentials, query or fragment")
HOSTED_PREFLIGHT_FAILED = ("hosted preflight failed: HTTP {status}; check the key or model id, or switch "
                           "response_format from json_schema to json_object to none in lab/models.json")
HOSTED_UNAVAILABLE = "hosted endpoint unavailable: {detail} after {calls} preflight calls; run the request again later"
HOSTED_LENGTH = ("hosted preflight: {task} stopped at the token limit; a reasoning model spends the task's small "
                 "budget thinking, so choose one without reasoning")
HOSTED_MAX_CALLS = "max_calls reached: this shard's share of the hosted calls is used up"
HOSTED_COST_NOTE = ("Estimated cost: the token counts the host reported times the manifest's prices. The host's bill "
                    "is the real cost; calls without token counts are left out, and max_calls caps only the units a "
                    "shard starts, so prepaid credits are the hard limit.")
E1_HOSTED_LABEL = ("Hosted endpoints answered {e_one} over the network: their scores are not deterministic across "
                   "repeats, and raw synthetic text was sent to the configured host.")

CLASS_REASONS = {
    "fake_kind": "the manifest entry is a fake model",
    "provider_override": "the shard ran with the fake provider in place of the model server",
    "fake_marker": "a response carried the fake-server marker",
    "no_model": "the unit uses no model",
    "no_evidence": "no provisioning or server evidence was recorded for the unit",
    "model_verified": "the model file was not verified against the hub or the lock, or its record changed",
    "server_verified": "the server archive was not verified, or its record changed",
    "download_hosts": "a file came from a host other than the one named, or from a private address",
    "model_path": "the server did not report the verified model file as the one it loaded",
    "ledger_host": "a model call went to a host other than the started server",
    "model_served": "a reply named a model other than the requested alias",
    "harness_measurement": "the harness itself did not count the run as a measurement",
    "participation": "the unit did not run to a valid result",
    "verified": "every provenance condition held: a verified model file served by a verified server on this runner",
    "hosted_host": "a hosted call went to a host other than the configured one",
    "hosted_verified": "the configured host answered every call: a hosted API result, not a measurement on this runner",
}

NOTES = {
    "plumbing": "Plumbing: a fake model answered; nothing here measures a model.",
    "plumbing_hosted": "Plumbing: this unit's hosted calls went to the configured host and any other call to a fake "
                       "model; it ran in a plumbing check, so nothing here measures a model.",
    "synthetic": "Synthetic data: every record and narrative was generated from a seed; no real record was used.",
    "runner_hardware": "Latency was measured on a shared GitHub-hosted runner, not on site hardware; it says how "
                       "this runner performed, not what a site would see.",
    "text_only_scan": "The canary scan covers text only: it reads the bytes that crossed a boundary, not timing, "
                      "sizes or other side channels.",
    "model_measurement": "Model measurement: a verified model file answered through a verified server on one shared "
                         "GitHub-hosted runner; quality numbers describe this model on synthetic data, and timings "
                         "describe this runner, not site hardware.",
    "public_narratives": "Public data: real NHTSA vehicle complaint narratives, read with each complaint's codes "
                         "hidden and scored against them. The codes are the components the complaint was filed "
                         "under, not checked labels, and the models may have seen public complaints in training.",
    "model_measurement_public": "Model measurement: a verified model file answered through a verified server on one "
                                "shared GitHub-hosted runner; quality numbers describe this model on public complaint "
                                "narratives against their filed codes, and timings describe this runner, not site "
                                "hardware.",
    "public_data": "Public data: openFDA records under an artificial partitioning; no model ran, and nothing here is "
                   "confidential or a confidentiality demonstration.",
    "hosted_api": "Hosted API: a model on the configured OpenAI-compatible host answered over the network. It is not "
                  "deterministic: temperature and seed are requests, not guarantees on shared batched servers. Its "
                  "latencies include the network and the host's queue and say nothing about this runner.",
    "hosted_raw": "Raw synthetic text was sent to the configured host under allow_external_raw synthetic; the lab "
                  "sends no partner data anywhere, and hosted models never run in the canary scan or the simulation.",
    "central_hosted": "The central comparator was a hosted API model: its scores are not deterministic, so the ratio "
                      "and the bar verdict compare the model under test with a moving reference.",
}

# summaries (summary.py) and the aggregate report (aggregate.py); identifiers are rendered in code spans beside them
PLUMBING_CHECK_LINE = "PLUMBING CHECK: no model was run"
PLUMBING_HOSTED_LINE = ("PLUMBING CHECK: no model was run on this runner, but any hosted unit's calls go to the "
                        "configured host")
NO_MEASUREMENT_LINE = "NO MEASUREMENT: no unit here passed every model check, so nothing below measures a model"
TRUNCATED = "Truncated: the rest did not fit in this summary; every value is in the artifact's files."
NO_PLAN = "No plan was written: the plan step failed before it could record why; read the plan job's log."
NO_REPORT = "No report was written: the aggregate step failed; read the aggregate job's log."
UNSEALED = "the shard was not sealed: its status file is missing or unreadable"
NOT_RUN = "the unit did not run"
NO_ARTIFACT = "no artifact: the shard job was cancelled, timed out at its job limit or never started"
OTHER_PLAN = "the artifact belongs to another plan"
ALTERED = "the artifact differs from the file list it was sealed with"
AMBIGUOUS_ARTIFACTS = "two artifacts of the same attempt claim this shard"
FILES_DIFFER = "a unit file differs from the unit's record"
UNIT_RECORD_INVALID = "the unit record is unreadable or names another unit or run"
STEP_FAILED = "a workflow step failed:"
CPU_MODELS_DIFFER = ("The shards ran on different CPU models: compare timings only between rows of the same CPU "
                     "model.")
WORLD_DIGEST_DIFFERS = ("Canary scans of the same pack, pack version, seed and size saw different synthetic worlds; "
                        "their results are not comparable.")
WORLD_DIGEST_SAME = "Canary scans of the same pack, pack version, seed and size saw the same synthetic world."
NOT_PINNED = "not pinned: trusted on first use"
DISPATCH_BY_HAND = ("To run a request by hand, dispatch the mycelic-lab workflow on the request's branch with the "
                    "request's path as its request input.")
PLAN_FIX_HINT = "Fix the named field of the named file and push the request again; nothing else ran."
LOCK_UNCHANGED = "The lock already pins every file this run verified."
LOCK_NEW = ("This run verified files the lock does not pin yet: copy lock-candidate.json from the report artifact to "
            "lab/models.lock.json and commit it, so later runs verify against it.")
LOCK_CONFLICT_NOTE = ("A verified file disagrees with the lock: the upstream file changed or the lock is wrong; check "
                      "the provision records before re-pinning.")
LOCK_NOT_COMPUTED = ("No lock candidate was computed: the manifest or lock changed since the plan, or the provision "
                     "records were ambiguous.")
REAGGREGATION_LINE = ("Re-aggregation of run {run} by commit {commit}: no unit ran again; this report re-reads that "
                      "run's sealed artifacts with this commit's aggregation code.")
REAGGREGATE_NO_CHANGE = "the workflow ran but the push changed no re-aggregation request"
REAGGREGATE_BAD_NAME = "a re-aggregation request file name must match the request name pattern"

HEADINGS = {
    "plan": "Lab plan",
    "refused": "Lab plan refused",
    "nothing": "Nothing to run",
    "models": "Models",
    "shards": "Shards",
    "units": "Units",
    "provision": "Files to provision once per run",
    "shard": "Lab shard",
    "state": "State",
    "host": "Runner",
    "server": "Model server and model file",
    "report": "Lab report",
    "no-result": "Units without a result",
    "model": "Model on runner CPU",
    "hosted-api": "Hosted API results: not measured on this runner",
    "unverified": "Unverified: not measurements",
    "plumbing": "Plumbing checks (fake provider): not model measurements",
    "no-model": "Units without a model",
    "e3": "Latency and throughput cells",
    "g0": "Canary leakage scans",
    "latency": "Model call latency from the ledgers",
    "sim": "Multi-site simulation: planted patterns found per channel",
    "sim-lifts": "Simulation lifts, bootstrapped over patterns",
    "sim-pushdown": "Simulation pushdown verification",
    "sizing": "Simulation sizing for the next request",
    "by-construction": "Baselines blind to narrative-only patterns by construction",
    "prereg": "Preregistration, fixed before any model runs",
    "e1": "Extraction compared across models",
    "e1-endpoints": "Extraction per model, pooled over repeats",
    "e1-paired": "Extraction paired against the reference",
    "e1-drops": "Extraction drops by reason, pooled over repeats",
    "e1-scores": "Each model's own scores",
    "j1": "Judge test: the verifier's narrow question, per model, beside the lexical judge",
    "j1-paired": "Judge test: model minus lexical judge, paired by record",
    "e2": "Pushdown verification against central reading: conditions",
    "e2-ratio": "Pushdown ratio and verdict",
    "e2-candidates": "Pushdown candidates",
    "e2-sizing": "Pushdown sizing",
    "x1": "Evaluation harness on a plant fixture: channels",
    "x1-lifts": "Evaluation harness lifts, bootstrapped over patterns",
    "openfda": "openFDA public replay: recalls found per channel",
    "openfda-fetch": "openFDA fetches",
    "sheets": "Labelling sheets for a human",
    "lock": "Lock",
    "notes": "Notes",
    "hosted": "Hosted calls and estimated cost",
    "plan-skipped": "Skipped by the plan",
    "plan-hosted": "Hosted calls: allowed and planned",
    "shard-hosted": "Hosted endpoint",
}

COLUMNS = {
    "request": "Request",
    "name": "name",
    "sha": "sha",
    "purpose": "Purpose:",
    "provider": "Provider",
    "commit": "Commit",
    "plan_sha": "Plan",
    "job_minutes": "Job minutes",
    "max_parallel": "Shards at once",
    "retention_days": "Days kept",
    "source": "Source",
    "path": "Path",
    "problem": "Problem",
    "notice": "Notice",
    "requests": "Requests",
    "key": "model",
    "kind": "kind",
    "alias": "alias",
    "repo": "repository",
    "file": "file",
    "revision": "revision",
    "pin": "pin",
    "shard": "shard",
    "model": "model",
    "units": "units",
    "planned_minutes": "planned minutes",
    "timeout_minutes": "job timeout minutes",
    "unit": "unit",
    "experiment": "experiment",
    "minutes": "minutes",
    "seed": "seed",
    "entry": "entry",
    "cache_key": "cache key",
    "failed_step": "Failed step",
    "prepare_problem": "Prepare problem",
    "provenance_complete": "Provenance complete",
    "interrupted": "Interrupted",
    "counts": "Units by status",
    "run_attempt": "Run attempt",
    "missing_units": "Planned units without a record",
    "cpu": "CPU",
    "nproc": "CPUs available",
    "mem_gib": "Memory GiB",
    "os": "OS",
    "python": "Python",
    "server_tag": "Server tag",
    "server_version": "Server version",
    "server_sha": "Server archive sha",
    "verified_by": "verified by",
    "model_repo": "Model repository",
    "model_file": "Model file",
    "model_commit": "Model commit",
    "model_sha": "Model file sha",
    "status": "status",
    "class": "class",
    "exit_code": "exit code",
    "wall_s": "wall seconds",
    "reason": "reason",
    "state": "state",
    "attempt": "attempt",
    "step": "failed step",
    "workload": "workload",
    "concurrency": "concurrency",
    "measured": "measured",
    "ok": "ok",
    "e2e_median": "end to end median s",
    "e2e_p95": "end to end ninety-fifth percentile s",
    "ttft_median": "first token median s",
    "decode_median": "decode tokens per s median",
    "requests_per_s": "requests per s",
    "harness_measurement": "harness measurement",
    "pack": "pack",
    "records": "records",
    "passed": "passed",
    "canaries": "canaries planted",
    "hits": "canary hits",
    "shingle_bytes": "shingle overlap bytes",
    "control_hits": "positive control hits",
    "model_path_problems": "model path problems",
    "world": "world digest",
    "task": "task",
    "n": "calls",
    "median_ms": "median ms",
    "p95_ms": "ninety-fifth percentile ms",
    "target": "target",
    "verified": "verified",
    "first_use": "first use",
    "plan_matches": "plan matches",
    "total_s": "seconds",
    "lock_status": "Lock status",
    "new_entries": "New entries",
    "cpu_models": "CPU models",
    "ignored": "Artifacts of shards not in the plan",
    "skipped": "Skipped by the plan",
    "digests": "digests",
    "consistent": "consistent",
    "unit_count": "Units",
    "shard_count": "Shards",
    "channel": "channel",
    "found": "found",
    "found_net": "found net of chance",
    "chance_found": "chance finds",
    "patterns": "patterns",
    "recall": "recall",
    "p_at_forty": "precision in the top forty",
    "ap": "average precision",
    "alerts": "alerts",
    "false_alarms": "false alarms",
    "lift": "lift",
    "estimate": "estimate",
    "ci_low": "interval low",
    "ci_high": "interval high",
    "candidates": "candidates",
    "true": "true",
    "supported": "supported",
    "ap_pushdown": "pushdown average precision",
    "ap_stats": "detector-score average precision",
    "raw_text": "raw text bytes crossed",
    "fallback_share": "lexical fallback share",
    "records_done": "records done",
    "extract_median_s": "extraction median s",
    "judge_median_s": "judge median s",
    "estimate_minutes": "estimated minutes",
    "suggested_minutes": "suggested minutes",
    "plant": "plant",
    "weeks": "weeks",
    "labels": "labels",
    "claims": "claims",
    "prereg": "prereg sha",
    "rehearsal": "Model-free rehearsal",
    "judge_calls": "site judge calls",
    "raw_max": "most records in one central reading",
    "field_f1": "field F one",
    "claim_f1": "claim F one",
    "json_validity": "valid JSON share",
    "zero_claim_share": "zero-claim share",
    "p50_ms": "median ms",
    "mismatch": "model mismatch",
    "drop_reason": "drop reason",
    "drop_count": "reply items",
    "repeats_used": "repeats used",
    "predicate_f1": "predicate F one",
    "lexical_predicate_f1": "lexical predicate F one",
    "transport_share": "transport failure share",
    "drops": "drops and re-attachments",
    "scores_problem": "why no scores",
    "reaggregation": "Re-aggregation request",
    "reaggregation_purpose": "Re-aggregation purpose:",
    "shard_commits": "Shards' commits",
    "shard_run_ids": "shards' run ids",
    "lab_code_differs": "lab code differs from the shards'",
    "against": "against",
    "decision_metric": "decision metric",
    "diff": "difference",
    "mean_diff": "mean difference",
    "sign_p": "sign test p",
    "underpowered": "underpowered",
    "non_inferior": "non-inferior",
    "kill_flag": "kill flag",
    "margin": "margin",
    "reference": "reference",
    "runs": "repeats",
    "condition": "condition",
    "p_at_k": "precision in the top k",
    "ratio": "pushdown over central raw",
    "pushdown_raw": "pushdown raw text bytes",
    "verdict": "bar verdict",
    "withheld": "withheld because",
    "central": "central",
    "protocol_min": "protocol minimum",
    "decoy": "decoy",
    "background": "background",
    "projected_minutes": "projected minutes",
    "budget_minutes": "unit minutes",
    "share": "share the projection may use",
    "seeds": "seeds",
    "channel_reason": "why no alerts",
    "label_source": "source",
    "pairs": "paired records",
    "underpowered_below": "underpowered below",
    "kill_below": "kill flag below",
    "exceeds": "exceeds",
    "eligible": "eligible",
    "blind": "blind",
    "extractor": "extractor",
    "in_scope": "recalls in scope",
    "recall_rate": "recall rate",
    "lead_days": "median lead days",
    "post_alerts": "post-recall alerts",
    "per_week": "false alarms per week",
    "dataset": "dataset",
    "code": "code",
    "total": "total",
    "fetched": "fetched",
    "truncated": "truncated",
    "sheet": "sheet",
    "requested": "requested",
    "written": "written",
    "protocol_records": "protocol records",
    "max_calls": "max calls",
    "bound": "planned bound",
    "calls_used": "calls used",
    "shard_share": "shard share",
    "tokens_in": "tokens in",
    "tokens_out": "tokens out",
    "tokens_missing": "calls without token counts",
    "estimated_usd": "estimated USD",
    "priced": "priced",
    "preflight": "preflight",
    "preflight_calls": "preflight calls",
    "scheme": "Scheme",
    "hosted_host": "Host",
    "listed": "model listed",
    "model_id": "model id",
    "visibility": "visibility",
    "label": "label",
    "saw_recalls": "recall outcomes seen before the preregistration",
    "warnings": "warnings",
    "parts_ok": "parts finished",
    "balanced_accuracy": "balanced accuracy",
    "lexical_balanced_accuracy": "lexical balanced accuracy",
    "lexical_all": "lexical judge, every record",
    "headline": "headline",
    "sensitivity": "sensitivity",
    "specificity": "specificity",
    "unknown_share": "unknown share",
    "records_dropped": "records left out",
    "headline_reason": "why no headline",
    "questions": "questions",
    "parts": "parts",
}
