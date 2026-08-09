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
            "segmentation_method": "cellpose",
            "cellpose_model": "cyto2",
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
    assert tab._seg_method_cb.currentData() == "cellpose"
    assert tab._fluor_tokens_list() == ["GFP", "RFP"]
    assert tab._smfish_tokens_list() == ["RFP"]
    assert tab._background_cb.currentText() == "top_hat"
    assert tab._flatfield_cb.currentText() == "gaussian"
    assert tab._cellpose_model_cb.currentText() == "cyto2"
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


def test_a_sidecar_never_re_arms_the_low_disk_override(qtbot, tmp_path):
    """``allow_low_disk`` is recorded in the sidecar and deliberately not
    restored from it.

    Every other execution option describes how a plate was analysed and is
    worth reproducing. That one disarms a check whose whole purpose is to stop
    a run that will die of ENOSPC hours in, and a sidecar describes some folder
    someone happened to open — a volume the user has not measured and may not
    even be writing to now. Re-arming it from that is a safety override nobody
    chose. A *queued job* is the opposite case and does restore it; see
    ``analyze_job_edit._hydrate_opts_without_sidecar_equivalents``.
    """
    from analyze_tab import AnalyzeTab

    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    assert tab._allow_low_disk_cb.isChecked() is False

    tab._apply_pipeline_info({
        "schema_version": 2,
        "execution_options": {"allow_low_disk": True, "cpu_only": True},
    })
    # The neighbouring flag proves the block was read at all, so this is not
    # passing because nothing happened.
    assert tab._cpu_only_cb.isChecked() is True
    assert tab._allow_low_disk_cb.isChecked() is False


def test_a_queued_job_does_restore_it(qtbot):
    """The other half of the asymmetry above: the user set that box for this
    job when they queued it, so editing the job has to show it back — otherwise
    saving the edit silently re-arms the check they turned off."""
    from analyze_tab import AnalyzeTab

    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    tab.hydrate_job({"opts": {"raw": "", "allow_low_disk": True}})
    assert tab._allow_low_disk_cb.isChecked() is True
