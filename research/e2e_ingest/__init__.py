"""The EMERGENCE end-to-end ingestion harness (``docs/collective/E2E-INGEST.md``).

Built in the order the brief fixes (section 12). This package holds S3 and S4, the bridge from HQ's store to the
keyed fabric:

* ``publish_api``: the event schema of ``research/routing_spike/publish.py`` sent through
  ``mycelic.sdk.MycelicClient.publish_events`` against a live service, one ``POST /events`` per event, the
  idempotency keys of section 7, bounded retries and a watermark;
* ``hq_bridge``: a read-only reader of HQ's ``pd_questions`` / ``pd_routes`` / ``pd_verdicts`` / ``pd_conclusions``
  tables publishing through ``publish_api`` with its own watermark;
* ``drills``: the harness-side pieces that need the broker itself (M3, M8, M25): the stream read by sequence with the
  keyring check, the forged publishes, the broker version, the metrics counter, and the ``ProcessDriver`` subclass of
  sections 3.6 and M5.

Nothing here reads a record, a narrative or a graph node: the bridge carries verdicts and conclusions only, and every
value it logs is an id, a count, a status or a sha256. Everything is synthetic and stamped ``measurement: false``.
Every pinned module is imported, never edited.
"""
