# Contributing

Thanks for looking at this. A few things worth knowing before you open a pull
request.

## The one rule that matters

**The harness must never assert a fact about the applicant that is not in
`profile/applicant.json` or the résumé.** Not a degree, not a date, not a visa
status, not a clearance. If a required field cannot be answered from those two
sources, the correct behaviour is to block the application and move on.

Every change is measured against that. A patch that makes the harness "more
helpful" by guessing will be declined, however convenient the guess.

The same applies to site protections: the harness does not solve CAPTCHAs,
bypass login walls, complete assessments meant for the applicant, or evade bot
detection. It detects them, logs them, and skips the job.

## Getting set up

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium
python run.py --demo          # offline self-test, no model server needed
```

## Running the tests

```bash
python -m pytest                    # everything
python -m pytest -m "not browser"   # skip Chromium, runs in seconds
```

The suite is fully offline: a rule-based backend in `qwen/fake.py` answers the
same prompts a real model would, and the ATS-shaped fixtures in
`fixtures/forms/` are served over a local HTTP server. No test needs a model
endpoint, an API key, or outbound network access — please keep it that way.

## Adding an ATS adapter

This is the most useful contribution. Subclass `DiscoveryAdapter`, normalize to
the `Job` dataclass, and register it:

```python
from job_harness.discovery.base import DiscoveryAdapter
from job_harness.discovery.engine import register_adapter

class MyAtsAdapter(DiscoveryAdapter):
    name = "my_ats"
    ats_type = "my_ats"

    def discover(self, target):
        ...
```

See `discovery/greenhouse.py` for the shape. Please include a test with a
recorded API response (as in `tests/test_discovery.py`) rather than a live call.

If the ATS also needs form handling, add a fixture to `fixtures/forms/` shaped
like the real thing and a case in `tests/test_forms_e2e.py`. Fixtures are how
this project finds form-understanding bugs — the first four found eight.

## What a good pull request looks like

- A test that fails before the change and passes after.
- `python -m pytest` green, including the browser tests.
- No new runtime dependency unless it earns its place.
- Commit messages that say what changed and why, not just what.

## Reporting a bug

The useful details are the ATS, the status the application ended in, and the
`blocker_detail` from the database:

```bash
sqlite3 logs/harness.db \
  "SELECT status, blocker_type, blocker_detail FROM applications
     ORDER BY updated_at DESC LIMIT 5;"
```

Please redact your own details before pasting anything from `form_answers`.

## Security

If you find something that could cause the harness to submit a false statement,
submit twice, or send data somewhere it should not, please open a private
security advisory on GitHub rather than a public issue.
