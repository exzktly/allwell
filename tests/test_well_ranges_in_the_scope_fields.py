"""*Only*, *Skip* and *Force these wells* take rectangles of wells written with a colon.

``A01:C06`` is rows A–C by columns 1–6, the reading the background-well field
already gives it (``util.well_token.expand_well_range``). An entry that is
neither a well nor a rectangle stops the run with a message naming it: the
pipeline side canonicalises these lists and drops what it cannot read, so an
*Only these wells* field holding only a mistyped range used to send no
``--only_wells`` at all and run the whole plate.
"""

from __future__ import annotations

import pytest

from analyze.domain.well_list import parse_well_list, unreadable_wells, well_scope_error


def test_a_colon_between_two_wells_is_the_rectangle_they_span():
    assert parse_well_list("A01:C03") == [
        "A01", "A02", "A03", "B01", "B02", "B03", "C01", "C02", "C03"]


def test_either_corner_may_come_first_and_spacing_and_padding_do_not_matter():
    assert parse_well_list("c3 : a1") == parse_well_list("A01:C03")


def test_a_single_row_or_column_is_a_rectangle_too():
    assert parse_well_list("H01:H12") == [f"H{c:02d}" for c in range(1, 13)]
    assert parse_well_list("A05:H05") == [f"{r}05" for r in "ABCDEFGH"]


def test_ranges_and_single_wells_mix_and_a_well_is_listed_once():
    assert parse_well_list("A1:A3, b2; A02:B02, H12") == ["A01", "A02", "A03", "b2", "H12"]


def test_single_wells_are_still_returned_as_typed():
    assert parse_well_list("A1, b02; C3") == ["A1", "b02", "C3"]


@pytest.mark.parametrize("text, bad", [
    ("A1:Z9", ["A1:Z9"]),          # a corner that is not a well
    ("A1:", ["A1:"]),              # a missing corner
    ("A13, B2", ["A13"]),          # a column past 12
    ("A01:C03, B2", []),
])
def test_an_entry_that_is_not_a_well_or_a_rectangle_is_kept_and_named(text, bad):
    assert unreadable_wells(text) == bad
    for token in bad:
        assert token in parse_well_list(text)


def test_the_message_names_each_bad_entry_under_its_field():
    msg = well_scope_error({"Only these wells": "A1:Z9", "Skip these wells": "B2", "Force these wells": "Q4"})
    assert "Only these wells: 'A1:Z9'" in msg
    assert "Force these wells: 'Q4'" in msg
    assert "Skip these wells" not in msg
    assert well_scope_error({"Only these wells": "A01:B12, H12", "Skip these wells": ""}) is None


@pytest.fixture
def form():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget

    from analyze.well_scope import AnalyzeRunScopeMixin, build_well_scope_rows

    QApplication.instance() or QApplication([])

    class _Form(AnalyzeRunScopeMixin):
        def _row(self, sec, label, tooltip=None):
            row = QWidget(sec)
            QHBoxLayout(row)
            return row

    f, sec = _Form(), QWidget()
    build_well_scope_rows(f, sec)
    f._keep = sec
    return f


def test_the_three_fields_send_the_expanded_wells_to_the_pipeline(form):
    """Through the real fields, the run options and the argv builder."""
    from pathlib import Path

    from analyze.well_scope import well_scope_options
    from services.pipeline_service import build_pipeline_args

    form._only_wells_edit.setText("A01:B02")
    form._skip_wells_edit.setText("B02")
    form._force_wells_edit.setText("A1:A2")
    opts = well_scope_options(form)
    assert opts == {"only_wells": ["A01", "A02", "B01", "B02"], "skip_wells": ["B02"],
                    "force_wells": ["A01", "A02"]}

    argv = build_pipeline_args(Path("process_microscopy.py"), Path("in"), Path("out"),
                               {**opts, "raw": Path("in"), "fluor_tokens": ["GFP"], "nuclear_token": "DAPI",
                                "filename_schema": "experiment:channel:well:fov:timepoint", "filename_sep": "_",
                                "csv_prefix": "measurements"})
    only = argv[argv.index("--only_wells") + 1:]
    assert only[:4] == ["A01", "A02", "B01", "B02"]
    force = argv[argv.index("--force_wells") + 1:]
    assert force[:2] == ["A01", "A02"]


def test_an_unreadable_entry_stops_the_run(form):
    form._only_wells_edit.setText("A1:Z9")
    msg = form._well_scope_validation_error(check_input=True)
    assert msg is not None and "Only these wells: 'A1:Z9'" in msg
    form._only_wells_edit.setText("A1:C9")
    assert form._well_scope_validation_error(check_input=True) is None


@pytest.fixture
def tab(qtbot, monkeypatch):
    pytest.importorskip("PySide6")
    import analyze.run_footer

    monkeypatch.setattr(analyze.run_footer, "load_remotes", lambda: {})
    from analyze.tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)
    t.resize(1280, 860)
    t.show()
    qtbot.waitExposed(t)
    return t


def test_the_real_form_lists_the_bad_entry_beside_run_and_opens_run_scope(tab):
    """Through ``AnalyzeTab._run_problems``, the list Run and Add to queue both read."""
    tab._only_wells_edit.setText("A1:Z9")
    problems = [p for p in tab._run_problems() if p.section == "Run scope"]
    assert [(p.section, p.title) for p in problems] == [("Run scope", "Well List Error")]
    assert "Only these wells: 'A1:Z9'" in problems[0].message
    body = tab._collapsible_sections["Run scope"][0]
    body.setVisible(False)
    tab._go_to_run_problem(problems[0])
    assert body.isVisibleTo(tab)

    tab._only_wells_edit.setText("A1:C9")
    assert not [p for p in tab._run_problems() if p.section == "Run scope"]
