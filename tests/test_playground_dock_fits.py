"""The Playground's controls dock never squeezes its own text.

The dock is a fixed-width column that is taller than a laptop window leaves
for it. It used to be a bare layout, and a layout handed less than its
minimum height squeezes every row at once: on a short window every button,
combo, entry and wrapped note in the panel lost the bottom of its text. It is
hosted in a vertical scroll area now, so a short window scrolls instead.

The same panel was also wider than the dock: the Segment row's three buttons
and the "Correct up to" label beside its three-segment control each needed
more than the ~330px the dock leaves, so they were squeezed sideways too.

Both tests measure the real builder, dressed in the real application
stylesheet (applied to the test's own frame, not app-wide), because the
paddings that decide whether text fits live in that stylesheet.
"""

from __future__ import annotations

import types

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QWidget,
)


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def _build_dock(qapp, monkeypatch, height: int) -> QWidget:
    """Build the Playground into a frame of *height* px; return the frame."""
    from ui import theme
    from review.core.widget_registry import WidgetRegistry
    from review.features.image_browser.playground import tab_view as T
    from review.features.image_browser.playground.state import PlaygroundState

    # Neither needs a dataset to lay the panel out; both would need one to run.
    monkeypatch.setattr(T, "playground_repopulate_dropdowns", lambda *_a: None)
    monkeypatch.setattr(T, "render_playground_display", lambda *_a: None)
    win = types.SimpleNamespace(
        _app_state=types.SimpleNamespace(playground=PlaygroundState()))
    ctx = types.SimpleNamespace(window=win, widgets=WidgetRegistry(),
                                state=win._app_state)
    win._ctx = ctx

    frame = QWidget()
    QHBoxLayout(frame)
    frame.setStyleSheet(theme.qss())
    T.build_playground_tab(ctx, frame)
    frame.resize(1000, height)
    frame.show()
    qapp.processEvents()
    return frame


def _panel(frame: QWidget) -> QWidget:
    panel = frame.findChild(QWidget, "PropertyPanel")
    assert panel is not None, "the Playground controls panel was not built"
    return panel


def _inside_stepper(w: QWidget) -> bool:
    """The Stepper pins its own inner heights by design; it is not a row."""
    from widgets.stepper import Stepper
    p = w.parentWidget()
    while p is not None:
        if isinstance(p, Stepper):
            return True
        p = p.parentWidget()
    return False


@pytest.mark.parametrize("height", [500, 700])
def test_no_control_is_squeezed_below_its_height_on_a_short_window(
        qapp, monkeypatch, height):
    frame = _build_dock(qapp, monkeypatch, height)
    panel = _panel(frame)
    squeezed = []
    for kind in (QPushButton, QComboBox, QLineEdit, QCheckBox, QLabel):
        for w in panel.findChildren(kind):
            if not w.isVisible() or _inside_stepper(w):
                continue
            if isinstance(w, QLabel) and w.wordWrap():
                need = w.heightForWidth(w.width())
            else:
                need = w.sizeHint().height()
            if w.height() < need:
                text = w.currentText() if isinstance(w, QComboBox) else w.text()
                squeezed.append(f"{kind.__name__} {text[:30]!r}: "
                                f"{w.height()}px < {need}px")
    assert not squeezed, (
        f"at a {height}px window the Playground dock clipped:\n  "
        + "\n  ".join(squeezed))


def test_the_panel_fits_the_dock_width_with_the_scrollbar_showing(
        qapp, monkeypatch):
    frame = _build_dock(qapp, monkeypatch, 500)
    panel = _panel(frame)
    # The width available is the smaller of the panel's own and its host's:
    # the scroll viewport, once the vertical bar is up, clips a panel that
    # grew past it, and a fixed-width panel squeezes what it cannot hold.
    avail = min(panel.width(), panel.parentWidget().width())
    need = panel.minimumSizeHint().width()
    assert need <= avail, (
        f"the Playground panel needs {need}px but the dock leaves it {avail}px")
