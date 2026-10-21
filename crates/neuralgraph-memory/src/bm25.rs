//! Okapi BM25 as implemented by `rank_bm25.BM25Okapi` (k1=1.5, b=0.75, epsilon=0.25).
//!
//! Only used when the SQLite build lacks FTS5 (the bundled SQLite always has it, so this is the safety net for a
//! system SQLite). Same arithmetic, same evaluation order, so scores are bit-identical to the Python fallback.

use indexmap::IndexMap;

pub struct Bm25Okapi {
    pub doc_freqs: Vec<IndexMap<String, f64>>,
    doc_len: Vec<f64>,
    avgdl: f64,
    idf: IndexMap<String, f64>,
    k1: f64,
    b: f64,
}

impl Bm25Okapi {
    /// `None` when the corpus is empty or has no tokens at all (rank_bm25 raises in that case).
    pub fn new(corpus: &[Vec<String>]) -> Option<Self> {
        if corpus.is_empty() {
            return None;
        }
        let mut nd: IndexMap<String, f64> = IndexMap::new();
        let mut num_doc = 0usize;
        let mut doc_freqs = Vec::with_capacity(corpus.len());
        let mut doc_len = Vec::with_capacity(corpus.len());
        for document in corpus {
            doc_len.push(document.len() as f64);
            num_doc += document.len();
            let mut freqs: IndexMap<String, f64> = IndexMap::new();
            for word in document {
                *freqs.entry(word.clone()).or_insert(0.0) += 1.0;
            }
            for word in freqs.keys() {
                *nd.entry(word.clone()).or_insert(0.0) += 1.0;
            }
            doc_freqs.push(freqs);
        }
        if nd.is_empty() {
            return None;
        }
        let corpus_size = corpus.len() as f64;
        let avgdl = num_doc as f64 / corpus_size;
        let mut idf: IndexMap<String, f64> = IndexMap::new();
        let mut idf_sum = 0.0;
        let mut negative: Vec<String> = Vec::new();
        for (word, freq) in &nd {
            let v = (corpus_size - freq + 0.5).ln() - (freq + 0.5).ln();
            idf.insert(word.clone(), v);
            idf_sum += v;
            if v < 0.0 {
                negative.push(word.clone());
            }
        }
        let average_idf = idf_sum / idf.len() as f64;
        let eps = 0.25 * average_idf;
        for w in negative {
            idf.insert(w, eps);
        }
        Some(Bm25Okapi { doc_freqs, doc_len, avgdl, idf, k1: 1.5, b: 0.75 })
    }

    pub fn get_scores(&self, query: &[String]) -> Vec<f64> {
        let mut score = vec![0.0f64; self.doc_freqs.len()];
        for q in query {
            let idf = self.idf.get(q).copied().unwrap_or(0.0);
            for (i, doc) in self.doc_freqs.iter().enumerate() {
                let qf = doc.get(q).copied().unwrap_or(0.0);
                let dl = self.doc_len[i];
                score[i] += idf * (qf * (self.k1 + 1.0) / (qf + self.k1 * (1.0 - self.b + self.b * dl / self.avgdl)));
            }
        }
        score
    }
}
