"""G0: plant canaries in raw records, then scan every byte that crossed a boundary for them and for narrative text.

**Scope: text only.** A clean scan shows that planted text did not leave; it says nothing about what counts and
claims reveal (STRATEGY section 6.4: the weakest leak mode). :data:`NOT_COVERED` lists what it does not cover, and
every report carries that list.

Canaries (:func:`plant_canaries`, from a ``random.Random`` passed in, on deep copies of the records):

* **class a**, letter-only: ``Qzxv`` plus 12 random lower-case letters, appended to every narrative, to every person
  field that does not hold a class-b token, and to every distinct reporter (one token per reporter value, so the
  reporter count is unchanged). A core is re-drawn while any of its 8-letter windows is in the pack's text (config,
  world spec, schema words), is already used, or could be hex (only ``a`` to ``f``), while the token scans to an
  entity mention or a lexicon, cue or alias term, or while it contains or is contained in an id canary;
* **class b**, id-shaped person data (25% of records): an id of an egress type, outside the world's ids, the pack's
  text and every other canary, set as the whole value of a person field and mirrored into the narrative as
  ``'<field phrase> <id>.'``. Extraction drops it as a person value;
* **class c**, id-shaped narrative-only data (25% of records): the same draw and sentence, but only in the
  narrative. With ``require_master_data`` the site drops it (not master data); without, it leaves as a cell key,
  which the report lists under ``known_limitation`` instead of ``hits``.

Id canaries use only types whose ids can reach :data:`MIN_ID_CANARY` characters, and must hold a letter ``g`` to
``z`` so no hex digest can contain one. The manifest (:func:`write_manifest`) is the only file holding tokens; it
must never sit inside a scanned path, and :func:`scan` refuses one that does.

The scan (:func:`scan`) reads each artifact as raw bytes (so SQLite pages and ``-wal`` files are scanned too) and
looks at up to three views: the bytes decoded as UTF-8 with replacement, then JSON-unescaped once and twice
(``\\uXXXX`` in either case, surrogate pairs, the short escapes), which catches ``ensure_ascii`` output, mixed and
double escaping. Canary search is case-insensitive: any 8 consecutive core letters of a class-a canary (a
``core_window`` hit, or ``token`` when the whole token is there) or a whole id token. Shingle search is exact: every
24-character window of every planted narrative, minus windows that also occur in the pack's config text (the
``config_hash`` file set, not the world spec, whose templates are not public). Hits name the canary by id, never by
token; the report never holds a token.
"""
from __future__ import annotations

import copy
import os
import random
import re
import string
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

from .edge.egress import schema_words
from .jsonio import StrictJsonError, canonical_dumps, strict_load
from .packs.canonical import Canonicaliser, folded, term_regex
from .packs.loader import HASH_SCOPES, compute_hashes

if TYPE_CHECKING:
    from .packs.canonical import IdFormat
    from .packs.loader import FrozenPack

CANARY_PREFIX = "Qzxv"
CORE_LETTERS = 12
CANARY_WINDOW = 8
SHINGLE_CHARS = 24
RATE_B = 0.25
RATE_C = 0.25
MIN_ID_CANARY = 6
MAX_DRAWS = 50
MAX_REPORTED_HITS = 1000
CLASSES = ("a", "b", "c")
SCOPE = "text-only"
MANIFEST_KIND = "g0_canary_manifest"
VIEWS = ("raw", "unescaped")
MATCHES = ("token", "core_window")
CANARY_KEYS = ("canary_class", "core", "entity_type", "field", "id", "record_ref", "token")
MANIFEST_KEYS = ("canaries", "config_hash", "kind", "pack", "prefix", "schema_version")
ARTIFACT_CLASS_RE = re.compile(r"[a-z][a-z0-9_]{0,40}", re.ASCII)
CANARY_ID_RE = re.compile(r"[abc]-[0-9]{6}", re.ASCII)
_HEX_ONLY = re.compile(r"[a-f]+", re.ASCII)
_PAST_HEX = re.compile(r"[g-z]", re.ASCII)
_LETTER_RUN = re.compile(rf"[a-z]{{{CANARY_WINDOW},}}", re.ASCII)
_HEX64 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_ESCAPE = re.compile(r'\\(?:u([0-9A-Fa-f]{4})|(["\\/bfnrt]))')
_SHORT_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}

NOT_COVERED = (
    "Attribute inference on counts and claims (X5).",
    "Membership inference (X5).",
    "Differencing between verdict buckets and weekly cells (G6, X5).",
    "Cross-site duplicates without an origin marker, which each site counts as independent (X5).",
    "A '<k' cell still reveals that an entity had at least one record with that predicate in that week, and entity "
    "ids are emitted in clear by design (STRATEGY section 6.4).",
    "Usage summaries reveal weekly extraction-call volume and latency per site, with counts of at least k.",
    "Encoded or transformed text (hashes, base64, translation, paraphrase).",
    "Fragments shorter than 8 canary-core characters or 24 narrative characters.",
    "Strings split across SQLite pages.",
    "Presence or absence of an entity at a site in a question window, revealed by a refute versus an unknown; "
    "limited, not prevented, by the per-entity daily question budget (G6, X5).",
    "Bucket transitions between overlapping question windows for one key, which can narrow a count inside its "
    "bucket (G6, X5).",
)
KNOWN_LIMITATION_NOTE = (
    "With require_master_data off, ids found only in narratives leave as cell keys (their counts suppressed), so "
    "id-shaped person data written into a narrative crosses. Turn require_master_data on, or keep person "
    "identifiers out of id formats."
)


class LeakageError(ValueError):
    pass


@dataclass(frozen=True)
class Canary:
    """``field`` is ``narrative``, ``persons.<f>`` or ``reporter``; ``core`` is the 12 random letters of a class-a
    token (None for id canaries); ``entity_type`` is the id type (None for class a)."""

    id: str
    canary_class: str
    token: str
    core: str | None
    record_ref: str
    field: str
    entity_type: str | None


@dataclass(frozen=True)
class Manifest:
    pack_id: str
    config_hash: str
    prefix: str
    canaries: tuple[Canary, ...]
    path: Path | None = None


@dataclass(frozen=True)
class Artifact:
    """Something that crossed (or, for the hygiene class, stayed): a file or directory ``path``, or ``data``."""

    artifact_class: str
    label: str
    path: str | Path | None = None
    data: bytes | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_class, str) or ARTIFACT_CLASS_RE.fullmatch(self.artifact_class) is None:
            raise ValueError("artifact_class must match [a-z][a-z0-9_]{0,40}") from None
        if (self.path is None) == (self.data is None):
            raise ValueError("an artifact has exactly one of path and data") from None


# --------------------------------------------------------------------------------------------------- manifest

def _manifest_obj(manifest: Manifest) -> dict[str, Any]:
    return {"kind": MANIFEST_KIND, "schema_version": 1, "pack": manifest.pack_id,
            "config_hash": manifest.config_hash, "prefix": manifest.prefix,
            "canaries": [{"id": c.id, "canary_class": c.canary_class, "token": c.token, "core": c.core,
                          "record_ref": c.record_ref, "field": c.field, "entity_type": c.entity_type}
                         for c in manifest.canaries]}


def write_manifest(manifest: Manifest, path: str | Path) -> Manifest:
    """Canonical JSON through a temporary file and ``os.replace``; returns the manifest with its resolved path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (canonical_dumps(_manifest_obj(manifest)) + "\n").encode("utf-8")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return replace(manifest, path=path.resolve())


def _canary_ok(c: Any) -> bool:
    if not isinstance(c, dict) or sorted(c) != list(CANARY_KEYS):
        return False
    if not isinstance(c["id"], str) or CANARY_ID_RE.fullmatch(c["id"]) is None or c["canary_class"] != c["id"][0]:
        return False
    if not isinstance(c["token"], str) or not c["token"] or not isinstance(c["record_ref"], str) \
            or not isinstance(c["field"], str):
        return False
    if c["canary_class"] == "a":
        return (isinstance(c["core"], str) and _LETTER_RUN.fullmatch(c["core"]) is not None
                and c["token"].lower().endswith(c["core"]) and c["entity_type"] is None)
    return c["core"] is None and isinstance(c["entity_type"], str)


def read_manifest(path: str | Path) -> Manifest:
    obj = None
    try:
        obj = strict_load(Path(path).read_bytes())
    except (OSError, StrictJsonError):
        obj = None
    ok = (isinstance(obj, dict) and sorted(obj) == list(MANIFEST_KEYS) and obj["kind"] == MANIFEST_KIND
          and obj["schema_version"] == 1 and not isinstance(obj["schema_version"], bool)
          and isinstance(obj["pack"], str) and isinstance(obj["config_hash"], str)
          and _HEX64.fullmatch(obj["config_hash"]) is not None and isinstance(obj["prefix"], str)
          and isinstance(obj["canaries"], list) and all(_canary_ok(c) for c in obj["canaries"])
          and len({c["id"] for c in obj["canaries"]}) == len(obj["canaries"]))
    if not ok:
        raise LeakageError("malformed manifest") from None
    canaries = tuple(Canary(id=c["id"], canary_class=c["canary_class"], token=c["token"], core=c["core"],
                            record_ref=c["record_ref"], field=c["field"], entity_type=c["entity_type"])
                     for c in obj["canaries"])
    return Manifest(pack_id=obj["pack"], config_hash=obj["config_hash"], prefix=obj["prefix"], canaries=canaries,
                    path=Path(path).resolve())


# --------------------------------------------------------------------------------------------------- pack text

def _strings(value: Any, out: list[str]) -> None:
    if isinstance(value, Mapping):
        for key in value:
            out.append(key)
            _strings(value[key], out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _strings(item, out)
    elif isinstance(value, str):
        out.append(value)


def config_strings(pack: "FrozenPack") -> list[str]:
    """Every key and string value of the pack's ``config_hash`` files, read again from disk; refuses files that no
    longer hash to the pack's ``config_hash``."""
    files: dict[str, Any] = {}
    changed = False
    for name in HASH_SCOPES["config_hash"]["files"]:
        path = Path(pack.directory) / name
        if not path.exists():
            continue
        try:
            files[name] = strict_load(path.read_bytes())
        except (OSError, StrictJsonError):
            changed = True
    if changed or compute_hashes(files, [])["config_hash"] != pack.config_hash:
        raise LeakageError("pack files changed since the pack was loaded") from None
    out: list[str] = []
    for name in sorted(files):
        _strings(files[name], out)
    return out


def collision_corpus(pack: "FrozenPack") -> tuple[str, frozenset[str]]:
    """The pack's text (config strings, the world spec the pack was loaded with, the artifact schema words), joined
    and lower-cased, and the set of its 8-character windows."""
    strings = config_strings(pack)
    _strings(pack.generator, strings)
    strings.extend(schema_words())
    corpus = "\n".join(strings).lower()
    windows = frozenset(corpus[i:i + CANARY_WINDOW] for i in range(len(corpus) - CANARY_WINDOW + 1))
    return corpus, windows


# --------------------------------------------------------------------------------------------------- planting

def _max_len(fmt: "IdFormat") -> int:
    total = 0
    for kind, value in fmt.segments:
        if kind == "literal":
            total += len(value)
        elif kind == "sep":
            total += 1 if fmt.separator else 0
        else:
            total += value[1]
    return total


class _Planter:
    """Draws canaries for one pack from the rng it is given (``random()`` and ``choice()`` only)."""

    def __init__(self, pack: "FrozenPack", rng: random.Random) -> None:
        self.pack = pack
        self.rng = rng
        self.corpus, self.corpus_windows = collision_corpus(pack)
        self.person_fields = sorted(pack.mapping()["persons"])
        self.id_types = sorted(t for t in pack.egress.egress_entity_types
                               if pack.entity_types[t].id_format is not None
                               and _max_len(pack.entity_types[t].id_format) >= MIN_ID_CANARY)
        terms = [folded(term) for p in pack.predicates.values() for lang in p.lexicon for term in p.lexicon[lang]]
        terms += [folded(cue) for neg in pack.negation.values() for cue in (*neg.pre, *neg.post)]
        terms += [folded(alias) for table in pack.aliases.values() for alias in table]
        self.lex = term_regex(t for t in terms if t)
        self.canon = Canonicaliser(pack)
        gen = pack.generator
        known = {i for ids in gen["universe"].values() for i in ids}
        known |= {i for per_type in gen["master_data"].values() for ids in per_type.values() if ids != "all"
                  for i in ids}
        known |= {i for targets in pack.alias_targets().values() for i in targets}
        self.known_ids = frozenset(known)
        self.used_windows: set[str] = set()
        self.tokens: list[str] = []          # every token so far, lower-cased
        self.id_tokens: list[str] = []       # id tokens so far, lower-cased
        self.canaries: list[Canary] = []
        self.counters = dict.fromkeys(CLASSES, 0)
        self.reporter_tokens: dict[str, str] = {}

    def add(self, canary_class: str, token: str, core: str | None, record_ref: str, field: str,
            entity_type: str | None) -> None:
        self.counters[canary_class] += 1
        self.canaries.append(Canary(id=f"{canary_class}-{self.counters[canary_class]:06d}",
                                    canary_class=canary_class, token=token, core=core, record_ref=record_ref,
                                    field=field, entity_type=entity_type))
        self.tokens.append(token.lower())
        if canary_class != "a":
            self.id_tokens.append(token.lower())

    def _hits_text(self, text: str) -> bool:
        return bool(self.canon.scan(text).mentions) or (self.lex is not None
                                                         and self.lex.search(folded(text)) is not None)

    def phrase(self, person_field: str) -> str:
        phrase = person_field.replace("_", " ")
        return "ref" if self._hits_text(phrase) else phrase

    def draw_letters(self) -> tuple[str, str]:
        for _ in range(MAX_DRAWS + 1):
            core = "".join(self.rng.choice(string.ascii_lowercase) for _ in range(CORE_LETTERS))
            token = CANARY_PREFIX + core
            windows = [core[i:i + CANARY_WINDOW] for i in range(CORE_LETTERS - CANARY_WINDOW + 1)]
            if any(w in self.corpus_windows or w in self.used_windows or _HEX_ONLY.fullmatch(w) for w in windows):
                continue
            if self._hits_text(token):
                continue
            low = token.lower()
            if any(low in t or t in low for t in self.id_tokens):
                continue
            self.used_windows.update(windows)
            return token, core
        raise LeakageError(f"no clean letter canary after {MAX_DRAWS} re-draws") from None

    def draw_id(self, entity_type: str, phrase: str) -> tuple[str, str]:
        fmt = self.pack.entity_types[entity_type].id_format
        letters = string.ascii_uppercase if fmt.case == "upper" else string.ascii_lowercase
        pools = {"alpha": letters, "digit": string.digits, "alnum": letters + string.digits}
        for _ in range(MAX_DRAWS + 1):
            parts = []
            for kind, value in fmt.segments:
                if kind == "literal":
                    parts.append(value)
                elif kind == "sep":
                    parts.append(fmt.separator or "")
                else:
                    n = self.rng.choice(range(value[0], value[1] + 1))
                    parts.append("".join(self.rng.choice(pools[kind]) for _ in range(n)))
            token = fmt.canonical_form("".join(parts))
            if token is None or fmt.canonical.fullmatch(token) is None or token in self.known_ids:
                continue
            low = token.lower()
            if low in self.corpus or _PAST_HEX.search(low) is None or len(token) < MIN_ID_CANARY:
                continue
            if any(low in t or t in low for t in self.tokens):
                continue
            sentence = f"{phrase} {token}."
            mentions = self.canon.scan(sentence).mentions
            if len(mentions) != 1 or (mentions[0].entity_type, mentions[0].entity_id) != (entity_type, token):
                continue
            return token, sentence
        raise LeakageError(f"no clean id canary after {MAX_DRAWS} re-draws") from None

    def plant(self, record: Mapping[str, Any]) -> dict[str, Any]:
        rec = copy.deepcopy(dict(record))
        ref = rec["record_ref"]
        persons = rec["persons"]
        narrative = rec["narrative"]
        holds_b = None
        u = self.rng.random()
        if self.id_types and self.person_fields and u < RATE_B + RATE_C:
            canary_class = "b" if u < RATE_B else "c"
            person_field = self.rng.choice(self.person_fields)
            entity_type = self.rng.choice(self.id_types)
            token, sentence = self.draw_id(entity_type, self.phrase(person_field))
            narrative += "\n" + sentence
            if canary_class == "b":
                persons[person_field] = token
                holds_b = person_field
                self.add("b", token, None, ref, f"persons.{person_field}", entity_type)
            else:
                self.add("c", token, None, ref, "narrative", entity_type)
        for person_field in self.person_fields:
            if person_field == holds_b:
                continue
            token, core = self.draw_letters()
            value = persons[person_field]
            persons[person_field] = f"{value} {token}" if value else token
            self.add("a", token, core, ref, f"persons.{person_field}", None)
        token, core = self.draw_letters()
        rec["narrative"] = (narrative + "\n" if narrative else "") + token + "."
        self.add("a", token, core, ref, "narrative", None)
        reporter = rec["reporter"]
        if reporter is not None:
            if reporter not in self.reporter_tokens:
                token, core = self.draw_letters()
                self.reporter_tokens[reporter] = token
                self.add("a", token, core, ref, "reporter", None)
            rec["reporter"] = f"{reporter} {self.reporter_tokens[reporter]}"
        return rec


def plant_canaries(records: Iterable[Mapping[str, Any]], rng: random.Random,
                   pack: "FrozenPack") -> tuple[tuple[dict[str, Any], ...], Manifest]:
    """Deep copies of ``records`` with canaries planted, and the manifest that lists them (no path yet)."""
    planter = _Planter(pack, rng)
    planted = tuple(planter.plant(r) for r in records)
    return planted, Manifest(pack_id=pack.id, config_hash=pack.config_hash, prefix=CANARY_PREFIX,
                             canaries=tuple(planter.canaries))


# --------------------------------------------------------------------------------------------------- scanning

def unescape(text: str) -> str:
    """JSON escapes decoded left to right (``\\uXXXX`` in either hex case and the short escapes), then surrogate
    pairs merged; a lone surrogate becomes U+FFFD."""
    out = _ESCAPE.sub(lambda m: chr(int(m.group(1), 16)) if m.group(1) else _SHORT_ESCAPES[m.group(2)], text)
    return out.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def _views(data: bytes) -> list[tuple[str, str]]:
    v0 = data.decode("utf-8", "replace")
    views = [("raw", v0)]
    if "\\" in v0:
        v1 = unescape(v0)
        if v1 != v0:
            views.append(("unescaped", v1))
            v2 = unescape(v1)
            if v2 != v1:
                views.append(("unescaped", v2))
    return views


def _read(path: Path) -> bytes:
    data = None
    try:
        data = path.read_bytes()
    except OSError:
        data = None
    if data is None:
        raise LeakageError("a scanned file cannot be read") from None
    return data


def _units(artifact: Artifact, manifest_path: Path | None) -> list[tuple[str, bytes]]:
    """(file label, bytes) for each file of an artifact; refuses a missing path and the manifest."""
    if artifact.data is not None:
        return [(artifact.label, bytes(artifact.data))]
    path = Path(artifact.path)
    if not path.exists():
        raise LeakageError("a scanned path is missing") from None
    resolved = path.resolve()
    if manifest_path is not None and (resolved == manifest_path
                                      or (path.is_dir() and manifest_path.is_relative_to(resolved))):
        raise LeakageError("a scanned path holds the canary manifest") from None
    if not path.is_dir():
        return [(artifact.label, _read(path))]
    out = []
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            file = Path(dirpath) / name
            if manifest_path is not None and file.resolve() == manifest_path:
                raise LeakageError("a scanned path holds the canary manifest") from None
            out.append((f"{artifact.label}/{file.relative_to(path).as_posix()}", _read(file)))
    return out


class _CanaryIndex:
    def __init__(self, manifest: Manifest) -> None:
        self.prefix_len = len(manifest.prefix)
        self.windows: dict[str, list[tuple[Canary, int]]] = {}
        self.ids: dict[int, dict[str, Canary]] = {}
        for c in manifest.canaries:
            if c.canary_class == "a":
                core = c.core.lower()
                for i in range(len(core) - CANARY_WINDOW + 1):
                    self.windows.setdefault(core[i:i + CANARY_WINDOW], []).append((c, i))
            else:
                self.ids.setdefault(len(c.token), {})[c.token.lower()] = c
        chars = sorted({ch for table in self.ids.values() for token in table for ch in token})
        shortest = min(self.ids, default=0)
        self.id_run = (re.compile("[" + "".join(re.escape(ch) for ch in chars) + "]{" + str(shortest) + ",}")
                       if chars else None)

    def search(self, view: str) -> dict[str, tuple[Canary, str]]:
        """``{canary_id: (canary, match)}`` for one lower-cased view."""
        found: dict[str, tuple[Canary, str]] = {}
        for m in _LETTER_RUN.finditer(view):
            run, base = m.group(), m.start()
            for i in range(len(run) - CANARY_WINDOW + 1):
                for c, j in self.windows.get(run[i:i + CANARY_WINDOW], ()):
                    start = base + i - self.prefix_len - j
                    token = c.token.lower()
                    match = "token" if start >= 0 and view[start:start + len(token)] == token else "core_window"
                    if match == "token" or c.id not in found:
                        found[c.id] = (c, match)
        if self.id_run is not None:
            for m in self.id_run.finditer(view):
                run = m.group()
                for length, table in self.ids.items():
                    for i in range(len(run) - length + 1):
                        c = table.get(run[i:i + length])
                        if c is not None:
                            found[c.id] = (c, "token")
        return found


@dataclass(frozen=True)
class _Shingles:
    index: frozenset[str]
    excluded: frozenset[str]
    narratives: int
    short: int
    windows_excluded: int


def _shingles(narratives: Iterable[str], pack: "FrozenPack") -> _Shingles:
    index: set[str] = set()
    n = short = 0
    for text in narratives:
        n += 1
        if len(text) < SHINGLE_CHARS:
            short += 1
            continue
        index.update(text[i:i + SHINGLE_CHARS] for i in range(len(text) - SHINGLE_CHARS + 1))
    vocabulary = {s[i:i + SHINGLE_CHARS] for s in config_strings(pack) for i in range(len(s) - SHINGLE_CHARS + 1)}
    excluded = index & vocabulary
    return _Shingles(index=frozenset(index - excluded), excluded=frozenset(excluded), narratives=n, short=short,
                     windows_excluded=len(excluded))


def _overlap(view: str, sh: _Shingles, seen_excluded: set[str]) -> int:
    """UTF-8 bytes of the union of positions covered by narrative windows in ``view``."""
    spans: list[list[int]] = []
    for i in range(len(view) - SHINGLE_CHARS + 1):
        w = view[i:i + SHINGLE_CHARS]
        if w in sh.index:
            if spans and i <= spans[-1][1]:
                spans[-1][1] = i + SHINGLE_CHARS
            else:
                spans.append([i, i + SHINGLE_CHARS])
        elif w in sh.excluded:
            seen_excluded.add(w)
    return sum(len(view[a:b].encode("utf-8", "surrogatepass")) for a, b in spans)


def _scan_units(units: Sequence[tuple[str, str, bytes]], idx: _CanaryIndex, sh: _Shingles, *,
                limitation: bool, seen_excluded: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]],
                                                                    list[dict[str, Any]]]:
    """(hits, known_limitation, shingle_hits) over (artifact_class, file, bytes) units."""
    hits, limited, shingle_hits = [], [], []
    for artifact_class, file, data in units:
        best: dict[str, tuple[Canary, str, str]] = {}
        overlap, overlap_view = 0, "raw"
        for view_name, view in _views(data):
            for cid, (c, match) in idx.search(view.lower()).items():
                held = best.get(cid)
                if held is None or (MATCHES.index(match), VIEWS.index(view_name)) < \
                        (MATCHES.index(held[1]), VIEWS.index(held[2])):
                    best[cid] = (c, match, view_name)
            n = _overlap(view, sh, seen_excluded)
            if n > overlap:
                overlap, overlap_view = n, view_name
        for cid in sorted(best):
            c, match, view_name = best[cid]
            if limitation and c.canary_class == "c" and match == "token":
                limited.append({"canary_id": cid, "artifact_class": artifact_class, "file": file})
            else:
                hits.append({"artifact_class": artifact_class, "file": file, "canary_id": cid,
                             "canary_class": c.canary_class, "match": match, "view": view_name})
        if overlap:
            shingle_hits.append({"artifact_class": artifact_class, "file": file, "bytes": overlap,
                                 "view": overlap_view})
    return hits, limited, shingle_hits


def _sorted(rows: list[dict[str, Any]], *keys: str) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: tuple(str(row[k]) for k in keys))


def scan(artifacts: Sequence[Artifact], manifest: Manifest, narratives: Iterable[str], pack: "FrozenPack", *,
         hygiene: Sequence[Artifact] = ()) -> dict[str, Any]:
    """Scan every crossing artifact (and, reported apart, the hygiene artifacts) for the manifest's canaries and
    for narrative shingles. The report never holds a token."""
    manifest_path = Path(manifest.path).resolve() if manifest.path is not None else None
    if manifest.config_hash != pack.config_hash:
        raise LeakageError("the manifest was planted under another pack config") from None
    crossing = [(a, _units(a, manifest_path)) for a in artifacts]
    kept = [(a, _units(a, manifest_path)) for a in hygiene]
    sh = _shingles(narratives, pack)               # runs config_strings, so the pack hash check runs here
    idx = _CanaryIndex(manifest)
    seen_excluded: set[str] = set()

    classes: dict[str, dict[str, int]] = {}
    scanned = []
    units: list[tuple[str, str, bytes]] = []
    for a, files in crossing:
        size = sum(len(data) for _, data in files)
        entry = classes.setdefault(a.artifact_class, {"bytes": 0, "items": 0})
        entry["bytes"] += size
        entry["items"] += len(files)
        scanned.append({"artifact_class": a.artifact_class, "label": a.label, "bytes": size})
        units += [(a.artifact_class, file, data) for file, data in files]
    hits, limited, shingle_hits = _scan_units(units, idx, sh, limitation=not pack.egress.require_master_data,
                                              seen_excluded=seen_excluded)

    hygiene_units: list[tuple[str, str, bytes]] = []
    for a, files in kept:
        scanned.append({"artifact_class": a.artifact_class, "label": a.label,
                        "bytes": sum(len(data) for _, data in files)})
        hygiene_units += [(a.artifact_class, file, data) for file, data in files]
    hygiene_hits, _, hygiene_shingles = _scan_units(hygiene_units, idx, sh, limitation=False, seen_excluded=set())

    hits = _sorted(hits, "artifact_class", "file", "canary_id")
    hygiene_hits = _sorted(hygiene_hits, "artifact_class", "file", "canary_id")
    return {
        "scope": SCOPE,
        "config_hash": pack.config_hash,
        "require_master_data": pack.egress.require_master_data,
        "canaries_planted": len(manifest.canaries),
        "canaries_by_class": {c: sum(1 for x in manifest.canaries if x.canary_class == c) for c in CLASSES},
        "artifact_classes": {c: classes[c] for c in sorted(classes)},
        "scanned": scanned,
        "hits": hits[:MAX_REPORTED_HITS],
        "hit_count": len(hits),
        "known_limitation": _sorted(limited, "artifact_class", "file", "canary_id"),
        "known_limitation_note": KNOWN_LIMITATION_NOTE if limited else None,
        "shingle_overlap_bytes": sum(s["bytes"] for s in shingle_hits),
        "shingle_hits": _sorted(shingle_hits, "artifact_class", "file"),
        "excluded_vocabulary_windows": len(seen_excluded),
        "shingles": {"window_chars": SHINGLE_CHARS, "narratives": sh.narratives, "short_narratives": sh.short,
                     "windows_indexed": len(sh.index), "windows_excluded_as_vocabulary": sh.windows_excluded},
        "site_ledger_hygiene": {"bytes": sum(len(data) for _, _, data in hygiene_units),
                                "items": len(hygiene_units), "hits": hygiene_hits[:MAX_REPORTED_HITS],
                                "shingle_overlap_bytes": sum(s["bytes"] for s in hygiene_shingles)},
        "not_covered": list(NOT_COVERED),
    }
