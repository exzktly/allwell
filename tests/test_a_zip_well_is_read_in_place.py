"""A zip-mode well reads its images out of the archive, one field at a time.

The worker used to unpack every image the well needed into the scratch
directory before its first field ran. On a cluster, with scratch on the
networked home directory and eight workers unpacking a well each at once, that
held the start of every batch for about ten minutes while each field then took
two seconds. Now nothing is unpacked: each field reads its members from the
archive in place when it runs (``pipeline.zip_members``), and decodes them from
memory.

These tests drive ``process_well_zip_task`` itself, because the decision that
matters — whether anything is copied out before the fields run — is made there,
and a test of the reader alone would pass with the upfront unpack still in
place.
"""

from __future__ import annotations

import io
import threading
import zipfile
from pathlib import Path

import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")

import process_microscopy as pm  # noqa: E402
from pipeline import fov_checkpoint as _ckpt  # noqa: E402
from pipeline.zip_members import ZipMemberReader  # noqa: E402

_FIELDS = ("F001", "F002", "F003")


def _tif(value: int) -> bytes:
    buf = io.BytesIO()
    tifffile.imwrite(buf, np.full((8, 8), value, dtype=np.uint16))
    return buf.getvalue()


def _plate(tmp_path: Path) -> Path:
    """One well, three fields, two channels; every frame holds its own value,
    so a field that decoded another field's member (or the other channel)
    reads a different number."""
    zip_path = tmp_path / "A01.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, fov in enumerate(_FIELDS, start=1):
            zf.writestr(f"exp_DAPI_A01_{fov}_0h.tif", _tif(100 * i))
            zf.writestr(f"exp_GFP_A01_{fov}_0h.tif", _tif(100 * i + 7))
    return zip_path


def _run(tmp_path, monkeypatch, group):
    scratch = tmp_path / "scratch"
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(scratch))
    monkeypatch.setattr(pm, "process_image_group", group)
    return pm.process_well_zip_task(
        "A01", _plate(tmp_path), tmp_path / "out", {}, "DAPI", ["GFP"],
        "measurements"), scratch


def _images_under(root: Path) -> list[str]:
    return sorted(p.name for p in root.rglob("*.tif")) if root.exists() else []


def test_each_field_decodes_its_own_members_and_nothing_is_unpacked(tmp_path, monkeypatch):
    seen: dict[str, tuple[float, float]] = {}
    on_disk_while_running: list[list[str]] = []
    scratch_box: list[Path] = []

    def _group(nuclear_path, fluor_paths, **kwargs):
        on_disk_while_running.append(_images_under(scratch_box[0]))
        nir, fluors, _acq, _ceil = pm._load_image_group(
            nuclear_path, fluor_paths, ["GFP"], kwargs["open_image"])
        seen[nuclear_path.name] = (float(nir.max()), float(fluors[0].max()))
        return [{"nucleus_id": 1}], None

    scratch_box.append(tmp_path / "scratch")
    (records, failed), _scratch = _run(tmp_path, monkeypatch, _group)

    assert failed == []
    assert len(records) == len(_FIELDS)
    assert seen == {
        "exp_DAPI_A01_F001_0h.tif": (100.0, 107.0),
        "exp_DAPI_A01_F002_0h.tif": (200.0, 207.0),
        "exp_DAPI_A01_F003_0h.tif": (300.0, 307.0),
    }
    assert on_disk_while_running == [[], [], []], (
        "input images were written to scratch before the fields ran")


def test_a_field_resumed_from_its_checkpoint_never_reads_the_archive(tmp_path, monkeypatch):
    """The resumed field's members are not read at all — the old upfront unpack
    copied them out even though the checkpoint already held the field."""
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(tmp_path / "scratch"))
    staging = _ckpt._staging_dir_for(tmp_path / "out", "A01", "")
    staging.mkdir(parents=True)
    _ckpt._save_fov_checkpoint(staging, "exp_DAPI_A01_F001_0h",
                               [{"nucleus_id": 1, "well": "A01"}], "")

    read: list[str] = []
    real_read = ZipMemberReader.read

    def _spy(self, name):
        read.append(name)
        return real_read(self, name)

    monkeypatch.setattr(ZipMemberReader, "read", _spy)

    def _group(nuclear_path, fluor_paths, **kwargs):
        pm._load_image_group(nuclear_path, fluor_paths, ["GFP"], kwargs["open_image"])
        return [{"nucleus_id": 1}], None

    (records, failed), _scratch = _run(tmp_path, monkeypatch, _group)

    assert failed == [] and len(records) == len(_FIELDS)
    assert sorted(read) == [
        "exp_DAPI_A01_F002_0h.tif", "exp_DAPI_A01_F003_0h.tif",
        "exp_GFP_A01_F002_0h.tif", "exp_GFP_A01_F003_0h.tif",
    ]


def test_the_reader_resolves_members_the_way_the_grouper_names_them(tmp_path):
    """Folders inside the zip are stripped and the first duplicate wins — the
    rule ``find_image_groups_in_zip`` applies — and a missing name says which
    archive it was looked for in."""
    zip_path = tmp_path / "B02.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("plate/sub/", b"")
        zf.writestr("plate/sub/a.tif", b"first")
        zf.writestr("other/a.tif", b"second")
    with ZipMemberReader(zip_path) as reader:
        assert reader.read("a.tif") == b"first"
        assert reader.open(tmp_path / "x" / "a.tif").read() == b"first"
        with pytest.raises(KeyError, match="B02.zip"):
            reader.read("missing.tif")


def test_reads_borrow_a_handle_so_threads_that_come_and_go_do_not_pile_handles_up_and_close_closes_them_all(
        tmp_path):
    """A handle per thread, kept for the well, piled up when a nanowell well made a thread pool per timepoint: every
    new thread opened another handle and re-read the archive's directory. A read now borrows a free handle."""
    zip_path = tmp_path / "C03.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.tif", b"payload")
    reader = ZipMemberReader(zip_path)
    for _ in range(5):                                   # five threads, one after another: one handle serves them all
        t = threading.Thread(target=lambda: reader.read("a.tif"))
        t.start()
        t.join()
    assert reader.open_handles == 1
    held = reader._borrow()                              # a read in flight: the next read needs a handle of its own
    got = []
    t = threading.Thread(target=lambda: got.append(reader.read("a.tif")))
    t.start()
    t.join()
    assert got == [b"payload"] and reader.open_handles == 2 and held[0] is not reader._free[-1][0]
    reader._give_back(held)
    handles = list(reader._handles)
    reader.close()
    assert all(h.fp is None for h in handles)
    with pytest.raises(ValueError, match="closed"):
        reader.read("a.tif")
