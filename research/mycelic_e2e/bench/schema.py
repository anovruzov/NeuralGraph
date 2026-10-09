"""Interfaces shared between the generator (WP2) and the evaluator (WP3). Deliberately minimal.

* :class:`TaskPublic` is what the system under test (feeder, issuer) may see: ids, the asker, the goal spec, the question
  text, the closed option set. It never carries a gold field. ``to_public_dict`` is the only serialisation used for
  ``tasks_<split>.public.json``.
* :class:`Gold` is what only the sealed sink ``bench/gold.py:GoldSink`` may store; its field names are the ones
  ``gold.py`` documents (``cls``, ``answer``, ``expected_abstain``, ``genuine_roots``, ``holders``, ``decoy_options``,
  ``forbidden_holder_ids``, ``forbidden_root_ids``, ``forbidden_markers``, ``raw_allowed_holder_ids``) plus a few
  generator-side facts (``fault``, ``departments``, ``entity_id``). Holder ids are coordinator holder ids (known only
  after the world is materialized), not generator keys.
* :class:`TemplateBank` is the surface-text bank of one split (dev or holdout); two banks must be disjoint (tested).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SPLITS = ("dev", "holdout")
SIZES = ("S", "M", "L")

# task classes (Gold.cls); the fixed mix per split is in world.TASK_MIX
CLASSES = ("cross_domain", "contradiction", "temporal", "common_origin_pos", "common_origin_copies", "coincidence",
           "single_domain", "denied", "cross_tenant", "fault")


@dataclass(frozen=True)
class TemplateBank:
    """Surface text of one split. Placeholders: ``{svc}`` service name (rendered ``<svc>-service``), ``{ctx}`` the task's
    symptom/context phrase, ``{n}`` the statement's number, ``{a}``/``{b}`` department names."""
    name: str
    service_prefixes: tuple[str, ...]
    service_suffixes: tuple[str, ...]
    ctx_adjectives: tuple[str, ...]          # ctx i = "<adjectives[i]> <nouns[i]>": token-disjoint across i
    ctx_nouns: tuple[str, ...]
    departments: tuple[tuple[str, str], ...]  # (display name, taxonomy domain id)
    obs_templates: tuple[str, ...]            # consistent first-hand observations naming the entity in the context
    decoy_templates: tuple[str, ...]          # the entity named in an unrelated context ({ctx} = some other context)
    filler_templates: tuple[str, ...]         # unrelated notes (no entity)
    correction_templates: tuple[str, ...]     # an author's later edit restating the finding
    question_templates: tuple[str, ...]       # cross-department question ({ctx} {a} {b})
    current_question_templates: tuple[str, ...]   # "currently" form for temporal tasks
    goal_templates: tuple[tuple[str, str], ...]   # (title, objective) with {ctx}/{a}/{b}
    goal_only_templates: tuple[tuple[str, str], ...]  # (title, objective) for goal-only tasks ({fam}, {ctx})
    background_templates: tuple[str, ...] = ()       # routine personal / team notes that mention a service ({svc}, {n}); never a task context
    goal_decoy_templates: tuple[str, ...] = ()       # in-scope single-department decoy of a goal-only task (disjoint vocabulary from GOAL_OBS)

    def all_template_strings(self) -> list[str]:
        out = list(self.obs_templates + self.decoy_templates + self.filler_templates + self.correction_templates
                   + self.question_templates + self.current_question_templates + self.goal_decoy_templates + self.background_templates)
        for t, o in self.goal_templates + self.goal_only_templates:
            out += [t, o]
        return out


@dataclass
class TaskPublic:
    task_id: str
    split: str
    tenant: str                      # tenant slug
    asker: str                       # asker's e-mail (login identity); resolved to a session by the issuer
    asker_key: str                   # generator user key
    scope_unit: str                  # generator unit key of the goal/question scope
    goal_title: str
    goal_objective: str
    goal_domains: list[str]          # taxonomy domain ids the goal declares (measurement_source.domains)
    question_text: str | None        # None for goal-only tasks
    candidate_domains: list[str]
    options: list[dict[str, Any]]    # K=4 entity options: {"label", "id", "display", "aliases"}; "abstain" is implicit
    policy: dict[str, Any] = field(default_factory=dict)       # question policy, e.g. {"min_independent_units": {"department": 2}}
    valid_from_days: int | None = None                         # question validity window start (days before issue time)
    goal_only: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Gold:
    task_id: str
    cls: str
    answer: str                                   # option label, or "abstain"
    expected_abstain: bool
    entity_id: str | None = None                  # canonical entity id of the answer (service:<name>)
    genuine_roots: int | None = None              # independent source roots behind the answer (copies counted once)
    holders: list[str] = field(default_factory=list)              # holder ids holding the genuine observations
    departments: list[str] = field(default_factory=list)
    decoy_options: list[str] = field(default_factory=list)        # option labels that are decoys for this task
    forbidden_holder_ids: list[str] = field(default_factory=list)
    forbidden_root_ids: list[str] = field(default_factory=list)
    forbidden_markers: list[str] = field(default_factory=list)
    raw_allowed_holder_ids: list[str] = field(default_factory=list)
    fault: str | None = None
    note: str = ""
    rival_size: int | None = None                 # popular rival: people (one department) who state the same context as a different service
    rival_department: str | None = None           # generator department key of the rival's people
    rival_entity_id: str | None = None            # the rival's canonical entity id (always one of the task's options)
