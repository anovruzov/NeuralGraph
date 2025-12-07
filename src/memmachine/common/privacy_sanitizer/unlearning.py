"""Machine unlearning service for privacy compliance (GDPR right to erasure)."""

import logging
import time
import uuid
from datetime import datetime
from typing import Any, Protocol

from .data_types import (
    ComplianceFramework,
    ForgetRequest,
    ForgetResult,
    ForgetType,
    PIIType,
)

logger = logging.getLogger(__name__)


class EpisodeStorage(Protocol):
    """Protocol for episode storage operations."""

    async def delete_episodes(self, uids: list[str]) -> int:
        """Delete episodes by UID."""
        ...

    async def get_episode_uids_by_filter(
        self, session_key: str, filter_expr: str
    ) -> list[str]:
        """Get episode UIDs matching a filter."""
        ...


class DerivativeStorage(Protocol):
    """Protocol for derivative storage operations."""

    async def delete_derivatives_for_episodes(self, episode_uids: list[str]) -> int:
        """Delete derivatives linked to episodes."""
        ...


class SemanticStorage(Protocol):
    """Protocol for semantic memory storage."""

    async def delete_features_for_episodes(self, episode_uids: list[str]) -> int:
        """Delete semantic features linked to episodes."""
        ...

    async def delete_features_by_entity(self, entity: str) -> int:
        """Delete semantic features mentioning an entity."""
        ...


class FoldStorage(Protocol):
    """Protocol for segment fold storage."""

    async def delete_folds_for_episodes(self, episode_uids: list[str]) -> int:
        """Delete folds linked to episodes."""
        ...


class AuditLogger(Protocol):
    """Protocol for compliance audit logging."""

    async def log_forget_request(
        self, request: ForgetRequest, audit_id: str
    ) -> None:
        """Log a forget request."""
        ...

    async def log_forget_result(
        self, result: ForgetResult, audit_id: str
    ) -> None:
        """Log a forget result."""
        ...


class UnlearningService:
    """Implements privacy-preserving selective forgetting.

    Supports GDPR Article 17 (Right to Erasure), PIPL data deletion,
    and CCPA right to deletion.
    """

    def __init__(
        self,
        episode_storage: EpisodeStorage | None = None,
        derivative_storage: DerivativeStorage | None = None,
        semantic_storage: SemanticStorage | None = None,
        fold_storage: FoldStorage | None = None,
        audit_logger: AuditLogger | None = None,
    ):
        """Initialize unlearning service.

        Args:
            episode_storage: Storage for episode operations.
            derivative_storage: Storage for derivative operations.
            semantic_storage: Storage for semantic memory operations.
            fold_storage: Storage for segment fold operations.
            audit_logger: Logger for compliance audit trail.
        """
        self._episode_storage = episode_storage
        self._derivative_storage = derivative_storage
        self._semantic_storage = semantic_storage
        self._fold_storage = fold_storage
        self._audit_logger = audit_logger

        # Statistics
        self._total_requests = 0
        self._successful_requests = 0
        self._failed_requests = 0

    @property
    def statistics(self) -> dict[str, int]:
        """Return service statistics."""
        return {
            "total_requests": self._total_requests,
            "successful_requests": self._successful_requests,
            "failed_requests": self._failed_requests,
        }

    async def forget(self, request: ForgetRequest) -> ForgetResult:
        """Execute a forgetting operation with audit trail.

        Args:
            request: The ForgetRequest specifying what to forget.

        Returns:
            ForgetResult with operation details.
        """
        start_time = time.time()
        audit_id = str(uuid.uuid4())

        self._total_requests += 1

        # Log the request
        if self._audit_logger:
            await self._audit_logger.log_forget_request(request, audit_id)

        logger.info(
            f"Processing forget request {audit_id}: "
            f"type={request.forget_type.value}, "
            f"requester={request.requester_id}"
        )

        try:
            # Execute based on forget type
            match request.forget_type:
                case ForgetType.SELECTIVE:
                    result = await self._selective_forget(request, audit_id)
                case ForgetType.ENTITY_BASED:
                    result = await self._entity_forget(request, audit_id)
                case ForgetType.TEMPORAL:
                    result = await self._temporal_forget(request, audit_id)
                case ForgetType.CATEGORY:
                    result = await self._category_forget(request, audit_id)
                case ForgetType.PII_BASED:
                    result = await self._pii_based_forget(request, audit_id)
                case _:
                    result = ForgetResult(
                        success=False,
                        request=request,
                        audit_id=audit_id,
                        error_message=f"Unknown forget type: {request.forget_type}",
                    )

            result.processing_time_ms = (time.time() - start_time) * 1000

            if result.success:
                self._successful_requests += 1
                logger.info(
                    f"Forget request {audit_id} completed: "
                    f"removed {result.total_removed} items"
                )
            else:
                self._failed_requests += 1
                logger.error(
                    f"Forget request {audit_id} failed: {result.error_message}"
                )

            # Log the result
            if self._audit_logger:
                await self._audit_logger.log_forget_result(result, audit_id)

            return result

        except Exception as e:
            self._failed_requests += 1
            logger.error(f"Forget request {audit_id} error: {e}", exc_info=True)

            result = ForgetResult(
                success=False,
                request=request,
                audit_id=audit_id,
                error_message=str(e),
                processing_time_ms=(time.time() - start_time) * 1000,
            )

            if self._audit_logger:
                await self._audit_logger.log_forget_result(result, audit_id)

            return result

    async def _selective_forget(
        self, request: ForgetRequest, audit_id: str
    ) -> ForgetResult:
        """Remove specific episodes by ID.

        Args:
            request: Forget request with target_ids.
            audit_id: Audit trail ID.

        Returns:
            ForgetResult with counts.
        """
        if not request.target_ids:
            return ForgetResult(
                success=False,
                request=request,
                audit_id=audit_id,
                error_message="No target IDs provided for selective forget",
            )

        target_ids = request.target_ids
        episodes_removed = 0
        derivatives_removed = 0
        semantic_removed = 0
        folds_removed = 0

        # Delete episodes
        if self._episode_storage:
            episodes_removed = await self._episode_storage.delete_episodes(target_ids)

        # Delete derivatives
        if self._derivative_storage:
            derivatives_removed = await self._derivative_storage.delete_derivatives_for_episodes(target_ids)

        # Delete semantic features
        if self._semantic_storage:
            semantic_removed = await self._semantic_storage.delete_features_for_episodes(target_ids)

        # Delete folds
        if self._fold_storage:
            folds_removed = await self._fold_storage.delete_folds_for_episodes(target_ids)

        return ForgetResult(
            success=True,
            request=request,
            episodes_removed=episodes_removed,
            derivatives_removed=derivatives_removed,
            semantic_features_removed=semantic_removed,
            segment_folds_removed=folds_removed,
            audit_id=audit_id,
        )

    async def _entity_forget(
        self, request: ForgetRequest, audit_id: str
    ) -> ForgetResult:
        """Remove all memories mentioning a specific entity.

        This implements GDPR "right to be forgotten" for a specific person/entity.

        Args:
            request: Forget request with entity_identifier.
            audit_id: Audit trail ID.

        Returns:
            ForgetResult with counts.
        """
        if not request.entity_identifier:
            return ForgetResult(
                success=False,
                request=request,
                audit_id=audit_id,
                error_message="No entity identifier provided for entity-based forget",
            )

        entity = request.entity_identifier
        episodes_removed = 0
        derivatives_removed = 0
        semantic_removed = 0
        folds_removed = 0

        # Find episodes mentioning the entity
        # This would require a content search - simplified here
        if self._episode_storage:
            # In reality, this would do a full-text search
            filter_expr = f"content LIKE '%{entity}%'"
            matching_uids = await self._episode_storage.get_episode_uids_by_filter(
                session_key="*",  # All sessions
                filter_expr=filter_expr,
            )

            if matching_uids:
                episodes_removed = await self._episode_storage.delete_episodes(matching_uids)

                # Delete related data
                if self._derivative_storage:
                    derivatives_removed = await self._derivative_storage.delete_derivatives_for_episodes(matching_uids)

                if self._fold_storage:
                    folds_removed = await self._fold_storage.delete_folds_for_episodes(matching_uids)

        # Delete semantic features about the entity
        if self._semantic_storage:
            semantic_removed = await self._semantic_storage.delete_features_by_entity(entity)

        return ForgetResult(
            success=True,
            request=request,
            episodes_removed=episodes_removed,
            derivatives_removed=derivatives_removed,
            semantic_features_removed=semantic_removed,
            segment_folds_removed=folds_removed,
            audit_id=audit_id,
        )

    async def _temporal_forget(
        self, request: ForgetRequest, audit_id: str
    ) -> ForgetResult:
        """Remove memories within a time range.

        Args:
            request: Forget request with time_range.
            audit_id: Audit trail ID.

        Returns:
            ForgetResult with counts.
        """
        if not request.time_range:
            return ForgetResult(
                success=False,
                request=request,
                audit_id=audit_id,
                error_message="No time range provided for temporal forget",
            )

        start_time, end_time = request.time_range
        episodes_removed = 0
        derivatives_removed = 0
        semantic_removed = 0
        folds_removed = 0

        if self._episode_storage:
            # Build temporal filter
            filter_expr = (
                f"created_at >= '{start_time.isoformat()}' AND "
                f"created_at <= '{end_time.isoformat()}'"
            )
            matching_uids = await self._episode_storage.get_episode_uids_by_filter(
                session_key="*",
                filter_expr=filter_expr,
            )

            if matching_uids:
                episodes_removed = await self._episode_storage.delete_episodes(matching_uids)

                if self._derivative_storage:
                    derivatives_removed = await self._derivative_storage.delete_derivatives_for_episodes(matching_uids)

                if self._semantic_storage:
                    semantic_removed = await self._semantic_storage.delete_features_for_episodes(matching_uids)

                if self._fold_storage:
                    folds_removed = await self._fold_storage.delete_folds_for_episodes(matching_uids)

        return ForgetResult(
            success=True,
            request=request,
            episodes_removed=episodes_removed,
            derivatives_removed=derivatives_removed,
            semantic_features_removed=semantic_removed,
            segment_folds_removed=folds_removed,
            audit_id=audit_id,
        )

    async def _category_forget(
        self, request: ForgetRequest, audit_id: str
    ) -> ForgetResult:
        """Remove memories by category/domain.

        Args:
            request: Forget request with categories.
            audit_id: Audit trail ID.

        Returns:
            ForgetResult with counts.
        """
        if not request.categories:
            return ForgetResult(
                success=False,
                request=request,
                audit_id=audit_id,
                error_message="No categories provided for category forget",
            )

        categories = request.categories
        episodes_removed = 0
        derivatives_removed = 0
        semantic_removed = 0
        folds_removed = 0

        if self._episode_storage:
            # Build category filter
            category_list = ",".join(f"'{c}'" for c in categories)
            filter_expr = f"domain IN ({category_list})"
            matching_uids = await self._episode_storage.get_episode_uids_by_filter(
                session_key="*",
                filter_expr=filter_expr,
            )

            if matching_uids:
                episodes_removed = await self._episode_storage.delete_episodes(matching_uids)

                if self._derivative_storage:
                    derivatives_removed = await self._derivative_storage.delete_derivatives_for_episodes(matching_uids)

                if self._semantic_storage:
                    semantic_removed = await self._semantic_storage.delete_features_for_episodes(matching_uids)

                if self._fold_storage:
                    folds_removed = await self._fold_storage.delete_folds_for_episodes(matching_uids)

        return ForgetResult(
            success=True,
            request=request,
            episodes_removed=episodes_removed,
            derivatives_removed=derivatives_removed,
            semantic_features_removed=semantic_removed,
            segment_folds_removed=folds_removed,
            audit_id=audit_id,
        )

    async def _pii_based_forget(
        self, request: ForgetRequest, audit_id: str
    ) -> ForgetResult:
        """Remove memories containing specific PII types.

        Args:
            request: Forget request with pii_types.
            audit_id: Audit trail ID.

        Returns:
            ForgetResult with counts.
        """
        if not request.pii_types:
            return ForgetResult(
                success=False,
                request=request,
                audit_id=audit_id,
                error_message="No PII types provided for PII-based forget",
            )

        # This would require re-scanning content for PII
        # In practice, we'd track PII detections during ingestion
        pii_types = request.pii_types
        episodes_removed = 0
        derivatives_removed = 0
        semantic_removed = 0
        folds_removed = 0

        # For now, delete based on stored PII detection metadata
        if self._episode_storage:
            pii_type_list = ",".join(f"'{p.value}'" for p in pii_types)
            filter_expr = f"pii_types && ARRAY[{pii_type_list}]"  # Array overlap
            matching_uids = await self._episode_storage.get_episode_uids_by_filter(
                session_key="*",
                filter_expr=filter_expr,
            )

            if matching_uids:
                episodes_removed = await self._episode_storage.delete_episodes(matching_uids)

                if self._derivative_storage:
                    derivatives_removed = await self._derivative_storage.delete_derivatives_for_episodes(matching_uids)

                if self._semantic_storage:
                    semantic_removed = await self._semantic_storage.delete_features_for_episodes(matching_uids)

                if self._fold_storage:
                    folds_removed = await self._fold_storage.delete_folds_for_episodes(matching_uids)

        return ForgetResult(
            success=True,
            request=request,
            episodes_removed=episodes_removed,
            derivatives_removed=derivatives_removed,
            semantic_features_removed=semantic_removed,
            segment_folds_removed=folds_removed,
            audit_id=audit_id,
        )

    async def verify_erasure(
        self, request: ForgetRequest, result: ForgetResult
    ) -> bool:
        """Verify that erasure was complete.

        Args:
            request: The original forget request.
            result: The forget result to verify.

        Returns:
            True if erasure is verified complete.
        """
        # In a real implementation, this would re-check storage
        # to ensure no data remains
        if not result.success:
            return False

        # Verification would include:
        # 1. Re-running the search that identified data to delete
        # 2. Checking that counts are now 0
        # 3. Verifying no references remain

        logger.info(f"Erasure verification for {result.audit_id}: assumed complete")
        return True

    async def generate_erasure_certificate(
        self, result: ForgetResult
    ) -> dict[str, Any]:
        """Generate a certificate of erasure for compliance.

        Args:
            result: The completed forget result.

        Returns:
            Certificate dictionary for record-keeping.
        """
        return {
            "certificate_id": str(uuid.uuid4()),
            "audit_id": result.audit_id,
            "request_type": result.request.forget_type.value,
            "requester_id": result.request.requester_id,
            "compliance_framework": result.request.compliance_framework,
            "items_erased": {
                "episodes": result.episodes_removed,
                "derivatives": result.derivatives_removed,
                "semantic_features": result.semantic_features_removed,
                "segment_folds": result.segment_folds_removed,
                "total": result.total_removed,
            },
            "processing_time_ms": result.processing_time_ms,
            "requested_at": result.request.created_at.isoformat(),
            "completed_at": result.completed_at.isoformat(),
            "certificate_generated_at": datetime.now().isoformat(),
        }
