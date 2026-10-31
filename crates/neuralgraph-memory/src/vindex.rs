//! Disk-backed IVF vector index (opt-in, `RetrievalConfig::vector_index = "ivf"`).
//!
//! The exact vector channel keeps every embedding resident and scans all of them per query, so both resident
//! memory and query cost grow linearly with the bank. This index keeps only the coarse centroids and an entry ->
//! memory id table in memory; the normalised vectors live in one file, grouped by inverted list, and a query reads
//! only the `nprobe` lists nearest to it (one positional read per list). The vectors therefore sit in the OS page
//! cache, which the kernel can evict, instead of in process RSS.
//!
//! The file covers rows up to `max_rid`; rows written later are scanned exactly from memory by the retriever
//! (a small delta) until the index is rebuilt.
//!
//! File layout (little-endian): magic `NGIVF001`, u32 dim, u32 nlist, u64 n, i64 max_rid,
//! nlist*dim f32 centroids, (nlist+1) u64 list offsets (entry numbers), n*dim f32 vectors in list order.
//! Ids: `<path>.ids`, one memory id per line in entry order.

use crate::error::{MemoryError, Result};
use std::fs::File;
use std::io::{BufRead, BufReader, BufWriter, Read, Write};
use std::os::unix::fs::FileExt;

const MAGIC: &[u8; 8] = b"NGIVF001";

fn dot(a: &[f32], b: &[f32]) -> f32 {
    let mut acc = [0f32; 8];
    let chunks = a.len() / 8;
    for c in 0..chunks {
        for l in 0..8 {
            acc[l] += a[c * 8 + l] * b[c * 8 + l];
        }
    }
    let mut s = ((acc[0] + acc[1]) + (acc[2] + acc[3])) + ((acc[4] + acc[5]) + (acc[6] + acc[7]));
    for i in chunks * 8..a.len() {
        s += a[i] * b[i];
    }
    s
}

fn normalise(v: &mut [f32]) {
    let n = dot(v, v).sqrt() + 1e-9;
    v.iter_mut().for_each(|x| *x /= n);
}

fn other(e: impl std::fmt::Display) -> MemoryError {
    MemoryError::Other("IvfError", e.to_string())
}

/// Index of the nearest centroid (by dot product of normalised vectors).
fn nearest(centroids: &[f32], dim: usize, v: &[f32]) -> usize {
    let mut best = (0usize, f32::MIN);
    for (c, cen) in centroids.chunks_exact(dim).enumerate() {
        let s = dot(cen, v);
        if s > best.1 {
            best = (c, s);
        }
    }
    best.0
}

/// Build statistics returned by [`IvfIndex::build`].
#[derive(Debug, Clone, serde::Serialize)]
pub struct IvfBuildStats {
    pub n: u64,
    pub dim: usize,
    pub nlist: usize,
    pub train_sample: usize,
    pub iterations: usize,
    pub max_rid: i64,
    pub seconds_read: f64,
    pub seconds_train: f64,
    pub seconds_assign_write: f64,
    pub file_bytes: u64,
    pub largest_list: u64,
}

pub struct IvfIndex {
    pub dim: usize,
    pub nlist: usize,
    pub n: u64,
    pub max_rid: i64,
    centroids: Vec<f32>,
    offsets: Vec<u64>,
    /// memory id of each entry
    pub ids: Vec<String>,
    file: File,
    data_start: u64,
}

impl IvfIndex {
    /// Build from `(rid, memory_id, embedding f32 bytes)` rows of a single dimension, streamed by `rows`.
    /// Rows are spooled to a temporary file so the build itself never holds all vectors in memory.
    pub fn build(
        path: &str,
        dim: usize,
        nlist: usize,
        iterations: usize,
        rows: impl FnOnce(&mut dyn FnMut(i64, String, &[u8]) -> Result<()>) -> Result<()>,
    ) -> Result<IvfBuildStats> {
        let t0 = std::time::Instant::now();
        let spool_path = format!("{path}.spool");
        let mut spool = BufWriter::new(File::create(&spool_path)?);
        let mut ids: Vec<String> = Vec::new();
        let mut max_rid = 0i64;
        let mut buf = vec![0f32; dim];
        rows(&mut |rid, id, blob| {
            if blob.len() != dim * 4 {
                return Ok(()); // other dimensions stay on the exact path
            }
            for (j, c) in blob.chunks_exact(4).enumerate() {
                buf[j] = f32::from_le_bytes([c[0], c[1], c[2], c[3]]);
            }
            normalise(&mut buf);
            for x in &buf {
                spool.write_all(&x.to_le_bytes())?;
            }
            ids.push(id);
            max_rid = max_rid.max(rid);
            Ok(())
        })?;
        spool.flush()?;
        drop(spool);
        let n = ids.len();
        if n == 0 {
            let _ = std::fs::remove_file(&spool_path);
            return Err(other("no vectors of this dimension to index"));
        }
        let nlist = nlist.clamp(1, n);
        let seconds_read = t0.elapsed().as_secs_f64();
        let spool = File::open(&spool_path)?;
        let read_row = |i: usize, out: &mut [f32]| -> Result<()> {
            let mut b = vec![0u8; dim * 4];
            spool.read_exact_at(&mut b, (i * dim * 4) as u64)?;
            for (j, c) in b.chunks_exact(4).enumerate() {
                out[j] = f32::from_le_bytes([c[0], c[1], c[2], c[3]]);
            }
            Ok(())
        };

        // spherical k-means on an evenly strided sample (deterministic: no RNG)
        let t1 = std::time::Instant::now();
        let sample_n = (nlist * 32).min(n);
        let stride = n as f64 / sample_n as f64;
        let mut sample = vec![0f32; sample_n * dim];
        for s in 0..sample_n {
            read_row((s as f64 * stride) as usize, &mut sample[s * dim..(s + 1) * dim])?;
        }
        let mut centroids: Vec<f32> = (0..nlist)
            .flat_map(|c| sample[(c * sample_n / nlist) * dim..(c * sample_n / nlist + 1) * dim].to_vec())
            .collect();
        let threads = std::thread::available_parallelism().map(|t| t.get()).unwrap_or(1);
        let mut assign = vec![0usize; sample_n];
        for _ in 0..iterations {
            let chunk = sample_n.div_ceil(threads);
            std::thread::scope(|sc| {
                for (t, a) in assign.chunks_mut(chunk).enumerate() {
                    let (cen, smp) = (&centroids, &sample);
                    sc.spawn(move || {
                        for (k, slot) in a.iter_mut().enumerate() {
                            let s = t * chunk + k;
                            *slot = nearest(cen, dim, &smp[s * dim..(s + 1) * dim]);
                        }
                    });
                }
            });
            let mut sums = vec![0f32; nlist * dim];
            let mut counts = vec![0usize; nlist];
            for (s, &c) in assign.iter().enumerate() {
                counts[c] += 1;
                for j in 0..dim {
                    sums[c * dim + j] += sample[s * dim + j];
                }
            }
            for c in 0..nlist {
                let cen = &mut sums[c * dim..(c + 1) * dim];
                if counts[c] == 0 {
                    // re-seed an empty list from a deterministic sample point
                    let s = (c * 7919) % sample_n;
                    cen.copy_from_slice(&sample[s * dim..(s + 1) * dim]);
                }
                normalise(cen);
            }
            centroids = sums;
        }
        let seconds_train = t1.elapsed().as_secs_f64();

        // assign every vector, then write the vectors grouped by list
        let t2 = std::time::Instant::now();
        let mut list_of = vec![0u32; n];
        let chunk = n.div_ceil(threads);
        std::thread::scope(|sc| -> Result<()> {
            let hs: Vec<_> = list_of
                .chunks_mut(chunk)
                .enumerate()
                .map(|(t, out)| {
                    let (cen, read_row) = (&centroids, &read_row);
                    sc.spawn(move || -> Result<()> {
                        let mut v = vec![0f32; dim];
                        for (k, slot) in out.iter_mut().enumerate() {
                            read_row(t * chunk + k, &mut v)?;
                            *slot = nearest(cen, dim, &v) as u32;
                        }
                        Ok(())
                    })
                })
                .collect();
            for h in hs {
                h.join().map_err(|_| other("assign thread panicked"))??;
            }
            Ok(())
        })?;
        let mut counts = vec![0u64; nlist];
        for &l in &list_of {
            counts[l as usize] += 1;
        }
        let mut offsets = vec![0u64; nlist + 1];
        for c in 0..nlist {
            offsets[c + 1] = offsets[c] + counts[c];
        }
        let mut order: Vec<usize> = (0..n).collect();
        order.sort_by_key(|&i| (list_of[i], i)); // stable within a list: rid order
        let tmp_path = format!("{path}.tmp");
        let mut w = BufWriter::with_capacity(1 << 20, File::create(&tmp_path)?);
        w.write_all(MAGIC)?;
        w.write_all(&(dim as u32).to_le_bytes())?;
        w.write_all(&(nlist as u32).to_le_bytes())?;
        w.write_all(&(n as u64).to_le_bytes())?;
        w.write_all(&max_rid.to_le_bytes())?;
        for x in &centroids {
            w.write_all(&x.to_le_bytes())?;
        }
        for o in &offsets {
            w.write_all(&o.to_le_bytes())?;
        }
        let mut b = vec![0u8; dim * 4];
        for &i in &order {
            spool.read_exact_at(&mut b, (i * dim * 4) as u64)?;
            w.write_all(&b)?;
        }
        w.flush()?;
        drop(w);
        let mut idw = BufWriter::new(File::create(format!("{tmp_path}.ids"))?);
        for &i in &order {
            writeln!(idw, "{}", ids[i])?;
        }
        idw.flush()?;
        drop(idw);
        std::fs::rename(format!("{tmp_path}.ids"), format!("{path}.ids"))?;
        std::fs::rename(&tmp_path, path)?;
        let _ = std::fs::remove_file(&spool_path);
        Ok(IvfBuildStats {
            n: n as u64,
            dim,
            nlist,
            train_sample: sample_n,
            iterations,
            max_rid,
            seconds_read,
            seconds_train,
            seconds_assign_write: t2.elapsed().as_secs_f64(),
            file_bytes: std::fs::metadata(path)?.len(),
            largest_list: counts.iter().copied().max().unwrap_or(0),
        })
    }

    pub fn open(path: &str) -> Result<IvfIndex> {
        let mut f = File::open(path)?;
        let mut head = [0u8; 32];
        f.read_exact(&mut head)?;
        if &head[0..8] != MAGIC {
            return Err(other(format!("{path}: not an NGIVF001 file")));
        }
        let dim = u32::from_le_bytes(head[8..12].try_into().unwrap()) as usize;
        let nlist = u32::from_le_bytes(head[12..16].try_into().unwrap()) as usize;
        let n = u64::from_le_bytes(head[16..24].try_into().unwrap());
        let max_rid = i64::from_le_bytes(head[24..32].try_into().unwrap());
        let mut cb = vec![0u8; nlist * dim * 4];
        f.read_exact(&mut cb)?;
        let centroids = cb.chunks_exact(4).map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]])).collect();
        let mut ob = vec![0u8; (nlist + 1) * 8];
        f.read_exact(&mut ob)?;
        let offsets: Vec<u64> = ob.chunks_exact(8).map(|c| u64::from_le_bytes(c.try_into().unwrap())).collect();
        let ids: Vec<String> =
            BufReader::new(File::open(format!("{path}.ids"))?).lines().collect::<std::io::Result<_>>()?;
        if ids.len() as u64 != n || offsets.last().copied() != Some(n) {
            return Err(other(format!("{path}: ids/offsets do not match header")));
        }
        let data_start = 32 + (nlist * dim * 4) as u64 + ((nlist + 1) * 8) as u64;
        Ok(IvfIndex { dim, nlist, n, max_rid, centroids, offsets, ids, file: f, data_start })
    }

    /// Top `k` entries by cosine among the `nprobe` nearest lists, skipping entries `keep` rejects and those
    /// below `min_sim`. `q` must be normalised. Returns (entry, similarity), highest first.
    pub fn search(&self, q: &[f32], nprobe: usize, k: usize, min_sim: f32, keep: &dyn Fn(usize) -> bool) -> Result<Vec<(usize, f32)>> {
        let mut lists: Vec<(usize, f32)> =
            self.centroids.chunks_exact(self.dim).enumerate().map(|(c, cen)| (c, dot(cen, q))).collect();
        let nprobe = nprobe.clamp(1, self.nlist);
        lists.select_nth_unstable_by(nprobe - 1, |a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        lists.truncate(nprobe);
        lists.sort_by_key(|(c, _)| *c); // ascending file offset
        let mut hits: Vec<(usize, f32)> = Vec::new();
        let mut buf: Vec<u8> = Vec::new();
        let row = self.dim * 4;
        let mut v = vec![0f32; self.dim];
        for (c, _) in lists {
            let (lo, hi) = (self.offsets[c] as usize, self.offsets[c + 1] as usize);
            if hi == lo {
                continue;
            }
            buf.resize((hi - lo) * row, 0);
            self.file.read_exact_at(&mut buf, self.data_start + (lo * row) as u64)?;
            for (k_i, chunk) in buf.chunks_exact(row).enumerate() {
                let entry = lo + k_i;
                if !keep(entry) {
                    continue;
                }
                for (j, b) in chunk.chunks_exact(4).enumerate() {
                    v[j] = f32::from_le_bytes([b[0], b[1], b[2], b[3]]);
                }
                let s = dot(&v, q);
                if s >= min_sim && s.is_finite() {
                    hits.push((entry, s));
                }
            }
        }
        // highest similarity first; ties by entry number (list order, then rid order) for determinism
        hits.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal).then(a.0.cmp(&b.0)));
        hits.truncate(k);
        Ok(hits)
    }

    /// Vectors visited by a query at `nprobe`, averaged over lists (for cost reporting).
    pub fn mean_list_len(&self) -> f64 {
        self.n as f64 / self.nlist as f64
    }
}
