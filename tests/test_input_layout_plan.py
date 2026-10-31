"""One layout decision, and the two consumers that must not disagree about it.

Analyze answers "what will this folder do?" twice: once under the input field
the moment a folder is picked, and once at Run. Both render
:func:`plan_input_layout`, so a ``✓`` in the readout is a promise the run keeps.

The pipeline reads one ``<WELL>.zip`` per well and nothing else. Loose images
and per-well folders are refused by name — beside the archives as well as
instead of them, because a run would silently skip either — and the message
sends the user to Datasets → Zip loose folder. These tests pin that table for
every shape and both selections (``plate`` and ``plate/in``).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from services.input_resolution_service import (
    EMPTY_IN,
    GROUPED,
    NEEDS_ZIPPING,
    NO_INPUT,
    NOT_A_DIR,
    UNRECOGNISED,
    plan_input_layout,
    resolve_input_output,
)


def _tif(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00")


def _zip(path: Path, members: tuple[str, ...] = ("exp_NIR_A01_1_T01.tif",)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for m in members:
            zf.writestr(m, b"\x00")


# ── the shapes ───────────────────────────────────────────────────────────────
# Each builder populates the *grouped* folder it is handed, so the same builder
# describes both selections: it is called on ``plate/in`` either way, and only
# the folder the user picks changes.

def _well_zips(grouped: Path) -> None:
    _zip(grouped / "A01.zip")
    _zip(grouped / "B02.zip")


def _well_folders(grouped: Path) -> None:
    for w in ("A01", "B02"):
        _tif(grouped / w / f"exp_NIR_{w}_1_T01.tif")


def _loose_tifs(grouped: Path) -> None:
    for w in ("A01", "B02", "C03", "D04"):
        _tif(grouped / f"exp_NIR_{w}_1_T01.tif")


def _loose_beside_zips(grouped: Path) -> None:
    _zip(grouped / "A01.zip")
    _tif(grouped / "exp_NIR_B02_1_T01.tif")


def _non_well_zips(grouped: Path) -> None:
    _zip(grouped / "Plate1_A1.zip")
    _zip(grouped / "Plate1_A2.zip")


def _nested_well_zips(grouped: Path) -> None:
    _zip(grouped / "t0" / "A01.zip")


def _folder_beside_its_own_zip(grouped: Path) -> None:
    """What earlier versions left behind on purpose: the archive is what runs."""
    _zip(grouped / "A01.zip")
    _tif(grouped / "A01" / "exp_NIR_A01_1_T01.tif")


def _empty(grouped: Path) -> None:
    grouped.mkdir(parents=True, exist_ok=True)


SHAPES = [
    pytest.param(_well_zips, GROUPED, id="well-zips"),
    pytest.param(_folder_beside_its_own_zip, GROUPED, id="twin-folder"),
    pytest.param(_well_folders, NEEDS_ZIPPING, id="well-folders"),
    pytest.param(_loose_tifs, NEEDS_ZIPPING, id="loose-tifs"),
    pytest.param(_loose_beside_zips, NEEDS_ZIPPING, id="loose-beside-zips"),
    pytest.param(_non_well_zips, UNRECOGNISED, id="non-well-zips"),
    pytest.param(_nested_well_zips, UNRECOGNISED, id="nested-well-zips"),
    pytest.param(_empty, EMPTY_IN, id="empty"),
]


@pytest.mark.parametrize("build, action", SHAPES)
@pytest.mark.parametrize("select", ["parent", "in"])
def test_both_selections_reach_the_same_verdict(tmp_path, build, action, select):
    """Picking ``plate`` and picking ``plate/in`` must classify identically."""
    root = tmp_path / "plate"
    grouped = root / "in"
    grouped.mkdir(parents=True)
    build(grouped)

    plan = plan_input_layout(root if select == "parent" else grouped)
    assert plan.action == action
    if action == GROUPED:
        assert plan.runnable
        assert plan.input_dir == grouped
        assert plan.output_dir == root / "out"
    else:
        assert not plan.runnable and plan.input_dir is None


@pytest.mark.parametrize("build, action", SHAPES)
def test_the_run_does_what_the_plan_said(tmp_path, build, action):
    """Run refuses exactly the plans the readout marked unrunnable, with the
    readout's own sentence in the refusal."""
    root = tmp_path / "plate"
    grouped = root / "in"
    grouped.mkdir(parents=True)
    build(grouped)
    plan = plan_input_layout(root)
    if plan.runnable:
        assert resolve_input_output(root) == (grouped, root / "out")
    else:
        with pytest.raises(ValueError) as refused:
            resolve_input_output(root)
        assert plan.message in str(refused.value)


def test_a_refusal_names_what_it_found_and_where_to_go(tmp_path):
    grouped = tmp_path / "plate" / "in"
    _zip(grouped / "A01.zip")
    _loose_tifs(grouped)
    _tif(grouped / "C03" / "exp_NIR_C03_1_T01.tif")

    plan = plan_input_layout(grouped)
    assert plan.action == NEEDS_ZIPPING
    assert plan.loose_count == 4
    assert "4 loose image(s) and 1 well folder(s) with no zip" in plan.message, plan.message
    assert "Datasets → Zip loose folder" in plan.message


def test_loose_images_at_the_root_are_refused_beside_an_in_folder(tmp_path):
    """Loose images next to ``in/`` were grouped into it at Run. Nothing does
    that now, so a run would silently leave them out."""
    root = tmp_path / "plate"
    _well_zips(root / "in")
    _loose_tifs(root)

    plan = plan_input_layout(root)
    assert plan.action == NEEDS_ZIPPING
    assert plan.loose_count == 4


def test_well_folders_at_a_root_with_no_in_folder_are_refused_by_name(tmp_path):
    root = tmp_path / "plate"
    _well_folders(root)

    plan = plan_input_layout(root)
    assert plan.action == NEEDS_ZIPPING
    assert "2 well folder(s)" in plan.message, plan.message


def test_a_root_with_nothing_in_it_is_still_an_error(tmp_path):
    root = tmp_path / "plate"
    root.mkdir()
    plan = plan_input_layout(root)
    assert plan.action == NO_INPUT and not plan.runnable

    plan = plan_input_layout(root / "nope.txt")
    assert plan.action == NOT_A_DIR and not plan.runnable


def test_the_readout_never_promises_more_than_the_run_delivers(tmp_path):
    """Every shape, through the real widget. A ``✓`` in the label is a
    promise, so it must appear if and only if the plan is runnable."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from analyze.tab import AnalyzeTab

    tab = AnalyzeTab()
    for i, (build, action) in enumerate(p.values for p in SHAPES):
        root = tmp_path / f"plate{i}"
        grouped = root / "in"
        grouped.mkdir(parents=True)
        build(grouped)
        for target in (root, grouped):
            tab._input_edit.setText(str(target))
            tab._refresh_output()
            label = tab._layout_lbl.text()
            runnable = action == GROUPED
            assert label.startswith("✓") is runnable, (target, action, label)
            assert tab._output_lbl.text() == (
                str(root / "out") if runnable else "—")


# ── a sidecar is not one of the run's images ────────────────────────────────

def test_an_appledouble_twin_is_not_counted_as_a_loose_image(tmp_path):
    """macOS writes `._<name>` beside every file copied to a non-HFS volume,
    which is the commonest way a real plate arrives with exactly twice as many
    "TIFs" as it has images. The refusal counts images, not sidecars."""
    root = tmp_path / "plate"
    root.mkdir()
    for name in ("Exp_GFP_A01_F1_T1.tif", "._Exp_GFP_A01_F1_T1.tif",
                 "Exp_GFP_A02_F1_T1.TIFF", "._Exp_GFP_A02_F1_T1.TIFF"):
        _tif(root / name)

    plan = plan_input_layout(root)
    assert plan.action == NEEDS_ZIPPING and plan.loose_count == 2, plan


def test_a_folder_holding_only_sidecars_and_archives_runs(tmp_path):
    grouped = tmp_path / "plate" / "in"
    _well_zips(grouped)
    _tif(grouped / "._exp_NIR_A01_1_T01.tif")
    (grouped / ".DS_Store").write_bytes(b"x")
    assert plan_input_layout(grouped).action == GROUPED
