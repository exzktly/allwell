"""Copying analysis through the real app (CLAUDE.md §7.8 rule 3): the real
prompt, the real dialog accepted, the real reload — then the copied ratio is
live and the copied gating is on disk.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6")

RATIO = {"name": "gfp/mcherry", "numerator_channel": "gfp",
         "numerator_metric": "mean_intensity", "denominator_channel": "mcherry",
         "denominator_metric": "mean_intensity", "epsilon": 1.0}
GATING = {"fluor_gates": {"gfp": 123.0}}


def test_copying_from_another_dataset_lands_and_reloads(loaded_app, mini_dataset,
                                                         tmp_path, monkeypatch):
    app = loaded_app
    ctx = app._ctx
    source = tmp_path / "plateA"
    source.mkdir()
    (source / "pipeline_info.json").write_text(json.dumps(
        {"fluor_tokens": ["gfp", "mcherry"], "nuclear_token": "dapi",
         "cell_gating": GATING}))
    (source / "persistence.json").write_text(json.dumps(
        {"schema_version": 1, "ratios": [RATIO]}))

    from PySide6.QtWidgets import QDialog

    from review.core import recipe_view
    monkeypatch.setattr(recipe_view.CopyAnalysisDialog, "exec",
                        lambda self: QDialog.Accepted)
    done = recipe_view.prompt_copy_analysis(
        ctx, folder=source, reload_after=lambda: app._load_path(mini_dataset))

    assert done == ["gating", "ratios"]
    names = [r.name for r in ctx.state.channel.ratio_metrics]
    assert "gfp/mcherry" in names
    info = json.loads((mini_dataset / "pipeline_info.json").read_text())
    assert info["cell_gating"]["fluor_gates"] == {"gfp": 123.0}
    assert ctx.state.cell_gating.fluor_gates.get("gfp") == 123.0

    # A recipe file goes through the same prompt, dialog and reload: save the
    # dataset's analysis out, then copy a file whose ratio is new to it.
    from review.persistence.recipe import read_recipe_file, write_recipe_file, read_dataset_blocks
    saved = recipe_view.prompt_export_recipe(ctx, path=tmp_path / "plate.json")
    assert read_recipe_file(saved).blocks[("pipeline_info.json", "cell_gating")][
        "fluor_gates"] == {"gfp": 123.0}

    other = tmp_path / "plateC"
    other.mkdir()
    (other / "pipeline_info.json").write_text(json.dumps(
        {"fluor_tokens": ["gfp", "mcherry"], "nuclear_token": "dapi"}))
    (other / "persistence.json").write_text(json.dumps(
        {"schema_version": 1, "ratios": [dict(RATIO, name="from a file")]}))
    recipe = write_recipe_file(read_dataset_blocks(other), tmp_path / "c.json")
    done = recipe_view.prompt_copy_analysis(
        ctx, recipe_file=str(recipe), reload_after=lambda: app._load_path(mini_dataset))
    assert done == ["ratios"]
    assert "from a file" in [r.name for r in ctx.state.channel.ratio_metrics]
