"""The Cell Browser's Timepoints, FOVs and Gates pickers are multi-select pages.

Each was a menu of checkboxes; Gates applied only when its menu closed. They
are pages of the inspector that apply as a box is ticked. The context here is a
stand-in with no window to host an inspector, so the one call that shows the
page is captured; the page is the real ``MultiSelect`` the view filled, and a
box ticked in it reaches the caller through the page's own ``changed``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QLabel, QPushButton

from review.core import inspector_service
from review.features.image_browser.cell_browser import criteria_view as view


@pytest.fixture
def shown(monkeypatch):
    seen = {}
    monkeypatch.setattr(inspector_service, "is_showing", lambda _ctx, _w: False)
    monkeypatch.setattr(inspector_service, "inspector",
                        lambda _ctx: SimpleNamespace(holds=lambda _w: False))

    def _push(_ctx, _key, title, widget, **kw):
        seen["title"], seen["page"], seen["tab"] = title, widget, kw.get("tab")
        return True
    monkeypatch.setattr(inspector_service, "push", _push)
    return seen


def test_timepoints_is_a_page_whose_ticks_write_the_selection_and_the_label(qtbot, shown):
    button = QPushButton("Timepoints")
    label = QLabel("(all)")
    qtbot.addWidget(button)
    qtbot.addWidget(label)
    # Numeric strings out of numeric order: "10" sorts before "2" as text.
    selections = {"10": True, "2": True, "0": True}
    view._open_multiselect(SimpleNamespace(), button, selections, label)
    picker = shown["page"]
    assert shown["title"] == "Timepoints"
    assert picker.keys() == ["0", "2", "10"] and picker.checked() == ["0", "2", "10"]
    picker.boxes()[1].click()                      # untick "2"
    assert selections == {"10": True, "2": False, "0": True}
    assert label.text() != "(all)" and "2" in label.text()
    picker.none_button.click()
    assert not any(selections.values())


def test_gates_apply_as_a_box_is_ticked_and_every_gate_ticked_means_all(qtbot, shown, monkeypatch):
    button = QPushButton("Gates")
    qtbot.addWidget(button)
    applied: list = []
    ctx = SimpleNamespace(runtime=SimpleNamespace(cell_browser=SimpleNamespace(
        gate_pick_btn=button, gate_choices=None, gate_pick_label=None)))
    monkeypatch.setattr(view, "_gate_names", lambda _ctx: ["live", "dim", "bright"])
    monkeypatch.setattr(view.ctl, "apply_filters", lambda _ctx: applied.append(ctx.runtime.cell_browser.gate_choices))
    view._open_gate_select(ctx)
    picker = shown["page"]
    assert shown["title"] == "Gates" and picker.checked() == ["live", "dim", "bright"]
    picker.boxes()[1].click()                      # untick "dim": applied on the tick
    assert applied == [{"live", "bright"}]
    picker.boxes()[1].click()                      # every gate again: None, which follows new gates
    assert applied[-1] is None
