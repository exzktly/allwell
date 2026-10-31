"""Starting a remote run must not stat every image in a plate it refuses.

The same defect as the Analyze pane's browse-time freeze, one pane over and one
step later: staging looks at the input folder before anything else, and on a
plate of loose images — which it refuses — asking ``Path.is_dir()`` or
``os.stat`` of every entry opens a file handle per image on Windows, over the
wire, for a plate of several thousand.

Counted rather than timed, and asserted as a slope rather than an absolute:
what must be true is that the cost does not grow with the number of images.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _flat_plate(root: Path, count: int) -> Path:
    root.mkdir(parents=True)
    wells = [f"{row}{col:02d}" for row in "ABCDEFGH" for col in range(1, 13)]
    written = 0
    for well in wells:
        for fov in range(1, 9):
            if written >= count:
                return root
            (root / f"exp_GFP_{well}_f{fov}_t0.tif").write_bytes(b"II*\0")
            written += 1
    raise AssertionError(f"the name generator cannot reach {count} files")


class _Stats:
    def __init__(self) -> None:
        self.real = os.stat
        self.n = 0

    def __enter__(self):
        os.stat = self._counting
        return self

    def __exit__(self, *exc):
        os.stat = self.real
        return False

    def _counting(self, *args, **kwargs):
        self.n += 1
        return self.real(*args, **kwargs)


def test_refusing_a_flat_plate_does_not_stat_every_loose_image(tmp_path):
    from services.remote_runner import stage_local_zips

    small = _flat_plate(tmp_path / "small", 40)
    large = _flat_plate(tmp_path / "large", 400)
    kw = dict(filename_schema="experiment:channel:well:fov:timepoint", filename_sep="_")
    counts = []
    for plate in (small, large):
        with _Stats() as stats, pytest.raises(RuntimeError, match="loose image"):
            stage_local_zips(plate, **kw)
        counts.append(stats.n)
    assert counts[1] - counts[0] <= 4, (
        f"refusing a flat plate stats every image: 40 images took {counts[0]} "
        f"os.stat calls, 400 took {counts[1]}")


def test_a_directory_named_like_an_archive_is_not_a_well_archive(tmp_path):
    """The planner and the runner must answer this the same way.

    A *directory* called ``A01.zip`` is what an expander leaves behind when it
    unpacks an archive in place. ``has_well_zip`` on the Analyze side says it is
    not a well archive; ``_well_zips_in`` matched it with ``glob("*.zip")`` and
    said it was, and that generator is the remote side's whole answer to which
    archives a run stages. So the planner refused a layout the runner then
    staged and skipped entirely.
    """
    from services.input_resolution_service import has_well_zip
    from services.remote_scratch import _well_zips_in
    from pipeline.input_layout import find_well_zips

    root = tmp_path / "in"
    root.mkdir()
    (root / "A01.zip").mkdir()
    (root / "A01.zip" / "exp_GFP_A01_f1_t0.tif").write_bytes(b"II*\0")

    assert has_well_zip(root) is False
    assert list(_well_zips_in(root)) == []
    assert find_well_zips(root) == []

    # A real archive beside it is still found, by all three.
    import zipfile
    with zipfile.ZipFile(root / "A02.zip", "w") as zf:
        zf.writestr("exp_GFP_A02_f1_t0.tif", b"II*\0")
    assert has_well_zip(root) is True
    assert [p.name for p in _well_zips_in(root)] == ["A02.zip"]
    assert [w for w, _p in find_well_zips(root)] == ["A02"]
