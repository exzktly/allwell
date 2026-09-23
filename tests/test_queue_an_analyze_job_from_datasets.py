"""Datasets → Job Queue → "+ Add job" → Analyze must reach a form you can fill.

Reported: clicking it did nothing. The session opened and a banner was put on
the Analyze form, but the Analyze form lives in a different pane and nothing
switched to it — ``DatasetsTab._begin_session`` assumed the user was already
looking at Analyze, which stopped being true once the queue page offered an
Analyze entry of its own. The same path serves a queued Analyze row's Edit….

These drive a real ``AnalyzeTab`` inside a real ``DatasetsTab`` through the
menu action a user clicks, with the shell's two pane-switch callbacks recorded,
because every earlier test entered one step past the defect: they called
``begin_new_job`` and asserted a session existed, which it always did.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def panes(qtbot, tmp_path, monkeypatch):
    import analyze.form
    from analyze.tab import AnalyzeTab
    from datasets.tab import DatasetsTab

    monkeypatch.setattr(analyze.form, "load_remotes", lambda: {})
    analyze_tab = AnalyzeTab(parent=None)
    qtbot.addWidget(analyze_tab)
    calls: list[str] = []
    datasets = DatasetsTab(
        analyze_controller=analyze_tab,
        queue_state_dir=tmp_path / "queue",
        on_reveal_analyze=lambda: (calls.append("analyze"), True)[1],
        on_reveal_datasets=lambda: (calls.append("datasets"), True)[1],
    )
    qtbot.addWidget(datasets)
    jq = datasets._controllers["job_queue"]
    notes: list[str] = []
    jq._notify = lambda text, kind="warn": notes.append(text)
    yield analyze_tab, datasets, jq, calls, notes
    datasets.shutdown()


def _click_add_analyze(jq) -> None:
    from services.job_kinds import KIND_LABEL

    menu = jq.page.build_add_menu()
    action = next(a for a in menu.actions() if a.text() == KIND_LABEL["analyze"])
    action.trigger()


def _banner_on(analyze_tab):
    from widgets.context_banner import ContextBanner

    layout = analyze_tab._form_layout
    for i in range(layout.count()):
        w = layout.itemAt(i).widget()
        if isinstance(w, ContextBanner):
            return w
    return None


def test_add_job_analyze_switches_to_the_analyze_form(panes):
    analyze_tab, _datasets, jq, calls, notes = panes
    _click_add_analyze(jq)
    assert calls == ["analyze"]
    assert jq.session() == ("analyze", None)
    assert _banner_on(analyze_tab) is not None
    assert notes == []


def test_saving_the_banner_queues_the_whole_form_and_returns_to_the_queue(
        panes, tmp_path):
    analyze_tab, datasets, jq, calls, _notes = panes
    raw = tmp_path / "plate"
    raw.mkdir()
    _click_add_analyze(jq)
    analyze_tab._input_edit.setText(str(raw))
    # Off its default, so a job carrying only the input folder would differ
    # (and so the default empty-well flat-field's background-well check does
    # not refuse the form).
    analyze_tab._flatfield_cb.setCurrentText("gaussian")
    expected = analyze_tab.collect_spec()

    _banner_on(analyze_tab).confirmed.emit()

    jobs = jq.bridge.queue.jobs
    assert [j.kind for j in jobs] == ["analyze"]
    # Every option the form holds, not just the input folder.
    assert jobs[0].spec == expected
    assert jobs[0].spec["opts"]["raw"] == str(raw)
    assert "gaussian" in repr(jobs[0].spec["opts"])
    assert jq.session() is None
    assert _banner_on(analyze_tab) is None
    assert calls == ["analyze", "datasets"]
    assert datasets._nav.currentKey() == "Job Queue"


def test_a_form_run_would_refuse_is_not_queued_and_stays_open(panes, monkeypatch):
    """The banner's Save is the same act as "+ Queue", so it takes the same
    gate. Without it an empty input folder was queued and failed at dequeue."""
    import analyze.tab as analyze_tab_mod

    analyze_tab, _datasets, jq, calls, _notes = panes
    refused: list[str] = []
    monkeypatch.setattr(analyze_tab_mod.QMessageBox, "critical",
                        lambda _parent, title, _text: refused.append(title))
    _click_add_analyze(jq)
    analyze_tab._input_edit.setText("")

    _banner_on(analyze_tab).confirmed.emit()

    assert refused == ["Input Error"]
    assert jq.bridge.queue.jobs == []
    assert jq.session() == ("analyze", None)
    assert _banner_on(analyze_tab) is not None
    assert calls == ["analyze"]          # left on the form to correct it


def test_cancel_returns_to_the_queue_without_queueing(panes):
    analyze_tab, _datasets, jq, calls, _notes = panes
    _click_add_analyze(jq)
    _banner_on(analyze_tab).cancelled.emit()
    assert jq.bridge.queue.jobs == []
    assert jq.session() is None
    assert calls == ["analyze", "datasets"]


def test_editing_a_queued_analyze_job_switches_to_the_form(panes, tmp_path):
    analyze_tab, _datasets, jq, calls, _notes = panes
    raw = tmp_path / "plate9"
    raw.mkdir()
    analyze_tab._input_edit.setText(str(raw))
    jq.add_job("analyze", analyze_tab.collect_spec())
    analyze_tab._input_edit.setText("")
    job_id = jq.bridge.queue.jobs[0].id

    jq.page.editRequested.emit(job_id)

    assert calls == ["analyze"]
    assert jq.session() == ("analyze", job_id)
    assert analyze_tab._input_edit.text() == str(raw)
    assert _banner_on(analyze_tab) is not None


def test_without_a_shell_the_click_says_it_could_not_open_analyze(
        qtbot, tmp_path, monkeypatch):
    """No pane switch available (a Datasets-only build, or no shell): refuse
    and say so, rather than opening a session behind a banner nobody sees."""
    import analyze.form
    from analyze.tab import AnalyzeTab
    from datasets.tab import DatasetsTab

    monkeypatch.setattr(analyze.form, "load_remotes", lambda: {})
    analyze_tab = AnalyzeTab(parent=None)
    qtbot.addWidget(analyze_tab)
    datasets = DatasetsTab(analyze_controller=analyze_tab,
                           queue_state_dir=tmp_path / "queue")
    qtbot.addWidget(datasets)
    jq = datasets._controllers["job_queue"]
    notes: list[str] = []
    jq._notify = lambda text, kind="warn": notes.append(text)
    try:
        assert jq.begin_new_job("analyze") is False
        assert jq.session() is None
        assert _banner_on(analyze_tab) is None
        assert any("Couldn't open the" in n for n in notes)
    finally:
        datasets.shutdown()
