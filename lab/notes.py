"""Every fixed sentence the lab writes into a record, a status line or a workflow command.

No constant here holds an ASCII digit (a guard test checks it): numbers and identifiers that contain digits (unit
ids, run ids, thresholds, counts) are inserted where a sentence is rendered, and G3 summaries put identifiers in
backticks. The keys of :data:`CLASS_REASONS` and :data:`NOTES` are what records store; the sentences are what a
reader sees.
"""
from __future__ import annotations

PLUMBING_BANNER = "Plumbing check: a fake model answered every call. These records test the lab, not any model."
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
}

NOTES = {
    "plumbing": "Plumbing: a fake model answered; nothing here measures a model.",
    "synthetic": "Synthetic data: every record and narrative was generated from a seed; no real record was used.",
    "runner_hardware": "Latency was measured on a shared GitHub-hosted runner, not on site hardware; it says how "
                       "this runner performed, not what a site would see.",
    "text_only_scan": "The canary scan covers text only: it reads the bytes that crossed a boundary, not timing, "
                      "sizes or other side channels.",
    "model_measurement": "Model measurement: a verified model file answered through a verified server on one shared "
                         "GitHub-hosted runner; quality numbers describe this model on synthetic data, and timings "
                         "describe this runner, not site hardware.",
}
