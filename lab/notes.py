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
NO_MODEL_SERVER = "no model server: the prepare step is not built yet; run with --provider fake for a plumbing check"

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
    "model_rule_pending": "a real server answered; the rule that admits a model measurement arrives with the "
                          "model server",
}

NOTES = {
    "plumbing": "Plumbing: a fake model answered; nothing here measures a model.",
    "synthetic": "Synthetic data: every record and narrative was generated from a seed; no real record was used.",
    "runner_hardware": "Latency was measured on a shared GitHub-hosted runner, not on site hardware; it says how "
                       "this runner performed, not what a site would see.",
    "text_only_scan": "The canary scan covers text only: it reads the bytes that crossed a boundary, not timing, "
                      "sizes or other side channels.",
}
