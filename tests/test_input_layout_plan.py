"""One layout decision, and the two consumers that must not disagree about it.

Analyze answers "what will this folder do?" twice: once under the input field
the moment a folder is picked, and once at Run. Those were two implementations
with two different ideas of what counts as well content, and the difference was
user-visible in both directions:

* A folder that *contained* ``in/`` was accepted only when ``in/`` held a
  well-named zip or a well-named subfolder, while a folder *named* ``in`` was
  accepted whatever was inside it. So ``plate/in/Plate1_A1.zip`` ran when the
  user selected ``plate/in`` and failed with "Cannot determine input layout" —
  a message listing "Selected folder contains a sub-folder named in" as an
  unmet requirement — when they selected ``plate``.
* The readout's own copy of the predicate accepted *any* zip, so that same
  folder displayed "✓ Found in/ subfolder — using as input" right up until the
  run failed.

Both consumers now render :func:`plan_input_layout`. These tests pin the
layout table for every shape and both selections, which is what stops the two
from drifting apart again.

Kept out of the slow lane: the planner is pure, and the resolver branches
exercised here are the ones that never shell out to WellPlateZipper.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from services.input_resolution_service import (
    EMPTY_IN,
    FOLD_LOOSE,
    GROUP_LOOSE,
    GROUPED,
    NO_INPUT,
    NOT_A_DIR,
    UNRECOGNISED,
    ZIP_ROOT,
    plan_input_layout,
)

SCHEMA = "experiment:channel:well:fov:timepoint"
SEP = "_"


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


def _empty(grouped: Path) -> None:
    grouped.mkdir(parents=True, exist_ok=True)


SHAPES = [
    pytest.param(_well_zips, GROUPED, id="well-zips"),
    pytest.param(_well_folders, GROUPED, id="well-folders"),
    pytest.param(_loose_tifs, GROUP_LOOSE, id="loose-tifs"),
    pytest.param(_loose_beside_zips, FOLD_LOOSE, id="loose-beside-zips"),
    pytest.param(_non_well_zips, UNRECOGNISED, id="non-well-zips"),
    pytest.param(_nested_well_zips, UNRECOGNISED, id="nested-well-zips"),
    pytest.param(_empty, EMPTY_IN, id="empty"),
]


@pytest.mark.parametrize("build, action", SHAPES)
@pytest.mark.parametrize("select", ["parent", "in"])
def test_both_selections_reach_the_same_verdict(tmp_path, build, action, select):
    """Picking ``plate`` and picking ``plate/in`` must classify identically.

    This is the reported bug: a folder that is not named ``in`` "seems to fail"
    for shapes that work when ``in/`` itself is selected. ``non-well-zips`` and
    ``nested-well-zips`` are the two that used to diverge — accepted on the
    right, ``ValueError`` on the left.
    """
    root = tmp_path / "plate"
    grouped = root / "in"
    grouped.mkdir(parents=True)
    build(grouped)

    plan = plan_input_layout(root if select == "parent" else grouped)
    assert plan.action == action
    if action == EMPTY_IN:
        assert plan.input_dir is None and not plan.runnable
        assert "empty" in plan.message.lower()
    else:
        assert plan.runnable
        assert plan.input_dir == grouped
        assert plan.output_dir == root / "out"


def test_loose_tifs_at_the_root_still_beat_an_empty_in_folder(tmp_path):
    """The widening must not steal the fresh-drop case.

    A prior interrupted run can leave an empty ``in/`` next to the TIFs it
    never consumed. Those TIFs still have to reach WellPlateZipper, so an
    ``in/`` with nothing in it must not win the layout decision.
    """
    root = tmp_path / "plate"
    (root / "in").mkdir(parents=True)
    _loose_tifs(root)

    plan = plan_input_layout(root)
    assert plan.action == ZIP_ROOT
    assert plan.input_dir == root / "in"
    assert plan.loose_count == 4


def test_a_root_with_nothing_in_it_is_still_an_error(tmp_path):
    root = tmp_path / "plate"
    root.mkdir()
    plan = plan_input_layout(root)
    assert plan.action == NO_INPUT and not plan.runnable

    plan = plan_input_layout(root / "nope.txt")
    assert plan.action == NOT_A_DIR and not plan.runnable


def test_the_readout_never_promises_more_than_the_run_delivers(tmp_path):
    """Every shape, through the real widget, checked against the real resolver.

    The divergence this replaces was exactly this pairing: a readout that said
    "using as input" over a run that then raised. A ``✓`` in the label is a
    promise, so it must appear if and only if the plan is runnable.
    """
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from analyze_tab import AnalyzeTab

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
            runnable = action != EMPTY_IN
            assert label.startswith("✓") is runnable, (target, action, label)
            assert tab._output_lbl.text() == (
                str(root / "out") if runnable else "—")


# ── the readout counts what the run will fold ───────────────────────────────
#
# `loose_count` came from a glob over TIF suffixes while the *act* goes through
# `plan_loose_append`, which rejects a name whose well token will not parse and
# a file already present in its target archive. The two disagree by
# construction, and the readout is the one the user reads before pressing Run.

SCHEMA = "experiment:channel:well:fov:timepoint"


def _fold_fixture(tmp_path: Path) -> Path:
    """One new image, one duplicate of an archived member, one calibration frame."""
    grouped = tmp_path / "in"
    grouped.mkdir()
    _zip(grouped / "A01.zip", ("Exp_GFP_A01_F1_T1.tif",))
    for name in ("Exp_GFP_A01_F2_T1.tif",      # new
                 "Exp_GFP_A01_F1_T1.tif",      # already in the archive
                 "calibration.tif"):           # no well token
        _tif(grouped / name)
    return grouped


def test_the_fold_readout_counts_what_the_run_folds(tmp_path):
    from services.input_resolution_service import plan_loose_append

    grouped = _fold_fixture(tmp_path)
    plan = plan_input_layout(grouped, filename_schema=SCHEMA, filename_sep="_")
    acted = plan_loose_append(grouped, filename_schema=SCHEMA, filename_sep="_")

    assert plan.action == FOLD_LOOSE
    assert acted.total == 1, "the fixture no longer produces a mismatch to catch"
    assert plan.loose_count == acted.total, (
        f"the readout promises {plan.loose_count} and the run folds "
        f"{acted.total}")
    assert "1 loose TIF" in plan.message, plan.message


def test_without_a_schema_the_count_is_still_the_glob(tmp_path):
    """The schema is optional, and its absence is stated rather than guessed.

    Two internal callers want the layout and not the count. Given no schema
    there is no way to know which files would fold, and reporting a number
    derived from a rule that has not been supplied would be worse than
    reporting the plain file count.
    """
    grouped = _fold_fixture(tmp_path)
    plan = plan_input_layout(grouped)
    assert plan.action == FOLD_LOOSE
    assert plan.loose_count == 3


def test_a_folder_whose_loose_files_are_all_duplicates_is_not_a_fold(tmp_path):
    """"Will fold 0" invites the user to wonder what broke; nothing at all hides
    that the folder is untidy. It is a grouped root, and the line says why."""
    grouped = tmp_path / "in"
    grouped.mkdir()
    _zip(grouped / "A01.zip", ("Exp_GFP_A01_F1_T1.tif",))
    _tif(grouped / "Exp_GFP_A01_F1_T1.tif")

    plan = plan_input_layout(grouped, filename_schema=SCHEMA, filename_sep="_")
    assert plan.action == GROUPED
    assert plan.loose_count == 0
    assert "already in the well zips" in plan.message, plan.message


# ── a sidecar is not one of the run's images ────────────────────────────────

def test_an_appledouble_twin_is_not_counted_as_a_loose_tif(tmp_path):
    """`TIF_GLOB` is a suffix match, and a suffix is not a decision.

    macOS writes `._<name>` beside every file copied to a non-HFS volume, which
    is the commonest way a real plate arrives with exactly twice as many "TIFs"
    as it has images. That count is the readout, the `tifs > 3` threshold that
    decides the whole folder is a zipper input, and the `source_tifs` tuples
    the post-run deletion closures are built from.
    """
    from services.input_resolution_service import tif_files_in

    root = tmp_path / "plate"
    root.mkdir()
    for name in ("Exp_GFP_A01_F1_T1.tif", "._Exp_GFP_A01_F1_T1.tif",
                 "Exp_GFP_A02_F1_T1.TIFF", "._Exp_GFP_A02_F1_T1.TIFF"):
        _tif(root / name)

    found = sorted(p.name for p in tif_files_in(root))
    assert found == ["Exp_GFP_A01_F1_T1.tif", "Exp_GFP_A02_F1_T1.TIFF"], found
