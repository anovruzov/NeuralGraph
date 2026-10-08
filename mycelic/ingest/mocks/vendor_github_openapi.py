"""Regenerate ``fixtures/github_openapi_subset.json`` from GitHub's published REST description.

Usage (the description is ~13 MB; download it into an empty directory and run this with ``python -I``)::

    curl -o /tmp/gh/api.github.com.json \\
      https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json
    python -I mycelic/ingest/mocks/vendor_github_openapi.py /tmp/gh/api.github.com.json mycelic/ingest/mocks/fixtures/github_openapi_subset.json

It keeps only the required keys and property types of the schemas, webhooks and operations the GitHub connector and its
mock rely on (no examples, no descriptions), plus the source file's sha256. ``test_mock_responses_conform_to_githubs_
openapi_description`` checks every mock response against it, so re-vendoring makes upstream drift visible. Standard
library only; nothing from the downloaded file is executed.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from typing import Any

SCHEMAS = ("issue", "issue-comment", "repository", "minimal-repository", "full-repository", "simple-user", "private-user", "public-user", "label")
EXTRA_PROPS = {"issue": ("body", "pull_request", "assignees", "state_reason", "author_association"),
               "issue-comment": ("body", "author_association"), "repository": ("visibility", "permissions"),
               "minimal-repository": ("visibility", "permissions", "archived", "default_branch"), "full-repository": ("visibility", "permissions")}
WEBHOOKS = ("issues-opened", "issues-edited", "issues-deleted", "issues-transferred", "issues-closed", "issues-reopened", "issues-labeled",
            "issues-unlabeled", "issue-comment-created", "issue-comment-edited", "issue-comment-deleted", "ping", "github-app-authorization-revoked")
OPERATIONS = (("/user", "get"), ("/user/repos", "get"), ("/orgs/{org}/repos", "get"), ("/installation/repositories", "get"),
              ("/repos/{owner}/{repo}", "get"), ("/repos/{owner}/{repo}/issues", "get"), ("/repos/{owner}/{repo}/issues/{issue_number}", "get"),
              ("/repos/{owner}/{repo}/issues/comments", "get"), ("/repos/{owner}/{repo}/issues/comments/{comment_id}", "get"),
              ("/repos/{owner}/{repo}/issues/{issue_number}/comments", "get"))


class Subset:
    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.schemas = spec["components"]["schemas"]

    def deref(self, p: Any) -> Any:
        for _ in range(10):
            if not (isinstance(p, dict) and "$ref" in p):
                break
            name = p["$ref"].split("/")[-1]
            p = dict(self.schemas.get(name, {}), _ref=name)
        return p

    def shape(self, p: Any) -> dict[str, Any]:
        """``{type, nullable[, ref]}`` of one property schema, following ``$ref`` / ``allOf`` / ``anyOf`` / ``oneOf``."""
        if not isinstance(p, dict):
            return {"type": "any", "nullable": True}
        nullable = bool(p.get("nullable"))
        if "$ref" in p:
            d = self.deref(p)
            return {"type": d.get("type") or "object", "nullable": nullable or bool(d.get("nullable")), "ref": d.get("_ref")}
        for comb in ("allOf", "anyOf", "oneOf"):
            if comb in p:
                types, refs = [], []
                for sub in p[comb]:
                    s = self.shape(sub)
                    if s["type"] == "null":
                        nullable = True
                        continue
                    types.append(s["type"])
                    nullable = nullable or s["nullable"]
                    if s.get("ref"):
                        refs.append(s["ref"])
                out: dict[str, Any] = {"type": types[0] if len(set(types)) == 1 else ("any" if types else "null"), "nullable": nullable}
                if refs:
                    out["ref"] = refs[0]
                return out
        t = p.get("type")
        if isinstance(t, list):
            nullable = nullable or "null" in t
            t = [x for x in t if x != "null"]
            t = t[0] if len(t) == 1 else "any"
        return {"type": t or "any", "nullable": nullable}

    def schema(self, name: str) -> dict[str, Any]:
        s = self.schemas[name]
        props = s.get("properties", {})
        required = list(s.get("required", []))
        keep = required + [k for k in EXTRA_PROPS.get(name, ()) if k in props and k not in required]
        return {"required": required, "properties": {k: self.shape(props.get(k)) for k in keep}}

    def webhook(self, name: str) -> dict[str, Any]:
        hooks = self.spec.get("webhooks") or self.spec.get("x-webhooks") or {}
        op = hooks[name]["post"]
        s = self.deref(op["requestBody"]["content"]["application/json"]["schema"])
        props = s.get("properties", {})
        return {"headers": [p.get("name") for p in op.get("parameters", [])], "required": s.get("required", []),
                "properties": {k: self.shape(props.get(k)) for k in s.get("required", [])}}

    def operation(self, path: str, method: str) -> tuple[str, dict[str, Any]]:
        op = self.spec["paths"][path][method]
        params = {}
        for prm in op.get("parameters", []):
            if "$ref" in prm:
                prm = self.spec["components"]["parameters"][prm["$ref"].split("/")[-1]]
            sch = prm.get("schema", {})
            params[prm.get("name")] = {k: sch[k] for k in ("type", "default", "enum") if k in sch}
        return op["operationId"], {"path": path, "method": method.upper(), "params": params, "responses": sorted(op.get("responses", {}))}


def main(src: str, out: str) -> None:
    raw = open(src, "rb").read()
    sub = Subset(json.loads(raw))
    result = {
        "_source": {"repository": "github/rest-api-description", "path": "descriptions/api.github.com/api.github.com.json", "ref": "main",
                    "fetched": date.today().isoformat(), "sha256": hashlib.sha256(raw).hexdigest(), "info_version": sub.spec["info"]["version"],
                    "note": "trimmed to required keys and property types; regenerate to make upstream drift visible"},
        "schemas": {n: sub.schema(n) for n in SCHEMAS},
        "webhooks": {n: sub.webhook(n) for n in WEBHOOKS},
        "operations": dict(sub.operation(p, m) for p, m in OPERATIONS),
    }
    with open(out, "w") as f:
        json.dump(result, f, indent=1, sort_keys=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
