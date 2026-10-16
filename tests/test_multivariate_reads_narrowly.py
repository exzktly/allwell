"""Multivariate's feature matrix is the same whether its wells are read narrowly or held (tracker F3g).

``collect_feature_matrix`` reads each well through ``narrow_rows`` — its
features, the timepoint and what the verdicts read — rather than whole frames
through the row cache, and builds its group / well / timepoint columns from
per-well arrays rather than per-cell lists. Neither may change the matrix: held
frames and narrow reads must give the same rows, in the same order, with every
inclusion layer that applies.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from review.data import data_loading as dl
from review.domain.qc_params import QCParams
from review.domain.ratio_models import RatioMetric
from review.features.multivariate.collect import collect_feature_matrix

pytest.importorskip("pyarrow.parquet")

RATIO = RatioMetric(name="g_over_c", numerator_channel="gfp", numerator_metric="mean_intensity",
                    denominator_channel="cy5", denominator_metric="mean_intensity")
FEATURES = ["gfp_mean_intensity", "area_px", RATIO.key()]


@pytest.fixture
def ctx(tmp_path):
    from review.core.app_runtime import AppRuntime
    from review.core.app_state import AppState
    from review.core.event_bus import EventBus
    from review.core.widget_registry import WidgetRegistry
    from review.data.well_repository import WellRepository
    from review.features.registry import attach_feature_state, build_feature_registry
    from review.viewer_context import ViewerContext

    build_feature_registry()
    state = AppState()
    attach_feature_state(state)
    rng = np.random.default_rng(4)
    for well, n in {"A01": 400, "B02": 250}.items():
        path = tmp_path / f"{well}.csv"
        pd.DataFrame({
            "well": [well] * n, "fov": [str(1 + i % 2) for i in range(n)],
            "timepoint": ["0h" if i % 3 else "24h" for i in range(n)],
            "timepoint_hours": [0.0 if i % 3 else 24.0 for i in range(n)],
            "filename": [f"{well}.tif"] * n, "nucleus_id": [str(i) for i in range(n)],
            "area_px": np.where(np.arange(n) % 11 == 0, 10.0, rng.uniform(80, 200, n)),
            "gfp_mean_intensity": rng.lognormal(3, 1, n), "cy5_mean_intensity": rng.uniform(1, 9, n),
            "m0": rng.normal(size=n), "m1": rng.normal(size=n),
        }).to_csv(path, index=False)
        state.dataset.well_paths[well] = path
        dl.read_well_frame(path)
    state.selection.selected_wells = {"A01", "B02"}
    state.channel.ratio_index = {RATIO.key(): RATIO}
    state.qc.params = QCParams(enabled=True, min_area=50.0)       # a layer that excludes cells
    store: dict = {}
    c = ViewerContext(state=state, bus=EventBus(), widgets=WidgetRegistry(None, store=store),
                      runtime=AppRuntime(None, store=store))
    state.repo = WellRepository(c)
    return c


def test_narrow_reads_give_the_held_frames_matrix(ctx):
    ctx.state.dataset.cache.clear()
    narrow = collect_feature_matrix(ctx, feature_cols=FEATURES, timepoints=[0.0])
    assert len(ctx.state.dataset.cache) == 0, "the collector read whole frames into the row cache"
    for well in ("A01", "B02"):
        dl.get_rows(ctx, well)
    held = collect_feature_matrix(ctx, feature_cols=FEATURES, timepoints=[0.0])
    assert narrow["n_cells"] == held["n_cells"] > 0
    assert held["n_cells"] < 650 * 2 / 3, "the QC layer excluded nothing; the comparison is weaker than it looks"
    assert np.array_equal(narrow["X"], held["X"])
    for key in ("group", "well", "timepoint"):
        assert narrow[key].tolist() == held[key].tolist(), key
    assert narrow["group"].dtype == object and narrow["timepoint"].dtype == float
