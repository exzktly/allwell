"""The Analyze pane's pre-run report, driven the way a user drives it.

CLAUDE.md §7.6 is about a control that existed, was offered, was asserted
present by two tests, and did nothing when clicked — because no test ever
clicked it. So the load path here is exercised by pressing the button and
reading the form afterwards, never by calling the callback directly. Reaching
past the widget is exactly the seam that would hide a broken connection.

Builds a real ``AnalyzeTab`` but **not** a ``WellViewerApp``, so these stay in
the fast lane (ARCHITECTURE §10.4). Do not reach for the ``viewer_app``
fixture here.
"""

from __future__ import annotations

import json
import shutil
import zipfile

import pytest
from PySide6.QtWidgets import QLabel

from pathlib import Path

from services import run_journal as J

SCHEMA = "experiment:well:fov:timepoint:channel"
OTHER_SCHEMA = "experiment:channel:well:fov:timepoint"
SEP = "_"


@pytest.fixture(autouse=True)
def _isolated_index(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "state" / "idx.json")


@pytest.fixture
def plate(tmp_path):
    """A two-well plate: A01 finished, A02 has no measurements at all."""
    root = tmp_path / "plate"
    in_dir, out_dir = root / "in", root / "out"
    in_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)
    for well in ("A01", "A02"):
        with zipfile.ZipFile(in_dir / f"{well}.zip", "w") as zf:
            for fov in ("F001", "F002"):
                zf.writestr(f"Exp_{well}_{fov}_T01_DAPI.tif", b"x")
    (out_dir / "measurements_A01.csv").write_text(
        "well,fov,timepoint\nA01,F001,T01\nA01,F002,T01\n")
    with zipfile.ZipFile(out_dir / "A01_out.zip", "w") as zf:
        zf.writestr("Exp_A01_F001_T01_DAPI_overlay.tif", b"x")
        zf.writestr("Exp_A01_F002_T01_DAPI_overlay.tif", b"x")
    (out_dir / "pipeline_info.json").write_text(json.dumps({
        "schema": SCHEMA, "separator": SEP,
        "nuclear_token": "DAPI", "fluor_tokens": ["GFP"],
    }))
    return root


def _journal(out_dir, in_dir, *, closed=None, spec_opts=None, wells=()):
    J.open_run(out_dir, mode="local", input_dir=in_dir, argv=[],
               spec={"opts": spec_opts if spec_opts is not None else {
                   "filename_schema": SCHEMA, "filename_sep": SEP,
                   "nuclear_token": "DAPI", "fluor_tokens": ["GFP"]}})
    for well, state in wells:
        J.record_well(out_dir, well, state)
    if closed is not None:
        J.close_run(out_dir, outcome=closed)


@pytest.fixture
def tab(qtbot):
    from analyze_tab import AnalyzeTab
    widget = AnalyzeTab()
    qtbot.addWidget(widget)
    return widget


def _point_at(tab, path) -> None:
    """Point the form at *path* and let the field settle, as a user does.

    Setting the text is a keystroke; the banners read the folder once the field
    stops changing (``analyze_preflight._SETTLE_MS``). Driving the settle here
    rather than waiting out a real 400 ms in every test runs the same code the
    timer runs — it is not a way around the debounce.
    """
    tab._input_edit.setText(str(path))
    tab._preflight_timer.stop()
    tab.settle_folder_banners()


def _text(tab) -> str:
    return tab._preflight_label.text()


def _sidebar_text(tab) -> str:
    """Everything the two folder strips have on screen, whichever holds it.

    The report and the resume offer are built from the same journal, so while
    the offer is up for this folder the report drops its own account of the run
    rather than printing it a second time (``_resume_card_covers``, and
    ``tests/test_the_two_cards_do_not_say_the_same_thing.py``). Tests whose
    subject is *that the user is told* therefore read both, since which of the
    two says it is exactly what that suppression moves around.
    """
    labels = [tab._preflight_label]
    labels += tab._resume_banner.findChildren(QLabel)
    return " ".join(lbl.text() for lbl in labels if lbl.text())


class _FakeThread:
    """A ``threading.Thread`` stand-in that records the start and runs nothing.

    The point of these tests is *how many* walks are started, so the walk
    itself must not run: a real one would emit the completion signal and clear
    the in-flight marker before the assertion could see it.
    """

    def __init__(self, started: list[str], kw: dict) -> None:
        self._started = started
        self._name = str(kw.get("name") or "")

    def start(self) -> None:
        self._started.append(self._name)


# ── tier one: what the journal says ─────────────────────────────────────────

def test_an_unfinished_run_is_reported(tab, plate):
    """Read across both strips: an unfinished run is also a *resumable* one, so
    the offer above the report is up for this same folder and the report defers
    its own account of the run to it. What must hold either way is that the
    sidebar says the run never finished and says what it recorded."""
    _journal(plate / "out", plate / "in",
             wells=[("A01", "fetched"), ("A02", "not-fetched")])
    _point_at(tab, plate)
    assert not tab._resume_banner.isHidden()
    assert "never finished" in _sidebar_text(tab)
    assert "1 finished" in _sidebar_text(tab)
    assert "1 never fetched" in _sidebar_text(tab)


@pytest.mark.parametrize("outcome,phrase", [
    ("ok", "completed"),
    ("error", "Failed run"),
    ("stopped", "was stopped"),
    ("suspended", "was suspended"),
])
def test_a_closed_run_is_reported_by_how_it_ended(tab, plate, outcome, phrase):
    """A run that ended is not the same as one that died, and the four ways it
    can end are not the same as each other.

    Read across both strips, and the expected wording is each strip's own. Only
    ``ok`` leaves nothing to resume, so only ``ok`` is still said by the report;
    the other three are said by the offer above it, which leads with the state
    as two words. The point of the test is unchanged — four endings, four
    distinct strings, none of them the crash wording.
    """
    _journal(plate / "out", plate / "in", closed=outcome)
    _point_at(tab, plate)
    assert phrase in _sidebar_text(tab)
    assert "never finished" not in _sidebar_text(tab)


def test_a_folder_with_a_sidecar_but_no_journal_is_still_reported(tab, plate):
    """Every dataset produced before the journal existed. The feature would be
    useless on exactly the plates a user already has if this were silent."""
    _point_at(tab, plate)
    assert not tab._preflight_banner.isHidden()
    assert "earlier run" in _text(tab)


def test_an_empty_output_folder_reports_nothing(tab, tmp_path):
    root = tmp_path / "fresh"
    (root / "in").mkdir(parents=True)
    (root / "out").mkdir(parents=True)
    with zipfile.ZipFile(root / "in" / "A01.zip", "w") as zf:
        zf.writestr("Exp_A01_F001_T01_DAPI.tif", b"x")
    _point_at(tab, root)
    assert tab._preflight_banner.isHidden()


def test_dismissing_hides_it_until_the_folder_changes(tab, plate, tmp_path):
    # A *completed* run, so the report is the strip carrying the sentence: an
    # unfinished one is resumable, and the offer above would take the sentence
    # over, leaving this test measuring the suppression instead of the
    # dismissal it is about.
    _journal(plate / "out", plate / "in", closed="ok")
    _point_at(tab, plate)
    assert not tab._preflight_banner.isHidden()
    tab._dismiss_preflight()
    assert tab._preflight_banner.isHidden()
    _point_at(tab, tmp_path)
    _point_at(tab, plate)
    assert tab._preflight_banner.isHidden(), "a dismissal must stick for its folder"


# ── the offer: press the button ─────────────────────────────────────────────

def test_clicking_load_puts_the_recorded_settings_on_the_form(tab, plate, qtbot):
    """The §7.6 test: press it, then read the form.

    Asserting the button is *there* is what let an unopenable editor ship. The
    schema is the field to check — it is the one the previous run's settings
    have to reach for the coverage scan below to parse the plate at all.
    """
    _journal(plate / "out", plate / "in")
    _point_at(tab, plate)

    from PySide6.QtCore import Qt
    qtbot.mouseClick(tab._preflight_load_btn, Qt.LeftButton)

    opts = tab._collect_run_options()
    assert opts["filename_schema"] == SCHEMA
    assert opts["nuclear_token"] == "DAPI"


def test_the_run_scope_knobs_are_never_restored(tab, plate, qtbot):
    """``force`` says what the *last* run was told to do. Restoring it would
    silently re-force a plate because the previous run happened to be forced —
    hours of compute nobody asked for."""
    _journal(plate / "out", plate / "in", spec_opts={
        "filename_schema": SCHEMA, "filename_sep": SEP, "nuclear_token": "DAPI",
        "force": True, "force_wells": ["A01"], "incremental": True})
    _point_at(tab, plate)

    from PySide6.QtCore import Qt
    qtbot.mouseClick(tab._preflight_load_btn, Qt.LeftButton)

    opts = tab._collect_run_options()
    assert opts["filename_schema"] == SCHEMA, "the rest of the settings did apply"
    assert not opts.get("force")
    assert not opts.get("incremental")


def test_the_sidecar_is_the_fallback_when_there_is_no_journal(tab, plate, qtbot):
    _point_at(tab, plate)
    from PySide6.QtCore import Qt
    qtbot.mouseClick(tab._preflight_load_btn, Qt.LeftButton)
    assert tab._collect_run_options()["filename_schema"] == SCHEMA


def test_loading_settings_keeps_the_folder_the_user_chose(tab, plate, qtbot):
    """The user picked this folder; the button offers its *settings*.

    ``execution_options`` is a ``vars(args)`` snapshot, so it always carries the
    ``input_dir`` of the machine the run happened on. Applying that replaced the
    user's own folder with a path that need not exist locally at all — which is
    every dataset produced by anyone else, and the sidecar fallback is exactly
    the case where the run was someone else's.
    """
    (plate / "out" / "pipeline_info.json").write_text(json.dumps({
        "execution_options": {
            "filename_schema": SCHEMA, "filename_sep": SEP,
            "nuclear_token": "DAPI",
            "input_dir": "/Volumes/labshare/someone-else/PlateX/in"}}))
    _point_at(tab, plate)
    from PySide6.QtCore import Qt
    qtbot.mouseClick(tab._preflight_load_btn, Qt.LeftButton)

    assert tab._input_edit.text() == str(plate), (
        "the folder on the form was replaced by the one the sidecar was "
        "written on")
    assert tab._collect_run_options()["nuclear_token"] == "DAPI", (
        "the settings themselves must still apply")


# ── tier two: coverage, and refusing to state a bad one ─────────────────────

def test_coverage_is_counted_from_the_resolved_input_dir(plate):
    """Not from the raw folder the user typed.

    Handed the folder holding ``in/`` and ``out/``, the scan finds no well
    containers, counts field groups from the output alone, and reports more
    covered than the input was ever measured to hold.
    """
    from analyze_preflight import _scan_coverage
    opts = {"filename_schema": SCHEMA, "filename_sep": SEP,
            "csv_prefix": "measurements"}
    block = _scan_coverage(plate / "in", plate / "out", opts)
    assert block["totals"]["intent"] == 4
    assert block["totals"]["covered_any"] == 2

    wrong = _scan_coverage(plate, plate / "out", opts)
    assert wrong["totals"]["covered_any"] > wrong["totals"]["intent"], (
        "the fixture no longer reproduces the mis-read this guards")


def test_the_coverage_line_states_the_measured_fraction(tab, plate):
    from analyze_preflight import _scan_coverage
    _journal(plate / "out", plate / "in")
    _point_at(tab, plate)
    tab._apply_pipeline_info({"execution_options": {
        "filename_schema": SCHEMA, "filename_sep": SEP}})
    block = _scan_coverage(plate / "in", plate / "out", tab._collect_run_options())
    tab._on_coverage_scanned(str(plate / "out"), block)
    assert "2 of 4 field group(s) already covered" in _text(tab)
    assert "1 of 2 well(s) are complete" in _text(tab)


def test_an_incoherent_fraction_is_refused_rather_than_printed(tab, plate):
    """More covered than intended is not a plate that is more than finished —
    it is the schema failing to parse one side. Printing the ratio anyway is
    the software knowing something is wrong and saying it in numbers.

    The wrong schema is the realistic cause: the field order shifts, so the
    ``(fov, timepoint)`` keys the input yields and the ones the output yields
    stop being the same keys, and the two counts stop being comparable.
    """
    from analyze_preflight import _scan_coverage
    _point_at(tab, plate)
    block = _scan_coverage(plate / "in", plate / "out", {
        "filename_schema": OTHER_SCHEMA, "filename_sep": SEP,
        "csv_prefix": "measurements"})
    totals = block["totals"]
    assert totals["covered_any"] > totals["intent"] > 0, (
        "the fixture no longer reproduces an incoherent comparison")
    tab._on_coverage_scanned(str(plate / "out"), block)
    assert "could not be compared" in _text(tab)
    assert "field group(s) already covered" not in _text(tab)


def test_an_unmeasurable_intent_claims_no_fraction(tab, plate):
    """The other half of the same rule. An input the scan could not read at
    all gives a denominator of zero, and a denominator of zero gets no
    fraction rather than a fabricated one."""
    from analyze_preflight import _scan_coverage
    _point_at(tab, plate)
    block = _scan_coverage(plate, plate / "out", {
        "filename_schema": SCHEMA, "filename_sep": SEP,
        "csv_prefix": "measurements"})
    assert block["totals"]["intent"] == 0
    tab._on_coverage_scanned(str(plate / "out"), block)
    assert "field group(s)" not in _text(tab)
    assert "well(s) are complete" in _text(tab)


def test_a_scan_for_another_folder_is_discarded(tab, plate):
    from analyze_preflight import _scan_coverage
    _journal(plate / "out", plate / "in")
    _point_at(tab, plate)
    before = _text(tab)
    block = _scan_coverage(plate / "in", plate / "out", {
        "filename_schema": SCHEMA, "filename_sep": SEP,
        "csv_prefix": "measurements"})
    tab._on_coverage_scanned("/somewhere/else/out", block)
    assert _text(tab) == before


def test_a_second_settle_does_not_put_a_second_walk_on_the_same_plate(
        tab, plate, monkeypatch):
    """The settle timer bounds how often a scan is *asked* for, not how long
    one takes. A plate on a slow volume outlives several settles, and without a
    bound each settle would start another thread walking the same directory."""
    started: list[str] = []
    monkeypatch.setattr("analyze_preflight.threading.Thread",
                        lambda **kw: _FakeThread(started, kw))
    _journal(plate / "out", plate / "in")

    _point_at(tab, plate)
    assert len(started) == 1, "the first settle did not start a scan"
    tab._preflight_timer.stop()
    tab.settle_folder_banners()
    tab._preflight_timer.stop()
    tab.settle_folder_banners()
    assert len(started) == 1, "a settle started a second walk of the same plate"

    # The one in flight lands; the pane is free to scan again.
    tab._on_coverage_scanned(str(plate / "out"), None)
    tab._preflight_timer.stop()
    tab.settle_folder_banners()
    assert len(started) == 2


def test_a_folder_chosen_mid_scan_is_walked_once_the_scan_lands(
        tab, plate, tmp_path, monkeypatch):
    """Holding the request is only safe if it is honoured. Dropping it would
    leave the coverage line describing the folder the user has moved away
    from, with nothing scheduled to correct it."""
    started: list[str] = []
    monkeypatch.setattr("analyze_preflight.threading.Thread",
                        lambda **kw: _FakeThread(started, kw))
    _journal(plate / "out", plate / "in")
    # A real second plate, not an empty pair of folders: the planner declines
    # to resolve a directory with no archives in it, so the form would never
    # name a second output folder to hold a request for.
    other = tmp_path / "other"
    shutil.copytree(plate, other)
    _journal(other / "out", other / "in")

    _point_at(tab, plate)
    assert len(started) == 1
    _point_at(tab, other)                  # while the first is still walking
    assert len(started) == 1, "a second walk started before the first landed"

    tab._on_coverage_scanned(str(plate / "out"), None)
    assert len(started) == 2, "the folder chosen mid-scan was never walked"
    assert tab._preflight_scanning == str(other / "out")


def test_a_scan_that_cannot_be_started_does_not_wedge_every_later_one(
        tab, plate, monkeypatch):
    """Nothing emits the signal that clears the in-flight marker when the
    thread never runs, so it has to come down by hand."""
    def _refuse(**kw):
        raise RuntimeError("can't start new thread")
    monkeypatch.setattr("analyze_preflight.threading.Thread", _refuse)
    _journal(plate / "out", plate / "in")

    _point_at(tab, plate)
    assert tab._preflight_scanning == ""


def test_typing_a_path_starts_at_most_one_scan(tab, plate, monkeypatch):
    """``textChanged`` fires per keystroke and each scan opens every archive in
    the plate, so a typed path would otherwise start one per character."""
    starts: list[int] = []
    monkeypatch.setattr(type(tab), "_start_coverage_scan",
                        lambda self: starts.append(1))
    text = str(plate)
    for i in range(1, len(text) + 1):
        tab._input_edit.setText(text[:i])
    assert starts == [], "a scan ran before the field settled"


def test_typing_a_path_touches_no_files_until_it_settles(tab, plate, tmp_path):
    """Every read behind these banners is per-folder, and the field changes once
    per keystroke. Doing them inline cost 9.6 ms and 140 file opens for a single
    character typed, on a machine with a dozen plates on record — the journal of
    *every* plate ever run was being read to find the one on the form.

    So the keystroke path must open nothing at all, and the settle must open
    only what this folder needs.
    """
    _journal(plate / "out", plate / "in", closed="error")
    for i in range(12):                       # other plates in the index
        other = tmp_path / f"Other{i}" / "out"
        other.mkdir(parents=True)
        J.open_run(other, mode="local", input_dir=tmp_path / f"Other{i}" / "in",
                   argv=[], spec={"opts": {}})

    opened: list[str] = []
    real = Path.open

    def counting(self, *a, **k):
        opened.append(str(self))
        return real(self, *a, **k)

    text = str(plate)
    tab._input_edit.setText(text[:-1])        # warm, and arm the timer
    tab._preflight_timer.stop()
    Path.open = counting
    try:
        tab._input_edit.setText(text)         # one keystroke
        assert opened == [], (
            f"the keystroke path opened {len(opened)} file(s); it must defer "
            f"every read to the settle")
        tab._preflight_timer.stop()
        tab.settle_folder_banners()
        journals = [o for o in opened if "aw_journal" in o]
        assert len(journals) <= 4, (
            f"the settle read {len(journals)} journal files for one folder — "
            f"the index is filtered before its entries are opened")
    finally:
        Path.open = real


# ── the scope line comes from the planner, not from here ────────────────────

def test_the_scope_line_is_the_planners_own_sentence(tab, plate):
    """``tests/test_layout_readout_single_source`` holds this over the layout
    line beside it, for the same reason: the preview and the run must not be
    able to disagree about what the run will do."""
    from services.input_resolution_service import plan_incremental_scope
    (plate / "in" / "Exp_A01_F003_T01_DAPI.tif").write_bytes(b"x")
    _journal(plate / "out", plate / "in")
    _point_at(tab, plate)
    tab._apply_pipeline_info({"execution_options": {
        "filename_schema": SCHEMA, "filename_sep": SEP}})
    expected = plan_incremental_scope(tab._collect_run_options(), plate / "in")
    assert expected.preview, "the fixture should have loose images to fold in"
    assert tab._scope_line() == expected.preview
    assert "Appended" not in tab._scope_line(), (
        "the readout prints the log's past-tense sentence, claiming work it "
        "has not done")
    assert expected.reason.startswith("Appended"), (
        "the run log keeps its past tense — it prints after the fold")


# ── after a run ends ────────────────────────────────────────────────────────

def _finish(tab, outcome, out_dir, in_dir, wells=()):
    """Close a journal the way a run does, then settle the tab as _poll_log does."""
    J.open_run(out_dir, mode="local", input_dir=in_dir, argv=[], spec={
        "opts": {"filename_schema": SCHEMA, "filename_sep": SEP}})
    for well, state in wells:
        J.record_well(out_dir, well, state)
    J.close_run(out_dir, outcome=outcome)
    tab._run_ok = outcome == "ok"
    tab._running = False
    tab._on_run_settled()


def test_the_readout_is_hidden_while_a_run_is_going(tab, plate):
    """What it says describes the folder as it stood *before* the run, and the
    run is in the middle of making that untrue."""
    _journal(plate / "out", plate / "in", closed="ok")
    _point_at(tab, plate)
    assert not tab._preflight_banner.isHidden()
    tab._set_running_ui_state(remote=None)
    assert tab._preflight_banner.isHidden()


def test_the_readout_re_reads_the_folder_after_a_failure(tab, plate):
    """The defect this closes: nothing re-read the folder after a run, because
    the readout is driven by ``textChanged`` and after a run that never fires.
    So a failure left the sidebar reporting the run *before* it — as completed.
    """
    _journal(plate / "out", plate / "in", closed="ok")
    _point_at(tab, plate)
    assert "completed" in _text(tab)

    tab._set_running_ui_state(remote=None)
    _finish(tab, "error", plate / "out", plate / "in", wells=[("A01", "analysed")])

    # The failed run is resumable, so the offer above now carries the account
    # of it and the report keeps the coverage and scope lines. What this test
    # is about — that *something* re-read the folder, rather than the sidebar
    # still describing the run before this one — is read across both.
    assert not tab._resume_banner.isHidden()
    assert "Failed run" in _sidebar_text(tab)
    assert "completed" not in _sidebar_text(tab)


def test_a_dismissal_does_not_outlive_the_run_that_followed_it(tab, plate):
    """A dismissal is scoped to the folder as it stood when it was waved away.
    A run has since written to that folder, which is new information."""
    _journal(plate / "out", plate / "in", closed="ok")
    _point_at(tab, plate)
    tab._dismiss_preflight()
    assert tab._preflight_banner.isHidden()

    _finish(tab, "error", plate / "out", plate / "in")
    assert not tab._preflight_dismissed, (
        "the dismissal was scoped to the folder as it stood when it was waved "
        "away, and a run has since written to it")
    # And the sidebar is describing the folder again. The run that just failed
    # is resumable, so it is the offer that says so.
    assert not tab._resume_banner.isHidden()


def test_the_stale_coverage_line_is_dropped_after_a_run(tab, plate):
    """The folder has not changed, so nothing else would clear it — but it was
    measured against the output as it stood before the run rewrote it."""
    from analyze_preflight import _scan_coverage
    _journal(plate / "out", plate / "in", closed="ok")
    _point_at(tab, plate)
    tab._apply_pipeline_info({"execution_options": {
        "filename_schema": SCHEMA, "filename_sep": SEP}})
    block = _scan_coverage(plate / "in", plate / "out", tab._collect_run_options())
    tab._on_coverage_scanned(str(plate / "out"), block)
    assert "field group(s) already covered" in _text(tab)

    _finish(tab, "error", plate / "out", plate / "in")
    assert "field group(s) already covered" not in _text(tab)


# ── a late scan must not undo an answer the user already gave ───────────────
#
# The coverage scan walks the output volume on a daemon thread and lands
# whenever it lands. ``settle_folder_banners`` checked both visibility gates
# and ``_render_preflight`` -- the seam every scan completion paints through --
# checked neither, so a scan started before Run was pressed put the strip back
# up mid-run, describing the folder the running job was overwriting.

def _deliver_scan(tab, plate, *, partial=False):
    """Land a *real* finished coverage scan through the app's own handler."""
    from analyze_preflight import _scan_coverage

    tab._apply_pipeline_info({"execution_options": {
        "filename_schema": SCHEMA, "filename_sep": SEP}})
    block = _scan_coverage(plate / "in", plate / "out",
                           tab._collect_run_options())
    if partial:
        block = {**block, "partial_scan": True}
    tab._on_coverage_scanned(str(plate / "out"), block)


def test_a_scan_landing_mid_run_does_not_put_the_report_back(tab, plate):
    # Closed ``ok`` for the reason the dismissal test gives: this needs the
    # report itself on screen to watch it stay down.
    _journal(plate / "out", plate / "in", closed="ok")
    _point_at(tab, plate)
    assert not tab._preflight_banner.isHidden(), "fixture did not show the strip"

    tab._running = True          # the user presses Run
    tab.suspend_preflight()
    assert tab._preflight_banner.isHidden()

    _deliver_scan(tab, plate)   # ...and the slow scan finally answers
    assert tab._preflight_banner.isHidden(), (
        "the pre-run report came back during the run, describing the folder "
        "the running job is overwriting"
    )


def test_a_scan_landing_after_a_dismissal_does_not_put_the_report_back(tab, plate):
    _journal(plate / "out", plate / "in")
    _point_at(tab, plate)
    tab._dismiss_preflight()
    assert tab._preflight_banner.isHidden()

    _deliver_scan(tab, plate)
    assert tab._preflight_banner.isHidden(), (
        "a dismissed report came back on its own, and the user has no second "
        "dismissal to give because they were never re-offered it"
    )


def test_a_partial_scan_landing_mid_run_is_equally_silent(tab, plate):
    """The partial branch is a second, earlier return through the same seam."""
    _journal(plate / "out", plate / "in")
    _point_at(tab, plate)
    tab._running = True
    tab.suspend_preflight()
    _deliver_scan(tab, plate, partial=True)
    assert tab._preflight_banner.isHidden()


def test_a_scan_landing_with_neither_gate_set_still_shows_the_report(tab, plate):
    """The gates must not have turned the feature off."""
    _journal(plate / "out", plate / "in")
    _point_at(tab, plate)
    _deliver_scan(tab, plate)
    assert not tab._preflight_banner.isHidden()
    assert "field group(s) already covered" in _text(tab), _text(tab)
