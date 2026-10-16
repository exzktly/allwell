"""Per-well CSVs are gzipped when a well finishes, and every reader accepts both.

Covers the shared helper (``util.csv_gz``), the pipeline's write/merge/resume
path, the fetched-partial merge, the remote-side compression script run for
real under bash, and the Review loader's entry points. Fixtures include the mix
a real dataset has: some wells gzipped, some plain, and a well with both.
"""

from __future__ import annotations

import csv
import gzip
import shutil
from pathlib import Path

import pytest

from util.csv_gz import (
    csv_stem, existing_csv, glob_csvs, gzip_csv, gzip_csvs, is_csv_name,
    open_csv_text,
)

HEADER = ["well", "fov", "timepoint", "nucleus_id", "dapi_mean_intensity"]


def _rows(fov: str, n: int = 2) -> list[dict]:
    return [{"well": "A1", "fov": fov, "timepoint": "0", "nucleus_id": str(i),
             "dapi_mean_intensity": str(10.0 + i)} for i in range(n)]


def _write_plain(path: Path, rows: list[dict]) -> Path:
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)
    return path


def _read_any(path: Path) -> list[dict]:
    with open_csv_text(path) as fh:
        return list(csv.DictReader(fh))


# ── util.csv_gz ─────────────────────────────────────────────────────────────

def test_names_and_stems():
    assert is_csv_name("m_A1.csv") and is_csv_name("m_A1.csv.gz")
    assert not is_csv_name("m_A1.csv.gz.part") and not is_csv_name("m_A1.zip")
    assert csv_stem("m_A1.csv") == "m_A1"
    assert csv_stem("m_A1.csv.gz") == "m_A1"        # Path.stem would say "m_A1.csv"


def test_gzip_csv_round_trips_and_removes_the_original(tmp_path):
    src = _write_plain(tmp_path / "m_A1.csv", _rows("1"))
    out = gzip_csv(src)
    assert out == tmp_path / "m_A1.csv.gz" and out.is_file() and not src.exists()
    assert gzip.open(out, "rb").read().startswith(b"well,fov")
    assert _read_any(out) == _rows("1")
    assert [p.name for p in tmp_path.iterdir()] == ["m_A1.csv.gz"]   # no .part left


def test_plain_wins_over_gzip_when_both_exist(tmp_path):
    _write_plain(tmp_path / "m_A1.csv", _rows("NEW"))
    gz = tmp_path / "m_A1.csv.gz"
    with gzip.open(gz, "wt", newline="") as fh:
        fh.write("well,fov\nA1,OLD\n")
    assert existing_csv(tmp_path / "m_A1.csv") == tmp_path / "m_A1.csv"
    assert existing_csv(gz) == tmp_path / "m_A1.csv"      # either spelling asked
    assert glob_csvs(tmp_path, "m_*") == [tmp_path / "m_A1.csv"]


def test_glob_mixed_directory_lists_each_logical_file_once(tmp_path):
    _write_plain(tmp_path / "m_A1.csv", _rows("1"))
    gzip_csv(_write_plain(tmp_path / "m_B1.csv", _rows("1")))
    (tmp_path / ".m_C1.csv.gz.part1").write_bytes(b"x")      # a partial: not a CSV
    (tmp_path / "m_D1.csv.gz.part").write_bytes(b"x")
    assert [p.name for p in glob_csvs(tmp_path)] == ["m_A1.csv", "m_B1.csv.gz"]
    assert [p.name for p in glob_csvs(tmp_path, "*_B1")] == ["m_B1.csv.gz"]


def test_gzip_csvs_compresses_only_plain_ones(tmp_path):
    _write_plain(tmp_path / "m_A1.csv", _rows("1"))
    gzip_csv(_write_plain(tmp_path / "m_B1.csv", _rows("2")))
    assert gzip_csvs(tmp_path) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["m_A1.csv.gz", "m_B1.csv.gz"]


def test_open_csv_text_writes_gzip_for_a_gz_name(tmp_path):
    p = tmp_path / "out.csv.gz"
    with open_csv_text(p, "w") as fh:
        csv.writer(fh).writerows([["a", "b"], ["1", "2"]])
    assert gzip.open(p, "rb").read() == b"a,b\r\n1,2\r\n"


# ── pipeline: write, merge, resume ──────────────────────────────────────────

def test_a_finished_well_is_written_gzipped_only(tmp_path):
    from pipeline.io import write_well_csv
    out = write_well_csv(_rows("1") + _rows("2"), tmp_path / "measurements_A1.csv")
    assert out.name == "measurements_A1.csv.gz"
    assert [p.name for p in tmp_path.iterdir()] == ["measurements_A1.csv.gz"]
    assert len(_read_any(out)) == 4


def test_incremental_merge_reads_gz_dedupes_and_recompresses(tmp_path):
    from pipeline.io import merge_well_records, write_well_csv
    target = tmp_path / "measurements_A1.csv"
    write_well_csv(_rows("1"), target)
    added = merge_well_records(target, _rows("1") + _rows("2"))     # fov 1 is a duplicate
    assert added == 2
    assert [p.name for p in tmp_path.iterdir()] == ["measurements_A1.csv.gz"]
    assert {r["fov"] for r in _read_any(tmp_path / "measurements_A1.csv.gz")} == {"1", "2"}


def test_merge_into_a_legacy_plain_csv_ends_up_gzipped(tmp_path):
    from pipeline.io import merge_well_records
    target = _write_plain(tmp_path / "measurements_A1.csv", _rows("1"))
    assert merge_well_records(target, _rows("2")) == 2
    assert [p.name for p in tmp_path.iterdir()] == ["measurements_A1.csv.gz"]
    assert len(_read_any(tmp_path / "measurements_A1.csv.gz")) == 4


def test_done_keys_read_either_spelling(tmp_path):
    from pipeline.incremental_keys import read_done_fov_keys
    _write_plain(tmp_path / "m_A1.csv", _rows("1"))
    gzip_csv(_write_plain(tmp_path / "m_B1.csv", _rows("7")))
    assert read_done_fov_keys(tmp_path / "m_A1.csv") == {("1", "0")}
    assert read_done_fov_keys(tmp_path / "m_B1.csv") == {("7", "0")}      # asked by plain name
    assert read_done_fov_keys(tmp_path / "m_B1.csv.gz") == {("7", "0")}


def test_a_truncated_gz_is_unreadable_not_a_crash(tmp_path):
    from pipeline.completeness import csv_keys_and_state
    from pipeline.incremental_keys import read_done_fov_keys
    gz = gzip_csv(_write_plain(tmp_path / "m_A1.csv", _rows("1", 400)))
    gz.write_bytes(gz.read_bytes()[:-30])
    assert read_done_fov_keys(gz) == set()
    assert csv_keys_and_state(gz)[1] in ("unreadable", "empty")


def test_well_skip_and_completeness_see_a_gzipped_csv(tmp_path):
    from pipeline.completeness import csv_keys_and_state, wells_with_output
    from pipeline.well_skip import output_exists_for_well
    gzip_csv(_write_plain(tmp_path / "measurements_A1.csv", _rows("1")))
    _write_plain(tmp_path / "measurements_B2.csv", _rows("1"))
    (tmp_path / "A1_out.zip").write_bytes(b"PK")
    assert output_exists_for_well(tmp_path, "A1", "measurements") is True
    assert wells_with_output(tmp_path, "measurements") == {"A01", "B02"}
    keys, state = csv_keys_and_state(tmp_path / "measurements_A1.csv")
    assert (keys, state) == ({("1", "0")}, "ok")


def test_control_well_lookup_and_values_through_gz(tmp_path):
    from pipeline.threshold_optimize import find_well_csv, read_channel_values
    gz = gzip_csv(_write_plain(tmp_path / "measurements_A1.csv", _rows("1", 3)))
    assert find_well_csv(tmp_path, "measurements", "A1") == gz
    assert list(read_channel_values(gz, "dapi")) == [10.0, 11.0, 12.0]


def test_fetched_partials_merge_into_a_gz_target_and_recompress(tmp_path):
    from pipeline.io import merge_fetched_csvs, write_well_csv
    out, tmp = tmp_path / "out", tmp_path / "tmp"
    out.mkdir(), tmp.mkdir()
    write_well_csv(_rows("1"), out / "measurements_A1.csv")
    gzip_csv(_write_plain(tmp / "measurements_A1.csv", _rows("2")))     # arrives gzipped
    assert merge_fetched_csvs(tmp, out) == 2
    assert [p.name for p in out.iterdir()] == ["measurements_A1.csv.gz"]
    assert {r["fov"] for r in _read_any(out / "measurements_A1.csv.gz")} == {"1", "2"}


# ── Review / persistence ────────────────────────────────────────────────────

def test_atomic_write_text_to_a_gz_path_writes_gzip(tmp_path):
    from review.persistence._io import atomic_write_text
    p = tmp_path / "m_A1.csv.gz"
    atomic_write_text(p, "a,b\n1,2\n")
    assert gzip.open(p, "rt").read() == "a,b\n1,2\n"       # not plain text under a .gz name


def test_review_loader_reads_gz_and_plain_alike(tmp_path):
    from review.core.load_controller import _looks_like_well_measurement_csv
    from review.data.data_loading import load_well_csv
    rows = [{**r, "timepoint_hours": "0"} for r in _rows("1", 3)]
    header = HEADER + ["timepoint_hours"]
    plain = tmp_path / "m_A1.csv"
    with open(plain, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        w.writerows(rows)
    gz = tmp_path / "m_B1.csv"
    shutil.copy(plain, gz)
    gz = gzip_csv(gz)
    assert _looks_like_well_measurement_csv(plain) and _looks_like_well_measurement_csv(gz)
    a, b = load_well_csv(plain), load_well_csv(gz)
    assert len(a) == len(b) == 3 and list(a.columns) == list(b.columns)


def test_parquet_cache_names_and_keeps_a_gz_wells_cache(tmp_path):
    from review.data import parquet_cache as pc
    gz = gzip_csv(_write_plain(tmp_path / "m_A1.csv", _rows("1")))
    assert pc.cache_path(gz).name == "m_A1.parquet"            # not "m_A1.csv.parquet"
    cache_dir = tmp_path / pc.CACHE_DIRNAME
    cache_dir.mkdir()
    (cache_dir / "m_A1.parquet").write_bytes(b"x")
    (cache_dir / "m_Z9.parquet").write_bytes(b"x")             # its CSV is gone
    pc.prune(tmp_path)
    assert sorted(p.name for p in cache_dir.iterdir()) == ["m_A1.parquet"]


def test_dataset_paths_map_gz_csvs_to_wells(tmp_path):
    from types import SimpleNamespace
    from datasets.core.dataset_paths import out_csvs
    gz = gzip_csv(_write_plain(tmp_path / "measurements_A1.csv", _rows("1")))
    plain = _write_plain(tmp_path / "measurements_B2.csv", _rows("1"))
    got = out_csvs(SimpleNamespace(out_dir=tmp_path))
    assert got == {"A01": gz, "B02": plain}


# ── remote side ─────────────────────────────────────────────────────────────

class _LocalHost:
    """Runs the 'remote' script in a local ``bash -s``: ``ssh_argv`` is empty."""
    ssh_alias = "local"

    @staticmethod
    def ssh_argv(allocate_tty=False):
        return []


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("gzip") is None,
                    reason="needs bash and gzip")
def test_remote_compression_script_gzips_only_this_wells_plain_csvs(tmp_path):
    from services.remote_csv import compress_remote_csvs
    _write_plain(tmp_path / "measurements_A1.csv", _rows("NEW"))
    _write_plain(tmp_path / "measurements_B1.csv", _rows("1"))       # another well
    with gzip.open(tmp_path / "measurements_A1.csv.gz", "wt") as fh:   # stale archive
        fh.write("well,fov\nA1,OLD\n")
    (tmp_path / "A1_out.zip").write_bytes(b"PK")
    lines: list[str] = []
    compress_remote_csvs(_LocalHost(), str(tmp_path), "A1", lines.append)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["A1_out.zip", "measurements_A1.csv.gz", "measurements_B1.csv"], names
    assert [r["fov"] for r in _read_any(tmp_path / "measurements_A1.csv.gz")] == ["NEW", "NEW"]
    assert lines == []


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("gzip") is None,
                    reason="needs bash and gzip")
def test_remote_compression_with_nothing_plain_is_a_quiet_no_op(tmp_path):
    from services.remote_csv import compress_remote_csvs
    gzip_csv(_write_plain(tmp_path / "measurements_A1.csv", _rows("1")))
    lines: list[str] = []
    compress_remote_csvs(_LocalHost(), str(tmp_path), "A1", lines.append)
    assert [p.name for p in tmp_path.iterdir()] == ["measurements_A1.csv.gz"] and lines == []


def test_a_fetched_gz_retires_the_stale_plain_twin(tmp_path):
    from services.remote_csv import retire_superseded_csvs
    stale = _write_plain(tmp_path / "measurements_A1.csv", _rows("OLD"))
    fetched = tmp_path / "measurements_A1.csv.gz"
    fetched.write_bytes(gzip.compress(b"well,fov\nA1,NEW\n"))
    other = _write_plain(tmp_path / "measurements_B1.csv", _rows("keep"))
    retire_superseded_csvs(
        ["/r/out/measurements_A1.csv.gz", "/r/out/measurements_B1.csv.gz"], tmp_path)
    assert not stale.exists() and fetched.exists()
    assert other.exists()               # B1's gz never arrived: its plain stays
    assert existing_csv(tmp_path / "measurements_A1.csv") == fetched


# ── wiring ──────────────────────────────────────────────────────────────────

def test_the_pipeline_writes_well_csvs_only_through_the_compressing_helpers():
    """``process_microscopy`` must reach the per-well CSV through
    ``write_well_csv`` / ``merge_well_records`` (which gzip the result). Calling
    ``_write_single_csv`` or ``merge_records_into_csv`` directly would leave the
    well's CSV uncompressed, and nothing else would notice."""
    import ast

    from tests._repo_scan import parse_source

    root = Path(__file__).resolve().parent.parent
    tree = parse_source(root / "process_microscopy.py")
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert {"write_well_csv", "merge_well_records"} <= called
    assert not called & {"_write_single_csv", "merge_records_into_csv"}


# ── a corrupt (not merely truncated) archive ────────────────────────────────

def _corrupt_gz(tmp_path: Path, name: str = "m_A1.csv") -> Path:
    """A ``.csv.gz`` whose deflate stream is damaged mid-file: reading it raises
    ``zlib.error``, which is neither an ``OSError`` nor an ``EOFError``."""
    gz = gzip_csv(_write_plain(tmp_path / name, _rows("1", 20000)))
    raw = bytearray(gz.read_bytes())
    for i in range(300, 340):
        raw[i] ^= 0xFF
    gz.write_bytes(bytes(raw))
    with pytest.raises(Exception) as exc:
        gzip.open(gz, "rb").read()
    assert type(exc.value).__name__ == "error", type(exc.value)    # zlib.error, not OSError/EOFError
    return gz


def test_a_corrupt_gz_reads_as_unreadable_everywhere_it_is_read(tmp_path):
    from pipeline.completeness import csv_keys_and_state
    from pipeline.incremental_keys import read_done_fov_keys
    from pipeline.threshold_optimize import read_channel_values
    from review.core.load_controller import _looks_like_well_measurement_csv
    from datasets.validate.validate_compute import _csv_header, _read_csv_rows

    gz = _corrupt_gz(tmp_path)
    assert read_done_fov_keys(gz) == set()
    assert csv_keys_and_state(gz) == (set(), "unreadable")
    assert read_channel_values(gz, "dapi").size == 0
    assert _csv_header(gz) is None
    assert _read_csv_rows(gz)[2] == "unreadable"
    _looks_like_well_measurement_csv(gz)            # must not raise


def test_one_corrupt_fetched_partial_does_not_stop_the_wells_after_it(tmp_path):
    from pipeline.io import merge_fetched_csvs
    src, out = tmp_path / "transit", tmp_path / "out"
    src.mkdir(), out.mkdir()
    _corrupt_gz(src, "measurements_A01.csv")
    gzip_csv(_write_plain(src / "measurements_B02.csv", _rows("2")))
    log: list[str] = []
    assert merge_fetched_csvs(src, out, log_fn=log.append) == 2
    assert (out / "measurements_B02.csv.gz").is_file()
    assert "measurements_A01.csv.gz" in "".join(log)


def test_gzip_csv_of_an_already_gzipped_path_keeps_the_file(tmp_path):
    gz = gzip_csv(_write_plain(tmp_path / "m_A1.csv", _rows("1")))
    assert gzip_csv(gz) == gz and gz.is_file()
    assert _read_any(gz) == _rows("1")
