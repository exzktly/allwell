"""``load_well_csv`` types the read instead of reading everything as text.

A plate of 537,393 cells × 37 columns died on load with

    Unable to allocate 152 MiB for an array with shape (37, 537393)
    and data type object

`(n_columns, n_rows)` object is what pandas allocates when a whole file is read
with ``dtype=str``: one consolidated block for every column at once, filled with
a Python string per cell — including for the 31 columns that are numbers and
were about to be converted back. ``load_well_csv``'s own docstring carries the
peak-RSS figures for the two read shapes, and ARCHITECTURE.md repeats them;
they are not restated a third time here. Chunking the old read was tried first
and measured **worse** (1780 MiB), because the coerced chunks stay alive until
the concat.

So the risk this file exists to hold is not the memory — it is that typing the
read quietly changes a *value*. Every case below is a differential against the
old implementation, reproduced inline, over a corpus carrying the mess a real
measurements CSV carries: blanks in numeric columns, a stray non-numeric cell, a
well whose token is the literal text ``NA``, upper-case column names, ``-1`` and
empty FOVs, and a missing ``Included``.
"""
from __future__ import annotations

import pandas as pd
import pytest

from review.data.data_loading import _STRING_COLS, load_well_csv


def _old_load(path):
    """The pre-change body, verbatim enough to compare against.

    Kept here rather than imported because the point is to compare the new
    result to what the old code produced, and the old code is gone.
    """
    from review.data.data_loading import (
        _METRIC_SUFFIXES, normalize_fov_series,
    )
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if df.empty:
        return df

    def _should_lower(col: str) -> bool:
        lc = str(col).strip().lower()
        if lc in _STRING_COLS:
            return True
        return any(lc.endswith(suf) for suf in _METRIC_SUFFIXES)

    rename = {col: str(col).strip().lower() for col in df.columns
              if _should_lower(col) and col != str(col).strip().lower()}
    if rename:
        df = df.rename(columns=rename)
    if "fov" in df.columns:
        df["fov"] = normalize_fov_series(df["fov"])
    if "Included" not in df.columns:
        df["Included"] = 1
    for col in df.columns:
        if str(col).strip().lower() in _STRING_COLS:
            continue
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


#: One row per line. Deliberately messy — a corpus of well-formed numbers would
#: validate the happy path and tell us nothing about the coercion.
MESSY = (
    "filename,experiment,channel,well,FOV,timepoint,"
    "GFP_mean_intensity,shape_area,area_px,nucleus_id\n"
    "a.tif,exp,GFP,A01,1,0h,1.5,120,150,1\n"
    "b.tif,exp,GFP,NA,-1,0h,,121,151,2\n"          # literal NA well; -1 fov; blank
    "c.tif,exp,GFP,B01,,0h,junk,122,152,3\n"       # empty fov; junk in a number
    "d.tif,exp,GFP,B01,2,24h,4.5,,153,4\n"         # blank in another number
)

CASES = {
    "messy": MESSY,
    # Every numeric column blank: the old path made all-NaN float64 columns.
    "all-blank-numerics": (
        "filename,well,fov,GFP_mean_intensity,area_px\n"
        "a.tif,A01,1,,\n"
        "b.tif,A01,1,,\n"
    ),
    # No Included column — the loader adds it.
    "no-included": (
        "filename,well,fov,timepoint,area_px\n"
        "a.tif,A01,1,0h,150\n"
    ),
    # Upper-case names that the rename lowers, on both kinds of column.
    "upper-case-names": (
        "Filename,Well,FOV,Timepoint,W3Cy5_smfish_count,GFP_Mean_Intensity\n"
        "a.tif,A01,1,0h,7,1.5\n"
    ),
    # Integers throughout: the dtype must not drift from int64 to float64.
    "pure-integers": (
        "filename,well,fov,timepoint,area_px,nucleus_id\n"
        "a.tif,A01,1,0h,150,1\n"
        "b.tif,A01,1,0h,151,2\n"
    ),
    # Header only. The early return has to survive the extra header read.
    "header-only": "filename,well,fov,timepoint,area_px\n",
}


def _text_as_str(df):
    """Text columns back to plain strings, for comparison only.

    The loader stores the repeating text columns as categories — see
    ``tests/test_text_columns_are_categories.py``, which pins that separately
    along with the three ways it could have changed an answer. This file is
    about the *typed read*, so it compares the values and lets that dtype
    difference through rather than re-asserting the old storage.
    """
    out = df.copy()
    for col in out.columns:
        if isinstance(out[col].dtype, pd.CategoricalDtype):
            out[col] = out[col].astype(str)
    return out


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_typed_read_returns_what_the_string_read_returned(tmp_path, name):
    path = tmp_path / f"measurements_{name}_A01.csv"
    path.write_text(CASES[name])
    pd.testing.assert_frame_equal(
        _text_as_str(load_well_csv(path)), _text_as_str(_old_load(path)))


def test_a_well_literally_named_NA_is_still_text(tmp_path):
    """The case the NA handling turns on.

    ``keep_default_na=False`` is why a well token of ``NA`` survives as text
    today; the typed read keeps it by pairing that flag with
    ``na_values=[""]`` rather than letting pandas' default NA list back in. Get
    this wrong and a plate with an NA-named group loads with a blank well
    column and every one of its rows unreachable.
    """
    path = tmp_path / "measurements_A01.csv"
    path.write_text("filename,well,fov,timepoint,area_px\na.tif,NA,1,0h,150\n")
    df = load_well_csv(path)
    assert df["well"].tolist() == ["NA"]


def test_a_blank_in_a_text_column_stays_empty_not_nan(tmp_path):
    """``na_values=[""]`` turns blanks into NaN in *every* column; the text ones
    are filled back. A NaN leaking through here would print as ``nan`` in a
    well label and compare unequal to ``""`` everywhere downstream."""
    path = tmp_path / "measurements_A01.csv"
    path.write_text("filename,well,fov,timepoint,channel,area_px\n"
                    "a.tif,A01,1,0h,,150\n")
    df = load_well_csv(path)
    assert df["channel"].tolist() == [""]
    assert not df["channel"].isna().any()


# ── the mechanism, asserted directly ─────────────────────────────────────────

def test_no_numeric_column_is_ever_materialised_as_text(tmp_path):
    """The memory fix, stated as a property rather than as a byte count.

    A peak-RSS assertion would be flaky and machine-dependent. What is stable
    is the block layout: after the load, the only object-dtype columns are the
    text ones. If a numeric column comes back object, it was built as Python
    strings and the allocation this change exists to avoid is back.
    """
    path = tmp_path / "measurements_A01.csv"
    path.write_text(MESSY)
    df = load_well_csv(path)
    object_cols = {c for c in df.columns if df[c].dtype == object}
    assert object_cols <= _STRING_COLS, (
        f"{object_cols - _STRING_COLS} came back as text; a numeric column read "
        "as strings is the allocation this change removes"
    )


def test_the_file_is_not_read_twice_in_full(tmp_path, monkeypatch):
    """The header read must stay a header read.

    ``_string_column_dtypes`` opens the file a second time. That is cheap only
    while it is bounded to the header — ``nrows=0``. Reading the body twice
    would trade one large allocation for two.
    """
    path = tmp_path / "measurements_A01.csv"
    path.write_text(MESSY)

    seen: list = []
    real = pd.read_csv

    def spy(*args, **kwargs):
        seen.append(kwargs.get("nrows"))
        return real(*args, **kwargs)

    monkeypatch.setattr(pd, "read_csv", spy)
    load_well_csv(path)
    assert seen == [0, None], (
        f"expected a header read then one body read, got nrows={seen}")
