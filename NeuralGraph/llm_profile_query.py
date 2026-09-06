"""
UNIVERSAL LLM-Based Profile Querying

Uses LLM to intelligently query speaker profiles.
NO hardcoded patterns, NO predefined categories - completely universal.

ARCHITECTURE:
- Given: speaker messages + extracted facts + question
- LLM analyzes ALL available information and extracts answer
- Works for ANY conversation type (tech, cooking, fitness, etc.)
- Handles: single answers vs lists, specificity, context
"""

import json
import aiohttp
from typing import Dict, Any


from . import llm_backend

OLLAMA_BASE_URL = llm_backend.LLM_BASE_URL
OLLAMA_MODEL = llm_backend.LLM_MODEL


async def query_profile_with_llm(
    session: aiohttp.ClientSession,
    speaker_name: str,
    profile_dict: Dict[str, Any],
    question: str,
    gold_answer: str = None
) -> Dict[str, Any]:
    """
    Use LLM to query a speaker profile and answer the question.

    Args:
        session: aiohttp session
        speaker_name: Name of the speaker
        profile_dict: The speaker's profile (dict format)
        question: The question to answer
        gold_answer: Optional gold answer for confidence calculation

    Returns:
        {
            'found': bool,
            'answer': str or None,
            'confidence': float (0-1),
            'reasoning': str
        }
    """

    # Build UNIVERSAL profile representation (NO hardcoded fields)
    profile_summary = f"""SPEAKER: {speaker_name}

"""

    # Add extracted facts if available
    extracted_facts = profile_dict.get('extracted_facts', [])
    if extracted_facts:
        profile_summary += "KNOWN FACTS:\n"
        for fact in extracted_facts[:100]:  # Limit for context
            profile_summary += f"  - {fact}\n"
        profile_summary += "\n"

    # Add recent messages for additional context
    all_messages = profile_dict.get('all_messages', [])
    if all_messages:
        profile_summary += "RECENT MESSAGES:\n"
        # Show last 20 messages (or all if less)
        recent_msgs = all_messages[-20:] if len(all_messages) > 20 else all_messages
        for i, msg in enumerate(recent_msgs, 1):
            # Truncate long messages
            msg_preview = msg[:200] + "..." if len(msg) > 200 else msg
            profile_summary += f"{i}. {msg_preview}\n"

    query_prompt = f"""{profile_summary}

QUESTION: {question}

Analyze the profile above and answer the question.

UNIVERSAL PRINCIPLES:
1. Read the question carefully - understand EXACTLY what it's asking for.
2. Filter the profile to match the question's specificity:
   - Specific question (singular)? → Return ONE answer
   - General question (plural/aggregate)? → Return ALL relevant items
   - Narrow category question? → Return ONLY items in that category
3. NEVER dump entire lists - extract ONLY what matches the question.
4. If information is NOT in profile: "NOT_FOUND"
5. Minimal precision: fewer, accurate items > many, irrelevant items

Return ONLY valid JSON:
{{
  "answer": "precise answer matching question scope" OR "NOT_FOUND",
  "reasoning": "how you filtered the profile"
}}"""

    try:
        if True:
            try:
                response_text = await llm_backend.llm_generate(
                    session, query_prompt,
                    model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL,
                    temperature=0.1, max_tokens=300, timeout_seconds=40,
                )
            except Exception:
                return {'found': False, 'answer': None, 'confidence': 0.0, 'reasoning': 'API error'}

            # Parse JSON
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
                response_text = response_text.strip()

            try:
                result = json.loads(response_text)
                answer_raw = result.get('answer', '')

                # Handle both string and list responses
                if isinstance(answer_raw, list):
                    answer = ', '.join(str(item) for item in answer_raw)
                else:
                    answer = str(answer_raw).strip()

                reasoning = result.get('reasoning', '')

                if answer == "NOT_FOUND" or not answer:
                    return {
                        'found': False,
                        'answer': None,
                        'confidence': 0.0,
                        'reasoning': reasoning
                    }

                # Calculate confidence based on gold answer if provided
                confidence = 1.0
                if gold_answer:
                    confidence = _calculate_answer_confidence(answer, gold_answer)

                return {
                    'found': True,
                    'answer': answer,
                    'confidence': confidence,
                    'reasoning': reasoning
                }

            except json.JSONDecodeError:
                return {'found': False, 'answer': None, 'confidence': 0.0, 'reasoning': 'Parse error'}

    except Exception as e:
        return {'found': False, 'answer': None, 'confidence': 0.0, 'reasoning': f'Error: {str(e)}'}


def _calculate_answer_confidence(answer: str, gold: str) -> float:
    """Calculate confidence by checking if gold parts are in answer."""
    answer_lower = answer.lower()
    gold_lower = str(gold).lower()

    # Split gold into parts
    gold_parts = [p.strip() for p in gold_lower.replace(',', '|').replace(' and ', '|').split('|')]
    gold_parts = [p.strip('"') for p in gold_parts if len(p) > 2]

    if not gold_parts:
        return 0.0

    found_count = sum(1 for part in gold_parts if part in answer_lower)
    return found_count / len(gold_parts)


# =============================================================================
# EXAMPLE USAGE
# =============================================================================

if __name__ == "__main__":
    import asyncio

    async def test_llm_query():
        async with aiohttp.ClientSession() as session:
            # Mock UNIVERSAL profile (no hardcoded categories)
            profile = {
                'name': 'Caroline',
                'extracted_facts': [
                    'moved from Sweden 4 years ago',
                    'practices pottery',
                    'does painting',
                    'mentors youth',
                    'attended pride parade',
                    'attended LGBTQ conference',
                    'pursuing counseling for transgender people'
                ],
                'all_messages': [
                    "I moved from Sweden 4 years ago and it was hard at first.",
                    "I love pottery! Been doing it for years.",
                    "Went to a pride parade yesterday - it was amazing!",
                    "I'm pursuing counseling as a career, specifically for transgender people."
                ]
            }

            # Test specific question (should return ONE answer)
            print("Test 1: Specific question")
            result = await query_profile_with_llm(
                session,
                "Caroline",
                profile,
                "Where did Caroline move from 4 years ago?",
                "Sweden"
            )
            print(f"  Answer: {result['answer']}")
            print(f"  Confidence: {result['confidence']}")
            print(f"  Reasoning: {result['reasoning']}\n")

            # Test aggregation question (should return MULTIPLE)
            print("Test 2: Aggregation question")
            result = await query_profile_with_llm(
                session,
                "Caroline",
                profile,
                "What activities does Caroline do?",
                "pottery, painting, mentoring"
            )
            print(f"  Answer: {result['answer']}")
            print(f"  Confidence: {result['confidence']}")
            print(f"  Reasoning: {result['reasoning']}\n")

            # Test NOT FOUND
            print("Test 3: Not in profile")
            result = await query_profile_with_llm(
                session,
                "Caroline",
                profile,
                "What is Caroline's favorite color?"
            )
            print(f"  Found: {result['found']}")
            print(f"  Answer: {result['answer']}")
            print(f"  Reasoning: {result['reasoning']}")

    asyncio.run(test_llm_query())
