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
import zipfile

import pytest

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


def _text(tab) -> str:
    return tab._preflight_label.text()


# ── tier one: what the journal says ─────────────────────────────────────────

def test_an_unfinished_run_is_reported(tab, plate):
    _journal(plate / "out", plate / "in",
             wells=[("A01", "fetched"), ("A02", "not-fetched")])
    tab._input_edit.setText(str(plate))
    assert not tab._preflight_banner.isHidden()
    assert "never finished" in _text(tab)
    assert "1 finished" in _text(tab) and "1 never fetched" in _text(tab)


@pytest.mark.parametrize("outcome,phrase", [
    ("ok", "completed"),
    ("error", "failed"),
    ("stopped", "was stopped"),
    ("suspended", "was suspended"),
])
def test_a_closed_run_is_reported_by_how_it_ended(tab, plate, outcome, phrase):
    """A run that ended is not the same as one that died, and the four ways it
    can end are not the same as each other."""
    _journal(plate / "out", plate / "in", closed=outcome)
    tab._input_edit.setText(str(plate))
    assert phrase in _text(tab) and "never finished" not in _text(tab)


def test_a_folder_with_a_sidecar_but_no_journal_is_still_reported(tab, plate):
    """Every dataset produced before the journal existed. The feature would be
    useless on exactly the plates a user already has if this were silent."""
    tab._input_edit.setText(str(plate))
    assert not tab._preflight_banner.isHidden()
    assert "earlier run" in _text(tab)


def test_an_empty_output_folder_reports_nothing(tab, tmp_path):
    root = tmp_path / "fresh"
    (root / "in").mkdir(parents=True)
    (root / "out").mkdir(parents=True)
    with zipfile.ZipFile(root / "in" / "A01.zip", "w") as zf:
        zf.writestr("Exp_A01_F001_T01_DAPI.tif", b"x")
    tab._input_edit.setText(str(root))
    assert tab._preflight_banner.isHidden()


def test_dismissing_hides_it_until_the_folder_changes(tab, plate, tmp_path):
    _journal(plate / "out", plate / "in")
    tab._input_edit.setText(str(plate))
    assert not tab._preflight_banner.isHidden()
    tab._dismiss_preflight()
    assert tab._preflight_banner.isHidden()
    tab._input_edit.setText(str(tmp_path))
    tab._input_edit.setText(str(plate))
    assert tab._preflight_banner.isHidden(), "a dismissal must stick for its folder"


# ── the offer: press the button ─────────────────────────────────────────────

def test_clicking_load_puts_the_recorded_settings_on_the_form(tab, plate, qtbot):
    """The §7.6 test: press it, then read the form.

    Asserting the button is *there* is what let an unopenable editor ship. The
    schema is the field to check — it is the one the previous run's settings
    have to reach for the coverage scan below to parse the plate at all.
    """
    _journal(plate / "out", plate / "in")
    tab._input_edit.setText(str(plate))

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
    tab._input_edit.setText(str(plate))

    from PySide6.QtCore import Qt
    qtbot.mouseClick(tab._preflight_load_btn, Qt.LeftButton)

    opts = tab._collect_run_options()
    assert opts["filename_schema"] == SCHEMA, "the rest of the settings did apply"
    assert not opts.get("force")
    assert not opts.get("incremental")


def test_the_sidecar_is_the_fallback_when_there_is_no_journal(tab, plate, qtbot):
    tab._input_edit.setText(str(plate))
    from PySide6.QtCore import Qt
    qtbot.mouseClick(tab._preflight_load_btn, Qt.LeftButton)
    assert tab._collect_run_options()["filename_schema"] == SCHEMA


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
    tab._input_edit.setText(str(plate))
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
    tab._input_edit.setText(str(plate))
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
    tab._input_edit.setText(str(plate))
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
    tab._input_edit.setText(str(plate))
    before = _text(tab)
    block = _scan_coverage(plate / "in", plate / "out", {
        "filename_schema": SCHEMA, "filename_sep": SEP,
        "csv_prefix": "measurements"})
    tab._on_coverage_scanned("/somewhere/else/out", block)
    assert _text(tab) == before


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


# ── the scope line comes from the planner, not from here ────────────────────

def test_the_scope_line_is_the_planners_own_sentence(tab, plate):
    """``tests/test_layout_readout_single_source`` holds this over the layout
    line beside it, for the same reason: the preview and the run must not be
    able to disagree about what the run will do."""
    from services.input_resolution_service import plan_incremental_scope
    (plate / "in" / "Exp_A01_F003_T01_DAPI.tif").write_bytes(b"x")
    _journal(plate / "out", plate / "in")
    tab._input_edit.setText(str(plate))
    tab._apply_pipeline_info({"execution_options": {
        "filename_schema": SCHEMA, "filename_sep": SEP}})
    expected = plan_incremental_scope(tab._collect_run_options(), plate / "in")
    assert expected.reason, "the fixture should have loose images to fold in"
    assert tab._scope_line() == expected.reason
