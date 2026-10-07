"""screen.json (G8): what the console shows, every value an item with its provenance.

**Fictional company, synthetic data, an illustration, not a measured result; internal and YC use only.**

The screen has static TEXT parts (the constants below: no digit outside the identifier allow-list, no number word)
and ITEMS. An item is ``{id, beat, label, display, src, fmt}``: ``src`` is ``<primary run file>#<RFC 6901 pointer>``
and ``display`` is :func:`format_value` of the scalar it points at. Every run-derived string (a display name, an id,
a label, the question text, a reason, a draft field, the run id and time) is an item; no other code formats a value,
and the console renders numbers only from ``display``. ``lint_numbers.py`` re-resolves every ``src`` and fails the
build when a display does not match.

Formats (:data:`FMTS`), with a fixed locale and no float repr: ``int`` ``f'{v:,}'``; ``rank`` an int, or ``not
alerted`` for null; ``pct`` ``f'{round(v * 100)}%'`` for v in [0, 1]; ``dec2`` ``f'{v:.2f}'``; ``text`` verbatim;
``digest12`` the first 12 of at least 12 lowercase hex characters; ``yesno``; ``mode`` (``RECORDED`` or ``LIVE``);
``approval`` (scripted or live); ``datetime`` ``YYYY-MM-DD HH:MM UTC`` from an ISO timestamp with ``Z``.
:func:`parse_display` reads a display back to the value the lint compares with :func:`comparable`.

Pure and deterministic: :func:`build_screen` gives the same screen for the same documents; it imports only
``jsonio``, ``schemacheck`` and ``runfiles``.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

from mycelic.collective import schemacheck
from mycelic.collective.jsonio import canonical_bytes, strict_load
from mycelic.collective.runfiles import resolve

KIND = "collective_demo_screen"
SCHEMA_VERSION = 1
FMTS = ("int", "rank", "pct", "dec2", "text", "digest12", "yesno", "mode", "approval", "datetime")
BLOCK_KINDS = ("headline", "caption", "channel_row", "verdict_card", "packet_card", "draft_field", "note", "warning",
               "footer")
PHASES = ("preparing", "ready", "running", "complete", "failed")
ACTIONS = ("next", "check", "approve")
MODES = ("record", "live")
BEATS = (("problem", "The problem"), ("alert", "The alert"), ("check", "Check with the sites"),
         ("followup", "Approval-routed follow-up"), ("real_data", "Real data"))
CUT_60S = ("problem", "alert", "check", "real_data")
FOOTER_BEAT = "footer"
NOT_ALERTED = "not alerted"
MODE_DISPLAY = {"record": "RECORDED", "live": "LIVE"}
APPROVAL_DISPLAY = {"recorded": "recorded approval (scripted)",
                    "live": "approved live in the console (the presenter acts as the named owner)"}

# ----------------------------------------------------------------------------------------------- static texts
R_DEFINITION = "R: the same detectors over the fields allowed to leave, record-level, no model"
S_DEFINITION = "S: the same detectors over structured codes only, no model"
X_DEFINITION = "X: detectors over k-suppressed counts that left the sites, from codes and narratives"
R_ALSO = "The restricted central baseline also caught this case"
S_ALSO = "The codes-only baseline also caught this case"
R_RELATED = "The restricted central baseline flagged a related key:"
S_RELATED = "The codes-only baseline flagged a related key:"
BY_CONSTRUCTION = "By construction, S and R cannot see this key:"
REASON_TEXTS = {
    "predicate_only_in_narrative": "its predicate is never coded in these records; it appears only in the narratives",
    "entity_and_predicate_only_in_narrative": "neither its predicate nor its entity is in a coded or structured "
                                              "field of these records; both appear only in the narratives",
    None: "it is in no field they read",
}
LEAK_LINE = ("text-overlap scan: ", " bytes of ", "-character narrative windows · canary hits: ",
             " · the schema allows no free text")
X4_CAPTION = "approval-routed follow-up — not measured (X4)"
REAL_DATA = "Real-data result: not yet measured (Phase-1 audit / public replay pending)"
OUTCOME = "Outcome: not yet checked —"
FOOTER = "Fictional company · synthetic data · illustration, not a measured result"
AUDIENCE = "Internal and YC use only"
NO_FOLLOWUP = "No follow-up: only a supported conclusion can propose one"
NOT_CHECKED = "Not checked: X did not alert this key"
NOT_ASKED = "Not asked yet: press check with sites, and each plant answers from its own records"
RULES_NONE = "Rules configured for other shapes: none fired on this key"
RULES_FIRED = "A configured rule fired on this key:"
RULES_RELATED = "A configured rule fired on a related key:"
PROBLEM_LINE = "The codes do not say what failed."
PREPARING = "Preparing the run: each plant ingests its own records and sends its suppressed weekly counts"
TASK_TEXTS = {"extract": "Extraction at the plants: ", "judge": "Checks at the plants: ", "draft": "Drafting at HQ: "}
ROLE_TEXTS = {"contributing": "contributing plant", "sibling": "sibling plant"}
CONTROL_LABELS = {"next": "Next", "check": "Check with sites", "approve": "Approve as the named owner"}

_INT = re.compile(r"-?[0-9]{1,3}(,[0-9]{3})*", re.ASCII)
_PCT = re.compile(r"([0-9]{1,3})%", re.ASCII)
_DEC2 = re.compile(r"-?[0-9]+\.[0-9]{2}", re.ASCII)
_RANK = re.compile(r"[0-9]+", re.ASCII)
_HEX12 = re.compile(r"[0-9a-f]{12,}", re.ASCII)
_HEX12_EXACT = re.compile(r"[0-9a-f]{12}", re.ASCII)
_TIMESTAMP = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})T([0-9]{2}:[0-9]{2})(:[0-9]{2}(\.[0-9]{1,9})?)?Z", re.ASCII)
_DATETIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2} UTC", re.ASCII)
RUN_ID_PATTERN = "[A-Za-z0-9][A-Za-z0-9_.-]{0,63}"


class ScreenError(ValueError):
    """A value that does not fit its format, or a screen that breaks a rule; the text never holds a value."""


# ----------------------------------------------------------------------------------------------- formats

def _number(value: Any) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, float)
                                                                        and math.isfinite(value))


def format_value(value: Any, fmt: str) -> str:
    """The display of ``value`` in ``fmt``; a wrong type (a bool for an int included) is :class:`ScreenError`."""
    if fmt == "int" and isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    if fmt == "rank":
        if value is None:
            return NOT_ALERTED
        if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
            return str(value)
    if fmt == "pct" and _number(value) and 0 <= value <= 1:
        return f"{round(value * 100)}%"
    if fmt == "dec2" and _number(value):
        return f"{value:.2f}"
    if fmt == "text" and isinstance(value, str):
        return value
    if fmt == "digest12" and isinstance(value, str) and _HEX12.fullmatch(value) is not None:
        return value[:12]
    if fmt == "yesno" and isinstance(value, bool):
        return "yes" if value else "no"
    if fmt == "mode" and value in MODE_DISPLAY:
        return MODE_DISPLAY[value]
    if fmt == "approval" and value in APPROVAL_DISPLAY:
        return APPROVAL_DISPLAY[value]
    if fmt == "datetime" and isinstance(value, str):
        m = _TIMESTAMP.fullmatch(value)
        if m is not None:
            return f"{m.group(1)} {m.group(2)} UTC"
    raise ScreenError(f"a value does not fit the format {fmt if fmt in FMTS else 'unknown'}") from None


def comparable(value: Any, fmt: str) -> Any:
    """What :func:`parse_display` of the value's display must equal."""
    format_value(value, fmt)
    if fmt == "pct":
        return round(value * 100)
    if fmt == "dec2":
        return round(value, 2)
    if fmt in ("int", "rank", "text", "yesno", "mode", "approval"):
        return value
    return format_value(value, fmt)


def parse_display(display: Any, fmt: str) -> Any:
    """The comparable value a display shows: an int (commas allowed) for int, the integer percent for pct, a float
    rounded to 2 places for dec2, an int or None for rank, and the canonical form for every other format."""
    ok = isinstance(display, str)
    if ok and fmt == "int" and _INT.fullmatch(display):
        return int(display.replace(",", ""))
    if ok and fmt == "pct" and _PCT.fullmatch(display):
        return int(display[:-1])
    if ok and fmt == "dec2" and _DEC2.fullmatch(display):
        return round(float(display), 2)
    if ok and fmt == "rank":
        if display == NOT_ALERTED:
            return None
        if _RANK.fullmatch(display) and display[0] != "0":
            return int(display)
    if ok and fmt == "text":
        return display
    if ok and fmt == "digest12" and _HEX12_EXACT.fullmatch(display):
        return display
    if ok and fmt == "yesno" and display in ("yes", "no"):
        return display == "yes"
    for name, table in (("mode", MODE_DISPLAY), ("approval", APPROVAL_DISPLAY)):
        if ok and fmt == name and display in table.values():
            return next(k for k, v in table.items() if v == display)
    if ok and fmt == "datetime" and _DATETIME.fullmatch(display):
        return display
    raise ScreenError(f"a display does not read as {fmt if fmt in FMTS else 'an unknown format'}") from None


# ----------------------------------------------------------------------------------------------- schema

def _obj(props: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": sorted(props), "properties": dict(props)}


def _arr(items: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": dict(items)}


_STR = {"type": "string"}
_NSTR = {"type": ["string", "null"]}
SCREEN_SCHEMA = _obj({
    "kind": {"type": "string", "const": KIND},
    "schema_version": {"type": "integer", "const": SCHEMA_VERSION},
    "run_id": {"type": "string", "pattern": RUN_ID_PATTERN},
    "mode": {"type": "string", "enum": list(MODES)},
    "phase": {"type": "string", "enum": list(PHASES)},
    "beats": _arr(_obj({"id": {"type": "string", "enum": [b for b, _ in BEATS]}, "title": _STR,
                        "in_cut": {"type": "boolean"}})),
    "cut_60s": _arr({"type": "string", "enum": [b for b, _ in BEATS]}),
    "controls": _arr(_obj({"action": {"type": "string", "enum": list(ACTIONS)}, "key": _NSTR, "beat": _STR,
                           "label": _STR, "enabled": {"type": "boolean"}})),
    "blocks": _arr(_obj({"id": _STR, "beat": _STR, "kind": {"type": "string", "enum": list(BLOCK_KINDS)},
                         "group": _NSTR, "parts": _arr(_obj({"text": _NSTR, "item": _NSTR}))})),
    "items": _arr(_obj({"id": _STR, "beat": _STR, "label": _STR, "display": _STR, "src": _STR,
                        "fmt": {"type": "string", "enum": list(FMTS)}})),
})
_COMPILED = schemacheck.compile(SCREEN_SCHEMA)


def screen_problems(screen: Any) -> list[tuple[str, str]]:
    """Schema problems ``(path, keyword)``, then the rules schemacheck cannot express: exactly one of text and item
    per part, unique item ids, every part's item defined, every block's beat a beat or the footer."""
    problems = _COMPILED.validate(screen)
    if problems:
        return problems
    ids = [i["id"] for i in screen["items"]]
    out = [(f"$.items[{n}].id", "unique") for n, i in enumerate(ids) if i in ids[:n]]
    beats = {b["id"] for b in screen["beats"]} | {FOOTER_BEAT}
    for n, block in enumerate(screen["blocks"]):
        if block["beat"] not in beats:
            out.append((f"$.blocks[{n}].beat", "enum"))
        for m, part in enumerate(block["parts"]):
            if (part["text"] is None) == (part["item"] is None):
                out.append((f"$.blocks[{n}].parts[{m}]", "oneOf"))
            elif part["item"] is not None and part["item"] not in ids:
                out.append((f"$.blocks[{n}].parts[{m}].item", "unresolved"))
    return out


# ----------------------------------------------------------------------------------------------- the builder

class _Builder:
    def __init__(self, docs: Mapping[str, Any] | None) -> None:
        self.docs = docs
        self.items: list[dict[str, Any]] = []
        self.blocks: list[dict[str, Any]] = []

    def item(self, item_id: str, beat: str, label: str, src: str, fmt: str) -> dict[str, Any]:
        if any(i["id"] == item_id for i in self.items):
            raise ScreenError("an item id is used twice") from None
        self.items.append({"id": item_id, "beat": beat, "label": label,
                           "display": format_value(resolve(self.docs, src), fmt), "src": src, "fmt": fmt})
        return {"text": None, "item": item_id}

    @staticmethod
    def text(value: str) -> dict[str, Any]:
        return {"text": value, "item": None}

    def block(self, block_id: str, beat: str, kind: str, parts: Sequence[Mapping[str, Any]],
              group: str | None = None) -> None:
        self.blocks.append({"id": block_id, "beat": beat, "kind": kind, "group": group,
                            "parts": [dict(p) for p in parts]})


def _site_index(trace: Mapping[str, Any], site: str) -> int:
    return [s["site_id"] for s in trace["org"]["sites"]].index(site)


def _problem(b: _Builder, sc: Mapping[str, Any], trace: Mapping[str, Any]) -> None:
    S = "scorecard.json#"
    b.block("problem-company", "problem", "headline", [b.item("company", "problem", "Company", S + "/company", "text")])
    parts = [b.text("At "), b.item("hero_sites", "problem", "Plants with the case", S + "/scenario/hero_sites", "int"),
             b.text(" plants, "), b.item("hero_records", "problem", "Complaints in the case",
                                          S + "/scenario/hero_records", "int"),
             b.text(" complaints carry the code: ")]
    for j, _ in enumerate(sc["hero"]["code_labels"]):
        if j:
            parts.append(b.text("; "))
        parts.append(b.item(f"hero_code_{j}", "problem", "Code on the complaints", f"{S}/hero/code_labels/{j}", "text"))
    b.block("problem-codes", "problem", "caption", parts)
    names = [b.text("Plants: ")]
    for j, site in enumerate(sc["hero"]["sites"]):
        if j:
            names.append(b.text(" · "))
        names.append(b.item(f"hero_site_{j}", "problem", "Plant", f"trace.json#/org/sites/"
                            f"{_site_index(trace, site['site'])}/display_name", "text"))
    b.block("problem-plants", "problem", "note", names)
    b.block("problem-line", "problem", "headline", [b.text(PROBLEM_LINE)])
    b.block("problem-illustration", "problem", "note",
            [b.item("illustration", "problem", "Illustration", S + "/illustration", "text")])


def _key_parts(b: _Builder, sc: Mapping[str, Any], prefix: str, item_id: str, beat: str,
               key: str) -> list[dict[str, Any]]:
    labels = sc["hero"]["case_key_labels"]
    j = [x["key"] for x in labels].index(key)
    base = f"scorecard.json#/hero/case_key_labels/{j}"
    return [b.item(f"{item_id}_type", beat, "Entity type", base + "/entity_type_label", "text"), b.text(" "),
            b.item(f"{item_id}_id", beat, "Entity", base + "/entity_id", "text"), b.text(" · "),
            b.item(f"{item_id}_predicate", beat, "Predicate", base + "/predicate_label", "text")]


def _alert(b: _Builder, sc: Mapping[str, Any]) -> None:
    S = "scorecard.json#/hero/detection"
    det = sc["hero"]["detection"]
    x = [b.text("X · rank "), b.item("x_rank", "alert", "X rank", S + "/X/rank", "rank")]
    if det["X"]["rank"] is not None:
        x += [b.text(" · score "), b.item("x_score", "alert", "X score", S + "/X/score", "dec2"),
              b.text(" · first alerted in week "),
              b.item("x_week", "alert", "X first alert week", S + "/X/detection_week", "text")]
    b.block("alert-x", "alert", "channel_row", x, group="X")
    b.block("alert-x-definition", "alert", "note", [b.text(X_DEFINITION)], group="X")
    b.block("alert-s", "alert", "channel_row", [b.text("S · rank "), b.item("s_rank", "alert", "S rank",
                                                                             S + "/S/rank", "rank")], group="S")
    b.block("alert-s-definition", "alert", "note", [b.text(S_DEFINITION)], group="S")
    b.block("alert-r", "alert", "channel_row", [b.text("R · rank "), b.item("r_rank", "alert", "R rank",
                                                                             S + "/R_mf/rank", "rank")], group="R")
    b.block("alert-r-definition", "alert", "note", [b.text(R_DEFINITION)], group="R")
    b.block("alert-references", "alert", "channel_row",
            [b.text("References: U · rank "), b.item("u_rank", "alert", "U rank", S + "/U/rank", "rank"),
             b.text(" (reference, not deployable at HQ) · each site alone · rank "),
             b.item("single_rank", "alert", "Each site alone rank", S + "/single_site/rank", "rank"),
             b.text(" (each site sees only its own records)")], group="references")
    if det["by_construction"]["S"]:
        b.block("alert-by-construction", "alert", "caption",
                [b.text(BY_CONSTRUCTION + " " + REASON_TEXTS[det["by_construction"]["reason"]])])
    if det["R_mf"]["caught"]:
        b.block("alert-r-also", "alert", "warning", [b.text(R_ALSO)], group="R")
    if det["S"]["caught"]:
        b.block("alert-s-also", "alert", "warning", [b.text(S_ALSO)], group="S")
    for channel, sentence, group in (("R_mf", R_RELATED, "R"), ("S", S_RELATED, "S")):
        for j, rel in enumerate(det[channel]["related"]):
            item_id = f"{group.lower()}_related_{j}"
            b.block(f"alert-{group.lower()}-related-{j}", "alert", "warning",
                    [b.text(sentence + " ")] + _key_parts(b, sc, S, item_id, "alert", rel["key"])
                    + [b.text(" · rank "), b.item(f"{item_id}_rank", "alert", "Rank of the related key",
                                                  f"{S}/{channel}/related/{j}/rank", "rank"),
                       b.text(" in week "), b.item(f"{item_id}_week", "alert", "Week of the related key",
                                                   f"{S}/{channel}/related/{j}/week", "text")], group=group)
    if det["rule_candidates"]:
        b.block("alert-rules", "alert", "warning", [b.text(RULES_FIRED + " ")] + [
            b.item(f"rule_{j}", "alert", "Rule", f"{S}/rule_candidates/{j}", "text")
            for j, _ in enumerate(det["rule_candidates"])])
    else:
        b.block("alert-rules", "alert", "note", [b.text(RULES_NONE)])
    for j, _ in enumerate(det["rule_hits_case_keys"]):
        b.block(f"alert-rule-related-{j}", "alert", "warning",
                [b.text(RULES_RELATED + " "), b.item(f"rule_related_{j}", "alert", "Rule",
                                                     f"{S}/rule_hits_case_keys/{j}/rule_id", "text"),
                 b.text(" · "), b.item(f"rule_related_{j}_key", "alert", "Key", f"{S}/rule_hits_case_keys/{j}/key",
                                       "text")])
    for j, _ in enumerate(sc["scenario"]["structured_fill"]):
        base = f"scorecard.json#/scenario/structured_fill/{j}"
        b.block(f"alert-fill-{j}", "alert", "note",
                [b.text("In these records the "), b.item(f"fill_{j}_label", "alert", "Structured field",
                                                         base + "/entity_type_label", "text"),
                 b.text(" field was filled in "), b.item(f"fill_{j}_filled", "alert", "Records with the field",
                                                         base + "/filled", "int"),
                 b.text(" of "), b.item(f"fill_{j}_records", "alert", "Records", base + "/records", "int"),
                 b.text(" (scenario setting)")])
    for j, decoy in enumerate(sc["decoys"]):
        base = f"scorecard.json#/decoys/{j}"
        parts = [b.text("Decoy: "), b.item(f"decoy_{j}_label", "alert", "Decoy", base + "/label", "text"),
                 b.text(" · X alerted: "), b.item(f"decoy_{j}_alerted", "alert", "X alerted the decoy",
                                                  base + "/x_alerted", "yesno")]
        if decoy["verified"]:
            parts += [b.text(" · after checking with the sites: "),
                      b.item(f"decoy_{j}_status", "alert", "Gate status of the decoy", base + "/status", "text")]
        else:
            parts.append(b.text(" · not checked with the sites: X did not alert it"))
        b.block(f"alert-decoy-{j}", "alert", "note", parts, group="decoys")


def _check(b: _Builder, sc: Mapping[str, Any], trace: Mapping[str, Any], leakage: Mapping[str, Any]) -> None:
    hero = sc["hero"]
    pd = hero["pushdown"]
    if pd is None:
        reason = hero["followup"].get("reason")
        b.block("check-none", "check", "headline", [b.text(NOT_CHECKED if reason == "not_alerted" else NOT_ASKED)])
        return
    P = "scorecard.json#/hero/pushdown"
    b.block("check-question", "check", "headline",
            [b.text("HQ asks the plants: "), b.item("question", "check", "Question", P + "/question_text", "text")])
    for j, v in enumerate(pd["verdicts"]):
        base = f"{P}/verdicts/{j}"
        parts = [b.item(f"verdict_{j}_site", "check", "Plant",
                        f"trace.json#/org/sites/{_site_index(trace, v['site'])}/display_name", "text"),
                 b.text(" · " + ROLE_TEXTS[v["role"]] + " · answers "),
                 b.item(f"verdict_{j}_verdict", "check", "Verdict", base + "/verdict", "text")]
        if v["support_bucket"] is not None:
            parts += [b.text(" · records "), b.item(f"verdict_{j}_support", "check", "Confirming records",
                                                    base + "/support_bucket", "text"),
                      b.text(" · independent roots "), b.item(f"verdict_{j}_roots", "check", "Independent roots",
                                                              base + "/roots_bucket", "text"),
                      b.text(" · reporters "), b.item(f"verdict_{j}_reporters", "check", "Independent reporters",
                                                      base + "/reporters_bucket", "text")]
        if v["entity_records_bucket"] is not None:
            parts += [b.text(" · records naming the entity, none describing the failure "),
                      b.item(f"verdict_{j}_entity", "check", "Records naming the entity",
                             base + "/entity_records_bucket", "text")]
        if v["reason"] is not None:
            parts += [b.text(" · reason "), b.item(f"verdict_{j}_reason", "check", "Reason", base + "/reason", "text")]
        if v["evidence_ref"] is not None:
            parts += [b.text(" · evidence reference "),
                      b.item(f"verdict_{j}_ref", "check", "Evidence reference", base + "/evidence_ref", "text")]
        b.block(f"check-verdict-{j}", "check", "verdict_card", parts, group=v["role"])
    b.block("check-gate", "check", "headline",
            [b.text("Gate: "), b.item("gate_status", "check", "Gate status", P + "/gate/status", "text")])
    for j, _ in enumerate(pd["gate"]["reasons"]):
        b.block(f"check-reason-{j}", "check", "note",
                [b.item(f"gate_reason_{j}", "check", "Gate reason", f"{P}/gate/reasons/{j}", "text")])
    b.block("check-resolvable", "check", "note",
            [b.text("Every counted confirm resolves to the plant's own records: "),
             b.item("resolvable", "check", "Evidence resolvable at the plants", P + "/resolvable", "yesno")])
    if leakage is not None and leakage.get("scans"):
        L = "leakage.json#/scans/0"
        b.block("check-leakage", "check", "caption",
                [b.text(LEAK_LINE[0]), b.item("leak_overlap", "check", "Narrative bytes found crossing",
                                              L + "/shingle_overlap_bytes", "int"),
                 b.text(LEAK_LINE[1]), b.item("leak_window", "check", "Narrative window",
                                              "leakage.json#/window_chars", "int"),
                 b.text(LEAK_LINE[2]), b.item("leak_hits", "check", "Canary hits", L + "/hit_count", "int"),
                 b.text(LEAK_LINE[3])])
    b.block("check-when", "check", "note",
            [b.text("Asked at "), b.item("pushdown_as_of", "check", "Asked at", P + "/as_of", "text"),
             b.text("; X first alerted in week "),
             b.item("pushdown_detection_week", "check", "X first alert week", P + "/detection_week", "text")])


def _followup(b: _Builder, sc: Mapping[str, Any], trace: Mapping[str, Any]) -> None:
    F = "scorecard.json#/hero/followup"
    fu = sc["hero"]["followup"]
    b.block("followup-caption", "followup", "caption", [b.text(X4_CAPTION)])
    if not fu["proposed"]:
        b.block("followup-none", "followup", "headline", [b.text(NO_FOLLOWUP)])
        return
    b.block("followup-label", "followup", "note", [b.item("followup_label", "followup", "Label", F + "/label", "text")])
    b.block("followup-gate", "followup", "note",
            [b.text("Only a supported conclusion can propose a follow-up. Gate: "),
             b.item("followup_gate", "followup", "Gate status", "scorecard.json#/hero/pushdown/gate/status", "text")])
    for name, title in (("packet", "Read-only evidence packet"), ("draft", "Draft for a named owner")):
        part = fu[name]
        if part is None:
            continue
        base = f"{F}/{name}"
        parts = [b.text(title + ": "), b.item(f"{name}_type", "followup", "Follow-up type", base + "/type_label",
                                               "text")]
        if part["owner"] is not None:
            parts += [b.text(" · owner "), b.item(f"{name}_owner", "followup", "Owner", base + "/owner", "text")]
        if part["owner_role_label"] is not None:
            parts += [b.text(" · role "),
                      b.item(f"{name}_role", "followup", "Owner role", base + "/owner_role_label", "text")]
        if part["ack_due"] is not None:
            parts += [b.text(" · acknowledge by "),
                      b.item(f"{name}_ack", "followup", "Acknowledge by", base + "/ack_due", "datetime")]
        parts += [b.text(" · state "), b.item(f"{name}_state", "followup", "State", base + "/state", "text")]
        b.block(f"followup-{name}", "followup", "headline", parts, group=name)
        if part["approval"] is not None:
            b.block(f"followup-{name}-approval", "followup", "note",
                    [b.item(f"{name}_approval", "followup", "Approval", base + "/approval", "approval"),
                     b.text(" · execute requested "),
                     b.item(f"{name}_requests", "followup", "Execute requests", base + "/execute_requests", "int"),
                     b.text(" · executor ran "),
                     b.item(f"{name}_calls", "followup", "Executor calls", base + "/executor_calls", "int")],
                    group=name)
        if name == "packet":
            for j, p in enumerate(part["packets"]):
                pb = f"{base}/packets/{j}"
                parts = [b.item(f"packet_{j}_site", "followup", "Plant",
                                f"trace.json#/org/sites/{_site_index(trace, p['site'])}/display_name", "text"),
                         b.text(" · "), b.item(f"packet_{j}_status", "followup", "Packet status", pb + "/status",
                                               "text"),
                         b.text(" · confirming records "),
                         b.item(f"packet_{j}_support", "followup", "Confirming records", pb + "/support_bucket",
                                "text")]
                for m, _ in enumerate(p["codes"]):
                    cb = f"{pb}/codes/{m}"
                    parts += [b.text(" · code "), b.item(f"packet_{j}_code_{m}", "followup", "Code", cb + "/code",
                                                         "text"),
                              b.text(" "), b.item(f"packet_{j}_code_{m}_label", "followup", "Code label",
                                                  cb + "/code_label", "text"),
                              b.text(" in "), b.item(f"packet_{j}_code_{m}_n", "followup", "Records with the code",
                                                     cb + "/n", "text")]
                for m, _ in enumerate(p["co_mentions"]):
                    cb = f"{pb}/co_mentions/{m}"
                    parts += [b.text(" · named with "),
                              b.item(f"packet_{j}_with_{m}", "followup", "Co-mentioned id", cb + "/entity_id", "text"),
                              b.text(" in "), b.item(f"packet_{j}_with_{m}_n", "followup", "Records naming both",
                                                     cb + "/n", "text")]
                b.block(f"followup-packet-{j}", "followup", "packet_card", parts, group="packet")
        else:
            for j, _ in enumerate(part["fields"]):
                fb = f"{base}/fields/{j}"
                b.block(f"followup-field-{j}", "followup", "draft_field",
                        [b.item(f"draft_field_{j}_name", "followup", "Field", fb + "/name", "text"), b.text(": "),
                         b.item(f"draft_field_{j}", "followup", "Draft text", fb + "/value", "text")], group="draft")
            for j, lst in enumerate(part["lists"]):
                lb = f"{base}/lists/{j}"
                parts = [b.item(f"draft_list_{j}_name", "followup", "Field", lb + "/name", "text"), b.text(": ")]
                if not lst["items"]:
                    parts.append(b.text("none listed"))
                for m, _ in enumerate(lst["items"]):
                    if m:
                        parts.append(b.text(", "))
                    parts.append(b.item(f"draft_list_{j}_{m}", "followup", "Listed value", f"{lb}/items/{m}", "text"))
                b.block(f"followup-list-{j}", "followup", "draft_field", parts, group="draft")
    if fu["ledger_head"] is not None and fu["ledger_entries"] is not None:
        b.block("followup-ledger", "followup", "note",
                [b.text("Every step is an entry of the hash-chained follow-up ledger; head "),
                 b.item("ledger_head", "followup", "Ledger head", F + "/ledger_head", "digest12"),
                 b.text(" after "),
                 b.item("ledger_entries", "followup", "Ledger entries", F + "/ledger_entries", "int"),
                 b.text(" entries")])
    b.block("followup-outcome", "followup", "note",
            [b.text(OUTCOME + " "), b.item("outcome_label", "followup", "Outcome label", F + "/outcome/label", "text")])


def _footer(b: _Builder, sc: Mapping[str, Any], trace: Mapping[str, Any]) -> None:
    b.block("footer-labels", FOOTER_BEAT, "footer", [b.text(FOOTER)])
    b.block("footer-audience", FOOTER_BEAT, "footer", [b.text(AUDIENCE)])
    b.block("footer-run", FOOTER_BEAT, "footer",
            [b.item("mode", FOOTER_BEAT, "Mode", "scorecard.json#/mode", "mode"), b.text(" · run "),
             b.item("run_id", FOOTER_BEAT, "Run", "scorecard.json#/run_id", "text"), b.text(" · recorded "),
             b.item("recorded_at", FOOTER_BEAT, "Recorded at", "trace.json#/meta/recorded_at", "datetime")])
    for j, p in enumerate(sc["providers"]):
        b.block(f"footer-provider-{j}", FOOTER_BEAT, "footer",
                [b.text(TASK_TEXTS[p["task"]]), b.item(f"provider_{j}", FOOTER_BEAT, "Provider",
                                                        f"scorecard.json#/providers/{j}/label", "text")])
    if trace["meta"]["sites_note"] is not None:
        b.block("footer-sites", FOOTER_BEAT, "footer",
                [b.item("sites_note", FOOTER_BEAT, "Sites", "trace.json#/meta/sites_note", "text")])
    failed = [j for j, c in enumerate(sc["checks"]) if not c["ok"]]
    if failed:
        b.block("footer-failed", FOOTER_BEAT, "warning", [b.text("Checks that failed: ")] + [
            b.item(f"failed_check_{j}", FOOTER_BEAT, "Failed check", f"scorecard.json#/checks/{j}/id", "text")
            for j in failed])


def _shell(run_id: str, mode: str, phase: str, controls: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {"kind": KIND, "schema_version": SCHEMA_VERSION, "run_id": run_id, "mode": mode, "phase": phase,
            "beats": [{"id": i, "title": t, "in_cut": i in CUT_60S} for i, t in BEATS], "cut_60s": list(CUT_60S),
            "controls": [dict(c) for c in controls], "blocks": [], "items": []}


def build_screen(docs: Mapping[str, Any] | None, *, mode: str, phase: str,
                 controls: Sequence[Mapping[str, Any]] = (), run_id: str | None = None) -> dict[str, Any]:
    """The screen for the primary documents (``{file name: document}``; the run id is the scorecard's), or, without
    documents (a run still preparing, or one that failed before it had any), a screen of static texts only for
    ``run_id``."""
    if mode not in MODES or phase not in PHASES:
        raise ScreenError("unknown mode or phase") from None
    out = _shell(docs["scorecard.json"]["run_id"] if docs is not None else run_id, mode, phase, controls)
    b = _Builder(docs)
    if docs is None:
        b.block("preparing", "problem", "headline", [b.text(PREPARING)])
        b.block("footer-labels", FOOTER_BEAT, "footer", [b.text(FOOTER)])
        b.block("footer-audience", FOOTER_BEAT, "footer", [b.text(AUDIENCE)])
    else:
        sc, trace = docs["scorecard.json"], docs["trace.json"]
        _problem(b, sc, trace)
        _alert(b, sc)
        _check(b, sc, trace, docs.get("leakage.json"))
        _followup(b, sc, trace)
        b.block("real-data", "real_data", "headline", [b.text(REAL_DATA)])
        _footer(b, sc, trace)
    out["blocks"], out["items"] = b.blocks, b.items
    problems = screen_problems(out)
    if problems:
        raise ScreenError("the screen breaks its schema") from None
    return strict_load(canonical_bytes(out))
