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
import sys
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


def _copy_mode_zipper(input_dir, _out, **_kwargs):
    """What ``run_zipper`` leaves behind in the default copy mode.

    A ``<WELL>/`` folder holding a copy of each image, and **every loose TIF
    still in place** — which is the condition the site below was measured in:
    with the sources gone there is nothing left to stat and the defect is
    invisible, so a fixture that moved them would have measured the happy path.
    """
    from util.well_token import canonical_well_label

    for tif in sorted(input_dir.glob("*.tif")):
        well = canonical_well_label(tif.stem.split("_")[2])
        if well is None:
            continue
        folder = input_dir / well
        folder.mkdir(exist_ok=True)
        (folder / tif.name).write_bytes(tif.read_bytes())


class _IsDirCalls:
    """``Path.is_dir()`` calls made *by the modules under test*, per entry.

    Neither the total ``os.stat`` count nor the total ``is_dir`` count can be the
    measure here. ``stage_local_zips`` reads and compresses every image, so it
    necessarily touches every file once, and ``zip_write`` skips directories as it
    walks a folder's members — one classification per file it is about to read
    and deflate anyway, which is noise beside the read. A slope over either total
    is therefore swamped by work that has to happen.

    What must not grow is the classification the *listing already answered*, in
    the modules this branch swept. Attributing each call to its immediate caller's
    file is what makes that claim measurable: it excludes the archive writer's own
    walk and this file's fixture, and would still catch a single re-introduced
    ``p.is_dir()`` anywhere in the staging path.
    """

    #: The files whose classification must not grow with the plate.
    WATCHED = ("remote_runner.py", "remote_scratch.py",
               "input_resolution_service.py", "input_layout.py")

    def __init__(self) -> None:
        self.real = Path.is_dir
        self.n = 0
        self.where: dict[str, int] = {}

    def __enter__(self):
        watched = self.WATCHED

        def counting(inner, *a, **k):
            frame = sys._getframe(1)
            while frame is not None:
                name = Path(frame.f_code.co_filename).name
                if name in watched:
                    key = f"{name}:{frame.f_lineno}"
                    self.where[key] = self.where.get(key, 0) + 1
                    self.n += 1
                    break
                if "/allwell/" in frame.f_code.co_filename:
                    break        # some other All-Well module; not ours to count
                frame = frame.f_back
            return self.real(inner, *a, **k)

        Path.is_dir = counting
        return self

    def __exit__(self, *exc):
        Path.is_dir = self.real
        return False


def test_staging_a_flat_plate_does_not_classify_every_loose_image(tmp_path, monkeypatch):
    """`stage_local_zips` walks the input folder twice more, and nothing counted it.

    ``folders_after_zipper`` was spelled identically to the ``_stage_mixed_mode``
    site three lines above it and was left asking ``p.is_dir()`` per entry. In
    copy mode — the default — every loose TIF is still sitting in the input
    folder when it runs, so that was one stat per image at the start of every
    remote run: the exact cost this branch exists to remove, inside a function no
    test looked at. The detector test above passes either way, because it never
    calls this function.
    """
    import services.input_resolution_service as irs
    from services.remote_runner import stage_local_zips

    monkeypatch.setattr(irs, "run_zipper", _copy_mode_zipper)

    small = _flat_plate(tmp_path / "small", 40)
    large = _flat_plate(tmp_path / "large", 160)
    kw = dict(filename_schema="experiment:channel:well:fov:timepoint",
              filename_sep="_")

    with _IsDirCalls() as a:
        first = stage_local_zips(small, **kw)
    with _IsDirCalls() as b:
        second = stage_local_zips(large, **kw)

    # The counter is live and did attribute to a watched file. A wrapper that
    # installed but attributed nothing — a renamed module, a changed frame shape —
    # would satisfy the slope below with no measurement behind it.
    assert a.where, "the counter attributed no call to any watched module"
    assert set(a.where) == set(b.where), (
        f"the two runs went through different sites: {a.where} versus {b.where}")

    # Staged something, so the counts are not two measurements of a no-op.
    for staged, n in ((first, 40), (second, 160)):
        assert staged.well_zips, f"nothing was staged for the {n}-image plate"
        assert all(p.is_file() for p in staged.well_zips)

    assert b.n - a.n <= 4, (
        f"staging classifies every loose image one entry at a time: 40 images "
        f"took {a.n} Path.is_dir calls, 160 took {b.n}. By line: "
        f"{a.where} versus {b.where}")


def test_a_directory_named_like_an_archive_is_not_a_well_archive(tmp_path):
    """The planner and the runner must answer this the same way.

    A *directory* called ``A01.zip`` is what an expander leaves behind when it
    unpacks an archive in place. ``has_well_zip`` on the Analyze side says it is
    not a well archive; ``_well_zips_in`` matched it with ``glob("*.zip")`` and
    said it was, and that generator is the remote side's whole answer — the mode
    detector, the staging set, the rollback set and the mixed-mode branch all
    read it. So the planner refused a layout the runner then staged as ZIP mode
    and skipped entirely.
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
