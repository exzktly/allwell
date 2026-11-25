"""The Rare responders test's *Donor* combo on the Nanowells page's Statistics view, driven through the real page
builder: with a Donor variable on the plate, each donor's groups are tested against that donor's own control and
corrected within the donor; choosing *none* goes back to one control for every group; a group whose wells come from
two donors is named on the figure and not tested.

Donor 2's wells all grow faster than donor 1's, so tested against donor 1's control its untreated wells read as
responders — the figure's comparisons name which control each group was tested against, so a page that forgot to
pass the donor plan would show ``vs D1 ctrl`` where it should show ``vs D2 ctrl (Donor 2)``.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from review.features.nanowell import rare_donor_compute as dn  # noqa: E402
from tests.test_nanowell_page import _loaded, _page, _prose, _texts, _write_nanowell_dataset  # noqa: E402
from tests.test_nanowell_rare_responders_page import _choose  # noqa: E402

N_SITES = 60
GROUPS = {"D1 ctrl": ["A01", "A02", "A03"], "D1 drug": ["B01", "B02"],
          "D2 ctrl": ["C01", "C02", "C03"], "D2 drug": ["D01", "D02"], "mixed": ["E01", "E02"]}
DONOR = {"A": "d1", "B": "d1", "C": "d2", "D": "d2", "E01": "d1", "E02": "d2"}
MOLECULE = {"A": "ctrl", "B": "drug", "C": "ctrl", "D": "drug", "E": "drug"}


def _plate() -> dict:
    rng = np.random.default_rng(3)
    plate = {}
    for wells in GROUPS.values():
        for well in wells:
            n0 = rng.integers(1, 4, N_SITES)
            fold = rng.integers(2, 5, N_SITES) * (3 if well[0] in "CD" else 1)    # donor 2 grows faster
            nt = n0 * fold
            if well[0] == "B":
                nt[[5, 17, 33, 48]] = n0[[5, 17, 33, 48]] * 30
            plate[well] = {"t_c": [int(v) for v in n0], "t_a": [int(v) for v in nt]}
    return plate


def _variables():
    from review.domain.sample_variables import Variable, VariableSet, VariableValue

    donor = Variable("v_donor", "Donor", values=(VariableValue("d1", "1"), VariableValue("d2", "2")))
    molecule = Variable("v_mol", "Molecule", values=(VariableValue("ctrl", "control"), VariableValue("drug", "drug")))
    wells = [w for ws in GROUPS.values() for w in ws]
    return VariableSet((donor, molecule), {"v_donor": {w: DONOR.get(w, DONOR.get(w[0])) for w in wells},
                                           "v_mol": {w: MOLECULE[w[0]] for w in wells}})


@pytest.fixture
def page(qtbot, tmp_path, monkeypatch):
    from review.domain import plot_members
    from review.domain.batch_models import ReplicateSet

    monkeypatch.setattr(plot_members, "plot_replicate_sets",
                        lambda ctx: [ReplicateSet(name, wells) for name, wells in GROUPS.items()])
    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path, counts=_plate()))
    ctx.state.selection.selected_wells = {w for ws in GROUPS.values() for w in ws}
    ctx.state.selection.variables = _variables()
    return ctx


def _open_rare(qtbot, ctx):
    from review.features.nanowell.tab_view import VIEWS

    st = _loaded(qtbot, ctx)
    ctx.widgets.get("_nw_view_bar").setCurrentIndex([k for k, _l in VIEWS].index("statistics"))
    _choose(ctx, "_nw_tp_cb", "t_a")
    _choose(ctx, "_nw_stats_test_cb", "rare")
    _choose(ctx, "_nw_control_cb", "D1 ctrl")
    return st


def _heads(ctx) -> list:
    return [t.replace("\n", " ") for t in _texts(ctx, "nw-rare-comparison-head:0")
            + _texts(ctx, "nw-rare-comparison-head:1")]


def test_each_donor_s_drug_is_tested_against_its_own_control_and_the_mixed_group_is_named(page, qtbot):
    st = _open_rare(qtbot, page)
    combo = page.widgets.get("_nw_rare_donor_cb")
    assert combo.isVisibleTo(page.window)
    # The variable named Donor is read while nothing has been chosen, and the combo shows it.
    assert st.rare_donor == dn.AUTO and combo.currentData() == "v_donor"
    assert [combo.itemData(i) for i in range(combo.count())] == [dn.OFF, "v_donor", "v_mol"]
    qtbot.waitUntil(lambda: len(_heads(page)) == 2, timeout=30000)
    assert _heads(page) == ["Comparison 1 of 2: D1 drug vs D1 ctrl (Donor 1)",
                            "Comparison 2 of 2: D2 drug vs D2 ctrl (Donor 2)"]
    figure = _prose(_texts(page, "nw-rare-line"))
    assert ("⚠ Not tested: mixed: not tested — its wells come from more than one Donor (1, 2): define a group per "
            "Donor.") in figure
    result = st.rare_results[next(reversed(st.rare_results))]
    assert {u.treatment: u.control_name for u in result.units} == {"D1 drug": "D1 ctrl", "D2 drug": "D2 ctrl"}
    assert {u.treatment: u.family_n for u in result.units} == {"D1 drug": 1, "D2 drug": 1}


def test_choosing_none_tests_every_group_against_the_one_control(page, qtbot):
    st = _open_rare(qtbot, page)
    qtbot.waitUntil(lambda: len(_heads(page)) == 2, timeout=30000)
    _choose(page, "_nw_rare_donor_cb", dn.OFF)
    assert st.rare_donor == dn.OFF
    qtbot.waitUntil(lambda: len([t for i in range(4) for t in _texts(page, f"nw-rare-comparison-head:{i}")]) == 4,
                    timeout=30000)
    heads = [t.replace("\n", " ") for i in range(4) for t in _texts(page, f"nw-rare-comparison-head:{i}")]
    assert all(h.endswith("vs D1 ctrl") for h in heads), heads
    assert "Not tested" not in _prose(_texts(page, "nw-rare-line"))
    # The choice is the page's, saved with it, and an unknown value is not invented into a variable.
    from review.features.nanowell.state import NanowellState

    again = NanowellState()
    again.apply_config(st.to_config())
    assert again.rare_donor == dn.OFF


def test_the_donor_combo_is_hidden_while_the_plate_has_no_variable(page, qtbot):
    from review.domain.sample_variables import VariableSet

    page.state.selection.variables = VariableSet()
    _open_rare(qtbot, page)
    assert not page.widgets.get("_nw_rare_donor_cb").isVisibleTo(page.window)
    # With no donor given, every group is tested against the one control, and the figure says so at its top.
    qtbot.waitUntil(lambda: len([t for i in range(4) for t in _texts(page, f"nw-rare-comparison-head:{i}")]) == 4,
                    timeout=30000)
    lines = _texts(page, "nw-rare-line")
    assert _prose(lines[:3]).startswith("⚠ No donor given: no sample variable names a Donor")
    assert "so every group is treated as one donor — each tested against D1 ctrl" in _prose(lines)


def test_choosing_none_on_the_band_draws_no_missing_donor_warning(page, qtbot):
    _open_rare(qtbot, page)
    _choose(page, "_nw_rare_donor_cb", dn.OFF)
    qtbot.waitUntil(lambda: len([t for i in range(4) for t in _texts(page, f"nw-rare-comparison-head:{i}")]) == 4,
                    timeout=30000)
    assert "No donor given" not in _prose(_texts(page, "nw-rare-line"))
