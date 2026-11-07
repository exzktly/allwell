"""A toast stays inside the window it is shown over.

``tests/test_every_page_renders.py`` found the toast for unusable FOV labels with its right edge 162px past the
phone window on the nanowell dataset: the toast was placed at the desk window's corner during the load, and the
window then shrank into phone layout under it, while a toast is a window of its own that nothing lays out with its
host. Upright, the same message (~670px on one line) was also wider than the 432px phone window altogether.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from widgets.toast import Toast

_LONG = ("3 well(s) have unusable FOV labels (A01, A02, B01) — per-field statistics are unavailable for them; "
         "re-export the plate with field labels to recover them.")


def _host(qapp, width: int) -> QWidget:
    host = QWidget()
    host.resize(width, 600)
    host.move(0, 0)
    host.show()
    qapp.processEvents()
    return host


def _span(toast: Toast, host: QWidget) -> tuple[int, int]:
    """The toast's left and right edge, in the host's coordinates."""
    left = toast.geometry().left() - host.mapToGlobal(host.rect().topLeft()).x()
    return left, left + toast.width()


def test_a_toast_follows_its_window_when_the_window_shrinks(qapp):
    host = _host(qapp, 1600)
    toast = Toast(host, "Saved")
    toast.popup()
    qapp.processEvents()
    assert _span(toast, host)[1] > 1000, "premise: shown at the wide window's right-hand corner"

    host.resize(432, 600)
    qapp.processEvents()
    left, right = _span(toast, host)
    assert 0 <= left and right <= 432, f"the toast spans {left}..{right} of a window now 432px wide"
    toast.close()
    host.close()


def test_a_message_wider_than_its_window_wraps_inside_it(qapp):
    host = _host(qapp, 432)
    toast = Toast(host, _LONG)
    toast.popup()
    qapp.processEvents()
    left, right = _span(toast, host)
    assert 0 <= left and right <= 432, f"the toast spans {left}..{right} of a 432px window"
    assert toast._label.wordWrap(), "the message should wrap rather than run past the window"
    assert toast._label.height() >= 2 * toast._label.fontMetrics().height(), "the wrapped message is cut to one line"
    toast.close()
    host.close()


def test_a_long_message_on_a_wide_window_is_placed_for_its_shown_width(qapp):
    """Sized with ``adjustSize``, a toast already shown on a wide window came out 533px wide (two thirds of the
    800px offscreen screen) when its host was resized, was placed for 533px, and was then widened by its own layout
    to the message's 671px — past the host's right edge by the difference. Measured in the page-render test."""
    host = _host(qapp, 1600)
    toast = Toast(host, _LONG)
    toast.popup()
    qapp.processEvents()
    assert toast.width() > 600, "premise: a message wider than two thirds of the 800px offscreen screen"
    for width in (1500, 1550):
        host.resize(width, 600)
        qapp.processEvents()
        left, right = _span(toast, host)
        assert 0 <= left and right <= width, f"the toast spans {left}..{right} of a {width}px window"
    assert not toast._label.wordWrap(), "a message that fits the window stays on one line"
    toast.close()
    host.close()
