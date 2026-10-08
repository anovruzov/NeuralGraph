# Mycelic: one image for every process. The command selects the role:
#   serve  (API + SSE, optionally in-process worker and embedded holders)   worker   holder   migrate   backup ...
# Build from the repository root so both the Python packages and the frontend are in the context:
#   docker build -f deploy/mycelic/Dockerfile -t mycelic:local .
#
# Stage 1 builds the React frontend; stage 2 is the Python runtime with the built assets copied in, so the
# runtime image carries no Node toolchain. Everything runs as root on purpose: the image is used with named
# volumes, Fly volumes and Render disks, all of which arrive root-owned, and an entrypoint that chowns and
# drops privileges is more machinery than this single-tenant service warrants. Front it with a TLS proxy.

# Base images are build arguments so a registry mirror can be used where Docker Hub is rate-limited, e.g.
#   --build-arg NODE_IMAGE=mirror.gcr.io/library/node:22-alpine --build-arg PYTHON_IMAGE=mirror.gcr.io/library/python:3.11-slim
ARG NODE_IMAGE=node:22-alpine
ARG PYTHON_IMAGE=python:3.11-slim

# ---------------------------------------------------------------- stage 1: frontend
FROM ${NODE_IMAGE} AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
# Behind a TLS-intercepting proxy, pass its CA as a build secret (never stored in a layer) and the standard proxy
# build args (not kept in the image):  --secret id=build_ca,src=/path/ca.crt --build-arg HTTPS_PROXY=...
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/build_ca npm_config_cafile=/run/secrets/build_ca; fi; \
    npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- stage 2: runtime
FROM ${PYTHON_IMAGE} AS runtime
ARG BUILD_SHA=dev
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    MYCELIC_DATA_DIR=/data \
    MYCELIC_HOST=0.0.0.0 \
    MYCELIC_PORT=8780 \
    MYCELIC_BUILD_SHA=$BUILD_SHA
WORKDIR /app
# Dependencies first so a code change does not reinstall them. requirements-mycelic.txt pulls in requirements.txt.
COPY requirements.txt requirements-mycelic.txt /app/
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip install --no-cache-dir -r /app/requirements-mycelic.txt
COPY NeuralGraph /app/NeuralGraph
COPY mycelic /app/mycelic
COPY --from=frontend /build/dist /app/frontend/dist
RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8780
# Liveness only: the API answers /healthz as soon as it is up; /readyz is the deeper check (see docs/mycelic/RUNBOOK.md).
# worker and holder containers have no HTTP port, so compose disables this check for them.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('MYCELIC_PORT','8780'), timeout=3).status == 200 else 1)"
ENTRYPOINT ["python", "-m", "mycelic"]
CMD ["serve"]
