"""Clearing the saved-plot gallery needs an answer, not the absence of one.

``_confirm_clear`` deletes every saved plot for the dataset and the PNGs behind
them, and it is not undoable. It used to wrap the confirmation dialog in a
``try`` that fell through to the clear — so a dialog that raised (the reason
given in the code was a headless run) was treated as a yes. "The question could
not be asked" and "the user said yes" are not the same answer.

``tests/conftest.py``'s autouse ``_no_modal_dialogs`` makes every
``QMessageBox.question`` raise, so the first test below reproduces the failure
without staging anything: it is the dialog-unavailable case, exactly.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from review.features.saved_plots import controller as saved_plots_controller  # noqa: E402
from review.features.saved_plots import tab_view  # noqa: E402


@pytest.fixture()
def cleared(monkeypatch):
    """Record calls to the destructive step instead of running it."""
    calls: list[str] = []
    monkeypatch.setattr(
        saved_plots_controller, "clear_saved_plots",
        lambda ctx: calls.append("cleared"),
    )
    return calls


def test_a_dialog_that_cannot_open_clears_nothing(cleared):
    tab_view._confirm_clear(SimpleNamespace())

    assert cleared == [], (
        "the gallery was wiped without a confirmation, because the "
        "confirmation dialog raised"
    )


def test_answering_no_clears_nothing(cleared, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.No, raising=False,
    )

    tab_view._confirm_clear(SimpleNamespace())

    assert cleared == []


def test_answering_yes_clears(cleared, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.Yes, raising=False,
    )

    tab_view._confirm_clear(SimpleNamespace())

    assert cleared == ["cleared"], "a confirmed clear must still clear"
