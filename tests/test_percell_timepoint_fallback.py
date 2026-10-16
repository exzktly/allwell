"""Violin and beeswarm read the timepoint the same way.

``timepoint_hours`` is the parsed column and is present on anything the current
pipeline wrote, but older CSVs — and any row whose parse failed — carry only
the text ``timepoint``. ``draw_beeswarm`` fell back to parsing that text;
``draw_violin`` required ``timepoint_hours`` outright and drew nothing without
it. Measured on a frame carrying only ``timepoint`` = "6h": the beeswarm put
two artists on the mean panel, the violin put none. Same well, same data, one
toggle apart, with nothing on the empty panel saying why.
"""

from __future__ import annotations

import types

import pytest

pytest.importorskip("matplotlib")
pytest.importorskip("scipy")

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from review.features.barplot import renderer  # noqa: E402

_VAL = "Nuclear_Mean"


def _ctx():
    return types.SimpleNamespace(state=types.SimpleNamespace(
        channel=types.SimpleNamespace(
            ratio_index={}, active_val_col=_VAL,
            active_metric="mean_intensity", active_channel="gfp"),
        plot=types.SimpleNamespace(show_threshold_line=False, time_scale=1.0),
    ))


def _frame(*, parsed_column: bool) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    cols = {"timepoint": ["6h"] * 60,
            _VAL: rng.normal(5.0, 1.0, 60),
            "Included": [1] * 60}
    if parsed_column:
        cols["timepoint_hours"] = [6.0] * 60
    return pd.DataFrame(cols)


def _mean_panel_artists(monkeypatch, draw_name, *, parsed_column: bool) -> int:
    from review.features.barplot import _pooled

    # Both renderers take a column's cells from ``_pooled.column_cells``, which
    # reads each well narrowly (or whole); either way it is this frame.
    frame = lambda *_a, **_k: _frame(parsed_column=parsed_column)  # noqa: E731
    monkeypatch.setattr(_pooled, "narrow_rows", frame)
    monkeypatch.setattr(_pooled, "get_rows", frame)
    monkeypatch.setattr(_pooled, "cells_upper_bound", lambda _c, _w: 0)
    fig, (ax_mean, ax_frac) = plt.subplots(2, 1)
    try:
        kw = {"bw": 0.5} if draw_name == "draw_violin" else {}
        getattr(renderer, draw_name)(
            _ctx(), ax_mean, ax_frac, wells=["A01"], colors=["#4488cc"],
            xlabels=["A01"], target_t=6.0, tp_str="6", threshold=0.0, **kw)
        return len(ax_mean.collections) + len(ax_mean.lines)
    finally:
        plt.close(fig)


@pytest.mark.parametrize("draw_name", ["draw_violin", "draw_beeswarm"])
def test_a_text_only_timepoint_still_draws(monkeypatch, draw_name):
    assert _mean_panel_artists(monkeypatch, draw_name, parsed_column=False) > 0, (
        f"{draw_name} drew nothing for a frame carrying only the text "
        "timepoint column, while its sibling drew the distribution"
    )


@pytest.mark.parametrize("draw_name", ["draw_violin", "draw_beeswarm"])
def test_the_parsed_column_is_still_the_one_that_is_read(monkeypatch, draw_name):
    assert _mean_panel_artists(monkeypatch, draw_name, parsed_column=True) > 0


def test_the_two_renderers_share_one_timepoint_reader():
    """The guard: two copies of this is how they came to disagree.

    Both renderers now take a column's cells from ``_pooled.column_cells``,
    whose one per-well filter (``_well_values``) reads the timepoint through
    ``_timepoint_hours``; neither renderer reads a row itself any more.
    """
    import ast
    import inspect
    import textwrap

    from review.features.barplot import _pooled

    def calls(fn, name):
        src = textwrap.dedent(inspect.getsource(fn))
        return [n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name]

    for name in ("draw_violin", "draw_beeswarm"):
        src = inspect.getsource(getattr(renderer, name))
        assert calls(getattr(renderer, name), "column_cells"), f"{name} filters its cells itself again"
        assert "timepoint_hours" not in src.replace("_timepoint_hours", ""), (
            f"{name} names the parsed column directly"
        )
    assert calls(_pooled._well_values, "_timepoint_hours"), "the shared filter reads the timepoint itself"
