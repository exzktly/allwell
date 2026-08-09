"""``inclusion_funnel`` — the per-layer breakdown behind the Cell Gating readout.

Its whole point is a distinction that is easy to lose: a layer that removed
nothing and a layer that was never applied are different facts, and reporting
both as "-0" is the silent substitution CLAUDE.md §7 warns about. These are pure
DataFrame-in, tuples-out tests; the tab that renders them is covered by
``test_cell_gating_layout.py``.
"""

from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")

from well_viewer.features.grouping.cell_gating.gating_compute import (
    format_inclusion_funnel, inclusion_funnel,
)


def _frame(**cols):
    n = len(next(iter(cols.values())))
    return pd.DataFrame({"Included": [1] * n, **cols})


def test_funnel_reports_each_layer_on_top_of_the_previous():
    df = _frame(
        Included=[1, 1, 1, 1, 0],       # curation/intensity drops one
        QC_Pass=[1, 1, 1, 0, 1],        # QC drops one more of the survivors
        Crop_Pass=[1, 1, 0, 1, 1],      # crop drops one more
    )
    stages = inclusion_funnel([df])

    assert stages[0] == ("Segmented cells", None, 5)
    assert stages[1] == ("Curation + intensity gate", 1, 4)
    assert stages[2] == ("QC debris gate", 1, 3)
    assert stages[3] == ("Crop region", 1, 2)
    # No Gate_Pass column at all — never applied, which is not the same as
    # applied and removing nothing.
    assert stages[4] == ("Scatter gates", None, 2)


def test_a_layer_that_removed_nothing_is_not_a_layer_that_never_ran():
    applied = inclusion_funnel([_frame(Included=[1, 1], Crop_Pass=[1, 1])])
    absent = inclusion_funnel([_frame(Included=[1, 1])])

    crop_applied = next(s for s in applied if s[0] == "Crop region")
    crop_absent = next(s for s in absent if s[0] == "Crop region")

    assert crop_applied[1] == 0, "an applied layer that kept everything removed 0"
    assert crop_absent[1] is None, "an absent layer must not report a count"

    applied_line = _line_for(format_inclusion_funnel(applied), "Crop region")
    absent_line = _line_for(format_inclusion_funnel(absent), "Crop region")
    assert "−0" in applied_line and "not applied" not in applied_line
    assert "not applied" in absent_line


def _line_for(text: str, label: str) -> str:
    return next(ln for ln in text.splitlines() if label in ln)


def test_funnel_last_stage_matches_the_composed_inclusion():
    from well_viewer.data.data_loading import df_included_mask

    df = _frame(
        Included=[1, 1, 1, 0],
        QC_Pass=[1, 1, 0, 1],
        Crop_Pass=[1, 0, 1, 1],
        Gate_Pass=[0, 1, 1, 1],
    )
    assert inclusion_funnel([df])[-1][2] == int(df_included_mask(df).sum())


def test_funnel_sums_across_wells_and_skips_empty_frames():
    a = _frame(Included=[1, 1], QC_Pass=[1, 0])
    b = _frame(Included=[1], QC_Pass=[1])
    stages = inclusion_funnel([a, None, pd.DataFrame(), b])

    assert stages[0][2] == 3
    assert next(s for s in stages if s[0] == "QC debris gate")[1:] == (1, 2)


def test_no_data_formats_to_nothing():
    assert format_inclusion_funnel(inclusion_funnel([])) == ""


