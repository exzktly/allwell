"""A constructor is never handed a keyword that names a *derived* property.

``BarGroup`` takes ``members`` and ``solo_wells``; ``wells`` is a read-only
property that unions them. Passing ``wells=`` is therefore always a
``TypeError`` at the call — never a wrong number, never a subtly different
group, just a crash the moment that line runs.

Which is exactly why it needs a guard rather than a test: the two sites that
shipped it were inside branches only reachable once a user had declared a
sample variable and picked one of the newer statistical tests. Forty-six pure
tests over the same feature were green, because the pure layer takes lists of
floats and never builds a group at all — the §7.7 shape, a branch that exists
and that the app cannot take. The slow lane found it in three minutes; this
finds it in two seconds.

Scoped deliberately to the classes whose derived properties collide with a real
constructor argument's meaning. A general "no kwarg may share a name with any
property anywhere" rule would need to resolve the callee's class at every call
site, which the AST cannot do, and would go wrong quietly in both directions.
"""

from __future__ import annotations

import ast

from tests._repo_scan import parse_source, python_sources

#: ``{class name: the keywords that are properties on it, not parameters}``.
#: Each entry is a name the class computes and cannot be told.
DERIVED_PROPERTIES = {
    "BarGroup": {"wells", "replicates"},
    "ReplicateSet": set(),
}


def _callee_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def test_no_constructor_is_passed_a_property_it_computes():
    offenders = []
    for path in python_sources():
        for node in ast.walk(parse_source(path)):
            if not isinstance(node, ast.Call):
                continue
            # An aliased import (``BarGroup as _BarGroup``) keeps the suffix,
            # which is what the two shipped sites used.
            name = _callee_name(node).lstrip("_")
            derived = DERIVED_PROPERTIES.get(name)
            if not derived:
                continue
            for kw in node.keywords:
                if kw.arg in derived:
                    offenders.append(
                        f"{path.name}:{node.lineno} — {name}({kw.arg}=…)")
    assert not offenders, (
        "these pass a keyword the class computes from its own fields, which "
        "raises TypeError the moment the line runs:\n  "
        + "\n  ".join(offenders)
        + "\nBarGroup takes `members` and `solo_wells`; `wells` is derived."
    )
