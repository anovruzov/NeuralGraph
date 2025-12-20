"""Test if the LLM profile query fix works for instruments"""
import asyncio
import aiohttp
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from NeuralGraph.llm_profile_query import query_profile_with_llm

async def test():
    async with aiohttp.ClientSession() as session:
        # Simulate Melanie's profile with ALL activities
        profile = {
            'name': 'Melanie',
            'activities': [
                'camping', 'pottery', 'painting', 'swimming', 'hiking',
                'playing clarinet', 'playing violin', 'playing guitar',
                'reading', 'running', 'dancing', 'cooking'
            ],
            'books_read': ["Charlotte's Web"],
            'places_visited': ['beach', 'mountains', 'museum']
        }

        # Test the instruments question
        print("="*80)
        print("TEST: What instruments does Melanie play?")
        print("="*80)
        print(f"Profile activities: {profile['activities']}\n")

        result = await query_profile_with_llm(
            session,
            "Melanie",
            profile,
            "What instruments does Melanie play?",
            "clarinet and violin"
        )

        print(f"Answer: {result['answer']}")
        print(f"Confidence: {result['confidence']}")
        print(f"Reasoning: {result['reasoning']}")
        print()

        # Expected: "clarinet, violin" or "playing clarinet, playing violin"
        # NOT: dump of all activities!

        if result['answer'] and len(result['answer']) < 100:
            print("[PASS] Answer is concise and focused!")
        else:
            print(f"[FAIL] Answer too long ({len(result['answer'])} chars) - dumping too much!")

asyncio.run(test())
