"""A plate is scanned in worker processes, and the answer is the same one.

``pipeline.czi_read`` is a parser written in Python, so a pool of threads over
it contends for the interpreter lock instead of overlapping — and one of the
threads it contends with is the one drawing the window. Measured on a 96-file
split export shaped like a real one (3,168 subblocks per file, four fields of
view, a plate document of its own), read through ``read_czi_set``:

======================  ========  ==============  ==================
                          wall     worst pause     window starved
======================  ========  ==============  ==================
eight threads             3.95 s      53–87 ms          1–10%
eight spawned processes   1.54 s      17–18 ms             0%
======================  ========  ==============  ==================

Two and a half times faster, and the window stops noticing the scan at all —
17 ms against a timer asked to fire every 10 ms.

Everything here **forces** the path under test through
``ALLWELL_SCAN_PROCESSES`` rather than relying on the file count, because a
fixture large enough to cross ``scan_pool.PROCESS_THRESHOLD`` on its own would
be a slow test that still only proved the threshold. The threshold is asserted
separately, as arithmetic.

These tests really do spawn interpreters. That is the point: the process path
is the one nothing else in the suite exercises, and a path nothing runs is a
path that works until the day it is needed (CLAUDE.md §7.7).
"""

from __future__ import annotations

import pytest

from pipeline import czi_read, czi_set, scan_pool
from tests._czi_fixture import plate_metadata_xml, plate_tiles, write_czi


@pytest.fixture(autouse=True)
def _cold_cache():
    czi_read.forget_plate_metadata()
    yield
    czi_read.forget_plate_metadata()


@pytest.fixture
def forced(monkeypatch):
    """Choose the path under test rather than waiting for a big enough plate."""
    def _choose(which: str) -> None:
        monkeypatch.setenv("ALLWELL_SCAN_PROCESSES",
                           {"processes": "1", "threads": "0"}[which])
    return _choose


@pytest.fixture
def plate(tmp_path):
    """A small split export: one well per file, two channels, two timepoints."""
    folder = tmp_path / "Exp_split"
    folder.mkdir()
    for i, well in enumerate(("C07", "A02", "H11", "B05")):
        write_czi(folder / f"Exp_{well}.czi",
                  plate_tiles(wells=1, fovs=2, timepoints=2, channels=2),
                  xml=plate_metadata_xml(scene_wells={0: well},
                                         channels=["DAPI", "GFP"]))
    return folder


# ── the answer does not depend on where it was computed ──────────────────────

def test_processes_and_threads_read_the_same_plate(plate, forced):
    """The whole safety claim in one assertion. A scan that came back faster
    and differently would be worse than the slow one it replaced."""
    forced("threads")
    by_threads = czi_set.read_czi_set(plate)
    czi_read.forget_plate_metadata()
    forced("processes")
    by_processes = czi_set.read_czi_set(plate)

    assert by_processes.problem == by_threads.problem == ""
    assert by_processes.wells_from_files() == by_threads.wells_from_files()
    assert by_processes.wells_from_files() == ["A02", "B05", "C07", "H11"]
    assert by_processes.n_positions == by_threads.n_positions
    assert by_processes.layout.channel_names == ["DAPI", "GFP"]
    assert by_processes.timepoint_tokens == by_threads.timepoint_tokens
    assert ([p.path.name for p in by_processes.positions]
            == [p.path.name for p in by_threads.positions])


def test_a_peek_across_processes_names_the_same_wells(plate, forced):
    forced("processes")

    peek = czi_set.peek_wells(plate)

    assert peek.wells == ["A02", "B05", "C07", "H11"]
    assert peek.complete
    assert peek.bytes_read > 0, "a peek that read nothing is not a peek"


def test_the_file_order_survives_the_process_boundary(plate, forced):
    """The order is the order ZEN wrote the export in and the order a grid
    mapping is laid over, so a result collected as it completed would make the
    well mapping depend on which child answered first."""
    forced("processes")

    names = [p.name for p in czi_set.read_czi_set(plate).files]

    assert names == ["Exp_A02.czi", "Exp_B05.czi", "Exp_C07.czi",
                     "Exp_H11.czi"]


def test_an_unreadable_member_costs_that_member_and_not_the_plate(tmp_path,
                                                                  forced):
    """The peek's refusal is caught inside the worker. Across a process
    boundary that matters more, not less: an exception raised in a child comes
    back at its own position and would otherwise take the whole scan down."""
    folder = tmp_path / "half"
    folder.mkdir()
    write_czi(folder / "Exp_A01.czi",
              plate_tiles(wells=1, fovs=1, timepoints=1, channels=1),
              xml=plate_metadata_xml(scene_wells={0: "A01"}, channels=["DAPI"]))
    (folder / "Exp_A02.czi").write_bytes(b"not a czi at all")
    forced("processes")

    peek = czi_set.peek_wells(folder)

    assert peek.wells == ["A01"]
    assert [p.name for p in peek.unnamed] == ["Exp_A02.czi"]


# ── what the parent keeps, which a worker cannot keep for it ─────────────────

def test_what_a_worker_parsed_is_remembered_by_the_parent(plate, forced):
    """A worker's caches are the worker's and die with it. Without the parsed
    document coming back with the answer, the full read that follows a peek
    would read and parse every file's document a second time — the cost the
    caching exists to remove."""
    forced("processes")
    assert czi_read.cached_plate_metadata(sorted(plate.glob("*.czi"))[0]) is None

    czi_set.peek_wells(plate)

    for path in sorted(plate.glob("*.czi")):
        assert czi_read.cached_plate_metadata(path) is not None, (
            f"{path.name}'s document was parsed in a child and forgotten")


def test_a_second_full_read_opens_no_file(plate, forced):
    """The layout cache still holds across the process boundary, because the
    parent stores what each worker sends back."""
    import builtins
    forced("processes")
    czi_set.read_czi_set(plate)

    opens = {"n": 0}
    real = builtins.open

    def _counting(file, *args, **kwargs):
        if str(file).endswith(".czi"):
            opens["n"] += 1
        return real(file, *args, **kwargs)

    builtins.open = _counting
    try:
        again = czi_set.read_czi_set(plate)
    finally:
        builtins.open = real

    assert opens["n"] == 0, f"{opens['n']} files re-opened"
    assert again.wells_from_files() == ["A02", "B05", "C07", "H11"]


# ── the constraints a spawned worker puts on the code ────────────────────────

def test_the_worker_entry_points_can_be_sent_to_a_child():
    """A spawned child is told a module and a qualified name, so a closure or
    a lambda cannot cross. Asserted here rather than left to a ``PicklingError``
    on the first real plate — these were closures until this change, and the
    thread path would never have noticed."""
    for fn in (czi_set._read_one_layout, czi_set._peek_one_file):
        assert scan_pool.is_picklable_by_name(fn), f"{fn!r} cannot be sent"


def test_a_closure_is_refused_by_the_same_check():
    """The check earns its keep only if it says no to something."""
    def _local(item):                      # pragma: no cover - never called
        return item

    assert not scan_pool.is_picklable_by_name(_local)
    assert not scan_pool.is_picklable_by_name(lambda item: item)


# ── when processes are worth starting ────────────────────────────────────────

@pytest.mark.parametrize("n_files,expected", [
    (1, False), (2, False), (23, False), (24, True), (96, True), (661, True)])
def test_the_threshold_is_where_it_says_it_is(n_files, expected, monkeypatch):
    monkeypatch.delenv("ALLWELL_SCAN_PROCESSES", raising=False)
    assert scan_pool.wants_processes(n_files) is expected


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("on", True),
    ("0", False), ("false", False), ("off", False)])
def test_the_environment_can_insist_either_way(value, expected, monkeypatch):
    """The escape hatch for a machine where the pool misbehaves — a
    locked-down host, a sandbox with no shared memory — without shipping a
    build to find out."""
    monkeypatch.setenv("ALLWELL_SCAN_PROCESSES", value)
    assert scan_pool.wants_processes(1) is expected
    assert scan_pool.wants_processes(10_000) is expected


def test_a_pool_that_cannot_start_falls_back_to_threads(plate, forced,
                                                        monkeypatch):
    """A scan's answer does not depend on how it was computed, so a machine
    that cannot give us a pool gets the slow path rather than an error."""
    import pipeline.scan_pool as pool_module

    def _no_pool(*_args, **_kwargs):
        raise OSError("this machine has no shared memory")

    monkeypatch.setattr(pool_module, "ProcessPoolExecutor", _no_pool)
    forced("processes")

    result = czi_set.read_czi_set(plate)

    assert result.problem == ""
    assert result.wells_from_files() == ["A02", "B05", "C07", "H11"]


# ── proof that the work left this interpreter ────────────────────────────────

def _explode(*_args, **_kwargs):          # pragma: no cover - the point is not to run
    raise AssertionError("this ran in the parent process")


def test_the_reading_really_happens_in_another_interpreter(plate, forced,
                                                           monkeypatch):
    """Every other test here would pass unchanged if the pool quietly fell
    back to threads, which is the way a process path stops being taken and
    nobody notices.

    So: break the per-file work **in this process**. A child imports
    ``czi_set`` fresh and never sees the patch, so a scan that still comes
    back with the plate is a scan that happened somewhere else.
    """
    forced("processes")
    monkeypatch.setattr(czi_set, "plate_metadata_for", _explode)

    peek = czi_set.peek_wells(plate)

    assert peek.wells == ["A02", "B05", "C07", "H11"]


def test_the_same_sabotage_stops_the_thread_path(plate, forced, monkeypatch):
    """The other half of the proof. If the patch above could not stop a scan
    that *did* run here, the test above would be measuring nothing at all."""
    forced("threads")
    monkeypatch.setattr(czi_set, "plate_metadata_for", _explode)

    with pytest.raises(AssertionError, match="ran in the parent"):
        czi_set.peek_wells(plate)
