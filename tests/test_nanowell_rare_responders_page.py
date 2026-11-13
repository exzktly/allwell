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
    # A group named as a control is preselected, and the figure says it was chosen by its name.
    assert st.control == "ctrl" and st.control_guessed == "ctrl"
    qtbot.waitUntil(lambda: rc.guessed_note("ctrl") in _texts(page, "nw-rare-line"), timeout=30000)
    assert not page.widgets.get("_nw_stats_unit_cb").isVisibleTo(page.window)     # never applies: hidden, not greyed
    assert page.widgets.get("_nw_stats_flagged_btn").isVisibleTo(page.window)
    # vs lists the groups, not the wells, even on the Wells unit: the test compares groups.
    control = page.widgets.get("_nw_control_cb")
    assert [control.itemData(i) for i in range(control.count())] == ["", "ctrl", "drug"]
    _choose(page, "_nw_control_cb", "ctrl")
    qtbot.waitUntil(lambda: bool(_texts(page, "nw-rare-line")), timeout=30000)
    # The figure leads with the answer in counts; under the panels the deciding test, the supporting ones and the
    # caveats; the full report is in Details, under the figure.
    assert [t.replace("\n", " ") for t in _texts(page, "nw-rare-unit")] == [
        "drug: rare responders detected. 8 of 120 drug nanowells (6.7%) grew past the control cutoff (its top 1%), "
        "against 0 of 180 control nanowells (0.0%) · 2 drug vs 3 control wells."]
    figure = " ".join(_texts(page, "nw-rare-line"))
    assert "Deciding: exceedance test p = 0.00072." in figure
    assert "Higher Criticism p = 0.1 (the lowest possible with these wells)" in figure
    assert "⚠ With 3 control and 2 drug wells, Higher Criticism and the shape test cannot go below p = 0.1" in figure
    assert rc.DETAILS_POINTER in figure and "anti-conservative" not in figure
    assert page.widgets.get("_nw_stats_flagged_btn").isEnabled()
    assert page.widgets.get("_nw_details_btn").isVisibleTo(page.window)
    assert page.widgets.get("_nw_details_summary").text().startswith("Rare responders: 1 treatment(s)")
    page.widgets.get("_nw_details_btn").setChecked(True)
    details = page.widgets.get("_nw_details_body").text()
    assert "drug (all plates): 2 drug vs 3 control wells — rare responders detected" in details
    assert "anti-conservative" in details and "uncalibrated" in details
    assert f"{rc.VALUE_NOTE} Timepoint: t_a · 5 h." in details      # its hours, as the Timepoint control names it
    assert rc.UNIT_IGNORED in details
    # The flagged nanowells are exactly the spiked ones of the treatment wells — the ones past the cutoff, nothing
    # else — highest fold change first.
    flagged = {row["site"] for row in st.rare_flagged}
    assert flagged == {(w, q, 0) for w in ("B01", "B02") for q in RESPONDERS}
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
    # Its columns name each timepoint as the band does (by its hours), and each row names its nanowell as the Array
    # view draws the address, never "q 5, r 0", which beside a statistics report reads as a q-value.
    heads = [t.get_text() for t in fig.texts if t.get_gid() == "nw-extremes-tp"]
    assert [h.split(" ·")[0] for h in heads[:2]] == ["0 h", "5 h"]
    rows = [t.get_text().split("\n")[0] for t in fig.texts if t.get_gid() == "nw-extremes-row"]
    assert rows and all(" nanowell " in r and " q " not in r for r in rows), rows
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
    # In replicate mode the plate does not write selected_wells (a click toggles a group), so it holds none here, as
    # in the reported session; with every set hidden the plot tabs draw selected_wells instead (roll-up review X4).
    ctx.state.selection.selected_wells = set()
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
    assert _texts(page, "nw-rare-unit")[0].startswith("drug: rare responders detected.")


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


def test_with_nothing_flagged_review_flagged_crops_is_greyed_out_and_says_why(qtbot, tmp_path, monkeypatch):
    """The null plate: nothing grew past the cutoff, so the button that opens the flagged nanowells is greyed out with
    the reason on it, and the pop-out — opened from its own source menu — says there is nothing to review rather than
    telling the user to run a test that has already run."""
    from review.domain import plot_members
    from review.domain.batch_models import ReplicateSet
    from review.features.nanowell import statistics_view as sv

    rng = np.random.default_rng(3)
    plate = {}
    for well in ("A01", "A02", "A03", "B01", "B02"):
        n0 = rng.integers(1, 4, N_SITES)
        plate[well] = {"t_c": [int(v) for v in n0], "t_a": [int(v) for v in n0 * rng.integers(2, 5, N_SITES)]}
    monkeypatch.setattr(plot_members, "plot_replicate_sets",
                        lambda ctx: [ReplicateSet("ctrl", ["A01", "A02", "A03"]),
                                     ReplicateSet("drug", ["B01", "B02"])])
    page = _page(qtbot, _write_nanowell_dataset(tmp_path, counts=plate))
    page.state.selection.selected_wells = set(plate)
    st = _open(qtbot, page)
    _choose(page, "_nw_stats_test_cb", "rare")
    button = page.widgets.get("_nw_stats_flagged_btn")
    qtbot.waitUntil(lambda: bool(_texts(page, "nw-rare-line")), timeout=30000)
    assert st.rare_flagged == []
    assert _texts(page, "nw-rare-unit")[0].replace("\n", " ").startswith("drug: rare responders not detected. 0 of 120")
    assert not button.isEnabled() and button.toolTip() == sv.FLAGGED_NONE_TIP
    rc.open_flagged_popout(page)                  # what the button runs; the pop-out's source menu reaches it too
    assert st.popout_source == "responders"
    assert _popout_texts(page, "nw-popout-flagged-empty") == [rc.FLAGGED_NONE]


def _popout_texts(ctx, gid):
    fig = ctx.widgets.get("_nw_popout_fig")
    return [t.get_text() for t in list(fig.texts) + [t for ax in fig.axes for t in ax.texts] if t.get_gid() == gid]


def test_the_computing_message_says_what_is_being_tested():
    """It said "Running the rare-responder tests on a worker…" — where it runs, not what it is doing."""
    assert rc.computing_text({"ctrl": ["A01"], "drug": ["B01"], "dmso": ["C01"]}, "ctrl") == (
        "Testing drug, dmso against ctrl for rare responders…")


def test_a_control_is_guessed_only_from_one_unambiguous_name_and_a_choice_ends_the_guess():
    assert rc.guess_control(["ctrl", "drug"]) == "ctrl"
    assert rc.guess_control(["DMSO 0.1%", "Compound A"]) == "DMSO 0.1%"
    assert rc.guess_control(["Vehicle-ctl", "drug"]) == "Vehicle-ctl"
    assert rc.guess_control(["drug", "doxorubicin"]) == ""                   # no name says control
    assert rc.guess_control(["ctrl", "DMSO"]) == ""                          # two do: no guess between them
    assert rc.guess_control(["controller line", "drug"]) == ""              # a word, not a substring


def test_with_no_control_named_the_figure_asks_for_one(qtbot, tmp_path, monkeypatch):
    from review.domain import plot_members
    from review.domain.batch_models import ReplicateSet

    monkeypatch.setattr(plot_members, "plot_replicate_sets",
                        lambda ctx: [ReplicateSet("plain", ["A01", "A02", "A03"]),
                                     ReplicateSet("drug", ["B01", "B02"])])
    page = _page(qtbot, _write_nanowell_dataset(tmp_path, counts=_plate()))
    page.state.selection.selected_wells = {"A01", "A02", "A03", "B01", "B02"}
    st = _open(qtbot, page)
    _choose(page, "_nw_stats_test_cb", "rare")
    from review.features.nanowell import statistics_view as sv

    assert st.control == "" and st.control_guessed == ""
    assert _texts(page, "nw-rare-why") == [rc.NO_CONTROL]
    button = page.widgets.get("_nw_stats_flagged_btn")
    assert not button.isEnabled() and button.toolTip() == sv.FLAGGED_NOT_RUN_TIP
    _choose(page, "_nw_control_cb", "plain")
    qtbot.waitUntil(lambda: bool(_texts(page, "nw-rare-line")), timeout=30000)
    assert not any(t.startswith("⚠ plain was taken") for t in _texts(page, "nw-rare-line"))


def test_the_control_combo_is_captioned_control_for_this_test_and_vs_for_the_others(page, qtbot):
    from PySide6.QtWidgets import QLabel

    from review.features.nanowell import tab_view

    _open(qtbot, page)
    caption = page.widgets.get("_nw_control_cb").parentWidget().findChild(QLabel)
    assert caption.text() == tab_view.VS_CAPTION
    _choose(page, "_nw_stats_test_cb", "rare")
    assert caption.text() == tab_view.CONTROL_CAPTION
    _choose(page, "_nw_stats_test_cb", "welch")
    assert caption.text() == tab_view.VS_CAPTION
