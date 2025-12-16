"""Embedding generation using Ollama.

Provides async embedding generation for memory storage and retrieval.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# Configuration from environment
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")


async def get_embedding(text: str, model: str | None = None) -> list[float] | None:
    """Generate embedding for text using Ollama.

    Args:
        text: Text to embed
        model: Model to use (defaults to EMBEDDING_MODEL env var)

    Returns:
        Embedding vector or None if failed
    """
    model = model or EMBEDDING_MODEL

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OLLAMA_BASE_URL}/api/embeddings",
                json={"model": model, "prompt": text},
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    logger.error(f"Embedding API error: {response.status}")
                    return None

                data = await response.json()
                return data.get("embedding")

    except aiohttp.ClientError as e:
        logger.error(f"Embedding request failed: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error generating embedding: {e}")
        return None


async def get_embeddings_batch(texts: list[str], model: str | None = None) -> list[list[float] | None]:
    """Generate embeddings for multiple texts.

    Args:
        texts: List of texts to embed
        model: Model to use

    Returns:
        List of embedding vectors (None for failed)
    """
    results = []
    for text in texts:
        embedding = await get_embedding(text, model)
        results.append(embedding)
    return results


async def llm_generate(
    prompt: str,
    model: str = "qwen2.5:7b-instruct",
    system: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 500
) -> str | None:
    """Generate text using Ollama LLM.

    Args:
        prompt: User prompt
        model: Model to use
        system: Optional system prompt
        temperature: Sampling temperature
        max_tokens: Maximum tokens to generate

    Returns:
        Generated text or None if failed
    """
    try:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens
                    }
                },
                timeout=aiohttp.ClientTimeout(total=120)
            ) as response:
                if response.status != 200:
                    logger.error(f"LLM API error: {response.status}")
                    return None

                data = await response.json()
                return data.get("message", {}).get("content")

    except aiohttp.ClientError as e:
        logger.error(f"LLM request failed: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error in LLM generation: {e}")
        return None


async def check_ollama_available() -> dict[str, Any]:
    """Check if Ollama is available and list models.

    Returns:
        Dict with status and available models
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{OLLAMA_BASE_URL}/api/tags",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status != 200:
                    return {"available": False, "error": f"Status {response.status}"}

                data = await response.json()
                models = [m["name"] for m in data.get("models", [])]
                return {
                    "available": True,
                    "models": models,
                    "has_embedding_model": EMBEDDING_MODEL in models or any(EMBEDDING_MODEL in m for m in models),
                    "embedding_model": EMBEDDING_MODEL,
                }

    except Exception as e:
        return {"available": False, "error": str(e)}
