"""Tests of ``research/e2e_ingest`` (``docs/collective/E2E-INGEST.md``). They run the 200-user EMERGENCE world for real
through the shipped pipeline and the persistent site graphs; nothing is skipped for the environment, and no
assertion message, log or print carries a record's text or ref (comparisons go through digests and counts)."""
