"""Reaching an inspector panel from a test that has no window.

A panel made by ``review.core.inspector_panels.make_popover_button`` is a page
of the window's inspector, and a test that has a real context presses the real
button (``inspector_panels.press`` is what the click runs). A unit test of one
panel's *contents* — built against a stand-in context with no window to host an
inspector — uses these instead: :func:`content` is the widget the page shows,
:func:`show` runs what the inspector runs just before it shows the page, and
:func:`hide` what it runs when the page is left.

These step past the button on purpose, so they are not evidence that the
button works: each converted page has one test that presses the real button
(``tests/test_*_controls_open_in_the_inspector.py``).
"""

from __future__ import annotations

from review.core import inspector_panels


def content(button):
    return inspector_panels.panel_of(button).content


def show(button) -> None:
    panel = inspector_panels.panel_of(button)
    inspector_panels._run_on_open(panel)
    panel.shown = True


def hide(button) -> None:
    panel = inspector_panels.panel_of(button)
    panel.shown = False
    for fn in list(panel.on_close):
        fn()
