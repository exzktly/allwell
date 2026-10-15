"""The fixed-cohort option (tracker I8): every line over time follows one set of nanowells.

The page fixture's A01 has nanowell 3 occluded at t_c and holding one cell at t_a, so the t_a median is 2.5 over
all four nanowells and 3.0 over the three measured at both timepoints: the two answers differ, and a cohort that
dropped rows instead of counting them would break the sum of used and excluded."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from review.features.nanowell import controller as nwc  # noqa: E402
from review.features.nanowell.distribution_compute import distribution_model  # noqa: E402
from tests.test_nanowell_page import _loaded, _texts, page  # noqa: E402,F401


def _a01(st, cohort):
    model = distribution_model(st.metrics, "n", channels=st.data.channels, groups={"A01": ["A01"]},
                               time_source=st.data.time_source, cohort=cohort)
    swarms = {s.label: s for s in model.panels[0].swarms}
    return model, swarms


def test_the_cohort_keeps_the_nanowells_measured_at_every_timepoint_and_counts_the_rest(page, qtbot):  # noqa: F811
    st = _loaded(qtbot, page)
    plain, swarms = _a01(st, cohort=False)
    assert plain.panels[0].medians["A01"][1][1] == 2.5
    model, swarms = _a01(st, cohort=True)
    assert model.panels[0].medians["A01"][1][1] == 3.0
    late = swarms["t_a"]
    assert sorted(late.values) == [2.0, 3.0, 4.0]
    assert late.excluded == {"outside the fixed cohort": 1}
    assert len(late.values) + late.n_excluded == late.n_roster == 4
    assert "Fixed cohort: only the 3 of 4 nanowells with a value at every timepoint of their well are used; the " \
           "rest are counted as outside the fixed cohort." in model.notes


def test_the_checkbox_sets_the_cohort_and_the_figure_says_so(page, qtbot):  # noqa: F811
    st = _loaded(qtbot, page)
    page.state.selection.selected_wells = {"A01"}
    nwc.nw_on_selection_changed(page)
    box = page.widgets.get("_nw_cohort_box")
    box.setChecked(True)
    assert st.cohort is True
    assert any(t.startswith("Fixed cohort: only the 3 of 4 nanowells") for t in _texts(page))
    nwc.set_view(page, "trajectories")
    assert any(t.startswith("Fixed cohort: 3 nanowells with a value at every timepoint drawn") for t in _texts(page))
    assert st.to_config()["cohort"] is True
    st.apply_config({"cohort": "yes"})                                        # only a real True is taken
    assert st.cohort is False


def test_the_well_summary_counts_the_cohort_s_exclusions_by_their_reason(page, qtbot):  # noqa: F811
    from review.features.nanowell.distribution_compute import COHORT_REASON, fixed_cohort
    from review.features.nanowell.series_compute import exclusion_reason
    from review.features.nanowell.summary_compute import well_summary

    st = _loaded(qtbot, page)
    a01 = st.metrics[st.metrics["well"] == "A01"]
    frame, reasons, kept, sites = fixed_cohort(a01, "n", exclusion_reason(a01, "n"))
    assert (kept, sites) == (3, 4)
    late = well_summary(frame, "n", reasons=reasons).set_index("timepoint").loc["t_a"]
    assert (late["n_used"], late["excluded"], late["median"]) == (3, {COHORT_REASON: 1}, 3.0)
