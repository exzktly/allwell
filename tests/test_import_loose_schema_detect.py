"""**Detect from filenames** on the Zip Loose Folder page.

The page parses the very filenames Analyze goes on to read, and shares the
schema control with it, but never called the inference — so the user typed by
hand, on this page, what the other pane fills in for them.

What is *not* shared is the verdict, and that is the point of the file: Analyze
applies a guess only when it found a well **and** a channel, because it has to
name the channels it will quantify. This page only groups files by well. A
single-channel plate carries a constant channel token, so no channel slot can be
placed — and refusing such a guess here would decline a layout that groups
perfectly well, for a reason belonging to a different page.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from datasets.import_loose.import_loose_compute import detection_for_grouping  # noqa: E402
from util.schema_infer import infer_schema  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _page():
    from datasets.import_loose.view import ImportLoosePage
    return ImportLoosePage()


def _write(folder, *, channels=("DAPI", "GFP")):
    folder.mkdir(parents=True, exist_ok=True)
    n = 0
    for well in ("A01", "A02", "B01"):
        for fov in ("f0", "f1"):
            for chan in channels or (None,):
                stem = (f"exp_{chan}_{well}_{fov}_t0" if chan
                        else f"exp_{well}_{fov}_t0")
                (folder / f"{stem}.tif").write_bytes(b"img")
                n += 1
    return n


# ── the rule, without a page ────────────────────────────────────────────────

def test_a_guess_with_no_well_slot_is_refused_for_grouping():
    """Excludes the rule that applies whatever the inference returned.

    Grouping by well is the page's only job, so a layout with no well position
    cannot do it and saying so beats writing the schema and failing later.
    """
    guess = infer_schema([f"plate_{c}_{n}" for c in ("DAPI", "GFP")
                          for n in ("1", "2", "3")])
    verdict = detection_for_grouping(guess)
    assert verdict.apply is False
    assert verdict.problem is True
    assert "No well position was identified" in verdict.message


def test_a_single_channel_plate_is_applied_here_although_analyze_would_refuse_it():
    """Excludes the rule copied from Analyze — the defect this split exists for.

    ``confident`` means a well *and* a channel. These names have a constant
    channel token, so no channel slot is placed and ``confident`` is False,
    yet the well slot is right there and grouping needs nothing else.
    """
    guess = infer_schema([f"exp_{w}_{f}_t0" for w in ("A01", "A02", "B01")
                          for f in ("f0", "f1")])
    assert guess.confident is False, "the premise: Analyze would decline this"
    assert "well" in guess.fields

    verdict = detection_for_grouping(guess)
    assert verdict.apply is True
    assert verdict.problem is False
    assert "No channel position was identified" in verdict.message


def test_nothing_detected_is_a_refusal_that_says_the_schema_is_unchanged():
    verdict = detection_for_grouping(None)
    assert verdict.apply is False and verdict.problem is True
    assert "unchanged" in verdict.message


# ── the button, pressed ─────────────────────────────────────────────────────

def test_pressing_detect_fills_the_picker_from_the_folder(qapp, tmp_path):
    """Excludes the button wired to nothing, which renders identically."""
    total = _write(tmp_path / "loose")
    page = _page()
    page._src_field.set_path(str(tmp_path / "loose"))
    button = page._schema_picker.detectButton()
    assert button is not None

    button.click()

    assert page._schema_picker.schema() == "experiment:channel:well:fov:timepoint"
    assert page._schema_picker.separator() == "_"
    assert f"All {total} filenames sampled matched" in page._schema_picker.detectionText()


def test_pressing_detect_on_a_single_channel_folder_applies_it_here(qapp, tmp_path):
    """The page-level half of the rule above, through the real control."""
    _write(tmp_path / "loose", channels=())
    page = _page()
    page._src_field.set_path(str(tmp_path / "loose"))
    page._schema_picker.detectButton().click()

    assert page._schema_picker.schema() == "experiment:well:fov:timepoint"
    assert "No channel position was identified" in page._schema_picker.detectionText()


def test_an_empty_source_field_is_answered_rather_than_scanned(qapp):
    page = _page()
    page._schema_picker.detectButton().click()
    assert "Choose a loose-TIF folder first" in page._schema_picker.detectionText()


def test_a_refused_detection_leaves_the_schema_the_user_set(qapp, tmp_path):
    src = tmp_path / "loose"
    src.mkdir()
    for i in range(6):
        (src / f"scan{i}.tif").write_bytes(b"img")
    page = _page()
    page._schema_picker.setSchema("well:channel:ignore:ignore:ignore")
    page._src_field.set_path(str(src))

    page._schema_picker.detectButton().click()

    assert "No filename layout could be detected" in page._schema_picker.detectionNoteText()
    assert page._schema_picker.schema() == "well:channel:ignore:ignore:ignore"


def test_the_page_states_the_shortfall_once_not_twice(qapp, tmp_path):
    """Excludes the host that copies Analyze's second register onto this page.

    The page's own readout already ends with "N of M file(s) didn't match the
    schema and would be left alone (e.g. …)". Repeating it directly above, in
    the same size and colour and a different vocabulary, is redundancy rather
    than emphasis.
    """
    src = tmp_path / "loose"
    _write(src)
    (src / "exp_GFP_ZZ9_f0_t0.tif").write_bytes(b"img")
    page = _page()
    page._src_field.set_path(str(src))
    page._schema_picker.detectButton().click()

    assert page._schema_picker.detectionNoteText() == ""
    assert "ZZ9" not in page._schema_picker.detectionText()
