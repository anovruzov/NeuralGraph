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

import inspect
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
from research.e2e_ingest import suites as su
from tests.e2e_ingest.helpers import suite_rows

#: What the short-world run of :class:`LiveRunTests` cannot reach, and why. Every other assertion of every check
#: must hold. ``one_unambiguous_supported`` is a floor over the whole candidate list (section 2.3: at least one
#: unambiguous planted candidate with expected ``supported`` among the 60 asked): the floor holds at 55 candidates
#: (the e2e job, which asks every candidate the 200-user world detects; the build review's run had 7 expected
#: supported and 0 mismatches); the first 20 by score need not hold one, so the full floor is the e2e job's.
#: ``twenty_queries_per_site`` is the 20 fixed queries, of which this run asks 2.
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
              "api_event_ids_in_stream": api_ids, "api_event_ids_duplicated": 0, "first_seq": 1,
              "last_seq": stream, "status_first_seq": 1, "status_last_seq": stream}
    after = {**before, "fetched": stream + 3, "verified": stream, "foreign": 3, "stream_messages": stream + 3,
             "last_seq": stream + 3, "status_last_seq": stream + 3}
    sites = {f"r0{i}-site-00": {"expected_records": 100, "ingested": 100, "duplicates": 0, "rejected": 0,
                                "graph_nodes": 100, "graph_nodes_db": 100, "node_ids_sha256": f"d{i}",
                                "record_refs_sha256": f"d{i}"} for i in range(7)}
    gold_patterns = [f"p{i:03d}" for i in range(40)]
    simulator = {"simulated": True, "synthetic": True, "measurement": False, "allocation": "back-loaded",
                 "architectures": {a: {"discovery_recall": 0.5, "rare_signal_recall": 0.1,
                                       "decoy_acceptance_all": 0.2, "recall_at_100": 0.5, "compute_units": 1.0e5,
                                       "seconds": 0.4} for a in rp.SIMULATOR_ARCHITECTURES}}
    route = ["r00-site-00", "r01-site-00", "r02-site-00"]
    # M4 check 3's witnesses of the mask: the masked site is in the unmasked arm-R route and not in the chosen one
    probe = {"question_id": "qp", "masked_site": "r00-site-00", "unmasked_route": route,
             "route": ["r01-site-00", "r02-site-00", "r03-site-00"], "route_size": 3, "hq_eligible": 7,
             "excluded": True, "verdict_sources": ["site"], "failure_injected": 1, "intervention_records": 1,
             "interventions_ended": 1, "candidate_source": "reused"}
    # the oracle's inputs (M12: ``expected`` and ``ambiguous`` are recomputable): two strong sites inside the
    # freshness window expect supported; one strong site is ambiguous
    strong = {"records": 3, "users": 3, "events": 3, "contradicted": False, "newest_week": "2024-W26",
              "strong": True}
    weak = {"records": 1, "users": 1, "events": 1, "contradicted": False, "newest_week": "2024-W26",
            "strong": False}
    empty = {"records": 0, "users": 0, "events": 0, "contradicted": False, "newest_week": None, "strong": False}
    trace = ["route_selected", "retrieval_selected", "retrieval_selected", "retrieval_selected"]
    clean = {"ok": True, "overlap_bytes": 0, "canary_hits": 0, "scanned_bytes": 4096, "scanned_items": 9}
    return {
        "kind": rp.KIND, "schema_version": rp.SCHEMA_VERSION, "synthetic": True, "measurement": False,
        "run": {"spike_code_hash": rp.SPIKE_CODE_HASH, "spike_code_files": rp.SPIKE_CODE_FILES,
                "code_commit": "a" * 40, "code_dirty_start": False, "code_dirty_end": False, "mode": "process",
                "suites": suite_rows("a" * 40), "suites_evidence": su.EVIDENCE, "needs_results": None,
                "github_run_id": None},
        "fabric": {"driver_is_process_driver": True, "nats_bin": "/usr/local/bin/nats-server",
                   "keyring": {"harness_key_set": True, "key_id": "k1", "key_id_in_service_db": True},
                   "transport": "nats-jetstream", "broker_version": "2.10.22",
                   "broker_binary_version": "2.10.22", "integrity_mode": "keyed",
                   "stream_messages": stream + 3},
        "world": {"sites": 7, "records": 700, "users": 309, "target_users": 200,
                  "expected_counts": {"users": 309, "sites": 7, "records": 700},
                  # seed 0's draw is section 3.1's row, so the diff a run reports is empty
                  "table_row_diff": [],},
        "ingest": {"per_site": sites, "total_expected": 700, "total_ingested": 700, "loss": 0,
                   "pipeline_raised": False, "seconds_per_1k_records": 0.3},
        "routing": {"candidates_asked": 20, "verdicts": {"budget_unknown": 0}, "route_selected_first_all": True,
                    "routes_equal_arm_order": True, "per_question_route_ok": True, "m": 3,
                    "hq_eligible_expected": 7, "questions_asked": 2,
                    "questions": [{"question_id": "q1", "hq_eligible": 7, "route": route, "route_size": 3,
                                   "status": "supported", "trace": trace},
                                  {"question_id": "q2", "hq_eligible": 7, "route": route, "route_size": 3,
                                   "status": "hypothesis", "trace": trace}],
                    "router_excluded_masked": 1, "intervention_records": 1, "probe": probe,
                    "coordinator_class": "NeuralGraph.research.coordination.core.TesseractCoordinator",
                    "router_class": "NeuralGraph.research.coordination.core.Router",
                    "tesseract": {"rows_equal_read_verdicts": True, "seconds_p50": 0.7, "seconds_p95": 1.2},
                    "question_to_verdict": {"per_site_p50": 0.7, "per_site_p95": 1.3, "per_question_p50": 2.2,
                                            "per_question_p95": 3.4}},
        "publish": {"publishes": publishes, "verdict_events": verdict_events, "conclusion_events": conclusions,
                    "created_to_applied": {"p50": 0.5, "p95": 1.5},
                    "created_to_applied_before_kill": {"p50": 0.5, "p95": 1.5, "k": publishes // 2}},
        "signatures": {"failed_before_forgery": 0, "failed_after_forgery": 3, "forged_memory_absent": True,
                       "forged_event_terminated": True, "stream_before": before, "stream_after": after,
                       "forgeries": [{"counter": 1}, {"counter": 2}, {"counter": 3}]},
        "lineage": {"conclusions": conclusions, "reconstructable": conclusions,
                    "source_events_complete": conclusions, "site_sourced_verdict_events": 55,
                    "site_sourced_answered_verdict_events": 50, "sha256_in_fabric_event": 55,
                    "sha256_in_pd_verdicts": 55,
                    "sha256_in_wire_log": 55, "sha256_in_intake_log": 55, "sha256_in_site_egress": 55,
                    "sha256_in_site_store": 55, "site_audit_joined": 50, "records_on_or_before_as_of": True,
                    "unknown_refs": 0,
                    "scan": {"record_ref_hits": 0, "narrative_marker_hits": 0, "narrative_prefix_hits": 0,
                             "db_bytes": 4096, "stream_messages_scanned": stream + 3},
                    "crossing": {"main": {"leakage": dict(clean),
                                          "crossing": {"ok": True, "problems": [], "hq_intake_refusals": 0,
                                                       "request_kinds": {"describe": 7, "question": 60},
                                                       "receive_types": {"verdict": 60}, "descriptor_rows": 14}},
                                 "pipeline": {"leakage": dict(clean)}}},
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
                                      "answer_matrix_identical": True, "killed": True}},
        "discovery": {"synthetic": True, "measurement": False, "gold_discoverable": 40, "gold_rare": 18,
                      "gold_common": 22, "gold_discoverable_patterns": gold_patterns,
                      "decoys_by_type": {"1": 10, "2": 10, "3": 10, "4": 10, "5": 10},
                      "candidates_true": 5, "candidates_decoy": 12, "candidates_background": 3,
                      "found_patterns": ["p001", "p007"], "recall_all": 2 / 40, "recall_common": 2 / 22,
                      "recall_rare": 0.0, "decoy_accepted_by_type": {"2": 1}, "background_supported": 0,
                      "simulator": simulator, "simulator_reason": None,
                      "oracle_rows": [{"question_id": "q1", "ambiguous": False, "expected": "supported",
                                       "status": "supported", "as_of": "2024-07-14", "route": route,
                                       "per_site_facet_counts": {route[0]: dict(strong), route[1]: dict(strong),
                                                                 route[2]: dict(empty)}},
                                      {"question_id": "q2", "ambiguous": True, "expected": None,
                                       "status": "hypothesis", "as_of": "2024-07-14", "route": route,
                                       "per_site_facet_counts": {route[0]: dict(strong), route[1]: dict(weak),
                                                                 route[2]: dict(empty)}}]},
        "guards": {"modules": {name: {"static": [], "fresh_interpreter": []}
                               for name in ("hq_bridge", "hq_tesseract", "publish_api")},
                   "drills": [], "spike_code_dirty": False},
    }


DROP = object()      # the mutation that removes a field instead of replacing it


def put(doc: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """A copy of ``doc`` with one field replaced, or removed when ``value`` is :data:`DROP` (the mutation a check
    must catch)."""
    out = json.loads(json.dumps(doc))
    node = out
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    last: Any = int(parts[-1]) if isinstance(node, list) else parts[-1]
    if value is DROP:
        del node[last]
    else:
        node[last] = value
    return out


class CheckTests(unittest.TestCase):
    """Section 9: one check per criterion, and one mutation per assertion. ``in_ci`` is False here, so the CI-only
    broker version assertion is not part of c0 (the workflow sets ``GITHUB_ACTIONS``)."""

    def setUp(self) -> None:
        self.doc = green_report()

    def checks(self, doc: dict[str, Any]) -> dict[str, Any]:
        out = dict(rp.run_checks(doc))
        out["c0_real_fabric_keyed"] = rp.c0_real_fabric_keyed(doc, in_ci=False)
        out["c10_suites_green"] = rp.c10_suites_green(doc, in_ci=False)
        return out

    def test_a_green_report_passes_every_check_that_the_e2e_job_runs(self) -> None:
        checks = self.checks(self.doc)
        self.assertEqual(sorted(checks), sorted(rp.E2E_CHECKS))
        for name, check in checks.items():
            with self.subTest(check=name):
                self.assertEqual(check["failed"], [], name)
                self.assertTrue(check["ok"])
                self.assertGreater(check["assertions"], 0)
        verdict = rp.verdict(checks, not_run={"c5_measured": "the measure job"}, suites_green=True)
        self.assertEqual((verdict["pass"], verdict["complete"], verdict["failed"]), (True, False, []))
        self.assertEqual(sorted(verdict["not_run"]), ["c5_measured"])

    def test_the_verdict_fails_when_any_check_that_ran_is_not_ok(self) -> None:
        broken = put(self.doc, "fabric.transport", "in-process")
        verdict = rp.verdict(self.checks(broken), suites_green=True)
        self.assertEqual((verdict["pass"], verdict["checks_pass"], verdict["failed"], verdict["complete"]),
                         (False, False, ["c0_real_fabric_keyed"], True))

    def test_a_run_without_the_suites_junit_evidence_does_not_count(self) -> None:
        """M2: green checks with no junit evidence of the suites is ``pass`` false, reason ``suites_not_green``."""
        verdict = rp.verdict(self.checks(self.doc))
        self.assertEqual((verdict["pass"], verdict["checks_pass"], verdict["reason"], verdict["suites_green"]),
                         (False, True, rp.SUITES_NOT_GREEN, False))
        self.assertEqual(rp.verdict(self.checks(self.doc), suites_green=True)["reason"], None)

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
            ("c1_signed", "stream_bounds_agree", "signatures.stream_after.status_last_seq", 1),
            ("c1_signed", "candidates_asked_floor", "routing.candidates_asked", 19),
            ("c1_signed", "verdict_events_floor", "publish.verdict_events", 59),
            ("c1_signed", "conclusion_events_floor", "publish.conclusion_events", 19),
            ("c1_signed", "no_budget_unknown", "routing.verdicts.budget_unknown", 1),
            ("c1_signed", "route_selected_before_retrieval", "routing.route_selected_first_all", False),
            ("c1_signed", "routes_are_the_arms_order", "routing.routes_equal_arm_order", False),
            ("c1_signed", "every_question_eligible_and_routed", "routing.per_question_route_ok", False),
            ("c1_signed", "coordinator_is_tesseracts", "routing.coordinator_class", "tests.stub.Coordinator"),
            ("c1_signed", "router_is_the_routers", "routing.router_class", "tests.stub.Router"),
            ("c1_signed", "router_excluded_the_masked_site", "routing.router_excluded_masked", 0),
            ("c1_signed", "one_intervention_record", "routing.intervention_records", 0),
            ("c1_signed", "probe_masked_site_in_unmasked_route", "routing.probe.unmasked_route",
             ["r01-site-00", "r02-site-00", "r03-site-00"]),
            ("c1_signed", "probe_route_size_m", "routing.probe.route_size", 2),
            ("c1_signed", "probe_hq_eligible_all_sites", "routing.probe.hq_eligible", 6),
            ("c1_signed", "probe_no_hq_record", "routing.probe.verdict_sources", ["hq", "site"]),
            ("c1_signed", "probe_one_failure_injected", "routing.probe.failure_injected", 0),
            ("c1_signed", "probe_intervention_ended", "routing.probe.interventions_ended", 0),
            ("c1_signed", "tesseract_row_per_read_verdict", "routing.tesseract.rows_equal_read_verdicts", False),
            ("c1_signed", "planted_statuses_equal_the_oracle", "discovery.oracle_rows",
             [{"question_id": "q1", "ambiguous": False, "expected": "supported", "status": "hypothesis"}]),
            ("c1_signed", "one_unambiguous_supported", "discovery.oracle_rows",
             [{"question_id": "q2", "ambiguous": True, "expected": None, "status": "hypothesis"}]),
            ("c2_lineage", "conclusions_floor", "lineage.conclusions", 19),
            ("c2_lineage", "conclusions_are_the_published_ones", "publish.conclusion_events", 21),
            ("c2_lineage", "all_reconstructable", "lineage.reconstructable", 19),
            ("c2_lineage", "source_events_complete", "lineage.source_events_complete", 19),
            ("c2_lineage", "witness_fabric_event", "lineage.sha256_in_fabric_event", 54),
            ("c2_lineage", "witness_pd_verdicts", "lineage.sha256_in_pd_verdicts", 54),
            ("c2_lineage", "witness_wire_log", "lineage.sha256_in_wire_log", 54),
            ("c2_lineage", "witness_intake_log", "lineage.sha256_in_intake_log", 54),
            ("c2_lineage", "witness_site_egress", "lineage.sha256_in_site_egress", 54),
            ("c2_lineage", "witness_site_store", "lineage.sha256_in_site_store", 54),
            ("c2_lineage", "site_audit_joined", "lineage.site_audit_joined", 49),
            ("c2_lineage", "records_on_or_before_as_of", "lineage.records_on_or_before_as_of", False),
            ("c2_lineage", "every_audited_ref_dated", "lineage.unknown_refs", 1),
            ("c2_lineage", "scan_record_ref_zero", "lineage.scan.record_ref_hits", 1),
            ("c2_lineage", "scan_narrative_marker_zero", "lineage.scan.narrative_marker_hits", 1),
            ("c2_lineage", "scan_narrative_prefix_zero", "lineage.scan.narrative_prefix_hits", 1),
            ("c2_lineage", "scan_read_the_db_and_the_stream", "lineage.scan.db_bytes", 0),
            ("c2_lineage", "crossing_leakage_zero", "lineage.crossing.pipeline.leakage.overlap_bytes", 24),
            ("c2_lineage", "crossing_leakage_zero", "lineage.crossing.main.leakage.canary_hits", 1),
            ("c2_lineage", "crossing_leakage_zero", "lineage.crossing.main.leakage.scanned_items", 0),
            ("c2_lineage", "crossing_leakage_zero", "lineage.crossing.pipeline", DROP),
            ("c2_lineage", "crossing_shapes_ok", "lineage.crossing.main.crossing.ok", False),
            ("c2_lineage", "crossing_shapes_ok", "lineage.crossing.main.crossing.hq_intake_refusals", 1),
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
            ("c4_recovery", "main_pass_in_process_mode", "run.mode", "in_process"),
            ("c4_recovery", "site_process_sigkilled", "recovery.site_restart.killed", False),
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
            ("c7_guards", "three_modules_checked", "guards.modules",
             {"hq_bridge": {"static": [], "fresh_interpreter": []}}),
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
            ("c8_emergence_load", "world_invariants_are_section_3_1", "world.users", 310),
            # a draw report that names a count the draw may not move is not a draw report
            ("c8_emergence_load", "world_draw_is_reported", "world.table_row_diff", ["users: 309 -> 310"]),
            ("c9_benchmark", "every_discovery_field_present", "discovery.decoy_accepted_by_type", DROP),
            ("c9_benchmark", "found_patterns_are_gold_discoverable", "discovery.found_patterns", ["p001", "x999"]),
            ("c9_benchmark", "recall_all_is_found_over_gold", "discovery.recall_all", 0.5),
            ("c9_benchmark", "per_hop_numbers_numeric", "routing.tesseract.seconds_p50", None),
            ("c9_benchmark", "simulator_columns_present", "discovery.simulator", None),
            ("c9_benchmark", "stamps_synthetic_not_measured", "discovery.measurement", True),
            ("c10_suites_green", "evidence_is_junit", "run.suites_evidence", None),
            ("c10_suites_green", "code_clean_at_start", "run.code_dirty_start", True),
            ("c10_suites_green", "code_clean_at_end", "run.code_dirty_end", True),
            *[m for i, s in enumerate(su.SUITES) for m in (
                ("c10_suites_green", f"{s.slug}:present", f"run.suites.{i}.suite", "another"),
                ("c10_suites_green", f"{s.slug}:sha_is_the_commit", f"run.suites.{i}.sha", "c" * 40),
                ("c10_suites_green", f"{s.slug}:no_failures", f"run.suites.{i}.failures", 1),
                ("c10_suites_green", f"{s.slug}:no_errors", f"run.suites.{i}.errors", 1),
                ("c10_suites_green", f"{s.slug}:tests_at_or_above_floor", f"run.suites.{i}.tests_distinct",
                 s.floor - 1),
                ("c10_suites_green", f"{s.slug}:junit_bound", f"run.suites.{i}.junit_bound", False),
                *([("c10_suites_green", f"{s.slug}:no_skips_in_scope", f"run.suites.{i}.skipped_in_scope", 1)]
                  if s.zero_skip_scope is not None else []))],
        ]

    def test_c9_accepts_null_simulator_columns_only_at_the_largest_size_with_the_budget_reason(self) -> None:
        """Section 10: the simulator's columns may be null with ``reason: budget`` at the 2,000-user size only."""
        null = put(put(self.doc, "discovery.simulator", None), "discovery.simulator_reason", "budget")
        self.assertIn("simulator_columns_present", rp.c9_benchmark(null)["failed"])
        at_2000 = put(null, "world.target_users", rp.SIMULATOR_BUDGET_SIZE)
        self.assertNotIn("simulator_columns_present", rp.c9_benchmark(at_2000)["failed"])
        half = put(self.doc, "discovery.simulator.architectures.A2_chunked_ctx.compute_units", None)
        self.assertIn("simulator_columns_present", rp.c9_benchmark(half)["failed"])

    def test_the_recomputation_of_m12_agrees_with_a_report_whose_aggregates_are_its_samples(self) -> None:
        doc = green_report()
        doc["routing"]["tesseract"]["samples"] = [{"seconds": s} for s in (0.5, 0.7, 1.2)]
        doc["routing"]["question_to_verdict"]["samples"] = [{"seconds": s} for s in (2.2, 3.4)]
        doc["routing"]["question_to_verdict"]["per_site_samples"] = [{"seconds": s} for s in (0.7, 1.3)]
        doc["publish"]["created_to_applied"]["samples"] = [{"seconds": s} for s in (0.5, 1.5)]
        doc["publish"]["created_to_applied_before_kill"]["samples"] = [{"seconds": s} for s in (0.5, 0.5)]
        for block, key, fields in rp.RECOMPUTE:
            node = rp._get(doc, block)
            for name, (sample_key, pct) in fields.items():
                node[name] = rp.stats.percentile([s[sample_key] for s in node[key]], pct)
        doc["lineage"]["rows"] = [{"reconstructable": True, "source_events_complete": True}] * 20
        self.assertEqual(rp.recompute(doc), [])
        self.assertTrue(any("seconds_p50" in p for p in rp.recompute(put(doc, "routing.tesseract.seconds_p50", 9))))
        self.assertTrue(any("before_kill.p95" in p
                            for p in rp.recompute(put(doc, "publish.created_to_applied_before_kill.p95", 9))))
        self.assertTrue(any("lineage.conclusions" in p for p in rp.recompute(put(doc, "lineage.conclusions", 19))))
        self.assertTrue(any("recall_all" in p for p in rp.recompute(put(doc, "discovery.recall_all", 0.9))))
        # M12 and M20: a typed-in oracle verdict disagrees with the facet counts the row carries
        for path, value in (("discovery.oracle_rows.0.expected", "hypothesis"),
                            ("discovery.oracle_rows.0.ambiguous", True),
                            ("discovery.oracle_rows.1.ambiguous", False),
                            ("discovery.oracle_rows.0.per_site_facet_counts.r01-site-00.records", 1),
                            ("discovery.oracle_rows.0.per_site_facet_counts", DROP)):
            with self.subTest(path=path):
                self.assertTrue(any("oracle_rows[0]" in p or "oracle_rows[1]" in p
                                    for p in rp.recompute(put(doc, path, value))))
        # M4: a typed-in routing flag disagrees with the per-question traces, eligible counts and route sizes
        for path, value in (("routing.questions.1.trace", ["retrieval_selected", "route_selected"]),
                            ("routing.questions.0.trace", ["retrieval_selected"]),
                            ("routing.questions.0.hq_eligible", 6), ("routing.questions.1.route_size", 2),
                            ("routing.questions", DROP)):
            with self.subTest(path=path):
                self.assertTrue(any(p.startswith("routing.") for p in rp.recompute(put(doc, path, value))))
        flipped = put(put(doc, "routing.route_selected_first_all", False), "routing.per_question_route_ok", False)
        self.assertEqual(sorted(p.split(":")[0] for p in rp.recompute(flipped)),
                         ["routing.per_question_route_ok", "routing.route_selected_first_all"])

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
    the suite's budget; :data:`SHORT_SCALE` names what that scale cannot reach: section 2.3's floor of one
    unambiguous planted candidate with expected ``supported`` holds at 55 candidates (the e2e job); the first 20
    by score need not hold one, so that floor is the e2e job's."""

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
        self.assertEqual(sorted(self.doc["checks"]), sorted(n for n in rp.E2E_CHECKS if n != "c10_suites_green"))
        self.assertEqual(sorted(self.doc["verdict"]["not_run"]), ["c10_suites_green", "c5_measured", "c6_ci"])
        self.assertEqual(self.doc["run"]["suites_evidence"], None)
        self.assertEqual(self.doc["run"]["suites_problems"], ["no suites directory"])
        self.assertFalse(self.doc["verdict"]["complete"])
        # M2: no junit evidence of the suites was given to this run, so it does not count whatever its checks say.
        # c10 is a required check, so a run that never evaluated it cannot report ``checks_pass`` either: the
        # verdict names it under ``unevaluated_required`` and says so as its reason.
        self.assertFalse(self.doc["verdict"]["pass"])
        self.assertEqual(self.doc["verdict"]["unevaluated_required"], ["c10_suites_green"])
        self.assertEqual(self.doc["verdict"]["reason"], "required checks not evaluated: c10_suites_green")
        self.assertFalse(self.doc["verdict"]["checks_pass"])
        self.assertEqual((self.doc["run"]["mode"], self.doc["phase"], self.doc["completed"]),
                         ("process", "finished", True))

    def test_the_partial_report_is_written_after_every_phase(self) -> None:
        """A job stopped at its bound leaves a report: the phases named by ``checkpoint`` are the run's, in order,
        and the file on disk after the run is the finished one."""
        source = inspect.getsource(H.E2ERun.run)
        names = re.findall(r'self\.checkpoint\("([a-z_]+)"\)', source)
        self.assertEqual(names, ["world", "reference_pass", "main_pass", "fixed_queries", "restart_witness",
                                 "crossing", "publish", "forgeries", "lineage", "discovery", "rebuild", "reingest",
                                 "bridge_rerun"])
        written = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual((written["phase"], written["completed"]), ("finished", True))
        self.assertIn("checks", written)

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
        # the stream read's bounds are /admin/status's (M3)
        for block in (before, after):
            self.assertEqual((block["first_seq"], block["last_seq"]),
                             (block["status_first_seq"], block["status_last_seq"]))
        # M8: exactly the three forgeries' own stream sequences were terminated and audited
        self.assertEqual(len(s["forged_stream_seqs"]), 3)
        self.assertEqual(s["forged_seq_rejected_audit_rows"], 3)
        self.assertTrue(s["forged_event_terminated"])
        self.assertEqual(sorted(f["stream_seq"] for f in s["forgeries"]), s["forged_stream_seqs"])
        # M18 beside drill 3: the first k publishes were taken by the live broker, and the full block says so
        bk = self.doc["recovery"]["broker_kill"]
        before_kill = p["created_to_applied_before_kill"]
        self.assertEqual((before_kill["k"], before_kill["n"]), (bk["k"], bk["k"]))
        self.assertIn("outage", p["created_to_applied"]["note"])

    def test_tesseract_routed_every_question_and_the_router_excluded_the_masked_site(self) -> None:
        r = self.doc["routing"]
        self.assertEqual(r["coordinator_class"], "NeuralGraph.research.coordination.core.TesseractCoordinator")
        self.assertEqual(r["router_class"], "NeuralGraph.research.coordination.core.Router")
        self.assertEqual(r["candidates_asked"], SHORT_CANDIDATES)
        self.assertEqual(r["m"], 3)
        self.assertIsNone(r["eval_window"])                            # M21
        self.assertTrue(r["route_selected_first_all"])
        self.assertTrue(r["routes_equal_arm_order"])
        self.assertTrue(r["per_question_route_ok"])
        self.assertEqual(len(r["questions"]), r["questions_asked"])           # M4: ``routing.questions[]``
        for q in r["questions"]:
            with self.subTest(question=q["question_id"][:12]):
                self.assertEqual(q["hq_eligible"], self.doc["world"]["sites"])
                self.assertEqual(q["route_size"], min(3, q["hq_eligible"]))
                trace = q["trace"]
                self.assertIn("route_selected", trace)
                self.assertIn("retrieval_selected", trace)
                self.assertLess(trace.index("route_selected"), trace.index("retrieval_selected"))
        # section 4, item 2: one site diagnostics row per (question, site) whose verdict came from a record read
        t = r["tesseract"]
        self.assertTrue(t["rows_equal_read_verdicts"])
        self.assertEqual(t["rows_for_read_verdicts"], t["read_verdicts"])
        self.assertEqual((t["rows_outside_asked_pairs"], t["rows_duplicated"]), (0, 0))
        self.assertEqual(t["read_verdicts"], r["verdicts"]["confirm"] + r["verdicts"]["refute"])
        self.assertEqual(t["rankings"], len(t["samples"]))
        probe = r["probe"]
        self.assertEqual(sorted(x["site"] for x in probe["received"]), sorted(probe["route"]))
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
        for witness in ("sha256_in_fabric_event", "sha256_in_pd_verdicts", "sha256_in_wire_log",
                        "sha256_in_intake_log", "sha256_in_site_egress", "sha256_in_site_store"):
            with self.subTest(witness=witness):
                self.assertEqual(ln[witness], ln["site_sourced_verdict_events"])
        self.assertGreater(ln["site_sourced_verdict_events"], 0)
        self.assertEqual(ln["fabric_events_source"], "admin_view")
        # every row's sha256s are the fabric's own values, and they are HQ's rows' (M1's first link)
        for row in ln["rows"]:
            self.assertTrue(all(isinstance(s, str) and len(s) == 64 for s in row["sha256s"]), row["question_id"])
        self.assertEqual(ln["site_audit_joined"], ln["site_sourced_answered_verdict_events"])
        self.assertTrue(ln["records_on_or_before_as_of"])
        self.assertEqual((ln["records_after_as_of"], ln["unknown_refs"]), (0, 0))
        # section 4: the spike's leakage scan and closed-shape check over every crossing byte, both halves clean
        crossing = ln["crossing"]
        self.assertEqual(sorted(crossing), ["main", "pipeline"])
        for name in ("main", "pipeline"):
            with self.subTest(crossing=name):
                leak = crossing[name]["leakage"]
                self.assertTrue(leak["ok"])
                self.assertEqual((leak["overlap_bytes"], leak["canary_hits"]), (0, 0))
                self.assertGreater(leak["scanned_items"], 0)
                self.assertGreater(leak["scanned_bytes"], 0)
        shapes = crossing["main"]["crossing"]
        self.assertTrue(shapes["ok"])
        self.assertEqual((shapes["problems"], shapes["hq_intake_refusals"]), ([], 0))
        self.assertGreater(shapes["request_kinds"].get("question", 0), 0)
        self.assertGreater(shapes["descriptor_rows"], 0)
        self.assertNotIn("crossing_leakage_zero", self.doc["checks"]["c2_lineage"]["failed"])
        self.assertNotIn("crossing_shapes_ok", self.doc["checks"]["c2_lineage"]["failed"])
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
        # the queries per site are measured from the rankings (every site ranked the fixed questions asked)
        self.assertEqual((si["queries_per_site"], si["queries_expected"]), (SHORT_QUERIES, SHORT_QUERIES))
        self.assertEqual(sorted(si["queries_per_site_by_site"].values()), [SHORT_QUERIES] * self.doc["world"]["sites"])
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
        self.assertEqual(len(d["gold_discoverable_patterns"]), 40)
        self.assertEqual(d["candidates_true"] + d["candidates_decoy"] + d["candidates_background"],
                         SHORT_CANDIDATES)
        self.assertTrue(d["measurement"] is False and d["synthetic"] is True)
        self.assertLessEqual(set(d["found_patterns"]), set(d["gold_discoverable_patterns"]))
        self.assertEqual(d["recall_all"], len(d["found_patterns"]) / 40)
        self.assertEqual(len(d["fabric_conclusions"]), self.doc["publish"]["conclusion_events"])
        for c in d["fabric_conclusions"]:
            self.assertIn(c["status"], ("supported", "hypothesis", "stale", "contested", "rejected"))
        # the simulator's two columns on the same world (section 10, M14), simulated and labelled so
        sim = d["simulator"]
        self.assertIsNone(d["simulator_reason"])
        self.assertEqual((sim["simulated"], sim["synthetic"], sim["measurement"]), (True, True, False))
        self.assertEqual((sim["users"], sim["records"]), (self.doc["world"]["users"], self.doc["world"]["records"]))
        self.assertEqual(sorted(sim["architectures"]), sorted(rp.SIMULATOR_ARCHITECTURES))
        for name, row in sim["architectures"].items():
            with self.subTest(architecture=name):
                for metric in rp.SIMULATOR_METRICS:
                    self.assertIsInstance(row[metric], float)
        for row in d["oracle_rows"]:
            with self.subTest(question=row["question_id"][:12]):
                self.assertEqual(sorted(row["per_site_facet_counts"]), sorted(row["route"]))
                self.assertIn(row["status"], ("supported", "hypothesis", "stale", "contested", "rejected"))
                self.assertEqual(row["status"], row["hq_status"])         # the fabric holds what HQ concluded
                if not row["ambiguous"]:
                    self.assertEqual(row["status"], row["expected"])

    def test_the_report_re_read_recomputes_to_itself_and_the_check_command_reads_it(self) -> None:
        """M12: every aggregate recomputed from the arrays the report carries; ``check REPORT`` re-runs the checks
        and exits 1 while the stored verdict does not pass (the short scale's floors) and 0 when it does."""
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(rp.recompute(doc), [])
        self.assertEqual(rp.run_checks(doc, list(doc["checks"])), doc["checks"])
        self.assertEqual(H.main(["check", str(self.path)]), 0 if doc["verdict"]["pass"] else 1)
        self.assertEqual(H.main(["check", str(self.path), "--recorded"]), 1)      # no CI run id locally
        green = Path(self.tmp.name) / "green.json"
        passing = json.loads(json.dumps(doc))
        for name, check in passing["checks"].items():
            check["ok"], check["failed"] = True, []
        passing["verdict"]["pass"], passing["verdict"]["failed"] = True, []
        rp.write(green, passing)
        self.assertEqual(H.main(["check", str(green)]), 0 if doc["verdict"]["pass"] else 1)   # a typed-in verdict

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
        # M24: the report records the allocation name and the five metric fields of the simulator's columns only,
        # never the tier labels of the allocation it ran (``models.py:216-217``, read from the module so none is
        # written here; the checkpoint name of ``models.py:8`` may not be written into code at all)
        from research.e2e_ingest.measure import ALLOCATION
        from research.mycelic.models import ALLOCATIONS
        tiers = sorted(set(ALLOCATIONS[ALLOCATION]))
        self.assertGreater(len(tiers), 3)
        self.assertEqual(rp.forbidden_strings(text, tiers), [])
        self.assertIn(ALLOCATION, text)
        self.assertEqual(json.loads(text)["discovery"]["simulator"]["allocation"], ALLOCATION)
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
