"""The Analyze form's **Detect from filenames** button, driven as a user drives it.

Every test here presses the real ``QPushButton`` on a real ``AnalyzeTab``
rather than calling the handler behind it. That is the whole point of the file:
the inference, the readout and the handler can each be correct while the button
reaches none of them, and only a press can tell. Writing this feature produced
exactly that failure — ``setDetection`` landed between an ``@property`` and its
``def`` in the picker, so the method existed, read correctly, and raised
``TypeError`` the first time the button was pressed. No unit test of any piece
would have seen it.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _plate(in_dir, *, extra=()):
    """A small well-formed plate, plus any *extra* stems that are not."""
    in_dir.mkdir(parents=True)
    for w in ("A01", "A02", "B01"):
        for c in ("DAPI", "GFP"):
            for f in ("f0", "f1"):
                (in_dir / f"exp_{c}_{w}_{f}_t0.tif").write_bytes(b"")
    for stem in extra:
        (in_dir / f"{stem}.tif").write_bytes(b"")
    return 12 + len(extra)


def _tab():
    from analyze.tab import AnalyzeTab
    return AnalyzeTab()


def test_the_button_exists_and_is_connected_to_something_that_runs(qapp, tmp_path):
    """Excludes the button wired to nothing — which renders identically."""
    total = _plate(tmp_path / "ds" / "in")
    tab = _tab()
    tab._input_edit.setText(str(tmp_path / "ds"))
    button = tab._schema_picker.detectButton()
    assert button is not None, "the Analyze host asked for the detect control"

    button.click()

    assert tab._schema_picker.schema() == "experiment:channel:well:fov:timepoint"
    assert tab._schema_picker.separator() == "_"
    assert f"All {total} filenames sampled matched" in tab._schema_picker.detectionText()


def test_the_readout_names_the_files_the_guess_does_not_parse(qapp, tmp_path):
    """Excludes the readout that reports only the schema it chose.

    ``ZZ9`` splits into the right five tokens, so a count built on token
    agreement alone calls this plate clean.
    """
    total = _plate(tmp_path / "ds" / "in", extra=("exp_GFP_ZZ9_f0_t0",))
    tab = _tab()
    tab._input_edit.setText(str(tmp_path / "ds"))
    tab._schema_picker.detectButton().click()

    assert f"Matched {total - 1} of {total}" in tab._schema_picker.detectionText()
    # The shortfall is the sentence about the user's data, so it lands in the
    # note register rather than in the neutral summary that is styled to skim.
    note = tab._schema_picker.detectionNoteText()
    assert "1 of the" in note and "does not fit this layout" in note
    assert "ZZ9" in note


def test_an_empty_input_field_is_answered_rather_than_scanned(qapp):
    """Excludes the handler that scans the process's working directory."""
    tab = _tab()
    tab._input_edit.setText("")
    tab._schema_picker.detectButton().click()

    assert "Choose an input folder first" in tab._schema_picker.detectionNoteText()
    assert tab._schema_picker.schema() == "experiment:channel:well:fov:timepoint"


def test_a_folder_with_no_detectable_layout_says_so_and_changes_nothing(qapp, tmp_path):
    """Excludes the silent decline — the failure the readout exists to end.

    A refusal and a success must not look alike, so the refusal states that the
    schema below is unchanged and is styled as a problem rather than as a
    quieter success.
    """
    src = tmp_path / "ds" / "in"
    src.mkdir(parents=True)
    for i in range(6):
        (src / f"scan{i}.tif").write_bytes(b"")
    tab = _tab()
    tab._schema_picker.setSchema("channel:well:fov")
    tab._input_edit.setText(str(tmp_path / "ds"))
    tab._schema_picker.detectButton().click()

    assert "Couldn't read a layout" in tab._schema_picker.detectionNoteText()
    assert tab._schema_picker.schema() == "channel:well:fov", "left as the user set it"


def test_pressing_detect_again_after_typing_over_the_guess_restores_it(qapp, tmp_path):
    """The reason the button exists at all: the scan already runs at browse
    time, so what is new is being able to ask a second time."""
    _plate(tmp_path / "ds" / "in")
    tab = _tab()
    tab._input_edit.setText(str(tmp_path / "ds"))
    tab._schema_picker.detectButton().click()
    tab._schema_picker.setSchema("well:channel:ignore:ignore:ignore")
    assert tab._schema_picker.schema() == "well:channel:ignore:ignore:ignore"

    tab._schema_picker.detectButton().click()

    assert tab._schema_picker.schema() == "experiment:channel:well:fov:timepoint"


def test_the_browse_path_writes_the_same_readout_as_the_button(qapp, tmp_path):
    """Two ways of asking one question are two answers waiting to disagree."""
    _plate(tmp_path / "ds" / "in")
    by_browse = _tab()
    by_browse._autofill_from_input_scan(str(tmp_path / "ds"))

    by_button = _tab()
    by_button._input_edit.setText(str(tmp_path / "ds"))
    by_button._schema_picker.detectButton().click()

    assert by_browse._schema_picker.detectionText() != ""
    assert by_browse._schema_picker.detectionText() == by_button._schema_picker.detectionText()


def test_the_control_says_what_it_is_for_before_it_is_pressed(qapp):
    """Excludes the button that is live from launch with nothing to say.

    It can only produce a refusal until a folder is chosen, which teaches the
    user in their first ten seconds that the feature does not work.
    """
    tab = _tab()
    assert "Pick an input folder" in tab._schema_picker.detectionText()
    assert tab._schema_picker.detectionNoteText() == "", "a hint is not a problem"


def test_a_shortfall_reaches_the_run_dashboard_too(qapp, tmp_path):
    """Excludes the dashboard that asserts a clean scan the sidebar qualifies.

    A reader who takes the top line and skips the sidebar would otherwise start
    a run believing every file is in play.
    """
    _plate(tmp_path / "ds" / "in", extra=("exp_GFP_ZZ9_f0_t0",))
    tab = _tab()
    tab._input_edit.setText(str(tmp_path / "ds"))
    seen = []
    tab._report_scan_guess = lambda tokens, **kw: seen.append(kw)
    tab._schema_picker.detectButton().click()

    assert seen and seen[-1].get("shortfall") == 1


# ── where the button sits ────────────────────────────────────────────────────
# Measured on the real hosts, laid out and shown, because the order is the
# behaviour: a layout index can say the button was added first while a host's
# own chrome puts it somewhere else, and only geometry says where it is drawn.

def _origin_in(widget, ancestor):
    from PySide6.QtCore import QPoint
    return widget.mapTo(ancestor, QPoint(0, 0))


def _shown(qapp, widget, width):
    widget.resize(width, 1400)
    widget.show()
    for _ in range(5):
        qapp.processEvents()


def _assert_detect_heads_the_picker(picker):
    """The button is above every row it fills and starts where they start."""
    button = picker.detectButton()
    assert button is not None and button.isVisibleTo(picker)
    top = _origin_in(button, picker)
    rows = [picker.separator_edit, *picker.combos, picker.schema_edit]
    below = [_origin_in(w, picker).y() for w in rows]
    assert top.y() < min(below), (
        f"Detect is drawn at y={top.y()}, not above the schema rows at {below}")
    field_x = _origin_in(picker.schema_edit, picker).x()
    assert top.x() == field_x, (
        f"Detect starts at x={top.x()}, the schema field at x={field_x}: "
        f"the button and the rows it fills should share one left edge")


def test_detect_is_the_first_row_of_the_analyze_schema_section(qapp, tmp_path):
    """Excludes the button at the foot of the section, where it sat beneath
    every row it rewrites — and where its own refusal had to say "above"."""
    _plate(tmp_path / "ds" / "in")
    tab = _tab()
    _shown(qapp, tab, 900)
    picker = tab._schema_picker
    _assert_detect_heads_the_picker(picker)

    # Still the button that works from up there.
    picker.setSchema("well:channel:ignore:ignore:ignore")
    tab._input_edit.setText(str(tmp_path / "ds"))
    picker.detectButton().click()
    assert picker.schema() == "experiment:channel:well:fov:timepoint"


def test_detect_is_the_first_row_of_the_zip_loose_folder_page(qapp, tmp_path):
    """The wide host: the plain row factory, on a full-width page.

    Excludes, besides the button at the foot, the schema string that floated
    to the middle of a wide page once its width cap bound — ~440px right of
    the button and every other row, so "aligned with the schema field" had no
    single edge to mean.
    """
    from datasets.import_loose.view import ImportLoosePage

    src = tmp_path / "loose"
    src.mkdir()
    for w in ("A01", "A02"):
        for c in ("DAPI", "GFP"):
            (src / f"exp_{c}_{w}_f0_t0.tif").write_bytes(b"")
    page = ImportLoosePage()
    _shown(qapp, page, 1440)
    picker = page._schema_picker
    _assert_detect_heads_the_picker(picker)
    # Anchored left by a spacer, not by an alignment flag, which would also
    # shrink the field to its minimum: it still grows to its cap here.
    from widgets.schema_picker import _SCHEMA_EDIT_MAX_W
    assert picker.schema_edit.width() == _SCHEMA_EDIT_MAX_W, picker.schema_edit.width()

    picker.setSchema("well:channel:ignore:ignore:ignore")
    page._src_field.set_path(str(src))
    picker.detectButton().click()
    assert picker.schema() == "experiment:channel:well:fov:timepoint"


def test_a_refusal_points_at_the_settings_where_they_are(qapp, tmp_path):
    """The refusal names a direction; it must be the direction the rows are in.

    The readout sits under the button, so every setting it leaves alone is
    beneath it — a refusal saying "above" pointed at the section's heading.
    """
    src = tmp_path / "ds" / "in"
    src.mkdir(parents=True)
    for i in range(6):
        (src / f"scan{i}.tif").write_bytes(b"")
    tab = _tab()
    _shown(qapp, tab, 900)
    picker = tab._schema_picker
    tab._input_edit.setText(str(tmp_path / "ds"))
    picker.detectButton().click()
    for _ in range(5):
        qapp.processEvents()

    note = picker.detectionNoteText()
    assert "your settings below are unchanged" in note, note
    note_lbl = picker._detect_note_lbl
    assert note_lbl.isVisibleTo(picker)
    note_bottom = _origin_in(note_lbl, picker).y() + note_lbl.height()
    assert note_bottom <= _origin_in(picker.separator_edit, picker).y(), (
        "the refusal says 'below', so the settings must be drawn below it")
