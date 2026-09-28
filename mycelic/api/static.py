"""Serve the built React application (``frontend/dist``) with the SPA fallback, or a page that says how to build it.

The catch-all is registered after every API route, so ``/api/...`` paths that reach it are unknown API routes (or a
known path with the wrong method, which aiohttp lets fall through to later resources); they get a JSON 404 rather
than ``index.html``, otherwise a typo in a client would receive HTML with status 200. Files are resolved inside the
dist directory only; ``index.html`` is sent with ``no-store`` so a deploy is picked up on the next load while hashed
assets under ``/assets`` may be cached.
"""
from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from .middleware import error_response

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIST = REPO_ROOT / "frontend" / "dist"

NOT_BUILT_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mycelic API is running</title>
<style>
  body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; max-width: 44rem; margin: 4rem auto; padding: 0 1.5rem; color: #1f2933; line-height: 1.5; }
  code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: #f1f4f8; border-radius: 4px; }
  code { padding: 0.1em 0.35em; }
  pre { padding: 0.9em 1em; overflow-x: auto; }
  h1 { font-size: 1.5rem; }
  a { color: #1d4ed8; }
</style>
</head>
<body>
<h1>Mycelic API is running; the web interface is not built</h1>
<p>The API answers under <code>/api/*</code>, liveness at <a href="/healthz">/healthz</a>, readiness at
<a href="/readyz">/readyz</a> and Prometheus metrics at <a href="/metrics">/metrics</a>.</p>
<p>The interface is a React application served from <code>frontend/dist</code>. Build it once (Node 22+):</p>
<pre>cd frontend
npm ci
npm run build</pre>
<p>then restart <code>python -m mycelic serve</code>. For development with hot reload run <code>npm run dev</code> in
<code>frontend/</code> and open <code>http://localhost:5173</code>; the Vite dev server proxies <code>/api</code> here.
The Docker image (<code>deploy/mycelic/Dockerfile</code>) builds the interface itself.</p>
<p>See <code>docs/mycelic/SETUP.md</code>, section 6.</p>
</body>
</html>
"""


def setup_static(app: web.Application, dist: Path | str | None = None) -> Path:
    """Register the asset route and the catch-all. Returns the directory that was looked at."""
    dist_dir = Path(dist).expanduser() if dist else DEFAULT_DIST
    index = dist_dir / "index.html"
    if index.is_file():
        root = dist_dir.resolve()
        assets = root / "assets"
        if assets.is_dir():
            app.router.add_static("/assets", assets, name="assets")

        async def spa(request: web.Request) -> web.StreamResponse:
            tail = request.match_info.get("tail", "")
            if tail.startswith("api/") or tail == "api":
                return error_response(404, "no such API route", "not_found")
            if tail:
                candidate = (root / tail).resolve()
                if candidate.is_file() and root in candidate.parents:
                    return web.FileResponse(candidate)
            return web.FileResponse(index, headers={"Cache-Control": "no-store"})

        app.router.add_get("/{tail:.*}", spa, name="spa")
        logger.info("serving frontend from %s", root)
    else:
        async def not_built(request: web.Request) -> web.StreamResponse:
            tail = request.match_info.get("tail", "")
            if tail.startswith("api/") or tail == "api":
                return error_response(404, "no such API route", "not_found")
            return web.Response(text=NOT_BUILT_HTML, content_type="text/html", headers={"Cache-Control": "no-store"})

        app.router.add_get("/{tail:.*}", not_built, name="spa")
        logger.info("frontend not built (%s missing); serving the build instructions page", index)
    return dist_dir


__all__ = ["setup_static", "DEFAULT_DIST", "NOT_BUILT_HTML"]
