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
    from review.features.batch_export import BarBatchExportPanel
    return BarBatchExportPanel(app._ctx, None, use_sidebar_groups=False)


def _add_solo_group(panel, app, name="G1"):
    """Add an export group with one solo well, triggering the persist chokepoint."""
    from review.features.batch_export._common import BarGroup
    well = next(iter(app._app_state.dataset.well_paths))
    grp = BarGroup(name)
    grp.solo_wells.append(well)
    panel._groups.append(grp)
    panel._active_grp = len(panel._groups) - 1
    panel._refresh_group_list()  # persists to app._ctx.runtime.batch_export.export_groups_doc + schedules save
    return well


def test_no_manual_save_load_route():
    # The file-picker save/load methods are gone entirely.
    from review.features.batch_export.base_panel import BatchExportPanel
    assert not hasattr(BatchExportPanel, "_save_groups")
    assert not hasattr(BatchExportPanel, "_load_groups")


def test_editing_groups_mirrors_to_app_doc(loaded_app):
    from review.features.batch_export.base_panel import SHARED_PERSIST_KEY

    panel = _panel(loaded_app)
    _add_solo_group(panel, loaded_app)
    doc = loaded_app._ctx.runtime.batch_export.export_groups_doc
    assert isinstance(doc, dict) and doc.get(SHARED_PERSIST_KEY)
    assert len(doc[SHARED_PERSIST_KEY]) == 1
    assert doc[SHARED_PERSIST_KEY][0]["name"] == "G1"


def test_groups_round_trip_through_persistence_json(loaded_app):
    from review.features.batch_export.base_panel import SHARED_PERSIST_KEY
    from review.persistence import _doc, export_groups

    panel = _panel(loaded_app)
    well = _add_solo_group(panel, loaded_app)

    # Flush to persistence.json, then wipe the in-memory cache + doc.
    export_groups.save_to_data_dir(loaded_app._ctx)
    assert _doc.get_section(loaded_app._ctx, "export_groups").get(SHARED_PERSIST_KEY)
    loaded_app._ctx.runtime.batch_export.export_groups_doc = {}
    loaded_app._app_state.dataset.persistence_doc = None

    # Reload from disk → app doc repopulated with the same group + solo well.
    export_groups.load_from_data_dir(loaded_app._ctx)
    saved = loaded_app._ctx.runtime.batch_export.export_groups_doc[SHARED_PERSIST_KEY]
    assert saved[0]["name"] == "G1"
    assert saved[0]["solo_wells"] == [well]


def test_new_panel_hydrates_from_app_doc(loaded_app):
    first = _panel(loaded_app)
    well = _add_solo_group(first, loaded_app)

    # A freshly constructed panel reads the persisted groups for its slot.
    second = _panel(loaded_app)
    assert [g.name for g in second._groups] == ["G1"]
    assert second._groups[0].solo_wells == [well]


def test_every_panel_shares_one_slot(loaded_app):
    """Rewritten: the line and bar panels used to persist under different keys,
    so a group built on one mode was invisible on the other and the same
    conditions had to be rebuilt per plot type. Export groups describe the
    dataset's conditions, not the plot they are drawn as."""
    from review.features.batch_export import BatchExportPanel
    from review.features.batch_export.base_panel import SHARED_PERSIST_KEY

    bar = _panel(loaded_app)
    well = _add_solo_group(bar, loaded_app, name="BarGrp")
    line = BatchExportPanel(loaded_app._ctx, None, use_sidebar_groups=False)
    assert [g.name for g in line._groups] == ["BarGrp"]
    assert line._groups[0].solo_wells == [well]
    doc = loaded_app._ctx.runtime.batch_export.export_groups_doc
    assert set(doc) == {SHARED_PERSIST_KEY}
    assert doc[SHARED_PERSIST_KEY][0]["name"] == "BarGrp"


def test_editing_does_not_write_disk_but_running_does(loaded_app, tmp_path, monkeypatch):
    """Editing groups only mirrors in memory (no disk write, so a well click on
    an iCloud-synced data dir doesn't spin fileproviderd); a real batch run is
    the single place the groups are flushed to persistence.json."""
    panel = _panel(loaded_app)
    reg = loaded_app._ctx.persistence
    calls: list = []
    real_save = reg.save
    monkeypatch.setattr(reg, "schedule_save",
                        lambda name: calls.append(("schedule", name)))
    monkeypatch.setattr(reg, "save",
                        lambda name: (calls.append(("save", name)), real_save(name))[1])

    # Editing a group must NOT touch disk for the export_groups domain.
    _add_solo_group(panel, loaded_app)
    assert not any(n == "export_groups" for _, n in calls), calls

    # Running a batch flushes exactly once via the immediate save.
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    panel._run_batch_jobs(
        jobs=[object()],
        progress_text_fn=lambda job, step, total: "x",
        run_job_fn=lambda job, write_dir: None,
        success_text="ok", status_text="ok", out_dir=out_dir,
    )
    assert ("save", "export_groups") in calls, calls


def test_groups_get_distinct_colors_by_index(loaded_app):
    """Plate-map / group-card colouring is by group index, so different groups
    get different colours (previously well-position rank gave same-column groups
    the same colour)."""
    from review.features.batch_export._common import BarGroup
    panel = _panel(loaded_app)
    wells = list(loaded_app._app_state.dataset.well_paths)
    g0 = BarGroup("G0"); g0.solo_wells.append(wells[0])
    g1 = BarGroup("G1"); g1.solo_wells.append(wells[1])
    panel._groups = [g0, g1]
    assert panel._group_color(0) != panel._group_color(1)


# ── one set of groups for every Batch Export mode ───────────────────────────

def _panel_for(app, mode: str):
    from review.features.batch_export import (
        BarBatchExportPanel, BatchExportPanel, ScatterBatchExportPanel,
    )
    if mode == "line":
        return BatchExportPanel(app._ctx, None, use_sidebar_groups=False)
    if mode == "bar":
        return BarBatchExportPanel(app._ctx, None, use_sidebar_groups=False)
    return ScatterBatchExportPanel(
        app._ctx, None,
        scatter_mode="aggregate" if mode == "scatter_agg" else "cells",
        use_sidebar_groups=False,
    )


def test_every_mode_reads_the_same_slot(loaded_app):
    """Line / Bar / Scatter (cells) / Scatter (aggregate) used to keep four
    private sets, so groups built in one mode were invisible in the others and
    anyone exporting the same conditions four ways built them four times."""
    from review.features.batch_export.base_panel import SHARED_PERSIST_KEY

    keys = {m: _panel_for(loaded_app, m)._persist_key
            for m in ("line", "bar", "scatter_cells", "scatter_agg")}
    assert set(keys.values()) == {SHARED_PERSIST_KEY}, keys


def test_a_group_added_on_one_mode_is_visible_on_another(loaded_app):
    """The request, stated as a test."""
    bar = _panel_for(loaded_app, "bar")
    well = _add_solo_group(bar, loaded_app, name="Shared")

    line = _panel_for(loaded_app, "line")
    assert [g.name for g in line._groups] == ["Shared"]
    assert line._groups[0].solo_wells == [well]


def test_a_legacy_document_merges_its_four_slots(loaded_app):
    """A document written before the modes shared a slot has up to four
    populated per-mode keys. Merge them in a fixed order, de-duplicated by
    name — a union, because picking one slot would silently delete three."""
    wells = list(loaded_app._app_state.dataset.well_paths)[:2]
    doc = {
        "line": [{"name": "L", "members": [], "solo_wells": [wells[0]]}],
        "bar": [{"name": "B", "members": [], "solo_wells": [wells[0]]},
                {"name": "L", "members": [], "solo_wells": [wells[1]]}],
        "scatter_cells": [{"name": "SC", "members": [], "solo_wells": [wells[0]]}],
        "scatter_agg": [{"name": "SA", "members": [], "solo_wells": [wells[0]]}],
    }
    loaded_app._ctx.runtime.batch_export.export_groups_doc = doc

    panel = _panel_for(loaded_app, "line")
    # Fixed order, and the name repeated across two slots appears once —
    # the first occurrence (line's "L", holding wells[0]) winning.
    assert [g.name for g in panel._groups] == ["L", "B", "SC", "SA"]
    assert panel._groups[0].solo_wells == [wells[0]]


def test_the_shared_slot_wins_over_the_legacy_ones(loaded_app):
    """Once the shared key exists it is the whole answer — otherwise deleting
    a group would be undone by a legacy slot re-supplying it on every load."""
    from review.features.batch_export.base_panel import SHARED_PERSIST_KEY

    wells = list(loaded_app._app_state.dataset.well_paths)[:1]
    loaded_app._ctx.runtime.batch_export.export_groups_doc = {
        SHARED_PERSIST_KEY: [{"name": "Kept", "members": [], "solo_wells": wells}],
        "bar": [{"name": "Deleted", "members": [], "solo_wells": wells}],
    }
    panel = _panel_for(loaded_app, "bar")
    assert [g.name for g in panel._groups] == ["Kept"]


def test_an_empty_shared_slot_is_not_treated_as_missing(loaded_app):
    """Clearing every group writes an empty list, which must stay empty rather
    than falling through to the legacy merge and resurrecting them."""
    from review.features.batch_export.base_panel import SHARED_PERSIST_KEY

    wells = list(loaded_app._app_state.dataset.well_paths)[:1]
    loaded_app._ctx.runtime.batch_export.export_groups_doc = {
        SHARED_PERSIST_KEY: [],
        "bar": [{"name": "Old", "members": [], "solo_wells": wells}],
    }
    assert _panel_for(loaded_app, "bar")._groups == []
