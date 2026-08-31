"""The shared filename-schema builder, and the two pages that mount it.

``widgets.schema_picker.SchemaPicker`` is one control with two views of one
value — five position dropdowns and the schema string they write. The pair has
to stay in step in both directions without chasing each other, and what it
reports has to be what the pipeline would parse, because the Analyze form and
the Datasets pane's Zip Loose Folder page both hand its output to code that
parses filenames with ``util.schema``.

The two hosts get a case each at the bottom: they must be reading through the
picker, not keeping a second copy of the value.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from util.schema import DEFAULT_SCHEMA, DEFAULT_SEP, parse_schema
from widgets.schema_picker import FIELD_LABELS, POSITIONS, SchemaPicker


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def picker(qapp):
    return SchemaPicker()


# ── the two views ────────────────────────────────────────────────────────────

def test_opens_on_the_default_schema(picker):
    assert picker.schema() == DEFAULT_SCHEMA
    assert picker.separator() == DEFAULT_SEP
    assert picker.fields() == DEFAULT_SCHEMA.split(":")


def test_picking_a_field_rewrites_the_string(picker):
    picker.combos[0].setCurrentText("— ignore —")
    assert picker.schema().split(":")[0] == "ignore"
    assert picker.fields()[0] == "ignore"


def test_typing_a_string_resets_the_dropdowns(picker):
    picker.setSchema("well:channel:fov")
    assert picker.fields() == ["well", "channel", "fov", "ignore", "ignore"]
    assert picker.combos[0].currentText() == "Well"


def test_the_two_views_do_not_chase_each_other(picker):
    """Each write blocks the other's signals; without that the string edit's
    rewrite would re-enter the combo handler and back again."""
    seen: list[int] = []
    picker.changed.connect(lambda: seen.append(1))
    picker.combos[3].setCurrentText("Timepoint")
    assert len(seen) == 1


def test_changed_fires_for_the_separator(picker):
    seen: list[int] = []
    picker.changed.connect(lambda: seen.append(1))
    picker.setSeparator("-")
    assert seen and picker.separator() == "-"


def test_a_blank_separator_falls_back_to_the_default(picker):
    picker.separator_edit.setText("")
    assert picker.separator() == DEFAULT_SEP


# ── agreement with the parser the pipeline uses ──────────────────────────────

def test_every_offered_label_maps_to_a_field_the_parser_knows(picker):
    """A label the picker offers that ``parse_schema`` turns into "ignore" would
    be a field the user can select and the pipeline then discards."""
    for index, label in enumerate(FIELD_LABELS):
        picker.combos[0].setCurrentText(label)
        field = picker.fields()[0]
        assert parse_schema(field) == [field], f"{label!r} (item {index}) is not a parsed field"


def test_a_string_with_an_empty_token_reads_as_the_pipeline_reads_it(picker):
    """``parse_schema`` drops empty tokens, so "experiment::well" puts well in
    position 2 for the pipeline. The dropdowns have to show that, not hold the
    gap open and leave the user reading a different layout than the run uses."""
    picker.setSchema("experiment::well")
    assert picker.fields()[:2] == parse_schema("experiment::well")


def test_errors_are_the_parsers_verdict(picker):
    assert picker.errors() == []
    picker.setSchema("experiment:well:fov")          # no channel
    assert any("channel" in e for e in picker.errors())
    picker.setSchema("channel:well:well")            # two wells
    assert any("well" in e for e in picker.errors())


def test_a_schema_longer_than_the_picker_survives(picker):
    """The string edit can express more positions than there are dropdowns; the
    extra fields must not be truncated away by the round trip."""
    long_schema = ":".join(["experiment", "channel", "well", "fov", "timepoint", "ignore"])
    assert len(long_schema.split(":")) > POSITIONS
    picker.setSchema(long_schema)
    assert picker.schema() == long_schema


# ── the hosts ────────────────────────────────────────────────────────────────

def test_analyze_reads_its_schema_through_the_picker(qapp):
    from analyze.tab import AnalyzeTab

    tab = AnalyzeTab()
    tab._schema_picker.setSchema("plate:channel:well:site:tp")
    tab._schema_picker.setSeparator("-")
    assert tab._build_schema_arg() == "plate:channel:well:site:tp"
    assert tab._filename_sep() == "-"
    assert tab._collect_run_options()["filename_sep"] == "-"


def test_zip_loose_folder_reads_its_schema_through_the_picker(qapp):
    from datasets.import_loose.view import ImportLoosePage

    page = ImportLoosePage()
    page.set_filename_schema("plate:channel:well:site:tp")
    page.set_filename_sep("-")
    assert page.filename_schema() == "plate:channel:well:site:tp"
    assert page.filename_sep() == "-"


def test_zip_loose_folder_refuses_a_schema_the_run_would_reject(qapp, tmp_path):
    """The page used to accept any text. A schema with no "well" scans every
    file and groups none of them, and an in-place run shells out to
    WellPlateZipper.py, which exits 2 on exactly this."""
    from datasets.import_loose.view import ImportLoosePage

    src = tmp_path / "loose"
    src.mkdir()
    (src / "Exp01_GFP_A01_F001_T1.tif").write_bytes(b"")

    page = ImportLoosePage()
    page._src_field.set_path(str(src))
    page.set_in_place(True)
    page._on_source_changed()
    assert page._run_btn.isEnabled(), "a valid schema should leave the run available"

    page.set_filename_schema("experiment:channel:fov:timepoint")   # no well
    assert not page._run_btn.isEnabled()
    assert page._schema_err.isVisibleTo(page)
    assert "well" in page._schema_err.text()

    page.set_filename_schema(DEFAULT_SCHEMA)
    assert page._run_btn.isEnabled()
    assert not page._schema_err.isVisibleTo(page)


# ── layout, in a host that is not a narrow sidebar ──────────────────────────

def test_the_rows_share_one_left_edge(qapp):
    """Sized to each label's natural width, the seven rows step in and out by
    tens of pixels and the block reads as three separate groups."""
    from PySide6.QtWidgets import QLabel

    picker = SchemaPicker()
    picker.resize(900, 400)
    picker.show()
    for _ in range(5):
        qapp.processEvents()
    widths = {lbl.width() for lbl in picker.findChildren(QLabel)
              if lbl.objectName() == "Muted"}
    assert len(widths) == 1, f"label column is ragged: {sorted(widths)}"


def test_the_schema_edit_does_not_swallow_a_wide_page(qapp):
    """Its stretch is right in a 165px sidebar and absurd on a full-width page,
    where it became a 1300px box for a 37-character value."""
    picker = SchemaPicker()
    picker.resize(1440, 400)
    picker.show()
    for _ in range(5):
        qapp.processEvents()
    assert picker.schema_edit.width() <= 500, picker.schema_edit.width()


def test_typing_in_the_schema_string_reports_immediately(qapp):
    """The separator beside it is live per keystroke; this was not, so the
    readout, plate, error label and run gate all went on describing the
    previous schema until focus left the field."""
    from PySide6.QtTest import QTest

    picker = SchemaPicker()
    picker.show()
    for _ in range(3):
        qapp.processEvents()
    seen: list[int] = []
    picker.changed.connect(lambda: seen.append(1))
    picker.schema_edit.setFocus()
    QTest.keyClicks(picker.schema_edit, "x")
    for _ in range(3):
        qapp.processEvents()
    assert seen, "one keystroke reported nothing"


def test_the_separator_shows_the_fallback_it_will_use(qapp):
    """An empty box still separates on the default; substituting it invisibly
    makes the readout look like it is ignoring the field."""
    picker = SchemaPicker()
    assert picker.separator_edit.placeholderText() == DEFAULT_SEP
