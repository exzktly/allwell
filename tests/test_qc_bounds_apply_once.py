"""A typed QC bound applies once, when the user is done with it.

Reported: "if I enter three characters it tries to update three times". Each
keystroke in a debris-gate box restarted a 350 ms timer, so typing "120" at a
normal pace applied 1, then 12, then 120 — each a recompute of the verdict over
every held cell, a save and a redraw of every plot — and Enter or a click
elsewhere afterwards applied the same value once more. A bound now applies on
``editingFinished`` only, and a value already in force applies nothing.

Driven through the shipped ``build_qc_gate_controls`` (``test_focus_gate._stub_row``)
with real key presses, counting the calls that reach
``set_qc_params_with_progress`` — the route a panel edit takes, which runs the
recompute behind a progress popup.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from review.features.qc import tab_view
from tests.test_focus_gate import _frame, _params, _stub_row


@pytest.fixture
def applied(monkeypatch):
    calls = []
    real = tab_view.set_qc_params_with_progress

    def _count(ctx, params, **kwargs):
        calls.append(params)
        return real(ctx, params, **kwargs)

    monkeypatch.setattr(tab_view, "set_qc_params_with_progress", _count)
    return calls


def test_typing_a_bound_applies_it_once_on_enter(qtbot, applied):
    app = _stub_row(qtbot, {"A01": _frame([0.3, 0.02])}, _params(enabled=True))
    box = app._qc_gate_widgets["min_area"]
    win = box.window()
    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    QTest.qWaitForWindowActive(win, 1000)
    box.setFocus()
    qtbot.waitUntil(box.hasFocus, timeout=2000)

    for ch in "120":
        QTest.keyClicks(box, ch)
        qtbot.wait(450)                 # slower than the old 350 ms debounce
    assert applied == [], "a keystroke applied a half-typed bound"

    QTest.keyClick(box, Qt.Key_Return)
    assert len(applied) == 1 and applied[0].min_area == pytest.approx(120.0)
    assert app._app_state.qc.params.min_area == pytest.approx(120.0)

    box.editingFinished.emit()          # Enter, then focus moves on
    assert len(applied) == 1, "an unchanged value was applied a second time"


def _typed(qtbot, app, key="min_area", text="120"):
    """Type *text* into a focused bound box, as a user does, without Enter."""
    box = app._qc_gate_widgets[key]
    win = box.window()
    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    QTest.qWaitForWindowActive(win, 1000)
    box.setFocus()
    qtbot.waitUntil(box.hasFocus, timeout=2000)
    QTest.keyClicks(box, text)
    return box


def test_a_bound_typed_then_left_by_a_click_that_takes_no_focus_still_applies(qtbot, applied):
    """Review of #1004 (M1): clicking a plot canvas, a tool button, a tab bar or
    a chip moves no focus, so ``editingFinished`` never comes and the box went
    on showing 120 while the old bound stayed in force — the control and the
    state disagreeing with nothing to say so. A pause in typing applies it."""
    from PySide6.QtWidgets import QToolButton

    app = _stub_row(qtbot, {"A01": _frame([0.3, 0.02])}, _params(enabled=True))
    box = _typed(qtbot, app)
    tool = QToolButton(box.window())
    tool.setFocusPolicy(Qt.NoFocus)
    tool.show()
    QTest.mouseClick(tool, Qt.LeftButton)
    assert box.hasFocus(), "fixture: the click must not move focus, or this measures Enter"

    qtbot.wait(2000)                    # well past the typing pause
    assert len(applied) == 1, "the typed bound was never applied: the box and the state disagree"
    assert applied[0].min_area == pytest.approx(120.0)
    assert app._app_state.qc.params.min_area == pytest.approx(120.0)

    box.editingFinished.emit()          # focus finally moves on
    assert len(applied) == 1, "the debounced value was applied a second time"


def test_enter_cancels_the_pending_apply(qtbot, applied):
    """Enter applies at once; the typing pause that follows must not apply
    the same value again."""
    app = _stub_row(qtbot, {"A01": _frame([0.3, 0.02])}, _params(enabled=True))
    box = _typed(qtbot, app)
    QTest.keyClick(box, Qt.Key_Return)
    qtbot.wait(1300)
    assert len(applied) == 1


def test_a_bound_typed_as_the_window_closes_is_saved(qtbot):
    """Review of #1004 (M2), through the close sequence the app runs
    (``allwell._save_review_before_close``). Closing a window moves no focus
    until it hides, so ``editingFinished`` came *after* the drain: the bound
    was applied during teardown and its save armed on a timer that never
    fired. What the drain must see is the typed value already in state and
    queued."""
    from types import SimpleNamespace

    from allwell import _save_review_before_close

    app = _stub_row(qtbot, {"A01": _frame([0.3, 0.02])}, _params(enabled=True))
    ctx = app._ctx
    seen = []

    class _Persistence:
        def save(self, name):
            seen.append(("save", name))

        def schedule_save(self, name):
            seen.append(("schedule_save", name))

        def flush_all(self):
            params = ctx.state.qc.params
            seen.append(("flush_all", params.min_area if params else None))

    ctx.persistence = _Persistence()
    _typed(qtbot, app)

    _save_review_before_close(SimpleNamespace(_persistence=ctx.persistence, _ctx=ctx))

    assert ("schedule_save", "qc_params") in seen, seen
    assert seen[-1] == ("flush_all", pytest.approx(120.0)), seen
    assert seen.index(("schedule_save", "qc_params")) < len(seen) - 1
