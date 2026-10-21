# Cross-chat memory server: worker + dashboard + REST + MCP (Streamable HTTP) in one container.
# Build from the repository root:  docker build -f deploy/chat_memory/Dockerfile -t neuralgraph-memory .

# --- build: one static-ish release binary (SQLite is compiled in, TLS is rustls) ---
FROM rust:1-slim-bookworm AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock rustfmt.toml ./
COPY crates crates
# the demos are cargo examples of the crate; the manifest needs their sources even for a binary build
COPY demo/chat_memory_demo.rs demo/chat_memory_live_demo.rs demo/
RUN cargo build --release --locked --bin neuralgraph-memory

# --- runtime ---
FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/target/release/neuralgraph-memory /usr/local/bin/neuralgraph-memory
ENV NEURALGRAPH_MEMORY_DB=/data/chat_memory.db \
    LLM_BASE_URL=http://ollama:11434 LLM_MODEL=qwen2.5:7b-instruct EMBED_MODEL=nomic-embed-text
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fsS http://127.0.0.1:8765/healthz > /dev/null || exit 1
ENTRYPOINT ["neuralgraph-memory", "serve", "--host", "0.0.0.0", "--port", "8765"]
CMD ["--parallel", "4"]
