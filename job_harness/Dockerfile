# Job harness: Python + Playwright Chromium.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    HARNESS_DB=/data/harness.db \
    HARNESS_LOG_DIR=/data/logs \
    BROWSER_PROFILE_DIR=/data/browser_profile \
    DASHBOARD_HOST=0.0.0.0 \
    DASHBOARD_PORT=8765

# poppler-utils gives pdftotext, used to read the resume.
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps chromium

COPY . /app/job_harness/
WORKDIR /app/job_harness

# Persist the database, logs, screenshots and the browser profile.
VOLUME ["/data"]
RUN mkdir -p /data/logs /data/browser_profile

EXPOSE 8765

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8765/healthz || exit 1

# Dry run by default: submitting for real must be an explicit choice.
CMD ["python", "run.py", "--dry-run", "--loop"]
