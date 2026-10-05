"""Always-zip / always-move behavior + skipped-well dashboard counting.

Covers the simplification that removed the three Output-Options checkboxes
(compress input, compress output, move source TIFs): inputs/outputs are always
zipped and loose TIFs are always moved into wells — and the regression where
already-complete (skipped) wells weren't counted as done on the dashboard.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from pipeline import cli_args


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_output_option_checkboxes_removed(qapp):
    from analyze.tab import AnalyzeTab
    tab = AnalyzeTab()
    assert not hasattr(tab, "_compress_input_cb")
    assert not hasattr(tab, "_compress_output_cb")
    assert not hasattr(tab, "_move_source_tifs_cb")


def test_opts_always_zip_and_move(qapp, tmp_path):
    from analyze.tab import AnalyzeTab
    tab = AnalyzeTab()
    tab._input_edit.setText(str(tmp_path))
    opts = tab._collect_run_options()
    assert opts["compress_input_well_folders"] is True
    assert opts["compress_output_well_folders"] is True
    assert opts["move_source_tifs"] is True


def test_build_pipeline_args_never_disables_compression(qapp, tmp_path):
    from analyze.tab import AnalyzeTab
    from services.pipeline_service import build_pipeline_args
    tab = AnalyzeTab()
    tab._input_edit.setText(str(tmp_path))
    opts = tab._collect_run_options()
    args = build_pipeline_args(
        Path("process_microscopy.py"), tmp_path / "in", tmp_path / "out", opts)
    joined = " ".join(args)
    # Compression is mandatory and implicit — no compress flag is emitted at all
    # (and certainly never a --no-… disabling form).
    assert "--no-compress" not in joined
    assert "--compress_input_well_folders" not in joined
    assert "--compress_output_well_folders" not in joined


def test_pipeline_arg_parser_has_no_compress_flags():
    # The pipeline must only ever emit .zip — there's no flag to turn it off.
    pytest.importorskip("scipy")
    parser = cli_args.build_parser()
    actions = {
        opt for a in parser._actions for opt in a.option_strings
    }
    assert "--compress_input_well_folders" not in actions
    assert "--compress_output_well_folders" not in actions


def test_skipped_wells_count_as_done():
    """A re-run that skips already-complete wells (output exists) must still
    mark them done on the dashboard rather than leaving them pending."""
    from services.pipeline_runner import ProgressTracker
    pt = ProgressTracker()
    pt.parse("Zip mode: 3 well zip(s) detected.")          # well_total = 3
    ev = pt.parse("SKIP well A01 — output already exists. Use --force to reprocess.")
    assert ("well_done", (1, 3)) in ev
    assert ("well_stage", ("A01", "process", "skipped")) in ev
    pt.parse("SKIP well B02 — output already exists. Use --force to reprocess.")
    pt.parse("Well C03 — starting (pid 1)")
    ev = pt.parse("Well C03 — done.")
    assert ("well_done", (3, 3)) in ev
    assert pt.well_done == 3
    # the auto-threshold pass over each existing _out.zip turns them green
    th = pt.parse("Auto-threshold: completed well 1/3 (A01_out.zip)")
    assert ("well_stage", ("A01", "threshold")) in th
