"""Every pane's warnings and errors also reach the application log.

The application log (the Help drawer) is fed by ``logging``. The Analyze run
log and the Datasets tool logs are written straight into widgets, so without
``core/log_mirror.py`` a failure reported there never reached it. These tests
drive the real entry points each pane writes through — the Analyze queue its
pipeline reader fills, and a real Datasets tool page — and assert the record
that arrives, by logger, level and message.
"""

from __future__ import annotations

import logging
import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from core.log_mirror import mirror_line, severity


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _records(caplog, prefix):
    return [(r.name, r.levelname, r.getMessage())
            for r in caplog.records if r.name.startswith(prefix)]


# ── the helper ───────────────────────────────────────────────────────────

def test_only_warnings_and_errors_are_mirrored(caplog):
    caplog.set_level(logging.DEBUG)
    mirror_line("analyze", "2026-10-14 10:00:00  INFO      quantify: 1.2 s\n", "INFO")
    mirror_line("analyze", "[warn] B03 has no nuclei\n", "WARNING")
    mirror_line("analyze", "FAILED B04 F001\n", "ERROR")
    mirror_line("analyze", "Processing Complete\n", "DONE")
    assert _records(caplog, "allwell.analyze") == [
        ("allwell.analyze", "WARNING", "[warn] B03 has no nuclei"),
        ("allwell.analyze", "ERROR", "FAILED B04 F001"),
    ]


def test_the_pipeline_s_own_timestamp_and_level_are_not_repeated(caplog):
    caplog.set_level(logging.DEBUG)
    mirror_line("analyze",
                "2026-10-14 10:00:00  WARNING   [worker 2] B03 skipped\n", "WARNING")
    assert _records(caplog, "allwell.analyze") == [
        ("allwell.analyze", "WARNING", "[worker 2] B03 skipped")]


def test_the_pane_s_tag_wins_over_the_classifier(caplog):
    # The classifier would call this an ERROR on the word "error"; the pane
    # coloured it as INFO, and the app log must agree with what was shown.
    caplog.set_level(logging.DEBUG)
    mirror_line("analyze", "wrote errors/ directory listing\n", "INFO")
    assert _records(caplog, "allwell.analyze") == []


@pytest.mark.parametrize("line, expected", [
    ("✗ Destination already holds a dataset", "ERROR"),
    ("  ✗ indented failure", "ERROR"),
    ("✓ Copied 12 wells", "INFO"),
    ("[warn] 3 wells have no timepoint 24h", "WARNING"),
    ("[remote] note: …/errors/ not present on remote", "INFO"),
    ("Copying B03.zip", "INFO"),
])
def test_untagged_lines_are_classified(line, expected):
    assert severity(line) == expected


# ── the two panes, through their real entry points ───────────────────────

def test_an_analyze_run_error_reaches_the_app_log(qapp, caplog):
    from analyze.tab import AnalyzeTab

    caplog.set_level(logging.DEBUG)
    tab = AnalyzeTab()
    # What the pipeline reader thread puts on the queue for each stdout line.
    tab._log_q.put(("line", "2026-10-14 10:00:00  ERROR     B03 F001: ValueError: bad mask\n"))
    tab._log_q.put(("line", "2026-10-14 10:00:01  INFO      quantify: 1.2 s\n"))
    tab._poll_log()
    assert _records(caplog, "allwell.analyze") == [
        ("allwell.analyze", "ERROR", "B03 F001: ValueError: bad mask")]
    # And the pane itself still shows both.
    shown = tab._log.toPlainText()
    assert "bad mask" in shown and "quantify: 1.2 s" in shown


def test_a_datasets_tool_failure_reaches_the_app_log(qapp, caplog):
    from datasets.subset.controller import SubsetController

    caplog.set_level(logging.DEBUG)
    page = SubsetController(on_open_in_review=lambda _p: None).page
    # What the worker's logLine signal and the failure handler deliver.
    page.append_log("Copying B03.zip")
    page.append_log("✗ Destination already holds a dataset")
    assert _records(caplog, "allwell.datasets") == [
        ("allwell.datasets.subset", "ERROR", "✗ Destination already holds a dataset")]
    assert "Copying B03.zip" in page.log_text()


# ── what no pane reports ─────────────────────────────────────────────────

def test_an_exception_that_kills_a_thread_reaches_the_app_log(monkeypatch, caplog):
    from startup import boot_diag

    monkeypatch.setattr(threading, "excepthook", lambda _args: None)
    monkeypatch.setattr(boot_diag, "boot", lambda _msg: None)
    boot_diag._install_thread_excepthook()
    caplog.set_level(logging.DEBUG)

    def _die():
        raise RuntimeError("fetcher lost the connection")

    t = threading.Thread(target=_die, name="fetcher")
    t.start()
    t.join()
    got = _records(caplog, "allwell.uncaught")
    assert len(got) == 1
    name, level, message = got[0]
    assert (name, level) == ("allwell.uncaught", "ERROR")
    assert message.startswith("Uncaught exception on thread 'fetcher':\n")
    assert message.endswith("RuntimeError: fetcher lost the connection")
