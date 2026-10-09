#!/usr/bin/env python
"""Build a public replay's pack from a built-in pack and the id-shapes probe, by the rule in CHOICE-002.

    python tools/market/replay_pack.py --shapes docs/collective/replay/inputs-002.json --base device_quality
        --id device_quality_bd --out lab/packs/device_quality_bd

The built-in ``device_quality`` pack was written for its synthetic generator: its lot and product id formats
(``L`` plus digits, two letters, a dash and digits) match almost none of a real manufacturer's identifiers, and its
openFDA mapping names six FDA problem terms. Public replay 001 therefore resolved too few entities to count anything.
This copies the base pack and changes only what `docs/collective/replay/CHOICE-002.md` says, each step a pure
function here:

* **Id formats** (:func:`shape_format`): of the probe's most reported values of a field (summed over the product
  codes after trimming and upper-casing), values without a digit (``UNK``, ``NA``) and values with a character other
  than A-Z, 0-9, ``-`` and ``/`` are dropped; each remaining value's signature is its sequence of letter runs, digit
  runs and single separators; the signature with the largest report count wins (ties: fewer runs, then the
  signature's text); its run lengths are the narrowest that cover the signature's most reported values up to
  :data:`COVER` of its count.
* **Product field** (:func:`product_path`): ``device[].model_number`` unless ``device[].catalog_number`` has the
  larger count of values with a digit.
* **Problem terms** (:func:`term_map`): a probe term the base mapping does not name maps to a predicate when its words
  contain, as consecutive whole words, the predicate's id (underscores read as spaces) or one of its English lexicon
  entries, and no other predicate's; it takes that predicate's first specific code (else its first code).
* So that the pack loads, the aliases of the lot and product types are dropped (they name the base pack's fictional
  ids), and the fictional lot and product ids of the generator and of the extraction fixtures (structured fields,
  gold claims and narratives, :func:`rename_text`) are renamed into the new formats (:func:`example_id`); the plant
  fixtures, which serve the built-in pack's simulations, are left out. The replay reads neither the generator nor the
  fixtures.

Detectors, predicates, codes, rules, egress, questions and follow-ups are the base pack's, byte for byte. The output
loads with ``mycelic.collective.packs.loader`` or this exits 2.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
COVER = 0.9
MAX_ID_LENGTH = 40                     # mycelic.collective.packs.canonical.MAX_ID_LENGTH
LOT_FIELD, MODEL_FIELD, CATALOG_FIELD, PROBLEM_FIELD = ("device.lot_number", "device.model_number",
                                                         "device.catalog_number", "product_problems")
MODEL_PATH, CATALOG_PATH = "device[].model_number", "device[].catalog_number"
PAD = {"lot": "9", "product": "8"}
FIXTURES = "fixtures/records.jsonl"
_ALLOWED = re.compile(r"[A-Z0-9/-]+", re.ASCII)
_RUN = re.compile(r"[A-Z]+|[0-9]+|[-/]", re.ASCII)
_WORD = re.compile(r"[a-z]+")
_VARIANT_SEP = "[-_\\s\u00a0\u200b\u2010-\u2015\u2212]{1,2}"


class BuildError(Exception):
    pass


# --------------------------------------------------------------------------------------------------- id formats

def merge(per_code: Mapping[str, Any], *, upper: bool = True) -> dict[str, int]:
    """The probe's ``[[value, count], ...]`` per code summed into one count per value (trimmed, upper-cased for ids);
    a code whose query failed contributes nothing."""
    out: Counter[str] = Counter()
    for rows in per_code.values():
        if not isinstance(rows, list):
            continue
        for value, count in rows:
            text = str(value).strip()
            out[text.upper() if upper else text] += int(count)
    return dict(out)


def signature(value: str) -> tuple[tuple[str, int], ...] | str:
    """The value's runs as ``(kind, length)``: ``alpha``, ``digit``, ``sep-`` or ``sep/``; else why it is dropped."""
    if len(value) > MAX_ID_LENGTH:
        return "too_long"
    if not any(c.isdigit() for c in value):
        return "no_digit"
    if _ALLOWED.fullmatch(value) is None:
        return "other_characters"
    runs = _RUN.findall(value)
    if runs[0] in "-/" or runs[-1] in "-/":
        return "separator_at_edge"
    out: list[tuple[str, int]] = []
    for run in runs:
        if run in ("-", "/"):
            if out[-1][0].startswith("sep"):
                return "adjacent_separators"
            out.append(("sep" + run, 1))
        else:
            out.append(("digit" if run.isdigit() else "alpha", len(run)))
    if len({kind for kind, _ in out if kind.startswith("sep")}) > 1:
        return "mixed_separators"
    return tuple(out)


def shape_format(counts: Mapping[str, int]) -> dict[str, Any]:
    """The id format the rule gives for one field's value counts, with how much of the count it covers."""
    dropped: Counter[str] = Counter()
    groups: dict[tuple[str, ...], list[tuple[str, int, tuple[int, ...]]]] = {}
    for value in sorted(counts):
        n = counts[value]
        sig = signature(value)
        if isinstance(sig, str):
            dropped[sig] += n
            continue
        groups.setdefault(tuple(k for k, _ in sig), []).append((value, n, tuple(length for _, length in sig)))
    total = sum(counts.values())
    eligible = sum(n for members in groups.values() for _, n, _ in members)
    base = {"total": total, "eligible": eligible, "dropped": dict(sorted(dropped.items()))}
    if not groups:
        return {**base, "format": None, "separator": None, "signature": None, "covered": 0}
    best = min(groups, key=lambda k: (-sum(n for _, n, _ in groups[k]), len(k), " ".join(k)))
    members = sorted(groups[best], key=lambda m: (-m[1], m[0]))
    need = COVER * sum(n for _, n, _ in members)
    kept, running = [], 0
    for member in members:
        kept.append(member)
        running += member[1]
        if running >= need:
            break
    lo = [min(m[2][i] for m in kept) for i in range(len(best))]
    hi = [max(m[2][i] for m in kept) for i in range(len(best))]
    segments: list[dict[str, Any]] = []
    separator = None
    for i, kind in enumerate(best):
        if kind.startswith("sep"):
            segments.append({"sep": "required"})
            separator = kind[3:]
        else:
            segments.append({kind: [lo[i], hi[i]]})
    covered = sum(n for _, n, lengths in members if all(lo[i] <= lengths[i] <= hi[i] for i in range(len(best))))
    return {**base, "format": segments, "separator": separator, "signature": list(best), "covered": covered}


def product_path(model: Mapping[str, int], catalog: Mapping[str, int]) -> str:
    """The rule's product field: the model number unless the catalog number has more reports with a digit."""
    def with_digit(counts: Mapping[str, int]) -> int:
        return sum(n for v, n in counts.items() if any(c.isdigit() for c in v))
    return CATALOG_PATH if with_digit(catalog) > with_digit(model) else MODEL_PATH


def example_id(segments: list[dict[str, Any]], separator: str | None, n: int, pad: str) -> str:
    """A canonical id of the format for the n-th renamed generator id: ``n`` in the first digit run, padded with
    ``pad``; letters ``X``; other digit runs all ``pad``."""
    parts: list[str] = []
    numbered = False
    for seg in segments:
        kind, value = next(iter(seg.items()))
        if kind == "sep":
            parts.append(separator or "")
        elif kind == "literal":
            parts.append(value)
        elif kind == "alpha":
            parts.append("X" * max(value[0], 1))
        else:
            width = max(value[0], 1)
            if not numbered:
                text = str(n)
                width = max(width, len(text))
                if width > value[1]:
                    raise BuildError(f"cannot number {n} ids in a digit run of at most {value[1]}")
                parts.append(text.rjust(width, pad))
                numbered = True
            else:
                parts.append(pad * width)
    return "".join(parts)


# --------------------------------------------------------------------------------------------------- problem terms

def _words(text: str) -> tuple[str, ...]:
    return tuple(_WORD.findall(text.lower()))


def _contains(seq: tuple[str, ...], sub: tuple[str, ...]) -> bool:
    return bool(sub) and any(seq[i:i + len(sub)] == sub for i in range(len(seq) - len(sub) + 1))


def term_predicates(term: str, predicates: Mapping[str, Any]) -> list[str]:
    words = _words(term)
    out = []
    for pid in sorted(predicates):
        forms = [pid.replace("_", " "), *predicates[pid].get("lexicon", {}).get("en", [])]
        if any(_contains(words, _words(form)) for form in forms):
            out.append(pid)
    return out


def predicate_code(pid: str, codes: Mapping[str, Any]) -> str | None:
    specific = sorted(c for c, spec in codes.items() if spec["predicate"] == pid and spec["specific"])
    every = sorted(c for c, spec in codes.items() if spec["predicate"] == pid)
    return (specific or every or [None])[0]


def term_map(terms: Mapping[str, int], predicates: Mapping[str, Any], codes: Mapping[str, Any],
             existing: Mapping[str, str]) -> dict[str, Any]:
    added: dict[str, str] = {}
    ambiguous: list[str] = []
    unmapped: list[str] = []
    for term in sorted(terms):
        if term in existing:
            continue
        hits = term_predicates(term, predicates)
        code = predicate_code(hits[0], codes) if len(hits) == 1 else None
        if code is not None:
            added[term] = code
        elif len(hits) > 1:
            ambiguous.append(term)
        else:
            unmapped.append(term)
    reports = sum(terms.values())
    mapped = sum(n for t, n in terms.items() if t in existing or t in added)
    return {"added": added, "ambiguous": ambiguous, "unmapped": unmapped, "reports": reports, "mapped": mapped}


# --------------------------------------------------------------------------------------------------- the build

def _rename(obj: Any, names: Mapping[str, str]) -> Any:
    if isinstance(obj, dict):
        return {names.get(k, k): _rename(v, names) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rename(v, names) for v in obj]
    if isinstance(obj, str):
        return names.get(obj, obj)
    return obj


def rename_text(text: str, names: Mapping[str, str]) -> str:
    """Each old id in free text renamed: in any letter case, with its dash written as any dash, underscore or space
    (the base fixtures' surface variants), and not inside a longer run of letters or digits."""
    for old in sorted(names, key=lambda k: (-len(k), k)):
        body = _VARIANT_SEP.join(re.escape(part) for part in old.split("-"))
        text = re.sub(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", names[old], text, flags=re.IGNORECASE)
    return text


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def plan(shapes: Mapping[str, Any], base: Path) -> dict[str, Any]:
    """Every rule step's outcome for the probe output and the base pack, without writing anything."""
    fields = shapes["fields"]
    lot = shape_format(merge(fields[LOT_FIELD]))
    model, catalog = merge(fields[MODEL_FIELD]), merge(fields[CATALOG_FIELD])
    path = product_path(model, catalog)
    product = shape_format(model if path == MODEL_PATH else catalog)
    vocabulary = _read(base / "vocabulary.json")
    mapping = _read(base / "mapping_openfda.json")
    existing = mapping["codes"][0]["value_map"]
    terms = term_map(merge(fields[PROBLEM_FIELD], upper=False), vocabulary["predicates"],
                     _read(base / "codes.json"), existing)
    return {"lot": lot, "product": product, "product_path": path, "terms": terms}


def build(shapes: Mapping[str, Any], base: Path, out: Path, pack_id: str) -> dict[str, Any]:
    steps = plan(shapes, base)
    for t in ("lot", "product"):
        if steps[t]["format"] is None:
            raise BuildError(f"no {t} value with a digit in the probe output; the rule gives no format")
    if out.exists():
        raise BuildError(f"{out} exists; remove it first")
    shutil.copytree(base, out)

    pack = _read(out / "pack.json")
    pack.update({
        "id": pack_id, "version": "0.1.0",
        "title": "Device quality complaints, public replay 002 (real lot and product id formats)",
        "disclaimer": ("Built by tools/market/replay_pack.py from the device_quality pack for public replay 002 "
                       "(docs/collective/replay/CHOICE-002.md). Its lot and product id formats and part of its "
                       "openFDA problem-term map come from public openFDA reports received before the replay's "
                       "window. Its ILL- codes and predicates are the base pack's illustrative placeholders, and the "
                       "lot and product ids of its generator and fixtures are renamed fictional ids that the replay "
                       "never reads.")})
    _write(out / "pack.json", pack)

    vocabulary = _read(out / "vocabulary.json")
    for t in ("lot", "product"):
        et = vocabulary["entity_types"][t]
        et.update({"id_format": steps[t]["format"], "separator": steps[t]["separator"],
                   "strip_leading_zeros": False})
    _write(out / "vocabulary.json", vocabulary)

    aliases = _read(out / "aliases.json")
    for t in ("lot", "product"):
        aliases.pop(t, None)
    _write(out / "aliases.json", aliases)

    mapping = _read(out / "mapping_openfda.json")
    mapping["codes"][0]["value_map"] = {**mapping["codes"][0]["value_map"], **steps["terms"]["added"]}
    mapping["entities"]["product"] = [steps["product_path"]]
    _write(out / "mapping_openfda.json", mapping)

    generator = _read(out / "generator.json")
    fixtures = [json.loads(line) for line in (base / FIXTURES).read_text(encoding="utf-8").splitlines()
                if line.strip()]
    names: dict[str, str] = {}
    for t in ("lot", "product"):
        universe = set(generator["universe"][t])
        extra = {v for line in fixtures for v in line["record"]["entities"].get(t, []) if v not in universe}
        extra |= {g["entity_id"] for line in fixtures for g in line["gold"]
                  if g["entity_type"] == t and g["entity_id"] not in universe}
        for i, old in enumerate([*sorted(universe), *sorted(extra)], start=1):
            names[old] = example_id(steps[t]["format"], steps[t]["separator"], i, PAD[t])
    _write(out / "generator.json", _rename(generator, names))
    for plant in sorted((out / "fixtures").glob("plant_*.json")):
        plant.unlink()                      # plant fixtures serve the built-in pack's simulations, not a replay
    lines = []
    for line in fixtures:
        line = _rename(line, names)
        line["record"]["narrative"] = rename_text(line["record"]["narrative"], names)
        lines.append(json.dumps(line, ensure_ascii=False) + "\n")
    (out / FIXTURES).write_text("".join(lines), encoding="utf-8")

    sys.path.insert(0, str(ROOT))
    from mycelic.collective.packs.loader import PackError, load_pack_dir  # noqa: E402
    try:
        frozen = load_pack_dir(out)
    except PackError as err:
        raise BuildError(f"the built pack does not load: {err}") from None
    return {**steps, "pack": {"id": frozen.id, "version": frozen.version, **frozen.hashes()}}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--shapes", required=True, help="the id-shapes probe's output")
    p.add_argument("--base", default="device_quality", help="a built-in pack id")
    p.add_argument("--id", required=True, help="the new pack's id")
    p.add_argument("--out", required=True, help="the new pack's directory (must not exist)")
    p.add_argument("--plan-only", action="store_true", help="print the rule's outcome without writing a pack")
    args = p.parse_args(argv)
    base = ROOT / "mycelic" / "collective" / "packs" / "data" / args.base
    shapes = _read(Path(args.shapes))
    try:
        result = plan(shapes, base) if args.plan_only else build(shapes, base, Path(args.out), args.id)
    except BuildError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
