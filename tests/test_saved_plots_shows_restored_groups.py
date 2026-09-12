"""A restored plot's comparison groups appear in the Statistics group editor.

Reported: restoring a saved graph with statistics left the editor's group list
empty while the groups were live in every other way — the Statistics tab ran its
test on them, and "Load groups from Home" warned about overwriting the four that
were not on screen.

``_restore_stats`` wrote ``stats.groups`` and ``stats.pairwise`` straight to the
state slice. Every *other* route that changes those groups — add, delete, rename,
clear, sync-from-home — funnels through ``stats_refresh_group_list`` /
``stats_refresh_map``, which are what actually paint the list and the plate. This
one went round them, so the state was right and the editor showed none of it.
(The editor was on the rail when this was reported and is on the Statistics page
now; the defect and the funnel it is about are the same either way.)

``_activate_statistics`` is not a backstop either: it re-seeds only when the group
list is *empty*, which is precisely what the restore has just made false, so
arriving at the tab repaints nothing.

The tests count the rendered group cards rather than reading ``stats.groups``.
Reading the state would have passed against the bug — the state was never the
problem.
"""

from __future__ import annotations

import pytest

from review.domain.batch_models import BarGroup
from review.features.saved_plots.controller import _restore_stats

pytestmark = pytest.mark.qt

_VAL_COL = "gfp_mean_intensity"
_TEST = "t-test (Welch)"


def _saved_groups():
    """Two solo-well groups of two wells each.

    The report described four groups, but the count is not what is under test —
    "the rows on the rail match the groups in the state" is. Two of two wells is
    what the mini dataset (A01, A02, B01, B02) can express *non-degenerately*:
    one well per group yields a single sample, ``compute_pairwise`` declines it,
    and with no comparisons ``_capture_stats_config`` returns ``None`` — so the
    restore would no-op and the test would pass against the bug it is for.
    """
    return [BarGroup("G1", solo_wells=["A01", "A02"]),
            BarGroup("G2", solo_wells=["B01", "B02"])]


def _stats_cfg(app, groups):
    """The saved ``stats`` block for *groups*, as ``capture_params`` writes it."""
    from review.features.saved_plots.collect import _capture_stats_config
    from review.features.stats.controller import compute_pairwise, threshold_for_val_col

    app._app_state.stats.groups = list(groups)
    app._app_state.stats.pairwise = compute_pairwise(
        app._ctx, groups=groups, target_t=0.0, val_col=_VAL_COL,
        statistic="mean",
        threshold=float(threshold_for_val_col(app._ctx, _VAL_COL)), test=_TEST)
    cfg = _capture_stats_config(app._ctx)
    # Clear the live state so the restore has to put it back — otherwise the
    # test passes on state that was never removed.
    app._app_state.stats.groups = []
    app._app_state.stats.pairwise = []
    return cfg


def _group_cards(app):
    """The group rows actually rendered in the sidebar list.

    ``StatsGroupCard`` is the object name ``stats_refresh_group_list`` gives each
    row it draws, so counting them is counting what the user sees — which is the
    thing that was wrong while ``stats.groups`` was right.
    """
    from PySide6.QtWidgets import QFrame
    inner = app._ctx.widgets.get_or_none("_stats_grp_inner")
    if inner is None:
        return []
    return [w for w in inner.findChildren(QFrame)
            if w.objectName() == "StatsGroupCard"]


@pytest.mark.slow
def test_restoring_a_saved_plot_shows_its_groups_in_the_sidebar(loaded_app):
    app = loaded_app
    app._notebook.setCurrentByName("Statistics")
    groups = _saved_groups()
    cfg = _stats_cfg(app, groups)
    assert cfg is not None, (
        "the fixture produced no stats config — the restore would no-op and "
        "this test would pass against the bug")
    assert _group_cards(app) == [], "the fixture did not clear the list first"

    _restore_stats(app._ctx, cfg)

    cards = _group_cards(app)
    assert len(cards) == len(groups), (
        f"{len(cards)} group rows on the rail, expected {len(groups)} — the "
        f"restore put the groups in the state without painting them")


@pytest.mark.slow
def test_the_restored_rows_name_the_restored_groups(loaded_app):
    """Not just the right count: a stale list of the right length would pass a
    count-only assertion."""
    from PySide6.QtWidgets import QLabel

    app = loaded_app
    app._notebook.setCurrentByName("Statistics")
    groups = _saved_groups()
    cfg = _stats_cfg(app, groups)

    _restore_stats(app._ctx, cfg)

    shown = set()
    for card in _group_cards(app):
        for lbl in card.findChildren(QLabel):
            shown.add(lbl.text())
    for grp in groups:
        assert grp.name in shown, f"{grp.name!r} is in the state but not on the rail"


@pytest.mark.slow
def test_the_state_is_still_restored(loaded_app):
    """The half that already worked, pinned so the refresh cannot be added by
    breaking it."""
    app = loaded_app
    app._notebook.setCurrentByName("Statistics")
    groups = _saved_groups()
    cfg = _stats_cfg(app, groups)

    _restore_stats(app._ctx, cfg)

    assert [g.name for g in app._app_state.stats.groups] == [g.name for g in groups]
    assert app._app_state.stats.pairwise, "the re-run produced no comparisons"


@pytest.mark.slow
def test_a_restore_with_no_stats_block_leaves_the_list_alone(loaded_app):
    """Backward compatibility: a plot saved before the stats block existed must
    not blank a list the user is already working with."""
    app = loaded_app
    app._notebook.setCurrentByName("Statistics")
    _restore_stats(app._ctx, _stats_cfg(app, _saved_groups()))
    before = len(_group_cards(app))
    assert before == 2

    _restore_stats(app._ctx, None)

    assert len(_group_cards(app)) == before
