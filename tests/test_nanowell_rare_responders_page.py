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
from tests.test_nanowell_page import _loaded, _page, _prose, _texts, _write_nanowell_dataset  # noqa: E402

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


def rx_num(st) -> str:
    """The drawn result's exceedance q as the report writes it."""
    from review.features.nanowell import rare_export_compute as rx

    return rx._num(st.rare_results[next(reversed(st.rare_results))].units[0].q["exceedance"])


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
    # The figure leads with the answer in counts; each test's row lists its p; under the panels, the caveats; the full
    # report is in Details, under the figure. Three control and two drug wells have 10 relabelings, so the relabeling
    # p cannot go below 0.1: with this few wells the regression alone cannot call it detected, and the headline says
    # why before its counts.
    assert [t.replace("\n", " ") for t in _texts(page, "nw-rare-unit")] == [
        "drug: cannot be decided with these wells — with 3 + 2 wells the regression's q = "
        f"{rx_num(st)} needs relabeling the wells to agree, and relabeling gives p = 0.1, the lowest 3 + 2 wells allow "
        "(1 in 10), which corrected across the 1 group tested is q = 0.1. These wells cannot say more: add control "
        "wells, or test fewer groups. 8 of 120 drug nanowells (6.7%) rose above the control cutoff (its top 1%) on "
        "cell count, against 0 of 180 control nanowells (0.0%) · 2 drug vs 3 control wells."]
    figure = " ".join(_texts(page, "nw-rare-line"))
    # The p is the result's own (its last digit depends on the platform's numeric libraries: 0.00072 here, 0.000717
    # on the CI runner), so the sentence is checked against the result and the value to 2%.
    from review.features.nanowell import rare_export_compute as rx

    drawn = st.rare_results[next(reversed(st.rare_results))].units[0].primary.p
    assert drawn == pytest.approx(0.00072, rel=0.02)
    assert _texts(page, "nw-rare-test-p:exceedance") == [f"drug: p = {rx._num(drawn)}"]
    # Unstratified, the control pool is larger than a well, so the spikes fall where HC+ does not look: said so.
    assert "Higher Criticism cannot see the 8 most extreme drug nanowell(s)" in figure
    assert "⚠ Comparison 1 of 1 (drug): With 3 control and 2 drug wells, Higher Criticism and the shape test cannot go below p = 0.1" in figure
    assert rc.DETAILS_POINTER in figure and "anti-conservative" not in figure
    assert page.widgets.get("_nw_stats_flagged_btn").isEnabled()
    assert page.widgets.get("_nw_details_btn").isVisibleTo(page.window)
    assert page.widgets.get("_nw_details_summary").text().startswith("Rare responders: 1 treatment(s)")
    page.widgets.get("_nw_details_btn").setChecked(True)
    details = page.widgets.get("_nw_details_body").text()
    assert "drug (all plates): 2 drug vs 3 control wells — cannot be decided with these wells" in details
    assert "anti-conservative" in details and "uncalibrated" in details
    assert f"{rc.VALUE_NOTE} Timepoint: t_a · 5 h." in details      # its hours, as the Timepoint control names it
    assert rc.UNIT_IGNORED in details
    # The flagged nanowells are exactly the spiked ones of the treatment wells — the ones past the cutoff, nothing
    # else — highest fold change first.
    flagged = {row["site"] for row in st.rare_flagged}
    assert flagged == {(w, q, 0) for w in ("B01", "B02") for q in RESPONDERS}
    assert st.rare_flagged[0]["fc"] == max(row["fc"] for row in st.rare_flagged)


def test_the_rare_page_grows_its_canvas_to_what_it_needs_and_another_test_gives_it_back(page, qtbot):
    """User decision: the figure never hides a comparison or a test, so the Rare responders page is as tall as it
    needs and the page scrolls. The canvas' minimum is the height the page asked for, the page is drawn again at that
    height once the canvas has grown, and any other test leaves the canvas no minimum — it fills the window again."""
    import math

    from review.features.nanowell._rare_render import needed_height, needs_relayout

    st = _open(qtbot, page)
    canvas, fig = page.widgets.get("_nw_canvas"), page.widgets.get("_nw_fig")
    scroll = page.widgets.get("_nw_figure_scroll")
    assert canvas.minimumHeight() == 0
    _choose(page, "_nw_stats_test_cb", "rare")
    _choose(page, "_nw_control_cb", "ctrl")
    qtbot.waitUntil(lambda: st.rare_footer is not None and canvas.minimumHeight() > 0, timeout=30000)
    need = needed_height(fig)
    ratio = getattr(canvas, "device_pixel_ratio", 1.0) or 1.0
    assert canvas.minimumHeight() == math.ceil(need * fig.dpi / ratio)
    qtbot.waitUntil(lambda: not needs_relayout(fig) and abs(fig.get_figheight() - need) < 0.05, timeout=10000)
    qtbot.waitUntil(lambda: scroll.verticalScrollBar().maximum() > 0, timeout=10000)   # taller than the window
    _choose(page, "_nw_stats_test_cb", "welch")
    qtbot.waitUntil(lambda: canvas.minimumHeight() == 0, timeout=10000)
    assert needed_height(fig) is None
    qtbot.waitUntil(lambda: scroll.verticalScrollBar().maximum() == 0, timeout=10000)


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
    # Its columns name each timepoint as the band does (by its hours), and each row names its nanowell by its address
    # as the Array view draws it, never "q 5, r 0", which beside a statistics report reads as a q-value.
    heads = [t.get_text() for t in fig.texts if t.get_gid() == "nw-extremes-tp"]
    assert [h.split(" ·")[0] for h in heads[:2]] == ["0 h", "5 h"]
    rows = [t.get_text().split("\n")[0] for t in fig.texts if t.get_gid() == "nw-extremes-row"]
    import re

    assert rows and all(re.fullmatch(r"#\d+  B0[12] \d+,\d+", r) for r in rows), rows      # never "q 5, r 0"
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
    assert _texts(page, "nw-rare-unit")[0].replace("\n", " ").startswith("drug: cannot be decided with these wells — with 3 + 2 wells the regression")


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
    note = _prose(_texts(page, sc.GID_NOTE))
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



def test_the_page_s_metric_and_direction_drive_the_test(page, qtbot):
    """User decision: the value is the page's Metric — the band's Metric control, not a fixed count fold change — and
    the band's direction chooses the tail; both re-run the test. Cell count is a level, so the figure warns that
    nanowells which started higher can be past the cutoff without responding; its fold change compares each with its
    own Baseline and carries no such warning."""
    st = _open(qtbot, page)
    _choose(page, "_nw_stats_test_cb", "rare")
    direction = page.widgets.get("_nw_rare_direction_cb")
    assert direction.isVisibleTo(page.window) and direction.currentData() == "higher"
    qtbot.waitUntil(lambda: bool(_texts(page, "nw-rare-line")), timeout=30000)
    head = _texts(page, "nw-rare-unit")[0].replace("\n", " ")
    assert " on cell count, " in head
    # The values tested are the Metric's own: a spiked nanowell's cell count is 30 × its starting cells (30, 60 or 90);
    # its count fold change is 30 whatever it started with. A test fed the fold change whatever the Metric would
    # flag 30s here.
    counts = sorted({row["fc"] for row in st.rare_flagged})
    assert set(counts) <= {30.0, 60.0, 90.0} and max(counts) > 30.0, counts
    assert any(t.startswith("⚠ Comparison 1 of 1 (drug): Cell count is a level, not a change from the Baseline") for t in _texts(page, "nw-rare-line"))

    _choose(page, "_nw_metric_cb", "count_fc")
    qtbot.waitUntil(lambda: " on count fold change" in " ".join(_texts(page, "nw-rare-unit")).replace("\n", " "),
                    timeout=30000)
    assert not any("is a level" in t for t in _texts(page, "nw-rare-line"))
    qtbot.waitUntil(lambda: {row["fc"] for row in st.rare_flagged} == {30.0}, timeout=30000)
    assert _texts(page, "nw-rare-unit")[0].replace("\n", " ").startswith("drug: cannot be decided with these wells — with 3 + 2 wells the regression")

    _choose(page, "_nw_rare_direction_cb", "lower")
    assert st.rare_direction == "lower"
    qtbot.waitUntil(lambda: "fell below the control cutoff (its bottom 1%)" in
                    " ".join(_texts(page, "nw-rare-unit")).replace("\n", " "), timeout=30000)
    assert _texts(page, "nw-rare-unit")[0].replace("\n", " ").startswith("drug: rare responders not detected.")
    _choose(page, "_nw_stats_test_cb", "welch")
    assert not direction.isVisibleTo(page.window)


def test_the_direction_is_saved_with_the_page_and_a_bad_one_is_not_restored():
    from review.features.nanowell.state import NanowellState

    st = NanowellState()
    st.rare_direction = "lower"
    again = NanowellState()
    again.apply_config(st.to_config())
    assert again.rare_direction == "lower"
    again.apply_config({"rare_direction": "sideways"})
    assert again.rare_direction == "higher"


def test_the_control_curve_is_dotted_on_the_page_as_its_title_says(page, qtbot):
    """Reported: the left panel's title says "dotted: control", but the control curve on the page was solid and told
    apart only by its colour. The renderer drew it dotted; the page's style pass then stamped the Properties Line
    style (solid by default) over every data line. Read off the page's own figure, after that pass."""
    st = _open(qtbot, page)
    _choose(page, "_nw_stats_test_cb", "rare")
    _choose(page, "_nw_control_cb", "ctrl")
    qtbot.waitUntil(lambda: st.rare_footer is not None, timeout=30000)
    fig = page.widgets.get("_nw_fig")
    styles = {line.get_gid(): line.get_linestyle() for ax in fig.axes for line in ax.lines
              if str(line.get_gid()).startswith("rare-ecdf-")}
    assert styles == {"rare-ecdf-control:all": ":", "rare-ecdf-treatment:all": "-"}
    # The pooled shape test's gap is shaded on its own row's panel, and survives the page's style pass too.
    assert [c.get_gid() for ax in fig.axes for c in ax.collections if c.get_gid() == "rare-pooled-gap:all"] == [
        "rare-pooled-gap:all"]
    assert any("dotted: control" in t.get_text() for ax in fig.axes for t in ax.texts + [ax.title])


def _flag(st, wells, flag, sites=None):
    """Set the run's *flag* on *wells*' nanowells (all, or the ``nw_q`` in *sites*) at the Timepoint t_a."""
    rows = st.metrics["well"].isin(wells) & (st.metrics["timepoint"] == "t_a")
    if sites is not None:
        rows &= st.metrics["nw_q"].isin(sites)
    st.metrics.loc[rows, flag] = True if flag != "identity_ok" else False


def test_flagged_nanowells_are_tested_as_every_view_draws_them_and_the_test_has_no_flag_choice_of_its_own(
        page, qtbot):
    """Reported: the Rare responders test emptied both treatment groups on a plate whose Distribution view drew their
    nanowells, because it alone left out what the run had flagged. User decisions since: the test keeps them, as every
    view does, and its own *QC flags* choice is gone — the flags are Subset conditions, so one Subset leaves them out
    of every view at once. Here every drug nanowell is flagged count-disagreeing at the Timepoint."""
    st = _open(qtbot, page)
    _flag(st, ["B01", "B02"], "count_disagreement")
    _choose(page, "_nw_stats_test_cb", "rare")
    _choose(page, "_nw_control_cb", "ctrl")
    assert page.widgets.get_or_none("_nw_rare_qc_btn") is None
    qtbot.waitUntil(lambda: bool(_texts(page, "nw-rare-unit") or _texts(page, "nw-rare-why")), timeout=30000)
    assert not _texts(page, "nw-rare-why"), _texts(page, "nw-rare-why")         # no group emptied by its flags
    assert _texts(page, "nw-rare-unit")[0].replace("\n", " ").startswith("drug: cannot be decided with these wells — with 3 + 2 wells the regression")
    assert st.rare_footer.counts.endswith("300 nanowells kept, 0 excluded")
    assert not any("QC" in t for t in _texts(page, "nw-rare-line"))


@pytest.mark.parametrize("field, column", [("count_disagreement", "count_disagreement"),
                                           ("partially_visible", "partially_visible"),
                                           ("fused", "fused"), ("identity_out", "identity_ok")])
def test_a_subset_on_a_run_flag_leaves_the_same_nanowells_out_of_the_test_and_of_distribution(page, qtbot, field,
                                                                                                column):
    """User decision: to compare the Rare responders test with the Distribution view on the same nanowells, the run's
    flags are Subset conditions. Two nanowells of each drug well (none of the responders) carry the flag at the
    Timepoint; a Subset with the flag's condition set to *no* there takes those four out of the test and out of the
    Distribution view alike, and both say so as every Subset does."""
    from review.features.nanowell import subset_compute as ss
    from review.features.nanowell.tab_view import VIEWS

    st = _open(qtbot, page)
    _flag(st, ["B01", "B02"], column, sites=[0, 1])
    _choose(page, "_nw_stats_test_cb", "rare")
    _choose(page, "_nw_control_cb", "ctrl")
    qtbot.waitUntil(lambda: st.rare_footer is not None, timeout=30000)
    assert st.rare_footer.counts.endswith("300 nanowells kept, 0 excluded")
    from review.features.nanowell import controller

    def dots() -> set:
        return {(s[0], int(s[1])) for _ax, _x, _y, sites in st.hits.entries for s in sites}

    flagged = {(w, q) for w in ("B01", "B02") for q in (0, 1)}
    page.widgets.get("_nw_view_bar").setCurrentIndex([k for k, _l in VIEWS].index("distribution"))
    assert flagged <= dots() and len(dots()) == 300                     # Distribution draws them, as the test did
    controller.set_subset(page, ss.Subset((ss.Condition(field, "no", at="tp:t_a"),)).validated())
    assert not flagged & dots() and len(dots()) == 296                 # gone from Distribution …
    page.widgets.get("_nw_view_bar").setCurrentIndex([k for k, _l in VIEWS].index("statistics"))
    qtbot.waitUntil(lambda: st.rare_footer is not None and st.rare_footer.counts.endswith("296 nanowells kept, "
                                                                                          "0 excluded"),
                    timeout=30000)                                      # … and from the test, the same four


def test_a_page_saved_with_the_test_s_old_qc_flags_choice_still_opens():
    """A page saved while the test had its own *QC flags* choice carries ``rare_qc_excluded``: it opens, the choice
    is not restored (a Subset's flag conditions replace it), and the direction beside it is."""
    from review.features.nanowell.state import NanowellState

    st = NanowellState()
    st.apply_config({"rare_qc_excluded": ["fused", "edge"], "rare_direction": "lower"})
    assert st.rare_direction == "lower" and not hasattr(st, "rare_qc_excluded")
    assert "rare_qc_excluded" not in st.to_config()


def _grid_rows_entry(ctx, rows):
    ctx.widgets.get("_nw_popout_grid_menu").aboutToShow.emit()           # what opening the Grid menu does
    for action in ctx.widgets.get("_nw_popout_grid_menu").actions():
        if action.data() == ("rows", rows):
            return action
    raise AssertionError(f"no {rows}-row entry in the Grid menu")


def _drawn_rows(ctx):
    fig = ctx.widgets.get("_nw_popout_fig")
    return [t.get_text().split("\n")[0] for t in fig.texts if t.get_gid() == "nw-extremes-row"]


def test_grid_sets_how_many_flagged_rows_a_page_holds_and_the_pager_follows(page, qtbot):
    st = _open(qtbot, page)
    _choose(page, "_nw_stats_test_cb", "rare")
    _choose(page, "_nw_control_cb", "ctrl")
    qtbot.waitUntil(lambda: bool(st.rare_flagged), timeout=30000)
    page.widgets.get("_nw_stats_flagged_btn").click()
    n = len(st.rare_flagged)
    assert n > 3, n                                         # enough flagged rows for a page of 2 to make 2+ pages
    label, ahead = page.widgets.get("_nw_popout_page_label"), page.widgets.get("_nw_popout_page_next")
    assert len(_drawn_rows(page)) == min(n, 24) and label.text() == f"Page 1 of {-(-n // 24)}"
    _grid_rows_entry(page, 2).trigger()
    assert st.popout_rows == 2 and page.widgets.get("_nw_popout_grid_btn").text() == "Grid: 2 rows a page"
    assert label.text() == f"Page 1 of {-(-n // 2)}" and not ahead.isHidden()
    assert _drawn_rows(page)[0].startswith("#1 ") and len(_drawn_rows(page)) == 2
    ahead.click()
    ahead.click()                                           # page 3 of 2 rows: rows 5 and 6 at the top
    assert st.window_page == 2 and _drawn_rows(page)[0].startswith("#5 ")
    _grid_rows_entry(page, 4).trigger()                     # the row at the top stays on the page shown: page 2 of 4
    assert st.window_page == 1 and label.text() == f"Page 2 of {-(-n // 4)}"
    assert _drawn_rows(page)[0].startswith("#5 ")
    menu = page.widgets.get("_nw_popout_grid_menu")
    menu.aboutToShow.emit()                                 # opened again, it ticks the choice in force alone
    assert [a.data() for a in menu.actions() if a.isChecked()] == [("rows", 4)]
