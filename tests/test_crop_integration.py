"""Crop layer integration on the real app: drawing a crop re-flags cells
dataset-wide through the inclusion chokepoint, non-destructively; Clear crop and
Reset restore them (Reset also wipes manual deletes)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.qt

pd = pytest.importorskip("pandas")

from well_viewer.data.data_loading import df_included_mask
from well_viewer.features.crop.controller import (
    clear_crop_region, get_crop_region, reset_crop_and_overrides, set_crop_region,
)

# Mini-dataset centroids (with_shape=True): cell i -> row=2i, col=3i. A region of
# (row0,col0,row1,col1)=(0,0,8,12) keeps cells 1..4 and excludes 5,6 per timepoint.
_REGION = (0, 0, 8, 12)


def test_crop_excludes_outside_centroids_via_chokepoint(loaded_app_shape):
    app = loaded_app_shape
    well = sorted(app._app_state.dataset.well_paths)[0]
    df = app._get_rows(well)
    rows_before = len(df)
    incl_before = int(df_included_mask(df).sum())

    set_crop_region(app, _REGION)

    df2 = app._get_rows(well)
    assert len(df2) == rows_before                 # non-destructive: no rows removed
    assert "Crop_Pass" in df2.columns
    assert int(df_included_mask(df2).sum()) < incl_before
    assert get_crop_region(app) == _REGION

    # A lazily-loaded *other* well also gets Crop_Pass projected (the _get_rows hook).
    other = sorted(app._app_state.dataset.well_paths)[-1]
    assert "Crop_Pass" in app._get_rows(other).columns


def test_clear_crop_restores_cells(loaded_app_shape):
    app = loaded_app_shape
    well = sorted(app._app_state.dataset.well_paths)[0]
    incl_full = int(df_included_mask(app._get_rows(well)).sum())

    set_crop_region(app, _REGION)
    assert int(df_included_mask(app._get_rows(well)).sum()) < incl_full

    clear_crop_region(app)
    assert get_crop_region(app) is None
    assert int(df_included_mask(app._get_rows(well)).sum()) == incl_full


def test_reset_restores_manual_deletes_and_clears_crop(loaded_app_shape, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)

    app = loaded_app_shape
    well = sorted(app._app_state.dataset.well_paths)[0]
    incl_full = int(df_included_mask(app._get_rows(well)).sum())

    # Simulate a manual delete projected onto the cache (Included=0 on one cell),
    # recorded in the override dict, plus an active crop.
    df = app._get_rows(well)
    df["Included"] = 1
    df.loc[df.index[0], "Included"] = 0
    app._review_included_overrides[(well, "1", "0h", "1")] = "0"
    set_crop_region(app, _REGION)
    assert int(df_included_mask(app._get_rows(well)).sum()) < incl_full

    reset_crop_and_overrides(app)

    assert get_crop_region(app) is None
    assert not app._review_included_overrides
    # Cache was invalidated → frames reload original (all-Included) rows → full set.
    assert int(df_included_mask(app._get_rows(well)).sum()) == incl_full
