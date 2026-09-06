"""Shared fixtures. No network and no real model server is used by any test."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from job_harness.browser.engine import resolve_chromium_path          # noqa: E402
from job_harness.config.settings import Config                        # noqa: E402
from job_harness.database.db import Database                          # noqa: E402
from job_harness.profile.applicant import Applicant                   # noqa: E402
from job_harness.qwen.fake import fake_client                         # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROFILE_PATH = FIXTURES / "applicant.test.json"
RESUME_PATH = FIXTURES / "resume.test.txt"


@pytest.fixture
def config(tmp_path: Path) -> Config:
    cfg = Config.load()
    cfg.run.database_path = str(tmp_path / "harness.db")
    cfg.run.log_dir = str(tmp_path / "logs")
    cfg.run.profile_path = str(PROFILE_PATH)
    cfg.run.resume_path = str(RESUME_PATH)
    cfg.browser.persistent_profile_dir = str(tmp_path / "browser")
    cfg.browser.screenshot_dir = str(tmp_path / "shots")
    cfg.dashboard.enabled = False
    return cfg


@pytest.fixture
def db(config: Config) -> Database:
    database = Database(config.run.database_path)
    database.start_run("test-run", "dry-run", {})
    yield database
    database.close()


@pytest.fixture
def applicant() -> Applicant:
    return Applicant.load(PROFILE_PATH, RESUME_PATH)


@pytest.fixture
def qwen(config: Config, db: Database):
    client = fake_client(config.qwen, db=db, run_id="test-run")
    yield client
    client.close()


@pytest.fixture(scope="session")
def chromium_path() -> str:
    path = resolve_chromium_path("")
    if not path:
        pytest.skip("no Chromium available")
    return path


@pytest.fixture(scope="session")
def fixture_server():
    from job_harness.fixtures.server import FixtureServer
    server = FixtureServer().start()
    yield server
    server.stop()


@pytest.fixture
def page(chromium_path: str):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True, executable_path=chromium_path,
                                 args=["--no-sandbox", "--disable-dev-shm-usage"])
    context = browser.new_context()
    page = context.new_page()
    yield page
    context.close()
    browser.close()
    pw.stop()
