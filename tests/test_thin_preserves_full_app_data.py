"""Cross-version data preservation: the thin build must NOT strip data written
by the full app when it re-saves.

When a dataset's ``persistence.json`` (or ``pipeline_info.json``) carries
sections/tabs for features this build no longer ships — Multivariate, Batch
Export, the Image Table view-state block — opening the dataset and then saving
must round-trip that data verbatim. Two mechanisms make this hold and are guarded
here:

  * ``persistence._doc`` caches the *whole* document and each domain updates only
    its own top-level key via ``set_section``, so unknown top-level sections are
    never touched.
  * ``view_state.restore`` stashes *every* per-tab block into
    ``ctx.runtime.view_state.pending``; a tab this build doesn't have is never
    activated (never popped), so ``_snapshot_tabs`` re-emits it on the next save.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6")


def test_unknown_persistence_sections_and_tabs_survive_resave(loaded_app):
    app = loaded_app
    ctx = app._ctx
    data_dir = ctx.state.dataset.data_dir
    assert data_dir is not None

    # A persistence.json as the FULL app would write it: top-level sections for
    # removed features (multivariate / export_groups), a removed tab's view_state
    # block (Image Table), and data the thin app owns (ratios, Bar Plots tab).
    full_doc = {
        "schema_version": 1,
        "multivariate": {"mode": "pca", "standardize": True},
        "export_groups": {"line": {"slot0": ["A01", "A02"]}},
        "ratios": {"defs": []},
        "view_state": {
            "schema_version": 1,
            "selected_wells": [],
            "tabs": {
                "Image Table": {"rows": 3, "cols": 4, "group_size": 2},
                "Bar Plots": {"timepoints": ["0.0"]},
            },
        },
    }
    (data_dir / "persistence.json").write_text(json.dumps(full_doc, indent=2))
    # Drop the in-memory cache the initial load populated so the next read picks
    # up the full-app document from disk.
    ctx.state.dataset.persistence_doc = None

    # Thin app hydrates view_state (stashing every tab into pending) then saves it
    # back, plus a domain it owns (ratios) — both go through set_section.
    app._persistence.load("view_state")
    app._persistence.save("view_state")
    app._persistence.save("ratios")

    on_disk = json.loads((data_dir / "persistence.json").read_text())

    # Top-level sections for removed features are untouched.
    assert on_disk.get("multivariate") == {"mode": "pca", "standardize": True}
    assert on_disk.get("export_groups") == {"line": {"slot0": ["A01", "A02"]}}
    # The removed tab's view_state block round-trips verbatim.
    assert on_disk["view_state"]["tabs"].get("Image Table") == {
        "rows": 3, "cols": 4, "group_size": 2,
    }


def test_pipeline_info_foreign_blocks_survive_sample_definition_save(loaded_app, tmp_path):
    """Saving the Sample Definitions block must leave pipeline_info.json blocks
    the thin build doesn't manage (e.g. smfish_thresholds) intact."""
    from well_viewer.domain.sample_definitions import (
        build_sample_definitions_v2,
        save_to_pipeline_info,
    )

    ctx = loaded_app._ctx
    data_dir = ctx.state.dataset.data_dir
    info_path = data_dir / "pipeline_info.json"
    info = json.loads(info_path.read_text()) if info_path.exists() else {}
    # A block only the full app writes.
    info["smfish_thresholds"] = {"Cy5": 42.0}
    info_path.write_text(json.dumps(info, indent=2))

    block = build_sample_definitions_v2({}, [], None, notes="thin-app note")
    save_to_pipeline_info(data_dir, block)

    after = json.loads(info_path.read_text())
    assert after.get("smfish_thresholds") == {"Cy5": 42.0}
    assert after.get("sample_definitions") is not None
