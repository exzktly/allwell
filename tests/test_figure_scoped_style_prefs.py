"""Every Properties preference is per figure — marks first, then everything.

The Properties sidebar used to hold every style pref in one app-wide dict, so
restyling the Scatter tab's Per-well aggregate restyled its Per-cell sub-tab —
and the line and bar plots — with it. Mark size in particular is a property of
what a plot draws, not of the publication: five aggregate points want an 8pt
marker, a 50,000-cell cloud wants 2pt.

Then the request: "everywhere in the app, changes made to the Properties bar
should affect only the plot for which it is edited." Typography, grid, legend,
ticks, layout, the export profile and format had stayed app-wide "so one setting
gives a whole figure set a consistent look", which is exactly what made a font
raised on one plot rise on every plot. ``plot_style.FIGURE_SCOPED_PREF_KEYS``
now holds every key. These pin scoped writes for every family, and the app-wide
fallback that keeps a style saved before the change applying until a figure is
given its own.
"""
from __future__ import annotations

from types import SimpleNamespace

from review.core.export_prefs_state import ExportPrefsState
from review.core.plot_style import (
    AXIS_SCOPED_PREF_KEYS,
    DEFAULT_EXPORT_STYLE_PREFS,
    FIGURE_SCOPED_PREF_KEYS,
    MARK_SCOPED_PREF_KEYS,
    figure_prefs,
)


class _Fig:
    """Stand-in for a matplotlib Figure — only identity matters here."""


def _ctx(cells_fig, agg_fig, *, style_prefs=None):
    widgets = {"_scatter_fig": cells_fig, "_scatter_agg_fig": agg_fig}
    prefs = ExportPrefsState()
    if style_prefs is not None:
        prefs.style_prefs = dict(style_prefs)
    return SimpleNamespace(
        state=SimpleNamespace(export_prefs=prefs),
        widgets=SimpleNamespace(get_or_none=widgets.get),
    )


def test_every_preference_is_scoped_to_its_figure():
    assert not (AXIS_SCOPED_PREF_KEYS & MARK_SCOPED_PREF_KEYS)
    assert FIGURE_SCOPED_PREF_KEYS >= set(DEFAULT_EXPORT_STYLE_PREFS) | AXIS_SCOPED_PREF_KEYS | MARK_SCOPED_PREF_KEYS


def test_every_mark_key_has_a_neutral_default():
    """A mark key with no default would read as ``None`` on a fresh figure and
    reach matplotlib as one. (``axis_buckets`` is the deliberate exception in
    the axis family — it is bookkeeping, has no default, and every reader spells
    it ``prefs.get("axis_buckets") or {}``.)"""
    for key in MARK_SCOPED_PREF_KEYS:
        assert key in DEFAULT_EXPORT_STYLE_PREFS, key


def test_mark_style_set_on_one_figure_does_not_reach_the_other():
    """The reported bug: styling Per-well aggregate restyled Per-cell points."""
    cells, agg = _Fig(), _Fig()
    ctx = _ctx(cells, agg)

    figure_prefs(ctx, agg)["marker_size"] = 12.0

    assert figure_prefs(ctx, agg)["marker_size"] == 12.0
    assert figure_prefs(ctx, cells)["marker_size"] == DEFAULT_EXPORT_STYLE_PREFS["marker_size"]


def test_each_figure_keeps_its_own_marker_shape():
    cells, agg = _Fig(), _Fig()
    ctx = _ctx(cells, agg)

    figure_prefs(ctx, agg)["marker_style"] = "s"
    figure_prefs(ctx, cells)["marker_style"] = "."

    assert figure_prefs(ctx, agg)["marker_style"] == "s"
    assert figure_prefs(ctx, cells)["marker_style"] == "."


def test_typography_grid_legend_ticks_layout_and_export_settings_stay_on_their_figure():
    """The request: an edit in the Properties bar affects only the plot it was made on — the families that used to
    be app-wide included."""
    cells, agg = _Fig(), _Fig()
    ctx = _ctx(cells, agg)
    edits = {"axis_label_size": 24, "tick_label_size": 7, "title_size": 30, "legend_font_size": 5,
             "legend_show": False, "grid_show": False, "tick_direction": "in", "layout_tight": True,
             "x_tick_angle": 45, "format": "svg", "export_profile": "Helvetica 22"}
    for key, value in edits.items():
        assert DEFAULT_EXPORT_STYLE_PREFS[key] != value, key          # so an unscoped write would show
        figure_prefs(ctx, agg)[key] = value

    for key, value in edits.items():
        assert figure_prefs(ctx, agg)[key] == value, key
        assert figure_prefs(ctx, cells)[key] == DEFAULT_EXPORT_STYLE_PREFS[key], key
    assert ctx.state.export_prefs.style_prefs is None or all(
        ctx.state.export_prefs.style_prefs.get(k) == DEFAULT_EXPORT_STYLE_PREFS[k] for k in edits)


def test_a_style_saved_before_preferences_became_per_plot_still_applies_until_a_plot_is_restyled():
    cells, agg = _Fig(), _Fig()
    ctx = _ctx(cells, agg, style_prefs={**DEFAULT_EXPORT_STYLE_PREFS, "axis_label_size": 9})
    assert figure_prefs(ctx, cells)["axis_label_size"] == 9 and figure_prefs(ctx, agg)["axis_label_size"] == 9
    figure_prefs(ctx, agg)["axis_label_size"] = 14
    assert figure_prefs(ctx, agg)["axis_label_size"] == 14 and figure_prefs(ctx, cells)["axis_label_size"] == 9


def test_axis_limits_stay_scoped_and_never_read_the_global():
    """Unchanged behaviour, re-pinned: a limit typed on one figure is
    meaningless on another, so it must not be visible there — not even as a
    fallback through the global dict."""
    cells, agg = _Fig(), _Fig()
    ctx = _ctx(cells, agg, style_prefs={**DEFAULT_EXPORT_STYLE_PREFS, "y_lim_max": "999"})

    assert figure_prefs(ctx, cells)["y_lim_max"] == DEFAULT_EXPORT_STYLE_PREFS["y_lim_max"]

    figure_prefs(ctx, agg)["y_lim_max"] = "50"

    assert figure_prefs(ctx, agg)["y_lim_max"] == "50"
    assert figure_prefs(ctx, cells)["y_lim_max"] == DEFAULT_EXPORT_STYLE_PREFS["y_lim_max"]


def test_a_mark_style_saved_before_the_split_still_applies_everywhere():
    """Migration: the pre-split value lives in the global dict. Nothing writes
    those globals now, so it reads as the baseline for every figure that has no
    value of its own — rather than silently reverting to the stock 5pt circle."""
    cells, agg = _Fig(), _Fig()
    ctx = _ctx(cells, agg, style_prefs={**DEFAULT_EXPORT_STYLE_PREFS, "marker_size": 11.0})

    assert figure_prefs(ctx, cells)["marker_size"] == 11.0
    assert figure_prefs(ctx, agg)["marker_size"] == 11.0

    # …until one figure is given its own, which must not disturb the other.
    figure_prefs(ctx, agg)["marker_size"] = 3.0

    assert figure_prefs(ctx, agg)["marker_size"] == 3.0
    assert figure_prefs(ctx, cells)["marker_size"] == 11.0


def test_a_scoped_write_never_touches_the_shared_dict():
    cells, agg = _Fig(), _Fig()
    ctx = _ctx(cells, agg, style_prefs=DEFAULT_EXPORT_STYLE_PREFS)

    figure_prefs(ctx, agg)["marker_size"] = 12.0
    figure_prefs(ctx, agg)["line_width"] = 4.0

    shared = ctx.state.export_prefs.style_prefs
    assert shared["marker_size"] == DEFAULT_EXPORT_STYLE_PREFS["marker_size"]
    assert shared["line_width"] == DEFAULT_EXPORT_STYLE_PREFS["line_width"]
    assert ctx.state.export_prefs.axis_prefs["scatter_agg"]["marker_size"] == 12.0
    assert ctx.state.export_prefs.axis_prefs["scatter_agg"]["line_width"] == 4.0


def test_the_view_still_behaves_like_a_dict():
    """Call sites do ``dict(prefs)`` / ``in`` / ``.get`` — every scoped key must
    be enumerated exactly once."""
    cells, agg = _Fig(), _Fig()
    ctx = _ctx(cells, agg, style_prefs=DEFAULT_EXPORT_STYLE_PREFS)
    prefs = figure_prefs(ctx, agg)
    prefs["marker_size"] = 9.0

    keys = list(prefs)
    assert len(keys) == len(set(keys)), "a key was yielded twice"
    for key in FIGURE_SCOPED_PREF_KEYS:
        assert key in prefs
    snapshot = dict(prefs)
    assert snapshot["marker_size"] == 9.0
    assert snapshot["axis_label_size"] == DEFAULT_EXPORT_STYLE_PREFS["axis_label_size"]
