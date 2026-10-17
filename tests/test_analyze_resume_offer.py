"""Which unfinished run the resume banner offers, and what it says about it.

Three things went wrong here at once and each is pinned below.

A run that **failed** offered nothing, because ``close_run`` stamps
``closed_at`` and only a still-open record counted as resumable — yet a failure
leaves exactly what a crash leaves, every finished well's output on disk, and is
the case where a user most wants to carry on.

The offer was taken **newest-anywhere**, unscoped to the folder in the form, so
the banner could propose a run against a plate the user was not looking at while
the pre-run report directly beneath it described a different one — and **Resume**
would then act on the plate that was not on screen.

And every offer named the *output folder*, which is the literal word ``out`` for
essentially every dataset, so it identified nothing.

Builds a real ``AnalyzeTab`` but no ``ReviewApp``, so this stays in the fast
lane. Presses the buttons rather than calling the callbacks — asserting a
control is offered is not asserting it works.
"""

from __future__ import annotations

import json
import time
import zipfile

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from services import run_journal as J
from services import suspend_record

SCHEMA = "experiment:well:fov:timepoint:channel"
SEP = "_"


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Both records go to temp files.

    The suspend record lives under ``Path.home()`` and the journal index under
    the state dir; a test writing to either real file would leave the developer's
    own app offering to resume a fixture.
    """
    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "state" / "idx.json")
    monkeypatch.setattr(
        suspend_record, "record_path",
        lambda: tmp_path / "home" / ".allwell" / "suspended_runs.json")


def _plate(root, name):
    """A minimal runnable dataset at ``<root>/<name>`` with one well."""
    plate = root / name
    (plate / "in").mkdir(parents=True)
    (plate / "out").mkdir(parents=True)
    with zipfile.ZipFile(plate / "in" / "A01.zip", "w") as zf:
        zf.writestr("Exp_A01_F001_T01_DAPI.tif", b"x")
    (plate / "out" / "pipeline_info.json").write_text(json.dumps(
        {"schema": SCHEMA, "separator": SEP, "nuclear_token": "DAPI"}))
    return plate


def _journal(plate, *, outcome=None, wells=()):
    """Open a journal for *plate*, and close it unless *outcome* is ``None``."""
    J.open_run(plate / "out", mode="local", input_dir=plate / "in", argv=[],
               spec={"opts": {"filename_schema": SCHEMA, "filename_sep": SEP,
                              "nuclear_token": "DAPI"}})
    for well, state in wells:
        J.record_well(plate / "out", well, state)
    if outcome is not None:
        J.close_run(plate / "out", outcome=outcome)


@pytest.fixture
def tab(qtbot):
    from analyze.tab import AnalyzeTab
    widget = AnalyzeTab()
    qtbot.addWidget(widget)
    return widget


def _point_at(tab, path) -> None:
    """Point the form at *path* and let the field settle, as a user does.

    Setting the text is a keystroke; the banners read the folder once the field
    stops changing (``analyze.preflight._SETTLE_MS``). Driving the settle here
    rather than waiting out a real 400 ms in every test runs the same code the
    timer runs — it is not a way around the debounce.
    """
    tab._input_edit.setText(str(path))
    tab._preflight_timer.stop()
    tab.settle_folder_banners()


def _text(tab) -> str:
    """Everything the strip has on screen, whichever label holds it.

    The offer is rendered across four labels — the state it is in, which plate
    and when, the prose, and the run's own tally — because one wrapping
    paragraph beside two buttons left the text 149 px of the 329-px sidebar.
    Reading the labels rather than one of them is also the stronger assertion:
    these tests are about what the user is told, not about which widget the
    words landed in.
    """
    return " ".join(lbl.text() for lbl in tab._resume_banner.findChildren(QLabel)
                    if lbl.text())


# ── which runs are offered at all ───────────────────────────────────────────

@pytest.mark.parametrize("outcome,phrase", [
    (None, "never finished"),
    ("error", "stopped on an error"),
    ("stopped", "was stopped"),
])
def test_a_run_that_did_not_finish_is_offered(tab, tmp_path, outcome, phrase):
    """A failure and a stop are offered alongside a crash. All three leave the
    same thing behind, and only the account of why differs."""
    plate = _plate(tmp_path, "PlateA")
    _journal(plate, outcome=outcome)
    _point_at(tab, plate)
    assert not tab._resume_banner.isHidden()
    assert phrase in _text(tab)


def test_a_completed_run_is_not_offered(tab, tmp_path):
    plate = _plate(tmp_path, "PlateA")
    _journal(plate, outcome="ok")
    _point_at(tab, plate)
    assert tab._resume_banner.isHidden()


def test_the_per_well_tally_is_reported(tab, tmp_path):
    plate = _plate(tmp_path, "PlateA")
    _journal(plate, outcome="error",
             wells=[("A01", "analysed"), ("A02", "failed"),
                    ("A03", "not-fetched")])
    _point_at(tab, plate)
    assert "1 finished" in _text(tab)
    assert "1 failed" in _text(tab)
    assert "1 never fetched" in _text(tab)


# ── which plate the offer is about ──────────────────────────────────────────

def test_the_offer_stays_on_the_folder_in_the_form(tab, tmp_path):
    """The defect: newest-anywhere could offer a plate the user was not looking
    at, directly above a pre-run report describing a different one — and Resume
    would act on the one off screen."""
    shown = _plate(tmp_path, "PlateShown")
    other = _plate(tmp_path, "PlateOther")
    _journal(shown, outcome="error")
    _journal(other, outcome=None)          # newer, and still open

    _point_at(tab, shown)
    assert "PlateShown" in _text(tab)
    assert "PlateOther" not in _text(tab)


def test_no_offer_when_the_form_names_a_folder_with_no_record(tab, tmp_path):
    """Offering some other plate here answers a question the user did not ask;
    the pre-run report is what speaks for the folder on screen."""
    _plate(tmp_path, "PlateShown")
    other = _plate(tmp_path, "PlateOther")
    _journal(other, outcome="error")

    _point_at(tab, tmp_path / "PlateShown")
    assert tab._resume_banner.isHidden()


def test_a_record_from_a_previous_session_is_offered_at_startup(tmp_path, qtbot):
    """The banner's headline use case, which never worked across a restart.

    Both strips are built hidden and were only refreshed by a run ending or the
    folder changing, so a record written yesterday had nothing to reveal it —
    "close All-Well, reopen it tomorrow, and it offers to carry on" did not
    survive the restart it names. The tab is constructed *after* the record
    exists here because that is the order the app starts in.
    """
    other = _plate(tmp_path, "PlateOther")
    _journal(other, outcome="error")

    from analyze.tab import AnalyzeTab
    fresh = AnalyzeTab()
    qtbot.addWidget(fresh)

    assert fresh._input_edit.text().strip() == "", "the form opens empty"
    assert not fresh._resume_banner.isHidden()
    assert "PlateOther" in _text(fresh), (
        "with no folder on the form, newest-anywhere is the right fallback")


def test_the_offer_names_the_plate_not_the_output_folder(tab, tmp_path):
    """Every dataset's output folder is ``<plate>/out``, so naming the folder
    identifies nothing and identifies it identically for two plates."""
    plate = _plate(tmp_path, "PlateA")
    _journal(plate, outcome="error")
    _point_at(tab, plate)
    assert tab._resume_title.text().startswith("PlateA"), (
        "the identity line leads with the plate, not with the folder it "
        "happens to be written into")
    assert not tab._resume_title.text().startswith("out")


@pytest.mark.parametrize("out_dir,expected", [
    ("/data/Plate1/out", "Plate1"),
    ("/data/Plate1/OUT", "Plate1"),
    ("/data/Plate1", "Plate1"),
    ("out", "out"),
])
def test_plate_name_reads_through_the_out_folder(out_dir, expected):
    assert suspend_record.plate_name(out_dir) == expected


# ── pressing the buttons ────────────────────────────────────────────────────

def test_resume_starts_a_run_against_the_offered_plate(tab, tmp_path, qtbot,
                                                       monkeypatch):
    """Press it, do not call it. And check *which* spec it started with — the
    whole risk of an unscoped offer is a run against the wrong plate."""
    plate = _plate(tmp_path, "PlateA")
    _journal(plate, outcome="error")
    _point_at(tab, plate)

    started: list = []
    monkeypatch.setattr(type(tab), "start_run",
                        lambda self, spec, **kw: started.append(spec) or None)
    resume_btn = next(b for b in tab._resume_banner.findChildren(type(tab._run_btn))
                      if b.text() == "Resume")
    qtbot.mouseClick(resume_btn, Qt.LeftButton)

    assert len(started) == 1
    spec = started[0]
    assert spec.opts["incremental"] is True, (
        "without it the pipeline skips every well whose CSV exists and a "
        "half-done plate comes back half-done, reporting success")
    assert spec.opts["filename_schema"] == SCHEMA


def test_resume_shows_the_resumed_run_s_settings_on_the_form(tmp_path, qtbot,
                                                              monkeypatch):
    """The run starts from the recorded spec, so the form went on showing what
    it held before — an empty input, default options — beside a dashboard
    reporting a different run. After Resume the sidebar reads as that run."""
    plate = _plate(tmp_path, "PlateA")
    J.open_run(plate / "out", mode="local", input_dir=plate / "in", argv=[],
               spec={"opts": {"raw": str(plate / "in"),
                              "filename_schema": SCHEMA, "filename_sep": SEP,
                              "nuclear_token": "Hoechst", "csv_prefix": "cells",
                              "remote_batch_size": "12", "incremental": False}})
    J.close_run(plate / "out", outcome="stopped")

    from analyze.tab import AnalyzeTab
    fresh = AnalyzeTab()                # opens empty, as the app does
    qtbot.addWidget(fresh)
    assert fresh._nuclear_token_edit.text() != "Hoechst"
    monkeypatch.setattr(type(fresh), "start_run", lambda self, spec, **kw: None)
    resume_btn = next(b for b in fresh._resume_banner.findChildren(type(fresh._run_btn))
                      if b.text() == "Resume")
    qtbot.mouseClick(resume_btn, Qt.LeftButton)

    assert fresh._input_edit.text() == str(plate / "in")
    assert fresh._nuclear_token_edit.text() == "Hoechst"
    assert fresh._csv_prefix_edit.text() == "cells"
    assert fresh._remote_batch_size_edit.text() == "12"
    assert fresh._incremental_cb.isChecked(), (
        "the run is forced incremental, so the form must say so too")


def test_resuming_stops_the_offer_repeating(tab, tmp_path, qtbot, monkeypatch):
    plate = _plate(tmp_path, "PlateA")
    _journal(plate, outcome="error")
    _point_at(tab, plate)
    monkeypatch.setattr(type(tab), "start_run", lambda self, spec, **kw: None)

    resume_btn = next(b for b in tab._resume_banner.findChildren(type(tab._run_btn))
                      if b.text() == "Resume")
    qtbot.mouseClick(resume_btn, Qt.LeftButton)

    # Asserted at the banner, which is what "the offer stops repeating" means.
    # The index entry itself now *stays*, carrying a dismissal: it is also what
    # `resume_aware_spec` reads to force `--incremental` on the next Run, and
    # deleting it silently disarmed that recovery.
    assert tab._resumable() is None, "the banner still offers the same run"
    entries = [e for e in J.unfinished_runs()
               if str(e.get("output_dir")) == str(plate / "out")]
    assert entries and entries[0].get("dismissed"), (
        "the entry was deleted rather than dismissed, so the next Run will not "
        "be forced incremental and every mid-flight well is skipped as complete")
    # The journal itself stays: it is the dataset's provenance.
    assert J.read_journal(plate / "out") is not None


# ── ranking the two records against each other ──────────────────────────────

def _suspend(plate, *, at: float, done: int = 1, total: int = 10) -> None:
    """Record *plate* as deliberately suspended, stopped at *at*."""
    suspend_record.save(suspend_record.SuspendedRun(
        spec={"opts": {"raw": str(plate), "filename_schema": SCHEMA,
                       "filename_sep": SEP, "nuclear_token": "DAPI"}},
        input_dir=str(plate / "in"), output_dir=str(plate / "out"),
        suspended_at=at, wells_done=done, wells_total=total,
    ))


def test_with_no_folder_on_the_form_the_newest_of_either_record_is_offered(
        tab, tmp_path):
    """Reported by the manual and by ARCHITECTURE, and not what it did.

    The two records were read into separate lists and concatenated, so every
    suspended run preceded every journal one however old. A plate suspended
    months ago beat one the app was killed on minutes ago — in exactly the
    empty-form case both documents describe as "the most recent unfinished run
    on any plate", and the state the app opens in.
    """
    old = _plate(tmp_path, "PLATE-OLD")
    new = _plate(tmp_path, "PLATE-NEW")
    _suspend(old, at=1_000.0)
    _journal(new)                                   # still open: it was killed

    offer = tab._resumable("")
    assert offer is not None
    assert offer.output_dir == str(new / "out"), (
        f"offered {offer.output_dir}, which is not the newest record")
    assert offer.from_journal is True


def test_the_older_record_wins_when_it_is_the_newer_one(tab, tmp_path):
    """The mirror, so the fix is a *ranking* and not a reversed preference."""
    old = _plate(tmp_path, "PLATE-OLD")
    new = _plate(tmp_path, "PLATE-NEW")
    _journal(old, outcome="error")
    _suspend(new, at=time.time() + 60.0)            # unambiguously the newest

    offer = tab._resumable("")
    assert offer is not None
    assert offer.output_dir == str(new / "out")
    assert offer.from_journal is False


def test_with_no_folder_on_the_form_one_plate_s_suspend_record_still_wins(
        tab, tmp_path):
    """A suspend writes both records, and the journal is closed *after* the
    suspend record is stamped — so ranked by time alone, the journal's offer
    for the same plate always won in the empty-form case, against the tie rule
    stated for it."""
    plate = _plate(tmp_path, "PLATE")
    _suspend(plate, at=time.time() - 60.0)
    _journal(plate, outcome="suspended")            # closed now, after it

    offer = tab._resumable("")
    assert offer is not None
    assert offer.output_dir == str(plate / "out")
    assert offer.from_journal is False, "the journal's offer outranked the suspend record"


def test_for_one_folder_the_suspend_record_still_wins(tab, tmp_path):
    """Ranking across plates must not disturb the specificity rule.

    Both records describing the *same* folder is not a question of recency: the
    user asked for the suspend, so it is the more specific statement about that
    plate, whichever was written last.
    """
    plate = _plate(tmp_path, "PLATE")
    _journal(plate)                                 # written now
    _suspend(plate, at=1_000.0)                     # written long ago

    offer = tab._resumable(str(plate / "out"))
    assert offer is not None
    assert offer.from_journal is False


def test_a_resume_the_run_refuses_leaves_the_user_s_form_alone(tmp_path, qtbot, monkeypatch):
    """Resume put the recorded run's settings on the form *before* ``start_run`` could refuse — and right after a
    crash it usually does, because the earlier run is still alive. The run did not start, the form now held the
    recorded settings with Incremental ticked, and the user's own edits were gone: the next Run used settings they
    had never chosen. Driven through the real ``start_run``; only the liveness probe answers "still running"."""
    import analyze.run_spec as run_spec_mod
    import analyze.suspend as suspend_mod

    plate = _plate(tmp_path, "PlateA")
    J.open_run(plate / "out", mode="local", input_dir=plate / "in", argv=[],
               spec={"opts": {"raw": str(plate / "in"), "filename_schema": SCHEMA, "filename_sep": SEP,
                              "nuclear_token": "Hoechst", "csv_prefix": "cells", "incremental": False},
                     "remote_name": "a-remote-this-install-no-longer-has"})
    J.close_run(plate / "out", outcome="stopped")

    from analyze.tab import AnalyzeTab
    fresh = AnalyzeTab()
    qtbot.addWidget(fresh)
    _point_at(fresh, plate)
    fresh._nuclear_token_edit.setText("MyNuclei")
    fresh._csv_prefix_edit.setText("mine")
    assert not fresh._incremental_cb.isChecked()
    refused = "An earlier run on this plate is still running."
    monkeypatch.setattr(run_spec_mod, "_earlier_run_still_alive", lambda spec: refused)
    shown: list[str] = []
    monkeypatch.setattr(suspend_mod.QMessageBox, "critical", lambda _p, _t, text, *a, **k: shown.append(text))
    logged: list[str] = []
    real_log = fresh._log_line
    monkeypatch.setattr(fresh, "_log_line", lambda text, *a, **k: (logged.append(text), real_log(text, *a, **k)))

    resume_btn = next(b for b in fresh._resume_banner.findChildren(type(fresh._run_btn)) if b.text() == "Resume")
    qtbot.mouseClick(resume_btn, Qt.LeftButton)

    assert shown == [refused], "the refusal was not shown"
    assert fresh._nuclear_token_edit.text() == "MyNuclei", "a refused resume replaced the user's settings"
    assert fresh._csv_prefix_edit.text() == "mine"
    assert fresh._input_edit.text() == str(plate)
    assert not fresh._incremental_cb.isChecked(), "a refused resume left Incremental ticked on the form"
    assert not any("saving will move it" in line for line in logged), (
        "a job-queue editor's warning was logged for a resume that never started")
    assert fresh._resumable() is not None, "the offer must survive a resume that did not start"
