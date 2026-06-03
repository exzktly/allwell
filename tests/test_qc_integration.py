"""QC layer integration on the real app: editing the gate re-flags cells
dataset-wide through the inclusion chokepoint, non-destructively."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.qt

pd = pytest.importorskip("pandas")

from well_viewer.data.data_loading import df_included_mask
from well_viewer.features.qc.controller import get_qc_params, set_qc_params
from well_viewer.features.qc.params import QCParams


def test_qc_gate_excludes_small_cells_via_chokepoint(loaded_app):
    app = loaded_app
    well = sorted(app._app_state.dataset.well_paths)[0]
    df = app._get_rows(well)
    areas = pd.to_numeric(df.get("area_px"), errors="coerce").dropna()
    if areas.empty or areas.nunique() < 2:
        pytest.skip("mini dataset has no usable area variation")

    rows_before = len(df)
    incl_before = int(df_included_mask(df).sum())
    thresh = float(areas.median()) + 1.0

    set_qc_params(app, QCParams(enabled=True, min_area=thresh))

    df2 = app._get_rows(well)
    # Non-destructive: no rows removed.
    assert len(df2) == rows_before
    assert "QC_Pass" in df2.columns
    # The chokepoint now excludes the sub-threshold cells.
    incl_after = int(df_included_mask(df2).sum())
    assert incl_after < incl_before
    assert get_qc_params(app).min_area == thresh


def test_qc_disabled_includes_everything(loaded_app):
    app = loaded_app
    set_qc_params(app, QCParams(enabled=False))
    well = sorted(app._app_state.dataset.well_paths)[0]
    df = app._get_rows(well)
    # QC_Pass all-pass when disabled → chokepoint == manual Included only.
    qc = pd.to_numeric(df["QC_Pass"], errors="coerce")
    assert (qc == 1).all()
