//! CPU-side cost of the memory system with the fake model (no model latency): insert rate, the first search (which
//! builds the vector index), warm search latency and `remember()` at N memories. Mirrors the Python benchmark the
//! port was measured against, so the numbers are comparable.
//!
//! ```text
//! cargo run --release --example bench -- 10000 768
//! cargo run --release --example bench -- 50000 768
//! ```

use neuralgraph_memory::llm::FakeLlmClient;
use neuralgraph_memory::models::{new_id, now_iso, Memory};
use neuralgraph_memory::retrieval::SearchOptions;
use neuralgraph_memory::service::{ChatMemory, ChatMemoryConfig, RememberOptions};
use neuralgraph_memory::testing::scripted_fake_llm;
use neuralgraph_memory::textutil::content_hash;
use rand::rngs::StdRng;
use rand::{RngExt, SeedableRng};
use std::sync::Arc;
use std::time::Instant;

const WORDS: &str =
    "berlin tokyo coffee project deadline sister marathon guitar python rust job interview flight hotel \
doctor dentist birthday cake dog cat garden kitchen budget salary rent apartment train bike book novel \
concert piano lesson exam thesis paper review meeting manager team launch bug release";
const SUBJECTS: [&str; 5] = ["user", "ali", "sara", "omar", "lena"];

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    let n: usize = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(10_000);
    let dim: usize = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(768);
    let words: Vec<&str> = WORDS.split_whitespace().collect();
    let dir = tempfile::tempdir()?;
    let llm = Arc::new(FakeLlmClient { dim, ..scripted_fake_llm() });
    let cm = ChatMemory::open(dir.path().join("m.db").to_str().unwrap(), Some(llm), ChatMemoryConfig::default())?;

    let mut rng = StdRng::seed_from_u64(1);
    let now = now_iso();
    let t0 = Instant::now();
    for i in 0..n {
        let mut text: Vec<&str> = (0..12).map(|_| words[rng.random_range(0..words.len())]).collect();
        let tag = format!("#{i}");
        text.push(&tag);
        let text = text.join(" ");
        let s = SUBJECTS[i % SUBJECTS.len()];
        let mut m = Memory::new(
            new_id("mem"),
            text.clone(),
            "fact".into(),
            s.into(),
            s.into(),
            s.into(),
            format!("c{}", i % 50),
            0.5,
            1.0,
            now.clone(),
            now.clone(),
            now.clone(),
        );
        m.text_hash = content_hash(&[&text]);
        m.embedding = Some((0..dim).map(|_| rng.random::<f64>() - 0.5).collect());
        cm.store.insert_memory(&m, &[], &[])?;
    }
    let insert = t0.elapsed().as_secs_f64();
    cm.retriever.invalidate();
    let q = "when is the marathon and what about the guitar lesson";
    let t0 = Instant::now();
    cm.search(q, &SearchOptions::default()).await?;
    let cold = t0.elapsed().as_secs_f64();
    let mut ts = Vec::new();
    for w in words.iter().take(20) {
        let t0 = Instant::now();
        cm.search(&format!("{q} {w}"), &SearchOptions::default()).await?;
        ts.push(t0.elapsed().as_secs_f64());
    }
    ts.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let t0 = Instant::now();
    let opts = RememberOptions { subject: "ali".into(), ..Default::default() };
    cm.remember(&format!("Ali started learning rust in {n}"), &opts).await?;
    let rem = t0.elapsed().as_secs_f64();
    println!(
        "N={n:>6} dim={dim}: insert {:.2} ms/mem | first search (index build) {:.0} ms | warm search p50 {:.1} ms p95 {:.1} ms | remember() {:.0} ms",
        insert / n as f64 * 1e3,
        cold * 1e3,
        ts[10] * 1e3,
        ts[18] * 1e3,
        rem * 1e3
    );
    cm.close().await;
    Ok(())
}
