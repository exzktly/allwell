"""The bar CSV under *Then relative to*: stage-1 columns unchanged, stage 2 and
its test in their own columns, references and refusals as blanks with a reason.
"""
from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from review.features.export import service as svc
from tests.test_bar_relative_to import _app, _on


def _export(ctx, tmp_path) -> dict:
    ctx.state.bar.tp_selections = {"0": True}
    ctx.notify = SimpleNamespace(set_status=lambda *a, **k: None)
    out = Path(tmp_path) / "bar.csv"
    with mock.patch.object(svc, "ask_save_csv", return_value=str(out)):
        svc.export_bar_plot_data(ctx)
    assert out.exists(), "the export wrote nothing"
    return {row["name"]: row for row in csv.DictReader(out.open())}


def test_stage_one_columns_keep_their_meaning_and_stage_two_has_its_own(tmp_path):
    ctx, ids = _app()
    _on(ctx, ids)
    rows = _export(ctx, tmp_path)
    drug = rows["Mut-drug"]
    assert float(drug["mean_gfp_mean_intensity"]) == pytest.approx(150.0)
    assert float(drug["fold_change_mean"]) == pytest.approx(0.75)
    assert float(drug["relative_to_baseline_mean"]) == pytest.approx(1.5)
    assert drug["relative_to_baseline_baseline"] == "Mut-veh"
    assert drug["relative_to_baseline_outcome"] == "ok"
    assert drug["interaction_test"] == "interaction, log scale"
    assert drug["interaction_scale"] == "log"
    assert float(drug["interaction_p"]) < 0.05
    assert drug["interaction_n"] == "3/3/3/3" and drug["interaction_df"] == "8"


def test_a_reference_condition_is_blank_and_says_so(tmp_path):
    ctx, ids = _app()
    _on(ctx, ids)
    wt = _export(ctx, tmp_path)["WT-drug"]
    assert wt["relative_to_baseline_outcome"] == "reference"
    assert wt["relative_to_baseline_mean"] == "" and wt["interaction_p"] == ""


def test_an_untested_bar_has_blank_numbers_and_the_reason(tmp_path):
    ctx, ids = _app(factors=(1.0,))
    _on(ctx, ids)
    drug = _export(ctx, tmp_path)["Mut-drug"]
    assert float(drug["relative_to_baseline_mean"]) == pytest.approx(1.5)
    assert drug["interaction_p"] == "" and drug["interaction_df"] == ""
    assert drug["interaction_undefined"].startswith("no replicate spread")


def test_with_the_setting_off_there_are_no_new_columns(tmp_path):
    ctx, _ids = _app()
    drug = _export(ctx, tmp_path)["Mut-drug"]
    assert "relative_to_baseline_mean" not in drug
    assert float(drug["fold_change_mean"]) == pytest.approx(0.75)
