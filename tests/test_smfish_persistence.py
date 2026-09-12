"""Regression tests for ``review.persistence.smfish``.

Pins the per-channel threshold round-trip through pipeline_info.json,
the in-memory ``_smfish_thresholds_saved`` cache, and the spinbox
restoration helper used by the channel-change handler.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace


from review.persistence import smfish as _smfish_persist
from review.core.app_runtime import AppRuntime

def _attach_ctx(app):
    app._ctx = SimpleNamespace(app=app, state=getattr(app, '_app_state', None), runtime=AppRuntime(app))
    return app


class _FakeEdit:
    """Stand-in for the smFISH threshold QLineEdit. ``blockSignals`` matches
    the QWidget API so apply_saved_to_spinbox can use it transparently."""

    def __init__(self, initial: str = "1500") -> None:
        self._text = initial
        self._blocked = False

    def text(self) -> str:
        return self._text

    def setText(self, value: str) -> None:
        self._text = value

    def blockSignals(self, blocked: bool) -> bool:
        prev = self._blocked
        self._blocked = blocked
        return prev


class _FakeCombo:
    def __init__(self, text: str = "") -> None:
        self._text = text

    def currentText(self) -> str:
        return self._text

    def setCurrentText(self, text: str) -> None:
        self._text = text


def _make_app(tmp_path: Path) -> SimpleNamespace:
    info = tmp_path / "pipeline_info.json"
    info.write_text(json.dumps({"some_other_block": {"x": 1}}))
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(dataset=SimpleNamespace(data_dir=tmp_path))
    app._smfish_channel_cb = _FakeCombo()
    app._smfish_threshold_edit = _FakeEdit()
    _attach_ctx(app)
    return app


def test_save_threshold_writes_to_pipeline_info(tmp_path):
    app = _make_app(tmp_path)
    _smfish_persist.save_threshold(app._ctx, "w3cy5", 1500.0)

    data = json.loads((tmp_path / "pipeline_info.json").read_text())
    assert data["smfish_thresholds"] == {"w3cy5": 1500.0}
    # Unrelated keys must not be clobbered.
    assert data["some_other_block"] == {"x": 1}
    # The in-memory cache mirrors the on-disk value.
    assert app._smfish_thresholds_saved == {"w3cy5": 1500.0}


def test_save_threshold_merges_multiple_channels(tmp_path):
    app = _make_app(tmp_path)
    _smfish_persist.save_threshold(app._ctx, "w3cy5", 1500.0)
    _smfish_persist.save_threshold(app._ctx, "w4gfp", 800.0)

    data = json.loads((tmp_path / "pipeline_info.json").read_text())
    assert data["smfish_thresholds"] == {"w3cy5": 1500.0, "w4gfp": 800.0}


def test_save_threshold_overwrites_existing_channel(tmp_path):
    app = _make_app(tmp_path)
    _smfish_persist.save_threshold(app._ctx, "w3cy5", 1500.0)
    _smfish_persist.save_threshold(app._ctx, "w3cy5", 1750.0)

    data = json.loads((tmp_path / "pipeline_info.json").read_text())
    assert data["smfish_thresholds"] == {"w3cy5": 1750.0}


def test_save_threshold_lowercases_channel(tmp_path):
    app = _make_app(tmp_path)
    _smfish_persist.save_threshold(app._ctx, "W3Cy5", 1500.0)

    data = json.loads((tmp_path / "pipeline_info.json").read_text())
    assert data["smfish_thresholds"] == {"w3cy5": 1500.0}


def test_save_threshold_no_data_dir_silently_noops(tmp_path):
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(dataset=SimpleNamespace(data_dir=None))
    _attach_ctx(app)
    # Must not raise even though no data_dir is set.
    _smfish_persist.save_threshold(app._ctx, "w3cy5", 1500.0)


def test_save_threshold_missing_sidecar_creates_minimal_file(tmp_path):
    """No pipeline_info.json on disk → create one with just the
    ``smfish_thresholds`` block. Previously this silently no-op'd
    (with the rationale "the pipeline owns this file") but that
    dropped every threshold the user applied during the session on
    legacy datasets that weren't processed through the current
    pipeline. The pipeline merges with any pre-existing block on its
    next run, so creating-on-missing is non-destructive."""
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(dataset=SimpleNamespace(data_dir=tmp_path))
    _attach_ctx(app)
    _smfish_persist.save_threshold(app._ctx, "w3cy5", 1500.0)

    info_path = tmp_path / "pipeline_info.json"
    assert info_path.exists()
    data = json.loads(info_path.read_text())
    assert data == {"smfish_thresholds": {"w3cy5": 1500.0}}
    # In-memory cache mirrors the saved value.
    assert app._smfish_thresholds_saved == {"w3cy5": 1500.0}


def test_load_from_pipeline_info_populates_cache_and_spinbox(tmp_path):
    info = tmp_path / "pipeline_info.json"
    info.write_text(json.dumps({
        "smfish_thresholds": {"w3cy5": 1500.0, "w4gfp": 800.0},
    }))
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(dataset=SimpleNamespace(data_dir=tmp_path))
    app._smfish_channel_cb = _FakeCombo("w3cy5")
    app._smfish_threshold_edit = _FakeEdit("1500")
    _attach_ctx(app)

    applied = _smfish_persist.load_from_pipeline_info(app._ctx)

    assert applied is True
    assert app._smfish_thresholds_saved == {"w3cy5": 1500.0, "w4gfp": 800.0}
    assert app._smfish_threshold_edit.text() == "1500.0"


def test_load_with_no_saved_block_clears_cache_and_returns_false(tmp_path):
    info = tmp_path / "pipeline_info.json"
    info.write_text(json.dumps({"other": 1}))
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(dataset=SimpleNamespace(data_dir=tmp_path))
    # Leave a stale cache from a previous dataset around to verify it gets
    # cleared on a load that finds no block — otherwise the spinbox would
    # restore a value belonging to a different folder.
    app._smfish_thresholds_saved = {"w3cy5": 9999.0}
    app._smfish_channel_cb = _FakeCombo("w3cy5")
    app._smfish_threshold_edit = _FakeEdit("1500")
    _attach_ctx(app)

    applied = _smfish_persist.load_from_pipeline_info(app._ctx)

    assert applied is False
    assert app._smfish_thresholds_saved == {}


def test_apply_saved_to_spinbox_channel_with_no_save(tmp_path):
    """Spinbox stays untouched when the current channel has no saved value."""
    app = SimpleNamespace()
    app._smfish_channel_cb = _FakeCombo("w4gfp")
    app._smfish_threshold_edit = _FakeEdit("1500")
    app._smfish_thresholds_saved = {"w3cy5": 1234.0}
    _attach_ctx(app)
    assert _smfish_persist.apply_saved_to_spinbox(app._ctx) is False
    assert app._smfish_threshold_edit.text() == "1500"


def test_apply_saved_to_spinbox_no_tab_built(tmp_path):
    """Safe to call before the smFISH tab is built (load_controller does)."""
    app = SimpleNamespace()
    app._smfish_thresholds_saved = {"w3cy5": 1234.0}
    _attach_ctx(app)
    assert _smfish_persist.apply_saved_to_spinbox(app._ctx) is False


def test_get_saved_threshold_lookup(tmp_path):
    app = SimpleNamespace()
    app._smfish_thresholds_saved = {"w3cy5": 1500.0}
    _attach_ctx(app)
    assert _smfish_persist.get_saved_threshold(app._ctx, "w3cy5") == 1500.0
    assert _smfish_persist.get_saved_threshold(app._ctx, "W3Cy5") == 1500.0
    assert _smfish_persist.get_saved_threshold(app._ctx, "missing") is None
