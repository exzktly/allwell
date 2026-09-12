"""A legend swatch looks like the trace it names.

Reported: the legend does not follow the line weight when it is changed in the
Properties sidebar. It follows none of them — matplotlib builds a legend from
*copies* of the artists, taken when the legend is created, so everything
``apply_export_style_prefs`` then does to the data lines lands on the originals
and on nothing else. Measured before the fix: raising Line width to 6 drew
six-point traces beside a legend still showing the two-point ones it was born
with, and the dash, the marker shape and the marker size were dropped the same
way.

The swatch is **copied from its own trace** rather than restyled from the prefs
a second time, and that is the part worth guarding. Re-deriving would be right
on the Line tab in plate mode and wrong in grey mode, where the dash and the
marker identify the trace rather than coming from a pref — a legend rebuilt
from the prefs there would show twelve identical swatches beside twelve
distinguishable traces, which is worse than the bug being fixed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("matplotlib")

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import to_hex  # noqa: E402

from review.core.plot_style import (  # noqa: E402
    DEFAULT_EXPORT_STYLE_PREFS,
    apply_export_style_prefs,
    sync_legend_handles,
)
from review.features.lineplot.controller import (  # noqa: E402
    LineAxes,
    LinePoint,
    LineSeries,
    render_line_series,
)


@pytest.fixture(autouse=True)
def _close_figures():
    yield
    plt.close("all")


def _series(n: int):
    return [
        LineSeries(
            key=f"A{i:02d}", display=f"A{i:02d}", color="#4488cc", kind="well",
            wells=[f"A{i:02d}"],
            points=[LinePoint(t=float(t), mean=10.0 + i + t, spread=0.5,
                              frac=0.2, n_above=5.0 + i, n_total=100,
                              frac_spread=0.02, n_above_spread=0.4)
                    for t in (0, 4, 8)],
            cdf_vals=np.array([1.0, 2.0, 3.0]),
        )
        for i in range(n)
    ]


def _render(mode: str, n: int = 4):
    fig, (axm, axf, axn, axc) = plt.subplots(4, 1, figsize=(6, 12))
    render_line_series(
        LineAxes(axm, axf, axn, axc), _series(n), fov_active=False,
        fc_active=False, threshold=1.0, legend_kw={"fontsize": 7},
        warn_color="#ff0000", muted_fg="#333333", color_mode=mode,
    )
    return fig, axm


def _traces(ax):
    return [ln for ln in ax.lines
            if not str(ln.get_label()).startswith(("_", "threshold="))]


def _handles(ax):
    leg = ax.get_legend()
    if leg is None:
        return []
    return list(getattr(leg, "legend_handles", None)
                or getattr(leg, "legendHandles", []))


def _look(artist):
    return (artist.get_linewidth(), artist.get_linestyle(),
            artist.get_marker(), artist.get_markersize(),
            str(artist.get_markerfacecolor()))


def _apply(fig, **over):
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs.update(over)
    apply_export_style_prefs(fig, prefs)


# ── the reported case ───────────────────────────────────────────────────────

def test_the_legend_follows_the_line_width():
    fig, axm = _render("plate")
    _apply(fig, line_width=6.0)
    assert {ln.get_linewidth() for ln in _traces(axm)} == {6.0}
    assert {h.get_linewidth() for h in _handles(axm)} == {6.0}, (
        "the traces are six points wide and the legend still shows two")


@pytest.mark.parametrize("pref,getter,expected", [
    ("line_width", "get_linewidth", 6.0),
    ("line_style", "get_linestyle", "--"),
    ("marker_style", "get_marker", "D"),
    ("marker_size", "get_markersize", 12.0),
])
def test_the_legend_follows_every_pref_that_reaches_the_trace(pref, getter,
                                                              expected):
    """Line width was what was reported; all four were dropped by the same
    mechanism. Fixing only the reported one would leave a legend that is right
    about the weight and wrong about the shape — the half-sweep this repository
    keeps paying for."""
    fig, axm = _render("plate")
    _apply(fig, **{pref: expected})
    drawn = {getattr(ln, getter)() for ln in _traces(axm)}
    shown = {getattr(h, getter)() for h in _handles(axm)}
    assert drawn == {expected}, f"the pref did not reach the traces: {drawn}"
    assert shown == drawn, f"the legend shows {shown} beside traces at {drawn}"


def test_every_swatch_matches_its_own_trace():
    fig, axm = _render("plate")
    _apply(fig, line_width=4.0, marker_style="s", marker_size=9.0)
    for trace, handle in zip(_traces(axm), _handles(axm)):
        assert _look(handle) == _look(trace)


# ── the mode the fix must not break ─────────────────────────────────────────

def test_a_grey_legend_keeps_the_cues_that_name_each_trace():
    """The reason the swatch copies its trace instead of re-reading the prefs.
    In grey mode the dash and the marker are the trace's identity, so a legend
    rebuilt from one pref would collapse twelve entries onto one look while the
    plot went on drawing twelve."""
    fig, axm = _render("grey", n=12)
    _apply(fig, line_width=3.0, line_style="-", marker_style="D")
    shown = [_look(h) for h in _handles(axm)]
    assert len(set(shown)) == len(shown), (
        f"the grey legend has repeated swatches: {shown}")
    for trace, handle in zip(_traces(axm), _handles(axm)):
        assert _look(handle) == _look(trace)


def test_the_swatch_keeps_its_own_colour():
    """Colour is deliberately not copied — the swatch already carries it, and
    it is the one property a *theme* flip rather than a pref is entitled to
    move. Copying it would be harmless today and a trap the day the two
    diverge."""
    fig, axm = _render("grey", n=5)
    _apply(fig, line_width=3.0)
    for trace, handle in zip(_traces(axm), _handles(axm)):
        assert to_hex(handle.get_color()) == to_hex(trace.get_color())


# ── the pairing ─────────────────────────────────────────────────────────────

def test_an_entry_that_names_no_trace_is_left_alone():
    """``_capped_legend`` truncates a long legend and appends a "+N more" entry
    that stands for nothing. Pairing by position rather than by label would
    shift every swatch by one the moment a plate-wide selection overflowed —
    so the legend would be *confidently* wrong instead of merely stale."""
    from review.features.lineplot.controller import _capped_legend

    fig, ax = plt.subplots()
    for i in range(4):
        ax.plot([0, 1], [i, i + 1], label=f"A{i:02d}", lw=1.0, marker="o")
    leg = _capped_legend(ax, {"fontsize": 7}, 2)
    assert "more" in leg.get_texts()[-1].get_text()

    for ln in ax.lines:
        ln.set_linewidth(5.0)
    spacer = list(getattr(leg, "legend_handles", []))[-1]
    before = spacer.get_linewidth()
    sync_legend_handles(ax)

    named = list(getattr(leg, "legend_handles", []))[:2]
    assert {h.get_linewidth() for h in named} == {5.0}
    assert spacer.get_linewidth() == before, (
        "the '+N more' spacer was styled as though it named a trace")


def test_a_swatch_tracks_the_trace_it_names_after_a_reorder():
    """Labels, not positions. The Line tab's drag-reorder rewrites the draw
    order, and a legend paired by index would then name each trace with the
    look of a different one — a wrong figure that reads as a right one."""
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1], label="A01", lw=1.0, linestyle="-")
    ax.plot([0, 1], [1, 2], label="A02", lw=1.0, linestyle=":")
    ax.legend()
    ax.lines[0].set_linewidth(7.0)
    sync_legend_handles(ax)
    by_label = {t.get_text(): h for h, t in
                zip(getattr(ax.get_legend(), "legend_handles", []),
                    ax.get_legend().get_texts())}
    assert by_label["A01"].get_linewidth() == 7.0
    assert by_label["A02"].get_linewidth() == 1.0


def test_an_axes_with_no_legend_is_not_an_error():
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1], label="A01")
    sync_legend_handles(ax)  # must not raise
