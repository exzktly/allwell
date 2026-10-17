"""A typed QC bound applies once, when the user is done with it.

Reported: "if I enter three characters it tries to update three times". Each
keystroke in a debris-gate box restarted a 350 ms timer, so typing "120" at a
normal pace applied 1, then 12, then 120 — each a recompute of the verdict over
every held cell, a save and a redraw of every plot — and Enter or a click
elsewhere afterwards applied the same value once more. A bound now applies on
``editingFinished`` only, and a value already in force applies nothing.

Driven through the shipped ``build_qc_gate_controls`` (``test_focus_gate._stub_row``)
with real key presses, counting the calls that reach ``set_qc_params``.
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
    real = tab_view.set_qc_params

    def _count(ctx, params):
        calls.append(params)
        return real(ctx, params)

    monkeypatch.setattr(tab_view, "set_qc_params", _count)
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
