"""A saved selection's expanded body: chips, or a placeholder that says why not.

``_SelectionRow._build_readonly`` used to build its chips inside
``except Exception`` and fall back to a comma-joined ``QLabel``. Nothing it
could catch was real — ``widgets/chip_group`` is first-party and ships in every
profile and every frozen bundle — so all the catch did was swallow faults in the
loop beneath it and render a comma list, which reads as a deliberate compact
style rather than as something going wrong. CLAUDE.md §7's one forbidden
substitution: degraded output indistinguishable from good output.

These tests hold the shape that replaced it. Chips when there are wells, a named
placeholder when there are none, and a fault that reaches the caller instead of
being dressed up as a rendering choice. The third is the one that matters: put
the ``except Exception`` back and it is the only test here that fails.
"""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

import widgets.saved_selections_list as ssl  # noqa: E402
from widgets.chip_group import ChipGroup  # noqa: E402

SEL = {"id": "g1", "name": "Treated", "wells": ["A01", "A02", "B03"], "color": "#112233"}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _row(qapp, sel=None):
    row = ssl._SelectionRow(dict(sel or SEL))
    row._fill_chips()
    return row


def _chip_groups(row) -> list[ChipGroup]:
    return row.findChildren(ChipGroup)


def _captions(row) -> list[str]:
    return [w.text() for w in row.findChildren(QLabel)
            if w.property("textRole") == "label"]


def test_the_wells_are_drawn_as_chips_carrying_their_own_tokens(qapp):
    """Asserted on the tokens, not on "a ChipGroup exists". A group built from
    the wrong list is still a ChipGroup."""
    row = _row(qapp)

    groups = _chip_groups(row)
    assert len(groups) == 1
    cg = groups[0]
    assert [cg.chipText(i) for i in range(cg.count())] == ["A01", "A02", "B03"]
    assert [cg.currentData() for _ in ()] == []          # exclusive mode is off
    assert cg.checkedData() == []


def test_the_read_only_chips_cannot_be_pressed(qapp):
    """This body mirrors a selection; it does not edit one. An enabled group
    would let a rail click silently toggle a chip that means nothing."""
    row = _row(qapp)

    assert _chip_groups(row)[0].isEnabled() is False


def test_a_selection_with_no_wells_says_so_rather_than_drawing_nothing(qapp):
    """An empty strip and a strip that failed to build look identical. The
    placeholder is a fact about the selection, so it is stated."""
    row = _row(qapp, {**SEL, "wells": []})

    assert _chip_groups(row) == []
    assert "(no wells)" in _captions(row)


def test_a_populated_selection_shows_no_placeholder(qapp):
    """The half that makes the test above mean something: a body that always
    drew the placeholder would satisfy it and be wrong."""
    row = _row(qapp)

    assert "(no wells)" not in _captions(row)


def test_a_broken_chip_build_reaches_the_caller_rather_than_degrading(qapp, monkeypatch):
    """The defect this file exists for. With the old ``except Exception`` in
    place this renders a comma-joined label and returns normally — the wells are
    on screen, in a plausible-looking compact style, with nothing anywhere
    saying the chips could not be built.

    The failure is injected into ``ChipGroup.addChip`` rather than into the
    import, because the import is the one thing that genuinely cannot fail here
    and the loop beneath it is what the catch was really swallowing."""
    def _boom(self, text, data=None):
        raise RuntimeError("chip build failed")

    monkeypatch.setattr(ChipGroup, "addChip", _boom)

    with pytest.raises(RuntimeError, match="chip build failed"):
        _row(qapp)


def test_a_plate_factory_that_raises_is_recorded(qapp, caplog):
    """Not silent, and not nothing. A factory that raises is different from no
    factory: the default plate enables a full grid, so a caller whose factory
    was meant to narrow the plate to its own dataset silently gets a chooser
    offering wells that dataset does not have."""
    row = ssl._SelectionRow(dict(SEL))
    row._enabled_wells = ["A01", "A02"]

    def _boom():
        raise RuntimeError("no plate for you")

    row._plate_factory = _boom
    with caplog.at_level(logging.WARNING, logger="allwell"):
        row._open_compose_popover()

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, caplog.text
    assert "plate factory" in warnings[0].getMessage()
    assert warnings[0].exc_info is not None, "the exception itself has to be in the record"
    assert "no plate for you" in caplog.text

    # …and the fallback still happened, so the popover is usable rather than
    # half-built. Logging the fault and then failing to open would trade a
    # silent substitution for a silent nothing.
    assert row._compose_pop is not None


def test_a_row_s_wells_chooser_is_a_page_through_the_list_s_opener_and_ok_writes_the_wells(qapp):
    """Not a popover over the rail: the chooser goes to the opener the window
    set (its inspector), and OK on that page writes the chosen wells — the
    second well here, so "keep what it had" writes something else."""
    from PySide6.QtWidgets import QPushButton

    from widgets.well_plate_selector import WellPlateSelector

    lst = ssl.SavedSelectionsList()
    shown, closed = [], []

    def opener(title, widget, _anchor):
        shown.append((title, widget))
        return lambda: closed.append(title)

    lst.setComposable(True)
    lst.setSelections([dict(SEL, wells=["A01"])])
    lst.setPageOpener(opener)                 # after the rows exist: they must be told too
    row = next(iter(lst._rows.values()))
    row._enabled_wells = ["A01", "A02"]
    edits = []
    row.compositionEdited.connect(lambda _sel_id, wells, _r: edits.append(list(wells)))
    row._open_compose_popover()
    (title, page), = shown
    assert title == "Wells"
    plate = page.findChild(WellPlateSelector)
    plate.setSelectedWellIds(["A02"])
    plate.selectionChanged.emit(["A02"])
    ok = next(b for b in page.findChildren(QPushButton) if b.text() == "OK")
    ok.click()
    assert row._wells == ["A02"] and edits and edits[-1] == ["A02"]
    assert closed == ["Wells"], "OK did not close the page"


def test_recolour_is_the_colour_page_through_the_list_s_opener(qapp):
    from widgets.colour_page import ColourPage

    lst = ssl.SavedSelectionsList()
    shown = []
    lst.setPageOpener(lambda title, widget, anchor: shown.append((title, widget)) or (lambda: None))
    lst.setSelections([dict(SEL, color="#112233")])
    sel_id = next(iter(lst._rows))
    recoloured = []
    lst.entryRecoloured.connect(lambda i, c: recoloured.append((i, c)))
    lst._on_recolour_requested(sel_id, lst)
    (title, page), = shown
    assert title == "Recolour" and isinstance(page, ColourPage)
    assert page.color().name().upper() == "#112233"
    page.hex_edit.setText("#AA5500")
    page.hex_edit.editingFinished.emit()
    assert recoloured == [(sel_id, "#AA5500")]
