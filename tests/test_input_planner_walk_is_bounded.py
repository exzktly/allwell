"""The Analyze pane's input planner stops walking a folder too large to be one plate's input.

``services.input_resolution_service.plan_input_layout`` runs on the GUI thread whenever the input field is set —
by Browse, by restoring a queued job, by opening a ``pipeline_info.json`` — and asks
``pipeline.input_layout.non_zip_input`` what the folder holds besides its well archives. That walk goes four levels
below every subfolder, and depth was its only bound: choosing ``/`` by mistake listed 566,000 folders and held the
window for about fifteen seconds. The planner now passes a folder budget (``PLANNER_MAX_FOLDERS``) and refuses a
folder that exhausts it, saying so, rather than presenting a partial walk as the whole one. The pipeline's own
refusal (``refuse_non_zip_input``) passes no budget and still walks everything, because it decides what a run reads.

The budget is patched small here so a tree that exceeds it costs a few dozen folders rather than ten thousand; the
rule under test is the same at either size.
"""

from __future__ import annotations

import pytest

import pipeline.input_layout as il
import services.input_resolution_service as irs


def _tree(root, folders: int, *, image_at_the_end: bool = False):
    """*root* holding *folders* subfolders in a chain of small branches, deep enough that the walk has to list each."""
    root.mkdir(parents=True, exist_ok=True)
    made = 0
    branch = 0
    while made < folders:
        top = root / f"branch{branch:03d}"
        top.mkdir()
        made += 1
        child = top
        for depth in range(3):
            if made >= folders:
                break
            child = child / f"level{depth}"
            child.mkdir()
            made += 1
        branch += 1
    if image_at_the_end:
        (root / f"branch{branch - 1:03d}" / "late.tif").write_bytes(b"x")
    return root


@pytest.fixture
def listed(monkeypatch):
    """Every folder the walk lists, in order."""
    seen: list = []
    real = il.files_and_subdirs

    def counting(folder):
        seen.append(folder)
        return real(folder)

    monkeypatch.setattr(il, "files_and_subdirs", counting)
    return seen


def test_a_walk_past_its_budget_stops_and_says_so(tmp_path, listed):
    root = _tree(tmp_path / "big", 40)
    found = il.non_zip_input(root, [], max_folders=10)
    assert found.too_large is True
    assert found.refused
    assert len(listed) == 10, f"the walk listed {len(listed)} folders against a budget of 10"
    assert found.problems()[0].startswith("more folders below it than one plate's input holds")


def test_a_walk_that_fits_its_budget_is_the_whole_walk(tmp_path, listed):
    """Exactly as many folders as the budget: every one is listed, and the answer is not ``too_large``."""
    root = _tree(tmp_path / "plate", 12, image_at_the_end=True)
    found = il.non_zip_input(root, [], max_folders=12)
    assert found.too_large is False
    assert len(listed) == 12
    assert [name for name, _n in found.nested] == [sorted(p.name for p in root.iterdir())[-1]], (
        "the image in the last folder the walk reaches was not found")


def test_the_pipeline_s_own_refusal_walks_everything(tmp_path, listed):
    """No budget, no bound: the run must refuse an image however deep in a large input it is."""
    root = _tree(tmp_path / "big", 40, image_at_the_end=True)
    found = il.non_zip_input(root, [])
    assert found.too_large is False
    assert len(listed) == 40
    assert found.nested, "the unbounded walk did not reach the image in the last folder"


def test_the_planner_refuses_a_folder_too_large_to_be_a_plate(tmp_path, monkeypatch, listed):
    monkeypatch.setattr(irs, "PLANNER_MAX_FOLDERS", 10)
    root = _tree(tmp_path / "home", 40)
    plan = irs.plan_input_layout(root)
    assert plan.action == irs.TOO_LARGE
    assert not plan.runnable
    assert plan.message == ("⚠ home/ holds more than 10 folders — more than one plate's input. Choose the "
                            "plate's own folder: the one holding in/, or the per-well zips.")
    assert len(listed) <= 10, f"the planner listed {len(listed)} folders against a budget of 10"


def test_the_planner_bounds_the_walk_inside_in_too(tmp_path, monkeypatch, listed):
    """A folder named ``in`` is classified by ``_plan_grouped_root``, the second of the planner's two walks."""
    monkeypatch.setattr(irs, "PLANNER_MAX_FOLDERS", 10)
    root = _tree(tmp_path / "in", 40)
    plan = irs.plan_input_layout(root)
    assert plan.action == irs.TOO_LARGE
    assert len(listed) <= 10


def test_the_root_of_a_volume_is_named_as_itself(tmp_path, monkeypatch):
    """``Path("/").name`` is empty; the readout must still say which folder it refused."""
    assert irs._too_large_plan(str(irs.Path("/"))).message.startswith("⚠ / holds more than")


def test_the_analyze_pane_says_so_when_pointed_at_one(qtbot, tmp_path, monkeypatch):
    """Through the real tab: setting the field, as Browse does, puts the planner's refusal under it."""
    pytest.importorskip("PySide6")
    from analyze.tab import AnalyzeTab

    monkeypatch.setattr(irs, "PLANNER_MAX_FOLDERS", 10)
    root = _tree(tmp_path / "home", 40)
    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    tab._input_edit.setText(str(root))
    assert tab._layout_lbl.text().startswith("⚠ home/ holds more than 10 folders")
    assert not tab._output_row.isVisibleTo(tab)


def _plate_with_in(root, wells=("A01", "B02")):
    import zipfile

    (root / "in").mkdir(parents=True)
    (root / "out").mkdir()
    for well in wells:
        with zipfile.ZipFile(root / "in" / f"{well}.zip", "w") as z:
            z.writestr(f"exp_GFP_{well}_f0_t0.tif", b"x")
    return root


def test_a_large_folder_beside_in_does_not_refuse_the_plate(tmp_path, monkeypatch, listed):
    """The pipeline reads only ``in/`` when the root holds one, so the root's other folders — ``out/``, the original
    export kept beside ``in/`` — decide nothing. Their size used to: the root's walk ran out of budget and the plate was
    refused with "Choose the plate's own folder: the one holding in/", which is the folder chosen.

    Wrong implementations this rejects: checking ``too_large`` before ``in/`` is considered (the defect); walking the
    root's siblings at all (``listed`` would include them)."""
    monkeypatch.setattr(irs, "PLANNER_MAX_FOLDERS", 10)
    root = _plate_with_in(tmp_path / "plate")
    _tree(root / "export", 40)
    plan = irs.plan_input_layout(root)
    assert plan.action == irs.GROUPED, plan.message
    assert irs.resolve_input_output(root)[:2] == (root / "in", root / "out")
    assert not [p for p in listed if "export" in p.parts], "the planner walked the export beside in/"


def test_loose_images_beside_in_are_still_refused(tmp_path, monkeypatch):
    """Not walking the root's folders must not stop it seeing images at the root itself, which a run would skip."""
    monkeypatch.setattr(irs, "PLANNER_MAX_FOLDERS", 10)
    root = _plate_with_in(tmp_path / "plate")
    (root / "exp_GFP_C03_f0_t0.tif").write_bytes(b"x")
    plan = irs.plan_input_layout(root)
    assert plan.action == irs.NEEDS_ZIPPING
    assert "1 loose image(s)" in plan.message
