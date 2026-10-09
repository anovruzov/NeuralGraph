"""Message ingestion shared by the research benchmark harnesses.

research/benchmarks/runner.py, retrieval_eval.py (and through it
two_agent_eval.py) turn a LoCoMo-format conversation into MESSAGE nodes here,
so every harness indexes the same fields:

- each turn keeps its ``dia_id`` and 1-based ``session_index`` in metadata;
- an image turn's caption (``blip_caption``) is appended to the stored text as
  ``[image: <caption>]`` and kept in ``metadata["image_caption"]``;
- ``created_at`` is the parsed session date, not the ingestion clock.

Embedding failures are counted, never skipped silently (call_center.py uses
this too): ``check_embeddings`` raises ``EmbeddingFailureError`` naming the
count unless missing embeddings are explicitly allowed (``allow_missing=True``
or ALLOW_MISSING_EMBEDDINGS=1).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from ...temporal_utils import (
    extract_duration_metadata,
    extract_explicit_date,
    generate_temporal_tokens,
    parse_datetime_flexible,
    resolve_relative_dates,
)
from .data_types import NeuralNode, NodeLayer
from .service import extract_keywords

logger = logging.getLogger(__name__)


class EmbeddingFailureError(RuntimeError):
    """One or more messages could not be embedded."""

    def __init__(self, failed: int, total: int, where: str = ""):
        self.failed = failed
        self.total = total
        place = f" ({where})" if where else ""
        super().__init__(
            f"{failed} of {total} messages could not be embedded{place}; "
            "check the embedding server, or pass allow_missing=True "
            "(ALLOW_MISSING_EMBEDDINGS=1) to index the rest without them"
        )


def allow_missing_from_env() -> bool:
    """ALLOW_MISSING_EMBEDDINGS=1 lets a harness skip messages it could not embed."""
    return os.environ.get("ALLOW_MISSING_EMBEDDINGS", "0") == "1"


def check_embeddings(
    embeddings: list[list[float] | None],
    where: str = "",
    allow_missing: bool = False,
) -> int:
    """Count empty embeddings; raise unless missing ones are allowed.

    Args:
        embeddings: One vector per message ([] or None = the call failed)
        where: Label for the error message (e.g. "conv_3")
        allow_missing: Log the count and return it instead of raising

    Returns:
        Number of messages without an embedding
    """
    failed = sum(1 for e in embeddings if not e)
    if failed and not allow_missing:
        raise EmbeddingFailureError(failed, len(embeddings), where)
    if failed:
        logger.warning("%d of %d messages have no embedding (%s); they are skipped",
                       failed, len(embeddings), where or "ingestion")
    return failed


def stored_text(text: str, caption: str | None) -> str:
    """Message text plus its image caption in a marked form."""
    if not caption:
        return text
    marker = f"[image: {caption}]"
    return f"{text} {marker}" if text else marker


def flatten_locomo(conv: dict) -> list[dict[str, Any]]:
    """Flatten a LoCoMo conversation into messages in dialogue order.

    Each message has ``speaker``, ``text`` (stored text, caption included),
    ``raw_text``, ``datetime`` (session date string), ``dia_id``,
    ``session_index`` and ``image_caption`` (None for text-only turns).
    """
    conversation = conv.get("conversation", conv)
    messages: list[dict[str, Any]] = []
    session_idx = 1
    while f"session_{session_idx}" in conversation:
        datetime_str = conversation.get(f"session_{session_idx}_date_time", "")
        for msg in conversation[f"session_{session_idx}"]:
            raw = msg.get("text", "")
            caption = msg.get("blip_caption") or None
            messages.append({
                "speaker": msg.get("speaker", "Unknown"),
                "text": stored_text(raw, caption),
                "raw_text": raw,
                "datetime": datetime_str,
                "dia_id": msg.get("dia_id"),
                "session_index": session_idx,
                "image_caption": caption,
            })
        session_idx += 1
    return messages


def build_message_node(
    node_id: str,
    session_key: str,
    msg: dict[str, Any],
    embedding: list[float],
) -> NeuralNode:
    """MESSAGE node for one flattened message (see ``flatten_locomo``).

    Content is the stored text; dates resolved from it go to metadata only.
    """
    text = msg["text"]
    message_date = parse_datetime_flexible(msg.get("datetime", ""))
    temporal = generate_temporal_tokens(message_date, text)
    _, resolved_dates = resolve_relative_dates(text, message_date)
    explicit_date = extract_explicit_date(text, message_date)
    duration = extract_duration_metadata(text, message_date)

    resolved_date, source = None, None
    if explicit_date:
        resolved_date, source = explicit_date, "explicit"
    elif resolved_dates:
        values = list(dict.fromkeys(
            v for v in resolved_dates.values() if isinstance(v, str) and re.search(r"\d", v)
        ))
        if len(values) == 1:
            resolved_date, source = values[0], "relative"
    if not resolved_date and message_date:
        resolved_date, source = message_date.strftime("%Y-%m-%d"), "message"

    entities = set(re.findall(r"\b[A-Z][a-z]+\b", text))
    entities.discard("I")

    node = NeuralNode(
        node_id=node_id,
        session_key=session_key,
        content=text,
        layer=NodeLayer.MESSAGE,
        embedding=embedding,
        metadata={
            "speaker": msg["speaker"],
            "datetime": msg.get("datetime", ""),  # Message timestamp for reasoning
            "dia_id": msg.get("dia_id"),
            "session_index": msg.get("session_index"),
            "image_caption": msg.get("image_caption"),
            "keywords": list(extract_keywords(text)),
            "entities": list(entities),
            "temporal_tokens": temporal["date_tokens"],  # For retrieval indexing only
            "temporal_metadata": temporal["temporal_metadata"],
            "resolved_date": resolved_date,
            "resolved_date_source": source,
            "explicit_date": explicit_date,
            "duration_years": duration.get("duration_years"),
            "duration_months": duration.get("duration_months"),
            "since_year": duration.get("since_year"),
            "since_date": duration.get("since_date"),
            "date_tokens": temporal["date_tokens"],  # Backward compat
            "temporal": temporal["temporal_metadata"],  # Backward compat
            "resolved_dates": resolved_dates,  # Metadata only, not in text
        },
    )
    if message_date is not None:
        node.created_at = message_date  # session date; unparseable dates keep the default
    return node
