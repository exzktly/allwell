"""Every feature-service key core calls is one some feature declares.

The service lookup (``ctx.features.call`` / ``.query``) is how ``core/`` reaches
a named feature without importing it — the mechanism behind the roadmap's
terminal condition 3. It buys the decoupling by addressing the feature through a
**string**, and a string key has a failure mode an import does not: a typo, a
rename on one side only, or a feature that stops declaring a service does not
raise. ``call`` simply returns ``False`` and the refresh silently never happens,
which is the exact shape of defect this codebase keeps paying for — degraded
behaviour indistinguishable from working behaviour.

So the two sides are related here, in both directions:

* a key called from the tree must be declared by some descriptor, or the call is
  dead;
* a declared key must be called from somewhere, or the declaration is dead.

Both halves matter. The first catches the typo; the second catches the service
left behind by a call site that was rewritten, which would otherwise sit there
looking like live wiring.
"""

from __future__ import annotations

import ast

import pytest

from tests._repo_scan import parse_source, python_sources, rel


def _declared_keys() -> set[str]:
    pytest.importorskip("PySide6")
    from review.features.registry import build_feature_registry

    # The full profile deliberately: a subset build drops features *and* their
    # services together, which is the point of the seam, not a violation of it.
    return set(build_feature_registry().service_keys())


#: The registry's spellings as call sites hold it: ``ctx.features`` in
#: ctx-native code, ``app._features`` on the window shell.
_REGISTRY_ATTRS = frozenset({"features", "_features"})


def _keys_in_tree(tree: ast.AST) -> set[str]:
    """Every literal service key addressed in *tree*."""
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr in ("call", "query")):
            continue
        # The receiver has to be the feature registry, or every ``.call`` in the
        # app matches — a predicate applied outside its domain.
        recv = fn.value
        if not (isinstance(recv, ast.Attribute) and recv.attr in _REGISTRY_ATTRS):
            continue
        if not node.args:
            continue
        key = node.args[0]
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
        elif isinstance(key, ast.IfExp):
            # ``call("a" if flag else "b", …)`` — both branches are real keys,
            # and reading only the first lets a typo in the second through.
            for branch in (key.body, key.orelse):
                if isinstance(branch, ast.Constant) and isinstance(branch.value, str):
                    keys.add(branch.value)
    return keys


def _called_keys() -> dict[str, list[str]]:
    """Every literal key passed to ``.call(...)`` / ``.query(...)``, by file."""
    found: dict[str, list[str]] = {}
    for path in python_sources():
        try:
            tree = parse_source(path)
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        for key in _keys_in_tree(tree):
            found.setdefault(key, []).append(rel(path))
    return found


def test_every_called_service_key_is_declared():
    declared = _declared_keys()
    called = _called_keys()
    assert called, "no feature-service calls found — the scan has stopped working"

    unknown = {k: v for k, v in called.items() if k not in declared}
    assert not unknown, (
        "these feature-service keys are called but no descriptor declares them, "
        "so the call is a silent no-op: "
        + ", ".join(f"{k!r} (from {', '.join(sorted(set(v)))})"
                    for k, v in sorted(unknown.items()))
    )


def test_every_declared_service_is_called():
    declared = _declared_keys()
    called = set(_called_keys())

    unused = sorted(declared - called)
    assert not unused, (
        f"these feature services are declared but never called: {unused}. A "
        f"service nobody addresses is dead wiring that reads as live — delete "
        f"it, or fix the call site that was meant to use it."
    )


# ── the scan itself, on synthetic sources ─────────────────────────────────


def _keys_in(source: str) -> set[str]:
    """The guard's own extractor over a snippet, so these cases exercise the
    scan itself rather than a paraphrase of it."""
    return _keys_in_tree(ast.parse(source))


@pytest.mark.parametrize("source, expected", [
    # The plain shapes.
    ('ctx.features.call("a.b", ctx)', {"a.b"}),
    ('ctx.features.query("a.b", ctx, default=0.0)', {"a.b"}),
    ('self._features.call("a.b", ctx)', {"a.b"}),
    # The conditional key: both branches are real call sites, and reading only
    # the first would let a typo in the second through unseen.
    ('ctx.features.call("a.b" if flag else "c.d", ctx)', {"a.b", "c.d"}),
    # Not the registry: any object in the app may have a .call().
    ('worker.call("a.b")', set()),
    ('ctx.bus.call("a.b")', set()),
    # No key to check.
    ('ctx.features.call()', set()),
    ('ctx.features.call(key_var, ctx)', set()),
])
def test_the_scan_reads_the_shapes_it_claims_to(source, expected):
    assert _keys_in(source) == expected
