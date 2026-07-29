"""``folder_well`` must survive a source file that vanished mid-run.

Loose-TIF ("zipper") mode discovers every TIF in the search dir with one
``os.walk``, then copies each into a per-well subfolder. On a network mount, or
when an acquisition writes and then cleans up partial files, a path collected by
the walk can be gone by the time the copy runs. Before this guard a single such
file raised a raw ``FileNotFoundError`` out of ``shutil.copy2``, crashed the
zipper subprocess with a non-zero exit, and ``run_zipper`` turned that into a
``RuntimeError`` that failed grouping for the entire plate.

``folder_well`` now skips a vanished source with a warning and keeps going, so
the other wells still group.
"""

import os

import WellPlateZipper as wpz


def test_places_all_present_files(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    files = []
    for name in ("A01_f1_z1_c1.tif", "A01_f2_z1_c1.tif"):
        p = src / name
        p.write_bytes(b"tif")
        files.append(str(p))
    out = tmp_path / "out"
    out.mkdir()

    placed = wpz.folder_well("A01", files, str(out))

    assert placed == 2
    dst = out / "A01"
    assert {f.name for f in dst.iterdir()} == {"A01_f1_z1_c1.tif", "A01_f2_z1_c1.tif"}


def test_vanished_source_is_skipped_not_fatal(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    present = src / "A01_f1_z1_c1.tif"
    present.write_bytes(b"tif")
    missing = src / "A01_f2_z1_c1.tif"      # never created — simulates a vanish

    out = tmp_path / "out"
    out.mkdir()

    # Must not raise even though one path does not exist.
    placed = wpz.folder_well("A01", [str(present), str(missing)], str(out))

    assert placed == 1
    dst = out / "A01"
    assert {f.name for f in dst.iterdir()} == {"A01_f1_z1_c1.tif"}

    err = capsys.readouterr().err
    assert "no longer exists" in err
    assert str(missing) in err


def test_empty_file_list_returns_zero(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    assert wpz.folder_well("A01", [], str(out)) == 0
    # No empty well folder created for a well with nothing to place.
    assert not (out / "A01").exists()


def test_move_mode_also_tolerates_a_vanished_source(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    present = src / "B02_f1_z1_c1.tif"
    present.write_bytes(b"tif")
    missing = str(src / "B02_gone.tif")

    out = tmp_path / "out"
    out.mkdir()

    placed = wpz.folder_well("B02", [str(present), missing], str(out), move=True)

    assert placed == 1
    assert not present.exists()                      # moved out of src
    assert (out / "B02" / "B02_f1_z1_c1.tif").exists()
    assert not os.path.exists(missing)
