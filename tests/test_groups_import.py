"""Home's **Import**: the app's one way to bring groups in from a file.

It replaced two buttons on the same layer — Home's *Import*, which added a
file's groups beside the current ones, and the Groups list's own *Import…*,
which threw the current groups away. Same name, opposite outcome. Now there is
one import, and when the plate already has groups the user is asked which of
the two they mean.

These drive ``import_selections_from_json`` itself, through patched
``QFileDialog`` / ``QMessageBox`` statics and a patched ``ask_import_mode``,
because every rule worth checking sits *after* the file picker: which shapes are
read, when the user is asked, what Replace discards, and that nothing is lost on
a cancel or a bad file. The selection state is the real ``SelectionState``;
only the redraw is a spy, because it is the outbound call this path must make.

Every refusal is checked for both halves — the message was shown **and** the
existing groups survived — because a load that reports failure after it has
already emptied the plate is the outcome this file exists to prevent.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget  # noqa: E402

from review.core import selection_controller as sc  # noqa: E402

V2 = {
    "schema_version": 2,
    "selections": [
        {"id": "g1", "name": "Treated", "wells": ["A01", "A02"], "color": "#112233"},
        {"id": "g2", "name": "Control", "wells": ["B01"], "color": "#445566"},
    ],
    "current_id": "g2",
}

# A real v1 block, written from what ``domain.selections_format`` migrates:
# ``members`` name rep-sets, so ``Legacy`` holds RS1's wells plus its solo well,
# and ``Loose`` — a member of nothing — must survive as a group of its own.
V1 = {
    "rep_sets": [{"name": "RS1", "wells": ["D1", "D2"]},
                 {"name": "Loose", "wells": ["E1"]}],
    "groups": [{"name": "Legacy", "members": ["RS1"], "solo_wells": ["C5"]}],
}


class _Coordinator:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.seen: list[list[str]] = []

    def rebuild_all(self) -> None:
        # What the plate held when the redraw was asked for: "applied, then
        # redrew" and "redrew, then applied" differ only here.
        self.seen.append([s["name"] for s in self.ctx.state.selection.selections])


@pytest.fixture
def ctx(qapp, tmp_path):
    from review.core.selection_state import SelectionState

    window = QWidget()
    toasts: list[str] = []
    c = SimpleNamespace(
        window=window,
        state=SimpleNamespace(
            selection=SelectionState(),
            dataset=SimpleNamespace(data_dir=tmp_path, well_labels={},
                                    well_paths={}),
        ),
        notify=SimpleNamespace(toast=lambda msg, **_k: toasts.append(msg)),
        toasts=toasts,
    )
    c.coordinator = _Coordinator(c)
    yield c
    window.deleteLater()


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialogs(monkeypatch):
    """Install answers for the picker and the add/replace question; record the
    rest. Returns a namespace the test reads back."""
    rec = SimpleNamespace(path="", mode=None, asked=[], picker_dirs=[],
                          critical=[], warning=[], refreshed=0,
                          overlap=None, overlap_asked=[])

    def pick(_parent, _title, start_dir, _filter):
        rec.picker_dirs.append(start_dir)
        return rec.path, ""

    def ask(_ctx, existing, incoming):
        rec.asked.append((existing, incoming))
        return rec.mode

    def ask_overlap(_ctx, overlap):
        rec.overlap_asked.append(overlap)
        return rec.overlap

    def refresh(_ctx):
        rec.refreshed += 1

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(pick))
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda *a, **_k: rec.critical.append(a)))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **_k: rec.warning.append(a)))
    monkeypatch.setattr(sc, "ask_import_mode", ask)
    monkeypatch.setattr(sc, "ask_import_overlap", ask_overlap)
    monkeypatch.setattr(sc, "groups_centre_refresh", refresh)
    return rec


def _file(tmp_path, payload, name="groups.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _plate(ctx):
    return [(s["name"], s["wells"]) for s in ctx.state.selection.selections]


def _seed(ctx):
    """One group already on the plate, named so a collision with V2 is real."""
    sc.sel_add(ctx, name="Treated", wells=["H01"])
    ctx.coordinator.seen.clear()
    return _plate(ctx)


# ── onto an empty plate ──────────────────────────────────────────────────────

def test_a_v2_file_onto_an_empty_plate_adds_its_groups_without_asking(ctx, dialogs, tmp_path):
    dialogs.path = _file(tmp_path, V2)

    sc.import_selections_from_json(ctx)

    assert dialogs.asked == [], "nothing to replace, so nothing to ask"
    assert _plate(ctx) == [("Treated", ["A01", "A02"]), ("Control", ["B01"])]
    assert [s["color"] for s in ctx.state.selection.selections] == ["#112233", "#445566"], (
        "the file's colours must be carried, not repainted from the palette")
    assert ctx.coordinator.seen[-1] == ["Treated", "Control"]
    assert dialogs.refreshed == 1
    assert ctx.toasts == ["Imported 2 group(s) from groups.json."]


def test_the_picker_opens_in_the_dataset_folder(ctx, dialogs, tmp_path):
    dialogs.path = ""
    sc.import_selections_from_json(ctx)
    assert dialogs.picker_dirs == [str(tmp_path)]


def test_a_legacy_v1_file_is_migrated_rather_than_refused(ctx, dialogs, tmp_path):
    """The shape only the removed *Load bar group definitions* button read.
    Dropping it without teaching Import this shape would have stranded every
    such file with "No groups found"."""
    dialogs.path = _file(tmp_path, V1)

    sc.import_selections_from_json(ctx)

    assert dialogs.warning == []
    assert _plate(ctx) == [("Legacy", ["D01", "D02", "C05"]), ("Loose", ["E01"])]


def test_a_block_nested_under_sample_definitions_is_read(ctx, dialogs, tmp_path):
    dialogs.path = _file(tmp_path, {"sample_definitions": V2, "fluor_tokens": ["gfp"]})
    sc.import_selections_from_json(ctx)
    assert [n for n, _w in _plate(ctx)] == ["Treated", "Control"]


# ── onto a plate that already has groups ─────────────────────────────────────

def test_the_user_is_asked_with_both_counts(ctx, dialogs, tmp_path):
    _seed(ctx)
    dialogs.path, dialogs.mode = _file(tmp_path, V2), sc.IMPORT_ADD

    sc.import_selections_from_json(ctx)

    assert dialogs.asked == [(1, 2)]


def test_add_keeps_the_current_groups_and_suffixes_a_clashing_name(ctx, dialogs, tmp_path):
    _seed(ctx)
    dialogs.path, dialogs.mode = _file(tmp_path, V2), sc.IMPORT_ADD

    sc.import_selections_from_json(ctx)

    names = [n for n, _w in _plate(ctx)]
    assert names[0] == "Treated" and _plate(ctx)[0][1] == ["H01"], "the existing group survives"
    assert len(names) == 3 and names[1] != "Treated" and names[1].startswith("Treated"), (
        f"a clashing name is suffixed, never overwritten: {names}")
    assert names[2] == "Control"
    assert dialogs.overlap_asked == [], "disjoint wells: nothing to ask about"


# ── Add, when the file's wells are already in a group ────────────────────────
#
# The realistic Add: groups exported from one plate and imported into a sister
# plate with the same layout, or a re-import of the plate's own export. The
# file's wells are then already grouped here. ``sel_add`` strips a new group's
# wells out of every other group, so an Add that went through it emptied the
# existing groups and parked their wells under suffixed names — while the dialog
# said Add "cannot lose anything". The user is now asked, naming the wells.

def _seed_overlapping(ctx):
    """``Treated`` shares A01 with the file; ``Old`` holds only B01, which the
    file's ``Control`` also claims; ``Spare`` shares nothing."""
    sc.sel_add(ctx, name="Treated", wells=["A01", "H01"])
    sc.sel_add(ctx, name="Old", wells=["B01"])
    sc.sel_add(ctx, name="Spare", wells=["G01"])
    ctx.coordinator.seen.clear()
    return _plate(ctx)


def test_add_over_grouped_wells_asks_naming_the_wells_and_their_groups(ctx, dialogs, tmp_path):
    _seed_overlapping(ctx)
    dialogs.path, dialogs.mode = _file(tmp_path, V2), sc.IMPORT_ADD
    dialogs.overlap = None

    sc.import_selections_from_json(ctx)

    assert dialogs.overlap_asked == [{"A01": "Treated", "B01": "Old"}], (
        "the second question must name exactly the wells already grouped here, "
        "with the group each one is in now")


def test_cancelling_the_overlap_question_changes_nothing(ctx, dialogs, tmp_path):
    before = _seed_overlapping(ctx)
    dialogs.path, dialogs.mode = _file(tmp_path, V2), sc.IMPORT_ADD
    dialogs.overlap = None

    sc.import_selections_from_json(ctx)

    assert _plate(ctx) == before
    assert ctx.coordinator.seen == [] and dialogs.refreshed == 0 and ctx.toasts == []


def test_skip_leaves_every_existing_group_whole(ctx, dialogs, tmp_path):
    """Skip: the grouped wells stay where they are, and the imported groups get
    only the wells nobody had. ``Control`` holds nothing but B01, which ``Old``
    keeps, so it is not added at all rather than added as an empty husk."""
    before = _seed_overlapping(ctx)
    dialogs.path, dialogs.mode = _file(tmp_path, V2), "add"
    dialogs.overlap = "skip"

    sc.import_selections_from_json(ctx)

    assert _plate(ctx)[:3] == before, "Skip must not take a single well from an existing group"
    assert _plate(ctx)[3:] == [("Treated_v2", ["A02"])]
    assert ctx.toasts == ["Imported 1 group(s) from groups.json; 1 left out, "
                          "every well of it already in a group."]


def test_move_hands_the_wells_to_the_imported_groups(ctx, dialogs, tmp_path):
    """Move: the file's groups get all their wells. ``Treated`` keeps H01;
    ``Old``, left with nothing, is removed rather than kept as an empty group,
    so the file's ``Control`` is not renamed around a husk."""
    _seed_overlapping(ctx)
    dialogs.path, dialogs.mode = _file(tmp_path, V2), "add"
    dialogs.overlap = "move"

    sc.import_selections_from_json(ctx)

    assert _plate(ctx) == [("Treated", ["H01"]), ("Spare", ["G01"]),
                           ("Treated_v2", ["A01", "A02"]), ("Control", ["B01"])]


def test_an_import_redraws_once_not_once_per_group(ctx, dialogs, tmp_path):
    """Every ``sel_add`` asks for a full ``rebuild_all``; an import of a
    96-group Quick Replicates file must not redraw 96 times against plates
    holding 1..95 of its groups."""
    _seed_overlapping(ctx)
    dialogs.path, dialogs.mode = _file(tmp_path, V2), "add"
    dialogs.overlap = "move"

    sc.import_selections_from_json(ctx)

    assert ctx.coordinator.seen == [["Treated", "Spare", "Treated_v2", "Control"]]


def test_add_keeps_the_file_ids_unless_they_collide(ctx, dialogs, tmp_path):
    """Saved plots hide groups by id, so an id is worth keeping — but never at
    the cost of two groups sharing one."""
    dialogs.path = _file(tmp_path, V2)
    sc.import_selections_from_json(ctx)
    assert [s["id"] for s in ctx.state.selection.selections] == ["g1", "g2"]

    dialogs.mode, dialogs.overlap = "add", "skip"
    sc.import_selections_from_json(ctx)
    assert [s["id"] for s in ctx.state.selection.selections] == ["g1", "g2"], (
        "Skip over a file whose every well is already grouped adds nothing")

    sc.sel_set_composition(ctx, "g1", wells=["H01"])
    sc.import_selections_from_json(ctx)
    ids = [s["id"] for s in ctx.state.selection.selections]
    assert len(ids) == 3 and ids[:2] == ["g1", "g2"] and ids[2] not in ("g1", "g2"), (
        f"the re-imported Treated's id collides with the existing g1, so it is "
        f"reminted rather than shared: {ids}")


# ── Replace keeps what the file says about each group ────────────────────────

HIDDEN = {
    "schema_version": 2,
    "selections": [
        {"id": "g1", "name": "Treated", "wells": ["A01", "A02"], "hidden": False},
        {"id": "g2", "name": "Control", "wells": ["B01"], "hidden": True},
        {"id": "g3", "name": "Vehicle", "wells": ["C01"], "hidden": False},
    ],
    # Neither the first nor the last group, so "first added" and "last added"
    # both answer differently from "the file's own current group".
    "current_id": "g2",
}


def test_replace_keeps_each_group_s_hidden_flag_and_id_and_the_current_group(
        ctx, dialogs, tmp_path):
    """What the removed *Load bar group definitions* loader restored, and what a
    saved plot's ``selection_visibility.hidden_ids`` is keyed on."""
    _seed(ctx)
    dialogs.path, dialogs.mode = _file(tmp_path, HIDDEN), sc.IMPORT_REPLACE

    sc.import_selections_from_json(ctx)

    sels = ctx.state.selection.selections
    assert [(s["id"], s["name"], s["hidden"]) for s in sels] == [
        ("g1", "Treated", False), ("g2", "Control", True), ("g3", "Vehicle", False)]
    assert ctx.state.selection.current_selection_id == "g2"
    assert len(ctx.coordinator.seen) == 1, "one redraw for the whole file"


def test_replace_discards_the_current_groups(ctx, dialogs, tmp_path):
    _seed(ctx)
    dialogs.path, dialogs.mode = _file(tmp_path, V2), sc.IMPORT_REPLACE

    sc.import_selections_from_json(ctx)

    assert _plate(ctx) == [("Treated", ["A01", "A02"]), ("Control", ["B01"])], (
        "after Replace the plate holds the file's groups and nothing else — "
        "and the file's Treated keeps its own name, since nothing clashes now")
    assert ctx.toasts == ["Replaced the groups with 2 group(s) from groups.json."]


def test_cancelling_the_question_changes_nothing(ctx, dialogs, tmp_path):
    before = _seed(ctx)
    dialogs.path, dialogs.mode = _file(tmp_path, V2), None

    sc.import_selections_from_json(ctx)

    assert _plate(ctx) == before
    assert ctx.coordinator.seen == [] and dialogs.refreshed == 0


# ── the imports that must not happen ─────────────────────────────────────────

def test_cancelling_the_picker_changes_nothing(ctx, dialogs):
    before = _seed(ctx)
    dialogs.path = ""

    sc.import_selections_from_json(ctx)

    assert _plate(ctx) == before
    assert dialogs.critical == [] and dialogs.warning == [], "a cancel is not an error"


@pytest.mark.parametrize("content", ["{not json at all", None], ids=["broken", "missing"])
def test_an_unreadable_file_is_reported_and_the_groups_survive(ctx, dialogs, tmp_path, content):
    before = _seed(ctx)
    path = tmp_path / "bad.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    dialogs.path, dialogs.mode = str(path), sc.IMPORT_REPLACE

    sc.import_selections_from_json(ctx)

    assert len(dialogs.critical) == 1
    assert dialogs.asked == []
    assert _plate(ctx) == before


@pytest.mark.parametrize("payload", [{"selections": []}, {"other": 1}, ["not", "groups"]],
                         ids=["empty", "unrelated", "no-dicts"])
def test_a_file_with_no_groups_is_reported_before_anything_is_asked(ctx, dialogs, tmp_path, payload):
    """Even with Replace pre-answered: a file holding no usable group must
    never reach the question, let alone empty the plate."""
    before = _seed(ctx)
    dialogs.path, dialogs.mode = _file(tmp_path, payload), sc.IMPORT_REPLACE

    sc.import_selections_from_json(ctx)

    assert len(dialogs.warning) == 1
    assert dialogs.asked == []
    assert _plate(ctx) == before


def test_replace_never_empties_the_plate_for_a_file_with_nothing_usable(ctx):
    """The guard inside the loop, for a caller that skips the dialog's check."""
    before = _seed(ctx)
    assert sc.merge_imported_selections(ctx, ["not a group"], replace=True) == 0
    assert _plate(ctx) == before


# ── the button that was removed stays removed ────────────────────────────────

def test_the_groups_list_has_no_import_button_of_its_own(qapp):
    """Two buttons called Import on one layer, one adding and one replacing,
    is the trap this change removed. The list widget keeps none."""
    from widgets.saved_selections_list import SavedSelectionsList

    lst = SavedSelectionsList()
    try:
        assert not hasattr(lst, "importRequested")
        assert not hasattr(lst, "_import_btn")
    finally:
        lst.deleteLater()
