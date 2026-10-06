"""Runs the production smoke test with the process driver (real nats-server, real service process, real agent
processes). Skipped when nats-server is unavailable; the compose driver is exercised manually / in CI with Docker."""
from __future__ import annotations

import os
import shutil
import unittest

NATS_BIN = os.environ.get("MYCELIC_NATS_SERVER_BIN") or shutil.which("nats-server")


@unittest.skipUnless(NATS_BIN, "nats-server binary not available (set MYCELIC_NATS_SERVER_BIN)")
class SmokeProcessTests(unittest.TestCase):
    def test_smoke_scenario_passes(self) -> None:
        from tests.smoke.mycelic_smoke import main

        self.assertEqual(main(["--driver", "process", "--agents-per-team", "2"]), 0)


if __name__ == "__main__":
    unittest.main()
