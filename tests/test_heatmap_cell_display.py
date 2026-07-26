"""A Heat Map grid cell that holds a replicate set shows the whole set.

The sidebar grid renders one line of text per cell, and it used to render
``wells[0]`` — so a cell auto-populated from a four-well replicate set read as
plain ``A01`` and the other three members looked dropped. The cell now shows the
same ``A01+3`` summary the heat map draws on the figure, with the members in the
tooltip.

That summary is display text, never a well token: the drag payload and the
double-click-to-clear lookup read the token stashed beside it, so an ``A01+3``
string can't reach the layout model. Moving such a cell moves the whole set —
assigning the dragged token alone would drop the rest of it.
"""

from __future__ import annotations

import types

from well_viewer.features.heatmap.models import HeatmapLayout
from well_viewer.features.heatmap.sidebar_view import (
    _CELL_TOKEN_ROLE, _cell_display, _cell_token, _on_drop_event,
)


def test_single_well_cell_shows_its_token():
    assert _cell_display(["A01"]) == "A01"


def test_empty_cell_shows_nothing():
    assert _cell_display([]) == ""


def test_replicate_set_cell_summarises_its_members():
    assert _cell_display(["A01", "B01", "C01", "D01"]) == "A01+3"


class _FakeItem:
    """The two QTableWidgetItem accessors ``_cell_token`` uses."""

    def __init__(self, text, token=None):
        self._text = text
        self._token = token

    def text(self):
        return self._text

    def data(self, role):
        return self._token if role == _CELL_TOKEN_ROLE else None


def test_cell_token_prefers_the_stashed_token_over_the_summary():
    assert _cell_token(_FakeItem("A01+3", "A01")) == "A01"


def test_cell_token_falls_back_to_the_text():
    """Items built before the role existed (or by a plain rebuild) still work."""
    assert _cell_token(_FakeItem("A01")) == "A01"


def _drop_ctx(layout):
    calls = {"saved": 0}

    def _schedule(_name):
        calls["saved"] += 1

    return types.SimpleNamespace(
        state=types.SimpleNamespace(
            heatmap=types.SimpleNamespace(sidebar_layout=layout),
        ),
        persistence=types.SimpleNamespace(schedule_save=_schedule),
        widgets=types.SimpleNamespace(get_or_none=lambda _n: None),
    )


def test_moving_a_replicate_set_cell_carries_every_member(monkeypatch):
    from well_viewer.features.heatmap import sidebar_view as SV

    layout = HeatmapLayout(name="Sidebar", rows=2, cols=2)
    layout.assign(0, 0, ["A01", "B01", "C01"])
    monkeypatch.setattr(SV, "_ensure_sidebar_layout", lambda _c: layout)
    monkeypatch.setattr(SV, "_persist_and_redraw", lambda _c: None)
    monkeypatch.setattr(SV, "refresh_heatmap_layout_sidebar", lambda _c: None)

    # The drag carries the cell's first well; the whole cell has to move.
    _on_drop_event(_drop_ctx(layout), "cell", (1, 1), "A01")

    assert layout.cells.get((1, 1)) == ["A01", "B01", "C01"]
    assert (0, 0) not in layout.cells


def test_a_well_dragged_from_the_plate_lands_alone(monkeypatch):
    from well_viewer.features.heatmap import sidebar_view as SV

    layout = HeatmapLayout(name="Sidebar", rows=2, cols=2)
    monkeypatch.setattr(SV, "_ensure_sidebar_layout", lambda _c: layout)
    monkeypatch.setattr(SV, "_persist_and_redraw", lambda _c: None)
    monkeypatch.setattr(SV, "refresh_heatmap_layout_sidebar", lambda _c: None)

    _on_drop_event(_drop_ctx(layout), "cell", (0, 1), "H12")

    assert layout.cells.get((0, 1)) == ["H12"]
