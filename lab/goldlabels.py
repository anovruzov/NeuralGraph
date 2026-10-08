"""E1's labelled records for a lab run: generator ground truth or the pack's fixture records, as label-file bytes.

:func:`build_labels` returns the bytes of an E1 labels file (``e1_extract.read_labels`` reads it: one canonical
``{"record", "gold"}`` line per record) and a record describing it.

* ``generator``: the world ``generate(pack, seed, GOLD_SITES, GOLD_WEEKS)``; its independent records (``origin_ref``
  None: no cross-site copies) sorted by ``record_ref``; ``random.Random(seed).sample`` of ``n`` of them; one line per
  sampled record in ``record_ref`` order, its gold the generator's own claims for it in generator order, each with
  exactly ``entity_type``, ``entity_id``, ``negated`` and ``predicate`` (the generator's ``entity_text`` is dropped).
  The labels are ground truth by construction on template text: they say how well a model reads the generator's
  sentences, not real narratives.
* ``fixtures``: the pack's ``fixtures/records.jsonl``, byte for byte (the pack author's own labelled records).

The record: ``{"schema_version": 1, "kind": "lab_e1_labels", "source", "pack", "pack_version", "vocabulary_hash",
"n", "seed", "sites", "weeks", "available", "world_digest", "records", "claims", "sha256"}``; ``available`` is the
number of independent records the world holds, and the generator-only fields (``n`` to ``world_digest``) are null for
fixtures. Equal arguments give equal bytes. Asking for more records than the world holds raises
:class:`GoldLabelsError`.
"""
from __future__ import annotations

import random
from typing import Any

from mycelic.collective.jsonio import canonical_dumps, sha256_hex, strict_load
from mycelic.collective.packs import loader
from mycelic.collective.packs.generator import generate, world_digest

GOLD_SITES = 6
GOLD_WEEKS = 104
GOLD_KEYS = ("entity_type", "entity_id", "negated", "predicate")
SOURCES = ("fixtures", "generator")


class GoldLabelsError(ValueError):
    def __init__(self, problem: str) -> None:
        super().__init__(problem)
        self.problem = problem


def _counts(data: bytes) -> tuple[int, int]:
    """(records, claims) of label-file bytes."""
    lines = [strict_load(line) for line in data.split(b"\n") if line.strip()]
    return len(lines), sum(len(line["gold"]) for line in lines)


def build_labels(source: str, pack_id: str, n: int | None = None,
                 seed: int | None = None) -> tuple[bytes, dict[str, Any]]:
    if source not in SOURCES:
        raise GoldLabelsError(f"source must be one of {', '.join(SOURCES)}")
    pack = loader.load_pack(pack_id)
    record: dict[str, Any] = {"schema_version": 1, "kind": "lab_e1_labels", "source": source, "pack": pack.id,
                              "pack_version": pack.version, "vocabulary_hash": pack.vocabulary_hash, "n": None,
                              "seed": None, "sites": None, "weeks": None, "available": None, "world_digest": None}
    if source == "fixtures":
        data = (loader.BUILTIN_ROOT / pack_id / "fixtures" / "records.jsonl").read_bytes()
    else:
        if n is None or seed is None:
            raise GoldLabelsError("generator labels need n and seed")
        world = generate(pack, seed, GOLD_SITES, GOLD_WEEKS)
        independent = sorted((r for r in world.records if r["origin_ref"] is None), key=lambda r: r["record_ref"])
        if len(independent) < n:
            raise GoldLabelsError(f"the generator world holds only {len(independent)} independent records")
        sample = sorted(random.Random(seed).sample(independent, n), key=lambda r: r["record_ref"])
        lines = []
        for r in sample:
            gold = [{key: claim[key] for key in GOLD_KEYS} for claim in world.gold[r["record_ref"]]]
            lines.append(canonical_dumps({"record": r, "gold": gold}) + "\n")
        data = "".join(lines).encode("utf-8")
        record.update(n=n, seed=seed, sites=GOLD_SITES, weeks=GOLD_WEEKS, available=len(independent),
                      world_digest=world_digest(world))
    records, claims = _counts(data)
    record.update(records=records, claims=claims, sha256=sha256_hex(data))
    return data, record
