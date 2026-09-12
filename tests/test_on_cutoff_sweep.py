"""Every path reaches its ON cutoff through the one resolver.

There are three ON cutoffs, because there are three scales: shape/morphology has
none, an integer spot tally is cut against ThreshCountOn, a fluorescence reading
against ThreshFracOn. When each plot path picked for itself they disagreed, and
the disagreement was not a rounding error — the intensity cutoff starts at 50.0
and sits above the entire range a spot tally can occupy, so feeding it to a
count metric emptied the ON population and the plot drew nothing at all. The
Scatter tab's Wells mode reported "No data available" against a plate whose
counts were sitting in the CSVs.

So the classification lives in one predicate
(``metric_registry.cutoff_kind_for_column``) and the lookup in one resolver
(``channel_state_controller.on_cutoff_for_column``). This guard fails when a
call site reaches past the resolver for a raw per-channel accessor, which is how
the rule would rot: the accessors have to stay reachable for the resolver and
the Cell Gating tab's own row plumbing, so this is an allowlist rather than a
ban, and an allowlist is only as good as the liveness test beside it.

Every spelling counts, not only the ones the codebase happens to use today. A
reviewer evaded an earlier version four ways — a non-underscore import alias,
``_lazy("...", "get_thresh_frac_on")``, ``getattr`` and ``vars()`` — and the
suite stayed green. ``_lazy`` is not a hypothetical: it is how
``features/preview/panel_view.py`` already reaches this module.
"""

from __future__ import annotations

import ast

from tests._repo_scan import python_sources, rel
from tests._repo_scan import parse_source


#: The raw per-channel accessors. Reaching one of these directly decides for
#: yourself which cutoff applies — the thing this rule exists to stop.
#: ``cell_gating_get_thresh_frac_on`` is the feature-side read of the same
#: stored value, so it is an equally raw route to it and equally guarded.
#: Every name here must exist — see :func:`test_guarded_names_are_real`.
GUARDED = (
    "get_thresh_frac_on",
    "get_thresh_count_on",
    "cell_gating_get_thresh_frac_on",
)

#: ``(repo-relative path, dotted enclosing function)`` for every legitimate
#: direct reach. The site, not the file: exempting a whole module would let the
#: next plot path in it quietly reintroduce the bug. Nested functions carry
#: their enclosing chain — a bare ``_sync`` would hand the exemption to any
#: other closure of that name added anywhere in a 1,300-line module.
ALLOWED: frozenset[tuple[str, str]] = frozenset({
    # The resolver itself.
    ("review/core/channel_state_controller.py", "on_cutoff_for_column"),
    # The quick-edit display sync reads the value it is about to show. It picks
    # its accessor from ``quick_edit_cutoff_is_count`` — the same predicate the
    # resolver uses — so it cannot disagree with what the write path stores.
    ("review/core/channel_state_controller.py",
     "refresh_thresh_frac_quick_edit._sync"),
    # The Playground's binary preview is an image-pixel cut, so it always wants
    # the intensity cutoff whatever Property the plots are showing — routing it
    # through the resolver handed it a spot tally to compare against pixels.
    ("review/features/image_browser/playground/controller.py",
     "_effective_threshold"),
    # The god-object shim, kept while the burn-down runs, and its import line.
    ("review/runtime_app.py", "_get_thresh_frac_on"),
    ("review/runtime_app.py", "<module>"),
})


def _enclosing_function(tree: ast.AST) -> dict[ast.AST, str]:
    """Map every node to the dotted name of the function that contains it.

    Nested functions win over their parents, which is what makes a per-site
    allowlist meaningful for a closure defined inside a tab builder — and they
    keep the chain that led to them (``outer.inner``), so an exemption granted
    to one closure cannot be inherited by a same-named closure somewhere else
    in the module.
    """
    owner: dict[ast.AST, str] = {}

    def walk(node: ast.AST, current: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = current
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = (child.name if current == "<module>"
                        else f"{current}.{child.name}")
            owner[child] = name
            walk(child, name)

    owner[tree] = "<module>"
    walk(tree, "<module>")
    return owner


def _guarded_name(node: ast.AST) -> str | None:
    """The accessor a node names, for every spelling that reaches a cutoff.

    ``get_thresh_frac_on(ctx)``       → the bare call
    ``_get_thresh_frac_on(ctx)``      → aliased import (both batch-export panels)
    ``app._get_thresh_frac_on()``     → the god-object method shim
    ``read = get_thresh_frac_on``     → bound to a local, called on the next line
    ``import ... as fetch_cutoff``    → aliased to an unrelated name
    ``_lazy("...", "get_thresh_frac_on")`` → this repo's own lazy-binding idiom
    ``getattr(csc, "get_thresh_frac_on")`` / ``vars(csc)["..."]`` → by string

    Two rounds of hardening, both from evasions demonstrated against an earlier
    version rather than imagined. The first matched ``ast.Call`` and walked past
    two sites that bind the accessor to a local; the second matched only
    identifiers, and walked past an import alias that does not happen to be
    underscore-prefixed and past every string spelling — including
    ``core.lazy.lazy``, which is how ``features/preview/panel_view.py`` already
    reaches this very module. A guard that only sees the spellings the codebase
    happens to use today is not a rule, it is a description.
    """
    # A string that names an accessor is a reach: it is the argument to _lazy /
    # getattr / vars(), and there is no other reason to write it.
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value if node.value in GUARDED else None
    # ``from ... import get_thresh_frac_on as anything`` — the alias may be
    # spelled freely, so the imported name is what counts.
    if isinstance(node, ast.alias):
        return node.name if node.name in GUARDED else None
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        raw = node.id
    elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
        raw = node.attr
    else:
        return None
    stripped = raw.lstrip("_")
    return stripped if stripped in GUARDED else None


def _references() -> list[tuple[str, str, int]]:
    """``(path, enclosing function, line)`` for every reach at a guarded name."""
    out: list[tuple[str, str, int]] = []
    for path in python_sources():
        try:
            tree = parse_source(path)
        except SyntaxError:            # pragma: no cover - not our concern here
            continue
        path_str = rel(path)
        owner = _enclosing_function(tree)
        for node in ast.walk(tree):
            if _guarded_name(node) is None:
                continue
            out.append((path_str, owner.get(node, "<module>"), node.lineno))
    return out


def _offenders() -> list[str]:
    return [
        f"{path_str}:{line} — {func}() reaches a raw per-channel cutoff; "
        "use on_cutoff_for_column()"
        for path_str, func, line in _references()
        if (path_str, func) not in ALLOWED
    ]


def test_no_path_picks_its_own_on_cutoff():
    found = _offenders()
    assert not found, (
        "these sites decide for themselves which ON cutoff applies, which is "
        "how a spot tally ends up measured against a fluorescence threshold:\n"
        + "\n".join(found)
    )


def test_guarded_names_are_real():
    """Every guarded name is a function that actually exists.

    The companion to the allowlist liveness test, and it caught the same rot
    from the other end: this roster once carried
    ``cell_gating_get_thresh_count_on``, which was never defined anywhere. A
    rule about a symbol that does not exist can never fire, so it reads as
    cover the suite does not have — and if the accessor is ever written, it
    arrives already believed to be guarded.
    """
    defined: set[str] = set()
    for path in python_sources():
        try:
            tree = parse_source(path)
        except SyntaxError:            # pragma: no cover - not our concern here
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add(node.name.lstrip("_"))
    missing = sorted(n for n in GUARDED if n not in defined)
    assert not missing, (
        "these guarded names are not defined anywhere in the app, so the rule "
        f"cannot fire for them — drop them or write them: {missing}"
    )


def test_allowlist_has_no_stale_entries():
    """Every exemption still names a real call.

    Without this the allowlist silently becomes a list of places the rule no
    longer looks, and the next path added to one of those functions inherits an
    exemption nobody granted it.
    """
    live = {(path_str, func) for path_str, func, _ in _references()}
    stale = sorted(ALLOWED - live)
    assert not stale, (
        "these allowlist entries no longer name a call to a raw cutoff "
        f"accessor — drop them: {stale}"
    )
