"""Data types for privacy sanitization, PII detection, and compliance."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class PIIType(str, Enum):
    """Types of personally identifiable information."""

    NAME = "name"                     # Personal names
    EMAIL = "email"                   # Email addresses
    PHONE = "phone"                   # Phone numbers
    SSN = "ssn"                       # Social Security Numbers
    CREDIT_CARD = "credit_card"       # Credit/debit card numbers
    ADDRESS = "address"               # Physical addresses
    DATE_OF_BIRTH = "date_of_birth"   # Birth dates
    MEDICAL_INFO = "medical_info"     # Health/medical information
    FINANCIAL_INFO = "financial_info" # Bank accounts, financial data
    BIOMETRIC = "biometric"           # Fingerprints, face data
    IP_ADDRESS = "ip_address"         # IP addresses
    LOCATION = "location"             # GPS coordinates, location data
    PASSPORT = "passport"             # Passport numbers
    DRIVER_LICENSE = "driver_license" # Driver's license numbers
    NATIONAL_ID = "national_id"       # National ID numbers (PIPL)
    CUSTOM = "custom"                 # Custom/user-defined PII

    @classmethod
    def from_string(cls, s: str) -> "PIIType":
        """Parse PII type from string, case-insensitive."""
        s = s.strip().lower().replace(" ", "_").replace("-", "_")
        for pii_type in cls:
            if pii_type.value == s:
                return pii_type
        return cls.CUSTOM

    @classmethod
    def all_values(cls) -> list[str]:
        """Return all PII type values as strings."""
        return [p.value for p in cls]


class SensitivityLevel(str, Enum):
    """Classification of sensitivity for detected PII.

    Based on GDPR Article 9 special categories and PIPL requirements.
    """

    LOW = "low"           # Can be retained with consent (e.g., IP address)
    MEDIUM = "medium"     # Should be anonymized/pseudonymized
    HIGH = "high"         # Must be redacted/encrypted
    CRITICAL = "critical" # Must be excluded entirely (SSN, credit cards)

    @property
    def requires_encryption(self) -> bool:
        """Whether this sensitivity level requires encryption at rest."""
        return self in (SensitivityLevel.HIGH, SensitivityLevel.CRITICAL)

    @property
    def allows_storage(self) -> bool:
        """Whether content at this level can be stored."""
        return self != SensitivityLevel.CRITICAL

    @classmethod
    def from_string(cls, s: str) -> "SensitivityLevel":
        """Parse sensitivity level from string."""
        s = s.strip().lower()
        for level in cls:
            if level.value == s:
                return level
        return cls.MEDIUM


# Default PII type to sensitivity mapping
PII_SENSITIVITY_MAP: dict[PIIType, SensitivityLevel] = {
    PIIType.NAME: SensitivityLevel.MEDIUM,
    PIIType.EMAIL: SensitivityLevel.MEDIUM,
    PIIType.PHONE: SensitivityLevel.MEDIUM,
    PIIType.SSN: SensitivityLevel.CRITICAL,
    PIIType.CREDIT_CARD: SensitivityLevel.CRITICAL,
    PIIType.ADDRESS: SensitivityLevel.HIGH,
    PIIType.DATE_OF_BIRTH: SensitivityLevel.MEDIUM,
    PIIType.MEDICAL_INFO: SensitivityLevel.HIGH,
    PIIType.FINANCIAL_INFO: SensitivityLevel.HIGH,
    PIIType.BIOMETRIC: SensitivityLevel.CRITICAL,
    PIIType.IP_ADDRESS: SensitivityLevel.LOW,
    PIIType.LOCATION: SensitivityLevel.MEDIUM,
    PIIType.PASSPORT: SensitivityLevel.CRITICAL,
    PIIType.DRIVER_LICENSE: SensitivityLevel.HIGH,
    PIIType.NATIONAL_ID: SensitivityLevel.CRITICAL,
    PIIType.CUSTOM: SensitivityLevel.MEDIUM,
}


def get_sensitivity_for_pii(pii_type: PIIType) -> SensitivityLevel:
    """Get default sensitivity level for a PII type."""
    return PII_SENSITIVITY_MAP.get(pii_type, SensitivityLevel.MEDIUM)


@dataclass
class PIIDetection:
    """A single PII detection result."""

    pii_type: PIIType
    value: str                           # The detected PII text
    start_pos: int                       # Start position in original text
    end_pos: int                         # End position in original text
    confidence: float                    # Detection confidence (0.0-1.0)
    sensitivity: SensitivityLevel        # Assigned sensitivity level
    context: str | None = None           # Surrounding text for validation
    detector_source: str = "unknown"     # Which detector found this

    def __post_init__(self):
        """Validate detection fields."""
        if self.confidence < 0.0 or self.confidence > 1.0:
            raise ValueError(f"Confidence must be 0.0-1.0, got {self.confidence}")
        if self.start_pos < 0:
            raise ValueError(f"start_pos must be >= 0, got {self.start_pos}")
        if self.end_pos < self.start_pos:
            raise ValueError(f"end_pos must be >= start_pos")

    @property
    def span_length(self) -> int:
        """Length of the detected text span."""
        return self.end_pos - self.start_pos


@dataclass
class SegmentFold:
    """A folded/locked segment of memory for privacy protection.

    Represents a portion of content that has been sanitized and can
    optionally be recovered under certain conditions (e.g., legal request).
    """

    fold_id: str                                  # Unique identifier for this fold
    start_pos: int                                # Original start position
    end_pos: int                                  # Original end position
    fold_type: str                                # "redacted", "encrypted", "pseudonymized"
    original_hash: str                            # SHA-256 hash for verification
    pii_type: PIIType | None = None               # Type of PII that was folded
    encryption_key_id: str | None = None          # Reference to encryption key
    can_unlock: bool = True                       # Whether unlocking is possible
    unlock_conditions: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime | None = None            # Auto-delete date
    compliance_framework: str | None = None       # GDPR, PIPL, CCPA

    def is_expired(self) -> bool:
        """Check if this fold has expired."""
        if self.expires_at is None:
            return False
        return datetime.now() > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "fold_id": self.fold_id,
            "start_pos": self.start_pos,
            "end_pos": self.end_pos,
            "fold_type": self.fold_type,
            "original_hash": self.original_hash,
            "pii_type": self.pii_type.value if self.pii_type else None,
            "encryption_key_id": self.encryption_key_id,
            "can_unlock": self.can_unlock,
            "unlock_conditions": self.unlock_conditions,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "compliance_framework": self.compliance_framework,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SegmentFold":
        """Create from dictionary."""
        return cls(
            fold_id=data["fold_id"],
            start_pos=data["start_pos"],
            end_pos=data["end_pos"],
            fold_type=data["fold_type"],
            original_hash=data["original_hash"],
            pii_type=PIIType.from_string(data["pii_type"]) if data.get("pii_type") else None,
            encryption_key_id=data.get("encryption_key_id"),
            can_unlock=data.get("can_unlock", True),
            unlock_conditions=data.get("unlock_conditions", {}),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
            expires_at=datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None,
            compliance_framework=data.get("compliance_framework"),
        )


@dataclass
class SanitizationResult:
    """Result of sanitizing content for privacy."""

    original_content: str                         # Original text before sanitization
    sanitized_content: str                        # Text after sanitization
    detections: list[PIIDetection] = field(default_factory=list)
    segment_folds: list[SegmentFold] = field(default_factory=list)
    is_blocked: bool = False                      # True if content should not be stored
    blocking_reason: str | None = None            # Reason for blocking
    processing_time_ms: float = 0.0               # Time taken to sanitize

    @property
    def pii_detected(self) -> bool:
        """Whether any PII was detected."""
        return len(self.detections) > 0

    @property
    def detection_count(self) -> int:
        """Number of PII detections."""
        return len(self.detections)

    @property
    def highest_sensitivity(self) -> SensitivityLevel | None:
        """Return the highest sensitivity level among detections."""
        if not self.detections:
            return None
        return max(self.detections, key=lambda d: list(SensitivityLevel).index(d.sensitivity)).sensitivity

    def get_detections_by_type(self, pii_type: PIIType) -> list[PIIDetection]:
        """Get all detections of a specific PII type."""
        return [d for d in self.detections if d.pii_type == pii_type]

    def get_detections_by_sensitivity(self, sensitivity: SensitivityLevel) -> list[PIIDetection]:
        """Get all detections at a specific sensitivity level."""
        return [d for d in self.detections if d.sensitivity == sensitivity]


class ForgetType(str, Enum):
    """Types of forgetting operations for machine unlearning."""

    SELECTIVE = "selective"           # Remove specific episodes by ID
    ENTITY_BASED = "entity_based"     # Remove all memories about an entity
    TEMPORAL = "temporal"             # Remove memories in time range
    CATEGORY = "category"             # Remove memories by category/domain
    PII_BASED = "pii_based"           # Remove based on PII type

    @classmethod
    def from_string(cls, s: str) -> "ForgetType":
        """Parse forget type from string."""
        s = s.strip().lower()
        for ft in cls:
            if ft.value == s:
                return ft
        return cls.SELECTIVE


@dataclass
class ForgetRequest:
    """Request to forget specific information (GDPR right to erasure)."""

    forget_type: ForgetType
    requester_id: str                                # Who made the request
    target_ids: list[str] | None = None              # Specific episode IDs
    entity_identifier: str | None = None             # Entity to forget
    time_range: tuple[datetime, datetime] | None = None  # Temporal bounds
    categories: list[str] | None = None              # Domain categories
    pii_types: list[PIIType] | None = None           # PII types to remove
    reason: str | None = None                        # For audit trail
    compliance_framework: str | None = None          # GDPR, PIPL, CCPA
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging/storage."""
        return {
            "forget_type": self.forget_type.value,
            "requester_id": self.requester_id,
            "target_ids": self.target_ids,
            "entity_identifier": self.entity_identifier,
            "time_range": (
                (self.time_range[0].isoformat(), self.time_range[1].isoformat())
                if self.time_range else None
            ),
            "categories": self.categories,
            "pii_types": [p.value for p in self.pii_types] if self.pii_types else None,
            "reason": self.reason,
            "compliance_framework": self.compliance_framework,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class ForgetResult:
    """Result of a forget operation."""

    success: bool
    request: ForgetRequest
    episodes_removed: int = 0
    derivatives_removed: int = 0
    semantic_features_removed: int = 0
    segment_folds_removed: int = 0
    audit_id: str = ""                               # For compliance tracking
    error_message: str | None = None
    processing_time_ms: float = 0.0
    completed_at: datetime = field(default_factory=datetime.now)

    @property
    def total_removed(self) -> int:
        """Total items removed."""
        return (
            self.episodes_removed +
            self.derivatives_removed +
            self.semantic_features_removed +
            self.segment_folds_removed
        )


class ComplianceFramework(str, Enum):
    """Supported compliance frameworks."""

    GDPR = "gdpr"     # EU General Data Protection Regulation
    PIPL = "pipl"     # China Personal Information Protection Law
    CCPA = "ccpa"     # California Consumer Privacy Act
    HIPAA = "hipaa"   # US Health Insurance Portability

    @property
    def requires_consent(self) -> bool:
        """Whether this framework requires explicit consent."""
        return self in (ComplianceFramework.GDPR, ComplianceFramework.PIPL)

    @property
    def supports_erasure(self) -> bool:
        """Whether this framework supports right to erasure."""
        return self in (
            ComplianceFramework.GDPR,
            ComplianceFramework.PIPL,
            ComplianceFramework.CCPA
        )

    @property
    def retention_limit_days(self) -> int | None:
        """Default retention limit in days, if any."""
        # PIPL requires data minimization, GDPR has purpose limitation
        return None  # Varies by purpose

    @classmethod
    def from_string(cls, s: str) -> "ComplianceFramework":
        """Parse framework from string."""
        s = s.strip().lower()
        for fw in cls:
            if fw.value == s:
                return fw
        return cls.GDPR  # Default to most restrictive

    @classmethod
    def all_values(cls) -> list[str]:
        """Return all framework values."""
        return [f.value for f in cls]
