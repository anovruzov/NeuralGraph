"""Harness: the frozen settings, the planted and no-plant worlds, the shared candidate list and the labels.

The harness sees everything, as every harness in this repo does. It hands HQ code exactly two things: the shared
candidate list and, for the oracle arm O only, the plant spec's sites of a true candidate. It never hands HQ a record,
a narrative, a site graph, a Tesseract score or a label (``docs/collective/ROUTING-SPIKE.md`` 1.1).

Per seed and world (section 3): ``generate(pack, seed, 6, 52)``, plus ``plant(world, spec)`` for the planted world;
the shipped pipeline (``evaluate.baselines.run_pipeline``: per site ingest, lexical extraction, weekly cells through
each site's Boundary, HQ ingests its receive log); detection run X at the pipeline's ``as_of`` with the tie salt; the
detector candidates whose first candidate week is an evaluation week, by ``(-snapshot score, sha256(tie_salt|key))``,
the first ``top_n``. A candidate is true for pattern p when its key is p's key and its snapshot week lies in p's found
window ``[start, min(end + grace, eval_to)]`` (``plant.labels_doc``).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from mycelic.collective.detect.detectors import detect
from mycelic.collective.evaluate.baselines import Pipeline, run_pipeline, world_weeks
from mycelic.collective.evaluate.plant import PlantSpec, check_plant, labels_doc, load_plant, plant
from mycelic.collective.jsonio import canonical_bytes, sha256_hex
from mycelic.collective.packs.generator import generate
from mycelic.collective.packs.loader import FrozenPack, load_pack

ROOT = Path(__file__).resolve().parents[2]
PLANT_PATH = "mycelic/collective/packs/data/device_quality/fixtures/plant_e2_smoke.json"
WORLDS = ("planted", "noplant")

#: section 8 of the design, frozen before any run; ``run.py prereg`` writes these values and a test compares them
#: with the design's table. One value changed after Run 1, before Run 2 (section 10, deviation 16): the ranker sees
#: only records received on or before the question's ``as_of`` (Run 1: "Tesseract over the whole session"). A test
#: requires that to be the only settings difference between ``prereg.json`` and ``prereg-run-2.json``.
SETTINGS: dict[str, Any] = {
    "pack": "device_quality",
    "sites": 6,
    "weeks": 52,
    "start": "2024-01-01",
    "eval_from": 20,
    "eval_to": 51,
    "grace_weeks": 4,
    "seeds": list(range(1, 21)),
    "plant": PLANT_PATH,
    "plant_sha256": "42f868eb2e3127a024b0c66457ae17edeba751f6fd1a57500a726e34501c85c4",
    "run_channel": "X",
    "tie_salt": "routing-spike-v1",
    "top_n": 60,
    "question": "snapshot as_of; question_window and build_question",
    "site_graph": {"storage": "sqlite", "sessions": "one per site", "nodes": "one MESSAGE node per own record",
                   "embedder": "NeuralGraph.chat_memory.llm.fake_embedding", "dim": 256},
    "site_retrieval": {"ranker": "Tesseract over the session's records received on or before the question's as_of",
                       "keep": "own records received in the window", "max_records": 50},
    "site_reader": {"judge": "edge.verify.lexical_judge", "rules": "edge.verify.decide",
                    "secret": "seeded-demo with the world seed"},
    "m": 3,
    "arms": {"primary": ["R", "U"], "context": ["A", "O"], "secondary": ["R1", "R0"], "check": ["P"]},
    "rank_fusion": {"method": "reciprocal rank", "k": 60, "weights": "equal",
                    "rankings": ["key_now", "supporting", "entity_span", "type_span"]},
    "ties": "sha256(tie_salt|question_id|site)",
    "gate": "pushdown.gate.evaluate with the pack's pushdown block; roles by D3",
    "fabric": {"service": "in-process MycelicService and transport", "organisations": "one per arm and per U subset",
               "agent": "hq-<route label>"},
    "bootstrap": {"B": 10000, "alpha": 0.05, "primary_seed": "routing-spike:primary",
                  "noplant_seed": "routing-spike:noplant", "secondary_seed": "routing-spike:<a>-<b>"},
    "noplant_bar": 0.05,
}


@dataclass
class World:
    seed: int
    planted: bool
    workdir: Path
    pack: FrozenPack
    pipeline: Pipeline
    records: list[dict[str, Any]]
    master_data: Mapping[str, Mapping[str, Sequence[str]]]
    site_ids: list[str]
    weeks: list[str]
    candidates: list[dict[str, Any]]
    detected: int
    labels: dict[str, Any]

    @property
    def name(self) -> str:
        return f"seed-{self.seed:02d}-{'planted' if self.planted else 'noplant'}"

    @property
    def candidate_sha256(self) -> str:
        return sha256_hex(canonical_bytes(self.candidates))


def load_spec(pack: FrozenPack, settings: Mapping[str, Any] = SETTINGS) -> PlantSpec:
    """The plant spec, refused unless its file's sha256 is the pre-registered one (Appendix A of the design)."""
    path = ROOT / settings["plant"]
    if sha256_hex(path.read_bytes()) != settings["plant_sha256"]:
        raise ValueError("the plant spec is not the pre-registered one") from None
    return load_plant(path, pack)


def build_world(seed: int, planted: bool, workdir: str | Path, *, settings: Mapping[str, Any] = SETTINGS,
                pack: FrozenPack | None = None, spec: PlantSpec | None = None,
                top_n: int | None = None) -> World:
    """Generate (and plant) one world, run the shipped pipeline and detection, and take the shared candidates."""
    pack = pack or load_pack(settings["pack"])
    spec = spec or load_spec(pack, settings)
    workdir = Path(workdir)
    weeks = world_weeks(settings["start"], settings["weeks"])
    generated = generate(pack, seed, settings["sites"], settings["weeks"])
    site_ids = [s["id"] for s in pack.generator["sites"][:settings["sites"]]]
    check_plant(spec, pack, site_ids=site_ids, master_data=generated.master_data, n_weeks=settings["weeks"],
                eval_from=settings["eval_from"], eval_to=settings["eval_to"])
    records = list(generated.records) + (list(plant(generated, spec, pack).records) if planted else [])
    pipeline = run_pipeline(pack, records, site_ids=site_ids, master_data=generated.master_data, weeks=weeks,
                            workdir=workdir)
    try:
        salt = settings["tie_salt"]
        result = detect(pipeline.store, as_of=pipeline.as_of, run_channel=settings["run_channel"], tie_salt=salt)
        first, last = weeks[settings["eval_from"]], weeks[settings["eval_to"]]
        detected = [c for c in result["candidates"] if c["first_candidate_week"] is not None
                    and first <= c["first_candidate_week"] <= last and c["snapshot"] is not None]
        detected.sort(key=lambda c: (-c["snapshot"]["score"], sha256_hex(f"{salt}|{c['key']}")))
        labels = labels_doc(spec, pack, weeks=weeks, eval_from=settings["eval_from"], eval_to=settings["eval_to"],
                            grace_weeks=settings["grace_weeks"])
    except BaseException:
        pipeline.close()
        raise
    n = settings["top_n"] if top_n is None else top_n
    return World(seed=seed, planted=planted, workdir=workdir, pack=pack, pipeline=pipeline, records=records,
                 master_data=generated.master_data, site_ids=site_ids, weeks=weeks, candidates=detected[:n],
                 detected=len(detected), labels=labels)


def candidate_label(key: str, week: str, labels: Mapping[str, Any]) -> dict[str, Any]:
    """``{label: true | decoy | background, pattern, decoy_class, sites}``: true for pattern p when the key is p's and
    the snapshot week lies in p's found window."""
    for p in labels["patterns"]:
        if p["key"] == key and p["found_from"] <= week <= p["found_to"]:
            return {"label": "true", "pattern": p["id"], "decoy_class": None, "sites": list(p["sites"])}
    for d in labels["decoys"]:
        if key in d["keys"]:
            return {"label": "decoy", "pattern": None, "decoy_class": d["class"], "sites": None}
    return {"label": "background", "pattern": None, "decoy_class": None, "sites": None}


def backup_sqlite(src: str | Path, dst: str | Path) -> None:
    """A consistent copy of a WAL database (SQLite's backup API)."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        Path(str(dst) + suffix).unlink(missing_ok=True)
    source = sqlite3.connect(str(src))
    target = sqlite3.connect(str(dst))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def restore_store(site_id: str, pristine: Path, dest_edge: Path) -> None:
    """The site's store as it was before any question (records, claims and emissions; no question or verdict)."""
    backup_sqlite(pristine / f"site-{site_id}.sqlite3", Path(dest_edge) / f"site-{site_id}.sqlite3")
