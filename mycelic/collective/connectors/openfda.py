"""openFDA device connector: fetch device adverse-event reports or recalls into a local, hash-checked cache.

    python -m mycelic.collective.connectors.openfda fetch --dataset event|recall --product-codes A,B,C
        --date-from YYYYMMDD --date-to YYYYMMDD --out DIR [--base-url https://api.fda.gov] [--limit 1000]
        [--max-records N] [--api-key-env OPENFDA_API_KEY] [--dry-run]

N1 (STRATEGY section 11.2) needs real openFDA narratives, and api.fda.gov is unreachable from the sandbox this was
written in, so the founder runs this on his own network. One query per product code::

    event:  device.device_report_product_code:"<CODE>" AND date_received:[<from> TO <to>]
    recall: product_code:"<CODE>" AND event_date_initiated:[<from> TO <to>]

Paging advances ``skip`` by the number of results received and stops on an empty page, at ``meta.results.total``,
at ``--max-records`` (per code; recorded as truncated, reason ``max_records``), or when the server answers 400
``Skip value must N or less`` (recorded as truncated, reason ``api_max_skip``, with ``max_skip`` and total vs
fetched). A 404 ``NOT_FOUND`` means no records (total 0). Any other 4xx (and any 3xx: redirects are refused, so an
api key is never forwarded to another host) is an error naming the status and ``error.code`` only. 429 and 5xx
are retried up to 6 times (``Retry-After`` up to 60 s, else 1, 2, 4, 8, 16, 32 s); an unreachable host gets 2
retries and then exit 3.

The api key, if any, is read from the environment variable named by ``--api-key-env`` and added on the wire only:
it is in no recorded URL, file, message or log. Each page is cached as the raw response bytes under
``<out>/pages/<dataset path with '/' as '_'>/<CODE>/page-<skip:07d>.json``; ``<out>/manifest.json`` (url without
key, sha256 and size of every page, per-code totals, field coverage) is written last, so a cache without a
manifest is incomplete and :func:`load_cache` refuses it. ``data_label`` is ``public`` only when the base URL is
the real openFDA host; anything else (a local test server) is ``synthetic``.

This module keeps its own UTC clock for ``fetched_at`` because connectors do not import the experiments package.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import quote_plus, urlencode, urlsplit

from ..inference.client import is_private_host, proxy_for
from ..jsonio import StrictJsonError, canonical_dumps, sha256_hex, strict_load

DEFAULT_BASE_URL = "https://api.fda.gov"
CONNECTOR_VERSION = 1
DATASETS = {
    "event": ("device/event", "device.device_report_product_code", "date_received"),
    "recall": ("device/recall", "product_code", "event_date_initiated"),
}
MAX_LIMIT = 1000
TIMEOUT_S = 60
STATUS_RETRIES = 6
NETWORK_RETRIES = 2
RETRY_AFTER_CAP_S = 60
MAX_PAGE_BYTES = 256 * 1024 * 1024
CODE_RE = re.compile(r"[A-Z]{3}", re.ASCII)
DATE_RE = re.compile(r"[0-9]{8}", re.ASCII)
_SKIP_RE = re.compile(r"Skip value must ([0-9]+) or less", re.ASCII)
_ERROR_CODE_RE = re.compile(r"[A-Z_]{1,40}", re.ASCII)
COVERAGE_FIELDS = ("lot_number", "model_number", "product_problems", "mdr_text")


class OpenFDAError(Exception):
    """A fetch failure with the exit code the CLI uses (2: usage or HTTP error; 3: host unreachable)."""

    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class CacheError(ValueError):
    pass


def utc_now() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


# --------------------------------------------------------------------------------------------------- validation

def check_query(dataset: str, product_codes: list[str], date_from: str, date_to: str, limit: int,
                max_records: int | None, base_url: str) -> str:
    """Validate the query; returns the base URL without a trailing slash. Raises OpenFDAError (exit 2)."""
    if dataset not in DATASETS:
        raise OpenFDAError("--dataset must be event or recall") from None
    if not product_codes or any(CODE_RE.fullmatch(c) is None for c in product_codes):
        raise OpenFDAError("--product-codes must be comma-separated three-letter upper-case codes") from None
    if len(set(product_codes)) != len(product_codes):
        raise OpenFDAError("--product-codes lists a code twice") from None
    for name, value in (("--date-from", date_from), ("--date-to", date_to)):
        valid = DATE_RE.fullmatch(value or "") is not None
        if valid:
            try:
                datetime.strptime(value, "%Y%m%d")
            except ValueError:
                valid = False
        if not valid:
            raise OpenFDAError(f"{name} must be a calendar date YYYYMMDD") from None
    if date_from > date_to:
        raise OpenFDAError("--date-from must not be after --date-to") from None
    if not 1 <= limit <= MAX_LIMIT:
        raise OpenFDAError(f"--limit must be in [1, {MAX_LIMIT}]") from None
    if max_records is not None and max_records < 1:
        raise OpenFDAError("--max-records must be >= 1") from None
    parts = urlsplit(base_url)
    if (parts.scheme not in ("http", "https") or not parts.hostname or "@" in parts.netloc or parts.query
            or parts.fragment or any(ord(c) <= 0x20 or ord(c) >= 0x7f for c in base_url)):
        raise OpenFDAError("--base-url must be a plain http(s) URL without credentials, query or fragment") from None
    return base_url[:-1] if base_url.endswith("/") else base_url


def page_url(base_url: str, dataset: str, code: str, date_from: str, date_to: str, limit: int, skip: int) -> str:
    """The recorded (keyless) URL of one page."""
    path, code_field, date_field = DATASETS[dataset]
    search = f'{code_field}:"{code}" AND {date_field}:[{date_from} TO {date_to}]'
    query = urlencode([("search", search), ("limit", str(limit)), ("skip", str(skip))], quote_via=quote_plus)
    return f"{base_url}/{path}.json?{query}"


# --------------------------------------------------------------------------------------------------- HTTP

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def build_opener(base_url: str, environ: Mapping[str, str] | None = None) -> urllib.request.OpenerDirector:
    """Environment proxies except for private hosts (decided here, not by mutating os.environ); no redirects."""
    proxy = proxy_for(base_url, environ)
    scheme = urlsplit(base_url).scheme
    return urllib.request.build_opener(urllib.request.ProxyHandler({scheme: proxy} if proxy else {}), _NoRedirect())


def _read_capped(fh: Any) -> bytes:
    """A response body up to MAX_PAGE_BYTES. Read errors propagate (the caller retries them as network failures),
    including a body cut short of its Content-Length, which ``http.client`` would otherwise return silently."""
    data = fh.read(MAX_PAGE_BYTES + 1)
    if len(data) > MAX_PAGE_BYTES:
        raise OpenFDAError(f"a page exceeded {MAX_PAGE_BYTES} bytes") from None
    if getattr(fh, "length", None):
        raise http.client.IncompleteRead(b"") from None
    return data


def _error_body(err: urllib.error.HTTPError) -> bytes:
    """An error response's body, for its ``error.code``/``error.message``; empty if it cannot be read."""
    try:
        return err.read(65536)
    except (OSError, http.client.HTTPException, AttributeError):
        return b""


def _backoff(retry: int, retry_after: str | None) -> float:
    value = (retry_after or "").strip()
    if re.fullmatch(r"[0-9]{1,6}", value, re.ASCII):
        return float(min(int(value), RETRY_AFTER_CAP_S))
    return float(2 ** (retry - 1))


def _get(opener: urllib.request.OpenerDirector, url: str, origin: str,
         sleep: Callable[[float], Any]) -> tuple[int, bytes]:
    status_retries = network_retries = 0
    while True:
        failure = None
        try:
            with opener.open(url, timeout=TIMEOUT_S) as resp:
                return resp.status, _read_capped(resp)
        except urllib.error.HTTPError as err:
            status, retry_after = err.code, err.headers.get("Retry-After") if err.headers else None
            body = _error_body(err)
            err.close()
            if (status == 429 or 500 <= status <= 599) and status_retries < STATUS_RETRIES:
                status_retries += 1
                sleep(_backoff(status_retries, retry_after))
                continue
            return status, body
        except urllib.error.URLError as err:
            reason = err.reason
            failure = reason.__class__.__name__ if isinstance(reason, BaseException) else "URLError"
        except (OSError, http.client.HTTPException) as err:
            failure = err.__class__.__name__
        if network_retries < NETWORK_RETRIES:
            network_retries += 1
            sleep(float(network_retries))
            continue
        raise OpenFDAError(f"cannot reach {origin}: {failure}; run from a network that can reach it", 3) from None


def _error_field(body: bytes, field: str) -> str | None:
    try:
        doc = json.loads(body)
    except (ValueError, RecursionError):
        return None
    err = doc.get("error") if isinstance(doc, dict) else None
    value = err.get(field) if isinstance(err, dict) else None
    return value if isinstance(value, str) else None


# --------------------------------------------------------------------------------------------------- coverage

def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def record_coverage(record: dict[str, Any]) -> dict[str, bool]:
    devices = [d for d in record.get("device") or [] if isinstance(d, dict)]
    texts = [t for t in record.get("mdr_text") or [] if isinstance(t, dict)]
    return {
        "lot_number": any(_nonempty(d.get("lot_number")) for d in devices),
        "model_number": any(_nonempty(d.get("model_number")) for d in devices),
        "product_problems": any(_nonempty(p) for p in record.get("product_problems") or []),
        "mdr_text": any(_nonempty(t.get("text")) for t in texts),
    }


def _coverage_block(counts: dict[str, int], records: int) -> dict[str, Any]:
    block: dict[str, Any] = {"records": records}
    for field in COVERAGE_FIELDS:
        block[field] = {"count": counts[field], "share": round(counts[field] / records, 6) if records else None}
    return block


# --------------------------------------------------------------------------------------------------- fetch

def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def fetch(dataset: str, product_codes: list[str], date_from: str, date_to: str, out: str | Path, *,
          base_url: str = DEFAULT_BASE_URL, limit: int = MAX_LIMIT, max_records: int | None = None,
          api_key_env: str | None = None, opener: urllib.request.OpenerDirector | None = None,
          sleep: Callable[[float], Any] | None = None, clock: Callable[[], str] = utc_now,
          environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    base = check_query(dataset, product_codes, date_from, date_to, limit, max_records, base_url)
    sleep = sleep or time.sleep
    out = Path(out)
    if out.exists():
        raise OpenFDAError(f"--out already exists: {out} (pick a new directory)") from None
    env = os.environ if environ is None else environ
    key = None
    if api_key_env:
        key = env.get(api_key_env)
        if not key:
            raise OpenFDAError(f"environment variable {api_key_env} is unset or empty") from None
    parts = urlsplit(base)
    origin = f"{parts.scheme}://{parts.netloc}"
    opener = opener or build_opener(base, environ)
    path, _, _ = DATASETS[dataset]
    pages: list[dict[str, Any]] = []
    per_code: dict[str, dict[str, Any]] = {}
    coverage = {code: dict.fromkeys(COVERAGE_FIELDS, 0) for code in product_codes}

    for code in product_codes:
        skip = fetched = 0
        total: int | None = None
        truncated, reason, max_skip = False, None, None
        while True:
            if max_records is not None and fetched >= max_records:
                if total is None or fetched < total:
                    truncated, reason = True, "max_records"
                break
            lim = limit if max_records is None else min(limit, max_records - fetched)
            url = page_url(base, dataset, code, date_from, date_to, lim, skip)
            status, body = _get(opener, url + ("&api_key=" + quote_plus(key) if key else ""), origin, sleep)
            if status == 404 and _error_field(body, "code") == "NOT_FOUND":
                total = 0 if total is None else total
                break
            if status == 400:
                match = _SKIP_RE.search(_error_field(body, "message") or "")
                if match:
                    truncated, reason, max_skip = True, "api_max_skip", int(match.group(1))
                    break
            if status != 200:
                code_text = _error_field(body, "code")
                code_text = code_text if code_text and _ERROR_CODE_RE.fullmatch(code_text) else None
                hint = " (redirects are refused)" if 300 <= status < 400 else ""
                raise OpenFDAError(f"HTTP {status}{hint} for product code {code}, error code {code_text}") from None
            try:
                doc = json.loads(body)
                results = doc["results"]
                total = int(doc["meta"]["results"]["total"])
            except (ValueError, RecursionError, KeyError, TypeError):
                results = None
            if not isinstance(results, list):
                raise OpenFDAError(f"unexpected page shape for product code {code} at skip {skip}") from None
            if not results:
                break
            page_path = Path("pages") / path.replace("/", "_") / code / f"page-{skip:07d}.json"
            (out / page_path).parent.mkdir(parents=True, exist_ok=True)
            (out / page_path).write_bytes(body)
            pages.append({"product_code": code, "url": url, "skip": skip, "status": status,
                          "path": page_path.as_posix(), "bytes": len(body), "sha256": sha256_hex(body),
                          "results": len(results)})
            if dataset == "event":
                for record in results:
                    if isinstance(record, dict):
                        for field, present in record_coverage(record).items():
                            coverage[code][field] += present
            fetched += len(results)
            skip += len(results)
            if skip >= total:
                break
        per_code[code] = {"total": total, "fetched": fetched, "truncated": truncated, "reason": reason,
                          "max_skip": max_skip}

    cov = None
    if dataset == "event":
        overall = {f: sum(coverage[c][f] for c in product_codes) for f in COVERAGE_FIELDS}
        n_all = sum(per_code[c]["fetched"] for c in product_codes)
        cov = {"overall": _coverage_block(overall, n_all),
               "per_code": {c: _coverage_block(coverage[c], per_code[c]["fetched"]) for c in product_codes}}
    manifest = {
        "kind": "openfda_cache", "schema_version": 1, "connector_version": CONNECTOR_VERSION, "base_url": base,
        "dataset": dataset, "endpoint": path,
        "query": {"product_codes": list(product_codes), "date_from": date_from, "date_to": date_to, "limit": limit,
                  "max_records": max_records},
        "fetched_at": clock(), "data_label": "public" if base == DEFAULT_BASE_URL else "synthetic",
        "pages": pages, "per_code": per_code, "coverage": cov,
    }
    out.mkdir(parents=True, exist_ok=True)
    _write_atomic(out / "manifest.json", (canonical_dumps(manifest) + "\n").encode("utf-8"))
    return manifest


# --------------------------------------------------------------------------------------------------- cache

class Cache:
    def __init__(self, directory: Path, manifest: dict[str, Any], manifest_sha256: str) -> None:
        self.directory = directory
        self.manifest = manifest
        self.manifest_sha256 = manifest_sha256

    def records(self) -> Iterator[tuple[str, dict[str, Any]]]:
        """(product code, record) in page order; every page's sha256 is re-checked as it is read."""
        for page in self.manifest["pages"]:
            data = _page_bytes(self.directory, page)
            for record in json.loads(data)["results"]:
                yield page["product_code"], record


def _page_bytes(directory: Path, page: dict[str, Any]) -> bytes:
    rel = Path(page["path"])
    if rel.is_absolute() or ".." in rel.parts:
        raise CacheError(f"page path escapes the cache: {page['path']}") from None
    try:
        data = (directory / rel).read_bytes()
    except OSError:
        data = None
    if data is None:
        raise CacheError(f"missing page {page['path']}") from None
    if sha256_hex(data) != page["sha256"]:
        raise CacheError(f"sha256 mismatch for {page['path']}") from None
    return data


def read_manifest(directory: str | Path) -> tuple[dict[str, Any], str]:
    path = Path(directory) / "manifest.json"
    if not path.is_file():
        raise CacheError(f"no manifest.json in {directory}: the fetch did not finish") from None
    data = path.read_bytes()
    try:
        manifest = strict_load(data)
    except StrictJsonError as err:
        raise CacheError(f"manifest.json is not strict JSON ({err})") from None
    ok = (isinstance(manifest, dict) and manifest.get("kind") == "openfda_cache"
          and manifest.get("schema_version") == 1 and manifest.get("dataset") in DATASETS
          and isinstance(manifest.get("pages"), list) and isinstance(manifest.get("per_code"), dict)
          and isinstance(manifest.get("query"), dict) and isinstance(manifest["query"].get("product_codes"), list)
          and all(isinstance(p, dict) and {"product_code", "path", "sha256"} <= set(p) for p in manifest["pages"]))
    if not ok:
        raise CacheError("manifest.json is not an openFDA cache manifest") from None
    return manifest, sha256_hex(data)


def load_cache(directory: str | Path) -> Cache:
    """Open a cache, checking the manifest and every page's sha256 before anything is used."""
    directory = Path(directory)
    manifest, digest = read_manifest(directory)
    for page in manifest["pages"]:
        _page_bytes(directory, page)
    return Cache(directory, manifest, digest)


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mycelic.collective.connectors.openfda",
                                description="Fetch openFDA device data into a hash-checked local cache.")
    sub = p.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fetch", help="fetch device events or recalls")
    f.add_argument("--dataset", required=True, choices=sorted(DATASETS))
    f.add_argument("--product-codes", required=True, help="comma-separated three-letter product codes")
    f.add_argument("--date-from", required=True, help="YYYYMMDD")
    f.add_argument("--date-to", required=True, help="YYYYMMDD")
    f.add_argument("--out", required=True, help="cache directory to create")
    f.add_argument("--base-url", default=DEFAULT_BASE_URL)
    f.add_argument("--limit", type=int, default=MAX_LIMIT)
    f.add_argument("--max-records", type=int, default=None, help="per product code")
    f.add_argument("--api-key-env", default=None, help="name of the environment variable holding an openFDA key")
    f.add_argument("--dry-run", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    codes = [c.strip() for c in args.product_codes.split(",") if c.strip()]
    try:
        base = check_query(args.dataset, codes, args.date_from, args.date_to, args.limit, args.max_records,
                           args.base_url)
        if Path(args.out).exists():
            raise OpenFDAError(f"--out already exists: {args.out} (pick a new directory)") from None
        if args.dry_run:
            print("dry-run: openfda fetch")
            if args.api_key_env and not os.environ.get(args.api_key_env):
                print(f"would need: env {args.api_key_env}")
            parts = urlsplit(base)
            port = parts.port or (443 if parts.scheme == "https" else 80)
            where = "" if is_private_host(parts.hostname or "") else " (a network that can reach it)"
            print(f"would need: network {parts.hostname}:{port}{where}")
            path = DATASETS[args.dataset][0].replace("/", "_")
            for code in codes:
                print(f"would write: {Path(args.out) / 'pages' / path / code}/page-*.json")
            print(f"would write: {Path(args.out) / 'manifest.json'}")
            return 0
        manifest = fetch(args.dataset, codes, args.date_from, args.date_to, args.out, base_url=base,
                         limit=args.limit, max_records=args.max_records, api_key_env=args.api_key_env)
    except OpenFDAError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    for code, info in manifest["per_code"].items():
        note = f", truncated ({info['reason']})" if info["truncated"] else ""
        print(f"openfda: {code} fetched {info['fetched']} of {info['total']}{note}")
    print(f"openfda: wrote {Path(args.out) / 'manifest.json'} (data_label={manifest['data_label']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
