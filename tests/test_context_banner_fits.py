"""A ContextBanner shows its whole sentence at any width.

Beside its two buttons in the Analyze form's narrow column, the sentence got
about 60 px and needed 255 px of height in a strip that stayed one line tall,
so most of it was cut off — the Job Queue's next-step setup says which job it
reads from there, and that was the part lost.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

_LONG = ("Setting up a queued Analyze job that runs after “Concatenate — "
         "plate_day1 + plate_day2 → plate_combined”. Inputs marked ⧗ are read "
         "from those jobs' outputs when it runs. Set every other option on the "
         "page, then add it to the queue.")


def _host(qtbot, text, width):
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from widgets.context_banner import ContextBanner

    host = QWidget()
    qtbot.addWidget(host)
    layout = QVBoxLayout(host)
    banner = ContextBanner(text, "Add to queue")
    layout.addWidget(banner)
    layout.addStretch(1)
    host.resize(width, 600)
    host.show()
    qtbot.waitExposed(host)
    # The host lays the strip out from its height-for-width on the next pass
    # of the event loop, as it does in the app.
    qtbot.wait(20)
    return host, banner


@pytest.mark.parametrize("width", [320, 600, 1100])
def test_the_whole_sentence_is_shown(qtbot, width):
    """Once the layout has settled — up to two seconds of event-loop passes,
    not one look at 20 ms. CI once found the label a line short at 320 px
    (56 px for 84) and the next run did not; a layout still settling and one
    that never does read the same at a single instant, so the wait tells them
    apart and the message below says what the strip looked like if it never
    settles."""
    _host_widget, banner = _host(qtbot, _LONG, width)
    label = banner._label

    def fits() -> bool:
        return label.height() >= label.heightForWidth(label.width())

    for _ in range(100):           # 100 × 20 ms; the message below says what never settled
        if fits():
            break
        qtbot.wait(20)
    assert fits(), (
        f"at {width} px the sentence needs {label.heightForWidth(label.width())} px and has "
        f"{label.height()} (label {label.width()} px wide, banner {banner.width()}x{banner.height()}, "
        f"stacked={banner.is_stacked()}, font {label.font().family()} {label.font().pointSizeF()}pt)")


def test_a_narrow_host_puts_the_buttons_under_the_sentence(qtbot):
    _host_widget, banner = _host(qtbot, _LONG, 320)
    assert banner.is_stacked()
    assert banner._buttons.geometry().top() >= banner._label.geometry().bottom()


def test_a_wide_host_keeps_one_row(qtbot):
    host, banner = _host(qtbot, "Editing queued job “Subset — a → b”.", 1100)
    assert not banner.is_stacked()
    assert banner.height() == banner._buttons.sizeHint().height() + 16  # Spacing.sm × 2


def test_narrowing_and_widening_again_restores_the_wide_height(qtbot):
    host, banner = _host(qtbot, _LONG, 1100)
    wide = banner.height()
    host.resize(320, 600)
    qtbot.wait(10)
    assert banner.height() > wide
    host.resize(1100, 600)
    qtbot.wait(10)
    assert banner.height() == wide


def test_a_font_that_changes_after_layout_still_fits(qtbot):
    """What CI showed: the sentence measured with one font and drawn with
    another (fonts can settle after the first layout on a machine without the
    bundled faces), so a height fixed at resize time was short of the text."""
    from PySide6.QtGui import QFont

    host, banner = _host(qtbot, _LONG, 600)
    font = QFont(banner.font())
    font.setPointSizeF(font.pointSizeF() * 1.6)
    banner.setFont(font)
    qtbot.wait(20)
    label = banner._label
    assert label.height() >= label.heightForWidth(label.width()), (
        f"needs {label.heightForWidth(label.width())} px, has {label.height()}")
