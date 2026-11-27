"""``build_section_header`` titles draw as section headings.

Without a rule for them they fell back to plain body text and read as no header at all — the bug the Cell Gating
and Statistics section titles hit. The title carries the section-heading text role (``ui/text_roles.py``); this
builds a header under the application sheet and measures the title's polished font, not the property.
"""
from __future__ import annotations

from PySide6.QtGui import QFont


def test_a_section_header_title_draws_as_a_section_heading(qapp):
    from PySide6.QtWidgets import QWidget

    from review.core.ui_helpers import build_section_header
    from ui import theme

    qapp.setStyleSheet(theme.qss())
    try:
        host = QWidget()
        build_section_header(host, "Cell inclusion")
        title = next(w for w in host.findChildren(QWidget) if getattr(w, "text", None) and w.text() == "Cell inclusion")
        title.ensurePolished()
        assert title.font().pixelSize() == theme.Typography.h3_size
        assert title.font().weight() == QFont.Weight.DemiBold
    finally:
        qapp.setStyleSheet("")
