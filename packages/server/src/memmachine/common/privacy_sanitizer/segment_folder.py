"""Segment folding for encrypted storage of sensitive data."""

import base64
import hashlib
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .data_types import SegmentFold, PIIType

logger = logging.getLogger(__name__)


@dataclass
class EncryptionKey:
    """Represents an encryption key with metadata."""

    key_id: str
    key_bytes: bytes
    created_at: datetime
    algorithm: str = "Fernet"  # AES-128-CBC under the hood

    def to_fernet(self) -> Fernet:
        """Create a Fernet instance from this key."""
        return Fernet(self.key_bytes)


class KeyManager:
    """Manages encryption keys for segment folding.

    In production, this should integrate with a proper key management
    service (KMS) like AWS KMS, Azure Key Vault, or HashiCorp Vault.
    """

    def __init__(self, master_password: str | None = None):
        """Initialize key manager.

        Args:
            master_password: Master password for key derivation.
                            If None, uses environment variable MEMMACHINE_MASTER_KEY.
        """
        self._master_password = master_password or os.environ.get(
            "MEMMACHINE_MASTER_KEY", "default-dev-key-change-in-production"
        )
        self._keys: dict[str, EncryptionKey] = {}
        self._default_key_id: str | None = None

    def generate_key(self, key_id: str | None = None) -> EncryptionKey:
        """Generate a new encryption key.

        Args:
            key_id: Optional key ID. If None, generates UUID.

        Returns:
            New EncryptionKey.
        """
        if key_id is None:
            key_id = secrets.token_hex(16)

        # Generate Fernet key (URL-safe base64-encoded 32 bytes)
        key_bytes = Fernet.generate_key()

        key = EncryptionKey(
            key_id=key_id,
            key_bytes=key_bytes,
            created_at=datetime.now(),
        )

        self._keys[key_id] = key
        if self._default_key_id is None:
            self._default_key_id = key_id

        return key

    def derive_key(self, key_id: str, salt: bytes | None = None) -> EncryptionKey:
        """Derive a key from master password using PBKDF2.

        Args:
            key_id: ID for the derived key.
            salt: Salt bytes. If None, generates random salt.

        Returns:
            Derived EncryptionKey.
        """
        if salt is None:
            salt = os.urandom(16)

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,  # OWASP recommended minimum
        )

        derived_key = base64.urlsafe_b64encode(
            kdf.derive(self._master_password.encode())
        )

        key = EncryptionKey(
            key_id=key_id,
            key_bytes=derived_key,
            created_at=datetime.now(),
        )

        self._keys[key_id] = key
        return key

    def get_key(self, key_id: str) -> EncryptionKey | None:
        """Retrieve a key by ID."""
        return self._keys.get(key_id)

    def get_default_key(self) -> EncryptionKey:
        """Get the default key, creating one if necessary."""
        if self._default_key_id is None or self._default_key_id not in self._keys:
            return self.generate_key()
        return self._keys[self._default_key_id]

    def set_default_key(self, key_id: str) -> None:
        """Set the default key ID."""
        if key_id not in self._keys:
            raise ValueError(f"Key {key_id} not found")
        self._default_key_id = key_id


class SegmentFolder:
    """Handles encryption and decryption of sensitive content segments.

    Implements "Segment Folding" - the process of replacing sensitive
    content with encrypted versions that can be selectively recovered.
    """

    def __init__(self, key_manager: KeyManager | None = None):
        """Initialize segment folder.

        Args:
            key_manager: KeyManager instance. Creates default if None.
        """
        self._key_manager = key_manager or KeyManager()

    def fold(
        self,
        content: str,
        pii_type: PIIType | None = None,
        key_id: str | None = None,
    ) -> tuple[str, SegmentFold]:
        """Fold (encrypt) a content segment.

        Args:
            content: The sensitive content to encrypt.
            pii_type: Type of PII being folded.
            key_id: Key ID to use. Uses default if None.

        Returns:
            Tuple of (encrypted_placeholder, SegmentFold).
        """
        # Get encryption key
        if key_id:
            key = self._key_manager.get_key(key_id)
            if key is None:
                raise ValueError(f"Key {key_id} not found")
        else:
            key = self._key_manager.get_default_key()

        # Encrypt content
        fernet = key.to_fernet()
        encrypted_bytes = fernet.encrypt(content.encode("utf-8"))
        encrypted_b64 = base64.urlsafe_b64encode(encrypted_bytes).decode("ascii")

        # Create hash for verification
        original_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # Create fold record
        fold = SegmentFold(
            fold_id=secrets.token_hex(16),
            start_pos=0,
            end_pos=len(content),
            fold_type="encrypted",
            original_hash=original_hash,
            pii_type=pii_type,
            encryption_key_id=key.key_id,
            can_unlock=True,
            unlock_conditions={
                "requires_key": True,
                "algorithm": key.algorithm,
            },
        )

        # Create placeholder with encrypted content reference
        # In production, store encrypted_b64 separately and reference by fold_id
        placeholder = f"[FOLDED:{fold.fold_id}:{encrypted_b64[:20]}...]"

        # Store encrypted content in fold for recovery
        fold.unlock_conditions["encrypted_content"] = encrypted_b64

        return placeholder, fold

    def unfold(
        self,
        fold: SegmentFold,
        encrypted_content: str | None = None,
    ) -> str | None:
        """Unfold (decrypt) a content segment.

        Args:
            fold: The SegmentFold record.
            encrypted_content: The encrypted content. If None, uses stored content.

        Returns:
            Decrypted content, or None if unfolding failed.
        """
        if not fold.can_unlock:
            logger.warning(f"Fold {fold.fold_id} cannot be unlocked")
            return None

        if fold.fold_type != "encrypted":
            logger.warning(f"Fold {fold.fold_id} is not encrypted (type: {fold.fold_type})")
            return None

        if fold.is_expired():
            logger.warning(f"Fold {fold.fold_id} has expired")
            return None

        # Get encrypted content
        if encrypted_content is None:
            encrypted_content = fold.unlock_conditions.get("encrypted_content")
            if encrypted_content is None:
                logger.error(f"No encrypted content for fold {fold.fold_id}")
                return None

        # Get decryption key
        key_id = fold.encryption_key_id
        if key_id is None:
            logger.error(f"No encryption key ID for fold {fold.fold_id}")
            return None

        key = self._key_manager.get_key(key_id)
        if key is None:
            logger.error(f"Encryption key {key_id} not found")
            return None

        try:
            # Decrypt
            fernet = key.to_fernet()
            encrypted_bytes = base64.urlsafe_b64decode(encrypted_content)
            decrypted = fernet.decrypt(encrypted_bytes).decode("utf-8")

            # Verify hash
            computed_hash = hashlib.sha256(decrypted.encode("utf-8")).hexdigest()
            if computed_hash != fold.original_hash:
                logger.error(f"Hash mismatch for fold {fold.fold_id}")
                return None

            return decrypted

        except Exception as e:
            logger.error(f"Failed to unfold {fold.fold_id}: {e}")
            return None

    def lock_fold(self, fold: SegmentFold) -> None:
        """Permanently lock a fold, preventing future unfolding.

        This is irreversible and used for compliance with erasure requests.

        Args:
            fold: The fold to lock.
        """
        fold.can_unlock = False
        # Remove encrypted content
        if "encrypted_content" in fold.unlock_conditions:
            del fold.unlock_conditions["encrypted_content"]

    def verify_fold(self, fold: SegmentFold, original_content: str) -> bool:
        """Verify that a fold matches original content.

        Args:
            fold: The fold to verify.
            original_content: The original content.

        Returns:
            True if hash matches, False otherwise.
        """
        computed_hash = hashlib.sha256(original_content.encode("utf-8")).hexdigest()
        return computed_hash == fold.original_hash


class SecureFoldStorage:
    """Secure storage for encrypted folds.

    In production, this should use a secure database with encryption at rest.
    """

    def __init__(self):
        """Initialize fold storage."""
        self._folds: dict[str, tuple[SegmentFold, str]] = {}  # fold_id -> (fold, encrypted_content)

    def store(self, fold: SegmentFold, encrypted_content: str) -> None:
        """Store a fold with its encrypted content.

        Args:
            fold: The SegmentFold record.
            encrypted_content: The encrypted content.
        """
        self._folds[fold.fold_id] = (fold, encrypted_content)

    def retrieve(self, fold_id: str) -> tuple[SegmentFold, str] | None:
        """Retrieve a fold and its encrypted content.

        Args:
            fold_id: The fold ID.

        Returns:
            Tuple of (fold, encrypted_content) or None if not found.
        """
        return self._folds.get(fold_id)

    def delete(self, fold_id: str) -> bool:
        """Delete a fold (for compliance erasure).

        Args:
            fold_id: The fold ID.

        Returns:
            True if deleted, False if not found.
        """
        if fold_id in self._folds:
            del self._folds[fold_id]
            return True
        return False

    def list_by_session(self, session_key: str) -> list[SegmentFold]:
        """List all folds for a session.

        Args:
            session_key: The session key.

        Returns:
            List of SegmentFold objects.
        """
        # In a real implementation, folds would be indexed by session
        return [fold for fold, _ in self._folds.values()]

    def cleanup_expired(self) -> int:
        """Remove expired folds.

        Returns:
            Number of folds removed.
        """
        expired = [
            fold_id
            for fold_id, (fold, _) in self._folds.items()
            if fold.is_expired()
        ]

        for fold_id in expired:
            del self._folds[fold_id]

        return len(expired)
