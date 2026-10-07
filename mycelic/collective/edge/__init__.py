"""What runs inside a site's boundary. ``extract`` turns records into typed claims through two channels: the
structured codes (no model) and in-boundary extraction from narratives (lexical, or a model behind the runtime
with deterministic post-processing). ``records`` is the site's own SQLite store (records, claims, the emission log),
``weeks`` the ISO-week rules every part shares, ``egress`` the Boundary (the only path out of a site: k-suppressed
weekly count cells and windowed usage summaries, both validated and never revised, and bucketed verdicts; and the
only path in for a question), ``site`` the ``EdgeSite`` that ties them together and ``verify`` (G6) the
``SiteVerifier`` that answers a question from the site's own records with the in-boundary model or the lexical
judge. Nothing here imports ``experiments``. This file imports nothing.
"""
