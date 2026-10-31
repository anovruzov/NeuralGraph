# Judge audit checklist (independent judge agent)

Inputs you get: baseline result JSON, variant result JSON, the hypothesis text, and the exact command/diff that
produced the variant. You do NOT get the orchestrator's opinion. Produce a verdict file
`research/overnight/judgments/<variant>.md` with these sections, tables over prose:

1. **Recompute.** Run `python3 research/overnight/scripts/compare.py <base> <variant>` and paste the table.
   Check n equals the expected sample size and that ids match exactly (no unpaired rows).
2. **Audit every flip.** For each question that changed verdict on strict or lenient, read gold, baseline answer,
   variant answer, and classify: `real-fix` (variant answer is right where baseline was wrong), `real-regression`,
   `judge-noise` (both answers equally right/wrong; the judge flipped), `format-only` (same content, wording made the
   strict judge accept/reject). Count each class.
3. **Leak check.** The variant must not have seen gold: grep the prompt file and any code diff for `gold`, `answer`
   (as a field read before answering), `has_answer`, `evidence`. State what you grepped and the result.
4. **Second judge.** If asked, re-grade both files' answers under the second judge model (strict prompt) with
   `answer_eval.py --regrade` or the supplied command, and report whether the sign of the delta holds.
5. **Verdict.** One of: `confirm` (real fixes ≥ 2x real regressions, delta ≥ 4 on one primary metric, nothing down > 2),
   `reject`, `judge-artifact` (gain is mostly judge-noise/format-only), `needs-more-data`.
   One sentence of justification. Then the exact ledger row to paste.
