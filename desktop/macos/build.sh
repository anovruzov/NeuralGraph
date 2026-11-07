#!/bin/sh
# Build NeuralGraph.app (native shell + bundled neuralgraph-memory) and optionally install it.
#   desktop/macos/build.sh             build into desktop/macos/build/NeuralGraph.app
#   desktop/macos/build.sh --install   also copy it to /Applications (or ~/Applications if that is not writable)
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
out="$here/build/NeuralGraph.app"
bin="${NEURALGRAPH_MEMORY_BIN:-}"
if [ -z "$bin" ]; then
    (cd "$repo" && cargo build --release -p neuralgraph-memory --bin neuralgraph-memory)
    bin="$repo/target/release/neuralgraph-memory"
fi
rm -rf "$out"
mkdir -p "$out/Contents/MacOS" "$out/Contents/Resources"
cp "$here/NeuralGraph/Info.plist" "$out/Contents/Info.plist"
swiftc -O -swift-version 5 -target "$(uname -m)-apple-macos13.0" "$here/NeuralGraph/main.swift" -o "$out/Contents/MacOS/NeuralGraph"
cp "$bin" "$out/Contents/Resources/neuralgraph-memory"
codesign --force --deep --sign - "$out" >/dev/null
echo "built $out"
if [ "${1:-}" = "--install" ]; then
    dest=/Applications
    [ -w "$dest" ] || { dest="$HOME/Applications"; mkdir -p "$dest"; }
    rm -rf "$dest/NeuralGraph.app"
    cp -R "$out" "$dest/"
    echo "installed $dest/NeuralGraph.app"
fi
