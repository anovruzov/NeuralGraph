"""S5 and S6: the end-to-end harness, its checks and one real run.

Three kinds of test:

* the pure pieces: the oracle of section 3.5 and M20 (``expected_status``), the scans of drill 5, the report's
  checks (every criterion's assertions, each one flipped in turn so a check that stopped checking fails here), and
  the import guards of criterion 7 — which are the real c7 check, run against fresh interpreters;
* one **real run** of the harness on the EMERGENCE 200-user world against a real nats-server and a real service
  under ``E2EProcessDriver``: every drill of section 8 runs (the reference pass, the main pass in process mode with
  the site kill and M4's eligibility probe, the publish with the broker kill, the three forgeries, the lineage
  witnesses and the scan, the service rebuild, the re-ingest and the bridge rerun). It asks the first 20 candidates
  instead of 60 and 2 fixed queries per site instead of 20, which keeps the suite inside its budget; the floors of
  section 2.3 that this scale cannot reach are named in :data:`SHORT_SCALE` and nothing else may fail;
* the report: the schema's header, the runner's CPU, and that no record ref, narrative marker or narrative prefix
  is anywhere in it.

Nothing here skips: a missing nats-server fails (M11). Everything is synthetic; no assertion message carries a
record's text or ref.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest import harness as H
from research.e2e_ingest import report as rp

#: What the short-world run of :class:`LiveRunTests` cannot reach, and why. Every other assertion of every check
#: must hold. ``one_unambiguous_supported`` is a floor over the whole candidate list (section 2.3: at least one
#: unambiguous planted candidate with expected ``supported`` among the 60 asked); the first 20 by score need not
#: hold one. ``twenty_queries_per_site`` is the 20 fixed queries, of which this run asks 2.
SHORT_SCALE: dict[str, set[str]] = {
    "c1_signed": {"one_unambiguous_supported"},
    "c3_idempotent": {"twenty_queries_per_site"},
}
SHORT_CANDIDATES = 20
SHORT_QUERIES = 2


def facts(records: int, users: int, events: int, *, contradicted: bool = False,
          newest_week: str | None = "2024-W20") -> H.SiteFacts:
    return H.SiteFacts(records=records, users=users, events=events, contradicted=contradicted,
                       newest_week=newest_week)


# =================================================================================================== the oracle

class OracleTests(unittest.TestCase):
    """Section 3.5 and M20: the expected status of a planted candidate from Gold and the pack thresholds alone."""

    as_of = "2024-07-14"

    def test_two_strong_sites_inside_the_freshness_window_expect_supported(self) -> None:
        expected, ambiguous, _ = H.expected_status({"a": facts(3, 3, 3, newest_week="2024-W26"),
                                                    "b": facts(4, 4, 4, newest_week="2024-W26"),
                                                    "c": facts(0, 0, 0, newest_week=None)}, self.as_of)
        self.assertEqual((expected, ambiguous), ("supported", False))

    def test_two_strong_sites_whose_newest_week_is_older_than_the_freshness_days_expect_stale(self) -> None:
        expected, ambiguous, reason = H.expected_status({"a": facts(3, 3, 3, newest_week="2024-W10"),
                                                         "b": facts(3, 3, 3, newest_week="2024-W12")}, self.as_of)
        self.assertEqual((expected, ambiguous), ("stale", False))
        self.assertIn("2024-W12", reason)

    def test_no_strong_site_expects_hypothesis(self) -> None:
        expected, ambiguous, _ = H.expected_status({"a": facts(2, 2, 2), "b": facts(3, 2, 3),
                                                    "c": facts(3, 3, 2)}, self.as_of)
        self.assertEqual((expected, ambiguous), ("hypothesis", False))

    def test_one_strong_site_is_ambiguous_and_is_not_asserted(self) -> None:
        expected, ambiguous, reason = H.expected_status({"a": facts(3, 3, 3), "b": facts(1, 1, 1)}, self.as_of)
        self.assertEqual((expected, ambiguous), (None, True))
        self.assertIn("one strong", reason)

    def test_a_contradicted_pattern_is_ambiguous_at_any_routed_site(self) -> None:
        expected, ambiguous, reason = H.expected_status({"a": facts(5, 5, 5), "b": facts(5, 5, 5),
                                                         "c": facts(3, 3, 3, contradicted=True)}, self.as_of)
        self.assertEqual((expected, ambiguous), (None, True))
        self.assertIn("contradicted", reason)

    def test_a_site_holding_a_polarity_minus_one_record_is_not_strong(self) -> None:
        self.assertFalse(facts(9, 9, 9, contradicted=True).strong)
        self.assertTrue(facts(3, 3, 3).strong)
        for bad in (facts(2, 3, 3), facts(3, 2, 3), facts(3, 3, 2)):
            with self.subTest(records=bad.records, users=bad.users, events=bad.events):
                self.assertFalse(bad.strong)


# =================================================================================================== the scans

class ScanTests(unittest.TestCase):
    """Drill 5's three patterns, which must count 0 over the fabric's database and the stream."""

    def scan(self, text: str, prefixes: tuple[str, ...] = ()) -> dict[str, int]:
        return rp.scan_hits(text, record_ref=ew.RECORD_REF_RE, narrative_marker=ew.NARRATIVE_MARKER_RE,
                            narrative_prefixes=prefixes, prefix_length=ew.NARRATIVE_PREFIX)

    def test_a_record_ref_a_marker_and_a_narrative_prefix_are_all_found(self) -> None:
        prefix = "x" * ew.NARRATIVE_PREFIX
        found = self.scan(f'{{"ref": "em0-0012345", "text": "[d042] R01S02-DEPT-7 :: {prefix} tail"}}', (prefix,))
        self.assertEqual(found, {"record_ref_hits": 1, "narrative_marker_hits": 1, "narrative_prefix_hits": 1})

    def test_a_verdict_shaped_payload_holds_none_of_them(self) -> None:
        clean = json.dumps({"site": "r00-site-00", "verdict": "confirm", "support_bucket": "3-9",
                            "sha256": "0" * 64, "window": {"start_week": "2024-W21", "end_week": "2024-W26"}})
        self.assertEqual(self.scan(clean, ("a" * ew.NARRATIVE_PREFIX,)),
                         {"record_ref_hits": 0, "narrative_marker_hits": 0, "narrative_prefix_hits": 0})

    def test_m24_forbidden_strings_are_named_when_present(self) -> None:
        self.assertEqual(rp.forbidden_strings("allocation back-loaded", ["back-loaded", "absent"]), ["back-loaded"])
        self.assertEqual(rp.forbidden_strings("nothing here", ["absent"]), [])


# =================================================================================================== the checks

def green_report() -> dict[str, Any]:
    """A report in which every assertion of every check holds. The check tests flip one field at a time, so a
    check that stopped reading a field fails here."""
    verdict_events, conclusions, publishes = 60, 20, 80
    api_ids = publishes + conclusions
    stream = api_ids + 4                                   # the service's own kinds (M3: registration writes some)
    kinds = {"agent.event": publishes, "memory.observed": conclusions, "rule.upserted": 3, "agent.registered": 1}
    before = {"fetched": stream, "verified": stream, "foreign": 0, "stream_messages": stream, "kinds": kinds,
              "api_event_ids_in_stream": api_ids, "api_event_ids_duplicated": 0}
    after = {**before, "fetched": stream + 3, "verified": stream, "foreign": 3, "stream_messages": stream + 3}
    sites = {f"r0{i}-site-00": {"expected_records": 100, "ingested": 100, "duplicates": 0, "rejected": 0,
                                "graph_nodes": 100, "graph_nodes_db": 100, "node_ids_sha256": f"d{i}",
                                "record_refs_sha256": f"d{i}"} for i in range(7)}
    return {
        "kind": rp.KIND, "schema_version": rp.SCHEMA_VERSION, "synthetic": True, "measurement": False,
        "run": {"spike_code_hash": rp.SPIKE_CODE_HASH, "spike_code_files": rp.SPIKE_CODE_FILES},
        "fabric": {"driver_is_process_driver": True, "nats_bin": "/usr/local/bin/nats-server",
                   "keyring": {"harness_key_set": True, "key_id": "k1", "key_id_in_service_db": True},
                   "transport": "nats-jetstream", "broker_version": "2.10.22",
                   "broker_binary_version": "2.10.22", "integrity_mode": "keyed",
                   "stream_messages": stream + 3},
        "world": {"sites": 7, "records": 700, "users": 309,
                  "expected_counts": {"users": 309, "sites": 7, "records": 700}},
        "ingest": {"per_site": sites, "total_expected": 700, "total_ingested": 700, "loss": 0,
                   "pipeline_raised": False},
        "routing": {"candidates_asked": 20, "verdicts": {"budget_unknown": 0}, "route_selected_first_all": True,
                    "routes_equal_arm_order": True, "router_excluded_masked": 1, "intervention_records": 1,
                    "coordinator_class": "NeuralGraph.research.coordination.core.TesseractCoordinator",
                    "router_class": "NeuralGraph.research.coordination.core.Router"},
        "publish": {"publishes": publishes, "verdict_events": verdict_events, "conclusion_events": conclusions},
        "signatures": {"failed_before_forgery": 0, "failed_after_forgery": 3, "forged_memory_absent": True,
                       "forged_event_terminated": True, "stream_before": before, "stream_after": after,
                       "forgeries": [{"counter": 1}, {"counter": 2}, {"counter": 3}]},
        "lineage": {"conclusions": conclusions, "reconstructable": conclusions,
                    "source_events_complete": conclusions, "site_sourced_verdict_events": 55,
                    "site_sourced_answered_verdict_events": 50, "sha256_in_pd_verdicts": 55,
                    "sha256_in_wire_log": 55, "sha256_in_intake_log": 55, "sha256_in_site_egress": 55,
                    "sha256_in_site_store": 55, "site_audit_joined": 50, "records_on_or_before_as_of": True,
                    "scan": {"record_ref_hits": 0, "narrative_marker_hits": 0, "narrative_prefix_hits": 0,
                             "db_bytes": 4096, "stream_messages_scanned": stream + 3}},
        "idempotency": {"site_reingest": {"same_node_ids": True, "same_count": True, "queries_per_site": 20,
                                          "same_top10": True, "same_order": True, "ingested": 0,
                                          "duplicates": 700, "rejected": 0},
                        "bridge_rerun": {"stream_messages_before": stream + 3, "stream_messages_after": stream + 3,
                                         "new_memory_ids": 0, "new_event_ids": 0, "created": 0,
                                         "publishes": publishes, "store_unchanged": True}},
        "recovery": {"broker_kill": {"N": publishes, "k": publishes // 2, "applied_exactly_once": True,
                                     "outbox_pending": 0, "consumer_pending": 0,
                                     "outbox_pending_during_outage": 7,
                                     "transport_connected_during_outage": False,
                                     "status_during_outage": "degraded", "transport_reconnects_after": 1,
                                     "applied_delta_agent_event": publishes,
                                     "applied_delta_memory_observed": conclusions,
                                     "active_notes_after": conclusions, "stream_duplicates": 0},
                     "service_rebuild": {"conclusions_applied_before_kill": conclusions,
                                         "active_memory_ids_identical": True, "verify_digest_identical": True,
                                         "replay_seen": stream + 3, "replay_rejected": 3, "replay_blocked": False,
                                         "ready_block": None, "ready_after_rebuild": True},
                     "site_restart": {"site": "r00-site-00", "questions_before": 5, "questions_after": 5,
                                      "verdict_bytes_identical": True, "nodes_added_after_restart": 0,
                                      "answer_matrix_identical": True}},
        "discovery": {"oracle_rows": [{"question_id": "q1", "ambiguous": False, "expected": "supported",
                                       "status": "supported"},
                                      {"question_id": "q2", "ambiguous": True, "expected": None,
                                       "status": "hypothesis"}]},
        "guards": {"modules": {name: {"static": [], "fresh_interpreter": []}
                               for name in ("hq_bridge", "hq_tesseract", "publish_api")},
                   "drills": [], "spike_code_dirty": False},
    }


def put(doc: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """A copy of ``doc`` with one field replaced (the mutation a check must catch)."""
    out = json.loads(json.dumps(doc))
    node = out
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value
    return out


class CheckTests(unittest.TestCase):
    """Section 9: one check per criterion, and one mutation per assertion. ``in_ci`` is False here, so the CI-only
    broker version assertion is not part of c0 (the workflow sets ``GITHUB_ACTIONS``)."""

    def setUp(self) -> None:
        self.doc = green_report()

    def checks(self, doc: dict[str, Any]) -> dict[str, Any]:
        out = dict(rp.run_checks(doc))
        out["c0_real_fabric_keyed"] = rp.c0_real_fabric_keyed(doc, in_ci=False)
        return out

    def test_a_green_report_passes_every_check_that_the_e2e_job_runs(self) -> None:
        checks = self.checks(self.doc)
        self.assertEqual(sorted(checks), sorted(rp.E2E_CHECKS))
        for name, check in checks.items():
            with self.subTest(check=name):
                self.assertEqual(check["failed"], [], name)
                self.assertTrue(check["ok"])
                self.assertGreater(check["assertions"], 0)
        verdict = rp.verdict(checks, not_run={"c5_measured": "the measure job"})
        self.assertEqual((verdict["pass"], verdict["complete"], verdict["failed"]), (True, False, []))
        self.assertEqual(sorted(verdict["not_run"]), ["c5_measured"])

    def test_the_verdict_fails_when_any_check_that_ran_is_not_ok(self) -> None:
        broken = put(self.doc, "fabric.transport", "in-process")
        verdict = rp.verdict(self.checks(broken))
        self.assertEqual((verdict["pass"], verdict["failed"], verdict["complete"]),
                         (False, ["c0_real_fabric_keyed"], True))

    def mutations(self) -> list[tuple[str, str, str, Any]]:
        """``(check, assertion, path, value)``: one field per assertion of section 9."""
        return [
            ("c0_real_fabric_keyed", "driver_is_process_driver", "fabric.driver_is_process_driver", False),
            ("c0_real_fabric_keyed", "nats_bin_set", "fabric.nats_bin", ""),
            ("c0_real_fabric_keyed", "harness_key_set", "fabric.keyring.harness_key_set", False),
            ("c0_real_fabric_keyed", "key_id_in_service_db", "fabric.keyring.key_id_in_service_db", False),
            ("c0_real_fabric_keyed", "transport_is_jetstream", "fabric.transport", "in-process"),
            ("c0_real_fabric_keyed", "broker_version_shape", "fabric.broker_version", "unknown"),
            ("c0_real_fabric_keyed", "broker_version_is_the_binarys", "fabric.broker_binary_version", "2.11.0"),
            ("c0_real_fabric_keyed", "integrity_mode_keyed", "fabric.integrity_mode", "unkeyed"),
            ("c0_real_fabric_keyed", "stream_messages_positive", "fabric.stream_messages", 0),
            ("c1_signed", "failed_before_forgery_zero", "signatures.failed_before_forgery", 1),
            ("c1_signed", "failed_after_forgery_is_three", "signatures.failed_after_forgery", 1),
            ("c1_signed", "counter_after_each_forgery", "signatures.forgeries",
             [{"counter": 1}, {"counter": 1}, {"counter": 3}]),
            ("c1_signed", "forged_memories_absent", "signatures.forged_memory_absent", False),
            ("c1_signed", "forged_events_terminated", "signatures.forged_event_terminated", False),
            ("c1_signed", "fetched_is_stream_messages", "signatures.stream_before.fetched", 999),
            ("c1_signed", "all_verified_before_forgery", "signatures.stream_before.verified", 1),
            ("c1_signed", "three_foreign_after_forgery", "signatures.stream_after.foreign", 2),
            ("c1_signed", "api_ids_each_once", "signatures.stream_after.api_event_ids_in_stream", 99),
            ("c1_signed", "api_ids_not_duplicated", "signatures.stream_after.api_event_ids_duplicated", 1),
            ("c1_signed", "agent_event_kind_count", "signatures.stream_after.kinds",
             {"agent.event": 1, "memory.observed": 20}),
            ("c1_signed", "memory_observed_kind_count", "signatures.stream_after.kinds",
             {"agent.event": 80, "memory.observed": 1}),
            ("c1_signed", "kinds_and_foreign_are_the_stream", "signatures.stream_after.stream_messages", 999),
            ("c1_signed", "candidates_asked_floor", "routing.candidates_asked", 19),
            ("c1_signed", "verdict_events_floor", "publish.verdict_events", 59),
            ("c1_signed", "conclusion_events_floor", "publish.conclusion_events", 19),
            ("c1_signed", "no_budget_unknown", "routing.verdicts.budget_unknown", 1),
            ("c1_signed", "route_selected_before_retrieval", "routing.route_selected_first_all", False),
            ("c1_signed", "routes_are_the_arms_order", "routing.routes_equal_arm_order", False),
            ("c1_signed", "coordinator_is_tesseracts", "routing.coordinator_class", "tests.stub.Coordinator"),
            ("c1_signed", "router_is_the_routers", "routing.router_class", "tests.stub.Router"),
            ("c1_signed", "router_excluded_the_masked_site", "routing.router_excluded_masked", 0),
            ("c1_signed", "one_intervention_record", "routing.intervention_records", 0),
            ("c1_signed", "planted_statuses_equal_the_oracle", "discovery.oracle_rows",
             [{"question_id": "q1", "ambiguous": False, "expected": "supported", "status": "hypothesis"}]),
            ("c1_signed", "one_unambiguous_supported", "discovery.oracle_rows",
             [{"question_id": "q2", "ambiguous": True, "expected": None, "status": "hypothesis"}]),
            ("c2_lineage", "conclusions_floor", "lineage.conclusions", 19),
            ("c2_lineage", "conclusions_are_the_published_ones", "publish.conclusion_events", 21),
            ("c2_lineage", "all_reconstructable", "lineage.reconstructable", 19),
            ("c2_lineage", "source_events_complete", "lineage.source_events_complete", 19),
            ("c2_lineage", "witness_pd_verdicts", "lineage.sha256_in_pd_verdicts", 54),
            ("c2_lineage", "witness_wire_log", "lineage.sha256_in_wire_log", 54),
            ("c2_lineage", "witness_intake_log", "lineage.sha256_in_intake_log", 54),
            ("c2_lineage", "witness_site_egress", "lineage.sha256_in_site_egress", 54),
            ("c2_lineage", "witness_site_store", "lineage.sha256_in_site_store", 54),
            ("c2_lineage", "site_audit_joined", "lineage.site_audit_joined", 49),
            ("c2_lineage", "records_on_or_before_as_of", "lineage.records_on_or_before_as_of", False),
            ("c2_lineage", "scan_record_ref_zero", "lineage.scan.record_ref_hits", 1),
            ("c2_lineage", "scan_narrative_marker_zero", "lineage.scan.narrative_marker_hits", 1),
            ("c2_lineage", "scan_narrative_prefix_zero", "lineage.scan.narrative_prefix_hits", 1),
            ("c2_lineage", "scan_read_the_db_and_the_stream", "lineage.scan.db_bytes", 0),
            ("c3_idempotent", "same_node_ids", "idempotency.site_reingest.same_node_ids", False),
            ("c3_idempotent", "same_count", "idempotency.site_reingest.same_count", False),
            ("c3_idempotent", "twenty_queries_per_site", "idempotency.site_reingest.queries_per_site", 19),
            ("c3_idempotent", "same_top10", "idempotency.site_reingest.same_top10", False),
            ("c3_idempotent", "same_order", "idempotency.site_reingest.same_order", False),
            ("c3_idempotent", "every_record_a_duplicate", "idempotency.site_reingest.ingested", 1),
            ("c3_idempotent", "no_rejections", "idempotency.site_reingest.rejected", 1),
            ("c3_idempotent", "stream_unchanged", "idempotency.bridge_rerun.stream_messages_after", 999),
            ("c3_idempotent", "no_new_memory_ids", "idempotency.bridge_rerun.new_memory_ids", 1),
            ("c3_idempotent", "no_new_event_ids", "idempotency.bridge_rerun.new_event_ids", 1),
            ("c3_idempotent", "every_post_a_duplicate", "idempotency.bridge_rerun.created", 1),
            ("c3_idempotent", "hq_store_unchanged", "idempotency.bridge_rerun.store_unchanged", False),
            ("c4_recovery", "publishes_floor", "recovery.broker_kill.N", 79),
            ("c4_recovery", "kill_point_in_range", "recovery.broker_kill.k", 75),
            ("c4_recovery", "applied_exactly_once", "recovery.broker_kill.applied_exactly_once", False),
            ("c4_recovery", "outbox_drained", "recovery.broker_kill.outbox_pending", 1),
            ("c4_recovery", "consumer_drained", "recovery.broker_kill.consumer_pending", 1),
            ("c4_recovery", "outage_seen_by_the_outbox", "recovery.broker_kill.outbox_pending_during_outage", 0),
            ("c4_recovery", "outage_seen_by_the_transport",
             "recovery.broker_kill.transport_connected_during_outage", True),
            ("c4_recovery", "outage_seen_by_the_status", "recovery.broker_kill.status_during_outage", "ok"),
            ("c4_recovery", "reconnected_after", "recovery.broker_kill.transport_reconnects_after", 0),
            ("c4_recovery", "applied_delta_agent_event", "recovery.broker_kill.applied_delta_agent_event", 79),
            ("c4_recovery", "applied_delta_memory_observed",
             "recovery.broker_kill.applied_delta_memory_observed", 19),
            ("c4_recovery", "active_notes_after", "recovery.broker_kill.active_notes_after", 19),
            ("c4_recovery", "no_stream_duplicates", "recovery.broker_kill.stream_duplicates", 1),
            ("c4_recovery", "conclusions_applied_before_the_kill",
             "recovery.service_rebuild.conclusions_applied_before_kill", 0),
            ("c4_recovery", "same_active_memory_ids",
             "recovery.service_rebuild.active_memory_ids_identical", False),
            ("c4_recovery", "same_verify_digest", "recovery.service_rebuild.verify_digest_identical", False),
            ("c4_recovery", "replay_saw_the_stream", "recovery.service_rebuild.replay_seen", 1),
            ("c4_recovery", "replay_rejected_the_forgeries", "recovery.service_rebuild.replay_rejected", 0),
            ("c4_recovery", "replay_not_blocked", "recovery.service_rebuild.replay_blocked", True),
            ("c4_recovery", "no_ready_block", "recovery.service_rebuild.ready_block", {"reason": "x"}),
            ("c4_recovery", "ready_after_the_rebuild", "recovery.service_rebuild.ready_after_rebuild", False),
            ("c4_recovery", "site_verdict_bytes_identical",
             "recovery.site_restart.verdict_bytes_identical", False),
            ("c4_recovery", "site_killed_and_reopened", "recovery.site_restart.questions_after", 0),
            ("c4_recovery", "no_node_added_after_the_restart",
             "recovery.site_restart.nodes_added_after_restart", 1),
            ("c4_recovery", "answer_matrix_equals_the_reference",
             "recovery.site_restart.answer_matrix_identical", False),
            ("c7_guards", "three_modules_checked", "guards.modules", {"hq_bridge": {"static": [],
                                                                                   "fresh_interpreter": []}}),
            ("c7_guards", "no_forbidden_import", "guards.modules.hq_bridge",
             {"static": ["numpy"], "fresh_interpreter": []}),
            ("c7_guards", "spike_code_hash_is_the_pinned_one", "run.spike_code_hash", "0" * 64),
            ("c7_guards", "spike_code_files", "run.spike_code_files", 141),
            ("c7_guards", "spike_paths_clean", "guards.spike_code_dirty", True),
            ("c7_guards", "drills_load_no_site_or_service_module", "guards.drills", ["mycelic.service"]),
            ("c8_emergence_load", "one_graph_per_site", "world.sites", 6),
            ("c8_emergence_load", "every_site_whole", "ingest.per_site.r00-site-00.graph_nodes_db", 99),
            ("c8_emergence_load", "totals_agree", "ingest.total_ingested", 699),
            ("c8_emergence_load", "no_loss", "ingest.loss", 1),
            ("c8_emergence_load", "pipeline_raised_nothing", "ingest.pipeline_raised", True),
            ("c8_emergence_load", "world_is_section_3_1", "world.users", 310),
        ]

    def test_every_assertion_of_every_check_catches_its_own_mutation(self) -> None:
        for check, assertion, path, value in self.mutations():
            with self.subTest(check=check, assertion=assertion):
                result = self.checks(put(self.doc, path, value))[check]
                self.assertFalse(result["ok"], f"{check}/{assertion} passed a mutated report")
                self.assertIn(assertion, result["failed"])

    def test_every_assertion_of_every_check_has_a_mutation(self) -> None:
        """One mutation per assertion, so no assertion of section 9 goes unexercised. ``in_ci`` is False here, so
        c0's CI-only broker-version assertion is not among them."""
        covered: dict[str, set[str]] = {}
        for check, assertion, _, _ in self.mutations():
            covered.setdefault(check, set()).add(assertion)
        for name, check in self.checks(self.doc).items():
            with self.subTest(check=name):
                self.assertEqual(len(covered.get(name, set())), check["assertions"])


# =================================================================================================== criterion 7

class GuardTests(unittest.TestCase):
    """Criterion 7 for real: the HQ-side and bridge modules against ``FORBIDDEN_HQ`` plus the site module (and
    ``mycelic.service`` for ``publish_api``), statically and in a fresh interpreter."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = H.guard_report()

    def test_no_guarded_module_imports_a_forbidden_module(self) -> None:
        self.assertEqual(sorted(self.report["modules"]), ["hq_bridge", "hq_tesseract", "publish_api"])
        for name, found in self.report["modules"].items():
            with self.subTest(module=name):
                self.assertEqual(found["static"], [])
                self.assertEqual(found["fresh_interpreter"], [])
                self.assertIn("research.e2e_ingest.site_graph", found["forbidden"])
        self.assertIn("mycelic.service", self.report["modules"]["publish_api"]["forbidden"])

    def test_the_drills_load_no_site_module_numpy_retrieval_or_service(self) -> None:
        self.assertEqual(self.report["drills"], [])

    def test_the_static_scan_resolves_this_packages_relative_imports(self) -> None:
        path = H.ROOT / "research" / "e2e_ingest" / "hq_bridge.py"
        named = H.static_imports(path, "research.e2e_ingest")
        self.assertIn("research.e2e_ingest.publish_api", named)        # ``from .publish_api import ...``
        self.assertIn("mycelic.collective.detect.store", named)

    def test_a_module_that_imports_a_forbidden_module_is_caught_in_both_ways(self) -> None:
        """The guard itself: a module that does import numpy and the service is named by both halves of the check
        (a check that cannot fail is no check). The probe package is written under this one and removed again."""
        package = H.ROOT / "research" / "e2e_ingest" / "guard_probe"
        try:
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            module = package / "probe.py"
            module.write_text("import numpy  # noqa: F401\nfrom mycelic.service import MycelicService  # noqa\n",
                              encoding="utf-8")
            named = H.static_imports(module, "research.e2e_ingest")
            self.assertEqual([m for m in named if m in H.FORBIDDEN_HQ or m == "mycelic.service"],
                             ["mycelic.service", "numpy"])
            loaded = H.imports_loaded("research.e2e_ingest.guard_probe.probe", ("numpy", "mycelic.service"))
            self.assertEqual(loaded, ["mycelic.service", "numpy"])
        finally:
            shutil.rmtree(package, ignore_errors=True)

    def test_the_spike_code_hash_is_the_value_the_brief_pinned(self) -> None:
        from research.routing_spike.run import spike_code_files, spike_code_hash
        self.assertEqual(spike_code_hash(), rp.SPIKE_CODE_HASH)
        self.assertEqual(len(spike_code_files()), rp.SPIKE_CODE_FILES)
        self.assertIs(self.report["spike_code_dirty"], False)


# =================================================================================================== the live run

class LiveRunTests(unittest.TestCase):
    """One real run: the EMERGENCE 200-user world, seven site processes, Tesseract routing, a real nats-server and
    a real service, every drill of section 8. The first 20 candidates and 2 fixed queries per site keep it inside
    the suite's budget; :data:`SHORT_SCALE` names what that scale cannot reach."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(prefix="e2e-harness-")
        out = Path(cls.tmp.name) / "run"
        options = H.Options(out=out, world=200, seed=0, mode="process", top_n=SHORT_CANDIDATES,
                            queries=SHORT_QUERIES, run_id="suite", jobs=4)
        cls.harness = H.E2ERun(options=options)
        cls.doc = cls.harness.run()
        cls.path = out / rp.REPORT_NAME
        cls.sha = rp.write(cls.path, cls.doc)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    # ---- the checks
    def test_only_the_floors_this_scale_cannot_reach_fail(self) -> None:
        for name, check in self.doc["checks"].items():
            with self.subTest(check=name):
                self.assertLessEqual(set(check["failed"]), SHORT_SCALE.get(name, set()),
                                     f"{name} failed assertions this scale should reach")
        self.assertEqual(sorted(self.doc["checks"]), sorted(rp.E2E_CHECKS))
        self.assertEqual(sorted(self.doc["verdict"]["not_run"]),
                         ["c10_suites_green", "c5_measured", "c6_ci", "c9_benchmark"])
        self.assertFalse(self.doc["verdict"]["complete"])

    def test_c0_the_fabric_is_real_and_keyed(self) -> None:
        fabric = self.doc["fabric"]
        self.assertTrue(fabric["driver_is_process_driver"])
        self.assertEqual(fabric["transport"], "nats-jetstream")
        self.assertEqual(fabric["integrity_mode"], "keyed")
        self.assertTrue(fabric["keyring"]["harness_key_set"])
        self.assertIn(fabric["keyring"]["key_id"], fabric["keyring"]["known_key_ids"])
        self.assertTrue(re.match(r"^\d+\.\d+\.\d+", fabric["broker_version"]))
        self.assertEqual(fabric["broker_version"], fabric["broker_binary_version"])
        self.assertEqual(fabric["max_active_memories_per_org"], 5000)
        self.assertEqual(fabric["replay_max_reject_ratio"], 0.05)
        self.assertEqual(fabric["org_id"], "emergence")
        self.assertEqual(fabric["agent_id"], "hq")

    def test_c1_every_event_is_signed_in_the_stream_once_and_the_forgeries_are_terminated(self) -> None:
        s, p = self.doc["signatures"], self.doc["publish"]
        before, after = s["stream_before"], s["stream_after"]
        self.assertEqual(s["failed_before_forgery"], 0)
        self.assertEqual([f["counter"] for f in s["forgeries"]], [1, 2, 3])
        self.assertTrue(all(f["memory_absent"] for f in s["forgeries"]))
        self.assertEqual(before["verified"], before["fetched"])
        self.assertEqual(before["foreign"], 0)
        self.assertEqual(after["foreign"], 3)
        self.assertEqual(after["verified"], after["fetched"] - 3)
        self.assertEqual(after["api_event_ids_in_stream"], p["publishes"] + p["conclusion_events"])
        self.assertEqual(after["api_event_ids_duplicated"], 0)
        self.assertEqual(after["kinds"]["agent.event"], p["publishes"])
        self.assertEqual(after["kinds"]["memory.observed"], p["conclusion_events"])
        self.assertEqual(after["stream_messages"], sum(after["kinds"].values()) + after["foreign"])

    def test_tesseract_routed_every_question_and_the_router_excluded_the_masked_site(self) -> None:
        r = self.doc["routing"]
        self.assertEqual(r["coordinator_class"], "NeuralGraph.research.coordination.core.TesseractCoordinator")
        self.assertEqual(r["router_class"], "NeuralGraph.research.coordination.core.Router")
        self.assertEqual(r["candidates_asked"], SHORT_CANDIDATES)
        self.assertEqual(r["m"], 3)
        self.assertIsNone(r["eval_window"])                            # M21
        self.assertTrue(r["route_selected_first_all"])
        self.assertTrue(r["routes_equal_arm_order"])
        for q in r["questions_detail"]:
            with self.subTest(question=q["question_id"][:12]):
                self.assertEqual(q["hq_eligible"], self.doc["world"]["sites"])
                self.assertEqual(q["route_size"], min(3, q["hq_eligible"]))
                trace = q["trace"]
                self.assertIn("route_selected", trace)
                self.assertIn("retrieval_selected", trace)
                self.assertLess(trace.index("route_selected"), trace.index("retrieval_selected"))
        probe = r["probe"]
        self.assertIn(probe["masked_site"], probe["unmasked_route"])
        self.assertNotIn(probe["masked_site"], probe["route"])
        self.assertEqual((probe["route_size"], probe["hq_eligible"]), (3, self.doc["world"]["sites"]))
        self.assertEqual(probe["verdict_sources"], ["site"])           # no hq error record: nobody asked the dead one
        self.assertEqual((probe["failure_injected"], probe["interventions_ended"]), (1, 1))
        self.assertEqual((r["router_excluded_masked"], r["intervention_records"]), (1, 1))
        self.assertGreater(r["tesseract"]["rankings"], 0)
        self.assertGreater(r["tesseract"]["seconds_p50"], 0.0)
        self.assertEqual(r["verdicts"]["budget_unknown"], 0)

    def test_c2_every_conclusion_has_five_witnesses_per_verdict_and_the_scans_are_zero(self) -> None:
        ln = self.doc["lineage"]
        self.assertEqual(ln["conclusions"], self.doc["publish"]["conclusion_events"])
        self.assertEqual(ln["reconstructable"], ln["conclusions"])
        self.assertEqual(ln["source_events_complete"], ln["conclusions"])
        for witness in ("sha256_in_pd_verdicts", "sha256_in_wire_log", "sha256_in_intake_log",
                        "sha256_in_site_egress", "sha256_in_site_store"):
            with self.subTest(witness=witness):
                self.assertEqual(ln[witness], ln["site_sourced_verdict_events"])
        self.assertEqual(ln["site_audit_joined"], ln["site_sourced_answered_verdict_events"])
        self.assertTrue(ln["records_on_or_before_as_of"])
        self.assertEqual(ln["records_after_as_of"], 0)
        self.assertEqual((ln["scan"]["record_ref_hits"], ln["scan"]["narrative_marker_hits"],
                          ln["scan"]["narrative_prefix_hits"]), (0, 0, 0))
        self.assertGreater(ln["scan"]["stream_messages_scanned"], 0)
        self.assertGreater(ln["scan"]["db_bytes"], 0)
        self.assertEqual(ln["scan"]["prefixes"], len(self.harness.prefixes))

    def test_c3_and_c4_the_drills(self) -> None:
        si = self.doc["idempotency"]["site_reingest"]
        br = self.doc["idempotency"]["bridge_rerun"]
        bk = self.doc["recovery"]["broker_kill"]
        sr = self.doc["recovery"]["service_rebuild"]
        st = self.doc["recovery"]["site_restart"]
        self.assertEqual((si["ingested"], si["rejected"]), (0, 0))
        self.assertEqual(si["duplicates"], self.doc["world"]["records"])
        self.assertTrue(si["same_node_ids"] and si["same_count"] and si["same_top10"] and si["same_order"])
        self.assertEqual(sorted(si["nodes_added_on_resync"].values()), [0] * self.doc["world"]["sites"])
        self.assertEqual((br["created"], br["new_memory_ids"], br["new_event_ids"]), (0, 0, 0))
        self.assertEqual(br["stream_messages_after"], br["stream_messages_before"])
        self.assertTrue(br["notes_identical"] and br["store_unchanged"])
        self.assertGreaterEqual(bk["N"], rp.FLOOR_PUBLISHES)
        self.assertTrue(1 <= bk["k"] <= bk["N"] - 10)
        self.assertTrue(bk["applied_exactly_once"])
        self.assertGreaterEqual(bk["outbox_pending_during_outage"], 1)
        self.assertIs(bk["transport_connected_during_outage"], False)
        self.assertEqual(bk["status_during_outage"], "degraded")
        self.assertEqual(bk["applied_delta_agent_event"], bk["N"])
        self.assertEqual(sr["replay_rejected"], 3)
        self.assertIs(sr["replay_blocked"], False)
        self.assertTrue(sr["active_memory_ids_identical"] and sr["verify_digest_identical"])
        self.assertTrue(sr["ready_after_rebuild"])
        self.assertTrue(st["killed"])
        self.assertEqual(st["nodes_added_after_restart"], 0)
        self.assertTrue(st["verdict_bytes_identical"])
        self.assertTrue(st["answer_matrix_identical"])
        self.assertEqual(st["answer_matrix_mismatches"], 0)

    def test_c8_the_world_is_loaded_whole_at_every_site(self) -> None:
        w, ing = self.doc["world"], self.doc["ingest"]
        self.assertEqual((w["users"], w["sites"], w["records"]), (309, 7, 12964))
        self.assertEqual(w["expected_counts"], dict(ew.WORLD_TABLE[200]))
        self.assertEqual(ing["total_ingested"], ing["total_expected"])
        self.assertEqual(ing["loss"], 0)
        self.assertIs(ing["pipeline_raised"], False)
        for site, row in ing["per_site"].items():
            with self.subTest(site=site):
                self.assertEqual(row["ingested"], row["expected_records"])
                self.assertEqual(row["graph_nodes_db"], row["expected_records"])
                self.assertEqual(row["graph_nodes"], row["graph_nodes_db"])
                self.assertEqual(row["node_ids_sha256"], row["record_refs_sha256"])
                self.assertEqual(row["graph_watermark"], row["expected_records"])

    def test_the_discovery_score_is_computed_from_the_fabric_and_gold(self) -> None:
        d = self.doc["discovery"]
        self.assertEqual(d["gold_discoverable"], 40)
        self.assertEqual(d["candidates_true"] + d["candidates_decoy"] + d["candidates_background"],
                         SHORT_CANDIDATES)
        self.assertIsNone(d["simulator"])
        self.assertTrue(d["measurement"] is False and d["synthetic"] is True)
        for row in d["oracle_rows"]:
            with self.subTest(question=row["question_id"][:12]):
                self.assertEqual(sorted(row["per_site_facet_counts"]), sorted(row["route"]))
                self.assertIn(row["status"], ("supported", "hypothesis", "stale", "contested", "rejected"))
                if not row["ambiguous"]:
                    self.assertEqual(row["status"], row["expected"])

    # ---- the report
    def test_the_report_carries_the_schema_the_runner_and_the_stamps(self) -> None:
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(doc["checks"], self.doc["checks"])           # the file is the report, written atomically
        self.assertEqual((doc["kind"], doc["schema_version"]), (rp.KIND, rp.SCHEMA_VERSION))
        self.assertIs(doc["synthetic"], True)
        self.assertIs(doc["measurement"], False)
        self.assertEqual(doc["run"]["job"], "e2e")
        self.assertEqual(doc["run"]["spike_code_hash"], rp.SPIKE_CODE_HASH)
        self.assertIsNotNone(doc["runner"]["cpu_model"])
        self.assertGreater(doc["runner"]["cpu_count"], 0)
        self.assertEqual(doc["run"]["code_dirty_paths"], list(rp.DIRTY_PATHS))
        self.assertIsNotNone(doc["run"]["code_commit"])
        self.assertGreater(doc["run"]["seconds"], 0.0)
        self.assertIn("world_seconds", doc["timings"])

    def test_the_report_holds_no_record_ref_no_narrative_and_no_tier_label(self) -> None:
        text = self.path.read_text(encoding="utf-8")
        found = rp.scan_hits(text, record_ref=ew.RECORD_REF_RE, narrative_marker=ew.NARRATIVE_MARKER_RE,
                             narrative_prefixes=self.harness.prefixes, prefix_length=ew.NARRATIVE_PREFIX)
        self.assertEqual(found, {"record_ref_hits": 0, "narrative_marker_hits": 0, "narrative_prefix_hits": 0})
        # M24: no tier label of the simulator's allocations is in the report (the simulator's own columns are the
        # measure job's, and the checkpoint name of ``models.py:8`` may not be written into code at all)
        self.assertEqual(rp.forbidden_strings(text, ["small-7b", "mid-14b", "mid-32b", "large-70b", "edge-3b",
                                                      "frontier", "back-loaded"]), [])
        # the pack's ``never_fields`` are ``narrative`` and ``reporter``: the only "narrative" in the report is the
        # name of drill 5's own two counters, and "reporter" appears only as a bucket name
        rest = text.replace("narrative_marker_hits", "").replace("narrative_prefix_hits", "")
        self.assertNotIn("narrative", rest)
        self.assertEqual(rp.forbidden_strings(text.replace("reporters_bucket", ""), ["reporter"]), [])

    def test_the_summary_names_every_check_and_the_verdict(self) -> None:
        text = H.summary(self.doc)
        self.assertIn("measurement: false", text)
        for name in self.doc["checks"]:
            self.assertIn(name, text)
        for name in self.doc["verdict"]["not_run"]:
            self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main()
