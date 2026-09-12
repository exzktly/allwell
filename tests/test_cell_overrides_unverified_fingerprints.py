"""Regression tests: an override the fingerprint check could not verify must
never be indistinguishable from one it verified.

Schema v2 stamps each override with the ``<well>_out.zip`` mtime so a pipeline
re-run drops overrides that would otherwise re-bind to freshly numbered nuclei.
Two rows escape that check entirely:

* a row whose well has no ``<well>_out.zip`` in the data directory — there is
  nothing to compare the stored fingerprint against, and the comparison
  ``current_fp and stored_fp != current_fp`` simply fell through;
* a row with an empty ``seg_fp`` inside a **v2** file — written when the zip was
  missing or unreadable at save time. The compensating warning was gated on
  ``file_version < 2``, so exactly the file that could carry such rows was the
  file that never warned about them.

Both rows are still loaded (dropping curation because a zip moved would be an
unrecoverable loss); what these tests pin is that the shortfall is stated.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from review.persistence.cell_overrides import load_overrides


def _write(data_dir: Path, overrides: list[dict], *, version: int = 2) -> None:
    (data_dir / "cell_overrides.json").write_text(
        json.dumps({"version": version, "overrides": overrides})
    )


def test_v2_row_without_a_fingerprint_warns(tmp_path, caplog):
    _write(tmp_path, [
        {"well": "C03", "fov": "1", "tp": "0", "nucleus_id": "3",
         "included": "0", "seg_fp": ""},
    ])

    with caplog.at_level(logging.WARNING, logger="review"):
        loaded = load_overrides(tmp_path)

    assert ("C03", "1", "0", "3") in loaded, "the row must still load"
    unfingerprinted = [
        r.getMessage() for r in caplog.records
        if "no segmentation fingerprint" in r.getMessage()
    ]
    assert unfingerprinted, (
        "a v2 file carrying a row with an empty seg_fp warned about nothing; "
        f"records were {[r.getMessage() for r in caplog.records]}"
    )


def test_stored_fingerprint_with_no_zip_on_disk_is_reported(tmp_path, caplog):
    # A01's zip is present and matches; B02's is absent, so B02's stored
    # fingerprint can be checked against nothing.
    (tmp_path / "A01_out.zip").write_bytes(b"x")
    a01_fp = f"{(tmp_path / 'A01_out.zip').stat().st_mtime_ns}"
    _write(tmp_path, [
        {"well": "A01", "fov": "1", "tp": "0", "nucleus_id": "7",
         "included": "0", "seg_fp": a01_fp},
        {"well": "B02", "fov": "1", "tp": "0", "nucleus_id": "9",
         "included": "0", "seg_fp": "999999"},
    ])

    with caplog.at_level(logging.WARNING, logger="review"):
        loaded = load_overrides(tmp_path)

    assert ("A01", "1", "0", "7") in loaded
    assert ("B02", "1", "0", "9") in loaded, "an unverifiable row is kept"
    unchecked = [
        r.getMessage() for r in caplog.records
        if "could not be checked" in r.getMessage()
    ]
    assert unchecked, (
        "the row whose well has no _out.zip passed the staleness guard "
        "silently; records were "
        f"{[r.getMessage() for r in caplog.records]}"
    )
    assert "1 override(s)" in unchecked[0], unchecked[0]


def test_verified_row_produces_no_unchecked_warning(tmp_path, caplog):
    (tmp_path / "A01_out.zip").write_bytes(b"x")
    fp = f"{(tmp_path / 'A01_out.zip').stat().st_mtime_ns}"
    _write(tmp_path, [
        {"well": "A01", "fov": "1", "tp": "0", "nucleus_id": "7",
         "included": "0", "seg_fp": fp},
    ])

    with caplog.at_level(logging.WARNING, logger="review"):
        loaded = load_overrides(tmp_path)

    assert ("A01", "1", "0", "7") in loaded
    assert not [
        r for r in caplog.records
        if "could not be checked" in r.getMessage()
        or "no segmentation fingerprint" in r.getMessage()
    ], "a fully verified file must warn about nothing"
