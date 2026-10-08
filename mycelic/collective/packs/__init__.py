"""Domain packs: what makes the collective layer general is data plus a small amount of generic code.

A pack (``data/<pack_id>/``) is strict JSON: vocabulary, id formats and aliases, structured-code mappings,
detector parameters, question templates, follow-up types, egress policy and k, connector mappings, a world spec
and labelled fixtures. ``loader`` validates it and freezes it with four sha256 hashes; ``canonical`` resolves entity
ids deterministically (never a model); ``connector`` maps vendor rows to internal records; ``generator`` builds a
seeded synthetic world from the world spec. No module here holds a domain literal (the guard test enforces it),
and nothing here imports ``edge`` or ``experiments``. This file imports nothing.
"""
