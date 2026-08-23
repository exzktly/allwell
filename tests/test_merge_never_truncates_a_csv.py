"""An unreadable per-well CSV fails the well; it never becomes an empty one.

`merge_records_into_csv` caught `OSError` while reading the existing target and
treated it as "there was no prior CSV". It then wrote — atomically, so there was
no partial file to notice — a file holding only the new rows, and returned their
count, which the caller logs as ``+N row(s), merged``.

Nothing downstream could see the loss. `done_keys` unions the CSV with the
well's ``_out.zip``, which still held every image, so `run_check` went on
reporting the well **complete**; the rows were unrecoverable short of
``--force``. The realistic trigger is one transient ``EIO`` on an SMB or NFS
output volume during an incremental run.

The same function already failed loudly on a *corrupt* CSV — `UnicodeDecodeError`
and `csv.Error` were never caught — so the two failure modes disagreed about
the same file.

Both local call sites sit inside `process_well_zip_task` /
`process_well_folder_task`, whose `except BaseException` dumps a traceback to
``<output_dir>/errors/`` and fails that well alone. The remote merge loop has
its own handler, because there the recoverable state is to leave the existing
CSV untouched and keep fetching.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from pipeline.io import merge_records_into_csv

FIELDS = ("well", "fov", "timepoint", "nucleus_id", "gfp_mean_intensity")


def _rows(tp: str, n: int) -> list[dict]:
    return [
        {"well": "A01", "fov": "F001", "timepoint": tp,
         "nucleus_id": str(i), "gfp_mean_intensity": str(100 + i)}
        for i in range(1, n + 1)
    ]


def _write(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(FIELDS))
        w.writeheader()
        w.writerows(rows)


def test_a_target_that_cannot_be_read_raises_instead_of_replacing_it(
        tmp_path, monkeypatch):
    """The whole point: existing-and-unreadable is not the same as absent.

    The failure is modelled by making the read of *this* file raise `EIO`,
    rather than by `chmod`: the suite runs as root in CI and in the dev
    container, where a mode of 0o000 does not stop a read at all — the test
    would then pass against the unfixed code and prove nothing.
    """
    import builtins
    import errno

    csv_path = tmp_path / "measurements_A01.csv"
    _write(csv_path, _rows("T01", 9))
    before = csv_path.read_text()

    real_open = builtins.open

    def _open(file, *a, **kw):
        if Path(file) == csv_path and (not a or "r" in str(a[0])):
            raise OSError(errno.EIO, "Input/output error", str(file))
        return real_open(file, *a, **kw)

    monkeypatch.setattr(builtins, "open", _open)
    with pytest.raises(OSError):
        merge_records_into_csv(csv_path, _rows("T02", 1))
    monkeypatch.undo()

    after = csv_path.read_text()
    assert after == before, (
        "an unreadable target was rewritten with only the new rows. It should "
        "have been left alone and the well failed. Rows before "
        f"{len(before.splitlines())}, after {len(after.splitlines())}")


def test_a_readable_target_still_merges(tmp_path):
    """The refusal must not have cost the ordinary path."""
    csv_path = tmp_path / "measurements_A01.csv"
    _write(csv_path, _rows("T01", 9))

    added = merge_records_into_csv(csv_path, _rows("T02", 3))

    assert added == 3, added
    with open(csv_path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 12, (
        f"the merge lost rows: {len(rows)} of an expected 12")
    assert {r["timepoint"] for r in rows} == {"T01", "T02"}


def test_an_absent_target_is_still_a_plain_fresh_write(tmp_path):
    """Absent genuinely is absent — that branch was never the defect."""
    csv_path = tmp_path / "measurements_B02.csv"

    added = merge_records_into_csv(csv_path, _rows("T01", 4))

    assert added == 4
    with open(csv_path, newline="") as fh:
        assert len(list(csv.DictReader(fh))) == 4


def test_a_duplicate_key_is_still_dropped(tmp_path):
    """Idempotence across a re-run of the same FOV is what the merge is for."""
    csv_path = tmp_path / "measurements_A01.csv"
    _write(csv_path, _rows("T01", 5))

    assert merge_records_into_csv(csv_path, _rows("T01", 5)) == 0
    with open(csv_path, newline="") as fh:
        assert len(list(csv.DictReader(fh))) == 5


def test_one_undecodable_partial_does_not_stop_the_wells_after_it(tmp_path):
    """The docstring's "a failure is per file" has to cover every failure.

    A CSV refuses to be read in three ways: an `OSError`, a `UnicodeDecodeError`
    when its bytes are not UTF-8, and a `csv.Error` when its structure is
    malformed. Catching only the first let either of the others abort the whole
    loop, so one damaged partial stopped every well after it from merging —
    silently, since the exception escaped into the fetch's own handler rather
    than the per-file warning this function writes.
    """
    from pipeline.io import merge_fetched_csvs

    src, target = tmp_path / "transit", tmp_path / "out"
    src.mkdir()
    target.mkdir()
    (src / "measurements_A01.csv").write_bytes(b"well,fov\n\xff\xfe,F1\n")
    (src / "measurements_B02.csv").write_text("well,fov\nB02,F1\n")

    log: list[str] = []
    added = merge_fetched_csvs(src, target, log_fn=log.append)

    assert added == 1
    assert (target / "measurements_B02.csv").is_file(), (
        "B02 never merged because A01's bytes were not decodable")
    assert "measurements_A01.csv" in "".join(log), (
        "the damaged partial was skipped without saying so")
