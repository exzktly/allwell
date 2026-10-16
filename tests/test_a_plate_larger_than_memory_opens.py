"""The real app opens a plate that does not fit its row-cache budget (tracker M1–M5).

End to end, through ``ReviewApp._load_path``: the same dataset opened once with
the default budget and once with a budget that holds a single well. The second
must open, hold less than the plate, and agree with the first on everything the
open produces — the timepoint and FOV lists, the channels, the threshold range,
the FOV verdicts — and on every well's included rows when they are read back.
"""

from __future__ import annotations

import pytest

from review.data.data_loading import df_included_mask
from review.data.row_cache import BUDGET_ENV


def _open(qtbot, path):
    from review.runtime_app import ReviewApp

    app = ReviewApp()
    qtbot.addWidget(app)
    app._load_path(path)
    return app


def _facts(app):
    dataset, channel = app._app_state.dataset, app._app_state.channel
    return {
        "timepoints": list(dataset.all_timepoints_cache),
        "fovs": list(dataset.all_fovs_cache),
        "channels": list(channel.fluor_channels),
        "range": (channel.threshold_min, channel.threshold_max),
        "fov_verdicts": {w: v.trusted for w, v in dataset.fov_integrity.items()},
        "included": {w: int(df_included_mask(app._get_rows(w)).sum()) for w in dataset.well_paths},
    }


@pytest.mark.slow
def test_a_plate_larger_than_the_budget_opens_and_agrees(qtbot, mini_dataset, monkeypatch, caplog):
    monkeypatch.delenv(BUDGET_ENV, raising=False)
    whole = _open(qtbot, mini_dataset)
    expected = _facts(whole)
    assert len(whole._app_state.dataset.cache) == len(whole._app_state.dataset.well_paths)

    monkeypatch.setenv(BUDGET_ENV, "0.001")           # about 1 KB: one well at a time
    caplog.clear()
    with caplog.at_level("ERROR"):
        small = _open(qtbot, mini_dataset)
    errors = [r.getMessage() for r in caplog.records if r.levelname in ("ERROR", "CRITICAL")]
    assert errors == [], errors
    dataset = small._app_state.dataset
    assert len(dataset.cache) < len(dataset.well_paths), "the plate fitted; nothing was exercised"
    assert sorted(dataset.summaries) == sorted(dataset.well_paths)
    assert _facts(small) == expected
    assert len(dataset.cache) == 1
