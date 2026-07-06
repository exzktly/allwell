"""Feature-page plates tint wells by plate-rank colour, like the main sidebar.

Two feature plates used to disagree with the "the plate is the legend"
invariant (a well is the same colour everywhere = ``color_for_well``):

- The Sample Definitions **Groups** plate (``rep_refresh_map``) coloured each
  selection's wells by the *group* colour (uniform per group), so a well showed
  a different colour than on the plots / main sidebar.
- The Image Browser **Image Table** sidebar picker
  (``image_table_refresh_picker``) pushed no colours at all, so the widget fell
  back to its selection-order palette.

Both now push ``color_for_well`` through ``setWellColors``. These refresh
helpers are pure over a small ``ctx`` stand-in, so they run without a
QApplication (a fake plate records the pushed colour map).
"""

from __future__ import annotations

from types import SimpleNamespace

from well_viewer.domain.selections_model import color_for_well

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


def test_groups_plate_uses_rank_colors():
    from well_viewer.features.grouping import controller as gc

    plate = _FakePlate()
    ctx = SimpleNamespace(
        widgets=SimpleNamespace(get_or_none=lambda key: plate),
        state=SimpleNamespace(
            selection=SimpleNamespace(
                # Two groups; B12 sharing a group with A01 previously took A01's
                # (lowest-rank) group colour — now it keeps its own rank colour.
                selections=[
                    {"id": "g1", "wells": ["A01", "B12"]},
                    {"id": "g2", "wells": ["A02", "B01"]},
                ],
                current_selection_id="g1",
            ),
            dataset=SimpleNamespace(well_paths=_WP),
        ),
    )

    gc.rep_refresh_map(ctx)

    assert plate.colors == {w: color_for_well(w) for w in _WP}
    # Active (current) group is still highlighted via the selection.
    assert plate.selected == ["A01", "B12"]


def test_image_table_picker_uses_rank_colors():
    from well_viewer.features.image_browser.image_table import controller as itc

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
