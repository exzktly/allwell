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
    assert f"{rc.VALUE_NOTE} Timepoint: t_a." in details and rc.UNIT_IGNORED in details
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
