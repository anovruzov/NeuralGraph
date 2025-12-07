"""PII detection implementations: Regex, LLM-based (Ollama), and Hybrid."""

import json
import logging
import re
import threading
from abc import ABC, abstractmethod
from typing import Protocol

from .data_types import (
    PIIDetection,
    PIIType,
    SensitivityLevel,
    get_sensitivity_for_pii,
)

logger = logging.getLogger(__name__)


class PIIDetector(ABC):
    """Abstract base class for PII detection."""

    @abstractmethod
    async def detect(self, content: str) -> list[PIIDetection]:
        """Detect PII in the given content.

        Args:
            content: Text content to scan for PII.

        Returns:
            List of PIIDetection objects for each detected PII.
        """
        raise NotImplementedError

    @abstractmethod
    async def detect_batch(self, contents: list[str]) -> list[list[PIIDetection]]:
        """Batch detect PII in multiple contents.

        Args:
            contents: List of text contents to scan.

        Returns:
            List of detection lists, one per input content.
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def detector_name(self) -> str:
        """Return the name of this detector for attribution."""
        raise NotImplementedError


class RegexPIIDetector(PIIDetector):
    """Regex-based PII detector for common patterns.

    High precision for structured PII like emails, phone numbers, SSNs.
    Fast and works offline.
    """

    # Comprehensive regex patterns for common PII types
    # Patterns are designed for high precision (fewer false positives)
    PATTERNS: dict[PIIType, tuple[str, SensitivityLevel]] = {
        # Email addresses - RFC 5322 simplified
        PIIType.EMAIL: (
            r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            SensitivityLevel.MEDIUM,
        ),
        # US Phone numbers - various formats
        PIIType.PHONE: (
            r'\b(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b',
            SensitivityLevel.MEDIUM,
        ),
        # US Social Security Numbers - XXX-XX-XXXX
        PIIType.SSN: (
            r'\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b',
            SensitivityLevel.CRITICAL,
        ),
        # Credit Card Numbers - Visa, MC, Amex, Discover patterns
        PIIType.CREDIT_CARD: (
            r'\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|'
            r'3[47][0-9]{13}|6(?:011|5[0-9]{2})[0-9]{12}|'
            r'(?:\d{4}[-\s]?){3}\d{4})\b',
            SensitivityLevel.CRITICAL,
        ),
        # IPv4 Addresses
        PIIType.IP_ADDRESS: (
            r'\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}'
            r'(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b',
            SensitivityLevel.LOW,
        ),
        # Dates of Birth - various formats (MM/DD/YYYY, YYYY-MM-DD, etc.)
        PIIType.DATE_OF_BIRTH: (
            r'\b(?:(?:0?[1-9]|1[0-2])[/\-](?:0?[1-9]|[12][0-9]|3[01])[/\-]'
            r'(?:19|20)\d{2}|(?:19|20)\d{2}[/\-](?:0?[1-9]|1[0-2])[/\-]'
            r'(?:0?[1-9]|[12][0-9]|3[01]))\b',
            SensitivityLevel.MEDIUM,
        ),
        # US Passport Numbers (9 digits)
        PIIType.PASSPORT: (
            r'\b[A-Z]?\d{8,9}\b',  # Simplified, may have false positives
            SensitivityLevel.CRITICAL,
        ),
        # US Driver's License - varies by state, generic pattern
        PIIType.DRIVER_LICENSE: (
            r'\b[A-Z]{1,2}\d{6,8}\b',  # Generic, many state variations
            SensitivityLevel.HIGH,
        ),
        # China National ID (18 digits, last can be X)
        PIIType.NATIONAL_ID: (
            r'\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b',
            SensitivityLevel.CRITICAL,
        ),
    }

    def __init__(self, confidence: float = 0.9):
        """Initialize regex detector.

        Args:
            confidence: Default confidence score for regex matches.
        """
        self._confidence = confidence
        self._compiled_patterns: dict[PIIType, re.Pattern] = {}
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        """Pre-compile regex patterns for efficiency."""
        for pii_type, (pattern, _) in self.PATTERNS.items():
            try:
                self._compiled_patterns[pii_type] = re.compile(
                    pattern, re.IGNORECASE | re.MULTILINE
                )
            except re.error as e:
                logger.error(f"Failed to compile pattern for {pii_type}: {e}")

    @property
    def detector_name(self) -> str:
        return "regex"

    async def detect(self, content: str) -> list[PIIDetection]:
        """Detect PII using regex patterns.

        Args:
            content: Text to scan.

        Returns:
            List of detections with positions and sensitivity levels.
        """
        if not content:
            return []

        detections: list[PIIDetection] = []

        for pii_type, pattern in self._compiled_patterns.items():
            _, sensitivity = self.PATTERNS[pii_type]

            for match in pattern.finditer(content):
                # Extract context (50 chars before and after)
                start = max(0, match.start() - 50)
                end = min(len(content), match.end() + 50)
                context = content[start:end]

                detection = PIIDetection(
                    pii_type=pii_type,
                    value=match.group(),
                    start_pos=match.start(),
                    end_pos=match.end(),
                    confidence=self._confidence,
                    sensitivity=sensitivity,
                    context=context,
                    detector_source=self.detector_name,
                )
                detections.append(detection)

        # Sort by position for consistent ordering
        detections.sort(key=lambda d: d.start_pos)
        return detections

    async def detect_batch(self, contents: list[str]) -> list[list[PIIDetection]]:
        """Batch detect PII in multiple texts."""
        return [await self.detect(content) for content in contents]


# LLM prompts for PII detection
PII_DETECTION_SYSTEM_PROMPT = """You are a privacy-focused AI that detects personally identifiable information (PII) in text.

Analyze the text and identify ALL instances of:
- Names (first, last, full names)
- Email addresses
- Phone numbers
- Social Security Numbers
- Credit card numbers
- Physical addresses
- Medical/health information
- Financial information (bank accounts, etc.)
- Dates of birth
- Passport numbers
- Driver's license numbers
- National ID numbers
- Location data (GPS, specific places)
- Any other personal identifiers

For each PII found, classify its sensitivity:
- LOW: General identifiers (IP addresses)
- MEDIUM: Contact info (email, phone, name)
- HIGH: Sensitive data (address, medical, financial)
- CRITICAL: Highly sensitive (SSN, credit card, passport, national ID)

Return ONLY valid JSON in this exact format:
{
    "detections": [
        {
            "type": "email|phone|ssn|credit_card|name|address|medical_info|financial_info|date_of_birth|passport|driver_license|national_id|ip_address|location|custom",
            "value": "the exact PII text",
            "sensitivity": "low|medium|high|critical"
        }
    ]
}

If no PII is found, return: {"detections": []}"""

PII_DETECTION_USER_PROMPT = """Analyze this text for PII:

{content}

Return JSON with all PII detections:"""


class OllamaPIIDetector(PIIDetector):
    """LLM-based PII detector using Ollama for nuanced detection.

    Better at detecting names, addresses, and context-dependent PII
    that regex cannot easily capture.
    """

    def __init__(
        self,
        model: str = "qwen2.5:7b-instruct",
        base_url: str = "http://localhost:11434/v1",
        api_key: str = "ollama",
        raise_on_error: bool = False,
        max_content_length: int = 2000,
    ):
        """Initialize Ollama PII detector.

        Args:
            model: Ollama model to use.
            base_url: Ollama API base URL.
            api_key: API key (usually "ollama" for local).
            raise_on_error: Whether to raise exceptions on failure.
            max_content_length: Maximum content length to process.
        """
        self._model = model
        self._base_url = base_url
        self._api_key = api_key
        self._client = None
        self._client_lock = threading.Lock()
        self._raise_on_error = raise_on_error
        self._max_content_length = max_content_length
        self._failure_count = 0
        self._success_count = 0

    def _get_client(self):
        """Lazy initialization of OpenAI client with thread safety."""
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    from openai import OpenAI
                    self._client = OpenAI(
                        base_url=self._base_url,
                        api_key=self._api_key,
                    )
        return self._client

    @property
    def detector_name(self) -> str:
        return "ollama"

    @property
    def failure_rate(self) -> float:
        """Return failure rate as percentage."""
        total = self._failure_count + self._success_count
        if total == 0:
            return 0.0
        return (self._failure_count / total) * 100

    async def detect(self, content: str) -> list[PIIDetection]:
        """Detect PII using LLM analysis.

        Args:
            content: Text to scan.

        Returns:
            List of detections from LLM analysis.
        """
        if not content or not content.strip():
            return []

        # Truncate content if too long
        truncated_content = content[:self._max_content_length]
        prompt = PII_DETECTION_USER_PROMPT.format(content=truncated_content)

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": PII_DETECTION_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=1000,
            )

            result = response.choices[0].message.content.strip()
            detections = self._parse_llm_response(result, content)
            self._success_count += 1
            return detections

        except Exception as e:
            self._failure_count += 1
            logger.error(
                f"Ollama PII detection failed (failure #{self._failure_count}): {e}",
                exc_info=True,
            )
            if self._raise_on_error:
                raise
            return []

    def _parse_llm_response(
        self, result: str, original_content: str
    ) -> list[PIIDetection]:
        """Parse LLM JSON response into PIIDetection objects."""
        detections = []

        try:
            # Clean up response - find JSON in the response
            json_match = re.search(r'\{[\s\S]*\}', result)
            if not json_match:
                logger.warning(f"No JSON found in LLM response: {result[:200]}")
                return []

            data = json.loads(json_match.group())

            if "detections" not in data:
                return []

            for item in data["detections"]:
                pii_type = PIIType.from_string(item.get("type", "custom"))
                value = item.get("value", "")
                sensitivity_str = item.get("sensitivity", "medium")
                sensitivity = SensitivityLevel.from_string(sensitivity_str)

                # Find position of value in original content
                start_pos = original_content.find(value)
                if start_pos == -1:
                    # Value might be slightly different, try lowercase
                    start_pos = original_content.lower().find(value.lower())

                if start_pos == -1:
                    start_pos = 0  # Fallback
                    end_pos = len(value)
                else:
                    end_pos = start_pos + len(value)

                # Extract context
                context_start = max(0, start_pos - 50)
                context_end = min(len(original_content), end_pos + 50)
                context = original_content[context_start:context_end]

                detection = PIIDetection(
                    pii_type=pii_type,
                    value=value,
                    start_pos=start_pos,
                    end_pos=end_pos,
                    confidence=0.8,  # LLM detections get slightly lower confidence
                    sensitivity=sensitivity,
                    context=context,
                    detector_source=self.detector_name,
                )
                detections.append(detection)

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse LLM response as JSON: {e}")
        except Exception as e:
            logger.warning(f"Error parsing LLM PII response: {e}")

        return detections

    async def detect_batch(self, contents: list[str]) -> list[list[PIIDetection]]:
        """Batch detect - currently sequential, could be parallelized."""
        return [await self.detect(content) for content in contents]


class HybridPIIDetector(PIIDetector):
    """Hybrid detector combining regex and LLM for best results.

    Uses regex for structured PII (emails, SSNs, etc.) and
    LLM for unstructured PII (names, addresses, medical info).
    """

    def __init__(
        self,
        regex_detector: RegexPIIDetector | None = None,
        llm_detector: OllamaPIIDetector | None = None,
        use_llm: bool = True,
        merge_strategy: str = "union",
    ):
        """Initialize hybrid detector.

        Args:
            regex_detector: RegexPIIDetector instance (created if None).
            llm_detector: OllamaPIIDetector instance (created if None).
            use_llm: Whether to use LLM detection.
            merge_strategy: How to merge results - "union" or "intersection".
        """
        self._regex = regex_detector or RegexPIIDetector()
        self._llm = llm_detector or OllamaPIIDetector() if use_llm else None
        self._use_llm = use_llm
        self._merge_strategy = merge_strategy

    @property
    def detector_name(self) -> str:
        return "hybrid"

    async def detect(self, content: str) -> list[PIIDetection]:
        """Detect PII using both regex and LLM."""
        if not content:
            return []

        # Always run regex detection
        regex_detections = await self._regex.detect(content)

        # Optionally run LLM detection
        llm_detections = []
        if self._use_llm and self._llm:
            llm_detections = await self._llm.detect(content)

        # Merge results
        return self._merge_detections(regex_detections, llm_detections)

    def _merge_detections(
        self,
        regex_detections: list[PIIDetection],
        llm_detections: list[PIIDetection],
    ) -> list[PIIDetection]:
        """Merge detections from both sources, removing duplicates."""
        if not llm_detections:
            return regex_detections

        if not regex_detections:
            return llm_detections

        # Create a map of positions to detections
        merged: dict[tuple[int, int], PIIDetection] = {}

        # Add regex detections (higher priority)
        for det in regex_detections:
            key = (det.start_pos, det.end_pos)
            merged[key] = det

        # Add LLM detections, avoiding overlaps
        for det in llm_detections:
            key = (det.start_pos, det.end_pos)
            if key not in merged:
                # Check for overlapping detections
                is_overlap = False
                for (s, e), existing in merged.items():
                    if self._ranges_overlap(det.start_pos, det.end_pos, s, e):
                        # Keep the one with higher sensitivity
                        if (
                            list(SensitivityLevel).index(det.sensitivity) >
                            list(SensitivityLevel).index(existing.sensitivity)
                        ):
                            merged[key] = det
                            del merged[(s, e)]
                        is_overlap = True
                        break

                if not is_overlap:
                    merged[key] = det

        # Sort by position
        result = sorted(merged.values(), key=lambda d: d.start_pos)
        return result

    def _ranges_overlap(
        self, s1: int, e1: int, s2: int, e2: int
    ) -> bool:
        """Check if two ranges overlap."""
        return s1 < e2 and s2 < e1

    async def detect_batch(self, contents: list[str]) -> list[list[PIIDetection]]:
        """Batch detect using hybrid approach."""
        return [await self.detect(content) for content in contents]
