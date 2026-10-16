"""The Concatenate gap preview reads the two datasets once per pair, not once per keystroke (tracker M21).

Measured on two raw 60-well plates (the user's inner 60 wells, 50 members each): reading the pair took about
75 ms, all of it inferring the filename schema from every archive, and the page asked twice per keystroke."""

from __future__ import annotations

import os
import zipfile

from datasets.concatenate import concatenate_compute as cc


def _raw(root, tp, wells=("B02", "B03")):
    (root / "in").mkdir(parents=True)
    for well in wells:
        with zipfile.ZipFile(root / "in" / f"{well}.zip", "w") as zf:
            for tile in range(4):
                for channel in ("PHASE", "GFP"):
                    zf.writestr(f"exp_{channel}_{well}_r{tile // 2}c{tile % 2}_{tp}.tif", b"x")
    return root


def test_keystrokes_reuse_the_reading_and_a_changed_dataset_is_read_again(tmp_path, monkeypatch):
    one, two = _raw(tmp_path / "one", "0h"), _raw(tmp_path / "two", "24h")
    reads = []
    real = cc.open_pair
    monkeypatch.setattr(cc, "open_pair", lambda a, b: reads.append(1) or real(a, b))
    monkeypatch.setattr(cc, "_preview_pairs", {})
    previews = [cc.gap_preview(one, two, text) for text in ("", "2", "24", "24h")]
    assert len(reads) == 1
    # Dataset 2's first timepoint lands 24 h after dataset 1's last (0h).
    assert previews[-1].labels == ("24h",) and not previews[-1].error
    with zipfile.ZipFile(two / "in" / "B04.zip", "w") as zf:      # dataset 2 gains a well
        zf.writestr("exp_PHASE_B04_r0c0_24h.tif", b"x")
    stamp = (two / "in").stat().st_mtime_ns + 1_000_000           # a coarse clock still moves
    os.utime(two / "in", ns=(stamp, stamp))
    cc.gap_preview(one, two, "24h")
    assert len(reads) == 2


def test_a_refusal_is_remembered_as_the_same_message(tmp_path, monkeypatch):
    analysed = tmp_path / "an"
    (analysed / "out").mkdir(parents=True)
    (analysed / "out" / "pipeline_info.json").write_text("{}")
    raw = _raw(tmp_path / "raw", "0h")
    monkeypatch.setattr(cc, "_preview_pairs", {})
    reads = []
    real = cc.open_pair
    monkeypatch.setattr(cc, "open_pair", lambda a, b: reads.append(1) or real(a, b))
    first = cc.gap_preview(raw, analysed, "")
    assert first.error.startswith("Dataset 1 is a raw (not yet analysed) dataset")
    assert cc.gap_preview(raw, analysed, "5h").error == first.error
    assert len(reads) == 1


def test_an_archive_overwritten_in_place_or_a_well_folder_gaining_images_is_read_again(tmp_path, monkeypatch):
    """Neither moves the in/ folder's own time: writing into an archive changes the archive, and an image added
    to a well folder changes that folder. Each input container is in the key."""
    one, two = _raw(tmp_path / "one", "0h"), _raw(tmp_path / "two", "24h")
    monkeypatch.setattr(cc, "_preview_pairs", {})
    reads = []
    real = cc.open_pair
    monkeypatch.setattr(cc, "open_pair", lambda a, b: reads.append(1) or real(a, b))
    cc.gap_preview(one, two, "24h")
    before = (two / "in").stat().st_mtime_ns
    with zipfile.ZipFile(two / "in" / "B02.zip", "a") as zf:           # the archive gains a timepoint in place
        zf.writestr("exp_PHASE_B02_r0c0_48h.tif", b"x")
    os.utime(two / "in", ns=(before, before))                          # and the folder's own time has not moved
    assert cc.gap_preview(one, two, "24h").labels == ("24h", "48h")
    assert len(reads) == 2
    folder = two / "in" / "B05"                                        # a well kept as a folder of images
    folder.mkdir()
    (folder / "exp_PHASE_B05_r0c0_24h.tif").write_bytes(b"x")
    cc.gap_preview(one, two, "24h")
    before = (two / "in").stat().st_mtime_ns
    (folder / "exp_PHASE_B05_r0c0_72h.tif").write_bytes(b"x")
    os.utime(two / "in", ns=(before, before))
    cc.gap_preview(one, two, "24h")
    assert len(reads) == 4
