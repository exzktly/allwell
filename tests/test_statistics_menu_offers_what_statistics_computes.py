"""The Statistics timepoint menu offers only timepoints Statistics can compute.

``get_timepoints_with_rows`` answers from each well's open-time summary. The
summary's ``timepoints`` also parse the ``timepoint`` label wherever
``timepoint_hours`` is blank — right for the menus that read the label too, wrong
for Statistics, whose collectors select rows strictly on ``timepoint_hours``
(``stats/controller.py``: ``tp_close_mask`` on that column, and a well without
the column is skipped). So a well with label-only rows got a ``48`` entry in the
menu at which every group then had zero cells — an empty table that looks like
a real answer.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from review.data import data_loading as dl
from review.data.well_summary import WellSummary, summarize_frame


def _well() -> pd.DataFrame:
    return pd.DataFrame({
        "well": ["A01"] * 3,
        "fov": ["1"] * 3,
        "timepoint": ["0h", "24h", "48h"],
        "timepoint_hours": [0.0, 24.0, np.nan],     # the 48 h row is labelled only
        "nucleus_id": [1, 2, 3],
    })


def _ctx(summary) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(dataset=SimpleNamespace(
        summaries={"A01": summary}, well_paths={"A01": "unused.csv"})))


def _statistics_can_compute(df: pd.DataFrame, t: float) -> bool:
    """The row selection Statistics' collectors make, for one timepoint."""
    from domain.tp_match import tp_close_mask

    tp = pd.to_numeric(df["timepoint_hours"], errors="coerce").to_numpy()
    with np.errstate(invalid="ignore"):
        return bool((np.isfinite(tp) & tp_close_mask(tp, t)).any())


def test_the_menu_offers_no_timepoint_that_only_a_label_names():
    df = _well()
    offered = dl.get_timepoints_with_rows(_ctx(summarize_frame(df)))
    assert offered == [0.0, 24.0], (
        f"the Statistics menu offered {offered}; 48 h exists only as a label, and "
        f"Statistics selects on timepoint_hours")
    assert all(_statistics_can_compute(df, t) for t in offered)


def test_the_label_still_counts_for_the_menus_that_read_it():
    """The summary's own ``timepoints`` keep their meaning — the parse every other
    timepoint list uses — so only the Statistics question changed."""
    assert summarize_frame(_well()).timepoints == (0.0, 24.0, 48.0)


def test_a_summary_round_trips_its_hours():
    s = summarize_frame(_well())
    assert WellSummary.from_json(s.to_json()) == s
