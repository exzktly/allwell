"""The exported Image Table carries the same axis labels the live grid shows.

The live grid labels its rows and columns with Qt widgets; the export is a separate
matplotlib figure. Both read one ``FacetPlan``, and these tests pin that the export
half — the geometry in ``image_table/renderer.py`` and its use inside
``export._build_export_figure`` — reproduces the plan's top / left / corner labels,
its group strips and its inter-group gaps.

Fast + Qt-light: the planner runs on the plain-data render model
(``{"well","chan","tp","fov"}`` string dicts) and the export builder is driven with a
fake ctx, so no ``WellViewerApp`` is constructed.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from matplotlib.figure import Figure

from well_viewer.features.image_browser.image_table import facet_view as F
from well_viewer.features.image_browser.image_table import renderer as R

CELL_W = 2.6
CELL_H = 2.6


def _cell(well, chan, tp, fov="1"):
    return {"well": well, "chan": chan, "tp": tp, "fov": fov}


def _plan_ctx(row_dims, col_dims, well_labels=None):
    st = SimpleNamespace(row_dims=row_dims, col_dims=col_dims)
    ds = SimpleNamespace(well_labels=dict(well_labels or {}))
    return SimpleNamespace(state=SimpleNamespace(image_table=st, dataset=ds))


def _nested_cells():
    """rows = well › channel, cols = timepoint — the grouped case with a strip."""
    cells = []
    for well in ("A01", "A02"):
        for chan in ("GFP", "DAPI"):
            cells.append([_cell(well, chan, tp) for tp in ("0h", "24h", "48h")])
    return cells


def _nested_plan(**kw):
    cells = _nested_cells()
    return F.plan_facets(_plan_ctx(["well", "channel"], ["timepoint"], **kw), cells), cells


def _drawn_texts(fig):
    return {t.get_text() for ax in fig.axes for t in ax.texts}


def _text_by_content(fig, wanted):
    for ax in fig.axes:
        for t in ax.texts:
            if t.get_text() == wanted:
                return t
    raise AssertionError(f"no text {wanted!r} in the figure")


# ── geometry ─────────────────────────────────────────────────────────────────

def test_px_to_pt_uses_the_css_ratio():
    # QSS font sizes are CSS pixels (1/96 in); matplotlib points are 1/72 in.
    assert R.px_to_pt(12) == pytest.approx(9.0)
    assert R.px_to_pt(96) == pytest.approx(72.0)
    # Floored so a tiny size still renders legibly rather than vanishing.
    assert R.px_to_pt(1) >= 4.0


def test_slot_inches_size_cells_gaps_and_labels():
    plan, cells = _nested_plan()
    heights, widths = R.facet_slot_inches(
        plan, len(cells), len(cells[0]), cell_w_in=CELL_W, cell_h_in=CELL_H)

    assert len(heights) == plan.bottom_row
    assert len(widths) == plan.bottom_col

    for gr in plan.grid_row_of:
        assert heights[gr] == pytest.approx(CELL_H)
    for gc in plan.grid_col_of:
        assert widths[gc] == pytest.approx(CELL_W)

    # The inter-group spacer carries the plan's gap, converted from Qt pixels.
    assert plan.spacer_rows
    for gr in plan.spacer_rows:
        assert heights[gr] == pytest.approx(plan.row_gap / 96.0)

    # Label strips are real but far thinner than a cell.
    for gr in range(plan.first_row):
        assert 0 < heights[gr] < CELL_H
    for gc in range(plan.first_col):
        assert 0 < widths[gc] < CELL_W


def test_slot_inches_track_the_state_font_sizes():
    cells = _nested_cells()
    ctx = _plan_ctx(["well", "channel"], ["timepoint"])
    small = F.plan_facets(ctx, cells)
    ctx.state.image_table.label_font_pt = 44
    big = F.plan_facets(ctx, cells)

    h_small, _ = R.facet_slot_inches(small, len(cells), len(cells[0]),
                                     cell_w_in=CELL_W, cell_h_in=CELL_H)
    h_big, _ = R.facet_slot_inches(big, len(cells), len(cells[0]),
                                   cell_w_in=CELL_W, cell_h_in=CELL_H)
    # The innermost top label row is sized off the per-value font.
    assert h_big[small.first_row - 1] > h_small[small.first_row - 1]


def test_figsize_grows_with_the_label_strips():
    plan, cells = _nested_plan()
    w, h = R.facet_figsize(plan, len(cells), len(cells[0]),
                           cell_w_in=CELL_W, cell_h_in=CELL_H, pad_in=0.07)
    # Strictly larger than the bare cell grid — the labels and gaps take room
    # rather than squeezing the images.
    assert w > len(cells[0]) * CELL_W
    assert h > len(cells) * CELL_H


def test_cell_pad_inches_keeps_the_font_relative_unit():
    import matplotlib

    font_pt = float(matplotlib.rcParams["font.size"])
    assert R.cell_pad_inches(0.5) == pytest.approx(0.5 * font_pt / 72.0)
    assert R.cell_pad_inches(-1.0) == 0.0


# ── label drawing ────────────────────────────────────────────────────────────

def _drawn_figure(plan, cells, **kw):
    pad_in = kw.pop("pad_in", 0.07)
    fig = Figure(figsize=R.facet_figsize(
        plan, len(cells), len(cells[0]),
        cell_w_in=CELL_W, cell_h_in=CELL_H, pad_in=pad_in))
    gs = R.make_facet_gridspec(fig, plan, len(cells), len(cells[0]),
                               cell_w_in=CELL_W, cell_h_in=CELL_H, pad_in=pad_in)
    R.draw_facet_labels(fig, gs, plan)
    return fig


def test_draw_facet_labels_puts_every_plan_text_in_the_figure():
    plan, cells = _nested_plan()
    drawn = _drawn_texts(_drawn_figure(plan, cells))
    # Column values, row values, the outer well strips, and the corner.
    assert {"T:0h", "T:24h", "T:48h", "GFP", "DAPI", "A01", "A02", "FOV:1"} <= drawn


def test_left_group_strip_is_rotated_and_inner_labels_are_not():
    plan, cells = _nested_plan()
    fig = _drawn_figure(plan, cells)
    # The vertical facet strip reads bottom-to-top, like the live _VLabel.
    assert _text_by_content(fig, "A01").get_rotation() == pytest.approx(90.0)
    assert _text_by_content(fig, "GFP").get_rotation() == pytest.approx(0.0)


def test_group_labels_get_a_band_and_inner_labels_do_not():
    plan, cells = _nested_plan()
    fig = _drawn_figure(plan, cells)
    assert _text_by_content(fig, "A01").get_bbox_patch() is not None
    assert _text_by_content(fig, "GFP").get_bbox_patch() is None


def test_label_axes_are_tagged_and_span_their_group():
    plan, cells = _nested_plan()
    fig = _drawn_figure(plan, cells)
    tagged = [ax for ax in fig.axes if ax.get_gid() == R.FACET_GID]
    assert len(tagged) == len(fig.axes)      # only chrome on this figure
    # The A01 strip spans both of that well's rows.
    strip = next(ax for ax in fig.axes
                 if any(t.get_text() == "A01" for t in ax.texts))
    assert len(strip.get_subplotspec().rowspan) == 2


def test_well_label_overrides_the_position_in_the_export():
    # Sample Definitions → Well Labels must reach the exported strip, not just
    # the live grid: both read the plan's _fmt.
    plan, cells = _nested_plan(well_labels={"A01": "Control"})
    drawn = _drawn_texts(_drawn_figure(plan, cells))
    assert "Control" in drawn and "A01" not in drawn


# ── the shared export builder ────────────────────────────────────────────────

def _export_ctx(cells, row_dims, col_dims, well_labels=None):
    from well_viewer.features.image_browser.image_table.state import ImageTableState

    st = ImageTableState()
    st.row_dims, st.col_dims = row_dims, col_dims
    return SimpleNamespace(
        window=None,
        state=SimpleNamespace(
            image_table=st,
            dataset=SimpleNamespace(well_labels=dict(well_labels or {}), data_dir=None),
            # _build_export_figure now also warms the parallel prefetch
            # (_prefetch_cell_arrays), which reads ratio_metrics while
            # deciding what to warm.
            channel=SimpleNamespace(ratio_metrics=[]),
        ),
        runtime=SimpleNamespace(
            image_table=SimpleNamespace(cells=cells, crop_tool=None)),
        notify=SimpleNamespace(set_status=lambda *_a, **_k: None),
    )


def _blank_export(monkeypatch, **ctx_kw):
    """``_build_export_figure`` on a grid whose cells decode to nothing.

    Every export path (file / Copy PNG / Copy SVG / Save to Saved Plots) funnels
    through this one builder, so the labels landing here is the whole contract.
    """
    from well_viewer.features.image_browser.image_table import export as E

    cells = _nested_cells()
    monkeypatch.setattr(E, "_load_array", lambda *a, **k: None)
    monkeypatch.setattr(E, "resolve_chrome_plan", lambda *a, **k: (0, set(), None))
    ctx = _export_ctx(cells, ["well", "channel"], ["timepoint"], **ctx_kw)
    fig, kwargs = E._build_export_figure(ctx)
    return fig, kwargs, cells


def test_build_export_figure_carries_the_facet_chrome(monkeypatch):
    fig, _kw, _cells = _blank_export(monkeypatch)
    drawn = _drawn_texts(fig)
    assert {"T:0h", "T:24h", "T:48h", "GFP", "DAPI", "A01", "A02"} <= drawn


def test_export_places_every_cell_and_tags_only_the_chrome(monkeypatch):
    fig, _kw, cells = _blank_export(monkeypatch)
    cell_axes = [ax for ax in fig.axes if ax.get_gid() != R.FACET_GID]
    assert len(cell_axes) == len(cells) * len(cells[0])
    # Placement mirrors the plan: no cell axes sits in a label or spacer slot.
    plan = F.plan_facets(_plan_ctx(["well", "channel"], ["timepoint"]), cells)
    for ax in cell_axes:
        spec = ax.get_subplotspec()
        assert spec.rowspan.start in plan.grid_row_of
        assert spec.colspan.start in plan.grid_col_of


def test_per_cell_titles_are_off_by_default(monkeypatch):
    # The axis labels identify every cell, so repeating a title above each image
    # would be redundant. It stays available as an explicit Export-settings opt-in.
    fig, _kw, _cells = _blank_export(monkeypatch)
    cell_axes = [ax for ax in fig.axes if ax.get_gid() != R.FACET_GID]
    assert not any(ax.get_title() for ax in cell_axes)


def test_show_titles_opt_in_still_draws_them(monkeypatch):
    from well_viewer.features.image_browser.image_table import export as E

    cells = _nested_cells()
    monkeypatch.setattr(E, "_load_array", lambda *a, **k: None)
    monkeypatch.setattr(E, "resolve_chrome_plan", lambda *a, **k: (0, set(), None))
    ctx = _export_ctx(cells, ["well", "channel"], ["timepoint"])
    ctx.state.image_table.export_opts = {"show_titles": True}
    fig, _kw = E._build_export_figure(ctx)
    cell_axes = [ax for ax in fig.axes if ax.get_gid() != R.FACET_GID]
    assert any(ax.get_title() for ax in cell_axes)


def test_progress_callback_fires_once_per_cell_in_order(monkeypatch):
    """``image_table_save_to_gallery`` drives a progress dialog off this
    callback -- it must fire exactly once per cell, with a monotonically
    increasing ``done`` count and a stable ``total``, or the dialog either
    stalls partway or overshoots its own maximum."""
    from well_viewer.features.image_browser.image_table import export as E

    cells = _nested_cells()
    monkeypatch.setattr(E, "_load_array", lambda *a, **k: None)
    monkeypatch.setattr(E, "resolve_chrome_plan", lambda *a, **k: (0, set(), None))
    ctx = _export_ctx(cells, ["well", "channel"], ["timepoint"])

    calls: list[tuple[int, int]] = []
    fig, _kw = E._build_export_figure(ctx, progress=lambda done, total: calls.append((done, total)))

    total_cells = sum(len(row) for row in cells)
    assert len(calls) == total_cells
    assert [c[0] for c in calls] == list(range(1, total_cells + 1))
    assert {c[1] for c in calls} == {total_cells}


def test_export_shares_the_persistent_image_cache_not_a_throwaway_copy(monkeypatch):
    """Regression: ``_build_export_figure`` used to copy ``image_cache`` into a
    local ``dict(...)`` before rendering, so any well/channel it scanned for
    the first time was discarded instead of being written back -- a save run
    without a prior Generate (or vice versa) never benefited the other path.
    The ``cache`` object every ``_load_array`` call receives must be the same
    object as ``ctx.state.image_table.image_cache``."""
    from well_viewer.features.image_browser.image_table import export as E

    cells = _nested_cells()
    seen_cache_ids: set = set()

    def _fake_load(ctx, cache, *a, **k):
        seen_cache_ids.add(id(cache))
        cache[("touched",)] = True
        return None

    monkeypatch.setattr(E, "_load_array", _fake_load)
    monkeypatch.setattr(E, "resolve_chrome_plan", lambda *a, **k: (0, set(), None))
    ctx = _export_ctx(cells, ["well", "channel"], ["timepoint"])

    E._build_export_figure(ctx)

    assert seen_cache_ids == {id(ctx.state.image_table.image_cache)}
    assert ("touched",) in ctx.state.image_table.image_cache
