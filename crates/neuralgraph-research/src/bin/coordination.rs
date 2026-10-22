//! `coordination`: the coordination simulator's entry points (the Python `python -m
//! NeuralGraph.coordination.{experiment,benchmark,scale,figures}` modules), with the same flags and output bytes.
//!
//! ```text
//! coordination experiment [--seed N] [--output PATH]
//! coordination benchmark  [--seed N] [--output PATH] [--format json|markdown] [--sweep TRIALS]
//! coordination scale      [--seed N] [--output PATH] [--format json|markdown]
//! coordination figures    [--output-dir DIR] [--check]
//! ```
//!
//! JSON output is `json.dumps(result, sort_keys=True, indent=2) + "\n"`; markdown is the Python renderer's text.
//! Without `--output` the payload goes to stdout unchanged. `figures` reads the committed artifact JSON from
//! `research/coordination/artifacts/` and writes (or, with `--check`, compares) `figures/*.svg` there by default.

use clap::{Parser, Subcommand, ValueEnum};
use neuralgraph_research::coordination::contracts::CoordinationError;
use neuralgraph_research::coordination::core::artifact_json;
use neuralgraph_research::coordination::{benchmark, experiment, figures, scale};
use std::io::Write;
use std::path::PathBuf;
use std::process::ExitCode;

#[derive(Parser, Debug)]
#[command(name = "coordination", about = "Deterministic multi-agent coordination simulator")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
enum Format {
    Json,
    Markdown,
}

#[derive(Subcommand, Debug)]
enum Command {
    /// Executable deterministic collective-capability failure experiment.
    Experiment {
        #[arg(long, default_value_t = experiment::DEFAULT_SEED, allow_negative_numbers = true)]
        seed: i64,
        #[arg(long)]
        output: Option<PathBuf>,
    },
    /// Capability-survival benchmark: placement strategies under targeted intervention.
    Benchmark {
        #[arg(long, default_value_t = benchmark::DEFAULT_SEED, allow_negative_numbers = true)]
        seed: i64,
        #[arg(long)]
        output: Option<PathBuf>,
        #[arg(long, value_enum, default_value_t = Format::Json)]
        format: Format,
        /// aggregate over TRIALS consecutive seeds instead of reporting one seed
        #[arg(long, value_name = "TRIALS", allow_negative_numbers = true)]
        sweep: Option<i64>,
    },
    /// Scale and generalization sweep: does the mechanism hold beyond the 4-node pair?
    Scale {
        #[arg(long, default_value_t = benchmark::DEFAULT_SEED, allow_negative_numbers = true)]
        seed: i64,
        #[arg(long)]
        output: Option<PathBuf>,
        #[arg(long, value_enum, default_value_t = Format::Json)]
        format: Format,
    },
    /// Render the paper's figures from the pinned artifacts, deterministically.
    Figures {
        #[arg(long, value_name = "OUTPUT_DIR")]
        output_dir: Option<PathBuf>,
        /// compare against what is committed; write nothing
        #[arg(long)]
        check: bool,
    },
}

/// `args.output.write_text(payload)` or `print(payload, end="")`.
fn emit(payload: &str, output: Option<PathBuf>) -> Result<(), CoordinationError> {
    match output {
        Some(path) => std::fs::write(&path, payload)
            .map_err(|e| CoordinationError::new("OSError", format!("{e}: {}", path.display()))),
        None => {
            let mut stdout = std::io::stdout().lock();
            stdout
                .write_all(payload.as_bytes())
                .and_then(|_| stdout.flush())
                .map_err(|e| CoordinationError::new("OSError", e.to_string()))
        }
    }
}

fn run(cli: Cli) -> Result<u8, CoordinationError> {
    match cli.command {
        Command::Experiment { seed, output } => {
            emit(&artifact_json(&experiment::run_experiment(seed)?), output)?;
        }
        Command::Benchmark { seed, output, format, sweep } => {
            // `if args.sweep:` — zero means "no sweep", like Python's truthiness
            let payload = match sweep {
                Some(trials) if trials != 0 => {
                    let result = benchmark::run_sweep(seed, trials)?;
                    match format {
                        Format::Markdown => benchmark::render_sweep_markdown(&result),
                        Format::Json => artifact_json(&result),
                    }
                }
                _ => {
                    let result = benchmark::run_benchmark(seed)?;
                    match format {
                        Format::Markdown => benchmark::render_markdown(&result),
                        Format::Json => artifact_json(&result),
                    }
                }
            };
            emit(&payload, output)?;
        }
        Command::Scale { seed, output, format } => {
            let result = scale::run_scale_sweep(seed, &scale::default_specs())?;
            let payload = match format {
                Format::Markdown => scale::render_markdown(&result),
                Format::Json => artifact_json(&result),
            };
            emit(&payload, output)?;
        }
        Command::Figures { output_dir, check } => {
            let output_dir = output_dir.unwrap_or_else(figures::figures_dir);
            let rendered = figures::render_all(&figures::artifacts_dir())?;
            let mut names: Vec<&String> = rendered.keys().collect();
            names.sort();
            if check {
                let stale: Vec<&String> = names
                    .into_iter()
                    .filter(|name| {
                        std::fs::read_to_string(output_dir.join(name.as_str()))
                            .map_or(true, |text| text != rendered[*name])
                    })
                    .collect();
                for name in &stale {
                    eprintln!("stale: {name}");
                }
                if !stale.is_empty() {
                    eprintln!("Figures do not match their artifacts. Regenerate deliberately.");
                    return Ok(1);
                }
                println!("{} figures match their artifacts.", rendered.len());
                return Ok(0);
            }
            std::fs::create_dir_all(&output_dir)
                .map_err(|e| CoordinationError::new("OSError", format!("{e}: {}", output_dir.display())))?;
            for name in names {
                let path = output_dir.join(name);
                std::fs::write(&path, &rendered[name])
                    .map_err(|e| CoordinationError::new("OSError", format!("{e}: {}", path.display())))?;
                println!("wrote {}", path.display());
            }
        }
    }
    Ok(0)
}

fn main() -> ExitCode {
    match run(Cli::parse()) {
        Ok(code) => ExitCode::from(code),
        Err(e) => {
            eprintln!("{}: {}", e.kind, e.message);
            ExitCode::from(1)
        }
    }
}
