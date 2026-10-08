# Lab models

The models a request may name are the entries of `lab/models.json`. This is the only lab document that names model
families; every other one uses the manifest keys. What each manifest key means is in
[REFERENCE.md](REFERENCE.md#model-manifest-and-lock).

The pinned server release asset and the model file names in lab/models.json are unverified until the check run downloads and verifies them.

Nothing below is a measurement: no run has measured these models yet, and the licences and file names are what the
manifest declares, which the provision step checks against the hub on every run.

## The entries

| key | kind | alias | repository | file | revision | licence | role |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `a-0p5b` | gguf | `lab-a-0p5b` | `Qwen/Qwen2.5-0.5B-Instruct-GGUF` | `qwen2.5-0.5b-instruct-q4_k_m.gguf` | `main` | apache-2.0 | the smallest rung of the size ladder: the check run's model, the smoke run's canary-scan model, and an E2 model of the main run |
| `a-1p5b` | gguf | `lab-a-1p5b` | `Qwen/Qwen2.5-1.5B-Instruct-GGUF` | `qwen2.5-1.5b-instruct-q4_k_m.gguf` | `main` | apache-2.0 | the next rung: an E2 model of the main run and of the hosted comparison |
| `a-4b` | gguf | `lab-a-4b` | `Qwen/Qwen3-4B-GGUF` | `Qwen3-4B-Q4_K_M.gguf` | `main` | apache-2.0 | the largest local rung: E1's reference in the main run; served with `--reasoning off` (its `server_args`), since the warm-up refuses a server that returns reasoning content |
| `b-2b` | gguf | `lab-b-2b` | `ibm-granite/granite-3.3-2b-instruct-GGUF` | `granite-3.3-2b-instruct-Q4_K_M.gguf` | `main` | apache-2.0 | a second publisher family, so the main run does not compare one family with itself |
| `fake-a` | fake | `lab-fake-a` | | | | | the plumbing run's fake model (persona `valid`) |
| `fake-b` | fake | `lab-fake-b` | | | | | the plumbing run's second fake model (persona `valid`) |

`main` is a branch: each run resolves it to a commit once, every shard of the run uses that commit, and the lock pins
it after the check or smoke run.

The server is the `llama-server` program of the llama.cpp release `b11476`, asset
`llama-b11476-bin-ubuntu-x64.tar.gz` (`server` in `lab/models.json`).

## The rules

- A model must be ungated and licensed apache-2.0 or mit (`license` in the manifest).
- The provision step asks the hub for the repository at the revision before downloading. It refuses a gated
  repository (`the repository is gated`), a licence that differs from the manifest's or is not stated (`the hub's
  licence differs from the manifest's or is not stated`) and a file the repository does not hold at that revision
  (`the file is not in the repository at this revision; available .gguf files:`, followed by the `.gguf` files it
  does hold). A renamed repository is refused as a redirect.
- Every model file is checked against the hub's published sha256, or the lock's once it is pinned; the server
  archive against the release's published digest (trusted on first use when none is published), or the lock's.

## Adding a model

1. Add an entry to `lab/models.json`: a key matching `[a-z0-9][a-z0-9-]{0,23}`, `kind` `gguf`, an `alias`, and
   `gguf` with `repo`, `file`, `revision` and `license`. Keep the context settings at their defaults unless a
   warm-up asks for more.
2. Run a check-style request for it: copy `lab/templates/check.json`, put the new key in `models` and push it. The
   check run verifies the licence, the gating and the file, and times a few short requests.
3. Copy the report's `lock-candidate.json` to `lab/models.lock.json` and commit it.

No hosted entry ships in `lab/models.json`: a hosted model's id depends on the host and its price on the account.
The guide's Optional secrets section shows the entry to add.

## Not in the manifest

STRATEGY section 11.2 lists further E1 candidates: Qwen3-1.7B, Qwen3-8B, Llama-3.2-3B, Gemma-3-4B, Phi-4-mini and,
as frontier references, Haiku 4.5 and Opus. They are not in the manifest. The local ones would each need an entry
and a check run to verify their licence, gating and file before any request names them; the lab states nothing about
them until such a run has. The frontier references are hosted APIs: they could run only as hosted entries (Optional
secrets in the guide), billed by their host.
