"""The Analyze sidebar states an unfinished run once, not twice.

Two cards sit at the head of the sidebar and are built from the same run
journal: the resume offer (``analyze_suspend``) and the pre-run report
(``analyze_preflight``). With an unfinished run on the folder named in the
form, both used to lead with the same fact and both appended the journal's own
``tally()`` — so the sidebar said, in two bordered cards stacked one above the
other:

    Interrupted run
    Plate_07 · started 24 Aug 18:30
    It never finished — All-Well closed while it was going. …
    It recorded 12 finished, 3 failed.

    A run started 24 Aug 18:30 never finished — All-Well closed while it was
    going. It recorded 12 finished, 3 failed.

The report now drops its own account of the run while the card above is
covering the same folder, and keeps what only it knows — the coverage scan and
the scope line. It takes the sentence back the moment that card goes away,
which is the half worth testing: Discard removes the offer and leaves the
report as the only thing that can say what the last run did.
"""
from __future__ import annotations

import json
import zipfile

import pytest

from services import run_journal as J
from services import suspend_record

SCHEMA = "experiment:well:fov:timepoint:channel"
SEP = "_"


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Both records go to temp files, never the developer's own."""
    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "state" / "idx.json")
    monkeypatch.setattr(
        suspend_record, "record_path",
        lambda: tmp_path / "home" / ".allwell" / "suspended_runs.json")


def _plate(root, name):
    plate = root / name
    (plate / "in").mkdir(parents=True)
    (plate / "out").mkdir(parents=True)
    with zipfile.ZipFile(plate / "in" / "A01.zip", "w") as zf:
        zf.writestr("Exp_A01_F001_T01_DAPI.tif", b"x")
    (plate / "out" / "pipeline_info.json").write_text(json.dumps(
        {"schema": SCHEMA, "separator": SEP, "nuclear_token": "DAPI"}))
    return plate


def _journal(plate, *, outcome=None, wells=()):
    J.open_run(plate / "out", mode="local", input_dir=plate / "in", argv=[],
               spec={"opts": {"filename_schema": SCHEMA, "filename_sep": SEP,
                              "nuclear_token": "DAPI"}})
    for well, state in wells:
        J.record_well(plate / "out", well, state)
    if outcome is not None:
        J.close_run(plate / "out", outcome=outcome)


@pytest.fixture
def tab(qtbot):
    from analyze_tab import AnalyzeTab
    widget = AnalyzeTab()
    qtbot.addWidget(widget)
    return widget


def _point_at(tab, path) -> None:
    """Point the form at *path* and let the field settle, as a user does."""
    tab._input_edit.setText(str(path))
    tab._preflight_timer.stop()
    tab.settle_folder_banners()


def _report(tab) -> str:
    return tab._preflight_label.text()


# ``isHidden`` rather than ``isVisible``: the tab is never shown in these
# tests, and every child of an unshown parent reports ``isVisible() is False``
# whatever its own flag says. ``isHidden`` reads the widget's own state, which
# is the thing the banners set.


# ── the duplication ─────────────────────────────────────────────────────────

def test_the_report_drops_the_account_the_card_above_already_gave(tab, tmp_path):
    plate = _plate(tmp_path, "PlateA")
    _journal(plate, wells=[("A01", "analysed")])      # left open: interrupted
    _point_at(tab, plate)

    assert not tab._resume_banner.isHidden(), "the offer is the premise of this test"
    assert "never finished" not in _report(tab), _report(tab)
    assert "It recorded" not in _report(tab), (
        "the journal's tally is in the card above; a second copy of it in the "
        "card below is the duplication this test exists for")


def test_the_report_still_says_it_when_no_card_covers_the_folder(tab, tmp_path):
    """A run that *completed* gets no resume offer, so nothing else on screen
    reports it — the report must keep saying so."""
    plate = _plate(tmp_path, "PlateB")
    _journal(plate, outcome="ok", wells=[("A01", "analysed")])
    _point_at(tab, plate)

    assert tab._resume_banner.isHidden()
    assert "completed" in _report(tab), _report(tab)
    assert "It recorded" in _report(tab), _report(tab)


def test_the_report_takes_the_sentence_back_when_the_offer_goes(tab, tmp_path,
                                                                monkeypatch):
    """Discard removes the card above. The report is then the only thing left
    that can say what the last run did, so it has to notice."""
    import analyze_suspend

    plate = _plate(tmp_path, "PlateC")
    _journal(plate, outcome="error", wells=[("A01", "failed")])
    _point_at(tab, plate)
    assert not tab._resume_banner.isHidden()
    assert "It recorded" not in _report(tab)

    # Discard asks first; answer yes without a modal (which blocks forever
    # offscreen — see tests/conftest.py's _no_modal_dialogs).
    monkeypatch.setattr(analyze_suspend.QMessageBox, "question",
                        lambda *a, **k: analyze_suspend.QMessageBox.Yes)
    tab._discard_suspended()

    assert tab._resume_banner.isHidden()
    assert "It recorded" in _report(tab), (
        "the offer is gone and the report is the only surface left that knows "
        "the run failed part-way, so it must state it again")


def test_an_offer_for_another_plate_does_not_silence_the_report(tab, tmp_path):
    """The card falls back to the newest unfinished run on *any* plate when the
    form names no folder. Such an offer says nothing about the folder the
    report describes, so it must not suppress it."""
    other = _plate(tmp_path, "Elsewhere")
    _journal(other, outcome="error")
    here = _plate(tmp_path, "Here")
    _journal(here, outcome="ok", wells=[("A01", "analysed")])

    _point_at(tab, here)
    assert tab._preflight_dir and "Here" in tab._preflight_dir
    assert not tab._resume_card_covers(tab._preflight_dir)
    assert "It recorded" in _report(tab), _report(tab)


def test_the_report_is_not_an_empty_bordered_box(tab, tmp_path):
    """With its sentence suppressed and no coverage line arrived, there is
    nothing in the card but its two buttons. It waits for the scan rather than
    showing a bordered strip with no words in it."""
    plate = _plate(tmp_path, "PlateD")
    _journal(plate)
    _point_at(tab, plate)

    if not _report(tab):
        assert tab._preflight_banner.isHidden()
