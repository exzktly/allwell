"""The ``dataset 1 + dataset 2 = destination`` row on the two pages that draw it.

Merge Plates and Concatenate had built this row twice, in twenty statements
each that differed only in two labels and the name of the slot the fields
report to. It is one builder now
(:mod:`datasets._common.equation_row`), and these tests are what say the two
pages still get what they got before.

They read the **rendered row** — the field labels in order, the operator glyphs
between them, the hint underneath — rather than the builder's return value,
because a builder can hand back four correct widgets and still have put them in
the wrong order or connected none of them (CLAUDE.md §7.4 #1). The connection
is the part with no visual evidence at all, so it gets its own test: a field
that reports to nothing looks exactly like one that reports correctly.

Written against the two hand-written copies and passed there unchanged, which
is what makes them an equivalence proof rather than a description of whatever
the shared builder happens to do.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _pages():
    from datasets.concatenate.view import ConcatenatePage
    from datasets.merge_plates.view import MergePlatesPage

    return {"merge_plates": MergePlatesPage, "concatenate": ConcatenatePage}


def _field_label(field) -> str:
    return next((k.text() for k in field.findChildren(QLabel) if k.text()), "")


@pytest.mark.parametrize("name,expected", [
    ("merge_plates", ("Dataset 1", "Dataset 2", "Destination")),
    ("concatenate", ("Dataset 1 (base)", "Dataset 2 (appended)", "Destination")),
])
def test_each_page_keeps_its_own_field_labels(qapp, name, expected):
    """The one thing that genuinely differs between the two rows. Concatenate
    names its inputs base and appended because the operation is ordered; Merge
    Plates does not, because its is not. A shared builder that hardcoded either
    wording would silently relabel the other page."""
    page = _pages()[name]()
    assert (_field_label(page._ds1_field),
            _field_label(page._ds2_field),
            _field_label(page._dest_field)) == expected


@pytest.mark.parametrize("name", ["merge_plates", "concatenate"])
def test_the_row_reads_as_an_equation(qapp, name):
    page = _pages()[name]()
    row = page._ds1_field.parentWidget()
    glyphs = [k.text() for k in row.findChildren(QLabel) if k.text() in ("+", "=")]
    assert glyphs == ["+", "="], (
        f"the operator column drew {glyphs}; the row has to read as an equation")


@pytest.mark.parametrize("name", ["merge_plates", "concatenate"])
def test_the_cross_plate_hint_starts_hidden_and_empty(qapp, name):
    """It is a red line the page shows only once it has something to say about
    the two datasets not fitting together. Visible at construction, it would
    accuse every user of a problem they do not have."""
    page = _pages()[name]()
    assert page._cross_hint.text() == ""
    assert not page._cross_hint.isVisible()


@pytest.mark.parametrize("name,field", [
    (n, f) for n in ("merge_plates", "concatenate")
    for f in ("_ds1_field", "_ds2_field", "_dest_field")
])
def test_every_field_reports_back_to_its_page(qapp, name, field):
    """The half of this row with no visual evidence. Each page connects the
    three fields to its own changed-slot, and both slots restore the Run
    button's primary emphasis — so a builder that connected nothing, or
    connected only the first field, renders an identical row that has quietly
    stopped responding to the user."""
    page = _pages()[name]()
    page._run_btn.setProperty("variant", "secondary")
    getattr(page, field).changed.emit()
    assert page._run_btn.property("variant") == "primary", (
        f"{name}.{field} emitted changed and the page did not react")
