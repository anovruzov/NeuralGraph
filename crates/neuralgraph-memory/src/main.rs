//! `neuralgraph-memory` binary: see [`neuralgraph_memory::cli`].

use clap::Parser;
use neuralgraph_memory::cli::{run, Cli, Command};
use tracing::{Event, Level, Subscriber};
use tracing_subscriber::fmt::format::Writer;
use tracing_subscriber::fmt::{FmtContext, FormatEvent, FormatFields};
use tracing_subscriber::registry::LookupSpan;

/// The log line format the Python CLI used: `%(asctime)s %(levelname)s %(name)s: %(message)s`.
struct PythonLogFormat;

impl<S, N> FormatEvent<S, N> for PythonLogFormat
where
    S: Subscriber + for<'a> LookupSpan<'a>,
    N: for<'a> FormatFields<'a> + 'static,
{
    fn format_event(&self, ctx: &FmtContext<'_, S, N>, mut w: Writer<'_>, event: &Event<'_>) -> std::fmt::Result {
        let meta = event.metadata();
        let level = match *meta.level() {
            Level::ERROR => "ERROR",
            Level::WARN => "WARNING",
            Level::INFO => "INFO",
            Level::DEBUG | Level::TRACE => "DEBUG",
        };
        let now = chrono::Local::now().format("%Y-%m-%d %H:%M:%S,%3f");
        write!(w, "{now} {level} {}: ", meta.target())?;
        ctx.field_format().format_fields(w.by_ref(), event)?;
        writeln!(w)
    }
}

fn main() {
    let cli = Cli::parse();
    let verbose = cli.cmd.common().verbose;
    let level = if verbose { tracing::Level::DEBUG } else { tracing::Level::INFO };
    // stdout is the protocol channel in `mcp` mode, so logs go to stderr there
    let to_stderr = matches!(cli.cmd, Command::Mcp { .. });
    let builder = tracing_subscriber::fmt().with_max_level(level).with_ansi(false).event_format(PythonLogFormat);
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
