"""Entropy calculation for query complexity estimation."""

import math
import re
from collections import Counter
from typing import Callable

from .data_types import (
    ENTROPY_THRESHOLDS,
    EntropyScore,
    GranularityLevel,
)


class EntropyCalculator:
    """Calculate Shannon entropy for query complexity estimation.

    Uses token frequency distribution to measure query information content.
    Higher entropy indicates more complex, diverse queries requiring
    coarser granularity retrieval.
    """

    def __init__(
        self,
        tokenizer: Callable[[str], list[str]] | None = None,
        stopwords: set[str] | None = None,
        min_token_length: int = 2,
    ):
        """Initialize entropy calculator.

        Args:
            tokenizer: Custom tokenizer function. Uses simple word split if None.
            stopwords: Set of stopwords to exclude. Uses English defaults if None.
            min_token_length: Minimum token length to include.
        """
        self._tokenizer = tokenizer or self._default_tokenizer
        self._stopwords = stopwords or self._default_stopwords()
        self._min_token_length = min_token_length

    @staticmethod
    def _default_tokenizer(text: str) -> list[str]:
        """Default word tokenizer."""
        # Simple word tokenization
        words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
        return words

    @staticmethod
    def _default_stopwords() -> set[str]:
        """Default English stopwords."""
        return {
            "a", "an", "the", "and", "or", "but", "in", "on", "at", "to",
            "for", "of", "with", "by", "from", "as", "is", "was", "are",
            "were", "been", "be", "have", "has", "had", "do", "does", "did",
            "will", "would", "could", "should", "may", "might", "must",
            "shall", "can", "need", "dare", "ought", "used", "to",
            "i", "me", "my", "myself", "we", "our", "ours", "ourselves",
            "you", "your", "yours", "yourself", "yourselves", "he", "him",
            "his", "himself", "she", "her", "hers", "herself", "it", "its",
            "itself", "they", "them", "their", "theirs", "themselves",
            "what", "which", "who", "whom", "this", "that", "these", "those",
            "am", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "having", "do", "does", "did", "doing",
            "would", "should", "could", "ought", "im", "youre", "hes",
            "shes", "its", "were", "theyre", "ive", "youve", "weve",
            "theyve", "id", "youd", "hed", "shed", "wed", "theyd",
            "ill", "youll", "hell", "shell", "well", "theyll",
            "isnt", "arent", "wasnt", "werent", "hasnt", "havent",
            "hadnt", "doesnt", "dont", "didnt", "wont", "wouldnt",
            "shant", "shouldnt", "cant", "cannot", "couldnt", "mustnt",
            "lets", "thats", "whos", "whats", "heres", "theres", "whens",
            "wheres", "whys", "hows", "because", "until", "while",
        }

    def tokenize(self, text: str) -> list[str]:
        """Tokenize text and filter.

        Args:
            text: Input text.

        Returns:
            List of filtered tokens.
        """
        tokens = self._tokenizer(text)

        # Filter by length and stopwords
        filtered = [
            t for t in tokens
            if len(t) >= self._min_token_length and t not in self._stopwords
        ]

        return filtered

    def calculate_entropy(self, tokens: list[str]) -> float:
        """Calculate Shannon entropy from token distribution.

        H(X) = -Σ p(x) * log2(p(x))

        Args:
            tokens: List of tokens.

        Returns:
            Shannon entropy value.
        """
        if not tokens:
            return 0.0

        # Count token frequencies
        counter = Counter(tokens)
        total = len(tokens)

        # Calculate entropy
        entropy = 0.0
        for count in counter.values():
            if count > 0:
                p = count / total
                entropy -= p * math.log2(p)

        return entropy

    def determine_granularity(self, entropy: float) -> GranularityLevel:
        """Determine recommended granularity from entropy.

        Args:
            entropy: Calculated entropy value.

        Returns:
            Recommended GranularityLevel.
        """
        if entropy < ENTROPY_THRESHOLDS[GranularityLevel.KEYWORD]:
            return GranularityLevel.KEYWORD
        elif entropy < ENTROPY_THRESHOLDS[GranularityLevel.SENTENCE]:
            return GranularityLevel.SENTENCE
        elif entropy < ENTROPY_THRESHOLDS[GranularityLevel.PARAGRAPH]:
            return GranularityLevel.PARAGRAPH
        else:
            return GranularityLevel.DOCUMENT

    def analyze(self, query: str) -> EntropyScore:
        """Analyze a query and compute entropy score.

        Args:
            query: Input query string.

        Returns:
            EntropyScore with entropy and recommended granularity.
        """
        tokens = self.tokenize(query)
        entropy = self.calculate_entropy(tokens)
        granularity = self.determine_granularity(entropy)

        return EntropyScore(
            query=query,
            entropy=entropy,
            token_count=len(tokens),
            unique_tokens=len(set(tokens)),
            recommended_granularity=granularity,
        )

    def batch_analyze(self, queries: list[str]) -> list[EntropyScore]:
        """Analyze multiple queries.

        Args:
            queries: List of query strings.

        Returns:
            List of EntropyScore objects.
        """
        return [self.analyze(q) for q in queries]


class AdaptiveEntropyCalculator(EntropyCalculator):
    """Adaptive entropy calculator that learns from query patterns.

    Adjusts thresholds based on historical query performance.
    """

    def __init__(
        self,
        tokenizer: Callable[[str], list[str]] | None = None,
        stopwords: set[str] | None = None,
        min_token_length: int = 2,
        learning_rate: float = 0.1,
    ):
        """Initialize adaptive entropy calculator.

        Args:
            tokenizer: Custom tokenizer function.
            stopwords: Set of stopwords to exclude.
            min_token_length: Minimum token length.
            learning_rate: Rate for threshold adjustment.
        """
        super().__init__(tokenizer, stopwords, min_token_length)
        self._learning_rate = learning_rate

        # Adaptive thresholds (start with defaults)
        self._thresholds = dict(ENTROPY_THRESHOLDS)

        # Performance tracking
        self._performance: dict[GranularityLevel, list[float]] = {
            g: [] for g in GranularityLevel
        }

    def record_performance(
        self,
        granularity: GranularityLevel,
        relevance_score: float,
    ) -> None:
        """Record retrieval performance for a granularity level.

        Args:
            granularity: Granularity level used.
            relevance_score: Retrieval relevance score (0-1).
        """
        self._performance[granularity].append(relevance_score)

        # Keep last 100 scores
        if len(self._performance[granularity]) > 100:
            self._performance[granularity] = (
                self._performance[granularity][-100:]
            )

    def adapt_thresholds(self) -> None:
        """Adapt thresholds based on recorded performance."""
        # This is a simplified adaptation strategy
        # In practice, you'd use more sophisticated optimization

        for granularity in GranularityLevel:
            if granularity == GranularityLevel.DOCUMENT:
                continue  # Don't adapt document threshold

            scores = self._performance[granularity]
            if len(scores) < 10:
                continue

            avg_score = sum(scores) / len(scores)

            # If performance is poor, widen the threshold
            # (make this granularity less likely)
            if avg_score < 0.5:
                self._thresholds[granularity] *= (1 - self._learning_rate)
            # If performance is good, narrow the threshold
            # (make this granularity more likely)
            elif avg_score > 0.8:
                self._thresholds[granularity] *= (1 + self._learning_rate)

    def determine_granularity(self, entropy: float) -> GranularityLevel:
        """Determine granularity using adaptive thresholds."""
        if entropy < self._thresholds[GranularityLevel.KEYWORD]:
            return GranularityLevel.KEYWORD
        elif entropy < self._thresholds[GranularityLevel.SENTENCE]:
            return GranularityLevel.SENTENCE
        elif entropy < self._thresholds[GranularityLevel.PARAGRAPH]:
            return GranularityLevel.PARAGRAPH
        else:
            return GranularityLevel.DOCUMENT

    @property
    def current_thresholds(self) -> dict[GranularityLevel, float]:
        """Get current adaptive thresholds."""
        return dict(self._thresholds)
