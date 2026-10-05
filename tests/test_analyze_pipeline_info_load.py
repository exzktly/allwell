"""Loading a prior run's pipeline_info.json back into the Analyze form."""

from __future__ import annotations

from analyze.form import AnalyzeTabFormMixin


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
    from analyze.tab import AnalyzeTab

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
    from analyze.tab import AnalyzeTab

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
    assert tab._schema_picker.separator() == "-"
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
    ``analyze.domain.job_edit._hydrate_opts_without_sidecar_equivalents``.
    """
    from analyze.tab import AnalyzeTab

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
    from analyze.tab import AnalyzeTab

    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    tab.hydrate_job({"opts": {"raw": "", "allow_low_disk": True}})
    assert tab._allow_low_disk_cb.isChecked() is True


# ── a remote run's sidecar describes the host, not this machine ──────────────

def _remote_sidecar(tab, plate):
    """The sidecar a remote run leaves in ``plate/out``, built the way one is.

    The form's options go through the real ``build_pipeline_args``, the real
    ``RemoteRunner._build_remote_argv`` and the ``--suspend_file`` ``spawn``
    appends, then the pipeline's own parser — ``execution_options`` is
    ``vars(args)`` of exactly that. The fetch brings the file home unchanged.
    """
    import json
    from pathlib import Path

    from pipeline.cli_args import build_parser
    from services.pipeline_service import build_pipeline_args
    from services.remote_runner import (
        LocalInputMode,
        OutputFilter,
        RemoteRunner,
    )
    from services.remote_config import RemoteHost

    host = RemoteHost(
        name="hpc", ssh_alias="hpc", remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging", remote_python="/opt/python",
        cpu_only=False, extra_args=(), cache_budget_gb=10,
        measured_bandwidth_mbps=100.0)
    local = build_pipeline_args(Path("process_microscopy.py"), plate / "in",
                                plate / "out", dict(tab._collect_run_options()))
    run_dir = "/home/me/.allwell-staging/runs/run-abc"
    argv = RemoteRunner(host)._build_remote_argv(
        local, f"{run_dir}/in", f"{run_dir}/out", OutputFilter.FULL,
        LocalInputMode.ZIP)
    argv += ["--suspend_file", f"{run_dir}/.aw_suspend_request"]
    args = argv[argv.index("--input_dir"):]
    ns = vars(build_parser().parse_known_args(args)[0])
    info = {"execution_options": {k: (str(v) if isinstance(v, Path) else v)
                                  for k, v in ns.items()}}
    (plate / "out").mkdir(parents=True, exist_ok=True)
    side = plate / "out" / "pipeline_info.json"
    side.write_text(json.dumps(info))
    return side, info


def test_a_remote_run_s_sidecar_is_opened_against_this_machine(qtbot, tmp_path):
    """Reported: opening a remote run's pipeline_info.json filled the form wrong.

    Its paths name the host — the input field became the host's
    ``runs/<id>/in``, the scratch disk its output dir — and "Run completeness
    check" came back unticked, because the remote runner adds
    ``--no_run_check`` to every launch whatever the form said.
    """
    from analyze.tab import AnalyzeTab

    plate = tmp_path / "plate"
    (plate / "in").mkdir(parents=True)
    source_tab = AnalyzeTab()
    qtbot.addWidget(source_tab)
    source_tab._run_check_cb.setChecked(True)
    side, info = _remote_sidecar(source_tab, plate)
    assert info["execution_options"]["no_run_check"] is True, "fixture: not remote-shaped"

    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    tab._run_check_cb.setChecked(True)
    tab._apply_pipeline_info(info, source=side)

    assert tab._input_edit.text() == str(plate / "in"), (
        "the input field names the host's run directory")
    assert tab._scratch_edit.text() == "", "the scratch disk names the host's output dir"
    assert tab._run_check_cb.isChecked(), (
        "the host's forced --no_run_check was restored as the user's choice")


def test_a_local_run_s_sidecar_still_restores_its_own_paths(qtbot, tmp_path):
    """The control: a sidecar recorded where it sits keeps every path it names."""
    from analyze.tab import AnalyzeTab

    plate = tmp_path / "plate"
    (plate / "in").mkdir(parents=True)
    (plate / "out").mkdir()
    scratch = tmp_path / "fast-disk"
    scratch.mkdir()
    info = {"execution_options": {
        "input_dir": str(plate / "in"), "output_dir": str(plate / "out"),
        "scratch_dir": str(scratch), "no_run_check": True}}
    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    tab._apply_pipeline_info(info, source=plate / "out" / "pipeline_info.json")
    assert tab._input_edit.text() == str(plate / "in")
    assert tab._scratch_edit.text() == str(scratch)
    assert not tab._run_check_cb.isChecked()


def test_a_remote_sidecar_applied_without_its_location_is_still_recognised(
        qtbot, tmp_path):
    """Validate & Repair's hand-off and the pre-run *Load these settings* apply
    a sidecar without saying where it sits, so the recorded ``output_dir``
    cannot be compared with anything; ``--suspend_file``, which only the remote
    runner passes, is what still says whose paths these are."""
    from analyze.tab import AnalyzeTab

    plate = tmp_path / "plate"
    (plate / "in").mkdir(parents=True)
    source_tab = AnalyzeTab()
    qtbot.addWidget(source_tab)
    _side, info = _remote_sidecar(source_tab, plate)

    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    tab._run_check_cb.setChecked(True)
    tab._apply_pipeline_info(info, set_input_path=False)
    assert tab._scratch_edit.text() == ""
    assert tab._run_check_cb.isChecked()
