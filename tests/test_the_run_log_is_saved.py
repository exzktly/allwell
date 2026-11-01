"""The Analyze log pane's text is saved to the run's output folder (``analyze.domain.run_log``).

Asked for after a remote nanowell run reported no confluence and nothing but its output files was left to read: the
pane keeps the last 10,000 lines and nothing once the window closes. Every line the pane is given now also goes to
``<output>/logs/analyze-<stamp>.log``.

The unit tests drive :class:`RunLogFile`; one test drives the real ``AnalyzeTab.start_run`` and the real
``PipelineRunner`` thread (§7.8 rule 3), with only the pipeline subprocess replaced, and reads the file the run left.
"""

from __future__ import annotations

import datetime as dt

import pytest

from analyze.domain.run_log import LOG_DIR, MAX_HELD, RunLogFile


def _clock():
    return dt.datetime(2026, 11, 1, 15, 4, 5)


def _read(path) -> str:
    return path.read_text(encoding="utf-8")


def test_lines_before_the_output_folder_is_known_are_written_first_in_order(tmp_path):
    log = RunLogFile(_clock)
    log.begin()
    log.runner_started()
    assert log.write([("resume note", "INFO"), ("detected layout\n", "INFO")], None) is None
    assert log.path is None and not (tmp_path / LOG_DIR).exists()       # nothing on disk without a folder
    assert log.write([("$ python process_microscopy.py …\n", "INFO")], tmp_path) is None
    log.write([("[warn] a well failed\n", "WARNING")], tmp_path)
    assert log.path == tmp_path / LOG_DIR / "analyze-20261101-150405.log"
    assert _read(log.path) == ("resume note\ndetected layout\n$ python process_microscopy.py …\n"
                               "[warn] a well failed\n")
    log.end()
    log.write([("after the run\n", "INFO")], tmp_path)                    # a straggler is not this run's
    assert "after the run" not in _read(log.path)


def test_each_run_has_its_own_file_even_within_one_second(tmp_path):
    log = RunLogFile(_clock)
    paths = []
    for text in ("first", "second"):
        log.begin()
        log.runner_started()
        log.write([(text, "INFO")], tmp_path)
        paths.append(log.path)
        log.end()
    assert paths[0] != paths[1] and paths[1].name == "analyze-20261101-150405-2.log"
    assert [_read(p) for p in paths] == ["first\n", "second\n"]


def test_the_folder_is_not_taken_until_the_runner_holds_the_run(tmp_path):
    """Before ``PipelineRunner.start`` returns, the folder it names is the previous run's: a note logged then is
    held, not written there."""
    previous, this = tmp_path / "previous", tmp_path / "this"
    log = RunLogFile(_clock)
    log.begin()
    log.write([("resume note", "INFO")], previous)
    assert log.path is None and not previous.exists()
    log.runner_started()
    log.write([("$ command", "INFO")], this)
    assert log.path.parent == this / LOG_DIR and _read(log.path) == "resume note\n$ command\n"


def test_a_run_that_never_reaches_an_output_folder_writes_nothing(tmp_path):
    log = RunLogFile(_clock)
    log.begin()
    log.runner_started()
    log.write([("refused\n", "ERROR")], None)
    log.end()
    assert log.path is None and not (tmp_path / LOG_DIR).exists()


def test_held_lines_are_bounded_and_the_shortfall_is_said(tmp_path):
    log = RunLogFile(_clock)
    log.begin()
    log.runner_started()
    log.write([(f"l{i}", "INFO") for i in range(MAX_HELD + 3)], None)
    log.write([("now known", "INFO")], tmp_path)
    lines = _read(log.path).splitlines()
    assert len(lines) == MAX_HELD + 2 and lines[0] == "l0" and lines[MAX_HELD - 1] == f"l{MAX_HELD - 1}"
    assert lines[MAX_HELD] == "[run log] 3 line(s) before the output folder was known were not kept."
    assert lines[-1] == "now known"


def test_a_folder_that_cannot_be_written_is_said_once_and_costs_nothing_else(tmp_path):
    blocker = tmp_path / "out"
    blocker.mkdir()
    (blocker / LOG_DIR).write_text("a file where the folder would go")
    log = RunLogFile(_clock)
    log.begin()
    log.runner_started()
    problem = log.write([("line", "INFO")], blocker)
    assert problem is not None and problem.startswith("[warn] This run's log is not being saved")
    assert str(blocker / LOG_DIR) in problem
    assert log.write([("another", "INFO")], blocker) is None              # once, not per batch
    log.end()


# ── the real pane and the real runner ──────────────────────────────────────
pytest.importorskip("PySide6")


def _plate(root):
    """A plate in the one layout Analyze reads: one ``<WELL>.zip`` per well."""
    import zipfile

    root.mkdir(parents=True, exist_ok=True)
    for well in ("A01", "B01"):
        with zipfile.ZipFile(root / f"{well}.zip", "w") as zf:
            for ch in ("DAPI", "GFP"):
                zf.writestr(f"Exp1_{ch}_{well}_f01_t01.tif", b"")
    return root


@pytest.fixture
def tab(qtbot, monkeypatch):
    import analyze.run_footer
    from analyze.tab import AnalyzeTab

    monkeypatch.setattr(analyze.run_footer, "load_remotes", lambda: {})
    t = AnalyzeTab(parent=None)
    qtbot.addWidget(t)
    t._flatfield_cb.setValue("gaussian")          # the default needs a named blank well; not what is under test
    yield t
    t._alive = False


def test_a_run_s_pane_text_is_in_its_output_folder_and_not_the_previous_run_s(tab, qtbot, tmp_path, monkeypatch):
    """Through ``start_run`` and the runner's own thread: the file lands in *this* run's output folder — the runner
    still held the previous run's until it resolved this one's, which is why ``start`` clears it — and holds the
    pane's lines from the runner's command line to the pipeline's own output, the run's ending and the line naming
    the file."""
    runner = tab._runner
    stale = tmp_path / "previous_run_out"
    stale.mkdir()
    runner._last_output_dir = stale                                      # what the last run left behind

    def fake_pipeline(args):
        runner._enqueue(("line", "2026-11-01 15:04:06  INFO Well A01 — done.\n"))
        runner._enqueue(("line", "2026-11-01 15:04:07  WARNING Well B01 — PARTIAL: 1 of 2 groups\n"))
        runner._enqueue(("done", "Pipeline completed successfully.\n"))

    monkeypatch.setattr(runner, "_run_pipeline_subprocess", fake_pipeline)
    _plate(tmp_path / "plate" / "in")
    tab._input_edit.setText(str(tmp_path / "plate"))
    tab._auto_detect_cb.setChecked(True)
    finished = []
    tab.runFinished.connect(lambda ok, msg: finished.append(ok))
    error = tab.start_run(tab._collect_run_spec())
    assert error is None, error
    qtbot.waitUntil(lambda: bool(finished), timeout=20000)

    out = runner.last_output_dir
    assert out is not None and out != stale
    assert not (stale / LOG_DIR).exists()
    files = list((out / LOG_DIR).glob("analyze-*.log"))
    assert len(files) == 1, files
    text = _read(files[0])
    pane = tab._log.toPlainText()
    for line in ("$ ", "Well A01 — done.", "PARTIAL: 1 of 2 groups", "Pipeline completed successfully.",
                 "Processing Complete", f"This run's log is saved as {files[0]}"):
        assert line in text, line
        assert line in pane, line
    # In the pane's order: the command, the pipeline, the ending, the file's name last.
    assert text.index("$ ") < text.index("Well A01") < text.index("Processing Complete") < text.index(
        "This run's log is saved as")


def test_the_runner_forgets_the_last_run_s_output_folder_when_a_run_starts(tmp_path):
    """Until a run resolves its own output folder, ``last_output_dir`` must not name the previous run's: the run
    log, the run journal's per-well states and the suspend record all read it. Held at the resolver, so the window
    the pane test can only race through is open for as long as this test looks."""
    import threading

    from services.pipeline_runner import PipelineRunner

    runner = PipelineRunner()
    runner._last_output_dir = tmp_path / "previous_run_out"
    release, entered = threading.Event(), threading.Event()

    def resolve(_opts, _log_q):
        entered.set()
        release.wait(10)
        return None                                   # resolves nothing: the run ends without an output folder

    runner.start(tmp_path / "process_microscopy.py", {"raw": str(tmp_path)}, resolve_dirs=resolve)
    try:
        assert entered.wait(10)
        assert runner.last_output_dir is None
    finally:
        release.set()
        runner._thread.join(10)
