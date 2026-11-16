"""The resume card can be dismissed while the form names an input not written yet.

Setting up a queued Analyze job puts a folder in the input field that an
earlier job has not written. Such a folder plans to no output directory, and
the settle path handed the card ``""`` — "the form names nothing, offer the
newest unfinished run anywhere" — so the card came up for some other plate.
Discard, Load settings and Resume then asked the form again, got "names an
input that cannot be resolved", found no offer and returned without a word:
a card none of its own buttons could take down.
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
    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "state" / "idx.json")
    monkeypatch.setattr(
        suspend_record, "record_path",
        lambda: tmp_path / "home" / ".allwell" / "suspended_runs.json")


def _stopped_plate(root, name):
    plate = root / name
    (plate / "in").mkdir(parents=True)
    (plate / "out").mkdir(parents=True)
    with zipfile.ZipFile(plate / "in" / "A01.zip", "w") as zf:
        zf.writestr("Exp_A01_F001_T01_DAPI.tif", b"x")
    (plate / "out" / "pipeline_info.json").write_text(json.dumps(
        {"schema": SCHEMA, "separator": SEP, "nuclear_token": "DAPI"}))
    J.open_run(plate / "out", mode="local", input_dir=plate / "in", argv=[],
               spec={"opts": {"raw": str(plate), "filename_schema": SCHEMA,
                              "filename_sep": SEP, "nuclear_token": "DAPI"}})
    J.record_well(plate / "out", "A01", "failed")
    J.close_run(plate / "out", outcome="stopped")
    return plate


@pytest.fixture
def tab(qtbot):
    from analyze.tab import AnalyzeTab
    widget = AnalyzeTab()
    qtbot.addWidget(widget)
    return widget


def _point_at(tab, path) -> None:
    tab._input_edit.setText(str(path))
    tab._preflight_timer.stop()
    tab.settle_folder_banners()


def test_an_unwritten_input_offers_no_other_plate(tab, tmp_path):
    """The folder a queued conversion will write is not 'no folder': the card
    must not stand in some other plate's stopped run for it."""
    _stopped_plate(tmp_path, "Elsewhere")
    _point_at(tab, tmp_path / "not_written_yet")
    assert tab._resume_banner.isHidden(), tab._resume_title.text()


def test_the_card_its_buttons_act_on_is_the_card_on_screen(tab, tmp_path, monkeypatch):
    """Whatever the card shows, Discard must take it down — the offer the
    buttons act on is the offer the card was painted from."""
    import analyze.suspend

    _stopped_plate(tmp_path, "Elsewhere")
    _point_at(tab, "")                     # an empty form: newest anywhere
    assert not tab._resume_banner.isHidden(), "the premise: a card is up"
    # The queue session then writes an input that does not exist yet without
    # the field settling (show_job / hydrate_job fill it programmatically).
    tab._input_edit.blockSignals(True)
    tab._input_edit.setText(str(tmp_path / "not_written_yet"))
    tab._input_edit.blockSignals(False)

    monkeypatch.setattr(analyze.suspend.QMessageBox, "question",
                        lambda *a, **k: analyze.suspend.QMessageBox.Yes)
    tab._discard_suspended()
    assert tab._resume_banner.isHidden()
