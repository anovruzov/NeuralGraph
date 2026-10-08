"""The codes-miss rule (B1b): does a constructed scenario illustrate the case the allowed central fields miss?

Pre-registered in ``docs/collective/b1/PREREG.md`` (committed with B1a, before any codes-miss world existed). This
module implements its section 5 and nothing else: :data:`RULE` is the rule verbatim, and :func:`evaluate` computes
it for the main seed, the robustness seeds and the grid, each against the same scenario built without the hero item.

**Constructed, not measured.** The scenario's author wrote the detectors and the baselines and knew how R ranks
(``scenario.AUTHOR_NOTE``). A run that holds shows the mechanism is possible on a constructed world; it says nothing
about how often such cases occur (``scenario.STATEMENT``).

Terms, as the rule uses them:

* **case keys**: the hero's case keys in the world with every item (``DemoWorld.case_keys``); a robustness seed or a
  grid cell uses its own world's.
* **X's week**: X's first alert of the hero key from the hero's first week on, or the last detection week when X
  never alerts it.
* **attributed**: an alert of a case key in channel R (model-free) or S, from the hero's first week on, unless the
  world without the hero item alerts the same key in the same channel in some week from the hero's first week to
  the alert's week.
* **cooling candidate**: a key the detector found in a week while it was cooling down after an earlier alert, so it
  could not alert (``detectors.run_detection``: a key cools until it has been absent for ``cooldown_weeks`` steps).

The robustness seeds and the grid run detection only (no site checks), so they hold without the gate. The engine
computes everything but the gate when it prepares (:func:`evaluate_detection`, so every screen of the run can carry
the statement) and applies the gate when it writes the scorecard (:func:`with_gate`): until the check with the sites
has run, ``holds_main``, ``holds`` and ``holds_strict`` are null unless X was not caught or a channel resolved the case
first, which decides them without the gate.
"""
from __future__ import annotations

import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from demo.collective.scenario import Scenario, ScenarioError

RULE = (
    "Channels R (model-free) and S. An alert of a case key in a channel in week w, from the hero's first week on, is "
    "attributed to the case unless the same scenario without the hero item alerts the same key in the same channel "
    "in some week from the hero's first week to w. X's week is X's first alert of the hero key from the hero's first "
    "week on, or the last detection week when X never alerts it. attributed_no_later: the channel has an attributed "
    "alert no later than X's week. strict_no_later: the channel alerts a case key from the first shift week to X's "
    "week, or holds a case key as a cooling candidate in X's week. X caught: X alerts the hero key from the hero's "
    "first week on, and the same scenario without the hero item has no X alert of the hero key from the hero's "
    "first week to X's week; an X alert that fails this is a chance find. holds_main: X caught, the gate says "
    "supported, and neither channel has attributed_no_later. A robustness seed or a grid cell holds by the same rule "
    "without the gate, against its own world without the hero item; a world that does not build does not hold. "
    "holds_robust: at least half of the robustness seeds hold. holds: holds_main and holds_robust. holds_strict: "
    "holds, and neither channel has strict_no_later.")
RULE_CHANNELS = ("R_mf", "S")
GRID_RATES = (1, 2)
SUPPORTED = "supported"
VERDICT_TEXTS = {True: "This run illustrates the case codes miss.",
                 False: "This run does not illustrate the case codes miss.",
                 None: "The rule's verdict waits for the check with the sites."}

Detection = Mapping[str, Any]          # DemoEngine.raw plus "case_keys" and "weeks_world"
DetectFn = Callable[[Scenario, "str | None"], "Detection | None"]

_KEY_WEEK = {"type": "object", "additionalProperties": False, "required": ["key", "rank", "week"],
             "properties": {"key": {"type": "string"}, "rank": {"type": "integer", "minimum": 1},
                            "week": {"type": "string"}}}
_CHANNEL = {"type": "object", "additionalProperties": False,
            "required": ["alerts", "attributed", "attributed_no_later", "strict_no_later", "cooling_at_x_week"],
            "properties": {"alerts": {"type": "array", "items": _KEY_WEEK},
                           "attributed": {"type": "array", "items": _KEY_WEEK},
                           "attributed_no_later": {"type": "boolean"},
                           "strict_no_later": {"type": ["boolean", "null"]},
                           "cooling_at_x_week": {"type": ["array", "null"], "items": {"type": "string"}}}}
_OUTCOME = {"type": "object", "additionalProperties": False,
            "required": ["built", "x_caught", "x_chance_find", "x_week", "channels", "holds"],
            "properties": {"built": {"type": "boolean"}, "x_caught": {"type": "boolean"},
                           "x_chance_find": {"type": "boolean"}, "x_week": {"type": ["string", "null"]},
                           "channels": {"type": ["object", "null"], "additionalProperties": False,
                                        "required": list(RULE_CHANNELS),
                                        "properties": {c: _CHANNEL for c in RULE_CHANNELS}},
                           "holds": {"type": "boolean"}}}
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["case_kind", "statement", "author_note", "rule", "hero_key", "hero_first_week",
                       "first_shift_week", "shifts", "main", "gate_status", "holds_main", "robustness",
                       "robust_seeds", "robust_holding", "holds_robust", "grid", "grid_cells", "grid_holding", "holds",
                       "holds_strict"],
          "properties": {
              "case_kind": {"type": "string", "const": "codes_miss"}, "statement": {"type": "string"},
              "author_note": {"type": "string"}, "rule": {"type": "string", "const": RULE},
              "hero_key": {"type": "string"}, "hero_first_week": {"type": "string"},
              "first_shift_week": {"type": "string"},
              "shifts": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                    "required": ["id", "label", "first_week", "sites", "to_code"],
                                                    "properties": {"id": {"type": "string"},
                                                                   "label": {"type": "string"},
                                                                   "first_week": {"type": "string"},
                                                                   "sites": {"type": "integer", "minimum": 1},
                                                                   "to_code": {"type": "string"}}}},
              "main": _OUTCOME,
              "gate_status": {"type": ["string", "null"]}, "holds_main": {"type": ["boolean", "null"]},
              "robustness": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                        "required": ["seed", "outcome"],
                                                        "properties": {"seed": {"type": "integer"},
                                                                       "outcome": _OUTCOME}}},
              "robust_seeds": {"type": "integer", "minimum": 0},
              "robust_holding": {"type": "integer", "minimum": 0}, "holds_robust": {"type": "boolean"},
              "grid": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                  "required": ["offset", "rate", "outcome"],
                                                  "properties": {"offset": {"type": "integer"},
                                                                 "rate": {"type": "integer"},
                                                                 "outcome": _OUTCOME}}},
              "grid_cells": {"type": "integer", "minimum": 0}, "grid_holding": {"type": "integer", "minimum": 0},
              "holds": {"type": ["boolean", "null"]}, "holds_strict": {"type": ["boolean", "null"]}}}


# --------------------------------------------------------------------------------------------------- variants

def grid_offsets(window_weeks: int) -> tuple[int, ...]:
    """{0, -window_weeks//2, -window_weeks}, sorted ascending (the grid iterates sorted by (offset, rate))."""
    return tuple(sorted({0, -(window_weeks // 2), -window_weeks}))


def variant(sc: Scenario, *, seed: int | None = None, shift_offset: int | None = None,
            hero_rate: int | None = None) -> Scenario:
    """The scenario with another seed, every shift starting at the hero's start week plus ``shift_offset`` (still
    running to the last world week), or every hero site at ``hero_rate`` records a week. A variant is built, never
    parsed: the grid moves the shift on purpose (R8 holds for the main scenario only)."""
    cm = sc.codes_miss
    if cm is None:
        raise ValueError("not a codes-miss scenario")
    hero, items, shifts = sc.hero, sc.items, cm.shifts
    if hero_rate is not None:
        hero = replace(hero, sites=tuple((s, lang, hero_rate) for s, lang, _ in hero.sites))
        items = tuple(hero if i.id == hero.id else i for i in items)
    if shift_offset is not None:
        start = hero.start_week + shift_offset
        shifts = tuple(replace(x, start_week=start, weeks=sc.weeks - start) for x in shifts)
    return replace(sc, seed=sc.seed if seed is None else seed, items=items, hero=hero,
                   codes_miss=replace(cm, shifts=shifts))


# --------------------------------------------------------------------------------------------------- the rule

def _alerts(result: Mapping[str, Any], keys: set[str], first: str, last: str) -> list[dict[str, Any]]:
    """Every alert of a key in ``keys`` with ``first <= week <= last``, ordered by (week, rank, key)."""
    return [{"key": a["key"], "rank": a["rank"], "week": a["week"]}
            for a in sorted(result["alerts"], key=lambda a: (a["week"], a["rank"], a["key"]))
            if a["key"] in keys and first <= a["week"] <= last]


def cooling_at(result: Mapping[str, Any], key: str, week: str, cooldown_weeks: int) -> bool:
    """True when the detector held ``key`` as a cooling candidate in ``week``: found that week while cooling after an
    earlier alert. Replays the detector's cooldown (released after ``cooldown_weeks`` consecutive absent steps)."""
    cand = next((c for c in result["candidates"] if c["key"] == key), None)
    if cand is None or cooldown_weeks <= 0:
        return False
    present, alerted = set(cand["candidate_weeks"]), set(cand["alert_weeks"])
    quiet: int | None = None
    for step in (w["week"] for w in result["weeks"]):
        if quiet is not None:
            if step in present:
                quiet = 0
            else:
                quiet += 1
                if quiet >= cooldown_weeks:
                    quiet = None
        if step == week:
            return quiet is not None and step in present
        if step in alerted:
            quiet = 0
    return False


def outcome(with_hero: Detection | None, without_hero: Detection | None, sc: Scenario, *,
            strict: bool) -> dict[str, Any]:
    """One world's verdict by the rule (without the gate). ``strict`` adds strict_no_later and the cooling list."""
    if with_hero is None or without_hero is None:
        return {"built": False, "x_caught": False, "x_chance_find": False, "x_week": None, "channels": None,
                "holds": False}
    weeks = with_hero["weeks_world"]
    first = weeks[sc.hero.start_week]
    last = with_hero["last_week"]
    hero_key = sc.hero.keys[0]
    case = set(with_hero["case_keys"])
    x_alerts = _alerts(with_hero["X"], {hero_key}, first, last)
    x_week = x_alerts[0]["week"] if x_alerts else last
    no_hero_x = _alerts(without_hero["X"], {hero_key}, first, x_week)
    x_caught = bool(x_alerts) and not no_hero_x
    channels: dict[str, Any] = {}
    for c in RULE_CHANNELS:
        alerts = _alerts(with_hero[c], case, first, last)
        attributed = [a for a in alerts
                      if not _alerts(without_hero[c], {a["key"]}, first, a["week"])]
        block: dict[str, Any] = {"alerts": alerts, "attributed": attributed,
                                 "attributed_no_later": any(a["week"] <= x_week for a in attributed),
                                 "strict_no_later": None, "cooling_at_x_week": None}
        if strict:
            shift_first = weeks[min(x.start_week for x in sc.codes_miss.shifts)]
            cooling = sorted(k for k in case if cooling_at(with_hero[c], k, x_week, with_hero["cooldown_weeks"]))
            block["cooling_at_x_week"] = cooling
            block["strict_no_later"] = bool(_alerts(with_hero[c], case, shift_first, x_week)) or bool(cooling)
        channels[c] = block
    holds = x_caught and not any(channels[c]["attributed_no_later"] for c in RULE_CHANNELS)
    return {"built": True, "x_caught": x_caught, "x_chance_find": bool(x_alerts) and not x_caught,
            "x_week": x_week if x_alerts else None, "channels": channels, "holds": holds}


def evaluate_detection(sc: Scenario, main: Detection, detect: DetectFn) -> dict[str, Any]:
    """The rule's block without the gate. ``main`` is the run's own detection; ``detect(scenario, without)`` runs
    detection only on a scenario (``without`` = an item id to leave out) and returns None when the world does not
    build. The gate-dependent verdicts are null here; :func:`with_gate` sets them."""
    cm = sc.codes_miss
    if cm is None:
        raise ValueError("not a codes-miss scenario")
    hero = sc.hero
    main_out = outcome(main, detect(sc, hero.id), sc, strict=True)
    robustness = []
    for seed in cm.robustness_seeds:
        v = variant(sc, seed=seed)
        robustness.append({"seed": seed, "outcome": outcome(detect(v, None), detect(v, hero.id), v, strict=False)})
    holding = sum(1 for r in robustness if r["outcome"]["holds"])
    grid = []
    window = sc.pack.detectors["window_weeks"]
    for offset in grid_offsets(window):
        without = detect(variant(sc, shift_offset=offset), hero.id)
        for rate in GRID_RATES:
            v = variant(sc, shift_offset=offset, hero_rate=rate)
            grid.append({"offset": offset, "rate": rate, "outcome": outcome(detect(v, None), without, v, strict=False)})
    weeks = main["weeks_world"]
    return {"case_kind": "codes_miss", "statement": cm.statement, "author_note": cm.author_note, "rule": RULE,
            "hero_key": hero.keys[0], "hero_first_week": weeks[hero.start_week],
            "first_shift_week": weeks[min(x.start_week for x in cm.shifts)],
            "shifts": [{"id": x.id, "label": x.label, "first_week": weeks[x.start_week], "sites": len(x.sites),
                        "to_code": x.to_code} for x in cm.shifts],
            "main": main_out, "gate_status": None, "holds_main": None, "robustness": robustness,
            "robust_seeds": len(robustness), "robust_holding": holding, "holds_robust": 2 * holding >= len(robustness),
            "grid": grid, "grid_cells": len(grid), "grid_holding": sum(1 for g in grid if g["outcome"]["holds"]),
            "holds": None, "holds_strict": None}


def with_gate(block: Mapping[str, Any], gate_status: str | None) -> dict[str, Any]:
    """``block`` with the gate applied: holds_main is X caught, the gate supported and no channel resolving the case
    no later than X. Without a gate status (the check with the sites has not run) it is null, unless the main world
    already fails the rule without the gate."""
    main = block["main"]
    if not main["holds"]:
        holds_main: bool | None = False
    elif gate_status is None:
        holds_main = None
    else:
        holds_main = gate_status == SUPPORTED
    holds = None if holds_main is None else holds_main and block["holds_robust"]
    strict = None if holds is None else holds and not any(main["channels"][c]["strict_no_later"]
                                                          for c in RULE_CHANNELS)
    return {**block, "gate_status": gate_status, "holds_main": holds_main, "holds": holds, "holds_strict": strict}


def evaluate(sc: Scenario, main: Detection, gate_status: str | None, detect: DetectFn) -> dict[str, Any]:
    """The rule's full block for the scorecard: :func:`evaluate_detection` with the gate applied."""
    return with_gate(evaluate_detection(sc, main, detect), gate_status)


# --------------------------------------------------------------------------------------------------- detection

def engine_detection(engine: Any) -> Detection:
    """A prepared DemoEngine's detection, in the shape :func:`outcome` reads."""
    return {**engine.raw, "case_keys": tuple(engine.world.case_keys), "weeks_world": tuple(engine.world.weeks)}


def detect_only(sc: Scenario, without: str | None, *, routing: Any = None,
                run_id: str = "codes-miss-variant") -> Detection | None:
    """Prepare a DemoEngine on ``sc`` (sites, cells, HQ, detection; no site checks) in a throwaway directory, with the
    main run's ``routing`` so every world's X reads the narratives the same way. None when the world does not build
    (a realism check or the generator refuses it). A variant engine never evaluates the rule itself."""
    from demo.collective.collective_demo import DemoEngine, Recorder

    workdir = Path(tempfile.mkdtemp(prefix="mycelic-codes-miss-"))
    engine = None
    try:
        engine = DemoEngine(sc, mode="record", run_id=run_id, workdir=workdir, routing=routing,
                            recorder=Recorder(run_id=run_id, mode="record"), out_dir=workdir / "out")
        engine.without = without
        engine.codes_miss_variants = False
        try:
            engine.prepare()
        except ScenarioError:
            return None
        return engine_detection(engine)
    finally:
        if engine is not None:
            engine.close()
        shutil.rmtree(workdir, ignore_errors=True)


def summary_lines(block: Mapping[str, Any]) -> Sequence[str]:
    """Plain lines for the console and the README: what the run shows, read from the block."""
    main = block["main"]
    lines = [block["statement"], block["author_note"]]
    lines.append(VERDICT_TEXTS[block["holds"]])
    for c in RULE_CHANNELS:
        ch = (main["channels"] or {}).get(c)
        if ch is not None and ch["attributed_no_later"]:
            lines.append(f"{c} resolved the case no later than X.")
    return lines
