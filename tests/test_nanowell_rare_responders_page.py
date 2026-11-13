"""The Rare responders test on the Nanowells page's Statistics view, driven through the real page builder and its
controls: the Test combo, *vs*, the worker run, the report on the figure, and the flagged nanowells in the pop-out.

The plate has three control wells and two treatment wells; four nanowells of each treatment well grow thirty-fold
from the baseline while every other nanowell doubles to quadruples, so the flagged set is known exactly.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from review.features.nanowell import _rare_controller as rc  # noqa: E402
from review.features.nanowell import statistics_compute as sc  # noqa: E402
from tests.test_nanowell_page import _loaded, _page, _texts, _write_nanowell_dataset  # noqa: E402

N_SITES = 60
RESPONDERS = (5, 17, 33, 48)


def _plate() -> dict:
    rng = np.random.default_rng(3)
    plate = {}
    for well in ("A01", "A02", "A03", "B01", "B02"):
        n0 = rng.integers(1, 4, N_SITES)
        nt = n0 * rng.integers(2, 5, N_SITES)
        if well.startswith("B"):
            for q in RESPONDERS:
                nt[q] = n0[q] * 30
        plate[well] = {"t_c": [int(v) for v in n0], "t_a": [int(v) for v in nt]}
    return plate


@pytest.fixture
def page(qtbot, tmp_path, monkeypatch):
    from review.domain import plot_members
    from review.domain.batch_models import ReplicateSet

    monkeypatch.setattr(plot_members, "plot_replicate_sets",
                        lambda ctx: [ReplicateSet("ctrl", ["A01", "A02", "A03"]),
                                     ReplicateSet("drug", ["B01", "B02"])])
    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path, counts=_plate()))
    ctx.state.selection.selected_wells = {"A01", "A02", "A03", "B01", "B02"}
    return ctx


def _choose(ctx, name, data):
    combo = ctx.widgets.get(name)
    index = combo.findData(data)
    assert index >= 0, (name, data)
    combo.setCurrentIndex(index)


def _open(qtbot, ctx):
    from review.features.nanowell.tab_view import VIEWS

    st = _loaded(qtbot, ctx)
    ctx.widgets.get("_nw_view_bar").setCurrentIndex([k for k, _l in VIEWS].index("statistics"))
    _choose(ctx, "_nw_tp_cb", "t_a")
    return st


def test_the_rare_test_runs_from_the_band_and_reports_on_the_figure(page, qtbot):
    st = _open(qtbot, page)
    _choose(page, "_nw_stats_test_cb", "rare")
    assert st.stats_test == "rare"
    assert _texts(page, "nw-rare-why") == [rc.NO_CONTROL]
    assert not page.widgets.get("_nw_stats_unit_cb").isEnabled()
    assert page.widgets.get("_nw_stats_flagged_btn").isVisibleTo(page.window)
    # vs lists the groups, not the wells, even on the Wells unit: the test compares groups.
    control = page.widgets.get("_nw_control_cb")
    assert [control.itemData(i) for i in range(control.count())] == ["", "ctrl", "drug"]
    _choose(page, "_nw_control_cb", "ctrl")
    qtbot.waitUntil(lambda: bool(_texts(page, "nw-rare-line")), timeout=30000)
    # The figure carries the verdict and each test's p; the full report is in Details, under the figure.
    figure = " ".join(_texts(page, "nw-rare-line"))
    assert "drug: 2 vs 3 control wells — supports rare-responder enrichment" in figure
    assert rc.DETAILS_POINTER in figure and "anti-conservative" not in figure
    assert _texts(page, "nw-rare-unit") == ["drug"]
    assert page.widgets.get("_nw_details_btn").isVisibleTo(page.window)
    assert page.widgets.get("_nw_details_summary").text().startswith("Rare responders: 1 treatment(s)")
    page.widgets.get("_nw_details_btn").setChecked(True)
    details = page.widgets.get("_nw_details_body").text()
    assert "drug (all): 2 treatment vs 3 control wells" in details
    assert "anti-conservative" in details and "uncalibrated" in details
    assert f"{rc.VALUE_NOTE} Timepoint: t_a · 5 h." in details      # its hours, as the Timepoint control names it and rc.UNIT_IGNORED in details
    # The flagged nanowells are exactly the spiked ones of the treatment wells, highest fold change first.
    flagged = {row["site"] for row in st.rare_flagged}
    assert {(w, q, 0) for w in ("B01", "B02") for q in RESPONDERS} <= flagged
    assert all(site[0] in ("B01", "B02") for site in flagged)
    assert st.rare_flagged[0]["fc"] == max(row["fc"] for row in st.rare_flagged)


def test_review_flagged_crops_shows_them_in_the_pop_out(page, qtbot):
    st = _open(qtbot, page)
    _choose(page, "_nw_stats_test_cb", "rare")
    _choose(page, "_nw_control_cb", "ctrl")
    qtbot.waitUntil(lambda: bool(st.rare_flagged), timeout=30000)
    page.widgets.get("_nw_stats_flagged_btn").click()
    assert st.popout_source == "responders"
    source = page.widgets.get("_nw_popout_source_cb")
    assert source.currentData() == "responders" and source.itemText(2) == f"Flagged ({len(st.rare_flagged):,})"
    fig = page.widgets.get("_nw_popout_fig")
    texts = [t.get_text() for t in fig.texts] + [t.get_text() for ax in fig.axes for t in ax.texts]
    assert any("Rare responders · flagged nanowells" in t for t in texts)
    # Each drawn row is a flagged nanowell, so a click on it selects that nanowell.
    assert set(st.popout_hits.values()) <= {row["site"] for row in st.rare_flagged}
    assert st.popout_hits


def test_switching_back_to_a_sample_test_draws_the_samples_again(page, qtbot):
    st = _open(qtbot, page)
    _choose(page, "_nw_stats_test_cb", "rare")
    _choose(page, "_nw_control_cb", "ctrl")
    qtbot.waitUntil(lambda: bool(_texts(page, "nw-rare-line")), timeout=30000)
    _choose(page, "_nw_stats_test_cb", "welch")
    assert page.widgets.get("_nw_stats_unit_cb").isEnabled()
    assert not _texts(page, "nw-rare-line") and _texts(page, sc.GID_HEADER)
    assert st.rare_flagged == [] and st.rare_footer is None
    assert not page.widgets.get("_nw_details_btn").isVisibleTo(page.window)


@pytest.fixture
def grouped_page(qtbot, tmp_path):
    """The same plate with two real replicate sets, ``ctrl`` and ``drug``, whose hidden flags the plate's clicks toggle
    — not a stubbed ``plot_replicate_sets``, so hiding one goes through the rule every tab draws by."""
    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path, counts=_plate()))
    ctx.state.selection.selections = [
        {"id": "s1", "name": "ctrl", "wells": ["A01", "A02", "A03"], "hidden": False},
        {"id": "s2", "name": "drug", "wells": ["B01", "B02"], "hidden": False}]
    ctx.state.selection.active_solo_wells = set()
    ctx.state.selection.selected_wells = {"A01", "A02", "A03", "B01", "B02"}
    return ctx


def _hide(ctx, name, hidden=True):
    from review.features.nanowell import controller as nwc

    next(s for s in ctx.state.selection.selections if s["name"] == name)["hidden"] = hidden
    nwc.selection_changed(ctx)


def test_a_control_hidden_on_the_plate_is_named_with_how_to_show_it_again(grouped_page, qtbot):
    """The reported defect: with vs set to the control, clicking the control's and the treatment's wells on the plate
    (which toggles each group off) left the page blank — the figure said the control "is not one of the groups
    drawn", and then the page said no wells were selected, although they all were. Each state now says which group
    the plate hides and how to bring it back, and showing the control again runs the test against it."""
    from review.features.nanowell import controller as nwc

    page = grouped_page
    st = _open(qtbot, page)
    _choose(page, "_nw_stats_test_cb", "rare")
    _choose(page, "_nw_control_cb", "ctrl")
    qtbot.waitUntil(lambda: bool(_texts(page, "nw-rare-line")), timeout=30000)

    _hide(page, "ctrl")
    qtbot.waitUntil(lambda: bool(_texts(page, "nw-rare-why")), timeout=10000)
    assert _texts(page, "nw-rare-why") == [
        "The control group ctrl is hidden on the plate, so there is nothing to test against. Hidden on the plate: "
        "ctrl (A01, A02, A03). A click on a well of a group toggles the whole group: click one of its wells to show "
        "it again, or All under the plate to show every group."]

    _hide(page, "drug")
    reason = nwc.nanowell_empty_reason(page)
    assert reason is not None and reason.title == "Every group is hidden on the plate."
    assert "ctrl (A01, A02, A03); drug (B01, B02)" in reason.hint

    _hide(page, "drug", False)
    _hide(page, "ctrl", False)
    assert nwc.nanowell_empty_reason(page) is None
    assert st.control == "ctrl"                          # the choice under vs survives the group being hidden
    qtbot.waitUntil(lambda: bool(_texts(page, "nw-rare-line")), timeout=30000)
    assert "drug: 2 vs 3 control wells" in " ".join(_texts(page, "nw-rare-line"))


def test_only_the_control_drawn_names_the_hidden_treatment(grouped_page, qtbot):
    page = grouped_page
    _open(qtbot, page)
    _choose(page, "_nw_stats_test_cb", "rare")
    _choose(page, "_nw_control_cb", "ctrl")
    _hide(page, "drug")
    qtbot.waitUntil(lambda: bool(_texts(page, "nw-rare-why")), timeout=10000)
    assert _texts(page, "nw-rare-why")[0].startswith("Only the control group is drawn. Hidden on the plate: drug (B01, "
                                                     "B02).")


def test_a_sample_test_whose_vs_well_is_hidden_says_it_compared_every_pair(grouped_page, qtbot):
    """A t-test with vs set to a well compares each other well against it; once that well's group is hidden it
    compares every pair instead, and says so rather than printing a report that reads as the one asked for."""
    page = grouped_page
    _open(qtbot, page)
    _choose(page, "_nw_control_cb", "A01")
    assert not any("vs A01" in t for t in _texts(page, sc.GID_NOTE))
    _hide(page, "ctrl")
    qtbot.waitUntil(lambda: any("vs A01" in t for t in _texts(page, sc.GID_NOTE)), timeout=10000)
    note = " ".join(_texts(page, sc.GID_NOTE))
    assert ("vs A01 is not drawn: its group ctrl is hidden on the plate, so every pair is compared instead. Hidden on "
            "the plate: ctrl (A01, A02, A03).") in note
