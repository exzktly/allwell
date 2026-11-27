"""Each text role draws at its size, weight and ink (``ui/text_roles.py``).

Measured on a polished label under the application stylesheet, in every palette: the font Qt resolved and the
colour the label paints its text in — not the ``textRole`` property, which a label carries whether or not any
rule reads it.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QFont, QPalette

from ui import text_roles as tr
from ui import theme


@pytest.fixture(params=theme.available_themes())
def sheet(qapp, request):
    theme.apply_theme(request.param)
    qapp.setStyleSheet(theme.qss())
    yield theme
    theme.apply_theme("blue")
    qapp.setStyleSheet("")


def _drawn(label):
    label.ensurePolished()
    font = label.font()
    ink = label.palette().color(QPalette.WindowText).name().upper()
    return font.pixelSize(), font.weight(), ink


SEMIBOLD, REGULAR = QFont.Weight.DemiBold, QFont.Weight.Normal


@pytest.mark.parametrize("build, size, weight, ink", [
    (tr.page_title, "h2_size", SEMIBOLD, "text_primary"),
    (tr.section_heading, "h3_size", SEMIBOLD, "text_primary"),
    (tr.eyebrow, "small_size", SEMIBOLD, "text_secondary"),
    (tr.body, "body_size", REGULAR, "text_primary"),
    (tr.field_label, "small_size", REGULAR, "text_secondary"),
    (tr.help_text, "small_size", REGULAR, "text_secondary"),
    (tr.number, "small_size", REGULAR, "text_primary"),
])
def test_each_role_draws_at_its_size_weight_and_ink(sheet, build, size, weight, ink):
    got = _drawn(build("Channel"))
    want = (getattr(sheet.Typography, size), weight, getattr(sheet.Colors, ink).upper())
    assert got == want, f"{build.__name__}: drawn {got}, the role is {want}"


def test_the_four_sizes_are_the_only_sizes(sheet):
    sizes = {_drawn(build("x"))[0] for build in (tr.page_title, tr.section_heading, tr.eyebrow, tr.body,
                                                  tr.field_label, tr.help_text, tr.number)}
    assert sizes == {17, 15, 13, 12}


def test_an_eyebrow_is_capitals_and_tracked(sheet):
    label = tr.eyebrow("Cohort")
    label.ensurePolished()
    assert label.text() == "COHORT"
    assert label.font().letterSpacing() > 0


def test_a_number_is_set_in_the_monospace_face(sheet):
    label = tr.number("12 / 96")
    label.ensurePolished()
    assert label.font().family() == sheet.Typography.family_mono


def test_help_wraps():
    assert tr.help_text("A sentence that explains.").wordWrap()


def test_an_unset_value_reads_not_set_in_the_disabled_ink_and_a_value_does_not(sheet):
    label = tr.body("")
    tr.show_value(label, None)
    assert label.text() == tr.NOT_SET == "not set"
    assert _drawn(label)[2] == sheet.Colors.text_disabled.upper()
    tr.show_value(label, "StarDist")
    assert label.text() == "StarDist"
    assert _drawn(label)[2] == sheet.Colors.text_primary.upper()
    tr.show_value(label, "   ")
    assert label.text() == tr.NOT_SET
    assert _drawn(label)[2] == sheet.Colors.text_disabled.upper()
