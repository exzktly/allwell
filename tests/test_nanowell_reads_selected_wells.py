"""The Nanowells page reads only the wells it draws.

Asked for: opening the Nanowells tab read every well's nanowell table and the per-cell CSVs of every well,
whatever was selected — "only read selected wells". Each well's table, grid record and cells' brightness are
read when the well is first drawn and kept (``collect.WellPart``), so selecting it again reads nothing, and a
per-cell CSV named for another well is not opened.

Driven through the page's own hooks (``nw_on_activate``, ``nw_on_selection_changed``), counting the reads at
``collect.read_well_part`` — the one function that opens a well's files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from tests.test_nanowell_page import WELLS, _loaded, _page, _write_nanowell_dataset  # noqa: E402


@pytest.fixture
def counted(qtbot, tmp_path, monkeypatch):
    """The page over the three-well dataset, nothing selected, and every well read listed in order."""
    from review.features.nanowell import collect

    reads: list[str] = []
    real = collect.read_well_part

    def spy(folder, well, cell_files, wells):
        reads.append(well)
        return real(folder, well, cell_files, wells)

    monkeypatch.setattr(collect, "read_well_part", spy)
    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path))
    return ctx, reads


def _select(qtbot, ctx, wells):
    from review.features.nanowell import controller as nwc

    ctx.state.selection.selected_wells = set(wells)
    nwc.nw_on_selection_changed(ctx)
    st = ctx.state.feature_slice("nanowell")
    qtbot.waitUntil(lambda: not st.computing, timeout=20000)
    return st


def test_opening_the_page_with_nothing_selected_reads_no_well(counted, qtbot):
    from review.features.nanowell import controller as nwc

    ctx, reads = counted
    st = _loaded(qtbot, ctx)
    assert reads == []
    assert (st.data.wells, st.data.available) == ([], list(WELLS))
    assert nwc.nanowell_empty_reason(ctx).title == "No nanowell wells selected."


def test_each_well_is_read_when_it_is_first_selected_and_never_again(counted, qtbot):
    from review.features.nanowell import controller as nwc

    ctx, reads = counted
    ctx.state.selection.selected_wells = {"A01"}
    st = _loaded(qtbot, ctx)
    assert reads == ["A01"] and st.data.wells == ["A01"]
    _select(qtbot, ctx, {"A01", "B01"})
    assert reads == ["A01", "B01"]                                     # only the well added
    assert nwc.page_wells(ctx) == ["A01", "B01"]
    request = st.request
    _select(qtbot, ctx, {"B01"})
    assert reads == ["A01", "B01"] and st.request == request          # deselecting reads and recomputes nothing
    assert nwc.page_wells(ctx) == ["B01"]
    _select(qtbot, ctx, {"A01", "B01"})
    assert reads == ["A01", "B01"]                                     # A01 was kept: not read again
    _select(qtbot, ctx, {"A01", "A02", "B01"})
    assert reads == ["A01", "B01", "A02"]


def test_a_well_without_a_table_is_named_and_one_being_read_is_not(qtbot, tmp_path):
    from review.features.nanowell import controller as nwc

    out = _write_nanowell_dataset(tmp_path)
    for name in ("A02.csv", "A02_grid.json"):                         # A02 has cells, but no nanowell table
        (out / "nanowells" / name).unlink()
    ctx = _page(qtbot, out)
    ctx.state.selection.selected_wells = {"A01", "A02"}
    st = _loaded(qtbot, ctx)
    assert nwc.tableless_wells(ctx) == ["A02"]
    # B01 has a table that is not read yet: until it is, it is not reported as a well without one.
    ctx.state.selection.selected_wells = {"A01", "A02", "B01"}
    assert "B01" not in st.loaded_wells
    assert nwc.tableless_wells(ctx) == ["A02"]


def test_a_cell_csv_named_for_another_well_is_never_opened():
    from review.features.nanowell.collect import _cell_files_of

    files = [Path(n) for n in ("measurements_A01.csv", "measurements_A02.csv.gz", "measurements_A010.csv",
                               "merged_cells.csv")]
    wells = ["A01", "A02", "A010"]
    assert [p.name for p in _cell_files_of("A01", files, wells)] == ["measurements_A01.csv", "merged_cells.csv"]
    assert [p.name for p in _cell_files_of("A02", files, wells)] == ["measurements_A02.csv.gz", "merged_cells.csv"]


def test_a_subset_read_gives_each_well_the_numbers_a_whole_read_gives_it(tmp_path):
    """Every metric is the well's own (its baseline, its nanowells), so reading fewer wells changes none of them;
    held here against a whole read, brightness included, with the cells in one merged CSV as well as per well."""
    import pandas as pd

    from review.features.nanowell.collect import load_nanowell_data
    from review.features.nanowell.series_compute import site_metrics

    for merged in (False, True):
        out = _write_nanowell_dataset(tmp_path / f"m{int(merged)}")
        if merged:
            frames = [pd.read_csv(p) for p in sorted(out.glob("measurements_*.csv"))]
            for p in out.glob("measurements_*.csv"):
                p.unlink()
            pd.concat(frames).to_csv(out / "merged_cells.csv", index=False)
        whole = site_metrics(load_nanowell_data(out), "earliest")
        assert whole["brightness_GFP"].notna().any()
        for wells in (["A01"], ["A02", "B01"]):
            part = load_nanowell_data(out, wells=wells)
            assert part.wells == wells and part.available == list(WELLS)
            got = site_metrics(part, "earliest").reset_index(drop=True)
            want = whole[whole["well"].isin(wells)].reset_index(drop=True)
            pd.testing.assert_frame_equal(got[want.columns], want, check_dtype=False)


def test_save_nanowell_data_writes_the_wells_the_page_draws(counted, qtbot, tmp_path, monkeypatch):
    import pandas as pd

    from review.core import csv_export
    from review.features.nanowell import controller as nwc

    ctx, _reads = counted
    ctx.state.selection.selected_wells = {"A01", "B01"}
    _loaded(qtbot, ctx)
    _select(qtbot, ctx, {"B01"})                                       # A01 stays loaded, but is not drawn
    path = tmp_path / "out.csv"
    monkeypatch.setattr(csv_export, "ask_save_csv", lambda *_a, **_k: str(path))
    nwc.export_page_csv(ctx)
    assert sorted(pd.read_csv(path)["well"].unique()) == ["B01"]
