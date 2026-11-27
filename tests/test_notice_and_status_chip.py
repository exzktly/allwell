"""The notice (``widgets/notice.py``) and the status chip (``widgets/status_chip.py``) draw their severity.

Measured on rendered widgets under the application stylesheet, in every palette: the colour of the notice's left
rule and of its dot, read off a grab, for each severity; the chip's dot for each tone; the lead set in bold; at
most one button. Not the ``severity`` property, which a widget carries whether or not a rule reads it.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

from ui import theme
from widgets.notice import SEVERITIES, Notice
from widgets.status_chip import TONES, StatusChip


@pytest.fixture(params=theme.available_themes())
def sheet(qapp, request):
    theme.apply_theme(request.param)
    qapp.setStyleSheet(theme.qss())
    yield theme
    theme.apply_theme("blue")
    qapp.setStyleSheet("")


def _show(qtbot, widget, width=520):
    host = QWidget()
    qtbot.addWidget(host)
    lay = QVBoxLayout(host)
    lay.addWidget(widget)
    host.resize(width, 120)
    host.show()
    for _ in range(3):
        QApplication.processEvents()
    return host


def _centre_of(widget, img, ref):
    p = widget.mapTo(ref, widget.rect().center())
    return img.pixelColor(p.x(), p.y()).name().upper()


NOTICE_INK = {"information": "text_secondary", "attention": "warn", "failure": "danger"}
CHIP_INK = {"neutral": "text_secondary", "running": "accent", "done": "success", "attention": "warn",
            "failure": "danger"}


@pytest.mark.parametrize("severity", SEVERITIES)
def test_the_rule_and_the_dot_take_the_severitys_colour(qtbot, sheet, severity):
    notice = Notice("No completeness record.", "This dataset was produced before the check existed.",
                    severity=severity, action_text="Check now")
    host = _show(qtbot, notice)
    img = host.grab().toImage()
    want = getattr(sheet.Colors, NOTICE_INK[severity]).upper()
    rule = notice.mapTo(host, notice.rect().topLeft())
    assert img.pixelColor(rule.x() + 1, rule.y() + notice.height() // 2).name().upper() == want
    assert _centre_of(notice._dot, img, host) == want


def test_changing_the_severity_repaints_it(qtbot, sheet):
    notice = Notice("NIR is not one of the channels above.", severity="information")
    host = _show(qtbot, notice)
    notice.setSeverity("attention")
    QApplication.processEvents()
    assert _centre_of(notice._dot, host.grab().toImage(), host) == sheet.Colors.warn.upper()


def test_the_lead_is_bold_and_the_text_follows_it_escaped(qtbot):
    notice = Notice("Lead <one>.", "Body & more.")
    assert notice._label.text() == "<b>Lead &lt;one&gt;.</b> Body &amp; more."
    assert notice.lead() == "Lead <one>." and notice.text() == "Body & more."


def test_a_notice_has_at_most_one_button_and_closes_with_a_symbol(qtbot, sheet):
    notice = Notice("Editing a queued job.", action_text="Save changes", close_tip="Cancel")
    host = _show(qtbot, notice)  # held: the host owns the notice
    buttons = notice.findChildren(QPushButton)
    assert [b.text() for b in buttons] == ["Save changes"]
    close = notice.close_button()
    assert close.text() == "" and close.toolTip() == "Cancel"
    fired = []
    notice.closed.connect(lambda: fired.append("closed"))
    notice.actionTriggered.connect(lambda: fired.append("action"))
    close.click()
    buttons[0].click()
    assert fired == ["closed", "action"]
    host.close()


def test_an_unknown_severity_is_refused():
    with pytest.raises(ValueError, match="not a severity"):
        Notice("x", severity="warning")


@pytest.mark.parametrize("tone", TONES)
def test_each_chip_tone_draws_its_dot(qtbot, sheet, tone):
    chip = StatusChip("Running" if tone == "running" else tone.capitalize(), tone=tone)
    host = _show(qtbot, chip, width=200)
    assert _centre_of(chip._dot, host.grab().toImage(), host) == getattr(sheet.Colors, CHIP_INK[tone]).upper()
    assert chip.text()


def test_a_chip_is_a_neutral_pill(qtbot, sheet):
    chip = StatusChip("4 wells")
    host = _show(qtbot, chip, width=200)
    img = host.grab().toImage()
    p = chip.mapTo(host, chip.rect().center())
    left = chip.mapTo(host, chip._label.geometry().topRight())
    assert img.pixelColor(left.x() + 2, p.y()).name().upper() == sheet.Colors.control.upper()
