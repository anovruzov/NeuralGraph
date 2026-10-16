import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from rollup.backends import Fixture, LocalEndpoint, SessionExchange
from rollup.edge import emulate
from rollup.gossip import Node, envelope
from rollup.protocol import (CHAINS, ENTITIES, ROLES, candidates, canonical, digest, entity_allowed, handle, make_prompt,
                             scan, validate_reply, validate_summary)


def summary(site="r00-site-00", week=1, kinds=("cert_rotation", "auth_failure", "api_error_spike")):
    return {"site": site, "week": week, "entries": [
        {"entity": "E-ORION", "kind": k, "negated": False, "distinct": 1, "copies": 0,
         "handles": [handle(b"test-company", k)]} for k in kinds]}


class PrivacyTests(unittest.TestCase):
    def test_large_world_generated_aliases_are_recognized_exactly(self):
        for good in ("E-NIMBUS", "E-NIMBUS420", "E-VERIDIAN421", "E-COBALT-MK3"):
            self.assertTrue(entity_allowed(good), good)
        for bad in ("E-NIMBUS421", "E-UNKNOWN420", "E-NIMBUS1", "E-NIMBUS0420", "E-NIMBUS-PAY420"):
            self.assertFalse(entity_allowed(bad), bad)

    def test_closed_schema_refuses_each_private_field(self):
        for field in ("record_ref", "uid", "reporter", "narrative", "quote", "text", "persons"):
            for top in (True, False):
                s = summary()
                (s if top else s["entries"][0])[field] = "private"
                with self.subTest(field=field, top=top), self.assertRaises(ValueError):
                    validate_summary(s)

    def test_private_value_cannot_hide_in_allowed_strings(self):
        for field, value in (("site", "u42"), ("entity", "em0-0000001"), ("kind", "private prose"),
                             ("handles", ["em0-0000001"])):
            s = summary()
            (s if field == "site" else s["entries"][0])[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_summary(s)

    def test_handles_are_keyed_not_ref_or_unkeyed_hash(self):
        h = handle(b"company", "content fingerprint")
        self.assertNotEqual(h, handle(b"other company", "content fingerprint"))
        self.assertNotEqual(h, hashlib.sha256(b"content fingerprint").hexdigest()[:16])
        for bad in (h, hashlib.sha256(b"em0-0000001").hexdigest()[:16]):
            s = summary()
            s["entries"][0]["handles"] = [bad]
            with self.assertRaises(ValueError):
                validate_summary(s, forbidden=[bad])

    def test_prompt_blocks_answer_vocabulary_and_identifiers(self):
        for text in ("Gold", "label", "decoy", "em0-0000001", "u42", "[d001]", "R01S01-DEPT-X"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                scan(text, prompt=True)

    def test_counts_and_polarity_are_strict(self):
        for key, value in (("distinct", 2), ("copies", -1), ("distinct", True), ("negated", 0)):
            s = summary()
            s["entries"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_summary(s)


class CandidateTests(unittest.TestCase):
    def test_trailing_window_and_single_kind(self):
        self.assertFalse(candidates([summary(week=1)], 13)[0])
        self.assertTrue(candidates([summary(week=2)], 13)[0])
        self.assertFalse(candidates([summary(kinds=("cert_rotation",))], 1)[0])

    def test_negative_evidence_not_a_positive_kind(self):
        s = summary(kinds=("cert_rotation", "auth_failure"))
        s["entries"][1]["negated"] = True
        self.assertFalse(candidates([s], 1)[0])

    def test_fixed_cap_and_deterministic_ties(self):
        ss = []
        for i in range(12):
            s = summary()
            for r in s["entries"]:
                r["entity"] = sorted(ENTITIES)[i]
            ss.append(s)
        capped, all_candidates = candidates(ss, 1)
        self.assertEqual(len(capped), 8)
        self.assertEqual(len(all_candidates), 12)
        self.assertEqual(capped, candidates(list(reversed(ss)), 1)[0])

    def test_redebate_and_watch(self):
        c = candidates([summary()], 1)[0][0]
        for ruling in ("supported", "rejected", "hypothesis"):
            history = {c["key"]: {"handles": c["handles"], "ruling": ruling}}
            self.assertFalse(candidates([summary()], 1, history)[0])
            self.assertEqual(bool(candidates([summary()], 1, history, [c["key"]])[0]), ruling == "hypothesis")
            s = summary(week=2)
            s["entries"][0]["handles"] = ["f" * 16]
            self.assertEqual(bool(candidates([summary(), s], 2, history)[0]), ruling != "supported")

    def test_one_story_chain_selected_by_kind_count(self):
        s = summary(kinds=("cert_rotation", "auth_failure", "policy_change", "manual_workaround", "audit_finding"))
        self.assertEqual(candidates([s], 1)[0][0]["chain"], "chain_policy")


class DebateTests(unittest.TestCase):
    def test_all_roles_and_privacy(self):
        c = candidates([summary()], 1)[0]
        prior = {}
        for role in ROLES:
            p = make_prompt(role, c, prior)
            self.assertNotIn("handles\":[]", p)
            prior[role] = validate_reply(role, Fixture().complete(p), c)
        self.assertEqual(prior["judge"][c[0]["key"]]["ruling"], "supported")

    def test_bad_reply_is_refused(self):
        c = candidates([summary()], 1)[0]
        for raw in ("{}", "[]", "bad", canonical({c[0]["key"]: {"ruling": "supported"}})):
            with self.subTest(raw=raw), self.assertRaises((ValueError, TypeError)):
                validate_reply("judge", raw, c)

    def test_no_implicit_hosted_calls_and_no_remote_local_adapter(self):
        with self.assertRaises(RuntimeError):
            SessionExchange().complete("prompt")
        with self.assertRaises(ValueError):
            LocalEndpoint("https://example.com", "a-4b")

    def test_no_unbounded_prompt(self):
        c = candidates([summary()], 1)[0]
        with self.assertRaises(ValueError):
            make_prompt("judge", c, {"advocate": {c[0]["key"]: "x" * 400001}})


class GossipTests(unittest.TestCase):
    def test_hash_chain_out_of_order_replay_tamper_and_fork(self):
        secret = b"test"
        first = envelope(summary(), 1, "0" * 64, secret)
        second = envelope(summary(week=2), 2, digest(first), secret)
        node = Node("r01-site-00", secret)
        self.assertEqual(node.receive(second), "pending")
        self.assertEqual(node.receive(first), "accepted")
        self.assertEqual(len(node.messages()), 2)
        self.assertEqual(node.receive(first), "duplicate")
        self.assertEqual(len(node.messages()), 2)
        bad = copy.deepcopy(first)
        bad["summary"]["week"] = 3
        self.assertEqual(node.receive(bad), "refused")
        forged = dict(first, origin="r01-site-00")
        self.assertEqual(node.receive(forged), "refused")
        fork = envelope(summary(week=3), 2, digest(first), secret)
        self.assertEqual(node.receive(fork), "fork")
        self.assertEqual(node.forks, 1)

    def test_wrong_predecessor_is_not_applied(self):
        node = Node("r00-site-00", b"test")
        self.assertEqual(node.receive(envelope(summary(), 1, "1" * 64, b"test")), "fork")
        self.assertFalse(node.messages())


if __name__ == "__main__":
    unittest.main()
