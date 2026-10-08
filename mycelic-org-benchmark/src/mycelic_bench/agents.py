"""Edge agents: the simulated local SLM perception channel.

A worker's local model turns raw interactions into *structured observations*
(attribute row + observed labels + evidence fingerprint), stripping text.  The
simulated SLM is a noise channel parameterised by a `ModelProfile`:

* attribute omission  - the model fails to register an attribute (cells that
  need it are not produced);
* attribute misread   - the model substitutes a wrong value;
* label drop/spurious - extraction noise on the worker's labels;
* injection susceptibility - a payload in the rationale makes the model emit
  the injected claim.

`ObservationBatch` is what leaves the device.  Bytes are metered on a fixed
compact encoding.  Any real model backend (`backends.py`) must return the
same structure so the rest of the pipeline is backend-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .schemas import ModelProfile
from .vocab import ATTR_INDEX, N_ATTR, N_LABELS, N_VALUES

OBS_BYTES_PER_RECORD = 40   # 9 attrs + 18-bit labels + fingerprint + worker + confidence


@dataclass
class ObservationBatch:
    """Structured observations promoted from workers to their team node."""
    unit_id: str
    round: int
    idx: np.ndarray            # indices into the world record arrays (for evaluation drill-down only)
    attrs: np.ndarray          # perceived attribute rows [n, 9]
    present: np.ndarray        # bool [n, 9] attributes registered by the model
    labels: np.ndarray         # perceived label masks [n]
    worker: np.ndarray         # claimed worker index [n]
    fingerprint: np.ndarray    # evidence root hash [n]
    confidence: np.ndarray     # worker confidence [n]
    signature_ok: np.ndarray   # bool [n]: provenance signature verifies (False for spoofed records)
    is_attack: np.ndarray      # int [n]: attack tag of the record (0 benign) — evaluation only
    quotes: list[str] = field(default_factory=list)   # raw text that left the device (privacy failure)
    injected_claims: list[dict] = field(default_factory=list)  # claims emitted due to prompt injection
    model_calls: int = 0
    tokens_in: int = 0

    def wire_bytes(self) -> int:
        return 16 + OBS_BYTES_PER_RECORD * len(self.idx) + sum(len(q) for q in self.quotes) + 96 * len(self.injected_claims)

    def __len__(self) -> int:
        return len(self.idx)


def schema_valid(attrs: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """Rows that parse: every attribute value inside its vocabulary and the label mask inside the 18 known
    labels.  Any consumer of structured observations validates its input schema; this is not a lineage
    feature, so every system (centralized or hierarchical) applies it at ingestion."""
    attrs = np.asarray(attrs); labels = np.asarray(labels).astype(np.int64)
    ok = np.ones(len(attrs), dtype=bool)
    for a in range(N_ATTR):
        ok &= (attrs[:, a] >= 0) & (attrs[:, a] < N_VALUES[a])
    ok &= (labels >= 0) & (labels < (1 << N_LABELS))
    return ok


class SimulatedSLM:
    """Backend-agnostic perception channel with profile-driven noise."""

    def __init__(self, profile: ModelProfile, seed: int) -> None:
        self.profile = profile
        self.rng = np.random.default_rng(seed)

    def perceive(self, attrs: np.ndarray, labels: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return (attrs_view, present_mask, labels_view)."""
        n = len(attrs)
        p = self.profile
        present = self.rng.random((n, N_ATTR)) >= p.attr_omission
        # model family and task family are salient: omitted 3x less often
        salient = (ATTR_INDEX["model_family"], ATTR_INDEX["task_family"])
        for a in salient:
            present[:, a] |= self.rng.random(n) >= p.attr_omission * 0.3
        view = attrs.astype(np.int64).copy()
        mis = self.rng.random((n, N_ATTR)) < p.attr_misread
        if mis.any():
            rr, cc = np.nonzero(mis)
            view[rr, cc] = (view[rr, cc] + self.rng.integers(1, 100, size=len(rr))) % N_VALUES[cc]
        lab = labels.astype(np.int64).copy()
        bits = ((lab[:, None] >> np.arange(N_LABELS)) & 1).astype(bool)
        drop = self.rng.random((n, N_LABELS)) < p.label_drop
        spur = self.rng.random((n, N_LABELS)) < (p.label_spurious / N_LABELS * 3)
        bits = (bits & ~drop) | spur
        lab = (bits.astype(np.int64) << np.arange(N_LABELS)).sum(axis=1)
        return view, present, lab

    def susceptible(self, n: int) -> np.ndarray:
        return self.rng.random(n) < self.profile.injection_susceptibility

    def classify_poison(self, is_attack: np.ndarray) -> np.ndarray:
        """Content classifier at the profile's operating point.  Returns flag mask.
        NOTE: this is a *profile assumption* (DESIGN.md §9), not a measured detector."""
        r = self.rng.random(len(is_attack))
        return np.where(is_attack > 0, r < self.profile.poison_tpr, r < self.profile.poison_fpr)


def make_batches(
    records,                       # world.RecordView
    round_idx: np.ndarray,         # indices of this round's records
    team_of_record: np.ndarray,
    n_teams: int,
    slm: SimulatedSLM,
    quote_policy: str = "none",
    attack_hook=None,
) -> dict[int, ObservationBatch]:
    """Perceive one round of records and group them by team.

    `attack_hook(batch)` (from attacks.py) may mutate a batch: spoof worker
    ids, inject claims, add malformed records.  It is called after perception
    so the attacker controls what the compromised device emits."""
    if len(round_idx) == 0:
        return {}
    attrs = records.attrs[round_idx]
    labels = records.observed_labels[round_idx]
    view, present, lab = slm.perceive(attrs, labels)
    teams = team_of_record[round_idx]
    order = np.argsort(teams, kind="stable")
    bounds = np.searchsorted(teams[order], np.arange(n_teams + 1))
    out: dict[int, ObservationBatch] = {}
    for t in range(n_teams):
        a, b = bounds[t], bounds[t + 1]
        if a == b:
            continue
        sel = order[a:b]
        gidx = round_idx[sel]
        quotes: list[str] = []
        if quote_policy == "raw":
            quotes = [records.rationale(int(i)) for i in gidx]
        elif quote_policy == "redacted":
            quotes = [_redact(records.rationale(int(i))) for i in gidx]
        batch = ObservationBatch(
            unit_id=f"T{t:04d}", round=int(records.round[gidx[0]]), idx=gidx, attrs=view[sel], present=present[sel],
            labels=lab[sel], worker=records.worker[gidx].astype(np.int64), fingerprint=records.fingerprint[gidx],
            confidence=records.confidence[gidx], signature_ok=np.ones(len(sel), dtype=bool),
            is_attack=records.attack_tag[gidx].astype(np.int64), quotes=quotes,
            model_calls=len(sel), tokens_in=int(len(sel) * 180),
        )
        if attack_hook is not None:
            batch = attack_hook(batch, slm)
        out[t] = batch
    return out


def _redact(text: str) -> str:
    import re
    return re.sub(r"CANARY-[a-z_]+-[0-9a-f]{8}", "[REDACTED]", text)
