"""Loading a prior run's pipeline_info.json back into the Analyze form."""

from __future__ import annotations

from analyze_tab_form import AnalyzeTabFormMixin


# ── _pinfo_flat (pure; no Qt) ─────────────────────────────────────────────────

def test_pinfo_flat_prefers_execution_options():
    info = {
        "schema": "a:b",                       # top-level (ignored when exec present)
        "execution_options": {"filename_schema": "x:y", "nuclear_token": "NIR"},
    }
    d = AnalyzeTabFormMixin._pinfo_flat(info)
    assert d["filename_schema"] == "x:y"
    assert d["nuclear_token"] == "NIR"


def test_pinfo_flat_falls_back_to_toplevel_and_qc():
    info = {
        "schema": "experiment:channel:well",
        "separator": "_",
        "nuclear_token": "NIR",
        "segmentation_method": "stardist_nuclei",
        "qc_options": {"background": "top_hat", "remove_debris": True, "min_area": 10},
    }
    d = AnalyzeTabFormMixin._pinfo_flat(info)
    assert d["filename_schema"] == "experiment:channel:well"
    assert d["filename_sep"] == "_"
    assert d["background"] == "top_hat"
    assert d["qc_remove_debris"] is True
    assert d["qc_min_area"] == 10
    # None-valued keys are dropped so they don't clobber widget defaults.
    assert "fluor_tokens" not in d


# ── full apply into a live AnalyzeTab ─────────────────────────────────────────

def test_apply_pipeline_info_populates_form(qtbot, tmp_path):
    from analyze_tab import AnalyzeTab

    tab = AnalyzeTab()
    qtbot.addWidget(tab)

    indir = tmp_path / "plate" / "in"
    indir.mkdir(parents=True)
    info = {
        "schema_version": 2,
        "execution_options": {
            "input_dir": str(indir),
            "output_dir": str(tmp_path / "plate" / "out"),
            "filename_schema": "experiment:channel:well:fov:timepoint",
            "filename_sep": "_",
            "nuclear_token": "DAPI",
            "fluor_tokens": ["GFP", "RFP"],
            "smfish_tokens": ["RFP"],
            "segmentation_method": "stardist_seeded_watershed_cell",
            "cytoplasm_token": "GFP",
            "background": "top_hat",
            "flatfield": "gaussian",
            "csv_prefix": "meas2",
            "scratch_dir": str(tmp_path / "scratch"),
            "workers": 3,
            "cpu_only": True,
            "qc_remove_debris": True,
            "phase_token": "",
        },
    }

    tab._apply_pipeline_info(info)

    assert tab._nuclear_token_edit.text() == "DAPI"
    assert tab._seg_method_cb.currentData() == "stardist_seeded_watershed_cell"
    assert tab._fluor_tokens_list() == ["GFP", "RFP"]
    assert tab._smfish_tokens_list() == ["RFP"]
    assert tab._background_cb.currentText() == "top_hat"
    assert tab._flatfield_cb.currentText() == "gaussian"
    assert tab._cytoplasm_edit.text() == "GFP"
    assert tab._csv_prefix_edit.text() == "meas2"
    assert tab._scratch_edit.text() == str(tmp_path / "scratch")
    assert tab._scratch_user_set is True
    assert tab._cpu_only_cb.isChecked()
    assert tab._workers_edit.text() == "3"
    assert tab._qc_remove_debris_cb.isChecked()
    assert tab._build_schema_arg() == "experiment:channel:well:fov:timepoint"
    # Paths restored (incl. input).
    assert tab._input_edit.text() == str(indir)


def test_apply_pipeline_info_v1_fallback_no_exec_options(qtbot, tmp_path):
    """Older sidecars without execution_options still restore the basics."""
    from analyze_tab import AnalyzeTab

    tab = AnalyzeTab()
    qtbot.addWidget(tab)

    info = {
        "schema_version": 1,
        "schema": "experiment:channel:well:fov:timepoint",
        "separator": "-",
        "nuclear_token": "NIR",
        "fluor_tokens": ["GFP"],
        "segmentation_method": "stardist_nuclei",
        "qc_options": {"background": "sliding_disk", "remove_debris": False},
    }
    tab._apply_pipeline_info(info)

    assert tab._nuclear_token_edit.text() == "NIR"
    assert tab._filename_sep_edit.text() == "-"
    assert tab._background_cb.currentText() == "sliding_disk"
    assert tab._seg_method_cb.currentData() == "stardist_nuclei"
    assert tab._fluor_tokens_list() == ["GFP"]
