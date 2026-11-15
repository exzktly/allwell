"""A folded Analyze section's summary never runs past the label that shows it.

Found by the slow lane's every-page render test on the desk screen: ``analyze/form: QLabel#Muted: text 98px wide in
86px: 'Otsu (whole data…'``. The summary is elided to the room its header leaves, and it was elided once, to the
label's full width rather than its contents rectangle, before the header had settled — so the cut was made for a
wider label than the one that ended up on screen.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QVBoxLayout, QWidget  # noqa: E402

from analyze.form_chrome import AnalyzeFormChromeMixin  # noqa: E402

SUMMARY = "Otsu (whole dataset) · every fluorescence channel · recorded with the run"


class _Form(QWidget, AnalyzeFormChromeMixin):
    def __init__(self) -> None:
        super().__init__()
        QVBoxLayout(self)
        self._section(self, "Auto-threshold", collapsible=True, collapsed=True, summary=lambda: SUMMARY)
        self.refresh_section_summaries()


def _label(form):
    return form._section_summaries["Auto-threshold"][1]


def _overrun(label) -> int:
    """How far the drawn text runs past the room the label gives it, in pixels (0 when it fits)."""
    text_px = label.fontMetrics().horizontalAdvance(label.text())
    return max(0, text_px - label.contentsRect().width())


@pytest.mark.parametrize("widths", [(900, 420), (420, 900), (900, 300, 520)])
def test_the_summary_is_cut_again_for_every_width_the_header_takes(qtbot, widths):
    form = _Form()
    qtbot.addWidget(form)
    form.show()
    for width in widths:
        form.resize(width, 200)
        qtbot.waitUntil(lambda w=width: form.width() == w, timeout=3000)
        qtbot.wait(50)
        label = _label(form)
        assert label.isVisible() and label.toolTip() == SUMMARY
        assert _overrun(label) == 0, (width, label.text(), label.contentsRect().width())
    # ... and it is the whole sentence again once there is room for it, not the cut made for a narrow header.
    form.resize(1600, 200)
    qtbot.waitUntil(lambda: form.width() == 1600, timeout=3000)
    qtbot.wait(50)
    assert _label(form).text() == SUMMARY
