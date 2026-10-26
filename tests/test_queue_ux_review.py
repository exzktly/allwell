"""The expert UX review of the Job Queue's chained inputs, one test per finding.

Each test is the user-visible behaviour the review asked for, checked where
the user meets it — the row, the menu, the message — rather than in the state
behind it (CLAUDE.md §7.4 #1).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from services.job_queue import JobQueue, JobSpec, JobState


def _conv(job_id, label=None):
    return JobSpec(id=job_id, kind="czi_import", label=label or job_id,
                   spec={"destination": f"/planned/{job_id}"})


def _concat(job_id="cat"):
    return JobSpec(id=job_id, kind="concatenate", label="Concatenate",
                   spec={"dataset1": "/planned/a", "dataset2": "/planned/b"})


def _chain():
    q = JobQueue([_conv("a", "Convert day1"), _conv("b", "Convert day2"), _concat()])
    q.set_link("cat", "dataset1", "a", kind="dataset_root")
    q.set_link("cat", "dataset2", "b", kind="dataset_root")
    return q


# ── C1 / M3: a source that leaves without writing anything ──────────────────

def test_c1_removing_a_failed_source_keeps_its_successor_blocked():
    q = _chain()
    q.mark_running("b")
    q.mark_finished("b", ok=False, message="truncated")
    assert q.get("cat").state == JobState.BLOCKED
    q.remove("b")
    cat = q.get("cat")
    assert cat.state == JobState.BLOCKED, "released onto a half-written folder"
    assert "“Convert day2”" in cat.message and "removed" in cat.message
    assert q.next_runnable().id == "a"   # the other conversion still runs


def test_m3_removing_a_pending_source_blocks_its_successor_on_the_row():
    q = _chain()
    q.remove("b")
    assert q.get("cat").state == JobState.BLOCKED
    assert "“Convert day2”" in q.get("cat").message


def test_choosing_the_input_again_releases_it():
    q = _chain()
    q.remove("b")
    q.set_link("cat", "dataset2", None)        # "Nothing (use the folder on its page)"
    assert q.get("cat").state == JobState.PENDING


def test_saving_an_edit_releases_it():
    q = _chain()
    q.remove("b")
    assert q.update_spec("cat", {"dataset1": "/planned/a", "dataset2": "/real/day2"})
    assert q.get("cat").state == JobState.PENDING


def test_the_orphaned_input_survives_a_restart():
    q = _chain()
    q.remove("b")
    again = JobQueue.from_json(q.to_json())
    assert again.get("cat").state == JobState.BLOCKED
    assert again.get("cat").orphaned_inputs == {"dataset2": "Convert day2"}


# ── M4: Clear completed keeps blocked jobs ───────────────────────────────────

def test_m4_clear_completed_keeps_a_blocked_chain():
    q = _chain()
    q.mark_running("b")
    q.mark_finished("b", ok=False, message="truncated")
    q.clear_terminal()
    assert [j.id for j in q.jobs] == ["a", "cat"]
    assert q.get("cat").state == JobState.BLOCKED


# ── M1: a blocked row says why, first, by name ───────────────────────────────

def _row(job, **kw):
    from datasets.job_queue.row import JobRow

    row = JobRow(job)
    row.update_from(job, **kw)
    return row


def test_m1_the_reason_names_the_job_and_comes_first(qtbot):
    # Ids the way the app makes them (uuid4().hex[:10]), so a message naming
    # the id rather than the job is visible.
    q = JobQueue([_conv("4f0c9e1a2b", "Convert day1"), _conv("1dbc420216", "Convert day2"),
                  _concat()])
    q.set_link("cat", "dataset1", "4f0c9e1a2b", kind="dataset_root")
    q.set_link("cat", "dataset2", "1dbc420216", kind="dataset_root")
    q.mark_running("1dbc420216")
    q.mark_finished("1dbc420216", ok=False, message="truncated")
    cat = q.get("cat")
    assert "“Convert day2”, which failed" in cat.message
    assert "1dbc420216" not in cat.message
    row = _row(cat, predecessors=[("4f0c9e1a2b", "Convert day1"),
                                  ("1dbc420216", "Convert day2")])
    qtbot.addWidget(row)
    assert row._detail_text.startswith("Blocked — waiting on “Convert day2”")


# ── M2: a long name never pushes the status and ⋯ off the row ────────────────

def test_m2_a_long_name_elides_and_keeps_the_status_and_menu_on_the_row(qtbot):
    job = JobSpec(id="j", kind="subset", label="Subset — " + "very_long_plate_name_" * 12,
                  spec={})
    row = _row(job)
    qtbot.addWidget(row)
    row.resize(600, 60)
    row.show()
    qtbot.waitExposed(row)
    assert row._name.text().endswith("…")
    assert row._name.toolTip() == job.label
    assert row._menu_btn.geometry().right() <= row.width()
    assert row._dot.geometry().right() <= row.width()


# ── M5: one word for one idea ────────────────────────────────────────────────

def test_m5_the_menus_say_input_from_and_new_job_using_this_output(qtbot):
    job = JobSpec(id="v", kind="validate", label="Validate", spec={})
    row = _row(job, predecessors=[("a", "Convert day1")])
    qtbot.addWidget(row)
    labels = [a.text() for a in row.build_menu().actions() if a.text()]
    assert "Input from" in labels and "New job using this output" in labels
    assert "Run after" not in labels
    assert "Queue next step from this output" not in labels


# ── M6: no input menu on a kind that reads files ─────────────────────────────

@pytest.mark.parametrize("kind", ["czi_import", "nd2_import", "ome_import", "import_loose"])
def test_m6_no_input_menu_on_a_kind_that_reads_files(qtbot, kind):
    job = JobSpec(id="c", kind=kind, label="Convert", spec={})
    row = _row(job, predecessors=[("a", "Something")])
    qtbot.addWidget(row)
    labels = [a.text() for a in row.build_menu().actions() if a.text()]
    assert "Input from" not in labels


def test_m6_the_dequeue_error_is_in_plain_words():
    q = JobQueue([_conv("a", "Convert day1"),
                  JobSpec(id="v", kind="validate", label="Validate", spec={})])
    q.set_link("v", "dataset", "a", kind="dataset_root")
    q.get("a").state = JobState.SUCCEEDED          # finished, reported nothing
    with pytest.raises(ValueError) as err:
        q.materialise("v")
    text = str(err.value)
    assert "“Convert day1” did not report the folder" in text
    assert "None" not in text and "'a'" not in text


# ── P1 / P9: names in sentences ──────────────────────────────────────────────

def test_p1_a_long_name_is_shortened_in_the_middle_in_a_sentence():
    from datasets.job_queue.session import pending_note, short_label

    label = "Convert CZI — " + "x" * 120 + ".czi → plate_day2"
    short = short_label(label)
    assert len(short) <= 56 and "…" in short
    assert short.startswith("Convert CZI") and short.endswith("plate_day2")
    note = pending_note(JobSpec(id="b", kind="czi_import", label=label),
                        "concatenate", "dataset2")
    assert short in note


def test_p1_a_path_field_shows_the_start_of_a_long_name(qtbot):
    from widgets.path_field import PathField, folder_picker

    field = PathField("Dataset", pick=folder_picker("x"))
    qtbot.addWidget(field)
    field.set_path("/data/plate_day2_" + "long_" * 20)
    assert field._edit.cursorPosition() == 0


def test_p9_the_banner_says_that_job_for_one_source(qtbot, tmp_path):
    from datasets.job_queue.controller import JobQueueController
    from datasets.validate.controller import ValidateController

    jq = JobQueueController(state_dir=tmp_path)
    messages = []
    jq.set_session_hooks(lambda _k, message: messages.append(message) or True,
                         lambda _t: None)
    v = ValidateController()
    qtbot.addWidget(v.page)
    qtbot.addWidget(jq.page)
    jq.register("validate", v)
    jq.bridge.add_job(JobSpec(id="s", kind="subset", label="Subset",
                              spec={"destination": "/planned"}))
    jq.page.nextStepRequested.emit("s", "validate", "")
    assert "that job's output" in messages[-1]
    assert "those jobs'" not in messages[-1]
    v.shutdown()


def test_p9_the_banner_buttons_have_no_box_of_their_own():
    from ui import theme

    assert "QWidget#ContextBannerButtons" in theme.qss()


# ── P2 / P3 / P4 / P6: the menus and the row ─────────────────────────────────

def test_p2_menu_entries_naming_jobs_carry_their_queue_position(qtbot):
    job = JobSpec(id="v", kind="validate", label="Validate", spec={})
    row = _row(job, predecessors=[("a", "Convert"), ("b", "Convert")],
               positions={"a": 1, "b": 2, "v": 3})
    qtbot.addWidget(row)
    menu = row.build_menu()
    sub = next(a.menu() for a in menu.actions() if a.text() == "Input from")
    labels = [a.text() for a in sub.actions() if a.text()]
    assert "1. Convert" in labels and "2. Convert" in labels


def test_p3_dataset_2_does_not_offer_the_job_feeding_dataset_1(qtbot):
    q = _chain()
    q.set_link("cat", "dataset2", None)
    row = _row(q.get("cat"), predecessors=[("a", "Convert day1"), ("b", "Convert day2")])
    qtbot.addWidget(row)
    menu = row.build_menu()
    sub = next(a.menu() for a in menu.actions() if a.text() == "Dataset 2 from")
    labels = [a.text() for a in sub.actions() if a.text()]
    assert "Convert day1" not in labels and "Convert day2" in labels


def test_p4_a_blocked_job_offers_no_retry(qtbot):
    job = JobSpec(id="v", kind="validate", label="Validate", spec={},
                  state=JobState.BLOCKED, message="waiting on “x”, which failed")
    row = _row(job)
    qtbot.addWidget(row)
    labels = [a.text() for a in row.build_menu().actions() if a.text()]
    assert "Retry" not in labels


def test_p6_the_chain_icon_says_what_it_means(qtbot):
    row = _row(JobSpec(id="v", kind="validate", label="Validate", spec={}))
    qtbot.addWidget(row)
    assert "another queued job's output" in row._chain_glyph.toolTip()


def test_p7_the_empty_queue_mentions_setting_up_a_next_step(qtbot):
    from datasets.job_queue.view import JobQueuePage

    page = JobQueuePage()
    qtbot.addWidget(page)
    assert "New job using this output" in page._empty_hint.text()


def test_p8_enter_opens_the_job_and_the_menu_button_is_a_real_target(qtbot):
    from PySide6.QtCore import Qt

    row = _row(JobSpec(id="v", kind="validate", label="Validate", spec={}))
    qtbot.addWidget(row)
    opened = []
    row.rowClicked.connect(opened.append)
    qtbot.keyClick(row, Qt.Key_Return)
    assert opened == ["v"]
    assert row.focusPolicy() == Qt.StrongFocus
    assert row._menu_btn.minimumWidth() >= 28 and row._menu_btn.minimumHeight() >= 28


# ── P5: Analyze's Run greys while the input is pending ───────────────────────

def test_p5_analyze_run_greys_and_says_why(qtbot, tmp_path):
    from analyze.tab import AnalyzeTab

    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    tab._input_edit.setText(str(tmp_path / "planned"))
    tab.set_pending_inputs({"opts.raw": "⧗ Output of queued job “x”"})
    assert not tab._run_btn.isEnabled()
    assert "queued job" in tab._run_btn.toolTip()
    tab._input_edit.setText(str(tmp_path / "real"))
    assert tab._run_btn.isEnabled()


def test_m1_a_long_name_does_not_push_the_reason_out_of_the_sentence():
    long_label = "Convert CZI — day2_long_acquisition_name_for_the_Hoechst_GFP_timelapse.czi → plate"
    q = JobQueue([_conv("a", long_label), _concat()])
    q.set_link("cat", "dataset1", "a", kind="dataset_root")
    q.remove("a")
    message = q.get("cat").message
    assert "…" in message
    assert message.index("was removed") < 80, message


def test_m2_a_narrow_queue_page_keeps_every_row_s_menu_on_screen(qtbot):
    from datasets.job_queue.view import JobQueuePage

    page = JobQueuePage()
    qtbot.addWidget(page)
    page.set_jobs([JobSpec(id=f"j{i}", kind="subset", label=f"Subset — plate_{i} → out_{i}")
                   for i in range(3)])
    page.resize(320, 600)
    page.show()
    qtbot.waitExposed(page)
    viewport = page._scroll.viewport().width()
    for row in page._rows.values():
        right = row._menu_btn.mapTo(page._scroll.viewport(), row._menu_btn.rect().topRight()).x()
        assert right <= viewport, f"⋯ at {right} px in a {viewport} px viewport"
