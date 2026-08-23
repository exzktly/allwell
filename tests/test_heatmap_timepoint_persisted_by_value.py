"""The heat map restores the timepoint the user chose, not a combo position.

The Heat Map draws exactly one timepoint, and the saved settings block used to
name it by ``tp_index`` — the position of the item in a combo that
``refresh_heatmap_timepoints`` rebuilds from the plate's own timepoints. The
documented add-timepoints workflow re-runs a plate with extra timepoints and
reloads it, which renumbers every position: a heat map saved showing 72 h came
back showing whatever now sits third in the list, with nothing to indicate the
substitution.

These drive the two real halves — ``_collect_settings`` at save and
``apply_persisted_settings`` at restore — with the timepoint set changing in
between, which is the sequence the app performs and the only one in which the
defect is visible.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from well_viewer.core.widget_registry import WidgetRegistry
from well_viewer.features.heatmap.state import HeatmapState
from well_viewer.persistence import heatmap_layouts


class _Combo:
    """The timepoint combo's surface as ``heatmap_layouts`` uses it."""

    def __init__(self, items: list[str]) -> None:
        self._items = list(items)
        self._index = 0

    def count(self) -> int:
        return len(self._items)

    def currentIndex(self) -> int:
        return self._index

    def setCurrentIndex(self, index: int) -> None:
        self._index = index

    def currentText(self) -> str:
        return self._items[self._index] if self._items else ""

    def findText(self, text: str) -> int:
        return self._items.index(text) if text in self._items else -1

    def blockSignals(self, blocked: bool) -> bool:
        return False

    def setChecked(self, checked: bool) -> None:  # scale combo stand-in
        pass


def _plate(timepoints: list[float], selected: int):
    """A heat-map tab showing *selected* out of *timepoints*."""
    hm = HeatmapState()
    hm.tp_values = list(timepoints)
    combo = _Combo([f"{v:g} h" for v in timepoints])
    combo.setCurrentIndex(selected)
    registry = WidgetRegistry(SimpleNamespace())
    registry.register("_heatmap_tp_cb", combo)
    # apply_persisted_settings bails unless one of the three "tab is built"
    # widgets exists; the scale combo is the cheapest of them.
    registry.register("_heatmap_scale_cb", _Combo(["Auto"]))
    slices = {"heatmap": hm}
    app = SimpleNamespace(
        _heatmap_tp_cb=combo,
        _app_state=SimpleNamespace(feature_slice=slices.get),
    )
    ctx = SimpleNamespace(
        widgets=registry,
        state=SimpleNamespace(feature_slice=slices.get),
    )
    return app, ctx, hm, combo


def test_adding_timepoints_still_restores_the_saved_timepoint():
    app, _ctx, _hm, combo = _plate([24.0, 48.0, 72.0], selected=2)
    assert combo.currentText() == "72 h"
    settings = heatmap_layouts._collect_settings(app)

    # The plate is re-run with two earlier timepoints and reopened.
    _app2, ctx2, hm2, combo2 = _plate([0.0, 6.0, 24.0, 48.0, 72.0], selected=0)
    hm2.persisted_settings = settings
    heatmap_layouts.apply_persisted_settings(ctx2)

    assert combo2.currentText() == "72 h", (
        "the heat map came back on a different timepoint than the one saved "
        f"({combo2.currentText()}); the saved settings were {settings!r}"
    )


def test_a_saved_timepoint_this_plate_lacks_restores_nothing_and_says_so(caplog):
    app, _ctx, _hm, combo = _plate([24.0, 48.0, 72.0], selected=2)
    settings = heatmap_layouts._collect_settings(app)

    # A plate that never had 72 h. Falling back to the index would land on
    # 12 h, which looks exactly like a successful restore.
    _app2, ctx2, hm2, combo2 = _plate([4.0, 8.0, 12.0], selected=0)
    hm2.persisted_settings = settings
    with caplog.at_level(logging.INFO, logger="well_viewer"):
        heatmap_layouts.apply_persisted_settings(ctx2)

    assert combo2.currentIndex() == 0, (
        "a saved timepoint this plate does not have must leave the combo on "
        f"its default, not on index {combo2.currentIndex()}"
    )
    assert any("saved timepoint" in r.getMessage() for r in caplog.records), (
        "the substitution was silent; records were "
        f"{[r.getMessage() for r in caplog.records]}"
    )


def test_a_file_written_without_a_value_still_restores_by_index():
    """Backwards compatibility: settings saved before ``tp_value`` existed."""
    _app, ctx, hm, combo = _plate([24.0, 48.0, 72.0], selected=0)
    hm.persisted_settings = {"tp_index": 2}

    heatmap_layouts.apply_persisted_settings(ctx)

    assert combo.currentText() == "72 h"


def test_a_legacy_index_past_the_end_is_clamped():
    _app, ctx, hm, combo = _plate([24.0, 48.0], selected=0)
    hm.persisted_settings = {"tp_index": 9}

    heatmap_layouts.apply_persisted_settings(ctx)

    assert combo.currentIndex() == 1
