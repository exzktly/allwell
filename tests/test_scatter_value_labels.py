"""Properties → Overlays → "Show value labels" on the Scatter tab.

The switch is global — it sits in the Properties sidebar on every plot tab —
and it drew nothing at all on the Scatter tab, on either of that tab's two
views. Reported as "show value labels does not work on scatter plot", which is
exactly what a control that is offered and does nothing looks like from the
outside (CLAUDE.md §7.6).

Both halves of the fix are pinned here, and both are asserted on the **axes**
rather than on the formatter's return value: a formatter that is right is not
evidence that anything was drawn.

* the **Aggregate** view labels every point with both of its coordinates,
  because on that figure both axes carry a ``mean ± spread``;
* the **per-cell** view still draws no labels — one point there is one cell,
  not a statistic — and says so on the figure, so the shortfall is visible in
  the artifact instead of reading as the same bug again.

The two ``redraw_*`` tests drive the real entry points with a stub ctx rather
than calling the labelling helper directly (§7.6 #2): the helper being correct
is what the previous version of this feature's bugs all had in common.
"""

from __future__ import annotations

import math
import types

import pytest

pytest.importorskip("matplotlib")

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


@pytest.fixture(autouse=True)
def _close_figures():
    """These tests measure drawn figures, so they cannot close as they go.
    Closing after each keeps the file off the fast lane's figure-leak radar."""
    yield
    plt.close("all")


from tests._repo_scan import REPO_ROOT, parse_source, python_sources, rel  # noqa: E402
from review.core.plot_annotations import (  # noqa: E402
    LABEL_SPAN_HEADROOM,
    format_point_label,
)
from review.core.plot_state import PlotState  # noqa: E402
from review.features.scatter.state import ScatterState  # noqa: E402
from review.features.scatter import controller as sc  # noqa: E402
from review.features.scatter._annotations import (  # noqa: E402
    PER_CELL_NOTE,
    VALUE_LABEL_NOTE_TAG,
    annotate_agg_points,
    note_value_labels_unavailable,
)


# ── the text ────────────────────────────────────────────────────────────────

def test_a_point_states_both_of_its_coordinates():
    # Unlike a bar, both axes here are the measured statistic; labelling the y
    # alone would drop half of what the point says.
    assert format_point_label(1200.0, 0.42, 85.0, 0.05) == "(1200 ± 85, 0.420 ± 0.050)"


@pytest.mark.parametrize("spread", [None, float("nan"), 0.0, -1.0])
def test_a_spread_that_was_not_measured_is_not_printed_as_zero(spread):
    # The same rule the bar labels keep: "± 0" claims the replicates agreed.
    label = format_point_label(1200.0, 0.42, spread, spread)
    assert "±" not in label, f"spread={spread!r} printed a ± in {label!r}"


def test_a_point_with_an_unprintable_coordinate_gets_no_label():
    # Half a label beside a marker on two measured axes does not say which
    # axis its number belongs to.
    assert format_point_label(float("nan"), 0.42, 1.0, 0.05) == ""
    assert format_point_label(1200.0, float("inf"), 1.0, 0.05) == ""


def test_a_users_format_governs_both_coordinates():
    assert format_point_label(1200.0, 0.42, fmt="%.1e") == "(1.2e+03, 4.2e-01)"


# ── the aggregate figure ────────────────────────────────────────────────────

def _agg_data(**over):
    data = {
        "x": [1200.0], "y": [0.42],
        "x_err": [85.0], "y_err": [0.05],
        "color": "#1f77b4", "marker": "o", "timepoint": 6.0,
        "label": "A01 (t=6.0h)", "group_label": "A01",
    }
    data.update(over)
    return {"A01_tp6.0": data}


def _draw_agg(data, settings):
    fig, ax = plt.subplots(figsize=(6.0, 4.5))
    for d in data.values():
        ax.errorbar(d["x"], d["y"], xerr=d["x_err"], yerr=d["y_err"],
                    color=d["color"], marker=d["marker"], linestyle="none")
    annotate_agg_points(settings, ax, data)
    return ax


def _texts(ax):
    return [t.get_text() for t in ax.texts]


def test_the_aggregate_points_are_unlabelled_when_the_overlay_is_off():
    assert _texts(_draw_agg(_agg_data(), (False, ""))) == []


def test_every_aggregate_point_is_labelled_when_the_overlay_is_on():
    assert _texts(_draw_agg(_agg_data(), (True, ""))) == ["(1200 ± 85, 0.420 ± 0.050)"]


def test_a_point_whose_spread_was_not_computed_states_its_value_alone():
    ax = _draw_agg(_agg_data(x_err=[float("nan")], y_err=[float("nan")]),
                   (True, ""))
    assert _texts(ax) == ["(1200, 0.420)"]


def test_a_label_wears_its_own_point_s_colour():
    # Colour is data on this figure — it is the timepoint's place on the
    # viridis gradient — so the label matches its marker and is not repainted
    # into the theme's ink on a publication flip.
    ax = _draw_agg(_agg_data(), (True, ""))
    assert ax.texts[0].get_color() == "#1f77b4"


def test_the_figure_makes_room_for_the_labels():
    off = _draw_agg(_agg_data(), (False, "")).get_ylim()[1]
    on = _draw_agg(_agg_data(), (True, "")).get_ylim()[1]
    assert on > off, (
        "the labels are drawn above the error cap in ink, which matplotlib's "
        f"own margin knows nothing about (off={off}, on={on})")


def test_the_headroom_on_a_log_axis_is_a_widening_and_stays_positive():
    # A linear pad added to a log axis is either invisible at the top or an
    # invalid limit at the floor.
    fig, ax = plt.subplots()
    ax.set_yscale("log")
    ax.set_ylim(1.0, 1000.0)
    from review.features.scatter._annotations import pad_axes_for_labels
    pad_axes_for_labels(ax)
    lo, hi = ax.get_ylim()
    assert lo > 0 and hi > 1000.0
    assert math.isclose(hi, 1000.0 * 1000.0 ** (LABEL_SPAN_HEADROOM - 1.0))


def test_the_headroom_never_crops_a_panel_holding_negatives():
    fig, ax = plt.subplots()
    ax.set_ylim(-5.0, -1.0)
    from review.features.scatter._annotations import pad_axes_for_labels
    pad_axes_for_labels(ax)
    lo, hi = ax.get_ylim()
    assert lo <= -5.0 and hi >= -1.0


def test_a_users_format_reaches_the_points():
    assert _texts(_draw_agg(_agg_data(), (True, "%.1e"))) == [
        "(1.2e+03 ± 8.5e+01, 4.2e-01 ± 5.0e-02)"]


# ── the tab's own redraw, driven end to end ─────────────────────────────────

class _Registry:
    def __init__(self, widgets):
        self._widgets = dict(widgets)

    def get_or_none(self, name):
        return self._widgets.get(name)


def _stub_ctx(widgets, *, labels_on: bool, fmt: str = ""):
    plot = PlotState()
    plot.show_value_labels = labels_on
    plot.value_label_format = fmt
    return types.SimpleNamespace(
        widgets=_Registry(widgets),
        state=types.SimpleNamespace(
            plot=plot,
            scatter=ScatterState(),
            export_prefs=types.SimpleNamespace(style_prefs={}),
        ),
        runtime=types.SimpleNamespace(
            scatter=types.SimpleNamespace(interaction_cache={}, metadata=None),
        ),
    )


class _Canvas:
    def draw(self):
        pass


@pytest.mark.parametrize("labels_on,expected", [
    (True, ["(1200 ± 85, 0.420 ± 0.050)"]),
    (False, []),
])
def test_the_aggregate_redraw_labels_its_points(monkeypatch, labels_on, expected):
    """The seam that was missing: the overlay state has to be read *by the
    redraw the tab actually calls*, not only by a helper that can label an
    axes. Nothing below stubs the labelling."""
    fig, ax = plt.subplots()
    ctx = _stub_ctx({
        "_ax_scatter_agg": ax, "_scatter_agg_fig": fig,
        "_scatter_agg_canvas": _Canvas(),
    }, labels_on=labels_on)
    monkeypatch.setattr(sc, "has_plottable_selection", lambda _c: True)
    monkeypatch.setattr(sc, "collect_scatter_agg_data",
                        lambda *a, **k: _agg_data())
    sc.redraw_scatter_agg(ctx, "Mean Fluorescence GFP", "Fraction On GFP",
                          [6.0], well_colors=["#1f77b4"])
    assert [t.get_text() for t in ax.texts] == expected


def test_the_per_cell_redraw_says_the_overlay_draws_nothing_here(monkeypatch):
    """A control that is on and changes nothing is the bug that was reported.
    The per-cell view has no statistic to state, so it says that on the figure
    rather than staying silent."""
    fig, ax = plt.subplots()
    ctx = _stub_ctx({
        "_ax_scatter": ax, "_scatter_fig": fig, "_scatter_canvas": _Canvas(),
        "_scatter_layout_var": None,
    }, labels_on=True)
    monkeypatch.setattr(sc, "has_plottable_selection", lambda _c: True)
    monkeypatch.setattr(sc, "collect_scatter_data", lambda *a, **k: {
        "A01": {"x": [1.0, 2.0], "y": [3.0, 4.0], "color": "#1f77b4",
                "metadata": [(), ()], "tp": [6.0, 6.0], "label": "A01"},
    })
    monkeypatch.setattr(sc, "render_scatter_points", lambda *a, **k: ([], []))
    monkeypatch.setattr(sc, "paint_gates", lambda *a, **k: None)
    monkeypatch.setattr(sc, "refresh_gate_selector", lambda *a, **k: None)
    sc.redraw_scatter(ctx, "gfp_mean_intensity", "mcherry_mean_intensity",
                      [6.0], well_colors=["#1f77b4"])
    notes = [t.get_text() for t in ax.texts
             if getattr(t, VALUE_LABEL_NOTE_TAG, False)]
    assert notes == [PER_CELL_NOTE]
    # And no per-cell point carries a number of its own.
    assert [t.get_text() for t in ax.texts if "±" in t.get_text()] == []


def test_the_per_cell_note_is_absent_when_the_overlay_is_off():
    # Nothing to explain on a figure whose overlay was never asked for; a note
    # on every scatter export would be noise.
    fig, ax = plt.subplots()
    note_value_labels_unavailable((False, ""), ax)
    assert list(ax.texts) == []


# ── the sweep: every surface that draws these figures ───────────────────────
#
# Four call sites draw a scatter, and only two of them have a cheap runtime
# test above (the tab's own two redraws). The other two — the stacked
# "Separate axes" per-cell layout, and Batch Export's two aggregate jobs —
# each want a loaded plate and a built Qt panel, which is exactly the shape
# §7.5 #5 warns about: a rule honoured at two of four sites leaves one
# function holding two meanings of the same switch and neither looking wrong.
# So the syntax tree decides it (§7.6 #3).
#
# Each row carries the reason it is in the roster, and every row is checked to
# still name a real function — a roster entry that has stopped describing a
# real site is indistinguishable from one that was never needed.

#: ``(file, function, called-name)`` — a function that draws a scatter figure,
#: and the overlay call it must make.
_LABELLED_SITES = (
    # The stacked per-cell layout draws the same one-point-per-cell clouds the
    # overlay path does, so it owes the same note.
    ("review/features/scatter/_layout.py", "_render_scatter_faceted",
     "note_value_labels_unavailable"),
    # Batch Export's aggregate figures are the twins of the tab's aggregate:
    # a plate exported in bulk is labelled like the figure the switch was set
    # on, exactly as the bar and line exports already are.
    ("review/features/batch_export/scatter_panel.py",
     "_run_scatter_agg_job", "annotate_agg_points"),
    ("review/features/batch_export/scatter_panel.py",
     "_run_scatter_agg_multi_tp_job", "annotate_agg_points"),
)


def _function_named(tree, name: str):
    import ast as _ast
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _call_name(call) -> str:
    """The bare name a ``Call`` node invokes — ``f()``, ``mod.f()`` alike."""
    import ast as _ast
    fn = call.func
    if isinstance(fn, _ast.Name):
        return fn.id
    return fn.attr if isinstance(fn, _ast.Attribute) else ""


def _called_names(node) -> set[str]:
    import ast as _ast
    names: set[str] = set()
    for sub in _ast.walk(node):
        if isinstance(sub, _ast.Call):
            names.add(_call_name(sub).lstrip("_"))
    return names


def test_every_scatter_surface_answers_the_overlay():
    offenders: list[str] = []
    for path, func, call in _LABELLED_SITES:
        tree = parse_source(REPO_ROOT / path)
        node = _function_named(tree, func)
        if node is None:
            offenders.append(
                f"{path}::{func} no longer exists — the roster entry is stale, "
                "so nothing here is measuring what it claims to")
            continue
        if call.lstrip("_") not in _called_names(node):
            offenders.append(f"{path}::{func} never calls {call}()")
    assert not offenders, (
        "a surface that draws a scatter figure ignores the value-label "
        "switch, which is global — so it either draws unlabelled points "
        "beside a labelled twin, or says nothing on a view that cannot carry "
        "labels, which is the bug this file exists for:\n  "
        + "\n  ".join(offenders))


def test_the_overlay_state_is_read_from_the_plot_state_at_every_call_site():
    """``annotate_agg_points`` takes the ``(enabled, fmt)`` pair rather than a
    ctx, so that Batch Export — which renders from its own ctx — can hand it
    over. The cost of that seam is that a call site can pass anything at all;
    a hardcoded ``(True, "")`` would draw labels on an export whose switch was
    off. Every call passes the pair, or a local bound from it."""
    import ast as _ast

    offenders: list[str] = []
    for path in python_sources():
        tree = parse_source(path)
        bound: set[str] = set()
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Assign) or not isinstance(node.value, _ast.Call):
                continue
            if not _call_name(node.value).lstrip("_").endswith("value_label_settings"):
                continue
            for tgt in node.targets:
                if isinstance(tgt, _ast.Name):
                    bound.add(tgt.id)
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Call):
                continue
            if _call_name(node).lstrip("_") not in ("annotate_agg_points",
                                                    "note_value_labels_unavailable"):
                continue
            if not node.args:
                offenders.append(f"{rel(path)}:{node.lineno} — no settings argument")
                continue
            first = node.args[0]
            ok = (isinstance(first, _ast.Name) and first.id in bound) or (
                isinstance(first, _ast.Call)
                and _call_name(first).lstrip("_").endswith("value_label_settings"))
            if not ok:
                offenders.append(
                    f"{rel(path)}:{node.lineno} — settings not read from the plot state")
    assert not offenders, (
        "a value-label call site invents its own overlay state:\n  "
        + "\n  ".join(offenders))
