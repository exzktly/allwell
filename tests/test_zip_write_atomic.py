"""The per-well archives All-Well publishes are readable and never member-less.

Both properties are what a user actually touches: the ``in/<WELL>.zip`` and
``out/<WELL>_out.zip`` files are opened by hand in Finder / Explorer, and a
truncated archive (a run hard-killed mid-write) or a zero-member archive (a
well with nothing to pack) is what macOS Archive Utility reports as *"Unable to
expand …. It is in an unsupported format."*

Stdlib-only on purpose — ``util.zip_write`` is Qt-free and numpy-free, so these
run in the fast lane. The call-site tests that need ``pipeline.io`` /
``process_microscopy`` import them lazily inside the test.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from util.zip_write import ZipWriteError, atomic_zip, staging_path, zip_members


def _tif(path: Path, payload: bytes = b"II*\x00fake-tif") -> Path:
    path.write_bytes(payload)
    return path


def _staging_leftovers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.name.startswith("__aw_tmp_"))


# ── atomic_zip ──────────────────────────────────────────────────────────────


def test_publishes_a_readable_archive(tmp_path):
    src = _tif(tmp_path / "A01_DAPI.tif")
    target = tmp_path / "A01.zip"

    with atomic_zip(target) as zf:
        zf.write(src, arcname=src.name)

    assert zip_members(target) == ["A01_DAPI.tif"]
    assert _staging_leftovers(tmp_path) == []


def test_member_less_archive_is_never_published(tmp_path):
    target = tmp_path / "C02.zip"

    with pytest.raises(ZipWriteError):
        with atomic_zip(target):
            pass

    # No file at all — better than a 22-byte archive macOS refuses to expand.
    assert not target.exists()
    assert _staging_leftovers(tmp_path) == []


def test_failed_build_leaves_the_previous_archive_intact(tmp_path):
    target = tmp_path / "C02.zip"
    src = _tif(tmp_path / "C02_DAPI.tif")
    with atomic_zip(target) as zf:
        zf.write(src, arcname=src.name)
    before = target.read_bytes()

    with pytest.raises(RuntimeError, match="worker died"):
        with atomic_zip(target) as zf:
            zf.writestr("C02_GFP.tif", b"partial")
            raise RuntimeError("worker died")

    assert target.read_bytes() == before
    assert zip_members(target) == ["C02_DAPI.tif"]
    assert _staging_leftovers(tmp_path) == []


def test_a_member_less_rewrite_keeps_the_existing_archive(tmp_path):
    target = tmp_path / "C02.zip"
    with atomic_zip(target) as zf:
        zf.writestr("C02_DAPI.tif", b"x")

    with pytest.raises(ZipWriteError):
        with atomic_zip(target):
            pass  # e.g. a filter pass that dropped every member

    assert zip_members(target) == ["C02_DAPI.tif"]


def test_staged_archive_is_verified_before_it_is_published(tmp_path, monkeypatch):
    """A staging file that doesn't read back must not take the target's name."""
    import util.zip_write as zw

    target = tmp_path / "C02.zip"

    real_zip_members = zw.zip_members

    def _truncate_then_report(path):
        # Stand in for a share that dropped mid-write: the staged file is
        # short, so re-reading its central directory fails.
        Path(path).write_bytes(b"PK\x03\x04truncated")
        return real_zip_members(path)

    monkeypatch.setattr(zw, "zip_members", _truncate_then_report)

    with pytest.raises(ZipWriteError, match="did not read back"):
        with zw.atomic_zip(target) as zf:
            zf.writestr("C02_DAPI.tif", b"x")

    assert not target.exists()
    assert _staging_leftovers(tmp_path) == []


def test_staging_name_is_the_prefix_the_pipeline_skips(tmp_path):
    # The image scanners drop files starting with ``__aw_tmp_``; a staging
    # archive left by a killed run must fall in that set, and must not be
    # mistaken for a well (``C02.zip.pid…`` is not a well name either).
    assert staging_path(tmp_path / "C02.zip").name.startswith("__aw_tmp_C02.zip.pid")


# ── call sites ──────────────────────────────────────────────────────────────


def test_consolidate_mixed_keeps_a_tif_less_well_folder(tmp_path):
    """A leftover well folder with no TIFs must not become an empty zip.

    Writing one would publish an ``in/<WELL>.zip`` that Finder refuses to
    expand, and ``shutil.rmtree`` would take the folder's other content with it.
    """
    from services.input_resolution_service import _consolidate_mixed_to_zips

    # Mixed input: one well already zipped, one bare folder with TIFs, one bare
    # folder holding no images at all.
    with zipfile.ZipFile(tmp_path / "A01.zip", "w") as zf:
        zf.writestr("A01_DAPI.tif", b"x")
    (tmp_path / "B02").mkdir()
    _tif(tmp_path / "B02" / "B02_DAPI.tif")
    (tmp_path / "C02").mkdir()
    (tmp_path / "C02" / "notes.txt").write_text("no images here")

    logs: list[str] = []
    assert _consolidate_mixed_to_zips(tmp_path, log_fn=logs.append) is True

    assert zip_members(tmp_path / "B02.zip") == ["B02_DAPI.tif"]
    assert not (tmp_path / "B02").exists()
    assert not (tmp_path / "C02.zip").exists()
    assert (tmp_path / "C02" / "notes.txt").read_text() == "no images here"
    assert any("no TIFs" in line for line in logs)


def test_consolidate_mixed_reports_nothing_zipped(tmp_path):
    from services.input_resolution_service import _consolidate_mixed_to_zips

    with zipfile.ZipFile(tmp_path / "A01.zip", "w") as zf:
        zf.writestr("A01_DAPI.tif", b"x")
    (tmp_path / "C02").mkdir()

    assert _consolidate_mixed_to_zips(tmp_path, log_fn=None) is False


def test_append_loose_publishes_a_readable_zip(tmp_path):
    from services.input_resolution_service import append_loose_images_to_zips

    with zipfile.ZipFile(tmp_path / "A01.zip", "w") as zf:
        zf.writestr("exp_A01_DAPI_f1_t1.tif", b"x")
    _tif(tmp_path / "exp_A01_GFP_f1_t1.tif")

    appended, wells = append_loose_images_to_zips(
        tmp_path, filename_schema="experiment:well:channel:fov:timepoint",
        filename_sep="_", log_fn=None,
    )

    assert (appended, wells) == (1, {"A01"})
    assert zip_members(tmp_path / "A01.zip") == [
        "exp_A01_DAPI_f1_t1.tif", "exp_A01_GFP_f1_t1.tif",
    ]
    assert _staging_leftovers(tmp_path) == []


def test_merge_images_into_zip_writes_nothing_when_there_are_no_images(tmp_path):
    """A well that produced no output images leaves no archive behind."""
    pytest.importorskip("numpy")
    from pipeline.io import merge_images_into_zip

    empty_dir = tmp_path / "images"
    empty_dir.mkdir()
    out_zip = tmp_path / "C02_out.zip"

    assert merge_images_into_zip(out_zip, empty_dir) == 0
    assert not out_zip.exists()


def test_compress_images_to_zip_publishes_a_readable_archive(tmp_path):
    pytest.importorskip("numpy")
    from process_microscopy import compress_images_to_zip

    image_dir = tmp_path / "images"
    image_dir.mkdir()
    _tif(image_dir / "C02_DAPI_labels.tif")
    out_zip = tmp_path / "C02_out.zip"

    assert compress_images_to_zip(image_dir, out_zip) == 1
    assert zip_members(out_zip) == ["C02_DAPI_labels.tif"]
