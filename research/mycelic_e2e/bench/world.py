"""Deterministic world generator, task planner and materializer (PLAN_v1 §B.1, §B.2, §B.4; WP2).

``generate(seed, size, bank)``    -> :class:`World` (tenants, units, users, memberships, holders, projects: specs only).
``materialize(rt, world)``        -> :class:`Ids` (creates everything through ``rt.auth`` / ``rt.org`` only).
``plan(world, bank, split, seed)`` -> :class:`Plan` (tasks + raw records). A task's gold lives in ``TaskSpec.gold_keys``; it
                                      leaves this module only through ``Plan.gold(ids)`` -> :class:`schema.Gold` objects that
                                      run.py hands to the sealed sink. Nothing here writes a claim, a discovery, a holder
                                      domain or an evidence reference: observations are raw records only.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from typing import Any, Iterable

from .hygiene import TITLES
from .schema import SIZES, Gold, TaskPublic, TemplateBank

# tenants x (target users per tenant, structure)
SHAPES: dict[str, dict[str, int]] = {
    "S": dict(users=56, regions=1, subs=2, depts=2, teams=2),       # disjoint goal-only scopes need >= 5 free employees per goal-only task
    "M": dict(users=500, regions=2, subs=2, depts=3, teams=4),
    "L": dict(users=5000, regions=3, subs=3, depts=4, teams=8),
}
PROJECTS_PER_TENANT = 5                       # one per goal-only task (they isolate the scope of a goal-only pattern)
TENANTS = (("acme-freight", "Acme Freight"), ("borealis-retail", "Borealis Retail"))
REGIONS = ("North", "South", "East", "West", "Central", "Coastal")
SUBSIDIARIES = ("Atlas", "Beacon", "Cedar", "Delta", "Everest", "Falcon", "Granite", "Horizon", "Icon", "Juniper", "Keystone", "Lantern",
                "Meridian", "Nova", "Orchard", "Pinnacle", "Quill", "Ridge")
SITES = ("Lyon", "Porto", "Turin", "Graz", "Oslo", "Riga", "Cork", "Bonn", "Gent", "Linz", "Brno", "Kiel")
FIRST = tuple("""Ana Ben Chen Dara Eli Finn Gia Hugo Ines Jon Kai Lena Marc Nia Omar Pia Quinn Rosa Sam Tess Uma Vik Wes Xia Yara Zed Alma Bram
    Cleo Dev Edda Farid Greta Hana Ivo Jun Kira Leo Mira Nils Opal Pavel Rina Sven Tara Ugo Vera Will Yuri Zoe""".split())
LAST = tuple("""Abbott Baker Castro Dubois Evans Fischer Garcia Huang Ito Jensen Khan Lopez Meyer Novak Ortiz Petrov Quist Rossi Silva Tanaka Ueda
    Varga Weber Xu Young Zhou Adler Brandt Cole Duarte Eriksen Ferrari Gomez Hart Ivanov Jovic Kowal Lund Moreau Nakamura Olsen Park
    Qureshi Rao Sato Torres Usher Vogel Wang Yilmaz Zielinski""".split())

TASK_MIX = (("cross_domain", 40), ("contradiction", 10), ("temporal", 10), ("common_origin_pos", 8), ("common_origin_copies", 7),
            ("coincidence", 10), ("single_domain", 10), ("denied", 10), ("cross_tenant", 5), ("fault", 10))
GOAL_ONLY = 10                                 # of the 40 cross_domain positives
FAULT_KINDS = ("duplicate", "replay", "out_of_order", "edit", "delete", "missing_metadata", "malformed", "restart_mid_ingest", "duplicate", "delete")
COMMENTS = ("Passing this along from the other group, it matches what we see on our side as well.",
            "Sharing the note below with you since it looks like the same situation we are handling.",
            "Forwarding this to the team because it describes something we also run into here.")
APPS = ("chat", "wiki", "tickets", "mail")


VIS_PUBLIC_SHARE = 60                          # % of user holders whose personal source is public; the rest is visible to the owner's team only


def _hash_int(*parts: Any) -> int:
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16)


def title_for(rid: str) -> str:
    """Role-neutral title: one shared pool for every record class, drawn by the record's opaque id."""
    return TITLES[_hash_int("title", rid) % len(TITLES)]


def _rng(*parts: Any) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16], 16))


# ================================================================================================ the world
@dataclass
class UnitSpec:
    key: str
    type: str
    name: str
    parent: str | None
    domain: str | None = None            # departments (and their teams): the evidence domain of the function


@dataclass
class UserSpec:
    key: str
    name: str
    email: str
    memberships: list[tuple[str, str]]    # (unit key, role)
    dept: str | None                      # department key of the primary membership


@dataclass
class HolderSpec:
    key: str
    owner_type: str                       # user | unit
    owner_key: str
    name: str
    dept: str | None
    vis: str = "public"                   # visibility of the holder's main source: public | members (the owner's team)
    peers: tuple[str, ...] = ()           # user keys of that team (the members of a members-visible source)


@dataclass
class ProjectSpec:
    key: str
    name: str
    depts: tuple[str, str]
    members: list[str]                    # user keys: 2 from the minority department, 3 from the majority one; disjoint across projects
    maj: str = ""                         # the department with 3 members
    asker: str = ""                       # department lead of the minority department: sees the scope, holds no observation


@dataclass
class TenantSpec:
    idx: int
    slug: str
    name: str
    admin: str                            # user key of the registering administrator
    units: dict[str, UnitSpec] = field(default_factory=dict)
    users: dict[str, UserSpec] = field(default_factory=dict)
    holders: dict[str, HolderSpec] = field(default_factory=dict)
    projects: list[ProjectSpec] = field(default_factory=list)

    def depts(self) -> list[UnitSpec]:
        return [u for u in self.units.values() if u.type == "department"]

    def children(self, key: str, type_: str | None = None) -> list[UnitSpec]:
        return [u for u in self.units.values() if u.parent == key and (type_ is None or u.type == type_)]

    def ancestors(self, key: str) -> list[str]:
        out, cur = [], self.units[key].parent
        while cur:
            out.append(cur)
            cur = self.units[cur].parent
        return out

    def dept_of(self, unit_key: str) -> str | None:
        cur: str | None = unit_key
        while cur:
            if self.units[cur].type == "department":
                return cur
            cur = self.units[cur].parent
        return None


@dataclass
class World:
    seed: int
    size: str
    bank: str
    tenants: list[TenantSpec]

    def counts(self) -> dict[str, int]:
        return {"tenants": len(self.tenants), "users": sum(len(t.users) for t in self.tenants), "units": sum(len(t.units) for t in self.tenants),
                "holders": sum(len(t.holders) for t in self.tenants), "unit_holders": sum(1 for t in self.tenants for h in t.holders.values() if h.owner_type == "unit"),
                "projects": sum(len(t.projects) for t in self.tenants)}

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        for t in self.tenants:
            h.update(t.slug.encode())
            for k in sorted(t.units):
                u = t.units[k]
                h.update(f"{k}|{u.type}|{u.name}|{u.parent}|{u.domain}".encode())
            for k in sorted(t.users):
                u = t.users[k]
                h.update(f"{k}|{u.name}|{u.memberships}".encode())
            for p in t.projects:
                h.update(f"{p.key}|{p.depts}|{p.members}".encode())
        return h.hexdigest()


def generate(seed: int, size: str, bank: TemplateBank) -> World:
    if size not in SIZES:
        raise ValueError(f"size must be one of {SIZES}")
    sh = SHAPES[size]
    tenants: list[TenantSpec] = []
    for ti, (slug, name) in enumerate(TENANTS):
        rng = _rng(seed, size, "world", ti)
        t = TenantSpec(idx=ti, slug=slug, name=name, admin=f"t{ti}-admin")
        root = f"t{ti}-root"
        t.units[root] = UnitSpec(root, "executive", name, None)
        deps = list(bank.departments[4 * ti:] + bank.departments[:4 * ti])        # tenants start at different functions
        dcount = 0
        first_names = list(FIRST)
        rng.shuffle(first_names)
        last_names = list(LAST)
        rng.shuffle(last_names)
        uidx = [0]

        def new_user(role_memberships: list[tuple[str, str]], dept: str | None, tag: str) -> str:
            i = uidx[0]
            uidx[0] += 1
            nm = f"{first_names[i % len(first_names)]} {last_names[(i // len(first_names)) % len(last_names)]}"
            key = f"t{ti}-u{i:05d}"
            email = f"{nm.lower().replace(' ', '.')}.u{i:05d}@{slug}.example"
            t.users[key] = UserSpec(key, nm, email, role_memberships, dept)
            return key

        admin = new_user([(root, "org_admin"), (root, "executive")], None, "admin")
        t.admin = admin
        sub_i = 0
        for r in range(sh["regions"]):
            rk = f"t{ti}-r{r}"
            t.units[rk] = UnitSpec(rk, "region", f"{REGIONS[r % len(REGIONS)]} Region", root)
            new_user([(rk, "regional_lead")], None, "rl")
            for s in range(sh["subs"]):
                sk = f"t{ti}-s{sub_i}"
                t.units[sk] = UnitSpec(sk, "subsidiary", f"{SUBSIDIARIES[sub_i % len(SUBSIDIARIES)]} {REGIONS[r % len(REGIONS)]}", rk)
                sub_i += 1
                new_user([(sk, "subsidiary_lead")], None, "sl")
                for d in range(sh["depts"]):
                    base, dom = deps[dcount % len(deps)]
                    dname = base if dcount < len(deps) else f"{base} {SITES[(dcount // len(deps) - 1) % len(SITES)]}"
                    dk = f"t{ti}-d{dcount}"
                    dcount += 1
                    t.units[dk] = UnitSpec(dk, "department", dname, sk, dom)
                    new_user([(dk, "department_lead")], dk, "dl")
                    for m in range(sh["teams"]):
                        tk = f"{dk}-tm{m}"
                        t.units[tk] = UnitSpec(tk, "team", f"{dname} Team {m + 1}", dk, dom)
        # employees: spread the remaining users evenly over the teams
        teams = [u for u in t.units.values() if u.type == "team"]
        n_emp = max(len(teams), sh["users"] - len(t.users))
        per = [n_emp // len(teams) + (1 if i < n_emp % len(teams) else 0) for i in range(len(teams))]
        for tm, cnt in zip(teams, per):
            dk = t.dept_of(tm.key)
            for e in range(cnt):
                new_user([(tm.key, "team_lead" if e % 10 == 0 else "employee")], dk, "emp")
        # holders: one per user, one per department and team
        for u in t.users.values():
            t.holders[f"h-{u.key}"] = HolderSpec(f"h-{u.key}", "user", u.key, f"{u.name} notes", u.dept)
        for u in t.units.values():
            if u.type in ("department", "team"):
                t.holders[f"h-{u.key}"] = HolderSpec(f"h-{u.key}", "unit", u.key, f"{u.name} memory", t.dept_of(u.key))
        # projects: cross-functional, spanning two departments of one family (one per goal-only task)
        sub_depts: dict[str, list[UnitSpec]] = {}
        for d in t.depts():
            sub_depts.setdefault(d.parent or "", []).append(d)
        pairs: list[tuple[UnitSpec, UnitSpec]] = []
        for ds in sub_depts.values():
            for i in range(len(ds)):
                for j in range(i + 1, len(ds)):
                    if (ds[i].domain or "").split(".")[0] == (ds[j].domain or "").split(".")[0]:
                        pairs.append((ds[i], ds[j]))
        if not pairs:                                                     # fall back: any two departments
            ds = t.depts()
            pairs = [(ds[0], ds[1])]
        employees_by_dept: dict[str, list[str]] = {}
        for u in t.users.values():
            if u.dept and u.memberships and u.memberships[0][1] in ("employee", "team_lead"):
                employees_by_dept.setdefault(u.dept, []).append(u.key)
        for lst in employees_by_dept.values():
            rng.shuffle(lst)
        used: set[str] = set()

        def take(dept: str, k: int) -> list[str]:
            got = [e for e in employees_by_dept.get(dept, []) if e not in used][:k]
            if len(got) < k:
                raise RuntimeError(f"department {dept} has too few free employees for disjoint goal-only projects")
            used.update(got)
            return got
        for pi in range(PROJECTS_PER_TENANT):
            a, b = pairs[pi % len(pairs)]
            maj, mn = (a, b) if pi % 2 == 0 else (b, a)
            pk = f"t{ti}-p{pi}"
            t.units[pk] = UnitSpec(pk, "project", f"Cross-functional Initiative {chr(65 + pi)}{ti + 1}", root)
            members = take(mn.key, 2) + take(maj.key, 3)
            asker = next(u.key for u in t.users.values() if (mn.key, "department_lead") in u.memberships)
            t.projects.append(ProjectSpec(pk, t.units[pk].name, (a.key, b.key), members, maj.key, asker))
            for mk in members:
                t.users[mk].memberships.append((pk, "employee"))
        # visibility of every user holder's personal source: one distribution for all classes (members of a goal-only project are public)
        in_project = {m for p in t.projects for m in p.members}
        teams: dict[str, list[str]] = {}
        for u in t.users.values():
            teams.setdefault(u.memberships[0][0], []).append(u.key)
        for h in t.holders.values():
            if h.owner_type == "user" and h.owner_key not in in_project and _hash_int(seed, size, "vis", h.key) % 100 >= VIS_PUBLIC_SHARE:
                h.vis = "members"
                h.peers = tuple(sorted(teams[t.users[h.owner_key].memberships[0][0]]))
        tenants.append(t)
    return World(seed, size, bank.name, tenants)


# ================================================================================================ materialization
@dataclass
class Ids:
    tenants: dict[str, str] = field(default_factory=dict)     # slug -> tenant_id
    units: dict[str, str] = field(default_factory=dict)       # unit key -> unit_id
    users: dict[str, str] = field(default_factory=dict)       # user key -> user_id
    holders: dict[str, str] = field(default_factory=dict)     # holder key -> holder_id
    holder_keys: dict[str, str] = field(default_factory=dict)  # holder_id -> holder key (reverse)
    timing: dict[str, float] = field(default_factory=dict)


async def materialize(rt: Any, world: World, *, log: Any = None) -> Ids:
    """Create tenants, units, users, memberships, project scopes, policies and holders through the real services. Holders are
    registered ``mode='embedded'`` with NO domains (routable domains must come from ingestion)."""
    import time

    from mycelic.auth import hash_password
    ids = Ids()
    t_all = time.perf_counter()
    for t in world.tenants:
        reg = await rt.auth.register_tenant(org_name=t.name, slug=t.slug, admin_email=t.users[t.admin].email, admin_name=t.users[t.admin].name,
                                            password="correct horse battery")
        tid = reg["tenant"]["tenant_id"]
        ids.tenants[t.slug] = tid
        ids.units[f"t{t.idx}-root"] = reg["root_unit"]["unit_id"]
        ids.users[t.admin] = reg["user"]["user_id"]
        for key, val in (("min_independent_roots", 2), ("freshness_days", 365),
                         ("holder_default_export", {"disclosure": "excerpt", "max_excerpt_chars": 480, "answer_scopes": ["unit", "org"]})):
            await rt.org.set_policy(tid, key, val, actor_id=ids.users[t.admin])
        order = {"executive": 0, "region": 1, "subsidiary": 2, "department": 3, "team": 4, "project": 5}
        for u in sorted(t.units.values(), key=lambda x: order[x.type]):
            if u.type == "executive":
                continue
            made = await rt.org.create_unit(tid, u.type, u.name, parent_id=ids.units[u.parent] if u.parent else None)
            ids.units[u.key] = made["unit_id"]
        for p in t.projects:
            await rt.org.set_project_scope(ids.units[p.key], [ids.units[d] for d in p.depts])
        for u in t.users.values():
            if u.key == t.admin:
                continue
            made = await rt.org.create_user(tid, u.email, u.name)
            ids.users[u.key] = made["user_id"]
            for unit_key, role in u.memberships:
                await rt.org.add_membership(tid, made["user_id"], ids.units[unit_key], role)
        for h in t.holders.values():
            owner = ids.users[h.owner_key] if h.owner_type == "user" else ids.units[h.owner_key]
            rec, _key = await rt.org.register_holder(tid, owner_type=h.owner_type, owner_id=owner, name=h.name, mode="embedded")
            ids.holders[h.key] = rec["holder_id"]
            ids.holder_keys[rec["holder_id"]] = h.key
        if log:
            log(f"materialized tenant {t.slug}: {len(t.users)} users, {len(t.units)} units, {len(t.holders)} holders")
    ids.timing["materialize_s"] = round(time.perf_counter() - t_all, 3)
    return ids


# ================================================================================================ records and tasks (planner)
@dataclass
class Rec:
    rid: str
    tenant: int
    holder: str
    source: str                      # "pub" | "priv" | "mem<k>"
    text: str
    title: str
    author: str
    days_ago: float
    typ: str = "document"            # document | edit | delete
    phase: str = "base"              # base | late
    forwarded: bool = False
    forward_of: str | None = None    # rid of the original (a forwarded copy)
    comment: str = ""                # own commentary written above the forwarded block (a forward that adds text)
    members: list[str] = field(default_factory=list)
    pattern: str = ""
    role: str = ""                   # obs | decoy | filler | copy | retracted | correction
    version_offset_days: float = 0.0  # updated_at = created_at + offset
    missing_meta: bool = False

    def __post_init__(self) -> None:
        if self.title:                                   # one shared title pool for every class (opaque, drawn by the record id)
            self.title = title_for(self.rid)


@dataclass
class TaskSpec:
    public: TaskPublic
    cls: str
    answer: str                                   # option label or "abstain"
    entity: str | None
    genuine_roots: int | None
    holder_keys: list[str]
    departments: list[str]
    decoy_options: list[str]
    forbidden_holders: list[str] = field(default_factory=list)
    forbidden_markers: list[str] = field(default_factory=list)
    forbidden_texts: list[str] = field(default_factory=list)   # texts whose roots must never be visible
    fault: str | None = None
    note: str = ""
    pattern: str = ""                              # planner-internal pattern id (records carry the same id)
    opt_args: tuple | None = None                 # (tenant idx, gold service, extra services): options are drawn once every entity exists


@dataclass
class Plan:
    split: str
    seed: int
    tasks: list[TaskSpec]
    records: list[Rec]
    fault_plan: dict[str, Any]                    # holder key -> {"replay": bool, "restart": bool, "malformed": bool, "duplicate": [rid], "out_of_order": bool}
    world: Any = None
    entities: list[dict] = field(default_factory=list)     # the world's whole entity vocabulary (every service of both tenants) in the options' dict form: public, not gold

    def public(self) -> list[TaskPublic]:
        return [t.public for t in self.tasks]

    def raw_allowed(self, asker_key: str) -> list[str]:
        """Holder keys whose raw content the asker may legitimately read (authz.can_view_raw_evidence): their own user holder and the
        unit holders of every unit they lead (and below)."""
        lead_roles = {"team_lead", "department_lead", "subsidiary_lead", "regional_lead", "executive"}
        out: set[str] = set()
        for t in self.world.tenants:
            u = t.users.get(asker_key)
            if u is None:
                continue
            out.add(f"h-{asker_key}")
            led = {unit for unit, role in u.memberships if role in lead_roles}
            for k in t.units:
                if k in led or any(a in led for a in t.ancestors(k)):
                    if f"h-{k}" in t.holders:
                        out.add(f"h-{k}")
        return sorted(out)

    def gold(self, ids: Ids) -> list[Gold]:
        from mycelic.util import fingerprint
        out = []
        for t in self.tasks:
            hid = lambda keys: [ids.holders[k] for k in keys if k in ids.holders]   # noqa: E731
            out.append(Gold(task_id=t.public.task_id, cls=t.cls, answer=t.answer, expected_abstain=t.answer == "abstain", entity_id=t.entity,
                            genuine_roots=t.genuine_roots, holders=hid(t.holder_keys), departments=list(t.departments), decoy_options=list(t.decoy_options),
                            forbidden_holder_ids=hid(t.forbidden_holders), forbidden_root_ids=[fingerprint(x) for x in t.forbidden_texts],
                            forbidden_markers=list(t.forbidden_markers), raw_allowed_holder_ids=hid(self.raw_allowed(t.public.asker_key)), fault=t.fault, note=t.note))
        return out


def svc_label(svc: str) -> str:
    return f"{svc}-service"


def _option(svc: str) -> dict[str, Any]:
    return {"label": svc_label(svc), "id": f"service:{svc}", "display": svc_label(svc), "aliases": [svc]}


class Planner:
    def __init__(self, world: World, bank: TemplateBank, split: str, seed: int, goal_obs: tuple[str, ...]) -> None:
        self.w, self.bank, self.split, self.seed, self.goal_obs = world, bank, split, seed, goal_obs
        self.rng = _rng(seed, world.size, split, "plan")
        self.records: list[Rec] = []
        self.specs: list[TaskSpec] = []
        self.fault_plan: dict[str, Any] = {}
        self.ctx_pool = min(len(bank.ctx_adjectives), len(bank.ctx_nouns))
        self.ctx_next = [0, 0]
        self.reserved_ctx = 60                                         # own tasks use contexts 0..59 in either tenant (separate worlds) ...
        self.reserved_next = [self.reserved_ctx, self.reserved_ctx + 5]  # ... a cross-tenant pattern uses a range that exists in ONE tenant only
        self.decoy_ctx_idx = self.reserved_ctx + 10                    # one context shared by the in-scope decoys of all goal-only tasks (scopes are exclusive)
        if self.ctx_pool < self.decoy_ctx_idx + 1:
            raise RuntimeError(f"the bank offers {self.ctx_pool} contexts; {self.decoy_ctx_idx + 1} are needed")
        names = sorted(p + s for p in bank.service_prefixes for s in bank.service_suffixes)
        _rng(seed, world.size, split, "svc").shuffle(names)
        self.svc_names: list[list[str]] = [names[0::2], names[1::2]]   # disjoint service names per tenant
        self.reserved_holders: list[set[str]] = [{f"h-{m}" for p in t.projects for m in p.members} for t in world.tenants]
        self.lead_holders: list[set[str]] = [{f"h-{u.key}" for u in t.users.values() if any(r == "department_lead" for _, r in u.memberships)} for t in world.tenants]
        self.svc_next = [0] * len(world.tenants)
        self.entities: list[list[str]] = [[] for _ in world.tenants]
        self.rec_counter = [0] * len(world.tenants)
        self.proj_next = [0] * len(world.tenants)
        self.used_pairs: dict[int, set] = {}

    # ---- allocation helpers
    def ctx(self, ti: int, reserved: bool = False) -> str:
        if reserved:
            i = self.reserved_next[ti]
            self.reserved_next[ti] += 1
        else:
            i = self.ctx_next[ti]
            self.ctx_next[ti] += 1
            if i >= self.reserved_ctx:
                raise RuntimeError("more task contexts than the bank supports")
        return f"{self.bank.ctx_adjectives[i]} {self.bank.ctx_nouns[i]}"

    def svc(self, ti: int) -> str:
        n = self.svc_names[ti][self.svc_next[ti]]
        self.svc_next[ti] += 1
        self.entities[ti].append(n)
        return n

    def rid(self, ti: int, tag: str) -> str:
        self.rec_counter[ti] += 1
        return hashlib.sha256(f"{self.seed}|{self.split}|{ti}|{tag}|{self.rec_counter[ti]}".encode()).hexdigest()[:12]        # opaque: no role prefix

    def number(self) -> int:
        return self.rng.randint(12, 97)

    def other_number(self, n: int) -> int:
        m = self.rng.randint(12, 97)
        return m if m != n else (m % 80) + 13

    def dept_holders(self, t: TenantSpec, dept: str) -> list[str]:
        """Holder keys inside a department that may carry observations: its unit holder, its team holders and the user holders of its
        people - except department leads (they ask goal-only questions) and members of a goal-only project (their scopes are exclusive)."""
        out = [f"h-{dept}"] + [f"h-{c.key}" for c in t.children(dept, "team")]
        out += [f"h-{u.key}" for u in t.users.values() if u.dept == dept]
        return [h for h in out if h not in self.reserved_holders[t.idx] and h not in self.lead_holders[t.idx]]

    def pick_holders(self, t: TenantSpec, dept: str, k: int, avoid: Iterable[str] = (), kind: str = "reachable") -> list[str]:
        """``reachable``: holders whose main source is public (unit holders always are), so an asker outside the owner's team can be answered
        from them; ``hidden``: user holders whose source is visible to the owner's team only (replacement allowed when too few)."""
        pool = [h for h in self.dept_holders(t, dept) if h not in set(avoid)]
        if kind == "hidden":
            hid = [h for h in pool if t.holders[h].owner_type == "user" and t.holders[h].vis == "members"]
            if not hid:
                raise RuntimeError(f"department {dept} has no members-only holder")
            self.rng.shuffle(hid)
            return [hid[i % len(hid)] for i in range(k)]
        pool = [h for h in pool if t.holders[h].vis == "public"]
        users = [h for h in pool if t.holders[h].owner_type == "user"]
        units = [h for h in pool if t.holders[h].owner_type == "unit"]
        self.rng.shuffle(users)
        self.rng.shuffle(units)
        chosen: list[str] = []
        # alternate unit-holder / user-holder so both kinds of org-wide and personal sources carry observations
        order = []
        for i in range(max(len(users), len(units))):
            if i < len(units) and (i % 2 == 0 or not users):
                order.append(units[i])
            if i < len(users):
                order.append(users[i])
            if i < len(units) and i % 2 == 1:
                order.append(units[i])
        for h in order:
            if h not in chosen:
                chosen.append(h)
            if len(chosen) == k:
                break
        if len(chosen) < k:
            raise RuntimeError(f"department {dept} has fewer than {k} reachable holders")
        return chosen

    def author_for(self, t: TenantSpec, holder: str, dept: str) -> str:
        h = t.holders[holder]
        if h.owner_type == "user":
            return h.owner_key
        people = [u.key for u in t.users.values() if u.dept == dept and not any(r == "department_lead" for _, r in u.memberships)]
        return self.rng.choice(people)

    def pair(self, t: TenantSpec, *, same_family: bool = False, exclude_keys: Iterable[str] = ()) -> tuple[UnitSpec, UnitSpec]:
        ds = [d for d in t.depts() if d.key not in set(exclude_keys)]
        for _ in range(200):
            a, b = self.rng.sample(ds, 2)
            if same_family and (a.domain or "").split(".")[0] != (b.domain or "").split(".")[0]:
                continue
            return a, b
        return ds[0], ds[1]

    def scope_and_asker(self, t: TenantSpec, a: str, b: str) -> tuple[str, str]:
        """The lowest unit containing both departments and its lead (or the administrator for the whole organization)."""
        anc_a = [a] + t.ancestors(a)
        common = next(u for u in anc_a if u in set([b] + t.ancestors(b)))
        lead = {"subsidiary": "subsidiary_lead", "region": "regional_lead"}.get(t.units[common].type)
        if lead:
            for u in t.users.values():
                if (common, lead) in u.memberships:
                    return common, u.key
        return f"t{t.idx}-root", t.admin

    # ---- record builders
    def obs_text(self, ti: int, svc: str, ctx: str, n: int, tmpl_i: int, templates: tuple[str, ...] | None = None) -> str:
        tpls = templates or self.bank.obs_templates
        return tpls[tmpl_i % len(tpls)].format(svc=svc, ctx=ctx, n=n)

    def add_obs(self, t: TenantSpec, holder: str, dept: str, svc: str, ctx: str, n: int, tmpl_i: int, *, pattern: str, days: float,
                templates: tuple[str, ...] | None = None, source: str = "pub", members: list[str] | None = None, role: str = "obs",
                rid: str | None = None) -> Rec:
        r = Rec(rid or self.rid(t.idx, "o"), t.idx, holder, source, self.obs_text(t.idx, svc, ctx, n, tmpl_i, templates), "Field note",
                self.author_for(t, holder, dept), days, pattern=pattern, role=role, members=list(members or []))
        self.records.append(r)
        return r

    def options_for(self, ti: int, gold_svc: str | None, extra: Iterable[str] = ()) -> tuple[list[dict[str, Any]], list[str]]:
        self._last_opt_args = (ti, gold_svc, tuple(extra))
        pool = [e for e in self.entities[ti] if e != gold_svc and e not in set(extra)]
        self.rng.shuffle(pool)
        chosen = list(extra) + pool[: 3 - len(list(extra))] if gold_svc else (list(extra) + pool)[:4]
        opts = ([gold_svc] if gold_svc else []) + chosen
        opts = opts[:4]
        self.rng.shuffle(opts)
        return [_option(o) for o in opts], [svc_label(o) for o in opts if o != gold_svc]

    def make_public(self, t: TenantSpec, a: UnitSpec, b: UnitSpec, ctx: str, options: list[dict[str, Any]], *, current: bool = False,
                    scope: str | None = None, asker: str | None = None) -> TaskPublic:
        scope, asker = (scope, asker) if scope else self.scope_and_asker(t, a.key, b.key)
        tpls = self.bank.current_question_templates if current else self.bank.question_templates
        q = tpls[self.rng.randrange(len(tpls))].format(ctx=ctx, a=a.name, b=b.name)
        gt, go = self.bank.goal_templates[self.rng.randrange(len(self.bank.goal_templates))]
        doms = [a.domain, b.domain]
        return TaskPublic(task_id="", split=self.split, tenant=t.slug, asker=t.users[asker].email, asker_key=asker, scope_unit=scope,
                          goal_title=gt.format(ctx=ctx, a=a.name, b=b.name), goal_objective=go.format(ctx=ctx, a=a.name, b=b.name),
                          goal_domains=[d for d in dict.fromkeys(doms) if d], question_text=q, candidate_domains=[d for d in dict.fromkeys(doms) if d],
                          options=options, policy={"min_independent_units": {"department": 2}}, valid_from_days=120 if current else None)

    # ---- classes
    def cross_domain(self, ti: int, *, goal_only: bool = False, fault: str | None = None, n_obs: int = 3) -> TaskSpec:
        t = self.w.tenants[ti]
        svc, n = self.svc(ti), self.number()
        ctx = self.ctx(ti)
        pid = f"p{len(self.specs)}"
        if goal_only:
            return self.goal_only_task(ti, svc, n, ctx, pid)
        a, b = self.pair(t)
        ha = self.pick_holders(t, a.key, 2 if n_obs >= 3 else 1)
        hb = self.pick_holders(t, b.key, n_obs - len(ha))
        tmpl = self.rng.sample(range(len(self.bank.obs_templates)), len(ha) + len(hb))
        recs = []
        for i, (h, d) in enumerate([(h, a.key) for h in ha] + [(h, b.key) for h in hb]):
            recs.append(self.add_obs(t, h, d, svc, ctx, n, tmpl[i], pattern=pid, days=self.rng.uniform(5, 90)))
        if not fault and self.rng.random() < 0.4:
            # a further consistent observation in a source only the owner's team may read: the asker cannot reach it, so it never
            # counts in the gold; it makes "a members-only holder states the pattern" occur in positives as well as in denied tasks
            hd = self.rng.choice([a.key, b.key])
            if any(t.holders[h].owner_type == "user" and t.holders[h].vis == "members" for h in self.dept_holders(t, hd)):
                self.add_obs(t, self.pick_holders(t, hd, 1, kind="hidden")[0], hd, svc, ctx, n, self.rng.randrange(len(self.bank.obs_templates)),
                             pattern=pid, days=self.rng.uniform(5, 90), role="hidden_obs")
        options, decoys = self.options_for(ti, svc)
        pub = self.make_public(t, a, b, ctx, options)
        spec = TaskSpec(pub, "fault" if fault else "cross_domain", svc_label(svc), f"service:{svc}", len(recs), sorted({r.holder for r in recs}), [a.key, b.key], decoys,
                        fault=fault)
        if fault:
            self.apply_fault(t, spec, recs, svc, ctx, n, fault)
        return spec

    def goal_only_task(self, ti: int, svc: str, n: int, ctx: str, pid: str) -> TaskSpec:
        """One exclusive scope (a project of five people) holds exactly ONE hidden positive pattern (3 observations, 2 departments)
        and one in-scope single-department decoy (2 observations, 1 department, its own service and context). The asker is the
        department lead of the minority department: they see the scope, hold no observation, and no other task shares the scope."""
        t = self.w.tenants[ti]
        if self.proj_next[ti] >= len(t.projects):
            raise RuntimeError("more goal-only tasks than projects")
        proj = t.projects[self.proj_next[ti]]
        self.proj_next[ti] += 1
        mn = proj.depts[0] if proj.maj == proj.depts[1] else proj.depts[1]
        mem_min = [m for m in proj.members if t.users[m].dept == mn]
        mem_maj = [m for m in proj.members if t.users[m].dept == proj.maj]
        assert len(mem_min) == 2 and len(mem_maj) == 3
        d_svc, d_n = self.svc(ti), self.other_number(n)
        d_ctx = f"{self.bank.ctx_adjectives[self.decoy_ctx_idx]} {self.bank.ctx_nouns[self.decoy_ctx_idx]}"
        tmpl = self.rng.sample(range(len(self.goal_obs)), 3)
        dtm = self.rng.sample(range(len(self.bank.goal_decoy_templates)), 2)
        pos = [(mem_min[0], mn), (mem_min[1], mn), (mem_maj[0], proj.maj)]
        recs = [self.add_obs(t, f"h-{m}", d, svc, ctx, n, tmpl[i], pattern=pid, days=self.rng.uniform(5, 90), templates=self.goal_obs)
                for i, (m, d) in enumerate(pos)]
        for i, m in enumerate(mem_maj[1:]):
            self.add_obs(t, f"h-{m}", proj.maj, d_svc, d_ctx, d_n, dtm[i], pattern=pid, days=self.rng.uniform(5, 90), templates=self.bank.goal_decoy_templates,
                         role="decoy_single_dept")
        options, decoys = self.options_for(ti, svc, extra=[d_svc])
        fam = (t.units[mn].domain or "operations").split(".")[0]
        gt, go = self.bank.goal_only_templates[self.rng.randrange(len(self.bank.goal_only_templates))]
        asker = proj.asker
        pub = TaskPublic(task_id="", split=self.split, tenant=t.slug, asker=t.users[asker].email, asker_key=asker, scope_unit=proj.key,
                         goal_title=gt.format(fam=fam, ctx=ctx), goal_objective=go.format(fam=fam, ctx=ctx), goal_domains=[fam], question_text=None,
                         candidate_domains=[], options=options, policy={"min_independent_units": {"department": 2}}, goal_only=True)
        return TaskSpec(pub, "cross_domain", svc_label(svc), f"service:{svc}", len(recs), [r.holder for r in recs], [mn, proj.maj], decoys,
                        note="goal-only; exclusive project scope with one hidden pattern and one in-scope single-department decoy")

    def apply_fault(self, t: TenantSpec, spec: TaskSpec, recs: list[Rec], svc: str, ctx: str, n: int, fault: str) -> None:
        fp = self.fault_plan
        hk = recs[0].holder
        if fault == "duplicate":
            fp.setdefault(hk, {}).setdefault("duplicate", []).append(recs[0].rid)
        elif fault == "replay":
            fp.setdefault(hk, {})["replay"] = True
        elif fault == "out_of_order":
            # the newest version of an observation is written first, an older (wrong) version of the same record later in the file
            wrong = self.rng.choice([e for e in self.entities[t.idx] if e != svc])
            old = Rec(recs[1].rid, t.idx, recs[1].holder, recs[1].source, self.obs_text(t.idx, wrong, ctx, self.other_number(n), 2), "Field note",
                      recs[1].author, recs[1].days_ago + 3, pattern=recs[1].pattern, role="stale_version")
            self.records.append(old)
            fp.setdefault(recs[1].holder, {})["out_of_order"] = True
        elif fault == "edit":
            wrong = self.rng.choice([e for e in self.entities[t.idx] if e != svc])
            orig = recs[1]
            orig.text = self.obs_text(t.idx, wrong, ctx, self.other_number(n), 4)                   # first written wrongly...
            orig.role = "wrong_before_edit"
            self.records.append(Rec(orig.rid, t.idx, orig.holder, orig.source, self.obs_text(t.idx, svc, ctx, n, 6), "Field note", orig.author,
                                    orig.days_ago, typ="edit", phase="late", pattern=orig.pattern, role="correction", version_offset_days=0.5))
        elif fault == "delete":
            extra = self.add_obs(t, self.pick_holders(t, t.units[spec.departments[0]].key, 1, avoid=[r.holder for r in recs])[0], spec.departments[0],
                                 self.rng.choice([e for e in self.entities[t.idx] if e != svc]), ctx, self.other_number(n), 5, pattern=recs[0].pattern,
                                 days=self.rng.uniform(5, 60), role="retracted")
            self.records.append(Rec(extra.rid, t.idx, extra.holder, extra.source, "", "", extra.author, extra.days_ago, typ="delete", phase="late",
                                    pattern=extra.pattern, role="deletion", version_offset_days=1.0))
        elif fault == "missing_metadata":
            for r in recs[:3]:
                r.missing_meta = True
        elif fault == "malformed":
            fp.setdefault(hk, {})["malformed"] = True
        elif fault == "restart_mid_ingest":
            fp.setdefault(hk, {})["restart"] = True
        spec.note = f"fault={fault}"

    def contradiction(self, ti: int, variant: str) -> TaskSpec:
        t = self.w.tenants[ti]
        keep, drop = self.svc(ti), self.svc(ti)
        n1 = self.number()
        n2 = self.other_number(n1)
        ctx = self.ctx(ti)
        a, b = self.pair(t)
        pid = f"p{len(self.specs)}"
        ha, hb = self.pick_holders(t, a.key, 2), self.pick_holders(t, b.key, 2)
        tmpl = self.rng.sample(range(len(self.bank.obs_templates)), 5)
        genuine = [self.add_obs(t, ha[0], a.key, keep, ctx, n1, tmpl[0], pattern=pid, days=self.rng.uniform(5, 80)),
                   self.add_obs(t, hb[0], b.key, keep, ctx, n1, tmpl[1], pattern=pid, days=self.rng.uniform(5, 80)),
                   self.add_obs(t, ha[1], a.key, keep, ctx, n1, tmpl[2], pattern=pid, days=self.rng.uniform(5, 80))]
        bad = [self.add_obs(t, hb[1], b.key, drop, ctx, n2, tmpl[3], pattern=pid, days=self.rng.uniform(5, 80), role="retracted"),
               self.add_obs(t, self.pick_holders(t, b.key, 1, avoid=[hb[0], hb[1]])[0], b.key, drop, ctx, n2, tmpl[4], pattern=pid,
                            days=self.rng.uniform(5, 80), role="retracted")]
        roots = 3
        for r in bad:
            if variant == "delete":
                self.records.append(Rec(r.rid, t.idx, r.holder, r.source, "", "", r.author, r.days_ago, typ="delete", phase="late", pattern=pid,
                                        role="deletion", version_offset_days=1.0))
            else:                                                        # the authors correct their records to the surviving entity
                self.records.append(Rec(r.rid, t.idx, r.holder, r.source, self.obs_text(ti, keep, ctx, n1, (tmpl[0] + 3 + len(self.records)) % 8), "Field note",
                                        r.author, r.days_ago, typ="edit", phase="late", pattern=pid, role="correction", version_offset_days=1.0))
                roots += 1
        options, decoys = self.options_for(ti, keep, extra=[drop])
        pub = self.make_public(t, a, b, ctx, options)
        return TaskSpec(pub, "contradiction", svc_label(keep), f"service:{keep}", roots, sorted({r.holder for r in genuine}), [a.key, b.key], decoys,
                        note=f"retraction by {variant}")

    def temporal(self, ti: int) -> TaskSpec:
        t = self.w.tenants[ti]
        old, new = self.svc(ti), self.svc(ti)
        n_old = self.number()
        n_new = self.other_number(n_old)
        ctx = self.ctx(ti)
        a, b = self.pair(t)
        pid = f"p{len(self.specs)}"
        ha, hb = self.pick_holders(t, a.key, 2), self.pick_holders(t, b.key, 2)
        tmpl = self.rng.sample(range(len(self.bank.obs_templates)), 6)
        for i, (h, d) in enumerate([(ha[0], a.key), (hb[0], b.key), (ha[1], a.key)]):
            self.add_obs(t, h, d, old, ctx, n_old, tmpl[i], pattern=pid, days=self.rng.uniform(200, 260), role="superseded")
        genuine = [self.add_obs(t, h, d, new, ctx, n_new, tmpl[3 + i], pattern=pid, days=self.rng.uniform(8, 40))
                   for i, (h, d) in enumerate([(hb[1], b.key), (ha[1], a.key), (self.pick_holders(t, b.key, 1, avoid=[hb[0], hb[1]])[0], b.key)])]
        options, decoys = self.options_for(ti, new, extra=[old])
        pub = self.make_public(t, a, b, ctx, options, current=True)
        return TaskSpec(pub, "temporal", svc_label(new), f"service:{new}", 3, sorted({r.holder for r in genuine}), [a.key, b.key], decoys,
                        note="newer finding supersedes an older one; question carries a validity window")

    def common_origin(self, ti: int, copies_only: bool) -> TaskSpec:
        t = self.w.tenants[ti]
        svc, n = self.svc(ti), self.number()
        ctx = self.ctx(ti)
        a, b = self.pair(t)
        others = [d for d in t.depts() if d.key not in (a.key, b.key)]
        c = self.rng.choice(others) if others else b
        pid = f"p{len(self.specs)}"
        tmpl = self.rng.sample(range(len(self.bank.obs_templates)), 3)
        ha, hb = self.pick_holders(t, a.key, 1)[0], self.pick_holders(t, b.key, 1)[0]
        hc = self.pick_holders(t, c.key, 1, avoid=[ha, hb])[0]
        orig = self.add_obs(t, ha, a.key, svc, ctx, n, tmpl[0], pattern=pid, days=self.rng.uniform(10, 80))

        # half of the copies-only decoys are forwards that add the forwarder's own commentary (expected: still one root)
        commentary = copies_only and (len([s for s in self.specs if s.cls == "common_origin_copies"]) % 2 == 0)

        def copy(holder: str, dept: str) -> Rec:
            r = Rec(self.rid(ti, "c"), ti, holder, "pub", orig.text, "Fwd: field note", self.author_for(t, holder, dept), orig.days_ago - 1, forwarded=True,
                    forward_of=orig.rid, pattern=pid, role="copy_with_comment" if commentary else "copy",
                    comment=self.rng.choice(COMMENTS) if commentary else "")
            self.records.append(r)
            return r
        if copies_only:
            copy(hb, b.key)
            copy(hc, c.key)
            roots, answer, ent, genuine = 1, "abstain", None, [orig]
            options, decoys = self.options_for(ti, None, extra=[svc])
            decoys = [svc_label(svc)] + [d for d in decoys if d != svc_label(svc)]
        else:
            second = self.add_obs(t, hb, b.key, svc, ctx, n, tmpl[1], pattern=pid, days=self.rng.uniform(10, 80))
            copy(hc, c.key)
            roots, answer, ent, genuine = 2, svc_label(svc), f"service:{svc}", [orig, second]
            options, decoys = self.options_for(ti, svc)
        pub = self.make_public(t, a, b, ctx, options)
        return TaskSpec(pub, "common_origin_copies" if copies_only else "common_origin_pos", answer, ent, roots, sorted({r.holder for r in genuine}), [a.key, b.key],
                        decoys, note="forwarded copies share the original's root")

    def coincidence(self, ti: int) -> TaskSpec:
        t = self.w.tenants[ti]
        svc, n = self.svc(ti), self.number()
        ctx = self.ctx(ti)
        a, b = self.pair(t)
        others = [d for d in t.depts() if d.key not in (a.key, b.key)]
        c = self.rng.choice(others) if others else b
        pid = f"p{len(self.specs)}"
        genuine = self.add_obs(t, self.pick_holders(t, a.key, 1)[0], a.key, svc, ctx, n, self.rng.randrange(8), pattern=pid, days=self.rng.uniform(5, 80))
        for k, d in enumerate([b.key, c.key, a.key]):
            h = self.pick_holders(t, d, 1, avoid=[genuine.holder])[0]
            txt = self.bank.decoy_templates[k % len(self.bank.decoy_templates)].format(svc=svc, n=self.number())
            self.records.append(Rec(self.rid(ti, "d"), ti, h, "pub", txt, "Housekeeping", self.author_for(t, h, d), self.rng.uniform(5, 80), pattern=pid, role="decoy"))
        options, decoys = self.options_for(ti, None, extra=[svc])
        decoys = [svc_label(svc)] + [d for d in decoys if d != svc_label(svc)]
        pub = self.make_public(t, a, b, ctx, options)
        return TaskSpec(pub, "coincidence", "abstain", None, None, [genuine.holder], [a.key, b.key], decoys,
                        note="the entity is named in unrelated contexts; one genuine observation for this context")

    def single_domain(self, ti: int) -> TaskSpec:
        t = self.w.tenants[ti]
        svc, n = self.svc(ti), self.number()
        ctx = self.ctx(ti)
        a, b = self.pair(t)
        pid = f"p{len(self.specs)}"
        hs = self.pick_holders(t, a.key, 3)
        tmpl = self.rng.sample(range(len(self.bank.obs_templates)), 3)
        recs = [self.add_obs(t, h, a.key, svc, ctx, n, tmpl[i], pattern=pid, days=self.rng.uniform(5, 80)) for i, h in enumerate(hs)]
        options, decoys = self.options_for(ti, None, extra=[svc])
        decoys = [svc_label(svc)] + [d for d in decoys if d != svc_label(svc)]
        pub = self.make_public(t, a, b, ctx, options)
        return TaskSpec(pub, "single_domain", "abstain", None, None, sorted({r.holder for r in recs}), [a.key, b.key], decoys,
                        note="three independent roots, one department; the question requires two")

    def denied(self, ti: int) -> TaskSpec:
        """The pattern is stated only by holders whose personal source is visible to their own team: the asker is outside every team."""
        t = self.w.tenants[ti]
        svc, n = self.svc(ti), self.number()
        ctx = self.ctx(ti)
        a, b = self.pair(t)
        pid = f"p{len(self.specs)}"
        ha, hb = self.pick_holders(t, a.key, 2, kind="hidden"), self.pick_holders(t, b.key, 1, kind="hidden")
        tmpl = self.rng.sample(range(len(self.bank.obs_templates)), 3)
        recs = [self.add_obs(t, h, d, svc, ctx, n, tmpl[i], pattern=pid, days=self.rng.uniform(5, 80), role="restricted")
                for i, (h, d) in enumerate([(ha[0], a.key), (ha[1], a.key), (hb[0], b.key)])]
        options, decoys = self.options_for(ti, None, extra=[svc])
        decoys = [svc_label(svc)] + [d for d in decoys if d != svc_label(svc)]
        pub = self.make_public(t, a, b, ctx, options)
        return TaskSpec(pub, "denied", "abstain", None, None, [], [a.key, b.key], decoys, forbidden_holders=sorted({r.holder for r in recs}),
                        forbidden_markers=[svc_label(svc), f"service:{svc}"], forbidden_texts=[r.text for r in recs],
                        note="the pattern is stated only in sources the asker may not read (members-only)")

    def cross_tenant(self, ti_asker: int) -> TaskSpec:
        tb = self.w.tenants[1 - ti_asker]
        ta = self.w.tenants[ti_asker]
        svc, n = self.svc(tb.idx), self.number()
        ctx = self.ctx(tb.idx, reserved=True)
        pid = f"p{len(self.specs)}"
        a, b = self.pair(tb)
        ha, hb = self.pick_holders(tb, a.key, 2), self.pick_holders(tb, b.key, 1)
        tmpl = self.rng.sample(range(len(self.bank.obs_templates)), 3)
        recs = [self.add_obs(tb, h, d, svc, ctx, n, tmpl[i], pattern=pid, days=self.rng.uniform(5, 80)) for i, (h, d) in enumerate([(ha[0], a.key), (ha[1], a.key), (hb[0], b.key)])]
        # the asker lives in the other tenant: the question names that tenant's own departments
        aa, ab = self.pair(ta)
        options, decoys = self.options_for(ti_asker, None, extra=[svc])        # distractors are real services of the ASKER's tenant
        decoys = [svc_label(svc)] + [d for d in decoys if d != svc_label(svc)]
        pub = self.make_public(ta, aa, ab, ctx, options)
        return TaskSpec(pub, "cross_tenant", "abstain", None, None, [], [aa.key, ab.key], decoys, forbidden_holders=sorted({r.holder for r in recs}),
                        forbidden_markers=[svc_label(svc), f"service:{svc}"], forbidden_texts=[r.text for r in recs],
                        note="the pattern exists only in the other tenant")

    # ---- the whole task set
    def build(self) -> Plan:
        r = self.rng
        nten = len(self.w.tenants)
        jobs: list[tuple[str, dict[str, Any]]] = []
        for k in range(40):
            jobs.append(("cross_domain", {"goal_only": k < GOAL_ONLY}))
        jobs += [("contradiction", {"variant": "delete" if k % 2 == 0 else "edit"}) for k in range(10)]
        jobs += [("temporal", {})] * 10
        jobs += [("common_origin_pos", {})] * 8 + [("common_origin_copies", {})] * 7
        jobs += [("coincidence", {})] * 10 + [("single_domain", {})] * 10 + [("denied", {})] * 10 + [("cross_tenant", {})] * 5
        jobs += [("fault", {"fault": f}) for f in FAULT_KINDS]
        assert len(jobs) == 120
        # tenants alternate within each class so both worlds see every class; goal-only tasks fill each tenant's five projects
        counters: dict[str, int] = {}
        for cls, kw in jobs:
            i = counters.get(cls, 0)
            counters[cls] = i + 1
            ti = i % nten
            if cls == "cross_domain":
                spec = self.cross_domain(ti, **kw)
            elif cls == "contradiction":
                spec = self.contradiction(ti, **kw)
            elif cls == "temporal":
                spec = self.temporal(ti)
            elif cls == "common_origin_pos":
                spec = self.common_origin(ti, False)
            elif cls == "common_origin_copies":
                spec = self.common_origin(ti, True)
            elif cls == "coincidence":
                spec = self.coincidence(ti)
            elif cls == "single_domain":
                spec = self.single_domain(ti)
            elif cls == "denied":
                spec = self.denied(ti)
            elif cls == "cross_tenant":
                spec = self.cross_tenant(ti)
            else:
                spec = self.cross_domain(ti, n_obs=4, **kw)
            spec.opt_args = getattr(self, "_last_opt_args", None)
            spec.pattern = f"p{len(self.specs)}"
            self.specs.append(spec)
        # option sets are drawn after every entity of the tenant exists (distractors are real services of the same world)
        for spec in self.specs:
            if spec.opt_args:
                ti, g, extra = spec.opt_args
                opts, _ = self.options_for(ti, g, extra)
                spec.public.options = opts
                spec.decoy_options = [o["label"] for o in opts if o["label"] != spec.answer]
        # opaque, shuffled ids: nothing in a public record tells the class
        order = list(range(len(self.specs)))
        r.shuffle(order)
        for pos, idx in enumerate(order):
            self.specs[idx].public.task_id = f"{self.split}-{pos:03d}"
        self.specs.sort(key=lambda s: s.public.task_id)
        self.add_filler()
        return Plan(self.split, self.seed, self.specs, self.records, self.fault_plan, self.w,
                    [_option(e) for e in sorted({e for lst in self.entities for e in lst})])         # same dict form as the options, so one entity has one key

    def add_filler(self) -> None:
        """Every holder that carries content gets unrelated notes, so its source domain reaches the publication threshold (5 records)
        and retrieval has distractors; plus a private notebook for some user holders (never exportable)."""
        per_holder: dict[str, list[Rec]] = {}
        for rec in self.records:
            if rec.typ == "document" and rec.source == "pub":
                per_holder.setdefault(rec.holder, []).append(rec)
        for ti, t in enumerate(self.w.tenants):
            for hk in sorted(h for h in per_holder if h in t.holders):
                have = len(per_holder[hk])
                dept = t.holders[hk].dept or ""
                for k in range(max(0, 6 - have) + self.rng.randint(0, 2)):
                    txt = self.bank.filler_templates[self.rng.randrange(len(self.bank.filler_templates))]
                    self.records.append(Rec(self.rid(ti, "f"), ti, hk, "pub", txt, "Notice", self.author_for(t, hk, dept), self.rng.uniform(3, 120), role="filler"))
                if t.holders[hk].owner_type == "user" and self.rng.random() < 0.5:
                    self.records.append(Rec(self.rid(ti, "n"), ti, hk, "priv", self.bank.filler_templates[self.rng.randrange(len(self.bank.filler_templates))], "Personal note",
                                            t.holders[hk].owner_key, self.rng.uniform(3, 120), role="filler",
                                            members=[t.holders[hk].owner_key]))


def plan(world: World, bank: TemplateBank, split: str, seed: int, goal_obs: tuple[str, ...]) -> Plan:
    return Planner(world, bank, split, seed, goal_obs).build()


def load_bank(split: str) -> tuple[TemplateBank, tuple[str, ...]]:
    """The dev bank is a normal import; the holdout bank is loaded only here, only when asked for (``--split holdout``)."""
    import importlib
    if split == "dev":
        m = importlib.import_module("research.mycelic_e2e.bench.templates_dev")
    elif split == "holdout":
        # The holdout bank is sealed OUTSIDE the repository tree (overnight run: /root/sealed_holdout/, sha256 recorded
        # in BENCHMARK_CONTRACT.md) so implementation agents cannot read it; it is loaded only from this explicit path.
        import importlib.util
        import os
        path = os.environ.get("MYCELIC_E2E_HOLDOUT_BANK")
        if not path:
            raise RuntimeError("holdout bank is sealed: set MYCELIC_E2E_HOLDOUT_BANK to the sealed templates_holdout.py")
        importlib.import_module("research.mycelic_e2e.bench.holdout")      # parent package, for the bank's relative import
        spec = importlib.util.spec_from_file_location("research.mycelic_e2e.bench.holdout.templates_holdout", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    else:
        raise ValueError(split)
    return m.BANK, m.GOAL_OBS_TEMPLATES
