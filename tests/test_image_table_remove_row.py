"""Image Table: removing a row must preserve the OTHER rows' per-row
assignments.

Per-row state (LUT colour, min/max levels, and the Channel/Well shortcut) is
keyed by row index. Before the fix, deleting a row left ``build_lut_row``'s
index-keyed preservation to hand each surviving row the assignment of the row
that shifted into its slot — so colours / channels visibly scrambled on delete.
"""

from __future__ import annotations

import pytest


def _fresh_grid(app, rows=3, cols=2):
    from well_viewer.features.image_browser.image_table import controller as itc
    st = app._app_state.image_table
    st.rows, st.cols, st.group_size = rows, cols, 0
    itc.image_table_rebuild_grid(app._ctx)
    return app._ctx.runtime.image_table


@pytest.mark.slow
def test_remove_middle_row_preserves_lut_colors(image_table_app):
    app = image_table_app
    from well_viewer.features.image_browser.image_table import grid_ops

    it = _fresh_grid(app)
    for cb, name in zip(it.row_lut_color_cbs, ["Reds", "Greens", "Blues"]):
        cb.setLut(name, reversed=False)

    grid_ops.image_table_remove_row(app._ctx, 1)

    colors = app._ctx.runtime.image_table.row_lut_color_cbs
    assert len(colors) == 2
    assert [cb.lut() for cb in colors] == ["Reds", "Blues"]


@pytest.mark.slow
def test_remove_first_row_preserves_lut_levels(image_table_app):
    app = image_table_app
    from well_viewer.features.image_browser.image_table import grid_ops

    it = _fresh_grid(app)
    # Distinct manual min/max per row.
    for r, (lo, hi) in enumerate([("1", "11"), ("2", "22"), ("3", "33")]):
        it.lut[r]["min"].setText(lo)
        it.lut[r]["max"].setText(hi)

    grid_ops.image_table_remove_row(app._ctx, 0)

    lut = app._ctx.runtime.image_table.lut
    assert len(lut) == 2
    # Old rows 1,2 survive as new rows 0,1 with their own levels intact.
    assert (lut[0]["min"].text(), lut[0]["max"].text()) == ("2", "22")
    assert (lut[1]["min"].text(), lut[1]["max"].text()) == ("3", "33")


@pytest.mark.slow
def test_remove_row_preserves_per_row_channel(image_table_app):
    """The per-row Channel selector (which fluorescent channel the row pulls
    from the zips) must survive a deletion — surviving rows keep their own
    channel, not the default the rebuilt combo would otherwise show."""
    app = image_table_app
    from well_viewer.features.image_browser.image_table import grid_ops

    it = _fresh_grid(app)
    chans = it.row_chan_cbs
    opts = [chans[0].itemText(i) for i in range(chans[0].count())]
    if len(opts) < 2:
        pytest.skip("dataset has <2 channels; per-row channel test needs 2")
    # Give rows 0 and 2 distinct channels (fires apply_row_channel → cells too).
    chans[0].setCurrentText(opts[0])
    chans[2].setCurrentText(opts[1])

    grid_ops.image_table_remove_row(app._ctx, 1)

    chans2 = app._ctx.runtime.image_table.row_chan_cbs
    assert len(chans2) == 2
    assert chans2[0].currentText() == opts[0]
    assert chans2[1].currentText() == opts[1]  # old row 2, not the deleted row 1


@pytest.mark.slow
def test_remove_row_preserves_rendered_channel_from_layout(image_table_app):
    """End-to-end: build a channels-down-rows layout, delete the middle row,
    and confirm each surviving row still resolves to its own channel via the
    render path (``image_compose._row_channel`` — the channel Generate loads
    from the zip), i.e. the assignments shift correctly rather than scramble."""
    app = image_table_app
    from well_viewer.features.image_browser.image_table import controller as itc
    from well_viewer.features.image_browser.image_table import grid_ops
    from well_viewer.features.image_browser.image_table import image_compose as ic

    ctx = app._ctx
    st = app._app_state.image_table
    st.active_channels = {"GFP", "MCHERRY", "DAPI"}
    st.active_wells = set(list(app._app_state.dataset.well_paths)[:1])
    itc.image_table_build_layout(ctx)

    before = [ic._row_channel(ctx, r)
              for r in range(len(ctx.runtime.image_table.cells))]
    if len(before) < 3:
        pytest.skip("layout produced <3 channel rows")

    grid_ops.image_table_remove_row(ctx, 1)

    after = [ic._row_channel(ctx, r)
             for r in range(len(ctx.runtime.image_table.cells))]
    assert after == [before[0], before[2]]
