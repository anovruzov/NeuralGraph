"""Drafting a pack from one export, with no code per field (tests D001 and D002).

``python -m mycelic.collective.onboard`` turns any export (CSV, pipe- or tab-delimited text with a header, or JSON
lines) into a pack that loads and runs through the existing pipeline: the loader's check, the lexical extractor and the
pilot audit. Its predicates are the export's own filed categories; its terms are counted from the export's own
narratives; everything else comes from a neutral template. The rule is ``docs/collective/onboard/CHOICE-D001.md``,
the build brief ``docs/collective/onboard/BUILD-D001.md``. D002 (``CHOICE-D002.md``, ``BUILD-D002.md``) changes two
rules: how a line is split (1.1) and when the run starts (9). The experiment id comes from the settings.

* :mod:`.exports` reads an export (rule 1.1 as D002 changed it: every line through the ``csv`` module);
* :mod:`.roles` holds the roles file, dates and role inference (rules 1.2 and 1.3);
* :mod:`.draft` drafts the pack and the normalised export (rules 1.3 to 1.6) and the control lexicons (rule 4); it
  holds the one refusal (amendment A2) that the drafter, the check, the label-names control and the last guard share,
  each over one company's own export (amendments A10 and A13), and counts what it removed (A14);
* :mod:`.check` checks the privacy floor and the loader (rule 1.7 as amended, M1 to M3);
* :mod:`.score` scores one arm (rules 2 to 6); :mod:`.report` merges the arms (rules 6 and 8), with the last guard
  per company and a backstop over the whole rendered report (A10 and A11).

Every word list, template sentence and pack default is data under ``data/``: the code names no field.
"""
