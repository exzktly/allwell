"""Characterization tests for ``find_well_images_and_masks`` (audit A4).

This function is the single highest-complexity node in the tree (CC 98). Before
decomposing it into named helpers, these tests pin its exact behaviour across
every discovery layout it supports, so the refactor can be proven
behaviour-identical:

* flat ``<well>/`` subfolder in ``data_dir``
* raw loose files in ``data_dir`` (rglob fallback) + foreign-well filtering
* structured ``in_dir`` plain zip + ``data_dir`` ``_out.zip``
* first-hit-wins precedence when the same (fov, tp) appears in both in/out
* ``in_dir`` / ``data_dir`` ``<well>/`` subfolders (folder mode, no zips)
* empty inputs

Each dict is compared as ``{(fov, tp): ImgRef.name}`` — stable and readable.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from well_viewer.data.image_discovery import find_well_images_and_masks


def _touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")


def _as_names(result):
    """(fluor, overlay, mask, tophat) → list of {(fov,tp): imgref.name} dicts."""
    return [{k: v.name for k, v in d.items()} for d in result]


def test_flat_well_subfolder_layout(tmp_path):
    dd = tmp_path / "out"
    for fn in ("exp_gfp_A1_F1_T1.tif", "exp_gfp_A1_F1_T1_labels.tif",
               "exp_gfp_A1_F1_T1_overlay.png", "exp_gfp_A1_F1_T1_tophat.tif"):
        _touch(dd / "A1" / fn)

    fluor, overlay, mask, tophat = _as_names(find_well_images_and_masks(dd, "A1"))
    assert fluor == {("F1", "T1"): "exp_gfp_A1_F1_T1.tif"}
    assert overlay == {("F1", "T1"): "exp_gfp_A1_F1_T1_overlay.png"}
    assert mask == {("F1", "T1"): "exp_gfp_A1_F1_T1_labels.tif"}
    assert tophat == {("F1", "T1"): "exp_gfp_A1_F1_T1_tophat.tif"}


def test_raw_file_fallback_filters_foreign_well(tmp_path):
    dd = tmp_path / "out"
    for fn in ("exp_gfp_A1_F1_T1.tif", "exp_gfp_A1_F1_T1_labels.tif",
               "exp_gfp_A2_F1_T1.tif"):  # A2 = foreign well, must be dropped
        _touch(dd / fn)

    fluor, overlay, mask, tophat = _as_names(find_well_images_and_masks(dd, "A1"))
    assert fluor == {("F1", "T1"): "exp_gfp_A1_F1_T1.tif"}
    assert mask == {("F1", "T1"): "exp_gfp_A1_F1_T1_labels.tif"}
    assert overlay == {}
    assert tophat == {}


def test_structured_in_out_zips(tmp_path):
    ind = tmp_path / "in"
    dd = tmp_path / "out"
    ind.mkdir()
    dd.mkdir()
    with zipfile.ZipFile(ind / "A1.zip", "w") as z:
        z.writestr("exp_gfp_A1_F1_T1.tif", b"")
        z.writestr("exp_gfp_A1_F1_T1_labels.tif", b"")
    with zipfile.ZipFile(dd / "A1_out.zip", "w") as z:
        z.writestr("exp_gfp_A1_F2_T1.tif", b"")
        z.writestr("exp_gfp_A1_F2_T1_tophat.tif", b"")

    fluor, overlay, mask, tophat = _as_names(
        find_well_images_and_masks(dd, "A1", in_dir=ind))
    assert fluor == {("F1", "T1"): "exp_gfp_A1_F1_T1.tif",
                     ("F2", "T1"): "exp_gfp_A1_F2_T1.tif"}
    assert mask == {("F1", "T1"): "exp_gfp_A1_F1_T1_labels.tif"}
    assert tophat == {("F2", "T1"): "exp_gfp_A1_F2_T1_tophat.tif"}
    assert overlay == {}


def test_in_dir_wins_on_duplicate_key(tmp_path):
    # Same (F1, T1) fluor exists in both the in_dir plain zip and the out zip.
    # in_dir is scanned first and setdefault means first-hit-wins.
    ind = tmp_path / "in"
    dd = tmp_path / "out"
    ind.mkdir()
    dd.mkdir()
    with zipfile.ZipFile(ind / "A1.zip", "w") as z:
        z.writestr("IN_gfp_A1_F1_T1.tif", b"")
    with zipfile.ZipFile(dd / "A1_out.zip", "w") as z:
        z.writestr("OUT_gfp_A1_F1_T1.tif", b"")

    fluor, *_ = _as_names(find_well_images_and_masks(dd, "A1", in_dir=ind))
    assert fluor == {("F1", "T1"): "IN_gfp_A1_F1_T1.tif"}


def test_in_out_well_subfolders(tmp_path):
    ind = tmp_path / "in"
    dd = tmp_path / "out"
    _touch(ind / "A1" / "exp_gfp_A1_F1_T1.tif")
    _touch(dd / "A1" / "exp_gfp_A1_F1_T1_labels.tif")

    fluor, overlay, mask, tophat = _as_names(
        find_well_images_and_masks(dd, "A1", in_dir=ind))
    assert fluor == {("F1", "T1"): "exp_gfp_A1_F1_T1.tif"}
    assert mask == {("F1", "T1"): "exp_gfp_A1_F1_T1_labels.tif"}


def test_empty_inputs_return_four_empty_dicts(tmp_path):
    dd = tmp_path / "out"
    dd.mkdir()
    result = find_well_images_and_masks(dd, "A1")
    assert result == ({}, {}, {}, {})
