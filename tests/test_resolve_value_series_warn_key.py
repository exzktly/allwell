"""Audit S7 — the missing-column warn-dedup key must not be ``id(df)``.

``id()`` is reused after a DataFrame is garbage-collected, so keying the
warn-set by it could suppress a legitimately-new warning (or grow the set
unbounded). The fix keys by (column, schema fingerprint): the same warning is
deduped across distinct DataFrame *objects* of the same schema, but a genuinely
different schema still warns.
"""
from __future__ import annotations

import logging

import pandas as pd

from well_viewer.data.data_loading import _missing_col_warned, resolve_value_series


def _missing_warnings(caplog):
    return [r for r in caplog.records if "not found" in r.getMessage()]


def test_dedup_across_distinct_df_objects_same_schema(caplog):
    _missing_col_warned.clear()
    df1 = pd.DataFrame({"a": [1, 2]})
    df2 = pd.DataFrame({"a": [3, 4]})   # same columns, different object

    with caplog.at_level(logging.WARNING, logger="well_viewer.data.data_loading"):
        r1 = resolve_value_series(df1, "missing_col")
        r2 = resolve_value_series(df2, "missing_col")

    assert r1.isna().all() and r2.isna().all()
    # Deduped even though the two frames are distinct objects → not keyed by id.
    assert len(_missing_warnings(caplog)) == 1


def test_distinct_schemas_each_warn(caplog):
    _missing_col_warned.clear()
    df1 = pd.DataFrame({"a": [1]})
    df2 = pd.DataFrame({"b": [1]})       # different schema, both missing "z"

    with caplog.at_level(logging.WARNING, logger="well_viewer.data.data_loading"):
        resolve_value_series(df1, "z")
        resolve_value_series(df2, "z")

    assert len(_missing_warnings(caplog)) == 2
