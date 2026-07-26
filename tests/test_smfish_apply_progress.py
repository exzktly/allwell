"""smFISH "Apply Global Threshold" progress reporting.

Apply to All drives the shared status-row progress bar the same way a dataset
load does. The bar is only honest if the worker reports every step of both
passes — the per-well counting pass and the CSV-write pass — and if every
terminal path still reaches ``done_cb``, which is what hides the bar again.

These tests exercise the worker only (Qt-free, fast lane): the bar itself is a
thin ``ctx.widgets`` lookup in ``tab_view``, but the reporting contract it
depends on lives here.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from well_viewer.data.image_identity import normalize_tp
from well_viewer.features.smfish.worker import (
    _write_counts_to_csvs,
    apply_global_threshold_async,
)


def _make_csv(path: Path, well: str) -> None:
    path.write_text(pd.DataFrame({
        "well": [well],
        "fov": ["1"],
        "timepoint": ["24h"],
        "nucleus_id": ["1"],
    }).to_csv(index=False))


def test_write_loop_reports_progress_for_every_well(tmp_path):
    wells = ["B02", "C03", "D04"]
    well_to_csv = {}
    for well in wells:
        csv_path = tmp_path / f"{well}.csv"
        _make_csv(csv_path, well)
        well_to_csv[well] = csv_path
    counts = {
        (well, "1", normalize_tp("24h"), "1"): 3 for well in wells
    }
    seen: list[tuple[int, int]] = []

    written = _write_counts_to_csvs(
        out_dir=tmp_path,
        well_to_zip={well: Path("ignored") for well in wells},
        counts=counts,
        column="w3cy5_smfish_count",
        well_to_csv=well_to_csv,
        progress_cb=lambda done, total: seen.append((done, total)),
    )

    assert written == 3
    # One reset at the start, then one report per well, ending at the maximum —
    # a bar driven by this sequence fills exactly once and finishes full.
    assert seen == [(0, 3), (1, 3), (2, 3), (3, 3)]


def test_write_loop_advances_past_a_well_with_no_csv(tmp_path):
    """A well the writer skips still moves the bar. Otherwise a dataset with an
    unresolvable CSV leaves the bar parked short of its maximum with no further
    reports coming."""
    csv_path = tmp_path / "B02.csv"
    _make_csv(csv_path, "B02")
    seen: list[tuple[int, int]] = []

    written = _write_counts_to_csvs(
        out_dir=tmp_path,
        well_to_zip={"B02": Path("ignored"), "C03": Path("ignored")},
        counts={("B02", "1", normalize_tp("24h"), "1"): 9},
        column="w3cy5_smfish_count",
        # C03 is absent from the map, so _resolve_csv_path returns None and the
        # writer skips the well.
        well_to_csv={"B02": csv_path},
        progress_cb=lambda done, total: seen.append((done, total)),
    )

    assert written == 1
    assert seen == [(0, 2), (1, 2), (2, 2)]


def test_write_loop_runs_without_a_progress_callback(tmp_path):
    """``progress_cb`` is optional — the pipeline-side callers pass nothing."""
    csv_path = tmp_path / "B02.csv"
    _make_csv(csv_path, "B02")

    written = _write_counts_to_csvs(
        out_dir=tmp_path,
        well_to_zip={"B02": Path("ignored")},
        counts={("B02", "1", normalize_tp("24h"), "1"): 4},
        column="w3cy5_smfish_count",
        well_to_csv={"B02": csv_path},
    )

    assert written == 1


def test_missing_channel_still_fires_done_cb():
    """The empty-channel guard used to ``return`` after a status line only.
    ``done_cb`` is the terminal signal: it clears the tab's busy flag and hides
    the progress bar, so skipping it wedged the button until a restart."""
    done: list[str] = []

    thread = apply_global_threshold_async(
        out_dir=Path("/tmp"),
        well_to_zip={},
        channel="",                      # nothing selected in the combo
        threshold=1.0,
        classifier=None,
        fov_tp_extractor=None,
        status_cb=lambda _m: None,
        done_cb=done.append,
    )
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert done, "the empty-channel path must reach done_cb"
    assert "channel" in done[0].lower()
