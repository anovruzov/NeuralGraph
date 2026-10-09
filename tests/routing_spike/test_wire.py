"""The narrow interface between HQ and a site: framing, the descriptor's closed schema, and bytes only."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mycelic.collective.jsonio import canonical_bytes
from mycelic.collective.packs.loader import load_pack
from research.routing_spike import wire

PACK = load_pack("device_quality")
TEMPLATES = sorted(PACK.questions)


def descriptor(site: str = "plant-ashvale") -> dict:
    return wire.descriptor(site, PACK.id, PACK.config_hash, TEMPLATES)


def problem(obj: object, site: str = "plant-ashvale") -> object:
    return wire.descriptor_problem(obj, site_id=site, pack_id=PACK.id, config_hash=PACK.config_hash,
                                   template_ids=TEMPLATES)


class FramingTests(unittest.TestCase):
    def test_request_and_response_round_trip_bytes_exactly(self) -> None:
        body = canonical_bytes({"a": "é", "b": [1, 2]})
        kind, got = wire.decode_request(wire.encode_request("question", body))
        self.assertEqual((kind, got), ("question", body))
        self.assertEqual(wire.decode_response(wire.encode_response(body=body)), body)

    def test_only_the_two_kinds_cross(self) -> None:
        self.assertEqual(wire.KINDS, ("describe", "question"))
        with self.assertRaises(wire.WireError) as ctx:
            wire.encode_request("packet_request", b"{}")
        self.assertEqual(ctx.exception.code, "unknown_kind")
        line = json.dumps({"body": "{}", "kind": "cells"}).encode() + b"\n"
        reply = wire.serve_line(lambda k, b: b"never", line)
        with self.assertRaises(wire.WireError) as ctx:
            wire.decode_response(reply)
        self.assertEqual(ctx.exception.code, "unknown_kind")

    def test_a_site_failure_crosses_as_a_fixed_code_without_its_text(self) -> None:
        secret = "the narrative of record 42 said the housing cracked"

        def handler(kind: str, body: bytes) -> bytes:
            raise RuntimeError(secret)

        reply = wire.serve_line(handler, wire.encode_request("question", b"{}"))
        self.assertNotIn(b"narrative", reply)
        self.assertEqual(json.loads(reply), {"error": "error", "ok": False})
        with self.assertRaises(wire.WireError) as ctx:
            wire.decode_response(reply)
        self.assertNotIn(secret, str(ctx.exception))

    def test_a_closed_or_garbled_stream_is_an_error(self) -> None:
        for line, code in ((b"", "closed"), (b"not json\n", "framing"), (b'{"ok": true}\n', "framing")):
            with self.subTest(line=line):
                with self.assertRaises(wire.WireError) as ctx:
                    wire.decode_response(line)
                self.assertEqual(ctx.exception.code, code)


class DescriptorTests(unittest.TestCase):
    def test_the_descriptor_holds_exactly_the_capability_fields(self) -> None:
        d = descriptor()
        self.assertEqual(sorted(d), list(wire.DESCRIPTOR_KEYS))
        self.assertIsNone(problem(d))
        self.assertEqual(d["description"], "Answers pushdown questions over this site's own records.")
        self.assertEqual(d["policy_scope"], ["pushdown:question"])
        self.assertEqual(d["query_types"], TEMPLATES)
        self.assertEqual(d["capability_id"], f"pushdown:{PACK.id}:{PACK.config_hash}")

    def test_the_schema_is_closed(self) -> None:
        cases = {
            "extra key": {**descriptor(), "records": 12},
            "missing key": {k: v for k, v in descriptor().items() if k != "availability"},
            "another site": descriptor("werk-dornhagen"),
            "another description": {**descriptor(), "description": "Holds 3 crack reports."},
            "another scope": {**descriptor(), "policy_scope": ["pushdown:question", "raw"]},
            "query types": {**descriptor(), "query_types": TEMPLATES[:1]},
            "availability": {**descriptor(), "availability": "busy"},
            "capability": {**descriptor(), "capability_id": "pushdown:x:y"},
        }
        for name, obj in cases.items():
            with self.subTest(name):
                self.assertIsNotNone(problem(obj))
        with self.assertRaises(wire.WireError):
            wire.parse_descriptor(canonical_bytes(cases["extra key"]), site_id="plant-ashvale", pack_id=PACK.id,
                                  config_hash=PACK.config_hash, template_ids=TEMPLATES)

    def test_a_non_canonical_descriptor_is_refused(self) -> None:
        data = json.dumps(descriptor(), indent=1).encode()
        with self.assertRaises(wire.WireError):
            wire.parse_descriptor(data, site_id="plant-ashvale", pack_id=PACK.id, config_hash=PACK.config_hash,
                                  template_ids=TEMPLATES)

    def test_the_descriptor_takes_no_record_derived_input(self) -> None:
        import inspect
        self.assertEqual(list(inspect.signature(wire.descriptor).parameters),
                         ["site_id", "pack_id", "config_hash", "template_ids"])


class EndpointTests(unittest.TestCase):
    def test_in_process_endpoint_passes_bytes_through_the_framing_and_logs_every_frame(self) -> None:
        calls = []

        class Site:
            def handle(self, kind: str, body: bytes) -> bytes:
                calls.append((kind, body))
                return canonical_bytes({"echo": body.decode()})

            def close(self) -> None:
                calls.append(("closed", b""))

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "wire.jsonl"
            ep = wire.InProcessEndpoint("plant-ashvale", Site, log_path=log)
            out = ep.request("question", b'{"q":1}')
            ep.close()
            self.assertIsInstance(out, bytes)
            self.assertEqual(out, canonical_bytes({"echo": '{"q":1}'}))
            rows = wire.read_frames(log)
            self.assertEqual([r["direction"] for r in rows], ["request", "response"])
            self.assertEqual(calls[-1][0], "closed")
        public = [name for name in vars(ep) if not name.startswith("_")]
        self.assertEqual(sorted(public), ["log_path", "site_id"])


if __name__ == "__main__":
    unittest.main()
