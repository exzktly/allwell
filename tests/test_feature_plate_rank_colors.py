"""Feature-page plate colouring.

Two feature plates colour their wells, but by different rules that each match
their context:

- The Home → **Groups** plate (``rep_refresh_map``) tints each
  well by its **group's** colour (``color_for_selection`` — explicit override,
  else the group's lowest-well rank), so all wells in a group read as one
  condition and a recolour is honoured. This matches the group row's swatch and
  the main sidebar's group mode.
- The Image Browser **Image Table** sidebar picker
  (``image_table_refresh_picker``) has no groups, so it tints each active well
  by its own plate-rank colour (``color_for_well``).

These refresh helpers are pure over a small ``ctx`` stand-in, so they run
without a QApplication (a fake plate records the pushed colour map).
"""

from __future__ import annotations

from types import SimpleNamespace

from review.domain.selections_model import color_for_selection, color_for_well

_WP = {"A01": 1, "A02": 1, "B01": 1, "B12": 1}


class _FakePlate:
    """Records the colour map / selection pushed by a refresh helper."""

    def __init__(self):
        self.colors: dict = {}
        self.selected: list = []
        self.enabled: list = []

    def setEnabledWells(self, wells):
        self.enabled = list(wells)

    def clearWellColors(self):
        self.colors = {}

    def setWellColors(self, mapping):
        self.colors = dict(mapping)

    def setSelectedWellIds(self, wells):
        self.selected = list(wells)


def test_groups_plate_uses_group_colors():
    from review.features.home.groups import controller as gc

    # g1: two wells sharing one group ⇒ both take the group's colour
    # (lowest-well rank = A01). g2 carries an explicit recolour ⇒ both its
    # wells take that exact colour, not their own rank colours.
    g1 = {"id": "g1", "wells": ["A01", "B12"]}
    g2 = {"id": "g2", "wells": ["A02", "B01"], "color": "#123456"}
    plate = _FakePlate()
    ctx = SimpleNamespace(
        widgets=SimpleNamespace(get_or_none=lambda key: plate),
        state=SimpleNamespace(
            selection=SimpleNamespace(
                selections=[g1, g2],
                current_selection_id="g1",
            ),
            dataset=SimpleNamespace(well_paths=_WP),
        ),
    )

    gc.rep_refresh_map(ctx)

    assert plate.colors == {
        "A01": color_for_selection(g1),
        "B12": color_for_selection(g1),
        "A02": "#123456",
        "B01": "#123456",
    }
    # A multi-well group's wells share one colour; the explicit override wins.
    assert plate.colors["A01"] == plate.colors["B12"]
    assert plate.colors["A02"] == plate.colors["B01"] == "#123456"
    # Active (current) group is still highlighted via the selection.
    assert plate.selected == ["A01", "B12"]


def test_image_table_picker_uses_rank_colors():
    from review.features.image_browser.image_table import controller as itc

    plate = _FakePlate()
    ctx = SimpleNamespace(
        runtime=SimpleNamespace(
            image_table=SimpleNamespace(sidebar_plate=plate, count_lbl=None),
        ),
        state=SimpleNamespace(
            dataset=SimpleNamespace(well_paths=_WP),
            image_table=SimpleNamespace(active_wells={"A01", "B12", "A02"}),
        ),
    )

    itc.image_table_refresh_picker(ctx)

    active = {"A01", "A02", "B12"}
    assert plate.colors == {w: color_for_well(w) for w in active}
    # Active set is highlighted, row-major sorted (A01, A02, B12).
    assert plate.selected == ["A01", "A02", "B12"]
