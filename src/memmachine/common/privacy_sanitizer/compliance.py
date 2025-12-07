"""Compliance auditing and logging for GDPR, PIPL, and CCPA."""

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from .data_types import (
    ComplianceFramework,
    ForgetRequest,
    ForgetResult,
    PIIType,
    SanitizationResult,
    SensitivityLevel,
)

logger = logging.getLogger(__name__)


class AuditEventType(str, Enum):
    """Types of audit events for compliance tracking."""

    # Data access events
    DATA_ACCESS = "data_access"           # Data was accessed/retrieved
    DATA_EXPORT = "data_export"           # Data was exported (portability)

    # Data modification events
    DATA_INGESTION = "data_ingestion"     # New data was stored
    DATA_UPDATE = "data_update"           # Data was modified
    DATA_DELETION = "data_deletion"       # Data was deleted

    # Privacy events
    PII_DETECTED = "pii_detected"         # PII was detected in content
    PII_SANITIZED = "pii_sanitized"       # PII was sanitized
    CONTENT_BLOCKED = "content_blocked"   # Content was blocked from storage

    # Consent events
    CONSENT_GRANTED = "consent_granted"   # User granted consent
    CONSENT_REVOKED = "consent_revoked"   # User revoked consent

    # Erasure events
    ERASURE_REQUESTED = "erasure_requested"   # Erasure was requested
    ERASURE_COMPLETED = "erasure_completed"   # Erasure was completed
    ERASURE_FAILED = "erasure_failed"         # Erasure failed

    # Security events
    ENCRYPTION_APPLIED = "encryption_applied"     # Content was encrypted
    DECRYPTION_PERFORMED = "decryption_performed" # Content was decrypted

    # Compliance events
    COMPLIANCE_CHECK = "compliance_check"     # Compliance validation
    RETENTION_EXPIRED = "retention_expired"   # Data retention expired


@dataclass
class AuditEvent:
    """A single audit event for compliance tracking."""

    event_id: str                              # Unique event ID
    event_type: AuditEventType                 # Type of event
    timestamp: datetime                        # When it occurred
    session_key: str | None = None             # Associated session
    user_id: str | None = None                 # User involved
    compliance_framework: ComplianceFramework | None = None
    details: dict[str, Any] = field(default_factory=dict)

    # Event outcome
    success: bool = True
    error_message: str | None = None

    # Data minimization - don't store actual PII in audit log
    pii_types_involved: list[str] = field(default_factory=list)
    item_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "session_key": self.session_key,
            "user_id": self.user_id,
            "compliance_framework": (
                self.compliance_framework.value
                if self.compliance_framework else None
            ),
            "details": self.details,
            "success": self.success,
            "error_message": self.error_message,
            "pii_types_involved": self.pii_types_involved,
            "item_count": self.item_count,
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2)


class AuditStorage(Protocol):
    """Protocol for audit event storage."""

    async def store_event(self, event: AuditEvent) -> None:
        """Store an audit event."""
        ...

    async def get_events(
        self,
        session_key: str | None = None,
        event_type: AuditEventType | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Retrieve audit events with filters."""
        ...


class InMemoryAuditStorage:
    """In-memory audit storage for development/testing."""

    def __init__(self, max_events: int = 10000):
        self._events: list[AuditEvent] = []
        self._max_events = max_events

    async def store_event(self, event: AuditEvent) -> None:
        """Store an audit event."""
        self._events.append(event)
        # Enforce size limit
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]

    async def get_events(
        self,
        session_key: str | None = None,
        event_type: AuditEventType | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Retrieve audit events with filters."""
        results = []
        for event in reversed(self._events):  # Most recent first
            if session_key and event.session_key != session_key:
                continue
            if event_type and event.event_type != event_type:
                continue
            if start_time and event.timestamp < start_time:
                continue
            if end_time and event.timestamp > end_time:
                continue
            results.append(event)
            if len(results) >= limit:
                break
        return results


class ComplianceAuditor:
    """Compliance auditing service for privacy regulations.

    Tracks all privacy-related events for GDPR, PIPL, and CCPA compliance.
    """

    def __init__(
        self,
        storage: AuditStorage | None = None,
        default_framework: ComplianceFramework = ComplianceFramework.GDPR,
        enabled: bool = True,
    ):
        """Initialize compliance auditor.

        Args:
            storage: Audit event storage. Uses in-memory if None.
            default_framework: Default compliance framework.
            enabled: Whether auditing is enabled.
        """
        self._storage = storage or InMemoryAuditStorage()
        self._default_framework = default_framework
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        """Whether auditing is enabled."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """Enable or disable auditing."""
        self._enabled = value

    async def log_event(
        self,
        event_type: AuditEventType,
        session_key: str | None = None,
        user_id: str | None = None,
        framework: ComplianceFramework | None = None,
        details: dict[str, Any] | None = None,
        success: bool = True,
        error_message: str | None = None,
        pii_types: list[PIIType] | None = None,
        item_count: int = 0,
    ) -> AuditEvent | None:
        """Log an audit event.

        Args:
            event_type: Type of event.
            session_key: Associated session.
            user_id: User involved.
            framework: Compliance framework.
            details: Additional event details.
            success: Whether the operation succeeded.
            error_message: Error message if failed.
            pii_types: PII types involved.
            item_count: Number of items affected.

        Returns:
            The created AuditEvent, or None if auditing disabled.
        """
        if not self._enabled:
            return None

        event = AuditEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            timestamp=datetime.now(),
            session_key=session_key,
            user_id=user_id,
            compliance_framework=framework or self._default_framework,
            details=details or {},
            success=success,
            error_message=error_message,
            pii_types_involved=[p.value for p in pii_types] if pii_types else [],
            item_count=item_count,
        )

        await self._storage.store_event(event)
        logger.debug(f"Audit event: {event.event_type.value} - {event.event_id}")

        return event

    async def log_sanitization(
        self,
        result: SanitizationResult,
        session_key: str | None = None,
        user_id: str | None = None,
    ) -> list[AuditEvent]:
        """Log sanitization events for a SanitizationResult.

        Args:
            result: The sanitization result.
            session_key: Associated session.
            user_id: User involved.

        Returns:
            List of created audit events.
        """
        events = []

        # Log PII detection if any found
        if result.pii_detected:
            pii_types = list(set(d.pii_type for d in result.detections))
            sensitivity_levels = list(set(d.sensitivity.value for d in result.detections))

            event = await self.log_event(
                event_type=AuditEventType.PII_DETECTED,
                session_key=session_key,
                user_id=user_id,
                details={
                    "detection_count": result.detection_count,
                    "sensitivity_levels": sensitivity_levels,
                    "highest_sensitivity": (
                        result.highest_sensitivity.value
                        if result.highest_sensitivity else None
                    ),
                },
                pii_types=pii_types,
                item_count=result.detection_count,
            )
            if event:
                events.append(event)

        # Log content blocked
        if result.is_blocked:
            event = await self.log_event(
                event_type=AuditEventType.CONTENT_BLOCKED,
                session_key=session_key,
                user_id=user_id,
                details={
                    "blocking_reason": result.blocking_reason,
                },
                success=True,  # Blocking is a successful security action
            )
            if event:
                events.append(event)

        # Log sanitization
        if result.segment_folds:
            fold_types = list(set(f.fold_type for f in result.segment_folds))
            event = await self.log_event(
                event_type=AuditEventType.PII_SANITIZED,
                session_key=session_key,
                user_id=user_id,
                details={
                    "fold_count": len(result.segment_folds),
                    "fold_types": fold_types,
                    "processing_time_ms": result.processing_time_ms,
                },
                item_count=len(result.segment_folds),
            )
            if event:
                events.append(event)

        return events

    async def log_forget_request(
        self,
        request: ForgetRequest,
        audit_id: str,
    ) -> AuditEvent | None:
        """Log an erasure request.

        Args:
            request: The forget request.
            audit_id: Audit trail ID.

        Returns:
            Created audit event.
        """
        return await self.log_event(
            event_type=AuditEventType.ERASURE_REQUESTED,
            user_id=request.requester_id,
            framework=(
                ComplianceFramework.from_string(request.compliance_framework)
                if request.compliance_framework else None
            ),
            details={
                "audit_id": audit_id,
                "forget_type": request.forget_type.value,
                "reason": request.reason,
                "has_target_ids": request.target_ids is not None,
                "has_entity": request.entity_identifier is not None,
                "has_time_range": request.time_range is not None,
                "categories": request.categories,
            },
            pii_types=request.pii_types,
        )

    async def log_forget_result(
        self,
        result: ForgetResult,
        audit_id: str,
    ) -> AuditEvent | None:
        """Log an erasure result.

        Args:
            result: The forget result.
            audit_id: Audit trail ID.

        Returns:
            Created audit event.
        """
        event_type = (
            AuditEventType.ERASURE_COMPLETED
            if result.success
            else AuditEventType.ERASURE_FAILED
        )

        return await self.log_event(
            event_type=event_type,
            user_id=result.request.requester_id,
            framework=(
                ComplianceFramework.from_string(result.request.compliance_framework)
                if result.request.compliance_framework else None
            ),
            details={
                "audit_id": audit_id,
                "episodes_removed": result.episodes_removed,
                "derivatives_removed": result.derivatives_removed,
                "semantic_features_removed": result.semantic_features_removed,
                "segment_folds_removed": result.segment_folds_removed,
                "total_removed": result.total_removed,
                "processing_time_ms": result.processing_time_ms,
            },
            success=result.success,
            error_message=result.error_message,
            item_count=result.total_removed,
        )

    async def log_data_access(
        self,
        session_key: str,
        user_id: str | None = None,
        query: str | None = None,
        result_count: int = 0,
    ) -> AuditEvent | None:
        """Log data access for compliance tracking.

        Args:
            session_key: Session being accessed.
            user_id: User accessing data.
            query: The search query (redacted).
            result_count: Number of results returned.

        Returns:
            Created audit event.
        """
        return await self.log_event(
            event_type=AuditEventType.DATA_ACCESS,
            session_key=session_key,
            user_id=user_id,
            details={
                "query_provided": query is not None,
                "result_count": result_count,
            },
            item_count=result_count,
        )

    async def log_data_ingestion(
        self,
        session_key: str,
        episode_count: int,
        pii_detected: bool = False,
        sanitization_applied: bool = False,
    ) -> AuditEvent | None:
        """Log data ingestion.

        Args:
            session_key: Session receiving data.
            episode_count: Number of episodes ingested.
            pii_detected: Whether PII was detected.
            sanitization_applied: Whether sanitization was applied.

        Returns:
            Created audit event.
        """
        return await self.log_event(
            event_type=AuditEventType.DATA_INGESTION,
            session_key=session_key,
            details={
                "episode_count": episode_count,
                "pii_detected": pii_detected,
                "sanitization_applied": sanitization_applied,
            },
            item_count=episode_count,
        )

    async def get_audit_trail(
        self,
        session_key: str | None = None,
        event_type: AuditEventType | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Retrieve audit trail.

        Args:
            session_key: Filter by session.
            event_type: Filter by event type.
            start_time: Start of time range.
            end_time: End of time range.
            limit: Maximum events to return.

        Returns:
            List of matching audit events.
        """
        return await self._storage.get_events(
            session_key=session_key,
            event_type=event_type,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )

    async def generate_compliance_report(
        self,
        session_key: str | None = None,
        framework: ComplianceFramework | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, Any]:
        """Generate a compliance report.

        Args:
            session_key: Filter by session.
            framework: Filter by framework.
            start_time: Report start time.
            end_time: Report end time.

        Returns:
            Compliance report dictionary.
        """
        events = await self._storage.get_events(
            session_key=session_key,
            start_time=start_time,
            end_time=end_time,
            limit=10000,
        )

        # Filter by framework if specified
        if framework:
            events = [
                e for e in events
                if e.compliance_framework == framework
            ]

        # Aggregate statistics
        event_counts: dict[str, int] = {}
        pii_type_counts: dict[str, int] = {}
        total_items_processed = 0
        erasure_requests = 0
        erasure_completed = 0
        items_erased = 0

        for event in events:
            event_counts[event.event_type.value] = (
                event_counts.get(event.event_type.value, 0) + 1
            )

            for pii_type in event.pii_types_involved:
                pii_type_counts[pii_type] = pii_type_counts.get(pii_type, 0) + 1

            total_items_processed += event.item_count

            if event.event_type == AuditEventType.ERASURE_REQUESTED:
                erasure_requests += 1
            elif event.event_type == AuditEventType.ERASURE_COMPLETED:
                erasure_completed += 1
                items_erased += event.item_count

        return {
            "report_id": str(uuid.uuid4()),
            "generated_at": datetime.now().isoformat(),
            "framework": framework.value if framework else "all",
            "session_key": session_key,
            "time_range": {
                "start": start_time.isoformat() if start_time else None,
                "end": end_time.isoformat() if end_time else None,
            },
            "summary": {
                "total_events": len(events),
                "total_items_processed": total_items_processed,
                "erasure_requests": erasure_requests,
                "erasure_completed": erasure_completed,
                "items_erased": items_erased,
                "erasure_completion_rate": (
                    (erasure_completed / erasure_requests * 100)
                    if erasure_requests > 0 else 100.0
                ),
            },
            "event_breakdown": event_counts,
            "pii_type_breakdown": pii_type_counts,
        }
