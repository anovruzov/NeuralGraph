#!/bin/sh
# NeuralGraph installer: one command from nothing to a running setup wizard.
#
#   curl -fsSL https://raw.githubusercontent.com/anovruzov/NeuralGraph/main/install.sh | sh
#
# 1. Downloads the prebuilt `neuralgraph-memory` binary for this machine from the latest GitHub release and checks
#    its SHA-256. No compiler, no Rust.
# 2. If there is no prebuilt binary for this machine (or NEURALGRAPH_FROM_SOURCE=1), builds from source instead,
#    installing what that needs first: the Xcode command-line tools on macOS (a C compiler) and Rust via rustup.
# 3. Puts the binary in ~/.local/bin (adds it to PATH in your shell profile if needed).
# 4. Runs `neuralgraph-memory setup`, which finds or installs a local model server and connects your AI tools.
#
# Environment:
#   NEURALGRAPH_INSTALL_DIR   where the binary goes (default ~/.local/bin)
#   NEURALGRAPH_VERSION       release tag to install, e.g. v1.1.0 (default: latest)
#   NEURALGRAPH_FROM_SOURCE=1 skip the prebuilt download and build from source
#   NEURALGRAPH_SOURCE        build from this local checkout instead of downloading the source
#   NEURALGRAPH_REF           branch or tag to build when downloading the source (default main)
#   NEURALGRAPH_NO_SETUP=1    install only; do not start the setup wizard
#   NEURALGRAPH_RELEASE_URL   base URL of the release assets (default: this repo's GitHub releases)

set -eu

REPO="anovruzov/NeuralGraph"
BIN="neuralgraph-memory"
INSTALL_DIR="${NEURALGRAPH_INSTALL_DIR:-$HOME/.local/bin}"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    B="$(printf '\033[1m')"; D="$(printf '\033[2m')"; A="$(printf '\033[38;5;33m')"
    G="$(printf '\033[32m')"; Y="$(printf '\033[33m')"; R="$(printf '\033[31m')"; N="$(printf '\033[0m')"
else
    B=""; D=""; A=""; G=""; Y=""; R=""; N=""
fi
say()  { printf '%s\n' "$*"; }
step() { printf '\n%s%s%s %s%s%s\n' "$A" "›" "$N" "$B" "$*" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '  %s✗%s %s\n' "$R" "$N" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

printf '\n  %s●──●──●%s  %sNeuralGraph installer%s\n' "$A" "$N" "$B" "$N"
printf '  %slocal graph memory for Claude Code, Codex and friends%s\n' "$D" "$N"

have curl || die "curl is required"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT INT TERM

# ------------------------------------------------------------------ which binary this machine needs
OS="$(uname -s)"; ARCH="$(uname -m)"
case "$OS" in
    Darwin) os="apple-darwin" ;;
    Linux)  os="unknown-linux-gnu" ;;
    *) die "unsupported OS: $OS (Windows: use WSL, or build with cargo)" ;;
esac
case "$ARCH" in
    arm64|aarch64) arch="aarch64" ;;
    x86_64|amd64)  arch="x86_64" ;;
    *) die "unsupported CPU: $ARCH" ;;
esac
# an x86_64 shell under Rosetta on Apple silicon still wants the native binary
if [ "$OS" = Darwin ] && [ "$arch" = x86_64 ] && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = 1 ]; then
    arch="aarch64"
fi
TARGET="$arch-$os"

sha256() {
    if have shasum; then shasum -a 256 "$1" | cut -d' ' -f1; else sha256sum "$1" | cut -d' ' -f1; fi
}

# ------------------------------------------------------------------ 1. prebuilt
fetch_prebuilt() {
    if [ -n "${NEURALGRAPH_RELEASE_URL:-}" ]; then
        base="$NEURALGRAPH_RELEASE_URL"
    elif [ -n "${NEURALGRAPH_VERSION:-}" ]; then
        base="https://github.com/$REPO/releases/download/$NEURALGRAPH_VERSION"
    else
        base="https://github.com/$REPO/releases/latest/download"
    fi
    asset="$BIN-$TARGET.tar.gz"
    step "Downloading $BIN for $TARGET"
    curl -fsSL "$base/$asset" -o "$TMP/$asset" 2>/dev/null || return 1
    curl -fsSL "$base/$asset.sha256" -o "$TMP/$asset.sha256" 2>/dev/null || die "release has no checksum for $asset"
    want="$(cut -d' ' -f1 <"$TMP/$asset.sha256")"
    got="$(sha256 "$TMP/$asset")"
    [ "$want" = "$got" ] || die "checksum mismatch for $asset (expected $want, got $got)"
    ok "checksum verified"
    tar -xzf "$TMP/$asset" -C "$TMP"
    [ -f "$TMP/$BIN" ] || die "$asset does not contain $BIN"
    mkdir -p "$INSTALL_DIR"
    mv "$TMP/$BIN" "$INSTALL_DIR/$BIN"
    chmod +x "$INSTALL_DIR/$BIN"
    ok "installed $INSTALL_DIR/$BIN"
}

# ------------------------------------------------------------------ 2. from source
need_compiler() {
    if [ "$OS" = Darwin ]; then
        xcode-select -p >/dev/null 2>&1 && return 0
        step "Installing the Xcode command-line tools (Apple's C compiler)"
        say "  A macOS dialog opens: click ${B}Install${N}. This takes 5-15 minutes."
        xcode-select --install >/dev/null 2>&1 || true
        printf '  waiting'
        until xcode-select -p >/dev/null 2>&1; do printf '.'; sleep 10; done
        printf '\n'; ok "Xcode command-line tools ready"
    else
        have cc || have gcc || have clang && return 0
        die "a C compiler is needed: install it (Debian/Ubuntu: sudo apt install build-essential; Fedora: sudo dnf install gcc) and re-run"
    fi
}

need_rust() {
    [ -f "$HOME/.cargo/env" ] && . "$HOME/.cargo/env"
    have cargo && return 0
    step "Installing Rust (rustup, about 2 minutes)"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --no-modify-path >/dev/null
    . "$HOME/.cargo/env"
    ok "Rust $(rustc --version | cut -d' ' -f2)"
}

build_from_source() {
    need_compiler
    need_rust
    if [ -n "${NEURALGRAPH_SOURCE:-}" ]; then
        src="$NEURALGRAPH_SOURCE"
    else
        ref="${NEURALGRAPH_REF:-main}"
        step "Downloading the source ($ref)"
        # a tarball, not git: on a fresh Mac `git` itself is a stub that wants the Xcode tools
        curl -fsSL "https://github.com/$REPO/archive/$ref.tar.gz" -o "$TMP/src.tar.gz" || die "could not download $REPO@$ref"
        mkdir -p "$TMP/src"
        tar -xzf "$TMP/src.tar.gz" -C "$TMP/src" --strip-components 1
        src="$TMP/src"
    fi
    step "Building $BIN (3-5 minutes)"
    cargo build --release --locked --quiet -p "$BIN" --manifest-path "$src/Cargo.toml" --target-dir "$TMP/target"
    mkdir -p "$INSTALL_DIR"
    cp "$TMP/target/release/$BIN" "$INSTALL_DIR/$BIN"
    chmod +x "$INSTALL_DIR/$BIN"
    ok "installed $INSTALL_DIR/$BIN"
}

if [ "${NEURALGRAPH_FROM_SOURCE:-}" = 1 ] || [ -n "${NEURALGRAPH_SOURCE:-}" ]; then
    build_from_source
elif ! fetch_prebuilt; then
    warn "no prebuilt binary for $TARGET yet; building from source instead"
    build_from_source
fi

# ------------------------------------------------------------------ 3. PATH
case ":$PATH:" in
    *":$INSTALL_DIR:"*) ;;
    *)
        case "${SHELL:-}" in
            */zsh)  rc="$HOME/.zshrc" ;;
            */bash) if [ "$OS" = Darwin ]; then rc="$HOME/.bash_profile"; else rc="$HOME/.bashrc"; fi ;;
            */fish) rc="" ;;
            *)      rc="$HOME/.profile" ;;
        esac
        line="export PATH=\"$INSTALL_DIR:\$PATH\""
        if [ -n "$rc" ] && ! grep -qsF "$line" "$rc"; then
            mkdir -p "$(dirname "$rc")"
            printf '\n# added by the NeuralGraph installer\n%s\n' "$line" >>"$rc"
            ok "added $INSTALL_DIR to PATH in $rc (new terminals pick it up)"
        elif [ -z "$rc" ]; then
            warn "add $INSTALL_DIR to your PATH: fish_add_path $INSTALL_DIR"
        fi
        export PATH="$INSTALL_DIR:$PATH"
        ;;
esac

# ------------------------------------------------------------------ 4. setup wizard
if [ "${NEURALGRAPH_NO_SETUP:-}" = 1 ]; then
    say ""; ok "done. Next: $BIN setup"
    exit 0
fi
# `curl … | sh` makes the script our stdin; the wizard needs the keyboard
if [ -r /dev/tty ] && (: </dev/tty) 2>/dev/null; then
    exec "$INSTALL_DIR/$BIN" setup </dev/tty
fi
say ""; ok "installed. No terminal attached, so run the wizard yourself: $BIN setup"
