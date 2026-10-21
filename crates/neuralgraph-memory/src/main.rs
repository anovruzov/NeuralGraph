//! `neuralgraph-memory` binary: see [`neuralgraph_memory::cli`].

use clap::Parser;
use neuralgraph_memory::cli::{run, Cli, Command};
use tracing_subscriber::fmt::format::Writer;
use tracing_subscriber::fmt::time::FormatTime;

/// `%(asctime)s` of Python logging: local time with milliseconds after a comma.
struct AscTime;

impl FormatTime for AscTime {
    fn format_time(&self, w: &mut Writer<'_>) -> std::fmt::Result {
        write!(w, "{}", chrono::Local::now().format("%Y-%m-%d %H:%M:%S,%3f"))
    }
}

fn main() {
    let cli = Cli::parse();
    let verbose = cli.cmd.common().verbose;
    let level = if verbose { tracing::Level::DEBUG } else { tracing::Level::INFO };
    // stdout is the protocol channel in `mcp` mode, so logs go to stderr there
    let to_stderr = matches!(cli.cmd, Command::Mcp { .. });
    let builder =
        tracing_subscriber::fmt().with_max_level(level).with_timer(AscTime).with_ansi(false).with_target(true);
    if to_stderr {
        builder.with_writer(std::io::stderr).init();
    } else {
        builder.with_writer(std::io::stdout).init();
    }
    let runtime = tokio::runtime::Builder::new_multi_thread().enable_all().build().expect("tokio runtime");
    // `serve` handles SIGINT/SIGTERM itself and shuts down cleanly; everywhere else Ctrl-C exits with 130
    let handles_signals = matches!(cli.cmd, Command::Serve { .. });
    let code = runtime.block_on(async move {
        let finish = |r: neuralgraph_memory::error::Result<i32>| match r {
            Ok(code) => code,
            Err(e) => {
                eprintln!("{}", e.qualified());
                1
            }
        };
        if handles_signals {
            return finish(run(cli).await);
        }
        tokio::select! {
            r = run(cli) => finish(r),
            _ = tokio::signal::ctrl_c() => 130,
        }
    });
    std::process::exit(code);
}
