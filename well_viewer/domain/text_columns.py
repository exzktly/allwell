"""Text views of the six columns a loaded plate stores as categories.

``data_loading._categorize_text_columns`` stores ``filename``, ``experiment``,
``channel``, ``well``, ``fov`` and ``timepoint`` as ``pd.Categorical`` — the
single biggest memory saving available on a loaded plate, because those columns
are mostly repetition (a 537,393-row plate goes from 327 MiB to 135 MiB). The
type change is invisible to anything that only *reads* the values, and it is not
invisible at all to the handful of sites that normalise them.

``Series.fillna(value)`` **validates its fill value against the categories**
whether or not any value needs filling, so ``df["fov"].fillna("1")`` raises

    TypeError: Cannot setitem on a Categorical with a new category (1),
    set the categories first

on every plate whose FOV labels happen not to include the literal ``"1"`` —
fields numbered from zero, zero-padded labels like ``01``, a plate whose only
field is ``2``. It is a load-time crash, not a wrong number: the traceback comes
out of the Review pane before the dataset finishes opening.

That is a property of the *column type*, not of any one caller, so the
normalisation lives here and every caller goes through it:

* :func:`as_text` — a stripped string view of any series, categorical or not,
  with missing and blank values mapped onto *default*.
* :func:`text_column` — the same for a named column of a frame, returning a
  full-length series of *default* when the column is absent.
* :func:`fov_text` / :func:`timepoint_text` — the two conventional spellings,
  ``"1"`` for a missing field label (the single-FOV convention every consumer
  shares) and ``""`` for a missing timepoint.

``tests/test_text_column_access.py`` is the guard: it fails on any ``.fillna``
applied directly to one of these six columns, anywhere in the app.
"""

from __future__ import annotations

import pandas as pd

#: The columns ``load_well_csv`` reads as text and may store as categories.
#: One definition, imported by the loader that types them and by the guard
#: that watches how they are read.
TEXT_COLUMNS = frozenset({
    "filename", "experiment", "channel", "well", "fov", "timepoint",
})

#: A missing or blank FOV label reads as field ``"1"`` — the single-FOV
#: convention shared by ``normalize_fov``, the plot aggregators and the smFISH
#: writer, so rows without a field label all belong to the same image.
FOV_DEFAULT = "1"


def as_text(values, *, default: str = "") -> pd.Series:
    """*values* as stripped strings, with missing and blank cells as *default*.

    Accepts a categorical series, an object series, or anything ``pd.Series``
    accepts. The categorical is widened to ``object`` first: filling and
    masking a categorical validates every value written against the category
    list, and the whole point of this helper is that the caller does not have
    to know which values happen to be categories on this particular plate.
    """
    s = values if isinstance(values, pd.Series) else pd.Series(values)
    if isinstance(s.dtype, pd.CategoricalDtype):
        s = s.astype(object)
    # `.where(notna, default)` rather than `.fillna(default)`: identical on an
    # object column, and it is the fillna that raises on a categorical one.
    s = s.where(s.notna(), default).astype(str).str.strip()
    if default:
        s = s.mask(s == "", default)
    return s


def text_column(df, column: str, *, default: str = "") -> pd.Series:
    """:func:`as_text` of *df*'s *column*, or a full column of *default*.

    A frame that lacks the column reads as *default* on every row rather than
    raising, which is what the call sites want: an absent ``fov`` column means
    one unnamed field, not an error.
    """
    if df is None:
        return pd.Series([], dtype=object)
    if column in getattr(df, "columns", ()):
        return as_text(df[column], default=default)
    return pd.Series([default] * len(df), index=df.index, dtype=object)


def fov_text(df) -> pd.Series:
    """Per-row FOV label, blanks and missing values as ``"1"``."""
    return text_column(df, "fov", default=FOV_DEFAULT)


def timepoint_text(df) -> pd.Series:
    """Per-row timepoint string, blanks and missing values as ``""``."""
    return text_column(df, "timepoint")
