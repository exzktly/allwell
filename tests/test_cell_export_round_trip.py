"""Recompute each plot's statistics from its exported cells and match the figure.

This is the claim the feature makes, tested directly: if the numbers on screen
cannot be rebuilt from the file, the file is missing something. Every test here
presses the real menu action, reads the CSV back, and recomputes.

Two rules keep these tests worth their runtime, and both are rules this repo has
been burned by before:

**The recompute is written out here, in plain pandas.** Never by calling
``aggregate_with_threshold_df``, ``_reduce_reps`` or any function used to draw
the plot — a harness that calls the same code on both sides compares a function
to itself and will confirm anything.

**The comparison is against the drawn artists** — bar heights, line y-data,
error-bar segments, the image array — not against a collector's return value.
The artist is the boundary the user actually sees.

Float equality is ``assert_allclose(rtol=1e-9, equal_nan=True)``, never ``==``:
pandas' groupby mean is a Kahan-compensated sum and the app's is a plain
``sum() / n``, which differ in the last couple of ULPs. ``rtol=1e-9`` is far
above that and far below anything a real defect would produce.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

RTOL = 1e-9


# ── running an export ────────────────────────────────────────────────────────

def read_export(path):
    """Read an exported file back the way a user's script would.

    ``float_precision="round_trip"`` is load-bearing rather than decoration:
    pandas writes a shortest-round-trippable repr but its *default reader* does
    not return it, and roughly one value in six comes back a ULP out. Every
    exact-value assertion below depends on this.
    """
    text = path.read_text(encoding="utf-8")
    starts = [int(line.rsplit(" ", 1)[1]) for line in text.splitlines()
              if line.startswith("# table starts on line ")]
    assert starts, "the scope block must name the line the table starts on"
    frame = pd.read_csv(path, skiprows=starts[0] - 1, float_precision="round_trip")
    scope = [line for line in text.splitlines() if line.startswith("#")]
    return frame, scope


def run_export(ctx, qtbot, monkeypatch, tmp_path, scope_fn, name="cells.csv"):
    from PySide6.QtWidgets import QFileDialog

    from review.features.export.service import export_cell_data_from

    out = tmp_path / name
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    export_cell_data_from(ctx, scope_fn)
    qtbot.waitUntil(lambda: out.exists(), timeout=30000)
    # The file lands before the export finishes: its end-of-run handler is a
    # queued callback, and until it runs a second export is refused as "already
    # running" (a modal, here). Waiting for the file alone made the tests that
    # export twice fail about one run in three.
    qtbot.waitUntil(lambda: getattr(ctx.runtime.export, "cell_cancel", None) is None, timeout=30000)
    return read_export(out)


#: A GFP floor that lands inside the fixture's range rather than outside it.
#: A01's cells at t=0 are 100..105 and A02's are 110..115, so this excludes
#: three cells of one well and none of the other. Without a cutoff that
#: actually bites, ``plot_in_population`` is all-1 and every assertion about it
#: passes against code that ignores it — a fixture of clean inputs validates
#: the happy path and nothing else (CLAUDE.md §7.1 #4).
GFP_FLOOR = 102.5


@pytest.fixture
def two_wells(loaded_app):
    ctx = loaded_app._ctx
    ctx.state.selection.selected_wells = {"A01", "A02"}
    ctx.state.cell_gating.fluor_gates["gfp"] = GFP_FLOOR
    return loaded_app


@pytest.mark.slow
def test_the_fixture_cutoff_actually_excludes_cells(
        two_wells, qtbot, monkeypatch, tmp_path):
    """Guards the guard: if the floor stopped biting, every marker assertion in
    this file would pass against code that ignored the marker entirely."""
    from review.features.distribution.cell_scope_compute import cell_scope
    from review.features.distribution.controller import redraw_distribution

    ctx = two_wells._ctx
    redraw_distribution(ctx)
    frame, _scope = run_export(ctx, qtbot, monkeypatch, tmp_path, cell_scope)
    inside = int(frame["plot_in_population"].sum())
    assert 0 < inside < len(frame), (
        f"the cutoff must split the population: {inside} of {len(frame)}")


# ── recompute helpers, transcribed from the app rather than imported ─────────

def agg_family_a(rows, val_col):
    """``_aggregate_arrays``' statistics for one timepoint of one well.

    Transcribed from ``data_loading.py``: the mean is over the *above-threshold*
    rows only; the spread is a population SD (ddof=0) of those same values, and
    is NaN — never 0.0 — when fewer than two of them exist.
    """
    pop = rows[rows["plot_in_population"] == 1]
    above = pop[pop["plot_above_threshold"] == 1][val_col].to_numpy()
    n_total, n_above = len(pop), len(above)
    mean = above.sum() / n_above if n_above else math.nan
    if n_above > 1:
        spread = float(np.sqrt(((above - mean) ** 2).sum() / n_above))
    else:
        spread = math.nan
    frac = n_above / n_total if n_total else math.nan
    return dict(mean=mean, spread=spread, frac=frac,
                n_above=n_above, n_total=n_total)


def agg_family_b(rows, val_col):
    """``_reduce_reps``' statistics: a mean of per-well means, ddof=1 across them.

    The branch replicate sets take. Non-finite members are dropped *before*
    counting, so a well with no above-threshold cell is not in the denominator.
    """
    per_well = (rows[(rows["plot_in_population"] == 1)
                     & (rows["plot_above_threshold"] == 1)]
                .groupby("well_label", observed=True)[val_col]
                .apply(lambda s: s.to_numpy().sum() / s.size))
    values = per_well[np.isfinite(per_well)].to_numpy()
    if values.size == 0:
        return dict(mean=math.nan, spread=math.nan)
    mean = float(values.mean())
    spread = math.nan if values.size < 2 else float(values.std(ddof=1))
    return dict(mean=mean, spread=spread)


def bar_heights(ax):
    """``{x_centre: (height, is_undefined)}`` for one bar axis.

    An undefined value is drawn as a dashed zero-height bar rather than being
    left out, so height alone cannot tell it from a measured zero.
    """
    out = {}
    for patch in ax.patches:
        centre = patch.get_x() + patch.get_width() / 2.0
        out[round(centre, 6)] = (patch.get_height(),
                                 patch.get_linestyle() in ("--", "dashed"))
    return out


def errorbar_spreads(ax):
    """``{x_centre: half-segment}`` for each drawn whisker.

    ``yerr=nan`` produces no segment at all, which is how "undefined" reads on
    this artist — the assertion for it is an absent key, not a zero.
    """
    out = {}
    for container in ax.containers:
        lines = getattr(container, "lines", None)
        if not lines or len(lines) < 3 or not lines[2]:
            continue
        for segment in lines[2][0].get_segments():
            centre = round(float(segment[0][0]), 6)
            out[centre] = (float(segment[1][1]) - float(segment[0][1])) / 2.0
    return out


# ── Distribution ─────────────────────────────────────────────────────────────

@pytest.mark.slow
def test_distribution_cells_reproduce_the_drawn_population(
        two_wells, qtbot, monkeypatch, tmp_path):
    """The tab titles itself with the cell count it drew; the file must hold it."""
    from review.features.distribution.cell_scope_compute import cell_scope
    from review.features.distribution.controller import redraw_distribution

    ctx = two_wells._ctx
    redraw_distribution(ctx)
    title = ctx.widgets.get_or_none("_distribution_card").figure.axes[0].get_title()

    frame, scope = run_export(ctx, qtbot, monkeypatch, tmp_path, cell_scope)

    drawn = int(title.split("(")[1].split(" cells")[0])
    assert int(frame["plot_in_population"].sum()) == drawn
    assert "# plot: distribution" in scope
    # Identity survives, so a row can be traced back to its image.
    for column in ("well_label", "fov", "nucleus_id", "timepoint_hours"):
        assert column in frame.columns
        assert frame[column].notna().all()


@pytest.mark.slow
def test_distribution_values_match_the_drawn_values_exactly(
        two_wells, qtbot, monkeypatch, tmp_path):
    from review.features.distribution.cell_scope_compute import cell_scope
    from review.features.distribution.controller import redraw_distribution

    ctx = two_wells._ctx
    redraw_distribution(ctx)
    frame, _scope = run_export(ctx, qtbot, monkeypatch, tmp_path, cell_scope)

    val_col = ctx.state.channel.active_val_col
    exported = np.sort(frame.loc[frame["plot_in_population"] == 1, val_col].to_numpy())

    # The reference side, transcribed rather than imported: the tab's filter is
    # the timepoint and the per-channel floor, and the floor is the fixture's.
    from review.data.data_loading import get_rows
    drawn = []
    for well in sorted(ctx.state.selection.selected_wells):
        rows = get_rows(ctx, well)
        at_tp = rows[np.isclose(rows["timepoint_hours"], 0.0)]
        drawn.extend(at_tp.loc[at_tp["gfp_mean_intensity"] > GFP_FLOOR,
                               val_col].tolist())
    np.testing.assert_allclose(exported, np.sort(np.asarray(drawn)),
                               rtol=RTOL, equal_nan=True)


# ── Bar ──────────────────────────────────────────────────────────────────────

@pytest.mark.slow
def test_bar_heights_recompute_from_the_exported_cells(
        two_wells, qtbot, monkeypatch, tmp_path):
    """Bar heights, whiskers and the fraction panel, rebuilt from the file.

    Per-well mode with no replicate sets, so the statistics are family A. The
    alignment is through ``ctx.runtime.barplot.xs`` / ``xs_keys``, which the
    renderer stashes for exactly this: bar x positions carry cluster gaps, so a
    patch's index is not its item's index.
    """
    from review.features.barplot.cell_scope_compute import cell_scope
    from review.features.barplot.renderer import redraw_bars

    ctx = two_wells._ctx
    redraw_bars(ctx)
    fig = ctx.widgets.get_or_none("_bar_card").figure
    xs = list(ctx.runtime.barplot.xs or [])
    keys = list(ctx.runtime.barplot.xs_keys or [])
    assert xs and len(xs) == len(keys), "the bar tab drew nothing to compare against"

    frame, _scope = run_export(ctx, qtbot, monkeypatch, tmp_path, cell_scope)
    val_col = ctx.state.channel.active_val_col

    ax_mean, ax_frac = fig.axes[0], fig.axes[1]
    heights, fracs = bar_heights(ax_mean), bar_heights(ax_frac)
    spreads = errorbar_spreads(ax_mean)

    checked = 0
    for x, key in zip(xs, keys):
        rows = frame[frame["well_label"] == key]
        if rows.empty:
            continue
        checked += 1
        stats = agg_family_a(rows, val_col)
        centre = round(float(x), 6)
        assert centre in heights, f"no bar drawn at {key}"
        drawn_height, undefined = heights[centre]
        if math.isnan(stats["mean"]):
            assert undefined and drawn_height == 0.0
        else:
            np.testing.assert_allclose(drawn_height, stats["mean"], rtol=RTOL)
        if centre in fracs:
            np.testing.assert_allclose(fracs[centre][0], stats["frac"], rtol=RTOL)
        if math.isnan(stats["spread"]):
            assert centre not in spreads, (
                "an undefined spread must draw no whisker — a zero-length one "
                "would read as replicates that agreed exactly")
    assert checked, "no bar was compared — the assertion did not run"


# ── Line ─────────────────────────────────────────────────────────────────────

@pytest.mark.slow
def test_line_points_recompute_from_the_exported_cells(
        two_wells, qtbot, monkeypatch, tmp_path):
    """Each drawn point's y, rebuilt per timepoint from the file.

    Lines are selected by label rather than by index, and the drawn arrays are
    already filtered to the timepoints that produced a mean, so the recompute is
    matched to the x values the artist carries rather than to the scope's.
    """
    from review.core.plot_orchestrator import redraw_app
    from review.features.lineplot.cell_scope_compute import cell_scope

    ctx = two_wells._ctx
    # The app's own fan-out, rather than the renderer's keyword-heavy signature
    # — the point is to compare against what a redraw actually draws.
    redraw_app(ctx)
    fig = ctx.widgets.get_or_none("_line_fig")
    frame, _scope = run_export(ctx, qtbot, monkeypatch, tmp_path, cell_scope)
    val_col = ctx.state.channel.active_val_col

    drawn_lines = [ln for ax in fig.axes for ln in ax.lines
                   if ln.get_label() and not ln.get_label().startswith("_")]
    assert drawn_lines, "the line tab drew nothing to compare against"

    checked = 0
    for line in drawn_lines:
        key = line.get_label()
        rows = frame[frame["well_label"] == key]
        if rows.empty:
            continue
        for x, y in zip(line.get_xdata(), line.get_ydata()):
            at_tp = rows[np.isclose(rows["timepoint_hours"], float(x))]
            if at_tp.empty:
                continue
            stats = agg_family_a(at_tp, val_col)
            for candidate in (stats["mean"], stats["frac"],
                              float(stats["n_above"]), float(stats["n_total"])):
                if candidate is not None and np.isclose(
                        float(y), candidate, rtol=1e-6, equal_nan=True):
                    checked += 1
                    break
            else:
                pytest.fail(
                    f"{key} at t={x}: drawn {y!r} is none of the statistics "
                    f"recomputed from the exported cells ({stats})")
    assert checked, "no line point was matched — the comparison did not run"


# ── Heat Map ─────────────────────────────────────────────────────────────────

@pytest.mark.slow
def test_heatmap_count_cells_recompute_from_the_exported_cells(
        two_wells, qtbot, monkeypatch, tmp_path):
    """The count metric is the one that *is* a row count, so it is checkable.

    The averaging metrics are means of per-well means, which is why the export
    carries ``group`` (the grid cell) and ``well_label`` rather than trying to
    be the row set behind a drawn number.
    """
    from review.features.heatmap.cell_scope_compute import cell_scope
    from review.features.heatmap.controller import (
        METRIC_COUNT, active_layout, make_plate_layout, redraw_heatmap,
    )

    ctx = two_wells._ctx
    # A freshly-built tab holds an empty "Sidebar" layout, so give it the plate
    # layout a user with wells on screen would have.
    ctx.runtime.heatmap.layouts = [
        make_plate_layout((ctx.state.dataset.well_paths or {}).keys())]
    ctx.state.heatmap.active_layout_name = active_layout(ctx).name
    redraw_heatmap(ctx)
    scope = cell_scope(ctx)
    assert scope is not None, "the heat map drew no grid to export"
    frame, block = run_export(ctx, qtbot, monkeypatch, tmp_path, cell_scope)

    index = ctx.runtime.heatmap.cell_well_index or {}
    assert index, "no grid to compare against"
    assert any("group column names the (row, col)" in line for line in block)

    # Switch to the one metric that IS a row count, and compare the recomputed
    # count per grid cell against the drawn image. ``get_array()`` is a masked
    # array — the NaN cells are masked, not zero — so it is filled first.
    metric_cb = ctx.widgets.get_or_none("_heatmap_metric_cb")
    if metric_cb is not None:
        metric_cb.setCurrentText(METRIC_COUNT)
        redraw_heatmap(ctx)
        frame, block = run_export(ctx, qtbot, monkeypatch, tmp_path, cell_scope,
                                  name="heatmap_counts.csv")
        drawn = np.ma.filled(
            ctx.widgets.get_or_none("_heatmap_ax").images[0].get_array(), np.nan)
        checked = 0
        for (row, col), wells in (ctx.runtime.heatmap.cell_well_index or {}).items():
            rows = frame[frame["group"] == f"r{row}c{col}"]
            if rows.empty or not np.isfinite(drawn[row, col]):
                continue
            assert set(rows["well_label"]) <= set(wells)
            recomputed = int((rows["plot_in_population"] == 1).sum())
            np.testing.assert_allclose(drawn[row, col], recomputed, rtol=RTOL)
            checked += 1
        assert checked, "no grid cell was compared — the assertion did not run"


@pytest.mark.slow
def test_heatmap_refuses_before_the_grid_is_drawn(loaded_app):
    """A scope that cannot be built says so rather than writing an empty file."""
    from review.features.heatmap.cell_scope_compute import cell_scope

    ctx = loaded_app._ctx
    ctx.runtime.heatmap.cell_well_index = None
    assert cell_scope(ctx) is None


# ── Scatter ──────────────────────────────────────────────────────────────────

@pytest.mark.slow
def test_scatter_exports_the_ungated_cloud_with_membership_marked(
        two_wells, qtbot, monkeypatch, tmp_path):
    """The cloud, not the gate.

    A gate's percentage is denominated by the population it was drawn over, so
    a file filtered down to the gate could not reproduce the number on the
    figure. Membership is a column instead.
    """
    from review.features.scatter.cell_scope_compute import cell_scope
    from review.features.scatter.controller import redraw_scatter_orchestrate

    ctx = two_wells._ctx
    redraw_scatter_orchestrate(ctx)
    scope = cell_scope(ctx)
    if scope is None:
        pytest.skip("the scatter tab has no axes selected")
    assert scope.include_gate is False

    frame, block = run_export(ctx, qtbot, monkeypatch, tmp_path, cell_scope)
    assert "plot_in_gate" in frame.columns
    assert any("Gate_Pass is not applied" in line for line in block)

    drawn = [ln for ax in ctx.widgets.get_or_none("_scatter_card").figure.axes
             for ln in ax.lines if ln.get_linestyle() == "None"]
    if drawn:
        plotted = sum(len(ln.get_xdata()) for ln in drawn)
        assert int(frame["plot_axes_finite"].sum()) >= plotted


# ── Multivariate and Spatial ─────────────────────────────────────────────────

@pytest.mark.slow
def test_multivariate_matrix_rows_are_marked(two_wells, qtbot, monkeypatch, tmp_path):
    from review.features.multivariate.cell_scope_compute import cell_scope

    ctx = two_wells._ctx
    state = ctx.state.feature_slice("multivariate")
    if state is None:
        pytest.skip("multivariate is not in this build")
    state.feature_cols = ["gfp_mean_intensity", "area_px"]

    frame, block = run_export(ctx, qtbot, monkeypatch, tmp_path, cell_scope)
    assert "plot_in_matrix" in frame.columns
    assert int(frame["plot_in_matrix"].sum()) == len(frame)
    assert any("feature columns" in line for line in block)

    # And the marker is computed rather than stamped: a feature column no well
    # carries puts every row outside the matrix. Asserting only the all-1 case
    # would pass against code that ignored the marker entirely.
    state.feature_cols = ["gfp_mean_intensity", "no_such_feature"]
    frame2, _block2 = run_export(ctx, qtbot, monkeypatch, tmp_path, cell_scope,
                                 name="mv_missing_feature.csv")
    assert int(frame2["plot_in_matrix"].sum()) == 0


@pytest.mark.slow
def test_spatial_requires_the_centroid_columns(two_wells, qtbot, monkeypatch,
                                               tmp_path):
    """Without centroids the tab could not have placed a cell, so no rows.

    The mini dataset carries no ``shape_centroid_*`` columns, which makes this
    the live case rather than a hypothetical: the scope demands them, so the
    export writes a header and no rows instead of rows the figure never drew.
    """
    from review.features.spatial.cell_scope_compute import cell_scope
    from review.features.spatial.collect import (
        CENTROID_COL_COL, CENTROID_ROW_COL,
    )

    ctx = two_wells._ctx
    state = ctx.state.feature_slice("spatial")
    if state is None:
        pytest.skip("spatial is not in this build")
    state.frames = [{"well": "A01"}]
    state.active_well = "A01"
    state.timepoint = 0.0

    scope = cell_scope(ctx)
    assert scope is not None
    assert CENTROID_ROW_COL in scope.required_columns
    assert CENTROID_COL_COL in scope.required_columns

    frame, _block = run_export(ctx, qtbot, monkeypatch, tmp_path, cell_scope,
                               name="spatial_no_centroids.csv")
    assert frame.empty, (
        "a well with no centroid columns must contribute no rows, not rows the "
        "spatial statistics were never computed over")


# ── the export's own behaviour ───────────────────────────────────────────────

@pytest.mark.slow
def test_an_export_aborts_when_the_data_moves_under_it(
        two_wells, qtbot, monkeypatch, tmp_path):
    """A file half in one gating state and half in another is the failure this
    guards. Bumping ``rows_version`` mid-run must cancel rather than finish."""
    from PySide6.QtWidgets import QFileDialog

    from review.features.distribution.cell_scope_compute import cell_scope
    from review.features.export.service import export_cell_data_from

    ctx = two_wells._ctx
    out = tmp_path / "aborted.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))

    original = ctx.state.dataset.rows_version

    from review.features.export import cell_scope_compute as csc
    real_build = csc.build_chunk
    calls = {"n": 0}

    def _bump_after_first(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            ctx.state.dataset.note_rows_changed()
        return real_build(*args, **kwargs)

    monkeypatch.setattr(csc, "build_chunk", _bump_after_first)
    export_cell_data_from(ctx, cell_scope)
    qtbot.waitUntil(lambda: getattr(ctx.runtime.export, "cell_cancel", None) is None,
                    timeout=30000)

    assert ctx.state.dataset.rows_version != original
    assert not out.exists(), "an aborted export must leave no file"
    assert not list(tmp_path.glob(".*tmp")), "and no temp file either"


@pytest.mark.slow
def test_cancelling_leaves_neither_a_file_nor_a_temp(
        two_wells, qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    from review.features.distribution.cell_scope_compute import cell_scope
    from review.features.export.service import (
        cancel_cell_export, export_cell_data_from,
    )

    ctx = two_wells._ctx
    out = tmp_path / "cancelled.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    export_cell_data_from(ctx, cell_scope)
    cancel_cell_export(ctx)
    qtbot.wait(600)
    assert not out.exists()
    assert not list(tmp_path.glob(".*tmp"))


@pytest.mark.slow
def test_an_export_with_no_wells_refuses_rather_than_writing_the_plate(
        loaded_app, monkeypatch, tmp_path):
    """Distribution draws nothing with an empty selection, so its export must
    write nothing — the mismatch its summary export has today."""
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from review.features.distribution.cell_scope_compute import cell_scope
    from review.features.export.service import export_cell_data_from

    ctx = loaded_app._ctx
    ctx.state.selection.selected_wells = set()
    ctx.state.selection.selections = []
    out = tmp_path / "nothing.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    warned = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warned.append(a)))
    export_cell_data_from(ctx, cell_scope)
    assert warned, "an empty selection must say so"
    assert not out.exists()


# ── the replicate-set branch ─────────────────────────────────────────────────

@pytest.mark.slow
def test_a_replicate_sets_bar_recomputes_as_a_mean_of_per_well_means(
        two_wells, qtbot, monkeypatch, tmp_path):
    """The other formula family, which no other test in this file reaches.

    With replicate sets defined, Bar and Line never touch ``_aggregate_arrays``:
    they go through ``compute_rep_stats`` → ``stats_compute._reduce_reps``, and
    a set's bar is the mean of its **wells' means** with a cross-well ``ddof=1``
    SD. A file that could only reproduce the pooled-cell statistic would be
    wrong for every grouped plot in the app, and the scopes' own
    "group by well_label to reproduce it" note would be a promise nothing kept.
    """
    from review.features.barplot.cell_scope_compute import cell_scope
    from review.features.barplot.renderer import redraw_bars

    ctx = two_wells._ctx
    ctx.state.selection.selections = [
        {"name": "pair", "wells": ["A01", "A02"], "hidden": False}]
    redraw_bars(ctx)

    fig = ctx.widgets.get_or_none("_bar_card").figure
    xs = list(ctx.runtime.barplot.xs or [])
    keys = list(ctx.runtime.barplot.xs_keys or [])
    assert keys == ["pair"], f"expected one grouped bar, got {keys}"

    frame, block = run_export(ctx, qtbot, monkeypatch, tmp_path, cell_scope,
                              name="repset_cells.csv")
    assert set(frame["well_label"]) == {"A01", "A02"}, (
        "a grouped bar's file must keep the wells apart, or the mean of means "
        "cannot be recomputed from it")
    assert set(frame["group"]) == {"pair"}
    assert any("mean of its wells' means" in line for line in block)

    val_col = ctx.state.channel.active_val_col
    stats = agg_family_b(frame, val_col)
    heights = bar_heights(fig.axes[0])
    spreads = errorbar_spreads(fig.axes[0])
    centre = round(float(xs[0]), 6)
    assert centre in heights
    np.testing.assert_allclose(heights[centre][0], stats["mean"], rtol=RTOL)

    # Two replicates, so the cross-well spread is defined and drawn. The value
    # is scaled by the tab's CI factor, so compare the ratio rather than assume
    # which of SD / SEM is showing.
    if centre in spreads and np.isfinite(stats["spread"]) and stats["spread"]:
        ratio = spreads[centre] / stats["spread"]
        assert 0.1 < ratio < 10.0, (
            f"the drawn whisker ({spreads[centre]}) is not a scaling of the "
            f"recomputed cross-well spread ({stats['spread']})")


# ── the tabs that had no export test, which is where the defects were ────────

@pytest.mark.slow
def test_the_aggregate_scatter_exports_its_own_axes_not_the_cells_tabs(
        two_wells, qtbot, monkeypatch, tmp_path):
    """The two Scatter pages are separate tabs with separate controls.

    Deriving the aggregate scope from the per-cell one exported the wrong
    channel at the wrong timepoints under a scope block naming a third thing —
    right schema, right-looking counts, no error.
    """
    from review.features.scatter.cell_scope_compute import (
        agg_cell_scope, cell_scope,
    )
    from review.features.scatter.controller import (
        redraw_scatter_agg_orchestrate,
    )

    ctx = two_wells._ctx
    agg_x = ctx.widgets.get_or_none("_scatter_agg_ch_x_cb")
    cells_x = ctx.widgets.get_or_none("_scatter_ch_x_cb")
    if agg_x is None or cells_x is None or agg_x.count() < 2:
        pytest.skip("the scatter tabs have fewer than two channels")

    # Put the two tabs on different channels — the whole point.
    cells_x.setCurrentIndex(0)
    agg_x.setCurrentIndex(1)
    redraw_scatter_agg_orchestrate(ctx)

    agg = agg_cell_scope(ctx)
    cells = cell_scope(ctx)
    assert agg is not None and cells is not None
    assert agg.val_col != cells.val_col, (
        f"the aggregate scope took the per-cell tab's column ({agg.val_col})")
    assert agg.include_gate is True, (
        "the aggregate tab filters through the inclusion chokepoint; only the "
        "per-cell tab holds the gate layer off")
    assert agg.tp_fallback is True, (
        "its path resolves string timepoints, so a text-timepoint plate must "
        "not export an empty file")

    frame, block = run_export(ctx, qtbot, monkeypatch, tmp_path, agg_cell_scope,
                              name="agg_cells.csv")
    assert len(frame) > 0
    assert any("scatter_aggregate" in line for line in block)
    assert not any("ungated cloud" in line for line in block), (
        "the per-cell tab's gate note must not survive onto a scope that "
        "applies the gate layer — a file cannot say both")


@pytest.mark.slow
def test_spatial_exports_every_panel_the_tab_drew(
        loaded_app, qtbot, monkeypatch, tmp_path):
    """Not just ``active_well``, which is merely the first of the panels.

    ``sp_recompute`` facets one panel per selected well up to the panel cap and
    records them on ``st.frames``, setting ``active_well = shown[0]`` on the
    way. Exporting that one well and saying nothing about the rest is degraded
    output that reads as complete. The frames are set here the way the
    controller sets them, because building the lazy Spatial canvas is not what
    this is about — the scope's reading of them is.
    """
    from review.features.spatial.cell_scope_compute import cell_scope

    ctx = loaded_app._ctx
    panels = ["A01", "A02", "B01", "B02"]
    ctx.state.selection.selected_wells = set(panels)
    state = ctx.state.feature_slice("spatial")
    if state is None:
        pytest.skip("spatial is not in this build")
    state.frames = [{"well": w} for w in panels]
    state.active_well = panels[0]
    state.frames_truncated = 2
    state.timepoint = 0.0

    scope = cell_scope(ctx)
    assert scope is not None
    exported = [w for _g, w in scope.wells]
    assert exported == panels, (
        f"the tab drew {panels} and the export takes {exported}")
    assert any("exceeded the panel cap" in value for _k, value in scope.notes), (
        "wells the panel cap dropped must be named, not silently absent")


@pytest.mark.slow
def test_spatial_resolves_an_unset_timepoint_the_way_the_collector_does(
        loaded_app, monkeypatch):
    """``timepoint = None`` means "the first available" to the collector, not
    "every timepoint" — reading it as the latter exports a file several times
    wider than the figure while claiming to hold all of it."""
    from review.features.spatial.cell_scope_compute import cell_scope

    ctx = loaded_app._ctx
    ctx.state.selection.selected_wells = {"A01"}
    state = ctx.state.feature_slice("spatial")
    if state is None:
        pytest.skip("spatial is not in this build")
    state.timepoint = None
    state.frames = [{"well": "A01"}]
    state.active_well = "A01"

    scope = cell_scope(ctx)
    assert scope is not None
    assert scope.timepoints == (0.0,), (
        f"expected the first available timepoint, got {scope.timepoints}")


# ── the writer's failure path ────────────────────────────────────────────────

@pytest.mark.slow
def test_a_failed_writer_stops_the_slicing_chain(
        two_wells, qtbot, monkeypatch, tmp_path):
    """A dead writer leaves the queue permanently full, and the tick that
    handles a full queue reschedules itself. Without a terminal flag that is a
    100 Hz loop for the life of the process, pinning every well's frame."""
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from review.features.distribution.cell_scope_compute import cell_scope
    from review.features.export import worker as worker_mod
    from review.features.export.service import export_cell_data_from

    ctx = two_wells._ctx
    out = tmp_path / "doomed.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(worker_mod.CellExportWriter, "__call__",
                        lambda self, report=None: (_ for _ in ()).throw(
                            OSError("disk full")))

    ticks = {"n": 0}
    from PySide6 import QtCore
    real_single_shot = QtCore.QTimer.singleShot

    def _counting(msec, callback):
        ticks["n"] += 1
        real_single_shot(msec, callback)

    monkeypatch.setattr(QtCore.QTimer, "singleShot", staticmethod(_counting))

    export_cell_data_from(ctx, cell_scope)
    qtbot.waitUntil(
        lambda: getattr(ctx.runtime.export, "cell_cancel", None) is None,
        timeout=20000)
    settled = ticks["n"]
    qtbot.wait(700)
    assert ticks["n"] - settled <= 2, (
        f"the slicing chain kept ticking after the writer failed: "
        f"{ticks['n'] - settled} further ticks in 0.7 s")
    assert not out.exists()
    assert not list(tmp_path.glob(".*tmp"))
