"""Multivariate can take each cell's own nanowell's metrics as features (NANOWELL_OBSERVATIONS_TRACKER #10).

On a nanowell plate the feature picker gains a **Nanowell** family; a cell's value is its own nanowell's at its
timepoint, joined on its address. So a PCA or correlation over GFP and the nanowell's count, confluence and
fluorescence is possible, and a cell's row holds its nanowell's number — not a well's, not a neighbour's.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def nw_ctx(qtbot, tmp_path):
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests.fixtures.nanowell_dataset import build_nanowell_dataset

    root = build_nanowell_dataset(tmp_path / "nw")
    ctx, _window = minimal_shell(qtbot)
    load_dataset(ctx, root / "out")
    ctx.state.dataset.data_dir = root / "out"
    ctx.state.selection.selected_wells = {"A01", "B01"}
    return ctx


def test_the_picker_offers_the_nanowell_family_and_the_matrix_holds_each_cell_s_own_nanowell(nw_ctx):
    from review.features.multivariate.collect import available_feature_columns, collect_feature_matrix
    from tests.fixtures.nanowell_dataset import COUNTS, GFP

    families = available_feature_columns(nw_ctx)
    assert "nw::n" in families["Nanowell"] and "nw::c" in families["Nanowell"]
    out = collect_feature_matrix(nw_ctx, feature_cols=["gfp_mean_intensity", "nw::n"], timepoints=[24.0])
    got = sorted(map(tuple, out["X"].tolist()))
    want = sorted((GFP[w][i], float(n)) for w in COUNTS for i, n in enumerate(COUNTS[w]["24h"]) for _ in range(n))
    assert got == want
    assert out["feature_labels"] == ["GFP Mean Intensity", "Nanowell ▸ Cell count"]


def test_a_standard_plate_has_no_nanowell_family(qtbot, mini_dataset):
    from review.features.multivariate.collect import available_feature_columns
    from tests._minimal_shell import load_dataset, minimal_shell

    ctx, _window = minimal_shell(qtbot)
    load_dataset(ctx, mini_dataset)
    assert "Nanowell" not in available_feature_columns(ctx)
