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

Three spellings all count, because all three exist in this repository: the bare
call, the aliased import (both batch-export panels), and the ``app._``-prefixed
method (``runtime_app``). A guard that saw only the first would have reported a
clean sweep over two unconverted sites.
"""

from __future__ import annotations

import ast

from tests._repo_scan import python_sources, rel


#: The raw per-channel accessors. Reaching one of these directly decides for
#: yourself which cutoff applies — the thing this rule exists to stop.
GUARDED = ("get_thresh_frac_on", "get_thresh_count_on")

#: ``(repo-relative path, enclosing function)`` for every legitimate direct
#: reach. The site, not the file: exempting a whole module would let the next
#: plot path in it quietly reintroduce the bug.
ALLOWED: frozenset[tuple[str, str]] = frozenset({
    # The resolver itself, and the shared row-key helper behind both accessors.
    ("well_viewer/core/channel_state_controller.py", "on_cutoff_for_column"),
    # The Image Table quick-edit reads the value it is about to display; which
    # accessor it picks comes from ``quick_edit_cutoff_is_count``, the same
    # predicate the resolver uses.
    ("well_viewer/core/channel_state_controller.py", "refresh_thresh_frac_quick_edit"),
    ("well_viewer/features/image_browser/image_table/tab_view.py",
     "_sync_thresh_for_channel"),
    # The god-object shim, kept while the burn-down runs.
    ("well_viewer/runtime_app.py", "_get_thresh_frac_on"),
})


def _enclosing_function(tree: ast.AST) -> dict[ast.AST, str]:
    """Map every node to the name of the function that lexically contains it.

    Nested functions win over their parents, which is what makes a per-site
    allowlist meaningful for a closure defined inside a tab builder.
    """
    owner: dict[ast.AST, str] = {}

    def walk(node: ast.AST, current: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = current
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            owner[child] = name
            walk(child, name)

    owner[tree] = "<module>"
    walk(tree, "<module>")
    return owner


def _guarded_name(node: ast.AST) -> str | None:
    """The accessor a node names, for every spelling that reaches a cutoff.

    ``get_thresh_frac_on(ctx)``   → ``get_thresh_frac_on``
    ``_get_thresh_frac_on(ctx)``  → ``get_thresh_frac_on`` (aliased import)
    ``app._get_thresh_frac_on()`` → ``get_thresh_frac_on`` (method shim)
    ``read = get_thresh_frac_on`` → ``get_thresh_frac_on`` (bound, called later)

    *References*, not calls. The first version of this guard matched
    ``ast.Call`` and reported a clean sweep over two sites that pick an accessor
    into a local and call it on the next line — the same shape a deliberate
    evasion would take. Binding the name is the reach; where it is invoked is
    incidental.
    """
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
            tree = ast.parse(path.read_text())
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
