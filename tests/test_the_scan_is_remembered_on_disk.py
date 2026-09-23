"""A plate's parsed headers survive the process, and are never served stale.

Reading a CZI's header walks its subblock directory entry by entry and parses
its plate document with ``ElementTree`` — a megabyte of directory and tens of
megabytes of XML per file, no pixels. A 660-file split export on a network
share is the better part of an hour, and the app paid it again every time the
folder was opened, for files that had not moved.

``pipeline.scan_cache`` writes what ``czi_read`` already remembers in memory to
a file in the user's cache directory, keyed on each file's size and
modification time, and loads it back before the next scan. So the interesting
assertions are not "a file was written" — they are:

* a warm scan opens **no** file and produces a set **equal** to the cold one;
* every key that makes an entry stale, mutated alone, declines it; and
* a cache that cannot be trusted is a slow scan rather than a wrong answer.

The second is the one this whole area is about. A reuse decision keyed on less
than everything that invalidates it returns stale numbers and reports success,
which is CLAUDE.md §7's cardinal failure and §7.7 #3's named defect class.
``tests/test_reuse_is_keyed_completely.py`` carries the contract; this file is
where each key is watched declining.
"""

from __future__ import annotations

import builtins
import dataclasses
import json

import pytest

from pipeline import czi_read, scan_cache
from pipeline.czi_set import peek_wells, read_czi_set
from tests._czi_fixture import plate_metadata_xml, plate_tiles, write_czi


@pytest.fixture(autouse=True)
def _cold(tmp_path_factory, monkeypatch):
    """A cache directory of this test's own, and no memory carried into it."""
    monkeypatch.setenv("ALLWELL_SCAN_CACHE",
                       str(tmp_path_factory.mktemp("scans")))
    czi_read.forget_plate_metadata()
    yield
    czi_read.forget_plate_metadata()


@pytest.fixture
def plate(tmp_path):
    """Four files of one split export, each naming its own well."""
    folder = tmp_path / "Exp7_split"
    folder.mkdir()
    for index, well in enumerate(("A01", "A02", "B01", "B02")):
        write_czi(folder / f"Exp7_{well}.czi",
                  plate_tiles(wells=1, fovs=2, timepoints=3, channels=2),
                  xml=plate_metadata_xml(scene_wells={0: well},
                                         channels=["DAPI", "GFP"]))
    return folder


class _Opens:
    """How many ``.czi`` files were opened while this was installed."""

    def __init__(self, monkeypatch):
        self.count = 0
        real = builtins.open

        def counted(file, *args, **kwargs):
            if str(file).endswith(".czi"):
                self.count += 1
            return real(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", counted)


def _second_scan(plate, monkeypatch, read=read_czi_set):
    """Scan twice with the in-memory caches dropped between, counting the
    reads the second one does. Dropping them is what makes this a test of the
    *disk* cache rather than of the memory one that always worked."""
    first = read(plate)
    czi_read.forget_plate_metadata()
    opens = _Opens(monkeypatch)
    return first, read(plate), opens


# ── the point of the whole thing ────────────────────────────────────────────

def test_a_second_scan_of_a_folder_opens_no_file(plate, monkeypatch):
    _first, _again, opens = _second_scan(plate, monkeypatch)

    assert opens.count == 0, (
        f"{opens.count} file(s) were still opened — the scan was not served "
        f"from the cache")


def test_the_warm_scan_is_the_same_plate_as_the_cold_one(plate, monkeypatch):
    """The assertion that matters. "It was fast" is satisfied by a cache that
    serves a wrong answer quickly (§7.8), so this compares the sets."""
    first, again, _opens = _second_scan(plate, monkeypatch)

    assert again.problem == first.problem == ""
    assert again.scene_wells == first.scene_wells == ["A01", "A02", "B01", "B02"]
    assert again.wells_from_files() == first.wells_from_files()
    assert again.positions == first.positions
    assert again.timepoint_tokens == first.timepoint_tokens
    assert again.fovs_per_scene == first.fovs_per_scene == 2
    assert again.describe() == first.describe()
    assert [dataclasses.asdict(lay) for lay in again.layouts] == \
        [dataclasses.asdict(lay) for lay in first.layouts]


def test_the_acquisition_times_survive_too(plate, monkeypatch):
    """``time_scan`` is read for the first file only and is the one field a
    layout carries conditionally, so it is the one most easily lost."""
    first, again, _opens = _second_scan(plate, monkeypatch)

    assert first.layouts[0].time_scan is not None
    assert again.layouts[0].time_scan == first.layouts[0].time_scan


def test_a_peek_is_remembered_as_well(plate, monkeypatch):
    first, again, opens = _second_scan(plate, monkeypatch, read=peek_wells)

    assert again.wells == first.wells == ["A01", "A02", "B01", "B02"]
    assert opens.count == 0


def test_a_peek_warms_the_full_read(plate, monkeypatch):
    """The two passes share one parsed document, and a session that peeks and
    then reads should pay for the document once across both processes."""
    peek_wells(plate)
    czi_read.forget_plate_metadata()
    opens = _Opens(monkeypatch)

    read_czi_set(plate)

    # The directory still has to be walked — a peek never reads it — but the
    # plate document does not have to be parsed again.
    assert opens.count <= len(list(plate.glob("*.czi")))


# ── every key that makes an entry stale ─────────────────────────────────────

def test_a_file_that_changed_length_is_read_again(plate, monkeypatch):
    """**The modification time is put back**, so that only the length differs.

    Writing a file moves both stamps at once, so a test that just rewrites it
    is passed by a cache comparing either one — measured: with the size dropped
    from the comparison the whole file stayed green. Restoring the mtime is
    what makes this test about the size (§7.8 rule 2: build the fixture so the
    right and the wrong answers differ).
    """
    import os

    read_czi_set(plate)
    czi_read.forget_plate_metadata()
    victim = plate / "Exp7_A02.czi"
    was = victim.stat().st_mtime_ns
    victim.write_bytes(victim.read_bytes() + b"\0" * 8)
    os.utime(victim, ns=(was, was))
    assert victim.stat().st_mtime_ns == was, "the fixture did not hold the mtime"
    opens = _Opens(monkeypatch)

    read_czi_set(plate)

    assert opens.count >= 1, "a file that grew was served from the cache"


def test_a_file_rewritten_to_the_same_length_is_read_again(plate, monkeypatch):
    """And the mirror image: the length is identical and only the time moves.

    Size alone misses a re-export that lands on the same byte count, which is
    what re-exporting one well of a plate from ZEN with the same settings
    produces.
    """
    import os

    read_czi_set(plate)
    czi_read.forget_plate_metadata()
    victim = plate / "Exp7_B01.czi"
    was = victim.stat().st_size
    victim.write_bytes(victim.read_bytes())
    os.utime(victim, ns=(0, 0))
    assert victim.stat().st_size == was, "the fixture did not hold the length"
    opens = _Opens(monkeypatch)

    read_czi_set(plate)

    assert opens.count >= 1, "a file rewritten in place was served stale"


def test_a_newer_reader_does_not_serve_the_old_reader_s_answers(plate,
                                                                monkeypatch):
    """The half a file stamp cannot cover. What is reused is not the file, it
    is ``read_czi_layout``'s output — a function of the file *and* of the
    parser. Without this key a fix to the reader keeps serving the answers the
    broken one gave, off every user's disk, with nothing saying so."""
    read_czi_set(plate)
    czi_read.forget_plate_metadata()
    monkeypatch.setattr(scan_cache, "PARSER_VERSION",
                        scan_cache.PARSER_VERSION + 1)
    opens = _Opens(monkeypatch)

    read_czi_set(plate)

    assert opens.count >= 1


def test_a_cache_in_an_older_shape_is_dropped_rather_than_half_read(
        plate, monkeypatch):
    read_czi_set(plate)
    czi_read.forget_plate_metadata()
    monkeypatch.setattr(scan_cache, "FORMAT_VERSION",
                        scan_cache.FORMAT_VERSION + 1)
    opens = _Opens(monkeypatch)

    read_czi_set(plate)

    assert opens.count >= 1


def test_the_timed_read_is_not_served_from_the_untimed_one(plate):
    """``read_times`` is part of the key because it changes the answer: a
    layout read without it carries no ``time_scan``, and serving one to a
    caller that asked would report a plate as having no acquisition times."""
    where = plate / "Exp7_A01.czi"
    scan_cache.save([where])
    czi_read.read_czi_layout(where, read_times=False)
    scan_cache.save([where])
    czi_read.forget_plate_metadata()

    assert scan_cache.load([where]) == 1
    assert czi_read.cached_layout(where, read_times=False) is not None
    assert czi_read.cached_layout(where, read_times=True) is None


# ── a cache that cannot be trusted is a slow scan, never a wrong answer ─────

@pytest.mark.parametrize("body", [
    "",                                   # truncated to nothing
    "{",                                  # truncated mid-write
    "[]",                                 # valid JSON, wrong shape
    '{"format": 1, "parser": 1, "files": "not a mapping"}',
    '{"format": 1, "parser": 1, "files": {"Exp7_A01.czi": {"size": 1, '
    '"mtime_ns": 2, "layouts": {"0": {"#obj": "Nonsense", "f": {}}}}}}',
])
def test_an_unusable_cache_file_is_ignored(plate, monkeypatch, body):
    first = read_czi_set(plate)
    czi_read.forget_plate_metadata()
    scan_cache.cache_file_for(plate).write_text(body)
    opens = _Opens(monkeypatch)

    again = read_czi_set(plate)

    assert again.wells_from_files() == first.wells_from_files()
    assert opens.count >= 1, "nothing was re-read, so something was trusted"


def test_a_cache_directory_that_cannot_be_written_is_not_an_error(plate,
                                                                  monkeypatch):
    monkeypatch.setenv("ALLWELL_SCAN_CACHE", "off")

    result = read_czi_set(plate)

    assert result.problem == ""
    assert result.wells_from_files() == ["A01", "A02", "B01", "B02"]
    assert scan_cache.cache_dir() is None


def test_nothing_is_written_beside_the_images(plate):
    """A plate is routinely on a read-only archive or somebody else's share,
    and a converter that drops files into raw acquisition data is one people
    stop pointing at raw acquisition data."""
    before = sorted(p.name for p in plate.iterdir())

    read_czi_set(plate)
    peek_wells(plate)

    assert sorted(p.name for p in plate.iterdir()) == before


# ── the merge, which is what makes it useful on the real workflow ───────────

def test_scanning_one_file_does_not_forget_its_siblings(plate):
    """The page's tick-box widens one file to its folder and back. A save that
    wrote only what this scan learned would throw away the other 659 entries
    the moment a user clicked a single file."""
    read_czi_set(plate)
    czi_read.forget_plate_metadata()
    read_czi_set(plate / "Exp7_A01.czi")

    body = json.loads(scan_cache.cache_file_for(plate).read_text())

    assert sorted(body["files"]) == [
        "Exp7_A01.czi", "Exp7_A02.czi", "Exp7_B01.czi", "Exp7_B02.czi"]


def test_a_peek_after_a_full_read_keeps_the_plate_warm(plate, monkeypatch):
    """The sequence the two passes make between them, driven end to end."""
    read_czi_set(plate)
    czi_read.forget_plate_metadata()
    peek_wells(plate)
    czi_read.forget_plate_metadata()
    opens = _Opens(monkeypatch)

    result = read_czi_set(plate)

    assert result.wells_from_files() == ["A01", "A02", "B01", "B02"]
    assert opens.count == 0


def test_writing_an_entry_never_removes_an_answer_from_it(plate):
    """A save must not be able to *subtract*.

    Driven at ``_entry_for`` rather than through the two passes, because
    through them it cannot currently happen: both call ``load`` before they
    scan, so memory holds at least what the file did by the time ``save``
    reads it. That invariant is three call sites wide and nothing states it —
    an entry point that saved without loading, or a plate big enough to evict
    entries from ``czi_read``'s LRU in between, would trade a cached layout
    for a cached document. This pins the property rather than the path, which
    is the half that would still hold if the paths changed.
    """
    where = plate / "Exp7_A01.czi"
    read_czi_set(plate)                     # the file's entry has a layout
    on_disk = json.loads(scan_cache.cache_file_for(plate).read_text())
    entry = on_disk["files"][where.name]
    assert entry["layouts"], "the fixture did not cache a layout"
    czi_read.forget_plate_metadata()
    czi_read.remember_plate_metadata(       # memory now holds the other half
        where, czi_read.plate_metadata_for(where))

    merged = scan_cache._entry_for(where, entry)

    assert merged["layouts"] == entry["layouts"], "the layout was dropped"
    assert merged["meta"] is not None


def test_a_merge_never_carries_an_answer_about_bytes_that_changed(plate):
    """The dangerous half of that merge: keeping the previous entry is only
    safe while the file has not moved."""
    where = plate / "Exp7_A01.czi"
    read_czi_set(plate)
    stale = json.loads(scan_cache.cache_file_for(plate).read_text())
    stale_entry = stale["files"][where.name]
    where.write_bytes(where.read_bytes() + b"\0" * 8)
    czi_read.forget_plate_metadata()

    merged = scan_cache._entry_for(where, stale_entry)

    assert merged is None or merged["layouts"] == {}, (
        "a layout read from the bytes before the rewrite was carried over")


def test_an_entry_whose_file_has_gone_is_dropped(plate):
    read_czi_set(plate)
    (plate / "Exp7_B02.czi").unlink()
    czi_read.forget_plate_metadata()

    read_czi_set(plate)

    body = json.loads(scan_cache.cache_file_for(plate).read_text())
    assert "Exp7_B02.czi" not in body["files"]


def test_two_folders_of_the_same_name_do_not_share_an_entry(tmp_path):
    """Named for the folder so a person can tell what they are looking at,
    hashed on its absolute path so that is all the name does."""
    one = scan_cache.cache_file_for(tmp_path / "a" / "split")
    two = scan_cache.cache_file_for(tmp_path / "b" / "split")

    assert one != two
    assert one.name.startswith("split-") and two.name.startswith("split-")


def test_the_directory_does_not_grow_without_end(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_cache, "_MAX_FOLDERS", 3)
    for index in range(6):
        folder = tmp_path / f"plate{index}"
        folder.mkdir()
        write_czi(folder / "one.czi",
                  plate_tiles(wells=1, fovs=1, timepoints=1, channels=1),
                  xml=plate_metadata_xml(scene_wells={0: "A01"},
                                         channels=["DAPI"]))
        read_czi_set(folder)

    assert len(list(scan_cache.cache_dir().glob("*.json"))) == 3


# ── the encoding, which is where a silent loss would come from ──────────────

def _every_field_filled(cls):
    """An instance of *cls* with every field set to something non-default.

    Built from ``dataclasses.fields`` rather than written out, so a field added
    to ``CziLayout`` next year is covered by having been declared — a
    hand-written round-trip test is green the day a new field starts being
    dropped.
    """
    samples = {
        "scenes": [3, 4], "tiles": {3: [0, 1], 4: [0]},
        "size_t": 7, "size_c": 2, "size_z": 5, "width": 11, "height": 13,
        "dtype": "uint16", "channel_names": ["DAPI", "GFP"],
        "raw_channel_names": ["DAPI-T1", "GFP-T2"],
        "scene_wells": {3: "C07", 4: "H12"},
        "compressions": {0, 6}, "pixel_types": {1},
        "extra_axes": {"H": 2}, "n_blocks": 21, "duplicate_blocks": 1,
        "foreign_part_blocks": 2, "total_images": 28, "missing_images": 3,
        "has_metadata": False, "from_directory": False,
        "time_scan": czi_read.TimeScan(hours_by_index={0: 0.0, 1: 4.5},
                                       reasons=["a reason"], scene=3,
                                       bytes_read=99),
        "warnings": ["a warning"],
        "declared_scene_indices": (3, 4), "parse_error": "a parse error",
        "bytes_read": 4096,
    }
    missing = [f.name for f in dataclasses.fields(cls) if f.name not in samples]
    assert not missing, f"no sample value for {cls.__name__}.{missing}"
    return cls(**{f.name: samples[f.name] for f in dataclasses.fields(cls)})


@pytest.mark.parametrize("cls", [czi_read.CziLayout, czi_read._PlateMetadata])
def test_every_field_survives_the_round_trip(cls):
    """Including the ones plain JSON cannot carry: ``tiles`` and
    ``scene_wells`` are keyed by *integer* and JSON object keys are strings, so
    an untagged round trip turns scene 3 into scene "3" and every lookup
    misses — quietly, as a plate whose scenes name no wells."""
    original = _every_field_filled(cls)

    back = scan_cache._decode(json.loads(json.dumps(
        scan_cache._encode(original))))

    assert back == original
    if cls is czi_read.CziLayout:
        assert list(back.tiles) == [3, 4], "integer keys came back as strings"
        assert isinstance(back.compressions, set)
        assert isinstance(back.time_scan, czi_read.TimeScan)


def test_a_value_the_encoding_cannot_carry_raises(tmp_path):
    """Rather than being dropped. A field added to ``CziLayout`` whose type
    this does not know must fail loudly here, not be absent from every cached
    entry — absent reads as "the file does not have that", which is the silent
    substitution the cache must not be able to make."""
    with pytest.raises(TypeError, match="cannot store"):
        scan_cache._encode({"when": complex(1, 2)})


def test_the_decoder_builds_only_what_it_was_told_to(tmp_path):
    with pytest.raises(ValueError, match="unknown cached type"):
        scan_cache._decode({"#obj": "Popen", "f": {}})


def test_a_changed_reader_invalidates_every_cached_scan():
    """``PARSER_VERSION`` must move when the reader could answer differently.

    Pinned against a hash of the reader's own source rather than by memory,
    because "remember to bump the constant" is exactly the instruction that is
    not followed — the same guard ``review.data.parquet_cache`` carries, for
    the same reason. If this fails, read the diff: if a layout or a parsed
    document produced from unchanged bytes can differ, bump ``PARSER_VERSION``
    so every user's cache invalidates, then put the new digest here; if it
    genuinely cannot (a comment, a docstring, a log line), update the digest
    alone and say which in the commit.
    """
    import hashlib
    import inspect

    material = "".join([
        inspect.getsource(czi_read.read_czi_layout),
        inspect.getsource(czi_read.parse_plate_metadata),
        inspect.getsource(czi_read.wells_from_scenes),
        inspect.getsource(czi_read.CziLayout),
        inspect.getsource(czi_read._PlateMetadata),
    ])
    digest = hashlib.sha256(material.encode()).hexdigest()[:16]

    assert (scan_cache.PARSER_VERSION, digest) == (1, "e21c609d39c01c3a"), (
        "the CZI reader has changed. Every cached scan on every user's disk "
        "was produced by the old one and the file stamps do not know that. "
        f"Bump PARSER_VERSION, then put the new digest here: {digest}")
