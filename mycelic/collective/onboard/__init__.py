"""Drafting a pack from one export, with no code per field (test D001).

``python -m mycelic.collective.onboard`` turns any export (CSV, pipe- or tab-delimited text with a header, or JSON
lines) into a pack that loads and runs through the existing pipeline: the loader's check, the lexical extractor and the
pilot audit. Its predicates are the export's own filed categories; its terms are counted from the export's own
narratives; everything else comes from a neutral template. The rule is ``docs/collective/onboard/CHOICE-D001.md``,
the build brief ``docs/collective/onboard/BUILD-D001.md``.

* :mod:`.exports` reads an export (rule 1.1);
* :mod:`.roles` holds the roles file, dates and role inference (rules 1.2 and 1.3);
* :mod:`.draft` drafts the pack and the normalised export (rules 1.3 to 1.6) and the control lexicons (rule 4); it
  holds the one refusal (amendment A2) that the drafter, the check and the last guard share;
* :mod:`.check` checks the privacy floor and the loader (rule 1.7 as amended, M1 to M3);
* :mod:`.score` scores one arm (rules 2 to 6); :mod:`.report` merges the arms (rules 6 and 8).

Every word list, template sentence and pack default is data under ``data/``: the code names no field.
"""
