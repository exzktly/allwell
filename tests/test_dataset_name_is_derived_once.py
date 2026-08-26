"""The dataset's display name comes from one rule, not from ``data_dir.name``.

Review loads ``<dataset_root>/out`` for the standard Analyze-pipeline layout, so
a folder's own name is the literal string "out" on every such dataset. Three
places in the app answered "what is this dataset called?" and only one of them —
the shell's dataset button — climbed to the root: the Saved Plots exports opened
their cover page with "Dataset: out", and Home's masthead titled every pipeline
dataset "out".

The behaviour tests pin the rule; the guard pins that nobody re-derives it,
because the failure mode here is not one site being wrong but a fourth site
being written the obvious way.
"""

from __future__ import annotations

import ast

import pytest

from well_viewer.domain.dataset_name import dataset_display_name

from tests._repo_scan import parse_source, python_sources, rel


# ── the rule ─────────────────────────────────────────────────────────────────

@pytest.fixture
def split_dataset(tmp_path):
    """A dataset in the pipeline's in/out layout; returns the loaded ``out``."""
    root = tmp_path / "Plate7_EGFR_screen"
    (root / "in").mkdir(parents=True)
    (root / "out").mkdir()
    return root / "out"


def test_the_pipeline_layout_is_named_for_its_root(split_dataset):
    assert dataset_display_name(split_dataset) == "Plate7_EGFR_screen"


def test_a_flat_dataset_keeps_its_own_name(tmp_path):
    flat = tmp_path / "some_plate"
    flat.mkdir()
    assert dataset_display_name(flat) == "some_plate"


def test_a_flat_dataset_that_happens_to_be_called_out_keeps_its_own_name(tmp_path):
    """The climb is for the split layout, detected structurally by the sibling
    ``in`` directory — not for every folder that happens to be named "out"."""
    flat = tmp_path / "out"
    flat.mkdir()
    assert dataset_display_name(flat) == "out"


def test_a_sibling_in_that_is_a_file_is_not_a_split_layout(tmp_path):
    """``is_dir`` rather than ``exists``: a file called ``in`` beside ``out``
    is not the pipeline's layout, and borrowing the parent's name there would
    be a guess."""
    root = tmp_path / "plate"
    (root / "out").mkdir(parents=True)
    (root / "in").write_text("not a directory")
    assert dataset_display_name(root / "out") == "out"


def test_no_dataset_has_no_name(tmp_path):
    assert dataset_display_name(None) == ""


def test_a_string_path_is_accepted(split_dataset):
    """Callers hold ``data_dir`` as a ``Path`` in some places and as the string
    the dataset button was given in others."""
    assert dataset_display_name(str(split_dataset)) == "Plate7_EGFR_screen"


# ── the callers ──────────────────────────────────────────────────────────────

def test_the_export_cover_names_the_dataset_root(split_dataset):
    """Reported: both exports opened with the folder name "out"."""
    from types import SimpleNamespace

    from well_viewer.features.saved_plots.controller import _dataset_name

    ctx = SimpleNamespace(state=SimpleNamespace(
        dataset=SimpleNamespace(data_dir=split_dataset)))
    assert _dataset_name(ctx) == "Plate7_EGFR_screen"


def test_the_home_masthead_names_the_dataset_root(split_dataset):
    """Same defect, unreported: Home titled every pipeline dataset "out"."""
    from types import SimpleNamespace

    from well_viewer.features.home.collect import identity

    ctx = SimpleNamespace(state=SimpleNamespace(
        dataset=SimpleNamespace(data_dir=split_dataset)))
    assert identity(ctx)["name"] == "Plate7_EGFR_screen"


# ── the guard ────────────────────────────────────────────────────────────────

#: The one module allowed to take ``.name`` off a dataset directory — it is
#: where the rule lives.
_RULE_MODULE = "well_viewer/domain/dataset_name.py"

#: Names that hold the directory Review loaded. ``.name`` on one of them is the
#: literal "out" for a pipeline dataset, which is the bug.
#:
#: ``data_dir`` only, deliberately. Batch Export has an ``out_dir`` in four
#: places and takes ``.name`` off it in all four, but that is the destination
#: folder the *user* chose for the export and its name is exactly what those
#: status lines mean to say. Widening the roster to catch a second spelling of
#: "the dataset directory" caught a different concept instead.
_DATASET_DIR_NAMES = ("data_dir",)


def _dataset_dir_attribute_reads(tree) -> list[tuple[int, str]]:
    """Every ``<dataset dir>.name`` read in *tree*, as ``(line, expression)``.

    Matches the bare local (``data_dir.name``), the attribute
    (``dataset.data_dir.name``) and the wrapped form (``Path(data_dir).name``),
    which are the three spellings the three original sites used between them.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr != "name":
            continue
        target = node.value
        # ``Path(data_dir).name`` — look through a one-argument call.
        if isinstance(target, ast.Call) and len(target.args) == 1:
            target = target.args[0]
        if isinstance(target, ast.Attribute):
            label = target.attr
        elif isinstance(target, ast.Name):
            label = target.id
        else:
            continue
        if label in _DATASET_DIR_NAMES:
            found.append((node.lineno, f"{label}.name"))
    return found


def test_nothing_re_derives_the_dataset_name():
    """A fourth site written the obvious way is the regression to catch.

    The three that existed were spelled three different ways in three packages,
    and each was locally reasonable — which is exactly why a reviewer reading one
    of them would not have seen the bug.
    """
    offenders = []
    for path in python_sources():
        where = rel(path)
        if where == _RULE_MODULE:
            continue
        for line, expression in _dataset_dir_attribute_reads(parse_source(path)):
            offenders.append(f"{where}:{line}: {expression}")
    assert not offenders, (
        "the dataset's display name comes from "
        "`well_viewer.domain.dataset_name.dataset_display_name`, not from the "
        "loaded directory's own name — Review loads `<dataset_root>/out`, so "
        "that name is the literal \"out\" on every pipeline dataset:\n  "
        + "\n  ".join(offenders))
