"""A faithful offline GitHub REST API (INGESTION.md §11.4) for tests and demos.

Start it with :func:`start_github_mock`::

    url, gh = await start_github_mock(load_fixture("github_acme"), clock=FakeClock(...))
    # config for the connector: {"api_base": url}; OAuth: OAuthAppConfig(client_id, Secret(secret), oauth_base=url,
    # allow_loopback_http=True); pipeline: IngestPipeline(..., http_factory=loopback_http_factory(clock))
    ...
    await gh.close()

Behaviour modelled on GitHub's documentation and OpenAPI description (``fixtures/github_openapi_subset.json`` lists the
required keys every response here is checked against):

* ``GET /user``, ``/user/repos``, ``/orgs/{org}/repos``, ``/installation/repositories``, ``/repos/{o}/{r}``,
  ``/repositories/{id}``, ``/repos/{o}/{r}/issues`` (``state`` defaults to ``open``; ``since`` filters ``updated_at >=``;
  ``sort``/``direction``), ``/repos/{o}/{r}/issues/{n}`` (301 transferred, 410 deleted, 404 hidden),
  ``/repos/{o}/{r}/issues/comments`` (+ ``/{id}``: 404 when deleted), ``/repos/{o}/{r}/issues/{n}/comments``,
  ``/rate_limit``; the same repository routes under ``/repositories/{id}/...``, which is where ``Link`` headers point.
* ``per_page`` clamped to 100 without an error; ``Link`` with absolute ``next``/``prev``/``first``/``last`` URLs;
  ``ETag: W/"<sha1>"`` and ``If-None-Match`` → 304 that does not count against the primary limit;
  ``x-ratelimit-limit/-remaining/-used/-reset/-resource`` on every response; ``X-OAuth-Scopes`` for classic and OAuth
  tokens only (fine-grained and installation tokens report none); 401 ``Bad credentials``; 403 ``Resource not accessible``
  for a fine-grained token without the Issues permission; unsupported ``X-GitHub-Api-Version`` → 400.
* Access: public repositories are readable by every token; private ones by classic/OAuth tokens with ``repo`` whose user
  collaborates, by fine-grained tokens that selected them, and by installation tokens that include them.
* Faults: :meth:`fail_next`, :meth:`secondary_limit`, :meth:`exhaust_primary`, :meth:`revoke_token`, :meth:`expire_token`,
  :meth:`crash_after_pages`, :meth:`hide_repo`. Assertions: ``requests`` (method, path, query, token hash, concurrency).
* Mutations return the webhook payload GitHub would deliver; :meth:`signed_delivery` / :meth:`send_webhook` sign it with
  ``X-Hub-Signature-256`` (``tamper=True`` flips one body byte after signing).
* OAuth: ``GET /login/oauth/authorize`` (auto-approves as the fixture's ``app.authorizing_user``, 302 to the redirect URI)
  and ``POST /login/oauth/access_token`` (code + PKCE ``S256``, refresh for expiring GitHub App user tokens).
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import secrets
import uuid
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp
from aiohttp import web

from .common import FakeClock, MockProvider, iso_z, load_fixture, parse_iso

SUPPORTED_VERSIONS = ("2022-11-28", "2026-03-10")
DOCS = "https://docs.github.com/rest"
SECONDARY_MESSAGE = ("You have exceeded a secondary rate limit. Please wait a few minutes before you try again. If you reach out to GitHub "
                     "Support for help, please include the request ID.")
_URL_KEYS = ("archive_url", "assignees_url", "blobs_url", "branches_url", "collaborators_url", "comments_url", "commits_url", "compare_url",
             "contents_url", "contributors_url", "deployments_url", "downloads_url", "events_url", "forks_url", "git_commits_url", "git_refs_url",
             "git_tags_url", "hooks_url", "issue_comment_url", "issue_events_url", "issues_url", "keys_url", "labels_url", "languages_url",
             "merges_url", "milestones_url", "notifications_url", "pulls_url", "releases_url", "stargazers_url", "statuses_url", "subscribers_url",
             "subscription_url", "tags_url", "teams_url", "trees_url")


async def start_github_mock(fixture: Mapping[str, Any] | None = None, *, clock: Callable[[], float] | None = None, latency: float = 0.0,
                            host: str = "127.0.0.1", port: int = 0) -> tuple[str, "GitHubMock"]:
    """Start a mock on loopback; returns ``(base_url, controller)``. ``fixture`` defaults to ``github_acme``."""
    mock = GitHubMock(fixture if fixture is not None else load_fixture("github_acme"), clock=clock, latency=latency)
    return await mock.start(host, port), mock


class GitHubMock(MockProvider):
    name = "github"

    def __init__(self, fixture: Mapping[str, Any], *, clock: Callable[[], float] | None = None, latency: float = 0.0, rate_limit: int = 5000) -> None:
        fx = copy.deepcopy(dict(fixture))
        self.fixture_now = parse_iso(fx.get("now") or "2026-10-01T12:00:00Z")
        self.app_config = dict(fx.get("app") or {})
        self.users: dict[str, dict[str, Any]] = {u["login"]: dict(u) for u in fx.get("users") or []}
        for o in fx.get("orgs") or []:
            self.users[o["login"]] = {**o, "type": "Organization"}
        self.tokens: dict[str, dict[str, Any]] = {t["token"]: dict(t) for t in fx.get("tokens") or []}
        self.repos: dict[str, dict[str, Any]] = {}
        for r in fx.get("repos") or []:
            r = dict(r)
            r["full_name"] = f"{r['owner']}/{r['name']}"
            r.setdefault("collaborators", [])
            r.setdefault("has_issues", True)
            r["hidden_from"] = set()
            self.repos[r["full_name"]] = r
        self.issues: dict[tuple[str, int], dict[str, Any]] = {}
        for i in fx.get("issues") or []:
            self.issues[(i["repo"], int(i["number"]))] = dict(i)
        self.comments: dict[int, dict[str, Any]] = {int(c["id"]): dict(c) for c in fx.get("comments") or []}
        self.rate_limit = int(rate_limit)
        self._remaining: dict[str, int] = {}
        self._reset: dict[str, float] = {}
        self._secondary: dict[str, Any] | None = None
        self._codes: dict[str, dict[str, Any]] = {}
        self._ids = max([*(int(i.get("id") or 0) for i in self.issues.values()), *self.comments, 900000]) + 1
        super().__init__(clock=clock, latency=latency)

    # ------------------------------------------------------------------ routes
    def _routes(self, app: web.Application) -> None:
        r = app.router
        r.add_get("/user", self.h_user)
        r.add_get("/user/repos", self.h_user_repos)
        r.add_get("/orgs/{org}/repos", self.h_org_repos)
        r.add_get("/installation/repositories", self.h_installation_repos)
        r.add_get("/rate_limit", self.h_rate_limit)
        for prefix in ("/repos/{owner}/{repo}", "/repositories/{repo_id:\\d+}"):
            r.add_get(prefix, self.h_repo)
            r.add_get(prefix + "/issues", self.h_issues)
            r.add_get(prefix + "/issues/comments", self.h_comments)
            r.add_get(prefix + "/issues/comments/{comment_id:\\d+}", self.h_comment)
            r.add_get(prefix + "/issues/{number:\\d+}", self.h_issue)
            r.add_get(prefix + "/issues/{number:\\d+}/comments", self.h_issue_comments)
        r.add_get("/login/oauth/authorize", self.h_authorize)
        r.add_post("/login/oauth/access_token", self.h_access_token)

    # ------------------------------------------------------------------ fault injection
    def secondary_limit(self, *, after: int = 0, retry_after: int | None = 1, count: int = 1, status: int = 403) -> None:
        """After ``after`` more API requests, the next ``count`` answer a secondary rate limit (403 or 429, ``retry-after``)."""
        self._secondary = {"after": int(after), "retry_after": retry_after, "count": int(count), "status": int(status)}

    def exhaust_primary(self, token: str | None = None, *, reset_in: float = 2.0) -> None:
        """Primary limit at zero for ``token`` (every known token when None) until ``reset_in`` seconds from now."""
        for t in [token] if token else list(self.tokens):
            self._remaining[t] = 0
            self._reset[t] = self.clock() + float(reset_in)

    def revoke_token(self, token: str) -> None:
        self.tokens[token]["revoked"] = True

    def expire_token(self, token: str) -> None:
        self.tokens[token]["expired"] = True

    def hide_repo(self, full_name: str, token: str | None = None) -> None:
        """The repository becomes unreadable (404) for ``token`` (every token when None): access lost, not deleted."""
        self.repos[full_name]["hidden_from"].add(token or "*")

    def restore_repo(self, full_name: str) -> None:
        self.repos[full_name]["hidden_from"] = set()

    # ------------------------------------------------------------------ JSON builders (shapes of the REST API)
    @property
    def api(self) -> str:
        return self.url

    def simple_user(self, login: str) -> dict[str, Any]:
        u = self.users.get(login) or {"login": login, "id": 0, "type": "User"}
        api, l = self.api, u["login"]
        return {"login": l, "id": int(u["id"]), "node_id": _node("U", u["id"]), "avatar_url": f"https://avatars.githubusercontent.com/u/{u['id']}?v=4",
                "gravatar_id": "", "url": f"{api}/users/{l}", "html_url": f"https://github.com/{l}", "followers_url": f"{api}/users/{l}/followers",
                "following_url": f"{api}/users/{l}/following{{/other_user}}", "gists_url": f"{api}/users/{l}/gists{{/gist_id}}",
                "starred_url": f"{api}/users/{l}/starred{{/owner}}{{/repo}}", "subscriptions_url": f"{api}/users/{l}/subscriptions",
                "organizations_url": f"{api}/users/{l}/orgs", "repos_url": f"{api}/users/{l}/repos", "events_url": f"{api}/users/{l}/events{{/privacy}}",
                "received_events_url": f"{api}/users/{l}/received_events", "type": u.get("type") or "User", "user_view_type": "public",
                "site_admin": False}

    def private_user(self, login: str) -> dict[str, Any]:
        u = self.users[login]
        own = [r for r in self.repos.values() if r["owner"] == login]
        return {**self.simple_user(login), "user_view_type": "private", "name": u.get("name"), "company": None, "blog": "", "location": None,
                "email": None, "hireable": None, "bio": None, "public_repos": sum(1 for r in own if r["visibility"] == "public"), "public_gists": 0,
                "followers": 0, "following": 0, "created_at": "2020-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
                "private_gists": 0, "total_private_repos": sum(1 for r in own if r["visibility"] != "public"),
                "owned_private_repos": sum(1 for r in own if r["visibility"] != "public"), "disk_usage": 0, "collaborators": 0,
                "two_factor_authentication": True}

    def repo_json(self, r: Mapping[str, Any], token: Mapping[str, Any] | None = None) -> dict[str, Any]:
        api, full = self.api, r["full_name"]
        base = f"{api}/repos/{full}"
        open_issues = sum(1 for (rn, _n), i in self.issues.items() if rn == full and self._listed(i) and i.get("state", "open") == "open")
        d: dict[str, Any] = {k: f"{base}/{k[:-4].replace('_', '/')}" for k in _URL_KEYS}
        d.update({"id": int(r["id"]), "node_id": _node("R", r["id"]), "name": r["name"], "full_name": full, "private": r["visibility"] != "public",
                  "owner": self.simple_user(r["owner"]), "html_url": f"https://github.com/{full}", "description": r.get("description"),
                  "fork": False, "url": base, "created_at": r.get("created_at") or "2024-01-01T00:00:00Z",
                  "updated_at": r.get("updated_at") or iso_z(self.fixture_now), "pushed_at": r.get("pushed_at") or iso_z(self.fixture_now),
                  "git_url": f"git://github.com/{full}.git", "ssh_url": f"git@github.com:{full}.git", "clone_url": f"https://github.com/{full}.git",
                  "svn_url": f"https://github.com/{full}", "homepage": None, "size": 1024, "stargazers_count": 0, "watchers_count": 0,
                  "language": r.get("language"), "has_issues": bool(r.get("has_issues", True)), "has_projects": False, "has_downloads": True,
                  "has_wiki": False, "has_pages": False, "has_discussions": False, "forks_count": 0, "mirror_url": None,
                  "archived": bool(r.get("archived")), "disabled": False, "open_issues_count": open_issues, "license": None, "forks": 0,
                  "open_issues": open_issues, "watchers": 0, "default_branch": r.get("default_branch") or "main", "visibility": r["visibility"],
                  "network_count": 0, "subscribers_count": 0, "permissions": self._permissions(r, token)})
        return d

    def _permissions(self, r: Mapping[str, Any], token: Mapping[str, Any] | None) -> dict[str, bool]:
        user = (token or {}).get("user")
        collab = user in r["collaborators"] or user == r["owner"]
        return {"admin": False, "maintain": False, "push": bool(collab and (token or {}).get("kind") == "classic"), "triage": False, "pull": True}

    def label_json(self, repo: str, name: str) -> dict[str, Any]:
        lid = int(hashlib.sha1(f"{repo}:{name}".encode()).hexdigest()[:8], 16)
        return {"id": lid, "node_id": _node("LA", lid), "url": f"{self.api}/repos/{repo}/labels/{name}", "name": name, "description": None,
                "color": "d73a4a", "default": name == "bug", "archived_at": None, "archived_by": None}

    def issue_json(self, i: Mapping[str, Any]) -> dict[str, Any]:
        repo, n = i["repo"], int(i["number"])
        base = f"{self.api}/repos/{repo}/issues/{n}"
        assignees = [self.simple_user(a) for a in i.get("assignees") or []]
        comments = sum(1 for c in self.comments.values() if c["repo"] == repo and int(c["issue"]) == n and not c.get("deleted"))
        closed = i.get("state", "open") == "closed"
        d: dict[str, Any] = {
            "id": int(i["id"]), "node_id": _node("I", i["id"]), "url": base, "repository_url": f"{self.api}/repos/{repo}",
            "labels_url": base + "/labels{/name}", "comments_url": base + "/comments", "events_url": base + "/events",
            "html_url": f"https://github.com/{repo}/{'pull' if i.get('pull_request') else 'issues'}/{n}", "number": n,
            "state": "closed" if closed else "open", "state_reason": i.get("state_reason") or ("completed" if closed else None),
            "title": i["title"], "body": i.get("body"), "user": self.simple_user(i["user"]), "labels": [self.label_json(repo, x) for x in i.get("labels") or []],
            "assignee": assignees[0] if assignees else None, "assignees": assignees, "milestone": None, "locked": False, "active_lock_reason": None,
            "comments": comments, "closed_at": i.get("closed_at") if closed else None, "created_at": i["created_at"], "updated_at": i["updated_at"],
            "author_association": "MEMBER", "closed_by": None, "timeline_url": base + "/timeline",
        }
        if i.get("pull_request"):
            d["pull_request"] = {"url": f"{self.api}/repos/{repo}/pulls/{n}", "html_url": f"https://github.com/{repo}/pull/{n}",
                                 "diff_url": f"https://github.com/{repo}/pull/{n}.diff", "patch_url": f"https://github.com/{repo}/pull/{n}.patch",
                                 "merged_at": None}
        return d

    def comment_json(self, c: Mapping[str, Any]) -> dict[str, Any]:
        repo, n, cid = c["repo"], int(c["issue"]), int(c["id"])
        return {"id": cid, "node_id": _node("IC", cid), "url": f"{self.api}/repos/{repo}/issues/comments/{cid}",
                "html_url": f"https://github.com/{repo}/issues/{n}#issuecomment-{cid}", "body": c["body"], "user": self.simple_user(c["user"]),
                "created_at": c["created_at"], "updated_at": c["updated_at"], "issue_url": f"{self.api}/repos/{repo}/issues/{n}",
                "author_association": "MEMBER"}

    # ------------------------------------------------------------------ access model
    def _token(self, request: web.Request) -> tuple[str | None, dict[str, Any] | None]:
        t = self._bearer(request)
        return t, (self.tokens.get(t) if t else None)

    def _can_read(self, token: str | None, info: Mapping[str, Any] | None, r: Mapping[str, Any]) -> bool:
        if "*" in r["hidden_from"] or (token and token in r["hidden_from"]):
            return False
        if r["visibility"] == "public":
            return True
        if not info:
            return False
        kind = info.get("kind")
        if kind in ("installation", "fine_grained"):
            return r["full_name"] in (info.get("repositories") or [])
        user = info.get("user")
        member = user in r["collaborators"] or user == r["owner"]
        if kind == "github_app_user":
            return member
        return member and "repo" in (info.get("scopes") or [])

    def _issues_allowed(self, info: Mapping[str, Any] | None, r: Mapping[str, Any]) -> bool:
        if info and info.get("kind") == "fine_grained" and r["visibility"] != "public":
            return (info.get("permissions") or {}).get("issues") in ("read", "write")
        return True

    def _repo_for(self, request: web.Request) -> dict[str, Any] | None:
        mi = request.match_info
        if "repo_id" in mi:
            return next((r for r in self.repos.values() if int(r["id"]) == int(mi["repo_id"])), None)
        return self.repos.get(f"{mi.get('owner')}/{mi.get('repo')}")

    @staticmethod
    def _listed(i: Mapping[str, Any]) -> bool:
        return not i.get("deleted") and not i.get("transferred_to")

    # ------------------------------------------------------------------ response helpers
    def _rate_headers(self, token: str | None, *, count: bool) -> dict[str, str]:
        key = token or "anonymous"
        limit = self.rate_limit if token else 60
        now = self.clock()
        if key not in self._reset or self._reset[key] <= now:
            self._reset[key] = now + 3600
            self._remaining[key] = limit
        if count and self._remaining[key] > 0:
            self._remaining[key] -= 1
        rem = self._remaining[key]
        return {"x-ratelimit-limit": str(limit), "x-ratelimit-remaining": str(rem), "x-ratelimit-used": str(limit - rem),
                "x-ratelimit-reset": str(int(self._reset[key])), "x-ratelimit-resource": "core"}

    def _json(self, request: web.Request, data: Any, *, status: int = 200, headers: Mapping[str, str] | None = None, etag: bool = True) -> web.Response:
        token, info = self._token(request)
        body = json.dumps(data).encode("utf-8")
        h = {"X-GitHub-Api-Version-Selected": request.headers.get("X-GitHub-Api-Version") or SUPPORTED_VERSIONS[0],
             "X-GitHub-Request-Id": uuid.uuid4().hex[:16].upper(), **(headers or {})}
        if info and info.get("kind") in ("classic", "oauth") and not info.get("revoked"):
            h["X-OAuth-Scopes"] = ", ".join(info.get("scopes") or [])
            h["X-Accepted-OAuth-Scopes"] = "repo"
        if status == 200 and etag:
            tag = 'W/"' + hashlib.sha1(body).hexdigest() + '"'
            h["ETag"] = tag
            inm = request.headers.get("If-None-Match", "")
            if inm and _strip_weak(inm) == _strip_weak(tag):
                h.update(self._rate_headers(token, count=False))           # conditional hits do not count against the limit
                return web.Response(status=304, headers=h)
        h.update(self._rate_headers(token, count=True))
        return web.Response(status=status, body=body, headers=h, content_type="application/json", charset="utf-8")

    def _error(self, request: web.Request, status: int, message: str, *, headers: Mapping[str, str] | None = None, **extra: Any) -> web.Response:
        return self._json(request, {"message": message, "documentation_url": DOCS, "status": str(status), **extra}, status=status,
                          headers=headers, etag=False)

    def _paginate(self, request: web.Request, items: list[Any], *, wrap: Callable[[list[Any]], Any] | None = None) -> web.Response:
        try:
            per_page = max(1, min(100, int(request.query.get("per_page", "30"))))
            page = max(1, int(request.query.get("page", "1")))
        except ValueError:
            return self._error(request, 422, "Validation Failed")
        last = max(1, -(-len(items) // per_page))
        chunk = items[(page - 1) * per_page: page * per_page]
        links = []
        # GitHub's Link URLs for repository listings use the stable /repositories/{id}/... form
        path = request.path
        repo = self._repo_for(request) if "owner" in request.match_info else None
        if repo is not None:
            path = path.replace(f"/repos/{repo['full_name']}", f"/repositories/{repo['id']}", 1)

        def link(p: int, rel: str) -> str:
            q = [(k, v) for k, v in request.query.items() if k != "page"] + [("page", str(p))]
            return f'<{self.url}{path}?{urlencode(q)}>; rel="{rel}"'
        if page < last:
            links += [link(page + 1, "next"), link(last, "last")]
        if page > 1:
            links = [link(page - 1, "prev"), link(1, "first")] + links
        return self._json(request, wrap(chunk) if wrap else chunk, headers={"Link": ", ".join(links)} if links else None)

    async def _guard(self, request: web.Request, *, needs_user: bool = False) -> web.Response | None:
        """Version check, authentication, primary and secondary limits — in GitHub's order."""
        version = request.headers.get("X-GitHub-Api-Version")
        if version and version not in SUPPORTED_VERSIONS:
            return self._error(request, 400, f"Unsupported 'X-GitHub-Api-Version' header: {version}")
        token, info = self._token(request)
        if token and (info is None or info.get("revoked") or info.get("expired")):
            return self._error(request, 401, "Bad credentials")
        if needs_user and not info:
            return self._error(request, 401, "Requires authentication")
        key = token or "anonymous"
        if self._remaining.get(key) == 0 and self._reset.get(key, 0) > self.clock():
            hdr = self._rate_headers(token, count=False)
            body = json.dumps({"message": f"API rate limit exceeded for user ID {(info or {}).get('user_id', 0)}.", "documentation_url": DOCS,
                               "status": "403"}).encode()
            return web.Response(status=403, body=body, headers=hdr, content_type="application/json", charset="utf-8")
        if self._secondary is not None:
            if self._secondary["after"] > 0:
                self._secondary["after"] -= 1
            elif self._secondary["count"] > 0:
                self._secondary["count"] -= 1
                hdr = {"retry-after": str(self._secondary["retry_after"])} if self._secondary["retry_after"] is not None else {}
                status = self._secondary["status"]
                if self._secondary["count"] <= 0:
                    self._secondary = None
                return self._error(request, status, SECONDARY_MESSAGE, headers=hdr)
        return None

    def _readable_repo(self, request: web.Request) -> tuple[dict[str, Any] | None, web.Response | None]:
        token, info = self._token(request)
        r = self._repo_for(request)
        if r is None or not self._can_read(token, info, r):
            return None, self._error(request, 404, "Not Found")
        return r, None

    # ------------------------------------------------------------------ handlers
    async def h_user(self, request: web.Request) -> web.Response:
        if (g := await self._guard(request, needs_user=True)) is not None:
            return g
        _t, info = self._token(request)
        if info.get("kind") == "installation":
            return self._error(request, 403, "Resource not accessible by integration")
        return self._json(request, self.private_user(info["user"]))

    def _visible_repos(self, request: web.Request, pred: Callable[[Mapping[str, Any]], bool]) -> list[dict[str, Any]]:
        token, info = self._token(request)
        rs = [r for r in self.repos.values() if pred(r) and self._can_read(token, info, r)]
        return [self.repo_json(r, info) for r in sorted(rs, key=lambda r: r["full_name"].lower())]

    async def h_user_repos(self, request: web.Request) -> web.Response:
        if (g := await self._guard(request, needs_user=True)) is not None:
            return g
        _t, info = self._token(request)
        user = info.get("user")
        if info.get("kind") == "fine_grained":
            pred = lambda r: r["full_name"] in (info.get("repositories") or [])        # noqa: E731
        else:
            pred = lambda r: r["owner"] == user or user in r["collaborators"]          # noqa: E731
        return self._paginate(request, self._visible_repos(request, pred))

    async def h_org_repos(self, request: web.Request) -> web.Response:
        if (g := await self._guard(request)) is not None:
            return g
        org = request.match_info["org"]
        if self.users.get(org, {}).get("type") != "Organization":
            return self._error(request, 404, "Not Found")
        return self._paginate(request, self._visible_repos(request, lambda r: r["owner"] == org))

    async def h_installation_repos(self, request: web.Request) -> web.Response:
        if (g := await self._guard(request, needs_user=True)) is not None:
            return g
        _t, info = self._token(request)
        if info.get("kind") != "installation":
            return self._error(request, 403, "You must authenticate with an installation access token in order to list repositories for an installation.")
        repos = self._visible_repos(request, lambda r: r["full_name"] in (info.get("repositories") or []))
        return self._paginate(request, repos, wrap=lambda chunk: {"total_count": len(repos), "repository_selection": "selected", "repositories": chunk})

    async def h_rate_limit(self, request: web.Request) -> web.Response:
        token, _info = self._token(request)
        core = self._rate_headers(token, count=False)
        rate = {"limit": int(core["x-ratelimit-limit"]), "remaining": int(core["x-ratelimit-remaining"]), "used": int(core["x-ratelimit-used"]),
                "reset": int(core["x-ratelimit-reset"]), "resource": "core"}
        return web.json_response({"resources": {"core": rate}, "rate": rate}, headers=core)

    async def h_repo(self, request: web.Request) -> web.Response:
        if (g := await self._guard(request)) is not None:
            return g
        r, err = self._readable_repo(request)
        if err:
            return err
        return self._json(request, self.repo_json(r, self._token(request)[1]))

    def _issue_listing_error(self, request: web.Request, r: Mapping[str, Any]) -> web.Response | None:
        if not r.get("has_issues", True):
            return self._error(request, 410, "Issues are disabled for this repo")
        if not self._issues_allowed(self._token(request)[1], r):
            return self._error(request, 403, "Resource not accessible by personal access token")
        return None

    async def h_issues(self, request: web.Request) -> web.Response:
        if (g := await self._guard(request)) is not None:
            return g
        r, err = self._readable_repo(request)
        if err:
            return err
        if (e := self._issue_listing_error(request, r)) is not None:
            return e
        q = request.query
        state, sort, direction = q.get("state", "open"), q.get("sort", "created"), q.get("direction", "desc")
        if state not in ("open", "closed", "all") or sort not in ("created", "updated", "comments") or direction not in ("asc", "desc"):
            return self._error(request, 422, "Validation Failed", errors=[{"resource": "Issue", "code": "invalid"}])
        items = [i for (repo, _n), i in self.issues.items() if repo == r["full_name"] and self._listed(i)]
        if state != "all":
            items = [i for i in items if i.get("state", "open") == state]
        if q.get("since"):
            try:
                since = parse_iso(q["since"])
            except ValueError:
                return self._error(request, 422, "Validation Failed")
            items = [i for i in items if parse_iso(i["updated_at"]) >= since]
        key = {"created": lambda i: (parse_iso(i["created_at"]), i["number"]), "updated": lambda i: (parse_iso(i["updated_at"]), i["number"]),
               "comments": lambda i: (self.issue_json(i)["comments"], i["number"])}[sort]
        items.sort(key=key, reverse=direction == "desc")
        return self._paginate(request, [self.issue_json(i) for i in items])

    async def h_issue(self, request: web.Request) -> web.Response:
        if (g := await self._guard(request)) is not None:
            return g
        r, err = self._readable_repo(request)
        if err:
            return err
        if not self._issues_allowed(self._token(request)[1], r):
            return self._error(request, 403, "Resource not accessible by personal access token")
        i = self.issues.get((r["full_name"], int(request.match_info["number"])))
        if i is None:
            return self._error(request, 404, "Not Found")
        if i.get("deleted"):
            return self._error(request, 410, "This issue was deleted")
        if i.get("transferred_to"):
            to = i["transferred_to"]
            target = self.repos[to["repo"]]
            token, info = self._token(request)
            if not self._can_read(token, info, target):
                return self._error(request, 404, "Not Found")            # transferred out of reach looks like not found
            loc = f"{self.url}/repositories/{target['id']}/issues/{to['number']}"
            return self._json(request, {"message": "Moved Permanently", "url": loc, "documentation_url": DOCS}, status=301,
                              headers={"Location": loc}, etag=False)
        return self._json(request, self.issue_json(i))

    def _comment_visible(self, c: Mapping[str, Any]) -> bool:
        if c.get("deleted"):
            return False
        i = self.issues.get((c["repo"], int(c["issue"])))
        return i is not None and self._listed(i)

    async def h_comments(self, request: web.Request) -> web.Response:
        if (g := await self._guard(request)) is not None:
            return g
        r, err = self._readable_repo(request)
        if err:
            return err
        if (e := self._issue_listing_error(request, r)) is not None:
            return e
        q = request.query
        sort = q.get("sort", "created")
        direction = q.get("direction", "asc") if "sort" in q else "asc"      # direction is ignored without sort
        if sort not in ("created", "updated") or direction not in ("asc", "desc"):
            return self._error(request, 422, "Validation Failed")
        items = [c for c in self.comments.values() if c["repo"] == r["full_name"] and self._comment_visible(c)]
        if q.get("since"):
            since = parse_iso(q["since"])
            items = [c for c in items if parse_iso(c["updated_at"]) >= since]
        field = "updated_at" if sort == "updated" else "created_at"
        items.sort(key=lambda c: (parse_iso(c[field]), int(c["id"])), reverse=direction == "desc")
        return self._paginate(request, [self.comment_json(c) for c in items])

    async def h_comment(self, request: web.Request) -> web.Response:
        if (g := await self._guard(request)) is not None:
            return g
        r, err = self._readable_repo(request)
        if err:
            return err
        c = self.comments.get(int(request.match_info["comment_id"]))
        if c is None or c["repo"] != r["full_name"] or not self._comment_visible(c):
            return self._error(request, 404, "Not Found")
        return self._json(request, self.comment_json(c))

    async def h_issue_comments(self, request: web.Request) -> web.Response:
        if (g := await self._guard(request)) is not None:
            return g
        r, err = self._readable_repo(request)
        if err:
            return err
        i = self.issues.get((r["full_name"], int(request.match_info["number"])))
        if i is None:
            return self._error(request, 404, "Not Found")
        if i.get("deleted"):
            return self._error(request, 410, "This issue was deleted")
        items = [c for c in self.comments.values() if c["repo"] == r["full_name"] and int(c["issue"]) == i["number"] and not c.get("deleted")]
        if request.query.get("since"):
            since = parse_iso(request.query["since"])
            items = [c for c in items if parse_iso(c["updated_at"]) >= since]
        items.sort(key=lambda c: int(c["id"]))
        return self._paginate(request, [self.comment_json(c) for c in items])

    # ------------------------------------------------------------------ OAuth
    async def h_authorize(self, request: web.Request) -> web.StreamResponse:
        q = request.query
        if q.get("client_id") != self.app_config.get("client_id"):
            return web.Response(status=404, text="Not Found")
        redirect = q.get("redirect_uri") or ""
        if q.get("code_challenge") and q.get("code_challenge_method", "plain") != "S256":
            return _redirect(redirect, {"error": "invalid_request", "state": q.get("state", "")})
        code = secrets.token_hex(10)
        self._codes[code] = {"user": self.app_config.get("authorizing_user") or next(iter(self.users)), "scopes": [s for s in q.get("scope", "").split() if s],
                             "redirect_uri": redirect, "challenge": q.get("code_challenge"), "expires": self.clock() + 600}
        return _redirect(redirect, {"code": code, "state": q.get("state", "")})

    async def h_access_token(self, request: web.Request) -> web.Response:
        form = {k: str(v) for k, v in (await request.post()).items()}       # cached: the middleware may have read it
        form.update({k: v for k, v in request.query.items() if k not in form})

        def out(data: Mapping[str, Any]) -> web.Response:
            if "application/json" in request.headers.get("Accept", ""):
                return web.json_response(data)
            return web.Response(text=urlencode(data), content_type="application/x-www-form-urlencoded")
        if form.get("client_id") != self.app_config.get("client_id") or form.get("client_secret") != self.app_config.get("client_secret"):
            return out({"error": "incorrect_client_credentials", "error_description": "The client_id and/or client_secret passed are incorrect."})
        expiring = bool(self.app_config.get("expiring_user_tokens"))
        if form.get("grant_type") == "refresh_token":
            old = next((t for t, i in self.tokens.items() if i.get("refresh_token") == form.get("refresh_token") and not i.get("revoked")), None)
            if old is None:
                return out({"error": "bad_refresh_token", "error_description": "The refresh token passed is incorrect or expired."})
            info = self.tokens[old]
            info["revoked"] = True
            return out(self._issue_token(info["user"], info.get("scopes") or [], expiring=True))
        grant = self._codes.pop(str(form.get("code") or ""), None)
        if grant is None or grant["expires"] < self.clock():
            return out({"error": "bad_verification_code", "error_description": "The code passed is incorrect or expired."})
        if form.get("redirect_uri") and form.get("redirect_uri") != grant["redirect_uri"]:
            return out({"error": "redirect_uri_mismatch", "error_description": "The redirect_uri MUST match the registered callback URL."})
        if grant["challenge"]:
            verifier = str(form.get("code_verifier") or "")
            digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if not verifier or not hmac.compare_digest(digest, grant["challenge"]):
                return out({"error": "bad_verification_code", "error_description": "The code_verifier does not match the code_challenge."})
        return out(self._issue_token(grant["user"], grant["scopes"], expiring=expiring))

    def _issue_token(self, user: str, scopes: list[str], *, expiring: bool) -> dict[str, Any]:
        token = ("ghu_mock" if expiring else "gho_mock") + secrets.token_hex(12)
        info: dict[str, Any] = {"token": token, "user": user, "kind": "github_app_user" if expiring else "oauth", "scopes": list(scopes)}
        data: dict[str, Any] = {"access_token": token, "token_type": "bearer", "scope": ",".join(scopes)}
        if expiring:
            info["refresh_token"] = "ghr_mock" + secrets.token_hex(16)
            data.update({"expires_in": 28800, "refresh_token": info["refresh_token"], "refresh_token_expires_in": 15897600, "scope": ""})
        self.tokens[token] = info
        return data

    # ------------------------------------------------------------------ mutations (each returns the webhook payload)
    def _now_iso(self, at: str | None) -> str:
        return at or iso_z(self.clock())

    def _new_id(self) -> int:
        self._ids += 1
        return self._ids

    def webhook_payload(self, event: str, action: str, *, repo: str, number: int | None = None, comment_id: int | None = None,
                        changes: Mapping[str, Any] | None = None, sender: str | None = None, issue: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """What GitHub delivers: full objects (titles, bodies, users) — the connector's parse_webhook must drop them."""
        r = self.repos[repo]
        sender = sender or self.app_config.get("authorizing_user") or "ana"
        payload: dict[str, Any] = {"action": action, "repository": self.repo_json(r), "sender": self.simple_user(sender)}
        if self.users.get(r["owner"], {}).get("type") == "Organization":
            payload["organization"] = {"login": r["owner"], "id": int(self.users[r["owner"]]["id"])}
        if issue is not None or number is not None:
            payload["issue"] = self.issue_json(issue if issue is not None else self.issues[(repo, int(number))])
        if comment_id is not None:
            payload["comment"] = self.comment_json(self.comments[int(comment_id)])
        if changes:
            payload["changes"] = dict(changes)
        return payload

    def add_issue(self, repo: str, *, title: str, body: str, user: str, labels: list[str] | None = None, pull_request: bool = False,
                  at: str | None = None) -> dict[str, Any]:
        n = max([num for (rn, num) in self.issues if rn == repo] or [0]) + 1
        now = self._now_iso(at)
        self.issues[(repo, n)] = {"repo": repo, "id": self._new_id(), "number": n, "title": title, "body": body, "user": user, "state": "open",
                                  "labels": list(labels or []), "created_at": now, "updated_at": now, "pull_request": pull_request}
        return self.webhook_payload("issues", "opened", repo=repo, number=n)

    def edit_issue(self, repo: str, number: int, *, title: str | None = None, body: str | None = None, labels: list[str] | None = None,
                   state: str | None = None, at: str | None = None) -> dict[str, Any]:
        i = self.issues[(repo, int(number))]
        changes: dict[str, Any] = {}
        if title is not None:
            changes["title"] = {"from": i["title"]}
            i["title"] = title
        if body is not None:
            changes["body"] = {"from": i.get("body") or ""}
            i["body"] = body
        if labels is not None:
            i["labels"] = list(labels)
        if state is not None:
            i["state"] = state
            i["closed_at"] = self._now_iso(at) if state == "closed" else None
        i["updated_at"] = self._now_iso(at)
        action = "edited" if changes else ("closed" if state == "closed" else "reopened" if state == "open" else "labeled")
        return self.webhook_payload("issues", action, repo=repo, number=number, changes=changes or None)

    def delete_issue(self, repo: str, number: int) -> dict[str, Any]:
        payload = self.webhook_payload("issues", "deleted", repo=repo, number=number)
        self.issues[(repo, int(number))]["deleted"] = True
        return payload

    def transfer_issue(self, repo: str, number: int, to_repo: str, *, at: str | None = None) -> dict[str, Any]:
        old = self.issues[(repo, int(number))]
        n = max([num for (rn, num) in self.issues if rn == to_repo] or [0]) + 1
        new = {**{k: v for k, v in old.items() if k not in ("transferred_to", "deleted")}, "repo": to_repo, "number": n, "id": self._new_id(),
               "updated_at": self._now_iso(at)}
        self.issues[(to_repo, n)] = new
        payload = self.webhook_payload("issues", "transferred", repo=repo, number=number,
                                       changes={"new_issue": self.issue_json(new), "new_repository": self.repo_json(self.repos[to_repo])})
        old["transferred_to"] = {"repo": to_repo, "number": n}
        for c in self.comments.values():                     # comments move with the issue
            if c["repo"] == repo and int(c["issue"]) == int(number):
                c["repo"], c["issue"] = to_repo, n
        return payload

    def add_comment(self, repo: str, number: int, *, body: str, user: str, at: str | None = None) -> dict[str, Any]:
        cid = self._new_id()
        now = self._now_iso(at)
        self.comments[cid] = {"repo": repo, "id": cid, "issue": int(number), "user": user, "body": body, "created_at": now, "updated_at": now}
        self.issues[(repo, int(number))]["updated_at"] = now
        return self.webhook_payload("issue_comment", "created", repo=repo, number=number, comment_id=cid)

    def edit_comment(self, comment_id: int, body: str, *, at: str | None = None) -> dict[str, Any]:
        c = self.comments[int(comment_id)]
        changes = {"body": {"from": c["body"]}}
        c["body"] = body
        c["updated_at"] = self._now_iso(at)
        return self.webhook_payload("issue_comment", "edited", repo=c["repo"], number=c["issue"], comment_id=comment_id, changes=changes)

    def delete_comment(self, comment_id: int) -> dict[str, Any]:
        c = self.comments[int(comment_id)]
        payload = self.webhook_payload("issue_comment", "deleted", repo=c["repo"], number=c["issue"], comment_id=comment_id)
        c["deleted"] = True
        return payload

    # ------------------------------------------------------------------ webhook deliveries
    def signed_delivery(self, event: str, payload: Mapping[str, Any], secret: bytes | str | None = None, *, delivery_id: str | None = None,
                        tamper: bool = False) -> tuple[dict[str, str], bytes]:
        """``(headers, raw body)`` exactly as GitHub sends a repository webhook delivery, signed with ``secret`` (default:
        the fixture's dummy ``app.webhook_secret``). ``tamper=True`` flips one byte of the body after signing."""
        key = secret if isinstance(secret, bytes) else str(secret or self.app_config.get("webhook_secret") or "").encode()
        body = json.dumps(payload).encode("utf-8")
        repo_id = str((payload.get("repository") or {}).get("id") or "")
        headers = {"Content-Type": "application/json", "User-Agent": "GitHub-Hookshot/mock", "X-GitHub-Event": event,
                   "X-GitHub-Delivery": delivery_id or str(uuid.uuid4()), "X-GitHub-Hook-ID": "424242",
                   "X-GitHub-Hook-Installation-Target-ID": repo_id, "X-GitHub-Hook-Installation-Target-Type": "repository",
                   "X-Hub-Signature": "sha1=" + hmac.new(key, body, hashlib.sha1).hexdigest(),
                   "X-Hub-Signature-256": "sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest()}
        if tamper:
            b = bytearray(body)
            b[len(b) // 2] ^= 0x01
            body = bytes(b)
        return headers, body

    async def send_webhook(self, url: str, event: str, payload: Mapping[str, Any], secret: bytes | str | None = None, *,
                           delivery_id: str | None = None, tamper: bool = False) -> int:
        """POST a signed delivery to ``url`` (a Mycelic webhook endpoint); returns the HTTP status."""
        headers, body = self.signed_delivery(event, payload, secret, delivery_id=delivery_id, tamper=tamper)
        async with aiohttp.ClientSession() as s:
            async with s.post(url, data=body, headers=headers) as resp:
                return resp.status


def _node(prefix: str, n: Any) -> str:
    return prefix + "_" + base64.b64encode(f"{prefix}{n}".encode()).decode().rstrip("=")


def _strip_weak(tag: str) -> str:
    t = tag.strip()
    return t[2:] if t.startswith("W/") else t


def _redirect(uri: str, params: Mapping[str, str]) -> web.Response:
    parts = urlsplit(uri)
    q = parse_qsl(parts.query) + [(k, v) for k, v in params.items() if v]
    return web.Response(status=302, headers={"Location": urlunsplit(parts._replace(query=urlencode(q)))})


__all__ = ["FakeClock", "GitHubMock", "start_github_mock"]
