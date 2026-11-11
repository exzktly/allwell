"""A Run the form refused must go on saying why.

Reported as "an Analyze run that was stopped, then started again, never starts —
even after closing and reopening the app": Run clicked, no Stop button, nothing
in the log, nothing under the button. The run *had* been refused, with a reason,
by one of the checks only Run asks — the ones that read the input folder (a
packed acquisition file, a blank or control well missing from the input, the
nanowell layout) and the two ``_validate_run_request`` adds beside the roster.
The reason was written under Run, and then the continuous check, which runs
every 700 ms and never asks those checks, repainted the line from its own
lighter list and erased it. The button read as dead.

Driven through the real button of a real ``AnalyzeTab``, and measured on the
label the user reads, after the continuous check has had several turns.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

REASON = "PACKED: this folder holds a packed acquisition file — convert it in Datasets first."


def _tab(tmp_path, monkeypatch, qtbot):
    import analyze.run_footer
    from analyze.tab import AnalyzeTab

    monkeypatch.setattr(analyze.run_footer, "load_remotes", lambda: {})
    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    tab.show()
    plate = tmp_path / "plate"
    plate.mkdir()
    tab._input_edit.setText(str(plate))
    started: list = []
    monkeypatch.setattr(type(tab), "start_run", lambda self, *a, **k: started.append(a))
    return tab, started


def _let_the_live_check_run(qtbot, seconds: float = 2.0) -> None:
    """Several turns of the 700 ms continuous check (``_LIVE_CHECK_MS``)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qtbot.wait(50)


def test_a_disk_check_refusal_survives_the_live_check(tmp_path, monkeypatch, qtbot):
    tab, started = _tab(tmp_path, monkeypatch, qtbot)
    # A refusal only the full check asks: `_packed_input_hint` is skipped under `live=True`.
    monkeypatch.setattr(type(tab), "_packed_input_hint", lambda self: REASON)

    tab._run_btn.click()
    assert not started, "the refused run was started anyway"
    assert tab._run_problems_lbl.text().startswith("PACKED:")

    _let_the_live_check_run(qtbot)
    label = tab._run_problems_lbl
    assert label.isVisibleTo(tab) and label.text().startswith("PACKED:"), (
        f"the reason Run was refused was erased by the continuous check (label now {label.text()!r}), so "
        "the button reads as one that does nothing")
    assert label.objectName() == "Error", "the carried refusal lost the error colour"


def test_a_refusal_validate_adds_beside_the_roster_survives_too(tmp_path, monkeypatch, qtbot):
    """``Configuration Error`` is added by ``_validate_run_request``, not by ``_run_problems``."""
    import analyze.run_spec

    tab, started = _tab(tmp_path, monkeypatch, qtbot)
    monkeypatch.setattr(analyze.run_spec, "find_pipeline_script", lambda: None)

    tab._run_btn.click()
    _let_the_live_check_run(qtbot)
    assert not started
    assert "process_microscopy.py not found" in tab._run_problems_lbl.text()


def test_choosing_another_input_folder_drops_the_old_folder_s_refusal(tmp_path, monkeypatch, qtbot):
    tab, _ = _tab(tmp_path, monkeypatch, qtbot)
    monkeypatch.setattr(type(tab), "_packed_input_hint", lambda self: REASON)
    tab._run_btn.click()
    other = Path(tmp_path / "other")
    other.mkdir()
    tab._input_edit.setText(str(other))
    _let_the_live_check_run(qtbot)
    assert "PACKED:" not in tab._run_problems_lbl.text(), (
        "a refusal about the previous input folder is still shown for a folder nobody has checked")


def test_pressing_run_again_re_asks_rather_than_carrying(tmp_path, monkeypatch, qtbot):
    tab, started = _tab(tmp_path, monkeypatch, qtbot)
    answers = [REASON, None]
    monkeypatch.setattr(type(tab), "_packed_input_hint", lambda self: answers[0])
    tab._run_btn.click()
    answers.pop(0)              # the user converted the file
    tab._run_btn.click()
    _let_the_live_check_run(qtbot)
    assert "PACKED:" not in tab._run_problems_lbl.text()
    assert started, "the second press, with nothing left to refuse, did not start the run"
