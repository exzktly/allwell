"""Well colouring is consistent across tabs.

The sidebar plate picker must tint each selected well by its absolute
plate-position rank (``color_for_well``) on EVERY per-well tab — the same colour
every plot renderer uses — not just on the plotting tabs. Previously the rank
tint was gated on ``_PLOTTING_TABS`` so the Multivariate picker fell back to a
selection-order palette and disagreed with both the plotting picker and the
Multivariate plot.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.qt


def test_multivariate_picker_pushes_rank_colors(loaded_app):
    from well_viewer.domain.selections_model import color_for_well

    app = loaded_app
    wells = list(app._app_state.dataset.well_paths.keys())[:3]
    assert wells, "dataset has wells"
    # Per-well mode (no replicate-set groups).
    app._app_state.selection.selections = []
    app._app_state.selection.selected_wells = set(wells)

    captured: dict = {}
    orig = app._sidebar_plate.setWellColors

    def _spy(mapping):
        captured.clear()
        captured.update(mapping)
        return orig(mapping)

    app._sidebar_plate.setWellColors = _spy

    app._notebook.setCurrentByName("Multivariate")
    app._refresh_sidebar_map_now()

    # Every selected well tinted by its rank colour — identical to what a
    # plotting tab pushes and to the plotted point colour.
    assert captured == {w: color_for_well(w) for w in wells}


def test_point_colors_by_well_uses_well_colors_when_groups_active():
    import numpy as np
    from well_viewer.features.multivariate.renderer import _point_colors

    # rep-set mode: group_colors is keyed by GROUP name; well_colors by well.
    data = {
        "well": np.array(["A01", "A02"], dtype=object),
        "group": np.array(["g1", "g1"], dtype=object),
        "group_colors": {"g1": "#111111"},
        "well_colors": {"A01": "#ff0000", "A02": "#00ff00"},
    }
    colors, legend = _point_colors(data, "well")
    # Uses the well-keyed colours, not the group_colors default fallback.
    assert colors == ["#ff0000", "#00ff00"]
    assert dict(legend) == {"A01": "#ff0000", "A02": "#00ff00"}
