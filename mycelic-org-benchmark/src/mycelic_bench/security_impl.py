"""Security pipeline implementation (Task C, DESIGN.md §7).

`build_security(detector, cfg, profile, seed, local_slm)` returns a `SecurityPipeline`
whose `filter_batch(batch, node)` screens worker observations arriving at a team node
and whose `inspect_claim(claim, sender, node)` screens claims arriving at any node.

Detectors are *capability sets* (a detector name may also be composed with "+",
e.g. ``"lineage_aware+rules"``; the ``local_slm`` flag adds the edge classifier):

==================== ======================================================================
detector             capabilities
==================== ======================================================================
none                 nothing (the trunk's own lineage dedup / signature_ok checks still run)
rules                schema sanity, provenance membership, duplicate fingerprints,
                     injection-signature regex on the rationale text (read *locally*, zero
                     exposure), claim rules (malformed, confidence inflation, echoed claims)
central_classifier   schema sanity + content classifier at the ``sim-14b`` operating point;
                     every record's text is shipped to the centre (bytes_exposed, canaries)
cloud_classifier     schema sanity + content classifier at the frontier operating point;
                     text shipped to the cloud (bytes_exposed, canaries, tokens_to_cloud)
local_slm            schema sanity + content classifier at the *edge* profile, on device
hybrid_local_rules   rules + local_slm
lineage_aware        structural verifier: schema sanity, provenance (signature_ok and team
                     membership), duplicate fingerprints, claim rules, producer-signature
                     verification, sketch-backing check, within-scope consistency test of
                     bare claims, independent-support requirement for bare claims
==================== ======================================================================

Honesty / modelling assumptions
-------------------------------
* The **content classifiers** are *profile operating points*, not measured detectors
  (DESIGN.md §9).  They are simulated by `SimulatedSLM.classify_poison(batch.is_attack)`,
  which draws a flag with probability ``poison_tpr`` for attack-tagged records and
  ``poison_fpr`` for benign ones.  This is the ONLY place where the ground-truth tags
  ``batch.is_attack`` / ``claim.origin_attack`` influence a decision, and only when a
  classifier capability is enabled.  Results produced with these detectors must be
  labelled "profile assumption".
* All **structural** rules (schema, membership, dedup, regex, signatures, backing,
  consistency, support) use only what a real node could see: the batch arrays, the
  node's own local store / received sketches, unit keys, and (for the local text
  rules) the rationale text that lives on the same device.
* ``batch.is_attack`` and ``claim.origin_attack`` are additionally read for
  *evaluation counters only* (per-attack-type totals and flags, so the runner can
  compute detection recall / precision / F1 / false suppression).  They never feed a
  keep/drop decision outside the classifier path described above.
* Record-level confidence is not used as a rule: worker confidence is clipped to
  0.99 in the world and benign "overconfident" workers saturate there, so a
  "constant 0.99" rule would be indistinguishable from them.  Confidence inflation is
  caught on *claims* (confidence > 0.9 with n < n_min) as specified.
* The consistency test against independent evidence is applied to bare
  (worker-layer) claims only, using the *rest of the same team*.  Local effects in
  this world are team-scoped and cross-team effects department-scoped, so a node
  comparing a child's claim with its *siblings'* counts would be testing scope, not
  forgery, and would quarantine genuine scoped findings; that disagreement is the
  trunk's `_consistency` job (contested / split_conditional, nothing deleted).  Node
  claims are instead checked for *backing*: a claim's (n, k) may not exceed what the
  sender's own promoted sketch holds for the cell.
"""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np

from .agents import SimulatedSLM
from .config import get_profile
from .hypothesis import rate_test
from .security import SecurityPipeline, Verdict
from .sketch import COL_K0, COL_N
from .vocab import N_ATTR, N_LABELS, N_VALUES, cell_index

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
CANARY_RE = re.compile(r"CANARY-[a-z_]+-[0-9a-f]{8}")

# Injection-signature list.  Real systems maintain such lists; this one covers the
# families of payloads in configs/attacks.yaml plus common jailbreak phrasings.
INJECTION_PATTERNS: tuple[str, ...] = (
    r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|directions|rules)",
    r"disregard\s+(?:all\s+|any\s+)?(?:previous|prior|above|the)\s+(?:instructions|directions|rules)",
    r"system\s+override",
    r"<!--\s*(?:assistant|system|instruction|note to (?:the )?(?:model|aggregator))\b",
    r"</?\s*(?:system|assistant|instruction)\s*>",
    r"\[\s*(?:INST|SYS|SYSTEM)\s*\]",
    r"\bpromote\s+(?:this\s+|the\s+)?(?:finding|claim|result)\b",
    r"\bat\s+confidence\s+(?:1\.0+|100%)",
    r"\breport\s+that\b.{0,120}\balways\s+fails\b",
    r"\bto\s+(?:the\s+)?executive\s+immediately\b",
    r"\bthe\s+aggregated\s+conclusion\s+is\b",
    r"\bnew\s+instructions?\s*:",
    r"\byou\s+are\s+now\s+(?:the\s+)?(?:system|assistant|aggregator)\b",
)
INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.IGNORECASE | re.DOTALL)

DETECTOR_CAPS: dict[str, frozenset[str]] = {
    "none": frozenset(),
    "rules": frozenset({"sanity", "provenance", "dedup", "regex", "claim_rules"}),
    "central_classifier": frozenset({"sanity", "classifier_central"}),
    "cloud_classifier": frozenset({"sanity", "classifier_cloud"}),
    "local_slm": frozenset({"sanity", "classifier_local"}),
    "hybrid_local_rules": frozenset({"sanity", "provenance", "dedup", "regex", "claim_rules", "classifier_local"}),
    "lineage_aware": frozenset({"sanity", "provenance", "signature_ok", "dedup", "claim_rules", "signature",
                                "backing", "consistency", "support"}),
}
CLASSIFIER_CAPS = ("classifier_local", "classifier_central", "classifier_cloud")
CENTRAL_PROFILE = "sim-14b"
CONSISTENCY_P = 1e-3          # one-sided p below which a bare claim is inconsistent with the rest of its team
BACKING_SLACK = (1.5, 4)      # claim counts may exceed the sender's sketch by at most 1.5x + 4 (delta/cadence lag)


def _worker_index(producer_id: str) -> int:
    if isinstance(producer_id, str) and len(producer_id) > 1 and producer_id[0] == "W" and producer_id[1:].isdigit():
        return int(producer_id[1:])
    return -1


def _team_index(unit_id: str) -> int | None:
    """Team index that a leaf aggregator node serves (T0017 -> 17, S0017.2 -> 17); None otherwise."""
    if not isinstance(unit_id, str):
        return None
    if unit_id.startswith("T") and unit_id[1:].isdigit():
        return int(unit_id[1:])
    if unit_id.startswith("S") and "." in unit_id and unit_id[1:5].isdigit():
        return int(unit_id[1:5])
    return None


def _finite(x: Any) -> bool:
    try:
        return bool(math.isfinite(float(x)))
    except (TypeError, ValueError):
        return False


class SecurityImpl(SecurityPipeline):
    """Concrete pipeline; see module docstring for semantics and assumptions."""

    def __init__(self, detector: str, cfg: dict[str, Any], profile, seed: int, local_slm: bool = False) -> None:
        super().__init__(name=detector)
        self.detector = detector
        caps: set[str] = set()
        for part in str(detector).split("+"):
            part = part.strip()
            if part not in DETECTOR_CAPS:
                raise ValueError(f"unknown detector {part!r}; known: {sorted(DETECTOR_CAPS)}")
            caps |= set(DETECTOR_CAPS[part])
        if local_slm:
            caps.add("classifier_local")
            caps.add("sanity")
        self.caps: frozenset[str] = frozenset(caps)
        self.cfg = cfg
        self.seed = int(seed)
        sec = cfg.get("security", {}) if isinstance(cfg, dict) else {}
        pol = cfg.get("policy", {}) if isinstance(cfg, dict) else {}
        self.n_min_default = int(pol.get("n_min", 8))
        self.support_min_default = float(sec.get("min_independent_support", pol.get("support_min", 2.0)))
        self.quarantine_threshold = float(sec.get("quarantine_threshold", 0.5))
        # profile-based content classifiers (assumption; DESIGN.md §9)
        self.edge_profile = profile
        self.central_profile = None
        self.cloud_profile = None
        self.slm_local = SimulatedSLM(profile, self.seed * 7919 + 101) if "classifier_local" in caps else None
        self.slm_central = None
        self.slm_cloud = None
        if "classifier_central" in caps:
            try:
                self.central_profile = get_profile(cfg, CENTRAL_PROFILE)
            except (KeyError, TypeError):
                self.central_profile = profile
            self.slm_central = SimulatedSLM(self.central_profile, self.seed * 7919 + 202)
        if "classifier_cloud" in caps:
            try:
                self.cloud_profile = get_profile(cfg, cfg["models"].get("frontier_profile", "sim-frontier"))
            except (KeyError, TypeError):
                self.cloud_profile = profile
            self.slm_cloud = SimulatedSLM(self.cloud_profile, self.seed * 7919 + 303)
        # state
        self._seen_fp: dict[str, set[int]] = {}
        self._echo: dict[tuple, set[str]] = {}
        self._store_cache: dict[str, tuple[int, np.ndarray, np.ndarray, np.ndarray]] = {}
        self.attack_names: dict[int, str] = {}     # optional code -> name map (set by the integrator / attacks.py)
        # evaluation counters (ground-truth tags used for accounting only)
        self.records_seen = 0
        self.attack_records_seen = 0
        self.attack_records_flagged = 0
        self.benign_records_seen = 0
        self.benign_records_flagged = 0
        self.record_type_counts: dict[int, list[int]] = {}
        self.claims_seen = 0
        self.attack_claims_seen = 0
        self.attack_claims_flagged = 0
        self.benign_claims_seen = 0
        self.benign_claims_flagged = 0
        self.claim_type_counts: dict[str, list[int]] = {}
        self.reasons: dict[str, int] = {}
        self.classifier_calls = 0
        self.classifier_tokens_edge = 0
        self.texts_read_locally = 0
        self.signatures_verified = 0

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _bump(self, reason: str, n: int = 1) -> None:
        if n:
            self.reasons[reason] = self.reasons.get(reason, 0) + int(n)

    def _texts(self, batch, node) -> list[str] | None:
        rec = getattr(getattr(node, "hier", None), "records", None)
        if rec is None or not hasattr(rec, "rationale"):
            return None
        try:
            return [rec.rationale(int(i)) for i in np.asarray(batch.idx).ravel()]
        except Exception:      # a synthetic batch whose idx does not point into the record store
            return None

    def _store(self, node) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Concatenated local observation store of a leaf node (attrs, labels, workers), cached per size."""
        obs_attrs = getattr(node, "obs_attrs", None)
        if not obs_attrs:
            z = np.zeros((0, N_ATTR), dtype=np.int64)
            return z, np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
        key = node.unit_id
        cached = self._store_cache.get(key)
        if cached is not None and cached[0] == len(obs_attrs):
            return cached[1], cached[2], cached[3]
        attrs = np.concatenate(obs_attrs).astype(np.int64)
        labels = np.concatenate(node.obs_labels).astype(np.int64)
        workers = np.concatenate(node.obs_worker).astype(np.int64)
        self._store_cache[key] = (len(obs_attrs), attrs, labels, workers)
        return attrs, labels, workers

    @staticmethod
    def _cell_mask(attrs: np.ndarray, cell: int) -> np.ndarray:
        ci = cell_index()
        m = np.ones(len(attrs), dtype=bool)
        for a, v in ci.decode(int(cell)):
            m &= attrs[:, a] == v
        return m

    def _n_min(self, node) -> int:
        pol = getattr(node, "policy", None)
        return int(getattr(pol, "n_min", self.n_min_default))

    def _support_min(self, node) -> float:
        pol = getattr(node, "policy", None)
        return float(getattr(pol, "support_min", self.support_min_default))

    # ------------------------------------------------------------------
    # records
    # ------------------------------------------------------------------
    def filter_batch(self, batch, node) -> np.ndarray:
        n = len(batch)
        keep = np.ones(n, dtype=bool)
        if n == 0:
            return keep
        caps = self.caps
        org = getattr(getattr(node, "hier", None), "org", None)
        flags: dict[str, np.ndarray] = {}

        # --- schema sanity: malformed artifacts are dropped -------------------------------------
        if "sanity" in caps:
            flags["malformed_record"] = self._malformed_records(batch, org)

        # --- provenance: signature_ok (lineage) and team membership ------------------------------
        if "signature_ok" in caps:
            sig = np.asarray(getattr(batch, "signature_ok", np.ones(n, dtype=bool)), dtype=bool)
            if sig.shape == (n,):
                flags["signature_invalid"] = ~sig
        if "provenance" in caps and org is not None:
            t = _team_index(getattr(node, "unit_id", ""))
            if t is not None:
                w = np.asarray(batch.worker).astype(np.int64)
                valid = (w >= 0) & (w < org.n_workers)
                member = np.zeros(n, dtype=bool)
                member[valid] = org.worker_team[w[valid]] == t
                flags["provenance_not_member"] = valid & ~member

        # --- duplicate evidence: same fingerprint within the batch or seen before at this node ---
        if "dedup" in caps:
            fps = np.asarray(batch.fingerprint).astype(np.int64)
            uniq, first = np.unique(fps, return_index=True)
            dup = np.ones(n, dtype=bool)
            dup[first] = False
            seen = self._seen_fp.setdefault(getattr(node, "unit_id", "?"), set())
            already = np.fromiter((int(f) in seen for f in fps), dtype=bool, count=n) if seen else np.zeros(n, dtype=bool)
            flags["duplicate_fingerprint"] = dup | already
            seen.update(int(f) for f in uniq)

        # --- text rules ---------------------------------------------------------------------------
        need_text = ("regex" in caps) or ("classifier_central" in caps) or ("classifier_cloud" in caps)
        texts = self._texts(batch, node) if need_text else None
        if "regex" in caps and texts is not None:
            # read on the device that holds the text: zero exposure
            self.texts_read_locally += len(texts)
            flags["injection_signature"] = np.fromiter((INJECTION_RE.search(t) is not None for t in texts), dtype=bool, count=n)

        # --- content classifiers (profile operating points; see module docstring) -----------------
        is_attack = np.asarray(getattr(batch, "is_attack", np.zeros(n, dtype=np.int64))).astype(np.int64)
        if is_attack.shape != (n,):
            is_attack = np.zeros(n, dtype=np.int64)
        if "classifier_central" in caps or "classifier_cloud" in caps:
            if texts is not None:
                total = sum(len(t) for t in texts)
                self.bytes_exposed += int(total)
                for t in texts:
                    self.text_exposed.extend(CANARY_RE.findall(t))
                if "classifier_cloud" in caps:
                    self.tokens_to_cloud += int(sum(len(t) // 4 for t in texts))
            else:
                # no text reachable: charge the compact observation encoding instead
                self.bytes_exposed += int(batch.wire_bytes())
                if "classifier_cloud" in caps:
                    self.tokens_to_cloud += int(batch.wire_bytes() // 4)
            slm = self.slm_cloud if "classifier_cloud" in caps else self.slm_central
            self.classifier_calls += n
            # ASSUMPTION: classifier at the profile's (poison_tpr, poison_fpr); consults is_attack (see docstring)
            flags["content_classifier_" + ("cloud" if "classifier_cloud" in caps else "central")] = slm.classify_poison(is_attack)
        if "classifier_local" in caps and self.slm_local is not None:
            self.classifier_calls += n
            self.classifier_tokens_edge += int(getattr(batch, "tokens_in", 0) or 0)
            # ASSUMPTION: edge classifier at the edge profile's (poison_tpr, poison_fpr); consults is_attack
            flags["content_classifier_local"] = self.slm_local.classify_poison(is_attack)

        # --- combine ------------------------------------------------------------------------------
        drop = np.zeros(n, dtype=bool)
        for reason, m in flags.items():
            m = np.asarray(m, dtype=bool)
            if m.shape != (n,):
                continue
            self._bump(reason, int(m.sum()))
            drop |= m
        keep &= ~drop

        # --- evaluation counters (ground truth used for accounting only) --------------------------
        att = is_attack > 0
        self.records_seen += n
        self.flagged_records += int(drop.sum())
        self.attack_records_seen += int(att.sum())
        self.attack_records_flagged += int((att & drop).sum())
        self.benign_records_seen += int((~att).sum())
        self.benign_records_flagged += int((~att & drop).sum())
        for code in np.unique(is_attack[att]):
            sel = is_attack == code
            c = self.record_type_counts.setdefault(int(code), [0, 0])
            c[0] += int(sel.sum())
            c[1] += int((sel & drop).sum())
        return keep

    def _malformed_records(self, batch, org) -> np.ndarray:
        n = len(batch)
        bad = np.zeros(n, dtype=bool)
        attrs = np.asarray(batch.attrs)
        if attrs.ndim != 2 or attrs.shape != (n, N_ATTR):
            bad[:] = True
        else:
            with np.errstate(invalid="ignore"):
                a = attrs.astype(float)
                bad |= ~np.isfinite(a).all(axis=1)
                bad |= (a < 0).any(axis=1) | (a >= N_VALUES[None, :]).any(axis=1)
        conf = np.asarray(batch.confidence)
        if conf.shape == (n,):
            with np.errstate(invalid="ignore"):
                c = conf.astype(float)
                bad |= ~np.isfinite(c) | (c < 0.0) | (c > 1.0)
        else:
            bad[:] = True
        labels = np.asarray(batch.labels)
        if labels.shape == (n,):
            with np.errstate(invalid="ignore"):
                l = labels.astype(float)
                bad |= ~np.isfinite(l) | (l < 0) | (l >= float(1 << N_LABELS))
        else:
            bad[:] = True
        w = np.asarray(batch.worker)
        if w.shape == (n,):
            with np.errstate(invalid="ignore"):
                wf = w.astype(float)
                bad |= ~np.isfinite(wf) | (wf < 0)
                if org is not None:
                    bad |= wf >= float(org.n_workers)
        else:
            bad[:] = True
        return bad

    # ------------------------------------------------------------------
    # claims
    # ------------------------------------------------------------------
    def inspect_claim(self, claim, sender: str, node) -> Verdict | None:
        caps = self.caps
        status = getattr(claim, "status", "proposed")
        if status in ("superseded", "rejected"):
            return None    # retirement notices carry no new content
        hier = getattr(node, "hier", None)
        nodes = getattr(hier, "nodes", {}) if hier is not None else {}
        policy = getattr(node, "policy", None)
        lineage_on = bool(getattr(policy, "lineage", True))
        producer = str(getattr(claim, "producer_id", ""))
        bare = getattr(claim, "layer", "") == "worker" or (producer not in nodes and _worker_index(producer) >= 0)
        origin = getattr(claim, "origin_attack", None)
        reasons: list[tuple[str, float]] = []
        n_min = self._n_min(node)

        # --- schema sanity ------------------------------------------------------------------------
        if "sanity" in caps and self._malformed_claim(claim):
            reasons.append(("malformed_claim", 1.0))

        # --- claim rules --------------------------------------------------------------------------
        if "claim_rules" in caps and not reasons:
            conf = getattr(claim, "confidence", 0.0)
            if _finite(conf) and float(conf) > 0.9 and int(claim.n) < n_min:
                reasons.append(("confidence_inflation", 1.0))
            if bare:
                key = (getattr(node, "unit_id", "?"), int(getattr(claim, "round_created", 0)), int(claim.cell),
                       int(claim.label), int(claim.sign), int(claim.n), int(claim.k))
                seen = self._echo.setdefault(key, set())
                if seen and producer not in seen:
                    reasons.append(("echoed_claim", 1.0))
                seen.add(producer)
        if "provenance" in caps and bare and hier is not None and not reasons:
            t = _team_index(getattr(node, "unit_id", ""))
            w = _worker_index(producer)
            org = getattr(hier, "org", None)
            if t is not None and org is not None and w >= 0:
                if w >= org.n_workers or int(org.worker_team[w]) != t:
                    reasons.append(("provenance_not_member", 1.0))

        # --- producer signature (node claims) -----------------------------------------------------
        if "signature" in caps and not bare and lineage_on and status in ("accepted", "contested") and not reasons:
            key_holder = nodes.get(producer) or nodes.get(sender)
            if key_holder is not None and hasattr(key_holder, "key"):
                sig = getattr(claim, "signature", "")
                if not sig:
                    reasons.append(("unsigned_claim", 1.0))
                elif not claim.verify(key_holder.key):
                    reasons.append(("signature_invalid", 1.0))
                else:
                    self.signatures_verified += 1

        # --- backing: counts must be supported by the sender's own promoted evidence ---------------
        if "backing" in caps and not reasons:
            if not bare:
                sk = getattr(node, "received_from", {}).get(sender)
                budget = int(getattr(policy, "byte_budget", 0) or 0)
                if sk is not None and len(getattr(sk, "ids", ())) and budget == 0:
                    cnt = sk.lookup(np.array([int(claim.cell)]))[0]
                    n_s, k_s = int(cnt[COL_N]), int(cnt[COL_K0 + int(claim.label)])
                    if n_s > 0 and (int(claim.n) > BACKING_SLACK[0] * n_s + BACKING_SLACK[1]
                                    or int(claim.k) > BACKING_SLACK[0] * k_s + BACKING_SLACK[1]):
                        reasons.append(("unbacked_by_sender_sketch", 1.0))
            else:
                attrs, labels, workers = self._store(node)
                w = _worker_index(producer)
                if len(attrs) and w >= 0:
                    m = self._cell_mask(attrs, int(claim.cell)) & (workers == w)
                    if int(m.sum()) < 0.5 * max(int(claim.n), 1):
                        reasons.append(("unbacked_by_producer_observations", 1.0))

        # --- consistency of a bare claim with the rest of its own team ------------------------------
        if "consistency" in caps and bare and not reasons:
            attrs, labels, workers = self._store(node)
            w = _worker_index(producer)
            if len(attrs):
                m = self._cell_mask(attrs, int(claim.cell)) & (workers != w)
                n_ind = int(m.sum())
                if n_ind >= n_min:
                    k_ind = int(((labels[m] >> int(claim.label)) & 1).sum())
                    sign = 1 if int(claim.sign) >= 0 else -1
                    p = float(rate_test(np.array([int(claim.n)]), np.array([int(claim.k)]), np.array([n_ind]),
                                        np.array([k_ind]), np.array([sign]))[0])
                    if p < CONSISTENCY_P:
                        reasons.append((f"inconsistent_with_independent_evidence(p={p:.1e})", 1.0 - p))

        # --- independent support requirement for bare claims (lineage_aware) -----------------------
        if "support" in caps and bare and not reasons:
            is_ = float(getattr(getattr(claim, "support", None), "independent_support", 0.0) or 0.0)
            if is_ < self._support_min(node):
                reasons.append(("insufficient_independent_support", 1.0))

        # --- content classifier on device-produced (bare) claims ------------------------------------
        if bare and not reasons and any(c in caps for c in CLASSIFIER_CAPS):
            if "classifier_cloud" in caps:
                slm, tag = self.slm_cloud, "cloud"
                self.tokens_to_cloud += int(claim.wire_bytes() // 4)
            elif "classifier_central" in caps:
                slm, tag = self.slm_central, "central"
            else:
                slm, tag = self.slm_local, "local"
            self.classifier_calls += 1
            # ASSUMPTION: classifier at the profile operating point; consults origin_attack (see docstring)
            if slm is not None and bool(slm.classify_poison(np.array([1 if origin else 0]))[0]):
                reasons.append((f"content_classifier_{tag}", 1.0))

        # --- counters --------------------------------------------------------------------------------
        flag = bool(reasons)
        self.claims_seen += 1
        if flag:
            self.flagged_claims += 1
            for r, _ in reasons:
                self._bump(r.split("(")[0])
        if origin:
            self.attack_claims_seen += 1
            self.attack_claims_flagged += int(flag)
            c = self.claim_type_counts.setdefault(str(origin), [0, 0])
        else:
            self.benign_claims_seen += 1
            self.benign_claims_flagged += int(flag)
            c = self.claim_type_counts.setdefault("benign", [0, 0])
        c[0] += 1
        c[1] += int(flag)
        if not flag:
            return None
        return Verdict(flag=True, score=max(s for _, s in reasons), reason="; ".join(r for r, _ in reasons))

    @staticmethod
    def _malformed_claim(claim) -> bool:
        ci = cell_index()
        try:
            cell, label, sign = int(claim.cell), int(claim.label), int(claim.sign)
            n, k = int(claim.n), int(claim.k)
        except (TypeError, ValueError):
            return True
        if not (0 <= cell < ci.total) or not (0 <= label < N_LABELS) or sign not in (1, -1):
            return True
        if n < 0 or k < 0 or k > n:
            return True
        for name, lo, hi in (("confidence", 0.0, 1.0), ("rate", 0.0, 1.0), ("p_value", 0.0, 1.0)):
            v = getattr(claim, name, 0.0)
            if not _finite(v) or not (lo <= float(v) <= hi):
                return True
        return False

    # ------------------------------------------------------------------
    # reporting
    # ------------------------------------------------------------------
    def _attack_name(self, code: int) -> str:
        if code in self.attack_names:
            return str(self.attack_names[code])
        try:   # attacks.py may expose a code table (any of these names); imported lazily, never required
            from . import attacks as _att  # type: ignore
            for attr in ("ATTACK_NAMES", "ATTACK_TYPES", "ATTACK_CODES", "CODE_TO_NAME", "NAME_TO_CODE"):
                tab = getattr(_att, attr, None)
                if isinstance(tab, dict):
                    if code in tab and isinstance(tab[code], str):
                        return tab[code]
                    for k, v in tab.items():
                        if isinstance(k, str) and v == code:
                            return k
                elif isinstance(tab, (list, tuple)) and 0 <= code < len(tab) and isinstance(tab[code], str):
                    return tab[code]
        except Exception:
            pass
        return f"attack_{int(code)}"

    @staticmethod
    def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        return prec, rec, f1

    def summary(self) -> dict[str, Any]:
        r_prec, r_rec, r_f1 = self._prf(self.attack_records_flagged, self.benign_records_flagged,
                                        self.attack_records_seen - self.attack_records_flagged)
        c_prec, c_rec, c_f1 = self._prf(self.attack_claims_flagged, self.benign_claims_flagged,
                                        self.attack_claims_seen - self.attack_claims_flagged)
        per_type: dict[str, dict[str, int]] = {}
        for code, (tot, fl) in sorted(self.record_type_counts.items()):
            d = per_type.setdefault(self._attack_name(int(code)), {})
            d["records"] = d.get("records", 0) + tot
            d["records_flagged"] = d.get("records_flagged", 0) + fl
        for name, (tot, fl) in sorted(self.claim_type_counts.items()):
            d = per_type.setdefault(name, {})
            d["claims"] = d.get("claims", 0) + tot
            d["claims_flagged"] = d.get("claims_flagged", 0) + fl
        return {
            "detector": self.detector,
            "capabilities": sorted(self.caps),
            "classifier_profiles": {
                "local": self.edge_profile.name if self.slm_local is not None else None,
                "central": self.central_profile.name if self.central_profile is not None else None,
                "cloud": self.cloud_profile.name if self.cloud_profile is not None else None,
                "note": "content classifiers are profile operating points (assumption), not measured detectors",
            },
            "records_seen": int(self.records_seen),
            "records_flagged": int(self.flagged_records),
            "attack_records_seen": int(self.attack_records_seen),
            "attack_records_flagged": int(self.attack_records_flagged),
            "benign_records_seen": int(self.benign_records_seen),
            "benign_records_flagged": int(self.benign_records_flagged),
            "record_detection_precision": r_prec,
            "record_detection_recall": r_rec,
            "record_detection_f1": r_f1,
            "record_false_suppression": self.benign_records_flagged / max(self.benign_records_seen, 1),
            "claims_seen": int(self.claims_seen),
            "claims_flagged": int(self.flagged_claims),
            "attack_claims_seen": int(self.attack_claims_seen),
            "attack_claims_flagged": int(self.attack_claims_flagged),
            "benign_claims_seen": int(self.benign_claims_seen),
            "benign_claims_flagged": int(self.benign_claims_flagged),
            "claim_detection_precision": c_prec,
            "claim_detection_recall": c_rec,
            "claim_detection_f1": c_f1,
            "claim_false_suppression": self.benign_claims_flagged / max(self.benign_claims_seen, 1),
            "per_attack_type": per_type,
            "reasons": dict(sorted(self.reasons.items())),
            "tokens_to_cloud": int(self.tokens_to_cloud),
            "bytes_exposed": int(self.bytes_exposed),
            "n_canaries_exposed": len(set(self.text_exposed)),
            "classifier_calls": int(self.classifier_calls),
            "classifier_tokens_edge": int(self.classifier_tokens_edge),
            "texts_read_locally": int(self.texts_read_locally),
            "signatures_verified": int(self.signatures_verified),
        }


def build_security(detector: str, cfg: dict[str, Any], profile, seed: int, local_slm: bool = False) -> SecurityPipeline:
    """Factory used by `security.build_security`.  ``detector`` is one of the names in
    `DETECTOR_CAPS` (or several joined with "+"); ``local_slm`` adds the edge content
    classifier.  ``"none"`` without ``local_slm`` returns the no-op base pipeline."""
    detector = (detector or "none").strip()
    if detector == "none" and not local_slm:
        return SecurityPipeline()
    return SecurityImpl(detector, cfg, profile, seed, local_slm=local_slm)
