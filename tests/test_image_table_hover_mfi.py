"""The Image Table's hover MFI readout must not re-read a well per cell.

Reported: in the Image Table, with one timepoint and one FOV selected, "even
just clicking on an image causes the app to stop responding". The click itself
does nothing heavy; the mouse move that precedes it does. Each cell the cursor
entered ran ``_cell_mfi`` on the UI thread, which fetched the well's whole frame
through ``get_rows`` (a full CSV/Parquet read whenever the row cache did not
hold the well — and since the load stopped reading every well up front, it
usually does not) and then built one Python key tuple per row: ~1.4 s per cell
on a 230k-row well before any read.

What holds it now:

* the mouse handler reads nothing from disk: the cell's mask and the well's
  index are read by a background job, and the readout says "loading" until
  it lands (the data sat on a network drive, where every read is slow);
* the per-well ``{(fov, tp): {nid: MFI}}`` index is built once per
  ``(well, channel)`` and memoized on the Image Table state, so entering a
  second cell of the same well reads nothing;
* the rows come through ``narrow_rows`` (identity columns + the one intensity
  column), never ``get_rows``;
* the faster derivation gives the same answer as the per-row key-tuple build it
  replaces, on tokens that need normalising (``01`` / ``1.0`` FOVs, ``3.0`` ids,
  blank ids, a non-finite value);
* ``map_distinct`` is the per-row map, value for value, including on a series
  with missing values.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from domain.image_identity import cell_key_tuples_for_dataframe, map_distinct, normalize_tp
from review.features.image_browser.image_table import _hover_mfi
from review.features.image_browser.image_table import controller as it_controller
from review.features.image_browser.image_table.state import ImageTableState
from review.features.image_browser.review_image import collect


def _messy_well() -> pd.DataFrame:
    """Rows whose frame and id tokens all need normalising, in an order where a
    lookup that skipped the normalisation would land on a different cell."""
    return pd.DataFrame({
        "well": ["A01"] * 8,
        "fov": ["01", "1.0", "2", "2", "1", "1", "02", "1"],
        "timepoint": ["0", "0", "0", "24", "24", "0", "24", "0"],
        "nucleus_id": ["3.0", "4", "3", "3", "3", "", "4", "7"],
        "gfp_mean_intensity": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, np.inf],
    })


def _reference_index(df: pd.DataFrame, col: str) -> dict:
    """The derivation the fix replaced: one normalised key tuple per row."""
    out: dict = {}
    keys = cell_key_tuples_for_dataframe(df)
    for (fov, tp, nid), v in zip(keys, pd.to_numeric(df[col], errors="coerce")):
        if nid == "" or not np.isfinite(v):
            continue
        out.setdefault((fov, tp), {})[int(float(nid))] = float(v)
    return out


@pytest.fixture
def reads(monkeypatch):
    """Serve ``_messy_well`` through ``narrow_rows`` and count the reads; any
    ``get_rows`` call fails the test, because that is the whole-frame read the
    readout must not make."""
    import review.data.narrow_rows as nr

    calls: list[tuple] = []

    def _narrow(ctx, well, want):
        calls.append((well, frozenset(want or ())))
        return _messy_well()

    def _no_full_read(*_a, **_k):
        raise AssertionError("the hover readout read the whole well via get_rows")

    monkeypatch.setattr(nr, "narrow_rows", _narrow)
    monkeypatch.setattr(collect, "get_rows", _no_full_read)
    return calls


def test_the_index_matches_the_per_row_key_build_it_replaces(reads):
    got = collect.review_mfi_index(None, "A01", channel="gfp")
    assert got == _reference_index(_messy_well(), "gfp_mean_intensity")
    # Spelled out, so the reference cannot be wrong in the same way as the code:
    # "01" and "1.0" are FOV 1, "3.0" is nucleus 3, the blank id and the inf
    # value are dropped, and the first duplicate (fov 1, tp 0, nid 3) is
    # overwritten by the later row exactly as the old dict build did.
    assert got == {
        ("1", "0"): {3: 10.0, 4: 20.0},
        ("2", "0"): {3: 30.0},
        ("2", "24"): {3: 40.0, 4: 70.0},
        ("1", "24"): {3: 50.0},
    }
    assert reads == [("A01", frozenset({"gfp_mean_intensity"}))]


def test_the_frame_map_keeps_only_nuclei_in_the_mask(reads):
    mask = np.array([[0, 4], [4, 9]], dtype=np.int32)
    got = collect.review_build_mfi_map(None, mask, "A01", "1", "0", channel="gfp")
    assert got == {4: 20.0}


def test_a_channel_without_an_intensity_column_reads_as_no_readout(reads):
    assert collect.review_mfi_index(None, "A01", channel="rfp") == {}


def _ctx():
    return SimpleNamespace(state=SimpleNamespace(image_table=ImageTableState()))


def test_moving_across_cells_of_one_well_builds_the_index_once(reads, monkeypatch):
    mask = np.zeros((4, 4), dtype=np.int32)
    mask[1, 2] = 4
    monkeypatch.setattr(_hover_mfi, "_load_mask_array",
                        lambda ctx, cache, well, tp, fov: mask)
    monkeypatch.setattr(_hover_mfi, "_submit_mfi", lambda job, **_k: job())
    ctx = _ctx()
    first, second = SimpleNamespace(), SimpleNamespace()

    assert it_controller._cell_mfi(ctx, first, "gfp", "A01", "0", "01", 2, 1) == 20.0
    # Same well, another cell (a binary variant of the channel, a raw-token FOV):
    # served from the memo, not a second read of the well.
    assert it_controller._cell_mfi(ctx, second, "gfp (binary)", "A01", "t0", "1", 2, 1) == 20.0
    assert it_controller._cell_mfi(ctx, second, "gfp", "A01", "0", "1", 0, 0) is None
    assert len(reads) == 1



def test_a_new_generation_drops_the_index_and_disowns_old_cells(reads, monkeypatch):
    """What ``clear_caches`` — which the load's swap path calls; the slow-lane
    ``test_image_table_hover_follows_the_dataset.py`` drives that real path —
    must do to the hover readout: a fresh memo, and a cell drawn for the previous
    generation quoting nothing rather than the previous dataset's MFI. A lookup
    queued before the bump writes into the memo it captured, never the new one."""
    mask = np.zeros((4, 4), dtype=np.int32)
    mask[1, 2] = 4
    monkeypatch.setattr(_hover_mfi, "_load_mask_array",
                        lambda ctx, cache, well, tp, fov: mask)
    queued: list = []
    monkeypatch.setattr(_hover_mfi, "_submit_mfi", lambda job, **_k: queued.append(job))
    ctx, old_cell, queued_cell = _ctx(), SimpleNamespace(), SimpleNamespace()
    assert it_controller._cell_mfi(ctx, old_cell, "gfp", "A01", "0", "1", 2, 1) is it_controller.MFI_PENDING
    queued.pop()()
    assert it_controller._cell_mfi(ctx, old_cell, "gfp", "A01", "0", "1", 2, 1) == 20.0
    assert it_controller._cell_mfi(ctx, queued_cell, "gfp", "A01", "0", "1", 2, 1) is it_controller.MFI_PENDING
    st = ctx.state.image_table
    st.mfi_index.clear()                     # so the queued job has to build it

    st.clear_caches()
    queued.pop()()                           # lands after the swap

    assert st.mfi_index == {}, "a lookup queued before the swap wrote into the new memo"
    assert it_controller._cell_mfi(ctx, old_cell, "gfp", "A01", "0", "1", 2, 1) is None, (
        "a cell drawn for the previous dataset still quotes its MFI")
    monkeypatch.setattr(_hover_mfi, "_submit_mfi", lambda job, **_k: job())
    assert it_controller._cell_mfi(ctx, SimpleNamespace(), "gfp", "A01", "0", "1", 2, 1) == 20.0
    assert len(reads) == 3, "the new generation must read the well again"


def test_the_mouse_handler_never_reads_the_disk_itself(reads, monkeypatch):
    """The reads go to the background job; until it has run, the readout says
    the MFI is loading instead of blocking — or silently showing no MFI."""
    loads: list = []

    def _mask(ctx, cache, well, tp, fov):
        loads.append(well)
        return np.full((4, 4), 4, dtype=np.int32)

    queued: list = []
    monkeypatch.setattr(_hover_mfi, "_load_mask_array", _mask)
    monkeypatch.setattr(_hover_mfi, "_submit_mfi", lambda job, **_k: queued.append(job))
    ctx, label = _ctx(), SimpleNamespace()

    for _ in range(3):  # three mouse moves before the worker gets to it
        assert it_controller._cell_mfi(ctx, label, "gfp", "A01", "0", "1", 1, 1) \
            is it_controller.MFI_PENDING
    assert loads == [] and reads == [], "a mouse move read the disk on the UI thread"
    assert len(queued) == 1, "one cell must queue one job, however often the mouse moves"

    queued[0]()  # the worker runs
    assert it_controller._cell_mfi(ctx, label, "gfp", "A01", "0", "1", 1, 1) == 20.0
    assert loads == ["A01"] and len(reads) == 1


@pytest.mark.parametrize("values", [
    ["0", "24", "0", "t24", "", "24h"],
    ["0", None, "24", None],
    [],
])
def test_map_distinct_is_the_per_row_map(values):
    s = pd.Series(values, dtype=object, index=range(10, 10 + len(values)))
    expected = s.map(normalize_tp)
    got = map_distinct(s, lambda u: u.map(normalize_tp))
    assert got.index.equals(expected.index)
    assert list(got) == list(expected)


@pytest.mark.parametrize("ids, dtype", [
    ([3, 4, 3, 3, 3, 9, 4, 7], "int64"),
    ([3.0, 4.0, 3.0, 3.0, 3.0, np.nan, 4.0, 7.7], "float64"),
    ([3, 4, 3, 3, 3, None, 4, 7], "Int64"),
])
def test_numeric_ids_take_the_fast_path_to_the_same_index(monkeypatch, ids, dtype):
    """A numeric id column skips the per-cell text normalisation; the index must
    be the one the text path builds from the same ids written as text."""
    import review.data.narrow_rows as nr

    numeric = _messy_well().assign(nucleus_id=pd.Series(ids, dtype=dtype))
    as_text = numeric.assign(nucleus_id=[("" if pd.isna(v) else str(v)) for v in numeric["nucleus_id"]])
    served = {"frame": numeric}
    monkeypatch.setattr(nr, "narrow_rows", lambda ctx, well, want: served["frame"])
    fast = dict(collect.review_mfi_index(None, "A01", channel="gfp"))
    served["frame"] = as_text
    slow = dict(collect.review_mfi_index(None, "A01", channel="gfp"))
    assert fast == slow == _reference_index(as_text, "gfp_mean_intensity")
    assert all(type(n) is int for frame in fast.values() for n in frame)


@pytest.mark.parametrize("column", [
    pd.Series([1, 2, -3, 40]),
    pd.Series([1.0, np.nan, 3.0, -7.0]),
    pd.Series([1.5, 2.0]),                  # fractional: the per-value path
    pd.Series([1e20, 2.0]),                 # scientific notation as text
    pd.Series([np.inf, 1.0]),
    pd.Series([5e-05, 1.0]),
    pd.Series(["3.0", "04", " 5 ", ""]),
    pd.Series(["7", "x"], dtype="category"),
    pd.Series([np.nan, np.nan]),
    pd.Series([], dtype=float),
])
def test_bulk_nucleus_id_normalisation_is_the_per_value_one(column):
    """``nucleus_id_series`` converts a numeric id column in bulk; whatever path
    it takes, each id must read as ``normalize_nucleus_id`` of its text."""
    from domain.image_identity import normalize_nucleus_id, nucleus_id_series
    from domain.text_columns import as_text

    want = as_text(column).map(normalize_nucleus_id).tolist()
    assert nucleus_id_series(column, column.index).tolist() == want
