//! `neuralgraph-memory setup`: the first-run wizard.
//!
//! One command takes a new user from "nothing installed" to a running dashboard: it finds LM Studio or Ollama, picks
//! and smoke-tests a chat and an embedding model, asks for a name and a database path, registers the MCP server
//! with the coding tools it finds (Claude Code, Codex, Claude Desktop, Cursor), saves the answers to
//! `~/.neuralgraph/config.json`, and offers to start `serve`.
//!
//! The saved file is the lowest-precedence layer: flags > environment variables > config file > built-in
//! defaults. [`apply_saved_config`] runs before argument parsing and only fills environment variables that are
//! unset, so MCP clients can launch a bare `neuralgraph-memory mcp` and still get the user's settings.

use crate::backend::{llm_embed, llm_generate};
use crate::error::{MemoryError, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::future::Future;
use std::io::{IsTerminal, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};

/// Name the MCP server is registered under in every client.
pub const SERVER_NAME: &str = "neuralgraph-memory";
const LM_STUDIO_URL: &str = "http://127.0.0.1:1234";
const OLLAMA_URL: &str = "http://127.0.0.1:11434";
/// Ollama's official installer (macOS and Linux; it installs the app or service and starts it).
const OLLAMA_INSTALLER: &str = "https://ollama.com/install.sh";

/// `$NEURALGRAPH_LMSTUDIO_URL`, else LM Studio's default port.
fn lm_studio_url() -> String {
    std::env::var("NEURALGRAPH_LMSTUDIO_URL").ok().filter(|u| !u.is_empty()).unwrap_or_else(|| LM_STUDIO_URL.into())
}

/// `$OLLAMA_HOST` (Ollama's own setting, `host:port` or a URL), else its default port.
fn ollama_url() -> String {
    match std::env::var("OLLAMA_HOST").ok().filter(|h| !h.is_empty()) {
        Some(h) if h.starts_with("http") => h.trim_end_matches('/').to_string(),
        Some(h) => format!("http://{}", h.replace("0.0.0.0", "127.0.0.1")),
        None => OLLAMA_URL.into(),
    }
}

/// The `ollama` CLI: on PATH, or inside the macOS app the installer just put in /Applications.
fn ollama_bin() -> Option<PathBuf> {
    which("ollama")
        .or_else(|| Some(PathBuf::from("/Applications/Ollama.app/Contents/Resources/ollama")).filter(|p| p.is_file()))
}

/// Run Ollama's installer with the terminal attached, then wait for the server to answer. Returns whether it did.
async fn install_ollama(ui: &Ui, http: &reqwest::Client) -> bool {
    println!("    {}", ui.dim(&format!("$ curl -fsSL {OLLAMA_INSTALLER} | sh")));
    let ran = Command::new("sh")
        .arg("-c")
        .arg(format!("curl -fsSL {OLLAMA_INSTALLER} | sh"))
        .status()
        .is_ok_and(|s| s.success());
    if !ran {
        ui.fail("Ollama", "the installer did not finish (see its output above)");
        return false;
    }
    let url = ollama_url();
    let wait = |secs: u64| {
        let url = url.clone();
        async move {
            for _ in 0..secs * 2 {
                if list_models(http, &url, true).await.is_some() {
                    return true;
                }
                tokio::time::sleep(Duration::from_millis(500)).await;
            }
            false
        }
    };
    if ui.spin("Waiting for Ollama to start…", wait(30)).await {
        return true;
    }
    // the installer did not start the server (e.g. Linux without systemd): run it in the background ourselves
    if let Some(bin) = ollama_bin() {
        let _ = Command::new(bin)
            .arg("serve")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn();
        if ui.spin("Starting `ollama serve`…", wait(30)).await {
            return true;
        }
    }
    ui.fail(
        "Ollama",
        "installed, but the server is not answering yet; open the Ollama app, then choose \"check again\"",
    );
    false
}

// ------------------------------------------------------------------------------------------------ saved config

/// What `setup` writes to `~/.neuralgraph/config.json`. Every field maps to one environment variable.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct SavedConfig {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub llm_base_url: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub llm_model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub embed_model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub db: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub user_name: Option<String>,
    /// a separate model that reads screenshots for Import, when the chat model cannot
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub vision_model: Option<String>,
    /// minutes of idleness before the chat model is unloaded (0 = never)
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub idle_unload_minutes: Option<String>,
    /// "ollama" or "openai"; only saved when the port alone would guess wrong (Ollama off :11434)
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub llm_backend: Option<String>,
}

impl SavedConfig {
    /// (environment variable, value) for every field that is set.
    pub fn env_pairs(&self) -> Vec<(&'static str, String)> {
        [
            ("LLM_BASE_URL", &self.llm_base_url),
            ("LLM_MODEL", &self.llm_model),
            ("EMBED_MODEL", &self.embed_model),
            ("NEURALGRAPH_MEMORY_DB", &self.db),
            ("NEURALGRAPH_USER_NAME", &self.user_name),
            ("LLM_BACKEND", &self.llm_backend),
            ("NEURALGRAPH_VISION_MODEL", &self.vision_model),
            ("NEURALGRAPH_IDLE_UNLOAD_MIN", &self.idle_unload_minutes),
        ]
        .into_iter()
        .filter_map(|(k, v)| v.as_ref().filter(|v| !v.is_empty()).map(|v| (k, v.clone())))
        .collect()
    }
}

/// `$NEURALGRAPH_CONFIG`, else `~/.neuralgraph/config.json`.
pub fn config_path() -> PathBuf {
    if let Some(p) = std::env::var_os("NEURALGRAPH_CONFIG").filter(|p| !p.is_empty()) {
        return PathBuf::from(p);
    }
    home().join(".neuralgraph").join("config.json")
}

fn home() -> PathBuf {
    std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE")).map(PathBuf::from).unwrap_or_default()
}

pub fn load_saved(path: &Path) -> Option<SavedConfig> {
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

/// Fill unset environment variables from the saved config. Call before spawning threads (it uses `set_var`).
pub fn apply_saved_config() {
    let Some(cfg) = load_saved(&config_path()) else { return };
    for (k, v) in cfg.env_pairs() {
        if std::env::var_os(k).is_none_or(|cur| cur.is_empty()) {
            std::env::set_var(k, v);
        }
    }
}

pub fn save(path: &Path, cfg: &SavedConfig) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    std::fs::write(path, serde_json::to_string_pretty(cfg).unwrap_or_default() + "\n")
}

// ------------------------------------------------------------------------------------------------ terminal UI

/// ANSI styling that switches itself off for pipes, `NO_COLOR` and `TERM=dumb`.
struct Ui {
    color: bool,
    truecolor: bool,
    interactive: bool,
}

const ACCENT: (u8, u8, u8) = (57, 135, 229);
const AQUA: (u8, u8, u8) = (27, 175, 122);

impl Ui {
    fn new(interactive: bool) -> Self {
        let color = std::io::stdout().is_terminal()
            && std::env::var_os("NO_COLOR").is_none()
            && std::env::var("TERM").as_deref() != Ok("dumb");
        let truecolor = matches!(std::env::var("COLORTERM").as_deref(), Ok("truecolor") | Ok("24bit"));
        Ui { color, truecolor, interactive }
    }
    fn sgr(&self, code: &str, s: &str) -> String {
        if self.color {
            format!("\x1b[{code}m{s}\x1b[0m")
        } else {
            s.to_string()
        }
    }
    fn rgb(&self, (r, g, b): (u8, u8, u8), s: &str) -> String {
        if !self.color {
            return s.to_string();
        }
        if self.truecolor {
            format!("\x1b[38;2;{r};{g};{b}m{s}\x1b[0m")
        } else {
            // nearest 6x6x6 cube colour
            let q = |c: u8| (c as u16 * 5 / 255) as u8;
            format!("\x1b[38;5;{}m{s}\x1b[0m", 16 + 36 * q(r) + 6 * q(g) + q(b))
        }
    }
    fn accent(&self, s: &str) -> String {
        self.rgb(ACCENT, s)
    }
    fn bold(&self, s: &str) -> String {
        self.sgr("1", s)
    }
    fn dim(&self, s: &str) -> String {
        self.sgr("2", s)
    }
    fn ok(&self, label: &str, detail: &str) {
        println!("  {} {:<14} {}", self.sgr("32", "✓"), label, detail);
    }
    fn warn(&self, label: &str, detail: &str) {
        println!("  {} {:<14} {}", self.sgr("33", "!"), label, detail);
    }
    fn fail(&self, label: &str, detail: &str) {
        println!("  {} {:<14} {}", self.sgr("31", "✗"), label, detail);
    }
    fn skip(&self, label: &str, detail: &str) {
        println!("  {} {:<14} {}", self.dim("–"), label, self.dim(detail));
    }
    fn note(&self, s: &str) {
        println!("    {}", self.dim(s));
    }
    fn step(&self, n: usize, total: usize, title: &str) {
        println!("\n{} {}", self.accent(&format!("[{n}/{total}]")), self.bold(title));
    }

    fn read_line(&self) -> Option<String> {
        let _ = std::io::stdout().flush();
        let mut s = String::new();
        match std::io::stdin().read_line(&mut s) {
            Ok(0) | Err(_) => None, // EOF: behave as if Enter was pressed
            Ok(_) => Some(s.trim().to_string()),
        }
    }
    /// Free text with a default (Enter keeps it).
    fn ask(&self, prompt: &str, default: &str) -> String {
        if !self.interactive {
            return default.to_string();
        }
        let hint = if default.is_empty() { String::new() } else { self.dim(&format!("[{default}]")) + " " };
        print!("  {} {prompt} {hint}", self.accent("?"));
        self.read_line().filter(|s| !s.is_empty()).unwrap_or_else(|| default.to_string())
    }
    fn confirm(&self, prompt: &str, default_yes: bool) -> bool {
        if !self.interactive {
            return default_yes;
        }
        print!("  {} {prompt} {} ", self.accent("?"), self.dim(if default_yes { "[Y/n]" } else { "[y/N]" }));
        match self.read_line().map(|s| s.to_lowercase()) {
            Some(s) if s.starts_with('y') => true,
            Some(s) if s.starts_with('n') => false,
            _ => default_yes,
        }
    }
    /// Numbered menu; returns the chosen index (Enter keeps `default`).
    fn choose(&self, prompt: &str, options: &[String], default: usize) -> usize {
        println!("  {} {prompt}", self.accent("?"));
        for (i, o) in options.iter().enumerate() {
            let mark = if i == default { self.accent("›") } else { " ".into() };
            println!("    {mark} {} {o}", self.dim(&format!("{}.", i + 1)));
        }
        if !self.interactive {
            return default;
        }
        loop {
            print!("    {} ", self.dim(&format!("choose 1-{} [{}]", options.len(), default + 1)));
            match self.read_line() {
                None => return default,
                Some(s) if s.is_empty() => return default,
                Some(s) => match s.parse::<usize>() {
                    Ok(n) if (1..=options.len()).contains(&n) => return n - 1,
                    _ => println!("    {}", self.sgr("33", "type a number from the list")),
                },
            }
        }
    }

    /// Run `fut` with a braille spinner on the current line.
    async fn spin<F: Future>(&self, msg: &str, fut: F) -> F::Output {
        if !self.color {
            println!("  … {msg}");
            return fut.await;
        }
        const FRAMES: [&str; 10] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
        tokio::pin!(fut);
        let mut tick = tokio::time::interval(Duration::from_millis(80));
        let mut i = 0;
        let out = loop {
            tokio::select! {
                v = &mut fut => break v,
                _ = tick.tick() => {
                    print!("\r  {} {msg}", self.accent(FRAMES[i % FRAMES.len()]));
                    let _ = std::io::stdout().flush();
                    i += 1;
                }
            }
        };
        print!("\r\x1b[2K");
        let _ = std::io::stdout().flush();
        out
    }
}

/// Glyphs of the figlet "standard" font, six rows each (row six holds descenders).
const GLYPHS: &[(char, [&str; 6])] = &[
    ('N', [" _   _ ", "| \\ | |", "|  \\| |", "| |\\  |", "|_| \\_|", "       "]),
    ('e', ["      ", "  ___ ", " / _ \\", "|  __/", " \\___|", "      "]),
    ('u', ["       ", " _   _ ", "| | | |", "| |_| |", " \\__,_|", "       "]),
    ('r', ["      ", " _ __ ", "| '__|", "| |   ", "|_|   ", "      "]),
    ('a', ["       ", "  __ _ ", " / _` |", "| (_| |", " \\__,_|", "       "]),
    ('l', [" _ ", "| |", "| |", "| |", "|_|", "   "]),
    ('G', ["  ____ ", " / ___|", "| |  _ ", "| |_| |", " \\____|", "       "]),
    ('p', ["       ", " _ __  ", "| '_ \\ ", "| |_) |", "| .__/ ", "|_|    "]),
    ('h', [" _     ", "| |__  ", "| '_ \\ ", "| | | |", "|_| |_|", "       "]),
];

/// The "NeuralGraph" wordmark as plain text rows (trailing blank rows dropped). Neighbouring glyphs slide together
/// by up to two columns while every overlapping cell is blank in one of them, or (at the seam) the same `|` or `_` in
/// both, which figlet's "smushed" layout merges into one.
pub fn banner_lines() -> Vec<String> {
    let mut rows: Vec<Vec<char>> = vec![Vec::new(); 6];
    for c in "NeuralGraph".chars() {
        let g: Vec<Vec<char>> = GLYPHS
            .iter()
            .find(|(k, _)| *k == c)
            .map(|(_, g)| g.iter().map(|r| r.chars().collect()).collect())
            .expect("glyph");
        let width = rows[0].len();
        let fits = |k: usize| {
            k <= width
                && rows.iter().zip(&g).all(|(r, part)| {
                    (0..k).all(|i| {
                        let (x, y) = (r[width - k + i], part.get(i).copied().unwrap_or(' '));
                        x == ' ' || y == ' ' || (x == y && i == k - 1 && "|_".contains(x))
                    })
                })
        };
        let k = (1..=2).take_while(|k| fits(*k)).last().unwrap_or(0);
        for (r, part) in rows.iter_mut().zip(&g) {
            for (i, ch) in part.iter().enumerate() {
                if i < k {
                    if *ch != ' ' {
                        r[width - k + i] = *ch;
                    }
                } else {
                    r.push(*ch);
                }
            }
        }
    }
    let mut rows: Vec<String> =
        rows.into_iter().map(|r| r.into_iter().collect::<String>().trim_end().to_string()).collect();
    while rows.last().is_some_and(|r| r.trim().is_empty()) {
        rows.pop();
    }
    rows
}

fn print_banner(ui: &Ui) {
    let wide = std::env::var("COLUMNS").ok().and_then(|c| c.parse::<usize>().ok()).is_none_or(|c| c >= 76);
    println!();
    if wide {
        // a left-to-right blue → aqua sweep across the wordmark
        let rows = banner_lines();
        let n = rows.iter().map(|r| r.chars().count()).max().unwrap_or(1).max(1);
        for row in rows {
            let line: String = row
                .chars()
                .enumerate()
                .map(|(i, ch)| {
                    let t = i as f32 / n as f32;
                    let mix = |a: u8, b: u8| (a as f32 + (b as f32 - a as f32) * t) as u8;
                    let c = (mix(ACCENT.0, AQUA.0), mix(ACCENT.1, AQUA.1), mix(ACCENT.2, AQUA.2));
                    if ch == ' ' {
                        " ".to_string()
                    } else {
                        ui.rgb(c, &ch.to_string())
                    }
                })
                .collect();
            println!("  {line}");
        }
    } else {
        println!("  {}", ui.bold(&ui.accent("◉ NeuralGraph")));
    }
    let graph = format!("{}{}{}{}{}", ui.accent("●"), ui.dim("──"), ui.rgb(AQUA, "●"), ui.dim("──"), ui.accent("●"));
    println!(
        "  {graph}  {}\n",
        ui.dim(&format!("v{} · local graph memory for your AI tools", env!("CARGO_PKG_VERSION")))
    );
    let lines = [
        format!("{} {}", ui.accent("✻"), ui.bold("Welcome to NeuralGraph setup")),
        String::new(),
        "  Remembers what you tell Claude, Codex and friends,".to_string(),
        "  across every chat. Runs on this machine only.".to_string(),
        String::new(),
        ui.dim("  About 2 minutes · Enter accepts the [default]"),
    ];
    boxed(ui, &lines);
}

/// Rounded box around `lines` (ANSI codes are not counted toward the width).
fn boxed(ui: &Ui, lines: &[String]) {
    let visible = |s: &str| {
        let mut n = 0;
        let mut esc = false;
        for c in s.chars() {
            match (esc, c) {
                (false, '\x1b') => esc = true,
                (true, 'm') => esc = false,
                (true, _) => {}
                (false, _) => n += 1,
            }
        }
        n
    };
    let w = lines.iter().map(|l| visible(l)).max().unwrap_or(0) + 2;
    println!("  {}", ui.dim(&format!("╭{}╮", "─".repeat(w))));
    for l in lines {
        println!("  {} {l}{} {}", ui.dim("│"), " ".repeat(w - 2 - visible(l)), ui.dim("│"));
    }
    println!("  {}", ui.dim(&format!("╰{}╯", "─".repeat(w))));
}

// ------------------------------------------------------------------------------------------------ model servers

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Kind {
    LmStudio,
    Ollama,
    Custom,
}

#[derive(Clone, Debug)]
struct Found {
    kind: Kind,
    url: String,
    models: Vec<String>,
}

impl Found {
    fn label(&self) -> String {
        match self.kind {
            Kind::LmStudio => format!("LM Studio · {}", self.url),
            Kind::Ollama => format!("Ollama · {}", self.url),
            Kind::Custom => format!("OpenAI-compatible · {}", self.url),
        }
    }
    /// Recommended (chat, embedding) model ids as the server lists them.
    fn recommended(&self) -> (&'static str, &'static str) {
        match self.kind {
            Kind::Ollama => ("qwen2.5:7b-instruct", "nomic-embed-text"),
            _ => ("google/gemma-4-e4b", "text-embedding-nomic-embed-text-v1.5"),
        }
    }
    /// What to download for `role` on this server, if it can download at all.
    fn download(&self, role: Role) -> Option<Download> {
        let (name, model, bin) = match (self.kind, role) {
            (Kind::Ollama, Role::Chat) => ("qwen2.5:7b-instruct", "qwen2.5:7b-instruct", ollama_bin()?),
            (Kind::Ollama, Role::Embedding) => ("nomic-embed-text", "nomic-embed-text", ollama_bin()?),
            (Kind::Ollama, Role::Vision) => {
                (crate::vision::OLLAMA_VISION.0, crate::vision::OLLAMA_VISION.1, ollama_bin()?)
            }
            (Kind::LmStudio, r) => {
                let lms = which("lms").or_else(|| Some(home().join(".lmstudio/bin/lms")).filter(|p| p.is_file()))?;
                match r {
                    Role::Chat => ("google/gemma-4-e4b", "google/gemma-4-e4b", lms),
                    Role::Embedding => ("nomic-embed-text-v1.5", "text-embedding-nomic-embed-text-v1.5", lms),
                    Role::Vision => (crate::vision::LMSTUDIO_VISION.0, crate::vision::LMSTUDIO_VISION.1, lms),
                }
            }
            (Kind::Custom, _) => return None,
        };
        let args = if self.kind == Kind::Ollama {
            vec!["pull".to_string(), name.to_string()]
        } else {
            vec!["get".to_string(), name.to_string(), "--yes".to_string()]
        };
        Some(Download { role, name: name.into(), model: model.into(), bin, args })
    }

    /// Whether the recommended chat model for this server reads images (LM Studio's Gemma 4 E4B does).
    fn recommended_chat_sees(&self) -> bool {
        self.kind == Kind::LmStudio
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Role {
    Chat,
    Embedding,
    Vision,
}

impl Role {
    fn name(self) -> &'static str {
        match self {
            Role::Chat => "chat",
            Role::Embedding => "embedding",
            Role::Vision => "image",
        }
    }
    fn label(self) -> &'static str {
        match self {
            Role::Chat => "Chat model",
            Role::Embedding => "Embeddings",
            Role::Vision => "Images",
        }
    }
}

/// A model `setup` will download once the user confirms.
#[derive(Clone, Debug)]
struct Download {
    role: Role,
    /// what the download command is given
    name: String,
    /// the id the server lists once it is downloaded
    model: String,
    bin: PathBuf,
    args: Vec<String>,
}

/// Download size in GB: exact from Ollama's registry; measured values for LM Studio's recommended models.
async fn download_size(srv: &Found, d: &Download) -> Option<f64> {
    if srv.kind == Kind::Ollama {
        let (repo, tag) = d.name.split_once(':').unwrap_or((d.name.as_str(), "latest"));
        let repo = if repo.contains('/') { repo.to_string() } else { format!("library/{repo}") };
        let v: Value = reqwest::Client::new()
            .get(format!("https://registry.ollama.ai/v2/{repo}/manifests/{tag}"))
            .header("Accept", "application/vnd.docker.distribution.manifest.v2+json")
            .timeout(Duration::from_secs(10))
            .send()
            .await
            .ok()?
            .json()
            .await
            .ok()?;
        let bytes: f64 = v.get("layers")?.as_array()?.iter().filter_map(|l| l.get("size")?.as_f64()).sum();
        return (bytes > 0.0).then_some(bytes / 1e9);
    }
    match d.name.as_str() {
        "google/gemma-4-e4b" => Some(6.9),
        "nomic-embed-text-v1.5" => Some(0.08),
        n if n == crate::vision::LMSTUDIO_VISION.0 => Some(crate::vision::LMSTUDIO_VISION.2),
        _ => None,
    }
}

fn gb(v: Option<f64>) -> String {
    match v {
        Some(g) if g < 0.1 => format!("{:.0} MB", g * 1000.0),
        Some(g) => format!("{g:.1} GB"),
        None => "size unknown".into(),
    }
}

/// Free space in the home folder's file system, in GB.
fn disk_free_gb() -> Option<f64> {
    let o = Command::new("df").args(["-k"]).arg(home()).output().ok()?;
    let line = String::from_utf8_lossy(&o.stdout).lines().nth(1)?.to_string();
    let avail_kb: f64 = line.split_whitespace().nth(3)?.parse().ok()?;
    Some(avail_kb / 1_048_576.0)
}

/// Installed memory in GB.
fn ram_gb() -> Option<f64> {
    if cfg!(target_os = "macos") {
        let o = Command::new("sysctl").args(["-n", "hw.memsize"]).output().ok()?;
        let b: f64 = String::from_utf8_lossy(&o.stdout).trim().parse().ok()?;
        return Some(b / 1_073_741_824.0);
    }
    let meminfo = std::fs::read_to_string("/proc/meminfo").ok()?;
    let kb: f64 = meminfo.lines().find(|l| l.starts_with("MemTotal:"))?.split_whitespace().nth(1)?.parse().ok()?;
    Some(kb / 1_048_576.0)
}

/// Models on the server that read images.
async fn vision_models(srv: &Found) -> Vec<String> {
    let mut out = Vec::new();
    for m in srv.models.iter().filter(|m| !is_embedding_model(m)) {
        if crate::vision::can_see(&srv.url, m).await == Some(true) {
            out.push(m.clone());
        }
    }
    out
}

/// Model ids a server reports (LM Studio/OpenAI `/v1/models`, Ollama `/api/tags`); `None` when nothing answers.
async fn list_models(http: &reqwest::Client, url: &str, ollama: bool) -> Option<Vec<String>> {
    let endpoint = if ollama { format!("{url}/api/tags") } else { format!("{url}/v1/models") };
    let v: Value = http.get(endpoint).timeout(Duration::from_millis(1500)).send().await.ok()?.json().await.ok()?;
    let (arr, key) = if ollama { (v.get("models")?, "name") } else { (v.get("data")?, "id") };
    Some(arr.as_array()?.iter().filter_map(|m| m.get(key)?.as_str().map(str::to_string)).collect())
}

async fn probe_all(http: &reqwest::Client, custom: Option<String>) -> Vec<Found> {
    let mut cands = vec![(Kind::LmStudio, lm_studio_url()), (Kind::Ollama, ollama_url())];
    if let Some(u) = custom.map(|u| u.trim_end_matches('/').to_string()) {
        if !cands.iter().any(|(_, c)| *c == u) {
            let kind = if u.contains(":11434") { Kind::Ollama } else { Kind::Custom };
            cands.insert(0, (kind, u));
        }
    }
    let probes = cands.into_iter().map(|(kind, url)| async move {
        let models = list_models(http, &url, kind == Kind::Ollama).await?;
        Some(Found { kind, url, models })
    });
    futures::future::join_all(probes).await.into_iter().flatten().collect()
}

pub fn is_embedding_model(id: &str) -> bool {
    let l = id.to_lowercase();
    ["embed", "bge-", "minilm", "e5-"].iter().any(|k| l.contains(k))
}

/// Index of the first model matching a preference, in order (`name` or `name:tag` for Ollama).
pub fn pick_default(models: &[String], prefs: &[&str]) -> Option<usize> {
    prefs.iter().filter(|p| !p.is_empty()).find_map(|p| {
        models.iter().position(|m| m == p || m.strip_prefix(*p).is_some_and(|rest| rest.starts_with(':')))
    })
}

fn which(bin: &str) -> Option<PathBuf> {
    let exts: &[&str] = if cfg!(windows) { &[".exe", ".cmd", ""] } else { &[""] };
    std::env::split_paths(&std::env::var_os("PATH")?)
        .find_map(|dir| exts.iter().map(|e| dir.join(format!("{bin}{e}"))).find(|p| p.is_file()))
}

/// Run a download command with the terminal attached so its own progress bar shows.
fn run_visible(ui: &Ui, cmd: &str, args: &[&str]) -> bool {
    println!("    {}", ui.dim(&format!("$ {cmd} {}", args.join(" "))));
    Command::new(cmd).args(args).status().is_ok_and(|s| s.success())
}

// ------------------------------------------------------------------------------------------------ MCP clients

/// Add (or replace) our server in an MCP JSON config (`{"mcpServers": {...}}`, used by Claude Desktop and Cursor),
/// keeping every other key. Errors instead of overwriting a file that is not a JSON object.
pub fn merge_mcp_json(existing: Option<&str>, exe: &str) -> std::result::Result<String, String> {
    let mut root: Value = match existing.map(str::trim).filter(|s| !s.is_empty()) {
        None => json!({}),
        Some(s) => serde_json::from_str(s).map_err(|e| format!("not valid JSON ({e}); left untouched"))?,
    };
    let obj = root.as_object_mut().ok_or("top level is not a JSON object; left untouched")?;
    let servers = obj.entry("mcpServers").or_insert_with(|| json!({}));
    let servers = servers.as_object_mut().ok_or("\"mcpServers\" is not an object; left untouched")?;
    servers.insert(SERVER_NAME.into(), json!({"command": exe, "args": ["mcp"]}));
    Ok(serde_json::to_string_pretty(&root).unwrap_or_default() + "\n")
}

/// Install (`command: Some`) or remove (`None`) the Claude Code hooks that make memory automatic, in the text of
/// `~/.claude/settings.json`: `Stop` saves each turn, `UserPromptSubmit` recalls memories for each prompt. Other
/// hooks and settings are kept; earlier copies of ours (any binary path) are replaced, never duplicated.
pub fn merge_claude_hooks(existing: Option<&str>, command: Option<&str>) -> std::result::Result<String, String> {
    let mut root: Value = match existing.map(str::trim).filter(|s| !s.is_empty()) {
        None => json!({}),
        Some(s) => serde_json::from_str(s).map_err(|e| format!("not valid JSON ({e}); left untouched"))?,
    };
    let obj = root.as_object_mut().ok_or("top level is not a JSON object; left untouched")?;
    let hooks = obj.entry("hooks").or_insert_with(|| json!({}));
    let hooks = hooks.as_object_mut().ok_or("\"hooks\" is not an object; left untouched")?;
    let ours = |h: &Value| {
        h.get("command")
            .and_then(Value::as_str)
            .is_some_and(|c| c.contains(SERVER_NAME) && c.contains("hook claude-code"))
    };
    for groups in hooks.values_mut() {
        let Some(groups) = groups.as_array_mut() else { continue };
        for g in groups.iter_mut() {
            if let Some(list) = g.get_mut("hooks").and_then(Value::as_array_mut) {
                list.retain(|h| !ours(h));
            }
        }
        groups.retain(|g| g.get("hooks").and_then(Value::as_array).is_none_or(|l| !l.is_empty()));
    }
    hooks.retain(|_, v| v.as_array().is_none_or(|a| !a.is_empty()));
    if let Some(cmd) = command {
        for (event, timeout) in [("Stop", 30), ("UserPromptSubmit", 15)] {
            let entry = json!({"hooks": [{"type": "command", "command": cmd, "timeout": timeout}]});
            match hooks.entry(event).or_insert_with(|| json!([])) {
                Value::Array(a) => a.push(entry),
                _ => return Err(format!("\"hooks.{event}\" is not a list; left untouched")),
            }
        }
    } else if hooks.is_empty() {
        obj.remove("hooks");
    }
    Ok(serde_json::to_string_pretty(&root).unwrap_or_default() + "\n")
}

/// `$CLAUDE_CONFIG_DIR/settings.json`, else `~/.claude/settings.json`.
fn claude_settings_path() -> PathBuf {
    std::env::var_os("CLAUDE_CONFIG_DIR")
        .filter(|d| !d.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| home().join(".claude"))
        .join("settings.json")
}

/// The hook command line for `exe` (single-quoted when the path needs it).
fn hook_command(exe: &str) -> String {
    if exe.chars().all(|c| c.is_ascii_alphanumeric() || "/._-+".contains(c)) {
        format!("{exe} hook claude-code")
    } else {
        format!("'{}' hook claude-code", exe.replace('\'', "'\\''"))
    }
}

/// Write the hooks (or remove them) and keep a `.bak` of the previous settings.
pub fn set_claude_hooks(exe: Option<&str>) -> std::result::Result<String, String> {
    let path = claude_settings_path();
    let existing = std::fs::read_to_string(&path).ok();
    let merged = merge_claude_hooks(existing.as_deref(), exe.map(hook_command).as_deref())?;
    if existing.as_deref() == Some(merged.as_str()) {
        return Ok(format!("{} unchanged", tilde(&path)));
    }
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    if let Some(old) = &existing {
        std::fs::write(path.with_extension("json.bak"), old).map_err(|e| e.to_string())?;
    }
    std::fs::write(&path, merged).map_err(|e| e.to_string())?;
    Ok(format!("wrote {}{}", tilde(&path), if existing.is_some() { " (backup: .bak)" } else { "" }))
}

#[derive(Clone, Debug)]
enum Client {
    /// a CLI with `mcp add/get/remove` subcommands
    Cli { name: &'static str, bin: PathBuf, scope: &'static [&'static str] },
    /// a JSON config file with an `mcpServers` map
    Json { name: &'static str, path: PathBuf },
}

impl Client {
    fn name(&self) -> &'static str {
        match self {
            Client::Cli { name, .. } | Client::Json { name, .. } => name,
        }
    }
}

fn claude_desktop_config() -> PathBuf {
    if cfg!(target_os = "macos") {
        home().join("Library/Application Support/Claude/claude_desktop_config.json")
    } else if cfg!(windows) {
        PathBuf::from(std::env::var_os("APPDATA").unwrap_or_default()).join("Claude/claude_desktop_config.json")
    } else {
        home().join(".config/Claude/claude_desktop_config.json")
    }
}

/// Every supported client, and whether it looks installed.
fn detect_clients() -> Vec<(Client, bool, String)> {
    let mut out = Vec::new();
    for (name, bin, scope) in [("Claude Code", "claude", &["-s", "user"][..]), ("Codex", "codex", &[][..])] {
        match which(bin) {
            Some(p) => out.push((Client::Cli { name, bin: p, scope }, true, format!("`{bin}` CLI"))),
            None => out.push((Client::Cli { name, bin: bin.into(), scope }, false, format!("`{bin}` CLI"))),
        }
    }
    let desktop = claude_desktop_config();
    let has_desktop = desktop.parent().is_some_and(Path::exists);
    out.push((Client::Json { name: "Claude Desktop", path: desktop.clone() }, has_desktop, tilde(&desktop)));
    let cursor = home().join(".cursor/mcp.json");
    let has_cursor = home().join(".cursor").exists();
    out.push((Client::Json { name: "Cursor", path: cursor.clone() }, has_cursor, tilde(&cursor)));
    out
}

fn tilde(p: &Path) -> String {
    let h = home();
    match p.strip_prefix(&h) {
        Ok(rest) if !h.as_os_str().is_empty() => format!("~/{}", rest.display()),
        _ => p.display().to_string(),
    }
}

/// Register the server with one client. Returns a one-line outcome.
/// `ui: None` (the web UI) replaces an existing entry without asking.
fn connect(ui: Option<&Ui>, client: &Client, exe: &str) -> std::result::Result<String, String> {
    match client {
        Client::Cli { name, bin, scope } => {
            let run = |args: &[&str]| Command::new(bin).args(args).output();
            let exists = run(&["mcp", "get", SERVER_NAME]).is_ok_and(|o| o.status.success());
            if exists {
                if ui.is_some_and(|ui| !ui.confirm(&format!("{name} already has {SERVER_NAME}. Replace it?"), true)) {
                    return Ok("kept the existing entry".into());
                }
                let mut rm = vec!["mcp", "remove", SERVER_NAME];
                rm.extend_from_slice(scope);
                let _ = run(&rm);
            }
            let mut add = vec!["mcp", "add"];
            add.extend_from_slice(scope);
            add.extend_from_slice(&[SERVER_NAME, "--", exe, "mcp"]);
            let o = run(&add).map_err(|e| e.to_string())?;
            if o.status.success() {
                Ok(if exists { "updated".into() } else { "connected".into() })
            } else {
                let err = String::from_utf8_lossy(&o.stderr);
                let first = err.lines().find(|l| !l.trim().is_empty()).unwrap_or("failed");
                Err(first.trim().to_string())
            }
        }
        Client::Json { path, .. } => {
            let existing = std::fs::read_to_string(path).ok();
            let merged = merge_mcp_json(existing.as_deref(), exe)?;
            if let Some(dir) = path.parent() {
                std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
            }
            if let Some(old) = &existing {
                let bak = path.with_extension("json.bak");
                std::fs::write(&bak, old).map_err(|e| e.to_string())?;
            }
            std::fs::write(path, merged).map_err(|e| e.to_string())?;
            Ok(format!("wrote {}{}", tilde(path), if existing.is_some() { " (backup: .bak)" } else { "" }))
        }
    }
}

// ------------------------------------------------------------------------------------------------ the wizard

/// What the wizard decided, for the caller.
pub struct SetupOutcome {
    pub exit_code: i32,
    /// start `serve --open` after the wizard
    pub start_server: bool,
}

pub async fn run_setup(yes: bool) -> Result<SetupOutcome> {
    let interactive = !yes && std::io::stdin().is_terminal();
    let ui = Ui::new(interactive);
    let path = config_path();
    let saved = load_saved(&path).unwrap_or_default();
    let env = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());
    let http = reqwest::Client::new();
    let total = 5;
    print_banner(&ui);
    if path.exists() {
        println!("  {}", ui.dim(&format!("Found {} — your previous answers are the defaults.", tilde(&path))));
    }

    // 1. model server
    ui.step(1, total, "Model server");
    ui.note("NeuralGraph turns chats into memories with a local model. Nothing is sent to the cloud.");
    let found = loop {
        let found = ui.spin("Looking for LM Studio and Ollama…", probe_all(&http, env("LLM_BASE_URL"))).await;
        if !found.is_empty() {
            break Some(found);
        }
        ui.fail(
            "Not found",
            &format!("nothing answered at {} (LM Studio) or {} (Ollama)", lm_studio_url(), ollama_url()),
        );
        let can_install = cfg!(unix) && which("curl").is_some() && ollama_bin().is_none();
        let mut opts = Vec::new();
        if can_install {
            opts.push("Install Ollama for me (free, open source · 0.23 GB; you confirm the models next)".to_string());
        }
        opts.extend([
            "I'll start LM Studio or Ollama myself, then check again".to_string(),
            "Continue without a model (settings saved; memory works once a model runs)".to_string(),
            "Quit".to_string(),
        ]);
        if !can_install {
            ui.note("Free options: LM Studio → https://lmstudio.ai (Developer → Start Server)");
            ui.note("              Ollama → https://ollama.com (or run `ollama serve`)");
        }
        // never install software unasked: --yes and pipes take "continue without a model"
        let choice = ui.choose("What now?", &opts, if interactive { 0 } else { opts.len() - 2 });
        match if can_install { choice } else { choice + 1 } {
            0 => {
                install_ollama(&ui, &http).await;
                continue;
            }
            1 => {
                ui.note(
                    "LM Studio: open it, then Developer → Start Server. Ollama: open the app or run `ollama serve`.",
                );
                if interactive {
                    let _ = ui.ask("Press Enter when it is running", "");
                }
                continue;
            }
            2 => break None,
            _ => return Ok(SetupOutcome { exit_code: 1, start_server: false }),
        }
    };
    let server = found.map(|found| {
        for f in &found {
            ui.ok("Found", &format!("{} · {} models", f.label(), f.models.len()));
        }
        if found.len() == 1 {
            return found[0].clone();
        }
        let prefer = saved.llm_base_url.as_deref().or(env("LLM_BASE_URL").as_deref()).unwrap_or("").to_string();
        let def = found.iter().position(|f| f.url == prefer.trim_end_matches('/')).unwrap_or(0);
        let labels: Vec<String> = found.iter().map(Found::label).collect();
        found[ui.choose("Which one should NeuralGraph use?", &labels, def)].clone()
    });

    // the backend is guessed from the port (11434 = Ollama); say it outright when that guess would be wrong
    let llm_backend = match &server {
        Some(s) if s.kind == Kind::Ollama && !s.url.contains(":11434") => Some("ollama".to_string()),
        Some(s) if s.kind != Kind::Ollama && s.url.contains(":11434") => Some("openai".to_string()),
        _ => None,
    };
    if let Some(b) = &llm_backend {
        std::env::set_var("LLM_BACKEND", b); // the smoke test below goes through the same backend switch
    }

    // 2. models: choose everything first, show what it costs, then download
    ui.step(2, total, "Models");
    let (mut chat_model, mut embed_model) = (
        saved.llm_model.clone().or(env("LLM_MODEL")).unwrap_or_else(crate::backend::default_model),
        saved.embed_model.clone().or(env("EMBED_MODEL")).unwrap_or_else(crate::backend::default_embed_model),
    );
    let mut vision_model: Option<String> = saved.vision_model.clone().or(env("NEURALGRAPH_VISION_MODEL"));
    let mut tested = (None, None);
    if let Some(mut srv) = server.clone() {
        let (rec_chat, rec_embed) = srv.recommended();
        let mut downloads: Vec<Download> = Vec::new();
        let mut declined = false;
        for role in [Role::Chat, Role::Embedding] {
            let pool: Vec<String> =
                srv.models.iter().filter(|m| is_embedding_model(m) == (role == Role::Embedding)).cloned().collect();
            let (current, rec) = if role == Role::Chat { (&chat_model, rec_chat) } else { (&embed_model, rec_embed) };
            let has_rec = pick_default(&pool, &[rec]).is_some();
            let mut labels: Vec<String> = pool
                .iter()
                .map(|m| {
                    if m == rec || m.starts_with(&format!("{rec}:")) {
                        format!("{m}  (recommended)")
                    } else {
                        m.clone()
                    }
                })
                .collect();
            let dl = srv.download(role);
            if !has_rec {
                if let Some(d) = &dl {
                    labels.push(format!("Download {} · {}  (recommended)", d.name, gb(download_size(&srv, d).await)));
                }
            }
            let def =
                pick_default(&pool, &[current, rec]).unwrap_or(if has_rec || dl.is_none() { 0 } else { pool.len() });
            if labels.is_empty() {
                ui.warn(
                    "Missing",
                    &format!("no {} model on {} and no way to download one here", role.name(), srv.label()),
                );
                continue;
            }
            let pick =
                if labels.len() == 1 { 0 } else { ui.choose(&format!("Which {} model?", role.name()), &labels, def) };
            if pick < pool.len() {
                let m = pool[pick].clone();
                ui.ok(role.label(), &m);
                if role == Role::Chat {
                    chat_model = m
                } else {
                    embed_model = m
                }
            } else if let Some(d) = dl {
                ui.ok(role.label(), &format!("{} (to download)", d.model));
                if role == Role::Chat {
                    chat_model = d.model.clone()
                } else {
                    embed_model = d.model.clone()
                }
                downloads.push(d);
            }
        }
        // screenshots: the chat model reads them when it can; otherwise a small vision model, swapped in on demand
        let chat_sees = if downloads.iter().any(|d| d.role == Role::Chat) {
            Some(srv.recommended_chat_sees())
        } else {
            crate::vision::can_see(&srv.url, &chat_model).await
        };
        if chat_sees == Some(true) {
            ui.ok("Images", &format!("{chat_model} reads screenshots too · nothing extra to download"));
            vision_model = None;
        } else {
            let seeing = vision_models(&srv).await;
            let dl = srv.download(Role::Vision);
            let mut labels: Vec<String> = seeing.clone();
            if let Some(d) = &dl {
                if !seeing.iter().any(|m| m == &d.model) {
                    labels.push(format!(
                        "Download {} · {}  (recommended, small)",
                        d.name,
                        gb(download_size(&srv, d).await)
                    ));
                }
            }
            labels.push("Skip: no screenshot import for now".into());
            ui.note(&format!("{chat_model} cannot read images. A small vision model reads screenshots for Import;"));
            ui.note("it is swapped in only while importing, so both are never in memory at once.");
            let def = vision_model.as_ref().and_then(|v| seeing.iter().position(|m| m == v)).unwrap_or(0);
            let pick = ui.choose("Which model should read screenshots?", &labels, def);
            if pick < seeing.len() {
                vision_model = Some(seeing[pick].clone());
                ui.ok("Images", &seeing[pick]);
            } else if pick + 1 < labels.len() {
                let d = dl.expect("download option shown");
                vision_model = Some(d.model.clone());
                ui.ok("Images", &format!("{} (to download)", d.model));
                downloads.push(d);
            } else {
                vision_model = None;
                ui.skip("Images", "skipped; choose one later under Settings → Models");
            }
        }
        // what it takes: every model with its size, what is already here, what would be downloaded
        let installed = installed_sizes(&srv).await;
        let mut dl_sizes = Vec::new();
        for d in &downloads {
            dl_sizes.push(download_size(&srv, d).await);
        }
        let total_gb: f64 = dl_sizes.iter().flatten().sum();
        let mut rows: Vec<(Role, String)> =
            vec![(Role::Chat, chat_model.clone()), (Role::Embedding, embed_model.clone())];
        if let Some(v) = &vision_model {
            rows.push((Role::Vision, v.clone()));
        }
        let mut lines = vec![format!("{} {}", ui.accent("◉"), ui.bold("Disk and memory"))];
        lines.push(String::new());
        let mut chat_gb = None;
        for (role, model) in &rows {
            let pending = downloads.iter().position(|d| d.role == *role);
            let size = match pending {
                Some(i) => dl_sizes[i],
                None => installed.get(model).copied(),
            };
            if *role == Role::Chat {
                chat_gb = size;
            }
            let state = if pending.is_some() { ui.sgr("33", "to download") } else { ui.dim("installed") };
            lines.push(format!("  {:<11} {:<38} {:>8}  {state}", role.label(), model, gb(size)));
        }
        let to_get = if downloads.is_empty() { "nothing".to_string() } else { gb(Some(total_gb)) };
        lines.push(format!("  {:<11} {:<38} {}", "", "to download", ui.bold(&format!("{to_get:>8}"))));
        lines.push(String::new());
        if let Some(free) = disk_free_gb() {
            let ok = free > total_gb + 2.0;
            lines.push(format!(
                "  {} disk     {:.0} GB free{}",
                if ok { ui.sgr("32", "✓") } else { ui.sgr("31", "✗") },
                free,
                if ok { "" } else { " — not enough room" }
            ));
        }
        if let (Some(ram), Some(c)) = (ram_gb(), chat_gb) {
            lines.push(format!(
                "  {} memory   about {:.0} GB of your {:.0} GB RAM while learning, 0 when paused",
                if c * 1.3 < ram * 0.6 { ui.sgr("32", "✓") } else { ui.sgr("33", "!") },
                c * 1.3,
                ram
            ));
        }
        if rows.iter().any(|(r, _)| *r == Role::Vision) {
            lines.push(format!("  {}", ui.dim("the image model is loaded only while importing screenshots")));
        }
        println!();
        boxed(&ui, &lines);
        if !downloads.is_empty() {
            if ui.confirm(&format!("Download {} now?", gb(Some(total_gb))), true) {
                for d in &downloads {
                    ui.note(&format!("{} · {}", d.role.label(), d.name));
                    if !run_visible(
                        &ui,
                        &d.bin.to_string_lossy(),
                        &d.args.iter().map(String::as_str).collect::<Vec<_>>(),
                    ) {
                        ui.fail(
                            "Download",
                            &format!("{} did not finish; retry with `neuralgraph-memory setup`", d.name),
                        );
                    }
                }
                if let Some(models) = list_models(&http, &srv.url, srv.kind == Kind::Ollama).await {
                    srv.models = models;
                }
                // LM Studio lists a download under its own id (e.g. text-embedding-…): pick the matching one
                for (role, slot) in [(Role::Chat, &mut chat_model), (Role::Embedding, &mut embed_model)] {
                    if !srv.models.contains(slot) {
                        let pool: Vec<String> = srv
                            .models
                            .iter()
                            .filter(|m| is_embedding_model(m) == (role == Role::Embedding))
                            .cloned()
                            .collect();
                        if let Some(i) = pick_default(&pool, &[slot.as_str()]).or_else(|| {
                            pool.iter().position(|m| m.contains(slot.rsplit('/').next().unwrap_or(slot.as_str())))
                        }) {
                            *slot = pool[i].clone();
                        }
                    }
                }
            } else {
                ui.note("Nothing downloaded. Run `neuralgraph-memory setup` again when you are ready.");
                declined = true;
            }
        }
        // smoke test: one tiny generation and one embedding (pointless when the models were not downloaded)
        let t0 = Instant::now();
        if declined {
            tested = (None, None);
        } else {
            let gen = ui
                .spin(
                    &format!("Saying hello to {chat_model} (the first call loads it, up to a minute)…"),
                    llm_generate(&http, "Reply with one word: ready", &chat_model, &srv.url, 0.0, 16, 180.0),
                )
                .await;
            match gen {
                Ok(_) => {
                    ui.ok("Chat test", &format!("replied in {:.1}s", t0.elapsed().as_secs_f64()));
                    tested.0 = Some(true);
                }
                Err(e) => {
                    ui.fail("Chat test", &short(&e));
                    tested.0 = Some(false);
                }
            }
            let emb = ui
                .spin(
                    &format!("Embedding a test sentence with {embed_model}…"),
                    llm_embed(&http, "hello memory", &embed_model, &srv.url, 120.0),
                )
                .await;
            match emb {
                Ok(v) if !v.is_empty() => {
                    ui.ok("Embed test", &format!("{} dimensions", v.len()));
                    tested.1 = Some(true);
                }
                Ok(_) => {
                    ui.fail("Embed test", "empty vector (is this an embedding model?)");
                    tested.1 = Some(false);
                }
                Err(e) => {
                    ui.fail("Embed test", &short(&e));
                    tested.1 = Some(false);
                }
            }
            if tested != (Some(true), Some(true)) {
                ui.note("Memory needs both. If the model is still loading, re-run setup in a minute.");
            }
        }
    } else {
        ui.skip("Models", "skipped (no server yet); using the defaults below");
        ui.note(&format!("chat: {chat_model}   embeddings: {embed_model}"));
    }

    // 3. you
    ui.step(3, total, "You");
    let user_default =
        saved.user_name.clone().or(env("NEURALGRAPH_USER_NAME")).or(env("USER")).unwrap_or_else(|| "me".into());
    let user_name = ui.ask("What should memory call you?", &user_default);
    let db_default =
        saved.db.clone().or(env("NEURALGRAPH_MEMORY_DB")).unwrap_or_else(|| crate::cli::DEFAULT_DB_FALLBACK.into());
    let db = ui.ask("Where should memories live?", &db_default);
    let cfg = SavedConfig {
        llm_base_url: server.as_ref().map(|s| s.url.clone()).or(saved.llm_base_url.clone()),
        llm_model: Some(chat_model.clone()),
        embed_model: Some(embed_model.clone()),
        db: Some(db.clone()),
        user_name: Some(user_name.clone()),
        vision_model: vision_model.clone(),
        idle_unload_minutes: saved.idle_unload_minutes.clone(),
        llm_backend,
    };
    save(&path, &cfg).map_err(MemoryError::from)?;
    for (k, v) in cfg.env_pairs() {
        std::env::set_var(k, v); // the server started below uses the new answers
    }
    ui.ok("Saved", &tilde(&path));

    // 4. tools
    ui.step(4, total, "Connect your tools");
    let exe = std::env::current_exe().ok().and_then(|p| p.canonicalize().ok()).map(|p| p.display().to_string());
    let exe = exe.unwrap_or_else(|| SERVER_NAME.to_string());
    if exe.contains("/target/debug/") || exe.contains("/target/release/") {
        ui.warn("Heads up", "this binary lives in a build folder, which `cargo clean` deletes");
        ui.note("Install it for good first:  cargo install --locked --path crates/neuralgraph-memory");
    }
    let clients = detect_clients();
    let mut connected: Vec<(String, std::result::Result<String, String>)> = Vec::new();
    let found_clients: Vec<&(Client, bool, String)> = clients.iter().filter(|c| c.1).collect();
    let mut n = 0;
    for (c, ok, how) in &clients {
        if *ok {
            n += 1;
            println!("    {} {} {:<15}{}", ui.accent("●"), ui.dim(&format!("{n}.")), c.name(), ui.dim(how));
        } else {
            println!("    {}    {}", ui.dim("○"), ui.dim(&format!("{:<15}not found · {how}", c.name())));
        }
    }
    if found_clients.is_empty() {
        ui.note("No supported tool found. Run `neuralgraph-memory setup` again after installing one.");
    } else if interactive {
        let names: Vec<&str> = found_clients.iter().map(|c| c.0.name()).collect();
        print!(
            "  {} Connect which? {} ",
            ui.accent("?"),
            ui.dim(&format!("[Enter = all: {} · or numbers like 1,2 · n = none]", names.join(", ")))
        );
        let picked = loop {
            match parse_pick(&ui.read_line().unwrap_or_default(), found_clients.len()) {
                Some(p) => break p,
                None => print!(
                    "    {} ",
                    ui.sgr(
                        "33",
                        &format!("numbers 1-{} separated by commas, Enter for all, n for none:", found_clients.len())
                    )
                ),
            }
        };
        for i in picked {
            let c = &found_clients[i].0;
            connected.push((c.name().to_string(), connect(Some(&ui), c, &exe)));
        }
    } else {
        ui.note(
            "Non-interactive run: no tools changed. Re-run `neuralgraph-memory setup` in a terminal to connect them.",
        );
    }
    for (name, r) in &connected {
        match r {
            Ok(msg) => ui.ok(name, msg),
            Err(e) => ui.fail(name, e),
        }
    }
    // automatic memory: without hooks, Claude only saves when it decides to call a tool
    let mut auto: Option<bool> = None;
    let claude_found = clients.iter().any(|(c, ok, _)| *ok && c.name() == "Claude Code");
    let claude_linked = connected.iter().any(|(n, r)| n == "Claude Code" && r.is_ok());
    if interactive && (claude_linked || claude_found) {
        println!();
        ui.note("Without this, Claude only saves a memory when it decides to call a tool.");
        ui.note("With it, every reply is queued and your local model learns from it in the background,");
        ui.note("and relevant memories are added to each new prompt. Nothing leaves this machine.");
        let on = ui.confirm("Make memory automatic in Claude Code?", true);
        match set_claude_hooks(on.then_some(exe.as_str())) {
            Ok(msg) => {
                ui.ok("Auto-memory", &format!("{} · {msg}", if on { "on" } else { "off" }));
                auto = Some(on);
            }
            Err(e) => ui.fail("Auto-memory", &e),
        }
    }

    // 5. summary
    ui.step(5, total, "Ready");
    let mark = |t: Option<bool>| match t {
        Some(true) => ui.sgr("32", "✓"),
        Some(false) => ui.sgr("31", "✗"),
        None => ui.dim("–"),
    };
    let mut lines = vec![
        format!("{} {}", ui.accent("◉"), ui.bold("NeuralGraph is set up")),
        String::new(),
        format!(
            "  {} model    {}",
            if server.is_some() { ui.sgr("32", "✓") } else { ui.dim("–") },
            server.as_ref().map_or("none yet".into(), Found::label)
        ),
        format!("  {} chat     {chat_model}", mark(tested.0)),
        format!("  {} embed    {embed_model}", mark(tested.1)),
        format!("  {} memory   {db}", ui.sgr("32", "✓")),
    ];
    for (name, r) in &connected {
        lines.push(format!("  {} {name}", if r.is_ok() { ui.sgr("32", "✓") } else { ui.sgr("31", "✗") }));
    }
    match auto {
        Some(true) => lines.push(format!("  {} auto     saves every Claude Code turn", ui.sgr("32", "✓"))),
        Some(false) => lines.push(format!("  {} auto     off (Claude saves only when it calls a tool)", ui.dim("–"))),
        None => {}
    }
    lines.extend([
        String::new(),
        format!("  {}", ui.dim("Web app     neuralgraph-memory open   →  http://127.0.0.1:8765/")),
        format!("  {}", ui.dim("Chat        neuralgraph-memory chat")),
        format!("  {}", ui.dim("Update      neuralgraph-memory update")),
        format!("  {}", ui.dim("Change any  neuralgraph-memory setup")),
    ]);
    boxed(&ui, &lines);
    if connected.iter().any(|(_, r)| r.is_ok()) {
        println!(
            "\n  {} Restart those tools, then ask: {}",
            ui.accent("→"),
            ui.bold("\"what do you remember about me?\"")
        );
    }
    let start = interactive && ui.confirm("Start NeuralGraph and open the dashboard now?", true);
    Ok(SetupOutcome { exit_code: 0, start_server: start })
}

/// The tools picked at the "Connect which?" prompt, as 0-based indexes: Enter/"all" = every one, "n"/"none" = none,
/// otherwise numbers separated by commas or spaces. `None` for anything else (the prompt asks again).
pub fn parse_pick(answer: &str, n: usize) -> Option<Vec<usize>> {
    let a = answer.trim().to_lowercase();
    if a.is_empty() || a == "a" || a == "all" {
        return Some((0..n).collect());
    }
    if a == "n" || a == "no" || a == "none" {
        return Some(vec![]);
    }
    let mut out = Vec::new();
    for part in a.split(|c: char| c == ',' || c.is_whitespace()).filter(|p| !p.is_empty()) {
        let k: usize = part.parse().ok()?;
        if !(1..=n).contains(&k) {
            return None;
        }
        if !out.contains(&(k - 1)) {
            out.push(k - 1);
        }
    }
    Some(out)
}

fn short(e: &str) -> String {
    let e = e.lines().next().unwrap_or(e);
    if e.chars().count() > 90 {
        format!("{}…", e.chars().take(90).collect::<String>())
    } else {
        e.to_string()
    }
}

// ------------------------------------------------------------------------------------------------ web UI helpers

/// This binary's absolute path (what MCP clients and hooks should launch).
pub fn current_exe() -> String {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.canonicalize().ok())
        .map(|p| p.display().to_string())
        .unwrap_or_else(|| SERVER_NAME.to_string())
}

/// Model servers that answer right now: `[{kind, label, url, models: {chat: [...], embedding: [...]}, recommended}]`.
pub async fn discover_servers(custom: Option<String>) -> Vec<Value> {
    let http = reqwest::Client::new();
    let mut out = Vec::new();
    for f in probe_all(&http, custom).await {
        let (chat, embed): (Vec<String>, Vec<String>) = f.models.iter().cloned().partition(|m| !is_embedding_model(m));
        let (rc, re) = f.recommended();
        let vision_rec =
            if f.kind == Kind::Ollama { crate::vision::OLLAMA_VISION.0 } else { crate::vision::LMSTUDIO_VISION.0 };
        out.push(json!({
            "kind": match f.kind { Kind::LmStudio => "lmstudio", Kind::Ollama => "ollama", Kind::Custom => "openai" },
            "label": f.label(),
            "url": f.url,
            "models": {"chat": chat, "embedding": embed, "vision": vision_models(&f).await},
            "sizes": installed_sizes(&f).await,
            "recommended": {"chat": rc, "embedding": re, "vision": vision_rec},
        }));
    }
    out
}

/// Whether our hooks are in Claude Code's settings.
pub fn claude_hooks_installed() -> bool {
    std::fs::read_to_string(claude_settings_path())
        .is_ok_and(|s| s.contains(SERVER_NAME) && s.contains("hook claude-code"))
}

/// Every supported tool: `[{name, found, connected, where}]`. Asks each CLI, so it takes about a second.
pub fn clients_status() -> Vec<Value> {
    detect_clients()
        .into_iter()
        .map(|(c, found, how)| {
            let connected = found
                && match &c {
                    Client::Cli { bin, .. } => {
                        Command::new(bin).args(["mcp", "get", SERVER_NAME]).output().is_ok_and(|o| o.status.success())
                    }
                    Client::Json { path, .. } => std::fs::read_to_string(path)
                        .ok()
                        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
                        .is_some_and(|v| v["mcpServers"].get(SERVER_NAME).is_some()),
                };
            json!({"name": c.name(), "found": found, "connected": connected, "where": how})
        })
        .collect()
}

/// Connect one tool by name (replacing an earlier entry), as `setup` does.
pub fn connect_client(name: &str) -> std::result::Result<String, String> {
    let (client, found, how) = detect_clients()
        .into_iter()
        .find(|(c, _, _)| c.name().eq_ignore_ascii_case(name))
        .ok_or_else(|| format!("unknown tool {name:?}"))?;
    if !found {
        return Err(format!("{} is not installed here ({how})", client.name()));
    }
    connect(None, &client, &current_exe())
}

/// One chat and one embedding call against the given server and models.
pub async fn test_models(url: &str, chat: &str, embed: &str) -> Value {
    let http = reqwest::Client::new();
    let t = Instant::now();
    let chat_r = match llm_generate(&http, "Reply with one word: ready", chat, url, 0.0, 16, 180.0).await {
        Ok(r) if r.trim().is_empty() => json!({
            "ok": false,
            "text": "answered with nothing. This model cannot be used for chat here; pick another one.",
        }),
        Ok(_) => json!({"ok": true, "text": format!("replied in {:.1}s", t.elapsed().as_secs_f64())}),
        Err(e) => json!({"ok": false, "text": short(&e)}),
    };
    let embed_r = match llm_embed(&http, "hello memory", embed, url, 120.0).await {
        Ok(v) if !v.is_empty() => json!({"ok": true, "text": format!("{} dimensions", v.len())}),
        Ok(_) => json!({"ok": false, "text": "empty vector (is this an embedding model?)"}),
        Err(e) => json!({"ok": false, "text": short(&e)}),
    };
    json!({"chat": chat_r, "embed": embed_r})
}

// ------------------------------------------------------------------------------------------------ the dashboard port

/// Whether `port` on loopback can be bound right now.
fn port_free(port: u16) -> bool {
    std::net::TcpListener::bind(("127.0.0.1", port)).is_ok()
}

/// Processes listening on `port` (via `lsof`; empty when it is unavailable).
fn listeners(port: u16) -> Vec<String> {
    // lsof lives in /usr/sbin on macOS, which a trimmed PATH may leave out
    let lsof = which("lsof").or_else(|| Some(PathBuf::from("/usr/sbin/lsof")).filter(|p| p.is_file()));
    let Some(lsof) = lsof else { return Vec::new() };
    Command::new(lsof)
        .args(["-t", "-nP", &format!("-iTCP:{port}"), "-sTCP:LISTEN"])
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).split_whitespace().map(str::to_string).collect())
        .unwrap_or_default()
}

/// Make the dashboard port usable before `setup` starts `serve`. A program keeps running its old code after its file
/// is replaced, so an earlier NeuralGraph may still hold the port after an update: offer to restart it with this
/// version. Returns false when the port stays taken (a message says why).
pub async fn free_dashboard_port(port: u16) -> bool {
    if port_free(port) {
        return true;
    }
    let ui = Ui::new(std::io::stdin().is_terminal());
    let url = format!("http://127.0.0.1:{port}/");
    let health: Option<Value> =
        match reqwest::Client::new().get(format!("{url}healthz")).timeout(Duration::from_secs(2)).send().await {
            Ok(r) => r.json().await.ok(),
            Err(_) => None,
        };
    let is_ours = health.as_ref().is_some_and(|h| h.get("worker_running").is_some());
    if !is_ours {
        ui.warn("Port busy", &format!("another program uses port {port}"));
        ui.note(&format!("Start NeuralGraph on another port: neuralgraph-memory serve --port {} --open", port + 1));
        return false;
    }
    let pids = listeners(port);
    let who = if pids.is_empty() { String::new() } else { format!(" (pid {})", pids.join(", ")) };
    ui.warn("Already on", &format!("a NeuralGraph started earlier is running at {url}{who}"));
    ui.note("It keeps running the version it started with until it is restarted.");
    if pids.is_empty() || !cfg!(unix) {
        ui.note("Stop it (Ctrl+C in its terminal), then run: neuralgraph-memory serve --open");
        return false;
    }
    if !ui.confirm(&format!("Restart it with this version (v{})?", env!("CARGO_PKG_VERSION")), true) {
        ui.note(&format!("Keeping it. Its dashboard is at {url}"));
        return false;
    }
    let _ = Command::new("kill").arg("-TERM").args(&pids).status();
    let freed = ui
        .spin("Stopping the earlier NeuralGraph…", async {
            for _ in 0..60 {
                if port_free(port) {
                    return true;
                }
                tokio::time::sleep(Duration::from_millis(250)).await;
            }
            false
        })
        .await;
    if freed {
        ui.ok("Stopped", &format!("the earlier NeuralGraph{who}"));
    } else {
        ui.fail(
            "Still running",
            "it did not stop within 15 s; stop it yourself, then run: neuralgraph-memory serve --open",
        );
    }
    freed
}

/// Stop the dashboard listening on `port` and start this binary's `serve` detached, logging to
/// `~/.neuralgraph/serve.log`. Used when the running dashboard is too old to restart itself.
pub async fn replace_dashboard(port: u16) -> bool {
    let pids = listeners(port);
    if pids.is_empty() {
        return false;
    }
    let _ = Command::new("kill").arg("-TERM").args(&pids).status();
    for _ in 0..60 {
        if port_free(port) {
            break;
        }
        tokio::time::sleep(Duration::from_millis(250)).await;
    }
    if !port_free(port) {
        return false;
    }
    spawn_dashboard()
}

/// Size on disk (GB) of every model the server has, by the id it lists them under.
async fn installed_sizes(srv: &Found) -> std::collections::HashMap<String, f64> {
    let mut out = std::collections::HashMap::new();
    if srv.kind == Kind::Ollama {
        let v: Option<Value> = async {
            reqwest::Client::new()
                .get(format!("{}/api/tags", srv.url))
                .timeout(Duration::from_secs(5))
                .send()
                .await
                .ok()?
                .json()
                .await
                .ok()
        }
        .await;
        for m in v.as_ref().and_then(|v| v.get("models")).and_then(Value::as_array).into_iter().flatten() {
            if let (Some(n), Some(sz)) = (m.get("name").and_then(Value::as_str), m.get("size").and_then(Value::as_f64))
            {
                out.insert(n.to_string(), sz / 1e9);
            }
        }
        return out;
    }
    let lms = which("lms").or_else(|| Some(home().join(".lmstudio/bin/lms")).filter(|p| p.is_file()));
    let Some(lms) = lms else { return out };
    let Ok(o) = tokio::process::Command::new(lms).args(["ls", "--json"]).output().await else { return out };
    let v: Value = serde_json::from_slice(&o.stdout).unwrap_or(Value::Null);
    for m in v.as_array().into_iter().flatten() {
        if let (Some(k), Some(sz)) =
            (m.get("modelKey").and_then(Value::as_str), m.get("sizeBytes").and_then(Value::as_f64))
        {
            out.insert(k.to_string(), sz / 1e9);
        }
    }
    out
}

/// `{ram_gb, disk_free_gb}` of this machine, for the settings page.
pub fn system_info() -> Value {
    json!({"ram_gb": ram_gb(), "disk_free_gb": disk_free_gb()})
}

/// Start this binary's `serve` detached from the terminal, logging to `~/.neuralgraph/serve.log`.
pub fn spawn_dashboard() -> bool {
    if let Some(dir) = config_path().parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let log_path = config_path().parent().map(|d| d.join("serve.log")).unwrap_or_else(|| PathBuf::from("serve.log"));
    let Ok(log) = std::fs::File::create(&log_path) else { return false };
    let Ok(err) = log.try_clone() else { return false };
    let mut cmd = Command::new(crate::update::exe_path());
    cmd.arg("serve").stdin(std::process::Stdio::null()).stdout(log).stderr(err);
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0); // not in the terminal's group: closing the terminal does not stop it
    }
    cmd.spawn().is_ok()
}

/// LM Studio's `lms` command: on PATH, or where LM Studio installs it.
pub fn lms_path() -> Option<PathBuf> {
    which("lms").or_else(|| Some(home().join(".lmstudio/bin/lms")).filter(|p| p.is_file()))
}

/// The recommended models for the server at `url`: `[{role, name, model, size_gb, installed}]`.
pub async fn suggested_models(url: &str) -> Vec<Value> {
    let http = reqwest::Client::new();
    let url = url.trim_end_matches('/');
    let Some(srv) = probe_all(&http, Some(url.to_string())).await.into_iter().find(|f| f.url == url) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for role in [Role::Chat, Role::Embedding, Role::Vision] {
        let Some(d) = srv.download(role) else { continue };
        let installed = srv
            .models
            .iter()
            .any(|m| m == &d.model || m.strip_prefix(d.model.as_str()).is_some_and(|r| r.starts_with(':')));
        out.push(json!({
            "role": role.name(), "name": d.name, "model": d.model, "size_gb": download_size(&srv, &d).await, "installed": installed,
        }));
    }
    out
}
