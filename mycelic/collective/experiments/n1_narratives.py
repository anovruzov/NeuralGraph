"""N1: how often does a device-event narrative carry information that the coded fields do not? (STRATEGY 11.2)

    python -m mycelic.collective.experiments.n1_narratives sample --cache DIR --product-codes A,B,C --run-id ID
        [--n 300] [--seed 1] [--text-types "Description of Event or Problem"] [--runs-dir runs] [--dry-run]
    python -m mycelic.collective.experiments.n1_narratives score --sheet LABELLED.csv --sample sample.json
        --out narrative_gain.json [--dry-run]

``sample`` draws a stratified, seeded sample of openFDA device events from a cache written by
``connectors/openfda.py fetch --dataset event`` and writes a labelling sheet (``sheet.csv`` for a spreadsheet,
``sheet.jsonl`` canonical) plus ``sample.json``:

* 3 to 5 product codes, each present in the cache; events are deduplicated by ``mdr_report_key`` and an event
  stays with the first code in the order given;
* an event is eligible when at least one ``mdr_text`` entry has a selected ``text_type_code`` and non-empty text;
  exclusions are counted per code (``no_mdr_text``, ``no_selected_text_type``, ``duplicate``, ``no_report_key``);
* each code gets ``n // k`` events and the first ``n % k`` codes one more; a code short of its share gives all it
  has and records the shortfall (no reallocation); within a code ``random.Random("n1:<seed>:<code>").sample`` over
  the sorted keys, then the whole sheet is shuffled with ``random.Random("n1:<seed>:order")``;
* the narrative joins the selected entries ordered by text type (as given) and then ``mdr_text_key``.

The labeller fills four columns with true/false for whether the narrative names something absent from the coded
fields (component, failure mode, lot, use condition). ``score`` reads the labelled CSV (a spreadsheet's UTF-8 BOM
is accepted), refuses unlabelled rows and any key set that differs from the sample, and writes
``narrative_gain.json``: the share of events with any true label, its Wilson 95% interval, per-column and
per-code shares, and the verdict against the pre-registered bars (at least 20% supports the extraction channel;
below 10% expect structured codes S to match extraction X; between is ambiguous), computed in integers
(``5k >= n``, ``10k < n``). ``measurement`` is true only for a cache fetched from the real openFDA host.
"""
from __future__ import annotations

import argparse
import csv
import io
import random
import sys
from pathlib import Path
from typing import Any

from ..connectors.openfda import CacheError, load_cache, read_manifest
from ..jsonio import StrictJsonError, canonical_dumps, load_json_file, sha256_hex
from ..stats import wilson
from .common import DryRun, UsageError, code_stamps, fail, run_dir, split_list, write_json_atomic

CLI_SAMPLE = "n1_narratives sample"
CLI_SCORE = "n1_narratives score"
DEFAULT_TEXT_TYPE = "Description of Event or Problem"
LABEL_COLUMNS = ("component_not_in_codes", "failure_mode_not_in_codes", "lot_not_in_codes",
                 "use_condition_not_in_codes")
COLUMNS = ("row", "mdr_report_key", "product_code", "date_received", "event_type", "generic_names", "model_numbers",
           "lot_numbers", "product_problems", "narrative", *LABEL_COLUMNS, "labeller_note")
LIST_COLUMNS = ("generic_names", "model_numbers", "lot_numbers", "product_problems")
TRUE_TOKENS = ("true", "yes", "y", "1")
FALSE_TOKENS = ("false", "no", "n", "0")
CAVEAT = "MAUDE holds reportable events, not internal complaints."
BARS = {"supports_at_or_above": 0.20, "expect_S_matches_X_below": 0.10}
TRUNCATION_WARNING = "the sample covers fetched records only; the cache was truncated for these codes"


# --------------------------------------------------------------------------------------------------- sample

def _texts(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in record.get("mdr_text") or [] if isinstance(t, dict)]


def _selected_texts(record: dict[str, Any], text_types: list[str]) -> list[dict[str, Any]]:
    return [t for t in _texts(record)
            if t.get("text_type_code") in text_types and isinstance(t.get("text"), str) and t["text"].strip()]


def _text_order(entry: dict[str, Any], text_types: list[str]) -> tuple[int, int, int, str]:
    key = str(entry.get("mdr_text_key") or "")
    numeric = key.isascii() and key.isdigit()
    return text_types.index(entry["text_type_code"]), 0 if numeric else 1, int(key) if numeric else 0, key


def narrative(record: dict[str, Any], text_types: list[str]) -> str:
    entries = sorted(_selected_texts(record, text_types), key=lambda e: _text_order(e, text_types))
    return "\n\n".join(e["text"].strip() for e in entries)


def _unique(values: list[Any]) -> list[str]:
    out: list[str] = []
    for v in values:
        if isinstance(v, str) and v.strip() and v.strip() not in out:
            out.append(v.strip())
    return out


def sheet_row(record: dict[str, Any], code: str, text_types: list[str]) -> dict[str, Any]:
    devices = [d for d in record.get("device") or [] if isinstance(d, dict)]
    row: dict[str, Any] = {
        "mdr_report_key": _key(record), "product_code": code,
        "date_received": record.get("date_received") if isinstance(record.get("date_received"), str) else None,
        "event_type": record.get("event_type") if isinstance(record.get("event_type"), str) else None,
        "generic_names": _unique([d.get("generic_name") for d in devices]),
        "model_numbers": _unique([d.get("model_number") for d in devices]),
        "lot_numbers": _unique([d.get("lot_number") for d in devices]),
        "product_problems": _unique(list(record.get("product_problems") or [])),
        "narrative": narrative(record, text_types),
    }
    for column in LABEL_COLUMNS:
        row[column] = None
    row["labeller_note"] = None
    return row


def _status(record: dict[str, Any], text_types: list[str]) -> str:
    if not any(isinstance(t.get("text"), str) and t["text"].strip() for t in _texts(record)):
        return "no_mdr_text"
    if not _selected_texts(record, text_types):
        return "no_selected_text_type"
    return "eligible"


def _key(record: Any) -> str | None:
    key = record.get("mdr_report_key") if isinstance(record, dict) else None
    return str(key) if isinstance(key, (str, int)) and not isinstance(key, bool) and str(key).strip() else None


def select(cache: Any, codes: list[str], n: int, seed: int, text_types: list[str]) -> dict[str, Any]:
    """Two passes over the cache: (key, eligibility) per record in pass 1, the sampled records' rows in pass 2."""
    seen_in_pages: dict[str, list[tuple[str | None, str]]] = {c: [] for c in codes}
    for code, record in cache.records():
        if code in seen_in_pages and isinstance(record, dict):
            seen_in_pages[code].append((_key(record), _status(record, text_types)))
    seen: set[str] = set()
    eligible: dict[str, list[str]] = {c: [] for c in codes}
    exclusions = {c: {"no_mdr_text": 0, "no_selected_text_type": 0, "duplicate": 0, "no_report_key": 0}
                  for c in codes}
    for code in codes:              # codes in the order given, so a shared event stays with the first code
        for key, status in seen_in_pages[code]:
            if key is None:
                exclusions[code]["no_report_key"] += 1
            elif key in seen:
                exclusions[code]["duplicate"] += 1
            else:
                seen.add(key)
                if status == "eligible":
                    eligible[code].append(key)
                else:
                    exclusions[code][status] += 1

    k = len(codes)
    allocation, shortfalls, chosen = {}, {}, {}
    for i, code in enumerate(codes):
        requested = n // k + (1 if i < n % k else 0)
        available = len(eligible[code])
        m = min(requested, available)
        if available < requested:
            shortfalls[code] = {"requested": requested, "available": available, "shortfall": requested - available}
        allocation[code] = m
        chosen[code] = random.Random(f"n1:{seed}:{code}").sample(sorted(eligible[code]), m)
    order = sorted(((codes.index(c), key, c) for c in codes for key in chosen[c]))
    picks = [(code, key) for _, key, code in order]
    random.Random(f"n1:{seed}:order").shuffle(picks)

    wanted = {(code, key) for code, key in picks}
    rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for code, record in cache.records():
        ident = (code, _key(record))
        if ident in wanted and ident not in rows_by_key:
            rows_by_key[ident] = sheet_row(record, code, text_types)
    rows = []
    for i, ident in enumerate(picks, start=1):
        rows.append({"row": i, **rows_by_key[ident]})
    return {"rows": rows, "allocation": allocation, "shortfalls": shortfalls, "exclusions": exclusions,
            "eligible": {c: len(eligible[c]) for c in codes}}


def sheet_bytes(rows: list[dict[str, Any]]) -> tuple[bytes, bytes]:
    jsonl = "".join(canonical_dumps(r) + "\n" for r in rows).encode("utf-8")
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(COLUMNS)
    for r in rows:
        writer.writerow([" | ".join(r[c]) if c in LIST_COLUMNS else ("" if r[c] is None else r[c]) for c in COLUMNS])
    return jsonl, buf.getvalue().encode("utf-8")


def _check_codes(codes: list[str], manifest: dict[str, Any]) -> None:
    if not 3 <= len(codes) <= 5 or len(set(codes)) != len(codes):
        raise UsageError("--product-codes must list 3 to 5 distinct codes") from None
    if manifest.get("dataset") != "event":
        raise UsageError("the cache must be a device event cache (--dataset event)") from None
    missing = [c for c in codes if c not in manifest["query"]["product_codes"]]
    if missing:
        raise UsageError(f"product codes not in the cache: {', '.join(missing)}") from None


def cmd_sample(args: argparse.Namespace) -> int:
    codes = split_list(args.product_codes)
    text_types = args.text_types or [DEFAULT_TEXT_TYPE]
    try:
        if args.n < 1:
            raise UsageError("--n must be >= 1") from None
        out_dir = run_dir(args.runs_dir, "n1", args.run_id)
        if args.dry_run:
            dry = DryRun(CLI_SAMPLE)
            if not Path(args.cache).is_dir():
                dry.need(f"openFDA event cache {args.cache} (run the openfda fetch command first)")
            else:
                manifest, _ = read_manifest(args.cache)
                _check_codes(codes, manifest)
            for name in ("sheet.jsonl", "sheet.csv", "sample.json"):
                dry.write(str(out_dir / name))
            return dry.emit()
        cache = load_cache(args.cache)
        _check_codes(codes, cache.manifest)
    except (UsageError, CacheError) as exc:
        return fail(str(exc))
    except OSError as exc:
        return fail(f"cannot read the cache ({exc.__class__.__name__})")

    picked = select(cache, codes, args.n, args.seed, text_types)
    jsonl, csv_bytes = sheet_bytes(picked["rows"])
    try:
        out_dir.mkdir(parents=True)
    except OSError as exc:
        return fail(f"cannot create the run directory ({exc.__class__.__name__})")
    (out_dir / "sheet.jsonl").write_bytes(jsonl)
    (out_dir / "sheet.csv").write_bytes(csv_bytes)
    truncated = [c for c in codes if (cache.manifest["per_code"].get(c) or {}).get("truncated")]
    data_label = cache.manifest.get("data_label")
    sample = {
        "kind": "n1_sample", "schema_version": 1, "run_id": args.run_id, "seed": args.seed, "n_requested": args.n,
        "n_sampled": len(picked["rows"]), "product_codes": codes, "text_types": text_types,
        "allocation": picked["allocation"], "shortfalls": picked["shortfalls"], "exclusions": picked["exclusions"],
        "eligible": picked["eligible"], "truncated_codes": truncated,
        "warning": TRUNCATION_WARNING if truncated else None,
        "rows": [{"row": r["row"], "mdr_report_key": r["mdr_report_key"], "product_code": r["product_code"]}
                 for r in picked["rows"]],
        "cache_manifest_sha256": cache.manifest_sha256, "sheet_jsonl_sha256": sha256_hex(jsonl),
        "sheet_csv_sha256": sha256_hex(csv_bytes), "data_label": data_label, "measurement": data_label == "public",
        **code_stamps(),
    }
    write_json_atomic(out_dir / "sample.json", sample)
    print(f"n1: sampled {len(picked['rows'])} of {args.n} requested; wrote {out_dir}")
    if truncated:
        print(f"n1: warning: {TRUNCATION_WARNING}: {', '.join(truncated)}")
    return 0


# --------------------------------------------------------------------------------------------------- score

def parse_label(token: str | None) -> bool | None:
    value = (token or "").strip().lower()
    if value in TRUE_TOKENS:
        return True
    if value in FALSE_TOKENS:
        return False
    return None


def read_sheet(path: str | Path) -> list[dict[str, str]]:
    """The labelled CSV; a spreadsheet's UTF-8 byte-order mark is accepted."""
    rows = None
    try:
        reader = csv.DictReader(io.StringIO(Path(path).read_text(encoding="utf-8-sig"), newline=""))
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    except (UnicodeDecodeError, csv.Error):
        fields = []
    if rows is None:
        raise UsageError("the sheet is not a UTF-8 CSV file") from None
    missing = [c for c in ("mdr_report_key", *LABEL_COLUMNS) if c not in fields]
    if missing:
        raise UsageError(f"the sheet lacks columns: {', '.join(missing)}") from None
    return rows


def read_sample(path: str | Path) -> dict[str, Any]:
    try:
        sample = load_json_file(path)
    except (OSError, StrictJsonError) as exc:
        raise UsageError(f"cannot read the sample file ({exc.__class__.__name__})") from None
    rows = sample.get("rows") if isinstance(sample, dict) else None
    if (not isinstance(sample, dict) or sample.get("kind") != "n1_sample" or not isinstance(rows, list)
            or not all(isinstance(r, dict) and isinstance(r.get("mdr_report_key"), str)
                       and isinstance(r.get("product_code"), str) for r in rows)):
        raise UsageError("the sample file is not an n1 sample.json") from None
    return sample


def _share(k: int, n: int) -> dict[str, Any]:
    return {"k": k, "n": n, "share": k / n if n else None, "ci95": wilson(k, n)}


def verdict(k: int, n: int) -> str:
    if 5 * k >= n:
        return "supports"
    if 10 * k < n:
        return "expect_S_matches_X"
    return "ambiguous"


def score(rows: list[dict[str, str]], sample: dict[str, Any]) -> dict[str, Any]:
    codes_by_key = {str(r["mdr_report_key"]): r["product_code"] for r in sample["rows"]}
    keys = [r.get("mdr_report_key", "") for r in rows]
    if len(set(keys)) != len(keys):
        raise UsageError("the sheet lists an mdr_report_key twice") from None
    if set(keys) != set(codes_by_key):
        extra, missing = len(set(keys) - set(codes_by_key)), len(set(codes_by_key) - set(keys))
        raise UsageError(f"the sheet's keys differ from the sample's ({extra} extra, {missing} missing)") from None
    unlabelled = []
    labels: list[dict[str, bool]] = []
    for position, r in enumerate(rows, start=1):
        parsed = {c: parse_label(r.get(c)) for c in LABEL_COLUMNS}
        if any(v is None for v in parsed.values()):
            row_no = (r.get("row") or "").strip()
            unlabelled.append(row_no if row_no.isdigit() else str(position))
        labels.append(parsed)
    if unlabelled:
        raise UsageError(f"{len(unlabelled)} rows are not fully labelled (use true/false); first rows: "
                         f"{', '.join(unlabelled[:10])}") from None
    n = len(rows)
    any_true = [any(lab.values()) for lab in labels]
    k = sum(any_true)
    per_code: dict[str, list[int]] = {}
    for r, hit in zip(rows, any_true):
        bucket = per_code.setdefault(codes_by_key[r["mdr_report_key"]], [0, 0])
        bucket[0] += hit
        bucket[1] += 1
    return {
        "n": n, "any_true": k, "share": k / n if n else None, "ci95": wilson(k, n), "ci_method": "wilson",
        "bars": BARS, "verdict": verdict(k, n) if n else None,
        "per_column": {c: _share(sum(lab[c] for lab in labels), n) for c in LABEL_COLUMNS},
        "per_product_code": {code: _share(hit, total) for code, (hit, total) in sorted(per_code.items())},
    }


def cmd_score(args: argparse.Namespace) -> int:
    try:
        if Path(args.out).exists():
            raise UsageError(f"--out already exists: {args.out}") from None
        if args.dry_run:
            dry = DryRun(CLI_SCORE)
            for flag, path, what in (("--sheet", args.sheet, "labelled sheet"), ("--sample", args.sample,
                                                                                 "n1 sample.json")):
                if not Path(path).is_file():
                    dry.need(f"{what} {path}")
                elif flag == "--sheet":
                    read_sheet(path)
                else:
                    read_sample(path)
            dry.write(args.out)
            return dry.emit()
        sample = read_sample(args.sample)
        rows = read_sheet(args.sheet)
        result = score(rows, sample)
    except UsageError as exc:
        return fail(str(exc))
    except OSError as exc:
        return fail(f"cannot read an input ({exc.__class__.__name__})")
    data_label = sample.get("data_label")
    out = {"kind": "n1", "schema_version": 1, "run_id": sample.get("run_id"), **result, "caveat": CAVEAT,
           "text_types": sample.get("text_types"), "data_label": data_label,
           "measurement": data_label == "public" and sample.get("measurement") is True,
           "sample_sha256": sha256_hex(Path(args.sample).read_bytes()),
           "sheet_sha256": sha256_hex(Path(args.sheet).read_bytes()), **code_stamps()}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(args.out, out)
    print(f"n1: {out['any_true']}/{out['n']} narratives carry information beyond the codes; verdict {out['verdict']}"
          f" (data_label={data_label}); wrote {args.out}")
    return 0


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mycelic.collective.experiments.n1_narratives",
                                description="N1: narrative gain over coded fields on openFDA device events.")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sample", help="draw the labelling sheet from an openFDA event cache")
    s.add_argument("--cache", required=True)
    s.add_argument("--product-codes", required=True, help="3 to 5 comma-separated codes, in priority order")
    s.add_argument("--run-id", required=True)
    s.add_argument("--n", type=int, default=300)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--text-types", action="append", default=None,
                   help=f"mdr_text text_type_code to include; repeatable (default: {DEFAULT_TEXT_TYPE!r})")
    s.add_argument("--runs-dir", default="runs")
    s.add_argument("--dry-run", action="store_true")
    c = sub.add_parser("score", help="score a labelled sheet")
    c.add_argument("--sheet", required=True, help="the labelled sheet.csv")
    c.add_argument("--sample", required=True, help="the sample.json written by 'sample'")
    c.add_argument("--out", required=True, help="where to write narrative_gain.json")
    c.add_argument("--dry-run", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return cmd_sample(args) if args.command == "sample" else cmd_score(args)


if __name__ == "__main__":
    sys.exit(main())
