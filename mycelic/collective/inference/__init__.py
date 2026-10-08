"""Model inference inside a data boundary.

``runtime.Runtime`` is the only way collective code calls a model. It is bound at construction to the boundary it
runs in (``site:<id>`` or ``central``), refuses any endpoint that would carry raw records out of that boundary,
validates every reply against a JSON schema, repairs once, escalates at most once, and writes one usage-ledger row
per logical attempt that never holds text. ``client`` speaks the OpenAI-compatible chat API over ``http.client``;
``fake`` and ``fakeserver`` are the deterministic stand-ins used by CI. This file imports nothing.
"""
