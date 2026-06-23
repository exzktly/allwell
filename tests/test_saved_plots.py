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


def test_store_move_reorders_in_display_order(tmp_path):
    app = _app(tmp_path)
    store = SavedPlotsStore()
    # Added oldest→newest; display order is newest-first: [c, b, a].
    for pid in ("a", "b", "c"):
        store.add(app, _rec(pid=pid), b"x")

    def display_ids():
        return [r.id for r in reversed(store.records)]

    assert display_ids() == ["c", "b", "a"]
    # Move the top item ("c") down → [b, c, a].
    assert store.move(app, "c", up=False) is True
    assert display_ids() == ["b", "c", "a"]
    # Move "a" up → [b, a, c].
    assert store.move(app, "a", up=True) is True
    assert display_ids() == ["b", "a", "c"]
    # No-ops at the ends return False and don't change order.
    assert store.move(app, "b", up=True) is False
    assert store.move(app, "c", up=False) is False
    assert display_ids() == ["b", "a", "c"]
    # The new order persisted to disk.
    store2 = SavedPlotsStore()
    store2.load(app)
    assert [r.id for r in reversed(store2.records)] == ["b", "a", "c"]


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


def test_store_rename_updates_title_and_persists(tmp_path):
    app = _app(tmp_path)
    store = SavedPlotsStore()
    store.add(app, _rec(), b"x")
    store.rename(app, "20260101-000000-000000", "My figure")
    assert store.get("20260101-000000-000000").title == "My figure"
    from well_viewer.persistence import _doc
    assert _doc.get_section(app, "saved_plots")[0]["title"] == "My figure"
