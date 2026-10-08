"""Shared fixtures for Mycelic tests: temp data dir, coordination DB, org/auth/authz services.

Every test is offline and deterministic: fake model provider, hash embeddings, SQLite transport.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from mycelic.auth import AuthService
from mycelic.authz import Authorizer
from mycelic.db import CoordDB
from mycelic.jobs import JobQueue
from mycelic.org import OrgService


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def db(tmp_path: Path) -> CoordDB:
    d = CoordDB(tmp_path / "coord.db")
    yield d
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(d.close()) if False else None


@pytest.fixture
def org(db: CoordDB) -> OrgService:
    return OrgService(db)


@pytest.fixture
def auth(db: CoordDB, org: OrgService) -> AuthService:
    return AuthService(db, org)


@pytest.fixture
def authz(db: CoordDB, org: OrgService) -> Authorizer:
    return Authorizer(db, org)


@pytest.fixture
def jobs(db: CoordDB) -> JobQueue:
    return JobQueue(db)


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "live: runs against a real provider; skipped unless its credentials are in the environment")
