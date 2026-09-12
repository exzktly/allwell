"""A stored comparison survives the Statistics tab moving on.

``StatsState.runs`` is keyed by ``(val_col, target_t, stat)``, so a run stored
under GFP/t=24/mean is a true comparison of GFP at t=24 whatever the tab's
selectors have since been moved to, and the Bar plot only ever draws it over
bars of that column and timepoint.

That inverts what this file used to assert. With a single stored run, a
comparison that no longer matched the tab's selectors was one nobody could
account for — an empty results panel beside a plot still carrying its asterisks
— and clearing it on every config change was the only way to keep the two halves
honest. Keyed, clearing would make the feature impossible: running a *second*
comparison requires changing a selector, so a handler that emptied the store on
every change would delete the first run on the way to the second.

What still clears is the results *text*. That panel shows one run, and the run
it shows is the one the current configuration produced.

The repopulate rules below are unchanged and still load-bearing: a programmatic
reseed of the Timepoint combo must not be mistaken for the user editing the
test, and must not silently move the tested timepoint.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QComboBox, QPlainTextEdit  # noqa: E402

from review.features.stats.controller import (  # noqa: E402
    stats_on_test_change,
    stats_update_tp_menu,
)
from review.features.stats.state import StatsState  # noqa: E402

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

def test_changing_the_test_keeps_the_stored_comparison(qtbot):
    """The inversion. A run under one context is not invalidated by the tab
    being pointed at another — it is simply not the run the tab is about to
    produce."""
    txt = QPlainTextEdit(); qtbot.addWidget(txt)
    txt.setPlainText("old results")
    ctx, _emitted = _ctx({"_stats_result_text": txt})

    stats_on_test_change(ctx)

    assert ctx.state.stats.pairwise == RUN, (
        "the stored comparison was discarded; a second comparison can then "
        "never coexist with the first, because running one means changing a "
        "selector")


def test_changing_the_test_still_clears_the_results_text(qtbot):
    """The half that does clear. The panel shows one run — the one the current
    configuration produced — so it must not keep showing the previous one."""
    txt = QPlainTextEdit(); qtbot.addWidget(txt)
    txt.setPlainText("old results")
    ctx, _emitted = _ctx({"_stats_result_text": txt})

    stats_on_test_change(ctx)

    assert txt.toPlainText() == ""


def test_changing_the_test_does_not_repaint_the_bar_plot(qtbot):
    """Nothing the Bar plot draws has changed, so nothing asks it to redraw.
    A repaint on every combo touch would be cost with no visible result."""
    ctx, emitted = _ctx({})
    stats_on_test_change(ctx)
    assert emitted == []


def test_two_comparisons_coexist(qtbot):
    """The point of the whole change, at the level of the store."""
    ctx, _emitted = _ctx({})
    second = [{**RUN[0], "val_col": "cy5_mean_intensity"}]
    ctx.state.stats.record_run(second)

    assert len(ctx.state.stats.runs) == 2
    assert ctx.state.stats.runs_matching("gfp_mean_intensity", [24.0])
    assert ctx.state.stats.runs_matching("cy5_mean_intensity", [24.0])


def test_a_rounded_timepoint_re_runs_the_same_comparison(qtbot):
    """A combo formats timepoints with ``:.4g``, so the timepoint that comes
    back out of the widget is a few parts in 1e5 off the float that went in.
    Under exact-match replacement the second run of one comparison keyed as a
    *different* comparison — and since the lookup matches within tolerance,
    both then matched and every bracket was drawn twice."""
    ctx, _emitted = _ctx({})
    ctx.state.stats.runs.clear()
    ctx.state.stats.record_run([{**RUN[0], "target_t": 2 / 3}])
    # The same timepoint, after a round trip through the combo's text.
    ctx.state.stats.record_run([{**RUN[0], "target_t": float(f"{2 / 3:.4g}")}])

    assert len(ctx.state.stats.runs) == 1
    assert len(ctx.state.stats.runs_matching("gfp_mean_intensity", [2 / 3])) == 1


def test_two_timepoints_apart_are_still_two_comparisons(qtbot):
    """The counterpart: collapsing on tolerance must not collapse timepoints a
    plate can actually tell apart."""
    ctx, _emitted = _ctx({})
    ctx.state.stats.runs.clear()
    ctx.state.stats.record_run([{**RUN[0], "target_t": 24.0}])
    ctx.state.stats.record_run([{**RUN[0], "target_t": 48.0}])

    assert len(ctx.state.stats.runs) == 2
    assert len(ctx.state.stats.runs_matching("gfp_mean_intensity", [24.0])) == 1


# ── a repopulate is not a change ────────────────────────────────────────────

def _tp_menu_ctx(qtbot, monkeypatch, timepoints, *, current=None):
    """A ctx whose Timepoint combo is a real widget over a stubbed dataset."""
    import pandas as pd

    from review.features.stats import controller as ctrl

    cb = QComboBox(); qtbot.addWidget(cb)
    ch_cb = QComboBox(); qtbot.addWidget(ch_cb)
    ctx, _emitted = _ctx({"_stats_tp_cb": cb, "_stats_channel_cb": ch_cb})
    ctx.state.dataset.well_paths = {"A01": None}
    ctx.state.channel.fluor_channels = ["gfp"]

    frame = pd.DataFrame({"timepoint_hours": list(timepoints)})
    monkeypatch.setattr(ctrl, "get_rows", lambda _c, _l: frame, raising=False)
    import review.data.data_loading as _dl
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
    import review.data.data_loading as _dl
    from review.features.stats import controller as ctrl
    frame = pd.DataFrame({"timepoint_hours": [0.0, 12.0]})
    monkeypatch.setattr(ctrl, "get_rows", lambda _c, _l: frame, raising=False)
    monkeypatch.setattr(_dl, "get_rows", lambda _c, _l: frame, raising=False)

    stats_update_tp_menu(ctx)

    assert cb.currentText() == "0"


# ── the controls, pressed ───────────────────────────────────────────────────
#
# Everything above drives the two functions directly. That is one step past the
# bug CLAUDE.md §7.6 describes: a control that exists is not a control that
# works, and the Timepoint combo shipped for a long time with no handler at all
# while a test of the handler would have passed. So this section builds the real
# results panel and moves each real widget the way a click does.

def _built_panel(qtbot, monkeypatch, timepoints=(0.0, 24.0, 48.0)):
    """The real Statistics control band, over a stub state."""
    import pandas as pd
    from PySide6.QtWidgets import QWidget

    import review.data.data_loading as _dl
    from review.features.stats import controller as ctrl
    from review.features.stats.tab_view import build_stats_results_panel

    frame = pd.DataFrame({"timepoint_hours": list(timepoints)})
    monkeypatch.setattr(ctrl, "get_rows", lambda _c, _l: frame, raising=False)
    monkeypatch.setattr(_dl, "get_rows", lambda _c, _l: frame, raising=False)

    class _Reg:
        """Registers like the real one, and stamps the legacy ``app._stats_*``
        names onto ``ctx.window`` the way production's registry does — several
        stats controllers still read the widgets that way, and a registry that
        skipped it would send them down a branch the app never takes."""

        def __init__(self, window):
            self._m = {}
            self._window = window

        def register(self, name, w):
            self._m[name] = w
            setattr(self._window, name, w)
            return w

        def get_or_none(self, name):
            return self._m.get(name)

        def get(self, name):
            return self._m[name]

        def has(self, name):
            return name in self._m

    # ``set_active_metric`` propagates the Property to global channel state and
    # has its own tests; what is under test here is the invalidation that runs
    # before it, so it is recorded rather than executed.
    monkeypatch.setattr(ctrl, "set_active_metric", lambda _c, _m: None,
                        raising=False)

    stats = StatsState()
    emitted: list[object] = []
    window = SimpleNamespace()
    state = SimpleNamespace(
        stats=stats,
        plot=SimpleNamespace(show_significance=True, use_fov_replicates=False),
        channel=SimpleNamespace(active_channel="gfp",
                                fluor_channels=["gfp", "cy5"],
                                smfish_channels=[], ratio_metrics=[],
                                shape_features=[], active_metric="mean_intensity"),
        dataset=SimpleNamespace(well_paths={"A01": None}),
    )
    state.feature_slice = lambda name: getattr(state, name, None)
    ctx = SimpleNamespace(
        state=state,
        widgets=_Reg(window),
        bus=SimpleNamespace(plot_options_changed=SimpleNamespace(
            emit=lambda s: emitted.append(s))),
        # The Property combo reaches its handler through this namespace in
        # production; pointing it at the real function is the difference between
        # testing the wiring and testing a stub of it.
        runtime=SimpleNamespace(stats=SimpleNamespace(
            property_change=lambda: ctrl.on_stats_property_change(ctx),
            run=lambda: None)),
        notify=SimpleNamespace(set_status=lambda *_a, **_k: None),
        window=window,
    )
    parent = QWidget()
    qtbot.addWidget(parent)
    build_stats_results_panel(ctx, parent)
    # Keep the parent alive on the ctx the test holds. Every combo below is a
    # child of it, so letting it fall out of scope has Qt delete the C++ objects
    # underneath the Python wrappers and every access raises — a fixture that
    # destroys the control it means to press.
    ctx._parent = parent
    return ctx, emitted


@pytest.mark.parametrize("widget,value", [
    ("_stats_tp_cb", "48"),
    ("_stats_channel_cb", "CY5"),
    ("_stats_statistic_cb", "Fraction above threshold"),
    ("_stats_test_cb", "Mann-Whitney U"),
    ("_stats_property_cb", "Total Intensity"),
])
def test_moving_a_scope_control_keeps_the_stored_run(qtbot, monkeypatch,
                                                     widget, value):
    """Each control that decides what gets tested, driven as a user drives it.

    They still all reach their handler — that wiring is what the Timepoint combo
    was missing entirely — but the handler no longer empties the store. Pressing
    these is how you set up a *second* comparison, so a handler that cleared
    would delete the first one on the way.
    """
    ctx, _emitted = _built_panel(qtbot, monkeypatch)
    cb = ctx.widgets.get(widget)
    ctx.state.stats.pairwise = list(RUN)

    cb.setCurrentText(value)

    assert cb.currentText() == value, "the fixture did not move the control"
    assert ctx.state.stats.pairwise == RUN, (
        f"moving {widget} discarded the previous comparison; setting up a "
        f"second one would destroy the first")


def test_moving_a_scope_control_does_not_repaint_the_bar_plot(qtbot, monkeypatch):
    ctx, emitted = _built_panel(qtbot, monkeypatch)
    ctx.state.stats.pairwise = list(RUN)
    ctx.widgets.get("_stats_tp_cb").setCurrentText("48")
    assert emitted == []


def test_building_the_panel_does_not_invalidate_anything(qtbot, monkeypatch):
    """The build seeds all five combos, which is a repopulate and not a change.

    ``build_stats_results_panel`` ends by calling ``stats_update_tp_menu``; if
    that counted as the user editing the test, simply arriving on the tab would
    throw away a run.
    """
    ctx, emitted = _built_panel(qtbot, monkeypatch)
    ctx.state.stats.pairwise = list(RUN)
    from review.features.stats.controller import stats_update_tp_menu
    emitted.clear()
    stats_update_tp_menu(ctx)
    assert ctx.state.stats.pairwise == list(RUN)
    assert emitted == []
