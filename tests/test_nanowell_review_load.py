"""Review reads what a real nanowell run wrote (tracker D1), and the well loader never reads it as a well.

The dataset comes from ``process_microscopy.main`` in nanowell mode over two rendered timepoints — the same
harness as ``test_nanowell_cli`` — so a column, a file name or a folder the pipeline changes breaks this test.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from review.features.nanowell.collect import load_nanowell_data
from review.features.nanowell.series_compute import site_metrics, site_series
from tests._nanowell_run_fixture import build_well, paint_cell
from tests.test_nanowell_cli import WELL, _run_main, _two_timepoint_plate, _write_plate


@pytest.fixture(scope="module")
def run_output(tmp_path_factory):
    monkeypatch = pytest.MonkeyPatch()
    try:
        plate, _record, _rendered = _two_timepoint_plate(tmp_path_factory.mktemp("nw_review"))
        yield _run_main(monkeypatch, plate, "--nanowell_format", "100um")
    finally:
        monkeypatch.undo()


def test_a_real_run_s_tables_load_and_join_each_nanowell_across_both_timepoints(run_output):
    data = load_nanowell_data(run_output)
    assert data.wells == [WELL] and data.problems == [] and data.channels == ["GFP"]
    assert data.timepoints(WELL) == ["0h", "24h"] and data.time_source == "label"
    metrics = site_metrics(data)
    addressed = metrics.drop_duplicates(["nw_q", "nw_r"])
    assert len(addressed) > 50
    series = site_series(metrics, WELL, 0, 0)
    assert list(series["timepoint"]) == ["0h", "24h"] and list(series["time_h"]) == [0.0, 24.0]
    assert set(series["state"]) <= {"measured", "empty", "occluded", "clipped", "absent"}


def test_the_well_loader_scans_only_the_top_level_and_would_not_take_a_nanowell_table_as_a_well(run_output):
    """``load_directory`` lists ``*.csv`` at the top of the folder; the nanowell tables are one level down."""
    from review.core import load_controller

    scanned: list[str] = []

    class _Stop(Exception):
        pass

    def phase(_ctx, text, **_kw):
        scanned.append(text)
        if text.startswith("Scanning ") and "file(s)" in text:
            raise _Stop

    original = load_controller._opening_set_phase
    load_controller._opening_set_phase = phase
    try:
        with pytest.raises(_Stop):
            load_controller.load_directory(SimpleNamespace(window=None), run_output)
    finally:
        load_controller._opening_set_phase = original
    top_level = sorted(p.name for p in run_output.glob("*.csv"))
    assert scanned[-1] == f"Scanning {len(top_level)} file(s)…" and top_level == [f"measurements_{WELL}.csv"]
    table = run_output / "nanowells" / f"{WELL}.csv"
    assert table.exists() and not load_controller._looks_like_well_measurement_csv(table)


@pytest.fixture(scope="module")
def run_with_a_cell(tmp_path_factory):
    """One timepoint with a cell painted in nanowell (1, 2) and a GFP glow over it, as test_nanowell_cli does."""
    rendered, record = build_well(timepoint="0h")
    truth = dict(zip(rendered.array.addresses, rendered.sites_um))
    paint_cell(rendered, truth[(1, 2)], dx_um=3.0, dy_um=-2.0)
    glow = SimpleNamespace(images={f: image * 0 + 10.0 for f, image in rendered.images.items()},
                           true_left_top_um=rendered.true_left_top_um)
    paint_cell(glow, truth[(1, 2)], dx_um=3.0, dy_um=-2.0, amount=200.0)
    monkeypatch = pytest.MonkeyPatch()
    try:
        plate = _write_plate(tmp_path_factory.mktemp("nw_review_cell"), rendered, record, fluorescence=glow.images)
        yield _run_main(monkeypatch, plate, "--nanowell_format", "100um")
    finally:
        monkeypatch.undo()


def test_brightness_comes_from_the_cells_the_run_placed_in_each_nanowell(run_with_a_cell):
    import pandas as pd

    sites = load_nanowell_data(run_with_a_cell).sites
    cells = pd.read_csv(run_with_a_cell / f"measurements_{WELL}.csv")
    assert len(cells) == 1
    lit = sites[(sites["nw_q"] == 1) & (sites["nw_r"] == 2)]
    assert lit["brightness_GFP"].tolist() == pytest.approx([cells["GFP_mean_intensity"].iloc[0]])
    assert lit["brightness_GFP"].iloc[0] > 100.0
    assert sites.drop(lit.index)["brightness_GFP"].isna().all()                 # no cells, no brightness
