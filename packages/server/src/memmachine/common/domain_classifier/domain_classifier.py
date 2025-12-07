"""Domain classifier using LLM for semantic classification."""

import json
import logging
import threading
from typing import Protocol

from .data_types import Domain, DomainClassification

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    """Protocol for LLM client compatibility."""

    def chat_completions_create(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Create a chat completion and return the response text."""
        ...


CLASSIFICATION_PROMPT = """Classify the following text into one or more domains. Return ONLY the domain names, comma-separated, nothing else.

Domains: events, personal, relationships, work, hobbies, health, finance, location, general

Examples:
"I went to the LGBTQ support group yesterday" → events, personal
"My friend Sarah loves hiking" → relationships, hobbies
"I lost my job at the bank" → work
"I donated my car to the shelter" → finance, general
"I'm feeling anxious about my health checkup" → health, personal
"We had dinner at that Italian restaurant downtown" → events, location
"My sister is getting married next month" → relationships, events
"I started learning guitar last week" → hobbies, events

Text: {content}
Domains:"""


QUERY_CLASSIFICATION_PROMPT = """Classify the following question to determine which memory domains to search. Return ONLY the domain names, comma-separated, nothing else.

Domains: events, personal, relationships, work, hobbies, health, finance, location, general

Examples:
"When did Caroline go to the LGBTQ support group?" → events, personal
"What is Caroline's identity?" → personal
"Who did Maria have dinner with?" → relationships, events
"When did John lose his job?" → work, events
"What martial arts has John done?" → hobbies
"When did Maria donate her car?" → finance, events
"Where has Maria made friends?" → relationships, location
"What is Caroline's relationship status?" → personal, relationships

Question: {query}
Domains:"""


# Batch classification prompt for processing multiple items at once
BATCH_CLASSIFICATION_PROMPT = """Classify each text into domains. Return ONLY numbered results, one per line.

Domains: events, personal, relationships, work, hobbies, health, finance, location, general

{numbered_contents}

Output format (one per line, no explanations):
1: domain1, domain2
2: domain1
..."""


class DomainClassifier:
    """Classifies content and queries into semantic domains for memory sharding."""

    def __init__(
        self,
        model: str = "qwen2.5:7b-instruct",
        base_url: str = "http://localhost:11434/v1",
        api_key: str = "ollama",
        raise_on_error: bool = False,
    ) -> None:
        """Initialize the domain classifier with LLM configuration.

        Args:
            model: The LLM model to use for classification.
            base_url: Base URL for the LLM API.
            api_key: API key for authentication.
            raise_on_error: If True, raise exceptions instead of falling back to GENERAL.
        """
        self._model = model
        self._base_url = base_url
        self._api_key = api_key
        self._client = None
        self._client_lock = threading.Lock()
        self._raise_on_error = raise_on_error
        self._failure_count = 0
        self._success_count = 0

    def _get_client(self):
        """Lazy initialization of OpenAI client with thread safety."""
        if self._client is None:
            with self._client_lock:
                # Double-check pattern for thread safety
                if self._client is None:
                    from openai import OpenAI

                    self._client = OpenAI(
                        base_url=self._base_url,
                        api_key=self._api_key,
                    )
        return self._client

    @property
    def failure_count(self) -> int:
        """Return the number of classification failures."""
        return self._failure_count

    @property
    def success_count(self) -> int:
        """Return the number of successful classifications."""
        return self._success_count

    @property
    def failure_rate(self) -> float:
        """Return the failure rate as a percentage."""
        total = self._failure_count + self._success_count
        if total == 0:
            return 0.0
        return (self._failure_count / total) * 100

    def classify_content(self, content: str) -> DomainClassification:
        """
        Classify content into one or more domains.

        Args:
            content: The text content to classify.

        Returns:
            DomainClassification with detected domains.
            On failure, returns GENERAL with confidence=0.0.
        """
        if not content or not content.strip():
            return DomainClassification(domains=[Domain.GENERAL], confidence=1.0)

        prompt = CLASSIFICATION_PROMPT.format(content=content[:500])

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=50,
            )
            result = response.choices[0].message.content.strip()
            domains = self._parse_domains(result)
            self._success_count += 1
            return DomainClassification(domains=domains, confidence=1.0)
        except Exception as e:
            self._failure_count += 1
            logger.error(
                f"Domain classification failed (failure #{self._failure_count}): {e}",
                exc_info=True
            )
            if self._raise_on_error:
                raise
            # Return with confidence=0.0 to indicate this is a fallback
            return DomainClassification(domains=[Domain.GENERAL], confidence=0.0)

    def classify_query(self, query: str) -> DomainClassification:
        """
        Classify a query to determine which domains to search.

        Args:
            query: The search query to classify.

        Returns:
            DomainClassification with domains to search.
            On failure, returns GENERAL with confidence=0.0.
        """
        if not query or not query.strip():
            return DomainClassification(domains=[Domain.GENERAL], confidence=1.0)

        prompt = QUERY_CLASSIFICATION_PROMPT.format(query=query)

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=50,
            )
            result = response.choices[0].message.content.strip()
            domains = self._parse_domains(result)
            self._success_count += 1
            return DomainClassification(domains=domains, confidence=1.0)
        except Exception as e:
            self._failure_count += 1
            logger.error(
                f"Query domain classification failed (failure #{self._failure_count}): {e}",
                exc_info=True
            )
            if self._raise_on_error:
                raise
            return DomainClassification(domains=[Domain.GENERAL], confidence=0.0)

    def classify_content_batch(
        self, contents: list[str], batch_size: int = 20
    ) -> list[DomainClassification]:
        """
        Classify multiple pieces of content efficiently using batched LLM calls.

        Args:
            contents: List of text content to classify.
            batch_size: Maximum items per LLM call (default 20).

        Returns:
            List of DomainClassification for each content.
        """
        if not contents:
            return []

        results: list[DomainClassification] = []

        for i in range(0, len(contents), batch_size):
            batch = contents[i:i + batch_size]
            batch_results = self._classify_batch(batch)
            results.extend(batch_results)

        return results

    def _classify_batch(self, contents: list[str]) -> list[DomainClassification]:
        """Classify a batch of contents with a single LLM call."""
        if not contents:
            return []

        # Create numbered content list (truncate each to 200 chars for context limits)
        numbered = "\n".join(
            f"{j + 1}. {c[:200]}" for j, c in enumerate(contents)
        )
        prompt = BATCH_CLASSIFICATION_PROMPT.format(numbered_contents=numbered)

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=len(contents) * 30,  # ~30 tokens per line
            )
            result = response.choices[0].message.content.strip()
            batch_results = self._parse_batch_response(result, len(contents))
            self._success_count += len(contents)
            return batch_results
        except Exception as e:
            self._failure_count += len(contents)
            logger.error(
                f"Batch domain classification failed for {len(contents)} items: {e}",
                exc_info=True
            )
            if self._raise_on_error:
                raise
            # Return fallbacks for all items
            return [
                DomainClassification(domains=[Domain.GENERAL], confidence=0.0)
                for _ in contents
            ]

    def _parse_batch_response(
        self, result: str, expected_count: int
    ) -> list[DomainClassification]:
        """Parse numbered batch response from LLM."""
        classifications = []
        lines = result.strip().split("\n")

        # Create a mapping from line number to domains
        line_results: dict[int, list[Domain]] = {}

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Try to parse "1: domain1, domain2" format
            if ":" in line:
                parts = line.split(":", 1)
                try:
                    line_num = int(parts[0].strip().rstrip("."))
                    domains_str = parts[1].strip()
                    domains = self._parse_domains(domains_str)
                    line_results[line_num] = domains
                except (ValueError, IndexError):
                    continue

        # Build results in order, using fallback for missing lines
        for i in range(1, expected_count + 1):
            if i in line_results:
                classifications.append(
                    DomainClassification(domains=line_results[i], confidence=1.0)
                )
            else:
                classifications.append(
                    DomainClassification(domains=[Domain.GENERAL], confidence=0.5)
                )

        return classifications

    def _parse_domains(self, result: str) -> list[Domain]:
        """Parse domain strings from LLM response with multiple fallback strategies."""
        result = result.strip()

        # Strategy 1: Try JSON parsing if it looks like JSON
        if result.startswith("{") or result.startswith("["):
            try:
                data = json.loads(result)
                if isinstance(data, list):
                    domains = [Domain.from_string(str(d)) for d in data if d]
                    if domains:
                        return domains
                if isinstance(data, dict) and "domains" in data:
                    domains = [Domain.from_string(str(d)) for d in data["domains"] if d]
                    if domains:
                        return domains
            except json.JSONDecodeError:
                pass

        # Strategy 2: Arrow notation (from examples in prompt)
        result_lower = result.lower()
        if "→" in result_lower:
            result_lower = result_lower.split("→")[-1].strip()

        # Strategy 3: Colon notation (e.g., "Domains: events, personal")
        if ":" in result_lower and not result_lower[0].isdigit():
            result_lower = result_lower.split(":")[-1].strip()

        # Strategy 4: Extract known domain words from anywhere in text
        all_domain_values = set(Domain.all_values())
        found_domains: list[Domain] = []

        # Split on common separators
        for word in result_lower.replace(",", " ").replace(";", " ").split():
            word = word.strip().strip('"\'').strip(".")
            if word in all_domain_values:
                domain = Domain.from_string(word)
                if domain not in found_domains:
                    found_domains.append(domain)

        # Ensure at least one domain
        if not found_domains:
            found_domains = [Domain.GENERAL]

        return found_domains
