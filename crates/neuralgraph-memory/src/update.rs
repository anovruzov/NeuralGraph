//! Self-update: `neuralgraph-memory update` and the "Update now" button.
//!
//! The latest version is the tag GitHub's `releases/latest` link redirects to (no API key, no rate limit). An update
//! downloads the release archive for this machine, checks its SHA-256 against the published one, and renames the new
//! binary over the running one (the running process keeps its old code until it restarts). The dashboard then
//! restarts itself in place ([`request_restart`]: same process id, same terminal, same settings), and copies that
//! AI tools started earlier are stopped so they cannot keep working with outdated rules.
//!
//! Only installs made by `install.sh` or a previous update are replaced: a binary inside a Cargo `target/` or
//! `~/.cargo/bin` folder was built from source and is updated with Cargo.

use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

pub const REPO: &str = "anovruzov/NeuralGraph";
const BIN: &str = "neuralgraph-memory";

/// This build's version, without the `v`.
pub fn current_version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// The release archive name part for this machine, if prebuilt binaries exist for it.
pub fn target() -> Option<&'static str> {
    match (std::env::consts::OS, std::env::consts::ARCH) {
        ("macos", "aarch64") => Some("aarch64-apple-darwin"),
        ("macos", "x86_64") => Some("x86_64-apple-darwin"),
        ("linux", "x86_64") => Some("x86_64-unknown-linux-gnu"),
        ("linux", "aarch64") => Some("aarch64-unknown-linux-gnu"),
        _ => None,
    }
}

/// The running binary's path, captured before any update replaces the file.
pub fn exe_path() -> PathBuf {
    static EXE: OnceLock<PathBuf> = OnceLock::new();
    EXE.get_or_init(|| {
        std::env::current_exe().ok().and_then(|p| p.canonicalize().ok()).unwrap_or_else(|| PathBuf::from(BIN))
    })
    .clone()
}

/// Why this install cannot update itself (`None` when it can).
pub fn blocked_reason() -> Option<String> {
    let exe = exe_path();
    let s = exe.to_string_lossy();
    if target().is_none() {
        return Some("there are no prebuilt binaries for this machine; update by building from source".into());
    }
    if s.contains("/target/debug/") || s.contains("/target/release/") || s.contains("/.cargo/bin/") {
        return Some(format!(
            "this copy was built from source ({s}); update it with `git pull` and `cargo install --locked --path crates/neuralgraph-memory`"
        ));
    }
    let dir = exe.parent().unwrap_or(Path::new("."));
    let probe = dir.join(format!(".{BIN}.write-test"));
    match std::fs::write(&probe, b"") {
        Ok(()) => {
            let _ = std::fs::remove_file(&probe);
            None
        }
        Err(e) => Some(format!("no permission to replace {s} ({e})")),
    }
}

/// `1.10.0` > `1.9.3`: compare dotted numbers, ignoring a leading `v` and anything after a `-`.
pub fn newer(candidate: &str, than: &str) -> bool {
    let parse = |v: &str| -> Vec<u64> {
        v.trim_start_matches('v').split('-').next().unwrap_or("").split('.').map(|p| p.parse().unwrap_or(0)).collect()
    };
    let (a, b) = (parse(candidate), parse(than));
    for i in 0..a.len().max(b.len()) {
        let (x, y) = (a.get(i).copied().unwrap_or(0), b.get(i).copied().unwrap_or(0));
        if x != y {
            return x > y;
        }
    }
    false
}

fn release_base(tag: &str) -> String {
    match std::env::var("NEURALGRAPH_RELEASE_URL").ok().filter(|u| !u.is_empty()) {
        Some(base) => base.trim_end_matches('/').to_string(),
        None => format!("https://github.com/{REPO}/releases/download/{tag}"),
    }
}

/// The newest released tag (e.g. `v1.6.0`). Cached for six hours; `force` asks again.
pub async fn latest_tag(force: bool) -> Result<String, String> {
    static CACHE: Mutex<Option<(Instant, String)>> = Mutex::new(None);
    if let Some(tag) = std::env::var("NEURALGRAPH_UPDATE_TAG").ok().filter(|t| !t.is_empty()) {
        return Ok(tag);
    }
    if !force {
        if let Some((at, tag)) = CACHE.lock().unwrap_or_else(|e| e.into_inner()).clone() {
            if at.elapsed() < Duration::from_secs(6 * 3600) {
                return Ok(tag);
            }
        }
    }
    let url = format!("https://github.com/{REPO}/releases/latest");
    let resp = reqwest::Client::new()
        .get(&url)
        .timeout(Duration::from_secs(15))
        .send()
        .await
        .map_err(|e| format!("could not reach GitHub: {e}"))?;
    let tag = resp
        .url()
        .path()
        .rsplit_once("/tag/")
        .map(|(_, t)| t.to_string())
        .filter(|t| !t.is_empty())
        .ok_or_else(|| format!("GitHub did not name a latest release (HTTP {})", resp.status().as_u16()))?;
    *CACHE.lock().unwrap_or_else(|e| e.into_inner()) = Some((Instant::now(), tag.clone()));
    Ok(tag)
}

async fn fetch(url: &str) -> Result<Vec<u8>, String> {
    let http = reqwest::Client::new();
    let mut last = String::new();
    // GitHub's download servers answer 5xx now and then: try a few times
    for attempt in 0..5 {
        if attempt > 0 {
            tokio::time::sleep(Duration::from_secs(2)).await;
        }
        match http.get(url).timeout(Duration::from_secs(300)).send().await {
            Ok(r) if r.status().is_success() => return r.bytes().await.map(|b| b.to_vec()).map_err(|e| e.to_string()),
            Ok(r) if r.status().as_u16() == 404 => return Err(format!("not found: {url}")),
            Ok(r) => last = format!("HTTP {}", r.status().as_u16()),
            Err(e) => last = e.to_string(),
        }
    }
    Err(format!("GitHub did not deliver {url} ({last}); try again in a minute"))
}

/// Download `tag` for this machine, verify it, and put it in place of the running binary. Returns the bytes
/// downloaded.
pub async fn install(tag: &str) -> Result<usize, String> {
    if let Some(why) = blocked_reason() {
        return Err(why);
    }
    let target = target().expect("checked by blocked_reason");
    let asset = format!("{BIN}-{target}.tar.gz");
    let base = release_base(tag);
    let archive = fetch(&format!("{base}/{asset}")).await?;
    let sums = fetch(&format!("{base}/{asset}.sha256")).await?;
    let want = String::from_utf8_lossy(&sums).split_whitespace().next().unwrap_or("").to_lowercase();
    let got: String = Sha256::digest(&archive).iter().map(|b| format!("{b:02x}")).collect();
    if want.is_empty() || want != got {
        return Err(format!("checksum mismatch for {asset} (expected {want}, got {got}); nothing was changed"));
    }
    let exe = exe_path();
    let size = archive.len();
    tokio::task::spawn_blocking(move || -> Result<(), String> {
        let mut tar = tar::Archive::new(flate2::read::GzDecoder::new(std::io::Cursor::new(archive)));
        let dir = exe.parent().unwrap_or(Path::new("."));
        let staged = dir.join(format!(".{BIN}.new"));
        let mut found = false;
        for entry in tar.entries().map_err(|e| e.to_string())? {
            let mut entry = entry.map_err(|e| e.to_string())?;
            let is_bin = entry.path().ok().and_then(|p| p.file_name().map(|n| n == BIN)).unwrap_or(false);
            if is_bin {
                let mut out = std::fs::File::create(&staged).map_err(|e| e.to_string())?;
                std::io::copy(&mut entry, &mut out).map_err(|e| e.to_string())?;
                found = true;
                break;
            }
        }
        if !found {
            return Err(format!("the release archive does not contain {BIN}"));
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&staged, std::fs::Permissions::from_mode(0o755)).map_err(|e| e.to_string())?;
        }
        // atomic: a process started from now on gets the new file, the running ones keep the old one open
        std::fs::rename(&staged, &exe).map_err(|e| format!("could not replace {}: {e}", exe.display()))
    })
    .await
    .map_err(|e| e.to_string())??;
    Ok(size)
}

/// What an update check found.
#[derive(Clone, Debug)]
pub struct Status {
    pub current: String,
    pub latest: Option<String>,
    pub available: bool,
    /// why this install cannot update itself
    pub blocked: Option<String>,
    pub error: Option<String>,
}

pub async fn status(force: bool) -> Status {
    let current = current_version().to_string();
    let (latest, error) = match latest_tag(force).await {
        Ok(t) => (Some(t), None),
        Err(e) => (None, Some(e)),
    };
    let available = latest.as_deref().is_some_and(|l| newer(l, &current));
    Status { current, latest, available, blocked: blocked_reason(), error }
}

// ------------------------------------------------------------------------------------------------ other copies

/// Another running `neuralgraph-memory` process.
#[derive(Clone, Debug)]
pub struct Copy {
    pub pid: u32,
    /// "mcp", "serve" or "setup"
    pub mode: String,
}

/// Other long-running copies of this program (the dashboard, and the MCP servers AI tools started).
pub fn other_copies() -> Vec<Copy> {
    let Ok(out) = std::process::Command::new("ps").args(["-axo", "pid=,command="]).output() else { return Vec::new() };
    let me = std::process::id();
    String::from_utf8_lossy(&out.stdout)
        .lines()
        .filter_map(|l| {
            let (pid, cmd) = l.trim().split_once(' ')?;
            let pid: u32 = pid.parse().ok()?;
            let mut words = cmd.split_whitespace();
            let prog = words.next()?;
            if pid == me || Path::new(prog).file_name()? != BIN {
                return None;
            }
            let mode = words.next()?;
            matches!(mode, "mcp" | "serve" | "setup").then(|| Copy { pid, mode: mode.to_string() })
        })
        .collect()
}

/// Stop the MCP servers AI tools started before an update (they would keep running the old version, and its worker
/// would keep learning with the old rules). The tools start a fresh one when they are restarted.
pub fn stop_old_mcp() -> usize {
    if std::env::var_os("NEURALGRAPH_KEEP_COPIES").is_some() {
        return 0;
    }
    let old: Vec<Copy> = other_copies().into_iter().filter(|c| c.mode == "mcp").collect();
    for c in &old {
        let _ = std::process::Command::new("kill").args(["-TERM", &c.pid.to_string()]).status();
    }
    old.len()
}

// ------------------------------------------------------------------------------------------------ restart in place

static RESTART: OnceLock<tokio::sync::Notify> = OnceLock::new();

fn restart_signal() -> &'static tokio::sync::Notify {
    RESTART.get_or_init(tokio::sync::Notify::new)
}

/// Ask the running `serve` to shut down cleanly and start again from the binary on disk.
pub fn request_restart() {
    restart_signal().notify_one();
}

/// Resolves when [`request_restart`] was called.
pub async fn restart_requested() {
    restart_signal().notified().await;
}

/// Replace this process with a fresh `serve` from the binary on disk (same pid and terminal). The arguments are the
/// ones this process was started with when that was `serve`; a `setup` that went on to serve restarts as `serve`.
/// Only returns on failure.
#[cfg(unix)]
pub fn exec_serve() -> std::io::Error {
    use std::os::unix::process::CommandExt;
    let mut args: Vec<String> = std::env::args().skip(1).filter(|a| a != "--open").collect();
    if args.first().map(String::as_str) != Some("serve") {
        args = vec!["serve".into()];
    }
    std::process::Command::new(exe_path()).args(args).exec()
}

#[cfg(not(unix))]
pub fn exec_serve() -> std::io::Error {
    std::io::Error::other("restart in place is not supported on this platform; start `neuralgraph-memory serve` again")
}
