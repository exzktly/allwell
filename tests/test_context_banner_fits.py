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
    return host, banner


@pytest.mark.parametrize("width", [320, 600, 1100])
def test_the_whole_sentence_is_shown(qtbot, width):
    _host_widget, banner = _host(qtbot, _LONG, width)
    label = banner._label
    assert label.height() >= label.heightForWidth(label.width()), (
        f"at {width} px the sentence needs {label.heightForWidth(label.width())} px "
        f"and has {label.height()}")


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
