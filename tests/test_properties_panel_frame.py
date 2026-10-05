"""The Properties panel's frame: the buttons around the sections, and the grid.

Row C2 of ``_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`` splits
``ExportStyleSidebar._build_ui`` into one builder per section. Mutating the
split against every test that builds the panel left five changes green, all
outside the sections the other Properties tests read:

* the header not built — no way to close the panel;
* the footer not built — no Reset, no Save Preset, no Export…;
* the label column losing its fixed width, which is what stops the longest
  label pushing the controls past the panel's fixed width;
* the default gap between the controls in one row changed;
* the stretch under the last section removed, so the sections spread down a
  tall panel instead of sitting at its top.

The buttons are pressed, not looked for: each must reach the method behind it.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QGridLayout, QPushButton, QSpacerItem

from tests.test_properties_hides_what_it_cannot_do import _sidebar
from ui import theme
from widgets.collapsible_section import CollapsibleSection

pytestmark = pytest.mark.qt


def _button(sidebar, text: str):
    found = [b for b in sidebar.findChildren(QPushButton) if b.text() == text]
    assert len(found) == 1, f"expected one {text!r} button, found {len(found)}"
    return found[0]


@pytest.mark.parametrize(("text", "method"), [
    ("Reset", "_reset_defaults"),
    ("Save Preset", "_save_preset"),
    ("Export…", "_export"),
])
def test_each_footer_button_reaches_its_action(qtbot, monkeypatch, text, method):
    from review.features.export.sidebar_view import ExportStyleSidebar

    calls: list[str] = []
    monkeypatch.setattr(ExportStyleSidebar, method,
                        lambda self, _m=method: calls.append(_m))
    _app, sb = _sidebar(qtbot, monkeypatch, kind="_line_fig")
    _button(sb, text).click()
    assert calls == [method]


def test_the_header_close_button_closes_the_panel(qtbot, monkeypatch):
    from review.features.export.sidebar_view import ExportStyleSidebar
    from widgets.icon_button import IconButton

    calls: list[str] = []
    monkeypatch.setattr(ExportStyleSidebar, "_close_dock",
                        lambda self: calls.append("close"))
    _app, sb = _sidebar(qtbot, monkeypatch, kind="_line_fig")
    closers = [b for b in sb.findChildren(IconButton)
               if b.toolTip() == "Hide this panel"]
    assert len(closers) == 1
    closers[0].click()
    assert calls == ["close"]


def _section_grids(sidebar) -> list[QGridLayout]:
    grids = []
    for sec in sidebar.findChildren(CollapsibleSection):
        grids += [g for g in sec.findChildren(QGridLayout)]
    return grids


@pytest.mark.parametrize("kind", ["_line_fig", "_bar_fig", "_heatmap_fig",
                                  "_scatter_fig"])
def test_every_section_keeps_the_fixed_label_column(qtbot, monkeypatch, kind):
    _app, sb = _sidebar(qtbot, monkeypatch, kind=kind)
    grids = _section_grids(sb)
    assert grids
    assert {g.columnMinimumWidth(0) for g in grids} == {96}
    assert {(g.columnStretch(0), g.columnStretch(1)) for g in grids} == {(0, 1)}


def test_a_row_of_controls_uses_the_medium_gap(qtbot, monkeypatch):
    """Tick visibility is a row of two toggles laid out by the shared row
    helper with its default gap."""
    from widgets.toggle_switch import ToggleSwitch

    _app, sb = _sidebar(qtbot, monkeypatch, kind="_line_fig")
    major = [t for t in sb.findChildren(ToggleSwitch) if t.text() == "Major"]
    assert len(major) == 1
    assert major[0].parentWidget().layout().spacing() == theme.Spacing.md


def test_the_sections_sit_at_the_top_of_a_tall_panel(qtbot, monkeypatch):
    _app, sb = _sidebar(qtbot, monkeypatch, kind="_line_fig")
    body = sb.findChildren(CollapsibleSection)[0].parentWidget().layout()
    last = body.itemAt(body.count() - 1)
    assert isinstance(last, QSpacerItem)
    assert body.stretch(body.count() - 1) == 1
