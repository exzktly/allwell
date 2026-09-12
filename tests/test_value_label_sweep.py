"""Every surface that draws a labelled panel actually passes the overlay on.

The same half-sweep shape ``test_bar_render_callers_pass_color_mode.py`` was
written for, one keyword over. Two surfaces draw the Bar plot's three panels —
the tab and the Batch Export panel — and the batch one calls the shared
renderer with ``ctx=None`` on purpose, so anything the renderer would otherwise
read off ``ctx`` has to be handed in by that call site. The Properties →
Colours mode was the first keyword to need this; the value-label overlay is the
second, and it arrived without the guard being extended.

The failure is silent in both directions and there is no cheap data-driven test
for the batch panels (each wants a loaded plate and a built Qt panel), so
§7.6 #3 applies: ask what else the syntax tree can decide. It can decide this —
a call either states the overlay or hands over a ctx to read it from, and
nothing else counts.

The per-cell Events panel is the one path with a cheap real test, so it gets
one: it draws the same mean-and-spread from a different collector, and a rule
honoured on one of two functions that draw one panel is exactly what §7.5 #5
is about.
"""

from __future__ import annotations

import ast
import types

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _close_figures():
    """These tests measure drawn figures, so they cannot close as they go.
    Closing after each keeps the file off the fast lane's figure-leak radar."""
    yield
    plt.close("all")


from tests._repo_scan import REPO_ROOT, parse_source, python_sources  # noqa: E402
from review.features.barplot import renderer  # noqa: E402

_BAR_CALL_NAMES = {"render_bar_items", "_render_bar_items", "_bar_render_items"}
_LINE_CALL_NAMES = {"render_line_series", "_render_line_series"}
_BAR_DEFINITION = "review/features/barplot/controller.py"
#: The line renderer lives in the same module as the tab's own call to it, so
#: unlike the bar side there is nothing to exclude — ``ast.Call`` never matches
#: the ``def`` itself.
_LINE_DEFINITION = ""
_RENDERER_MODULE = "review/features/barplot/renderer.py"


def rel(path) -> str:
    """Repo-relative path. A named function so ``tests/test_guards_bite.py``
    can redirect it at a temp file the way it redirects the scan itself."""
    return path.relative_to(REPO_ROOT).as_posix()


def _is_settings_call(value: ast.AST) -> bool:
    """True when *value* is a call to ``value_label_settings``, any alias."""
    if not isinstance(value, ast.Call):
        return False
    fn = value.func
    name = fn.id if isinstance(fn, ast.Name) else (
        fn.attr if isinstance(fn, ast.Attribute) else "")
    return name.endswith("value_label_settings")


def _names_from_settings(tree: ast.AST) -> set[str]:
    """Every local name in *tree* bound from a ``value_label_settings`` call.

    Both real call sites unpack the pair into locals before passing it, and
    they have to: the same pair also decides the Fraction panel's clamp, and a
    surface that read it twice could answer the two questions differently. So
    the guard follows the binding rather than demanding the call be written
    inline — a `value_labels=_value_labels` is honest, a
    `value_labels=False` is the divergence wearing the guard's own uniform.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not _is_settings_call(node.value):
            continue
        for target in node.targets:
            for name in ast.walk(target):
                if isinstance(name, ast.Name):
                    bound.add(name.id)
    return bound


def _resolves_the_overlay(value: ast.AST, bound: set[str]) -> bool:
    """True when *value* states the overlay rather than switching it off.

    The call itself, a name bound from it, or a subscript of one (``[0]``, the
    boolean half). A literal never counts: ``value_labels=(False, "")`` and
    ``value_labels=False`` both satisfy a presence check while turning the
    overlay off on that surface alone, which is how the colour-mode guard
    beside this one learned to check the value and not the keyword.
    """
    if _is_settings_call(value):
        return True
    if isinstance(value, ast.Subscript):
        return _resolves_the_overlay(value.value, bound)
    if isinstance(value, ast.Name):
        return value.id in bound
    return False


def _call_name(node: ast.Call):
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return None


def _hands_over_a_ctx(node: ast.Call) -> bool:
    """True when the call passes a ``ctx=`` that is not the literal ``None``.

    That is the tab's route: the renderer reads the overlay off the ctx it was
    given. ``ctx=None`` is the batch route and does not count — it is precisely
    the barrier this guard exists for.
    """
    kw = next((k for k in node.keywords if k.arg == "ctx"), None)
    if kw is None:
        return False
    return not (isinstance(kw.value, ast.Constant) and kw.value.value is None)


def _offending_calls(tree: ast.AST, names: set[str], *, allow_ctx: bool) -> list[int]:
    bound = _names_from_settings(tree)
    out: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) not in names:
            continue
        if any(kw.arg is None for kw in node.keywords):
            continue  # ``**kwargs`` forwarding — decided upstream.
        if allow_ctx and _hands_over_a_ctx(node):
            continue
        kw = next((k for k in node.keywords if k.arg == "value_labels"), None)
        if kw is not None and _resolves_the_overlay(kw.value, bound):
            continue
        out.append(node.lineno)
    return out


def _scan(names: set[str], definition: str, *, allow_ctx: bool) -> list[str]:
    offenders: list[str] = []
    for path in python_sources():
        relpath = rel(path)
        if relpath == definition:
            continue
        try:
            tree = parse_source(path)
        except (OSError, SyntaxError):
            continue
        for lineno in _offending_calls(tree, names, allow_ctx=allow_ctx):
            offenders.append(f"{relpath}:{lineno}")
    return offenders


def _call_sites(names: set[str], definition: str) -> set[str]:
    found: set[str] = set()
    for path in python_sources():
        relpath = rel(path)
        if relpath == definition:
            continue
        try:
            tree = parse_source(path)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) in names:
                found.add(relpath)
    return found


def test_every_bar_render_call_states_the_overlay():
    offenders = _scan(_BAR_CALL_NAMES, _BAR_DEFINITION, allow_ctx=True)
    assert not offenders, (
        "render_bar_items called with neither a real ctx nor "
        "value_labels=value_label_settings(<ctx>) — the value-label overlay "
        "would be dropped on this surface while the other honours it "
        "(CLAUDE.md §7.5 #5):\n  " + "\n  ".join(offenders)
    )


def test_every_line_render_call_states_the_overlay():
    offenders = _scan(_LINE_CALL_NAMES, _LINE_DEFINITION, allow_ctx=False)
    assert not offenders, (
        "render_line_series called without "
        "value_labels=value_label_settings(<ctx>)[0] — the batch line figure "
        "and the tab would disagree about the overlay:\n  "
        + "\n  ".join(offenders)
    )


def test_the_guard_can_see_every_known_call_site():
    """A scan surface reaching none of the call sites would pass silently,
    which is §7.5 #3 — the guard measuring nothing and reporting success."""
    bar = _call_sites(_BAR_CALL_NAMES, _BAR_DEFINITION)
    line = _call_sites(_LINE_CALL_NAMES, _LINE_DEFINITION)
    assert {"review/features/barplot/renderer.py",
            "review/features/batch_export/bar_panel.py"} <= bar, sorted(bar)
    assert {"review/features/batch_export/base_panel.py",
            "review/features/lineplot/controller.py"} <= line, sorted(line)


def test_the_per_cell_events_panel_reads_the_overlay():
    """The AST half of the per-cell path: it takes a ctx, so it resolves the
    overlay itself rather than being handed it, and must actually ask."""
    tree = parse_source(REPO_ROOT / _RENDERER_MODULE)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "draw_per_well_n_bars"), None)
    assert fn is not None, f"draw_per_well_n_bars is gone from {_RENDERER_MODULE}"
    asks = any(isinstance(n, ast.Call) and _is_settings_call(n) for n in ast.walk(fn))
    labels = any(isinstance(n, ast.Call) and _call_name(n) == "label_bar"
                 for n in ast.walk(fn))
    assert asks and labels, (
        "draw_per_well_n_bars draws the same mean-and-spread the aggregate path "
        f"does (asks={asks}, labels={labels}) — a rule honoured on one of the "
        "two functions that draw one panel is the half-sweep, not the fix")


# ── and the same path, actually drawn ────────────────────────────────────────

def _percell_ctx(*, labels: bool, fmt: str = ""):
    return types.SimpleNamespace(
        state=types.SimpleNamespace(
            plot=types.SimpleNamespace(
                use_fov_replicates=True, use_sem=True,
                bar_err_kind="sem", bar_show_mode="mean_spread",
                show_value_labels=labels, value_label_format=fmt,
            ),
            channel=types.SimpleNamespace(
                active_val_col="w2_mean_intensity", active_metric="mean_intensity",
            ),
        ),
    )


def _draw_percell(ctx, monkeypatch):
    # AggPoint: 0 = timepoint, 4 = total n_above, 7 = per-FOV mean, 8 = spread.
    pt = (0.0, 0.0, 0.0, 0.0, 40.0, 0.0, 0.0, 10.0, 2.0, 0.0)
    monkeypatch.setattr(renderer, "get_all_fluor_gates", lambda _c: {})
    monkeypatch.setattr("review.domain.stats_compute.aggregate_group",
                        lambda *a, **k: [pt])
    fig, ax = plt.subplots()
    renderer.draw_per_well_n_bars(ctx, ax, ["A01"], ["#888888"], ["A01"], 0.0, 0.0,
                                  pool_groups={"A01": ["A01"]})
    texts = [t.get_text() for t in ax.texts]
    ylim = ax.get_ylim()
    plt.close(fig)
    return texts, ylim


def test_the_per_cell_events_panel_draws_no_label_when_the_overlay_is_off(monkeypatch):
    texts, _ = _draw_percell(_percell_ctx(labels=False), monkeypatch)
    assert texts == []


def test_the_per_cell_events_panel_labels_its_bars(monkeypatch):
    texts, _ = _draw_percell(_percell_ctx(labels=True), monkeypatch)
    assert texts == ["10 ± 2"]


def test_the_per_cell_events_panel_honours_a_typed_format(monkeypatch):
    texts, _ = _draw_percell(_percell_ctx(labels=True, fmt="%.2f"), monkeypatch)
    assert texts == ["10.00 ± 2.00"]


def test_the_per_cell_events_panel_makes_room(monkeypatch):
    _, off = _draw_percell(_percell_ctx(labels=False), monkeypatch)
    _, on = _draw_percell(_percell_ctx(labels=True), monkeypatch)
    assert on[1] > off[1], (
        f"the panel reserved no room for its labels (off={off}, on={on})")


def test_the_per_cell_events_panel_states_no_spread_it_did_not_draw(monkeypatch):
    ctx = _percell_ctx(labels=True)
    ctx.state.plot.bar_err_kind = "none"
    texts, _ = _draw_percell(ctx, monkeypatch)
    assert texts == ["10"], (
        f"error bars are off and the label still claims a spread: {texts}")


def test_the_per_cell_events_panel_states_the_confidence_interval(monkeypatch):
    ctx = _percell_ctx(labels=True)
    ctx.state.plot.bar_err_kind = "ci95"
    texts, _ = _draw_percell(ctx, monkeypatch)
    assert texts == ["10 ± 3.92"], (
        f"the panel draws a 1.96x whisker; the label must say so: {texts}")


@pytest.mark.parametrize("mode", ["mean", "mean_spread"])
def test_the_per_cell_label_agrees_with_its_own_whisker(mode, monkeypatch):
    """The label and the error bar are two statements about one number. This
    is the pairing §7.4 #1 asks for: assert what reached the axes, both of it."""
    ctx = _percell_ctx(labels=True)
    ctx.state.plot.bar_show_mode = mode
    texts, _ = _draw_percell(ctx, monkeypatch)
    assert ("±" in texts[0]) is (mode != "mean")
