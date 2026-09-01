"""One closed export panel must not freeze the SEM / FOV toggles.

``ctx.runtime.sem.btns`` and ``ctx.runtime.fov.btns`` are append-only: the tab
builders and every batch-export panel append their toggle button and nothing
removes it. Once a panel is closed its button is a Python wrapper around a
deleted C++ object, and the first call on it raises ``RuntimeError: Internal
C++ object already deleted``. The button loops were unguarded, so that
exception propagated out of ``toggle_sem`` part-way through: buttons after the
dead one kept the previous label while the state had already flipped, and the
``redraw_app`` at the end of the function never ran, leaving the plots on the
old error band. The observer loops immediately beneath each button loop had
guarded exactly this case all along.
"""

from __future__ import annotations

import types

import pytest

from well_viewer.core import plot_toggles  # noqa: E402


class _DeadButton:
    """Every call raises, the way a wrapper around a deleted QWidget does."""

    def __getattr__(self, name):
        def _boom(*_a, **_k):
            raise RuntimeError("Internal C++ object (QToolButton) already deleted.")
        return _boom


class _LiveButton:
    def __init__(self):
        self.text = None
        self.props: dict = {}
        self.min_width = None
        self.enabled = None

    def objectName(self):
        return "Toggle"

    def setEnabled(self, v):
        self.enabled = v

    def setText(self, t):
        self.text = t

    def setToolTip(self, _t):
        pass

    def setProperty(self, k, v):
        self.props[k] = v

    def setMinimumWidth(self, w):
        self.min_width = w

    def font(self):
        from PySide6.QtGui import QFont
        return QFont()

    def style(self):
        return types.SimpleNamespace(unpolish=lambda _w: None, polish=lambda _w: None)


class _DeadObserver:
    """An observer closing over a deleted widget."""

    def __call__(self, _value):
        raise RuntimeError("Internal C++ object (QToolButton) already deleted.")


def _ctx(sem_bank, fov_bank, *, use_fov=False, use_sem=True,
         sem_observers=None, fov_observers=None):
    calls: list[str] = []

    class _Plot:
        use_fov_replicates = use_fov

        def toggle_sem(self):
            calls.append("toggle_sem")
            return use_sem

    return types.SimpleNamespace(
        state=types.SimpleNamespace(plot=_Plot()),
        runtime=types.SimpleNamespace(
            sem=types.SimpleNamespace(btns=sem_bank,
                                      observers=sem_observers if sem_observers is not None else []),
            fov=types.SimpleNamespace(btns=fov_bank,
                                      observers=fov_observers if fov_observers is not None else []),
        ),
        _calls=calls,
    )


@pytest.fixture()
def no_side_effects(monkeypatch):
    """Stub what the toggles do *besides* walking their button banks.

    The repaint is one call now — ``plot_orchestrator.redraw_every_scope``,
    which draws the shared scopes through ``redraw_app`` and fans the rest out
    through the scope registry — so it is stubbed here as a whole rather than by
    naming each of the redraws it makes. The tests below still assert that it ran:
    the failure this file exists for is a dead widget aborting the toggle
    part-way, which shows up as the repaint never happening.
    """
    monkeypatch.setattr(plot_toggles, "invalidate_stats_cache", lambda _c: None)
    monkeypatch.setattr(plot_toggles, "redraw_every_scope",
                        lambda _c: _c._calls.append("redraw"))


def test_a_dead_button_does_not_stop_the_live_ones_from_updating(no_side_effects):
    live_before, live_after = _LiveButton(), _LiveButton()
    bank = [live_before, _DeadButton(), live_after]
    ctx = _ctx(bank, [])

    plot_toggles.toggle_sem(ctx)

    assert live_before.text == "SEM"
    assert live_after.text == "SEM", (
        "the button behind the dead one never got the new label"
    )
    assert "redraw" in ctx._calls, "toggle_sem returned before redrawing"


def test_the_dead_button_is_dropped_from_the_bank(no_side_effects):
    live = _LiveButton()
    bank = [_DeadButton(), live, _DeadButton()]
    ctx = _ctx(bank, [])

    plot_toggles.toggle_sem(ctx)

    assert bank == [live], (
        "the append-only bank keeps growing with wrappers around deleted "
        f"widgets: {bank}"
    )


def test_the_fov_bank_is_guarded_the_same_way(no_side_effects, qapp):
    live_before, live_after = _LiveButton(), _LiveButton()
    bank = [live_before, _DeadButton(), live_after]
    ctx = _ctx([], bank, use_fov=True)

    plot_toggles.refresh_fov_btn_state(ctx)

    assert live_before.text == "FOV ✓"
    assert live_after.text == "FOV ✓"
    assert bank == [live_before, live_after]


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ── the observer banks grow the same way ────────────────────────────────────

def test_a_dead_observer_is_dropped_and_the_live_ones_still_fire(no_side_effects):
    """The button banks were pruned and these were not — the same unbounded
    growth in the list immediately below the one that was fixed. A guarded but
    unpruned observer raises on every toggle for the rest of the session."""
    seen: list[bool] = []
    observers = [seen.append, _DeadObserver(), seen.append]
    ctx = _ctx([], [], sem_observers=observers)

    plot_toggles.toggle_sem(ctx)

    assert seen == [True, True], "the observer behind the dead one never fired"
    assert observers == [seen.append, seen.append] or len(observers) == 2, (
        f"the dead observer is still in the bank: {observers}"
    )


def test_an_observer_that_raises_for_its_own_reasons_is_kept(no_side_effects):
    """Only a RuntimeError means a dead widget. Dropping anything else would
    silently unsubscribe a live listener over its own bug."""
    calls: list[int] = []

    def _buggy(_value):
        calls.append(1)
        raise ValueError("my own bug")

    observers = [_buggy]
    ctx = _ctx([], [], sem_observers=observers)

    plot_toggles.toggle_sem(ctx)
    plot_toggles.toggle_sem(ctx)

    assert len(calls) == 2, "a buggy observer was unsubscribed"
    assert observers == [_buggy]


def test_the_fov_observers_are_pruned_the_same_way(no_side_effects, qapp):
    seen: list[bool] = []
    observers = [_DeadObserver(), seen.append]
    ctx = _ctx([], [], use_fov=True, fov_observers=observers)

    plot_toggles.refresh_fov_btn_state(ctx)

    assert seen == [True]
    assert len(observers) == 1
