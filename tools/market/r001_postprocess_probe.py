#!/usr/bin/env python
"""Probe for reader test R001: what the model reading path keeps of a reply, on ten constructed NHTSA-style narratives.

    python tools/market/r001_postprocess_probe.py

reader-001 scored every model at predicate F1 0.000 and stored no raw replies (``CHOICE-R001.md``, "What the zero
most likely is"). This probe sends hypothetical reply shapes through the same path E1 uses: the repo's Runtime against
a fake OpenAI-compatible server (JSON schema validated over HTTP), ``ModelExtractor`` without fallback, and E1's own
``record_counts`` and ``micro_f1``. The narratives are written in NHTSA house style for the probe and are not real
complaints. The reply shapes are hypotheses about what a small model might answer, not observed outputs:

- ``typed_vehicle``: the entity type is always ``vehicle`` (the pack's only type), the text is the vehicle as written
  when the narrative names it, else null;
- ``verbatim_or_null``: the vehicle as written with its type when named, both entity fields null otherwise;
- ``null_entity``: both entity fields null, as the prompt asks when a predicate names no listed entity.

It prints, per shape, the predicate F1 and the drops by reason, then the lexical extractor's predicate F1 on the
same records.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mycelic.collective.edge.extract import LexicalExtractor, ModelExtractor, codes_channel  # noqa: E402
from mycelic.collective.experiments.e1_extract import micro_f1, record_counts  # noqa: E402
from mycelic.collective.inference.fakeserver import FakeOpenAIServer, request_payload  # noqa: E402
from mycelic.collective.inference.routing import parse_routing  # noqa: E402
from mycelic.collective.inference.runtime import Runtime  # noqa: E402
from mycelic.collective.packs.canonical import Canonicaliser  # noqa: E402
from mycelic.collective.packs.loader import load_pack  # noqa: E402

PACK_DIR = ROOT / "docs/collective/replay/vehicles/pack"

# (record ref, structured vehicle id, narrative, filed predicates, the vehicle as the narrative writes it)
CASES = (
    ("c01", "FORD-F150-2021", "THE CONTACT OWNS A 2021 FORD F-150. THE CONTACT STATED THAT WHILE DRIVING AT "
     "APPROXIMATELY 45 MPH, THE VEHICLE STALLED WITHOUT WARNING. THE VEHICLE WAS TOWED TO THE DEALER WHERE IT WAS "
     "DIAGNOSED THAT THE ENGINE NEEDED TO BE REPLACED. THE MANUFACTURER WAS NOT NOTIFIED OF THE FAILURE.",
     ("engine",), "2021 FORD F-150"),
    ("c02", "JEEP-GRANDCHEROKEE-2019", "I WAS DRIVING MY 2019 JEEP GRAND CHEROKEE AND WHEN I PRESSED THE BRAKE PEDAL "
     "IT WENT TO THE FLOOR. THE BRAKES DID NOT WORK AND I REAR ENDED ANOTHER CAR.", ("service_brakes",),
     "2019 JEEP GRAND CHEROKEE"),
    ("c03", "HONDA-CRV-2020", "THE CONTACT OWNS A 2020 HONDA CR-V. WHILE DRIVING, THE CONTACT WAS INVOLVED IN A "
     "FRONTAL CRASH AND NO AIR BAGS DEPLOYED.", ("air_bags",), "2020 HONDA CR-V"),
    ("c04", "NISSAN-ROGUE-2022", "MY 2022 NISSAN ROGUE TRANSMISSION SLIPS AND HESITATES WHEN ACCELERATING FROM A STOP. "
     "THE DEALER SAID THE CVT NEEDS TO BE REPLACED.", ("power_train",), "2022 NISSAN ROGUE"),
    ("c05", "CHEVROLET-EQUINOX-2018", "THE STEERING WHEEL LOCKED UP WHILE MAKING A LEFT TURN IN MY 2018 CHEVROLET "
     "EQUINOX. I ALMOST HIT A POLE.", ("steering",), "2018 CHEVROLET EQUINOX"),
    ("c06", "DODGE-DURANGO-2017", "THERE IS A STRONG SMELL OF GASOLINE IN THE CABIN AND FUEL IS LEAKING FROM UNDER THE "
     "VEHICLE.", ("fuel_propulsion_system",), None),
    ("c07", "FORD-ESCAPE-2020", "THE DRIVER SEAT BELT WOULD NOT RETRACT AND WOULD NOT LATCH. THE DEALER COULD NOT "
     "REPAIR IT.", ("seat_belts",), None),
    ("c08", "HONDA-CIVIC-2022", "THE AUTOMATIC EMERGENCY BRAKING ACTIVATED FOR NO REASON ON THE HIGHWAY AND THE CAR "
     "BEHIND ME ALMOST HIT ME.", ("forward_collision_avoidance",), None),
    ("c09", "FORD-EXPLORER-2020", "ON 05-23-2023 THE ENGINE CAUGHT FIRE IN MY DRIVEWAY. THE FIRE DEPARTMENT PUT IT "
     "OUT.", ("engine",), None),
    ("c10", "CHEVROLET-SILVERADO1500-2019", "THE CONTACT OWNS A 2019 CHEVROLET SILVERADO 1500. THE CONTACT STATED "
     "THAT THE ELECTRICAL SYSTEM FAILED AND THE INSTRUMENT PANEL WENT DARK WHILE DRIVING.", ("electrical_system",),
     "2019 CHEVROLET SILVERADO 1500"),
)


def record(ref: str, vehicle: str, text: str) -> dict:
    return {"record_ref": ref, "site": "public", "received_date": "2024-01-02", "language": None, "codes": [],
            "entities": {"vehicle": [vehicle]}, "persons": {}, "reporter": None, "narrative": text,
            "origin_ref": None, "origin_site": None, "synthetic": False}


def reply(shape: str, predicates: tuple[str, ...], phrase: str | None) -> list[dict]:
    if shape == "typed_vehicle":
        etype, etext = "vehicle", phrase
    elif shape == "verbatim_or_null":
        etype, etext = ("vehicle", phrase) if phrase else (None, None)
    else:
        etype, etext = None, None
    return [{"entity_type": etype, "entity_text": etext, "predicate": p, "negated": False} for p in predicates]


def main() -> int:
    pack = load_pack(PACK_DIR)
    canon = Canonicaliser(pack)            # as E1 builds it: no master data
    answers: dict[str, list[dict]] = {}
    server = FakeOpenAIServer("valid", responder=lambda req: {"claims": answers[request_payload(req)["text"]]}).start()
    out: dict[str, dict] = {}
    try:
        config = parse_routing({"schema_version": 1, "endpoints": {"m": {
            "provider": "openai_compat", "boundary": "site:lab", "base_url": server.base_url, "model": "m",
            "response_format": "json_schema", "transport_schema": "full"}}, "routes": {}})
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Runtime(config, boundary="site:lab", ledger_path=Path(tmp) / "ledger.jsonl", run_id="probe",
                              clock=lambda: "2026-10-09T00:00:00.000Z", data_label="public", environ={})
            model = ModelExtractor(pack, canon, runtime, endpoint="m", fallback=False)
            for shape in ("typed_vehicle", "verbatim_or_null", "null_entity"):
                counts, drops = [], {}
                for i, (ref, vehicle, text, predicates, phrase) in enumerate(CASES):
                    answers[text] = reply(shape, predicates, phrase)
                    rec = record(ref, vehicle, text)
                    result = model.extract(rec, codes_channel(rec, pack, canon), ref=f"{shape}-{i}")
                    claims = [{"entity_type": c.entity_type, "entity_id": c.entity_id, "predicate": c.predicate,
                               "negated": c.negated} for c in result.claims]
                    gold = [{"entity_type": "vehicle", "entity_id": vehicle, "negated": False, "predicate": p}
                            for p in predicates]
                    counts.append(record_counts(claims, gold)["predicate"])
                    for reason, n in result.drops.items():
                        if n:
                            drops[reason] = drops.get(reason, 0) + n
                out[shape] = {"predicate_f1": micro_f1(counts), "drops": drops}
    finally:
        server.stop()
    lexical, counts = LexicalExtractor(pack, canon), []
    for ref, vehicle, text, predicates, _ in CASES:
        rec = record(ref, vehicle, text)
        result = lexical.extract(rec, codes_channel(rec, pack, canon))
        claims = [{"entity_type": c.entity_type, "entity_id": c.entity_id, "predicate": c.predicate,
                   "negated": c.negated} for c in result.claims]
        gold = [{"entity_type": "vehicle", "entity_id": vehicle, "negated": False, "predicate": p} for p in predicates]
        counts.append(record_counts(claims, gold)["predicate"])
    out["lexical"] = {"predicate_f1": micro_f1(counts)}
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
