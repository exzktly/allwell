"""Regression tests for ``well_viewer.features.smfish.worker._write_counts_to_csvs``.

Pins two writer bugs that made Apply Global Threshold appear to do nothing:

1. **Column-case collision.** The writer wrote the column name as
   ``f"{channel.lower()}_smfish_count"``, but if the upstream CSV used
   a mixed-case channel prefix (e.g. ``W3Cy5_smfish_count``) the
   reassignment added a *new* lowercase column alongside the existing
   uppercase one — case-sensitive readers kept seeing the stale
   pre-apply counts, and the new ``load_well_csv`` case-fold rename
   hit a duplicate-column collision.

2. **Glob-based file discovery.** ``out_dir.glob(f"*_{well}.csv")``
   silently skipped wells whose per-well CSV was named ``<well>.csv``
   (no underscore prefix) or whose name had multiple matches. The
   writer now accepts the explicit ``well_to_csv`` map the app's
   loader populates, so it always hits the same files the rest of the
   app reads from.
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

from well_viewer.features.smfish.worker import (
    _drop_case_equivalent_columns,
    _resolve_csv_path,
    _write_counts_to_csvs,
)


def _write_csv(path: Path, df: pd.DataFrame) -> None:
    path.write_text(df.to_csv(index=False))


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def test_drop_case_equivalent_columns_collapses_mixed_case_dups():
    df = pd.DataFrame({
        "W3Cy5_smfish_count": ["1", "2", "3"],
        "other": ["a", "b", "c"],
    })
    out = _drop_case_equivalent_columns(df, "w3cy5_smfish_count")
    assert "W3Cy5_smfish_count" not in out.columns
    assert list(out.columns) == ["other"]


def test_drop_case_equivalent_columns_leaves_canonical_alone():
    df = pd.DataFrame({"w3cy5_smfish_count": ["1", "2"], "other": ["a", "b"]})
    out = _drop_case_equivalent_columns(df, "w3cy5_smfish_count")
    assert list(out.columns) == ["w3cy5_smfish_count", "other"]


def test_drop_case_equivalent_columns_noop_when_target_absent():
    df = pd.DataFrame({"other": ["a", "b"]})
    out = _drop_case_equivalent_columns(df, "w3cy5_smfish_count")
    assert list(out.columns) == ["other"]


def test_writer_overwrites_existing_mixed_case_column(tmp_path):
    """The bug-reproducer: upstream CSV has ``W3Cy5_smfish_count``; the
    writer must NOT leave that stale column on disk alongside a new
    lowercase one."""
    csv_path = tmp_path / "B02.csv"
    _write_csv(csv_path, pd.DataFrame({
        "well": ["B02", "B02"],
        "fov": ["1", "1"],
        "timepoint": ["24h", "24h"],
        "nucleus_id": ["1", "2"],
        "W3Cy5_smfish_count": ["999", "999"],  # pre-apply, will be replaced
    }))
    counts = {("B02", "1", "24h", "1"): 5, ("B02", "1", "24h", "2"): 7}

    _write_counts_to_csvs(
        out_dir=tmp_path,
        well_to_zip={"B02": Path("ignored")},
        counts=counts,
        column="w3cy5_smfish_count",
        well_to_csv={"B02": csv_path},
    )

    written = _read_csv(csv_path)
    # The stale uppercase column must be gone — both columns surviving
    # would let a case-sensitive reader keep showing pre-apply numbers.
    assert "W3Cy5_smfish_count" not in written.columns
    assert "w3cy5_smfish_count" in written.columns
    assert list(written["w3cy5_smfish_count"]) == ["5", "7"]


def test_writer_uses_well_to_csv_map_over_glob(tmp_path):
    """Datasets whose CSVs are named ``<well>.csv`` (no underscore
    prefix) used to be silently skipped by the legacy glob."""
    csv_path = tmp_path / "B02.csv"  # no prefix; legacy glob "*_B02.csv" would miss this
    _write_csv(csv_path, pd.DataFrame({
        "well": ["B02"],
        "fov": ["1"],
        "timepoint": ["24h"],
        "nucleus_id": ["1"],
        "w3cy5_smfish_count": ["999"],
    }))
    counts = {("B02", "1", "24h", "1"): 42}

    _write_counts_to_csvs(
        out_dir=tmp_path,
        well_to_zip={"B02": Path("ignored")},
        counts=counts,
        column="w3cy5_smfish_count",
        well_to_csv={"B02": csv_path},
    )

    written = _read_csv(csv_path)
    assert list(written["w3cy5_smfish_count"]) == ["42"]


def test_writer_skipped_well_does_not_corrupt_other_csvs(tmp_path):
    csv_path = tmp_path / "B02.csv"
    _write_csv(csv_path, pd.DataFrame({
        "well": ["B02"],
        "fov": ["1"],
        "timepoint": ["24h"],
        "nucleus_id": ["1"],
        "w3cy5_smfish_count": ["100"],
    }))
    # well_to_zip mentions C03 but the well_to_csv map omits it (the well
    # has no CSV loaded). The writer must skip C03 silently and still
    # write B02 correctly.
    counts = {("B02", "1", "24h", "1"): 7, ("C03", "1", "24h", "1"): 9}

    _write_counts_to_csvs(
        out_dir=tmp_path,
        well_to_zip={"B02": Path("ignored"), "C03": Path("ignored")},
        counts=counts,
        column="w3cy5_smfish_count",
        well_to_csv={"B02": csv_path},
    )

    written = _read_csv(csv_path)
    assert list(written["w3cy5_smfish_count"]) == ["7"]
    assert not (tmp_path / "C03.csv").exists()


def test_writer_zero_count_cells_get_zero_not_missing(tmp_path):
    """Cells with no detected spots (i.e. absent from the counts dict)
    must be written as '0', not left blank or carrying the old value."""
    csv_path = tmp_path / "B02.csv"
    _write_csv(csv_path, pd.DataFrame({
        "well": ["B02", "B02"],
        "fov": ["1", "1"],
        "timepoint": ["24h", "24h"],
        "nucleus_id": ["1", "2"],
        "w3cy5_smfish_count": ["100", "200"],  # both will be replaced
    }))
    counts = {("B02", "1", "24h", "1"): 5}  # cell 2 has no spots above the new threshold

    _write_counts_to_csvs(
        out_dir=tmp_path,
        well_to_zip={"B02": Path("ignored")},
        counts=counts,
        column="w3cy5_smfish_count",
        well_to_csv={"B02": csv_path},
    )

    written = _read_csv(csv_path)
    assert list(written["w3cy5_smfish_count"]) == ["5", "0"]


def test_resolve_csv_path_uses_map_when_provided(tmp_path):
    csv = tmp_path / "B02.csv"
    csv.write_text("dummy")
    out = _resolve_csv_path("B02", {"B02": csv}, tmp_path)
    assert out == csv


def test_resolve_csv_path_returns_none_when_well_unknown(tmp_path):
    out = _resolve_csv_path("Z99", {"B02": tmp_path / "x.csv"}, tmp_path)
    assert out is None


def test_resolve_csv_path_glob_fallback_no_match(tmp_path):
    # No map → glob fallback. No matching files in tmp_path → None.
    out = _resolve_csv_path("B02", None, tmp_path)
    assert out is None


def test_resolve_csv_path_glob_fallback_single_match(tmp_path):
    path = tmp_path / "experiment_B02.csv"
    path.write_text("dummy")
    out = _resolve_csv_path("B02", None, tmp_path)
    assert out == path
