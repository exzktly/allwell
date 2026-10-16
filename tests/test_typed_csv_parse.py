"""The well CSV is parsed by ``pyarrow.csv``, typed, and gives pandas' frame (tracker F1).

``data_loading._read_well_table`` reads with pyarrow when it can and pandas when
it cannot. It was adopted on the condition that it yields the same frame as the
pandas parse, or that every difference is measured and stated. These tests hold
both halves:

* every file of a corpus carrying the mess real CSVs carry gives the frame the
  pandas parse gives — with pandas asked to round floats correctly
  (``float_precision="round_trip"``), because its default parser does not;
* the one numeric difference from the default pandas parse is stated as a value:
  pyarrow's float is exactly ``float(text)``, and pandas' default is not.

Every equality test also checks that pyarrow did the read. A fallback to pandas
would pass every equality trivially, so without that check the file would hold
nothing about the reader it exists for.
"""

from __future__ import annotations

import logging
import sys

import numpy as np
import pandas as pd
import pytest

from review.data import data_loading as dl

pytest.importorskip("pyarrow.csv")

_REAL_READ_CSV = pd.read_csv

_LATE_STRAY = ("well,fov,timepoint,v,s\n"
               + "".join(f"A01,1,0h,{i}.5,x\n" for i in range(60_000))
               + "A01,1,0h,abc,x\n")

#: name -> (file text, whether pyarrow reads it rather than falling back)
CORPUS = {
    "messy": (
        'Well,FOV,timepoint,filename,nucleus_id,GFP_mean_intensity,area_px,channel,experiment,note_col\n'
        'A01,01,0h,"a,b.tif",1,10.5,100,DAPI,exp,NA\n'
        'A01,-1,0h,x.tif,2,,200,DAPI,exp,\n'
        'A01,,NA,x.tif,3,abc,,DAPI,exp,null\n'
        'A01,2,24h,y.tif,4,1e3,300.0,DAPI,exp,NaN\n'
        'A01,2,24h,y.tif,5,-0.0,9007199254740993,DAPI,exp,7\n', True),
    "na_text_in_numbers": ("well,fov,timepoint,v,w\nA01,1,0h,NA,nan\nA01,1,0h,1.5,NaN\nA01,1,0h,N/A,-inf\n", False),
    "na_text_without_infinity": ("well,fov,timepoint,v,w\nA01,1,0h,NA,nan\nA01,1,0h,1.5,NaN\nA01,1,0h,N/A,2\n", True),
    "timestamps": ("well,fov,timepoint,acquired,d\nA01,1,0h,2024-01-01 10:00:00,2024-01-01\n"
                   "A01,1,0h,2024-01-02 11:00:00,2024-01-03\n", True),
    "clock_times": ("well,fov,timepoint,v\nA01,1,0h,10:00:00\nA01,1,0h,11:00:00\n", True),
    "stray_text_after_60k_rows": (_LATE_STRAY, True),
    "spaces": ("well,fov,timepoint,v,t\nA01, 1 ,0h, 2.5 , x \nA01,2,0h,3, y\n", True),
    "bools": ("well,fov,timepoint,b,c\nA01,1,0h,TRUE,1\nA01,1,0h,FALSE,0\n", True),
    "bool_with_blank": ("well,fov,timepoint,b\nA01,1,0h,TRUE\nA01,1,0h,\n", True),
    "quoted_numbers": ('well,fov,timepoint,v\nA01,1,0h,"1.5"\nA01,1,0h,"2"\n', True),
    "all_blank_columns": ("well,fov,timepoint,v,channel\nA01,1,0h,,\nA01,1,0h,,\n", True),
    "repeated_column_name": ("well,fov,timepoint,v,v\nA01,1,0h,1,2\n", True),
    "unnamed_column": ("well,,timepoint,v\nA01,1,0h,1\n", True),
    "trailing_comma": ("well,fov,timepoint,v,\nA01,1,0h,1,\n", True),
    "bom_and_crlf": ("﻿well,fov,timepoint,v\r\nA01,1,0h,1.25\r\n", True),
    "infinities_and_plus": ("well,fov,timepoint,v\nA01,1,0h,inf\nA01,1,0h,-Infinity\nA01,1,0h,+3\n", False),
    "header_only": ("well,fov,timepoint,gfp_mean_intensity\n", True),
    "short_row": ("well,fov,timepoint,v,w\nA01,1,0h,1\nA01,1,0h,1,2\n", False),
    # Review findings: pyarrow's own boolean spellings include 1/0, and it reads
    # an over-large literal as inf where pandas reads text that becomes NaN.
    "true_and_one": ("well,fov,timepoint,v\nA01,1,0h,True\nA01,1,0h,1\n", True),
    "true_and_zero": ("well,fov,timepoint,v\nA01,1,0h,True\nA01,1,0h,0\n", True),
    "overflowing_literal": ("well,fov,timepoint,v\nA01,1,0h,1e400\nA01,1,0h,1\n", False),
    "negative_overflow": ("well,fov,timepoint,v\nA01,1,0h,-1e400\nA01,1,0h,1\n", False),
}


def _write(tmp_path, name: str, text: str):
    path = tmp_path / f"{name}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _arrow(path):
    """What pyarrow reads of *path*, or ``None`` where it falls back."""
    names = [str(c) for c in _REAL_READ_CSV(path, nrows=0).columns]
    return dl._arrow_read(path, names, dl._string_column_dtypes(names))


def _pandas_frame(path, monkeypatch, **read_kwargs):
    """``load_well_csv`` with pyarrow switched off, *read_kwargs* added to pandas' read."""
    with monkeypatch.context() as m:
        m.setattr(dl, "_arrow_read", lambda *a, **k: None)
        m.setattr(pd, "read_csv", lambda *a, **k: _REAL_READ_CSV(*a, **{**k, **read_kwargs}))
        return dl.load_well_csv(path)


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_pyarrow_gives_the_pandas_frame(tmp_path, monkeypatch, name):
    text, arrow_reads = CORPUS[name]
    path = _write(tmp_path, name, text)
    taken = _arrow(path) is not None
    assert taken is arrow_reads, (
        f"pyarrow {'fell back on' if arrow_reads else 'read'} {name}; "
        "an equality with the pandas frame measures nothing about pyarrow when pandas did the read")
    # pyarrow's reads are held to pandas' correctly-rounded parse (the stated
    # float difference); a file it hands to pandas is held to pandas' own default
    # parse, which is what the app then runs.
    expected = (_pandas_frame(path, monkeypatch, float_precision="round_trip") if arrow_reads
                else _pandas_frame(path, monkeypatch))
    pd.testing.assert_frame_equal(dl.load_well_csv(path), expected, check_exact=True)


def test_field_labels_stay_text(tmp_path):
    """The defect that declined ``engine="pyarrow"`` (tracker M8): ``01`` read as ``1.0``."""
    path = _write(tmp_path, "labels", "well,fov,timepoint,v\nA01,01,0h,1\nA01,-1,24h,2\nA01,2,048,3\n")
    raw = _arrow(path)
    assert raw["fov"].tolist() == ["01", "-1", "2"]
    assert raw["timepoint"].tolist() == ["0h", "24h", "048"]


def test_a_float_is_the_correctly_rounded_double_of_its_text(tmp_path, monkeypatch):
    """The stated difference from pandas' default parse, as values.

    pandas' default C parser does not round correctly — badly so for small
    magnitudes, so the corpus includes values in [0, 1e-3). pyarrow gives
    ``float(text)`` for each; the bound on pandas' error is the one the reader's
    docstring states.
    """
    texts = ["0.1234567890123456789", "3.0000000000000004", "123456.78901234567",
             "0.30000000000000004", "2.2250738585072014e-308", "99999999999999999999"]
    rng = np.random.default_rng(7)
    texts += [repr(float(x)) for x in np.concatenate([rng.random(2000) * 1e4, rng.random(2000) * 1e-3])]
    path = _write(tmp_path, "floats", "well,fov,timepoint,v\n"
                  + "".join(f"A01,1,0h,{t}\n" for t in texts))
    got = dl.load_well_csv(path)["v"].tolist()
    assert got == [float(t) for t in texts]
    default = _pandas_frame(path, monkeypatch)["v"].to_numpy()
    wrong = default != np.array([float(t) for t in texts])
    assert wrong.any(), "pandas' default parse got every value right; the stated difference is not shown here"
    exact = np.array(got)[wrong]
    relative = np.abs(default[wrong] - exact) / np.abs(exact)
    assert relative.max() <= 1e-12, "pandas' default parse is further off than the docstring states"


def test_an_integer_above_int64_becomes_a_float(tmp_path, monkeypatch):
    """The other stated difference: pandas keeps ``uint64``, pyarrow gives the nearest double."""
    path = _write(tmp_path, "u64", "well,fov,timepoint,v\nA01,1,0h,18446744073709551615\nA01,1,0h,1\n")
    assert dl.load_well_csv(path)["v"].tolist() == [18446744073709551615.0, 1.0]
    assert str(_pandas_frame(path, monkeypatch)["v"].dtype) == "uint64"


def test_a_file_pyarrow_cannot_read_is_parsed_by_pandas_and_says_so(tmp_path, monkeypatch, caplog):
    path = _write(tmp_path, "short", CORPUS["short_row"][0])
    with caplog.at_level(logging.INFO, logger="review"):
        df = dl.load_well_csv(path)
    assert df["w"].isna().tolist() == [True, False]
    assert any("parsing it with pandas" in r.getMessage() and "short.csv" in r.getMessage()
               for r in caplog.records)


def test_an_empty_file_raises_as_it_always_did(tmp_path):
    path = _write(tmp_path, "empty", "")
    with pytest.raises(pd.errors.EmptyDataError):
        dl.load_well_csv(path)


def test_without_pyarrow_the_pandas_parse_is_used(tmp_path, monkeypatch):
    path = _write(tmp_path, "messy", CORPUS["messy"][0])
    monkeypatch.setitem(sys.modules, "pyarrow.csv", None)
    assert _arrow(path) is None
    pd.testing.assert_frame_equal(dl.load_well_csv(path), _pandas_frame(path, monkeypatch), check_exact=True)


def test_running_out_of_memory_is_raised_not_fallen_back_from(tmp_path, monkeypatch):
    """A pandas parse needs more memory, not less; the caller's retry must see it."""
    import pyarrow as pa
    import pyarrow.csv as pcsv

    path = _write(tmp_path, "messy", CORPUS["messy"][0])

    def out_of_memory(*_a, **_k):
        raise pa.ArrowMemoryError("malloc of size 512 failed")

    monkeypatch.setattr(pcsv, "read_csv", out_of_memory)
    with pytest.raises(MemoryError):
        dl.load_well_csv(path)


def test_a_hexadecimal_literal_is_the_stated_difference(tmp_path, monkeypatch):
    path = _write(tmp_path, "hex", "well,fov,timepoint,v\nA01,1,0h,0x10\nA01,1,0h,1\n")
    assert dl.load_well_csv(path)["v"].tolist() == [16, 1]
    assert np.isnan(_pandas_frame(path, monkeypatch)["v"].tolist()[0])
