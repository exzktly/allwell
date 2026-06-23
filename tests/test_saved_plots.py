"""Saved Plots: store round-trip, capture/summary, and restore wiring."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from well_viewer.features.saved_plots.state import SavedPlot, SavedPlotsStore, get_store


def _app(tmp_path: Path):
    """Minimal app stub exposing what the store + capture paths read."""
    dataset = SimpleNamespace(data_dir=tmp_path, persistence_doc=None)
    state = SimpleNamespace(dataset=dataset)
    return SimpleNamespace(_app_state=state)


def _rec(pid="20260101-000000-000000", tab="Bar Plots"):
    return SavedPlot(
        id=pid, created_at="2026-01-01T00:00:00", tab=tab, title="Panel A",
        png_filename=f"{pid}.png", params={"active_centre_tab": tab},
        thresholds={}, summary="Tab: " + tab,
    )


def test_store_add_writes_png_and_persists(tmp_path):
    app = _app(tmp_path)
    store = SavedPlotsStore()
    assert store.add(app, _rec(), b"\x89PNG-bytes") is True
    # PNG written under <data_dir>/saved_plots/
    img = tmp_path / "saved_plots" / "20260101-000000-000000.png"
    assert img.exists() and img.read_bytes() == b"\x89PNG-bytes"
    # Metadata landed in the persistence doc section.
    from well_viewer.persistence import _doc
    section = _doc.get_section(app, "saved_plots")
    assert isinstance(section, list) and len(section) == 1
    assert section[0]["tab"] == "Bar Plots"


def test_store_reload_from_disk(tmp_path):
    app = _app(tmp_path)
    SavedPlotsStore().add(app, _rec(), b"x")
    # A fresh store loading the same dataset sees the persisted record.
    app2 = _app(tmp_path)
    # share the on-disk persistence.json by clearing the cached doc
    app2._app_state.dataset.persistence_doc = None
    store2 = SavedPlotsStore()
    store2.load(app2)
    assert [r.id for r in store2.records] == ["20260101-000000-000000"]


def test_store_delete_removes_image_and_record(tmp_path):
    app = _app(tmp_path)
    store = SavedPlotsStore()
    store.add(app, _rec(), b"x")
    img = tmp_path / "saved_plots" / "20260101-000000-000000.png"
    assert img.exists()
    store.delete(app, "20260101-000000-000000")
    assert not img.exists()
    assert store.records == []


def test_add_without_dataset_dir_is_noop(tmp_path):
    app = _app(tmp_path)
    app._app_state.dataset.data_dir = None
    assert SavedPlotsStore().add(app, _rec(), b"x") is False


def test_build_summary_lists_key_params():
    from well_viewer.features.saved_plots.collect import build_summary
    params = {
        "selected_wells": ["A01", "B01"],
        "active_channel": "gfp", "active_metric": "mean_intensity",
        "fold_change": {"vs_control_on": True, "control_label": "ctrl",
                        "op_ctrl": "ratio"},
        "use_sem": True, "use_fov_replicates": False,
        "tabs": {"Bar Plots": {"timepoint": "24"}},
    }
    thresholds = {"thresh_frac_on": {"gfp": 12.5}}
    text = build_summary(None, "Bar Plots", params, thresholds)
    assert "Tab: Bar Plots" in text
    assert "A01" in text and "gfp" in text
    assert "vs ctrl" in text
    assert "ThreshFracOn[gfp]: 12.5" in text


def test_get_store_is_singleton_on_app():
    app = SimpleNamespace()
    s1 = get_store(app)
    s2 = get_store(app)
    assert s1 is s2
