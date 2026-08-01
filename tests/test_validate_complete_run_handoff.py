"""Validate & Repair's "Complete this run in Analyze" handoff.

The button's whole purpose is that the run it opens can actually finish the
partial one: same settings as the original run, and FOV-level incremental mode
so wells that already hold *some* output are completed rather than skipped.
Handing over a bare folder path satisfies neither, and both failures are
silent — a re-run configured by hand reports success having processed nothing.

Offscreen and fixture-driven (``tests.fixtures.dataset_builder``), no mocking
of the compute layer; the Analyze side is a real ``AnalyzeTab``.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

from tests.fixtures.dataset_builder import build_dataset  # noqa: E402


def _handoff_recorder() -> tuple[dict, object]:
    calls: dict = {}

    def _open(path, pipeline_info=None, *, incremental=False):
        calls["path"] = path
        calls["info"] = pipeline_info
        calls["incremental"] = incremental

    return calls, _open


# ── the Datasets side: what Validate hands over ──────────────────────────────

def test_complete_run_hands_over_the_datasets_own_settings_and_asks_for_incremental(
    qtbot, tmp_path,
):
    """Regression guard: the handoff used to pass the folder path alone. The
    Analyze form kept whatever was already in it -- schema, separator, channel
    roles, QC options, and the csv_prefix the incremental done-set lookup reads
    -- and the run was a plain re-run, which SKIPS any well whose out-zip and
    CSV both exist however few of its FOV-groups landed. Pressing Run did
    nothing for the very wells the findings list had just named."""
    from datasets.validate.controller import ValidateController

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    calls, on_open = _handoff_recorder()
    ctrl = ValidateController(on_open_in_analyze=on_open)
    qtbot.addWidget(ctrl.page)

    ctrl._on_complete_run(str(tmp_path))

    assert calls["path"] == str(tmp_path)
    assert calls["incremental"] is True
    # The dataset's own recorded sidecar, not a hand-built subset of it.
    on_disk = json.loads((tmp_path / "out" / "pipeline_info.json").read_text())
    assert calls["info"] == on_disk


def test_complete_run_says_so_when_the_settings_cant_be_read(qtbot, tmp_path):
    """A dataset whose sidecar is missing or corrupt still opens in Analyze --
    refusing would strand the user -- but the form then keeps whatever was
    already in it, and a run configured by hand against the wrong csv_prefix
    reads its done-set from files that don't exist and reprocesses everything.
    The shortfall has to be visible in the page, not only in the handoff."""
    from datasets.validate.controller import ValidateController

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    (tmp_path / "out" / "pipeline_info.json").write_text("{ not json")
    calls, on_open = _handoff_recorder()
    ctrl = ValidateController(on_open_in_analyze=on_open)
    qtbot.addWidget(ctrl.page)

    ctrl._on_complete_run(str(tmp_path))

    assert calls["path"] == str(tmp_path)
    assert calls["info"] == {}
    log = ctrl.page._status.toPlainText()
    assert "pipeline_info.json" in log
    assert "NOT pre-filled" in log


def test_read_dataset_settings_never_raises_on_a_non_dataset(tmp_path):
    from datasets.validate.validate_compute import read_dataset_settings

    info, problem = read_dataset_settings(tmp_path / "nope")
    assert info == {}
    assert "'in'" in problem and "'out'" in problem


def test_datasets_tab_forwards_the_incremental_request(qtbot, tmp_path):
    """The `DatasetsTab` seam between a feature controller and the shell has to
    carry both extra arguments through; dropping either one downgrades the
    handoff back to a bare path with no error anywhere."""
    from datasets_tab import DatasetsTab

    calls, on_open = _handoff_recorder()
    tab = DatasetsTab(on_open_in_analyze=on_open)
    qtbot.addWidget(tab)

    tab._open_in_analyze(str(tmp_path), {"separator": "_"}, incremental=True)

    assert calls["path"] == Path(tmp_path)
    assert calls["info"] == {"separator": "_"}
    assert calls["incremental"] is True


# ── the Analyze side: what the shell does with it ────────────────────────────

class _NotebookStub:
    def __init__(self) -> None:
        self.index = None

    def setCurrentIndex(self, index) -> None:
        self.index = index


def test_open_dataset_in_analyze_prefills_the_form_and_keeps_the_callers_path(
    qtbot, tmp_path,
):
    """The recorded `execution_options.input_dir` is the path on whatever
    machine ran the pipeline -- a volume that need not exist here. The folder
    the caller names is the one the user just picked, so the input field is
    written after the settings are applied rather than before, where the
    sidecar's stale path would win."""
    from allwell import AllWellApp
    from analyze_tab import AnalyzeTab

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    info = json.loads((tmp_path / "out" / "pipeline_info.json").read_text())
    info["execution_options"] = {
        "filename_schema": info["schema"],
        "filename_sep": info["separator"],
        "nuclear_token": "nir",
        "fluor_tokens": ["nir", "gfp"],
        "csv_prefix": "run7",
        "input_dir": "/Volumes/another-machine/plate",
    }

    analyze = AnalyzeTab()
    qtbot.addWidget(analyze)
    shell = type("_Shell", (), {})()
    shell._analyze = analyze
    shell._nb = _NotebookStub()

    AllWellApp._open_dataset_in_analyze(shell, tmp_path, info, incremental=True)

    assert shell._nb.index == 1
    assert analyze._input_edit.text() == str(tmp_path)
    assert analyze._csv_prefix_edit.text() == "run7"
    assert analyze._incremental_cb.isChecked()
    # Output resolves off the dataset root, which is also the prior dir the
    # incremental done-set is read from.
    assert analyze._output_lbl.text() == str(tmp_path / "out")


def test_incremental_checkbox_reaches_the_pipeline_command_line(qtbot, tmp_path):
    """The end of the chain: without `--incremental` the pipeline skips every
    well that already has output, so everything upstream of this assertion
    could be correct and the run would still complete nothing."""
    from analyze_tab import AnalyzeTab
    from services.pipeline_service import build_pipeline_args

    analyze = AnalyzeTab()
    qtbot.addWidget(analyze)
    analyze._input_edit.setText(str(tmp_path))

    assert not analyze._incremental_cb.isChecked()   # off by default
    plain = build_pipeline_args(
        Path("process_microscopy.py"), tmp_path / "in", tmp_path / "out",
        analyze._collect_run_options())
    assert "--incremental" not in plain

    analyze._incremental_cb.setChecked(True)
    opts = analyze._collect_run_options()
    assert opts["incremental"] is True
    args = build_pipeline_args(
        Path("process_microscopy.py"), tmp_path / "in", tmp_path / "out", opts)
    assert "--incremental" in args
    # The prior dir is where the already-done (fov, timepoint) set is read
    # from; --incremental without it silently reprocesses the whole plate.
    assert args[args.index("--incremental_prior_dir") + 1] == str(tmp_path / "out")
