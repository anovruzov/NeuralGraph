"""
UNIVERSAL SPEAKER PROFILE SYSTEM

Builds profiles for EVERY speaker - works for ANY conversation type.
NO hardcoded categories, NO assumptions about conversation domain.

Works for:
- Multi-person conversations (Caroline & Melanie in benchmark)
- User-AI conversations (production)
- Group chats with N speakers
- ANY conversation domain: tech, cooking, fitness, travel, etc.

ARCHITECTURE:
- Each speaker gets a profile built during ingestion
- Profiles store: raw messages + LLM-extracted facts (flexible format)
- Single_hop questions query profiles using LLM (no hardcoded patterns)
- Falls back to retrieval if profile doesn't have the answer

UNIVERSAL PROFILE SCHEMA (per speaker):
{
    "speaker_name": "Alice",
    "profile": {
        "all_messages": ["raw message text..."],
        "extracted_facts": [
            "moved from Sweden 4 years ago",
            "practices pottery",
            "uses React for frontend development",
            "runs 5k every morning",
            "favorite movie is Inception"
        ]
    }
}

NO predefined categories - adapts to ANY conversation content.
"""

from collections import defaultdict
from typing import Dict, List, Any
import re


class SpeakerProfile:
    """UNIVERSAL profile for a single speaker - works for ANY conversation type."""

    def __init__(self, name: str):
        self.name = name
        self.all_messages = []  # Raw message text (always preserved)
        self.extracted_facts = []  # Flexible fact storage (NO predefined schema)

    async def add_message(self, text: str, llm_extractor=None):
        """
        Add a message and optionally extract facts from it.

        Args:
            text: The message text
            llm_extractor: Optional async LLM function for extraction
                          Should be: async fn(speaker_name, text) -> List[str]
                          Returns list of fact strings
        """
        self.all_messages.append(text)

        if llm_extractor:
            # Use LLM to extract ANY notable facts (no predefined categories)
            facts = await llm_extractor(self.name, text)
            if facts:
                self.extracted_facts.extend(facts)

    async def query(self, session, question: str, gold_answer: str = None) -> Dict[str, Any]:
        """
        Query this profile using LLM (NO hardcoded patterns).

        Args:
            session: aiohttp session for LLM calls
            question: The question being asked
            gold_answer: Optional gold answer for confidence calculation

        Returns:
            {
                'found': bool,
                'answer': str or None,
                'confidence': float (0-1),
                'source': str
            }
        """
        # Use LLM to query the profile intelligently
        from NeuralGraph.llm_profile_query import query_profile_with_llm

        profile_dict = self.to_dict()
        result = await query_profile_with_llm(
            session,
            self.name,
            profile_dict,
            question,
            gold_answer
        )

        result['source'] = 'llm_profile_query'
        return result

    def to_dict(self) -> Dict[str, Any]:
        """Export profile to dictionary (universal format)."""
        return {
            'name': self.name,
            'all_messages': self.all_messages,
            'extracted_facts': self.extracted_facts,
            'message_count': len(self.all_messages),
            'fact_count': len(self.extracted_facts)
        }


class UniversalSpeakerProfiler:
    """
    Universal profiler that works for any conversation format.
    Builds profiles for ALL speakers, regardless of whether it's:
    - Caroline & Melanie (benchmark)
    - User & AI (production)
    - Group chat with N people
    """

    def __init__(self):
        self.profiles: Dict[str, SpeakerProfile] = {}

    async def add_message(self, speaker: str, text: str, llm_extractor=None):
        """
        Add a message to the appropriate speaker's profile.

        Args:
            speaker: Name of the speaker
            text: Message text
            llm_extractor: Optional async LLM for smart extraction
        """
        if speaker not in self.profiles:
            self.profiles[speaker] = SpeakerProfile(speaker)

        await self.profiles[speaker].add_message(text, llm_extractor)

    async def query_single_hop(self, session, question: str, gold_answer: str = None) -> Dict[str, Any]:
        """
        Answer a single_hop question using speaker profiles (LLM-based).

        Args:
            session: aiohttp session for LLM calls
            question: The question
            gold_answer: Optional gold answer for evaluation

        Returns:
            {
                'speaker': str or None,
                'found': bool,
                'answer': str or None,
                'confidence': float,
                'source': str
            }
        """
        # Determine which speaker the question is about
        question_lower = question.lower()

        target_speaker = None
        for speaker_name in self.profiles.keys():
            if speaker_name.lower() in question_lower:
                target_speaker = speaker_name
                break

        if not target_speaker:
            return {
                'speaker': None,
                'found': False,
                'answer': None,
                'confidence': 0.0,
                'source': None,
                'error': 'Could not determine target speaker'
            }

        # Query that speaker's profile using LLM
        result = await self.profiles[target_speaker].query(session, question, gold_answer)
        result['speaker'] = target_speaker
        return result

    def get_all_profiles(self) -> Dict[str, Dict]:
        """Get all speaker profiles as dictionaries."""
        return {name: profile.to_dict() for name, profile in self.profiles.items()}


# =============================================================================
# EXAMPLE USAGE
# =============================================================================

if __name__ == "__main__":
    # Example: Build profiles from a conversation
    profiler = UniversalSpeakerProfiler()

    # Add messages (works for any speaker names)
    profiler.add_message("Caroline", "I went to a pride parade yesterday!")
    profiler.add_message("Melanie", "I went camping at the beach with my kids")
    profiler.add_message("Caroline", "I'm pursuing counseling as a career")
    profiler.add_message("Melanie", 'I read "Charlotte\'s Web" last night')

    # Query single_hop questions
    result = profiler.query_single_hop(
        "What events has Caroline attended?",
        gold_answer="pride parade"
    )
    print(f"Question: What events has Caroline attended?")
    print(f"Found: {result['found']}")
    print(f"Answer: {result['answer']}")
    print(f"Confidence: {result['confidence']}")
    print(f"Source: {result['source']}")

    # Export all profiles
    all_profiles = profiler.get_all_profiles()
    print(f"\nAll profiles: {all_profiles}")
