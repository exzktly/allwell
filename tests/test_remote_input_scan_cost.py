"""Starting a remote run must not stat every image in a FLAT plate.

The same defect as the Analyze pane's browse-time freeze, one pane over and one
step later. ``_detect_local_input_mode`` runs at the start of every remote run,
*before* the input mode is known, so on a FLAT plate — a folder of loose images
— it sees the whole plate; and ``_unconsumed_loose_tifs``' inner
``_basenames_inside_well_folders`` runs unconditionally right after it. Both
asked ``Path.is_dir()`` of every entry, which on Windows opens a file handle per
image, over the wire, for a plate of several thousand.

Counted rather than timed, and asserted as a slope rather than an absolute:
what must be true is that the cost does not grow with the number of images.
"""

from __future__ import annotations

import os
from pathlib import Path


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


def test_detecting_the_input_mode_does_not_stat_every_loose_image(tmp_path):
    from services.remote_runner import LocalInputMode, _detect_local_input_mode

    small = _flat_plate(tmp_path / "small", 40)
    large = _flat_plate(tmp_path / "large", 160)

    # The counter is live before anything is claimed from it.
    probe = _Stats()
    with probe:
        for path in sorted(small.iterdir()):
            path.is_file()
    assert probe.n >= 40

    with _Stats() as a:
        assert _detect_local_input_mode(small) is LocalInputMode.FLAT
    with _Stats() as b:
        assert _detect_local_input_mode(large) is LocalInputMode.FLAT

    assert b.n - a.n <= 4, (
        f"detecting the input mode costs a metadata call per image: 40 images "
        f"took {a.n} os.stat calls, 160 took {b.n}")


def test_the_mode_is_still_detected_for_every_layout(tmp_path):
    """The counting test above passes for a function that answers FLAT always."""
    import zipfile

    from services.remote_runner import LocalInputMode, _detect_local_input_mode

    zipped = tmp_path / "zipped"
    zipped.mkdir()
    for well in ("A01", "A02"):
        with zipfile.ZipFile(zipped / f"{well}.zip", "w") as zf:
            zf.writestr(f"exp_GFP_{well}_f1_t0.tif", b"II*\0")
    assert _detect_local_input_mode(zipped) is LocalInputMode.ZIP

    foldered = tmp_path / "foldered"
    foldered.mkdir()
    for well in ("A01", "A02"):
        (foldered / well).mkdir()
        (foldered / well / f"exp_GFP_{well}_f1_t0.tif").write_bytes(b"II*\0")
    assert _detect_local_input_mode(foldered) is LocalInputMode.FOLDER

    mixed = tmp_path / "mixed"
    mixed.mkdir()
    with zipfile.ZipFile(mixed / "A01.zip", "w") as zf:
        zf.writestr("exp_GFP_A01_f1_t0.tif", b"II*\0")
    (mixed / "A02").mkdir()
    (mixed / "A02" / "exp_GFP_A02_f1_t0.tif").write_bytes(b"II*\0")
    assert _detect_local_input_mode(mixed) is LocalInputMode.MIXED
