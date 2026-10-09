"""Centralized baseline (PLAN_v1 §B.6 with orchestrator amendment O4; BENCHMARK_CONTRACT §7).

The same raw source files go through the same ``local_export`` connector and the same ingestion pipeline (normalize, ACL,
domain classification, dedupe, chunk, embed, graph) into **one** ``EvidenceStore`` per tenant instead of one per holder. A task
is answered with the same code the holders run: ``EvidenceStore.answer_question`` (audience filter before ranking, hybrid
NeuralGraph retrieval, disclosure policy, the deterministic ``answer_from_evidence`` task), then the same
``evaluate_responses`` task and the same ``CommitGate.check`` with the tenant policy. The result is written as the asker's view
(``views/<task_id>.json``) in exactly the format ``bench/score.py`` reads, so the scorer is unchanged.

What it consumes (nothing else; it never imports ``world.py`` / ``events.py`` and never reads the gold file)::

    <world_dir>/sources_manifest.json     one entry per source file (file, late_file, holder_id, tenant, app, account, ...)
    <world_dir>/sources/                  the raw JSONL files, copied before use (originals untouched)
    <world_dir>/tasks_<split>.public.json the public task file
    <world_dir>/data/coord.db             the organization (units, users, memberships, holders): read through a read-only
                                          connection and copied; used for the asker's audience, the gate's authorization/unit
                                          checks and raw-access refusals. Holder domains are blanked in the copy, so no routing
                                          signal of the system run reaches the baseline.
    <world_dir>/ids.json (optional)       {"tenants","units","users","holders"} key -> id; without it keys are recovered from the
                                          generator's naming (user ``tN-uNNNNN`` <-> e-mail ``...uNNNNN@slug.example``, units by
                                          creation order within their type)
    <world_dir>/run_manifest.json         seed, size, started_utc (the generation clock)

Declared differences from the system (BENCHMARK_CONTRACT §7), all of them deliberate:

* no routing: one store per tenant holds every record, so no holder is chosen, ranked or asked;
* no per-person stores and no per-holder export policy: one policy (the tenant default ``excerpt``) applies; every record keeps its
  source ACL, and the audience filter is the holders' own (``Audience`` of the users who can read the question's result);
* **one retrieval per task** with the system's evidence budget: up to ``30`` chunks (``max_holders x 3``, O4) instead of the
  holder-local 8. ``RETRIEVAL_K`` is raised for this process only and the deterministic ``answer_from_evidence`` rule keeps up to
  30 items instead of 3 (same predicate: >= 2 shared content tokens with the question);
* no verification question, no loop follow-up, no discovery synthesis, no hypergraph, no cross-holder independence beyond what the
  commit gate computes from source roots (copies still de-duplicate by root);
* ``variant='source'`` (default, the PRIMARY reported baseline): the retained chunks are grouped by the origin holder of their source (at most 3
  chunks per origin, at most 10 origins = the system's maximum) into pseudo-responses, so ``evaluate_responses`` can cluster and detect
  disagreements exactly as it does over holder responses. ``variant='single'`` (secondary; the literal PLAN §B.6 text): one response with
  all retained chunks, evaluated as one finding (claim text clipped to the gate's 4000-character limit), which never sees a disagreement.
  Both are run and reported, with ``source`` first;
* each reference carries its origin holder id (from the source -> holder map of the manifest) because the commit gate counts
  independent departments from the holders behind the roots (``min_independent_units``); nothing else of the system run is used;
* goal-only tasks (no question text) are answered from ``goal title + objective`` as the query: the baseline has no loop that drafts a
  question; replay / restart faults are not simulated (the files are ingested once, the late phase appended and synced);
* raw-access checks are decided by ``Authorizer.can_view_raw_evidence`` on the origin holder (the API route's own rule): 403 unless the
  asker owns or leads that holder.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import arch_gate, ledger
from .gold import copy_gold

PROVIDER_LABEL = "deterministic-provider"
EVIDENCE_BUDGET = 30            # max_holders (10) x 3 chunks per holder
PER_ORIGIN_CAP = 3
MAX_ORIGINS = 10
CLAIM_TEXT_LIMIT = 4000         # the commit gate's schema check
RAW_REFUSED = 403
PRIMARY_VARIANT = "source"          # reported as the baseline; 'single' is the secondary, literal-text variant
VARIANTS = ("source", "single")


class BaselineError(RuntimeError):
    pass


# ---------------------------------------------------------------------------------------------- helpers
class _NullPublisher:
    async def publish_ingest_output(self, kind: str, payload: dict[str, Any], *, msg_id: str) -> bool:
        return True


def _as_dict(x: Any) -> dict[str, Any]:
    if isinstance(x, Mapping):
        return dict(x)
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return dataclasses.asdict(x)
    raise TypeError(f"cannot read a task from {type(x)}")


def _load_json(p: Path, default: Any = None) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _jl(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def load_public_tasks(path: Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, Mapping):
        data = data.get("tasks", data)
    return [_as_dict(t) for t in data]


# ---------------------------------------------------------------------------------------------- the organization (read-only copy)
class OrgContext:
    """Org services over a scratch copy of the run's ``coord.db`` (made through a read-only connection)."""

    def __init__(self, coord_db: Path, scratch: Path) -> None:
        from mycelic.authz import Authorizer
        from mycelic.db.coord import CoordDB
        from mycelic.knowledge.gate import CommitGate
        from mycelic.org import OrgService
        src = arch_gate.ro_connect(coord_db)
        scratch.parent.mkdir(parents=True, exist_ok=True)
        dst = sqlite3.connect(str(scratch))
        try:
            src.backup(dst)
            dst.execute("UPDATE holders SET domains='[]', published_domains='[]', stats='{}'")      # no routing signal from the system run
            dst.commit()
        finally:
            dst.close()
            src.close()
        self.path = scratch
        self.db = CoordDB(scratch, migrate=False)
        self.org = OrgService(self.db)
        self.authz = Authorizer(self.db, self.org)
        self.gate = CommitGate(self.authz)
        self._aud: dict[tuple, dict[str, Any]] = {}

    def close(self) -> None:
        try:
            self.db._conn.close()
        except Exception:      # noqa: BLE001
            pass

    # ---- identity resolution
    def tenant_ids(self) -> dict[str, str]:
        return {r["slug"]: r["tenant_id"] for r in self.db.all("SELECT slug, tenant_id FROM tenants")}

    def user_by_email(self, email: str) -> str | None:
        r = self.db.one("SELECT user_id FROM users WHERE lower(email)=lower(?)", (email,))
        return r["user_id"] if r else None

    def audience(self, tenant_id: str, scope_unit_id: str | None, visibility: str, asker_id: str) -> dict[str, Any]:
        """Everyone who can read what this question produces (``QuestionService.question_audience``'s rule, cached per scope)."""
        key = (tenant_id, scope_unit_id, visibility, asker_id)
        if key in self._aud:
            return self._aud[key]
        row = {"tenant_id": tenant_id, "visibility": visibility, "scope_unit_id": scope_unit_id, "asker_id": asker_id}
        ids = []
        for u in self.db.all("SELECT user_id FROM users WHERE tenant_id=? AND status='active'", (tenant_id,)):
            p = self.authz.principal_for_user(u["user_id"])
            if p is not None and (u["user_id"] == asker_id or self.authz.can_view_scoped(p, row, resource_type="claim")
                                  or self.authz.can_view_scoped(p, row, resource_type="question")):
                ids.append(u["user_id"])
        out = {"principal_ids": sorted(set(ids) | {asker_id}), "complete": True, "owner": False}
        self._aud[key] = out
        return out


def _recover_ids(ctx: OrgContext, manifest: Sequence[Mapping[str, Any]], tasks: Sequence[Mapping[str, Any]], ids_json: Mapping[str, Any] | None) -> dict[str, dict[str, str]]:
    """key -> id maps for users and units (see module docstring)."""
    if ids_json and ids_json.get("users") and ids_json.get("units"):
        return {"users": dict(ids_json["users"]), "units": dict(ids_json["units"])}
    users: dict[str, str] = {}
    slug_idx: dict[str, int] = {}
    for m in manifest:
        mm = re.search(r"(?:^|-)t(\d+)-", str(m.get("holder_key") or ""))
        if mm:
            slug_idx[str(m["tenant"])] = int(mm.group(1))
    tenants = ctx.tenant_ids()
    for slug, tid in tenants.items():
        ti = slug_idx.get(slug)
        if ti is None:
            continue
        for r in ctx.db.all("SELECT user_id, email FROM users WHERE tenant_id=?", (tid,)):
            mm = re.search(r"\.u(\d{5})@", r["email"])
            if mm:
                users[f"t{ti}-u{mm.group(1)}"] = r["user_id"]
    units: dict[str, str] = {}
    order = {"executive": 0, "region": 1, "subsidiary": 2, "department": 3, "team": 4, "project": 5}
    prefix = {"region": "r", "subsidiary": "s", "department": "d", "project": "p"}
    for slug, tid in tenants.items():
        ti = slug_idx.get(slug)
        if ti is None:
            continue
        rows = ctx.db.all("SELECT unit_id, type, parent_id FROM org_units WHERE tenant_id=? ORDER BY rowid", (tid,))
        by_type: dict[str, list[Any]] = {}
        for r in rows:
            by_type.setdefault(r["type"], []).append(r)
        for r in by_type.get("executive", [])[:1]:
            units[f"t{ti}-root"] = r["unit_id"]
        for typ, pre in prefix.items():
            for i, r in enumerate(by_type.get(typ, [])):
                units[f"t{ti}-{pre}{i}"] = r["unit_id"]
        # teams: {dept key}-tm{m} in the order the departments were created, then by creation order within a department
        dept_key = {v: k for k, v in units.items() if re.fullmatch(rf"t{ti}-d\d+", k)}
        per_dept: dict[str, int] = {}
        for r in by_type.get("team", []):
            dk = dept_key.get(r["parent_id"])
            if dk is not None:
                m = per_dept.get(dk, 0)
                per_dept[dk] = m + 1
                units[f"{dk}-tm{m}"] = r["unit_id"]
    missing = [t["scope_unit"] for t in tasks if t.get("scope_unit") and t["scope_unit"] not in units]
    if missing:
        raise BaselineError(f"cannot resolve scope units {sorted(set(missing))[:3]} without ids.json")
    return {"users": users, "units": units}


# ---------------------------------------------------------------------------------------------- ingestion
async def _ingest_tenant(slug: str, tenant_id: str, entries: Sequence[Mapping[str, Any]], src_dir: Path, work: Path, principal_map: Mapping[str, str],
                         router: Any, log: Any) -> tuple[Any, Any, dict[str, Any]]:
    from mycelic.evidence import EvidenceStore
    from mycelic.evidence.service import EmbeddingAdapter
    from mycelic.ingest.connectors import register_builtin
    from mycelic.ingest.pipeline import IngestPipeline
    from mycelic.ingest.registry import ConnectorRegistry
    from mycelic.ingest.service import IngestService
    from mycelic.models.openai_provider import HashEmbeddings

    path = work / "central" / slug / "evidence.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    store = EvidenceStore(path, holder_id=f"central-{slug}", tenant_id=tenant_id, llm=EmbeddingAdapter(HashEmbeddings()), router=router,
                          export_policy={"disclosure": "excerpt", "max_excerpt_chars": 480, "answer_scopes": ["unit", "org"]})
    pipe = IngestPipeline(store, holder_kind="unit", registry=register_builtin(ConnectorRegistry()), router=router, publisher=_NullPublisher(),
                          batch_debounce_seconds=0.0)
    svc = IngestService(pipe)
    copied = work / "central_sources"
    copied.mkdir(parents=True, exist_ok=True)
    connectors: list[tuple[Mapping[str, Any], str]] = []
    t0 = time.perf_counter()
    errors: list[str] = []
    for e in entries:
        shutil.copyfile(src_dir / e["file"], copied / e["file"])
        cfg = {"source_app": e["app"], "account_id": e["account"], "paths": [str(copied / e["file"])], "auto_include": True,
               "principal_map": {k: v for k, v in principal_map.items() if k in set(e.get("members") or [])}}
        try:
            con = await svc.add_connector("local_export", created_by="central-baseline", config=cfg, display_name=e.get("source_header_id"))
            await svc.discover_sources(con["connector_id"])
            for s in svc.sources(con["connector_id"]):
                kw: dict[str, Any] = {"selection": "included"}
                if e.get("domain"):
                    kw["default_domain_ids"] = [e["domain"]]
                if e.get("visibility") != "private":
                    kw["exportable"] = True
                await svc.set_source(s.source_id, actor="central-baseline", **kw)
            connectors.append((e, con["connector_id"]))
        except Exception as exc:         # noqa: BLE001 - recorded, the run goes on (same as the feeder)
            errors.append(f"{e['file']}: {type(exc).__name__}: {exc}")
    totals = {"raw_items": 0, "enqueued": 0, "duplicates": 0, "normalize_errors": 0, "processed": 0}

    async def sync_all() -> None:
        for _e, cid in connectors:
            rep = await pipe.sync(cid)
            for k in ("raw_items", "enqueued", "duplicates", "normalize_errors"):
                totals[k] += int(getattr(rep, k, 0) or 0)
            while True:
                pr = await pipe.process_available()
                totals["processed"] += pr.processed
                if not pr.processed:
                    break
    await sync_all()
    for e, cid in connectors:                                      # the late phase: edits and deletions appended to the exported file
        if e.get("late_file"):
            with open(copied / e["file"], "a", encoding="utf-8") as f:
                f.write((src_dir / e["late_file"]).read_text(encoding="utf-8"))
            rep = await pipe.sync(cid)
            for k in ("raw_items", "enqueued", "duplicates", "normalize_errors"):
                totals[k] += int(getattr(rep, k, 0) or 0)
            while True:
                pr = await pipe.process_available()
                totals["processed"] += pr.processed
                if not pr.processed:
                    break
    await pipe.flush_batch(force=True)
    stats = {"tenant": slug, "sources": len(entries), "connectors": len(connectors), "errors": errors, "wall_s": round(time.perf_counter() - t0, 2), **totals}
    if log:
        log(f"  central store {slug}: {stats}")
    return store, pipe, stats


def _origin_map(store: Any, header_to_holder: Mapping[str, str]) -> dict[str, str]:
    """ref_id -> origin holder id, through the export ledger -> record -> source header id."""
    rows = store.store._conn.execute(
        """SELECT e.ref_id, s.external_id FROM exports e JOIN ingest_records r ON r.record_id = e.doc_id
           JOIN connector_sources s ON s.source_id = r.source_id""").fetchall()
    return {r["ref_id"]: header_to_holder.get(r["external_id"], "") for r in rows}


# ---------------------------------------------------------------------------------------------- answering
class _Kept:
    """Side channel of the 30-item ``answer_from_evidence`` rule: the retained items of the latest call, in retrieval order."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def rule(self, inp: Mapping[str, Any], _model: str) -> dict[str, Any]:
        from mycelic.models import fake
        question = fake._s(inp.get("question"))
        kept = []
        for item in fake._dicts(inp.get("evidence")):
            excerpt = fake._text_of(item, "excerpt", "text", "content")
            if excerpt and fake.shared_tokens(question, excerpt) >= 2:
                kept.append(item)
            if len(kept) >= EVIDENCE_BUDGET:
                break
        self.items = [dict(i) for i in kept]
        if not kept:
            return {"answer": "", "confidence": 0.0, "used_ref_ids": [], "no_evidence": True}
        return {"answer": " ".join(fake._text_of(i, "excerpt", "text", "content") for i in kept), "confidence": round(min(0.9, 0.5 + 0.15 * len(kept)), 4),
                "used_ref_ids": [fake._id_of(i, "ref_id", "id") for i in kept if fake._id_of(i, "ref_id", "id")], "no_evidence": False}


def _ref_view(ref: Mapping[str, Any], role: str, demoted: str | None) -> dict[str, Any]:
    return {"ref_id": ref["ref_id"], "holder_id": ref.get("holder_id"), "source_root_id": ref.get("source_root_id"), "root_known": bool(ref.get("root_known")),
            "kind": ref.get("kind"), "title": ref.get("title"), "disclosed_excerpt": ref.get("disclosed_excerpt") or "",
            "disclosure_level": ref.get("disclosure_level"), "observed_at": ref.get("observed_at"), "freshness_at": ref.get("freshness_at"),
            "status": "active", "version": 1, "role": role, "weight": 1.0, "demoted": demoted}


async def _answer_task(task: dict[str, Any], *, ctx: OrgContext, store: Any, router: Any, kept: _Kept, tenant_id: str, user_id: str, scope_id: str,
                       origin_fn: Any, variant: str, now: datetime, evidence_budget: int) -> dict[str, Any]:
    from mycelic.evidence import service as evsvc
    t0 = time.perf_counter()
    tid = task["task_id"]
    qid = f"bl-q-{tid}"
    policy = dict(task.get("policy") or {})
    vis = policy.get("visibility", "unit")
    text = task.get("question_text") or f"{task.get('goal_title', '')}. {task.get('goal_objective', '')}".strip(". ")
    valid_from = None
    if task.get("valid_from_days"):
        valid_from = ((now) - timedelta(days=int(task["valid_from_days"]))).replace(microsecond=0).isoformat()
    audience = ctx.audience(tenant_id, scope_id, vis, user_id)
    question = {"question_id": qid, "text": text, "candidate_domains": list(task.get("candidate_domains") or []), "valid_from": valid_from, "valid_to": None,
                "tenant_id": tenant_id, "policy": {"visibility": vis, "disclosure": None, **{k: v for k, v in policy.items() if k != "visibility"}},
                "audience": audience, "scope_unit_id": scope_id}
    evsvc.RETRIEVAL_K = evidence_budget
    kept.items = []
    resp = await store.answer_question(question)
    view: dict[str, Any] = {"task_id": tid, "status": "ok", "error": None, "latency_s": None, "tenant_id": tenant_id,
                            "question": {"question_id": qid, "status": "retained_uncertain", "result": {"outcome": "no_findings", "summary": ""}},
                            "claims": [], "discoveries": [], "evidence": [], "raw_checks": {}, "goal": None,
                            "baseline": {"variant": variant, "retrieved": len(kept.items), "response_status": resp.get("status")}}
    if resp.get("status") != "answered" or not kept.items:
        view["latency_s"] = round(time.perf_counter() - t0, 4)
        return view
    shapes = {r["ref_id"]: r for r in resp.get("evidence_refs") or []}
    origin = origin_fn()                           # after the answer: the export ledger now holds this question's references
    items = [i for i in kept.items if i.get("ref_id") in shapes]
    for i in items:
        i["holder_id"] = origin.get(i["ref_id"]) or ""

    def ref_row(ref_id: str) -> dict[str, Any]:
        s = shapes[ref_id]
        return {**s, "holder_id": origin.get(ref_id) or "", "status": "active", "role": "supports", "weight": 1.0}

    # ---- responses handed to the evaluator
    responses: list[dict[str, Any]] = []
    if variant == "single":
        content = " ".join(i["excerpt"] for i in items)
        responses.append({"response_id": f"{qid}:all", "holder_id": "central", "content": content, "confidence": 0.8,
                          "refs": [{"ref_id": i["ref_id"], "root_id": shapes[i["ref_id"]].get("source_root_id"), "root_known": bool(shapes[i["ref_id"]].get("root_known")),
                                    "observed_at": shapes[i["ref_id"]].get("observed_at")} for i in items]})
    else:
        groups: dict[str, list[dict[str, Any]]] = {}
        for i in items:
            groups.setdefault(i["holder_id"], []).append(i)
        for hid, its in list(groups.items())[:MAX_ORIGINS]:
            its = its[:PER_ORIGIN_CAP]
            responses.append({"response_id": f"{qid}:{hid}", "holder_id": hid, "content": " ".join(i["excerpt"] for i in its)[:1200], "confidence": 0.8,
                              "refs": [{"ref_id": i["ref_id"], "root_id": shapes[i["ref_id"]].get("source_root_id"), "root_known": bool(shapes[i["ref_id"]].get("root_known")),
                                        "observed_at": shapes[i["ref_id"]].get("observed_at")} for i in its]})
    ev = await router.run_task("evaluate_responses", {"question": {"question_id": qid, "text": text, "kind": "gap"}, "responses": responses, "existing_claims": [],
                                                      "today": now.isoformat()[:10]}, tenant_id=tenant_id, question_id=qid)
    view["baseline"]["findings"] = len(ev.get("findings") or [])
    view["baseline"]["disagreements"] = len(ev.get("disagreements") or [])
    # ---- commit gate (the system's own check, the tenant's policy, the question's policy)
    principal = ctx.authz.principal_for_loop(tenant_id, {"owner_type": "user", "owner_id": user_id, "goal_id": f"bl-{tid}"})
    pol = ctx.org.policies(tenant_id)
    gate_q = {"scope_unit_id": scope_id, "policy": {"visibility": vis, **{k: v for k, v in policy.items() if k != "visibility"}}}
    base = {"tenant_id": tenant_id, "scope_unit_id": scope_id, "visibility": vis, "question_id": qid, "created_by_type": "loop", "created_by_id": principal.id,
            "valid_from": valid_from, "valid_to": None}
    claims: list[dict[str, Any]] = []
    all_refs: dict[str, dict[str, Any]] = {}

    def commit(idx: str, text_: str, ref_ids: Sequence[str], conf: float, conflict: bool) -> None:
        rows = [ref_row(r) for r in dict.fromkeys(ref_ids) if r in shapes]
        res = ctx.gate.check(principal, {**base, "text": text_[:CLAIM_TEXT_LIMIT], "kind": "finding", "confidence": conf}, evidence=rows, policy=pol,
                             has_open_conflict=conflict, question=gate_q, input_claims=[])
        if not res.ok:
            view["baseline"].setdefault("rejected", []).append({"finding": idx, "reasons": res.reasons})
            return
        cid = f"bl-c-{tid}-{idx}"
        support = dict(res.support)
        support["freshness"] = res.freshness
        refs_out = [_ref_view(r, r.get("role") or "supports", r.get("demoted")) for r in res.refs]
        for r in refs_out:
            all_refs.setdefault(r["ref_id"], r)
        claims.append({"claim": {"claim_id": cid, "tenant_id": tenant_id, "scope_unit_id": scope_id, "visibility": vis, "text": text_[:CLAIM_TEXT_LIMIT], "kind": "finding",
                                 "status": res.status, "confidence": conf, "question_id": qid, "support": support, "created_by": {"type": "loop", "id": principal.id}},
                       "evidence": refs_out, "support": support, "gate": {"status": res.status, "reasons": res.reasons}})

    for i, f in enumerate(ev.get("findings") or []):
        if isinstance(f, dict) and (f.get("text") or "").strip():
            commit(str(i), f["text"].strip(), f.get("supporting_ref_ids") or [], float(f.get("confidence", 0.5)), False)
    by_resp = {r["response_id"]: r for r in responses}
    for k, d in enumerate(ev.get("disagreements") or []):
        for side, key in (("a", "a_text"), ("b", "b_text")):
            rids = [x for x in d.get(f"{side}_response_ids") or [] if x in by_resp]
            ref_ids = [r["ref_id"] for x in rids for r in by_resp[x]["refs"]]
            if ref_ids and (d.get(key) or "").strip():
                commit(f"d{k}{side}", d[key].strip(), ref_ids, 0.5, True)
    view["claims"] = claims
    view["evidence"] = list(all_refs.values())
    # ---- raw access exactly as the API route decides it
    for rid, r in all_refs.items():
        holder = ctx.org.get_holder(r.get("holder_id") or "")
        asker = ctx.authz.principal_for_user(user_id)
        allowed = bool(holder) and asker is not None and ctx.authz.can_view_raw_evidence(asker, holder)
        view["raw_checks"][rid] = 200 if allowed else RAW_REFUSED
    view["question"]["status"] = "committed" if claims else "retained_uncertain"
    view["question"]["result"] = {"outcome": "committed" if claims else "no_findings", "claim_ids": [c["claim"]["claim_id"] for c in claims], "summary": ""}
    view["latency_s"] = round(time.perf_counter() - t0, 4)
    return view


# ---------------------------------------------------------------------------------------------- raw-access authority
def _authority(ctx: OrgContext, tasks: Sequence[Mapping[str, Any]], ids: Mapping[str, Mapping[str, str]]) -> dict[str, list[str]]:
    tenants = ctx.tenant_ids()
    holders_by_tenant: dict[str, list[dict[str, Any]]] = {}
    out: dict[str, list[str]] = {}
    for t in tasks:
        uid = ctx.user_by_email(t["asker"]) or ids["users"].get(t.get("asker_key", ""))
        p = ctx.authz.principal_for_user(uid) if uid else None
        tid = tenants.get(t["tenant"])
        if p is None or tid is None:
            out[t["task_id"]] = []
            continue
        if tid not in holders_by_tenant:
            holders_by_tenant[tid] = [dict(r) for r in ctx.db.all("SELECT holder_id, tenant_id, owner_type, owner_id FROM holders WHERE tenant_id=?", (tid,))]
        out[t["task_id"]] = sorted(h["holder_id"] for h in holders_by_tenant[tid] if ctx.authz.can_view_raw_evidence(p, h))
    return out


def _reach_authority(ctx: OrgContext, tasks: Sequence[Mapping[str, Any]], ids: Mapping[str, Mapping[str, str]]) -> dict[str, dict[str, list[str]]]:
    """task id -> ``{"tenant_holders": all holders of the asker's tenant, "reachable_holders": the holders ``can_route`` admits for the task's
    scope (visibility unit, no domain filter) with the asker as the routing principal}``. Organization only; no answers."""
    tenants = ctx.tenant_ids()
    holders: dict[str, list[dict[str, Any]]] = {}
    cache: dict[tuple, list[str]] = {}
    out: dict[str, dict[str, list[str]]] = {}
    for t in tasks:
        tid = tenants.get(t["tenant"])
        uid = ctx.user_by_email(t["asker"]) or ids["users"].get(t.get("asker_key", ""))
        if tid is None:
            out[t["task_id"]] = {"tenant_holders": [], "reachable_holders": []}
            continue
        if tid not in holders:
            holders[tid] = [h for h in (ctx.org.get_holder(r["holder_id"]) for r in ctx.db.all("SELECT holder_id FROM holders WHERE tenant_id=?", (tid,))) if h]
        scope = ids["units"].get(t.get("scope_unit", ""))
        key = (tid, uid, scope)
        if key not in cache:
            p = ctx.authz.principal_for_user(uid) if uid else None
            q = {"tenant_id": tid, "scope_unit_id": scope, "policy": {"visibility": "unit"}, "candidate_domains": []}
            cache[key] = sorted(h["holder_id"] for h in holders[tid] if ctx.authz.can_route(q, h, asker=p)[0])
        out[t["task_id"]] = {"tenant_holders": sorted(h["holder_id"] for h in holders[tid]), "reachable_holders": cache[key]}
    return out


def write_raw_authority(run_dir: str | Path, *, org_db: str | Path | None = None, out_file: str | Path | None = None) -> Path:
    """Write ``raw_authority.json`` (task id -> holders whose raw content the task's asker is entitled to read) and ``reach_authority.json`` (the holders
    of the asker's tenant and the ones the asker can route to) for a run directory.

    The API lets a person read raw evidence of the holders they own and of the unit holders of units they lead
    (``Authorizer.can_view_raw_evidence``); the scorer's "raw access refused where the asker lacks it" must not count those. The set is
    computed from the organization alone, with the same rule the API route applies, so it says nothing about answers. Works for the
    system run directory and for a baseline run directory alike."""
    d = Path(run_dir)
    tasks = load_public_tasks(next(iter(sorted(d.glob("tasks_*.public.json")))))
    manifest = _load_json(d / "sources_manifest.json") or []
    coord = Path(org_db) if org_db else (d / "data" / "coord.db" if (d / "data" / "coord.db").exists() else d.parent / "data" / "coord.db")
    scratch = Path(tempfile.mkdtemp(prefix="authority-")) / "org.db"
    ctx = OrgContext(coord, scratch)
    try:
        ids = _recover_ids(ctx, manifest, tasks, _load_json(d / "ids.json")) if manifest else {"users": {}, "units": {}}
        out = _authority(ctx, tasks, ids)
        reach = _reach_authority(ctx, tasks, ids)
    finally:
        ctx.close()
        shutil.rmtree(scratch.parent, ignore_errors=True)
    target = Path(out_file) if out_file else d / "raw_authority.json"
    target.write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
    (target.parent / "reach_authority.json").write_text(json.dumps(reach, indent=1, sort_keys=True), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------------------------- entry point
def run(world_dir: str | Path, tasks_public: Sequence[Any] | None, out_dir: str | Path, *, variant: str = PRIMARY_VARIANT, org_db: str | Path | None = None,
        evidence_budget: int = EVIDENCE_BUDGET, split: str | None = None, log: Any = print, ledger_rows: bool = False) -> dict[str, Any]:
    """Run the baseline on the sources of ``world_dir`` and write a scorable run directory to ``out_dir``. ``tasks_public`` defaults to the
    public task file of ``world_dir``. Returns the run manifest."""
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {VARIANTS}")
    return asyncio.run(_run(Path(world_dir), tasks_public, Path(out_dir), variant, Path(org_db) if org_db else None, evidence_budget, split, log, ledger_rows))


async def _run(world: Path, tasks_public: Sequence[Any] | None, out: Path, variant: str, org_db: Path | None, budget: int, split: str | None, log: Any,
               ledger_rows: bool = False) -> dict[str, Any]:
    t_start = time.perf_counter()
    run_manifest = _load_json(world / "run_manifest.json", {}) or {}
    split = split or run_manifest.get("split") or "dev"
    # the holdout guard first: nothing is created if a holdout baseline may not run (raises ledger.HoldoutRefused)
    ledger_run_id = None
    if split == "holdout" or ledger_rows:
        ledger_run_id = ledger.start_run({"split": split, "mode": "baseline", "variant": variant, "ablation": None, "seed": run_manifest.get("seed"),
                                          "size": run_manifest.get("size"), "provider_label": PROVIDER_LABEL, "transport": None, "inprocess": True,
                                          "run_dir": str(out.resolve())})
    try:
        manifest = await _run_guarded(world, tasks_public, out, variant, org_db, budget, split, log, t_start, run_manifest, ledger_run_id)
    except BaseException as exc:
        if ledger_run_id:
            ledger.abort_run(ledger_run_id, f"{type(exc).__name__}: {exc}")
        raise
    if ledger_run_id:
        ledger.mark_completed(ledger_run_id, {"out": str(out)})
    return manifest


async def _run_guarded(world: Path, tasks_public: Sequence[Any] | None, out: Path, variant: str, org_db: Path | None, budget: int, split: str, log: Any,
                       t_start: float, run_manifest: dict[str, Any], ledger_run_id: str | None) -> dict[str, Any]:
    from mycelic.models.fake import FakeProvider
    from mycelic.models.ledger import MemoryUsageLedger
    from mycelic.models.router import DefaultModelRouter

    out.mkdir(parents=True, exist_ok=True)
    (out / "views").mkdir(exist_ok=True)
    src_manifest = _load_json(world / "sources_manifest.json")
    if not src_manifest:
        raise BaselineError(f"{world}/sources_manifest.json missing or empty")
    pub_file = world / f"tasks_{split}.public.json"
    tasks = [_as_dict(t) for t in tasks_public] if tasks_public is not None else load_public_tasks(pub_file)
    coord = org_db or (world / "data" / "coord.db")
    if not Path(coord).exists():
        raise BaselineError(f"organization database {coord} not found")
    now = datetime.fromisoformat(run_manifest["started_utc"]) if run_manifest.get("started_utc") else datetime.now(timezone.utc)

    from mycelic.evidence import service as evsvc
    saved_k = evsvc.RETRIEVAL_K
    ctx = OrgContext(Path(coord), out / "work" / "org_snapshot.db")
    kept = _Kept()
    ledger = MemoryUsageLedger()
    router = DefaultModelRouter({"fake": FakeProvider(overrides={"answer_from_evidence": kept.rule})}, tiers={}, ledger=ledger)
    ids = _recover_ids(ctx, src_manifest, tasks, _load_json(world / "ids.json"))
    tenant_ids = ctx.tenant_ids()
    header_to_holder = {m["source_header_id"]: m["holder_id"] for m in src_manifest}
    stores: dict[str, tuple[Any, Any, dict[str, Any]]] = {}
    ingest_stats = []
    try:
        for slug in sorted({m["tenant"] for m in src_manifest}):
            entries = [m for m in src_manifest if m["tenant"] == slug]
            stores[slug] = await _ingest_tenant(slug, tenant_ids[slug], entries, world / "sources", out / "work", ids["users"], router, log)
            ingest_stats.append(stores[slug][2])
        t_ing = time.perf_counter() - t_start
        views: dict[str, dict[str, Any]] = {}
        answered_tenants: set[str] = set()
        for task in tasks:
            tid = task["task_id"]
            slug = task["tenant"]
            try:
                store = stores[slug][0]
                user_id = ctx.user_by_email(task["asker"]) or ids["users"].get(task.get("asker_key", ""))
                if not user_id:
                    raise BaselineError(f"asker {task['asker']} not found in the organization")
                view = await _answer_task(task, ctx=ctx, store=store, router=router, kept=kept, tenant_id=tenant_ids[slug], user_id=user_id,
                                          scope_id=ids["units"][task["scope_unit"]], origin_fn=lambda st=store: _origin_map(st, header_to_holder), variant=variant, now=now, evidence_budget=budget)
                if view["claims"]:
                    answered_tenants.add(slug)
            except Exception as exc:         # noqa: BLE001 - an error is a wrong answer, never a skipped task
                view = {"task_id": tid, "status": "error", "error": f"{type(exc).__name__}: {exc}", "tenant_id": tenant_ids.get(slug), "latency_s": None, "question": None, "claims": [],
                        "discoveries": [], "evidence": [], "raw_checks": {}}
            views[tid] = view
            (out / "views" / f"{tid}.json").write_text(json.dumps(view, indent=1, default=str), encoding="utf-8")
        # ---- the scorer's inputs beside the views
        if pub_file.exists():
            shutil.copyfile(pub_file, out / pub_file.name)
        elif tasks_public is not None:
            (out / f"tasks_{split}.public.json").write_text(json.dumps({"split": split, "tasks": tasks}, indent=1, sort_keys=True))
        copy_gold(world, out, split)              # a byte copy; this module never opens or parses it
        (out / "raw_authority.json").write_text(json.dumps(_authority(ctx, tasks, ids), indent=1, sort_keys=True), encoding="utf-8")
        (out / "reach_authority.json").write_text(json.dumps(_reach_authority(ctx, tasks, ids), indent=1, sort_keys=True), encoding="utf-8")
        tasks_sha = hashlib.sha256((out / f"tasks_{split}.public.json").read_bytes()).hexdigest()
        calls = ledger.calls
        by_purpose: dict[str, int] = {}
        for c in calls:
            by_purpose[c.purpose] = by_purpose.get(c.purpose, 0) + 1
        errors = sum(1 for v in views.values() if v["status"] != "ok")
        manifest = {
            "run_id": "base_" + uuid.uuid4().hex[:10], "ledger_run_id": ledger_run_id, "split": split, "mode": "baseline", "ablation": None, "variant": variant, "seed": run_manifest.get("seed"),
            "size": run_manifest.get("size"), "provider_label": PROVIDER_LABEL, "provider": "fake (deterministic)", "tasks_sha256": tasks_sha, "n_tasks": len(tasks),
            "raw_checks_enabled": True, "evidence_budget_items": budget, "per_origin_cap": PER_ORIGIN_CAP if variant == "source" else None,
            "counts": {"created": len(stores), "with_records": len(stores), "activated": len(answered_tenants), "routed": 0},
            "model_usage": {"calls": len(calls), "input_tokens": sum(c.input_tokens for c in calls), "output_tokens": sum(c.output_tokens for c in calls),
                            "providers": "fake", "by_purpose": by_purpose},
            "ingest": ingest_stats, "view_errors": errors, "timings_s": {"ingest_s": round(t_ing, 2), "total_s": round(time.perf_counter() - t_start, 2)},
            "world_dir": str(world), "differences": [
                "no routing: one central store per tenant", "no per-person stores / per-holder export policy", f"one retrieval per task, up to {budget} chunks (O4)",
                "no verification question, no loop follow-up, no discovery synthesis, no hypergraph",
                "source variant: chunks grouped by origin holder into <=3-chunk pseudo-responses" if variant == "source" else "single variant: one response with all retained chunks",
                "goal-only tasks queried with goal title + objective", "replay/restart faults not simulated"],
            "started_utc": now.isoformat(), "finished_utc": datetime.now(timezone.utc).isoformat(),
        }
        (out / "run_manifest.json").write_text(json.dumps(manifest, indent=1, default=str), encoding="utf-8")
        return manifest
    finally:
        evsvc.RETRIEVAL_K = saved_k
        for store, pipe, _ in stores.values():
            try:
                await pipe.aclose()
            except Exception:      # noqa: BLE001
                pass
            try:
                await store.close()
            except Exception:      # noqa: BLE001
                pass
        ctx.close()
        try:
            await router.close()
        except Exception:      # noqa: BLE001
            pass


def main(argv: Iterable[str] | None = None) -> int:      # pragma: no cover - thin CLI
    import argparse
    ap = argparse.ArgumentParser(description="Centralized baseline over the sources of a system run directory")
    ap.add_argument("world_dir", help="the system run directory (sources, public tasks, data/coord.db)")
    ap.add_argument("--out", help="the baseline run directory to write")
    ap.add_argument("--authority-only", action="store_true", help="only write <world_dir>/raw_authority.json (for scoring the system run) and exit")
    ap.add_argument("--variant", choices=VARIANTS, default=PRIMARY_VARIANT)
    ap.add_argument("--org-db")
    ap.add_argument("--ledger", action="store_true", help="also write ledger rows for a dev run (a holdout run always does, and is refused by the holdout guard)")
    ns = ap.parse_args(list(argv) if argv is not None else None)
    if ns.authority_only:
        print(write_raw_authority(ns.world_dir, org_db=ns.org_db))
        return 0
    if not ns.out:
        ap.error("--out is required")
    try:
        m = run(ns.world_dir, None, ns.out, variant=ns.variant, org_db=ns.org_db, ledger_rows=ns.ledger)
    except ledger.HoldoutRefused as exc:
        print(f"HOLDOUT REFUSED: {exc}", file=sys.stderr)
        return 3
    print(json.dumps({k: m[k] for k in ("run_id", "variant", "n_tasks", "view_errors", "counts", "model_usage", "timings_s")}, indent=2, default=str))
    return 0


if __name__ == "__main__":      # pragma: no cover
    raise SystemExit(main())
