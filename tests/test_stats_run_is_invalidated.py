"""A stored comparison lives exactly as long as it describes the Statistics tab.

Two halves of one rule, both of which were broken and both of which surface on
the Bar plot's significance brackets:

**Changing the test must invalidate the run.** ``stats_on_test_change`` cleared
the result *text* and left ``StatsState.pairwise`` in place. The Bar renderer's
context gate compares that stored run against the **bars**, not against the
Statistics tab, so an invalidated run kept drawing its asterisks for as long as
the bar plot happened to show the column it was run on. In Split View the two
are on screen together: an empty result panel beside a plot carrying asterisks
attributed to it.

**A repopulate is not a change.** ``stats_update_tp_menu`` reset the Timepoint
combo to the earliest entry every time it ran — and it runs on a channel switch
and on every arrival at the Statistics tab. The user's chosen timepoint reverted
without a word, the next test ran at a timepoint they had not picked, and the
brackets, gated on the tested timepoint matching the bars, drew nothing.

Fast lane: real ``QComboBox`` widgets (so the signal semantics are the app's,
not a stand-in's) over a stub state.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QComboBox, QPlainTextEdit  # noqa: E402

from well_viewer.features.stats.controller import (  # noqa: E402
    stats_on_test_change,
    stats_update_tp_menu,
)
from well_viewer.features.stats.state import StatsState  # noqa: E402

pytestmark = pytest.mark.qt

RUN = [{"a": "CTRL", "b": "DRUG", "p": 0.01, "p_adj": 0.01, "stat": "mean",
        "val_col": "gfp_mean_intensity", "target_t": 24.0}]


class _Widgets:
    def __init__(self, mapping):
        self._m = mapping

    def get_or_none(self, name):
        return self._m.get(name)

    def has(self, name):
        return self._m.get(name) is not None


def _ctx(widgets, *, show_significance=True, pairwise=None):
    """A stub ctx carrying the pieces these two functions actually touch."""
    stats = StatsState()
    stats.pairwise = list(pairwise if pairwise is not None else RUN)
    emitted: list[object] = []
    state = SimpleNamespace(
        stats=stats,
        plot=SimpleNamespace(show_significance=show_significance),
        channel=SimpleNamespace(active_channel="gfp", smfish_channels=[],
                                ratio_metrics=[], shape_features=[]),
        dataset=SimpleNamespace(well_paths={}),
    )
    state.feature_slice = lambda name: getattr(state, name, None)
    ctx = SimpleNamespace(
        state=state,
        widgets=_Widgets(widgets),
        bus=SimpleNamespace(plot_options_changed=SimpleNamespace(
            emit=lambda s: emitted.append(s))),
    )
    return ctx, emitted


# ── changing the test invalidates the run ───────────────────────────────────

def test_changing_the_test_clears_the_stored_comparison(qtbot):
    txt = QPlainTextEdit(); qtbot.addWidget(txt)
    txt.setPlainText("old results")
    ctx, _emitted = _ctx({"_stats_result_text": txt})

    stats_on_test_change(ctx)

    assert ctx.state.stats.pairwise == [], (
        "the results text was cleared but the structured run survived to keep "
        "drawing brackets")
    assert txt.toPlainText() == ""


def test_changing_the_test_repaints_the_bar_plot(qtbot):
    """Clearing the records is not enough on its own: in Split View the Bar
    plot is already on screen, so without a redraw the stale asterisks stay
    painted until something else happens to touch the figure."""
    ctx, emitted = _ctx({})
    stats_on_test_change(ctx)
    assert emitted, "nothing asked the Bar plot to drop the stale brackets"


def test_no_repaint_when_significance_is_off(qtbot):
    """A redraw on every combo touch, for a figure drawing no brackets, is cost
    with nothing to show for it."""
    ctx, emitted = _ctx({}, show_significance=False)
    stats_on_test_change(ctx)
    assert ctx.state.stats.pairwise == []
    assert emitted == []


def test_no_repaint_when_there_was_nothing_to_invalidate(qtbot):
    ctx, emitted = _ctx({}, pairwise=[])
    stats_on_test_change(ctx)
    assert emitted == []


# ── a repopulate is not a change ────────────────────────────────────────────

def _tp_menu_ctx(qtbot, monkeypatch, timepoints, *, current=None):
    """A ctx whose Timepoint combo is a real widget over a stubbed dataset."""
    import pandas as pd

    from well_viewer.features.stats import controller as ctrl

    cb = QComboBox(); qtbot.addWidget(cb)
    ch_cb = QComboBox(); qtbot.addWidget(ch_cb)
    ctx, _emitted = _ctx({"_stats_tp_cb": cb, "_stats_channel_cb": ch_cb})
    ctx.state.dataset.well_paths = {"A01": None}
    ctx.state.channel.fluor_channels = ["gfp"]

    frame = pd.DataFrame({"timepoint_hours": list(timepoints)})
    monkeypatch.setattr(ctrl, "get_rows", lambda _c, _l: frame, raising=False)
    import well_viewer.data.data_loading as _dl
    monkeypatch.setattr(_dl, "get_rows", lambda _c, _l: frame, raising=False)

    stats_update_tp_menu(ctx)
    if current is not None:
        cb.setCurrentText(current)
    return ctx, cb


def test_a_repopulate_keeps_the_users_timepoint(qtbot, monkeypatch):
    ctx, cb = _tp_menu_ctx(qtbot, monkeypatch, [0.0, 24.0, 48.0], current="24")
    assert cb.currentText() == "24"

    stats_update_tp_menu(ctx)      # a channel switch, or arriving on the tab

    assert cb.currentText() == "24", (
        "the repopulate silently moved the tested timepoint back to the "
        "earliest one")


def test_a_repopulate_does_not_count_as_the_user_changing_the_test(qtbot, monkeypatch):
    """The combo now invalidates the run when it changes, so a programmatic
    reseed that fired the handler would throw away results the user had just
    computed — a fix that reintroduces the bug it was fixing."""
    ctx, cb = _tp_menu_ctx(qtbot, monkeypatch, [0.0, 24.0, 48.0], current="24")
    fired: list[int] = []
    cb.currentIndexChanged.connect(lambda _i: fired.append(1))
    ctx.state.stats.pairwise = list(RUN)

    stats_update_tp_menu(ctx)

    assert fired == []
    assert ctx.state.stats.pairwise == list(RUN)


def test_a_timepoint_that_no_longer_exists_falls_back_to_the_first(qtbot, monkeypatch):
    """The fallback is still needed — it is only meant for the case it was
    written for, a selection the new dataset does not offer."""
    ctx, cb = _tp_menu_ctx(qtbot, monkeypatch, [0.0, 24.0, 48.0], current="48")
    assert cb.currentText() == "48"

    import pandas as pd
    import well_viewer.data.data_loading as _dl
    from well_viewer.features.stats import controller as ctrl
    frame = pd.DataFrame({"timepoint_hours": [0.0, 12.0]})
    monkeypatch.setattr(ctrl, "get_rows", lambda _c, _l: frame, raising=False)
    monkeypatch.setattr(_dl, "get_rows", lambda _c, _l: frame, raising=False)

    stats_update_tp_menu(ctx)

    assert cb.currentText() == "0"
