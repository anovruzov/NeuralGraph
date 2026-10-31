//! Process resource readings (macOS / Linux) and environment capture.

use serde_json::{json, Value};

fn rusage() -> libc::rusage {
    let mut ru: libc::rusage = unsafe { std::mem::zeroed() };
    unsafe { libc::getrusage(libc::RUSAGE_SELF, &mut ru) };
    ru
}

/// Peak resident set size of this process (`ru_maxrss`: bytes on macOS, KiB on Linux).
pub fn peak_rss_bytes() -> u64 {
    let r = rusage().ru_maxrss as u64;
    if cfg!(target_os = "macos") {
        r
    } else {
        r * 1024
    }
}

/// User + system CPU seconds consumed by this process so far.
pub fn cpu_seconds() -> f64 {
    let r = rusage();
    let t = |tv: libc::timeval| tv.tv_sec as f64 + tv.tv_usec as f64 / 1e6;
    t(r.ru_utime) + t(r.ru_stime)
}

/// Current resident set size, read with `ps` (portable, no extra crates).
pub fn current_rss_bytes() -> u64 {
    let out = std::process::Command::new("ps").args(["-o", "rss=", "-p", &std::process::id().to_string()]).output();
    out.ok().and_then(|o| String::from_utf8_lossy(&o.stdout).trim().parse::<u64>().ok()).map(|k| k * 1024).unwrap_or(0)
}

fn cmd(c: &str, args: &[&str]) -> String {
    std::process::Command::new(c)
        .args(args)
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default()
}

pub fn env() -> Value {
    json!({
        "git_commit": cmd("git", &["rev-parse", "HEAD"]),
        "git_dirty_files": cmd("git", &["status", "--porcelain", "--untracked-files=no"]).lines().count(),
        "cpu": cmd("sysctl", &["-n", "machdep.cpu.brand_string"]),
        "ncpu": cmd("sysctl", &["-n", "hw.ncpu"]),
        "mem_bytes": cmd("sysctl", &["-n", "hw.memsize"]),
        "os": cmd("uname", &["-srm"]),
        "rustc": cmd("rustc", &["--version"]),
        "threads_available": std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1),
    })
}
