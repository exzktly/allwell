"""The Statistics group card's count elides instead of widening the card.

``review/features/stats/group_card_view.ElidingLabel``: whole text whenever the
layout has the room, cut with "…" — and the whole text in its tooltip — when it
does not, so a card in a list narrower than itself does not scroll the list
sideways (``tests/test_stats_group_editor_layout.py`` holds that on the page).
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QWidget


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


TEXT = "  (12 sets, 3 solo wells)"


def _host(label_cls_text_width: int):
    from review.features.stats.group_card_view import ElidingLabel
    host = QWidget()
    lay = QHBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(QLabel("Ctrl"))
    label = ElidingLabel(TEXT)
    lay.addWidget(label)
    host.resize(label_cls_text_width, 40)
    return host, label


def _settle(qapp):
    for _ in range(5):
        qapp.processEvents()


def test_with_room_it_reads_whole_and_without_it_is_cut_with_its_tooltip_whole(qapp):
    from review.features.stats.group_card_view import ElidingLabel
    probe = ElidingLabel(TEXT)
    whole = probe.fontMetrics().horizontalAdvance(TEXT)
    host, label = _host(whole + 200)
    try:
        host.show()
        _settle(qapp)
        assert label.text() == TEXT
        assert label.sizeHint().width() >= whole
        assert label.minimumSizeHint().width() < whole // 3     # the card's floor does not hold the text
        host.resize(label.fontMetrics().horizontalAdvance("Ctrl") + whole // 2, 40)
        _settle(qapp)
        assert label.text() != TEXT and label.text().endswith("…")
        assert label.fontMetrics().horizontalAdvance(label.text()) <= label.contentsRect().width()
        assert label.toolTip() == TEXT and label.full_text() == TEXT
        host.resize(whole + 200, 40)
        _settle(qapp)
        assert label.text() == TEXT                  # widening gives the whole text back
    finally:
        host.deleteLater()
        probe.deleteLater()
