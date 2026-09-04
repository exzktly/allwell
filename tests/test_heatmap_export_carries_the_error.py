"""The Heat Map's CSV carries the same ``±`` the grid drew, blank where undefined.

``export_heatmap_data`` gained an ``err_SD`` / ``err_SEM`` column, and the
promise attached to it is the one this repository keeps having to re-learn: an
**undefined** spread is written as an empty cell, never as ``0``. A
single-replicate set exported ``err_SD = 0.000000`` once before
(``stats_compute._reduce_reps`` carries the paragraph about it), and a reader
cannot tell that from replicates that agreed exactly.

Nothing tested the column. An adversarial pass changed the export's ``e = ""``
to ``e = 0.0`` — writing ``0.000000`` for exactly the undefined spread the code
comment forbids — and forty tests stayed green. This file is what notices.

It also pins that the figure and the file agree: the numbers come from
``ctx.runtime.heatmap.spread``, stamped by the same redraw that stamped
``.array``, rather than from a second reduction that could drift.
"""

from __future__ import annotations

import csv
import types

import numpy as np
import pytest

pytest.importorskip("PySide6")

from well_viewer.features.export import service as EX
from well_viewer.features.heatmap.models import HeatmapLayout


@pytest.fixture
def exported(tmp_path, monkeypatch):
    """Run ``export_heatmap_data`` over a stamped runtime and read the rows back."""

    def _run(*, array, spread, spread_label="SEM"):
        layout = HeatmapLayout(name="Row", rows=1, cols=3)
        layout.assign(0, 0, ["A01", "A02"])
        layout.assign(0, 1, ["B01"])
        # (0, 2) deliberately empty — a cell with no wells and no value.
        out = tmp_path / "heatmap.csv"
        ctx = types.SimpleNamespace(
            window=None,
            state=types.SimpleNamespace(
                heatmap=types.SimpleNamespace(tp_values=[24.0]),
                channel=types.SimpleNamespace(active_channel="GFP"),
            ),
            runtime=types.SimpleNamespace(heatmap=types.SimpleNamespace(
                array=np.asarray(array, dtype=float),
                spread=np.asarray(spread, dtype=float),
                spread_label=spread_label,
                layout_active=layout,
                cell_well_index={(0, 0): ["A01", "A02"], (0, 1): ["B01"]},
            )),
            widgets=types.SimpleNamespace(get_or_none={
                "_heatmap_tp_cb": types.SimpleNamespace(currentIndex=lambda: 0),
                "_heatmap_metric_cb": types.SimpleNamespace(
                    currentText=lambda: "Mean above threshold"),
            }.get),
            notify=types.SimpleNamespace(set_status=lambda _m: None),
        )
        monkeypatch.setattr(EX, "ask_save_csv", lambda *_a, **_k: str(out))
        monkeypatch.setattr(EX, "figure_names_map", lambda _c: {})
        EX.export_heatmap_data(ctx)
        with open(out, newline="") as fh:
            return list(csv.DictReader(fh))

    return _run


def test_a_measured_spread_is_written_out(exported):
    rows = exported(array=[[6.0, 2.0, np.nan]], spread=[[4.0, np.nan, np.nan]])

    assert rows[0]["err_SEM"] == "4.000000"


def test_an_undefined_spread_is_blank_and_not_a_zero(exported):
    """The single-replicate cell. ``0.000000`` here is the defect, not the value."""
    rows = exported(array=[[6.0, 2.0, np.nan]], spread=[[4.0, np.nan, np.nan]])

    assert rows[1]["err_SEM"] == ""
    assert rows[2]["err_SEM"] == ""


def test_a_measured_zero_spread_is_written_as_a_zero(exported):
    """The other half, and the reason blank cannot simply mean "small".

    Replicates that agreed exactly have a spread, and it is 0. A file that
    blanked this would lose a real measurement to protect the one above it.
    """
    rows = exported(array=[[6.0, 2.0, np.nan]], spread=[[0.0, np.nan, np.nan]])

    assert rows[0]["err_SEM"] == "0.000000"


def test_the_column_names_which_spread_it_holds(exported):
    """SD, SEM and a 95% CI are three intervals; an ``err`` column is unusable."""
    rows = exported(array=[[6.0, 2.0, np.nan]], spread=[[4.0, np.nan, np.nan]],
                    spread_label="SD")

    assert "err_SD" in rows[0] and "err_SEM" not in rows[0]


def test_the_column_name_survives_a_label_with_punctuation_in_it(exported):
    """The figure says ``95% CI``; a CSV header should not hold a space or a ``%``.

    Asserting the sanitising expression here would pass against a writer that
    never applies it, so this runs the export with that label and reads the
    header back off the file.
    """
    rows = exported(array=[[6.0, 2.0, np.nan]], spread=[[4.0, np.nan, np.nan]],
                    spread_label="95% CI")

    assert "err_95CI" in rows[0]
    assert rows[0]["err_95CI"] == "4.000000"
    assert not any(" " in k or "%" in k for k in rows[0])


def test_a_heatmap_drawn_without_a_spread_still_exports(exported):
    """``spread`` is absent on a runtime stamped before this column existed."""
    rows = exported(array=[[6.0, 2.0, np.nan]], spread=[[np.nan] * 3])

    assert [r["err_SEM"] for r in rows] == ["", "", ""]
    assert rows[0]["GFP_mean_above_threshold"] == "6.000000"
