"""One or more CZI files read as one plate (``pipeline.czi_set``).

ZEN saves a plate scan two ways — one file holding every scene, or one file per
scene — and nothing inside a split export's members says it is part of a plate.
The plate is the folder, which is what this module decides and what these
check, against real ZISRAW bytes rather than a mocked reader.
"""

from __future__ import annotations

import builtins
import contextlib
import io
import os
import threading
import time

import pytest

from tests._czi_fixture import (
    Tile,
    plate_metadata_xml,
    plate_tiles,
    write_czi,
)
from pipeline import czi_set
from pipeline.czi_read import CziReadError
from pipeline.czi_set import (
    discover_czi_files,
    peek_wells,
    read_czi_set,
    read_czi_set_safely,
    scan_workers,
    siblings_of,
)


def _scene_file(folder, name, well=None, *, channels=("DAPI", "GFP"),
                fovs=1, timepoints=1, scenes=1):
    folder.mkdir(parents=True, exist_ok=True)
    wells = {} if well is None else {i: well for i in range(scenes)}
    return write_czi(
        folder / name,
        plate_tiles(wells=scenes, fovs=fovs, timepoints=timepoints,
                    channels=len(channels)),
        xml=plate_metadata_xml(scene_wells=wells, channels=list(channels)),
    )


def _split_plate(tmp_path, wells=("C07", "A02", "H11"), **kwargs):
    folder = tmp_path / "Exp7_split"
    for well in wells:
        _scene_file(folder, f"Exp7_{well}.czi", well, **kwargs)
    return folder


# ── what a selection stands for ──────────────────────────────────────────────

def test_a_file_stands_for_itself_alone(tmp_path):
    """Picking one file and silently converting its neighbours is a surprise,
    so widening is the caller's decision and not this function's."""
    folder = _split_plate(tmp_path)
    picked = folder / "Exp7_A02.czi"
    assert discover_czi_files(picked) == [picked]


def test_a_folder_stands_for_the_czi_files_in_it_sorted(tmp_path):
    folder = _split_plate(tmp_path)
    assert [p.name for p in discover_czi_files(folder)] == [
        "Exp7_A02.czi", "Exp7_C07.czi", "Exp7_H11.czi"]


def test_a_nested_folder_is_not_swept_up(tmp_path):
    """Nothing recursive: a folder of folders is a different thing, and walking
    into one would silently convert a nested export as part of this plate."""
    folder = _split_plate(tmp_path, wells=("A01",))
    _scene_file(folder / "other", "Exp9_B02.czi", "B02")
    assert [p.name for p in discover_czi_files(folder)] == ["Exp7_A01.czi"]


def test_non_czi_files_beside_the_plate_are_ignored(tmp_path):
    folder = _split_plate(tmp_path, wells=("A01",))
    (folder / "notes.txt").write_text("hello")
    (folder / "Exp7.ome.tif").write_bytes(b"not a czi")
    assert [p.name for p in discover_czi_files(folder)] == ["Exp7_A01.czi"]


def test_siblings_of_a_member_are_the_whole_folder(tmp_path):
    folder = _split_plate(tmp_path)
    assert siblings_of(folder / "Exp7_A02.czi") == discover_czi_files(folder)


# ── a split-scene plate ──────────────────────────────────────────────────────

def test_a_split_scene_folder_reads_as_one_plate(tmp_path):
    folder = _split_plate(tmp_path, fovs=2, timepoints=2)
    czi_set = read_czi_set(folder)

    assert czi_set.problem == ""
    assert czi_set.n_files == 3
    assert czi_set.n_scenes == 3
    assert czi_set.n_positions == 6          # 3 scenes x 2 tiles
    assert czi_set.fovs_per_scene == 2
    assert czi_set.layout.size_t == 2
    assert czi_set.layout.channel_names == ["DAPI", "GFP"]
    # File-name order, which is the order ZEN writes a split export in — not
    # plate order, which would be A02, C07, H11 for these three.
    assert czi_set.wells_from_files() == ["A02", "C07", "H11"]


def test_each_position_carries_the_file_it_is_in(tmp_path):
    """The whole reason a position is not a bare ``(scene, tile)`` pair: a
    split plate has one file per well, and the exploder walks wells."""
    folder = _split_plate(tmp_path, wells=("A01", "B02"), fovs=2)
    czi_set = read_czi_set(folder)

    assert [(p.path.name, p.scene, p.tile) for p in czi_set.positions] == [
        ("Exp7_A01.czi", 0, 0), ("Exp7_A01.czi", 0, 1),
        ("Exp7_B02.czi", 0, 0), ("Exp7_B02.czi", 0, 1),
    ]


def test_a_single_file_is_a_set_of_one(tmp_path):
    """The same structure, so the exploder has no branch for the two shapes."""
    path = write_czi(
        tmp_path / "Whole.czi",
        plate_tiles(wells=2, fovs=1, timepoints=1, channels=1),
        xml=plate_metadata_xml(scene_wells={0: "A01", 1: "A02"},
                               channels=["DAPI"]),
    )
    czi_set = read_czi_set(path)

    assert czi_set.n_files == 1
    assert czi_set.n_scenes == 2
    assert czi_set.wells_from_files() == ["A01", "A02"]
    assert {p.path for p in czi_set.positions} == {path}


def test_an_explicit_file_list_overrides_what_the_path_stands_for(tmp_path):
    """What the page's tick-box does when it is unticked on a folder member."""
    folder = _split_plate(tmp_path)
    picked = folder / "Exp7_H11.czi"
    czi_set = read_czi_set(folder, files=[picked])

    assert czi_set.n_files == 1
    assert czi_set.wells_from_files() == ["H11"]


# ── where a split member's well comes from ───────────────────────────────────

def test_a_one_scene_file_may_be_named_by_its_filename(tmp_path):
    """ZEN's split export names each file after the scene it holds, and does
    not always keep the scene's own name inside it."""
    folder = tmp_path / "bare_split"
    for well in ("A01", "B02"):
        _scene_file(folder, f"Exp7_{well}.czi", None)
    assert read_czi_set(folder).wells_from_files() == ["A01", "B02"]


def test_the_scene_s_own_metadata_beats_the_filename(tmp_path):
    """The acquisition saying which well it scanned outranks a name a user or
    a copy step could have changed."""
    folder = tmp_path / "renamed"
    _scene_file(folder, "Exp7_A01.czi", "H12")
    assert read_czi_set(folder).wells_from_files() == ["H12"]


def test_a_whole_plate_file_is_not_named_by_its_filename(tmp_path):
    """The one-scene condition, and the reason for it. ``Exp_A1.czi`` holding
    ninety-six unnamed scenes is not one well of ninety-six fields, and reading
    it as one would produce a plate that is wrong everywhere at once."""
    path = write_czi(tmp_path / "Exp_A1.czi",
                     plate_tiles(wells=4, fovs=1, timepoints=1, channels=1))
    czi_set = read_czi_set(path)

    assert czi_set.n_scenes == 4
    assert czi_set.wells_from_files() == []


def test_a_partly_named_split_set_is_refused_rather_than_half_used(tmp_path):
    folder = tmp_path / "half"
    _scene_file(folder, "Exp7_A01.czi", "A01")
    _scene_file(folder, "scan2.czi", None)
    assert read_czi_set(folder).wells_from_files() == []


def test_two_files_claiming_one_well_are_that_well_s_two_fields(tmp_path):
    """Not a clash. ZEN writes one scene per imaged position and names it
    after the well it sits in, so two files naming A01 are the two positions
    imaged in A01 — ``scene_well_groups`` reads them as its fields of view.

    This asserted a refusal until a real 660-scene plate showed what the shape
    means: 660 scenes over ninety-six wells is seven fields each, and refusing
    it left the whole plate unconvertible for being ordinary.
    """
    folder = tmp_path / "two_fields"
    _scene_file(folder, "a.czi", "A01")
    _scene_file(folder, "b.czi", "A01")
    source = read_czi_set(folder)

    assert source.wells_from_files() == ["A01"]
    assert source.scene_well_groups() == [("A01", [0, 1])]


# ── what the files of a set must agree about ─────────────────────────────────

@pytest.mark.parametrize("kwargs,noun", [
    ({"channels": ("DAPI",)}, "number of channels"),
    ({"channels": ("DAPI", "mCherry")}, "channel names"),
    ({"timepoints": 3}, "number of timepoints"),
])
def test_files_that_disagree_about_the_filenames_are_refused(
        tmp_path, kwargs, noun):
    """They become one dataset, so a difference here means the same filename
    means different things in different wells — and the run reports success."""
    folder = tmp_path / "mixed"
    _scene_file(folder, "a_A01.czi", "A01")
    _scene_file(folder, "b_A02.czi", "A02", **kwargs)
    problem = read_czi_set(folder).problem

    assert noun in problem
    assert "a_A01.czi" in problem and "b_A02.czi" in problem


def test_files_may_disagree_about_z(tmp_path):
    """Z is projected away, so two files whose stacks are different depths
    produce the same images. ``ome_explode`` leaves Z out of its own ragged
    check for the same reason; refusing here would reject a sound plate."""
    folder = tmp_path / "depths"
    _scene_file(folder, "a_A01.czi", "A01")
    write_czi(folder / "b_A02.czi",
              plate_tiles(wells=1, fovs=1, timepoints=1, channels=2, z=4),
              xml=plate_metadata_xml(scene_wells={0: "A02"},
                                     channels=["DAPI", "GFP"]))
    czi_set = read_czi_set(folder)

    assert czi_set.problem == ""
    assert czi_set.wells_from_files() == ["A01", "A02"]


def test_a_member_short_of_a_plane_is_named_and_still_converted(tmp_path):
    """Not as a disagreement: "these two disagree about the channel names" is a
    confusing thing to say about a file that is short of an image, and it names
    the wrong remedy. Each file is checked on its own first."""
    folder = tmp_path / "broken"
    _scene_file(folder, "a_A01.czi", "A01")
    # Two scenes, two channels, with one channel of one scene absent — so the
    # file still *declares* two channels and is genuinely incomplete, rather
    # than being a sound one-channel file that merely differs from its sibling.
    tiles = plate_tiles(wells=2, fovs=1, timepoints=1, channels=2)
    del tiles[1]
    write_czi(folder / "b_A02.czi", tiles,
              xml=plate_metadata_xml(scene_wells={0: "A02", 1: "A03"},
                                     channels=["DAPI", "GFP"]))
    result = read_czi_set(folder)

    # Not refused. One file short of a plane used to take the whole plate
    # down with it, which on a 661-file export was six hundred and sixty
    # sound wells lost for the sake of one — and it was decided after the
    # read, so the wait bought nothing either.
    assert result.problem == ""
    assert "b_A02.czi" in result.shortfall_note
    assert "3 of the 4 image(s)" in result.shortfall_note
    # All three wells convert, the short one included.
    assert result.wells_from_files() == ["A01", "A02", "A03"]


def test_an_unreadable_member_raises_but_the_safe_form_reports(tmp_path):
    folder = tmp_path / "junk"
    _scene_file(folder, "a_A01.czi", "A01")
    (folder / "b.czi").write_bytes(b"II*\0" + b"\0" * 200)

    with pytest.raises(CziReadError, match="not a CZI file"):
        read_czi_set(folder)
    assert "not a CZI file" in read_czi_set_safely(folder).problem


def test_an_empty_folder_says_so(tmp_path):
    (tmp_path / "nothing").mkdir()
    assert "No .czi files" in read_czi_set(tmp_path / "nothing").problem


# ── the third shape: a multi-file CZI ────────────────────────────────────────

def test_a_multi_file_czi_is_refused_by_name(tmp_path):
    """A single logical CZI split into numbered parts that share one subblock
    directory. Its tiles' offsets are into a sibling file, so reading them
    through one handle lands at an arbitrary position in the wrong one. Refused
    with the two exports this converter *does* read."""
    tiles = plate_tiles(wells=1, fovs=1, timepoints=1, channels=2)
    tiles[1] = Tile(pixels=tiles[1].pixels, scene=0, tile=0, t=0, c=1, z=0,
                    file_part=1)
    path = write_czi(tmp_path / "huge.czi", tiles)
    problem = read_czi_set(path).problem

    assert "multi-file CZI" in problem
    assert "one file per scene" in problem


# ── peeking: the plate before the plate has been read ────────────────────────

class _CountingFile(io.FileIO):
    """A file object that records how many bytes were actually read from it."""

    total = 0

    def read(self, size=-1):
        data = super().read(size)
        _CountingFile.total += len(data)
        return data


@contextlib.contextmanager
def _counting_reads():
    """Count bytes read from every ``.czi`` opened inside the block."""
    real_open = builtins.open

    def _open(path, mode="r", *args, **kwargs):
        if "b" in mode and str(path).endswith(".czi"):
            return _CountingFile(path, "rb")
        return real_open(path, mode, *args, **kwargs)

    _CountingFile.total = 0
    builtins.open = _open
    try:
        yield lambda: _CountingFile.total
    finally:
        builtins.open = real_open


def _big_plate(tmp_path, scenes=96):
    """A plate whose subblock directory is worth not reading: 96 scenes x 4
    tiles x 3 channels x 5 Z is 5,760 directory entries."""
    wells = [f"{'ABCDEFGH'[i // 12]}{i % 12 + 1:02d}" for i in range(scenes)]
    return write_czi(
        tmp_path / "Plate.czi",
        plate_tiles(wells=scenes, fovs=4, timepoints=1, channels=3, z=5,
                    shape=(8, 8)),
        xml=plate_metadata_xml(scene_wells=dict(enumerate(wells)),
                               channels=["DAPI", "GFP", "Cy5"])), wells


def test_a_peek_reads_a_fraction_of_what_a_full_read_does(tmp_path):
    """The number the feature rests on.

    A peek reads the 112-byte file header and the metadata segment it points
    at. A full read additionally walks the subblock directory, which is one
    entry per stored tile and about a megabyte for a plate this size — and
    neither reads a pixel, so the gap is the same whether the images weigh
    three megabytes or forty gigabytes. Asserted as a ratio rather than an
    absolute, because the fixture's own sizes are not the point.
    """
    path, wells = _big_plate(tmp_path)

    with _counting_reads() as counted:
        peek = peek_wells(path)
        peek_bytes = counted()
    with _counting_reads() as counted:
        read_czi_set(path)
        full_bytes = counted()

    assert peek.wells == wells
    assert peek_bytes < full_bytes / 20, (
        f"the peek read {peek_bytes:,} bytes against the full read's "
        f"{full_bytes:,} — it is walking the subblock directory")
    assert peek.bytes_read == peek_bytes, (
        "PlatePeek.bytes_read is what the page reports; it has to be what was "
        "read and not, say, the offset the reads ended at")


def test_a_peek_and_a_full_read_name_the_same_wells(tmp_path):
    """The peek paints a plate the full read then confirms, so the two must
    not be able to disagree — they derive the wells through one function."""
    folder = _split_plate(tmp_path, wells=("C07", "A02", "H11"))
    assert peek_wells(folder).wells == read_czi_set(folder).wells_from_files()


def test_a_split_export_is_peeked_for_a_few_kilobytes(tmp_path):
    folder = _split_plate(tmp_path, wells=("C07", "A02", "H11"))
    with _counting_reads() as counted:
        peek = peek_wells(folder)
        peek_bytes = counted()
    with _counting_reads() as counted:
        read_czi_set(folder)
        full_bytes = counted()

    assert peek.wells == ["A02", "C07", "H11"]
    assert peek.complete is True
    assert peek_bytes < full_bytes


def test_a_file_named_only_by_its_filename_is_peeked_from_it(tmp_path):
    """A split member whose metadata lists channels but names no scene. The
    peek can see it holds one scene, so the filename may stand in — and it has
    to give the same answer the full read does, or the plate it paints is one
    the run contradicts."""
    folder = tmp_path / "unnamed_scenes"
    _scene_file(folder, "Exp7_A01.czi", None)
    _scene_file(folder, "Exp7_B02.czi", None)
    peek = peek_wells(folder)

    assert peek.wells == ["A01", "B02"]
    assert peek.complete is True
    assert peek.wells == read_czi_set(folder).wells_from_files()


def test_a_file_with_no_metadata_at_all_is_left_out_rather_than_guessed(
        tmp_path):
    """The filename may stand in for a scene name only on a file holding one
    scene, and with no metadata a peek cannot tell one scene from ninety-six.
    Painting it from the filename anyway would put a well on the plate that the
    full read could contradict — a provisional answer may be incomplete, and
    may not be wrong.
    """
    folder = tmp_path / "mixed"
    _scene_file(folder, "Exp7_A01.czi", "A01")
    write_czi(folder / "Exp7_B02.czi",
              plate_tiles(wells=1, fovs=1, timepoints=1, channels=2))
    peek = peek_wells(folder)

    assert peek.wells == ["A01"]
    assert [p.name for p in peek.unnamed] == ["Exp7_B02.czi"]
    assert peek.complete is False
    assert "not the whole plate" in peek.note()
    # And this particular set is refused outright once really read, because a
    # file carrying no metadata carries no channel names either: it reports
    # `c0, c1` against its sibling's `DAPI, GFP`. Worth pinning, because it is
    # why the case above — metadata without scene names — is the realistic one
    # and this is the damaged one.
    assert "channel names" in read_czi_set(folder).problem


def test_a_peek_never_raises_on_a_broken_member(tmp_path):
    """It is called to draw something quickly, not to decide anything; the
    full read is the authority on a file being unusable."""
    folder = tmp_path / "junk"
    _scene_file(folder, "a_A01.czi", "A01")
    (folder / "b.czi").write_bytes(b"II*\0" + b"\0" * 200)
    peek = peek_wells(folder)

    assert peek.wells == ["A01"]
    assert [p.name for p in peek.unnamed] == ["b.czi"]


def test_an_empty_folder_peek_says_so(tmp_path):
    (tmp_path / "nothing").mkdir()
    assert "No .czi files" in peek_wells(tmp_path / "nothing").problem


def test_discovering_a_folder_does_not_stat_every_entry(tmp_path):
    """``iterdir()`` plus ``is_file()`` is one ``stat`` per entry, which on
    Windows is a file handle opened and closed and against a network share is a
    round trip each — the case ``util.dir_scan`` exists for, and the case a
    96-file plate on a NAS is. ``scandir`` answers from the listing it already
    paid for."""
    folder = _split_plate(tmp_path, wells=("A01", "B02", "C03"))
    with _counting_stats(folder) as counted:
        found = discover_czi_files(folder)

    assert [p.name for p in found] == [
        "Exp7_A01.czi", "Exp7_B02.czi", "Exp7_C03.czi"]
    assert counted() == 0, (
        f"{counted()} stat(s) for a three-file folder — the listing is being "
        f"re-asked about every entry it already described")


@contextlib.contextmanager
def _counting_stats(folder):
    """Count ``os.stat`` calls on the ``.czi`` files inside *folder*."""
    calls: list[str] = []
    real_stat = os.stat

    def _watch(path, *args, **kwargs):
        if str(path).endswith(".czi"):
            calls.append(str(path))
        return real_stat(path, *args, **kwargs)

    os.stat = _watch
    try:
        yield lambda: len(calls)
    finally:
        os.stat = real_stat


# ── reading the files at once ────────────────────────────────────────────────

def test_a_multi_file_scan_reads_the_files_at_once(tmp_path, monkeypatch):
    """A scan is round trips, not cycles: eight files in flight cover eight
    latencies instead of paying them end to end.

    Asserted as a barrier every read must reach, rather than as a duration or
    as a count of distinct thread ids. A duration is a flake with a schedule.
    A thread-id count is a flake too, for a subtler reason: a
    ``ThreadPoolExecutor`` spawns a worker only when no existing one has gone
    idle, so on a loaded machine several quick header reads can legitimately
    all land on the first thread. Measured rather than assumed — four reads
    used one thread in 238 of 400 runs under CPU load, and the rate is the
    same on the code this pool predates, so it was never a statement about the
    implementation at all.

    The barrier states the property the test is named for: every read is in
    flight at the same moment. Reads run one after another cannot all reach
    it, so the barrier breaks and the failure is the assertion below rather
    than a hang.
    """
    folder = _split_plate(tmp_path, wells=tuple(
        f"A{i + 1:02d}" for i in range(8)))
    #: Eight files against ``scan_workers(8)`` == 8, so a pool doing its job
    #: can satisfy this and a serial read cannot. The timeout is long enough
    #: that a starved container still passes and short enough that a genuinely
    #: serial read reports rather than wedging the lane.
    gate = threading.Barrier(8, timeout=30)
    real = czi_set.plate_metadata_for

    def _record(path, **kwargs):
        gate.wait()
        return real(path, **kwargs)

    monkeypatch.setattr(czi_set, "plate_metadata_for", _record)
    try:
        peek = peek_wells(folder)
    except threading.BrokenBarrierError:
        pytest.fail("the eight files were read one after another: they never "
                    "reached the barrier together")

    assert len(peek.wells) == 8


def test_a_full_read_of_several_files_is_concurrent_too(tmp_path, monkeypatch):
    """The second stage walks the same file list and pays the same latencies,
    so it uses the same pool. Stated with the same barrier and for the same
    reason as the peek above."""
    folder = _split_plate(tmp_path, wells=("A01", "A02", "A03", "A04"))
    gate = threading.Barrier(4, timeout=30)
    real = czi_set.read_czi_layout

    def _record(path, **kwargs):
        gate.wait()
        return real(path, **kwargs)

    monkeypatch.setattr(czi_set, "read_czi_layout", _record)
    try:
        read_czi_set(folder)
    except threading.BrokenBarrierError:
        pytest.fail("the four files were read one after another: they never "
                    "reached the barrier together")


@pytest.mark.parametrize("stage", ["peek", "full"])
def test_the_order_is_the_file_order_however_the_reads_finish(
        tmp_path, monkeypatch, stage):
    """The order is not a nicety. It is the order ZEN wrote a split export in,
    the order a grid mapping lays its well labels over, and the order
    ``positions`` is walked in — so collecting results as they complete would
    make a plate's well mapping depend on which file the filer answered first.

    The first file is held back so it finishes *last*, which is the case that
    tells ``map``'s by-argument ordering apart from as-completed.
    """
    folder = _split_plate(tmp_path, wells=("C07", "A02", "H11"))
    first = sorted(folder.glob("*.czi"))[0]           # Exp7_A02.czi
    name = {"peek": "plate_metadata_for", "full": "read_czi_layout"}[stage]
    real = getattr(czi_set, name)

    def _slow_first(path, **kwargs):
        if path == first:
            time.sleep(0.05)
        return real(path, **kwargs)

    monkeypatch.setattr(czi_set, name, _slow_first)

    if stage == "peek":
        assert peek_wells(folder).wells == ["A02", "C07", "H11"]
    else:
        assert read_czi_set(folder).wells_from_files() == ["A02", "C07", "H11"]


@pytest.mark.parametrize("stage", ["peek", "full"])
def test_a_single_file_is_read_on_the_calling_thread(tmp_path, monkeypatch,
                                                     stage):
    """No pool for one file: two context switches to save nothing, and every
    single-file conversion in the app takes this path."""
    path = write_czi(tmp_path / "Solo.czi",
                     plate_tiles(wells=1, fovs=1, timepoints=1, channels=1),
                     xml=plate_metadata_xml(scene_wells={0: "A01"},
                                            channels=["DAPI"]))
    name = {"peek": "plate_metadata_for", "full": "read_czi_layout"}[stage]
    real = getattr(czi_set, name)
    seen: list[int] = []

    def _record(p, **kwargs):
        seen.append(threading.get_ident())
        return real(p, **kwargs)

    monkeypatch.setattr(czi_set, name, _record)
    (peek_wells if stage == "peek" else read_czi_set)(path)

    assert seen == [threading.get_ident()]


def test_one_unreadable_file_costs_only_that_file(tmp_path):
    """The per-file catch lives inside the worker, so a broken member does not
    take the rest of the plate's peek down with it."""
    folder = tmp_path / "one_bad"
    for well in ("A01", "A02", "A03"):
        _scene_file(folder, f"Exp7_{well}.czi", well)
    (folder / "Exp7_B01.czi").write_bytes(b"II*\0" + b"\0" * 200)
    peek = peek_wells(folder)

    assert peek.wells == ["A01", "A02", "A03"]
    assert [p.name for p in peek.unnamed] == ["Exp7_B01.czi"]


@pytest.mark.parametrize("n_files,expected", [
    (0, 1), (1, 1), (3, 3), (8, 8), (96, 8), (1000, 8),
])
def test_the_worker_count_is_capped_by_the_share_not_the_cpu(n_files, expected):
    """Capped at the file count below the ceiling and at the ceiling above it —
    and never at the CPU count, because no CPU work happens. Sizing a
    latency-bound read to the cores would leave a four-core laptop reading a
    96-file plate four at a time for no reason."""
    assert scan_workers(n_files) == expected


# ── the order a grid is laid over ────────────────────────────────────────────

@pytest.mark.parametrize("names,expected", [
    # ZEN does not zero-pad its scene numbers, which is the whole case.
    ([f"scan_s{i}.czi" for i in (1, 2, 10, 11)],
     ["scan_s1", "scan_s2", "scan_s10", "scan_s11"]),
    # Zero-padded names must not change, or every existing plate moves.
    (["Exp_A01.czi", "Exp_A02.czi", "Exp_A10.czi"],
     ["Exp_A01", "Exp_A02", "Exp_A10"]),
    # Case folded on the text runs, so a stray capital does not sort apart.
    (["Exp_a2.czi", "Exp_A10.czi"], ["Exp_a2", "Exp_A10"]),
])
def test_files_sort_by_their_numbers_not_by_their_text(tmp_path, names,
                                                       expected):
    """Lexicographic order puts ``scan_s10`` between ``scan_s1`` and
    ``scan_s2``. For a split export the file order **is** the acquisition order
    a grid mapping lays its well labels over, so on a 3x4 grid that put scene 2
    in B01 and scene 10 in A02 — a wrong plate, reported as a success."""
    folder = tmp_path / "unpadded"
    folder.mkdir()
    for name in names:
        write_czi(folder / name,
                  plate_tiles(wells=1, fovs=1, timepoints=1, channels=1))

    assert [p.stem for p in discover_czi_files(folder)] == expected


def test_a_grid_lays_its_labels_over_the_numeric_order(tmp_path):
    """The consequence, asserted where a user would see it."""
    from pipeline.czi_explode import resolve_well_map

    folder = tmp_path / "unpadded"
    folder.mkdir()
    for index in range(1, 13):
        write_czi(folder / f"scan_s{index}.czi",
                  plate_tiles(wells=1, fovs=1, timepoints=1, channels=1))
    well_map = resolve_well_map(read_czi_set(folder), well_source="grid",
                                grid_rows=3, grid_cols=4)
    placed = {p.path.stem: well
              for well, group in zip(well_map.wells, well_map.positions)
              for p in group}

    assert placed["scan_s1"] == "A01"
    assert placed["scan_s2"] == "A02"
    assert placed["scan_s10"] == "C02"        # 3x4 serpentine, position 10


# ── tiles per scene across a set ─────────────────────────────────────────────

def test_files_holding_different_tile_counts_are_refused(tmp_path):
    """A set is chopped into wells by a *count*: the grid arm divides the flat
    position list evenly, so files of 1, 2 and 1 tiles put the middle file's
    two tiles either side of a well boundary — one well ends up holding tiles
    from two different files, and the run reports success.

    ``CziLayout._ragged_scenes`` catches this inside one file and is blind to
    it across a set, because a split member has a single scene and there is
    nothing for it to compare.
    """
    folder = tmp_path / "ragged"
    folder.mkdir()
    for name, fovs in (("a.czi", 1), ("b.czi", 2), ("c.czi", 1)):
        write_czi(folder / name,
                  plate_tiles(wells=1, fovs=fovs, timepoints=1, channels=1))
    problem = read_czi_set(folder).problem

    assert "number of tiles per scene" in problem
    assert "a.czi" in problem and "b.czi" in problem


def test_a_member_whose_own_scenes_disagree_is_named(tmp_path):
    """A member that is ragged *inside itself* is refused as itself.

    Not by the set comparison — ``validation_report`` gets there first, on the
    line above it. Worth pinning anyway, because the reading that says the set
    check needs a companion "is this knowable?" guard is wrong and produced an
    unreachable branch here once already.
    """
    folder = tmp_path / "inner"
    folder.mkdir()
    _scene_file(folder, "ok.czi", "A01")
    ragged = [t for t in plate_tiles(wells=2, fovs=2, timepoints=1, channels=2)
              if not (t.scene == 1 and t.tile == 1)]
    write_czi(folder / "uneven.czi", ragged,
              xml=plate_metadata_xml(scene_wells={0: "A02", 1: "A03"},
                                     channels=["DAPI", "GFP"]))

    assert "uneven.czi" in read_czi_set(folder).problem


# ── the filename rule belongs to a split export ──────────────────────────────

def test_a_lone_file_is_not_named_by_a_well_shaped_fragment_of_its_name(
        tmp_path):
    """``20240612_Plate_D1_60x.czi`` — one scene, nine tiles, no scene metadata
    — became well D01. ``D1`` there is far likelier to be a condition than a
    plate position, and a single file gives the rule nothing to corroborate it
    with. The split export it was written for is unaffected."""
    lone = write_czi(tmp_path / "20240612_Plate_D1_60x.czi",
                     plate_tiles(wells=1, fovs=9, timepoints=1, channels=2),
                     xml=plate_metadata_xml(channels=["DAPI", "GFP"]))

    assert read_czi_set(lone).wells_from_files() == []
    assert peek_wells(lone).wells == []


def test_a_split_member_is_still_named_by_its_filename(tmp_path):
    """The control. Removing the rule outright would be the other defect."""
    folder = tmp_path / "split"
    folder.mkdir()
    for well in ("A01", "B02"):
        _scene_file(folder, f"Exp7_{well}.czi", None)

    assert read_czi_set(folder).wells_from_files() == ["A01", "B02"]
    assert peek_wells(folder).wells == ["A01", "B02"]


# ── the master file a split-scene export writes beside its scenes ────────────

def _master_plate(tmp_path, wells=("A01", "A02")):
    """A Split Scenes export as ZEN writes one: a master file describing the
    plate and holding no image tiles, plus one file per scene."""
    folder = tmp_path / "Exp_split"
    folder.mkdir(parents=True, exist_ok=True)
    write_czi(folder / "Exp.czi", [],
              xml=plate_metadata_xml(
                  scene_wells={i: w for i, w in enumerate(wells)},
                  channels=["DAPI", "GFP"]))
    for well in wells:
        write_czi(folder / f"Exp_{well}.czi",
                  plate_tiles(wells=1, fovs=1, timepoints=2, channels=2),
                  xml=plate_metadata_xml(scene_wells={0: well},
                                         channels=["DAPI", "GFP"]))
    return folder


def test_a_master_file_does_not_refuse_the_folder(tmp_path):
    """ZEN's Split Scenes export writes a master file beside the per-scene
    ones. It describes the plate and holds no image tiles, which is what it is
    for — not a fault in the export, and not something that should be able to
    take the whole folder down.

    It refused twice over, and the second refusal is the one that would have
    survived a careless fix: the master sorts first (``Exp.czi`` before
    ``Exp_A01.czi``), so it was also the file every other member was compared
    against, and dropping it *after* that comparison still failed on
    "0 tiles against 1".
    """
    folder = _master_plate(tmp_path)

    result = read_czi_set(folder)

    assert result.problem == "", result.problem
    assert result.wells_from_files() == ["A01", "A02"]
    assert [p.name for p in result.files] == ["Exp_A01.czi", "Exp_A02.czi"]
    assert [p.name for p in result.index_files] == ["Exp.czi"]


def test_the_dropped_master_is_named_rather_than_silently_skipped(tmp_path):
    """A converter that passes over a file the user pointed it at, without a
    word, is indistinguishable from one that failed to notice it."""
    folder = _master_plate(tmp_path)

    note = read_czi_set(folder).shortfall_note

    assert "Exp.czi" in note
    assert "no image tiles" in note
    assert "not converted" in note


def test_the_timing_survives_the_master_being_dropped(tmp_path):
    """The acquisition stamps are asked of whichever file sorts first, and
    that is the master. Dropping it without re-asking left the plate with no
    timing at all — its timepoints named by position, silently, which is a
    dataset with no time axis in Review."""
    folder = _master_plate(tmp_path)

    result = read_czi_set(folder)

    assert len(result.timepoint_tokens) == 2
    assert result.layouts[0].time_scan is not None, (
        "the surviving first file was never asked for its stamps")


def test_a_single_file_holding_no_images_is_still_refused(tmp_path):
    """The drop is for a member of a *set*. One file, picked on its own and
    holding nothing, is the user having picked the wrong file — and saying so
    is the whole value of the check."""
    path = write_czi(tmp_path / "Exp.czi", [],
                     xml=plate_metadata_xml(scene_wells={0: "A01"},
                                            channels=["DAPI"]))

    result = read_czi_set(path)

    assert "holds no image tiles" in result.problem


def test_a_folder_where_every_file_is_empty_is_refused(tmp_path):
    """Dropping them all would leave an empty set reported as a fine one."""
    folder = tmp_path / "all_empty"
    folder.mkdir()
    for name in ("Exp.czi", "Exp_two.czi"):
        write_czi(folder / name,
                  [], xml=plate_metadata_xml(scene_wells={0: "A01"},
                                             channels=["DAPI"]))

    result = read_czi_set(folder)

    assert "holds no image tiles" in result.problem
    assert result.index_files == []
