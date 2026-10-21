"""Load a reference module with its ``set``s made insertion-ordered (used by gen_dialogue_linker / gen_mega_search).

Some reference modules iterate Python ``set``s of strings (entity sets, set intersections, ``list(set(...))``),
and what they produce depends on that iteration order: which antecedent wins a tie, the order of a joined topic
string, the order PageRank mass is summed in. CPython orders a ``set`` by string hash, randomised per process,
so those outputs were never reproducible in Python itself. The Rust port iterates every such set in insertion
order (``IndexSet``), one of the orders Python could have produced.

To pin the port bit for bit anyway, the generators also run a copy of the module whose sets are insertion-ordered:
the source is parsed, every set display / set comprehension / ``set`` name is replaced by :class:`OSet`, and the
result is executed as a sibling module of the original (so relative imports keep working). Nothing else changes.
Set algebra keeps the left operand's order: ``a | b`` is ``a`` then the new members of ``b``; ``a & b`` and
``a - b`` filter ``a``. The generators check the ordered copy against the original module on order-free
summaries (multisets, score maps within 1e-12), so the rewrite cannot hide a semantic change.
"""
from __future__ import annotations

import ast
import importlib
import sys
import types


class OSet:
    """A ``set`` that iterates in insertion order (dict-backed)."""

    __slots__ = ("_d",)

    def __init__(self, items=()):
        self._d = dict.fromkeys(items)

    def __iter__(self):
        return iter(self._d)

    def __len__(self):
        return len(self._d)

    def __contains__(self, x):
        return x in self._d

    def __bool__(self):
        return bool(self._d)

    def __repr__(self):
        return f"OSet({list(self._d)!r})"

    def __eq__(self, other):
        return set(self._d) == set(other)

    __hash__ = None

    def add(self, x):
        self._d[x] = None

    def discard(self, x):
        self._d.pop(x, None)

    def remove(self, x):
        del self._d[x]

    def update(self, *iterables):
        for it in iterables:
            for x in it:
                self._d[x] = None

    def copy(self):
        return OSet(self._d)

    def __or__(self, other):
        r = OSet(self._d)
        r.update(other)
        return r

    def __ror__(self, other):
        r = OSet(other)
        r.update(self)
        return r

    def __ior__(self, other):
        self.update(other)
        return self

    def __and__(self, other):
        return OSet(x for x in self._d if x in other)

    def __rand__(self, other):
        return OSet(x for x in other if x in self._d)

    def __iand__(self, other):
        self._d = dict.fromkeys(x for x in self._d if x in other)
        return self

    def __sub__(self, other):
        return OSet(x for x in self._d if x not in other)

    def __rsub__(self, other):
        return OSet(x for x in other if x not in self._d)

    def __isub__(self, other):
        self._d = dict.fromkeys(x for x in self._d if x not in other)
        return self

    union = __or__
    intersection = __and__
    difference = __sub__


class _ToOSet(ast.NodeTransformer):
    def visit_Set(self, node):
        self.generic_visit(node)
        return ast.copy_location(
            ast.Call(func=ast.Name(id="_OSet", ctx=ast.Load()), args=[ast.List(elts=node.elts, ctx=ast.Load())],
                     keywords=[]), node)

    def visit_SetComp(self, node):
        self.generic_visit(node)
        gen = ast.GeneratorExp(elt=node.elt, generators=node.generators)
        return ast.copy_location(ast.Call(func=ast.Name(id="_OSet", ctx=ast.Load()), args=[gen], keywords=[]), node)

    def visit_Name(self, node):
        if node.id == "set" and isinstance(node.ctx, ast.Load):
            return ast.copy_location(ast.Name(id="_OSet", ctx=ast.Load()), node)
        return node


def load_ordered(module_name: str) -> types.ModuleType:
    """Import ``module_name``'s source again with insertion-ordered sets, as ``<module_name>__ordered``."""
    original = importlib.import_module(module_name)
    path = original.__file__
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    tree = ast.fix_missing_locations(_ToOSet().visit(tree))
    name = module_name + "__ordered"
    mod = types.ModuleType(name)
    mod.__file__ = path
    mod.__package__ = original.__package__
    mod._OSet = OSet
    sys.modules[name] = mod
    exec(compile(tree, path, "exec"), mod.__dict__)
    return mod
