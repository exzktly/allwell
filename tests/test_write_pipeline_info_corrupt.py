"""Audit E3 — writing ``pipeline_info.json`` must not silently clobber a
corrupt existing sidecar. The old code swallowed a JSON/OS error while trying
to preserve user-side blocks (cell_gating / sample_definitions / ratios /
notes) and then overwrote the file, irreversibly losing that data. The fix
backs the unreadable original up and logs an ERROR before writing.
"""
from __future__ import annotations

import json

import process_microscopy as pm


def _write_info(out_dir, in_dir):
    return pm.write_pipeline_info(
        out_dir,
        input_dir=in_dir,
        filename_schema="experiment:channel:well:fov:timepoint",
        filename_sep="_",
        nuclear_token="NIR",
        fluor_tokens=["GFP"],
        smfish_tokens=[],
        segmentation_method="stardist_nuclei",
        cytoplasm_token="",
        min_nucleus_area_px=50,
    )


def test_valid_existing_user_block_is_preserved(tmp_path):
    out = tmp_path / "out"; out.mkdir()
    src = tmp_path / "in"; src.mkdir()
    p = out / "pipeline_info.json"
    p.write_text(json.dumps({"cell_gating": {"A01": 0.5}, "schema": "stale"}))

    _write_info(out, src)

    data = json.loads(p.read_text())
    assert data["cell_gating"] == {"A01": 0.5}   # preserved
    assert data["schema"] != "stale"             # rest rewritten


def test_corrupt_existing_is_backed_up_not_clobbered(tmp_path, caplog):
    out = tmp_path / "out"; out.mkdir()
    src = tmp_path / "in"; src.mkdir()
    p = out / "pipeline_info.json"
    garbage = "{ not valid json ]"
    p.write_text(garbage)

    with caplog.at_level("ERROR", logger="process_microscopy"):
        _write_info(out, src)

    backup = out / "pipeline_info.json.corrupt.bak"
    assert backup.exists(), "the unreadable original must be preserved"
    assert backup.read_text() == garbage
    # the new sidecar is valid JSON again
    json.loads(p.read_text())
    assert any("unreadable" in r.getMessage().lower() for r in caplog.records), \
        "a corrupt existing sidecar must be surfaced at ERROR, not swallowed"
