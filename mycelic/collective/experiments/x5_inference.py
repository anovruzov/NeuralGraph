"""X5 (B3): leakage beyond text, measured by a red team that holds only what HQ holds. Synthetic, same-author and
internal only; ``LEAKAGE.md`` section 12 publishes the result, whatever direction it falls.

    python -m mycelic.collective.experiments.x5_inference prereg --packs P[,P] --run-id ID [--n 1000]
        [--seeds S,..] [--shadow-seeds S,..] [--volume-seeds S,..] [--volume-factor 4] [--k-settings auto|K,..]
        [--variants all|V,..] [--attacks all|A,..] [--artifact-types all|T,..] [--simulated all|none|T,..]
        [--a6-targets-per-site 4] [--primary-b 10000] [--exploratory-b 2000] [--bootstrap-seed x5]
        [--max-pipelines 60] [--runs-dir runs] [--allow-dirty] [--dry-run]
    python -m mycelic.collective.experiments.x5_inference run --prereg FILE --run-id ID --work-dir DIR
        [--runs-dir runs] [--allow-dirty] [--dry-run]

**What the red team holds and may do.** Exactly what HQ holds after G0's stages (edge, pushdown, follow-up, run
files) on a member world: the cells by channel, the usage summaries, HQ's own questions and verdicts, the packets, the
drafts, the outbox, the approvals ledger and the central draft ledger, HQ's store results (the detection result,
recomputed, and the conclusions) and the run files; and it asks questions of its own through the real, budgeted site
verifier (A6). It knows the packs, the lexical extractor and every site's master data. The attacks and their
statistics are in :mod:`.x5_attacks` (pure); this module builds the worlds, runs the pipelines, turns every artifact
into facts, builds the targets with their truth (kept apart) and the shadow priors, and writes the result.

**Worlds.** ``make_world(pack, seed, 2n)``; an original record is a member when
``random.Random(f"x5:{seed}:split:{record_ref}").random() < 0.5``, a forwarded copy follows its origin, and only the
members go through the pipeline (``mode fake``). Shadow worlds (other seeds) never run a pipeline: they give the
priors and the A1 threshold from the lexical rows (:func:`lexical_rows`, equal to what the sites store) and
``build_cells`` at each variant's settings. Variants (pack copies written canonically into the work directory, their
hashes pinned in the prereg): ``default``; ``k<K>`` (egress k and the verdict buckets); ``rmd_flipped``;
``minus_type`` (the pack's primary entity type no longer leaves); ``volume`` (every site's weekly volume times the
factor); and two derived from the default worlds without a pipeline: ``k1_reference`` (the cells replaced by exact
counts; reference, not deployable) and ``a5_injected`` (a cell naming each A5 target's surname added to its site's
bundle after the Boundary, which would refuse it). Pipelines run in the order packs, then default, the k
variants, rmd_flipped, minus_type, volume, then seeds; a variant that cannot run all its seeds under
``--max-pipelines`` runs none.

**Facts** (:class:`.x5_attacks.Fact`; weeks are indexes from the generator's start): a cell's ``n``, ``n_roots`` and
``n_reporters`` (``'<k'`` is ``(1, k - 1)``) and per site one ``covered`` fact per week of each bundle's span and
channel, so a covered week with no cell reads as 0; a usage group's ``calls``; a verdict's buckets (``'a-b'`` is
``(a, b)``, ``'a+'`` is ``(a, None)``, a truncated verdict leaves ``hi`` open): a confirm's support, roots and
reporters over its window, support at least 1 in the newest week and 0 after it; a refute's zero support and its
entity-records bucket; an unknown without a wire reason, for an id that passes the site's master-data rule, as zero
records (an ``unclear`` unknown is misread as absence, which counts against the attacker); a packet's buckets, its
codes (lower bounds) and co-mentions; every egress id a draft, outbox line or ledger payload names, as presence at the
conclusion's confirming sites over its window; the detection result's per-site window counts and contributing sites;
a conclusion's confirming and refuting sites; the run files' verdicts (no truncation flag there, so every bucket is
open above). Every string leaf of every item is a ``string`` fact, deduplicated. The allowed-fields reference is R
(model-free)'s exact cells (codes channel, ``n`` only: its roots and reporters are the records themselves).

**Results** (``runs/x5/<id>/x5.json``, closed schema): per (pack, variant, attack, artifact type) a uniform entry,
controls, measured and simulated mitigations, the primary family with Bonferroni intervals, the bar, the NOT_COVERED
mapping, per-world counts; and ``leakage_section.md``, LEAKAGE section 12's body rendered from x5.json alone. Before
writing: the schema, portability (no path, host or user, no 64-hex token) and a self-scan (no narrative shingle of
any generated world, no person, name or reporter value as a whole token); any failure writes nothing.

Exit codes: 0 ok; 1 the negative control failed (x5.json written); 2 usage, pin, schema, portability or self-scan
error (nothing written), or a positive control not labelled leak (x5.json written); 130 interrupted. The work
directory is removed at the end and on every refusal. The only wall-clock values are ``created_at`` and ``timings``.
"""
from __future__ import annotations

import argparse
import getpass
import random
import re
import shutil
import socket
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .. import runfiles, schemacheck
from ..detect.detectors import detect
from ..detect.store import CollectiveStore, HqReader
from ..edge.egress import CHANNELS, PACKET_SUPPRESSED, SUPPRESSED, VERDICTS, read_log
from ..edge.extract import LexicalExtractor, sense
from ..edge.records import InputRow, RecordStore
from ..edge.site import EdgeSite, build_cells, in_master_data, valid_claim
from ..edge.verify import SiteVerifier
from ..edge.weeks import closed_through, iso_week, week_monday
from ..evaluate.baselines import org_for_sites, r_mf_cells
from ..evaluate.harness import arr_schema, obj_schema, typed_schema, union_problems
from ..followup.ledger import LEDGER_FILE, FollowupLedger
from ..inference.ledger import read_ledger
from ..jsonio import StrictJsonError, canonical_bytes, canonical_dumps, sha256_hex, strict_load
from ..leakage import CANARY_PREFIX, NOT_COVERED, Artifact, LeakageError, Manifest, scan
from ..packs.canonical import Canonicaliser
from ..packs.generator import GeneratorError, world_digest
from ..packs.loader import FrozenPack, PackError, is_builtin_ref, load_pack
from ..pushdown.gate import STATUSES as GATE_STATUSES
from ..pushdown.questions import PushdownError, build_question
from . import x5_attacks as xa
from .common import (ROOT, DryRun, UsageError, check_run_id, code_commit, code_dirty, code_files, code_hash, fail,
                     run_dir, split_list, utc_clock, write_json_atomic)
from .g0_canary import DAY_TIME, G0_ENTERPRISE, G0_TIE_SALT, MAX_RECORDS, build_context, make_world, run_stages

CLI = "experiments.x5_inference"
KIND = "x5"
SCHEMA_VERSION = 1
X5_CODE_FILES = ("mycelic/collective/*.py", "mycelic/collective/detect/*.py", "mycelic/collective/edge/*.py",
                 "mycelic/collective/evaluate/*.py", "mycelic/collective/followup/*.py",
                 "mycelic/collective/inference/*.py", "mycelic/collective/packs/*.py",
                 "mycelic/collective/pushdown/*.py", "mycelic/collective/experiments/__init__.py",
                 "mycelic/collective/experiments/common.py", "mycelic/collective/experiments/g0_canary.py",
                 "mycelic/collective/experiments/x5_inference.py", "mycelic/collective/experiments/x5_attacks.py")
SITES = 6
DEFAULT_N = 1000
MIN_N = 20
MAX_VOLUME_FACTOR = 10
N_SEEDS, N_SHADOW, N_VOLUME = 5, 3, 2
SEED_MODULUS = 100000
MAX_SEED_CANDIDATES = 1000
DEFAULT_VOLUME_FACTOR = 4
DEFAULT_A6_TARGETS = 4
MAX_A6_TARGETS = 50
DEFAULT_PRIMARY_B = 10000
DEFAULT_EXPLORATORY_B = 2000
DEFAULT_BOOTSTRAP_SEED = "x5"
DEFAULT_MAX_PIPELINES = 60
AUTO_K = (2, 10)
MAX_K = 49
BOOTSTRAP_SEED_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,39}", re.ASCII)
K_VARIANT_RE = re.compile(r"k([1-9][0-9]?)", re.ASCII)
DERIVED_VARIANTS = ("k1_reference", "a5_injected")
SIMULATED = ("drop_lt_k", "four_week")
FOUR_WEEKS = 4
SIMULATED_LABEL = "simulated: not implemented in the Boundary; implementing it is a code change"
K1_LABEL = "reference, not deployable"
A5_INJECTED_LABEL = "positive control: injected after the Boundary, which would refuse it; never deployable"
DESIGNED_LABEL = "designed disclosure, STRATEGY 6.4"
CAP_REASON = "cap max_pipelines reached"
NO_VOLUME_REASON = "no volume world"
NO_VOLUME_SHADOW_REASON = "no volume shadow world"
STAMPS = {"synthetic": True, "internal_only": True, "measurement": False, "data_label": "synthetic",
          "same_author": True, "mode": "fake"}
SPLIT_RULE = ('random.Random(f"x5:{seed}:split:{record_ref}").random() < 0.5 makes an original a member; a forwarded '
              'copy follows its origin')
SEED_RULE = ('1 + int(sha256(f"x5:{code_commit}:{i}".encode()).hexdigest()[:8], 16) % 100000 for i = 0, 1, ...; '
             'duplicates skipped; the first 5 are the seeds, the next 3 the shadow seeds; the volume seeds are the '
             'first 2 seeds in ascending order whose volume world generates in every pack')
A1_THRESHOLD_RULE = ("the value among the sorted distinct shadow scores (shadow cells at the variant's own settings) "
                     "that maximises shadow accuracy, the smallest on ties; member iff score >= t; A1_fixed: member "
                     "iff score == 1; a record without keys takes random.Random(f\"x5:{seed}:a1coin:{ref}\")")
A2_TRUTH_RULE = ("the set of the record's affirmed gold predicates (negated false, predicate not null); the "
                 "prediction is correct when it is in the set")
A6_PREDICATE_RULE = ("per sampled entity, the most common affirmed predicate of the shadow worlds' member records that "
                     "name that entity id among their structured entities, ties and an unseen entity broken by the "
                     "sorted predicate id")
VALUE_RULE = ("per target: A1 2 * correct - 1 (baseline 0.5); A2 to A6 attack_correct - baseline_correct; the "
              "advantage is their mean with a cluster bootstrap over (seed, site, week), A6 (seed, site, window start)")
NOT_COVERED_MAPPING = (
    ("Attribute inference on counts and claims (X5).", ["A2", "A3"], None, None),
    ("Membership inference (X5).", ["A1_calibrated", "A1_fixed"], None, None),
    ("Differencing between verdict buckets and weekly cells (G6, X5).", ["A3"], None, None),
    ("Cross-site duplicates without an origin marker, which each site counts as independent (X5).", [], None,
     "not attacked: an independence (detection-integrity) limit, not a disclosure of a record value; X1 "
     "known_hard_cases covers it"),
    ("Presence or absence of an entity at a site in a question window, revealed by a refute versus an unknown; "
     "limited, not prevented, by the per-entity and per-site daily question budgets (G6, X5); with "
     "require_master_data only for master-data ids, and never through a person value or the reporter.", ["A6"],
     "verdicts_active", None),
    ("Bucket transitions between overlapping question windows for one key, which can narrow a count inside its "
     "bucket (G6, X5).", ["A3", "A6"], None, None),
    ("A packet discloses, for one window, which pack codes and which master-data ids co-occur with the key in at least "
     "k confirmed records at a site, as count buckets (G7, X5).", ["A2"], "packets", None),
)
CONTENT_HASH_EXCLUDES = ("/content_hash", "/created_at", "/run_id", "/timings")


class HarnessError(RuntimeError):
    """A check the harness makes on itself failed: a bug, never a result. The CLI exits 2."""


# --------------------------------------------------------------------------------------------------- code and seeds

def x5_code_files() -> list[str]:
    return code_files(X5_CODE_FILES)


def x5_code_hash() -> str:
    return code_hash([ROOT / p for p in x5_code_files()])


def seed_candidate(commit: str, i: int) -> int:
    return 1 + int(sha256_hex(f"x5:{commit}:{i}".encode("utf-8"))[:8], 16) % SEED_MODULUS


def derive_seeds(commit: str, wanted: int) -> tuple[list[int], list[dict[str, Any]]]:
    """The first ``wanted`` distinct candidates of the seed rule, and every candidate examined."""
    if commit == "unknown":
        raise UsageError("the seed rule needs a known code_commit (or pass --seeds and --shadow-seeds)") from None
    out: list[int] = []
    examined: list[dict[str, Any]] = []
    for i in range(MAX_SEED_CANDIDATES):
        value = seed_candidate(commit, i)
        status = "duplicate" if value in out else "accepted"
        examined.append({"i": i, "value": value, "status": status})
        if status == "accepted":
            out.append(value)
            if len(out) == wanted:
                return out, examined
    raise UsageError("the seed rule found too few distinct seeds") from None


def split_members(records: Sequence[Mapping[str, Any]], seed: int) -> dict[str, bool]:
    """Membership per record ref: an original by its coin, a forwarded copy by its origin."""
    member: dict[str, bool] = {}
    for r in records:
        if r["origin_ref"] is None:
            member[r["record_ref"]] = random.Random(f"x5:{seed}:split:{r['record_ref']}").random() < 0.5
        else:
            member[r["record_ref"]] = member.get(r["origin_ref"], False)
    return member


# --------------------------------------------------------------------------------------------------- variants

@dataclass(frozen=True)
class Variant:
    id: str
    kind: str                  # "pipeline" or "derived"
    pack: FrozenPack
    k: int
    volume_factor: int


def removed_type(pack: FrozenPack) -> str:
    return pack.mapping()["primary_entity_type"]


def a5_fields(pack: FrozenPack) -> list[str]:
    persons = pack.generator["persons"]
    return sorted(f for f in persons if persons[f]["kind"] == "name")


def bucket_edges(pack: FrozenPack, k: int) -> list[int]:
    return [k] + [b for b in pack.egress.verdict_count_buckets[1:] if b > k]


def _rewrite(path: Path, change: Any) -> None:
    doc = strict_load(path.read_bytes())
    change(doc)
    path.write_bytes((canonical_dumps(doc) + "\n").encode("utf-8"))


def write_variant(base: FrozenPack, variant_id: str, dest: Path, *, volume_factor: int,
                  entity_type: str | None = None) -> FrozenPack:
    """The pack copy of a pipeline variant in ``dest`` (absent), each edited file canonical JSON plus a newline.
    ``minus_type`` removes ``entity_type`` (default: the pack's primary entity type) from what may leave."""
    shutil.copytree(base.directory, dest)
    k_match = K_VARIANT_RE.fullmatch(variant_id)
    if variant_id == "default":
        pass
    elif k_match is not None:
        k = int(k_match.group(1))

        def edit_k(doc: dict[str, Any]) -> None:
            doc["k"] = k
            doc["verdict_count_buckets"] = bucket_edges(base, k)
        _rewrite(dest / "egress.json", edit_k)
    elif variant_id == "rmd_flipped":
        _rewrite(dest / "egress.json", lambda doc: doc.update(require_master_data=not doc["require_master_data"]))
    elif variant_id == "minus_type":
        t = entity_type or removed_type(base)

        def edit_vocabulary(doc: dict[str, Any]) -> None:
            doc["entity_types"][t]["egress"] = False

        def edit_egress(doc: dict[str, Any]) -> None:
            doc["egress_entity_types"] = [x for x in doc["egress_entity_types"] if x != t]

        def edit_questions(doc: dict[str, Any]) -> None:
            templates = {}
            for name, template in doc["templates"].items():
                types = [x for x in template["entity_types"] if x != t]
                if types:
                    templates[name] = {**template, "entity_types": types}
            doc["templates"] = templates
        _rewrite(dest / "vocabulary.json", edit_vocabulary)
        _rewrite(dest / "egress.json", edit_egress)
        _rewrite(dest / "questions.json", edit_questions)
    elif variant_id == "volume":
        def edit_volume(doc: dict[str, Any]) -> None:
            for site in doc["sites"]:
                site["weekly_volume"] = [v * volume_factor for v in site["weekly_volume"]]
        _rewrite(dest / "generator.json", edit_volume)
    else:
        raise UsageError(f"unknown pipeline variant {variant_id}") from None
    try:
        return load_pack(dest)
    except PackError:
        if variant_id == "minus_type":
            raise UsageError(f"pack {base.id}: a copy without the egress type {entity_type or removed_type(base)} "
                             "does not load") from None
        raise UsageError(f"pack {base.id}: the {variant_id} copy does not load") from None


def variant_record(base: FrozenPack, variant_id: str, copy: FrozenPack | None, volume_factor: int) -> dict[str, Any]:
    pack = copy if copy is not None else base
    kind = "derived" if variant_id in DERIVED_VARIANTS else "pipeline"
    k = 1 if variant_id == "k1_reference" else pack.egress.k
    return {"id": variant_id, "kind": kind, "k": k, "buckets": list(pack.egress.verdict_count_buckets),
            "require_master_data": pack.egress.require_master_data,
            "egress_entity_types": list(pack.egress.egress_entity_types),
            "volume_factor": volume_factor if variant_id == "volume" else 1,
            "hashes": pack.hashes() if kind == "pipeline" else None}


# --------------------------------------------------------------------------------------------------- worlds

@dataclass
class WorldData:
    seed: int
    records: tuple[dict[str, Any], ...]
    gold: Mapping[str, Any]
    master: Mapping[str, Mapping[str, tuple[str, ...]]]
    sites: tuple[str, ...]
    start: date
    weeks: int
    digest: str
    member: dict[str, bool]
    week_of: dict[str, int]
    last_week: int

    @property
    def members(self) -> tuple[dict[str, Any], ...]:
        return tuple(r for r in self.records if self.member[r["record_ref"]])

    def originals(self) -> list[dict[str, Any]]:
        return [r for r in self.records if r["origin_ref"] is None]

    def iso(self, index: int) -> str:
        return iso_week(self.start + timedelta(days=7 * index))


def week_index(start: date, week: str) -> int:
    return (week_monday(week) - start).days // 7


def build_world(pack: FrozenPack, seed: int, n_records: int) -> WorldData:
    """The pack's seeded world cut to ``n_records`` records, split into members and non-members."""
    world, weeks = make_world(pack, seed, n_records)
    records = tuple(world.records[:n_records])
    start = date.fromisoformat(world.params["start"])
    week_of = {r["record_ref"]: week_index(start, iso_week(r["received_date"])) for r in records}
    return WorldData(seed=seed, records=records, gold=world.gold, master=world.master_data,
                     sites=tuple(world.params["site_ids"]), start=start, weeks=weeks, digest=world_digest(world),
                     member=split_members(records, seed), week_of=week_of, last_week=max(week_of.values()))


# --------------------------------------------------------------------------------------------------- lexical rows

def lexical_claims(pack: FrozenPack, record: Mapping[str, Any], canonicaliser: Canonicaliser,
                   extractor: LexicalExtractor) -> tuple[Any, ...]:
    """The claims a site stores for ``record`` under the lexical semantics (the fake model replays them)."""
    _, _, claims = sense(record, pack, canonicaliser, extractor)
    return tuple(c for c in claims if valid_claim(c, pack, canonicaliser))


def _site_tools(pack: FrozenPack, master: Mapping[str, Any], site: str) -> tuple[Canonicaliser, LexicalExtractor]:
    canonicaliser = Canonicaliser(pack, known=master[site])
    return canonicaliser, LexicalExtractor(pack, canonicaliser)


def lexical_rows(pack: FrozenPack, records: Iterable[Mapping[str, Any]],
                 master: Mapping[str, Mapping[str, Sequence[str]]]) -> dict[str, list[InputRow]]:
    """Per site, the emission input rows of its own (non-forwarded) records among ``records``: each record's lexical
    claims (codes and text channels, the site's canonicaliser, ``valid_claim``), counted in its received week, its
    own ref as root. Equal to ``RecordStore.emission_inputs`` of a fake-mode site that ingested the same records."""
    tools: dict[str, tuple[Canonicaliser, LexicalExtractor]] = {}
    rows: dict[str, list[InputRow]] = {}
    for r in records:
        site = r["site"]
        if r["origin_site"] is not None and r["origin_site"] != site:
            continue
        if site not in tools:
            tools[site] = _site_tools(pack, master, site)
        week = iso_week(r["received_date"])
        for c in lexical_claims(pack, r, *tools[site]):
            rows.setdefault(site, []).append(InputRow(week, r["record_ref"], r["record_ref"], r["reporter"],
                                                      c.entity_type, c.entity_id, c.predicate, c.channel,
                                                      c.res_conf))
    return {site: sorted(rows[site], key=lambda x: (x.count_week, x.record_ref, x.entity_type, x.entity_id,
                                                    x.predicate)) for site in sorted(rows)}


def record_keys(variant: Variant, world: WorldData) -> dict[str, tuple[tuple[str, str, str, str], ...]]:
    """Per original record, the attacker's keys: its lexical claims of an egress type that pass the site's
    master-data rule, as (type, id, predicate, channel), sorted."""
    pack = variant.pack
    egress = frozenset(pack.egress.egress_entity_types)
    tools: dict[str, tuple[Canonicaliser, LexicalExtractor]] = {}
    out: dict[str, tuple[tuple[str, str, str, str], ...]] = {}
    for r in world.originals():
        site = r["site"]
        if site not in tools:
            tools[site] = _site_tools(pack, world.master, site)
        keys = {(c.entity_type, c.entity_id, c.predicate, c.channel) for c in lexical_claims(pack, r, *tools[site])
                if c.entity_type in egress and in_master_data(pack, world.master[site], c.entity_type, c.entity_id)}
        out[r["record_ref"]] = tuple(sorted(keys))
    return out


def true_cells(variant: Variant, rows: Mapping[str, Sequence[InputRow]],
               master: Mapping[str, Mapping[str, Sequence[str]]],
               start: date) -> dict[tuple[str, str, str, str, int, str], dict[str, str | None]]:
    """The variant's would-be cells at k = 1 (egress types and the master-data rule applied, as ``build_cells``
    does): per (site, type, id, predicate, week index, channel), each record's reporter."""
    pack = variant.pack
    egress = frozenset(pack.egress.egress_entity_types)
    out: dict[tuple[str, str, str, str, int, str], dict[str, str | None]] = {}
    for site in sorted(rows):
        for row in rows[site]:
            if row.entity_type not in egress or not in_master_data(pack, master[site], row.entity_type,
                                                                   row.entity_id):
                continue
            key = (site, row.entity_type, row.entity_id, row.predicate, week_index(start, row.count_week), row.channel)
            out.setdefault(key, {})[row.record_ref] = row.reporter_id
    return out


# --------------------------------------------------------------------------------------------------- facts

def string_leaves(obj: Any) -> list[str]:
    out: list[str] = []
    stack = [obj]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, Mapping):
            stack.extend(node[k] for k in sorted(node, reverse=True))
        elif isinstance(node, (list, tuple)):
            stack.extend(reversed(node))
    return out


def bucket_interval(label: str, k: int, open_above: bool = False) -> tuple[int, int | None]:
    """A count label as an interval: ``'<k'`` is (1, k - 1), ``'a-b'`` (a, b), ``'a+'`` (a, None), ``'a'`` (a, a);
    ``open_above`` (a truncated verdict, or a verdict without its truncation flag) leaves ``hi`` None."""
    if label == SUPPRESSED:
        lo, hi = 1, k - 1
    elif label.endswith("+"):
        lo, hi = int(label[:-1]), None
    elif "-" in label:
        a, b = label.split("-")
        lo, hi = int(a), int(b)
    else:
        lo = hi = int(label)
    return lo, (None if open_above else hi)


def count_interval(value: Any, k: int) -> tuple[int, int]:
    return (1, k - 1) if value == SUPPRESSED else (value, value)


@dataclass
class ConclusionContext:
    window: tuple[int, int]
    predicate: str
    confirming: tuple[str, ...]


class Extractor:
    """Turns what HQ holds after one pipeline into :class:`.x5_attacks.Fact` s, one list per artifact type."""

    def __init__(self, pack: FrozenPack, world: WorldData) -> None:
        self.pack = pack
        self.k = pack.egress.k
        self.world = world
        self.span = (0, world.last_week)
        self.egress = frozenset(pack.egress.egress_entity_types)
        self.canonicaliser = Canonicaliser(pack)
        self._seen: set[tuple[Any, ...]] = set()

    def week(self, iso: str) -> int:
        return week_index(self.world.start, iso)

    def strings(self, out: list[xa.Fact], source: str, site: str | None, first: int, last: int, obj: Any) -> None:
        for text in string_leaves(obj):
            key = (source, site, first, last, text)
            if key not in self._seen:
                self._seen.add(key)
                out.append(xa.Fact(source, site, first, last, None, None, None, None, "string", 0, None, text))

    def coverage(self, body: Mapping[str, Any]) -> tuple[int, int]:
        first = 0 if body["after"] is None else self.week(body["after"]) + 1
        return first, self.week(body["closed_through"])

    # ------------------------------------------------------------------ cells and usage
    def cells(self, source: str, bodies: Sequence[Mapping[str, Any]], channels: Sequence[str], *, period: int = 1,
              missing_hi: int = 0, k: int | None = None) -> list[xa.Fact]:
        """Count facts per cell (spans of ``period`` weeks) and one covered fact per period of each bundle's span and
        channel: no cell there means a count of at most ``missing_hi`` (0, or k - 1 when cells below k are dropped)."""
        k = self.k if k is None else k
        out: list[xa.Fact] = []
        for body in bodies:
            site = body["site"]
            first, last = self.coverage(body)
            self.strings(out, source, site, first, last, {key: v for key, v in body.items() if key != "cells"})
            for ch in channels:
                for q in range(first // period, last // period + 1):
                    out.append(xa.Fact(source, site, q * period, q * period + period - 1, None, None, None, ch,
                                       "covered", 0, missing_hi))
            for c in body["cells"]:
                if c["channel"] not in channels:
                    continue
                w = self.week(c["iso_week"])
                for name in ("n", "n_roots", "n_reporters"):
                    lo, hi = count_interval(c[name], k)
                    out.append(xa.Fact(source, site, w, w + period - 1, c["entity_type"], c["entity_id"],
                                       c["predicate"], c["channel"], name, lo, hi))
                self.strings(out, source, site, w, w + period - 1, c)
        return out

    def usage(self, bodies: Sequence[Mapping[str, Any]]) -> list[xa.Fact]:
        out: list[xa.Fact] = []
        for body in bodies:
            first, last = self.coverage(body)
            self.strings(out, "usage_summary", body["site"], first, last, body)
            for g in body["groups"]:
                lo, hi = count_interval(g["calls"], self.k)
                out.append(xa.Fact("usage_summary", body["site"], first, last, None, None, None, None, "calls", lo,
                                   hi))
        return out

    # ------------------------------------------------------------------ verdicts and packets
    def verdict(self, out: list[xa.Fact], source: str, body: Mapping[str, Any], params: Mapping[str, str],
                window: Mapping[str, str], site: str) -> None:
        t, e, p = params["entity_type"], params["entity_id"], params["predicate"]
        first, last = self.week(window["start_week"]), self.week(window["end_week"])
        open_above = body.get("truncated") is not False
        verdict = body["verdict"]
        if verdict == "confirm":
            for name, key in (("support", "support_bucket"), ("roots", "roots_bucket"),
                              ("reporters", "reporters_bucket")):
                lo, hi = bucket_interval(body[key], self.k, open_above)
                out.append(xa.Fact(source, site, first, last, t, e, p, None, name, lo, hi))
            newest = body.get("newest_week")
            if newest is not None:
                nw = self.week(newest)
                out.append(xa.Fact(source, site, nw, nw, t, e, p, None, "support", 1, None))
                out += [xa.Fact(source, site, w, w, t, e, p, None, "support", 0, 0) for w in range(nw + 1, last + 1)]
        elif verdict == "refute":
            out.append(xa.Fact(source, site, first, last, t, e, p, None, "support", 0, 0))
            if body.get("entity_records_bucket") is not None:
                lo, hi = bucket_interval(body["entity_records_bucket"], self.k, open_above)
                out.append(xa.Fact(source, site, first, last, t, e, None, None, "entity_records", lo, hi))
        elif body.get("reason") is None and in_master_data(self.pack, self.world.master[site], t, e):
            out.append(xa.Fact(source, site, first, last, t, e, None, None, "entity_records", 0, 0))
            out.append(xa.Fact(source, site, first, last, t, e, None, None, "support", 0, 0))

    def verdicts(self, source: str, verdict_rows: Sequence[Mapping[str, Any]],
                 question_rows: Sequence[Mapping[str, Any]]) -> list[xa.Fact]:
        out: list[xa.Fact] = []
        questions = {row["body"]["question_id"]: row["body"] for row in question_rows}
        for row in question_rows:
            w = row["body"]["window"]
            self.strings(out, source, row["site"], self.week(w["start_week"]), self.week(w["end_week"]), row["body"])
        for row in verdict_rows:
            body = row["body"]
            w = body["window"]
            self.strings(out, source, body["site"], self.week(w["start_week"]), self.week(w["end_week"]), body)
            question = questions.get(body["question_id"])
            if question is not None:
                self.verdict(out, source, body, question["params"], w, body["site"])
        return out

    def packet(self, out: list[xa.Fact], source: str, body: Mapping[str, Any]) -> None:
        site, w = body["site"], body["window"]
        first, last = self.week(w["start_week"]), self.week(w["end_week"])
        self.strings(out, source, site, first, last, body)
        t, e, p = body["candidate_key"].split(":")
        if body["verdict"] == "confirm":
            open_above = body.get("truncated") is not False
            for name, key in (("support", "support_bucket"), ("roots", "roots_bucket"),
                              ("reporters", "reporters_bucket")):
                lo, hi = bucket_interval(body[key], self.k, open_above)
                out.append(xa.Fact(source, site, first, last, t, e, p, None, name, lo, hi))
        for code in body["codes"]:
            found = self.pack.codes.get(code["code"])
            if found is None:
                continue
            lo, hi = (1, None) if code["n"] == PACKET_SUPPRESSED else bucket_interval(code["n"], self.k)
            out.append(xa.Fact(source, site, first, last, t, e, found.predicate, None, "code", lo, hi))
        for m in body["co_mentions"]:
            lo, hi = bucket_interval(m["n"], self.k)
            out.append(xa.Fact(source, site, first, last, m["entity_type"], m["entity_id"], p, None, "co_mention",
                               lo, hi))

    def packets(self, rows: Sequence[Mapping[str, Any]]) -> list[xa.Fact]:
        out: list[xa.Fact] = []
        for row in rows:
            self.packet(out, "packets", row["body"])
        return out

    # ------------------------------------------------------------------ follow-up, HQ results and run files
    def presence(self, out: list[xa.Fact], source: str, obj: Any, context: ConclusionContext | None) -> None:
        """Every egress id named in a payload string, as presence at the conclusion's confirming sites."""
        if context is None:
            return
        named: set[tuple[str, str]] = set()
        for text in string_leaves(obj):
            named.update((m.entity_type, m.entity_id) for m in self.canonicaliser.scan(text).mentions
                         if m.entity_type in self.egress)
        first, last = context.window
        for t, e in sorted(named):
            out += [xa.Fact(source, site, first, last, t, e, context.predicate, None, "presence", 1, None)
                    for site in context.confirming]

    def entries(self, out: list[xa.Fact], source: str, entries: Sequence[Mapping[str, Any]],
                context: Any) -> None:
        """Follow-up ledger entries (or their run-file lines): strings, the packets of executed results, and the ids
        named in payloads as presence."""
        for entry in entries:
            self.strings(out, source, None, *self.span, entry)
            payload = entry.get("payload")
            if not isinstance(payload, Mapping):
                continue
            result = payload.get("result")
            if entry.get("kind") == "executed" and isinstance(result, Mapping) and "packets" in result:
                for body in result["packets"]:
                    self.packet(out, source, body)
            self.presence(out, source, payload, context(entry.get("key")))

    def followup(self, entries: Sequence[Mapping[str, Any]], outbox: Sequence[Mapping[str, Any]],
                 central: Sequence[Mapping[str, Any]], context: Any) -> list[xa.Fact]:
        out: list[xa.Fact] = []
        self.entries(out, "followup", entries, context)
        for line in outbox:
            self.strings(out, "followup", None, *self.span, line)
            self.presence(out, "followup", line.get("draft"), context(line.get("key")))
        for row in central:
            self.strings(out, "followup", None, *self.span, row)
        return out

    def hq_results(self, result: Mapping[str, Any], conclusions: Sequence[tuple[Mapping[str, Any],
                                                                               Mapping[str, Any]]]) -> list[xa.Fact]:
        out: list[xa.Fact] = []
        for c in result["candidates"]:
            snapshot = c["snapshot"]
            if snapshot is None:
                continue
            first, last = self.week(snapshot["window"][0]), self.week(snapshot["window"][1])
            self.strings(out, "hq_results", None, first, last, c)
            t, e, p = c["entity_type"], c["entity_id"], c["predicate"]
            for d in snapshot["d2"]["sites"]:
                if d["c"] is not None and d["c"] >= 1:
                    out.append(xa.Fact("hq_results", d["site"], first, last, t, e, p, None, "support", d["c"], None))
            out += [xa.Fact("hq_results", s, first, last, t, e, p, None, "presence", 1, None)
                    for s in snapshot["contributing_sites"]]
        for body, question in conclusions:
            w = question["window"]
            first, last = self.week(w["start_week"]), self.week(w["end_week"])
            self.strings(out, "hq_results", None, first, last, body)
            t, e, p = body["candidate_key"].split(":")
            used = body["gate"]["used"]
            out += [xa.Fact("hq_results", u["site"], first, last, t, e, p, None, "presence", 1, None)
                    for u in used if u["verdict"] == "confirm"]
            out += [xa.Fact("hq_results", u["site"], first, last, t, e, p, None, "support", 0, 0)
                    for u in used if u["verdict"] == "refute"]
            support = body["gate"]["support"]
            newest = support.get("newest_week")
            counted = support.get("confirming_sites") or []
            if newest is not None:
                nw = self.week(newest)
                if len(counted) == 1:
                    out.append(xa.Fact("hq_results", counted[0], nw, nw, t, e, p, None, "support", 1, None))
                out += [xa.Fact("hq_results", s, w_, w_, t, e, p, None, "support", 0, 0)
                        for s in counted for w_ in range(nw + 1, last + 1)]
        return out

    def run_files(self, trace: Mapping[str, Any], approvals: Sequence[Mapping[str, Any]],
                  scorecard: Mapping[str, Any], ledger: Sequence[Mapping[str, Any]]) -> list[xa.Fact]:
        out: list[xa.Fact] = []
        questions = {q["question_id"]: q for q in trace["questions"]}
        contexts: dict[str, ConclusionContext] = {}
        for q in trace["questions"]:
            w = q["window"]
            self.strings(out, "run_files", None, self.week(w["start_week"]), self.week(w["end_week"]), q)
        for c in trace["conclusions"]:
            q = questions.get(c["conclusion_id"][2:])
            if q is None:
                self.strings(out, "run_files", None, *self.span, c)
                continue
            t, e, p = q["candidate_key"].split(":")
            w = q["window"]
            first, last = self.week(w["start_week"]), self.week(w["end_week"])
            self.strings(out, "run_files", None, first, last, c)
            for v in c["verdicts"]:
                self.verdict(out, "run_files", v, {"entity_type": t, "entity_id": e, "predicate": p}, w, v["site"])
            contexts[c["conclusion_id"]] = ConclusionContext(
                (first, last), p, tuple(v["site"] for v in c["verdicts"] if v["verdict"] == "confirm"))
        self.entries(out, "run_files", approvals, lambda key: contexts.get(conclusion_of(key)))
        self.strings(out, "run_files", None, *self.span, scorecard)
        for row in ledger:
            self.strings(out, "run_files", None, *self.span, row)
        return out

    def reference(self, cells: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[xa.Fact]:
        """R (model-free)'s exact cells, codes channel, ``n`` only, and every week of every site covered."""
        out: list[xa.Fact] = []
        source = "allowed_fields_reference"
        for site in self.world.sites:
            out += [xa.Fact(source, site, w, w, None, None, None, CHANNELS[0], "covered", 0, 0)
                    for w in range(self.span[0], self.span[1] + 1)]
        for site in sorted(cells):
            for c in cells[site]:
                w = self.week(c["iso_week"])
                out.append(xa.Fact(source, site, w, w, c["entity_type"], c["entity_id"], c["predicate"], c["channel"],
                                   "n", c["n"], c["n"]))
                self.strings(out, source, site, w, w, c)
        return out


def conclusion_of(key: Any) -> str | None:
    """The conclusion id of a follow-up key ``act:<conclusion id>:<type>:<digest>``."""
    if not isinstance(key, str) or not key.startswith("act:"):
        return None
    parts = key.split(":")
    return parts[1] if len(parts) == 4 else None


# --------------------------------------------------------------------------------------------------- A6 probe

@dataclass
class A6Probe:
    windows: list[tuple[str, str, str, str, int, int, str | None]]    # site, type, id, predicate, first, last, verdict
    questions: int
    days_needed: int
    budget_responses: int


def askable(pack: FrozenPack, master: Mapping[str, Sequence[str]]) -> list[tuple[str, str]]:
    """The ids the attacker can ask a site about: of every egress type, the site's master-data ids (types with an id
    format) or every id (alias-only types), sorted."""
    out = []
    for t in sorted(pack.egress.egress_entity_types):
        et = pack.entity_types[t]
        out += [(t, e) for e in sorted(master.get(t, ()) if et.id_format is not None else (et.ids or ()))]
    return out


def a6_targets(pack: FrozenPack, world: WorldData, per_site: int,
               predicate_of: Mapping[tuple[str, str], str]) -> dict[str, list[tuple[str, str, str]]]:
    out = {}
    for site in sorted(world.sites):
        ids = askable(pack, world.master[site])
        drawn = random.Random(f"x5:{world.seed}:a6:{site}").sample(ids, min(per_site, len(ids)))
        out[site] = [(t, e, predicate_of[(t, e)]) for t, e in sorted(drawn)]
    return out


def a6_windows(world: WorldData, m: int) -> list[tuple[int, int]]:
    return [(w, w + m - 1) for w in range(0, world.last_week - m + 2)]


def a6_probe(pack: FrozenPack, out_dir: Path, clock: Any, *, sites: Sequence[str],
             master: Mapping[str, Mapping[str, Sequence[str]]], targets: Mapping[str, Sequence[tuple[str, str, str]]],
             windows: Sequence[tuple[int, int]], world: WorldData, start_day: date, max_days: int) -> A6Probe:
    """Ask every target every window through a fresh real site and verifier per site, from ``start_day`` on: each
    day, each target at most the pack's per-entity budget of new windows. A ``budget`` answer is never an answer:
    the window is asked again on a later day (and counted)."""
    budget = pack.egress.question_budget_per_entity_per_day
    pending = {(site, target): list(range(len(windows))) for site in sorted(targets) for target in targets[site]}
    answers: dict[tuple[str, tuple[str, str, str], int], str] = {}
    questions = budget_responses = days = 0
    edge, hq = out_dir / "edge", out_dir / "hq"
    opened = [EdgeSite(pack, s, edge, runtime=None, clock=clock, master_data=master[s], hq_dir=hq)
              for s in sorted(sites)]
    try:
        verifiers = {s.site_id: SiteVerifier(s, runtime=None, clock=clock, demo_seed=world.seed) for s in opened}
        day = start_day
        while any(pending.values()):
            if days >= max_days:
                raise HarnessError("the A6 probe did not finish within its day cap") from None
            clock.set(day.isoformat() + DAY_TIME)
            for site in sorted(targets):
                for target in targets[site]:
                    queue = pending[(site, target)]
                    asked = 0
                    while queue and asked < budget:
                        first, last = windows[queue[0]]
                        question = build_question(pack, entity_type=target[0], entity_id=target[1],
                                                  predicate=target[2],
                                                  window={"start_week": world.iso(first), "end_week": world.iso(last)},
                                                  as_of=day.isoformat())
                        verdict = verifiers[site].answer(question)
                        questions += 1
                        if verdict["reason"] == "budget":
                            budget_responses += 1
                            break
                        answers[(site, target, queue.pop(0))] = verdict["verdict"]
                        asked += 1
            days += 1
            day += timedelta(days=1)
    finally:
        for s in opened:
            s.close()
    rows = [(site, *target, *windows[j], answers.get((site, target, j)))
            for site in sorted(targets) for target in targets[site] for j in range(len(windows))]
    return A6Probe(windows=rows, questions=questions, days_needed=days, budget_responses=budget_responses)


# --------------------------------------------------------------------------------------------------- one pipeline

@dataclass
class WorldRun:
    facts: dict[str, list[xa.Fact]]
    reference: list[xa.Fact]
    rows: dict[str, list[InputRow]]
    bundles: list[dict[str, Any]]
    a6: A6Probe | None
    counts: dict[str, Any]


def _hq_context(reader: HqReader, ex: Extractor) -> Any:
    cache: dict[str, ConclusionContext | None] = {}

    def resolve(key: Any) -> ConclusionContext | None:
        cid = conclusion_of(key)
        if cid is None:
            return None
        if cid not in cache:
            rows = reader.conclusions(cid)
            context = None
            if rows:
                body = strict_load(rows[-1].body)
                question = reader.question(rows[-1].question_id)
                if question is not None:
                    q = strict_load(question.body)
                    w = q["window"]
                    context = ConclusionContext(
                        (ex.week(w["start_week"]), ex.week(w["end_week"])), q["params"]["predicate"],
                        tuple(u["site"] for u in body["gate"]["used"] if u["verdict"] == "confirm"))
            cache[cid] = context
        return cache[cid]

    return resolve


def _of(rows: Sequence[Mapping[str, Any]], artifact_type: str) -> list[Mapping[str, Any]]:
    return [r for r in rows if r["artifact_type"] == artifact_type]


def run_world(variant: Variant, world: WorldData, directory: Path, *,
              a6: Mapping[str, Sequence[tuple[str, str, str]]] | None, max_a6_days: int) -> WorldRun:
    """G0's stages on the members of ``world`` under ``variant``, then the A6 probe, then what HQ holds as facts
    and, separately, the sites' true rows. ``directory`` is deleted before this returns."""
    pack = variant.pack
    directory.mkdir(parents=True)
    try:
        ctx = build_context(pack, world.members, world.master, directory, mode="fake", seed=world.seed, routing=None,
                            sites=world.sites)
        run_stages(ctx)
        hq, base = directory / "hq", directory / "followup"
        receive, asked = read_log(hq / "receive.jsonl"), read_log(hq / "questions.jsonl")
        ex = Extractor(pack, world)
        bundles = [dict(r["body"]) for r in _of(receive, "cells_bundle")]
        facts = {"cells_codes": ex.cells("cells_codes", bundles, CHANNELS[:1]),
                 "cells_text": ex.cells("cells_text", bundles, CHANNELS[1:]),
                 "usage_summary": ex.usage([r["body"] for r in _of(receive, "usage_summary")]),
                 "verdicts_passive": ex.verdicts("verdicts_passive", _of(receive, "verdict"),
                                                 _of(asked, "question")),
                 "packets": ex.packets(_of(receive, "packet"))}
        ledger = FollowupLedger.open(base / LEDGER_FILE, pack=pack, enterprise=G0_ENTERPRISE, clock=ctx.clock)
        try:
            entries = [e._asdict() for e in ledger.entries()]
        finally:
            ledger.close()
        outbox = [strict_load(line) for line in (base / "outbox.jsonl").read_bytes().split(b"\n") if line]
        central = read_ledger(base / "central.ledger.jsonl") if (base / "central.ledger.jsonl").exists() else []
        reader = HqReader(directory / "hqdb" / "collective.sqlite3")
        facts["followup"] = ex.followup(entries, outbox, central, _hq_context(reader, ex))
        store = CollectiveStore(directory / "hqdb" / "collective.sqlite3", pack,
                                org_for_sites(world.sites, G0_ENTERPRISE), clock=ctx.clock)
        try:
            result = detect(store, as_of=ctx.as_of, run_channel="X", tie_salt=G0_TIE_SALT)
            if any(store.candidate(result["run_id"], c["key"]) != strict_load(canonical_bytes(c))
                   for c in result["candidates"]):
                raise HarnessError("the recomputed detection result differs from the stored run") from None
        finally:
            store.close()
        conclusions = []
        for cid in sorted(set(ctx.conclusion_ids)):
            row = reader.conclusions(cid)[-1]
            conclusions.append((strict_load(row.body), strict_load(reader.question(row.question_id).body)))
        facts["hq_results"] = ex.hq_results(result, conclusions)
        docs = runfiles.read_run_files(directory / "run", ("scorecard.json", "trace.json", "ledger.jsonl",
                                                           "approvals.jsonl"))
        facts["run_files"] = ex.run_files(docs["trace.json"], docs["approvals.jsonl"], docs["scorecard.json"],
                                          docs["ledger.jsonl"])
        probe = None
        facts["verdicts_active"] = []
        if a6 is not None:
            windows = a6_windows(world, pack.egress.min_window_weeks)
            probe = a6_probe(pack, directory, ctx.clock, sites=world.sites, master=world.master, targets=a6,
                             windows=windows, world=world, start_day=date.fromisoformat(ctx.as_of) + timedelta(days=1),
                             max_days=max_a6_days)
            after_receive, after_asked = read_log(hq / "receive.jsonl")[len(receive):], \
                read_log(hq / "questions.jsonl")[len(asked):]
            facts["verdicts_active"] = ex.verdicts("verdicts_active", _of(after_receive, "verdict"),
                                                   _of(after_asked, "question"))
        last = closed_through(ctx.as_of, pack.egress.close_lag_days)
        rows = {}
        for site in world.sites:
            records = RecordStore(directory / "edge" / f"site-{site}.sqlite3", site_id=site, pack_id=pack.id,
                                  config_hash=pack.config_hash)
            try:
                rows[site] = records.emission_inputs(None, last)
            finally:
                records.close()
        reference = ex.reference(r_mf_cells(pack, world.members, master_data=world.master, last_week=last))
        verdicts = dict.fromkeys(VERDICTS, 0)
        for r in _of(receive, "verdict"):
            verdicts[r["body"]["verdict"]] += 1
        counts = {"cells": ctx.totals["cells"], "cells_n_ge_k": ctx.totals["cells_n_ge_k"],
                  "candidates": ctx.pushdown_totals["candidates"], "questions": ctx.pushdown_totals["questions"],
                  "routes": ctx.pushdown_totals["routes"], "verdicts": verdicts,
                  "conclusions": dict(ctx.pushdown_totals["statuses"]), "packets": len(_of(receive, "packet")),
                  "drafts": ctx.followup_totals["drafts"], "outbox_lines": ctx.followup_totals["outbox_lines"]}
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    return WorldRun(facts=facts, reference=reference, rows=rows, bundles=bundles, a6=probe, counts=counts)


# --------------------------------------------------------------------------------------------------- cell settings

@dataclass(frozen=True)
class CellSetting:
    """How cells are built for a variant or a transform: the suppression ``k``, the period in weeks and whether
    cells below k are dropped (then a missing cell means at most ``k - 1``)."""

    name: str
    k: int
    period: int = 1
    drop: bool = False


def rebuild_bodies(rows: Mapping[str, Sequence[InputRow]], pack: FrozenPack, world: WorldData, setting: CellSetting,
                   envelopes: Sequence[Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Bundle-shaped bodies built from rows (``build_cells`` at the setting's k; a 4-week period counts each row in
    the first ISO week of its period), on the envelopes of the real bundles when given."""
    by_site = {e["site"]: e for e in envelopes} if envelopes is not None else {}
    out = []
    for site in world.sites:
        site_rows = list(rows.get(site, ()))
        if setting.period > 1:
            site_rows = [r._replace(count_week=world.iso(
                week_index(world.start, r.count_week) // setting.period * setting.period)) for r in site_rows]
        cells, _ = build_cells(site_rows, pack, master=world.master[site], k=setting.k)
        if setting.drop:
            cells = [c for c in cells if c["n"] != SUPPRESSED]
        envelope = by_site.get(site, {"site": site, "after": None, "closed_through": world.iso(world.last_week)})
        out.append({"site": site, "after": envelope["after"], "closed_through": envelope["closed_through"],
                    "cells": cells})
    return out


def drop_lt_k(bundles: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{**b, "cells": [c for c in b["cells"] if c["n"] != SUPPRESSED]} for b in bundles]


def cell_facts(pack: FrozenPack, world: WorldData, bodies: Sequence[Mapping[str, Any]],
               setting: CellSetting) -> dict[str, list[xa.Fact]]:
    ex = Extractor(pack, world)
    missing = setting.k - 1 if setting.drop else 0
    return {name: ex.cells(name, bodies, chans, period=setting.period, missing_hi=missing, k=setting.k)
            for name, chans in (("cells_codes", CHANNELS[:1]), ("cells_text", CHANNELS[1:]))}


# --------------------------------------------------------------------------------------------------- targets

@dataclass
class Targets:
    a1: list[tuple[xa.A1Known, bool, tuple[Any, ...]]] = field(default_factory=list)
    a1_dropped: int = 0
    a2: list[tuple[xa.A2Known, frozenset[str], tuple[Any, ...]]] = field(default_factory=list)
    a3: list[tuple[xa.A3Known, int, tuple[Any, ...]]] = field(default_factory=list)
    a4: list[tuple[xa.A4Known, bool, tuple[Any, ...]]] = field(default_factory=list)
    a5: list[tuple[xa.A5Known, str, tuple[Any, ...]]] = field(default_factory=list)
    a6: list[tuple[str | None, bool, bool, tuple[Any, ...]]] = field(default_factory=list)


def a1_targets(world: WorldData, keys: Mapping[str, tuple[tuple[str, str, str, str], ...]]) -> tuple[list[Any], int]:
    """Originals of each (site, week) stratum, balanced: every record of the smaller class and a seeded sample of
    the larger; a single-class stratum is dropped and counted. Forwarded copies are never targets."""
    strata: dict[tuple[str, int], tuple[list[str], list[str]]] = {}
    for r in world.originals():
        members, others = strata.setdefault((r["site"], world.week_of[r["record_ref"]]), ([], []))
        (members if world.member[r["record_ref"]] else others).append(r["record_ref"])
    out, dropped = [], 0
    for site, week in sorted(strata):
        members, others = (sorted(x) for x in strata[(site, week)])
        if not members or not others:
            dropped += 1
            continue
        small, large = (members, others) if len(members) <= len(others) else (others, members)
        chosen = small + random.Random(f"x5:{world.seed}:a1:{site}:{week}").sample(large, len(small))
        for ref in sorted(chosen):
            out.append((xa.A1Known(site, week, ref, keys[ref], xa.a1_coin(world.seed, ref)), world.member[ref],
                        (world.seed, site, week)))
    return out, dropped


def structured_entities(pack: FrozenPack, record: Mapping[str, Any], canonicaliser: Canonicaliser) -> set[tuple[str,
                                                                                                             str]]:
    out = set()
    for t in sorted(record["entities"]):
        for v in record["entities"][t]:
            m = canonicaliser.resolve_exact(t, v)
            if m is not None:
                out.add((t, m.entity_id))
    return out


def affirmed(world: WorldData, ref: str) -> frozenset[str]:
    return frozenset(g["predicate"] for g in world.gold[ref] if g["predicate"] is not None and not g["negated"])


def member_originals(world: WorldData) -> list[dict[str, Any]]:
    return [r for r in world.originals() if world.member[r["record_ref"]]]


def a2_targets(pack: FrozenPack, world: WorldData) -> list[tuple[xa.A2Known, frozenset[str], tuple[Any, ...]]]:
    primary_type = removed_type(pack)
    canon = {site: Canonicaliser(pack, known=world.master[site]) for site in world.sites}
    out = []
    for r in member_originals(world):
        truth = affirmed(world, r["record_ref"])
        entities = tuple(sorted(structured_entities(pack, r, canon[r["site"]])))
        if not truth or not entities:
            continue
        primary = next((e for e in entities if e[0] == primary_type), entities[0])
        week = world.week_of[r["record_ref"]]
        out.append((xa.A2Known(r["site"], week, entities, primary), truth, (world.seed, r["site"], week)))
    return out


def cell_targets(cells: Mapping[tuple[str, str, str, str, int, str], Mapping[str, str | None]], k: int,
                 seed: int) -> tuple[list[Any], list[Any]]:
    """A3: every true cell with 1 <= n < k; A4: every unordered pair of its records when 2 <= n < k, the truth
    being a shared reporter (an unknown reporter is one shared reporter)."""
    a3, a4 = [], []
    for key in sorted(cells):
        site, t, e, p, week, ch = key
        records = cells[key]
        n = len(records)
        if not 1 <= n < k:
            continue
        a3.append((xa.A3Known(site, week, t, e, p, ch, True), n, (seed, site, week)))
        refs = sorted(records)
        for i, a in enumerate(refs):
            for b in refs[i + 1:]:
                a4.append((xa.A4Known(site, week, t, e, p, ch), records[a] == records[b], (seed, site, week)))
    return a3, a4


def surname(value: str) -> str:
    return value.split()[-1].upper()


def a5_targets(pack: FrozenPack, world: WorldData) -> list[tuple[xa.A5Known, str, tuple[Any, ...]]]:
    out = []
    for r in member_originals(world):
        week = world.week_of[r["record_ref"]]
        for f in a5_fields(pack):
            value = r["persons"].get(f)
            if isinstance(value, str) and value.strip():
                out.append((xa.A5Known(r["site"], week, f), surname(value), (world.seed, r["site"], week)))
    return out


def named_weeks(pack: FrozenPack, world: WorldData) -> dict[tuple[str, str, str], list[int]]:
    """Per (site, type, id), the weeks of the site's own member records that name the entity in their gold claims
    (any predicate, negated or entity-only included) or their canonical structured entities."""
    canon = {site: Canonicaliser(pack, known=world.master[site]) for site in world.sites}
    out: dict[tuple[str, str, str], list[int]] = {}
    for r in member_originals(world):
        site = r["site"]
        named = {(g["entity_type"], g["entity_id"]) for g in world.gold[r["record_ref"]]}
        named |= structured_entities(pack, r, canon[site])
        for t, e in sorted(named):
            out.setdefault((site, t, e), []).append(world.week_of[r["record_ref"]])
    return out


def present(weeks: Sequence[int], first: int, last: int) -> bool:
    return any(first <= w <= last for w in weeks)


# --------------------------------------------------------------------------------------------------- shadow

@dataclass
class Shadow:
    thresholds: dict[str, float]
    a1_targets: int
    a2: xa.A2Prior
    a3: dict[int, int]
    a3_cells: int
    a4_pairs: int
    a4_same: int
    a5: dict[str, dict[str, int]]
    a5_targets: int
    a6_share: dict[tuple[str, str, str], float]
    a6_windows: int
    predicate_of: dict[tuple[str, str], str]

    @property
    def a4_prior_same(self) -> bool:
        return 2 * self.a4_same > self.a4_pairs

    def a3_mode(self, k: int) -> int:
        return xa.mode_in(self.a3, 1, k - 1)


def surnames(pack: FrozenPack, f: str) -> list[str]:
    return sorted({n.upper() for n in pack.generator["persons"][f]["last"]})


def shadow_stats(variant: Variant, worlds: Sequence[WorldData], settings: Sequence[CellSetting],
                 k_target: int) -> Shadow:
    """Priors and A1 thresholds from shadow worlds only (no pipeline; their lexical rows are what a site stores)."""
    pack = variant.pack
    scores: dict[str, list[float | None]] = {s.name: [] for s in settings}
    members: list[bool] = []
    by_entity_site: dict[tuple[str, str, str], dict[str, int]] = {}
    by_entity: dict[tuple[str, str], dict[str, int]] = {}
    by_named: dict[tuple[str, str], dict[str, int]] = {}      # every structured entity of the record (A6)
    by_type: dict[str, dict[str, int]] = {}
    overall: dict[str, int] = {}
    a3: dict[int, int] = {}
    a3_cells = a4_pairs = a4_same = a5_count = a6_count = 0
    a5 = {f: dict.fromkeys(surnames(pack, f), 0) for f in a5_fields(pack)}
    presence_hits: dict[tuple[str, str, str], int] = {}
    m = pack.egress.min_window_weeks
    for world in worlds:
        rows = lexical_rows(pack, world.members, world.master)
        targets, _ = a1_targets(world, record_keys(variant, world))
        for setting in settings:
            index = xa.FactIndex(f for facts in cell_facts(pack, world, rebuild_bodies(rows, pack, world, setting),
                                                           setting).values() for f in facts)
            scores[setting.name] += [xa.a1_score(known, index) for known, _, _ in targets]
        members += [member for _, member, _ in targets]
        for known, truth, _ in a2_targets(pack, world):
            t, e = known.primary
            for p in sorted(truth):
                for table, key in ((by_entity_site, (t, e, known.site)), (by_entity, (t, e)), (by_type, t)):
                    table.setdefault(key, {})[p] = table.setdefault(key, {}).get(p, 0) + 1
                overall[p] = overall.get(p, 0) + 1
                for named in known.entities:
                    by_named.setdefault(named, {})[p] = by_named.setdefault(named, {}).get(p, 0) + 1
        cells = true_cells(variant, rows, world.master, world.start)
        cell_a3, cell_a4 = cell_targets(cells, k_target, world.seed)
        for _, n, _ in cell_a3:
            a3[n] = a3.get(n, 0) + 1
        a3_cells += len(cell_a3)
        a4_pairs += len(cell_a4)
        a4_same += sum(1 for _, same, _ in cell_a4 if same)
        for known, name, _ in a5_targets(pack, world):
            a5[known.field][name] = a5[known.field].get(name, 0) + 1
            a5_count += 1
        weeks = named_weeks(pack, world)
        windows = a6_windows(world, m)
        a6_count += len(windows)
        for key in sorted(weeks):
            presence_hits[key] = presence_hits.get(key, 0) + sum(1 for a, b in windows if present(weeks[key], a, b))
    predicates = sorted(pack.predicates)
    predicate_of = {}
    for t in sorted(pack.entity_types):
        et = pack.entity_types[t]
        ids = set(et.ids or ()) | {e for (tt, e) in by_named if tt == t}
        for w in worlds:
            for site in w.sites:
                ids |= set(w.master[site].get(t, ()))
        for e in sorted(ids):
            counts = by_named.get((t, e), {})
            predicate_of[(t, e)] = min(predicates, key=lambda p: (-counts.get(p, 0), p))
    return Shadow(
        thresholds={name: xa.calibrate(s, members) for name, s in scores.items()}, a1_targets=len(members),
        a2=xa.A2Prior(by_entity_site, by_entity, by_type, overall), a3=a3, a3_cells=a3_cells, a4_pairs=a4_pairs,
        a4_same=a4_same, a5=a5, a5_targets=a5_count,
        a6_share={key: hits / a6_count for key, hits in presence_hits.items()} if a6_count else {},
        a6_windows=a6_count, predicate_of=predicate_of)


# --------------------------------------------------------------------------------------------------- evaluation

Outcomes = dict[tuple[str, str], list[xa.Outcome]]


def evaluate(acc: Outcomes, pairs: Sequence[tuple[str, str]], indexes: Mapping[str, xa.FactIndex], targets: Targets,
             shadow: Shadow, *, threshold: float, k_target: int, pack: FrozenPack) -> None:
    """Every (attack, artifact type) pair's outcomes on one world, appended to ``acc``. Attack functions see only
    what the attacker knows; :func:`.x5_attacks.outcome` meets prediction and truth."""
    predicates = sorted(pack.predicates)
    names = {f: surnames(pack, f) for f in a5_fields(pack)}
    a2_base = [xa.a2_prior_prediction(known, shadow.a2, predicates) for known, _, _ in targets.a2]
    a3_base = shadow.a3_mode(k_target) if k_target > 1 else 1
    a5_base = {f: xa.prior_mode(shadow.a5[f]) for f in names}
    scores: dict[str, list[float | None]] = {}
    for attack, typ in pairs:
        index = indexes[typ]
        out = acc.setdefault((attack, typ), [])
        if attack in ("A1_calibrated", "A1_fixed"):
            if typ not in scores:
                scores[typ] = [xa.a1_score(known, index) for known, _, _ in targets.a1]
            t = threshold if attack == "A1_calibrated" else None
            for (known, truth, cluster), score in zip(targets.a1, scores[typ]):
                predicted, covered = xa.a1_predict(score, known.coin, t)
                out.append(xa.outcome(cluster, predicted, None, truth, covered))
        elif attack == "A2":
            for (known, truth, cluster), base in zip(targets.a2, a2_base):
                predicted, covered = xa.a2_predict(known, index, shadow.a2, predicates)
                out.append(xa.outcome(cluster, predicted, base, truth, covered))
        elif attack == "A3":
            for known, truth, cluster in targets.a3:
                predicted, covered = xa.a3_predict(known, index, k_target, shadow.a3)
                out.append(xa.outcome(cluster, predicted, a3_base, truth, covered))
        elif attack == "A4":
            for known, truth, cluster in targets.a4:
                predicted, covered = xa.a4_predict(known, index, shadow.a4_prior_same)
                out.append(xa.outcome(cluster, predicted, shadow.a4_prior_same, truth, covered))
        elif attack == "A5":
            for known, truth, cluster in targets.a5:
                predicted, covered = xa.a5_predict(known, index, names[known.field], shadow.a5[known.field])
                out.append(xa.outcome(cluster, predicted, a5_base[known.field], truth, covered))
        else:
            for verdict, truth, baseline, cluster in targets.a6:
                predicted, covered = xa.a6_predict(verdict)
                out.append(xa.outcome(cluster, predicted, baseline, truth, covered))


def build_indexes(facts: Mapping[str, Sequence[xa.Fact]], reference: Sequence[xa.Fact],
                  types: Iterable[str]) -> dict[str, xa.FactIndex]:
    """One index per artifact type needed: the nine HQ types, ``all`` (their union), the allowed-fields reference
    and ``allowed_plus_all``."""
    out = {}
    for typ in sorted(set(types)):
        if typ in xa.BASE_TYPES:
            out[typ] = xa.FactIndex(facts.get(typ, ()))
        elif typ == xa.ALL_TYPE:
            out[typ] = xa.FactIndex(f for t in xa.BASE_TYPES for f in facts.get(t, ()))
        elif typ == "allowed_fields_reference":
            out[typ] = xa.FactIndex(reference)
        else:
            out[typ] = xa.FactIndex([*(f for t in xa.BASE_TYPES for f in facts.get(t, ())), *reference])
    return out


def inject_a5(bundles: Sequence[Mapping[str, Any]], targets: Sequence[tuple[xa.A5Known, str, Any]],
              pack: FrozenPack, world: WorldData) -> list[dict[str, Any]]:
    """The positive control's bundles: per A5 target, a ``'<k'`` text cell whose entity id is the upper-cased
    surname, on the pack's first egress type and first predicate, in the record's week; sorted, deduplicated. The
    Boundary would refuse it (the id is not canonical)."""
    entity_type = sorted(pack.egress.egress_entity_types)[0]
    predicate = sorted(pack.predicates)[0]
    added: dict[str, list[dict[str, Any]]] = {}
    for known, name, _ in targets:
        added.setdefault(known.site, []).append({
            "channel": CHANNELS[1], "entity_id": name, "entity_type": entity_type, "iso_week": world.iso(known.week),
            "n": SUPPRESSED, "n_reporters": SUPPRESSED, "n_roots": SUPPRESSED, "predicate": predicate})
    out = []
    for body in bundles:
        cells = {canonical_dumps(c): c for c in [*body["cells"], *added.get(body["site"], ())]}
        ordered = sorted(cells.values(), key=lambda c: (c["entity_type"], c["entity_id"], c["predicate"],
                                                         c["iso_week"], c["channel"]))
        out.append({**body, "cells": ordered})
    return out


def applicable_pairs(variant_id: str, kind: str, k: int, attacks: Sequence[str],
                     types: Sequence[str]) -> list[tuple[str, str]]:
    out = [(a, t) for a in attacks for t in types if xa.applicability(variant_id, kind, k, a, t)[0] == "applicable"]
    if any(t == "allowed_plus_all" for _, t in out):
        out += [(a, "allowed_fields_reference") for a, t in out
                if t == "allowed_plus_all" and (a, "allowed_fields_reference") not in out]
    return out


def entries(variant_id: str, kind: str, k: int, acc: Outcomes, *, plan: "Plan", pack_id: str, run: bool,
            reason: str | None, prefix: str | None = None,
            attacks: Sequence[str] | None = None, types: Sequence[str] | None = None) -> dict[str, Any]:
    """The results entries of one variant (or simulated transform, ``prefix`` naming it in the bootstrap seed)."""
    name = prefix or variant_id
    out: dict[str, Any] = {}
    for attack in (plan.attacks if attacks is None else attacks):
        out[attack] = {}
        for typ in (plan.types if types is None else types):
            status, why = xa.applicability(variant_id, kind, k, attack, typ)
            if status != "applicable":
                out[attack][typ] = xa.empty_entry(status, why)
                continue
            if not run:
                out[attack][typ] = xa.empty_entry("not_run", reason)
                continue
            primary = prefix is None and (pack_id, attack, variant_id, typ) in plan.primary
            seed = f"{plan.bootstrap_seed}:{pack_id}:{name}:{attack}:{typ}"
            entry = xa.summarise(attack, acc.get((attack, typ), []), B=plan.primary_b if primary else
                                 plan.exploratory_b, seed=seed, primary=primary, family_size=max(1, plan.family_size))
            if typ == "allowed_plus_all":
                entry["incremental"] = xa.incremental(attack, acc.get((attack, typ), []),
                                                      acc.get((attack, "allowed_fields_reference"), []),
                                                      B=plan.exploratory_b, seed=seed + ":incremental")
            out[attack][typ] = entry
    return out


# --------------------------------------------------------------------------------------------------- the plan

@dataclass
class Plan:
    """What a prereg fixes, read back for a run."""

    n: int
    volume_factor: int
    seeds: list[int]
    shadow_seeds: list[int]
    volume_seeds: list[int]
    variants: list[str]
    attacks: list[str]
    types: list[str]
    simulated: list[str]
    a6_per_site: int
    primary_b: int
    exploratory_b: int
    bootstrap_seed: str
    primary: set[tuple[str, str, str, str]]
    family_size: int
    max_pipelines: int
    digests: dict[tuple[str, str, int], str]
    excluded: set[tuple[str, str, int]]

    @classmethod
    def of(cls, prereg: Mapping[str, Any]) -> "Plan":
        w = prereg["world"]
        return cls(n=w["n"], volume_factor=w["volume_factor"], seeds=list(w["seeds"]),
                   shadow_seeds=list(w["shadow_seeds"]), volume_seeds=list(w["volume_seeds"]),
                   variants=list(prereg["variants"]), attacks=list(prereg["attacks"]),
                   types=list(prereg["artifact_types"]), simulated=list(prereg["simulated"]),
                   a6_per_site=prereg["a6"]["targets_per_site"], primary_b=prereg["bootstrap"]["primary_B"],
                   exploratory_b=prereg["bootstrap"]["exploratory_B"], bootstrap_seed=prereg["bootstrap"]["seed"],
                   primary={(p["pack"], p["attack"], p["variant"], p["artifact_type"]) for p in prereg["primary"]},
                   family_size=prereg["rules"]["family_size"], max_pipelines=prereg["cap"]["max_pipelines"],
                   digests={(d["pack"], d["kind"], d["seed"]): d["world_digest"] for d in w["digests"]},
                   excluded={(e["pack"], e["kind"], e["seed"]) for e in w["volume_excluded"]})

    def volume_shadow(self, pack_id: str) -> list[int]:
        return [s for s in self.shadow_seeds if (pack_id, "volume_shadow", s) not in self.excluded]


def pipeline_order(variants: Sequence[str]) -> list[str]:
    """The pipeline variants in run order: default, the k variants (ascending k), rmd_flipped, minus_type, volume."""
    ks = sorted((v for v in variants if K_VARIANT_RE.fullmatch(v)), key=lambda v: int(v[1:]))
    return [v for v in ("default", *ks, "rmd_flipped", "minus_type", "volume") if v in variants]


def pipeline_plan(pack_ids: Sequence[str], variants: Sequence[str], *, seeds: Sequence[int],
                  volume_seeds: Sequence[int], volume_shadow: Mapping[str, Sequence[int]],
                  max_pipelines: int) -> tuple[dict[tuple[str, str], tuple[bool, str | None]], int]:
    """Which pipeline variants run, counted in order against the cap: a variant that cannot run all its seeds runs
    none. Returns ``{(pack, variant): (runs, reason)}`` and the pipelines needed without a cap."""
    used = needed = 0
    out: dict[tuple[str, str], tuple[bool, str | None]] = {}
    for pack_id in pack_ids:
        for v in pipeline_order(variants):
            count = len(volume_seeds) if v == "volume" else len(seeds)
            if v == "volume" and not volume_seeds:
                out[(pack_id, v)] = (False, NO_VOLUME_REASON)
                continue
            if v == "volume" and not volume_shadow.get(pack_id):
                out[(pack_id, v)] = (False, NO_VOLUME_SHADOW_REASON)
                continue
            needed += count
            if used + count > max_pipelines:
                out[(pack_id, v)] = (False, CAP_REASON)
                continue
            used += count
            out[(pack_id, v)] = (True, None)
    return out, needed


# --------------------------------------------------------------------------------------------------- one pack

@dataclass
class ScanInputs:
    """What the self-scan needs of every world generated for a pack: narratives per world, and the person,
    surname and reporter values. A world is added once however many variants rebuild it."""

    narratives: list[list[str]] = field(default_factory=list)
    values: set[str] = field(default_factory=set)
    digests: set[str] = field(default_factory=set)

    def add(self, world: WorldData, pack: FrozenPack) -> None:
        if world.digest in self.digests:
            return
        self.digests.add(world.digest)
        self.narratives.append([r["narrative"] for r in world.records])
        names = set(a5_fields(pack))
        for r in world.records:
            for f, value in r["persons"].items():
                if isinstance(value, str) and value.strip():
                    self.values.add(value)
                    if f in names:
                        self.values.update(t for t in value.split() if sum(ch.isalpha() for ch in t) >= 4)
            if isinstance(r["reporter"], str) and r["reporter"]:
                self.values.add(r["reporter"])


@dataclass
class PackResult:
    variants: dict[str, Any]
    results: dict[str, Any]
    simulated: dict[str, Any]
    scan: ScanInputs
    timings: dict[str, float]


def _world_block(world: WorldData, run: WorldRun | None, targets: Targets, n_facts: Mapping[str, int]) -> dict[str,
                                                                                                            Any]:
    a6 = None
    if run is not None and run.a6 is not None:
        verdicts = dict.fromkeys(VERDICTS, 0)
        for *_, verdict in run.a6.windows:
            if verdict is not None:
                verdicts[verdict] += 1
        a6 = {"targets": len({w[:4] for w in run.a6.windows}), "windows": len(run.a6.windows),
              "questions": run.a6.questions, "days_needed": run.a6.days_needed,
              "budget_responses": run.a6.budget_responses, "verdicts": verdicts}
    members = sum(1 for r in world.originals() if world.member[r["record_ref"]])
    return {"seed": world.seed, "world_digest": runfiles.digest(world.digest), "records": len(world.records),
            "originals": len(world.originals()), "members": members, "non_members": len(world.originals()) - members,
            "weeks": world.last_week + 1, "pipeline": run.counts if run is not None else None, "a6": a6,
            "facts": dict(n_facts),
            "targets": {"A1": len(targets.a1), "A2": len(targets.a2), "A3": len(targets.a3), "A4": len(targets.a4),
                        "A5": len(targets.a5), "A6": len(targets.a6)},
            "a1_single_class_strata": targets.a1_dropped}


def _targets(variant: Variant, world: WorldData, rows: Mapping[str, Sequence[InputRow]], k_target: int,
             keys: Mapping[str, tuple[tuple[str, str, str, str], ...]]) -> Targets:
    t = Targets()
    t.a1, t.a1_dropped = a1_targets(world, keys)
    t.a2 = a2_targets(variant.pack, world)
    t.a3, t.a4 = cell_targets(true_cells(variant, rows, world.master, world.start), k_target, world.seed)
    t.a5 = a5_targets(variant.pack, world)
    return t


def run_pack(plan: Plan, base: FrozenPack, copies: Mapping[str, FrozenPack],
             runnable: Mapping[tuple[str, str], tuple[bool, str | None]], work: Path) -> PackResult:
    """Every variant of one pack: shadow priors, then per seed the pipeline, the facts, the targets and the
    outcomes, summarised per (variant, attack, artifact type); the derived variants and the simulated transforms
    ride on the default worlds."""
    pid = base.id
    scan_inputs = ScanInputs()
    timings: dict[str, float] = {}
    shadow_worlds = []
    for s in plan.shadow_seeds:
        shadow_worlds.append(checked_world(plan, base, base, "shadow", s, plan.n * 2))
        scan_inputs.add(shadow_worlds[-1], base)
    variants: dict[str, Any] = {}
    results: dict[str, Any] = {}
    simulated: dict[str, Any] = {}
    derived_acc: dict[str, Outcomes] = {v: {} for v in (*DERIVED_VARIANTS, *SIMULATED)}
    derived_shadow: Shadow | None = None
    cells_counts = {name: [0, 0] for name in SIMULATED}
    for vid in pipeline_order(plan.variants):
        started = time.perf_counter()
        runs, reason = runnable[(pid, vid)]
        pack = copies[vid]
        variant = Variant(vid, "pipeline", pack, pack.egress.k, plan.volume_factor if vid == "volume" else 1)
        record = variant_block(base, variant, label=None)
        if not runs:
            variants[vid] = {**record, "status": "not_run", "reason": reason}
            results[vid] = entries(vid, "pipeline", variant.k, {}, plan=plan, pack_id=pid, run=False, reason=reason)
            if vid == "default":
                raise UsageError(f"pack {pid}: the default variant cannot run ({reason})") from None
            continue
        volume = vid == "volume"
        n_records = plan.n * 2 * (plan.volume_factor if volume else 1)
        if volume:
            worlds = []
            for s in plan.volume_shadow(pid):
                worlds.append(checked_world(plan, base, pack, "volume_shadow", s, n_records))
                scan_inputs.add(worlds[-1], base)
        else:
            worlds = shadow_worlds
        settings = [CellSetting("variant", variant.k)]
        if vid == "default":
            if "k1_reference" in plan.variants:
                settings.append(CellSetting("k1_reference", 1))
            settings += [CellSetting(name, base.egress.k, period=FOUR_WEEKS if name == "four_week" else 1,
                                     drop=name == "drop_lt_k") for name in plan.simulated]
        shadow = shadow_stats(variant, worlds, settings, variant.k)
        if vid == "default":
            derived_shadow = shadow
        pairs = applicable_pairs(vid, "pipeline", variant.k, plan.attacks, plan.types)
        acc: Outcomes = {}
        world_blocks = []
        designed = {"cells_lt_k": 0, "cells_exact": 0, "exact_correct": 0, "members_with_keys": 0, "all_present": 0}
        for seed in (plan.volume_seeds if volume else plan.seeds):
            world = checked_world(plan, base, pack if volume else base, "volume" if volume else "base", seed,
                                  n_records)
            scan_inputs.add(world, base)
            keys = record_keys(variant, world)
            a6 = None
            if "A6" in plan.attacks and not volume:
                a6 = a6_targets(pack, world, plan.a6_per_site, shadow.predicate_of)
            run = run_world(variant, world, work / pid / vid / str(seed), a6=a6,
                            max_a6_days=len(a6_windows(world, pack.egress.min_window_weeks)) + 10)
            targets = _targets(variant, world, run.rows, variant.k, keys)
            if run.a6 is not None:
                named = named_weeks(pack, world)
                for site, t, e, _, first, last, verdict in run.a6.windows:
                    targets.a6.append((verdict, present(named.get((site, t, e), ()), first, last),
                                       shadow.a6_share.get((site, t, e), 0.0) >= 0.5, (seed, site, first)))
            needed = {typ for _, typ in pairs}
            indexes = build_indexes(run.facts, run.reference, needed)
            evaluate(acc, pairs, indexes, targets, shadow, threshold=shadow.thresholds["variant"],
                     k_target=variant.k, pack=pack)
            _designed(designed, variant, world, run, keys)
            n_facts = {typ: len(run.facts[typ]) for typ in xa.BASE_TYPES}
            n_facts["allowed_fields_reference"] = len(run.reference)
            world_blocks.append(_world_block(world, run, targets, n_facts))
            if vid == "default":
                _derived(plan, base, world, run, targets, derived_shadow, derived_acc, cells_counts)
        variants[vid] = {**record, "status": "run", "reason": None, "worlds": world_blocks,
                         "shadow": _shadow_block(shadow, worlds, settings, variant.k),
                         "designed_disclosures": _designed_block(designed)}
        results[vid] = entries(vid, "pipeline", variant.k, acc, plan=plan, pack_id=pid, run=True, reason=None)
        timings[vid] = time.perf_counter() - started
    for vid in DERIVED_VARIANTS:
        if vid not in plan.variants:
            continue
        variant = Variant(vid, "derived", base, 1 if vid == "k1_reference" else base.egress.k, 1)
        label = K1_LABEL if vid == "k1_reference" else A5_INJECTED_LABEL
        variants[vid] = {**variant_block(base, variant, label=label), "status": "run", "reason": None}
        results[vid] = entries(vid, "derived", variant.k, derived_acc[vid], plan=plan, pack_id=pid, run=True,
                               reason=None)
    for name in plan.simulated:
        before, after = cells_counts[name]
        simulated[name] = {"label": SIMULATED_LABEL, "status": "run", "reason": None, "cells_before": before,
                           "cells_after": after, "removed_cell_share": 1 - after / before if before else None,
                           "entries": entries("default", "pipeline", base.egress.k, derived_acc[name], plan=plan,
                                              pack_id=pid, run=True, reason=None, prefix=name,
                                              attacks=[a for a in plan.attacks if a != "A6"],
                                              types=[t for t in plan.types if t in (*xa.CELL_TYPES, xa.ALL_TYPE)])}
    return PackResult(variants=variants, results=results, simulated=simulated, scan=scan_inputs, timings=timings)


def checked_world(plan: Plan, base: FrozenPack, generator_pack: FrozenPack, kind: str, seed: int,
                  n_records: int) -> WorldData:
    world = build_world(generator_pack, seed, n_records)
    if plan.digests.get((base.id, kind, seed)) != world.digest:
        raise HarnessError(f"pack {base.id}: the {kind} world of seed {seed} no longer matches its prereg digest")
    return world


def variant_block(base: FrozenPack, variant: Variant, *, label: str | None) -> dict[str, Any]:
    pack = variant.pack
    return {"kind": variant.kind, "label": label, "k": variant.k, "buckets": list(pack.egress.verdict_count_buckets),
            "require_master_data": pack.egress.require_master_data,
            "egress_entity_types": list(pack.egress.egress_entity_types), "volume_factor": variant.volume_factor,
            "hashes": ({name: runfiles.digest(h) for name, h in pack.hashes().items()}
                       if variant.kind == "pipeline" else None),
            "worlds": [], "shadow": None, "designed_disclosures": None}


def _designed(acc: dict[str, int], variant: Variant, world: WorldData, run: WorldRun,
              keys: Mapping[str, tuple[tuple[str, str, str, str], ...]]) -> None:
    """The designed disclosures (STRATEGY 6.4): '<k' cells reveal presence, exact cells reveal counts."""
    truth = true_cells(variant, run.rows, world.master, world.start)
    present_keys = set()
    for body in run.bundles:
        for c in body["cells"]:
            w = week_index(world.start, c["iso_week"])
            present_keys.add((body["site"], c["entity_type"], c["entity_id"], c["predicate"], w, c["channel"]))
            if c["n"] == SUPPRESSED:
                acc["cells_lt_k"] += 1
            else:
                acc["cells_exact"] += 1
                acc["exact_correct"] += len(truth.get((body["site"], c["entity_type"], c["entity_id"], c["predicate"],
                                                       w, c["channel"]), {})) == c["n"]
    for r in member_originals(world):
        own = keys[r["record_ref"]]
        if own:
            acc["members_with_keys"] += 1
            week = world.week_of[r["record_ref"]]
            acc["all_present"] += all((r["site"], t, e, p, week, ch) in present_keys for t, e, p, ch in own)


def _designed_block(acc: Mapping[str, int]) -> dict[str, Any]:
    return {"label": DESIGNED_LABEL, "cells_lt_k": acc["cells_lt_k"], "members_with_keys": acc["members_with_keys"],
            "members_all_keys_present_share": (acc["all_present"] / acc["members_with_keys"]
                                               if acc["members_with_keys"] else None),
            "cells_exact": acc["cells_exact"],
            "exact_correct_share": acc["exact_correct"] / acc["cells_exact"] if acc["cells_exact"] else None}


def _shadow_block(shadow: Shadow, worlds: Sequence[WorldData], settings: Sequence[CellSetting],
                  k: int) -> dict[str, Any]:
    return {"worlds": [{"seed": w.seed, "world_digest": runfiles.digest(w.digest),
                        "members": len(member_originals(w))} for w in worlds],
            "a1_thresholds": {s.name: shadow.thresholds[s.name] for s in settings}, "a1_targets": shadow.a1_targets,
            "a3_cells": shadow.a3_cells, "a3_mode": shadow.a3_mode(k) if k > 1 else None,
            "a4_pairs": shadow.a4_pairs, "a4_same": shadow.a4_same, "a5_targets": shadow.a5_targets,
            "a6_windows": shadow.a6_windows}


def _derived(plan: Plan, base: FrozenPack, world: WorldData, run: WorldRun, targets: Targets, shadow: Shadow,
             acc: Mapping[str, Outcomes], cells_counts: dict[str, list[int]]) -> None:
    """On one default world: the k1 reference, the injected A5 control and the simulated transforms. Their other
    artifacts are the unmitigated run's (detection is not re-run)."""
    k = base.egress.k
    others = {typ: run.facts[typ] for typ in xa.BASE_TYPES if typ not in xa.CELL_TYPES}
    total_cells = sum(len(b["cells"]) for b in run.bundles)
    if "k1_reference" in plan.variants:
        setting = CellSetting("k1_reference", 1)
        facts = {**others, **cell_facts(base, world, rebuild_bodies(run.rows, base, world, setting, run.bundles),
                                        setting)}
        pairs = applicable_pairs("k1_reference", "derived", 1, plan.attacks, plan.types)
        evaluate(acc["k1_reference"], pairs, build_indexes(facts, [], {t for _, t in pairs}), targets, shadow,
                 threshold=shadow.thresholds["k1_reference"], k_target=k, pack=base)
    if "a5_injected" in plan.variants:
        injected = inject_a5(run.bundles, targets.a5, base, world)
        facts = {**run.facts, "cells_text": Extractor(base, world).cells("cells_text", injected, CHANNELS[1:])}
        pairs = applicable_pairs("a5_injected", "derived", k, plan.attacks, plan.types)
        evaluate(acc["a5_injected"], pairs, build_indexes(facts, [], {t for _, t in pairs}), targets, shadow,
                 threshold=shadow.thresholds["variant"], k_target=k, pack=base)
    for name in plan.simulated:
        setting = CellSetting(name, k, period=FOUR_WEEKS if name == "four_week" else 1, drop=name == "drop_lt_k")
        bodies = (drop_lt_k(run.bundles) if setting.drop
                  else rebuild_bodies(run.rows, base, world, setting, run.bundles))
        cells_counts[name][0] += total_cells
        cells_counts[name][1] += sum(len(b["cells"]) for b in bodies)
        facts = {**others, **cell_facts(base, world, bodies, setting)}
        attacks = [a for a in plan.attacks if a != "A6"]
        types = [t for t in plan.types if t in (*xa.CELL_TYPES, xa.ALL_TYPE)]
        pairs = applicable_pairs("default", "pipeline", k, attacks, types)
        evaluate(acc[name], pairs, build_indexes(facts, [], {t for _, t in pairs}), targets, shadow,
                 threshold=shadow.thresholds[name], k_target=k, pack=base)


# --------------------------------------------------------------------------------------------------- schemas

_O, _A = obj_schema, arr_schema
_STR = typed_schema("string")
_NSTR = typed_schema("string", nullable=True)
_NAT = typed_schema("integer", minimum=0)
_NNAT = typed_schema("integer", nullable=True, minimum=0)
_POS = typed_schema("integer", minimum=1)
_NUM = typed_schema("number")
_NNUM = typed_schema("number", nullable=True)
_BOOL = typed_schema("boolean")
_NBOOL = typed_schema("boolean", nullable=True)
_HEX = typed_schema("string", pattern="[0-9a-f]{64}")
_D32 = typed_schema("string", pattern="[0-9a-f]{32}")
_COMMIT = typed_schema("string", pattern="[0-9a-f]{40}|unknown")
_HASHES = ("config_hash", "vocabulary_hash", "detector_hash", "fixtures_hash")


def _enum(values: Sequence[Any], nullable: bool = False) -> dict[str, Any]:
    kinds = {type(v) for v in values if v is not None}
    name = "integer" if kinds == {int} else "string"
    return typed_schema(name, nullable=nullable, enum=[*values, *([None] if nullable else [])])


def _const(value: Any) -> dict[str, Any]:
    name = {bool: "boolean", int: "integer", float: "number", str: "string"}[type(value)]
    return typed_schema(name, const=value)


_STAMPS = _O({k: _const(v) for k, v in STAMPS.items()})
_VARIANT_RECORD = _O({"id": _STR, "kind": _enum(xa.VARIANT_KINDS), "k": _POS, "buckets": _A(_POS, 1),
                      "require_master_data": _BOOL, "egress_entity_types": _A(_STR, 1), "volume_factor": _POS,
                      "hashes": _O({h: _HEX for h in _HASHES}, nullable=True)})
_RULES = {"labels": _O({name: _const(text) for name, text in xa.LABEL_RULES.items()}),
          "at_chance_margin": _const(xa.AT_CHANCE_MARGIN), "alpha": _const(xa.ALPHA), "family_size": _NAT,
          "bonferroni_alpha": _NNUM, "a1_threshold_rule": _const(A1_THRESHOLD_RULE),
          "a2_truth_rule": _const(A2_TRUTH_RULE), "a6_predicate_rule": _const(A6_PREDICATE_RULE),
          "value_rule": _const(VALUE_RULE)}
_WORLD_ENTRY = _O({"pack": _STR, "kind": _enum(("base", "shadow", "volume", "volume_shadow")), "seed": _POS,
                   "world_digest": _HEX, "records": _POS, "weeks": _POS})
PREREG_SCHEMA = schemacheck.compile(_O({
    "kind": _const("x5_prereg"), "schema_version": _const(SCHEMA_VERSION), "run_id": _STR, "created_at": _STR,
    "stamps": _STAMPS,
    "packs": _A(_O({"ref": _STR, "id": _STR, "version": _STR, **{h: _HEX for h in _HASHES}, "k": _POS,
                    "verdict_count_buckets": _A(_POS, 1), "min_window_weeks": _POS, "removed_type": _STR,
                    "a5_fields": _A(_STR), "k_settings": _A(_POS, 1), "variants": _A(_VARIANT_RECORD, 1)}), 1),
    "code_hash": _HEX, "code_files": _A(_STR, 1), "code_commit": _COMMIT,
    "code_dirty": typed_schema("boolean", nullable=True), "allow_dirty": _BOOL,
    "world": _O({"n": _POS, "records_per_world": _const("2n"), "sites": _const(SITES), "split_rule": _const(SPLIT_RULE),
                 "seeds": _A(_POS, 1), "shadow_seeds": _A(_POS, 1), "volume_seeds": _A(_POS),
                 "volume_excluded": _A(_O({"pack": _STR, "kind": _enum(("volume", "volume_shadow")), "seed": _POS,
                                           "reason": _const("generator capacity")})),
                 "volume_factor": _POS, "seed_rule": _const(SEED_RULE),
                 "seed_source": _enum(("derived from code_commit", "given")),
                 "candidates": _A(_O({"i": _NAT, "value": _POS, "use": _enum(("seed", "shadow", "duplicate"))})),
                 "digests": _A(_WORLD_ENTRY, 1)}),
    "attacks": _A(_enum(xa.ATTACKS), 1), "artifact_types": _A(_enum(xa.ARTIFACT_TYPES), 1),
    "reference_types": _A(_enum(xa.REFERENCE_TYPES)), "variants": _A(_STR, 1), "simulated": _A(_enum(SIMULATED)),
    "applicability": _A(_O({"variant": _STR, "attack": _enum(xa.ATTACKS), "artifact_type": _enum(xa.ARTIFACT_TYPES),
                            "status": _enum(("applicable", "not_applicable", "not_run")), "reason": _NSTR})),
    "rules": _O(_RULES),
    "primary": _A(_O({"pack": _STR, "attack": _enum(xa.PRIMARY_ATTACKS), "variant": _const("default"),
                      "artifact_type": _const(xa.ALL_TYPE)})),
    "bootstrap": _O({"primary_B": _POS, "exploratory_B": _POS,
                     "seed": typed_schema("string", pattern=BOOTSTRAP_SEED_RE.pattern)}),
    "a6": _O({"targets_per_site": _POS, "window": _const("min_window_weeks"),
              "predicate_rule": _const(A6_PREDICATE_RULE), "start": _const("as_of + 1 day")}),
    "cap": _O({"max_pipelines": _POS, "needed": _NAT}),
    "not_covered_mapping": _A(_O({"item": _STR, "attacks": _A(_enum(xa.ATTACKS)),
                                  "artifact_type": _enum(xa.ARTIFACT_TYPES, nullable=True), "reason": _NSTR})),
    "notes": _A(_STR),
}))
PREREG_NOTES = [
    xa.STATEMENT,
    "Settings, code, packs, variant copies and worlds are frozen and hashed here before any attack outcome exists.",
    "The first run of this prereg is the result; a run with changed code or settings is a different experiment.",
]


def prereg_problems(doc: Any) -> list[tuple[str, str]]:
    return union_problems(PREREG_SCHEMA, doc, ("code_dirty",))


def read_prereg(path: str | Path) -> tuple[dict[str, Any], bytes]:
    try:
        data = Path(path).read_bytes()
        doc = strict_load(data)
    except (OSError, StrictJsonError):
        raise UsageError("--prereg is not a readable strict JSON file") from None
    problems = prereg_problems(doc)
    if problems:
        raise UsageError(f"the prereg fails its schema at {problems[0][0]} ({problems[0][1]})") from None
    return doc, data


def not_covered_mapping() -> list[dict[str, Any]]:
    by_text = {text: (attacks, typ, reason) for text, attacks, typ, reason in NOT_COVERED_MAPPING}
    out = []
    for item in NOT_COVERED:
        if "X5" not in item:
            continue
        if item not in by_text:
            raise HarnessError("a leakage.NOT_COVERED item naming X5 has no mapping") from None
        attacks, typ, reason = by_text[item]
        out.append({"item": item, "attacks": list(attacks), "artifact_type": typ, "reason": reason})
    return out


# --------------------------------------------------------------------------------------------------- prereg

def _selection(text: str, allowed: Sequence[str], what: str) -> list[str]:
    if text == "all":
        return list(allowed)
    chosen = split_list(text)
    unknown = [c for c in chosen if c not in allowed]
    if not chosen or unknown or len(set(chosen)) != len(chosen):
        raise UsageError(f"--{what} must be all or distinct values of: {', '.join(allowed)}") from None
    return [a for a in allowed if a in chosen]


def _seed_list(text: str, what: str) -> list[int]:
    try:
        values = [int(v) for v in split_list(text)]
    except ValueError:
        raise UsageError(f"--{what} must be comma-separated integers") from None
    if not values or any(not 1 <= v <= SEED_MODULUS for v in values) or len(set(values)) != len(values):
        raise UsageError(f"--{what} must be distinct integers in [1, {SEED_MODULUS}]") from None
    return sorted(values)


def _k_settings(text: str, packs: Sequence[FrozenPack]) -> list[int]:
    """The k values of the k variants (each pack's own k is its default variant)."""
    if text == "auto":
        return list(AUTO_K)
    try:
        values = sorted({int(v) for v in split_list(text)})
    except ValueError:
        raise UsageError("--k-settings must be auto or comma-separated integers") from None
    own = {p.egress.k for p in packs}
    if not values or any(not 2 <= v <= MAX_K or v in own for v in values):
        raise UsageError(f"--k-settings values must be in [2, {MAX_K}] and differ from every pack's own k") from None
    return values


def _check_numbers(args: argparse.Namespace) -> None:
    if not MIN_N <= args.n or 2 * args.n * args.volume_factor > MAX_RECORDS:
        raise UsageError(f"--n must be at least {MIN_N} and 2 * n * volume factor at most {MAX_RECORDS}") from None
    if not 2 <= args.volume_factor <= MAX_VOLUME_FACTOR:
        raise UsageError(f"--volume-factor must be in [2, {MAX_VOLUME_FACTOR}]") from None
    if not 1 <= args.a6_targets_per_site <= MAX_A6_TARGETS:
        raise UsageError(f"--a6-targets-per-site must be in [1, {MAX_A6_TARGETS}]") from None
    if args.primary_b < 1 or args.exploratory_b < 1:
        raise UsageError("--primary-b and --exploratory-b must be positive") from None
    if BOOTSTRAP_SEED_RE.fullmatch(args.bootstrap_seed) is None:
        raise UsageError("--bootstrap-seed must match [A-Za-z0-9][A-Za-z0-9_.-]{0,39}") from None
    if args.max_pipelines < 1:
        raise UsageError("--max-pipelines must be positive") from None


def _try_world(pack: FrozenPack, seed: int, n_records: int) -> WorldData | None:
    try:
        return build_world(pack, seed, n_records)
    except GeneratorError:
        return None


def _entry(pack_id: str, kind: str, world: WorldData) -> dict[str, Any]:
    return {"pack": pack_id, "kind": kind, "seed": world.seed, "world_digest": world.digest,
            "records": len(world.records), "weeks": world.last_week + 1}


def cmd_prereg(args: argparse.Namespace) -> int:
    dry = DryRun(f"{CLI} prereg") if args.dry_run else None
    try:
        check_run_id(args.run_id)
        out_dir = run_dir(args.runs_dir, KIND, args.run_id)
        refs = split_list(args.packs)
        if not refs or len(set(refs)) != len(refs):
            raise UsageError("--packs must name one or more distinct packs") from None
        _check_numbers(args)
        attacks = _selection(args.attacks, xa.ATTACKS, "attacks")
        types = _selection(args.artifact_types, xa.ARTIFACT_TYPES, "artifact-types")
        if "allowed_plus_all" in types and "allowed_fields_reference" not in types:
            raise UsageError("allowed_plus_all needs allowed_fields_reference among --artifact-types") from None
        simulated = [] if args.simulated == "none" else _selection(args.simulated, SIMULATED, "simulated")
        if dry is not None and any(not is_builtin_ref(r) and not Path(r).exists() for r in refs):
            for r in refs:
                if not is_builtin_ref(r) and not Path(r).exists():
                    dry.need(f"pack {r}")
            dry.write(str(out_dir / "prereg.json"))
            return dry.emit()
        packs = [load_pack(r) for r in refs]
        if len({p.id for p in packs}) != len(packs):
            raise UsageError("--packs names one pack twice") from None
        ks = _k_settings(args.k_settings, packs)
        allowed = ["default", *(f"k{k}" for k in ks), "rmd_flipped", "minus_type", "volume", *DERIVED_VARIANTS]
        variants = _selection(args.variants, allowed, "variants")
        if "default" not in variants:
            raise UsageError("--variants must include default (the derived variants and the primary family ride on "
                             "it)") from None
        if simulated and "default" not in variants:
            raise UsageError("--simulated needs the default variant") from None
        explicit = args.seeds is not None
        if not explicit and (args.shadow_seeds is not None or args.volume_seeds is not None):
            raise UsageError("--shadow-seeds and --volume-seeds need --seeds") from None
        if explicit and args.shadow_seeds is None:
            raise UsageError("--seeds needs --shadow-seeds") from None
        dirty = code_dirty()
        commit = code_commit()
        if dry is not None:
            if dirty is not False and not args.allow_dirty:
                dry.need("committed collective code (or --allow-dirty, which is stamped)")
            if not explicit and commit == "unknown":
                dry.need("a git commit for the seed rule (or --seeds and --shadow-seeds)")
            dry.write(str(out_dir / "prereg.json"))
            return dry.emit()
        if dirty is not False and not args.allow_dirty:
            raise UsageError("the collective code has uncommitted changes (or git is unavailable); commit first or "
                             "pass --allow-dirty, which is stamped") from None
        if explicit:
            seeds = _seed_list(args.seeds, "seeds")
            shadow = _seed_list(args.shadow_seeds, "shadow-seeds")
            given_volume = _seed_list(args.volume_seeds, "volume-seeds") if args.volume_seeds is not None else None
            if set(seeds) & set(shadow) or (given_volume is not None and not set(given_volume) <= set(seeds)):
                raise UsageError("--seeds and --shadow-seeds must be disjoint, and --volume-seeds among --seeds") \
                    from None
            candidates: list[dict[str, Any]] = []
        else:
            values, examined = derive_seeds(commit, N_SEEDS + N_SHADOW)
            seeds, shadow, given_volume = sorted(values[:N_SEEDS]), sorted(values[N_SEEDS:]), None
            candidates = [{"i": c["i"], "value": c["value"],
                           "use": "duplicate" if c["status"] == "duplicate"
                           else ("seed" if c["value"] in seeds else "shadow")} for c in examined]
        doc = build_prereg(args, packs, refs, variants=variants, ks=ks, attacks=attacks, types=types,
                           simulated=simulated, seeds=seeds, shadow=shadow, given_volume=given_volume,
                           candidates=candidates, explicit=explicit, commit=commit, dirty=dirty)
    except (UsageError, PackError) as exc:
        return fail(str(exc))
    problems = prereg_problems(doc)
    if problems:
        return fail(f"harness error: the prereg fails its own schema at {problems[0][0]} ({problems[0][1]})")
    out_dir.mkdir(parents=True)
    digest = write_json_atomic(out_dir / "prereg.json", doc)
    print(f"x5 prereg: wrote {out_dir / 'prereg.json'} sha256 {digest} (pipelines needed {doc['cap']['needed']}, "
          f"cap {doc['cap']['max_pipelines']}; synthetic, internal only)")
    return 0


def build_prereg(args: argparse.Namespace, packs: Sequence[FrozenPack], refs: Sequence[str], *,
                 variants: Sequence[str], ks: Sequence[int], attacks: Sequence[str], types: Sequence[str],
                 simulated: Sequence[str], seeds: Sequence[int], shadow: Sequence[int],
                 given_volume: Sequence[int] | None, candidates: list[dict[str, Any]], explicit: bool, commit: str,
                 dirty: bool | str) -> dict[str, Any]:
    """The prereg document: every variant copy built and hashed, every world generated and digested."""
    n_base, factor = 2 * args.n, args.volume_factor
    entries_: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    pack_docs = []
    volume_copies: dict[str, FrozenPack] = {}
    with tempfile.TemporaryDirectory(prefix="x5-prereg-") as tmp:
        for ref, pack in zip(refs, packs):
            records = []
            for vid in variants:
                copy = None
                if vid not in DERIVED_VARIANTS:
                    copy = write_variant(pack, vid, Path(tmp) / pack.id / vid, volume_factor=factor)
                    if vid == "volume":
                        volume_copies[pack.id] = copy
                records.append(variant_record(pack, vid, copy, factor))
            pack_docs.append({"ref": ref if is_builtin_ref(ref) else str(Path(ref)), "id": pack.id,
                              "version": pack.version, **pack.hashes(), "k": pack.egress.k,
                              "verdict_count_buckets": list(pack.egress.verdict_count_buckets),
                              "min_window_weeks": pack.egress.min_window_weeks, "removed_type": removed_type(pack),
                              "a5_fields": a5_fields(pack), "k_settings": sorted({*ks, pack.egress.k}),
                              "variants": records})
            for kind, group in (("base", seeds), ("shadow", shadow)):
                for s in group:
                    world = _try_world(pack, s, n_base)
                    if world is None:
                        raise UsageError(f"pack {pack.id}: the {kind} world of seed {s} cannot be generated") \
                            from None
                    entries_.append(_entry(pack.id, kind, world))
        volume_seeds: list[int] = []
        if "volume" in variants:
            for s in (given_volume if given_volume is not None else seeds):
                if given_volume is None and len(volume_seeds) == N_VOLUME:
                    break
                made = {p.id: _try_world(volume_copies[p.id], s, n_base * factor) for p in packs}
                failed = [pid for pid, w in made.items() if w is None]
                if failed and given_volume is not None:
                    raise UsageError(f"the volume world of seed {s} cannot be generated") from None
                excluded += [{"pack": pid, "kind": "volume", "seed": s, "reason": "generator capacity"}
                             for pid in failed]
                if not failed:
                    volume_seeds.append(s)
                    entries_ += [_entry(pid, "volume", made[pid]) for pid in sorted(made)]
            for p in packs:
                for s in shadow:
                    world = _try_world(volume_copies[p.id], s, n_base * factor)
                    if world is None:
                        excluded.append({"pack": p.id, "kind": "volume_shadow", "seed": s,
                                         "reason": "generator capacity"})
                    else:
                        entries_.append(_entry(p.id, "volume_shadow", world))
    primary = [{"pack": p.id, "attack": a, "variant": "default", "artifact_type": xa.ALL_TYPE}
               for p in packs for a in xa.PRIMARY_ATTACKS if a in attacks and xa.ALL_TYPE in types]
    family = len(primary)
    volume_shadow = {p.id: [s for s in shadow if not any(e["pack"] == p.id and e["kind"] == "volume_shadow"
                                                         and e["seed"] == s for e in excluded)] for p in packs}
    _, needed = pipeline_plan([p.id for p in packs], variants, seeds=seeds, volume_seeds=volume_seeds,
                              volume_shadow=volume_shadow, max_pipelines=args.max_pipelines)
    first = pack_docs[0]["variants"]
    table = [{"variant": v["id"], "attack": a, "artifact_type": t,
              **dict(zip(("status", "reason"), xa.applicability(v["id"], v["kind"], v["k"], a, t)))}
             for v in first for a in attacks for t in types]
    return {
        "kind": "x5_prereg", "schema_version": SCHEMA_VERSION, "run_id": args.run_id, "created_at": utc_clock(),
        "stamps": dict(STAMPS), "packs": pack_docs, "code_hash": x5_code_hash(), "code_files": x5_code_files(),
        "code_commit": commit, "code_dirty": dirty, "allow_dirty": bool(args.allow_dirty),
        "world": {"n": args.n, "records_per_world": "2n", "sites": SITES, "split_rule": SPLIT_RULE,
                  "seeds": list(seeds), "shadow_seeds": list(shadow), "volume_seeds": volume_seeds,
                  "volume_excluded": excluded, "volume_factor": factor, "seed_rule": SEED_RULE,
                  "seed_source": "given" if explicit else "derived from code_commit", "candidates": candidates,
                  "digests": entries_},
        "attacks": list(attacks), "artifact_types": list(types),
        "reference_types": [t for t in xa.REFERENCE_TYPES if t in types], "variants": list(variants),
        "simulated": list(simulated), "applicability": table,
        "rules": {"labels": dict(xa.LABEL_RULES), "at_chance_margin": xa.AT_CHANCE_MARGIN, "alpha": xa.ALPHA,
                  "family_size": family, "bonferroni_alpha": xa.ALPHA / family if family else None,
                  "a1_threshold_rule": A1_THRESHOLD_RULE, "a2_truth_rule": A2_TRUTH_RULE,
                  "a6_predicate_rule": A6_PREDICATE_RULE, "value_rule": VALUE_RULE},
        "primary": primary,
        "bootstrap": {"primary_B": args.primary_b, "exploratory_B": args.exploratory_b, "seed": args.bootstrap_seed},
        "a6": {"targets_per_site": args.a6_targets_per_site, "window": "min_window_weeks",
               "predicate_rule": A6_PREDICATE_RULE, "start": "as_of + 1 day"},
        "cap": {"max_pipelines": args.max_pipelines, "needed": needed},
        "not_covered_mapping": not_covered_mapping(), "notes": list(PREREG_NOTES),
    }


# --------------------------------------------------------------------------------------------------- x5.json

POSITIVE_CONTROLS = (("k1_reference", ("A1_calibrated", "A2", "A3", "A4")), ("a5_injected", ("A5",)))
POSITIVE_EXPECTED = xa.LEAK
NEGATIVE_EXPECTED = "at_chance or inconclusive, with ci_low <= 0 <= ci_high"


def format_value(value: Any) -> str:
    """A table cell: true/false, an int, a float to 3 decimals, null, or a string with ``|`` escaped."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.3f}"
    if value is None:
        return "null"
    if isinstance(value, str):
        return value.replace("|", "\\|")
    raise TypeError("not a scalar") from None


def pointer(*parts: str) -> str:
    return "/" + "/".join(p.replace("~", "~0").replace("/", "~1") for p in parts)


def primary_sentence(entry: Mapping[str, Any], *, pack_id: str, attack: str, family_size: int) -> str:
    adv = entry["advantage"] or {}
    return xa.SENTENCES[(entry["label"], entry["label_bonferroni"])].format(
        pack=pack_id, attack_name=xa.ATTACK_NAMES[attack], estimate=format_value(adv.get("estimate")),
        ci_low=format_value(adv.get("ci_low")), ci_high=format_value(adv.get("ci_high")), family_size=family_size)


def bar_outcome(primary_entries: Sequence[Mapping[str, Any]]) -> str:
    if any(e["label"] == xa.LEAK for e in primary_entries):
        return "fails"
    if primary_entries and all(e["label_bonferroni"] == xa.AT_CHANCE for e in primary_entries):
        return "met"
    return "undecided"


def controls_block(results: Mapping[str, Any], pack_ids: Sequence[str]) -> dict[str, Any]:
    positive, negative = [], []
    for pid in pack_ids:
        for vid, attacks in POSITIVE_CONTROLS:
            for attack in attacks:
                entry = results[pid].get(vid, {}).get(attack, {}).get(xa.ALL_TYPE)
                ran = entry is not None and entry["status"] == "run"
                positive.append({"pack": pid, "variant": vid, "attack": attack, "artifact_type": xa.ALL_TYPE,
                                 "pointer": pointer("results", pid, vid, attack, xa.ALL_TYPE) if entry else None,
                                 "expected": POSITIVE_EXPECTED, "label": entry["label"] if ran else None,
                                 "holds": entry["label"] == xa.LEAK if ran else None})
        for vid in pipeline_order(list(results[pid])):
            entry = results[pid][vid].get("A5", {}).get(xa.ALL_TYPE)
            if entry is None or entry["status"] != "run":
                continue
            adv = entry["advantage"]
            holds = entry["label"] in (xa.AT_CHANCE, xa.INCONCLUSIVE) and (
                adv is None or adv["ci_low"] <= 0 <= adv["ci_high"])
            negative.append({"pack": pid, "variant": vid, "attack": "A5", "artifact_type": xa.ALL_TYPE,
                             "pointer": pointer("results", pid, vid, "A5", xa.ALL_TYPE),
                             "expected": NEGATIVE_EXPECTED, "label": entry["label"], "holds": holds})
    all_hold = bool(positive) and bool(negative) and all(c["holds"] is True for c in (*positive, *negative))
    return {"positive": positive, "negative": negative, "all_hold": all_hold}


def assemble(prereg: Mapping[str, Any], prereg_data: bytes, plan: Plan, packs: Mapping[str, FrozenPack],
             outcome: Mapping[str, PackResult], *, run_id: str, code: str, dirty: bool | str, allow_dirty: bool,
             timings: Mapping[str, Any]) -> dict[str, Any]:
    pack_ids = [p["id"] for p in prereg["packs"]]
    results = {pid: outcome[pid].results for pid in pack_ids}
    primary: dict[str, Any] = {}
    primary_entries = []
    for p in prereg["primary"]:
        entry = results[p["pack"]]["default"][p["attack"]][xa.ALL_TYPE]
        primary_entries.append(entry)
        primary.setdefault(p["pack"], {})[p["attack"]] = {
            "pointer": pointer("results", p["pack"], "default", p["attack"], xa.ALL_TYPE), "label": entry["label"],
            "label_bonferroni": entry["label_bonferroni"],
            "sentence": primary_sentence(entry, pack_id=p["pack"], attack=p["attack"],
                                         family_size=plan.family_size)}
    outcome_name = bar_outcome(primary_entries)
    measured = {pid: {vid: {a: pointer("results", pid, vid, a, xa.ALL_TYPE)
                            for a in xa.PRIMARY_ATTACKS if a in plan.attacks}
                      for vid in pipeline_order(plan.variants) if vid != "default"}
                for pid in pack_ids} if xa.ALL_TYPE in plan.types else {pid: {} for pid in pack_ids}
    doc = {
        "stamps": dict(STAMPS), "statement": xa.STATEMENT, "run_id": run_id, "created_at": utc_clock(),
        "prereg_digest": runfiles.digest(sha256_hex(prereg_data)), "prereg_code_commit": prereg["code_commit"],
        "code_hash": runfiles.digest(code), "code_commit": code_commit(), "code_dirty": dirty,
        "allow_dirty": allow_dirty, "mode": "fake",
        "rules": {**prereg["rules"], "bootstrap": dict(prereg["bootstrap"]), "applicability": prereg["applicability"],
                  "selection": {"packs": list(pack_ids), "attacks": list(plan.attacks),
                                "artifact_types": list(plan.types), "variants": list(plan.variants),
                                "simulated": list(plan.simulated)}},
        "primary_family": {"entries": list(prereg["primary"]), "size": plan.family_size, "alpha": xa.ALPHA,
                           "bonferroni_alpha": prereg["rules"]["bonferroni_alpha"]},
        "packs": {pid: {"id": pid, "version": packs[pid].version, "k": packs[pid].egress.k,
                        "removed_type": removed_type(packs[pid]), "a5_fields": a5_fields(packs[pid]),
                        "hashes": {name: runfiles.digest(h) for name, h in packs[pid].hashes().items()},
                        "variants": outcome[pid].variants} for pid in pack_ids},
        "results": results, "primary": primary, "controls": controls_block(results, pack_ids),
        "mitigations": {"measured": measured, "simulated": {pid: outcome[pid].simulated for pid in pack_ids}},
        "not_covered": not_covered_mapping(),
        "bar": {"outcome": outcome_name,
                "sentence": xa.BAR_SENTENCES[outcome_name].format(family_size=plan.family_size)},
        "timings": dict(timings),
    }
    doc["content_hash"] = runfiles.content_hash(doc, CONTENT_HASH_EXCLUDES)
    return doc


def _interval() -> dict[str, Any]:
    return _O({"estimate": _NUM, "ci_low": _NUM, "ci_high": _NUM, "B": _POS, "seed": _STR, "method": _STR,
               "n_clusters": _POS}, nullable=True)


def _entry_schema() -> dict[str, Any]:
    labels = _enum(xa.LABELS, nullable=True)
    return _O({"status": _enum(xa.STATUSES), "reason": _NSTR, "primary": _BOOL, "family": _enum(xa.FAMILIES),
               "n_targets": _NNAT, "coverage": _NNUM, "accuracy": _NNUM,
               "accuracy_wilson": _O({"ci_low": _NUM, "ci_high": _NUM}, nullable=True), "baseline_accuracy": _NNUM,
               "advantage": _interval(), "label": labels, "label_bonferroni": labels,
               "advantage_bonferroni": _O({"ci_low": _NUM, "ci_high": _NUM, "alpha": _NUM}, nullable=True),
               "incremental": _O({"n_targets": _NAT, "advantage": _interval(), "label": _enum(xa.LABELS)},
                                 nullable=True)})


def results_schema(prereg: Mapping[str, Any]) -> schemacheck.Schema:
    """x5.json's closed schema, built from the prereg (every pack, variant, attack and artifact type it names)."""
    pack_ids = [p["id"] for p in prereg["packs"]]
    variants, attacks, types, simulated = (prereg["variants"], prereg["attacks"], prereg["artifact_types"],
                                           prereg["simulated"])
    entry = _entry_schema()
    world = _O({"seed": _POS, "world_digest": _D32, "records": _POS, "originals": _NAT, "members": _NAT,
                "non_members": _NAT, "weeks": _POS,
                "pipeline": _O({"cells": _NAT, "cells_n_ge_k": _NAT, "candidates": _NAT, "questions": _NAT,
                                "routes": _NAT, "verdicts": _O({v: _NAT for v in VERDICTS}),
                                "conclusions": _O({s: _NAT for s in GATE_STATUSES}), "packets": _NAT,
                                "drafts": _NAT, "outbox_lines": _NAT}, nullable=True),
                "a6": _O({"targets": _NAT, "windows": _NAT, "questions": _NAT, "days_needed": _NAT,
                          "budget_responses": _NAT, "verdicts": _O({v: _NAT for v in VERDICTS})}, nullable=True),
                "facts": _O({t: _NAT for t in (*xa.BASE_TYPES, "allowed_fields_reference")}),
                "targets": _O({a: _NAT for a in ("A1", "A2", "A3", "A4", "A5", "A6")}),
                "a1_single_class_strata": _NAT})
    designed = _O({"label": _const(DESIGNED_LABEL), "cells_lt_k": _NAT, "members_with_keys": _NAT,
                   "members_all_keys_present_share": _NNUM, "cells_exact": _NAT, "exact_correct_share": _NNUM},
                  nullable=True)

    def shadow(vid: str) -> dict[str, Any]:
        names = ["variant"]
        if vid == "default":
            names += [n for n in ("k1_reference",) if n in variants] + list(simulated)
        return _O({"worlds": _A(_O({"seed": _POS, "world_digest": _D32, "members": _NAT})),
                   "a1_thresholds": _O({n: _NUM for n in names}), "a1_targets": _NAT, "a3_cells": _NAT,
                   "a3_mode": _NNAT, "a4_pairs": _NAT, "a4_same": _NAT, "a5_targets": _NAT, "a6_windows": _NAT},
                  nullable=True)

    def variant(vid: str) -> dict[str, Any]:
        return _O({"kind": _enum(xa.VARIANT_KINDS), "label": _NSTR, "k": _POS, "buckets": _A(_POS, 1),
                   "require_master_data": _BOOL, "egress_entity_types": _A(_STR, 1), "volume_factor": _POS,
                   "hashes": _O({h: _D32 for h in _HASHES}, nullable=True), "status": _enum(("run", "not_run")),
                   "reason": _NSTR, "worlds": _A(world), "shadow": shadow(vid), "designed_disclosures": designed})

    matrix = _O({v: _O({a: _O({t: entry for t in types}) for a in attacks}) for v in variants})
    sim_attacks = [a for a in attacks if a != "A6"]
    sim_types = [t for t in types if t in (*xa.CELL_TYPES, xa.ALL_TYPE)]
    sim_entry = _O({"label": _const(SIMULATED_LABEL), "status": _enum(("run", "not_run")), "reason": _NSTR,
                    "cells_before": _NAT, "cells_after": _NAT, "removed_cell_share": _NNUM,
                    "entries": _O({a: _O({t: entry for t in sim_types}) for a in sim_attacks})})
    control = _O({"pack": _enum(pack_ids), "variant": _STR, "attack": _enum(xa.ATTACKS),
                  "artifact_type": _const(xa.ALL_TYPE), "pointer": _NSTR,
                  "expected": _enum((POSITIVE_EXPECTED, NEGATIVE_EXPECTED)), "label": _enum(xa.LABELS, nullable=True),
                  "holds": _NBOOL})
    primary_attacks = {p["pack"]: [] for p in prereg["primary"]}
    for p in prereg["primary"]:
        primary_attacks[p["pack"]].append(p["attack"])
    measured_variants = [v for v in pipeline_order(variants) if v != "default"]
    measured_attacks = [a for a in xa.PRIMARY_ATTACKS if a in attacks] if xa.ALL_TYPE in types else []
    return schemacheck.compile(_O({
        "stamps": _STAMPS, "statement": _const(xa.STATEMENT), "run_id": _STR, "created_at": _STR,
        "prereg_digest": _D32, "prereg_code_commit": _COMMIT, "code_hash": _D32, "code_commit": _COMMIT,
        "code_dirty": typed_schema("boolean", nullable=True), "allow_dirty": _BOOL, "mode": _const("fake"),
        "rules": _O({**_RULES,
                     "bootstrap": _O({"primary_B": _POS, "exploratory_B": _POS, "seed": _STR}),
                     "applicability": _A(_O({"variant": _STR, "attack": _enum(xa.ATTACKS),
                                             "artifact_type": _enum(xa.ARTIFACT_TYPES),
                                             "status": _enum(("applicable", "not_applicable", "not_run")),
                                             "reason": _NSTR})),
                     "selection": _O({"packs": _A(_STR), "attacks": _A(_STR), "artifact_types": _A(_STR),
                                      "variants": _A(_STR), "simulated": _A(_STR)})}),
        "primary_family": _O({"entries": _A(_O({"pack": _STR, "attack": _STR, "variant": _STR,
                                                 "artifact_type": _STR})),
                              "size": _NAT, "alpha": _NUM, "bonferroni_alpha": _NNUM}),
        "packs": _O({pid: _O({"id": _const(pid), "version": _STR, "k": _POS, "removed_type": _STR,
                              "a5_fields": _A(_STR), "hashes": _O({h: _D32 for h in _HASHES}),
                              "variants": _O({v: variant(v) for v in variants})}) for pid in pack_ids}),
        "results": _O({pid: matrix for pid in pack_ids}),
        "primary": _O({pid: _O({a: _O({"pointer": _STR, "label": _enum(xa.LABELS),
                                       "label_bonferroni": _enum(xa.LABELS), "sentence": _STR}) for a in attacks_})
                       for pid, attacks_ in primary_attacks.items()}),
        "controls": _O({"positive": _A(control), "negative": _A(control), "all_hold": _BOOL}),
        "mitigations": _O({"measured": _O({pid: _O({v: _O({a: _STR for a in measured_attacks})
                                                    for v in measured_variants}) for pid in pack_ids}),
                           "simulated": _O({pid: _O({s: sim_entry for s in simulated}) for pid in pack_ids})}),
        "not_covered": _A(_O({"item": _STR, "attacks": _A(_enum(xa.ATTACKS)),
                              "artifact_type": _enum(xa.ARTIFACT_TYPES, nullable=True), "reason": _NSTR})),
        "bar": _O({"outcome": _enum(xa.BAR_OUTCOMES), "sentence": _STR}),
        "timings": _O({"total_s": _NUM, "checks_s": _NUM, "packs": _O({pid: _NUM for pid in pack_ids})}),
        "content_hash": _D32,
    }))


# --------------------------------------------------------------------------------------------------- self-scan

_WORD = re.compile(r"[a-z0-9]+")


def value_hits(data: bytes, values: Iterable[str]) -> int:
    """How many of ``values`` occur in ``data`` as a whole token, case-insensitively (a value's first word must
    be one of the text's words before the exact search runs)."""
    text = data.decode("utf-8", "replace").lower()
    words = set(_WORD.findall(text))
    hits = 0
    for value in sorted(set(values)):
        low = value.lower()
        first = _WORD.findall(low)
        if not first or first[0] not in words:
            continue
        if re.search(r"(?<![a-z0-9_])" + re.escape(low) + r"(?![a-z0-9_])", text) is not None:
            hits += 1
    return hits


def self_scan(data: bytes, packs: Mapping[str, FrozenPack], inputs: Mapping[str, ScanInputs]) -> list[str]:
    """Problems (fixed texts, never a value): narrative shingles of any generated world of a pack, or a person,
    surname or reporter value of those worlds as a whole token."""
    problems = []
    for pid in sorted(inputs):
        pack = packs[pid]
        manifest = Manifest(pack.id, pack.config_hash, CANARY_PREFIX, ())
        for narratives in inputs[pid].narratives:
            report = scan([Artifact("x5_json", "x5.json", data=data)], manifest, narratives, pack)
            if report["shingle_overlap_bytes"]:
                problems.append(f"{pid}: narrative text")
                break
        if value_hits(data, inputs[pid].values):
            problems.append(f"{pid}: a person, surname or reporter value")
    return problems


def _forbidden(*paths: Path) -> list[str]:
    names = [str(p.resolve()) for p in paths] + [str(ROOT.resolve()), str(Path.home())]
    for get in (socket.gethostname, getpass.getuser):
        try:
            names.append(get())
        except (OSError, KeyError):
            pass
    return names


# --------------------------------------------------------------------------------------------------- LEAKAGE section 12

ENTRY_COLUMNS = ("Pointer", "n", "coverage", "accuracy", "baseline", "advantage", "95% CI", "label")
PRIMARY_COLUMNS = (*ENTRY_COLUMNS, "Bonferroni CI", "Bonferroni label")
INCREMENTAL_COLUMNS = ("Pointer", "n", "advantage", "95% CI", "label")
DESIGNED_COLUMNS = ("Pointer", "cells below k", "members with keys", "members with every key present", "exact cells",
                    "exact counts correct", "label")
CONTROL_COLUMNS = ("Pointer", "expected", "label", "holds")
MAPPING_COLUMNS = ("Pointer", "item", "attacks", "artifact type", "reason")
SIMULATED_COLUMNS = ("Pointer", "label", "cells before", "cells after", "removed cell share")


def _ci(interval: Mapping[str, Any] | None) -> str:
    if interval is None:
        return "null"
    return f"[{format_value(interval['ci_low'])}, {format_value(interval['ci_high'])}]"


def cell(column: str, node: Mapping[str, Any]) -> str:
    """One table cell from the object a row points at (the independent parser in the tests rebuilds the same)."""
    adv = node.get("advantage")
    simple = {"n": "n_targets", "coverage": "coverage", "accuracy": "accuracy", "baseline": "baseline_accuracy",
              "label": "label", "Bonferroni label": "label_bonferroni", "cells below k": "cells_lt_k",
              "members with keys": "members_with_keys",
              "members with every key present": "members_all_keys_present_share", "exact cells": "cells_exact",
              "exact counts correct": "exact_correct_share", "expected": "expected", "holds": "holds",
              "item": "item", "artifact type": "artifact_type", "reason": "reason", "cells before": "cells_before",
              "cells after": "cells_after", "removed cell share": "removed_cell_share"}
    if column in simple:
        return format_value(node[simple[column]])
    if column == "advantage":
        return format_value(adv["estimate"] if adv is not None else None)
    if column == "95% CI":
        return _ci(adv)
    if column == "Bonferroni CI":
        return _ci(node["advantage_bonferroni"])
    if column == "attacks":
        return ", ".join(node["attacks"])
    raise ValueError("unknown column") from None


def lookup(doc: Mapping[str, Any], ptr: str) -> Any:
    node: Any = doc
    for token in ptr[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        node = node[int(token)] if isinstance(node, list) else node[token]
    return node


def table(doc: Mapping[str, Any], columns: Sequence[str], pointers: Sequence[str]) -> list[str]:
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for ptr in pointers:
        node = lookup(doc, ptr)
        lines.append("| " + " | ".join([f"`{ptr}`", *(cell(c, node) for c in columns[1:])]) + " |")
    return lines


def leakage_section(doc: Mapping[str, Any]) -> str:
    """LEAKAGE section 12's body, rendered from x5.json alone: the bar, one sentence per primary test, the statement,
    then pointer-row tables (every cell is ``format_value`` of the field the row's pointer names). Every order comes
    from a list in the document (never from an object's key order, which canonical JSON sorts)."""
    sel = doc["rules"]["selection"]
    pack_ids = sel["packs"]
    attacks, types = sel["attacks"], sel["artifact_types"]
    primary_ptrs = [doc["primary"][p["pack"]][p["attack"]]["pointer"] for p in doc["primary_family"]["entries"]]
    lines = [doc["bar"]["sentence"], ""]
    lines += [doc["primary"][p["pack"]][p["attack"]]["sentence"] for p in doc["primary_family"]["entries"]]
    lines += ["", doc["statement"], ""]

    def entries_run(pid: str, vid: str, attack_list: Sequence[str], type_list: Sequence[str]) -> list[str]:
        out = []
        for a in attack_list:
            for t in type_list:
                ptr = pointer("results", pid, vid, a, t)
                if vid in doc["results"][pid] and a in attacks and t in types \
                        and doc["results"][pid][vid][a][t]["status"] == "run" and ptr not in primary_ptrs:
                    out.append(ptr)
        return out

    def section(title: str, columns: Sequence[str], pointers: Sequence[str]) -> None:
        if pointers:
            lines.extend([f"### {title}", "", *table(doc, columns, pointers), ""])

    family = doc["primary_family"]
    section(f"Primary ({family['size']} tests; Bonferroni alpha {format_value(family['bonferroni_alpha'])})",
            PRIMARY_COLUMNS, primary_ptrs)
    section("By artifact type (default variant; exploratory)", ENTRY_COLUMNS,
            [ptr for pid in pack_ids for ptr in entries_run(pid, "default", attacks, types)])
    variants = sel["variants"]
    k_variants = [v for v in pipeline_order(variants) if K_VARIANT_RE.fullmatch(v)]
    main = [a for a in xa.PRIMARY_ATTACKS if a in attacks]
    section("By k (exploratory)", ENTRY_COLUMNS,
            [ptr for pid in pack_ids for v in k_variants for ptr in entries_run(pid, v, main, [xa.ALL_TYPE])])
    section("By variant: rmd_flipped and minus_type (exploratory)", ENTRY_COLUMNS,
            [ptr for pid in pack_ids for v in ("rmd_flipped", "minus_type") if v in variants
             for ptr in entries_run(pid, v, main, [xa.ALL_TYPE])])
    section("Volume (exploratory)", ENTRY_COLUMNS,
            [ptr for pid in pack_ids if "volume" in variants for ptr in entries_run(pid, "volume", main,
                                                                                    [xa.ALL_TYPE])])
    section("Designed disclosures (STRATEGY 6.4; never labelled leak)", DESIGNED_COLUMNS,
            [pointer("packs", pid, "variants", v, "designed_disclosures") for pid in pack_ids
             for v in pipeline_order(variants) if doc["packs"][pid]["variants"][v]["designed_disclosures"]])
    reference = [t for t in xa.REFERENCE_TYPES if t in types]
    section("Allowed-fields reference (exploratory)", ENTRY_COLUMNS,
            [ptr for pid in pack_ids for ptr in entries_run(pid, "default", attacks, reference)])
    section("Incremental over the allowed-fields reference (exploratory)", INCREMENTAL_COLUMNS,
            [ptr + "/incremental" for pid in pack_ids
             for ptr in entries_run(pid, "default", attacks, ["allowed_plus_all"])
             if lookup(doc, ptr)["incremental"] is not None])
    controls = doc["controls"]
    section("Controls", CONTROL_COLUMNS,
            [pointer("controls", kind, str(i)) for kind in ("positive", "negative")
             for i in range(len(controls[kind]))])
    section("NOT_COVERED mapping", MAPPING_COLUMNS, [pointer("not_covered", str(i))
                                                     for i in range(len(doc["not_covered"]))])
    section("Mitigations: measured knobs (exploratory)", ENTRY_COLUMNS,
            [ptr for pid in pack_ids for v in pipeline_order(variants) if v != "default"
             for ptr in entries_run(pid, v, main, [xa.ALL_TYPE])])
    sim = doc["mitigations"]["simulated"]
    sim_attacks = [a for a in attacks if a != "A6"]
    sim_types = [t for t in types if t in (*xa.CELL_TYPES, xa.ALL_TYPE)]
    section("Mitigations: simulated transforms (" + SIMULATED_LABEL + ")", SIMULATED_COLUMNS,
            [pointer("mitigations", "simulated", pid, name) for pid in pack_ids for name in sel["simulated"]])
    section("Mitigations: simulated transforms, re-run attacks (exploratory)", ENTRY_COLUMNS,
            [pointer("mitigations", "simulated", pid, name, "entries", a, t) for pid in pack_ids
             for name in sel["simulated"] for a in sim_attacks for t in sim_types
             if sim[pid][name]["entries"][a][t]["status"] == "run"])
    statuses = [e["status"] for pid in pack_ids for v in doc["results"][pid].values() for a in v.values()
                for e in a.values()]
    lines.append(f"Not applicable: {statuses.count('not_applicable')} entries; not run: {statuses.count('not_run')} "
                 "entries (each with its status and reason under `/results` in x5.json).")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------------------- run

def pinned_differences(prereg: Mapping[str, Any], packs: Mapping[str, FrozenPack], code: str) -> list[str]:
    out = ["code_hash"] if prereg["code_hash"] != code else []
    for entry in prereg["packs"]:
        pack = packs[entry["id"]]
        out += [f"{entry['id']}.{h}" for h in _HASHES if entry[h] != getattr(pack, h)]
    return out


def build_copies(prereg: Mapping[str, Any], packs: Mapping[str, FrozenPack],
                 root: Path) -> tuple[dict[str, dict[str, FrozenPack]], list[str]]:
    """Every pipeline variant's pack copy under ``root``, and every hash that differs from the prereg (named)."""
    copies: dict[str, dict[str, FrozenPack]] = {}
    differing = []
    factor = prereg["world"]["volume_factor"]
    for entry in prereg["packs"]:
        base = packs[entry["id"]]
        copies[base.id] = {}
        for record in entry["variants"]:
            if record["kind"] != "pipeline":
                continue
            vid = record["id"]
            copy = base if vid == "default" else write_variant(base, vid, root / base.id / vid, volume_factor=factor)
            copies[base.id][vid] = copy
            differing += [f"{base.id}.{vid}.{h}" for h in _HASHES if record["hashes"][h] != getattr(copy, h)]
    return copies, differing


def world_differences(prereg: Mapping[str, Any], copies: Mapping[str, Mapping[str, FrozenPack]]) -> list[str]:
    differing = []
    for d in prereg["world"]["digests"]:
        generator_pack = copies[d["pack"]]["volume" if d["kind"].startswith("volume") else "default"]
        world = _try_world(generator_pack, d["seed"], d["records"])
        if world is None or world.digest != d["world_digest"]:
            differing.append(f"{d['pack']}/{d['kind']}/{d['seed']}")
    return differing


def estimate(prereg: Mapping[str, Any]) -> str:
    """The dry run's estimate line, from the prereg alone."""
    plan = Plan.of(prereg)
    pack_ids = [p["id"] for p in prereg["packs"]]
    runnable, _ = pipeline_plan(pack_ids, plan.variants, seeds=plan.seeds, volume_seeds=plan.volume_seeds,
                                volume_shadow={pid: plan.volume_shadow(pid) for pid in pack_ids},
                                max_pipelines=plan.max_pipelines)
    pipelines = sum(len(plan.volume_seeds if v == "volume" else plan.seeds)
                    for (_, v), (ok, _) in runnable.items() if ok)
    over = [f"{pid}/{v}" for (pid, v), (ok, reason) in runnable.items() if not ok and reason == CAP_REASON]
    kinds = [d["kind"] for d in prereg["world"]["digests"]]
    weeks = {(d["pack"], d["seed"]): d["weeks"] for d in prereg["world"]["digests"] if d["kind"] == "base"}
    m = {p["id"]: p["min_window_weeks"] for p in prereg["packs"]}
    questions = 0
    if "A6" in plan.attacks:
        questions = sum(SITES * plan.a6_per_site * max(0, weeks[(pid, s)] - m[pid] + 1)
                        for (pid, v), (ok, _) in runnable.items() if ok and v != "volume" for s in plan.seeds)
    cells = 0
    for p in prereg["packs"]:
        for record in p["variants"]:
            ok = record["kind"] == "derived" or runnable[(p["id"], record["id"])][0]
            if ok:
                cells += len(applicable_pairs(record["id"], record["kind"], record["k"], plan.attacks, plan.types))
        cells += len(plan.simulated) * len(applicable_pairs(
            "default", "pipeline", p["k"], [a for a in plan.attacks if a != "A6"],
            [t for t in plan.types if t in (*xa.CELL_TYPES, xa.ALL_TYPE)]))
    return (f"estimate: pipelines {pipelines} (cap {plan.max_pipelines}; over cap: {', '.join(over) or 'none'}), "
            f"worlds {kinds.count('base') + kinds.count('volume')}, shadow worlds "
            f"{kinds.count('shadow') + kinds.count('volume_shadow')}, a6 questions ~{questions}, bootstrap cells "
            f"~{cells} (primary 2x{plan.family_size} at B={plan.primary_b}, others at B={plan.exploratory_b})")


def _would_write(dry: DryRun, out_dir: Path, work: Path) -> None:
    dry.write(str(out_dir / "x5.json"))
    dry.write(str(out_dir / "leakage_section.md"))
    dry.write(f"{work} (removed at the end: pack copies and one pipeline directory at a time)")


def cmd_run(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    dry = DryRun(f"{CLI} run") if args.dry_run else None
    try:
        out_dir = run_dir(args.runs_dir, KIND, check_run_id(args.run_id))
        work = Path(args.work_dir)
        if work.exists() and (not work.is_dir() or any(work.iterdir())):
            raise UsageError(f"--work-dir must be absent or an empty directory: {work}") from None
        if not Path(args.prereg).is_file():
            if dry is None:
                raise UsageError(f"--prereg is not a file: {args.prereg}") from None
            dry.need(f"prereg file {args.prereg}")
            _would_write(dry, out_dir, work)
            return dry.emit()
        prereg, data = read_prereg(args.prereg)
        plan = Plan.of(prereg)
        packs = {entry["id"]: load_pack(entry["ref"]) for entry in prereg["packs"]}
        code = x5_code_hash()
        differing = pinned_differences(prereg, packs, code)
        if differing:
            raise UsageError(f"pinned values differ from the prereg: {', '.join(differing)}") from None
        dirty = code_dirty()
        if dirty is not False and not args.allow_dirty:
            if dry is None:
                raise UsageError("the collective code has uncommitted changes (or git is unavailable); commit "
                                 "first or pass --allow-dirty, which is stamped") from None
            dry.need("committed collective code (or --allow-dirty, which is stamped)")
        if dry is not None:
            _would_write(dry, out_dir, work)
            code_ = dry.emit()
            print(estimate(prereg))
            return code_
    except (UsageError, PackError) as exc:
        return fail(str(exc))
    timings: dict[str, Any] = {"total_s": 0.0, "checks_s": 0.0, "packs": {}}
    try:
        work.mkdir(parents=True, exist_ok=True)
        copies, differing = build_copies(prereg, packs, work / "packs")
        differing += world_differences(prereg, copies)
        if differing:
            raise UsageError(f"pinned values differ from the prereg: {', '.join(differing)}") from None
        pack_ids = [p["id"] for p in prereg["packs"]]
        runnable, _ = pipeline_plan(pack_ids, plan.variants, seeds=plan.seeds, volume_seeds=plan.volume_seeds,
                                    volume_shadow={pid: plan.volume_shadow(pid) for pid in pack_ids},
                                    max_pipelines=plan.max_pipelines)
        blocked = [pid for pid in pack_ids if not runnable[(pid, "default")][0]]
        if blocked:
            raise UsageError(f"the default variant cannot run in full under the cap: {', '.join(blocked)}") from None
        timings["checks_s"] = time.perf_counter() - started
        outcome = {}
        for pid in pack_ids:
            t0 = time.perf_counter()
            outcome[pid] = run_pack(plan, packs[pid], copies[pid], runnable, work)
            timings["packs"][pid] = time.perf_counter() - t0
        timings["total_s"] = time.perf_counter() - started
        doc = assemble(prereg, data, plan, packs, outcome, run_id=args.run_id, code=code, dirty=dirty,
                       allow_dirty=bool(args.allow_dirty), timings=timings)
        problems = union_problems(results_schema(prereg), doc, ("code_dirty",))
        if problems:
            raise HarnessError(f"x5.json fails its schema at {problems[0][0]} ({problems[0][1]})") from None
        blob = (canonical_dumps(doc) + "\n").encode("utf-8")
        portability = runfiles.portability_problems(blob, forbidden=_forbidden(work, Path(args.runs_dir)))
        if portability:
            raise UsageError(f"x5.json is not portable ({', '.join(portability)}); nothing written") from None
        scanned = self_scan(blob, packs, {pid: outcome[pid].scan for pid in pack_ids})
        if scanned:
            raise UsageError(f"x5.json fails the self-scan ({'; '.join(scanned)}); nothing written") from None
        section = leakage_section(doc)
        out_dir.mkdir(parents=True)
        digest = write_json_atomic(out_dir / "x5.json", doc)
        (out_dir / "leakage_section.md").write_bytes(section.encode("utf-8"))
    except KeyboardInterrupt:
        print("x5 run: interrupted; nothing was written and the work directory is removed", file=sys.stderr)
        return 130
    except (UsageError, PackError, GeneratorError, PushdownError, LeakageError, runfiles.RunFileError) as exc:
        return fail(str(exc))
    except HarnessError as exc:
        return fail(f"harness error: {exc}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    controls = doc["controls"]
    print(f"x5: wrote {out_dir / 'x5.json'} sha256 {digest} content_hash {doc['content_hash']} bar "
          f"{doc['bar']['outcome']} controls_hold {str(controls['all_hold']).lower()} (synthetic, same-author, "
          "internal only)")
    code_, messages = control_exit(controls)
    for message in messages:
        print(f"error: {message}", file=sys.stderr)
    return code_


def control_exit(controls: Mapping[str, Any]) -> tuple[int, list[str]]:
    """2 when a positive control that ran is not labelled leak (the harness cannot detect a known leak), else 1
    when a negative control that ran is outside its rule, else 0; with the messages to print."""
    failed = [c for c in controls["positive"] if c["holds"] is False]
    if failed:
        return 2, [f"harness cannot detect a known leak: {c['pack']}/{c['variant']}/{c['attack']}" for c in failed]
    if any(c["holds"] is False for c in controls["negative"]):
        return 1, ["the negative control (A5) is not at chance: stop and investigate before publishing"]
    return 0, []


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mycelic.collective.experiments.x5_inference",
                                description="X5: leakage beyond text, measured by an HQ-level red team on synthetic "
                                            "worlds (synthetic, same-author, internal only).")
    sub = p.add_subparsers(dest="command", required=True)
    pr = sub.add_parser("prereg", help="freeze the settings, code, packs, variant copies and worlds")
    pr.add_argument("--packs", required=True, help="comma-separated built-in pack ids or pack directories")
    pr.add_argument("--run-id", required=True)
    pr.add_argument("--n", type=int, default=DEFAULT_N, help="members per world on average (2n records)")
    pr.add_argument("--seeds", help="tests only: explicit world seeds (default: the seed rule)")
    pr.add_argument("--shadow-seeds", help="tests only: explicit shadow seeds, disjoint from --seeds")
    pr.add_argument("--volume-seeds", help="tests only: explicit volume seeds, among --seeds")
    pr.add_argument("--volume-factor", type=int, default=DEFAULT_VOLUME_FACTOR)
    pr.add_argument("--k-settings", default="auto", help="auto (2 and 10) or comma-separated k values")
    pr.add_argument("--variants", default="all")
    pr.add_argument("--attacks", default="all")
    pr.add_argument("--artifact-types", default="all")
    pr.add_argument("--simulated", default="all", help="all, none or comma-separated transforms")
    pr.add_argument("--a6-targets-per-site", type=int, default=DEFAULT_A6_TARGETS)
    pr.add_argument("--primary-b", type=int, default=DEFAULT_PRIMARY_B)
    pr.add_argument("--exploratory-b", type=int, default=DEFAULT_EXPLORATORY_B)
    pr.add_argument("--bootstrap-seed", default=DEFAULT_BOOTSTRAP_SEED)
    pr.add_argument("--max-pipelines", type=int, default=DEFAULT_MAX_PIPELINES)
    pr.add_argument("--runs-dir", default="runs")
    pr.add_argument("--allow-dirty", action="store_true")
    pr.add_argument("--dry-run", action="store_true")
    ru = sub.add_parser("run", help="run a prereg once and write x5.json and leakage_section.md")
    ru.add_argument("--prereg", required=True)
    ru.add_argument("--run-id", required=True)
    ru.add_argument("--work-dir", required=True, help="absent or empty; removed at the end")
    ru.add_argument("--runs-dir", default="runs")
    ru.add_argument("--allow-dirty", action="store_true")
    ru.add_argument("--dry-run", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return cmd_prereg(args) if args.command == "prereg" else cmd_run(args)
    except (OSError, StrictJsonError) as exc:
        return fail(f"cannot read or write a run file ({exc.__class__.__name__})")


if __name__ == "__main__":
    sys.exit(main())
