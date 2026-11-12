"""The Nanowells page's subsets can be named and saved in a dataset, listed in the Subset box from then on.

Asked for: "make subsets nameable and saveable in a given dataset". A subset was one unnamed set of conditions,
kept with the page's settings; building a second one lost the first.

What is pinned: a name is trimmed and bounded, and one that is not a name is refused with its reason; saving
keeps the subset as written under its name in the dataset's ``persistence.json`` (one dataset's saved subsets are
not another's); the Subset box lists each saved subset by name and choosing one makes it the page's subset, which
the figure then draws; the box names a saved subset only while it is exactly the one saved; renaming keeps its
place, deleting forgets it without changing the figure; and a damaged saved entry is dropped on restore rather than
restored as a different subset under its name.

The two subsets saved by the page tests keep different nanowells, so a box that applied the wrong one — or applied
none — draws a different set of dots.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6")

from review.features.nanowell import subset_compute as ss  # noqa: E402
from review.features.nanowell.state import NanowellState  # noqa: E402
from tests.test_nanowell_page_starting_cells import _dots  # noqa: E402
from tests.test_nanowell_subset import _editor, page  # noqa: E402,F401

C = ss.Condition
SINGLES = ss.Subset((C("n0", "eq", 1),)).validated()
RING = ss.Subset((C("ring", "le", 1), C("n0", "ge", 2)), "any").validated()


# ── names and the saved set (pure) ───────────────────────────────────────────
def test_a_name_is_trimmed_and_one_that_is_not_a_name_is_refused():
    assert ss.clean_name("  Singles   at  start ") == "Singles at start"
    with pytest.raises(ValueError, match="needs a name"):
        ss.clean_name("   ")
    with pytest.raises(ValueError, match=f"at most {ss.MAX_NAME}"):
        ss.clean_name("x" * (ss.MAX_NAME + 1))


def test_saving_keeps_each_subset_under_its_name_and_the_same_name_replaces_in_place():
    saved = ss.with_saved({}, "Singles", SINGLES)
    saved = ss.with_saved(saved, "Ring", RING)
    assert list(saved) == ["Singles", "Ring"] and saved["Ring"] == RING
    pairs = ss.Subset((C("n0", "eq", 2),)).validated()
    saved = ss.with_saved(saved, "Singles", pairs)
    assert list(saved) == ["Singles", "Ring"] and saved["Singles"] == pairs
    with pytest.raises(ValueError, match="no conditions"):
        ss.with_saved(saved, "Everything", ss.Subset())


def test_a_saved_subset_is_named_only_while_it_is_exactly_the_one_saved():
    saved = {"Singles": SINGLES, "Ring": RING}
    assert ss.saved_name_of(saved, RING) == "Ring"
    assert ss.saved_name_of(saved, ss.Subset(RING.conditions, "all")) == ""     # the same conditions, under all
    assert ss.saved_name_of(saved, ss.Subset()) == ""


def test_renaming_keeps_the_place_and_refuses_a_name_already_taken():
    saved = {"Singles": SINGLES, "Ring": RING}
    assert list(ss.renamed(saved, "Singles", "One cell")) == ["One cell", "Ring"]
    with pytest.raises(ValueError, match="already saved"):
        ss.renamed(saved, "Singles", "Ring")


def test_the_saved_subsets_round_trip_and_a_damaged_entry_is_dropped_not_changed():
    state = NanowellState()
    state.saved_subsets = {"Singles": SINGLES, "Ring": RING}
    block = json.loads(json.dumps(state.to_config()))
    block["saved_subsets"]["Broken"] = {"conditions": [{"field": "n0", "op": "between", "a": 5, "b": 1}]}
    block["saved_subsets"][""] = SINGLES.to_config()
    restored = NanowellState()
    restored.apply_config(block)
    assert restored.saved_subsets == {"Singles": SINGLES, "Ring": RING}


# ── the page ─────────────────────────────────────────────────────────────────
def _save(page, subset, name):  # noqa: F811
    from review.features.nanowell import controller as nwc

    assert nwc.set_subset(page, subset)
    editor = _editor(page) if not page.widgets.get("_nw_subset_editor").isVisibleTo(page.window) else \
        page.widgets.get("_nw_subset_editor")
    editor.name_edit.setText(name)
    editor.save_btn.click()
    return editor


def _choose(page, label):  # noqa: F811
    combo = page.widgets.get("_nw_subset_cb")
    index = combo.findText(label)
    assert index >= 0, [combo.itemText(i) for i in range(combo.count())]
    combo.setCurrentIndex(index)


def test_a_subset_saved_by_name_is_listed_and_choosing_it_draws_it(page):  # noqa: F811
    from review.features.nanowell import controller as nwc

    st = page.state.feature_slice("nanowell")
    nwc.set_subset(page, SINGLES)
    singles = _dots(page)
    nwc.set_subset(page, RING)
    ring = _dots(page)
    assert singles != ring                                     # the two subsets keep different nanowells
    editor = _save(page, SINGLES, "Singles")
    assert editor.status.text().startswith("Saved as “Singles”")
    _save(page, RING, "Ring at start")
    assert st.saved_subsets == {"Singles": SINGLES, "Ring at start": RING}
    combo = page.widgets.get("_nw_subset_cb")
    assert combo.currentText() == "Ring at start"              # the subset in use, by its name
    _choose(page, "All nanowells")
    assert st.subset == ss.Subset()
    _choose(page, "Singles")
    assert st.subset == SINGLES and _dots(page) == singles
    _choose(page, "Ring at start")
    assert st.subset == RING and _dots(page) == ring
    editor.add_btn.click()                                     # one condition more: no longer the saved one
    assert combo.currentText() == "Ring at start"              # until it is applied
    editor.apply_btn.click()
    assert combo.currentData() == "custom" and st.saved_subsets["Ring at start"] == RING


def test_the_editor_s_saved_dropdown_switches_between_the_saved_subsets(page):  # noqa: F811
    """Asked for: "there is no way to toggle between saved subsets" in the editor. Its *Saved* dropdown lists every
    saved subset; choosing one makes it the page's subset, rebuilds the rows from it, puts its name in the name box
    and draws it — the two saved subsets keep different nanowells, so drawing the wrong one or none shows."""
    from review.features.nanowell import controller as nwc
    from review.features.nanowell.subset_view import UNSAVED_LABEL

    st = page.state.feature_slice("nanowell")
    nwc.set_subset(page, SINGLES)
    singles = _dots(page)
    nwc.set_subset(page, RING)
    ring = _dots(page)
    assert singles != ring
    editor = _save(page, SINGLES, "Singles")
    _save(page, RING, "Ring at start")
    combo = editor.saved_cb
    assert combo.isEnabled()
    assert [combo.itemText(i) for i in range(combo.count())] == [UNSAVED_LABEL, "Singles", "Ring at start"]
    assert combo.currentText() == "Ring at start"              # the subset in use, by its name
    combo.setCurrentIndex(combo.findText("Singles"))
    assert st.subset == SINGLES and _dots(page) == singles
    assert editor.written().validated() == SINGLES and editor.name_edit.text() == "Singles"
    assert page.widgets.get("_nw_subset_cb").currentText() == "Singles"   # the band's box follows
    editor.rows[0].op_cb.setCurrentIndex(editor.rows[0].op_cb.findData("ge"))   # an edit not applied ...
    combo.setCurrentIndex(combo.findText("Ring at start"))
    assert st.subset == RING and _dots(page) == ring
    assert editor.written().validated() == RING and not editor.pending()   # ... is discarded, as Reset does
    editor.apply_btn.click()
    nwc.set_subset(page, ss.Subset((C("n0", "ge", 3),)).validated())   # a subset saved under no name
    assert combo.currentText() == UNSAVED_LABEL
    combo.setCurrentIndex(combo.findText(UNSAVED_LABEL))       # names no subset: nothing changes
    assert st.subset == ss.Subset((C("n0", "ge", 3),)).validated() and combo.currentText() == UNSAVED_LABEL


def test_the_saved_dropdown_is_disabled_while_nothing_is_saved(page):  # noqa: F811
    editor = _editor(page)
    assert not editor.saved_cb.isEnabled() and editor.saved_cb.count() == 1


def test_rename_and_delete_act_on_the_saved_subset_in_use(page):  # noqa: F811
    st = page.state.feature_slice("nanowell")
    editor = _save(page, SINGLES, "Singles")
    assert not editor.rename_btn.isEnabled() and editor.delete_btn.isEnabled()   # the name typed is its own
    editor.name_edit.setText("One cell at start")
    assert editor.rename_btn.isEnabled()
    editor.rename_btn.click()
    assert list(st.saved_subsets) == ["One cell at start"]
    assert page.widgets.get("_nw_subset_cb").currentText() == "One cell at start"
    editor.delete_btn.click()
    assert st.saved_subsets == {} and st.subset == SINGLES     # forgotten, and the figure still uses it
    combo = page.widgets.get("_nw_subset_cb")
    assert combo.findText("One cell at start") < 0


def test_the_saved_subsets_are_kept_in_the_dataset_s_own_file(page):  # noqa: F811
    from pathlib import Path

    from review.persistence import nanowell as persisted

    _save(page, SINGLES, "Singles")
    persisted.save_to_data_dir(page)
    doc = json.loads((Path(page.state.dataset.data_dir) / "persistence.json").read_text())
    assert doc["nanowell"]["saved_subsets"] == {"Singles": SINGLES.to_config()}
    fresh = NanowellState()
    persisted.load_from_data_dir(page, fresh)
    assert fresh.saved_subsets == {"Singles": SINGLES}


# ── found by review ──────────────────────────────────────────────────────────
from tests.test_reset_view_persists_every_domain import stub  # noqa: E402,F401


def test_reset_view_keeps_the_saved_subsets_and_resets_the_page(stub):  # noqa: F811
    """Reset view returns the page's choices to their defaults; the subsets saved by name are a library the user
    built in the dataset, not a view setting, and survive it."""
    from review.persistence import reset_scopes

    ctx, _written = stub
    nw = ctx.state.feature_slice("nanowell")
    nw.saved_subsets = {"Singles": SINGLES, "Ring": RING}
    nw.subset = RING
    reset_scopes.reset_view(ctx)
    assert nw.subset == ss.Subset() and nw.saved_subsets == {"Singles": SINGLES, "Ring": RING}


def test_one_subset_has_one_name():
    """The same conditions under a second name are refused, naming the first: with two names the box could show
    only one, and Rename or Delete would act on the name the user did not pick. A file holding two keeps the first."""
    saved = ss.with_saved({}, "alpha", SINGLES)
    with pytest.raises(ValueError, match="already saved as “alpha”"):
        ss.with_saved(saved, "beta", SINGLES)
    assert ss.with_saved(saved, "alpha", SINGLES) == saved           # the same name again replaces it
    restored = ss.saved_from_config({"alpha": SINGLES.to_config(), "beta": SINGLES.to_config()})
    assert restored == {"alpha": SINGLES}


def test_saving_the_same_conditions_under_a_second_name_says_so_on_the_page(page):  # noqa: F811
    st = page.state.feature_slice("nanowell")
    editor = _save(page, SINGLES, "alpha")
    editor.name_edit.setText("beta")
    editor.save_btn.click()
    assert list(st.saved_subsets) == ["alpha"]
    assert editor.status.objectName() == "Warn" and "already saved as “alpha”" in editor.status.text()


def test_a_name_typed_for_a_draft_survives_apply(page):  # noqa: F811
    """Save waits while edits are pending and says to Apply first; pressing Apply must not then wipe the new name
    the user typed for the draft."""
    from review.features.nanowell import controller as nwc

    st = page.state.feature_slice("nanowell")
    nwc.set_subset(page, SINGLES)
    editor = _save(page, SINGLES, "alpha")
    editor.name_edit.setText("beta")
    editor.rows[0].op_cb.setCurrentIndex(editor.rows[0].op_cb.findData("ge"))
    assert not editor.save_btn.isEnabled()                           # Apply first
    editor.apply_btn.click()
    assert editor.name_edit.text() == "beta" and editor.save_btn.isEnabled()
    editor.save_btn.click()
    assert list(st.saved_subsets) == ["alpha", "beta"] and st.saved_subsets["beta"] == st.subset


def test_a_subset_a_row_cannot_show_exactly_is_not_an_edit_nobody_made(page):  # noqa: F811
    """A bound past the spin box's decimals (a restored config, a hand-edited file) is shown rounded. That is not a
    pending edit: Apply would silently round the subset in use, and Reset could never clear it."""
    from review.features.nanowell import controller as nwc

    st = page.state.feature_slice("nanowell")
    precise = ss.Subset((C("c", "ge", 0.12345, at="baseline"),)).validated()
    nwc.set_subset(page, precise)
    editor = _editor(page)
    assert not editor.pending()
    assert not editor.apply_btn.isEnabled() and not editor.reset_btn.isEnabled()
    assert not editor.status.text().startswith("Not applied yet")
    assert st.subset == precise
