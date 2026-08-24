"""A well whose spot-counting pass raised must not be written as zeros.

``apply_global_threshold_async`` fans the plate out across a thread pool and
merges each well's result into one ``counts`` dict. A well whose future raises
contributes nothing, so every lookup for its cells misses — and the write loop's
``counts.get(key, 0)`` then substitutes ``0`` for every row of that well's CSV.

The resulting column is indistinguishable from a well that genuinely has no
spots above threshold, which is the exact substitution CLAUDE.md section 7
forbids: the software knew the well had failed and wrote a plausible number
anyway. The well must instead be skipped, keeping its previous counts, and
counted in the run's shortfall so the banner says so.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from well_viewer.features.smfish.worker import _write_counts_to_csvs


def _plate(tmp_path: Path) -> tuple[Path, dict[str, Path], dict[str, Path]]:
    """Two wells, each with one row already carrying a previous count."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    well_to_zip = {}
    well_to_csv = {}
    for well in ("A01", "A02"):
        pd.DataFrame(
            {
                "well": [well],
                "fov": ["1"],
                "timepoint": ["0"],
                "nucleus_id": ["1"],
                "w3cy5_smfish_count": ["7"],
            }
        ).to_csv(out_dir / f"{well}.csv", index=False)
        well_to_zip[well] = out_dir / f"{well}.zip"
        well_to_csv[well] = out_dir / f"{well}.csv"
    return out_dir, well_to_zip, well_to_csv


def test_a_failed_well_keeps_its_previous_counts(tmp_path):
    out_dir, well_to_zip, well_to_csv = _plate(tmp_path)
    # A02 raised in the worker, so it contributed no keys at all. A01 succeeded
    # and genuinely found three spots.
    counts = {("A01", "1", "0", "1"): 3}

    written = _write_counts_to_csvs(
        out_dir, well_to_zip, counts, "w3cy5_smfish_count", well_to_csv,
        failed_wells={"A02"},
    )

    a01 = pd.read_csv(out_dir / "A01.csv", dtype=str)
    a02 = pd.read_csv(out_dir / "A02.csv", dtype=str)
    assert a01["w3cy5_smfish_count"].tolist() == ["3"]
    # The whole point: not "0". A fabricated zero here is a measurement the
    # run never made, and nothing downstream could tell it from a real one.
    assert a02["w3cy5_smfish_count"].tolist() == ["7"], (
        "the failed well was overwritten with a fabricated count"
    )
    # And it must not count as written, or the caller's shortfall arithmetic
    # reports the run complete.
    assert written == 1


def test_a_well_that_really_found_nothing_is_still_written_as_zero(tmp_path):
    """The other direction: zero is a measurement when the pass succeeded.

    Skipping must key on the well having *failed*, not on it being absent from
    ``counts`` — a well whose every cell fell below threshold is legitimately
    absent from ``counts`` and its zeros are real.
    """
    out_dir, well_to_zip, well_to_csv = _plate(tmp_path)
    counts = {("A01", "1", "0", "1"): 3}

    written = _write_counts_to_csvs(
        out_dir, well_to_zip, counts, "w3cy5_smfish_count", well_to_csv,
        failed_wells=set(),
    )

    a02 = pd.read_csv(out_dir / "A02.csv", dtype=str)
    assert a02["w3cy5_smfish_count"].tolist() == ["0"]
    assert written == 2
