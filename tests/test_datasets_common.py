"""Qt-free tests for the shared Datasets-pane helpers under ``datasets/_common``.

Complements ``tests/test_concatenate_compute.py`` (which exercises these
through Concatenate's re-exports) with direct coverage of the generalised
API: multi-marker destination-state predicates and the field-agnostic
``rewrite_member_field``.
"""

from __future__ import annotations

import zipfile

import pytest

from datasets._common import containers as ct
from datasets._common import dataset_paths as dp
from datasets._common import member_names as mn
from tests.fixtures.dataset_builder import build_dataset
from util.timepoint import parse_hours
from util.well_token import canonical_well_label
from well_viewer.data.image_identity import load_schema


def _schema():
    return load_schema({
        "schema_fields": ["experiment", "channel", "well", "fov", "timepoint"],
        "separator": "_",
    })


# ── dataset_paths ────────────────────────────────────────────────────────────
def test_well_from_stem_pads_and_uppercases():
    assert dp.well_from_stem("measurements_a1") == "A01"
    assert dp.well_from_stem("measurements_B10") == "B10"
    assert dp.well_from_stem("no_well_here") is None


def test_resolve_dataset_requires_in_and_out(tmp_path):
    with pytest.raises(dp.DatasetPathsError, match="not a dataset"):
        dp.resolve_dataset(tmp_path, label="dataset")


def test_dataset_wells_union_of_in_and_out(tmp_path):
    build_dataset(tmp_path, wells=["A01", "B02"], timepoints=["0", "24h"])
    paths = dp.resolve_dataset(tmp_path, label="d")
    assert dp.dataset_wells(paths) == {"A01", "B02"}


def test_dataset_wells_includes_folder_mode_output(tmp_path):
    """Regression guard: dataset_wells's own docstring claims "every 96-well
    position present in a dataset's in or out", but its body used to union
    only in_containers/out_csvs/out_zips -- never out_folders (the
    compressed_output=False layout out_folders() reads, see
    has_folder_mode_output). A well present only as a folder-mode out/<WELL>/
    -- no zip, no CSV -- was silently missing from the result."""
    build_dataset(tmp_path, wells=["A01"], timepoints=["0"])
    (tmp_path / "out" / "H12").mkdir()  # folder-mode QC output, no zip/CSV
    paths = dp.resolve_dataset(tmp_path, label="d")
    assert dp.dataset_wells(paths) == {"A01", "H12"}


def test_out_csvs_duplicate_prefix_overwrites_by_glob_order(tmp_path):
    build_dataset(tmp_path, wells=["A01"], timepoints=["0"], csv_prefix="measurements")
    (tmp_path / "out" / "run2_A01.csv").write_text("well,fov,timepoint,timepoint_hours\n")
    paths = dp.resolve_dataset(tmp_path, label="d")
    csvs = dp.out_csvs(paths)
    # Exactly one of the two candidate CSVs survives under the "A01" key —
    # documents the known limitation callers that care about a mixed-prefix
    # out/ folder (Validate) must not rely on this map for that purpose.
    assert csvs["A01"].name in ("measurements_A01.csv", "run2_A01.csv")
    assert len(list((tmp_path / "out").glob("*.csv"))) == 2


def test_destination_state_predicates_are_per_marker(tmp_path):
    dest_root = tmp_path / "dest"
    (dest_root / "in").mkdir(parents=True)
    (dest_root / "in" / "A01.zip").touch()
    dest = dp.DatasetPaths(root=dest_root, in_dir=dest_root / "in", out_dir=dest_root / "out")

    assert dp.destination_has_content(dest)
    assert not dp.destination_has_marker(dest, ".aw_subset_in_progress")
    assert dp.destination_has_unrecognized_content(dest, ".aw_subset_in_progress")

    (dest_root / ".aw_subset_in_progress").write_text("")
    assert dp.destination_has_marker(dest, ".aw_subset_in_progress")
    assert not dp.destination_has_marker(dest, ".aw_merge_in_progress")
    assert dp.destination_is_failed_run(dest, ".aw_subset_in_progress")
    assert not dp.destination_has_unrecognized_content(dest, ".aw_subset_in_progress")

    (dest_root / "out").mkdir()
    (dest_root / "out" / "pipeline_info.json").write_text("{}")
    assert dp.destination_is_complete_dataset(dest)
    assert not dp.destination_is_failed_run(dest, ".aw_subset_in_progress")


# ── containers ───────────────────────────────────────────────────────────────
def test_read_members_zip_and_folder_agree(tmp_path):
    zpath = tmp_path / "A01.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("a.tif", b"one")
        zf.writestr("b.tif", b"two")
    assert sorted(ct.read_members(zpath)) == [("a.tif", b"one"), ("b.tif", b"two")]

    folder = tmp_path / "A02"
    folder.mkdir()
    (folder / "a.tif").write_bytes(b"one")
    (folder / "b.tif").write_bytes(b"two")
    assert sorted(ct.read_members(folder)) == [("a.tif", b"one"), ("b.tif", b"two")]


def test_write_zip_drops_collisions_and_reports_them(tmp_path):
    dest = tmp_path / "out.zip"
    written, dropped = ct.write_zip(dest, [("a.tif", b"1"), ("a.tif", b"2"), ("b.tif", b"3")])
    assert written == {"a.tif", "b.tif"}
    assert dropped == {"a.tif"}
    with zipfile.ZipFile(dest) as zf:
        assert zf.read("a.tif") == b"1"  # first write wins


def test_clear_destination_removes_in_and_out(tmp_path):
    dest_root = tmp_path / "dest"
    (dest_root / "in").mkdir(parents=True)
    (dest_root / "out").mkdir(parents=True)
    (dest_root / "in" / "A01.zip").write_bytes(b"x")
    dest = dp.DatasetPaths(root=dest_root, in_dir=dest_root / "in", out_dir=dest_root / "out")
    ct.clear_destination(dest)
    assert not dest.in_dir.exists()
    assert not dest.out_dir.exists()


# ── member_names ─────────────────────────────────────────────────────────────
def test_rewrite_member_field_timepoint_shift():
    schema = _schema()

    def _shift(old_token):
        hours = parse_hours(old_token)
        if hours is None:
            return None
        return f"{hours + 24:g}h"

    new_name, rewrote = mn.rewrite_member_field(
        "exp_GFP_A01_F001_0h.tif", schema=schema, channels=(), field="timepoint",
        new_value=_shift,
    )
    assert rewrote
    assert new_name == "exp_GFP_A01_F001_24h.tif"


def test_rewrite_member_field_well_remap():
    schema = _schema()

    def _remap(old_token):
        if canonical_well_label(old_token) != "A01":
            return None
        return "E01"

    new_name, rewrote = mn.rewrite_member_field(
        "exp_GFP_A01_F001_0h.tif", schema=schema, channels=(), field="well",
        new_value=_remap,
    )
    assert rewrote
    assert new_name == "exp_GFP_E01_F001_0h.tif"

    # A member sitting in the wrong well's archive refuses rather than guesses.
    new_name, rewrote = mn.rewrite_member_field(
        "exp_GFP_B02_F001_0h.tif", schema=schema, channels=(), field="well",
        new_value=_remap,
    )
    assert not rewrote
    assert new_name == "exp_GFP_B02_F001_0h.tif"


def test_rewrite_member_field_refuses_non_conformant_names():
    schema = _schema()
    new_name, rewrote = mn.rewrite_member_field(
        "notes.tif", schema=schema, channels=(), field="timepoint",
        new_value=lambda old: "99h",
    )
    assert not rewrote
    assert new_name == "notes.tif"

    new_name, rewrote = mn.rewrite_member_field(
        "readme.txt", schema=schema, channels=(), field="timepoint",
        new_value=lambda old: "99h",
    )
    assert not rewrote


def test_rewrite_member_field_preserves_channel_scoped_role_suffix():
    schema = _schema()
    new_name, rewrote = mn.rewrite_member_field(
        "exp_GFP_A01_F001_0h_tophat_gfp.tif", schema=schema, channels=("gfp",),
        field="timepoint", new_value=lambda old: "24h",
    )
    assert rewrote
    assert new_name == "exp_GFP_A01_F001_24h_tophat_gfp.tif"
