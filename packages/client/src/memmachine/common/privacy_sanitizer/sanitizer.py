"""Privacy sanitizer orchestrator for content sanitization."""

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from .data_types import (
    ComplianceFramework,
    PIIDetection,
    PIIType,
    SanitizationResult,
    SegmentFold,
    SensitivityLevel,
)
from .pii_detector import PIIDetector, HybridPIIDetector

logger = logging.getLogger(__name__)


class SanitizationConfig(BaseModel):
    """Configuration for sanitization behavior."""

    # Blocking settings
    block_critical: bool = Field(
        default=True,
        description="Block content with critical sensitivity PII",
    )

    # Handling by sensitivity level
    redact_high: bool = Field(
        default=True,
        description="Redact high sensitivity PII",
    )
    pseudonymize_medium: bool = Field(
        default=True,
        description="Replace medium sensitivity PII with pseudonyms",
    )
    allow_low: bool = Field(
        default=True,
        description="Allow low sensitivity PII through unchanged",
    )

    # Placeholders
    redaction_placeholder: str = Field(
        default="[REDACTED]",
        description="Text to replace redacted content",
    )
    pseudonym_prefix: str = Field(
        default="ANON_",
        description="Prefix for pseudonymized values",
    )

    # Encryption settings
    encrypt_high_sensitivity: bool = Field(
        default=False,
        description="Encrypt high sensitivity content instead of redacting",
    )
    encryption_key_id: str | None = Field(
        default=None,
        description="Encryption key ID for encrypted folds",
    )

    # Compliance
    compliance_frameworks: list[str] = Field(
        default=["gdpr", "pipl", "ccpa"],
        description="Compliance frameworks to consider",
    )

    # Fold settings
    create_segment_folds: bool = Field(
        default=True,
        description="Create SegmentFold objects for audit trail",
    )
    fold_expiration_days: int | None = Field(
        default=None,
        description="Days until folds expire (None = never)",
    )

    # Custom rules
    custom_pii_sensitivity: dict[str, str] = Field(
        default_factory=dict,
        description="Override sensitivity for specific PII types",
    )
    custom_redaction_templates: dict[str, str] = Field(
        default_factory=dict,
        description="Custom redaction text per PII type",
    )

    def get_sensitivity_override(self, pii_type: PIIType) -> SensitivityLevel | None:
        """Get custom sensitivity for a PII type if defined."""
        if pii_type.value in self.custom_pii_sensitivity:
            return SensitivityLevel.from_string(
                self.custom_pii_sensitivity[pii_type.value]
            )
        return None

    def get_redaction_text(self, pii_type: PIIType) -> str:
        """Get redaction text for a PII type."""
        if pii_type.value in self.custom_redaction_templates:
            return self.custom_redaction_templates[pii_type.value]
        return self.redaction_placeholder


class PrivacySanitizer:
    """Orchestrates PII detection and content sanitization.

    This class coordinates the detection of PII in content and applies
    appropriate sanitization based on sensitivity levels and configuration.
    """

    def __init__(
        self,
        detector: PIIDetector | None = None,
        config: SanitizationConfig | None = None,
    ):
        """Initialize the privacy sanitizer.

        Args:
            detector: PII detector to use. Defaults to HybridPIIDetector.
            config: Sanitization configuration. Defaults to standard config.
        """
        self._detector = detector or HybridPIIDetector()
        self._config = config or SanitizationConfig()

        # Pseudonym cache for consistent replacement
        self._pseudonym_cache: dict[str, str] = {}
        self._pseudonym_counter = 0

        # Statistics
        self._sanitization_count = 0
        self._blocked_count = 0
        self._pii_detection_count = 0

    @property
    def config(self) -> SanitizationConfig:
        """Return current configuration."""
        return self._config

    @config.setter
    def config(self, value: SanitizationConfig) -> None:
        """Update configuration."""
        self._config = value

    @property
    def statistics(self) -> dict[str, Any]:
        """Return sanitization statistics."""
        return {
            "sanitization_count": self._sanitization_count,
            "blocked_count": self._blocked_count,
            "pii_detection_count": self._pii_detection_count,
            "pseudonym_cache_size": len(self._pseudonym_cache),
        }

    async def sanitize(self, content: str) -> SanitizationResult:
        """Sanitize content by detecting and handling PII.

        Args:
            content: The text content to sanitize.

        Returns:
            SanitizationResult with sanitized content and metadata.
        """
        start_time = time.time()
        self._sanitization_count += 1

        # Handle empty content
        if not content or not content.strip():
            return SanitizationResult(
                original_content=content,
                sanitized_content=content,
                processing_time_ms=0.0,
            )

        # Detect PII
        detections = await self._detector.detect(content)
        self._pii_detection_count += len(detections)

        # Apply custom sensitivity overrides
        detections = self._apply_sensitivity_overrides(detections)

        # Check for blocking conditions
        if self._should_block(detections):
            self._blocked_count += 1
            critical_types = [
                d.pii_type.value
                for d in detections
                if d.sensitivity == SensitivityLevel.CRITICAL
            ]
            return SanitizationResult(
                original_content=content,
                sanitized_content="",
                detections=detections,
                segment_folds=[],
                is_blocked=True,
                blocking_reason=f"Critical PII detected: {critical_types}",
                processing_time_ms=(time.time() - start_time) * 1000,
            )

        # Apply sanitization
        sanitized, segment_folds = self._apply_sanitization(content, detections)

        return SanitizationResult(
            original_content=content,
            sanitized_content=sanitized,
            detections=detections,
            segment_folds=segment_folds,
            is_blocked=False,
            processing_time_ms=(time.time() - start_time) * 1000,
        )

    async def sanitize_batch(
        self, contents: list[str]
    ) -> list[SanitizationResult]:
        """Sanitize multiple contents.

        Args:
            contents: List of text contents to sanitize.

        Returns:
            List of SanitizationResult objects.
        """
        return [await self.sanitize(content) for content in contents]

    def _apply_sensitivity_overrides(
        self, detections: list[PIIDetection]
    ) -> list[PIIDetection]:
        """Apply custom sensitivity overrides from config."""
        for detection in detections:
            override = self._config.get_sensitivity_override(detection.pii_type)
            if override is not None:
                detection.sensitivity = override
        return detections

    def _should_block(self, detections: list[PIIDetection]) -> bool:
        """Determine if content should be blocked based on detections."""
        if not self._config.block_critical:
            return False

        return any(
            d.sensitivity == SensitivityLevel.CRITICAL
            for d in detections
        )

    def _apply_sanitization(
        self,
        content: str,
        detections: list[PIIDetection],
    ) -> tuple[str, list[SegmentFold]]:
        """Apply sanitization rules to content.

        Args:
            content: Original content.
            detections: List of PII detections.

        Returns:
            Tuple of (sanitized_content, segment_folds).
        """
        if not detections:
            return content, []

        # Sort detections by position (process from end to start to preserve positions)
        sorted_detections = sorted(
            detections, key=lambda d: d.start_pos, reverse=True
        )

        sanitized = content
        segment_folds: list[SegmentFold] = []

        for detection in sorted_detections:
            # Determine action based on sensitivity
            action, replacement = self._get_action_for_detection(detection)

            if action == "keep":
                continue

            # Apply replacement
            start = detection.start_pos
            end = detection.end_pos
            sanitized = sanitized[:start] + replacement + sanitized[end:]

            # Create segment fold if configured
            if self._config.create_segment_folds and action != "keep":
                fold = self._create_segment_fold(detection, action)
                segment_folds.append(fold)

        # Reverse folds to match original order
        segment_folds.reverse()

        return sanitized, segment_folds

    def _get_action_for_detection(
        self, detection: PIIDetection
    ) -> tuple[str, str]:
        """Determine action and replacement for a detection.

        Args:
            detection: The PII detection.

        Returns:
            Tuple of (action, replacement_text).
        """
        sensitivity = detection.sensitivity

        if sensitivity == SensitivityLevel.CRITICAL:
            # Critical should be blocked, but if we get here, redact
            return "redact", self._config.get_redaction_text(detection.pii_type)

        elif sensitivity == SensitivityLevel.HIGH:
            if self._config.redact_high:
                if self._config.encrypt_high_sensitivity:
                    return "encrypt", self._create_encrypted_placeholder(detection)
                return "redact", self._config.get_redaction_text(detection.pii_type)
            return "keep", detection.value

        elif sensitivity == SensitivityLevel.MEDIUM:
            if self._config.pseudonymize_medium:
                return "pseudonymize", self._get_or_create_pseudonym(detection)
            return "keep", detection.value

        else:  # LOW
            if self._config.allow_low:
                return "keep", detection.value
            return "pseudonymize", self._get_or_create_pseudonym(detection)

    def _get_or_create_pseudonym(self, detection: PIIDetection) -> str:
        """Get or create a consistent pseudonym for a value.

        Args:
            detection: The PII detection.

        Returns:
            Pseudonym string.
        """
        # Use value as key for consistent pseudonymization
        key = f"{detection.pii_type.value}:{detection.value.lower()}"

        if key not in self._pseudonym_cache:
            self._pseudonym_counter += 1
            type_prefix = detection.pii_type.value.upper()[:3]
            pseudonym = f"{self._config.pseudonym_prefix}{type_prefix}_{self._pseudonym_counter}"
            self._pseudonym_cache[key] = pseudonym

        return self._pseudonym_cache[key]

    def _create_encrypted_placeholder(self, detection: PIIDetection) -> str:
        """Create an encrypted placeholder (actual encryption would be in SegmentFolder).

        Args:
            detection: The PII detection.

        Returns:
            Placeholder indicating encrypted content.
        """
        # Hash for reference (actual encryption is handled by SegmentFolder)
        hash_prefix = hashlib.sha256(detection.value.encode()).hexdigest()[:8]
        return f"[ENCRYPTED:{hash_prefix}]"

    def _create_segment_fold(
        self, detection: PIIDetection, action: str
    ) -> SegmentFold:
        """Create a SegmentFold for audit trail.

        Args:
            detection: The PII detection.
            action: The sanitization action taken.

        Returns:
            SegmentFold object.
        """
        from datetime import datetime, timedelta

        # Calculate expiration if configured
        expires_at = None
        if self._config.fold_expiration_days is not None:
            expires_at = datetime.now() + timedelta(
                days=self._config.fold_expiration_days
            )

        # Determine fold type
        fold_type_map = {
            "redact": "redacted",
            "pseudonymize": "pseudonymized",
            "encrypt": "encrypted",
        }
        fold_type = fold_type_map.get(action, "redacted")

        return SegmentFold(
            fold_id=str(uuid.uuid4()),
            start_pos=detection.start_pos,
            end_pos=detection.end_pos,
            fold_type=fold_type,
            original_hash=hashlib.sha256(detection.value.encode()).hexdigest(),
            pii_type=detection.pii_type,
            encryption_key_id=(
                self._config.encryption_key_id
                if action == "encrypt" else None
            ),
            can_unlock=(action != "redact"),  # Redacted cannot be recovered
            unlock_conditions={
                "requires_legal_request": True,
                "sensitivity_level": detection.sensitivity.value,
            },
            expires_at=expires_at,
            compliance_framework=",".join(self._config.compliance_frameworks),
        )

    def clear_pseudonym_cache(self) -> None:
        """Clear the pseudonym cache.

        Call this when starting a new session if you want fresh pseudonyms.
        """
        self._pseudonym_cache.clear()
        self._pseudonym_counter = 0

    def reset_statistics(self) -> None:
        """Reset all statistics counters."""
        self._sanitization_count = 0
        self._blocked_count = 0
        self._pii_detection_count = 0
