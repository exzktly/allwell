"""The Heat Map's two newest display toggles survive a save and a reload.

``show_error`` (print each cell's spread under its value) and
``label_from_sample_map`` (name the row and column ticks from Home → Sample
Map) join the colormap, the scale mode and the rep-set/log toggles in the
``heatmap_layouts`` section, and they go round the same loop:
``_collect_settings`` reads the typed slice, ``apply_persisted_settings``
writes it back into the slice and the widgets.

The half worth a test of its own is the **default a file that predates the key
restores**, because the two defaults differ. Reading both with a blanket
``False`` would silently turn the sample-map tick labels off for every plate
saved before they existed — the arrangement would come back drawing ``A``–``H``
with no control having been touched, which is exactly the state the feature was
added to end.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from review.core.widget_registry import WidgetRegistry
from review.features.heatmap.state import HeatmapState
from review.persistence import heatmap_layouts as hl


class _Check:
    """The slice of QCheckBox ``apply_persisted_settings`` touches."""

    def __init__(self) -> None:
        self._checked = None

    def setChecked(self, value: bool) -> None:
        self._checked = bool(value)

    def isChecked(self):
        return self._checked

    def blockSignals(self, _blocked: bool) -> bool:
        return False


class _Combo:
    def __init__(self, items=()) -> None:
        self._items = list(items)
        self._index = 0

    def count(self) -> int:
        return len(self._items)

    def currentIndex(self) -> int:
        return self._index

    def setCurrentIndex(self, index: int) -> None:
        self._index = index

    def findText(self, text):
        return self._items.index(text) if text in self._items else -1

    def blockSignals(self, _blocked: bool) -> bool:
        return False


def _built_session(saved: dict):
    """A live Heat Map tab with *saved* parked on its slice, ready to restore."""
    hm = HeatmapState()
    hm.persisted_settings = dict(saved)
    slices = {"heatmap": hm}
    app = SimpleNamespace(_app_state=SimpleNamespace(feature_slice=slices.get))
    registry = WidgetRegistry(app)
    registry.register("_heatmap_scale_cb", _Combo(["Auto", "Fixed"]))
    registry.register("_heatmap_repset_avg_cb", _Check())
    registry.register("_heatmap_log_scale_cb", _Check())
    registry.register("_heatmap_show_error_cb", _Check())
    registry.register("_heatmap_sample_labels_cb", _Check())
    ctx = SimpleNamespace(
        window=app,
        state=SimpleNamespace(feature_slice=slices.get, heatmap=hm),
        widgets=registry,
    )
    return ctx, app, hm


def test_both_toggles_are_written_out():
    hm = HeatmapState()
    hm.show_error = True
    hm.label_from_sample_map = False
    hm.tp_values = [24.0]
    slices = {"heatmap": hm}
    app = SimpleNamespace(_app_state=SimpleNamespace(feature_slice=slices.get))
    registry = WidgetRegistry(app)
    registry.register("_heatmap_scale_cb", _Combo(["Auto", "Fixed"]))
    registry.register("_heatmap_tp_cb", _Combo(["24 h"]))

    settings = hl._collect_settings(app)
    assert settings["show_error"] is True
    assert settings["label_from_sample_map"] is False


def test_a_saved_block_reaches_both_the_slice_and_the_checkboxes():
    ctx, app, hm = _built_session(
        {"show_error": True, "label_from_sample_map": False})
    hl.apply_persisted_settings(ctx)

    assert hm.show_error is True
    assert hm.label_from_sample_map is False
    assert app._heatmap_show_error_cb.isChecked() is True
    assert app._heatmap_sample_labels_cb.isChecked() is False


def test_a_file_written_before_these_keys_restores_each_ones_own_default():
    """The one asymmetry: the error is off by default, the tick labels are on.

    The slice is seeded to the *opposite* of each default first, so the
    assertions measure the restore rather than the dataclass. Handing in a
    fresh ``HeatmapState`` and asserting its own defaults back is a test that
    passes when ``apply_persisted_settings`` never touches the slice at all —
    which was this test's first version, and is CLAUDE.md §7.8 face two.
    """
    ctx, app, hm = _built_session({"cmap": "magma"})
    hm.show_error = True
    hm.label_from_sample_map = False
    hl.apply_persisted_settings(ctx)

    assert hm.show_error is False
    assert hm.label_from_sample_map is True
    assert app._heatmap_show_error_cb.isChecked() is False
    assert app._heatmap_sample_labels_cb.isChecked() is True


def test_the_two_defaults_are_not_the_same_value():
    """Guards the test above against passing for the wrong reason.

    If both defaulted alike, a single blanket ``settings.get(key, False)``
    would satisfy one of its assertions and the pair would stop measuring the
    asymmetry they exist for.
    """
    fresh = HeatmapState()
    assert fresh.show_error != fresh.label_from_sample_map


def test_a_reset_moves_the_checkboxes_with_the_slice():
    """``reset_view`` restores the slice; the controls have to follow it.

    ``label_from_sample_map`` is the first setting here that defaults to
    **True**, so a reset that moved only the slice turned the sample-map tick
    labels back on underneath an unchecked box — a control that has stopped
    describing what the figure is drawn with. The slice is seeded to the
    opposite of every default so each assertion measures the sync.
    """
    ctx, app, hm = _built_session({})
    hm.show_error = True
    hm.label_from_sample_map = False
    hm.log_scale = True
    hm.repset_avg = True
    app._heatmap_show_error_cb.setChecked(True)
    app._heatmap_sample_labels_cb.setChecked(False)
    app._heatmap_log_scale_cb.setChecked(True)
    app._heatmap_repset_avg_cb.setChecked(True)

    from review.domain.state_defaults import restore_defaults
    restore_defaults(hm)
    hl.sync_widgets_to_slice(ctx)

    assert app._heatmap_show_error_cb.isChecked() is False
    assert app._heatmap_sample_labels_cb.isChecked() is True
    assert app._heatmap_log_scale_cb.isChecked() is False
    assert app._heatmap_repset_avg_cb.isChecked() is False


def test_the_reset_path_calls_that_sync():
    """The seam above is worthless if ``reset_view`` does not reach it.

    Asserting the helper works is not asserting the reset uses it — the same
    gap that left the helper unwritten for as long as it did.
    """
    import ast
    import inspect

    from review.persistence import reset_scopes

    src = inspect.getsource(reset_scopes.reset_view)
    names = {n.func.attr if isinstance(n.func, ast.Attribute) else
             getattr(n.func, "id", "")
             for n in ast.walk(ast.parse(src.strip()))
             if isinstance(n, ast.Call)}

    assert "sync_widgets_to_slice" in names
