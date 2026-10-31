//! o100m: build memory banks through the NeuralGraph store and measure them through the NeuralGraph retriever.
//!
//! Every subcommand writes raw artifacts (JSON / JSONL) next to a summary; the summary is the Retriever's own
//! reading and is never trusted by the Critic, which recomputes everything from the raw files.

mod corpus;
mod sys;

use blake2::digest::consts::U32;
use blake2::{Blake2b, Digest};
use clap::{Parser, Subcommand};
use corpus::{day_iso, Corpus, Kind, Query};
use neuralgraph_memory::{ChatMemoryStore, FakeLlmClient, LlmClient, Memory, MemoryRetriever, RetrievalConfig, SearchOptions};
use serde_json::{json, Value};
use std::fs::File;
use std::io::{BufRead, BufReader, BufWriter, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Instant;

#[derive(Parser)]
#[command(about = "Operation 100M benchmark harness over the NeuralGraph memory engine")]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Write record and query texts as JSONL (input for an external embedder).
    Texts {
        #[arg(long, default_value_t = 0)]
        bank: u32,
        #[arg(long)]
        n: u64,
        #[arg(long, default_value_t = 20261031)]
        seed: u64,
        #[arg(long, default_value_t = 2000)]
        nq: u64,
        #[arg(long)]
        out_dir: PathBuf,
    },
    /// Generate queries with the generator's ground truth.
    Queries {
        #[arg(long, default_value_t = 0)]
        bank: u32,
        #[arg(long)]
        n: u64,
        #[arg(long, default_value_t = 20261031)]
        seed: u64,
        #[arg(long, default_value_t = 2000)]
        nq: u64,
        #[arg(long)]
        out: PathBuf,
    },
    /// Build one bank through ChatMemoryStore (bulk transactions).
    Build {
        #[arg(long, default_value_t = 0)]
        bank: u32,
        #[arg(long)]
        n: u64,
        #[arg(long, default_value_t = 20261031)]
        seed: u64,
        #[arg(long)]
        db: PathBuf,
        /// `synthetic:<dim>` or `file:<path to n x dim little-endian f32>`
        #[arg(long)]
        emb: String,
        #[arg(long, default_value_t = 10_000)]
        batch: usize,
        #[arg(long)]
        out: PathBuf,
    },
    /// Build the disk IVF vector index next to the bank (`<db>.ivf`).
    BuildIvf {
        #[arg(long)]
        db: PathBuf,
        /// inverted lists; 0 = 4 * sqrt(n)
        #[arg(long, default_value_t = 0)]
        nlist: usize,
        #[arg(long, default_value_t = 10)]
        iterations: usize,
        #[arg(long)]
        out: PathBuf,
    },
    /// Attribute per-query cost to search stages.
    Profile {
        #[arg(long, default_value_t = 0)]
        bank: u32,
        #[arg(long)]
        n: u64,
        #[arg(long, default_value_t = 20261031)]
        seed: u64,
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        queries: PathBuf,
        #[arg(long)]
        qvec: String,
        #[arg(long, default_value_t = 300)]
        limit: usize,
        #[arg(long)]
        out: PathBuf,
    },
    /// Cold load, warm serial pass and concurrency sweep through MemoryRetriever::search.
    Run {
        #[arg(long, default_value_t = 0)]
        bank: u32,
        #[arg(long)]
        n: u64,
        #[arg(long, default_value_t = 20261031)]
        seed: u64,
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        queries: PathBuf,
        /// `synthetic:<dim>` or `file:<path to nq x dim f32>`
        #[arg(long)]
        qvec: String,
        #[arg(long, default_value = "1,2,4,8")]
        concurrency: String,
        /// passes over the query set per concurrency level
        #[arg(long, default_value_t = 1)]
        passes: usize,
        #[arg(long, default_value_t = 10)]
        k: usize,
        #[arg(long)]
        out_dir: PathBuf,
    },
}

fn file_size(p: &Path) -> u64 {
    std::fs::metadata(p).map(|m| m.len()).unwrap_or(0)
}

fn db_bytes(db: &Path) -> Value {
    let s = db.to_string_lossy();
    let main = file_size(db);
    let wal = file_size(Path::new(&format!("{s}-wal")));
    let shm = file_size(Path::new(&format!("{s}-shm")));
    json!({"db": main, "wal": wal, "shm": shm, "total": main + wal + shm})
}

enum Vectors {
    Synthetic(usize),
    File { dim: usize, data: Vec<f32> },
}

impl Vectors {
    fn parse(spec: &str, rows: u64) -> Vectors {
        if let Some(d) = spec.strip_prefix("synthetic:") {
            return Vectors::Synthetic(d.parse().expect("synthetic:<dim>"));
        }
        let path = spec.strip_prefix("file:").expect("emb must be synthetic:<dim> or file:<path>");
        let mut bytes = Vec::new();
        File::open(path).expect("vector file").read_to_end(&mut bytes).expect("read vectors");
        let data: Vec<f32> = bytes.chunks_exact(4).map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]])).collect();
        assert!(rows > 0 && data.len() as u64 % rows == 0, "vector file is not rows x dim");
        Vectors::File { dim: data.len() / rows as usize, data }
    }

    fn dim(&self) -> usize {
        match self {
            Vectors::Synthetic(d) => *d,
            Vectors::File { dim, .. } => *dim,
        }
    }

    fn label(&self) -> &'static str {
        match self {
            Vectors::Synthetic(_) => "synthetic-latent",
            Vectors::File { .. } => "file",
        }
    }
}

fn to_f64(v: &[f32]) -> Vec<f64> {
    v.iter().map(|x| *x as f64).collect()
}

fn engine_memory(c: &Corpus, r: &corpus::Record, emb: Vec<f64>, base: &str) -> Memory {
    let t = day_iso(base, r.observed_day);
    Memory {
        memory_id: r.id.clone(),
        text: r.text.clone(),
        kind: "fact".into(),
        subject: c.entity_id(r.entity),
        subject_name: c.entity_name(r.entity),
        speaker: r.speaker.into(),
        chat_id: format!("b{:02}c{:06}", c.bank, r.chat),
        importance: r.importance,
        confidence: 0.9,
        event_time: None,
        event_time_precision: "none".into(),
        observed_at: t.clone(),
        created_at: t.clone(),
        updated_at: t,
        // every memory is written active, as the extraction worker writes it; superseded versions are then retired
        // through the store's own supersede path (status + `supersedes` link), see `cmd_build`
        status: "active".into(),
        superseded_by: None,
        version: if r.kind == Kind::Update { 2 } else { 1 },
        text_hash: String::new(),
        embedding: Some(emb),
        access_count: 0,
        last_accessed_at: None,
        metadata: json!({"o100m_kind": r.kind.as_str()}),
        source_message_ids: Vec::new(),
        entity_ids: Vec::new(),
    }
}

fn query_json(q: &Query, bank: u32) -> Value {
    json!({"qid": q.qid, "bank": bank, "type": q.qtype, "text": q.text, "slot": q.slot, "entity": q.entity,
           "attr": q.attr, "include_superseded": q.include_superseded, "relevant": q.relevant,
           "expected_values": q.expected_values})
}

fn cmd_texts(bank: u32, n: u64, seed: u64, nq: u64, out_dir: &Path) {
    std::fs::create_dir_all(out_dir).unwrap();
    let c = Corpus::new(seed, bank, n);
    let mut w = BufWriter::new(File::create(out_dir.join("records.jsonl")).unwrap());
    for i in 0..n {
        let r = c.record(i);
        writeln!(w, "{}", json!({"i": i, "id": r.id, "text": r.text})).unwrap();
    }
    let mut w = BufWriter::new(File::create(out_dir.join("queries_text.jsonl")).unwrap());
    for q in c.queries(nq) {
        writeln!(w, "{}", json!({"qid": q.qid, "text": q.text})).unwrap();
    }
}

fn cmd_queries(bank: u32, n: u64, seed: u64, nq: u64, out: &Path) {
    let c = Corpus::new(seed, bank, n);
    let mut w = BufWriter::new(File::create(out).unwrap());
    for q in c.queries(nq) {
        writeln!(w, "{}", query_json(&q, bank)).unwrap();
    }
}

fn cmd_build(bank: u32, n: u64, seed: u64, db: &Path, emb: &str, batch: usize, out: &Path) {
    let c = Corpus::new(seed, bank, n);
    let vecs = Vectors::parse(emb, n);
    let dim = vecs.dim();
    let base = c.spec.time_base.clone();
    let _ = std::fs::remove_file(db);
    let store = ChatMemoryStore::open(&db.to_string_lossy()).expect("open store");
    let t0 = Instant::now();
    let cpu0 = sys::cpu_seconds();

    // entity registry (what the extraction worker would have built), with mention counts
    let mut mentions = vec![0i64; c.layout.entities as usize];
    for i in 0..n {
        mentions[c.record(i).entity as usize] += 1;
    }
    let ents: Vec<_> = (0..c.layout.entities)
        .map(|e| (c.entity_id(e), c.entity_name(e), "person".to_string(), Some(day_iso(&base, 0)), mentions[e as usize], Vec::new()))
        .collect();
    for chunk in ents.chunks(batch) {
        store.upsert_entities_bulk(chunk).expect("entities");
    }
    let t_entities = t0.elapsed().as_secs_f64();

    let mut text_hash = Blake2b::<U32>::new();
    let mut vec_hash = Blake2b::<U32>::new();
    let mut kinds = std::collections::BTreeMap::<&str, u64>::new();
    let mut text_bytes = 0u64;
    let mut items = Vec::with_capacity(batch);
    let mut gen_s = 0.0;
    let mut insert_s = 0.0;
    for i in 0..n {
        let tg = Instant::now();
        let r = c.record(i);
        let v: Vec<f32> = match &vecs {
            Vectors::Synthetic(d) => c.synthetic_vector(&r, *d),
            Vectors::File { dim, data } => data[i as usize * dim..(i as usize + 1) * dim].to_vec(),
        };
        text_hash.update(
            format!("{}\t{}\t{}\t{}\t{}\n", r.id, r.status, r.superseded_by.as_deref().unwrap_or(""), day_iso(&base, r.observed_day), r.text)
                .as_bytes(),
        );
        for x in &v {
            vec_hash.update(x.to_le_bytes());
        }
        *kinds.entry(r.kind.as_str()).or_default() += 1;
        text_bytes += r.text.len() as u64;
        let ent = c.entity_id(r.entity);
        items.push((engine_memory(&c, &r, to_f64(&v), &base), Vec::new(), vec![ent]));
        gen_s += tg.elapsed().as_secs_f64();
        if items.len() == batch || i + 1 == n {
            let ti = Instant::now();
            store.insert_memories_bulk(&items).expect("insert");
            insert_s += ti.elapsed().as_secs_f64();
            items.clear();
        }
    }
    let ts = Instant::now();
    let pairs: Vec<(String, String, String)> = (0..c.layout.updates)
        .map(|s| (corpus::memory_id(bank, s), corpus::memory_id(bank, c.layout.update_index(s)), "supersedes".to_string()))
        .collect();
    for chunk in pairs.chunks(batch) {
        store.supersede_bulk(chunk).expect("supersede");
    }
    let supersede_s = ts.elapsed().as_secs_f64();
    insert_s += supersede_s;
    let t_load = t0.elapsed().as_secs_f64();
    // checkpoint so the on-disk size is the steady-state size
    store.with_conn(|c| Ok(c.execute_batch("PRAGMA wal_checkpoint(TRUNCATE)")?)).expect("checkpoint");
    let manifest = json!({
        "bank": bank, "n": n, "seed": seed, "spec_version": c.spec.version,
        "layout": {"slots": c.layout.slots, "updates": c.layout.updates, "duplicates": c.layout.dups,
                    "contradictions": c.layout.contras, "distractors": c.layout.distractors, "entities": c.layout.entities},
        "kinds": kinds, "embedding": {"source": vecs.label(), "spec": emb, "dim": dim},
        "dataset_text_blake2b256": hex(&text_hash.finalize()), "dataset_vec_blake2b256": hex(&vec_hash.finalize()),
        "text_bytes": text_bytes,
        "timing_s": {"entities": t_entities, "total": t_load, "generate": gen_s, "engine_insert": insert_s, "engine_supersede": supersede_s},
        "ingest_memories_per_s_engine_insert": n as f64 / insert_s,
        "ingest_memories_per_s_wall": n as f64 / t_load,
        "cpu_s": sys::cpu_seconds() - cpu0, "peak_rss_bytes": sys::peak_rss_bytes(),
        "disk_bytes": db_bytes(db), "batch": batch, "env": sys::env(),
    });
    std::fs::write(out, serde_json::to_string_pretty(&manifest).unwrap()).unwrap();
    println!("{}", serde_json::to_string_pretty(&manifest).unwrap());
}


/// Engine configuration for this process: shipped defaults, overridden by O100M_* variables (the Planner's variants).
fn engine_config() -> (RetrievalConfig, bool, f64) {
    let mut cfg = RetrievalConfig::default();
    if let Ok(v) = std::env::var("O100M_VECTOR_INDEX") {
        cfg.vector_index = v;
    }
    if let Ok(v) = std::env::var("O100M_NPROBE") {
        cfg.ivf_nprobe = v.parse().expect("O100M_NPROBE");
    }
    if let Ok(v) = std::env::var("O100M_IVF_PATH") {
        cfg.ivf_path = Some(v);
    }
    let pool = std::env::var("O100M_READ_POOL").is_ok_and(|v| v == "1");
    let max_df = std::env::var("O100M_KEYWORD_MAX_DF").map(|v| v.parse().expect("O100M_KEYWORD_MAX_DF")).unwrap_or(0.0);
    (cfg, pool, max_df)
}

fn open_engine(db: &Path) -> (Arc<ChatMemoryStore>, Arc<MemoryRetriever>, Value) {
    let (cfg, pool, max_df) = engine_config();
    let store = Arc::new(ChatMemoryStore::open(&db.to_string_lossy()).expect("open"));
    store.set_read_pool(pool);
    store.set_keyword_max_df(max_df);
    let desc = json!({"vector_index": cfg.vector_index, "ivf_nprobe": cfg.ivf_nprobe, "ivf_path": cfg.ivf_path, "read_pool": pool,
                      "keyword_max_df": max_df, "channels": cfg.channels, "depth": cfg.depth});
    let llm: Arc<dyn LlmClient> = Arc::new(FakeLlmClient::with_default(""));
    (store.clone(), Arc::new(MemoryRetriever::new(store, llm, cfg)), desc)
}

fn cmd_build_ivf(db: &Path, nlist: usize, iterations: usize, out: &Path) {
    let (_, retriever, _) = open_engine(db);
    let cpu0 = sys::cpu_seconds();
    let t = Instant::now();
    let stats = retriever.build_ivf(nlist, iterations).expect("build ivf");
    let v = json!({"stats": stats, "seconds": t.elapsed().as_secs_f64(), "cpu_s": sys::cpu_seconds() - cpu0,
                   "peak_rss_bytes": sys::peak_rss_bytes(), "env": sys::env()});
    std::fs::write(out, serde_json::to_string_pretty(&v).unwrap()).unwrap();
    println!("{}", serde_json::to_string_pretty(&v).unwrap());
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn pct(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return f64::NAN;
    }
    let rank = ((p / 100.0) * sorted.len() as f64).ceil().max(1.0) as usize - 1;
    sorted[rank.min(sorted.len() - 1)]
}

fn lat_summary(mut v: Vec<f64>) -> Value {
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let mean = v.iter().sum::<f64>() / v.len().max(1) as f64;
    json!({"n": v.len(), "p50_ms": pct(&v, 50.0), "p95_ms": pct(&v, 95.0), "p99_ms": pct(&v, 99.0),
           "max_ms": v.last().copied().unwrap_or(f64::NAN), "mean_ms": mean})
}

struct Loaded {
    queries: Vec<Value>,
    qvecs: Vec<Vec<f64>>,
}

fn load_queries(c: &Corpus, path: &Path, qvec: &str) -> Loaded {
    let queries: Vec<Value> =
        BufReader::new(File::open(path).expect("queries")).lines().map(|l| serde_json::from_str(&l.unwrap()).unwrap()).collect();
    let nq = queries.len() as u64;
    let qvecs = match Vectors::parse(qvec, nq) {
        Vectors::Synthetic(d) => {
            let gen = c.queries(nq);
            gen.iter().map(|q| to_f64(&c.synthetic_query_vector(q, d))).collect()
        }
        Vectors::File { dim, data } => (0..nq as usize).map(|i| to_f64(&data[i * dim..(i + 1) * dim])).collect(),
    };
    Loaded { queries, qvecs }
}

fn search_opts(q: &Value, qv: &[f64], k: usize) -> SearchOptions {
    SearchOptions {
        k,
        include_superseded: q["include_superseded"].as_bool().unwrap_or(false),
        with_sources: false,
        query_embedding: Some(qv.to_vec()),
        touch: false,
        ..Default::default()
    }
}

#[allow(clippy::too_many_arguments)]
fn cmd_run(bank: u32, n: u64, seed: u64, db: &Path, queries: &Path, qvec: &str, conc: &str, passes: usize, k: usize, out_dir: &Path) {
    std::fs::create_dir_all(out_dir).unwrap();
    let c = Corpus::new(seed, bank, n);
    let Loaded { queries, qvecs } = load_queries(&c, queries, qvec);
    let rss_start = sys::current_rss_bytes();
    let cpu0 = sys::cpu_seconds();

    // cold: process-cold (fresh process, empty engine caches); the OS page cache is NOT purged
    let t_open = Instant::now();
    let (_store, retriever, engine) = open_engine(db);
    let open_ms = t_open.elapsed().as_secs_f64() * 1e3;
    let t_first = Instant::now();
    let first = futures::executor::block_on(retriever.search(queries[0]["text"].as_str().unwrap(), &search_opts(&queries[0], &qvecs[0], k)));
    let first_query_ms = t_first.elapsed().as_secs_f64() * 1e3;
    let rss_after_load = sys::current_rss_bytes();
    let t_second = Instant::now();
    let _ = futures::executor::block_on(retriever.search(queries[0]["text"].as_str().unwrap(), &search_opts(&queries[0], &qvecs[0], k)));
    let second_query_ms = t_second.elapsed().as_secs_f64() * 1e3;
    let index_len = retriever.get_index().map(|i| i.len()).unwrap_or(0);

    let mut raw = BufWriter::new(File::create(out_dir.join("raw_queries.jsonl")).unwrap());
    writeln!(raw, "{}", json!({"phase": "cold", "qid": queries[0]["qid"], "latency_ms": first_query_ms,
        "ok": first.is_ok(), "top": first.as_ref().map(|r| r.iter().map(|m| m.memory.memory_id.clone()).collect::<Vec<_>>()).unwrap_or_default()})).unwrap();

    let mut levels = Vec::new();
    for cl in conc.split(',').map(|s| s.trim().parse::<usize>().expect("concurrency")) {
        let total = queries.len() * passes;
        let next = AtomicUsize::new(0);
        let t0 = Instant::now();
        let rows: Vec<(usize, f64, Result<Vec<String>, String>)> = std::thread::scope(|sc| {
            let hs: Vec<_> = (0..cl)
                .map(|_| {
                    let (next, queries, qvecs, retriever) = (&next, &queries, &qvecs, &retriever);
                    sc.spawn(move || {
                        let mut out = Vec::new();
                        loop {
                            let j = next.fetch_add(1, Ordering::SeqCst);
                            if j >= total {
                                break;
                            }
                            let qi = j % queries.len();
                            let q = &queries[qi];
                            let ts = Instant::now();
                            let r = futures::executor::block_on(retriever.search(q["text"].as_str().unwrap(), &search_opts(q, &qvecs[qi], k)));
                            let ms = ts.elapsed().as_secs_f64() * 1e3;
                            out.push((qi, ms, r.map(|v| v.iter().map(|m| m.memory.memory_id.clone()).collect()).map_err(|e| e.to_string())));
                        }
                        out
                    })
                })
                .collect();
            hs.into_iter().flat_map(|h| h.join().unwrap()).collect()
        });
        let wall = t0.elapsed().as_secs_f64();
        let ok = rows.iter().filter(|r| r.2.is_ok()).count();
        for (qi, ms, r) in &rows {
            let (okf, top, err) = match r {
                Ok(t) => (true, t.clone(), None),
                Err(e) => (false, Vec::new(), Some(e.clone())),
            };
            writeln!(raw, "{}", json!({"phase": "warm", "concurrency": cl, "qid": queries[*qi]["qid"], "latency_ms": ms, "ok": okf, "top": top, "err": err})).unwrap();
        }
        let lat: Vec<f64> = rows.iter().filter(|r| r.2.is_ok()).map(|r| r.1).collect();
        levels.push(json!({"concurrency": cl, "requests": total, "ok": ok, "errors": total - ok, "wall_s": wall,
                           "successful_qps": ok as f64 / wall, "latency": lat_summary(lat)}));
        eprintln!("concurrency {cl}: {:.1} qps", ok as f64 / wall);
    }
    raw.flush().unwrap();
    let summary = json!({
        "bank": bank, "n": n, "db": db, "engine": engine, "index_rows": index_len, "queries": queries.len(), "k": k,
        "cold": {"definition": "fresh process, empty engine caches, OS page cache not purged",
                  "store_open_ms": open_ms, "first_query_ms_incl_index_load": first_query_ms, "second_query_ms": second_query_ms},
        "levels": levels,
        "rss_bytes": {"process_start": rss_start, "after_index_load": rss_after_load, "end": sys::current_rss_bytes(),
                       "peak": sys::peak_rss_bytes()},
        "cpu_s": sys::cpu_seconds() - cpu0, "disk_bytes": db_bytes(db), "env": sys::env(),
        "timing_scope": "MemoryRetriever::search wall time in-process; query embedding precomputed and excluded; touch=false; with_sources=false",
    });
    std::fs::write(out_dir.join("run_summary.json"), serde_json::to_string_pretty(&summary).unwrap()).unwrap();
    println!("{}", serde_json::to_string_pretty(&summary).unwrap());
}

/// Per-stage cost of a search, through the engine's public API: full index load, channel-restricted searches, and
/// the store calls a search makes (keyword FTS, cache key, entity lookups, hydration).
fn cmd_profile(bank: u32, n: u64, seed: u64, db: &Path, queries: &Path, qvec: &str, limit: usize, out: &Path) {
    let c = Corpus::new(seed, bank, n);
    let Loaded { queries, qvecs } = load_queries(&c, queries, qvec);
    let m = limit.min(queries.len());
    let rss0 = sys::current_rss_bytes();
    let (store, retriever, engine) = open_engine(db);
    let t = Instant::now();
    let index = retriever.get_index().expect("index");
    let load_ms = t.elapsed().as_secs_f64() * 1e3;
    let rss1 = sys::current_rss_bytes();
    let rows_t = Instant::now();
    let rows = store.index_rows(None, None).expect("rows");
    let index_rows_ms = rows_t.elapsed().as_secs_f64() * 1e3;
    drop(rows);
    let time = |f: &mut dyn FnMut(usize)| -> Value {
        let mut v = Vec::with_capacity(m);
        for i in 0..m {
            let t = Instant::now();
            f(i);
            v.push(t.elapsed().as_secs_f64() * 1e3);
        }
        lat_summary(v)
    };
    let filt = neuralgraph_memory::store::ChannelFilter::default();
    let mut stages = serde_json::Map::new();
    for ch in ["VK", "V", "K"] {
        stages.insert(format!("search_channels_{ch}"), time(&mut |i| {
            let mut o = search_opts(&queries[i], &qvecs[i], 10);
            o.channels = Some(ch.into());
            futures::executor::block_on(retriever.search(queries[i]["text"].as_str().unwrap(), &o)).unwrap();
        }));
    }
    stages.insert("store.keyword_candidates(depth*2=200, active)".into(), time(&mut |i| {
        store.keyword_candidates(queries[i]["text"].as_str().unwrap(), 200, "active", &filt).unwrap();
    }));
    stages.insert("store.cache_key (PRAGMA data_version)".into(), time(&mut |_| {
        store.cache_key();
    }));
    stages.insert("retriever.query_entities (alias map clone + scan)".into(), time(&mut |i| {
        retriever.query_entities(queries[i]["text"].as_str().unwrap()).unwrap();
    }));
    let ids: Vec<String> = index.ids.iter().take(100).cloned().collect();
    stages.insert("store.entity_ids_for_memories(100)".into(), time(&mut |_| {
        store.entity_ids_for_memories(&ids).unwrap();
    }));
    stages.insert("store.get_memories_by_ids(10)".into(), time(&mut |_| {
        store.get_memories_by_ids(&ids[..10], false).unwrap();
    }));
    let summary = json!({
        "n": n, "engine": engine, "index_rows": index.len(), "queries_profiled": m,
        "index_full_load_ms": load_ms, "store.index_rows_ms": index_rows_ms,
        "rss_before_load": rss0, "rss_after_load": rss1, "resident_bytes_per_memory": (rss1.saturating_sub(rss0)) as f64 / n as f64,
        "entity_keys": store.all_entity_keys().map(|k| k.len()).unwrap_or(0),
        "stages": stages, "env": sys::env(),
    });
    std::fs::write(out, serde_json::to_string_pretty(&summary).unwrap()).unwrap();
    println!("{}", serde_json::to_string_pretty(&summary).unwrap());
}

fn main() {
    match Cli::parse().cmd {
        Cmd::BuildIvf { db, nlist, iterations, out } => cmd_build_ivf(&db, nlist, iterations, &out),
        Cmd::Profile { bank, n, seed, db, queries, qvec, limit, out } => cmd_profile(bank, n, seed, &db, &queries, &qvec, limit, &out),
        Cmd::Texts { bank, n, seed, nq, out_dir } => cmd_texts(bank, n, seed, nq, &out_dir),
        Cmd::Queries { bank, n, seed, nq, out } => cmd_queries(bank, n, seed, nq, &out),
        Cmd::Build { bank, n, seed, db, emb, batch, out } => cmd_build(bank, n, seed, &db, &emb, batch, &out),
        Cmd::Run { bank, n, seed, db, queries, qvec, concurrency, passes, k, out_dir } => {
            cmd_run(bank, n, seed, &db, &queries, &qvec, &concurrency, passes, k, &out_dir)
        }
    }
}
