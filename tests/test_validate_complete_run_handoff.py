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

    def _open(path, pipeline_info=None, *, incremental=False, force_wells=None):
        calls["path"] = path
        calls["info"] = pipeline_info
        calls["incremental"] = incremental
        calls["force_wells"] = list(force_wells or [])

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
    from analyze.tab import AnalyzeTab

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
    shell = _shell_with(analyze)

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
    from analyze.tab import AnalyzeTab
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


def _shell_with(analyze):
    """A stand-in host carrying the real handoff methods off ``AllWellApp``."""
    from allwell import AllWellApp

    shell = type("_Shell", (), {})()
    shell._analyze = analyze
    shell._nb = _NotebookStub()
    shell._announce_analyze_handoff = AllWellApp._announce_analyze_handoff.__get__(shell)
    return shell


def test_handoff_unfolds_the_section_holding_the_control_it_changed(qtbot, tmp_path):
    """Regression guard: everything the handoff sets is either a field whose
    new value looks like the old one at a glance, or -- for Incremental, the
    setting that decides whether the run completes anything -- a checkbox
    inside "Compute Options", which is collapsed by default. A correctly
    configured completion run was therefore indistinguishable from landing on
    an untouched Analyze tab."""
    from allwell import AllWellApp
    from analyze.tab import AnalyzeTab

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    info = json.loads((tmp_path / "out" / "pipeline_info.json").read_text())
    analyze = AnalyzeTab()
    qtbot.addWidget(analyze)
    body, caret = analyze._collapsible_sections["Compute Options"]
    assert not body.isVisibleTo(analyze), "section starts folded (the premise)"

    AllWellApp._open_dataset_in_analyze(
        _shell_with(analyze), tmp_path, info, incremental=True)

    assert analyze._incremental_cb.isChecked()
    assert body.isVisibleTo(analyze)
    assert caret.text() == "▾"


def test_handoff_states_what_it_configured(qtbot, tmp_path):
    """And says it in words, naming the dataset -- including, when the sidecar
    could not be read, that the settings did *not* travel. A user who can't
    tell those apart presses Run on whatever was in the form before."""
    from allwell import AllWellApp
    from analyze.tab import AnalyzeTab

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    info = json.loads((tmp_path / "out" / "pipeline_info.json").read_text())
    analyze = AnalyzeTab()
    qtbot.addWidget(analyze)

    AllWellApp._open_dataset_in_analyze(
        _shell_with(analyze), tmp_path, info, incremental=True)
    status = analyze._dashboard._status.text()
    assert tmp_path.name in status
    assert "pipeline_info.json" in status
    assert "Incremental is on" in status

    AllWellApp._open_dataset_in_analyze(
        _shell_with(analyze), tmp_path, {}, incremental=True)
    assert "NOT loaded" in analyze._dashboard._status.text()


def test_unreadable_settings_warning_reaches_the_findings_list(qtbot, tmp_path):
    """The warning used to go only to the raw log, which sits behind a
    checkbox that is off by default -- a warning about a half-configured
    handoff that nobody would read."""
    from datasets.validate.controller import ValidateController

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    (tmp_path / "out" / "pipeline_info.json").write_text("{ not json")
    calls, on_open = _handoff_recorder()
    ctrl = ValidateController(on_open_in_analyze=on_open)
    qtbot.addWidget(ctrl.page)

    ctrl._on_complete_run(str(tmp_path))

    assert calls["info"] == {}
    shown = [ctrl.page._findings_list.item(i).text()
             for i in range(ctrl.page._findings_list.count())]
    assert any("NOT pre-filled" in text for text in shown)


# ── the well an incremental run can never fix ────────────────────────────────

def _well_with_images_but_no_csv(root):
    """Build a dataset where C08 has processed images and no measurements CSV.

    The reported case: Validate paints it red "no measurements recorded" while
    the pipeline's own incremental diff calls the same well up to date, because
    incremental_done_fov_keys unions the CSV's rows with the OUT-ZIP's members
    -- so C08's own processed images are counted as already done and the run
    skips the very well whose CSV is missing.
    """
    build_dataset(root, wells=["B02", "C08"], timepoints=["0h", "24h"],
                  fovs=("F001", "F002"))
    (root / "out" / "measurements_C08.csv").unlink()


def test_a_well_with_images_and_no_csv_is_what_incremental_skips_forever(tmp_path):
    """Pins the disagreement itself: no missing (fov, timepoint) pairs, so the
    incremental run has nothing to do, yet the CSV is absent."""
    from datasets.validate import validate_compute as vc
    from pipeline.incremental_keys import incremental_done_fov_keys
    from well_viewer.data.image_identity import load_schema

    _well_with_images_but_no_csv(tmp_path)
    report = vc.build_report(tmp_path)
    c08 = next(w for w in report.wells if w.well == "C08")
    assert c08.csv_state == "missing"
    assert c08.missing == ()                      # nothing for incremental to do
    assert vc.well_health(c08) == "missing"

    info = vc._read_sidecar(vc.resolve_dataset(tmp_path, label="d"))
    schema = load_schema(info)
    done, from_csv = incremental_done_fov_keys(
        tmp_path / "out", "C08", csv_prefix="measurements", safe_well="C08",
        schema=list(schema.fields), sep=schema.sep)
    assert done and not from_csv                  # recovered from the out-zip
    assert vc.wells_needing_force(report) == ("C08",)
    # B02 is fine and must not be dragged into a needless re-segmentation.
    assert "B02" not in vc.wells_needing_force(report)

    no_csv = [f for f in report.findings if f.code == "no_csv" and f.well == "C08"]
    assert len(no_csv) == 1
    assert "forced reprocess" in no_csv[0].message


def test_completion_is_offered_when_no_csv_wells_are_the_only_fault(qtbot, tmp_path):
    """`needs_completion` used to key off missing (fov, timepoint) pairs alone,
    so a dataset whose only fault was a no-CSV well never offered the button --
    the one case where the user most needs it, since nothing else recovers
    that well."""
    from datasets.validate.controller import ValidateController

    _well_with_images_but_no_csv(tmp_path)
    ctrl = ValidateController()
    qtbot.addWidget(ctrl.page)
    ctrl.page.show_report(__import__(
        "datasets.validate.validate_compute", fromlist=["build_report"]
    ).build_report(tmp_path))
    assert ctrl.page._complete_btn.isVisibleTo(ctrl.page)


def test_handoff_forces_exactly_the_wells_incremental_would_skip(qtbot, tmp_path):
    """End of the chain: those wells reach the pipeline command line as
    --force_wells, which is the only thing that regenerates their CSVs."""
    from allwell import AllWellApp
    from analyze.tab import AnalyzeTab
    from datasets.validate import validate_compute as vc
    from services.pipeline_service import build_pipeline_args

    _well_with_images_but_no_csv(tmp_path)
    info = json.loads((tmp_path / "out" / "pipeline_info.json").read_text())
    forced = list(vc.wells_needing_force(vc.build_report(tmp_path)))

    analyze = AnalyzeTab()
    qtbot.addWidget(analyze)
    AllWellApp._open_dataset_in_analyze(
        _shell_with(analyze), tmp_path, info, incremental=True, force_wells=forced)

    assert analyze._force_wells_edit.text() == "C08"
    assert "C08" in analyze._dashboard._status.text()
    opts = analyze._collect_run_options()
    assert opts["force_wells"] == ["C08"]
    args = build_pipeline_args(
        Path("process_microscopy.py"), tmp_path / "in", tmp_path / "out", opts)
    assert args[args.index("--force_wells") + 1] == "C08"
    # Incremental stays on for everything else — the partial wells it does fix
    # must not be re-segmented from scratch.
    assert "--incremental" in args


def test_force_wells_only_overrides_the_named_wells(tmp_path):
    """The pipeline-side decision, on its own: a forced well runs as if
    --incremental were off; every other well is untouched by the list."""
    import process_microscopy as pm

    forced = pm._canonical_force_wells(["c8", "F08"])
    assert sorted(forced) == ["C08", "F08"]        # C8 and C08 are one well
    assert pm._incremental_for_well(True, "C08", forced) is False
    assert pm._incremental_for_well(True, "C8", forced) is False
    assert pm._incremental_for_well(True, "B02", forced) is True
    assert pm._incremental_for_well(True, "B02", frozenset()) is True

    args = pm.build_parser().parse_args(
        ["--input_dir", "in", "--output_dir", "out", "--force_wells", "c8"])
    assert args.force_wells == ["c8"]
    assert pm.build_parser().parse_args(
        ["--input_dir", "in", "--output_dir", "out"]).force_wells is None


def test_open_tool_shows_the_tool_and_carries_the_dataset_path(qtbot, tmp_path):
    """Review's Home page offers to check a dataset whose run left no
    completeness record, and this is the seam that offer lands on.

    Switching pane alone is not enough: a button labelled "Check this dataset
    now" that leaves the user on whichever tool happened to be showing, with an
    empty folder field, does less than it says and makes them re-find a folder
    the app already has open.
    """
    from datasets_tab import DatasetsTab

    tab = DatasetsTab(queue_state_dir=tmp_path)
    qtbot.addWidget(tab)

    assert tab.open_tool("validate", dataset_path=str(tmp_path)) is True
    assert tab.current_help_page() == "Validate & Repair"

    page = tab._pages_by_kind["validate"]
    assert str(tmp_path) in page.dataset_path(), (
        f"the folder must arrive with the user, got {page.dataset_path()!r}")


def test_open_tool_reports_a_tool_this_build_does_not_have(qtbot, tmp_path):
    """A profiled build can drop a tool. The caller has to be able to tell
    "shown" from "nothing happened" rather than assuming it worked."""
    from datasets_tab import DatasetsTab

    tab = DatasetsTab(queue_state_dir=tmp_path)
    qtbot.addWidget(tab)

    assert tab.open_tool("no-such-tool") is False
