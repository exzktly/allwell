"""Every timepoint picker offers the same list.

Reported: the Timepoints dropdown on Response Curves "only shows 0".

There are two questions about a dataset's timepoints and they have different
answers. ``data_loading.get_all_timepoints`` answers *which timepoints did the
included rows measure*, by reading ``timepoint_hours`` and skipping any well
whose CSV has no such column — right for a collector deciding what it can
reduce. ``DatasetState.rebuild_timepoints`` answers *which timepoints does this
dataset have*, merging three sources — the numeric ``timepoint_hours``, the
parsed text ``timepoint`` strings, and ``pipeline_info``'s own
``available_timepoints`` — into ``all_timepoints_cache``, which its docstring
says exists so that every tab reading it agrees.

A picker wants the second. Response Curves asked the first, so on a plate
recording its timepoints either of the other two ways it enumerated nothing:
``sync_timepoint_menu`` set ``tp_selections`` to ``{}``, the button read
``Timepoints: 0 selected`` and the menu was empty, while Bar, Scatter, Cell
Browser, Fold change, Batch Export and the Segmentation scope all read the
plate correctly off the cache.

``offerable_timepoints`` is that second question named once. The guard at the
foot is the half that matters going forward: a picker's enumeration written out
at each call site is one call site away from disagreeing, and Response was that
call site.
"""

from __future__ import annotations

import ast

import pandas as pd
import pytest

from tests._repo_scan import parse_source, python_sources, rel


def _dataset(df, info=None):
    from well_viewer.core.dataset_state import DatasetState

    ds = DatasetState()
    ds.well_paths = {"A01": "/A01.csv"}
    ds.cache = {"A01": df}
    ds.pipeline_info = info or {}
    ds.rebuild_timepoints()
    return ds


def _ctx(ds, monkeypatch, *, rows=None):
    from types import SimpleNamespace

    import well_viewer.data.data_loading as dl

    # The row scan is the fallback, so it is stubbed explicitly rather than
    # left to walk a cache this test did not build: a fallback that happened to
    # return the right answer would make the cache read untestable.
    monkeypatch.setattr(dl, "get_rows", lambda _c, label: rows)
    return SimpleNamespace(state=SimpleNamespace(dataset=ds))


_BASE = {"well": "A01", "included": True}


@pytest.mark.parametrize("df, info, expected", [
    # The shape the narrow reader handles, where both answers agree.
    (pd.DataFrame([{**_BASE, "timepoint_hours": 0.0},
                   {**_BASE, "timepoint_hours": 24.0}]), {}, [0.0, 24.0]),
    # A text timepoint column and no numeric one. The reported plate's shape:
    # the narrow reader returns nothing at all here.
    (pd.DataFrame([{**_BASE, "timepoint": "T0"},
                   {**_BASE, "timepoint": "T24"}]), {}, [0.0, 24.0]),
    # Timepoints the pipeline recorded but the rows do not carry.
    (pd.DataFrame([dict(_BASE)]), {"available_timepoints": ["0", "24", "48"]},
     [0.0, 24.0, 48.0]),
])
def test_a_picker_offers_every_timepoint_the_dataset_holds(df, info, expected,
                                                           monkeypatch):
    """All three of the sources the canonical list merges, one per case.

    The expected values are spelled out rather than compared against
    ``all_timepoints_cache``: asserting the two agree would pass just as well
    if both were empty, which is the shape the bug had.
    """
    from well_viewer.data.data_loading import offerable_timepoints

    ds = _dataset(df, info)
    assert offerable_timepoints(_ctx(ds, monkeypatch, rows=df)) == expected


def test_the_row_scan_is_the_fallback_when_the_cache_is_empty(monkeypatch):
    """A ctx built before the cache was rebuilt still gets an answer."""
    from well_viewer.data.data_loading import offerable_timepoints

    df = pd.DataFrame([{**_BASE, "timepoint_hours": 12.0}])
    ds = _dataset(df)
    ds.all_timepoints_cache = []
    assert offerable_timepoints(_ctx(ds, monkeypatch, rows=df)) == [12.0]


def test_the_two_readers_really_do_disagree(monkeypatch):
    """Without this the tests above prove nothing.

    If the narrow reader answered these plates too, ``offerable_timepoints``
    would be a rename rather than a fix and every case above would pass against
    the code that shipped the bug.
    """
    from well_viewer.data.data_loading import get_all_timepoints

    df = pd.DataFrame([{**_BASE, "timepoint": "T0"},
                       {**_BASE, "timepoint": "T24"}])
    ds = _dataset(df)
    assert ds.all_timepoints_cache == [0.0, 24.0]
    assert get_all_timepoints(_ctx(ds, monkeypatch, rows=df)) == [], (
        "the narrow reader now sees a text timepoint column, so the picker "
        "sweep this file guards is no longer measuring anything")


# ── the sweep ────────────────────────────────────────────────────────────────

#: ``get_all_timepoints`` answers the collector's question and stays available
#: to the data layer that owns it. A **feature** asking it is a picker asking
#: the wrong question — that is the whole defect — so the roster of entitled
#: callers is "nothing under features/".
_PICKER_ANSWER = "offerable_timepoints"
_COLLECTOR_ANSWER = "get_all_timepoints"


def test_no_feature_enumerates_timepoints_with_the_collectors_reader():
    offenders = []
    for path in python_sources():
        posix = rel(path)
        if not posix.startswith("well_viewer/features/"):
            continue
        for node in ast.walk(parse_source(path)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == _COLLECTOR_ANSWER:
                    offenders.append(f"{posix}:{node.lineno}")
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name == _COLLECTOR_ANSWER:
                        offenders.append(f"{posix}:{node.lineno} (import)")
    assert not offenders, (
        f"`{_COLLECTOR_ANSWER}` answers *which timepoints did the rows "
        f"measure*, from `timepoint_hours` alone. A tab filling a picker is "
        f"asking *which timepoints does this dataset have*, whose one answer "
        f"is `data_loading.{_PICKER_ANSWER}` — the cache "
        f"`DatasetState.rebuild_timepoints` merges from three sources. Reading "
        f"the narrow one is what left Response Curves offering none:\n  "
        + "\n  ".join(offenders))


def test_the_picker_answer_prefers_the_canonical_cache():
    """The rule the guard above is worth having, pinned at the source.

    Read from the function rather than asserted behaviourally so a rewrite that
    silently drops the cache read — leaving every picker back on the row scan,
    with every test above still green on a ctx whose fallback agrees — fails
    here.
    """
    import inspect

    from well_viewer.data import data_loading

    source = inspect.getsource(data_loading.offerable_timepoints)
    assert "all_timepoints_cache" in source, (
        "offerable_timepoints no longer reads the canonical cache, so every "
        "picker in the app is back on the collector's narrow row scan")
