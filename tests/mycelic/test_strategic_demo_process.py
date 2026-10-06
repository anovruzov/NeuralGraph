"""Runs the strategic-synthesis demo end to end with the process driver; every printed verdict is a check."""
from __future__ import annotations

import os
import shutil
import unittest

NATS_BIN = os.environ.get("MYCELIC_NATS_SERVER_BIN") or shutil.which("nats-server")


@unittest.skipUnless(NATS_BIN, "nats-server binary not available (set MYCELIC_NATS_SERVER_BIN)")
class StrategicDemoProcessTests(unittest.TestCase):
    def test_strategic_demo_passes(self) -> None:
        from demo.mycelic_strategic_demo import main

        self.assertEqual(main(["--driver", "process", "--pace", "0"]), 0)


if __name__ == "__main__":
    unittest.main()
