"""A window of its own — a dialog, a pop-out — in phone layout (``ui/phone_window.py``).

Reported from a phone: "Pop out windows in the app are not sized for mobile. For example crop montage, remote
manager, storage control and others. The buttons get clipped, window is impossible to close." Measured on a
432 × 936 px phone and the same phone held sideways, before the fix: the Storage dialog kept its 590 px height on
a 432 px-tall screen with Open folder, Clear and Close below its foot; Reset analysis put Reset and Cancel off the
screen sideways; Test detection — a ``QWidget`` with the ``Qt.Window`` flag rather than a dialog, which phone
layout did not look at at all — opened 984 px wide with nothing on screen that closes it.

Each test runs through the real path: a ``PhoneShell`` on a window, phone layout switched on, the dialog built
the way the app builds it and shown, and the shell's own sweep (what its 300 ms tick and a window gaining focus
run) finding it. The phone's screen is the screen's geometry as Python reads it (``QScreen.availableGeometry``),
the same reading the old clamp made, so each test measures the old code and the new on the same screen.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QRect, Qt  # noqa: E402
from PySide6.QtGui import QScreen  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from core import phone_layout  # noqa: E402
from ui import theme  # noqa: E402

UPRIGHT = QRect(0, 0, 432, 936)
SIDEWAYS = QRect(0, 0, 936, 432)

_WINDOWS: list = []


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def phone(qapp, monkeypatch):
    """Point the screen at a phone. Returns a setter, for the phone turning."""
    screen = {"rect": UPRIGHT}
    monkeypatch.setattr(QScreen, "availableGeometry", lambda _self: QRect(screen["rect"]))
    monkeypatch.setattr(QScreen, "geometry", lambda _self: QRect(screen["rect"]))
    before = qapp.styleSheet()
    yield lambda rect: screen.__setitem__("rect", rect)
    import shiboken6
    phone_layout.set_on(False)
    theme.apply_ui_scale(1.0)
    theme.set_touch_unit(None)
    while _WINDOWS:
        w = _WINDOWS.pop()
        if shiboken6.isValid(w):
            w.hide()
            shiboken6.delete(w)
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.setStyleSheet(before)


def _pump(qapp, n=5):
    for _ in range(n):
        qapp.processEvents()


def _shell(qapp):
    """The app's window and its shell, mounted the way ``allwell`` mounts it, in phone layout."""
    from ui.phone_shell import PhoneHandles, PhoneShell
    qapp.setStyleSheet(theme.qss())
    win = QWidget()
    _WINDOWS.append(win)
    QVBoxLayout(win)
    band = QWidget(win)
    QHBoxLayout(band)
    win.layout().addWidget(band)
    win.show()
    shell = PhoneShell(win, PhoneHandles(band=band))
    band.layout().addWidget(shell.desk_button)
    shell.set_on(True)
    _pump(qapp)
    shell._sweep_timer.stop()            # the test ticks the sweep itself
    return win, shell


def _open(qapp, shell, window):
    """Show *window* as the app does, then let the shell's sweep find it."""
    _WINDOWS.append(window)
    window.show()
    _pump(qapp)
    shell._sweep(force=False)
    _pump(qapp)


def _on_screen(button, screen: QRect) -> bool:
    """The whole button is drawn, inside its window and inside the screen."""
    win = button.window()
    local = QRect(button.mapTo(win, button.rect().topLeft()), button.size())
    glob = QRect(button.mapToGlobal(button.rect().topLeft()), button.size())
    return (button.isVisible() and win.rect().contains(local) and screen.contains(glob)
            and button.width() >= button.minimumSizeHint().width())


@pytest.fixture
def storage_home(tmp_path, monkeypatch):
    """A private home folder, so the Storage dialog surveys a small tree of its own."""
    from core import local_storage as ls
    from util import state_dir
    home = tmp_path / "home"
    (home / "system-temp").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(state_dir.STATE_DIR_ENV, raising=False)
    monkeypatch.setattr(state_dir, "is_windows", lambda: False)
    monkeypatch.setattr(ls.tempfile, "tempdir", str(home / "system-temp"))
    return home


def _storage(qapp, qtbot, shell, parent):
    """The title bar's Storage dialog, opened on the window and surveyed: its rows are what make it tall."""
    from ui.local_storage_view import StorageDialog
    dialog = StorageDialog(parent, dataset_dir=None)
    _open(qapp, shell, dialog)
    qtbot.waitUntil(lambda: not dialog._working, timeout=10_000)
    _pump(qapp)
    return dialog


def _foot_texts(window) -> list[str]:
    foot = window.findChild(QWidget, "PhoneWindowFoot")
    return [] if foot is None else [b.text() for b in foot.findChildren(QPushButton) if b.isVisible()]


def test_the_storage_dialog_on_a_phone_held_sideways_scrolls_with_its_buttons_at_its_foot(
        qapp, qtbot, phone, storage_home):
    """The Storage dialog (the title bar's storage control) is 590 px tall; a phone held sideways gives it 432.
    It used to keep its height, its foot and all three of its buttons off the bottom of the screen."""
    phone(SIDEWAYS)
    win, shell = _shell(qapp)
    dialog = _storage(qapp, qtbot, shell, win)

    own = [dialog._open_btn, dialog._clear_btn, *(b for b in dialog.findChildren(QPushButton) if b.text() == "Close")]
    for button in own:
        assert _on_screen(button, SIDEWAYS), f"{button.text()!r} is off the screen or cut"
    assert dialog.frameGeometry() == SIDEWAYS, "the dialog does not cover the phone's screen"
    foot = _foot_texts(dialog)
    assert (len(foot), foot[0], foot[-1]) == (3, "Open folder", "Close"), foot
    scroll = dialog.findChild(QWidget, "PhoneWindowScroll")
    assert scroll.verticalScrollBar().maximum() > 0, "the rows that do not fit do not scroll"
    assert scroll.viewport().property("_awDragScroll"), "the dialog's content does not scroll by a drag"


def test_a_pop_out_that_is_not_a_dialog_fits_the_phone_and_its_close_closes_it(qapp, phone):
    """Built as Test detection is (``analyze/nanowell_preview.py``): a ``QWidget`` with the ``Qt.Window`` flag,
    980 × 760, a row of controls wider than a phone and no button of its own that closes it. Phone layout only
    ever looked at dialogs, so it opened past the screen's right edge with its title bar's ✕ there too."""
    win, shell = _shell(qapp)
    tab = QWidget(win)
    popout = QWidget(tab, Qt.Window)
    popout.setWindowTitle("Test detection")
    popout.resize(980, 760)
    lay = QVBoxLayout(popout)
    row = QHBoxLayout()
    for text in ("Well", "Timepoint", "Run", "Nanowell outlines", "Zoom", "Fit to window"):
        row.addWidget(QPushButton(text, popout))
    lay.addLayout(row)
    lay.addWidget(QLabel("Choose a well and a timepoint, then Run.", popout), 1)
    _open(qapp, shell, popout)

    assert popout.frameGeometry() == UPRIGHT
    assert _foot_texts(popout) == ["Close ✕"]
    close = popout.findChild(QPushButton, "PhoneWindowClose")
    assert _on_screen(close, UPRIGHT)
    assert close.height() >= phone_layout.touch_px(shell.unit(), phone_layout.TOUCH_MIN_PT)
    # The controls row was turned top-to-bottom to fit the phone, so nothing is out to the side.
    body = popout.findChild(QWidget, "PhoneWindowBody")
    assert body.width() <= popout.findChild(QWidget, "PhoneWindowScroll").viewport().width()
    close.click()
    _pump(qapp)
    assert not popout.isVisible(), "Close ✕ did not close the window"


def test_the_window_s_own_cancel_at_its_foot_means_no_second_close(qapp, phone):
    """Reset analysis sideways: its Reset and Cancel were below the screen. Its button box is brought to the foot,
    and since Cancel is already the way out, no Close ✕ is added beside it."""
    from review.core.reset_scopes_view import ResetAnalysisDialog
    phone(SIDEWAYS)
    win, shell = _shell(qapp)
    dialog = ResetAnalysisDialog(win)
    _open(qapp, shell, dialog)

    assert dialog.frameGeometry() == SIDEWAYS
    assert _foot_texts(dialog) == ["Reset", "Cancel"]
    assert dialog.findChild(QPushButton, "PhoneWindowClose") is None
    for button in dialog.findChild(QWidget, "PhoneWindowFoot").findChildren(QPushButton):
        assert _on_screen(button, SIDEWAYS), button.text()


def test_a_dialog_that_grows_after_it_opened_stays_on_the_screen(qapp, qtbot, phone, storage_home):
    """A dialog that resizes itself later — a survey filling in, ``adjustSize`` — was clamped once, on Show, and
    then free to grow past the screen again."""
    win, shell = _shell(qapp)
    dialog = _storage(qapp, qtbot, shell, win)
    dialog.resize(1200, 1400)
    dialog.adjustSize()
    _pump(qapp)
    assert UPRIGHT.contains(dialog.frameGeometry())


def test_a_window_that_was_open_when_the_phone_turned_is_fitted_to_the_new_shape(qapp, qtbot, phone, storage_home):
    win, shell = _shell(qapp)
    dialog = _storage(qapp, qtbot, shell, win)
    assert dialog.frameGeometry() == UPRIGHT
    phone(SIDEWAYS)
    shell._on_geometry_changed(SIDEWAYS)        # what the screen's availableGeometryChanged calls
    _pump(qapp)
    assert dialog.frameGeometry() == SIDEWAYS


def test_a_message_box_keeps_its_own_size_and_buttons(qapp, phone):
    """Qt's own small dialogs size themselves and carry their buttons: kept on the screen, never re-formed."""
    win, shell = _shell(qapp)
    box = QMessageBox(QMessageBox.Question, "Reset crop", "Clear the crop region?",
                      QMessageBox.Yes | QMessageBox.No, win)
    box.setModal(False)
    _open(qapp, shell, box)
    assert box.findChild(QWidget, "PhoneWindowBody") is None
    assert box.width() < UPRIGHT.width() and UPRIGHT.contains(box.frameGeometry())


def test_leaving_phone_layout_gives_the_window_its_desk_form_back(qapp, qtbot, phone, storage_home):
    from ui.local_storage_view import StorageDialog
    win, shell = _shell(qapp)
    dialog = StorageDialog(win, dataset_dir=None)
    desk_layout = dialog.layout()
    clear = dialog._clear_btn
    dialog.resize(560, 600)
    desk_size = dialog.size()
    desk_max = dialog.maximumSize()
    _open(qapp, shell, dialog)
    qtbot.waitUntil(lambda: not dialog._working, timeout=10_000)
    assert dialog.layout() is not desk_layout and clear.parentWidget() is not dialog

    phone(QRect(0, 0, 1920, 1040))
    shell.set_on(False)
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    _pump(qapp)
    assert dialog.layout() is desk_layout
    assert clear.window() is dialog and desk_layout.indexOf(clear.parentWidget()) >= 0, \
        "the row of buttons did not go back into the dialog's layout"
    assert dialog.findChild(QWidget, "PhoneWindowBody") is None
    assert dialog.findChild(QWidget, "PhoneWindowFoot") is None
    assert dialog.maximumSize() == desk_max
    assert dialog.size() == desk_size
