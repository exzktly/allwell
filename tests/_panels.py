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


# ── multi-select pages ────────────────────────────────────────────────────────

def picker(button, key: str | None = None):
    """The ``MultiSelect`` *button* opens (``inspector_pages.open_multi_select``)
    — the one page it has, or the page named *key*."""
    from review.core import inspector_pages
    mine = inspector_pages._pickers.get(button, {})
    if key is None:
        assert len(mine) == 1, f"the button has {len(mine)} multi-select pages: {sorted(mine)}"
        return next(iter(mine.values()))
    return mine[key]


def settle(page) -> None:
    """Run a settled change now — what happens when the pause after the last
    tick elapses, or the page is left."""
    from review.core import inspector_pages
    inspector_pages._flush(page)
