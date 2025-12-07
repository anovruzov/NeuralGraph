"""Entity and relation extraction from text using LLM."""

import json
import logging
import re
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any

from .data_types import (
    Entity,
    EntityType,
    ExtractionResult,
    Relation,
    RelationType,
)

logger = logging.getLogger(__name__)


class EntityExtractor(ABC):
    """Abstract base class for entity extraction."""

    @abstractmethod
    async def extract(
        self,
        text: str,
        session_key: str,
        episode_uid: str | None = None,
    ) -> ExtractionResult:
        """Extract entities and relations from text.

        Args:
            text: Input text to analyze.
            session_key: Session key for the entities.
            episode_uid: Source episode UID.

        Returns:
            ExtractionResult with entities and relations.
        """
        ...


class OllamaEntityExtractor(EntityExtractor):
    """LLM-based entity extraction using Ollama.

    Uses qwen2.5:7b-instruct for entity and relation extraction.
    """

    SYSTEM_PROMPT = """You are an expert at extracting entities and relationships from text.

Extract all named entities and their relationships from the given text.

Entity types:
- person: People, characters, users
- organization: Companies, institutions
- location: Places, addresses
- event: Activities, meetings, occurrences
- concept: Abstract ideas, topics
- object: Physical things, products
- time: Dates, periods, deadlines
- preference: Likes, dislikes, preferences
- attribute: Properties, characteristics

Relationship types:
- knows, works_with, related_to, friend_of, family_of
- works_for, member_of, owns, manages
- located_in, lives_in, visited
- participated_in, organized, attended
- is_a, part_of, interested_in
- likes, dislikes, prefers
- happened_at, before, after, during
- mentions, associated_with

Respond ONLY with a JSON object:
{
    "entities": [
        {
            "name": "entity name",
            "type": "entity type",
            "description": "brief description",
            "aliases": ["other names"]
        }
    ],
    "relations": [
        {
            "source": "source entity name",
            "target": "target entity name",
            "type": "relationship type",
            "confidence": 0.9
        }
    ]
}"""

    def __init__(
        self,
        model: str = "qwen2.5:7b-instruct",
        base_url: str = "http://localhost:11434/v1",
        api_key: str = "ollama",
        timeout: float = 60.0,
    ):
        """Initialize Ollama entity extractor.

        Args:
            model: Ollama model to use.
            base_url: Ollama API base URL.
            api_key: API key (default "ollama" for local).
            timeout: Request timeout in seconds.
        """
        self._model = model
        self._base_url = base_url
        self._api_key = api_key
        self._timeout = timeout
        self._client: Any = None

    def _get_client(self) -> Any:
        """Get or create OpenAI client for Ollama."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(
                    base_url=self._base_url,
                    api_key=self._api_key,
                    timeout=self._timeout,
                )
            except ImportError:
                logger.warning("openai package not installed")
                return None
        return self._client

    async def extract(
        self,
        text: str,
        session_key: str,
        episode_uid: str | None = None,
    ) -> ExtractionResult:
        """Extract entities and relations using Ollama LLM."""
        start_time = time.time()

        client = self._get_client()
        if client is None:
            return ExtractionResult(
                text=text,
                entities=[],
                relations=[],
                confidence=0.0,
                processing_time_ms=(time.time() - start_time) * 1000,
                extractor_type="ollama_unavailable",
            )

        try:
            response = await client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": f"Extract entities and relationships from:\n\n{text}"},
                ],
                temperature=0.1,
                max_tokens=1024,
            )

            raw_response = response.choices[0].message.content
            result = self._parse_response(
                text, raw_response, session_key, episode_uid, start_time
            )
            return result

        except Exception as e:
            logger.warning(f"Ollama entity extraction failed: {e}")
            return ExtractionResult(
                text=text,
                entities=[],
                relations=[],
                confidence=0.0,
                processing_time_ms=(time.time() - start_time) * 1000,
                extractor_type="ollama_error",
            )

    def _parse_response(
        self,
        text: str,
        raw_response: str,
        session_key: str,
        episode_uid: str | None,
        start_time: float,
    ) -> ExtractionResult:
        """Parse LLM response into ExtractionResult."""
        processing_time = (time.time() - start_time) * 1000

        try:
            # Extract JSON from response
            json_match = re.search(r'\{[\s\S]*\}', raw_response)
            if json_match:
                data = json.loads(json_match.group())
            else:
                data = json.loads(raw_response)

            # Parse entities
            entities = []
            entity_name_to_uid: dict[str, str] = {}

            for ent_data in data.get("entities", []):
                entity_uid = str(uuid.uuid4())
                entity_name = ent_data.get("name", "")
                entity_name_to_uid[entity_name.lower()] = entity_uid

                # Map entity type
                ent_type_str = ent_data.get("type", "concept").lower()
                try:
                    entity_type = EntityType(ent_type_str)
                except ValueError:
                    entity_type = EntityType.CONCEPT

                entity = Entity(
                    uid=entity_uid,
                    name=entity_name,
                    entity_type=entity_type,
                    session_key=session_key,
                    aliases=ent_data.get("aliases", []),
                    description=ent_data.get("description"),
                    source_episode_uids=[episode_uid] if episode_uid else [],
                    mention_count=1,
                )
                entities.append(entity)

            # Parse relations
            relations = []
            for rel_data in data.get("relations", []):
                source_name = rel_data.get("source", "").lower()
                target_name = rel_data.get("target", "").lower()

                source_uid = entity_name_to_uid.get(source_name)
                target_uid = entity_name_to_uid.get(target_name)

                if not source_uid or not target_uid:
                    continue

                # Map relation type
                rel_type_str = rel_data.get("type", "associated_with").lower()
                try:
                    relation_type = RelationType(rel_type_str)
                except ValueError:
                    relation_type = RelationType.ASSOCIATED_WITH

                relation = Relation(
                    uid=str(uuid.uuid4()),
                    source_entity_uid=source_uid,
                    target_entity_uid=target_uid,
                    relation_type=relation_type,
                    session_key=session_key,
                    confidence=float(rel_data.get("confidence", 0.8)),
                    source_episode_uids=[episode_uid] if episode_uid else [],
                )
                relations.append(relation)

            return ExtractionResult(
                text=text,
                entities=entities,
                relations=relations,
                confidence=0.8 if entities else 0.0,
                processing_time_ms=processing_time,
                extractor_type="ollama",
                raw_response=raw_response,
            )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Failed to parse extraction response: {e}")
            return ExtractionResult(
                text=text,
                entities=[],
                relations=[],
                confidence=0.0,
                processing_time_ms=processing_time,
                extractor_type="ollama_parse_failed",
                raw_response=raw_response,
            )


class RuleBasedEntityExtractor(EntityExtractor):
    """Rule-based entity extraction using patterns.

    Fast fallback when LLM is unavailable.
    """

    # Simple patterns for common entities
    PATTERNS = {
        EntityType.PERSON: [
            r'\b([A-Z][a-z]+ [A-Z][a-z]+)\b',  # Two capitalized words
            r'\b(Mr\.|Mrs\.|Ms\.|Dr\.) ([A-Z][a-z]+)\b',
        ],
        EntityType.ORGANIZATION: [
            r'\b([A-Z][A-Za-z]+ (?:Inc|Corp|LLC|Ltd|Company|Co)\.?)\b',
            r'\b([A-Z][A-Z]+)\b',  # All caps acronyms
        ],
        EntityType.LOCATION: [
            r'\bin ([A-Z][a-z]+(?:, [A-Z][a-z]+)?)\b',
            r'\bat ([A-Z][a-z]+(?: [A-Z][a-z]+)?)\b',
        ],
        EntityType.TIME: [
            r'\b(\d{1,2}[:/]\d{2}(?: ?[AP]M)?)\b',
            r'\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}(?:, \d{4})?)\b',
            r'\b(today|tomorrow|yesterday|next week|last week)\b',
        ],
    }

    async def extract(
        self,
        text: str,
        session_key: str,
        episode_uid: str | None = None,
    ) -> ExtractionResult:
        """Extract entities using pattern matching."""
        start_time = time.time()

        entities = []
        seen_names: set[str] = set()

        for entity_type, patterns in self.PATTERNS.items():
            for pattern in patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                for match in matches:
                    # Handle tuple matches from groups
                    name = match if isinstance(match, str) else " ".join(match)
                    name = name.strip()

                    if name.lower() in seen_names:
                        continue
                    seen_names.add(name.lower())

                    entity = Entity(
                        uid=str(uuid.uuid4()),
                        name=name,
                        entity_type=entity_type,
                        session_key=session_key,
                        source_episode_uids=[episode_uid] if episode_uid else [],
                        mention_count=1,
                    )
                    entities.append(entity)

        processing_time = (time.time() - start_time) * 1000

        return ExtractionResult(
            text=text,
            entities=entities,
            relations=[],  # Rule-based doesn't extract relations
            confidence=0.5 if entities else 0.0,
            processing_time_ms=processing_time,
            extractor_type="rule_based",
        )


class HybridEntityExtractor(EntityExtractor):
    """Hybrid extractor combining LLM and rule-based approaches."""

    def __init__(
        self,
        llm_extractor: OllamaEntityExtractor | None = None,
        rule_extractor: RuleBasedEntityExtractor | None = None,
        use_llm_threshold: int = 100,
    ):
        """Initialize hybrid extractor.

        Args:
            llm_extractor: LLM-based extractor.
            rule_extractor: Rule-based extractor.
            use_llm_threshold: Text length threshold for LLM use.
        """
        self._llm = llm_extractor or OllamaEntityExtractor()
        self._rules = rule_extractor or RuleBasedEntityExtractor()
        self._threshold = use_llm_threshold

    async def extract(
        self,
        text: str,
        session_key: str,
        episode_uid: str | None = None,
    ) -> ExtractionResult:
        """Extract using hybrid approach."""
        # Short texts: use rules
        if len(text) < self._threshold:
            result = await self._rules.extract(text, session_key, episode_uid)
            result.extractor_type = "hybrid_rule_based"
            return result

        # Long texts: use LLM
        result = await self._llm.extract(text, session_key, episode_uid)

        # If LLM fails, fall back to rules
        if not result.entities:
            rule_result = await self._rules.extract(text, session_key, episode_uid)
            if rule_result.entities:
                rule_result.extractor_type = "hybrid_rule_fallback"
                return rule_result

        result.extractor_type = "hybrid_llm"
        return result
