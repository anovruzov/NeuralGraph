"""A small, strict JSON-schema subset that every model reply is validated against locally.

Why our own and why this subset:

* Servers differ in which schema keywords they enforce (some reject ``pattern`` or ``maxLength`` outright), so the
  schema sent on the wire may be reduced, but the reply is always checked here against the full schema.
* The subset is the one OpenAI-style strict mode accepts: every object lists all its properties in ``required``
  and sets ``additionalProperties: false``; an optional field is nullable (``["string", "null"]``), never omitted.
  Anything outside the subset (``anyOf``, ``$ref``, ``format``, even ``description``) fails at :func:`compile`,
  so a schema cannot silently mean less locally than it says.
* Problems are reported as ``(json_path, keyword)`` pairs whose names come only from the schema. A value, or the
  name of an extra key (model-controlled text), never appears in a problem or in ``str(SchemaError)``, so a
  validation failure cannot carry record text into a log, a ledger or a repair prompt.

Type rules: ``integer`` is an ``int`` that is not a ``bool`` (``3.0`` fails); ``number`` is a finite ``int`` or
``float`` that is not a ``bool`` (NaN and Infinity fail with ``type``). ``enum``/``const`` compare type-strictly
(``True != 1``, ``1 == 1.0``). ``pattern`` is matched with ``re.fullmatch`` and ``re.ASCII``, so a trailing newline
or an Arabic-Indic digit cannot satisfy ``[0-9]``. Lengths count code points.
"""
from __future__ import annotations

import copy
import math
import re
from typing import Any

ALLOWED_KEYWORDS = frozenset({
    "type", "properties", "required", "additionalProperties", "enum", "const", "pattern", "minLength", "maxLength",
    "minimum", "maximum", "items", "minItems", "maxItems",
})
STRICT_KEYWORDS = ("pattern", "minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems")
TYPES = ("object", "array", "string", "integer", "number", "boolean", "null")
MAX_PROBLEMS = 50

_KEYWORDS_FOR = {
    "object": {"properties", "required", "additionalProperties"},
    "array": {"items", "minItems", "maxItems"},
    "string": {"pattern", "minLength", "maxLength"},
    "integer": {"minimum", "maximum"},
    "number": {"minimum", "maximum"},
}
_TYPED_KEYWORDS = set().union(*_KEYWORDS_FOR.values())


class SchemaError(ValueError):
    """Compile time: a message naming the schema path. Check time: ``problems`` as ``(json_path, keyword)`` pairs."""

    def __init__(self, message: str | None = None, *, problems: tuple[tuple[str, str], ...] | list = ()) -> None:
        self.problems = tuple((str(p), str(k)) for p, k in problems)
        if message is None:
            message = "schema violation: " + "; ".join(f"{p} {k}" for p, k in self.problems)
        super().__init__(message)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v: Any) -> bool:
    return (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and math.isfinite(v))


def _is_scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, bool, int)) or (isinstance(v, float) and math.isfinite(v))


def _scalar_equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return type(a) is type(b) and a == b


def _base_type(node_type: Any) -> tuple[str, bool]:
    if isinstance(node_type, str):
        return node_type, False
    return node_type[0], True


class _Node:
    __slots__ = ("base", "nullable", "properties", "items", "enum", "const", "has_const", "pattern", "min_length",
                 "max_length", "minimum", "maximum", "min_items", "max_items")

    def __init__(self) -> None:
        self.base = "null"
        self.nullable = False
        self.properties: dict[str, _Node] = {}
        self.items: _Node | None = None
        self.enum: list[Any] | None = None
        self.const: Any = None
        self.has_const = False
        self.pattern: re.Pattern[str] | None = None
        self.min_length = self.max_length = self.min_items = self.max_items = None
        self.minimum = self.maximum = None


def _compile_node(schema: Any, path: str) -> _Node:
    if not isinstance(schema, dict):
        raise SchemaError(f"schema node must be an object at {path}") from None
    for key in sorted(schema):
        if key not in ALLOWED_KEYWORDS:
            raise SchemaError(f"unsupported keyword {key!r} at {path}") from None
    if "type" not in schema:
        raise SchemaError(f"missing 'type' at {path}") from None
    node_type = schema["type"]
    valid_list = (isinstance(node_type, list) and len(node_type) == 2 and node_type[1] == "null"
                  and isinstance(node_type[0], str) and node_type[0] in TYPES and node_type[0] != "null")
    if not ((isinstance(node_type, str) and node_type in TYPES) or valid_list):
        raise SchemaError(f"invalid 'type' at {path}") from None
    node = _Node()
    node.base, node.nullable = _base_type(node_type)
    allowed = _KEYWORDS_FOR.get(node.base, set())
    for key in sorted(schema):
        if key in _TYPED_KEYWORDS and key not in allowed:
            raise SchemaError(f"keyword {key!r} does not apply to type {node.base!r} at {path}") from None

    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or not enum or not all(_is_scalar(v) for v in enum):
            raise SchemaError(f"'enum' must be a non-empty list of scalars at {path}") from None
        node.enum = list(enum)
    if "const" in schema:
        if not _is_scalar(schema["const"]):
            raise SchemaError(f"'const' must be a scalar at {path}") from None
        node.const, node.has_const = schema["const"], True

    for key, attr in (("minLength", "min_length"), ("maxLength", "max_length"), ("minItems", "min_items"),
                      ("maxItems", "max_items")):
        if key in schema:
            if not _is_int(schema[key]) or schema[key] < 0:
                raise SchemaError(f"{key!r} must be a non-negative int at {path}") from None
            setattr(node, attr, schema[key])
    for key in ("minimum", "maximum"):
        if key in schema:
            if not _is_number(schema[key]):
                raise SchemaError(f"{key!r} must be a finite number at {path}") from None
            setattr(node, key, schema[key])
    if "pattern" in schema:
        if not isinstance(schema["pattern"], str):
            raise SchemaError(f"'pattern' must be a string at {path}") from None
        try:
            node.pattern = re.compile(schema["pattern"], re.ASCII)
        except re.error:
            node.pattern = None
        if node.pattern is None:
            raise SchemaError(f"'pattern' does not compile at {path}") from None

    if node.base == "object":
        if schema.get("additionalProperties", None) is not False:
            raise SchemaError(f"objects need 'additionalProperties': false at {path}") from None
        props = schema.get("properties")
        if not isinstance(props, dict):
            raise SchemaError(f"objects need 'properties' at {path}") from None
        required = schema.get("required")
        if (not isinstance(required, list) or not all(isinstance(r, str) for r in required)
                or len(set(required)) != len(required) or set(required) != set(props)):
            raise SchemaError(f"'required' must list every property exactly once at {path}") from None
        for name in sorted(props):
            node.properties[name] = _compile_node(props[name], f"{path}.properties.{name}")
    elif node.base == "array":
        if "items" not in schema:
            raise SchemaError(f"arrays need 'items' at {path}") from None
        node.items = _compile_node(schema["items"], f"{path}.items")
    return node


def _type_ok(node: _Node, value: Any) -> bool:
    base = node.base
    if base == "object":
        return isinstance(value, dict)
    if base == "array":
        return isinstance(value, list)
    if base == "string":
        return isinstance(value, str)
    if base == "integer":
        return _is_int(value)
    if base == "number":
        return _is_number(value)
    if base == "boolean":
        return isinstance(value, bool)
    return value is None


def _validate(node: _Node, value: Any, path: str, out: list[tuple[str, str]]) -> None:
    if len(out) >= MAX_PROBLEMS:
        return
    if value is None and node.nullable:
        return
    if not _type_ok(node, value):
        out.append((path, "type"))
        return
    if node.enum is not None and not any(_scalar_equal(value, e) for e in node.enum):
        out.append((path, "enum"))
    if node.has_const and not _scalar_equal(value, node.const):
        out.append((path, "const"))
    if node.base == "string":
        if node.pattern is not None and node.pattern.fullmatch(value) is None:
            out.append((path, "pattern"))
        if node.min_length is not None and len(value) < node.min_length:
            out.append((path, "minLength"))
        if node.max_length is not None and len(value) > node.max_length:
            out.append((path, "maxLength"))
    elif node.base in ("integer", "number"):
        if node.minimum is not None and value < node.minimum:
            out.append((path, "minimum"))
        if node.maximum is not None and value > node.maximum:
            out.append((path, "maximum"))
    elif node.base == "array":
        if node.min_items is not None and len(value) < node.min_items:
            out.append((path, "minItems"))
        if node.max_items is not None and len(value) > node.max_items:
            out.append((path, "maxItems"))
        for i, item in enumerate(value):
            if len(out) >= MAX_PROBLEMS:
                break
            _validate(node.items, item, f"{path}[{i}]", out)
    elif node.base == "object":
        for name in sorted(node.properties):
            if len(out) >= MAX_PROBLEMS:
                break
            child = f"{path}.{name}"
            if name not in value:
                out.append((child, "required"))
            else:
                _validate(node.properties[name], value[name], child, out)
        if any(k not in node.properties for k in value):
            out.append((path, "additionalProperties"))
    del out[MAX_PROBLEMS:]


def reduced(schema: dict[str, Any]) -> dict[str, Any]:
    """A copy without the seven keywords some servers reject; local validation still uses the full schema."""
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in STRICT_KEYWORDS:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {name: reduced(sub) for name, sub in value.items()}
        elif key == "items" and isinstance(value, dict):
            out[key] = reduced(value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Schema:
    """A compiled schema. Build it with :func:`compile`."""

    def __init__(self, source: dict[str, Any], root: _Node) -> None:
        self._source = source
        self._root = root

    @property
    def full(self) -> dict[str, Any]:
        return copy.deepcopy(self._source)

    @property
    def reduced(self) -> dict[str, Any]:
        return reduced(self._source)

    @property
    def top_type(self) -> str:
        return self._root.base if not self._root.nullable else "nullable"

    def validate(self, value: Any) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        _validate(self._root, value, "$", out)
        return out

    def check(self, value: Any) -> None:
        problems = self.validate(value)
        if problems:
            raise SchemaError(problems=problems) from None


def compile(schema: dict[str, Any]) -> Schema:  # noqa: A001 - the module's documented entry point
    source = copy.deepcopy(schema)
    return Schema(source, _compile_node(source, "$"))
