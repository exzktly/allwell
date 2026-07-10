"""``build_section_header`` titles (role="section") must be styled as headers.

Without a theme rule they fall back to plain body text and read as no header at
all — the bug the Cell Gating / Stats section titles hit.
"""
from __future__ import annotations


def test_section_role_is_styled_in_theme():
    import theme
    qss = theme.qss()
    assert 'role="section"' in qss, (
        "build_section_header sets role='section' expecting a theme rule; "
        "without one the section titles render as plain text."
    )
