"""The loose-TIF grouping (zipper) phase must not crash on a broken symlink.

Big microscopy plates are often presented to the pipeline as a folder of
symlinks pointing at the raw TIFs on another (often network) volume. When such a
link's target is unmounted, moved, or deleted, the link itself is still a
directory entry — ``ls`` shows it and ``os.walk`` lists it among ``files`` — but
opening it fails.

The rest of the pipeline never trips on this: ``organize_loose_tifs_into_well_folders``
and ``find_image_groups`` discover images through ``Path.is_file()`` / an
existence check, both of which follow the link and treat a broken one as absent.
The zipper's ``group_by_well`` used a bare ``os.walk`` + extension check, so it
collected the broken link, and ``folder_well``'s ``shutil.copy2`` (which follows
symlinks) then opened the missing target and raised a bare ``FileNotFoundError``
that crashed the zipper subprocess and failed grouping for the whole plate.

``group_by_well`` now applies the same ``os.path.isfile`` rule at discovery, so a
broken link is skipped (loudly) and the remaining wells still group.
"""

import os

import WellPlateZipper as wpz

_SCHEMA = ["well", "field", "z", "channel"]
_SEP = "_"


def _touch(p):
    with open(p, "w") as fh:
        fh.write("tif")


def test_broken_symlink_is_skipped_not_collected(tmp_path, capsys):
    search = tmp_path / "in"
    search.mkdir()
    real = tmp_path / "real"
    real.mkdir()

    # One ordinary file, and one symlink whose target we then delete.
    _touch(search / "A01_1_1_DAPI.tif")
    target = real / "A01_1_1_GFP.tif"
    _touch(target)
    link = search / "A01_1_1_GFP.tif"
    os.symlink(target, link)
    target.unlink()                      # link is now dangling

    assert os.path.lexists(link)         # the "file" is still present on disk
    assert not os.path.isfile(link)      # but it does not resolve to a file

    grouped = wpz.group_by_well(str(search), False, _SCHEMA, _SEP)

    # The dangling link is dropped; the real file is kept.
    assert grouped == {"A01": [str(search / "A01_1_1_DAPI.tif")]}

    err = capsys.readouterr().err
    assert "broken symlink" in err
    assert str(link) in err


def test_broken_symlink_does_not_crash_the_zipper_run(tmp_path):
    """End to end: group_by_well -> folder_well no longer raises FileNotFoundError."""
    search = tmp_path / "in"
    search.mkdir()
    real = tmp_path / "real"
    real.mkdir()

    _touch(search / "A01_1_1_DAPI.tif")
    target = real / "A01_1_1_GFP.tif"
    _touch(target)
    os.symlink(target, search / "A01_1_1_GFP.tif")
    target.unlink()

    grouped = wpz.group_by_well(str(search), False, _SCHEMA, _SEP)
    # This is what main() does next; before the fix it raised FileNotFoundError.
    for well, files in grouped.items():
        wpz.folder_well(well, files, str(search))

    assert (search / "A01" / "A01_1_1_DAPI.tif").exists()


def test_valid_symlink_is_still_grouped(tmp_path):
    """A symlink whose target IS present must be treated as a normal image."""
    search = tmp_path / "in"
    search.mkdir()
    real = tmp_path / "real"
    real.mkdir()

    target = real / "A02_1_1_DAPI.tif"
    _touch(target)
    os.symlink(target, search / "A02_1_1_DAPI.tif")

    grouped = wpz.group_by_well(str(search), False, _SCHEMA, _SEP)
    assert grouped == {"A02": [str(search / "A02_1_1_DAPI.tif")]}

    # And it copies through fine (follows the link to the real target).
    wpz.folder_well("A02", grouped["A02"], str(search))
    assert (search / "A02" / "A02_1_1_DAPI.tif").read_text() == "tif"


def test_main_repairs_stdio_before_emitting_progress(tmp_path, monkeypatch):
    """In a frozen windowed build sys.stdout/sys.stderr are None in this
    subprocess; main() must rebind them (via util.frozen_io.ensure_std_streams)
    before the first print, or every progress line and warning is silently
    dropped and the Analyze grouping phase looks frozen."""
    import util.frozen_io as frozen_io

    calls = []
    monkeypatch.setattr(frozen_io, "ensure_std_streams", lambda: calls.append(True))

    search = tmp_path / "in"
    search.mkdir()
    _touch(search / "A01_1_1_DAPI.tif")
    monkeypatch.setattr(
        wpz.sys, "argv",
        ["WellPlateZipper.py", "--search-dir", str(search),
         "--output-dir", str(search),
         "--filename_schema", "well:field:z:channel", "--filename_sep", "_"],
    )

    wpz.main()

    assert calls, "main() did not call ensure_std_streams()"
    assert (search / "A01" / "A01_1_1_DAPI.tif").exists()


def test_ordinary_files_group_unchanged(tmp_path):
    search = tmp_path / "in"
    search.mkdir()
    for name in ("A01_1_1_DAPI.tif", "A01_2_1_DAPI.tif", "B02_1_1_DAPI.tif"):
        _touch(search / name)

    grouped = wpz.group_by_well(str(search), False, _SCHEMA, _SEP)
    assert set(grouped) == {"A01", "B02"}
    assert len(grouped["A01"]) == 2
    assert len(grouped["B02"]) == 1
