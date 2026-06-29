"""Batch-export groups persist via persistence.json (no separate save route).

The Save…/Load… file-picker route was removed; export groups now autosave into
the dataset's consolidated ``persistence.json`` under ``export_groups`` and
re-hydrate on load.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def _panel(app):
    from well_viewer.features.batch_export import BarBatchExportPanel
    return BarBatchExportPanel(app, None, use_sidebar_groups=False)


def _add_solo_group(panel, app, name="G1"):
    """Add an export group with one solo well, triggering the persist chokepoint."""
    from well_viewer.features.batch_export._common import BarGroup
    well = next(iter(app._app_state.dataset.well_paths))
    grp = BarGroup(name)
    grp.solo_wells.append(well)
    panel._groups.append(grp)
    panel._active_grp = len(panel._groups) - 1
    panel._refresh_group_list()  # persists to app._export_groups_doc + schedules save
    return well


def test_no_manual_save_load_route():
    # The file-picker save/load methods are gone entirely.
    from well_viewer.features.batch_export.base_panel import BatchExportPanel
    assert not hasattr(BatchExportPanel, "_save_groups")
    assert not hasattr(BatchExportPanel, "_load_groups")


def test_editing_groups_mirrors_to_app_doc(loaded_app):
    panel = _panel(loaded_app)
    _add_solo_group(panel, loaded_app)
    doc = getattr(loaded_app, "_export_groups_doc", None)
    assert isinstance(doc, dict) and doc.get("bar")
    assert len(doc["bar"]) == 1
    assert doc["bar"][0]["name"] == "G1"


def test_groups_round_trip_through_persistence_json(loaded_app):
    from well_viewer.persistence import _doc, export_groups

    panel = _panel(loaded_app)
    well = _add_solo_group(panel, loaded_app)

    # Flush to persistence.json, then wipe the in-memory cache + doc.
    export_groups.save_to_data_dir(loaded_app)
    assert _doc.get_section(loaded_app, "export_groups").get("bar")
    loaded_app._export_groups_doc = {}
    loaded_app._app_state.dataset.persistence_doc = None

    # Reload from disk → app doc repopulated with the same group + solo well.
    export_groups.load_from_data_dir(loaded_app)
    bar = loaded_app._export_groups_doc["bar"]
    assert bar[0]["name"] == "G1"
    assert bar[0]["solo_wells"] == [well]


def test_new_panel_hydrates_from_app_doc(loaded_app):
    first = _panel(loaded_app)
    well = _add_solo_group(first, loaded_app)

    # A freshly constructed panel reads the persisted groups for its slot.
    second = _panel(loaded_app)
    assert [g.name for g in second._groups] == ["G1"]
    assert second._groups[0].solo_wells == [well]


def test_distinct_panels_keep_separate_slots(loaded_app):
    # The line and bar panels persist under different keys → independent groups.
    from well_viewer.features.batch_export import BatchExportPanel
    bar = _panel(loaded_app)
    _add_solo_group(bar, loaded_app, name="BarGrp")
    line = BatchExportPanel(loaded_app, None, use_sidebar_groups=False)
    assert line._groups == []  # the "line" slot is still empty
    doc = loaded_app._export_groups_doc
    assert set(doc) == {"bar"} and doc["bar"][0]["name"] == "BarGrp"
