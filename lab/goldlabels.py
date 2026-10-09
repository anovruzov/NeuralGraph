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
* ``nhtsa`` (reader test R001, ``docs/collective/replay/vehicles/CHOICE-R001.md``): real public complaints. NHTSA's
  complaint file is downloaded in the plan job; the complaints of :data:`NHTSA_MAKES` received in
  :data:`NHTSA_WINDOW` are exported as the vehicle replays export them (``tools/market/nhtsa_export.py``) and mapped
  through the vehicle pack (:data:`NHTSA_PACK`), each record's site set to ``public`` (E1's mark of a public
  source). A record is eligible when it has a narrative,
  one vehicle and at least one specific component code. Each label line holds the record **with its codes removed**,
  so a reader sees only the narrative and the vehicle, and its gold is one claim per specific code (the vehicle, the
  code's predicate, not negated): the components the complaint was filed under, not checked labels.
  ``random.Random("nhtsa:<seed>").sample`` takes ``n`` eligible records; lines are in ``record_ref`` order. The
  record's ``public`` block gives the makes, the window, the counts, and the lexical extractor's scores on the same
  lines (:func:`lexical_scores`, E1's own counting), the model-free baseline the models are read against.

The record: ``{"schema_version": 1, "kind": "lab_e1_labels", "source", "pack", "pack_version", "vocabulary_hash",
"n", "seed", "sites", "weeks", "available", "world_digest", "records", "claims", "sha256", "public"}``; ``available``
is the number of independent records the world holds (for ``nhtsa``, the eligible records), the generator-only fields
(``n`` to ``world_digest``) are null for fixtures, and ``public`` is null except for ``nhtsa``. Equal arguments give equal bytes. Asking for more records than the world holds raises
:class:`GoldLabelsError`.
"""
from __future__ import annotations

import importlib.util
import random
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping, Sequence

from mycelic.collective.jsonio import canonical_dumps, sha256_hex, strict_load
from mycelic.collective.packs import loader
from mycelic.collective.packs.generator import generate, world_digest

ROOT = Path(__file__).resolve().parents[1]
GOLD_SITES = 6
GOLD_WEEKS = 104
GOLD_KEYS = ("entity_type", "entity_id", "negated", "predicate")
SOURCES = ("fixtures", "generator", "nhtsa")
NHTSA_PACK = "docs/collective/replay/vehicles/pack"
NHTSA_MAKES = ("FORD", "CHEVROLET", "JEEP", "HONDA", "NISSAN", "DODGE")
NHTSA_WINDOW = ("20230101", "20241231")
PUBLIC_SITE = "public"


class GoldLabelsError(ValueError):
    def __init__(self, problem: str) -> None:
        super().__init__(problem)
        self.problem = problem


def _counts(data: bytes) -> tuple[int, int]:
    """(records, claims) of label-file bytes."""
    lines = [strict_load(line) for line in data.split(b"\n") if line.strip()]
    return len(lines), sum(len(line["gold"]) for line in lines)


def nhtsa_export() -> ModuleType:
    """``tools/market/nhtsa_export.py``, the vehicle replays' exporter (loaded by path: tools is not a package)."""
    spec = importlib.util.spec_from_file_location("nhtsa_export", ROOT / "tools" / "market" / "nhtsa_export.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def nhtsa_lines(pack: loader.FrozenPack, export_rows: Sequence[Mapping[str, Any]], n: int,
                seed: int) -> tuple[list[str], dict[str, Any]]:
    """The label lines of ``n`` sampled eligible records (codes removed, gold from the codes) and the counts."""
    from mycelic.collective.packs.connector import map_rows
    mapped = map_rows(export_rows, pack, mapping="mapping", synthetic=False)
    eligible = []
    for r in mapped.records:
        vehicles = r["entities"].get("vehicle", [])
        preds = sorted({pack.codes[c].predicate for c in r["codes"] if c in pack.codes and pack.codes[c].specific})
        if r["narrative"].strip() and len(vehicles) == 1 and preds:
            gold = [{"entity_type": "vehicle", "entity_id": vehicles[0], "negated": False, "predicate": p}
                    for p in preds]
            eligible.append((dict(r, codes=[], site=PUBLIC_SITE), gold))   # a public source's records
    eligible.sort(key=lambda pair: pair[0]["record_ref"])
    if len(eligible) < n:
        raise GoldLabelsError(f"only {len(eligible)} eligible public records")
    sample = sorted(random.Random(f"nhtsa:{seed}").sample(eligible, n), key=lambda pair: pair[0]["record_ref"])
    lines = [canonical_dumps({"record": r, "gold": gold}) + "\n" for r, gold in sample]
    return lines, {"mapped": len(mapped.records), "rejected": dict(sorted(mapped.rejected.items())),
                   "eligible": len(eligible)}


def lexical_scores(pack: loader.FrozenPack, data: bytes) -> dict[str, Any]:
    """The lexical extractor's micro F1 on label-file bytes, counted as E1 counts a model's claims."""
    from mycelic.collective.edge.extract import LexicalExtractor, codes_channel
    from mycelic.collective.experiments.e1_extract import micro_f1, record_counts
    from mycelic.collective.packs.canonical import Canonicaliser
    canonicaliser = Canonicaliser(pack)
    lexical = LexicalExtractor(pack, canonicaliser)
    counts: dict[str, list[tuple[int, int, int]]] = {"field": [], "claim": [], "entity": [], "predicate": []}
    for line in data.split(b"\n"):
        if not line.strip():
            continue
        doc = strict_load(line)
        record = doc["record"]
        result = lexical.extract(record, codes_channel(record, pack, canonicaliser))
        claims = [{"entity_type": c.entity_type, "entity_id": c.entity_id, "predicate": c.predicate,
                   "negated": c.negated} for c in result.claims]
        for key, value in record_counts(claims, doc["gold"]).items():
            counts[key].append(value)
    return {f"{key}_f1": micro_f1(value) for key, value in counts.items()}


def build_labels(source: str, pack_id: str, n: int | None = None, seed: int | None = None, *,
                 fetch: Callable[[], bytes] | None = None) -> tuple[bytes, dict[str, Any]]:
    """``fetch`` returns the NHTSA complaint archive's bytes (``nhtsa`` only; default: download it)."""
    if source not in SOURCES:
        raise GoldLabelsError(f"source must be one of {', '.join(SOURCES)}")
    if source == "nhtsa" and pack_id != NHTSA_PACK:
        raise GoldLabelsError(f"nhtsa labels read the vehicle pack {NHTSA_PACK}")
    pack = loader.load_pack(ROOT / pack_id if source == "nhtsa" else pack_id)
    record: dict[str, Any] = {"schema_version": 1, "kind": "lab_e1_labels", "source": source, "pack": pack.id,
                              "pack_version": pack.version, "vocabulary_hash": pack.vocabulary_hash, "n": None,
                              "seed": None, "sites": None, "weeks": None, "available": None, "world_digest": None,
                              "public": None}
    if source == "nhtsa":
        if n is None or seed is None:
            raise GoldLabelsError("nhtsa labels need n and seed")
        export = nhtsa_export()
        blob = fetch() if fetch is not None else export._fetch(export.COMPLAINTS_URL)
        categories, _ = export.pack_categories(ROOT / pack_id)
        rows: list[dict[str, Any]] = []
        kept: dict[str, int] = {}
        for make in NHTSA_MAKES:
            made, counts = export.complaints(export._rows(blob), make, *NHTSA_WINDOW, categories)
            rows += made
            kept[make] = counts.get("complaints", 0)
        lines, counts = nhtsa_lines(pack, rows, n, seed)
        data = "".join(lines).encode("utf-8")
        record.update(n=n, seed=seed, available=counts["eligible"],
                      public={"makes": list(NHTSA_MAKES), "window": list(NHTSA_WINDOW), "complaints": kept,
                              **counts, "lexical": lexical_scores(pack, data)})
    elif source == "fixtures":
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
