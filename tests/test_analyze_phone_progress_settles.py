"""Analyze's phone Progress view stops fitting once it fits.

The fit re-lays the run column (``AnalyzePhoneView._relayout`` invalidates and activates each layout), and the
invalidate posts the column a ``LayoutRequest`` that arrives after the fit's ``_fitting`` guard is down.
``_RefitOnLayout`` took that request for a re-layout and scheduled another fit, which posted another request, so
the page re-fitted on every event-loop pass, at the same size, for as long as Progress was on screen — 153 fits in
two idle seconds. (Review of the PRs merged 2026-10-25..2026-11-01, row R12.)

Behaviour, not appearance, so it is kept out of the UI roster that CI does not run.
"""

from __future__ import annotations

import time

from PySide6.QtWidgets import QApplication

from analyze.phone_view import PROGRESS, AnalyzePhoneView
from tests.test_analyze_phone_view import _pump, hosted, tab  # noqa: F401 — fixtures, used by name


def test_an_idle_progress_view_runs_no_further_fit(tab, hosted, monkeypatch):  # noqa: F811
    hosted(390, 480)
    view = tab._phone_view
    view.show_view(PROGRESS)
    for _ in range(10):
        _pump()
    fits = []
    real = AnalyzePhoneView._fit_progress
    monkeypatch.setattr(AnalyzePhoneView, "_fit_progress", lambda self: (fits.append(1), real(self))[1])
    end = time.monotonic() + 1.0
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    assert fits == [], f"{len(fits)} fits while nothing changed"


def test_a_resize_still_refits(tab, hosted):  # noqa: F811
    view = tab._phone_view
    plate = tab._dashboard._plate
    hosted(390, 480)
    view.show_view(PROGRESS)
    _pump()
    natural = plate.heightForWidth(tab._dashboard.plate_room_width())
    assert plate.maximumHeight() < natural
    hosted(390, 1400)
    assert plate.maximumHeight() == natural
