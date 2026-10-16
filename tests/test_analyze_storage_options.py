"""The Analyze form's two image-storage checkboxes, driven on the real form.

``analyze/storage_options.py`` builds them; these tests tick them the way a user
does and read back what the run would be sent, so a checkbox wired to nothing,
or a restore that inherits an old run's "copy / float32", fails here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def tab(qtbot, monkeypatch):
    import analyze.form

    monkeypatch.setattr(analyze.form, "load_remotes", lambda: {})
    from analyze.tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)
    return t


def _storage(tab) -> tuple[str, str]:
    opts = tab._collect_run_options()
    return opts["phase_output"], opts["tophat_dtype"]


def test_both_are_on_by_default(tab):
    assert tab._phase_reference_cb.isChecked()
    assert tab._tophat_source_dtype_cb.isChecked()
    assert _storage(tab) == ("reference", "source")


def test_unticking_each_box_changes_only_its_own_option(tab):
    tab._phase_reference_cb.setChecked(False)
    assert _storage(tab) == ("copy", "source")
    tab._phase_reference_cb.setChecked(True)
    tab._tophat_source_dtype_cb.setChecked(False)
    assert _storage(tab) == ("reference", "float32")


def test_the_unticked_boxes_reach_the_pipeline_argv(tab):
    """The boundary: the argv the run is launched with."""
    from services.pipeline_service import build_pipeline_args

    tab._phase_reference_cb.setChecked(False)
    tab._tophat_source_dtype_cb.setChecked(False)
    opts = tab._collect_run_options()
    opts.update(fluor_tokens=["GFP"], filename_schema="experiment:channel:well:fov:timepoint")
    argv = build_pipeline_args(Path("pm.py"), Path("in"), Path("out"), opts)
    assert argv[argv.index("--phase_output") + 1] == "copy"
    assert argv[argv.index("--tophat_dtype") + 1] == "float32"


def test_a_sidecar_restores_both_choices(tab):
    tab._apply_pipeline_info({"schema_version": 2, "execution_options": {
        "phase_output": "copy", "tophat_dtype": "float32"}})
    assert _storage(tab) == ("copy", "float32")
    tab._apply_pipeline_info({"schema_version": 2, "execution_options": {
        "phase_output": "reference", "tophat_dtype": "source"}})
    assert _storage(tab) == ("reference", "source")


@pytest.mark.parametrize("execution_options", [
    {},                                                  # a run older than the options
    {"phase_output": "link", "tophat_dtype": 16},        # values the pipeline never wrote
])
def test_a_sidecar_that_states_no_choice_leaves_the_boxes_alone(tab, execution_options):
    """Inheriting "copy / float32" from a run that predates the options would
    quietly turn the defaults off for every plate re-run from an old folder.
    The boxes are unticked first so a restore that resets them shows up."""
    tab._phase_reference_cb.setChecked(False)
    tab._tophat_source_dtype_cb.setChecked(False)
    tab._apply_pipeline_info({"schema_version": 2, "execution_options": execution_options})
    assert _storage(tab) == ("copy", "float32")
    tab._phase_reference_cb.setChecked(True)
    tab._tophat_source_dtype_cb.setChecked(True)
    tab._apply_pipeline_info({"schema_version": 2, "execution_options": execution_options})
    assert _storage(tab) == ("reference", "source")
