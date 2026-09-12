"""Review CSV must not materialise the whole plate to show five thousand rows.

`_refresh_review_csv_impl` built one Python dict per cell for every selected
well, derived the FOV / timepoint combo values by scanning that list twice per
row, handed it on, and only then applied `MAX_TABLE_ROWS`. The cap therefore
bounded the `QTableWidget` and nothing above it. The module's own comment
already called the unfiltered path "the OOM that SIGKILLs the app"; what it
described was the widget, and the list feeding the widget was the larger half.

Measured on a 24-well plate of 28,800 rows and 40 columns, that pooled list
costs ~200 MB resident — about 7 KB per row — and it scales with the plate, so
a real plate of several hundred thousand cells reaches several gigabytes. A
user opening a plate mid-run hit `MemoryError: unable to allocate 4.56 MiB for
an array with shape (597726,) and data type object`; 4.56 MiB is nothing, so
the process was already at its ceiling when that array was asked for.

These guards assert on **counts**, never on resident memory: RSS is a property
of the machine, and a test that passes because CI has more memory than a laptop
guards nothing.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.qt


#: Wide enough that a full pool exceeds the cap: 4 wells × 3 timepoints ×
#: 6 cells × 100 FOVs = 7,200 rows against a 5,000-row cap. The real plate that
#: produced the report was two orders of magnitude past this; the point here is
#: only to be on the far side of the cap.
_N_FOVS = 100
_EXPECTED_ROWS = 4 * 3 * 6 * _N_FOVS


@pytest.fixture
def wide_dataset(tmp_path):
    from tests.fixtures.mini_dataset import build_mini_dataset
    return build_mini_dataset(tmp_path / "wide_dataset", n_fovs=_N_FOVS)


@pytest.fixture
def wide_app(viewer_app, wide_dataset):
    viewer_app._load_path(wide_dataset)
    return viewer_app


def _cap() -> int:
    """The cap, read from the module when it names one.

    The fallback lets this file measure real behaviour against a branch that
    still keeps the number as a local inside the function.
    """
    from review.features.image_browser.review_image import controller as RC
    return int(getattr(RC, "MAX_TABLE_ROWS", 5000))


class _RecordCounter:
    """Counts the row dicts pandas is asked to build during a call."""

    def __init__(self, monkeypatch):
        import pandas as pd
        self.total = 0
        real = pd.DataFrame.to_dict

        def spy(df, orient="dict", *a, **k):
            out = real(df, orient, *a, **k)
            if orient == "records":
                self.total += len(out)
            return out

        monkeypatch.setattr(pd.DataFrame, "to_dict", spy)


def test_refreshing_review_csv_does_not_pool_the_whole_plate(
        wide_app, monkeypatch):
    """The guard. Every selected well's rows used to be materialised first and
    truncated afterwards."""
    app = wide_app
    total_rows = sum(len(df) for df in app._app_state.dataset.cache.values())
    assert total_rows == _EXPECTED_ROWS, (
        f"fixture drifted: {total_rows} rows, expected {_EXPECTED_ROWS} — this "
        "test only bites when the plate is larger than the cap"
    )

    assert app._notebook.setCurrentByName("Review CSV")
    app._set_selected_wells(set(app._app_state.dataset.well_paths), commit=False)

    counter = _RecordCounter(monkeypatch)
    from review.features.image_browser.review_image.controller import (
        refresh_review_csv,
    )
    refresh_review_csv(app._ctx)

    cap = _cap()
    assert counter.total <= cap, (
        f"Review CSV materialised {counter.total} row dicts to show at most "
        f"{cap} — the whole plate ({total_rows} rows) was pooled before the cap "
        f"was applied. At ~7 KB per row that is where the memory went."
    )


def test_the_table_still_fills_and_is_capped(wide_app):
    """The saving must not come from showing nothing."""
    app = wide_app
    assert app._notebook.setCurrentByName("Review CSV")
    app._set_selected_wells(set(app._app_state.dataset.well_paths), commit=False)

    from review.features.image_browser.review_image.controller import (
        refresh_review_csv,
    )
    refresh_review_csv(app._ctx)

    table = app._review_csv_table
    assert table.rowCount() > 0, "the table came back empty"
    assert table.rowCount() <= _cap()
    assert table.columnCount() > 0


def test_the_combo_values_cover_every_fov_and_timepoint(wide_app):
    """The FOV / timepoint combos are derived from distinct column values now,
    not from the pooled rows — they must still offer all of them, or the user
    cannot reach the fields the truncation hid."""
    app = wide_app
    assert app._notebook.setCurrentByName("Review CSV")
    app._set_selected_wells(set(app._app_state.dataset.well_paths), commit=False)

    from review.features.image_browser.review_image.controller import (
        refresh_review_csv,
    )
    refresh_review_csv(app._ctx)

    fov_cb = app._ctx.widgets.get_or_none("_review_fov_cb")
    tp_cb = app._ctx.widgets.get_or_none("_review_tp_cb")
    assert fov_cb is not None and tp_cb is not None
    assert fov_cb.count() == _N_FOVS, (
        f"{fov_cb.count()} FOV(s) offered, expected {_N_FOVS}"
    )
    assert tp_cb.count() == 3, f"{tp_cb.count()} timepoint(s) offered, expected 3"


def test_a_selected_fov_and_timepoint_narrow_the_table(wide_app):
    """Filtering happens on the frame now. The rows shown must be only that
    field of view and that timepoint — 4 wells × 6 cells = 24 rows here."""
    app = wide_app
    assert app._notebook.setCurrentByName("Review CSV")
    app._set_selected_wells(set(app._app_state.dataset.well_paths), commit=False)

    from review.features.image_browser.review_image.controller import (
        refresh_review_csv, refresh_review_csv_rows,
    )
    refresh_review_csv(app._ctx)
    fov_cb = app._ctx.widgets.get_or_none("_review_fov_cb")
    tp_cb = app._ctx.widgets.get_or_none("_review_tp_cb")
    fov_cb.setCurrentText(fov_cb.itemText(0))
    tp_cb.setCurrentText(tp_cb.itemText(0))
    refresh_review_csv_rows(app._ctx, None)

    table = app._review_csv_table
    assert 0 < table.rowCount() <= 4 * 6, (
        f"{table.rowCount()} rows for one FOV at one timepoint on a 4-well "
        "plate of 6 cells each"
    )
