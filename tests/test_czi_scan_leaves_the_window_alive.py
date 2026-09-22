"""A plate scan reads wide and parses narrow.

``czi_set.scan_workers`` runs a plate's files across up to eight threads, and
the reason written beside it is entirely about *waiting*: eight requests in
flight cover eight network round trips, measured at 797 ms down to 104 ms for
a 96-file peek with 8 ms of per-file latency.

But ``pipeline.czi_read`` is a parser written in Python, so the work between
the reads is bytecode and holds the interpreter lock. Eight threads of that do
not go eight times faster; they contend, and the thread they contend with
includes the one drawing the window. Measured on 96 local files, where there
is no latency to cover and the cost is therefore all parsing: one worker took
3.48 s and never kept the event loop waiting, while eight took 4.22 s and
starved it for 65.6% of the run, with single pauses of 266 ms. Slower *and*
unusable, from threads added to make it faster.

So the reads stay as wide as the pool and the parsing is serialised behind
``_PARSE_LOCK``. Both halves are asserted here, because each one is what
stops the opposite mistake: with no lock the parses overlap and the window
starves, and with the lock taken around the reading the round trips go back
end to end and the pool stops doing anything at all.

Asserted as concurrency counts and as a ratio between two measured times,
never as an absolute duration — a wall-clock threshold on a build machine is
a flake with a schedule.
"""

from __future__ import annotations

import builtins
import threading
import time
import xml.etree.ElementTree as ET

import pytest

from pipeline import czi_read, czi_set
from tests._czi_fixture import plate_metadata_xml, plate_tiles, write_czi


@pytest.fixture(autouse=True)
def _cold_cache():
    czi_read.forget_plate_metadata()
    yield
    czi_read.forget_plate_metadata()


@pytest.fixture
def plate(tmp_path):
    """Sixteen files, each naming one well and carrying enough metadata that
    parsing one is long enough for another to start during it.

    **Eight timepoints**, which is not decoration. A set reads the acquisition
    times from one of its files, and that is a parse of a small document per
    timepoint — a stream of them on one thread, while the other threads parse
    the large plate documents. With a single timepoint that stream is one
    call, and whether it lands inside another file's parse is a race: the
    guard below then passes or fails by luck, which is worse than not having
    it. Measured — with one timepoint, removing the lock from the per-plane
    parse left the guard green.
    """
    folder = tmp_path / "split"
    folder.mkdir()
    filler = "".join(f'<Tile Index="{j}"><X>{j}</X></Tile>' for j in range(400))
    for i in range(16):
        well = f"{chr(ord('A') + i // 12)}{i % 12 + 1:02d}"
        write_czi(
            folder / f"Exp_{well}.czi",
            plate_tiles(wells=1, fovs=1, timepoints=8, channels=1),
            xml=plate_metadata_xml(scene_wells={0: well}, channels=["DAPI"])
            .replace("</ImageDocument>", f"<Filler>{filler}</Filler></ImageDocument>"))
    return folder


class _Concurrency:
    """How many threads were inside a thing at once, and the most there were."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.now = 0
        self.most = 0

    def __enter__(self):
        with self.lock:
            self.now += 1
            self.most = max(self.most, self.now)
        return self

    def __exit__(self, *_exc):
        with self.lock:
            self.now -= 1
        return False


def test_no_two_files_are_parsed_at_the_same_time(plate, monkeypatch):
    """The parsing is what holds the interpreter lock, so doing it eight ways
    at once buys nothing and costs the window its event loop."""
    parsing = _Concurrency()
    real = ET.fromstring

    def _counted(text, *args, **kwargs):
        with parsing:
            time.sleep(0.002)      # long enough for an overlap to be observed
            return real(text, *args, **kwargs)

    monkeypatch.setattr(czi_read.ET, "fromstring", _counted)

    result = czi_set.read_czi_set(plate)

    # The read is the read, locked or not (§7.8: "nothing overlapped" is
    # satisfied by a scan that did nothing).
    assert len(result.layouts) == 16
    assert not result.problem, result.problem
    assert parsing.most == 1, (
        f"{parsing.most} files were parsed at once")


def test_the_files_are_still_read_at_the_same_time(plate, monkeypatch):
    """The other half, and the one a careless lock breaks: a lock taken
    around the *reading* would put the round trips back end to end and undo
    the pool entirely, while leaving the test above perfectly green."""
    reading = _Concurrency()
    real = builtins.open

    def _counted(file, *args, **kwargs):
        if str(file).endswith(".czi"):
            with reading:
                time.sleep(0.01)   # a round trip, standing in for a share
                return real(file, *args, **kwargs)
        return real(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _counted)

    result = czi_set.read_czi_set(plate)

    assert len(result.layouts) == 16
    assert reading.most > 1, (
        "the files were opened one after another — the pool is doing nothing")


def test_a_peek_over_a_slow_share_still_gains_from_the_pool(plate, monkeypatch):
    """The measurement ``scan_workers`` was sized by, kept as a guard.

    A ratio rather than a duration: the same work, the same machine, the same
    injected latency, once on one thread and once on the pool. What is
    asserted is that the pool is *for* something, which a lock held a little
    too widely would quietly end.
    """
    real = builtins.open

    def _slow(file, *args, **kwargs):
        if str(file).endswith(".czi"):
            time.sleep(0.008)
        return real(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _slow)

    monkeypatch.setattr(czi_set, "scan_workers", lambda n: 1)
    czi_read.forget_plate_metadata()
    start = time.perf_counter()
    serial = czi_set.peek_wells(plate)
    serial_s = time.perf_counter() - start

    monkeypatch.setattr(czi_set, "scan_workers", lambda n: min(8, n))
    czi_read.forget_plate_metadata()
    start = time.perf_counter()
    pooled = czi_set.peek_wells(plate)
    pooled_s = time.perf_counter() - start

    assert serial.wells == pooled.wells, "the two runs must agree on the plate"
    assert len(pooled.wells) == 16
    # Sixteen files at 8 ms is 128 ms serial against about a quarter of that
    # on eight threads. Asserted at 2x rather than at the ~5x measured, so
    # that a loaded machine does not fail it while a lock around the reads —
    # which takes the whole gain, not a slice of it — still does.
    assert serial_s > pooled_s * 2, (
        f"one thread took {serial_s * 1000:.0f} ms and eight took "
        f"{pooled_s * 1000:.0f} ms — the pool has stopped overlapping the "
        f"round trips")
