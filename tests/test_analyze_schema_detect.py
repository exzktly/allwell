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
    assert f"all {total} sampled filenames parse" in tab._schema_picker.detectionText()


def test_the_readout_names_the_files_the_guess_does_not_parse(qapp, tmp_path):
    """Excludes the readout that reports only the schema it chose.

    ``ZZ9`` splits into the right five tokens, so a count built on token
    agreement alone calls this plate clean.
    """
    total = _plate(tmp_path / "ds" / "in", extra=("exp_GFP_ZZ9_f0_t0",))
    tab = _tab()
    tab._input_edit.setText(str(tmp_path / "ds"))
    tab._schema_picker.detectButton().click()

    text = tab._schema_picker.detectionText()
    assert f"{total - 1} of {total}" in text
    assert "ZZ9" in text


def test_an_empty_input_field_is_answered_rather_than_scanned(qapp):
    """Excludes the handler that scans the process's working directory."""
    tab = _tab()
    tab._input_edit.setText("")
    tab._schema_picker.detectButton().click()

    assert "Choose an input folder first" in tab._schema_picker.detectionText()
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

    assert "No filename layout could be detected" in tab._schema_picker.detectionText()
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
