"""
UNIVERSAL LLM-Based Fact Extractor

Extracts ANY notable facts from messages - NO predefined categories.
Works for conversations about ANYTHING: tech, cooking, fitness, travel, etc.

ARCHITECTURE:
- Each message is analyzed by LLM
- Extracts: any concrete, verifiable facts mentioned
- Returns list of fact strings (no schema imposed)
- Completely universal - adapts to conversation domain
"""

import json
import os
import aiohttp
from typing import List, Dict, Any


from . import llm_backend

OLLAMA_BASE_URL = llm_backend.LLM_BASE_URL
OLLAMA_MODEL = llm_backend.LLM_MODEL

# OpenAI config (set via environment or override in caller)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
USE_OPENAI_EXTRACTION = False  # Default to Ollama for fact extraction
OPENAI_EXTRACTION_MODEL = "gpt-4.1-mini"  # Fast model for extraction (if enabled)


async def extract_speaker_facts_llm(
    session: aiohttp.ClientSession,
    speaker_name: str,
    message_text: str
) -> List[str]:
    """
    UNIVERSALLY extract notable facts about a speaker from their message.
    NO predefined categories - works for ANY conversation type.

    Args:
        session: aiohttp session
        speaker_name: Name of the speaker
        message_text: The message text

    Returns:
        List of fact strings, e.g.:
        ["went camping at the beach", "has two kids", "reads Charlotte's Web",
         "practices pottery", "moved from Sweden 4 years ago"]
    """

    extraction_prompt = f"""Extract any notable FACTS about {speaker_name} from this message.

MESSAGE:
{message_text}

WHAT TO EXTRACT:
- Concrete, verifiable information about {speaker_name}
- Actions they performed/will perform
- Things they own, read, visited, attended
- Preferences, goals, characteristics
- Any details that could help answer future questions about {speaker_name}

WHAT NOT TO EXTRACT:
- Opinions about others
- General statements not about {speaker_name}
- Vague or unverifiable claims

FORMAT:
- Short, precise fact phrases (5-10 words each)
- Use past/present tense naturally
- Preserve specific names, titles, locations
- Each fact should be self-contained

Return ONLY valid JSON:
{{
  "facts": ["fact1", "fact2", "fact3"]
}}

If no notable facts about {speaker_name}, return {{"facts": []}}"""

    try:
        if USE_OPENAI_EXTRACTION and OPENAI_API_KEY:
            # Use OpenAI for extraction
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": OPENAI_EXTRACTION_MODEL,
                    "messages": [{"role": "user", "content": extraction_prompt}],
                    "temperature": 0.1,
                    "max_tokens": 500
                },
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    return []

                data = await resp.json()
                response_text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        else:
            # Use Ollama for extraction
            try:
                response_text = await llm_backend.llm_generate(
                    session, extraction_prompt,
                    model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL,
                    temperature=0.1, max_tokens=500, timeout_seconds=60,
                )
            except Exception:
                return []

        # Parse JSON from response
        # Sometimes LLM adds markdown code blocks, remove them
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]
            response_text = response_text.strip()

        try:
            extracted = json.loads(response_text)

            # Extract facts list
            facts = extracted.get('facts', [])
            if not isinstance(facts, list):
                return []

            # Clean and validate
            cleaned_facts = [
                fact.strip()
                for fact in facts
                if isinstance(fact, str) and fact.strip()
            ]

            return cleaned_facts

        except json.JSONDecodeError:
            return []

    except Exception as e:
        return []


# =============================================================================
# PARALLEL BATCH EXTRACTION (for speed)
# =============================================================================

async def extract_facts_parallel(
    session: aiohttp.ClientSession,
    messages: List[Dict[str, str]],
    batch_size: int = 15
) -> List[List[str]]:
    """
    Extract facts from many messages in parallel batches.

    Args:
        session: aiohttp session
        messages: List of {"speaker": "...", "text": "..."}
        batch_size: How many to process in parallel (default: 15)

    Returns:
        List of fact lists (same order as messages)
    """
    import asyncio

    results = []
    for i in range(0, len(messages), batch_size):
        batch = messages[i:i + batch_size]

        # Extract batch in parallel
        tasks = [
            extract_speaker_facts_llm(session, msg['speaker'], msg['text'])
            for msg in batch
        ]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle exceptions
        for result in batch_results:
            if isinstance(result, Exception):
                results.append([])  # Empty facts on error
            else:
                results.append(result)

    return results


# =============================================================================
# EXAMPLE USAGE
# =============================================================================

if __name__ == "__main__":
    import asyncio

    async def test_extraction():
        async with aiohttp.ClientSession() as session:
            # Test message
            message = "I went camping at the beach last weekend with my kids. We also did some pottery in the pottery class I signed up for. I've been reading 'Charlotte's Web' to them at night."

            print("Testing UNIVERSAL fact extraction...")
            print(f"Message: {message}\n")

            facts = await extract_speaker_facts_llm(session, "Melanie", message)

            print("Extracted facts:")
            for fact in facts:
                print(f"  - {fact}")

    asyncio.run(test_extraction())
