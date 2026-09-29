"""The shipped broker config takes its credentials from the environment, unquoted, and that constrains the password.

``deploy/mycelic/nats/nats.conf`` (and the Kubernetes ConfigMap) write ``password: $NATS_PASSWORD``; nats-server
substitutes the value and then parses it as if it had been written there, so a password that starts with a digit is
read as a number (``1e5…`` is a float) and the broker refuses to start.  Plain ``openssl rand -hex 32`` produces such a
value about one time in fifteen, which is how the compose smoke test failed on CI.  Quoting the reference is not a
fix: nats-server then takes ``$NATS_PASSWORD`` literally.  The generators start with a letter instead.

These tests pin the generator's shape, that the documented commands generate a letter-first password, and that the
shipped config starts with a letter-first password whose hex part is the troublesome ``1e5…`` and admits a client
with it (and refuses a wrong one), i.e. the substitution still works.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from mycelic.harness import secret_hex

ROOT = Path(__file__).resolve().parents[2]
NATS_CONF = ROOT / "deploy" / "mycelic" / "nats" / "nats.conf"
NATS_BIN = os.environ.get("MYCELIC_NATS_SERVER_BIN") or shutil.which("nats-server")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class GeneratedPasswordTests(unittest.TestCase):
    def test_secret_hex_always_starts_with_a_letter(self) -> None:
        for _ in range(5000):
            self.assertRegex(secret_hex(16), r"^[a-f][0-9a-f]{32}$")

    def test_documented_generators_prefix_a_letter(self) -> None:
        for rel in ("DEPLOYMENT.md", "deploy/mycelic/k8s/secret.example.yaml", "deploy/mycelic/.env.example"):
            with self.subTest(file=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                self.assertNotRegex(text, r"NATS_PASSWORD=\$\(openssl", "a bare openssl value can start with a digit")
                self.assertRegex(text, r"n\$\(openssl rand -hex 32\)")


@unittest.skipUnless(NATS_BIN, "nats-server binary not available (set MYCELIC_NATS_SERVER_BIN)")
class ShippedConfigTests(unittest.TestCase):
    PASSWORD = "n1e5" + "0123456789abcdef" * 2  # letter first; without the "n" this is the value that broke CI

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="mycelic-natsconf-")
        self.port = free_port()
        env = {**os.environ, "NATS_USER": "mycelic", "NATS_PASSWORD": self.PASSWORD, "NATS_STORE_DIR": self.tmp.name}
        self.proc = subprocess.Popen([NATS_BIN, "-c", str(NATS_CONF), "-p", str(self.port), "-m", str(free_port())],
                                     env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        deadline = time.time() + 10
        while time.time() < deadline and self.proc.poll() is None:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.05)
        out = self.proc.communicate(timeout=5)[0].decode(errors="replace") if self.proc.poll() is not None else ""
        self.tearDown()
        self.fail(f"nats-server did not start with the shipped config and a letter-first password:\n{out}")

    def tearDown(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        if self.proc.stdout:
            self.proc.stdout.close()
        self.tmp.cleanup()

    async def _connect(self, password: str) -> None:
        import nats

        nc = await nats.connect(f"nats://127.0.0.1:{self.port}", user="mycelic", password=password,
                                allow_reconnect=False, connect_timeout=3, max_reconnect_attempts=0)
        await nc.close()

    def test_client_connects_with_the_substituted_password(self) -> None:
        asyncio.run(self._connect(self.PASSWORD))

    def test_wrong_password_is_refused(self) -> None:
        with self.assertRaises(Exception):
            asyncio.run(self._connect("n" + "f" * 32))


if __name__ == "__main__":
    unittest.main()
