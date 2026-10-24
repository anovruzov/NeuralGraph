"""An unevaluated required gate may not report ``checks_pass: true``.

``checks_pass`` used to mean "nothing that ran failed", so a run that skipped its privacy and integrity drills, or
carried no suites evidence, said ``checks_pass: true`` while the gates that would have caught a defect were never
evaluated.  That is how a stale detector pin survived a whole ablation series: the runs skipped the drills and
passed no suites directory, c10 never evaluated, and the verdict still read as green at a glance.
"""
from __future__ import annotations

import unittest

from research.e2e_ingest import report as rp

OK = {"ok": True, "assertions": 1}
BAD = {"ok": False, "assertions": 1}


class RequiredChecksTests(unittest.TestCase):
    def test_a_skipped_drill_fails_checks_pass_and_is_named(self) -> None:
        v = rp.verdict({"c0_real_fabric_keyed": OK}, not_run={"c1_signed": "--skip-drills"},
                       suites_green=True, required=rp.RUN_REQUIRED_CHECKS)
        self.assertFalse(v["checks_pass"], "a run that skipped a drill has not passed its checks")
        self.assertEqual(v["unevaluated_required"], ["c1_signed"])
        self.assertIn("c1_signed", v["reason"])
        self.assertFalse(v["pass"])

    def test_missing_suites_evidence_fails_checks_pass(self) -> None:
        v = rp.verdict({"c0_real_fabric_keyed": OK}, not_run={"c10_suites_green": "no suites directory"},
                       suites_green=False, required=rp.RUN_REQUIRED_CHECKS)
        self.assertFalse(v["checks_pass"])
        self.assertEqual(v["unevaluated_required"], ["c10_suites_green"])

    def test_another_jobs_check_does_not_fail_this_one(self) -> None:
        # c5 belongs to the measure job and c6 to the workflow test: a run does not owe them
        v = rp.verdict({"c0_real_fabric_keyed": OK},
                       not_run={"c5_measured": "the measure job", "c6_ci": "the workflow test"},
                       suites_green=True, required=rp.RUN_REQUIRED_CHECKS)
        self.assertTrue(v["checks_pass"])
        self.assertEqual(v["unevaluated_required"], [])
        self.assertTrue(v["pass"])

    def test_a_failed_check_still_fails_as_before(self) -> None:
        v = rp.verdict({"c0_real_fabric_keyed": BAD}, suites_green=True, required=rp.RUN_REQUIRED_CHECKS)
        self.assertFalse(v["checks_pass"])
        self.assertEqual(v["failed"], ["c0_real_fabric_keyed"])
        self.assertEqual(v["unevaluated_required"], [])

    def test_without_a_required_set_the_old_contract_holds(self) -> None:
        # callers that owe nothing (the measure job passes its own set) keep the previous meaning
        v = rp.verdict({"c0_real_fabric_keyed": OK}, not_run={"c1_signed": "--skip-drills"}, suites_green=True)
        self.assertTrue(v["checks_pass"])
        self.assertEqual(v["unevaluated_required"], [])

    def test_the_run_owes_every_e2e_check_but_the_two_other_jobs_own(self) -> None:
        self.assertEqual(set(rp.RUN_REQUIRED_CHECKS), set(rp.E2E_CHECKS))
        self.assertNotIn("c5_measured", rp.RUN_REQUIRED_CHECKS)
        self.assertNotIn("c6_ci", rp.RUN_REQUIRED_CHECKS)
        for drill in ("c1_signed", "c2_lineage", "c3_idempotent", "c4_recovery", "c10_suites_green"):
            self.assertIn(drill, rp.RUN_REQUIRED_CHECKS)


if __name__ == "__main__":
    unittest.main()
