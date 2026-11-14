"""Distribution's *Pool replicates*: with replicate sets on the plate, off draws each well of a set as its own series.

A plate click in group mode shows a whole replicate set, and the figure pooled every well of it into one series —
so one replicate could not be seen on its own. These drive the real tab builder and the real checkbox, and read
the series the figure drew, not the state behind them (CLAUDE.md §7.6, §7.8 rule 3).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def _drawn_labels(fig) -> list[str]:
    """The legend entries of the overlay figure: one per series drawn."""
    out = []
    for ax in fig.axes:
        leg = ax.get_legend()
        if leg is not None:
            out += [t.get_text() for t in leg.get_texts()]
    return out


@pytest.fixture
def tab(qtbot, mini_dataset):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from domain.selections_format import make_selection
    from review.core.ui_helpers import PLOTTING_BAND_KEY
    from review.features.distribution.tab_view import build_distribution_tab
    from tests._minimal_shell import load_dataset, minimal_shell
    from widgets.control_band import ControlBand

    shell, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = shell, shell.state, shell.features
    load_dataset(shell, mini_dataset)
    frame = QWidget()
    QHBoxLayout(frame)
    window.setCentralWidget(frame)
    shell.widgets.register(PLOTTING_BAND_KEY, ControlBand(frame))
    build_distribution_tab(shell, frame)
    wells = sorted(shell.state.dataset.well_paths)[:2]
    shell.state.selection.selections = [make_selection(name="Treated", wells=wells)]
    shell.widgets.get("_distribution_mode_cb").setCurrentText("Histogram")
    return shell, wells


def test_unticking_draws_each_well_of_the_set_and_ticking_pools_them_again(tab):
    from review.features.distribution import controller as C
    from review.domain.selections_model import well_display_label

    ctx, wells = tab
    cb = ctx.widgets.get("_distribution_pool_cb")
    fig = ctx.widgets.get("_distribution_fig")
    C.redraw_distribution(ctx)
    assert cb.isEnabled() and cb.isChecked()
    pooled = _drawn_labels(fig)
    assert len(pooled) == 1 and pooled[0].startswith("Treated (n="), pooled
    n_pooled = int(pooled[0].split("n=")[1].rstrip(")"))

    cb.setChecked(False)                      # the user's click: stateChanged → redraw
    assert ctx.state.distribution.pool_replicates is False
    split = _drawn_labels(fig)
    assert [lbl.split(" (n=")[0] for lbl in split] == [well_display_label(ctx, w) for w in wells]
    ns = [int(lbl.split("n=")[1].rstrip(")")) for lbl in split]
    assert all(n > 0 for n in ns) and sum(ns) == n_pooled, (split, pooled)

    cb.setChecked(True)
    assert _drawn_labels(fig) == pooled


def test_the_toggle_is_disabled_without_a_replicate_set(tab):
    from review.features.distribution import controller as C

    ctx, wells = tab
    ctx.state.selection.selections = []
    ctx.state.selection.selected_wells = set(wells)
    C.redraw_distribution(ctx)
    assert not ctx.widgets.get("_distribution_pool_cb").isEnabled()


def test_the_view_state_keeps_it():
    from types import SimpleNamespace

    from review.features.distribution.state import DistributionState
    from review.persistence import view_state as V

    dist = DistributionState(pool_replicates=False)
    ctx = SimpleNamespace(state=SimpleNamespace(feature_slice=lambda name: dist),
                          widgets=SimpleNamespace(get_or_none=lambda name: None))
    snap = V._snapshot_distribution(ctx)
    assert snap["pool_replicates"] is False
    dist.pool_replicates = True
    V._apply_distribution(ctx, snap)
    assert dist.pool_replicates is False


def test_the_nanowell_members_split_the_same_way(tab):
    """Under Observations: Nanowells the tab's members come from ``observation_groups``; unpooled, a set's wells."""
    from review.data.observations import observation_groups
    from review.domain.selections_model import well_display_label

    ctx, wells = tab
    pooled = observation_groups(ctx)
    assert [(lbl, ws) for lbl, ws, _c in pooled] == [("Treated", tuple(wells))]
    split = observation_groups(ctx, pool=False)
    assert [(lbl, ws) for lbl, ws, _c in split] == [(well_display_label(ctx, w), (w,)) for w in wells]
