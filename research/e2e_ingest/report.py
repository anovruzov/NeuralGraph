"""The JSON report and the checks (``E2E-INGEST.md`` sections 5 and 9; M2, M10, M12, M13, M24).

Every number in the report is computed by the harness from its own logs, the fabric's API, metrics and database, the
sites' stores and graphs, HQ's store and Gold; nothing is typed in. This module holds:

* :func:`skeleton`, :func:`runner_info` and :func:`run_stamps`: the report's header, the runner's CPU (criterion 5's
  requirement, carried by every report) and the code stamps of ``experiments/common.py``;
* :func:`stat_block`: ``{p50, p95, n, samples}`` from one list of samples, ``percentile`` as
  ``mycelic.collective.stats`` computes it (M12: every aggregate is recomputable from the samples the report
  carries);
* one function per criterion (:func:`c0_real_fabric_keyed` to :func:`c9_benchmark`), each returning
  ``{"ok": bool, "failed": [...], ...}`` where ``failed`` names every assertion of section 9 that did not hold, and
  :func:`verdict`, which is ``pass`` only when every check that ran is ``ok`` and names the checks that did not run.
  A check is code, never prose: each assertion below is one line of this module reading one field of the report.
* :func:`recompute`: M12, every aggregate the report carries recomputed from the per-item arrays beside it
  (``check REPORT`` runs it);
* :func:`scan_hits`: drill 5's three patterns over one text (the record-ref pattern, the narrative marker and the
  first 24 characters of every narrative), and :func:`forbidden_strings`, M24's scan for tier labels.

Nothing here reads a record: it reads the report, which holds ids, counts, statuses, digests and seconds.
"""
from __future__ import annotations

import math
import os
import platform
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from mycelic.collective import stats
from mycelic.collective.experiments.common import code_commit, code_dirty, write_json_atomic

KIND = "e2e_ingest_report"
SCHEMA_VERSION = 1
REPORT_NAME = "e2e-ingest-report.json"
BROKER_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+")
CI_BROKER_VERSION = "2.10.29"
#: M13: ``run.spike_code_hash`` at ``e3052c0`` over ``spike_code_files()``; a different value means a pinned file
#: changed (rule 4) and the run fails.
SPIKE_CODE_HASH = "22dc1abaaf3a30ba346118f180f255939cd04c69732d21a03eaacd0f8dfb55cd"
SPIKE_CODE_FILES = 142
#: M10: the paths ``code_dirty`` is evaluated over, at the start and at the end of the run.
DIRTY_PATHS = ("mycelic", "research/e2e_ingest", "research/routing_spike", "research/mycelic",
               "NeuralGraph/research/coordination", "NeuralGraph/research/retrieval", "NeuralGraph/chat_memory",
               "tests", "deploy/mycelic", "requirements.txt", "requirements-lock.txt", "requirements-runtime.txt",
               ".github/workflows/e2e-ingest.yml", ".github/workflows/mycelic.yml")
#: The floors of section 2.3, as the checks read them.
FLOOR_CANDIDATES = 20
FLOOR_VERDICT_EVENTS = 60
FLOOR_CONCLUSION_EVENTS = 20
FLOOR_PUBLISHES = 80
FIXED_QUERIES = 20
FORGERIES = 3


# --------------------------------------------------------------------------------------------------- the header

def runner_info() -> dict[str, Any]:
    """The runner's CPU model (criterion 5), core count, Python and OS."""
    model = None
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
    except OSError:
        model = None
    return {"cpu_model": model, "cpu_count": os.cpu_count(), "python": sys.version.split()[0],
            "os": platform.platform()}


def run_stamps(*, run_id: str, job: str, command: Sequence[str], started_at: str) -> dict[str, Any]:
    """``run.*`` at the start of a run: the code commit, ``code_dirty`` over M10's paths, the spike's code hash and
    file count (M13), the CI run id and the command."""
    from research.routing_spike.run import spike_code_files, spike_code_hash
    return {"run_id": run_id, "job": job, "started_at": started_at, "finished_at": None, "seconds": None,
            "code_commit": code_commit(), "code_dirty_start": code_dirty(list(DIRTY_PATHS)), "code_dirty_end": None,
            "code_dirty_paths": list(DIRTY_PATHS), "spike_code_hash": spike_code_hash(),
            "spike_code_files": len(spike_code_files()), "github_run_id": os.environ.get("GITHUB_RUN_ID"),
            "command": list(command), "suites": [], "suites_evidence": None, "needs_results": None}


def skeleton(*, run_id: str, job: str, command: Sequence[str], started_at: str) -> dict[str, Any]:
    return {"kind": KIND, "schema_version": SCHEMA_VERSION, "synthetic": True, "measurement": False,
            "design": "docs/collective/E2E-INGEST.md",
            "run": run_stamps(run_id=run_id, job=job, command=command, started_at=started_at),
            "runner": runner_info()}


def stat_block(samples: Sequence[Mapping[str, Any]], key: str = "seconds") -> dict[str, Any]:
    """``{p50, p95, n, samples}`` over one list of per-item samples (M12: the aggregates are recomputable)."""
    values = [float(s[key]) for s in samples if s.get(key) is not None]
    return {"p50": stats.percentile(values, 50), "p95": stats.percentile(values, 95), "n": len(values),
            "samples": [dict(s) for s in samples]}


def write(path: str | Path, doc: Mapping[str, Any]) -> str:
    """The report, canonical JSON through ``write_json_atomic``; returns its sha256 (M12's printed digest)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return write_json_atomic(path, doc)


# --------------------------------------------------------------------------------------------------- the scans

def scan_hits(text: str, *, record_ref: re.Pattern[str], narrative_marker: re.Pattern[str],
              narrative_prefixes: Iterable[str], prefix_length: int) -> dict[str, int]:
    """Drill 5's three counts over one text: the record-ref pattern, the EMERGENCE narrative marker, and the first
    ``prefix_length`` characters of any narrative of the world (every window of the text is looked up in the prefix
    set, so a prefix anywhere in the text is found)."""
    prefixes = set(narrative_prefixes)
    hits = 0
    if prefixes and len(text) >= prefix_length:
        for i in range(len(text) - prefix_length + 1):
            if text[i:i + prefix_length] in prefixes:
                hits += 1
    return {"record_ref_hits": len(record_ref.findall(text)),
            "narrative_marker_hits": len(narrative_marker.findall(text)), "narrative_prefix_hits": hits}


def forbidden_strings(text: str, needles: Iterable[str]) -> list[str]:
    """M24: the needles present in the report's text (tier labels and checkpoint names must not be)."""
    return sorted({n for n in needles if n and n in text})


# --------------------------------------------------------------------------------------------------- the checks

def _result(items: Sequence[tuple[str, bool]], values: Mapping[str, Any]) -> dict[str, Any]:
    failed = [name for name, ok in items if not ok]
    return {"ok": not failed, "assertions": len(items), "failed": failed, **dict(values)}


def _get(doc: Mapping[str, Any], path: str, default: Any = None) -> Any:
    node: Any = doc
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return default
        node = node[part]
    return node


def c0_real_fabric_keyed(doc: Mapping[str, Any], *, in_ci: bool | None = None) -> dict[str, Any]:
    """Criterion 0: a real keyed fabric, the key the harness holds bound to the stream (M3) and to the service's
    own record of it, the transport and broker named by the live service and broker, the mode keyed (M22)."""
    in_ci = bool(os.environ.get("GITHUB_ACTIONS")) if in_ci is None else in_ci
    f = _get(doc, "fabric", {}) or {}
    keyring = f.get("keyring") or {}
    version = str(f.get("broker_version") or "")
    binary = str(f.get("broker_binary_version") or "")
    numeric = BROKER_VERSION_RE.match(binary)
    items = [
        ("driver_is_process_driver", bool(f.get("driver_is_process_driver"))),
        ("nats_bin_set", bool(f.get("nats_bin"))),
        ("harness_key_set", keyring.get("harness_key_set") is True),
        ("key_id_in_service_db", keyring.get("key_id_in_service_db") is True),
        ("transport_is_jetstream", f.get("transport") == "nats-jetstream"),
        ("broker_version_shape", BROKER_VERSION_RE.match(version) is not None),
        ("broker_version_is_the_binarys", numeric is not None and bool(version) and version == numeric.group(0)),
        ("integrity_mode_keyed", f.get("integrity_mode") == "keyed"),
        ("stream_messages_positive", int(f.get("stream_messages") or 0) > 0),
    ]
    if in_ci:
        items.append(("ci_broker_version", version == CI_BROKER_VERSION))
    return _result(items, {"in_ci": in_ci, "broker_version": version, "broker_binary_version": binary,
                           "transport": f.get("transport"), "integrity_mode": f.get("integrity_mode"),
                           "stream_messages": f.get("stream_messages"), "key_id": keyring.get("key_id")})


def c1_signed(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Criterion 1: every event of ours signed and in the stream exactly once (M3), the three forgeries terminated
    and counted (M8), the floors of section 2.3, and every unambiguous planted candidate's status equal to the
    oracle's (section 3.5, M20)."""
    s = _get(doc, "signatures", {}) or {}
    before = s.get("stream_before") or {}
    after = s.get("stream_after") or {}
    p = _get(doc, "publish", {}) or {}
    r = _get(doc, "routing", {}) or {}
    d = _get(doc, "discovery", {}) or {}
    oracle = [row for row in (d.get("oracle_rows") or []) if not row.get("ambiguous")]
    mismatched = [row["question_id"] for row in oracle if row.get("status") != row.get("expected")]
    supported = [row for row in oracle if row.get("expected") == "supported"]
    api_ids = int(p.get("publishes") or 0) + int(p.get("conclusion_events") or 0)
    kinds = after.get("kinds") or {}
    items = [
        ("failed_before_forgery_zero", s.get("failed_before_forgery") == 0),
        ("failed_after_forgery_is_three", s.get("failed_after_forgery") == FORGERIES),
        ("counter_after_each_forgery", [f.get("counter") for f in (s.get("forgeries") or [])]
         == list(range(1, FORGERIES + 1))),
        ("forged_memories_absent", s.get("forged_memory_absent") is True),
        ("forged_events_terminated", s.get("forged_event_terminated") is True),
        ("fetched_is_stream_messages", before.get("fetched") == before.get("stream_messages")
         and after.get("fetched") == after.get("stream_messages")),
        ("all_verified_before_forgery", before.get("verified") == before.get("fetched")
         and before.get("foreign") == 0),
        ("three_foreign_after_forgery", after.get("foreign") == FORGERIES
         and after.get("verified") == int(after.get("fetched") or 0) - FORGERIES),
        ("api_ids_each_once", after.get("api_event_ids_in_stream") == api_ids),
        ("api_ids_not_duplicated", after.get("api_event_ids_duplicated") == 0),
        ("agent_event_kind_count", kinds.get("agent.event") == p.get("publishes")),
        ("memory_observed_kind_count", kinds.get("memory.observed") == p.get("conclusion_events")),
        ("kinds_and_foreign_are_the_stream", int(after.get("stream_messages") or 0)
         == sum(int(v) for v in kinds.values()) + int(after.get("foreign") or -1)),
        ("candidates_asked_floor", int(r.get("candidates_asked") or 0) >= FLOOR_CANDIDATES),
        ("verdict_events_floor", int(p.get("verdict_events") or 0) >= FLOOR_VERDICT_EVENTS),
        ("conclusion_events_floor", int(p.get("conclusion_events") or 0) >= FLOOR_CONCLUSION_EVENTS),
        ("no_budget_unknown", _get(doc, "routing.verdicts.budget_unknown") == 0),
        ("route_selected_before_retrieval", r.get("route_selected_first_all") is True),
        ("routes_are_the_arms_order", r.get("routes_equal_arm_order") is True),
        ("every_question_eligible_and_routed", r.get("per_question_route_ok") is True),
        ("coordinator_is_tesseracts", r.get("coordinator_class") == "NeuralGraph.research.coordination.core."
         "TesseractCoordinator"),
        ("router_is_the_routers", r.get("router_class") == "NeuralGraph.research.coordination.core.Router"),
        ("router_excluded_the_masked_site", r.get("router_excluded_masked") == 1),
        ("one_intervention_record", r.get("intervention_records") == 1),
        ("tesseract_row_per_read_verdict", _get(r, "tesseract.rows_equal_read_verdicts") is True),
        ("planted_statuses_equal_the_oracle", not mismatched),
        ("one_unambiguous_supported", bool(supported)),
    ]
    return _result(items, {"unambiguous_planted": len(oracle), "status_mismatches": mismatched,
                           "unambiguous_supported": len(supported), "api_event_ids": api_ids})


def c2_lineage(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Criterion 2: every conclusion reconstructable to its verdict events, each verdict witnessed five times (M1),
    the site-side join to the records read on or before ``as_of``, and the three scans at 0."""
    ln = _get(doc, "lineage", {}) or {}
    p = _get(doc, "publish", {}) or {}
    site_sourced = int(ln.get("site_sourced_verdict_events") or -1)
    scan = ln.get("scan") or {}
    items = [
        ("conclusions_floor", int(ln.get("conclusions") or 0) >= FLOOR_CONCLUSION_EVENTS),
        ("conclusions_are_the_published_ones", ln.get("conclusions") == p.get("conclusion_events")),
        ("all_reconstructable", ln.get("reconstructable") == ln.get("conclusions")),
        ("source_events_complete", ln.get("source_events_complete") == ln.get("conclusions")),
        ("witness_pd_verdicts", ln.get("sha256_in_pd_verdicts") == site_sourced),
        ("witness_wire_log", ln.get("sha256_in_wire_log") == site_sourced),
        ("witness_intake_log", ln.get("sha256_in_intake_log") == site_sourced),
        ("witness_site_egress", ln.get("sha256_in_site_egress") == site_sourced),
        ("witness_site_store", ln.get("sha256_in_site_store") == site_sourced),
        ("site_audit_joined", ln.get("site_audit_joined") == ln.get("site_sourced_answered_verdict_events")),
        ("records_on_or_before_as_of", ln.get("records_on_or_before_as_of") is True),
        ("scan_record_ref_zero", scan.get("record_ref_hits") == 0),
        ("scan_narrative_marker_zero", scan.get("narrative_marker_hits") == 0),
        ("scan_narrative_prefix_zero", scan.get("narrative_prefix_hits") == 0),
        ("scan_read_the_db_and_the_stream", int(scan.get("db_bytes") or 0) > 0
         and int(scan.get("stream_messages_scanned") or 0) > 0),
    ]
    return _result(items, {"site_sourced_verdict_events": site_sourced, "scan": dict(scan)})


def c3_idempotent(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Criterion 3: a second site ingest leaves the graph and the 20 fixed rankings alone; a second bridge run
    leaves the stream alone and creates nothing."""
    si = _get(doc, "idempotency.site_reingest", {}) or {}
    br = _get(doc, "idempotency.bridge_rerun", {}) or {}
    items = [
        ("same_node_ids", si.get("same_node_ids") is True),
        ("same_count", si.get("same_count") is True),
        ("twenty_queries_per_site", si.get("queries_per_site") == FIXED_QUERIES),
        ("same_top10", si.get("same_top10") is True),
        ("same_order", si.get("same_order") is True),
        ("every_record_a_duplicate", si.get("ingested") == 0 and int(si.get("duplicates") or 0) > 0),
        ("no_rejections", si.get("rejected") == 0),
        ("stream_unchanged", br.get("stream_messages_after") == br.get("stream_messages_before")),
        ("no_new_memory_ids", br.get("new_memory_ids") == 0),
        ("no_new_event_ids", br.get("new_event_ids") == 0),
        ("every_post_a_duplicate", br.get("created") == 0 and int(br.get("publishes") or 0) > 0),
        ("hq_store_unchanged", br.get("store_unchanged") is True),
    ]
    return _result(items, {"site_reingest": dict(si), "bridge_rerun": dict(br)})


def c4_recovery(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Criterion 4: the broker killed mid-publish (M6), the service's database deleted and rebuilt (M5), and the
    site process killed mid-pass (drill 2)."""
    bk = _get(doc, "recovery.broker_kill", {}) or {}
    sr = _get(doc, "recovery.service_rebuild", {}) or {}
    st = _get(doc, "recovery.site_restart", {}) or {}
    n, k = int(bk.get("N") or 0), int(bk.get("k") or 0)
    items = [
        ("publishes_floor", n >= FLOOR_PUBLISHES),
        ("kill_point_in_range", 1 <= k <= n - 10),
        ("applied_exactly_once", bk.get("applied_exactly_once") is True),
        ("outbox_drained", bk.get("outbox_pending") == 0),
        ("consumer_drained", bk.get("consumer_pending") == 0),
        ("outage_seen_by_the_outbox", int(bk.get("outbox_pending_during_outage") or 0) >= 1),
        ("outage_seen_by_the_transport", bk.get("transport_connected_during_outage") is False),
        ("outage_seen_by_the_status", bk.get("status_during_outage") == "degraded"),
        ("reconnected_after", int(bk.get("transport_reconnects_after") or 0) >= 1),
        ("applied_delta_agent_event", bk.get("applied_delta_agent_event") == n),
        ("applied_delta_memory_observed", bk.get("applied_delta_memory_observed")
         == _get(doc, "publish.conclusion_events")),
        ("active_notes_after", bk.get("active_notes_after") == _get(doc, "publish.conclusion_events")),
        ("no_stream_duplicates", bk.get("stream_duplicates") == 0),
        ("conclusions_applied_before_the_kill", int(sr.get("conclusions_applied_before_kill") or 0) >= 1),
        ("same_active_memory_ids", sr.get("active_memory_ids_identical") is True),
        ("same_verify_digest", sr.get("verify_digest_identical") is True),
        ("replay_saw_the_stream", sr.get("replay_seen") == _get(doc, "fabric.stream_messages")),
        ("replay_rejected_the_forgeries", sr.get("replay_rejected") == FORGERIES),
        ("replay_not_blocked", sr.get("replay_blocked") is False),
        ("no_ready_block", sr.get("ready_block") is None),
        ("ready_after_the_rebuild", sr.get("ready_after_rebuild") is True),
        ("site_verdict_bytes_identical", st.get("verdict_bytes_identical") is True),
        ("site_killed_and_reopened", int(st.get("questions_before") or 0) >= 1
         and int(st.get("questions_after") or 0) >= 1),
        ("no_node_added_after_the_restart", st.get("nodes_added_after_restart") == 0),
        ("answer_matrix_equals_the_reference", st.get("answer_matrix_identical") is True),
    ]
    return _result(items, {"N": n, "k": k, "site": st.get("site")})


def c7_guards(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Criterion 7: the HQ-side and bridge modules import none of ``FORBIDDEN_HQ`` (statically and in a fresh
    interpreter), the spike's code hash is M13's value, and nothing under the spike's paths is uncommitted."""
    g = _get(doc, "guards", {}) or {}
    modules = g.get("modules") or {}
    bad = sorted(name for name, found in modules.items()
                 if (found.get("static") or found.get("fresh_interpreter")))
    items = [
        ("three_modules_checked", sorted(modules) == ["hq_bridge", "hq_tesseract", "publish_api"]),
        ("no_forbidden_import", not bad),
        ("spike_code_hash_is_the_pinned_one", _get(doc, "run.spike_code_hash") == SPIKE_CODE_HASH),
        ("spike_code_files", _get(doc, "run.spike_code_files") == SPIKE_CODE_FILES),
        ("spike_paths_clean", g.get("spike_code_dirty") is False),
        ("drills_load_no_site_or_service_module", not (g.get("drills") or [])),
    ]
    return _result(items, {"modules_with_a_forbidden_import": bad, "spike_code_dirty": g.get("spike_code_dirty")})


def c8_emergence_load(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Criterion 8: the EMERGENCE world of section 3.1 loaded with no loss, one graph node per own record at every
    site, the node ids the record refs (M9), the ``ingested`` count read from the site store (M16)."""
    ing = _get(doc, "ingest", {}) or {}
    per_site = ing.get("per_site") or {}
    w = _get(doc, "world", {}) or {}
    table = w.get("expected_counts") or {}
    bad_sites = []
    for site, row in sorted(per_site.items()):
        expected = row.get("expected_records")
        if not (row.get("ingested") == expected and row.get("duplicates") == 0 and row.get("rejected") == 0
                and row.get("graph_nodes_db") == expected and row.get("graph_nodes") == row.get("graph_nodes_db")
                and row.get("node_ids_sha256") == row.get("record_refs_sha256")):
            bad_sites.append(site)
    items = [
        ("one_graph_per_site", len(per_site) == int(w.get("sites") or -1)),
        ("every_site_whole", not bad_sites),
        ("totals_agree", ing.get("total_ingested") == ing.get("total_expected") == w.get("records")),
        ("no_loss", ing.get("loss") == 0),
        ("pipeline_raised_nothing", ing.get("pipeline_raised") is False),
        ("world_is_section_3_1", bool(table) and all(w.get(k) == v for k, v in table.items())),
    ]
    return _result(items, {"sites": len(per_site), "sites_with_a_problem": bad_sites,
                           "total_ingested": ing.get("total_ingested"), "records": w.get("records")})


#: Section 10's ``discovery`` fields, every one of which c9 requires present.
DISCOVERY_FIELDS = ("gold_discoverable", "gold_rare", "gold_common", "decoys_by_type", "candidates_true",
                    "candidates_decoy", "candidates_background", "found_patterns", "recall_all", "recall_common",
                    "recall_rare", "decoy_accepted_by_type", "background_supported", "simulator")
#: Criterion 5's per-hop numbers at the run's size (section 10, "both jobs"), which c9 requires numeric.
PER_HOP_FIELDS = ("ingest.seconds_per_1k_records", "routing.tesseract.seconds_p50", "routing.tesseract.seconds_p95",
                  "routing.question_to_verdict.per_site_p50", "routing.question_to_verdict.per_site_p95",
                  "routing.question_to_verdict.per_question_p50", "routing.question_to_verdict.per_question_p95",
                  "publish.created_to_applied.p50", "publish.created_to_applied.p95")
SIMULATOR_ARCHITECTURES = ("H_mycelic_full", "A2_chunked_ctx")
SIMULATOR_METRICS = ("discovery_recall", "rare_signal_recall", "decoy_acceptance_all", "recall_at_100",
                     "compute_units")
SIMULATOR_BUDGET_SIZE = 2000


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def c9_benchmark(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Criterion 9: every discovery field present and computed from the fabric and Gold (the found patterns are
    Gold-discoverable ones, the recall is their ratio), the per-hop numbers of criterion 5 at this size numeric,
    the simulator's two columns present (or null with ``reason: budget`` at the 2,000-user size only), and the
    synthetic and measurement stamps on the header and on the block."""
    d = _get(doc, "discovery", {}) or {}
    gold = d.get("gold_discoverable_patterns") or []
    found = d.get("found_patterns")
    found_list = found if isinstance(found, list) else []
    discoverable = d.get("gold_discoverable")
    sim = d.get("simulator")
    size = _get(doc, "world.target_users")
    sim_ok = (isinstance(sim, Mapping) and sim.get("simulated") is True
              and all(isinstance((sim.get("architectures") or {}).get(a), Mapping)
                      and all(_number(sim["architectures"][a].get(m)) for m in SIMULATOR_METRICS)
                      for a in SIMULATOR_ARCHITECTURES))
    sim_budget = sim is None and d.get("simulator_reason") == "budget" and size == SIMULATOR_BUDGET_SIZE
    missing = [f for f in DISCOVERY_FIELDS if f not in d]
    not_numeric = [p for p in PER_HOP_FIELDS if not _number(_get(doc, p))]
    recall = (len(found_list) / discoverable) if isinstance(discoverable, int) and discoverable else None
    items = [
        ("every_discovery_field_present", not missing),
        ("found_patterns_are_gold_discoverable", isinstance(found, list) and set(found_list) <= set(gold)
         and len(gold) == discoverable),
        ("recall_all_is_found_over_gold", recall is not None and d.get("recall_all") == recall),
        ("per_hop_numbers_numeric", not not_numeric),
        ("simulator_columns_present", sim_ok or sim_budget),
        ("stamps_synthetic_not_measured", doc.get("synthetic") is True and doc.get("measurement") is False
         and d.get("synthetic") is True and d.get("measurement") is False),
    ]
    return _result(items, {"missing_fields": missing, "per_hop_not_numeric": not_numeric, "found": len(found_list),
                           "gold_discoverable": discoverable, "recall_all": d.get("recall_all"),
                           "simulator_reason": d.get("simulator_reason")})


CHECKS = {"c0_real_fabric_keyed": c0_real_fabric_keyed, "c1_signed": c1_signed, "c2_lineage": c2_lineage,
          "c3_idempotent": c3_idempotent, "c4_recovery": c4_recovery, "c7_guards": c7_guards,
          "c8_emergence_load": c8_emergence_load, "c9_benchmark": c9_benchmark}
#: The checks the end-to-end job runs (criteria 0-4, 7, 8 and 9, section 2.3); c5 belongs to the measure job, c6 to
#: the workflow and c10's junit evidence to the suites CLI.
E2E_CHECKS = ("c0_real_fabric_keyed", "c1_signed", "c2_lineage", "c3_idempotent", "c4_recovery", "c7_guards",
              "c8_emergence_load", "c9_benchmark")


def run_checks(doc: Mapping[str, Any], names: Sequence[str] = E2E_CHECKS) -> dict[str, Any]:
    return {name: CHECKS[name](doc) for name in names}


# --------------------------------------------------------------------------------------------------- M12

#: The aggregates a report carries beside the per-item arrays they come from: ``(block, samples key, {field: (key,
#: percentile)})``.
RECOMPUTE = (
    ("routing.tesseract", "samples", {"seconds_p50": ("seconds", 50), "seconds_p95": ("seconds", 95)}),
    ("routing.question_to_verdict", "samples", {"per_question_p50": ("seconds", 50),
                                                "per_question_p95": ("seconds", 95)}),
    ("routing.question_to_verdict", "per_site_samples", {"per_site_p50": ("seconds", 50),
                                                         "per_site_p95": ("seconds", 95)}),
    ("publish.created_to_applied", "samples", {"p50": ("seconds", 50), "p95": ("seconds", 95)}),
)


def recompute(doc: Mapping[str, Any]) -> list[str]:
    """M12: every percentile of :data:`RECOMPUTE` recomputed from the samples the report carries, the lineage
    counts from ``lineage.rows`` and the discovery recall from ``found_patterns``; returns the disagreements (an
    empty list when the report's aggregates are its samples')."""
    problems: list[str] = []
    for block, key, fields in RECOMPUTE:
        node = _get(doc, block)
        if not isinstance(node, Mapping):
            problems.append(f"{block}: missing")
            continue
        samples = node.get(key)
        if not isinstance(samples, list):
            problems.append(f"{block}.{key}: no samples")
            continue
        for name, (sample_key, pct) in fields.items():
            values = [float(s[sample_key]) for s in samples if isinstance(s, Mapping) and _number(s.get(sample_key))]
            expected = stats.percentile(values, pct)
            got = node.get(name)
            if not (_number(got) and _number(expected) and abs(float(got) - float(expected)) <= 1e-9) \
                    and not (got is None and expected is None):
                problems.append(f"{block}.{name}: report {got}, recomputed {expected} from {len(values)} samples")
    ln = _get(doc, "lineage")
    if isinstance(ln, Mapping) and isinstance(ln.get("rows"), list):
        rows = [r for r in ln["rows"] if isinstance(r, Mapping)]
        derived = {"conclusions": len(rows), "reconstructable": sum(1 for r in rows if r.get("reconstructable")),
                   "source_events_complete": sum(1 for r in rows if r.get("source_events_complete"))}
        for name, value in derived.items():
            if ln.get(name) != value:
                problems.append(f"lineage.{name}: report {ln.get(name)}, recomputed {value} from the rows")
    d = _get(doc, "discovery")
    if isinstance(d, Mapping) and isinstance(d.get("found_patterns"), list) and _number(d.get("gold_discoverable")):
        expected = len(d["found_patterns"]) / d["gold_discoverable"] if d["gold_discoverable"] else None
        if d.get("recall_all") != expected:
            problems.append(f"discovery.recall_all: report {d.get('recall_all')}, recomputed {expected}")
    return problems


SUITES_NOT_GREEN = "suites_not_green"


def verdict(checks: Mapping[str, Mapping[str, Any]], *, not_run: Mapping[str, str] | None = None,
            suites_green: bool = False) -> dict[str, Any]:
    """``checks_pass`` when every check that ran is ``ok``; ``pass`` only when, besides, the suites' junit evidence
    of M2 is in the report and green (``suites_green``), else ``reason`` is ``suites_not_green`` (criterion 10: a
    run on a tree whose suites are not shown green is not a result). ``not_run`` names the checks this job did not
    run and why, and ``complete`` is false while any is missing: a report never claims a check it did not make."""
    failed = sorted(name for name, c in checks.items() if not c.get("ok"))
    missing = dict(not_run or {})
    checks_pass = not failed
    return {"pass": checks_pass and bool(suites_green), "checks_pass": checks_pass,
            "reason": None if suites_green else SUITES_NOT_GREEN, "suites_green": bool(suites_green),
            "failed": failed, "checks_run": sorted(checks), "not_run": dict(sorted(missing.items())),
            "complete": not missing}
