//! `rescore`: N5, re-score LoCoMo answer files under three judges (port of `research/benchmarks/rescore.py`; the
//! logic is `neuralgraph_research::benchmarks::rescore`).
//!
//! ```text
//! rescore                       # judge the default files, caching in $N5_SCRATCH_DIR
//! rescore count                 # dry run: judge calls needed
//! rescore --file flat=/path/a.json --file pairs=/path/b.json   # instead of editing FILES
//! ```

use clap::Parser;
use neuralgraph_research::benchmarks::rescore::{
    count, count_line, default_files, llm, parse_file_arg, results_dir, scratch_dir, Rescore, RunPaths, SystemClock,
};

#[derive(Parser, Debug)]
#[command(name = "rescore")]
struct Cli {
    /// `count` for the dry run; anything else (or nothing) re-scores
    mode: Vec<String>,
    /// answer file as name=path (repeatable; replaces the default list, like editing FILES in the Python)
    #[arg(long = "file", value_parser = parse_file_arg)]
    files: Vec<(String, String)>,
}

fn main() {
    let cli = Cli::parse();
    let ov = scratch_dir();
    if let Err(e) = std::fs::create_dir_all(&ov) {
        eprintln!("OSError: {e}");
        std::process::exit(1);
    }
    let files = if cli.files.is_empty() { default_files(&ov, &results_dir()) } else { cli.files };
    let result = if cli.mode.first().map(String::as_str) == Some("count") {
        count(&files).map(|(j1, j2)| println!("{}", count_line(j1, j2)))
    } else {
        let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().expect("tokio runtime");
        let client = reqwest::Client::new();
        let mut judge = |prompt: &str| rt.block_on(llm(&client, prompt));
        let mut stdout = std::io::stdout();
        Rescore { files, paths: RunPaths::in_scratch(&ov), judge: &mut judge, clock: &SystemClock, stdout: &mut stdout }
            .run()
            .map(|_| ())
    };
    if let Err(e) = result {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
