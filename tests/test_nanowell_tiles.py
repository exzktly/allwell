"""A nanowell well's phase tiles are read from its archive once, and a tile read from memory is the tile read anew.

``run_well`` reads every timepoint's tiles to locate it and, because every timepoint is located before any is
measured, again to measure it: about 8 GB a well of 17 timepoints off the cluster's shared filesystem, which the app's
heartbeat also lands on. ``pipeline.nanowell_tiles.WellTiles`` keeps the tiles between the two reads as far as the
worker's memory allows, and copies the archive to the node's own disk when it cannot keep them all and the archive is
on a network filesystem.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pytest
from tifffile import imwrite

from pipeline import nanowell_task as nt
from pipeline import nanowell_tiles as tiles


def _archive(tmp_path: Path, n: int = 3) -> tuple[Path, list[str]]:
    archive = tmp_path / "A01.zip"
    names = []
    with zipfile.ZipFile(archive, "w") as zf:
        for k in range(n):
            pixels = (np.arange(64 * 48, dtype=np.uint16).reshape(64, 48) * (k + 3)) % 4093
            path = tmp_path / f"t{k}.tif"
            imwrite(str(path), pixels)
            names.append(f"exp_PHASE_A01_F{k}_0h.tif")
            zf.write(path, f"inner/{names[-1]}")
    return archive, [f"/scratch/_tmp_extract_A01/{n}" for n in names]


def _counted(well: tiles.WellTiles) -> list[str]:
    reads = []
    real = well.reader.read

    def read(name):
        reads.append(Path(name).name)
        return real(name)
    well.reader.read = read
    return reads


def test_a_tile_from_memory_is_the_tile_read_anew_and_a_fresh_copy_each_time(tmp_path):
    archive, members = _archive(tmp_path)
    well = tiles.WellTiles(archive, "A01", members, budget=10 ** 9, network=False)
    reads = _counted(well)
    first = well.tile(("0h", "F0"), members[0], tiles.read_native)
    again = well.tile(("0h", "F0"), members[0], tiles.read_native)
    direct = nt._read_tif(well.reader.open(members[0]))
    assert first.dtype == np.float32 and np.array_equal(first, direct) and np.array_equal(again, direct)
    assert reads == [Path(members[0]).name, Path(members[0]).name]  # once for the cache, once for `direct` above
    first[:] = -1                                                    # a caller writing into its tile
    assert np.array_equal(well.tile(("0h", "F0"), members[0], tiles.read_native), direct)
    assert well.kept == 1 and well.reread == 0
    well.close()


def test_a_tile_without_room_is_read_again_and_counted(tmp_path):
    archive, members = _archive(tmp_path)
    well = tiles.WellTiles(archive, "A01", members, budget=64 * 48 * 2, network=False)   # room for one tile
    reads = _counted(well)
    for _ in range(2):
        for k, member in enumerate(members):
            well.tile(("0h", f"F{k}"), member, tiles.read_native)
    assert reads.count(Path(members[0]).name) == 1 and reads.count(Path(members[1]).name) == 2
    assert well.kept == 1 and well.refused == 4 and well.reread == 2
    assert "2 without room" not in well.summary() and "4 without room, read again (2 re-read)" in well.summary()
    well.close()


def test_a_released_timepoint_s_tiles_are_forgotten(tmp_path):
    archive, members = _archive(tmp_path)
    well = tiles.WellTiles(archive, "A01", members, budget=10 ** 9, network=False)
    well.tile(("0h", "F0"), members[0], tiles.read_native)
    well.tile(("24h", "F1"), members[1], tiles.read_native)
    well.release("0h")
    reads = _counted(well)
    well.tile(("0h", "F0"), members[0], tiles.read_native)
    well.tile(("24h", "F1"), members[1], tiles.read_native)
    assert reads == [Path(members[0]).name]
    well.close()


def test_an_archive_on_a_network_filesystem_that_memory_cannot_hold_is_read_from_a_local_copy(tmp_path):
    archive, members = _archive(tmp_path)
    local = tmp_path / "node"
    local.mkdir()
    well = tiles.WellTiles(archive, "A01", members, budget=0, network=True, local_dir=local, workers=2)
    assert well.copy is not None and well.reader.zip_path == well.copy
    # Open and already unlinked: a worker killed now leaves nothing on the node's disk.
    assert list(local.glob("allwell_*")) == [] and str(well.copy).startswith("/proc/self/fd/")
    for k in range(2):                                   # each read opens the unlinked copy afresh
        tile = well.tile(("0h", f"F{k}"), members[k], tiles.read_native)
        assert np.array_equal(tile, nt._read_tif(zipfile.ZipFile(archive).open(f"inner/{Path(members[k]).name}")))
    assert "copied to" in well.summary()
    well.close()
    assert list(local.glob("allwell_*")) == [] and archive.exists()


def test_reading_one_timepoint_of_a_long_well_copies_nothing(tmp_path):
    """An incremental run that adds one timepoint estimates that timepoint's tiles: copying the whole archive to read
    them would read every timepoint off the network to spare one re-read."""
    archive, members = _archive(tmp_path, n=6)
    local = tmp_path / "node"
    local.mkdir()
    well = tiles.WellTiles(archive, "A01", members[:1], budget=0, network=True, local_dir=local)
    assert well.copy is None and well.reader.zip_path == archive
    well.close()


def test_memory_backed_temporary_folders_are_not_the_node_s_disk(tmp_path, monkeypatch):
    shm = tmp_path / "shm"
    disk = tmp_path / "disk"
    shm.mkdir()
    disk.mkdir()
    monkeypatch.setattr(tiles, "is_network", lambda path: False)
    monkeypatch.setattr(tiles, "filesystem_type", lambda path, mounts="/proc/mounts":
                        "tmpfs" if Path(path) == shm else "ext4")
    assert tiles.local_temp_dir({"SLURM_TMPDIR": str(shm), "TMPDIR": str(disk)}) == disk


def test_workers_on_one_node_copy_one_at_a_time(tmp_path):
    import threading
    import time

    state = {"in": 0, "most": 0}
    lock = threading.Lock()

    def copy():
        with tiles._one_copy_at_a_time(tmp_path):
            with lock:
                state["in"] += 1
                state["most"] = max(state["most"], state["in"])
            time.sleep(0.05)
            with lock:
                state["in"] -= 1

    threads = [threading.Thread(target=copy) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state["most"] == 1


def test_no_copy_when_the_node_s_disk_has_no_room_or_the_archive_is_local_or_memory_holds_it(tmp_path, monkeypatch):
    archive, members = _archive(tmp_path)
    local = tmp_path / "node"
    local.mkdir()
    monkeypatch.setattr(tiles.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    full = tiles.WellTiles(archive, "A01", members, budget=0, network=True, local_dir=local)
    assert full.copy is None and full.reader.zip_path == archive and "too little to copy it to" in full.summary()
    full.close()
    monkeypatch.undo()
    for well in (tiles.WellTiles(archive, "A01", members, budget=0, network=False, local_dir=local),
                 tiles.WellTiles(archive, "A01", members, budget=10 ** 9, network=True, local_dir=local)):
        assert well.copy is None and well.reader.zip_path == archive
        well.close()
    assert list(local.iterdir()) == []


def test_the_estimate_is_the_phase_members_uncompressed_size(tmp_path):
    archive, members = _archive(tmp_path)
    with zipfile.ZipFile(archive) as zf:
        sizes = [i.file_size for i in zf.infolist()]
    assert tiles._phase_bytes(archive, members[:2]) == sum(sizes[:2])


def test_the_filesystem_is_read_from_the_longest_mount_point_containing_the_path(tmp_path):
    mounts = tmp_path / "mounts"
    mounts.write_text("/dev/vda / ext4 rw 0 0\n"
                      "server:/export /shared nfs4 rw 0 0\n"
                      "server:/export/deep /shared/local-ish xfs rw 0 0\n"
                      "tmpfs /scratchmem tmpfs rw 0 0\n"
                      "server:/b /mnt/my\\040plates nfs rw 0 0\n")
    assert tiles.filesystem_type("/shared/plate/in/A01.zip", str(mounts)) == "nfs4"
    assert tiles.filesystem_type("/shared/local-ish/x", str(mounts)) == "xfs"
    assert tiles.filesystem_type("/sharedness/x", str(mounts)) == "ext4"     # a prefix that is not a parent
    assert tiles.filesystem_type("/scratchmem/x", str(mounts)) == "tmpfs"
    assert tiles.filesystem_type("/mnt/my plates/in/A01.zip", str(mounts)) == "nfs"   # an escaped space
    assert tiles.filesystem_type("/x", str(tmp_path / "missing")) is None


def test_the_node_s_own_disk_is_slurm_s_first_and_never_a_network_one(tmp_path, monkeypatch):
    slurm, tmp = tmp_path / "slurm", tmp_path / "tmp"
    slurm.mkdir()
    tmp.mkdir()
    monkeypatch.setattr(tiles, "is_network", lambda path: False)
    assert tiles.local_temp_dir({"SLURM_TMPDIR": str(slurm), "TMPDIR": str(tmp)}) == slurm
    assert tiles.local_temp_dir({"SLURM_TMPDIR": str(tmp_path / "gone"), "TMPDIR": str(tmp)}) == tmp
    monkeypatch.setattr(tiles, "is_network", lambda path: Path(path) == slurm)
    assert tiles.local_temp_dir({"SLURM_TMPDIR": str(slurm), "TMPDIR": str(tmp)}) == tmp


@pytest.mark.parametrize("budget", [0, 10 ** 9])
def test_the_summary_says_how_the_tiles_were_read(tmp_path, budget):
    archive, members = _archive(tmp_path)
    well = tiles.WellTiles(archive, "A01", members, budget=budget, network=False)
    well.tile(("0h", "F0"), members[0], tiles.read_native)
    text = well.summary()
    assert ("1 tile(s) kept in memory" in text) is (budget > 0)
    well.close()
