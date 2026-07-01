"""Regression tests for ``well_viewer.features.smfish.worker._write_counts_to_csvs``.

Pins four writer bugs that made Apply Global Threshold appear to do nothing:

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

3. **Empty-FOV ⇄ single-FOV-extractor key drift.** ``load_well_csv``
   normalises an empty / ``"-1"`` ``fov`` column to ``"1"`` so the rest
   of the app and the smFISH single-FOV extractor (which synthesises
   FOV=``"1"``) agree.

4. **Cross-axis normalisation via `CellKey`.** Producer (``_process_well``)
   and consumer (``_write_counts_to_csvs``) both build keys via
   ``image_identity.CellKey`` so well/fov/tp/nid normalisation is
   guaranteed to match — empty-fov, ``"-1"``, letter-prefixed tp like
   ``"T01"``, padding like ``"001"``, and ``.0`` float artefacts on nid
   all collapse identically on both sides. Test counts dicts mirror
   what ``_process_well`` actually produces (post-``CellKey``).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from well_viewer.data.image_identity import normalize_tp
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
    counts = {
        ("B02", "1", normalize_tp("24h"), "1"): 5,
        ("B02", "1", normalize_tp("24h"), "2"): 7,
    }

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
    counts = {("B02", "1", normalize_tp("24h"), "1"): 42}

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
    counts = {
        ("B02", "1", normalize_tp("24h"), "1"): 7,
        ("C03", "1", normalize_tp("24h"), "1"): 9,
    }

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
    counts = {("B02", "1", normalize_tp("24h"), "1"): 5}  # cell 2 has no spots above the new threshold

    _write_counts_to_csvs(
        out_dir=tmp_path,
        well_to_zip={"B02": Path("ignored")},
        counts=counts,
        column="w3cy5_smfish_count",
        well_to_csv={"B02": csv_path},
    )

    written = _read_csv(csv_path)
    assert list(written["w3cy5_smfish_count"]) == ["5", "0"]


def test_writer_matches_single_fov_dataset(tmp_path):
    """Reproduces the user's reported bug. Single-FOV pipeline writes a
    blank ``fov`` column. Both the producer (``_process_well``) and the
    consumer (``_write_counts_to_csvs``) now build keys via ``CellKey``
    so ``fov=""`` on the CSV side and the parser's synthesised ``fov="1"``
    collapse to the same key, and ``tp="T01"`` normalises identically on
    both sides (``normalize_tp("T01") == "1"``)."""
    csv_path = tmp_path / "A02.csv"
    _write_csv(csv_path, pd.DataFrame({
        "well": ["A02"],
        "fov": [""],          # blank — this is the smoking gun
        "timepoint": ["T01"],
        "nucleus_id": ["1"],
        "w2turq_smfish_count": ["0"],
    }))
    counts = {("A02", "1", normalize_tp("T01"), "1"): 42}

    _write_counts_to_csvs(
        out_dir=tmp_path,
        well_to_zip={"A02": Path("ignored")},
        counts=counts,
        column="w2turq_smfish_count",
        well_to_csv={"A02": csv_path},
    )

    written = _read_csv(csv_path)
    assert list(written["w2turq_smfish_count"]) == ["42"]


def test_writer_normalises_fov_minus_one_to_one(tmp_path):
    """``"-1"`` is the other sentinel load_well_csv folds to ``"1"``.
    The writer must do the same so a CSV that survived an older
    pipeline still matches the single-FOV-extractor key."""
    csv_path = tmp_path / "B02.csv"
    _write_csv(csv_path, pd.DataFrame({
        "well": ["B02"],
        "fov": ["-1"],
        "timepoint": ["T02"],
        "nucleus_id": ["3"],
        "w3cy5_smfish_count": ["999"],
    }))
    counts = {("B02", "1", normalize_tp("T02"), "3"): 7}

    _write_counts_to_csvs(
        out_dir=tmp_path,
        well_to_zip={"B02": Path("ignored")},
        counts=counts,
        column="w3cy5_smfish_count",
        well_to_csv={"B02": csv_path},
    )

    assert list(_read_csv(csv_path)["w3cy5_smfish_count"]) == ["7"]


def test_writer_letter_prefixed_tp_normalises_uniformly(tmp_path):
    """Letter-prefixed timepoints (``T01``/``T05``) collapse via
    ``normalize_tp`` to ``"1"``/``"5"`` on both producer and consumer
    sides — the unified module's canonical form."""
    csv_path = tmp_path / "A02.csv"
    _write_csv(csv_path, pd.DataFrame({
        "well": ["A02", "A02"],
        "fov": ["1", "1"],
        "timepoint": ["T01", "T05"],
        "nucleus_id": ["1", "1"],
        "w2turq_smfish_count": ["0", "0"],
    }))
    counts = {
        ("A02", "1", normalize_tp("T01"), "1"): 12,
        ("A02", "1", normalize_tp("T05"), "1"): 34,
    }

    _write_counts_to_csvs(
        out_dir=tmp_path,
        well_to_zip={"A02": Path("ignored")},
        counts=counts,
        column="w2turq_smfish_count",
        well_to_csv={"A02": csv_path},
    )

    assert list(_read_csv(csv_path)["w2turq_smfish_count"]) == ["12", "34"]


def test_writer_logs_zero_match_diagnostic(tmp_path, caplog):
    """When every lookup misses, the writer must emit a WARN naming
    the (matched, total) and sample keys from both sides so the next
    drift mode reports itself instead of silently writing zeros."""
    csv_path = tmp_path / "A02.csv"
    _write_csv(csv_path, pd.DataFrame({
        "well": ["A02"],
        "fov": ["2"],        # deliberately not "1" — provokes a miss
        "timepoint": ["T01"],
        "nucleus_id": ["1"],
        "w2turq_smfish_count": ["0"],
    }))
    # Counts key uses the canonical normalised form (matches what the
    # production worker now produces via CellKey). The deliberate fov="2"
    # in the CSV provokes the miss.
    counts = {("A02", "1", normalize_tp("T01"), "1"): 99}

    caplog.set_level(logging.WARNING, logger="smfish_tab")
    _write_counts_to_csvs(
        out_dir=tmp_path,
        well_to_zip={"A02": Path("ignored")},
        counts=counts,
        column="w2turq_smfish_count",
        well_to_csv={"A02": csv_path},
    )

    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "wrote 0 matched rows" in m
        and "w2turq_smfish_count" in m
        and f"('A02', '1', '{normalize_tp('T01')}', '1')" in m  # counts-side sample
        for m in msgs
    )
    # The column is still written (preserves the file mtime semantics);
    # every cell gets the miss value "0".
    assert list(_read_csv(csv_path)["w2turq_smfish_count"]) == ["0"]


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


def test_writer_skips_locked_well_and_continues(tmp_path, caplog, monkeypatch):
    """A user-reported Windows traceback: ``atomic_write_text`` raised
    ``PermissionError`` mid Apply-Global-Threshold because Excel /
    AV / cloud-sync held a handle on one well's CSV. Previously the
    error bubbled up the worker thread and killed the entire run,
    leaving subsequent wells unwritten. Now: the failing well is
    skipped with a WARN naming it, and the remaining wells are still
    written."""
    import logging
    from unittest.mock import patch
    from well_viewer.persistence import _io as _atomic_io

    # Two wells. Lock the first; the second must still receive its update.
    locked_csv = tmp_path / "A02.csv"
    _write_csv(locked_csv, pd.DataFrame({
        "well": ["A02"], "fov": ["1"], "timepoint": ["T01"],
        "nucleus_id": ["1"], "w3cy5_smfish_count": ["0"],
    }))
    ok_csv = tmp_path / "B02.csv"
    _write_csv(ok_csv, pd.DataFrame({
        "well": ["B02"], "fov": ["1"], "timepoint": ["T01"],
        "nucleus_id": ["1"], "w3cy5_smfish_count": ["0"],
    }))
    counts = {
        ("A02", "1", normalize_tp("T01"), "1"): 42,
        ("B02", "1", normalize_tp("T01"), "1"): 99,
    }

    real_replace = __import__("os").replace

    def selective_replace(src, dst):
        if str(dst).endswith("A02.csv"):
            raise PermissionError("[WinError 5] Access is denied")
        return real_replace(src, dst)

    # Make the retry shield exhaust immediately so the test doesn't
    # actually spend ~3s on the timing budget.
    monkeypatch.setattr(_atomic_io, "_RETRY_BASE_DELAY_S", 0.0)
    monkeypatch.setattr(_atomic_io, "_RETRY_ATTEMPTS", 2)
    caplog.set_level(logging.WARNING, logger="smfish_tab")
    with patch("os.replace", side_effect=selective_replace):
        _write_counts_to_csvs(
            out_dir=tmp_path,
            well_to_zip={"A02": Path("ignored"), "B02": Path("ignored")},
            counts=counts,
            column="w3cy5_smfish_count",
            well_to_csv={"A02": locked_csv, "B02": ok_csv},
        )

    msgs = [rec.getMessage() for rec in caplog.records]
    assert any("could not write" in m and "A02.csv" in m for m in msgs), (
        "expected a per-well WARN naming A02.csv as locked"
    )
    # B02 was NOT locked → its update went through despite A02 failing.
    assert list(_read_csv(ok_csv)["w3cy5_smfish_count"]) == ["99"]
    # A02 still has its pre-apply zero — the locked write was skipped,
    # not silently replaced with stale content.
    assert list(_read_csv(locked_csv)["w3cy5_smfish_count"]) == ["0"]
