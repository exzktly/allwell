"""Cell Gating's first frame is found from the numeric timepoints without parsing every text label.

``gating_compute._cell_gating_first_frame_rows`` picks each well's first field at its first timepoint for the
Cell Gating CDFs, on the tab's first visit. It used to parse every cell's text timepoint (``"t00"``) as a number
whether or not ``timepoint_hours`` already answered, and that parse was 3.2 s of a 6.7 s first visit on a 60-well,
12-timepoint plate. The text is now read only when the numeric column is empty, and the answer is the same.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from review.features.home.cell_gating import gating_compute as gc


def _frame(hours):
    tps = ["t00", "t00", "t01", "t01", "t02", "t02"]
    return pd.DataFrame({"fov": ["1", "2"] * 3, "timepoint": tps, "timepoint_hours": hours,
                         "area_px": np.arange(6, dtype=float) + 10})


def test_numeric_timepoints_never_parse_the_text_labels(monkeypatch):
    def no_text(*_a, **_k):
        raise AssertionError("the text timepoints were read although timepoint_hours answers")

    monkeypatch.setattr(gc, "timepoint_text", no_text)
    frame, desc = gc._cell_gating_first_frame_rows(_frame([0.0, 0.0, 1.0, 1.0, 2.0, 2.0]))
    assert frame.index.tolist() == [0]
    assert desc == "fov=1, tp=0"


@pytest.mark.parametrize("labels, want_rows, want_tp", [
    (["t00", "t00", "t01", "t01", "t02", "t02"], [0], "t00"),          # text only: the first label
    (["24", "24", "0", "0", "48", "48"], [2], "0"),                    # numbers written as text: the smallest
])
def test_with_no_numeric_column_the_text_still_decides(labels, want_rows, want_tp):
    df = _frame([np.nan] * 6)
    df["timepoint"] = labels
    frame, desc = gc._cell_gating_first_frame_rows(df)
    assert frame.index.tolist() == want_rows
    assert desc == f"fov=1, tp={want_tp}"
