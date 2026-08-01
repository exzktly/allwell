"""Audit A2 — shared stdlib-only (fov, tp) done-key helpers.

The remote client used to hand-copy twins of ``pipeline.io``'s done-FOV-key
readers because importing ``pipeline.io`` drags in numpy/tifffile/skimage. The
readers now live in the stdlib-only ``pipeline.incremental_keys`` and both sides
import them. These tests pin:

* the module stays imaging-stack-free (the constraint the whole fix exists for),
* ``services.remote_runner`` still imports without the imaging stack,
* the shared readers behave as the old twins did.
"""
from __future__ import annotations

import subprocess
import sys
import zipfile

from pipeline import incremental_keys as ik


def test_incremental_keys_is_imaging_stack_free():
    # A fresh interpreter importing only this module must not pull numpy et al.
    code = (
        "import sys; import pipeline.incremental_keys; "
        "heavy=[m for m in sys.modules if m.split('.')[0] in "
        "('numpy','tifffile','skimage','scipy','PIL','pandas')]; "
        "print(sorted(set(m.split('.')[0] for m in heavy)))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]", f"imaging stack leaked: {out.stdout}"


def test_remote_runner_stays_imaging_stack_free():
    code = (
        "import sys; import services.remote_runner; "
        "heavy=[m for m in sys.modules if m.split('.')[0] in "
        "('numpy','tifffile','skimage','scipy','PIL','pandas')]; "
        "print(sorted(set(m.split('.')[0] for m in heavy)))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]", f"remote client pulled imaging stack: {out.stdout}"


def test_pipeline_io_reexports_the_shared_names():
    from pipeline import io
    assert io.read_done_fov_keys is ik.read_done_fov_keys
    assert io.read_done_fov_keys_from_zip is ik.read_done_fov_keys_from_zip
    assert io.incremental_done_fov_keys is ik.incremental_done_fov_keys
    assert io._fov_tp_from_name is ik.fov_tp_from_name


def test_read_done_fov_keys_csv(tmp_path):
    csv = tmp_path / "w_A1.csv"
    csv.write_text("fov,timepoint,val\nF1,T1,5\nF2,T2,7\n")
    assert ik.read_done_fov_keys(csv) == {("F1", "T1"), ("F2", "T2")}
    # missing column → empty (process everything)
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n")
    assert ik.read_done_fov_keys(bad) == set()
    # missing file → empty
    assert ik.read_done_fov_keys(tmp_path / "nope.csv") == set()


def test_fov_tp_from_name_schema_indexed():
    schema = "experiment:channel:well:fov:timepoint"
    # role suffix past timepoint is ignored
    assert ik.fov_tp_from_name("exp_dapi_A1_F3_T2_labels.tif",
                               schema=schema, sep="_") == ("F3", "T2")


def test_read_done_fov_keys_from_zip(tmp_path):
    schema = "experiment:channel:well:fov:timepoint"
    zp = tmp_path / "A1_out.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("exp_dapi_A1_F1_T1_labels.tif", b"")
        z.writestr("exp_gfp_A1_F2_T1_overlay.png", b"")
        z.writestr("adir/", b"")  # directory entry ignored
    assert ik.read_done_fov_keys_from_zip(zp, schema=schema, sep="_") == {
        ("F1", "T1"), ("F2", "T1"),
    }
    assert ik.read_done_fov_keys_from_zip(tmp_path / "missing.zip",
                                          schema=schema, sep="_") == set()


def test_fov_keys_from_member_names_matches_reading_the_container(tmp_path):
    """The name-based helper is what both readers delegate to, and what
    Validate calls with members it has already listed — so a caller who skips
    the second open must get exactly what opening again would have given."""
    schema = "experiment:channel:well:fov:timepoint"
    names = [
        "exp_GFP_A01_F001_0h.tif",
        "exp_GFP_A01_F001_24h_labels.tif",
        "exp_GFP_A01_F002_0h.tif",
        "notes.txt",                      # parses to no fov/tp -> dropped
    ]

    zip_path = tmp_path / "A01_out.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for n in names:
            zf.writestr(n, b"x")
    from_zip = ik.read_done_fov_keys_from_zip(zip_path, schema=schema, sep="_")
    assert ik.fov_keys_from_member_names(names, schema=schema, sep="_") == from_zip

    folder = tmp_path / "A01"
    folder.mkdir()
    for n in names:
        (folder / n).write_bytes(b"x")
    (folder / ".DS_Store").write_bytes(b"x")
    from_folder = ik.read_done_fov_keys_from_folder(folder, schema=schema, sep="_")
    listed = [p.name for p in folder.iterdir() if p.is_file()]
    assert ik.fov_keys_from_member_names(
        listed, schema=schema, sep="_", skip_hidden=True) == from_folder


def test_zip_reader_keeps_its_own_hidden_file_behaviour(tmp_path):
    """The folder reader has always dropped dotfiles and the zip reader never
    has. Sharing one helper must not quietly change what an incremental run
    considers already done, so the filter stays opt-in per caller."""
    schema = "fov:timepoint"
    assert ik.fov_keys_from_member_names(
        [".hidden_0h.tif"], schema=schema, sep="_") == {(".hidden", "0h")}
    assert ik.fov_keys_from_member_names(
        [".hidden_0h.tif"], schema=schema, sep="_", skip_hidden=True) == set()
