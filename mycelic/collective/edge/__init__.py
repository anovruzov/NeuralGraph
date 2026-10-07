"""What runs inside a site's boundary. ``extract`` turns records into typed claims through two channels: the
structured codes (no model) and in-boundary extraction from narratives (lexical, or a model behind the runtime
with deterministic post-processing). ``records`` is the site's own SQLite store (records, claims, the emission log),
``weeks`` the ISO-week rules every part shares, ``egress`` the Boundary (the only path out of a site: k-suppressed
weekly count cells and windowed usage summaries, both validated and never revised, and bucketed verdicts; and the
only path in for a question and, since G7, a packet request; since G7 also the bucketed, suppressed packet
summary out), ``site`` the ``EdgeSite`` that ties them together, ``verify`` (G6) the ``SiteVerifier`` that answers a
question from the site's own records with the in-boundary model or the lexical judge, and ``packets`` (G7) the
``PacketAssembler`` that answers a packet request from a stored confirm's records, keeps the full packet inside the
site and lets only the summary out. Nothing here imports ``experiments``. This file imports nothing.
"""
