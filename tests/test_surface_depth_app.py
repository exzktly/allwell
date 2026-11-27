"""The depth rule on the built application: no press opens a second surface.

``tests/test_surface_depth.py`` crawls fifteen pages built alone in a minimal
shell. Two pages cannot be crawled there (``CRAWLED_ON_THE_BUILT_APP`` says
what was measured), and phone layout is a property of the whole window, so
this file crawls on the real ``ReviewApp`` — full-app tests, the slow lane:

* Image Browser's Image Table and Batch Export, at the desk.

Phone layout is held elsewhere, on the real window: the Controls sheet is one
surface and a page inside it is depth one
(``tests/test_phone_layout_app.py::test_style_is_the_tab_beside_data_in_the_controls_sheet``),
and the every-page tour opens every page's Controls sheet on every phone screen
(``tests/test_every_page_renders.py``).
"""

from __future__ import annotations

import pytest

from tests import _surface_checks as surf
from tests.test_surface_depth import CRAWLED_ON_THE_BUILT_APP

pytestmark = pytest.mark.slow

#: Presses this crawl leaves alone: each would end the run it is part of.
_SKIP = ("Close", "Quit", "Generate", "Run", "Start", "Export", "Save", "Delete", "Remove",
         "Reset", "Clear", "Desktop layout")


def _skip(label: str) -> bool:
    return any(word in label for word in _SKIP)


def _report(found) -> str:
    return "\n  ".join(f"{label}: {n} open — {what}" for label, n, what in found)


def test_the_pages_the_minimal_shell_cannot_build_are_the_ones_crawled_here():
    assert set(CRAWLED_ON_THE_BUILT_APP) == {"Image Browser", "Batch Export"}


def test_no_press_on_the_image_table_opens_a_second_surface(loaded_app, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from review.features.image_browser.controller import show_image_browser_subtab

    surf.stub_blocking(monkeypatch)
    app = loaded_app
    assert show_image_browser_subtab(app._ctx, "Image Table")
    QApplication.processEvents()
    frame = app._ctx.widgets.get("_image_table_page").window()
    found = surf.crawl(app._ctx.widgets.get("_image_table_style_btn").parentWidget(), frame, skip=_skip)
    surf.close_transients(frame)
    assert len(found.pressed) >= 5, f"the crawl pressed only {found.pressed}, so it proved little"
    assert not found, "a press left more than one surface open:\n  " + _report(found)


def test_no_press_on_batch_export_opens_a_second_surface(loaded_app, monkeypatch):
    from PySide6.QtWidgets import QApplication

    surf.stub_blocking(monkeypatch)
    app = loaded_app
    assert app._notebook.setCurrentByName("Batch Export")
    app._drain_pending_centre_builders()
    QApplication.processEvents()
    page = app._notebook.currentWidget()
    found = surf.crawl(page, app, skip=_skip)
    surf.close_transients(app)
    assert len(found.pressed) >= 5, f"the crawl pressed only {found.pressed}, so it proved little"
    assert not found, "a press left more than one surface open:\n  " + _report(found)
