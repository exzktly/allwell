"""Saving must not replace the heat map's settings with defaults it never read.

``heatmap_layouts._collect_settings`` reads the visual settings — colormap,
scale mode, vmin/vmax, the replicate-average and log toggles, the selected
timepoint — off the typed ``HeatmapState`` slice and writes them into
``persistence.json``. Those fields are only ever filled in by
``apply_persisted_settings``, which bails when the Heat Map tab has not been
built, and by the tab's own widgets, which do not exist then either.

So in a session where the user never opened the Heat Map, every one of those
fields was still at its dataclass default when the collector read it, and the
next save of *any* section — the whole document is rewritten together — put
those defaults on disk in place of the user's saved settings. Nothing about
the file, the tab or the plate looked wrong afterwards; the arrangement simply
came back grey, auto-scaled and on the first timepoint.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from review.core.widget_registry import WidgetRegistry
from review.features.heatmap.state import HeatmapState
from review.persistence import heatmap_layouts as hl


ON_DISK = {
    "cmap": "magma", "scale_mode": "Manual", "vmin": 10.0, "vmax": 900.0,
    "repset_avg": True, "log_scale": True, "tp_index": 2, "tp_value": 72.0,
}


class _Combo:
    """The slice of QComboBox / LutSelector the collector touches."""

    def __init__(self, items=()) -> None:
        self._items = list(items)
        self._index = 0

    def count(self) -> int:
        return len(self._items)

    def currentIndex(self) -> int:
        return self._index

    def setCurrentIndex(self, index: int) -> None:
        self._index = index

    def blockSignals(self, blocked: bool) -> bool:
        return False


def _session(*, tab_built: bool, saved=None, slice_state=None):
    hm = slice_state if slice_state is not None else HeatmapState()
    if saved is not None:
        hm.persisted_settings = dict(saved)
    slices = {"heatmap": hm}
    app = SimpleNamespace(_app_state=SimpleNamespace(feature_slice=slices.get))
    registry = WidgetRegistry(app)
    if tab_built:
        # ``register`` stamps ``app._<name>``, which is how the collector sees
        # that the tab exists — the same three widgets
        # ``apply_persisted_settings`` tests before it will restore anything.
        registry.register("_heatmap_scale_cb", _Combo(["Auto", "Manual"]))
        registry.register("_heatmap_tp_cb", _Combo(["24 h", "48 h", "72 h"]))
    return app, hm


def test_a_session_that_never_opened_the_heat_map_writes_back_what_it_loaded():
    app, _hm = _session(tab_built=False, saved=ON_DISK)

    assert hl._collect_settings(app) == ON_DISK


def test_the_defaults_it_would_otherwise_have_written_are_not_the_saved_ones():
    """Guards the test above against passing for the wrong reason.

    If the slice's defaults happened to equal the saved block, the assertion
    above would hold with the bug still present.
    """
    app, _hm = _session(tab_built=False, saved=None)
    fresh = HeatmapState()

    assert fresh.cmap_name != ON_DISK["cmap"]
    assert fresh.scale_mode != ON_DISK["scale_mode"]
    assert fresh.repset_avg != ON_DISK["repset_avg"]
    assert fresh.log_scale != ON_DISK["log_scale"]
    # And with nothing loaded there is nothing to preserve.
    assert hl._collect_settings(app) == {}


def test_a_built_tab_still_writes_what_its_widgets_hold():
    hm = HeatmapState()
    hm.cmap_name = "viridis"
    hm.scale_mode = "Manual"
    hm.vmin, hm.vmax = 1.0, 2.0
    hm.repset_avg = True
    hm.log_scale = True
    hm.tp_values = [24.0, 48.0, 72.0]
    # A stale block from the file must NOT win once the tab is live.
    app, _hm = _session(tab_built=True, saved=ON_DISK, slice_state=hm)
    app._heatmap_tp_cb.setCurrentIndex(1)

    settings = hl._collect_settings(app)
    assert settings["cmap"] == "viridis"
    assert settings["vmin"] == 1.0 and settings["vmax"] == 2.0
    assert settings["tp_value"] == 48.0


def test_a_reset_still_empties_the_section():
    """The reset clears the slice before it saves, so nothing is restored here.

    ``reset_scopes`` calls ``restore_slice_defaults`` — which puts
    ``persisted_settings`` back to ``None`` — and only then saves. The guard
    must not turn that into a save of the old settings.
    """
    app, hm = _session(tab_built=False, saved=ON_DISK)
    from review.persistence.reset_scopes import restore_slice_defaults

    restore_slice_defaults(hm, "heatmap")
    assert hl._collect_settings(app) == {}
