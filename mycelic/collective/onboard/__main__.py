"""The onboard CLI: draft, export, check, score and report (``docs/collective/onboard/BUILD-D001.md``, section 3;
``BUILD-D002.md`` for what D002 changed).

    python -m mycelic.collective.onboard draft --export FILE --roles FILE --pack-id ID --train-from DATE
        --train-to DATE --out DIR [--params FILE]
    python -m mycelic.collective.onboard export --export FILE --roles FILE --pack DIR --from DATE --to DATE --out FILE
    python -m mycelic.collective.onboard check --pack DIR --export FILE --roles FILE --train-from DATE --train-to DATE
        --out FILE [--params FILE] [--settings FILE]
    python -m mycelic.collective.onboard score --settings FILE --arm NAME --exports DIR --out DIR
    python -m mycelic.collective.onboard report --settings FILE --arms DIR [DIR ...] --audit FILE --out DIR

Every command takes ``--dry-run``: it says what it would read and write and touches nothing. Exit codes: 0 done; 1 a
check failed (a draft the rule refuses, a pack the loader refuses, a privacy floor not held, a company not drafted, a
verdict other than pass); 2 a usage or configuration error. Nothing here prints a record value.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from ..experiments.common import DryRun, code_commit, fail
from ..jsonio import StrictJsonError
from ..packs.loader import ID_RE, RESERVED, PackError, load_pack_dir
from .check import check_pack
from .draft import (DraftError, EntityType, date_column, draft_export, load_language, load_params, load_template,
                    load_written, normalised_rows, parse_window, write_jsonl, write_pack)
from .exports import ExportError, read_export
from .roles import RolesError, load_roles
from .score import ScoreError, load_settings, run_arm, write_json

CLI = "onboard"
ERRORS = (DraftError, ExportError, RolesError, ScoreError, StrictJsonError, OSError, KeyError, ValueError)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mycelic.collective.onboard",
                                description="Draft a pack from one export, check it, and score a drafting test.")
    sub = p.add_subparsers(dest="command", required=True)
    d = sub.add_parser("draft", help="draft a pack from one export")
    d.add_argument("--export", required=True)
    d.add_argument("--roles", required=True)
    d.add_argument("--pack-id", required=True)
    d.add_argument("--train-from", required=True)
    d.add_argument("--train-to", required=True)
    d.add_argument("--out", required=True)
    d.add_argument("--params")
    e = sub.add_parser("export", help="write the normalised export of a date window")
    e.add_argument("--export", required=True)
    e.add_argument("--roles", required=True)
    e.add_argument("--pack", required=True)
    e.add_argument("--from", dest="date_from", required=True)
    e.add_argument("--to", dest="date_to", required=True)
    e.add_argument("--out", required=True)
    c = sub.add_parser("check", help="check a drafted pack's privacy floor and loader check")
    c.add_argument("--pack", required=True)
    c.add_argument("--export", required=True)
    c.add_argument("--roles", required=True)
    c.add_argument("--train-from", required=True)
    c.add_argument("--train-to", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--params")
    c.add_argument("--settings")
    s = sub.add_parser("score", help="score one arm of the settings' drafting test")
    s.add_argument("--settings", required=True)
    s.add_argument("--arm", required=True)
    s.add_argument("--exports", required=True)
    s.add_argument("--out", required=True)
    r = sub.add_parser("report", help="merge the arms into the drafting test's report")
    r.add_argument("--settings", required=True)
    r.add_argument("--arms", required=True, nargs="+")
    r.add_argument("--audit", required=True)
    r.add_argument("--out", required=True)
    for q in (d, e, c, s, r):
        q.add_argument("--dry-run", action="store_true", help="say what would be read and written; touch nothing")
    return p


def _dry(args: argparse.Namespace) -> int:
    dry = DryRun(f"{CLI} {args.command}")
    files = {"draft": ("export", "roles", "params"), "export": ("export", "roles"),
             "check": ("export", "roles", "params", "settings"), "score": ("settings",),
             "report": ("settings", "audit")}[args.command]
    for name in files:
        value = getattr(args, name, None)
        if value is not None and not Path(value).is_file():
            dry.need(f"file {value}")
    for name in ("pack", "exports"):
        value = getattr(args, name, None)
        if value is not None and not Path(value).is_dir():
            dry.need(f"directory {value}")
    for value in getattr(args, "arms", None) or ():
        if not (Path(value) / "arm.json").is_file():
            dry.need(f"file {Path(value) / 'arm.json'}")
    out = Path(args.out)
    writes = {"draft": [out / "pack", out / "draft.json"], "export": [out], "check": [out],
              "score": [out / "arm.json", out / "<company>" / "pack"],
              "report": [out / "report.json", out / "report.md"]}[args.command]
    for w in writes:
        dry.write(str(w))
    return dry.emit()


def _draft(args: argparse.Namespace) -> int:
    if ID_RE.fullmatch(args.pack_id) is None or args.pack_id in RESERVED:
        return fail("--pack-id must be a pack id ([a-z][a-z0-9_]{1,40}, not a reserved word)")
    roles = load_roles(args.roles)
    export = read_export(args.export)
    out = Path(args.out)
    if (out / "pack").exists():
        return fail(f"{out / 'pack'} exists; pick a new --out")
    d = draft_export(export, roles, parse_window(args.train_from, args.train_to), args.pack_id,
                     params=load_params(args.params))
    hashes = write_pack(d.files, out / "pack")
    pack, error = load_written(out / "pack")
    write_json(out / "draft.json", {"kind": "onboard_draft", "schema_version": 1, **d.facts, "pack_files": hashes,
                                    "loader": {"passed": pack is not None, "error": error},
                                    "hashes": pack.hashes() if pack is not None else None})
    print(f"onboard draft: pack {args.pack_id}, {len(d.plan.ids)} specific predicates, corpus "
          f"{len(d.corpus)} records, loader {'passed' if pack is not None else 'failed'} -> {out / 'pack'}")
    return 0 if pack is not None else 1


def _pack_entity_types(pack_dir: Path, template: dict) -> list[EntityType]:
    vocab = json.loads((pack_dir / "vocabulary.json").read_text(encoding="utf-8"))
    aliases = json.loads((pack_dir / "aliases.json").read_text(encoding="utf-8"))
    scope = next(iter(template["vocabulary_base.json"]["entity_types"]))
    return [EntityType(id=t, column=et["label"], label=et["label"], ids=tuple(et["ids"] or ()),
                       aliases=aliases.get(t, {})) for t, et in sorted(vocab["entity_types"].items()) if t != scope]


def _export(args: argparse.Namespace) -> int:
    roles = load_roles(args.roles)
    export = read_export(args.export)
    template = load_template()
    pack_dir = Path(args.pack)
    pack = load_pack_dir(pack_dir)
    lang = load_language(roles.language)
    dated = date_column(export, roles, lang, load_params())
    rows, rejected = normalised_rows(export, roles, dated, parse_window(args.date_from, args.date_to), template,
                                     _pack_entity_types(pack_dir, template))
    from ..packs.connector import map_rows
    mapped = map_rows(rows, pack)
    write_jsonl(rows, args.out)
    print(f"onboard export: {len(rows)} rows written, rejected {json.dumps(rejected, sort_keys=True)}; the pack's "
          f"mapping rejected {sum(mapped.rejected.values())} -> {args.out}")
    return 0 if not mapped.rejected else 1


def _check(args: argparse.Namespace) -> int:
    roles = load_roles(args.roles)
    export = read_export(args.export)
    settings = load_settings(args.settings)[0] if args.settings else None
    result = check_pack(args.pack, export, roles, parse_window(args.train_from, args.train_to),
                        params=load_params(args.params), settings=settings)
    write_json(Path(args.out), result)
    failed = [k for k, v in result["checks"].items() if not v["passed"]]
    print(f"onboard check: pack {result['pack']} {'passed' if result['passed'] else 'failed'}"
          + (f" ({', '.join(failed)})" if failed else "") + f" -> {args.out}")
    return 0 if result["passed"] else 1


def _score(args: argparse.Namespace) -> int:
    settings, sha = load_settings(args.settings)
    doc = run_arm(settings, sha, args.arm, Path(args.exports), Path(args.out), code_commit)
    c = doc["criterion"]
    failed = bool(doc["errors"]) or any(not co["check"]["passed"] for co in doc["companies"].values()
                                         if "error" not in co)
    print(f"onboard score: arm {args.arm}, {len(doc['companies'])} companies, {len(doc['errors'])} not drafted; "
          f"{c['id']} {'passed' if c['passed'] else 'failed'} -> {Path(args.out) / 'arm.json'}")
    return 1 if failed else 0


def _report(args: argparse.Namespace) -> int:
    from .report import run_report
    settings, sha = load_settings(args.settings)
    doc = run_report(settings, args.settings, sha, args.arms, args.audit, args.out, code_commit)
    return 0 if doc["verdict"] == "pass" else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.dry_run:
        return _dry(args)
    handlers = {"draft": _draft, "export": _export, "check": _check, "score": _score, "report": _report}
    try:
        return handlers[args.command](args)
    except DraftError as err:
        return fail(str(err), 1)
    except PackError as err:
        return fail(f"pack: {err}", 2)
    except ERRORS as err:
        return fail(f"{err.__class__.__name__}: {err}", 2)


if __name__ == "__main__":
    sys.exit(main())
