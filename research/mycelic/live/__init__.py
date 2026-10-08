"""Mycelic with live LLM agents (local open-weight models via llama-server).

See live/README.md.  Modules: backend (chat client, record/replay cache,
MockBackend), notes (private note stores), agents (edge agents and the A2
kernel reader, code-side canonicalisation), score (evaluator-side extraction
quality; the only module that reads ground truth), run (CLI).
"""
