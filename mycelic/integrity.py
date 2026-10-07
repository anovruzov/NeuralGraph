"""Tamper-evident memory rows: a canonical form of what each row asserts, its keyed digest, and the keyring that signs
digests and events.

Every memory row carries ``digest``, ``digest_key_id`` and ``digest_origin`` (schema 4).  The statement that inserts
the row writes them (:meth:`mycelic.store.Tx.insert_memory`, the only insert), so a rebuild from the log, which inserts
the same content, reproduces every digest byte for byte.  :func:`check_memory` recomputes a digest from the row as it
is stored now and its lineage edges; G6's downward verification is built on it.

**Covered** (a change to any of these makes the row read ``mismatch``):

* every row: :data:`CONTENT_FIELDS` as stored, and ``confidence`` rounded to 6 places (so float noise such as
  ``0.1 + 0.2`` is not an edit); :data:`OPTIONAL_FIELDS` only when a row has them and they are not None, so adding such
  a field in a later release leaves every existing digest valid: a raw note's ``expires_at`` and ``attested_at``
  (schema 5) are covered when they are set.  ``attested_at`` is the one covered field written after insert: a
  producer's re-attestation sets it and signs the row again (``store.Tx.set_attested``), only once the row's digest
  checked ``ok``, so an edited row is never re-signed;
* a raw observation (``operator='agent_observation'``): also ``created_at`` (the producer's ``observed_at``),
  ``event_id``, ``local_ref``, ``source_event_ids`` (sorted, not de-duplicated) and the agent's metadata without the
  :data:`LIFECYCLE_METADATA` keys;
* a derived memory: also the ids of its parents (its lineage edges: sorted, de-duplicated) and the metadata keys in
  :data:`DERIVED_METADATA`, each a function of the parent set (a missing key and an explicit null are the same, which
  covers derived rows written before a key existed).

**Not covered**, because it changes after insert or differs between a live node and a rebuild: ``status``,
``superseded_by``, ``applied_at``, ``apply_seq``, a derived memory's ``created_at`` and ``event_id`` (the apply-time
clock; the event id is set after the insert), and the metadata ``version_of``, ``fragility``, ``candidates``,
``fragility_scored_candidates``, ``registered_child_units``, ``reactivated_at`` and ``status_reason``.  A derived
memory's ``source_event_ids`` and ``local_ref`` are always empty and are not in its form either.  Lineage edges'
``contributed_by`` and ``parent_layer`` are not covered either: a verifier checks them against the parent rows.

**Encoding**: ``json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`` as strict UTF-8 with no text
normalisation: what is stored is what is signed.  A lone surrogate raises ``UnicodeEncodeError``, at the point where
the SQLite insert of the same text already fails.

**Keys.**  With ``MYCELIC_EVENT_SIGNING_KEY`` set, a digest is ``HMAC-SHA256(subkey, canonical form)`` with the subkey
``HMAC-SHA256(key, DOMAINS[origin])``.  That separates digests from event signatures (an HMAC under the key itself) and
the two origins from each other: ``write`` (signed by the insert) and ``backfill`` (signed at start-up for rows that
existed before schema 4), so neither can be relabelled as the other.  ``digest_key_id`` names the key by a truncated
hash (:func:`key_id`; not secret).  A key listed in ``MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS`` keeps verifying the digests
and the events it signed; a row signed by a key that is no longer listed reads ``unknown_key``, never ``ok``.  Without a
key the digest is a domain-separated SHA-256 with key id ``none``: it detects corruption, not edits, and once a key is
set a ``none`` row reads ``downgraded``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Iterable

DOMAINS = {"write": b"mycelic-digest-v1", "backfill": b"mycelic-digest-v1/backfill"}
ORIGINS = tuple(DOMAINS)
KEY_ID_DOMAIN = b"mycelic-key-id\x1f"
UNKEYED = "none"
#: fields a later release may add; in the canonical form only when a row has them and they are not None
OPTIONAL_FIELDS = ("expires_at", "attested_at")
CONTENT_FIELDS = ("memory_id", "org_id", "layer", "scope", "text", "topic", "slot", "entity", "kind",
                  "support", "independent_teams", "producer_id", "operator", "rule_id", "visibility")
RAW_FIELDS = ("created_at", "event_id", "local_ref")
#: what retraction and reactivation write into a raw row's metadata; dropped from its canonical form
LIFECYCLE_METADATA = ("status_reason", "reactivated_at")
#: the derived metadata a digest covers: functions of the parent set, shown to readers or needed by verification
DERIVED_METADATA = ("agg_key", "child_layer", "children", "contributing_agents", "contributing_teams",
                    "corroborated_units", "derivation", "effective_min_support", "evidence", "parent_count",
                    "private_observations", "promoted_from", "roots", "rule_chain", "slots",
                    "statement_origins", "statements")


def _encode(d: dict[str, Any]) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _content(m: Any, form: str) -> dict[str, Any]:
    d = {"form": form, **{k: getattr(m, k) for k in CONTENT_FIELDS}, "confidence": round(float(m.confidence), 6)}
    for name in OPTIONAL_FIELDS:
        value = getattr(m, name, None)
        if value is not None:
            d[name] = value
    return d


def canonical_raw(m: Any) -> bytes:
    """The canonical form of a raw observation."""
    d = _content(m, "raw")
    d.update({k: getattr(m, k) for k in RAW_FIELDS})
    d["source_event_ids"] = sorted(m.source_event_ids)
    d["metadata"] = {k: v for k, v in m.metadata.items() if k not in LIFECYCLE_METADATA}
    return _encode(d)


def canonical_derived(m: Any, parent_ids: Iterable[str]) -> bytes:
    """The canonical form of a derived memory with the ids of its parents (in any order, duplicates allowed)."""
    d = _content(m, "derived")
    d["parent_ids"] = sorted(set(parent_ids))
    d["metadata"] = {k: m.metadata.get(k) for k in DERIVED_METADATA}
    return _encode(d)


def canonical(m: Any, parent_ids: Iterable[str] = ()) -> bytes:
    return canonical_raw(m) if m.operator == "agent_observation" else canonical_derived(m, parent_ids)


def key_id(key: str | None) -> str:
    """A key's public name: a truncated, domain-separated hash (``none`` for no key)."""
    if not key:
        return UNKEYED
    return hashlib.sha256(KEY_ID_DOMAIN + key.encode("utf-8")).hexdigest()[:12]


def _unkeyed(origin: str, data: bytes) -> str:
    return hashlib.sha256(DOMAINS[origin] + b"\x1f" + data).hexdigest()


def _same(given: Any, expected: str) -> bool:
    # bytes on both sides, so a non-ASCII value is a mismatch, never a TypeError
    return hmac.compare_digest(str(given).encode("utf-8", "surrogatepass"), expected.encode("ascii"))


class Keyring:
    """The current signing key and the previous ones that still verify.  Never shows a key: ``repr`` lists key ids."""

    __slots__ = ("keyed", "key_id", "key_ids", "_keys", "_subkeys")

    def __init__(self, current: str | None = None, previous: Iterable[str] = ()) -> None:
        keys = [k for k in dict.fromkeys([current, *previous]) if k] if current else []
        self.keyed = bool(keys)
        self.key_id = key_id(current) if keys else UNKEYED
        self.key_ids = [key_id(k) for k in keys]
        self._keys = [k.encode("utf-8") for k in keys]                # current first
        self._subkeys = {(kid, origin): hmac.new(k, domain, hashlib.sha256).digest()
                         for kid, k in zip(self.key_ids, self._keys) for origin, domain in DOMAINS.items()}

    @classmethod
    def from_settings(cls, settings: Any) -> "Keyring":
        return cls(settings.event_signing_key, getattr(settings, "event_signing_keys_previous", None) or ())

    def __repr__(self) -> str:
        return f"Keyring(key_id={self.key_id!r}, key_ids={self.key_ids!r})"

    # ---- memory digests
    def sign(self, data: bytes, *, origin: str = "write") -> tuple[str, str]:
        """(digest, key id) of a canonical form under the current key."""
        if not self.keyed:
            return _unkeyed(origin, data), UNKEYED
        return hmac.new(self._subkeys[(self.key_id, origin)], data, hashlib.sha256).hexdigest(), self.key_id

    def check(self, data: bytes, digest: Any, key_id: Any, *, origin: Any = "write") -> str:
        """``ok``, ``mismatch``, ``unknown_key`` (signed by a key this keyring does not hold) or ``downgraded`` (an
        unkeyed digest where a key is set).  Never raises on what a database returns."""
        if origin not in ORIGINS or not isinstance(key_id, str):
            return "mismatch"
        if key_id == UNKEYED:
            if self.keyed:
                return "downgraded"
            expected = _unkeyed(origin, data)
        else:
            subkey = self._subkeys.get((key_id, origin))
            if subkey is None:
                return "unknown_key"
            expected = hmac.new(subkey, data, hashlib.sha256).hexdigest()
        return "ok" if _same(digest, expected) else "mismatch"

    # ---- event signatures (the wire format predates the keyring: an HMAC under the key itself)
    def event_signature(self, wire: bytes) -> str | None:
        if not self.keyed:
            return None
        return "v1=" + hmac.new(self._keys[0], wire, hashlib.sha256).hexdigest()

    def verify_event(self, wire: bytes, given: Any) -> bool:
        """True when unkeyed (nothing to check), else when any listed key signed ``wire``."""
        if not self.keyed:
            return True
        ok = False
        for k in self._keys:
            ok |= _same(given, "v1=" + hmac.new(k, wire, hashlib.sha256).hexdigest())
        return ok


def check_memory(keyring: Keyring, m: Any, parent_ids: Iterable[str], digest: Any, key_id: Any, origin: Any) -> str:
    """The integrity of one stored row given its lineage parents and its digest columns: ``ok``, ``missing`` (no
    digest), ``mismatch``, ``unknown_key`` or ``downgraded`` (see :meth:`Keyring.check`)."""
    if digest is None:
        return "missing"
    if not isinstance(digest, str) or not isinstance(key_id, str) or not isinstance(origin, str):
        return "mismatch"
    try:
        data = canonical(m, parent_ids)
    except (TypeError, ValueError, AttributeError):         # a tampered row that cannot even be encoded
        return "mismatch"
    return keyring.check(data, digest, key_id, origin=origin)
