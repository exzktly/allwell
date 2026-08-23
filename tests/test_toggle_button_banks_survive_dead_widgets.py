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


def _ctx(sem_bank, fov_bank, *, use_fov=False, use_sem=True):
    calls: list[str] = []

    class _Plot:
        use_fov_replicates = use_fov

        def toggle_sem(self):
            calls.append("toggle_sem")
            return use_sem

    return types.SimpleNamespace(
        state=types.SimpleNamespace(plot=_Plot()),
        runtime=types.SimpleNamespace(
            sem=types.SimpleNamespace(btns=sem_bank, observers=[]),
            fov=types.SimpleNamespace(btns=fov_bank, observers=[]),
        ),
        _calls=calls,
    )


@pytest.fixture()
def no_side_effects(monkeypatch):
    monkeypatch.setattr(plot_toggles, "invalidate_stats_cache", lambda _c: None)
    monkeypatch.setattr(plot_toggles, "redraw_app", lambda _c: _c._calls.append("redraw"))
    monkeypatch.setattr(plot_toggles, "is_tab_visible", lambda *_a, **_k: False)


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
