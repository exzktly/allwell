"""Image Table row/column add + one-click remove (grid_ops)."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

pytestmark = pytest.mark.qt

from well_viewer.features.image_browser.image_table import controller as C  # noqa: E402
from well_viewer.features.image_browser.image_table import grid_ops as G  # noqa: E402


def _build_image_table(app):
    """Drive the lazy Image Browser → Image Table build like a real user, so
    ``_image_table_selector_grid`` and friends exist."""
    app._notebook.setCurrentByName("Image Browser")
    sub = getattr(app, "_image_browser_subtabs", None)
    assert sub is not None, "Image Browser container should build on navigation"
    for i in range(sub.count()):
        if sub.tabText(i) == "Image Table":
            sub.setCurrentIndex(i)
            from well_viewer.features.image_browser.controller import build_image_browser_subtab
            build_image_browser_subtab(app, i)
            break
    assert hasattr(app, "_image_table_selector_grid")


def _dims(app):
    cells = app._image_table_cells
    return len(cells), (len(cells[0]) if cells else 0)


def test_remove_col_shrinks_grid(loaded_app):
    app = loaded_app
    _build_image_table(app)
    C.image_table_repopulate_dropdowns(app._ctx)
    C.image_table_apply_preset(app._ctx, "channel", "timepoint")
    rows0, cols0 = _dims(app)
    if cols0 < 2:
        pytest.skip("need ≥2 columns to remove one")
    G.image_table_remove_col(app._ctx, 0)
    rows1, cols1 = _dims(app)
    assert (rows1, cols1) == (rows0, cols0 - 1)
    assert app._app_state.image_table.cols == cols0 - 1


def test_remove_row_shrinks_grid(loaded_app):
    app = loaded_app
    _build_image_table(app)
    C.image_table_repopulate_dropdowns(app._ctx)
    C.image_table_apply_preset(app._ctx, "channel", "timepoint")
    rows0, cols0 = _dims(app)
    if rows0 < 2:
        pytest.skip("need ≥2 rows to remove one")
    G.image_table_remove_row(app._ctx, 0)
    rows1, cols1 = _dims(app)
    assert (rows1, cols1) == (rows0 - 1, cols0)


def test_remove_guards_last_row_and_col(loaded_app):
    app = loaded_app
    _build_image_table(app)
    C.image_table_repopulate_dropdowns(app._ctx)
    # 1x1 grid: removing the only row/column must be refused.
    app._app_state.image_table.rows = 1
    app._app_state.image_table.cols = 1
    C.image_table_rebuild_grid(app._ctx)
    G.image_table_remove_row(app._ctx, 0)
    G.image_table_remove_col(app._ctx, 0)
    assert _dims(app) == (1, 1)


def test_add_varied_col_appends_and_clones(loaded_app):
    app = loaded_app
    _build_image_table(app)
    C.image_table_repopulate_dropdowns(app._ctx)
    C.image_table_apply_preset(app._ctx, "channel", "timepoint")
    rows0, cols0 = _dims(app)
    chans = C._channel_options(app._ctx)
    G.image_table_add_varied_col(app._ctx, src_idx=cols0 - 1, dim="channel",
                                 mode="uniform", value=chans[0])
    rows1, cols1 = _dims(app)
    assert (rows1, cols1) == (rows0, cols0 + 1)
    # New last column has the chosen channel in every row.
    for row in app._image_table_cells:
        assert row[-1]["chan_cb"].currentText() == chans[0]
