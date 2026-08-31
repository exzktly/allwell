"""ChipGroup: the chips do something when pressed, and say what.

This widget had **no test of any kind** — it was one of the five modules the
combined-lane zero-coverage inventory named, reached only from
``widgets/saved_selections_list._build_readonly`` and therefore never
constructed by the suite.

What these tests are written against is the failure mode CLAUDE.md §7.6
describes: asserting that a control is *offered* is not asserting that it
*works*. So the chip presses here go through ``QToolButton.click()`` — the real
signal path a user's press takes, through ``QButtonGroup.idToggled`` and
``_on_id_toggled`` — rather than through ``setCurrentIndex``, which is the seam
underneath the wiring and would pass with the wiring cut.

The fixtures are built so a wrong implementation produces different bytes
(§7.8 rule 2). Chip data is deliberately assigned *against* chip order, and the
multi-select chips are checked in an order that is not their layout order, so
"returns the data of the first chip" and "returns them in click order" both
fail rather than coincidentally agreeing.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from widgets.chip_group import ChipGroup  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _spy(signal):
    """Collect a signal's arguments. One list, appended to in emission order."""
    seen: list = []
    signal.connect(lambda *args: seen.append(args if len(args) > 1 else args[0]))
    return seen


# ── single-select ────────────────────────────────────────────────────────────

def test_the_first_chip_added_becomes_the_selection(qapp):
    """An exclusive group with nothing selected is a group whose ``currentData``
    is ``None`` while a chip is plainly drawn checked — so the first ``addChip``
    selects itself, and says so on the signal."""
    cg = ChipGroup(exclusive=True)
    seen = _spy(cg.currentChanged)
    cg.addChip("DAPI", data="dapi")

    assert cg.currentIndex() == 0
    assert cg.currentData() == "dapi"
    assert seen == [0], "adding the first chip must announce the selection it made"


def test_adding_more_chips_does_not_move_the_selection(qapp):
    cg = ChipGroup(exclusive=True)
    cg.addChip("DAPI", data="dapi")
    seen = _spy(cg.currentChanged)
    cg.addChip("GFP", data="gfp")
    cg.addChip("RFP", data="rfp")

    assert cg.currentIndex() == 0
    assert cg.currentData() == "dapi"
    assert seen == [], "a later chip must not steal the selection"


def test_pressing_a_chip_selects_it_and_announces_the_index(qapp):
    """The press, not ``setCurrentIndex``. Cutting the ``idToggled`` connection
    leaves ``setCurrentIndex`` working and this test failing, which is the whole
    point of driving the button."""
    cg = ChipGroup(exclusive=True)
    # Data assigned against chip order: chip 0 carries "z", chip 2 carries "a".
    # An implementation returning ``self._data[0]``, or the data in insertion
    # order, gives a different answer from the right one.
    for text, data in (("A", "z"), ("B", "m"), ("C", "a")):
        cg.addChip(text, data=data)
    seen = _spy(cg.currentChanged)

    cg._buttons[2].click()

    assert cg.currentIndex() == 2
    assert cg.currentData() == "a"
    assert cg.chipText(2) == "C"
    assert seen == [2]


def test_pressing_the_selected_chip_again_announces_nothing(qapp):
    """What this pins is Qt's, not ours: an exclusive ``QButtonGroup`` refuses
    to un-check its only checked button, so the second press emits no
    ``toggled`` at all and ``_on_id_toggled`` is never entered. Worth a test
    because the widget's contract is "one chip is always selected", and that
    contract rests entirely on a Qt behaviour nothing else here states."""
    cg = ChipGroup(exclusive=True)
    cg.addChip("A", data="a")
    cg.addChip("B", data="b")
    cg._buttons[1].click()
    seen = _spy(cg.currentChanged)

    cg._buttons[1].click()

    assert cg.currentIndex() == 1
    assert cg.isChecked(1) is True, "the selection must survive its own re-press"
    assert seen == []


def test_a_repeat_announcement_is_suppressed_even_if_qt_ever_makes_one(qapp):
    """``_on_id_toggled``'s ``idx != self._current`` guard, driven directly —
    and said plainly: **no press reaches it.** An exclusive ``QButtonGroup``
    never re-emits ``toggled`` for the button already checked, and
    ``setChecked(i, False)`` on the only checked chip is refused for the same
    reason, so both routes a user has are closed.

    It is kept, and pinned here, because it is what makes ``currentChanged``
    mean "the selection moved" rather than "a button was touched" — and because
    a guard nothing exercises is a guard that gets deleted as dead. Labelled
    defensive rather than dressed up as a click: a test that reached this
    through the widget would be claiming a route that does not exist, which is
    the mistake CLAUDE.md §7.7 is about."""
    cg = ChipGroup(exclusive=True)
    cg.addChip("A", data="a")
    cg.addChip("B", data="b")
    seen = _spy(cg.currentChanged)

    cg._on_id_toggled(0, True)          # 0 is already ``_current``
    assert seen == []

    cg._on_id_toggled(1, True)          # a real move still speaks
    assert seen == [1]


def test_set_current_by_data_finds_the_chip_carrying_that_value(qapp):
    cg = ChipGroup(exclusive=True)
    for text, data in (("A", "z"), ("B", "m"), ("C", "a")):
        cg.addChip(text, data=data)

    cg.setCurrentByData("m")

    assert cg.currentIndex() == 1
    assert cg.currentData() == "m"


def test_set_current_by_data_leaves_the_selection_alone_for_an_unknown_value(qapp):
    cg = ChipGroup(exclusive=True)
    cg.addChip("A", data="a")
    cg.addChip("B", data="b")
    cg._buttons[1].click()

    cg.setCurrentByData("nothing-has-this")

    assert cg.currentIndex() == 1, "an unknown value must not reset to chip 0"


def test_an_out_of_range_index_is_refused_rather_than_wrapped(qapp):
    cg = ChipGroup(exclusive=True)
    cg.addChip("A", data="a")
    cg.addChip("B", data="b")

    cg.setCurrentIndex(7)
    assert cg.currentIndex() == 0
    cg.setCurrentIndex(-1)
    assert cg.currentIndex() == 0, "-1 must not select the last chip Python-style"


def test_current_data_is_none_before_any_chip_exists(qapp):
    cg = ChipGroup(exclusive=True)
    assert cg.count() == 0
    assert cg.currentIndex() == -1
    assert cg.currentData() is None


# ── multi-select ─────────────────────────────────────────────────────────────

def test_a_multi_select_group_selects_nothing_on_its_own(qapp):
    """The mirror of the single-select rule above, and the reason the two modes
    cannot share one branch: an exclusive group must open with a selection and a
    multi-select group must open with none."""
    cg = ChipGroup(exclusive=False)
    seen = _spy(cg.chipToggled)
    cg.addChip("Grid")
    cg.addChip("Legend")

    assert cg.checkedIndices() == []
    assert cg.currentIndex() == -1
    assert seen == []


def test_pressing_chips_reports_each_toggle_with_its_index_and_state(qapp):
    cg = ChipGroup(exclusive=False)
    for name in ("Grid", "Threshold", "Legend"):
        cg.addChip(name)
    seen = _spy(cg.chipToggled)

    cg._buttons[2].click()
    cg._buttons[0].click()
    cg._buttons[2].click()

    assert seen == [(2, True), (0, True), (2, False)]
    assert cg.checkedIndices() == [0]


def test_checked_indices_come_back_in_chip_order_not_press_order(qapp):
    """Pressed 2, then 0. A collector appending as chips are pressed returns
    ``[2, 0]``; the widget's contract is chip order."""
    cg = ChipGroup(exclusive=False)
    for text, data in (("A", "z"), ("B", "m"), ("C", "a")):
        cg.addChip(text, data=data)

    cg._buttons[2].click()
    cg._buttons[0].click()

    assert cg.checkedIndices() == [0, 2]
    assert cg.checkedData() == ["z", "a"]


def test_set_checked_data_unchecks_what_is_not_named(qapp):
    """The half a "check the ones in the list" implementation forgets. Chip 1 is
    checked first, so an implementation that only ever checks leaves it on and
    fails here."""
    cg = ChipGroup(exclusive=False)
    for text, data in (("A", "z"), ("B", "m"), ("C", "a")):
        cg.addChip(text, data=data)
    cg.setChecked(1, True)

    cg.setCheckedData(["z", "a"])

    assert cg.checkedIndices() == [0, 2]
    assert cg.isChecked(1) is False


def test_set_checked_data_with_an_empty_list_clears_everything(qapp):
    cg = ChipGroup(exclusive=False)
    for name in ("A", "B"):
        cg.addChip(name, data=name)
    cg.setCheckedData(["A", "B"])

    cg.setCheckedData([])

    assert cg.checkedIndices() == []


def test_multi_select_chips_are_independent(qapp):
    """``QButtonGroup`` is exclusive by default; a group constructed with
    ``exclusive=False`` that forgot to pass it on would let the second press
    release the first."""
    cg = ChipGroup(exclusive=False)
    for name in ("A", "B", "C"):
        cg.addChip(name)

    cg._buttons[0].click()
    cg._buttons[1].click()

    assert cg.checkedIndices() == [0, 1]


def test_set_current_index_does_nothing_to_a_multi_select_group(qapp):
    cg = ChipGroup(exclusive=False)
    cg.addChip("A")
    cg.addChip("B")

    cg.setCurrentIndex(1)

    assert cg.checkedIndices() == []


# ── binding adapter ──────────────────────────────────────────────────────────

def test_the_binding_adapter_reads_and_writes_the_exclusive_selection(qapp):
    cg = ChipGroup(exclusive=True)
    for text, data in (("A", "z"), ("B", "m")):
        cg.addChip(text, data=data)
    getter, setter, signal = cg.bindingAdapter()

    setter("m")

    assert getter() == "m"
    assert signal is cg.currentChanged


def test_the_binding_adapter_reads_and_writes_the_multi_select_set(qapp):
    cg = ChipGroup(exclusive=False)
    for text, data in (("A", "z"), ("B", "m"), ("C", "a")):
        cg.addChip(text, data=data)
    getter, setter, signal = cg.bindingAdapter()

    setter(["a", "z"])

    assert getter() == ["z", "a"], "chip order, not the order the caller wrote"
    assert signal is cg.chipToggled


# ── chrome ───────────────────────────────────────────────────────────────────

def test_the_stylesheet_declares_a_tone_for_every_chip_state(qapp):
    """Four states are painted by QSS alone, so nothing but this notices when
    one is dropped."""
    cg = ChipGroup()
    qss = cg._build_qss()
    for state in ("", ":hover", ":checked", ":disabled"):
        assert f"#ChipGroup QToolButton#Chip{state} {{" in qss, state


def test_chip_text_is_empty_rather_than_raising_for_a_bad_index(qapp):
    cg = ChipGroup()
    cg.addChip("A")
    assert cg.chipText(0) == "A"
    assert cg.chipText(9) == ""
    assert cg.chipText(-1) == ""
