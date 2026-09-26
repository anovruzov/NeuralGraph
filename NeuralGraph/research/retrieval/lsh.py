"""Locality-Sensitive Hashing (LSH) for approximate nearest neighbor search.

This module implements Random Projection LSH for cosine similarity,
enabling O(log n) retrieval instead of O(n) brute-force search.

Key insight: The neural memory graph suffers from O(n·d) vector search
at every retrieval. With LSH, we can:
1. Hash embeddings to buckets during ingestion (one-time cost)
2. Query by hashing and bucket lookup (O(1) per bucket)
3. Only compute full similarity for candidates in matching buckets

This transforms retrieval from O(n·d) to O(k·d) where k << n.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class LSHConfig:
    """Configuration for LSH index."""

    # Number of hash tables (more = higher recall, more memory)
    num_tables: int = 8

    # Number of hash functions per table (more = higher precision, lower recall)
    num_hashes: int = 12

    # Embedding dimension (set automatically from first embedding)
    embedding_dim: int = 768

    # Random seed for reproducibility
    seed: int = 42


class RandomProjectionLSH:
    """Locality-Sensitive Hashing using Random Projections.

    For cosine similarity, we use hyperplane LSH:
    - Each hash function is a random hyperplane through the origin
    - Hash value is 1 if point is above plane, 0 if below
    - Points with similar directions hash to same bucket with high probability

    Probability of collision for vectors with angle θ:
    P(h(u) = h(v)) = 1 - θ/π

    Using k hash functions, probability of at least one collision across L tables:
    P(collision) = 1 - (1 - (1 - θ/π)^k)^L
    """

    def __init__(self, config: LSHConfig | None = None):
        """Initialize LSH index.

        Args:
            config: LSH configuration
        """
        self._config = config or LSHConfig()
        self._initialized = False

        # Projection matrices: L tables × k hashes × d dimensions
        self._projections: list[np.ndarray] = []

        # Hash tables: table_idx -> hash_code -> list of (node_id, embedding)
        self._tables: list[dict[int, list[tuple[str, np.ndarray]]]] = []

        # All indexed embeddings for fallback full search
        self._all_embeddings: dict[str, np.ndarray] = {}

        # Statistics
        self._total_indexed = 0
        self._total_queries = 0
        self._total_candidates_examined = 0

    def _initialize(self, embedding_dim: int) -> None:
        """Initialize projection matrices for given embedding dimension."""
        if self._initialized:
            return

        self._config.embedding_dim = embedding_dim

        # Set random seed for reproducibility
        np.random.seed(self._config.seed)

        # Create random projection matrices
        # Each table has k random hyperplanes (rows of projection matrix)
        for _ in range(self._config.num_tables):
            # Random unit vectors for hyperplane normals
            proj = np.random.randn(self._config.num_hashes, embedding_dim)
            # Normalize rows (not strictly necessary but helps numerically)
            norms = np.linalg.norm(proj, axis=1, keepdims=True)
            proj = proj / (norms + 1e-10)
            self._projections.append(proj)
            self._tables.append({})

        self._initialized = True
        logger.info(
            f"LSH initialized: {self._config.num_tables} tables × "
            f"{self._config.num_hashes} hashes, dim={embedding_dim}"
        )

    def _compute_hash(self, embedding: np.ndarray, table_idx: int) -> int:
        """Compute hash code for embedding in given table.

        Args:
            embedding: Normalized embedding vector
            table_idx: Which hash table to use

        Returns:
            Integer hash code (k-bit binary number)
        """
        # Project embedding onto hyperplane normals
        projections = self._projections[table_idx] @ embedding

        # Sign of projection determines which side of hyperplane
        # Convert to binary hash code
        bits = (projections > 0).astype(int)

        # Pack bits into single integer
        hash_code = 0
        for i, bit in enumerate(bits):
            hash_code |= (bit << i)

        return hash_code

    def index(self, node_id: str, embedding: list[float] | np.ndarray) -> None:
        """Add embedding to LSH index.

        Args:
            node_id: Unique identifier for this embedding
            embedding: Vector to index (will be normalized)
        """
        # Convert to numpy array
        if not isinstance(embedding, np.ndarray):
            embedding = np.array(embedding, dtype=np.float32)

        # Initialize on first embedding
        if not self._initialized:
            self._initialize(len(embedding))

        # Normalize embedding (LSH for cosine similarity works on unit vectors)
        norm = np.linalg.norm(embedding)
        if norm > 1e-10:
            embedding = embedding / norm

        # Store in all_embeddings for fallback
        self._all_embeddings[node_id] = embedding

        # Hash into each table
        for table_idx in range(self._config.num_tables):
            hash_code = self._compute_hash(embedding, table_idx)

            if hash_code not in self._tables[table_idx]:
                self._tables[table_idx][hash_code] = []

            self._tables[table_idx][hash_code].append((node_id, embedding))

        self._total_indexed += 1

    def remove(self, node_id: str) -> bool:
        """Remove embedding from LSH index.

        Args:
            node_id: ID of embedding to remove

        Returns:
            True if found and removed
        """
        if node_id not in self._all_embeddings:
            return False

        embedding = self._all_embeddings.pop(node_id)

        # Remove from all tables
        for table_idx in range(self._config.num_tables):
            hash_code = self._compute_hash(embedding, table_idx)

            if hash_code in self._tables[table_idx]:
                self._tables[table_idx][hash_code] = [
                    (nid, emb) for nid, emb in self._tables[table_idx][hash_code]
                    if nid != node_id
                ]
                # Clean up empty buckets
                if not self._tables[table_idx][hash_code]:
                    del self._tables[table_idx][hash_code]

        return True

    def query(
        self,
        query_embedding: list[float] | np.ndarray,
        k: int = 20,
        similarity_threshold: float = 0.0
    ) -> list[tuple[str, float]]:
        """Find approximate nearest neighbors.

        Args:
            query_embedding: Query vector
            k: Number of neighbors to return
            similarity_threshold: Minimum cosine similarity

        Returns:
            List of (node_id, similarity) tuples, sorted by similarity descending
        """
        if not self._initialized:
            return []

        # Convert to numpy array
        if not isinstance(query_embedding, np.ndarray):
            query_embedding = np.array(query_embedding, dtype=np.float32)

        # Normalize query
        norm = np.linalg.norm(query_embedding)
        if norm > 1e-10:
            query_embedding = query_embedding / norm

        # Collect candidates from all tables
        candidate_ids: set[str] = set()

        for table_idx in range(self._config.num_tables):
            hash_code = self._compute_hash(query_embedding, table_idx)

            if hash_code in self._tables[table_idx]:
                for node_id, _ in self._tables[table_idx][hash_code]:
                    candidate_ids.add(node_id)

        # Compute exact similarity for candidates only
        results: list[tuple[str, float]] = []

        for node_id in candidate_ids:
            embedding = self._all_embeddings[node_id]
            # Cosine similarity (embeddings are already normalized)
            similarity = float(np.dot(query_embedding, embedding))

            if similarity >= similarity_threshold:
                results.append((node_id, similarity))

        # Sort by similarity descending
        results.sort(key=lambda x: x[1], reverse=True)

        # Update statistics
        self._total_queries += 1
        self._total_candidates_examined += len(candidate_ids)

        return results[:k]

    def query_with_probing(
        self,
        query_embedding: list[float] | np.ndarray,
        k: int = 20,
        similarity_threshold: float = 0.0,
        num_probes: int = 2
    ) -> list[tuple[str, float]]:
        """Find nearest neighbors with multi-probe LSH.

        Multi-probe examines nearby buckets (flipping bits) for higher recall.

        Args:
            query_embedding: Query vector
            k: Number of neighbors to return
            similarity_threshold: Minimum cosine similarity
            num_probes: Number of nearby buckets to examine per table

        Returns:
            List of (node_id, similarity) tuples
        """
        if not self._initialized:
            return []

        # Convert to numpy array
        if not isinstance(query_embedding, np.ndarray):
            query_embedding = np.array(query_embedding, dtype=np.float32)

        # Normalize query
        norm = np.linalg.norm(query_embedding)
        if norm > 1e-10:
            query_embedding = query_embedding / norm

        # Collect candidates from all tables with probing
        candidate_ids: set[str] = set()

        for table_idx in range(self._config.num_tables):
            hash_code = self._compute_hash(query_embedding, table_idx)

            # Examine primary bucket
            if hash_code in self._tables[table_idx]:
                for node_id, _ in self._tables[table_idx][hash_code]:
                    candidate_ids.add(node_id)

            # Examine nearby buckets (flip one bit at a time)
            for probe in range(min(num_probes, self._config.num_hashes)):
                probed_hash = hash_code ^ (1 << probe)  # Flip bit

                if probed_hash in self._tables[table_idx]:
                    for node_id, _ in self._tables[table_idx][probed_hash]:
                        candidate_ids.add(node_id)

        # Compute exact similarity for candidates
        results: list[tuple[str, float]] = []

        for node_id in candidate_ids:
            embedding = self._all_embeddings[node_id]
            similarity = float(np.dot(query_embedding, embedding))

            if similarity >= similarity_threshold:
                results.append((node_id, similarity))

        results.sort(key=lambda x: x[1], reverse=True)

        self._total_queries += 1
        self._total_candidates_examined += len(candidate_ids)

        return results[:k]

    def get_stats(self) -> dict[str, Any]:
        """Get index statistics."""
        total_bucket_entries = sum(
            sum(len(bucket) for bucket in table.values())
            for table in self._tables
        )

        avg_candidates = (
            self._total_candidates_examined / max(1, self._total_queries)
        )

        return {
            "indexed": self._total_indexed,
            "queries": self._total_queries,
            "avg_candidates_per_query": avg_candidates,
            "num_tables": self._config.num_tables,
            "num_hashes": self._config.num_hashes,
            "embedding_dim": self._config.embedding_dim,
            "total_bucket_entries": total_bucket_entries,
            "reduction_factor": self._total_indexed / max(1, avg_candidates),
        }

    def clear(self) -> None:
        """Clear all indexed data."""
        for table in self._tables:
            table.clear()
        self._all_embeddings.clear()
        self._total_indexed = 0
        self._total_queries = 0
        self._total_candidates_examined = 0


class SimHash:
    """SimHash for approximate duplicate detection.

    SimHash (Charikar's algorithm) creates compact 64-bit signatures
    where Hamming distance approximates cosine distance.

    Key property: P(sign(u·r) = sign(v·r)) = 1 - arccos(u·v)/π

    This enables O(n log n) duplicate detection instead of O(n²).
    """

    def __init__(self, embedding_dim: int = 768, num_bits: int = 64, seed: int = 42):
        """Initialize SimHash.

        Args:
            embedding_dim: Dimension of input embeddings
            num_bits: Number of bits in signature (64 or 128)
            seed: Random seed
        """
        self.embedding_dim = embedding_dim
        self.num_bits = num_bits

        # Random projection matrix: num_bits × embedding_dim
        np.random.seed(seed)
        self._projection = np.random.randn(num_bits, embedding_dim).astype(np.float32)

    def compute_signature(self, embedding: list[float] | np.ndarray) -> int:
        """Compute SimHash signature for embedding.

        Args:
            embedding: Input vector

        Returns:
            64-bit integer signature
        """
        if not isinstance(embedding, np.ndarray):
            embedding = np.array(embedding, dtype=np.float32)

        # Project and take sign
        projections = self._projection @ embedding
        bits = (projections > 0).astype(np.uint64)

        # Pack into single integer
        signature = np.uint64(0)
        for i, bit in enumerate(bits):
            signature |= (bit << i)

        return int(signature)

    @staticmethod
    def hamming_distance(sig1: int, sig2: int) -> int:
        """Compute Hamming distance between signatures."""
        xor = sig1 ^ sig2
        return bin(xor).count('1')

    @staticmethod
    def estimated_cosine_similarity(sig1: int, sig2: int, num_bits: int = 64) -> float:
        """Estimate cosine similarity from SimHash signatures.

        Args:
            sig1, sig2: SimHash signatures
            num_bits: Number of bits in signatures

        Returns:
            Estimated cosine similarity
        """
        hamming = SimHash.hamming_distance(sig1, sig2)
        # Convert Hamming distance to angle estimate
        angle_estimate = (hamming / num_bits) * math.pi
        return math.cos(angle_estimate)

    def find_near_duplicates(
        self,
        embeddings: dict[str, list[float] | np.ndarray],
        threshold: float = 0.9
    ) -> list[tuple[str, str, float]]:
        """Find near-duplicate pairs efficiently.

        Args:
            embeddings: Dict of node_id -> embedding
            threshold: Minimum cosine similarity to consider duplicate

        Returns:
            List of (id1, id2, similarity) tuples
        """
        # Compute signatures for all embeddings
        signatures: list[tuple[str, int]] = []
        for node_id, embedding in embeddings.items():
            sig = self.compute_signature(embedding)
            signatures.append((node_id, sig))

        # Sort by signature for efficient comparison
        signatures.sort(key=lambda x: x[1])

        # Convert threshold to max Hamming distance
        # cos(θ) ≈ 1 - 2*(hamming/num_bits) for small angles
        max_hamming = int((1 - threshold) * self.num_bits / 2)

        duplicates: list[tuple[str, str, float]] = []

        # Compare adjacent signatures (most likely to be similar)
        for i in range(len(signatures) - 1):
            id1, sig1 = signatures[i]
            id2, sig2 = signatures[i + 1]

            hamming = self.hamming_distance(sig1, sig2)
            if hamming <= max_hamming:
                sim = self.estimated_cosine_similarity(sig1, sig2, self.num_bits)
                if sim >= threshold:
                    duplicates.append((id1, id2, sim))

        # Also check within a window for more recall
        window_size = min(10, len(signatures))
        for i in range(len(signatures)):
            for j in range(i + 2, min(i + window_size, len(signatures))):
                id1, sig1 = signatures[i]
                id2, sig2 = signatures[j]

                hamming = self.hamming_distance(sig1, sig2)
                if hamming <= max_hamming:
                    sim = self.estimated_cosine_similarity(sig1, sig2, self.num_bits)
                    if sim >= threshold:
                        # Avoid duplicates
                        pair = (min(id1, id2), max(id1, id2), sim)
                        if pair not in duplicates:
                            duplicates.append(pair)

        return duplicates


# Export for easy access
__all__ = ["LSHConfig", "RandomProjectionLSH", "SimHash"]
