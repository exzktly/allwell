"""Review opens a dataset whose per-well CSVs are gzipped, plain, or both.

Drives the real ``ReviewApp`` load (``load_directory``), which is where the CSV
glob, the header sniff, the well-key derivation and the Parquet cache name all
meet — the unit tests of each cannot see them disagree. Full-app test: one app
construction, run alone.
"""

from __future__ import annotations

from util.csv_gz import glob_csvs, gzip_csv


def _snapshot(app) -> dict:
    ds = app._app_state.dataset
    return {well: len(ds.cache[well]) for well in sorted(ds.well_paths)}


def test_a_mixed_gz_and_plain_dataset_loads_the_same_wells_and_rows(viewer_app, mini_dataset):
    viewer_app._load_path(mini_dataset)
    plain = _snapshot(viewer_app)
    assert plain, "the mini dataset loaded no wells"

    csvs = glob_csvs(mini_dataset)
    assert len(csvs) >= 2
    for csv_path in csvs[::2]:                 # every other well: a real mix
        gzip_csv(csv_path)
    names = sorted(p.name for p in glob_csvs(mini_dataset))
    assert any(n.endswith(".csv.gz") for n in names) and any(n.endswith(".csv") for n in names)

    viewer_app._load_path(mini_dataset)
    assert _snapshot(viewer_app) == plain
    keys = set(viewer_app._app_state.dataset.well_paths)
    assert not any(k.endswith(".csv") for k in keys), f"a well key kept '.csv': {keys}"
