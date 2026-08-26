"""Every Bar-plot draw path applies the Properties → Colours mode.

Two rules, both mechanical, both about the same half-sweep.

Two surfaces draw the Bar plot's three panels: the tab
(``features/barplot/renderer.draw_grouped_bar_mode``) and the Batch Export bar
panel, which renders the same figures for a whole plate. The batch one calls the
shared renderer with ``ctx=None`` on purpose — it does not want that function's
other ctx-derived behaviour — so the colour mode cannot be read off ``ctx``
inside the renderer and must be handed in by each caller.

Which makes this a half-sweep waiting to happen, and §7.5 #5 is explicit about
what that costs: one function ends up holding two definitions of the same word,
and neither call site looks wrong. The failure is silent — a batch PNG in plate
colours beside an on-screen figure in grey, both drawn from the same setting.

So the rule is mechanical: a call to ``render_bar_items`` passes ``bar_color``.
The keyword is required rather than defaulted-from-ctx because a default is
exactly what a third caller would inherit without noticing.
"""

from __future__ import annotations

import ast

from tests._repo_scan import REPO_ROOT, python_sources
from tests._repo_scan import parse_source

#: The renderer, and the aliases the call sites import it under. Aliases are
#: listed rather than resolved because an ``import ... as`` is how the batch
#: panel already spells it, and a guard that only matched the bare name would
#: have been blind to the very call site this rule exists for.
_CALL_NAMES = {"render_bar_items", "_render_bar_items", "_bar_render_items"}

#: The renderer's own module — the ``def`` lives there, not a call.
_DEFINITION = "well_viewer/features/barplot/controller.py"


def rel(path) -> str:
    """Repo-relative path. A named function rather than an inline expression so
    ``tests/test_guards_bite.py`` can redirect it at a temp file the same way it
    redirects the scan itself."""
    return path.relative_to(REPO_ROOT).as_posix()


def _resolves_the_mode(value: ast.AST) -> bool:
    """True when *value* is a call to the mode resolver, under any alias.

    ``mono_bar_color`` is imported as ``_mono_bar_color`` by the Batch Export
    panel, so the suffix is matched rather than the exact name — and only a
    ``Call`` counts, because a literal (``None``, a colour string) is a call
    site that decided the mode statically and therefore did not read the state.
    """
    if not isinstance(value, ast.Call):
        return False
    fn = value.func
    name = fn.id if isinstance(fn, ast.Name) else (
        fn.attr if isinstance(fn, ast.Attribute) else "")
    return name.endswith("mono_bar_color")


def _calls_missing_bar_color(tree: ast.AST) -> list[int]:
    out: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (
            fn.attr if isinstance(fn, ast.Attribute) else None)
        if name not in _CALL_NAMES:
            continue
        bar_color = next((kw for kw in node.keywords if kw.arg == "bar_color"), None)
        if bar_color is not None:
            # The keyword being *present* is not the rule. ``bar_color=None``
            # satisfies a presence check and silently reverts that surface to
            # plate colours while the other stays grey — the exact divergence
            # this guard exists to prevent, wearing the guard's own uniform. So
            # the value must be a call to the resolver, whatever alias the call
            # site imports it under.
            if _resolves_the_mode(bar_color.value):
                continue
            out.append(node.lineno)
            continue
        # ``**kwargs`` forwarding (kw.arg is None) is a pass-through, not a
        # call site that decides the mode — the decision was made upstream.
        if any(kw.arg is None for kw in node.keywords):
            continue
        out.append(node.lineno)
    return out


def test_every_bar_render_call_states_the_colour_mode():
    offenders: list[str] = []
    for path in python_sources():
        relpath = rel(path)
        if relpath == _DEFINITION:
            continue
        try:
            tree = parse_source(path)
        except (OSError, SyntaxError):
            continue
        for lineno in _calls_missing_bar_color(tree):
            offenders.append(f"{relpath}:{lineno}")
    assert not offenders, (
        "render_bar_items called without bar_color= — the Properties → Colours "
        "mode would be dropped on this surface while the other honours it "
        "(CLAUDE.md §7.5 #5). Pass "
        "``bar_color=mono_bar_color(<ctx>.state.plot)``:\n  "
        + "\n  ".join(offenders)
    )


def test_the_guard_can_see_both_known_call_sites():
    """A scan surface that reaches neither call site would pass silently, which
    is §7.5 #3 — the guard measuring nothing and reporting success. So the two
    call sites this rule exists for are named, and must be found."""
    expected = {
        "well_viewer/features/barplot/renderer.py",
        "well_viewer/features/batch_export/bar_panel.py",
    }
    found: set[str] = set()
    for path in python_sources():
        relpath = rel(path)
        if relpath == _DEFINITION:
            continue
        try:
            tree = parse_source(path)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else None)
            if name in _CALL_NAMES:
                found.add(relpath)
    assert expected <= found, (
        f"the guard's scan surface missed a known call site: {sorted(expected - found)}")


# ── the per-cell renderers ──────────────────────────────────────────────────
#: The Bar tab's other three draw paths. Each takes ``ctx`` and a ``colors``
#: list, so each resolves the mode itself rather than being handed it — but
#: "each" is the load-bearing word: Violin, Beeswarm and the events panel drawn
#: beside them are three separate functions, and a mode applied to one leaves a
#: grey distribution above a plate-coloured count. There is no cheap
#: data-driven test for these (every one wants a loaded dataframe per column),
#: and §7.6 #3 is the answer to that: ask what else the syntax tree can decide.
_PER_CELL_RENDERERS = ("draw_violin", "draw_beeswarm", "draw_per_well_n_bars")

_RENDERER_MODULE = "well_viewer/features/barplot/renderer.py"


def _is_apply_call(value: ast.AST) -> bool:
    if not isinstance(value, ast.Call):
        return False
    fn = value.func
    name = fn.id if isinstance(fn, ast.Name) else (
        fn.attr if isinstance(fn, ast.Attribute) else "")
    return name == "apply_bar_color_mode"


def test_each_per_cell_renderer_applies_the_colour_mode():
    tree = parse_source((REPO_ROOT / _RENDERER_MODULE))
    by_name = {
        node.name: node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    missing = []
    for name in _PER_CELL_RENDERERS:
        fn = by_name.get(name)
        assert fn is not None, f"{name} is gone from {_RENDERER_MODULE} — update this guard"
        # The *binding*, not the call. ``apply_bar_color_mode`` returns a new
        # list and mutates nothing, so a bare expression statement calling it
        # is dead code that a "does the call appear?" check waves through —
        # and ruff does not flag it either, because a call expression is not an
        # unused value. That mutation (dropping ``colors = ``) leaves Violin,
        # Beeswarm and the events panel in plate colours beside a grey Mean
        # panel, with the whole suite green. CLAUDE.md §7.5 #2.
        applies = any(
            isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "colors" for t in n.targets)
            and _is_apply_call(n.value)
            for n in ast.walk(fn)
        )
        if not applies:
            missing.append(f"{name} (line {fn.lineno})")
    assert not missing, (
        "Bar per-cell renderer(s) that never apply the Properties → Colours "
        "mode back onto ``colors``. Grey mode would restyle the panels beside "
        "them and leave these in plate colours, on the same figure:\n  "
        + "\n  ".join(missing)
    )
