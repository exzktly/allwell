"""Audit E19 — smFISH apply-global must stop writing mid-loop on a swap.

The dataset-identity guard only ran once, before the (potentially slow,
many-well) CSV write loop. A dataset swap / cancel landing mid-loop would keep
writing late wells into the now-wrong dataset. ``_write_counts_to_csvs`` now
re-checks a ``should_abort`` callback at the top of every iteration.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from well_viewer.features.smfish.worker import _write_counts_to_csvs


def _make_csv(path: Path, well: str) -> None:
    pd.DataFrame(
        {"well": [well], "fov": ["f0"], "timepoint": ["0"], "nucleus_id": ["1"]}
    ).to_csv(path, index=False)


def test_write_loop_aborts_midway(tmp_path):
    wells = ["A1", "A2", "A3"]
    well_to_csv = {}
    for w in wells:
        p = tmp_path / f"{w}.csv"
        _make_csv(p, w)
        well_to_csv[w] = p
    well_to_zip = {w: tmp_path / f"{w}_out.zip" for w in wells}

    calls = {"n": 0}

    def _abort() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # allow the first well, abort before the second

    written = _write_counts_to_csvs(
        tmp_path, well_to_zip, {}, "cy5_smfish_count", well_to_csv,
        should_abort=_abort,
    )

    assert written == 1
    # Only A1 (sorted first) gained the count column; A2/A3 untouched.
    assert "cy5_smfish_count" in pd.read_csv(tmp_path / "A1.csv").columns
    assert "cy5_smfish_count" not in pd.read_csv(tmp_path / "A2.csv").columns
    assert "cy5_smfish_count" not in pd.read_csv(tmp_path / "A3.csv").columns


def test_write_loop_writes_all_when_not_aborted(tmp_path):
    wells = ["A1", "A2"]
    well_to_csv = {}
    for w in wells:
        p = tmp_path / f"{w}.csv"
        _make_csv(p, w)
        well_to_csv[w] = p
    well_to_zip = {w: tmp_path / f"{w}_out.zip" for w in wells}

    written = _write_counts_to_csvs(
        tmp_path, well_to_zip, {}, "cy5_smfish_count", well_to_csv,
        should_abort=lambda: False,
    )
    assert written == 2
    for w in wells:
        assert "cy5_smfish_count" in pd.read_csv(tmp_path / f"{w}.csv").columns
