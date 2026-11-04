"""The nanowell focus filter's site-table read: pyarrow, and frame-for-frame what pandas' read was.

``review.data.nanowell_focus_gate._read_site_table`` runs on every well at every open of a nanowell dataset, so it
reads with ``pyarrow.csv`` rather than pandas (about a quarter of the time). Each file here is read both ways and
the frames must be equal in values, dtypes and column order — and pyarrow must actually have done the read, since a
fallback to pandas would pass every equality trivially. The corpus carries the mess a real table can: blank and
``NA``-spelled text, timepoints that look like numbers, a byte-order mark, CRLF line endings, quoting, a column of
blanks, and columns in another order.
"""

from __future__ import annotations

import pandas as pd
import pytest

pytest.importorskip("pyarrow")

from review.data import nanowell_focus_gate as gate  # noqa: E402
from review.domain.nanowell_rim_focus import SITE_COLUMNS  # noqa: E402

HEADER = "timepoint,nw_q,nw_r,nw_ring,owner,state,focus,GFP_integrated\n"

CORPUS = {
    "plain": HEADER + "0h,0,0,0,F0,measured,1.5,3\n0h,1,0,1,F0,empty,0.9,\n",
    "unaddressed rows": HEADER + "0h,,,,F0,unaddressed,,\n24h,1,0,1,F0,measured,2,1\n",
    "numeric-looking timepoints": HEADER + "0,0,0,0,F0,measured,1,1\n24.0,1,0,1,F0,measured,2,1\n",
    "blank and NA text": HEADER + "NA,0,0,0,F0,measured,1,1\n,1,0,1,F0,,2,1\nnan,2,0,2,F0,NA,3,1\n",
    "no focus column": "timepoint,nw_q,nw_r,state\n0h,0,0,measured\n",
    "a focus column of blanks": HEADER + "0h,0,0,0,F0,measured,,1\n0h,1,0,1,F0,measured,,1\n",
    "CRLF": (HEADER + "0h,0,0,0,F0,measured,1.5,3\n").replace("\n", "\r\n"),
    "byte-order mark": "\ufeff" + HEADER + "0h,0,0,0,F0,measured,1.5,3\n",
    "quoted": HEADER + '"0h",0,0,0,"F0","measured",1.5,3\n"",1,0,1,F0,"",2,1\n',
    "columns reordered": "focus,state,nw_r,timepoint,nw_q\n1.0,measured,0,0h,0\n",
    "negative coordinates": HEADER + "0h,-3,2,3,F0,measured,1e-3,1\n",
    "fractional coordinates": HEADER + "0h,1.0,2.0,3,F0,measured,1,1\n",
    "text in a number column": HEADER + "0h,0,0,0,F0,measured,bad,3\n",
}


def _pandas(path):
    return pd.read_csv(path, usecols=lambda c: c in SITE_COLUMNS, dtype={"timepoint": str, "state": str})


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_pyarrow_reads_what_pandas_read(tmp_path, name):
    path = tmp_path / "A01.csv"
    path.write_bytes(CORPUS[name].encode("utf-8"))
    got = gate._arrow_site_table(str(path))
    assert got is not None, f"pyarrow declined {name!r}; the equality below would then measure nothing"
    pd.testing.assert_frame_equal(got, _pandas(path))


def test_a_file_pyarrow_refuses_is_read_by_pandas(tmp_path):
    path = tmp_path / "A01.csv"
    path.write_text(HEADER + "0h,0,0,0,F0,measured,1.5,3,ONE TOO MANY\n")
    assert gate._arrow_site_table(str(path)) is None
    stat = path.stat()
    read = gate._read_site_table(str(path), stat.st_mtime_ns, stat.st_size)
    pd.testing.assert_frame_equal(read, _pandas(path))


def test_a_table_with_no_rows_is_read_by_pandas(tmp_path):
    """pandas types an empty table's columns as text; matching that in pyarrow buys nothing, so pandas reads it."""
    path = tmp_path / "A01.csv"
    path.write_text(HEADER)
    assert gate._arrow_site_table(str(path)) is None
    stat = path.stat()
    pd.testing.assert_frame_equal(gate._read_site_table(str(path), stat.st_mtime_ns, stat.st_size), _pandas(path))


def test_the_cached_read_goes_through_pyarrow(tmp_path, monkeypatch):
    """The read the filter uses takes the pyarrow path, rather than the fast reader existing beside it unused."""
    path = tmp_path / "A01.csv"
    path.write_text(CORPUS["plain"])

    def no_pandas(*_a, **_k):
        raise AssertionError("the site table was parsed by pandas")

    monkeypatch.setattr(gate.pd, "read_csv", no_pandas)
    stat = path.stat()
    gate._read_site_table.cache_clear()
    frame = gate._read_site_table(str(path), stat.st_mtime_ns, stat.st_size)
    assert list(frame.columns) == ["timepoint", "nw_q", "nw_r", "state", "focus"]
    assert frame["timepoint"].tolist() == ["0h", "0h"]
