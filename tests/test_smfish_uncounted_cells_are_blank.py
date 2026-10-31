"""smFISH *Apply Global Threshold*: a cell that was never counted is written blank, never ``0``.

The counting pass looks only at the frames that have both an smFISH image and a mask in the well's archive and
whose bytes could be read. It used to record only nuclei with at least one hit, and the writer filled every other
row with ``0`` — so a cell in a field with no mask, or whose image could not be read, was written exactly like a
cell with no spots, and every fraction-positive built on the column counted it as negative (cell population audit
P5). Now the pass gives every nucleus of every frame it counted an entry, ``0`` included, and a cell without one is
left blank.

The fixture has two fields: field 1 is counted and holds a nucleus with no spot above threshold, field 2 has no mask
member, so its cell was never counted. The right answer and the old one differ in exactly that second row.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import tifffile

from domain.image_identity import normalize_tp
from review.features.smfish import worker


def _tif(array) -> bytes:
    buf = io.BytesIO()
    tifffile.imwrite(buf, np.asarray(array))
    return buf.getvalue()


def test_a_field_with_no_mask_is_left_blank_and_a_counted_zero_is_written_zero(tmp_path, monkeypatch):
    labels = np.array([[1, 1, 0], [0, 2, 2]], dtype=np.uint16)          # nuclei 1 and 2 in field 1
    spots = np.array([[9.0, 0.0, 0.0], [0.0, 0.0, 0.0]], dtype=np.float32)   # one hit, in nucleus 1
    members = {"sm1": _tif(spots), "mk1": _tif(labels), "sm2": _tif(spots)}
    ref = lambda name: SimpleNamespace(zip_path=tmp_path / "A01.zip", zip_member=name)   # noqa: E731
    monkeypatch.setattr(worker, "scan_well_zip", lambda **_kw: (
        {("1", "24h"): ref("sm1"), ("2", "24h"): ref("sm2")},          # smFISH in both fields
        {("1", "24h"): ref("mk1")}))                                    # a mask for field 1 only
    monkeypatch.setattr(worker, "read_member_bytes", lambda *, zip_path, member, logger: members[member])

    counts = worker._process_well(well="A01", zip_path=tmp_path / "A01.zip", channel="w3cy5", threshold=5.0,
                                  classifier=None, fov_tp_extractor=None)
    tp = normalize_tp("24h")
    assert counts == {("A01", "1", tp, "1"): 1, ("A01", "1", tp, "2"): 0}

    csv_path = tmp_path / "A01.csv"
    pd.DataFrame({"well": ["A01"] * 3, "fov": ["1", "1", "2"], "timepoint": ["24h"] * 3,
                  "nucleus_id": ["1", "2", "1"], "w3cy5_smfish_count": ["7", "7", "7"]}).to_csv(csv_path, index=False)
    tally: dict = {}
    written = worker._write_counts_to_csvs(Path(tmp_path), {"A01": tmp_path / "A01.zip"}, counts,
                                           "w3cy5_smfish_count", {"A01": csv_path}, tally=tally)
    column = pd.read_csv(csv_path, dtype=str, keep_default_na=False)["w3cy5_smfish_count"].tolist()
    assert written == 1
    assert column == ["1", "0", ""]                  # counted 1, counted 0, never counted
    assert tally == {"uncounted": 1}
