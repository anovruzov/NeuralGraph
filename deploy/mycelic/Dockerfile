# Mycelic service image: API + outbox publisher + JetStream consumer/aggregator in one process.
# Build from the repository root:  docker build -f deploy/mycelic/Dockerfile -t mycelic:local .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    MYCELIC_HOST=0.0.0.0 MYCELIC_PORT=8080 MYCELIC_DB_PATH=/data/mycelic.db

WORKDIR /app
COPY requirements-runtime.txt /app/requirements-runtime.txt
# Behind a TLS-inspecting corporate proxy, pass its CA bundle as a build secret so pip can reach PyPI:
#   docker build --secret id=ca_bundle,src=/path/to/corporate-ca.pem ...   (compose: CA_BUNDLE_FILE in .env)
# Without the secret the mount is absent or empty and pip uses its normal trust store.
RUN --mount=type=secret,id=ca_bundle,required=false \
    sh -c 'if [ -s /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
           pip install --no-cache-dir -r /app/requirements-runtime.txt'

COPY NeuralGraph /app/NeuralGraph
COPY mycelic /app/mycelic

# Run as a dedicated non-root user; /data is prepared so a fresh named volume is writable on first start.
RUN useradd -r -u 10001 -s /usr/sbin/nologin mycelic && mkdir -p /data && chown mycelic:mycelic /data
VOLUME ["/data"]
USER mycelic
EXPOSE 8080

# python:3.11-slim has no curl/wget: probe with the standard library. /health answers 200 while the database is open.
# The probe speaks HTTPS (without verifying the certificate) when the service is configured to serve TLS itself.
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=4 \
  CMD python -c "import os,ssl,sys,urllib.request; tls=bool(os.environ.get('MYCELIC_TLS_CERT_FILE')); ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE; sys.exit(0 if urllib.request.urlopen(('https' if tls else 'http')+'://127.0.0.1:8080/health', timeout=4, context=ctx if tls else None).status == 200 else 1)"

ENTRYPOINT ["python", "-m", "mycelic"]
CMD ["serve"]
