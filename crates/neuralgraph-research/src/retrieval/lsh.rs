//! Random-projection LSH for approximate cosine search (port of `retrieval/lsh.py`).
//!
//! Projections are drawn exactly as numpy drew them: `np.random.seed(42)` (re-seeding the global generator every
//! time an index initialises), then one `np.random.randn(num_hashes, dim)` per table in float64, rows normalised
//! with `norm(axis=1) + 1e-10`. Embeddings are stored as float32 unit vectors; hashing projects them in float64
//! (`float64 @ float32` promotes to float64), similarity is a float32 dot product. See [`crate::numpy`] for why
//! these match numpy bit for bit on macOS.

use crate::numpy::{dgemv, norm_f32, row_norms_f64, sdot, sgemv, to_f32};
use crate::pyrandom::NumpyRandomState;
use indexmap::{IndexMap, IndexSet};
use serde_json::{json, Value};

/// One hash bucket: (node id, unit float32 embedding) in insertion order.
type Bucket = Vec<(String, Vec<f32>)>;

/// LSH configuration.
#[derive(Clone, Debug)]
pub struct LshConfig {
    pub num_tables: usize,
    pub num_hashes: usize,
    pub embedding_dim: usize,
    pub seed: u32,
}

impl Default for LshConfig {
    fn default() -> Self {
        LshConfig { num_tables: 8, num_hashes: 12, embedding_dim: 768, seed: 42 }
    }
}

/// Hyperplane LSH index over float32 unit vectors.
#[derive(Clone, Debug)]
pub struct RandomProjectionLsh {
    config: LshConfig,
    initialized: bool,
    /// Per table: `num_hashes x dim` row-major float64 hyperplane normals.
    projections: Vec<Vec<f64>>,
    /// Per table: hash code -> bucket of (node id, unit embedding), in insertion order.
    tables: Vec<IndexMap<u64, Bucket>>,
    all_embeddings: IndexMap<String, Vec<f32>>,
    total_indexed: usize,
    total_queries: usize,
    total_candidates_examined: usize,
}

impl RandomProjectionLsh {
    pub fn new(config: LshConfig) -> Self {
        RandomProjectionLsh {
            config,
            initialized: false,
            projections: Vec::new(),
            tables: Vec::new(),
            all_embeddings: IndexMap::new(),
            total_indexed: 0,
            total_queries: 0,
            total_candidates_examined: 0,
        }
    }

    pub fn config(&self) -> &LshConfig {
        &self.config
    }

    fn initialize(&mut self, embedding_dim: usize) {
        if self.initialized {
            return;
        }
        self.config.embedding_dim = embedding_dim;
        let mut rng = NumpyRandomState::new(self.config.seed);
        for _ in 0..self.config.num_tables {
            let mut proj = rng.randn(self.config.num_hashes, embedding_dim);
            let norms = row_norms_f64(&proj, embedding_dim);
            for (row, n) in proj.chunks_mut(embedding_dim.max(1)).zip(norms) {
                let d = n + 1e-10;
                for v in row {
                    *v /= d;
                }
            }
            self.projections.push(proj);
            self.tables.push(IndexMap::new());
        }
        self.initialized = true;
    }

    /// The projection matrix of one table (row-major `num_hashes x dim`), for parity tests.
    pub fn projection(&self, table_idx: usize) -> Option<&[f64]> {
        self.projections.get(table_idx).map(Vec::as_slice)
    }

    fn compute_hash(&self, embedding: &[f32], table_idx: usize) -> u64 {
        let as_f64: Vec<f64> = embedding.iter().map(|v| *v as f64).collect();
        let projections = dgemv(&self.projections[table_idx], self.config.num_hashes, &as_f64);
        let mut code = 0u64;
        for (i, p) in projections.iter().enumerate() {
            if *p > 0.0 {
                code |= 1 << i;
            }
        }
        code
    }

    /// `np.array(embedding, dtype=float32)`, normalised when its norm exceeds 1e-10.
    fn unit(embedding: &[f64]) -> Vec<f32> {
        let mut e = to_f32(embedding);
        let norm = norm_f32(&e);
        if norm > 1e-10 {
            for v in &mut e {
                *v /= norm;
            }
        }
        e
    }

    /// Add an embedding.
    pub fn index(&mut self, node_id: &str, embedding: &[f64]) {
        if !self.initialized {
            self.initialize(embedding.len());
        }
        let e = Self::unit(embedding);
        self.all_embeddings.insert(node_id.to_string(), e.clone());
        for t in 0..self.config.num_tables {
            let code = self.compute_hash(&e, t);
            self.tables[t].entry(code).or_default().push((node_id.to_string(), e.clone()));
        }
        self.total_indexed += 1;
    }

    /// Remove an embedding; false when unknown.
    pub fn remove(&mut self, node_id: &str) -> bool {
        let Some(e) = self.all_embeddings.shift_remove(node_id) else { return false };
        for t in 0..self.config.num_tables {
            let code = self.compute_hash(&e, t);
            if let Some(bucket) = self.tables[t].get_mut(&code) {
                bucket.retain(|(nid, _)| nid != node_id);
                if bucket.is_empty() {
                    self.tables[t].shift_remove(&code);
                }
            }
        }
        true
    }

    fn rank(&mut self, query: &[f32], candidate_ids: IndexSet<String>, k: usize, threshold: f64) -> Vec<(String, f64)> {
        let mut results: Vec<(String, f64)> = Vec::new();
        for node_id in &candidate_ids {
            let e = &self.all_embeddings[node_id];
            let similarity = sdot(query, e) as f64;
            if similarity >= threshold {
                results.push((node_id.clone(), similarity));
            }
        }
        // Python sorted a set, whose iteration order is unspecified; equal similarities keep insertion order here.
        results.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        self.total_queries += 1;
        self.total_candidates_examined += candidate_ids.len();
        results.truncate(k);
        results
    }

    /// Approximate nearest neighbours from the query's bucket in every table.
    pub fn query(&mut self, query_embedding: &[f64], k: usize, similarity_threshold: f64) -> Vec<(String, f64)> {
        if !self.initialized {
            return Vec::new();
        }
        let q = Self::unit(query_embedding);
        let mut candidates = IndexSet::new();
        for t in 0..self.config.num_tables {
            let code = self.compute_hash(&q, t);
            if let Some(bucket) = self.tables[t].get(&code) {
                for (nid, _) in bucket {
                    candidates.insert(nid.clone());
                }
            }
        }
        self.rank(&q, candidates, k, similarity_threshold)
    }

    /// Multi-probe query: also the buckets one bit-flip away (first `num_probes` bits) in every table.
    pub fn query_with_probing(
        &mut self,
        query_embedding: &[f64],
        k: usize,
        similarity_threshold: f64,
        num_probes: usize,
    ) -> Vec<(String, f64)> {
        if !self.initialized {
            return Vec::new();
        }
        let q = Self::unit(query_embedding);
        let mut candidates = IndexSet::new();
        for t in 0..self.config.num_tables {
            let code = self.compute_hash(&q, t);
            if let Some(bucket) = self.tables[t].get(&code) {
                for (nid, _) in bucket {
                    candidates.insert(nid.clone());
                }
            }
            for probe in 0..num_probes.min(self.config.num_hashes) {
                let probed = code ^ (1 << probe);
                if let Some(bucket) = self.tables[t].get(&probed) {
                    for (nid, _) in bucket {
                        candidates.insert(nid.clone());
                    }
                }
            }
        }
        self.rank(&q, candidates, k, similarity_threshold)
    }

    pub fn get_stats(&self) -> Value {
        let total_bucket_entries: usize = self.tables.iter().map(|t| t.values().map(Vec::len).sum::<usize>()).sum();
        let avg_candidates = self.total_candidates_examined as f64 / self.total_queries.max(1) as f64;
        json!({
            "indexed": self.total_indexed,
            "queries": self.total_queries,
            "avg_candidates_per_query": avg_candidates,
            "num_tables": self.config.num_tables,
            "num_hashes": self.config.num_hashes,
            "embedding_dim": self.config.embedding_dim,
            "total_bucket_entries": total_bucket_entries,
            "reduction_factor": self.total_indexed as f64 / avg_candidates.max(1.0),
        })
    }

    pub fn clear(&mut self) {
        for t in &mut self.tables {
            t.clear();
        }
        self.all_embeddings.clear();
        self.total_indexed = 0;
        self.total_queries = 0;
        self.total_candidates_examined = 0;
    }
}

/// SimHash signatures for near-duplicate detection.
#[derive(Clone, Debug)]
pub struct SimHash {
    pub embedding_dim: usize,
    pub num_bits: usize,
    /// `num_bits x dim` row-major float32 projection (`randn(...).astype(np.float32)`).
    projection: Vec<f32>,
}

impl SimHash {
    pub fn new(embedding_dim: usize, num_bits: usize, seed: u32) -> Self {
        let mut rng = NumpyRandomState::new(seed);
        let projection = to_f32(&rng.randn(num_bits, embedding_dim));
        SimHash { embedding_dim, num_bits, projection }
    }

    /// Bit `i` set when the i-th projection is positive (`num_bits` up to 64).
    pub fn compute_signature(&self, embedding: &[f64]) -> u64 {
        let e = to_f32(embedding);
        let projections = sgemv(&self.projection, self.num_bits, &e);
        let mut sig = 0u64;
        for (i, p) in projections.iter().enumerate() {
            if *p > 0.0 {
                sig |= 1 << i;
            }
        }
        sig
    }

    pub fn hamming_distance(sig1: u64, sig2: u64) -> u32 {
        (sig1 ^ sig2).count_ones()
    }

    pub fn estimated_cosine_similarity(sig1: u64, sig2: u64, num_bits: usize) -> f64 {
        let hamming = Self::hamming_distance(sig1, sig2) as f64;
        (hamming / num_bits as f64 * std::f64::consts::PI).cos()
    }

    /// Near-duplicate pairs among `embeddings` (adjacent signatures, then a window of 10).
    pub fn find_near_duplicates(
        &self,
        embeddings: &IndexMap<String, Vec<f64>>,
        threshold: f64,
    ) -> Vec<(String, String, f64)> {
        let mut signatures: Vec<(String, u64)> =
            embeddings.iter().map(|(id, e)| (id.clone(), self.compute_signature(e))).collect();
        signatures.sort_by_key(|(_, s)| *s);
        let max_hamming = ((1.0 - threshold) * self.num_bits as f64 / 2.0) as u32;
        let mut duplicates: Vec<(String, String, f64)> = Vec::new();
        for i in 0..signatures.len().saturating_sub(1) {
            let (id1, s1) = &signatures[i];
            let (id2, s2) = &signatures[i + 1];
            if Self::hamming_distance(*s1, *s2) <= max_hamming {
                let sim = Self::estimated_cosine_similarity(*s1, *s2, self.num_bits);
                if sim >= threshold {
                    duplicates.push((id1.clone(), id2.clone(), sim));
                }
            }
        }
        let window = signatures.len().min(10);
        for i in 0..signatures.len() {
            for j in i + 2..(i + window).min(signatures.len()) {
                let (id1, s1) = &signatures[i];
                let (id2, s2) = &signatures[j];
                if Self::hamming_distance(*s1, *s2) <= max_hamming {
                    let sim = Self::estimated_cosine_similarity(*s1, *s2, self.num_bits);
                    if sim >= threshold {
                        let pair = (id1.min(id2).clone(), id1.max(id2).clone(), sim);
                        if !duplicates.contains(&pair) {
                            duplicates.push(pair);
                        }
                    }
                }
            }
        }
        duplicates
    }
}
