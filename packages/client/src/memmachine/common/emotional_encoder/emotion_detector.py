"""Emotion detection from text using LLM and rule-based approaches."""

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Any

from .data_types import (
    EMOTION_KEYWORDS,
    EmotionalVector,
    EmotionDetectionResult,
    EmotionType,
)

logger = logging.getLogger(__name__)


class EmotionDetector(ABC):
    """Abstract base class for emotion detection."""

    @abstractmethod
    async def detect(self, text: str) -> EmotionDetectionResult:
        """Detect emotions in text.

        Args:
            text: Input text to analyze.

        Returns:
            EmotionDetectionResult with emotional vector and metadata.
        """
        ...

    @abstractmethod
    async def detect_batch(
        self, texts: list[str]
    ) -> list[EmotionDetectionResult]:
        """Detect emotions in multiple texts.

        Args:
            texts: List of texts to analyze.

        Returns:
            List of EmotionDetectionResult objects.
        """
        ...


class RuleBasedEmotionDetector(EmotionDetector):
    """Rule-based emotion detection using keyword matching.

    Fast fallback when LLM is unavailable or for simple cases.
    """

    def __init__(
        self,
        keywords: dict[EmotionType, list[str]] | None = None,
        case_sensitive: bool = False,
    ):
        """Initialize rule-based detector.

        Args:
            keywords: Custom keyword dictionary. Uses defaults if None.
            case_sensitive: Whether to use case-sensitive matching.
        """
        self._keywords = keywords or EMOTION_KEYWORDS
        self._case_sensitive = case_sensitive
        self._patterns: dict[EmotionType, re.Pattern] = {}
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        """Compile regex patterns for each emotion."""
        flags = 0 if self._case_sensitive else re.IGNORECASE
        for emotion, words in self._keywords.items():
            # Create pattern that matches whole words
            pattern = r'\b(' + '|'.join(re.escape(w) for w in words) + r')\b'
            self._patterns[emotion] = re.compile(pattern, flags)

    async def detect(self, text: str) -> EmotionDetectionResult:
        """Detect emotions using keyword matching."""
        start_time = time.time()

        # Count matches for each emotion
        emotion_scores: dict[EmotionType, float] = {e: 0.0 for e in EmotionType}
        total_matches = 0

        for emotion, pattern in self._patterns.items():
            matches = pattern.findall(text)
            count = len(matches)
            emotion_scores[emotion] = float(count)
            total_matches += count

        # Normalize scores
        if total_matches > 0:
            max_score = max(emotion_scores.values())
            if max_score > 0:
                for emotion in emotion_scores:
                    emotion_scores[emotion] /= max_score

        # Create emotional vector
        vector = EmotionalVector(
            joy=emotion_scores[EmotionType.JOY],
            trust=emotion_scores[EmotionType.TRUST],
            fear=emotion_scores[EmotionType.FEAR],
            surprise=emotion_scores[EmotionType.SURPRISE],
            sadness=emotion_scores[EmotionType.SADNESS],
            disgust=emotion_scores[EmotionType.DISGUST],
            anger=emotion_scores[EmotionType.ANGER],
            anticipation=emotion_scores[EmotionType.ANTICIPATION],
        )

        # Confidence based on match count
        confidence = min(1.0, total_matches / 5.0) if total_matches > 0 else 0.0

        processing_time = (time.time() - start_time) * 1000

        return EmotionDetectionResult(
            text=text,
            emotional_vector=vector,
            confidence=confidence,
            dominant_emotion=vector.dominant_emotion(),
            processing_time_ms=processing_time,
            detector_type="rule_based",
        )

    async def detect_batch(
        self, texts: list[str]
    ) -> list[EmotionDetectionResult]:
        """Detect emotions in multiple texts."""
        return [await self.detect(text) for text in texts]


class OllamaEmotionDetector(EmotionDetector):
    """LLM-based emotion detection using Ollama.

    Uses qwen2.5:7b-instruct for nuanced emotional understanding.
    """

    SYSTEM_PROMPT = """You are an emotion analysis expert. Analyze the emotional content of the given text and provide scores for Plutchik's 8 basic emotions.

For each emotion, provide a score from 0.0 to 1.0:
- joy: happiness, pleasure, contentment
- trust: acceptance, admiration, loyalty
- fear: apprehension, anxiety, terror
- surprise: amazement, astonishment, wonder
- sadness: grief, sorrow, melancholy
- disgust: loathing, contempt, aversion
- anger: annoyance, rage, fury
- anticipation: interest, expectation, vigilance

Respond ONLY with a JSON object in this exact format:
{
    "joy": 0.0,
    "trust": 0.0,
    "fear": 0.0,
    "surprise": 0.0,
    "sadness": 0.0,
    "disgust": 0.0,
    "anger": 0.0,
    "anticipation": 0.0,
    "confidence": 0.0,
    "reasoning": "brief explanation"
}"""

    def __init__(
        self,
        model: str = "qwen2.5:7b-instruct",
        base_url: str = "http://localhost:11434/v1",
        api_key: str = "ollama",
        timeout: float = 30.0,
        fallback_detector: EmotionDetector | None = None,
    ):
        """Initialize Ollama emotion detector.

        Args:
            model: Ollama model to use.
            base_url: Ollama API base URL.
            api_key: API key (default "ollama" for local).
            timeout: Request timeout in seconds.
            fallback_detector: Detector to use if Ollama fails.
        """
        self._model = model
        self._base_url = base_url
        self._api_key = api_key
        self._timeout = timeout
        self._fallback = fallback_detector or RuleBasedEmotionDetector()
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
                logger.warning("openai package not installed, using fallback detector")
                return None
        return self._client

    async def detect(self, text: str) -> EmotionDetectionResult:
        """Detect emotions using Ollama LLM."""
        start_time = time.time()

        client = self._get_client()
        if client is None:
            result = await self._fallback.detect(text)
            result.detector_type = "rule_based_fallback"
            return result

        try:
            response = await client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": f"Analyze the emotions in this text:\n\n{text}"},
                ],
                temperature=0.1,
                max_tokens=256,
            )

            raw_response = response.choices[0].message.content
            result = self._parse_response(text, raw_response, start_time)
            return result

        except Exception as e:
            logger.warning(f"Ollama emotion detection failed: {e}, using fallback")
            result = await self._fallback.detect(text)
            result.detector_type = "rule_based_fallback"
            return result

    def _parse_response(
        self,
        text: str,
        raw_response: str,
        start_time: float,
    ) -> EmotionDetectionResult:
        """Parse LLM response into EmotionDetectionResult."""
        processing_time = (time.time() - start_time) * 1000

        try:
            # Extract JSON from response
            json_match = re.search(r'\{[^{}]*\}', raw_response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
            else:
                data = json.loads(raw_response)

            vector = EmotionalVector(
                joy=float(data.get("joy", 0.0)),
                trust=float(data.get("trust", 0.0)),
                fear=float(data.get("fear", 0.0)),
                surprise=float(data.get("surprise", 0.0)),
                sadness=float(data.get("sadness", 0.0)),
                disgust=float(data.get("disgust", 0.0)),
                anger=float(data.get("anger", 0.0)),
                anticipation=float(data.get("anticipation", 0.0)),
            )

            confidence = float(data.get("confidence", 0.8))

            return EmotionDetectionResult(
                text=text,
                emotional_vector=vector,
                confidence=confidence,
                dominant_emotion=vector.dominant_emotion(),
                processing_time_ms=processing_time,
                detector_type="ollama",
                raw_response=raw_response,
            )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Failed to parse emotion response: {e}")
            # Return neutral result on parse failure
            return EmotionDetectionResult(
                text=text,
                emotional_vector=EmotionalVector.neutral(),
                confidence=0.0,
                dominant_emotion=None,
                processing_time_ms=processing_time,
                detector_type="ollama_parse_failed",
                raw_response=raw_response,
            )

    async def detect_batch(
        self, texts: list[str]
    ) -> list[EmotionDetectionResult]:
        """Detect emotions in multiple texts.

        Processes sequentially to avoid overwhelming Ollama.
        """
        results = []
        for text in texts:
            result = await self.detect(text)
            results.append(result)
        return results


class HybridEmotionDetector(EmotionDetector):
    """Hybrid detector combining LLM and rule-based approaches.

    Uses rule-based for simple cases, LLM for complex ones.
    """

    def __init__(
        self,
        llm_detector: OllamaEmotionDetector | None = None,
        rule_detector: RuleBasedEmotionDetector | None = None,
        complexity_threshold: int = 50,
        use_llm_for_low_confidence: bool = True,
        low_confidence_threshold: float = 0.3,
    ):
        """Initialize hybrid detector.

        Args:
            llm_detector: LLM-based detector.
            rule_detector: Rule-based detector.
            complexity_threshold: Text length threshold for LLM use.
            use_llm_for_low_confidence: Whether to escalate to LLM on low confidence.
            low_confidence_threshold: Confidence threshold for escalation.
        """
        self._llm = llm_detector or OllamaEmotionDetector()
        self._rules = rule_detector or RuleBasedEmotionDetector()
        self._complexity_threshold = complexity_threshold
        self._use_llm_for_low_confidence = use_llm_for_low_confidence
        self._low_confidence_threshold = low_confidence_threshold

    async def detect(self, text: str) -> EmotionDetectionResult:
        """Detect emotions using hybrid approach."""
        # Short texts: use rules
        if len(text) < self._complexity_threshold:
            result = await self._rules.detect(text)

            # Escalate to LLM if confidence is low
            if (
                self._use_llm_for_low_confidence
                and result.confidence < self._low_confidence_threshold
            ):
                llm_result = await self._llm.detect(text)
                if llm_result.confidence > result.confidence:
                    llm_result.detector_type = "hybrid_llm_escalated"
                    return llm_result

            result.detector_type = "hybrid_rule_based"
            return result

        # Long/complex texts: use LLM
        result = await self._llm.detect(text)
        result.detector_type = "hybrid_llm"
        return result

    async def detect_batch(
        self, texts: list[str]
    ) -> list[EmotionDetectionResult]:
        """Detect emotions in multiple texts."""
        results = []
        for text in texts:
            result = await self.detect(text)
            results.append(result)
        return results
