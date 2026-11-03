"""The nanowell crops pop-out in phone layout — the "crop montage" of the report.

Reported from a phone: the crop montage, among other pop-out windows, "not sized for mobile … the buttons get
clipped, window is impossible to close". The pop-out is a 900 × 640 non-modal dialog holding a row of controls and
a plot card, with no button of its own that closes it: on a phone its only way out was the title bar's ✕.

Built here as the app builds it — the Nanowells page through ``build_nanowell_tab`` in the minimal shell, the
pop-out opened by ``controller.pop_out_crops`` — with a ``PhoneShell`` on the shell's window and phone layout on.
The phone's screen and the shell's sweep are ``tests/test_phone_windows.py``'s.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QPushButton, QWidget  # noqa: E402

from tests.test_nanowell_page import _page, _write_nanowell_dataset  # noqa: E402
from tests.test_phone_windows import _WINDOWS, UPRIGHT, _on_screen, _pump, phone, qapp  # noqa: E402,F401


def test_the_crops_pop_out_covers_the_phone_and_closes_from_its_foot(qtbot, tmp_path, qapp, phone):  # noqa: F811
    from review.features.nanowell import controller as nwc
    from ui import theme
    from ui.phone_shell import PhoneHandles, PhoneShell

    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path))
    ctx.state.selection.selected_wells = {"A01"}
    qapp.setStyleSheet(theme.qss())
    shell = PhoneShell(ctx.window, PhoneHandles())
    shell.desk_button.setParent(ctx.window)
    shell._rail_status.setParent(ctx.window)
    shell.set_on(True)
    _pump(qapp)
    shell._sweep_timer.stop()                    # the test ticks the sweep itself

    nwc.pop_out_crops(ctx)                       # the page's Pop out button
    popout = ctx.widgets.get("_nw_popout")
    _WINDOWS.append(popout)
    _pump(qapp)
    shell._sweep(force=False)                    # what the shell's 300 ms tick runs
    _pump(qapp)

    assert popout.isVisible()
    assert popout.frameGeometry() == UPRIGHT, "the pop-out does not cover the phone's screen"
    close = popout.findChild(QPushButton, "PhoneWindowClose")
    assert close is not None and _on_screen(close, UPRIGHT), "the pop-out has no Close on the screen"
    card = ctx.widgets.get("_nw_popout_card")
    assert popout.findChild(QWidget, "PhoneWindowBody").isAncestorOf(card), "the crops are not in what scrolls"
    close.click()
    _pump(qapp)
    assert not popout.isVisible()
