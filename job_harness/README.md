# Job Harness

An autonomous job-search and application system. It discovers openings on
public job boards, scores them against your résumé, opens the application form
in a real browser, understands the form, fills it **only** with facts you have
supplied, submits it, and verifies the submission actually landed.

```
SEARCH → DISCOVER → FILTER → SCORE → OPEN → UNDERSTAND FORM → FILL
       → VALIDATE → SUBMIT → VERIFY → LOG → NEXT
```

- **Qwen** (any OpenAI-compatible endpoint) does classification, scoring, form
  reasoning and submission judgement.
- **Playwright** drives a real Chromium with a persistent profile.
- **SQLite** holds all state, so a crash or restart resumes where it stopped.
- **`profile/applicant.json` + your résumé** are the only sources of personal
  fact. Nothing else is ever asserted on your behalf.

## What it will not do

These are design guarantees, each covered by tests:

| Guarantee | Enforced by |
|---|---|
| Never invents a degree, employer, date, GPA, skill, citizenship, visa status, or clearance | `application/field_resolver.py` + grounding check against your profile/résumé |
| Never guesses a legal attestation — a required question you have not answered blocks the application instead | `semantics.SENSITIVE_KEYS` |
| Never solves a CAPTCHA, bypasses a login wall, completes an assessment, or defeats bot detection — it logs the blocker and moves on | `application/blockers.py` |
| Never counts a submission as successful without evidence | `verification/verifier.py` |
| Never submits the same job twice, even across restarts | partial unique index in `database/schema.sql` |
| Never clicks Submit in dry-run mode | `application/filler.py` |

---

## 1. Installation

```bash
git clone <your-repo> && cd job_harness

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Chromium for Playwright (~150MB)
python -m playwright install chromium
python -m playwright install-deps chromium      # Linux system libraries

# Optional but recommended: lets the harness read a PDF résumé
sudo apt-get install -y poppler-utils           # Debian/Ubuntu
brew install poppler                            # macOS
```

Python 3.11 or newer.

## 2. Configure Qwen

The harness talks to any OpenAI-compatible `/chat/completions` endpoint. No
provider is hardcoded.

```bash
cp .env.example .env
```

Then set three variables in `.env`:

```bash
QWEN_BASE_URL=http://localhost:8000/v1
QWEN_API_KEY=
QWEN_MODEL=qwen2.5-72b-instruct
```

Common backends:

```bash
# vLLM (self-hosted)
python -m vllm.entrypoints.openai.api_server \
    --model Qwen/Qwen2.5-72B-Instruct --port 8000
# QWEN_BASE_URL=http://localhost:8000/v1

# Ollama (local, smaller models)
ollama serve && ollama pull qwen2.5:14b-instruct
# QWEN_BASE_URL=http://localhost:11434/v1
# QWEN_MODEL=qwen2.5:14b-instruct

# Alibaba DashScope
# QWEN_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
# QWEN_API_KEY=sk-...
# QWEN_MODEL=qwen-max
```

If your endpoint rejects `response_format={"type":"json_object"}`, set
`QWEN_JSON_MODE=false`; the harness also detects this and disables it itself.

Verify the connection:

```bash
python run.py --check
```

## 3. Configure your profile

```bash
python run.py --init-profile          # writes profile/applicant.json
cp /path/to/your/resume.pdf resumes/resume.pdf
```

Fill in `profile/applicant.json`. See `profile/applicant.example.json` for the
annotated version. **Every field you leave blank is a question the harness will
refuse to answer** — that is intentional. If a required form field maps to a
blank profile field, the application is marked `BLOCKED` and the harness moves
to the next job rather than inventing a value.

Key fields:

| Field | Used for |
|---|---|
| `legal_name`, `email`, `phone` | required; the harness will not start in `--apply` without them |
| `work_authorization` | must begin with a clear yes/no ("Authorized to work…", "No, I am not…") |
| `sponsorship_requirement` | the opposite question; state it explicitly |
| `security_clearance`, `criminal_history`, `visa_status` | left blank means "refuse to answer" |
| `demographic_response_policy` | `decline` selects a prefer-not-to-say option; or give the exact value to disclose |
| `cover_letter_policy.mode` | `generate` composes prose from profile facts only, `file` uploads/pastes `cover_letter_path`, `skip` leaves optional cover letters blank |
| `employment_history`, `skills` | grounds open-ended answers |

Confirm it loaded:

```bash
python run.py --check
```

## 4. Choose what to apply to

Discovery targets go in `config/default_config.json` or on the command line.

```json
{
  "discovery": {
    "boards": {
      "greenhouse": ["anthropic", "openai"],
      "lever": ["some-startup"],
      "ashby": ["another-startup"],
      "workday": ["tenant/CareerSite"],
      "smartrecruiters": ["CompanyName"],
      "workable": ["account-slug"]
    },
    "seed_urls": ["https://example.com/careers"],
    "freshness_days": 7,
    "remote_only": false
  }
}
```

Or per run:

```bash
python run.py --dry-run \
  --board greenhouse:anthropic \
  --board lever:some-startup \
  --url https://example.com/careers
```

A board token is the slug in the board URL —
`https://boards.greenhouse.io/**acme**/jobs/1` → `acme`. Full URLs work too.
Workday targets are `tenant/SiteName`, or paste any career-site URL.
SmartRecruiters uses the company identifier from
`careers.smartrecruiters.com/**CompanyName**`; Workable uses the account slug
from `apply.workable.com/**account-slug**`.

Adding another ATS is a subclass of `DiscoveryAdapter` plus one
`register_adapter()` call; see `discovery/greenhouse.py` for the shape.

## 5. Dry run (do this first)

Dry run performs **everything except the final Submit click**: it discovers,
scores, opens each form, fills it, validates it, and records
`READY_TO_SUBMIT`.

```bash
python run.py --dry-run
```

Useful variants:

```bash
python run.py --dry-run --max-applications 5           # small first pass
python run.py --dry-run --headful                      # watch the browser
python run.py --discover-only                          # score only, no browser
python run.py --dry-run --fake-qwen                    # no model server needed
python run.py --demo                                   # offline self-test
python run.py --status                                 # statistics
python run.py --status --json                          # machine readable
```

Then inspect what it *would* have submitted:

```bash
sqlite3 logs/harness.db \
  "SELECT label, answer, source, resolver, safe_to_submit
     FROM form_answers ORDER BY id DESC LIMIT 40;"

sqlite3 logs/harness.db \
  "SELECT j.company, j.title, a.status, a.blocker_type, a.blocker_detail
     FROM applications a JOIN jobs j USING(job_id) ORDER BY a.updated_at DESC;"
```

Read every answer before switching to `--apply`. Anything the harness could not
ground appears as a `BLOCKED` application with the exact reason.

## 6. Production run

```bash
python run.py --apply
```

`--apply` clicks Submit when — and only when — every required field has a
truthful, deterministic answer and validation passes. It does not ask for
confirmation on ordinary applications; that is the authorized behaviour.

```bash
python run.py --apply --max-applications 20 --max-per-hour 8 --min-score 70
python run.py --apply --loop                # keep running, re-discovering
python run.py --apply --remote-only --freshness-days 3
```

Every flag:

| Flag | Meaning |
|---|---|
| `--dry-run` / `--apply` | fill only, or actually submit |
| `--status` / `--check` | statistics; configuration and connectivity check |
| `--demo` | offline self-test against the bundled fixtures |
| `--init-profile` | write a blank applicant profile |
| `--discover-only` | discover and score, no browser |
| `--no-discovery` | work the existing queue only |
| `--loop` | keep running after the queue empties |
| `--retry-blocked` | requeue jobs blocked for fixable reasons before running |
| `--max-applications N` | stop after N applications |
| `--max-per-hour N` | rate limit |
| `--min-score N` | hard floor on the fit score (0–100); also raises the rubric threshold if needed |
| `--freshness-days N` | only postings newer than N days |
| `--remote-only` | remote positions only |
| `--allow-senior` | do not reject staff/principal/manager titles |
| `--roles "A,B"` | override target roles |
| `--board SRC:TOKEN`, `--url URL` | discovery targets (repeatable) |
| `--headful` | show the browser |
| `--fake-qwen` | rule-based backend, for testing without a model |
| `--dashboard-host`, `--dashboard-port`, `--no-dashboard` | dashboard control |

Stop it with `Ctrl-C`: it finishes the job in flight, then exits cleanly.
Restarting picks the queue back up.

## 7. Dashboard and phone control

The dashboard starts with the harness at `http://127.0.0.1:8765`. It shows
discovered / scored / queued / submitted / verified / blocked / failed /
duplicate counts, applications per hour, average fit score, Qwen requests,
tokens and estimated cost, cost per verified application, and the company and
role currently being worked on. It has START, PAUSE, RESUME and STOP, and lets
you change limits, minimum score, freshness, remote-only and target roles while
the campaign runs.

**Authentication is mandatory off loopback.** The harness refuses to start with
a non-loopback bind and no token.

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"   # put in DASHBOARD_TOKEN
```

### Reaching it from your phone

Preferred — **Tailscale** (no public exposure):

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
export DASHBOARD_HOST=0.0.0.0
export DASHBOARD_TOKEN=<your token>
python run.py --apply --loop
```

Then open `http://<machine>.<tailnet>.ts.net:8765/?token=<your token>` on your
phone. The token is stored in a `SameSite=Strict; HttpOnly` cookie after the
first load.

Alternative — **SSH tunnel** (nothing listens publicly):

```bash
ssh -L 8765:127.0.0.1:8765 user@your-vps
```

If you must expose it directly, put it behind a TLS reverse proxy and keep the
token set. Do not serve the control interface unauthenticated over the
internet.

## 8. VPS deployment

### Docker (recommended)

```bash
git clone <your-repo> && cd job_harness
cp .env.example .env && $EDITOR .env         # set QWEN_* and DASHBOARD_TOKEN
cp /path/to/resume.pdf resumes/resume.pdf
$EDITOR profile/applicant.json

docker compose build
docker compose run --rm job-harness python run.py --check
docker compose up -d                          # dry-run --loop by default
docker compose logs -f
```

Switch to production by editing the `command` in `docker-compose.yml`:

```yaml
command: ["python", "run.py", "--apply", "--loop"]
```

State (database, logs, screenshots, browser profile) lives in the
`harness-data` volume and survives restarts and rebuilds.

### systemd (no Docker)

```bash
sudo useradd -r -m -d /opt/job-harness harness
sudo -u harness git clone <your-repo> /opt/job-harness/app
cd /opt/job-harness/app
sudo -u harness python3 -m venv .venv
sudo -u harness .venv/bin/pip install -r requirements.txt
sudo -u harness .venv/bin/python -m playwright install --with-deps chromium
```

`/etc/systemd/system/job-harness.service`:

```ini
[Unit]
Description=Autonomous job application harness
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=harness
WorkingDirectory=/opt/job-harness/app
EnvironmentFile=/opt/job-harness/app/.env
ExecStart=/opt/job-harness/app/.venv/bin/python run.py --apply --loop
Restart=always
RestartSec=30
# The harness resumes from SQLite, so a restart never loses or repeats work.

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now job-harness
sudo systemctl status job-harness
journalctl -u job-harness -f
```

### Starting and stopping from your phone

Once the dashboard is reachable (Tailscale or tunnel), use its START / PAUSE /
RESUME / STOP buttons — they take effect within a couple of seconds without
restarting the process. From a phone terminal you can also use the API:

```bash
curl -X POST http://<host>:8765/api/control \
  -H "X-Auth-Token: $DASHBOARD_TOKEN" -H 'Content-Type: application/json' \
  -d '{"action":"pause"}'

curl -X POST http://<host>:8765/api/config \
  -H "X-Auth-Token: $DASHBOARD_TOKEN" -H 'Content-Type: application/json' \
  -d '{"min_score":75,"max_applications_per_hour":6,"remote_only":true}'

curl -s http://<host>:8765/api/status -H "X-Auth-Token: $DASHBOARD_TOKEN" | jq .pipeline
```

Or over systemd: `sudo systemctl {stop,start,restart} job-harness`.

## 9. How scoring works

Deterministic prefilters run first — no tokens are spent rejecting an obvious
mismatch. They reject senior/management titles (unless `--allow-senior`),
internships, off-domain roles, stale postings, non-US locations, jobs requiring
a security clearance, and jobs demanding more years than
`scoring.max_years_experience_required`.

Surviving jobs get a relevance classification, then requirement extraction,
then a 0–100 rubric score:

| Component | Max |
|---|---|
| Technical fit | 30 |
| Résumé evidence | 25 |
| AI relevance | 20 |
| Career upside | 10 |
| Company / compensation | 10 |
| Application friction | 5 |

- **≥ 65** → apply
- **50–64** → apply only if the experience requirement is still plausible
- **< 50** → skip

Missing *preferred* qualifications never cause a skip; only unmet *required*
ones do.

Two settings interact here. `scoring.apply_threshold` (default 65) is where the
rubric says "apply"; `run.min_score` (default 50) is a hard floor applied after
scoring. Leaving the floor at 50 is what lets the borderline band work at all —
raise it to 65 to apply only to clear matches, or to 75 to be strict. The
harness refuses to start with a floor above the threshold, since that would
discard every job the scorer accepts. Both are adjustable from the dashboard
while the campaign runs.

## 10. Application statuses

`DISCOVERED → SCORED → QUEUED → OPENED → FILLING → READY_TO_SUBMIT →
SUBMITTED → VERIFIED`, with `SKIPPED`, `BLOCKED`, `FAILED` and `DUPLICATE` as
exits.

**Only `VERIFIED` counts as a successful application.** `SUBMITTED` means the
button was clicked but no confirmation could be established. Those appear under
**Needs review** on the dashboard and in `--status`, with a link to each
posting, so you can check them yourself. The harness never retries them: the
click may have landed, and a retry could submit twice.

A submit the form *rejected* is different — nothing was submitted, so it is
recorded `BLOCKED` and stays retryable.

Verification accepts: a confirmation page, a confirmation message, an
application ID, or a successful application POST. It rejects validation errors,
a bare URL change, and a failed POST.

## 11. Cost control

Qwen does the semantic work; everything mechanical is deterministic:

- job relevance is classified in batches, turning N calls into ceil(N/10)
- semantically equivalent target titles are expanded once and cached in SQLite
- deterministic label patterns classify most form fields with no model call
- profile lookups answer most fields with no model call
- prefilters reject unsuitable jobs before any model call
- dedupe uses URL / ATS key / title hashes, calling the model only for
  genuinely ambiguous near-misses
- identical requests are cached in SQLite across runs
- the model receives a compact structured form, never raw page HTML
- descriptions are hashed so a re-listed job is not re-scored

Every call is recorded in the `qwen_calls` table with tokens, latency and
estimated cost. `--status` reports cost per verified application.

## 12. Testing

```bash
python -m pytest                       # 268 tests
python -m pytest -m "not browser"      # skip the Chromium end-to-end tests
python -m pytest tests/test_grounding.py -v
```

The suite needs no model server and no network: a rule-based backend answers
the same prompts a real model would, and the ATS-shaped HTML fixtures in
`fixtures/forms/` are served over a local HTTP server.

To exercise the whole pipeline offline in one command:

```bash
python run.py --demo
```

`--demo` serves the bundled ATS form fixtures over a local HTTP server, runs a
full dry run against them with the rule-based backend, and prints the result.
It needs no model server, no network and no profile of your own -- use it to
confirm an install or a deployment works before pointing the harness at real
boards. Expect 7 discovered, 5 reaching `READY_TO_SUBMIT`, and 2 blocked (the
CAPTCHA and assessment fixtures).

## 13. Layout

```
job_harness/
├── run.py                  CLI entry point
├── orchestrator.py         campaign loop, limits, pause/resume/stop
├── config/                 layered settings, structured logging
├── database/               schema, models, repositories
├── qwen/                   client, schemas, prompts, JSON repair, fake backend
├── profile/                applicant.json loading and grounding
├── scoring/                deterministic prefilter + rubric scoring
├── discovery/              Greenhouse, Lever, Ashby, Workday, SmartRecruiters,
│                           Workable, seed URLs, dedupe
├── ats/                    ATS detection and apply-URL derivation
├── browser/                Playwright engine, DOM form extraction, actions
├── application/            semantics, field resolver, blockers, filler, pipeline
├── verification/           submission evidence
├── dashboard/              control server, stats, UI
├── fixtures/forms/         ATS-shaped HTML used by the tests
├── tests/                  268 tests
└── logs/                   database, JSONL logs, screenshots, browser profile
```

## 14. Troubleshooting

| Symptom | Fix |
|---|---|
| `no Chromium found` | `python -m playwright install chromium`, or set `BROWSER_EXECUTABLE` |
| `qwen FAIL` from `--check` | check `QWEN_BASE_URL` reachability and `QWEN_MODEL` |
| Model output rejected repeatedly | set `QWEN_JSON_MODE=false`, or use a larger model |
| `resume WARN no text extracted` | install `poppler-utils`, or `pip install pypdf`, or supply a `.txt` résumé |
| Everything `BLOCKED: missing_answer` | a required profile field is blank — check `blocker_detail` in the database, fill it in, then `python run.py --apply --retry-blocked` |
| `BLOCKED: captcha` | expected and correct; that job needs you personally |
| Dashboard refuses to start | non-loopback bind requires `DASHBOARD_TOKEN` |
| Chromium crashes in Docker | raise `shm_size` |

## 15. Operating notes

- Run a dry run against a new board before applying: ATS layouts vary.
- Keep `max_applications_per_hour` modest. Volume is not the goal, and boards
  rate-limit.
- `logs/screenshots/` holds a capture for every blocker and every submission,
  which is the fastest way to see what a form actually did.
- The browser profile persists, so a site you log into once stays logged in.
- Review the `form_answers` table periodically: it is the audit trail of every
  value submitted on your behalf and where each came from.
- When the harness blocks on a missing profile value, fill it in and run with
  `--retry-blocked`; blockers that need you personally (CAPTCHA, assessments,
  login walls) are never retried.
- `logs/harness.jsonl` can contain profile values inside blocker reasons (for
  example, "Bachelor of Science does not match any allowed option"). It is your
  own data, but treat the log directory as personal if you ship logs off the
  machine. Credentials are never logged: the API key and dashboard token are
  redacted everywhere they are stored or served.
- Check **Needs review** regularly. Those are applications where Submit was
  clicked but no confirmation appeared: open each link and confirm by hand.
