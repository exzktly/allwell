"""A restore that half-succeeds says so, on the status line and in the log.

``view_state.restore`` applies fourteen independent blocks and deliberately
lets one fail without costing the others — a saved view naming a channel this
dataset does not have should still bring back the wells, the draw order and the
tab. The cost of that design is that a restore can half-succeed, and it used to
do so in silence: every handler logged at DEBUG and the function returned
``None``, so nothing downstream could tell a complete restore from one that
applied four blocks of fourteen. The user asked Saved Plots to bring a figure
back, got a different figure, and was told nothing.

These press the boundary rather than the bookkeeping. What matters is not that
a list was appended to — it is that ``ctx.notify.set_status`` is *called*, with
the missing settings named in it. A test that read the internal list would pass
just as happily with the status-line call missing, which is the shape that lets
a correct value be computed and never reach the screen.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from well_viewer.persistence import view_state


class _RefusesUseSem:
    """A plot-state stand-in whose ``use_sem`` write raises.

    Assigning any other field works, so this fails exactly one block and lets
    the test show the rest of the restore carrying on past it.
    """

    def __init__(self) -> None:
        object.__setattr__(self, "_store", {})

    def __setattr__(self, name, value):
        if name == "use_sem":
            raise RuntimeError("no error-bar mode on this dataset")
        object.__setattr__(self, name, value)


class _Notify:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def set_status(self, message: str) -> None:
        self.messages.append(message)


def _make_ctx(plot=None):
    notify = _Notify()
    activated: list[str] = []
    # A real registry, empty: the metric restore syncs whatever combos exist,
    # and with none registered that is a no-op rather than a failure. A bare
    # namespace here would make the metric block raise and quietly turn every
    # test below into a test of the wrong gap.
    from well_viewer.core.widget_registry import WidgetRegistry

    ctx = SimpleNamespace(
        notify=notify,
        activated=activated,
        widgets=WidgetRegistry(SimpleNamespace()),
        state=SimpleNamespace(
            dataset=SimpleNamespace(well_paths={}),
            selection=SimpleNamespace(selections=[], active_solo_wells=set()),
            plot=plot if plot is not None else SimpleNamespace(),
            channel=SimpleNamespace(
                active_channel="", active_metric="", sync_val_col=lambda: None,
            ),
            export_prefs=SimpleNamespace(style_prefs=None, axis_prefs={}),
        ),
        runtime=SimpleNamespace(
            view_state=SimpleNamespace(absent_wells=set(), absent_solo=set(), pending={}),
        ),
        coordinator=SimpleNamespace(refresh_sidebar_map=lambda: None),
    )
    return ctx


@pytest.fixture(autouse=True)
def _no_tab_activation(monkeypatch):
    """``_activate_tab`` reaches the real centre-view stack; stub the seam."""
    monkeypatch.setattr(view_state, "_activate_tab", lambda ctx, name: None)
    monkeypatch.setattr(view_state, "_has_home", lambda ctx: False)


def test_a_block_that_fails_is_named_on_the_status_line():
    ctx = _make_ctx(plot=_RefusesUseSem())

    failed = view_state.restore(ctx, {"use_sem": True, "active_centre_tab": "Home"})

    # The boundary first: what the user can see is the whole point, and a test
    # that checked the return value first would report the bookkeeping as the
    # defect when the real one is that nothing reached the screen.
    assert ctx.notify.messages, "the user was told nothing about the missing setting"
    assert "error-bar mode" in ctx.notify.messages[-1]
    assert "1 setting" in ctx.notify.messages[-1]
    assert failed == ["error-bar mode"]


def test_the_blocks_after_a_failure_still_apply():
    """The whole reason the handlers exist — one gap must not cost the rest."""
    ctx = _make_ctx(plot=_RefusesUseSem())

    failed = view_state.restore(ctx, {
        "use_sem": True,                 # raises
        "active_metric": "smfish_count",  # must still land
        "focus_timepoint": "not-a-number",  # raises too
    })

    assert ctx.state.channel.active_metric == "smfish_count"
    assert failed == ["error-bar mode", "focus timepoint"]
    assert "2 setting" in ctx.notify.messages[-1]


def test_a_clean_restore_says_nothing():
    """A message on every restore would train the user to ignore it."""
    ctx = _make_ctx()

    failed = view_state.restore(ctx, {"use_sem": True, "active_metric": "mean_intensity"})

    assert failed == []
    assert ctx.notify.messages == []


def test_the_gap_reaches_the_app_log_too(caplog):
    """The status line is transient; the log drawer is the record."""
    ctx = _make_ctx(plot=_RefusesUseSem())

    with caplog.at_level(logging.WARNING, logger="well_viewer"):
        view_state.restore(ctx, {"use_sem": True})

    assert any("error-bar mode" in r.getMessage() for r in caplog.records), (
        f"nothing at WARNING named the gap: {[r.getMessage() for r in caplog.records]}"
    )


def test_the_prefer_home_exit_reports_too():
    """``restore`` returns early on the dataset-arrival path; that exit was the
    one most likely to skip the report, since it does not fall through the end
    of the function."""
    ctx = _make_ctx(plot=_RefusesUseSem())
    view_state._has_home = lambda _ctx: True
    try:
        failed = view_state.restore(ctx, {"use_sem": True}, prefer_home=True)
    finally:
        del view_state._has_home

    assert failed == ["error-bar mode"]
    assert ctx.notify.messages and "error-bar mode" in ctx.notify.messages[-1]
