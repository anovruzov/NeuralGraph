# Cross-chat memory server: worker + dashboard + REST + MCP (Streamable HTTP) in one container.
# Build from the repository root:  docker build -f deploy/chat_memory/Dockerfile -t neuralgraph-memory .
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    NEURALGRAPH_MEMORY_DB=/data/chat_memory.db \
    LLM_BASE_URL=http://ollama:11434 LLM_MODEL=qwen2.5:7b-instruct EMBED_MODEL=nomic-embed-text
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir aiohttp numpy rank-bm25
COPY NeuralGraph /app/NeuralGraph
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3).status == 200 else 1)"
ENTRYPOINT ["python", "-m", "NeuralGraph.chat_memory", "serve", "--host", "0.0.0.0", "--port", "8765"]
CMD ["--parallel", "4"]
