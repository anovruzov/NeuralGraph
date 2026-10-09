"""The vehicle-like background for the power check (``tools/market/vehicle_power.py``), offline."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VEHICLES = ROOT / "docs" / "collective" / "replay" / "vehicles"
COMMITTED = ROOT / "docs" / "collective" / "power" / "vehicle-background.json"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "market" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


VP = _load("vehicle_power")


class VehicleBackgroundTests(unittest.TestCase):
    def setUp(self) -> None:
        self.probe = json.loads((VEHICLES / "nhtsa-probe.json").read_text(encoding="utf-8"))
        self.bg = VP.background(self.probe, VEHICLES / "pack")

    def test_volumes_come_from_the_recorded_runs(self) -> None:
        self.assertEqual(self.bg["weekly_volume"], round(27397 * 7 / 731, 2))
        self.assertEqual(self.bg["sites"]["count"], 60)
        per_vehicle = 332225 / 3420 / (3653 / 7)
        self.assertEqual(self.bg["entities"]["count"], round(27397 * 7 / 731 / per_vehicle))
        lo, hi = self.bg["plant_entity_rate"]
        self.assertAlmostEqual(lo, 0.24 / 0.05 / (365.25 / 28), places=3)
        self.assertAlmostEqual(hi, 1.75 / 0.20 / (365.25 / 28), places=3)
        klo, khi = self.bg["sextuple_key_rate"]
        self.assertAlmostEqual(klo, 0.97 / (365.25 / 28), places=3)
        self.assertAlmostEqual(khi, 2.27 / (365.25 / 28), places=3)

    def test_failure_shares_fold_categories_the_pack_lacks_into_unknown(self) -> None:
        preds = self.bg["predicates"]
        top = dict((c, n) for c, n in self.probe["complaints"]["top_components"])
        self.assertEqual(sum(preds.values()), sum(top.values()))
        self.assertEqual(preds["engine"], top["ENGINE"])
        # VISIBILITY has no phrase of its own in the pack and FUEL SYSTEM, DIESEL is under the pack's minimum
        self.assertEqual(preds["unknown_or_other"],
                         top["UNKNOWN OR OTHER"] + top["VISIBILITY"] + top["FUEL SYSTEM, DIESEL"]
                         + sum(n for c, n in top.items() if c in ("TRACTION CONTROL SYSTEM",))
                         + sum(n for c, n in top.items() if c not in self._pack_categories()
                               and c not in ("UNKNOWN OR OTHER", "VISIBILITY", "FUEL SYSTEM, DIESEL",
                                             "TRACTION CONTROL SYSTEM")))

    def _pack_categories(self) -> set[str]:
        mapping = json.loads((VEHICLES / "pack" / "mapping.json").read_text(encoding="utf-8"))
        return set(mapping["codes"][0]["value_map"])

    def test_the_committed_file_is_the_builders_output_and_the_power_check_reads_it(self) -> None:
        self.assertEqual(json.loads(COMMITTED.read_text(encoding="utf-8")), self.bg)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bg.json"
            self.assertEqual(VP.main(["--probe", str(VEHICLES / "nhtsa-probe.json"), "--pack", str(VEHICLES / "pack"),
                                      "--out", str(out)]), 0)
            self.assertEqual(out.read_bytes(), COMMITTED.read_bytes())
        from mycelic.collective.pilot import power as P
        from mycelic.collective.pilot.audit import _load as load_pack
        P.check_background(self.bg, load_pack(str(VEHICLES / "pack")))


if __name__ == "__main__":
    unittest.main()
