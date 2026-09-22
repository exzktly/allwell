"""A CZI's metadata document is walked as few times as the questions need.

Reading a CZI header costs nothing for the file's pixels and everything for
its metadata document: measured on synthetic ZEN-shaped files, about 80 ms per
megabyte of XML, of which four fifths is ``ET.fromstring`` building the tree
and the rest is walking it. A ZEN split-scene export puts the *whole plate's*
document in every file, so on the 661-file export this was reported against,
each traversal saved is 661 traversals saved per pass.

Asserted as the number of elements visited rather than as a duration, because
a timing assertion on a build machine is a flake with a schedule — and counted
through ``_localname``, which every traversal in this module calls exactly once
per element it looks at. That makes the count the traversals themselves, not a
proxy for them.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import pytest

from pipeline import czi_read


def _plate_xml(n_scenes: int, tiles_per_scene: int) -> str:
    """A plate document shaped like ZEN's: one ``Scene`` per well, each
    carrying the per-tile stage positions a tiled acquisition records.

    The tile elements are the bulk, which is the point: they are what makes a
    real document large, they sit *inside* the scenes, and they are of no
    interest to any of the three questions asked of the document.
    """
    scenes = []
    for i in range(n_scenes):
        well = f"{chr(ord('A') + i // 12)}{i % 12 + 1}"
        positions = "".join(
            f'<Tile Index="{j}"><X>{j}</X><Y>{j}</Y><Z>0</Z></Tile>'
            for j in range(tiles_per_scene))
        scenes.append(
            f'<Scene Index="{i}" Name="{well}">'
            f'<Shape Name="{well}"/>'
            f"<Positions>{positions}</Positions></Scene>")
    return ("<ImageDocument><Metadata><Information><Image>"
            f"<SizeS>{n_scenes}</SizeS><Dimensions><Channels>"
            '<Channel Id="Channel:0" Name="DAPI"/>'
            "</Channels></Dimensions></Image></Information>"
            f"<Scenes>{''.join(scenes)}</Scenes></Metadata></ImageDocument>")


@pytest.fixture
def counted(monkeypatch):
    """Count every element any traversal in ``czi_read`` looks at."""
    seen = {"n": 0}
    real = czi_read._localname

    def _counting(tag):
        seen["n"] += 1
        return real(tag)

    monkeypatch.setattr(czi_read, "_localname", _counting)
    return seen


def test_the_peek_s_two_questions_cost_two_walks_not_four(counted):
    """``_scene_wells`` and ``_declared_scene_indices`` are the whole of a
    header read, and between them they used to walk the document four times:
    once for the scenes, once again for the scene *list*, and once per scene
    for each of ``Shape`` and ``ArrayName``.

    One index pass answers both root-level questions, and one walk of each
    scene collects both of the names inside it — so the cost is the document
    twice over, and the second of those is only the part inside the scenes.
    """
    root = ET.fromstring(_plate_xml(n_scenes=96, tiles_per_scene=200))
    elements = sum(1 for _ in root.iter())

    index = czi_read._DocIndex(root)
    wells = czi_read._scene_wells(index)
    indices = czi_read._declared_scene_indices(index)

    # The answers first: a walk count means nothing about a function that has
    # stopped working (§7.8 — name the wrong implementation that still passes;
    # "it visited few elements" is satisfied by one that visits none).
    assert wells[0] == "A01"
    assert wells[95] == "H12"
    assert indices == list(range(96))

    # 2.2 rather than 2: the index pass is every element, the scene pass is
    # every element inside a scene, and the handful outside them is the slack.
    assert counted["n"] <= elements * 2.2, (
        f"{counted['n']} element visits for a {elements}-element document — "
        f"that is {counted['n'] / elements:.1f} walks of it")


def test_the_full_read_asks_its_two_questions_off_one_index(counted):
    """``_build_layout`` wants the scene wells and the channel names. Those
    are two more root-level lookups, and they share the index the scenes were
    found with rather than each walking the tree again."""
    root = ET.fromstring(_plate_xml(n_scenes=96, tiles_per_scene=200))
    elements = sum(1 for _ in root.iter())

    index = czi_read._DocIndex(root)
    wells = czi_read._scene_wells(index)
    channels = czi_read._channel_names(index)

    assert wells[7] == "A08"
    assert channels == ["DAPI"]
    assert counted["n"] <= elements * 2.2, (
        f"{counted['n']} element visits for a {elements}-element document")


def test_a_scene_named_only_by_an_array_name_is_still_read(counted):
    """The single-walk rewrite collects ``Shape`` and ``ArrayName`` in one
    pass, so it has to keep their order of preference — and keep finding a
    well that only the second of them names.

    Ordered so that a rule which simply took the first name it met would pick
    the wrong one: the array name here is the well, and the scene's own free
    text is a label that is not.
    """
    xml = ('<ImageDocument><Scenes>'
           '<Scene Index="0" Name="Position 1">'
           '<Positions><ArrayName>C07</ArrayName></Positions>'
           '</Scene>'
           '<Scene Index="1" Name="D04">'
           '<Shape Name="B02"/><ArrayName>G11</ArrayName>'
           '</Scene>'
           '</Scenes></ImageDocument>')
    index = czi_read._DocIndex(ET.fromstring(xml))

    wells = czi_read._scene_wells(index)

    assert wells[0] == "C07"
    # Shape beats ArrayName beats the scene's own Name, and all three name a
    # different well here, so each wrong order picks a different answer.
    assert wells[1] == "B02"


# ── the document is parsed once per file, not once per pass ──────────────────

@pytest.fixture(autouse=True)
def _cold_cache():
    """Every test here starts with nothing remembered, and leaves nothing
    behind for the next one."""
    czi_read.forget_plate_metadata()
    yield
    czi_read.forget_plate_metadata()


@pytest.fixture
def parses(monkeypatch):
    """Count the times a metadata document is parsed."""
    seen = {"n": 0}
    real = ET.fromstring

    def _counting(text, *args, **kwargs):
        seen["n"] += 1
        return real(text, *args, **kwargs)

    monkeypatch.setattr(czi_read.ET, "fromstring", _counting)
    return seen


def _one_file(tmp_path, xml, name="plate.czi"):
    from tests._czi_fixture import plate_tiles, write_czi
    return write_czi(tmp_path / name,
                     plate_tiles(wells=1, fovs=1, timepoints=1, channels=1),
                     xml=xml)


def test_four_passes_over_a_plate_parse_its_document_once(tmp_path, parses):
    """A plate is read four times before an archive is written: the page
    peeks, the page reads in full, the worker reads again to validate, and
    ``explode_czi`` reads once more. A split-scene export puts the whole
    plate's document in every file, so that is four parses of the same
    megabytes per file — 2,644 of them on the 661-file export reported here.
    """
    path = _one_file(tmp_path, _plate_xml(n_scenes=8, tiles_per_scene=20))

    first = czi_read.peek_scene_wells(path)
    layouts = [czi_read.read_czi_layout(path) for _ in range(3)]

    assert parses["n"] == 1, (
        f"{parses['n']} parses of one document across four passes")
    # The answers are the answers, parsed once or four times (§7.8: "it parsed
    # once" is satisfied by a reader that returns nothing).
    assert first.wells[0] == "A01"
    assert all(layout.scene_wells[0] == "A01" for layout in layouts)
    assert all(layout.channel_names == ["DAPI"] for layout in layouts)


def test_a_file_rewritten_under_the_cache_is_read_again(tmp_path, parses):
    """The reuse key has to change whenever the answer could have. A plate
    re-exported in place, with no setting touched, is the case: the page would
    otherwise convert the new images against the old file's scene mapping and
    report success — a complete, correctly named archive of the wrong thing.

    Rewritten to the **same length** on purpose, so that the size alone cannot
    be what notices; only the modification time can.
    """
    before = _plate_xml(n_scenes=2, tiles_per_scene=3)
    path = _one_file(tmp_path, before)
    assert czi_read.peek_scene_wells(path).wells == ["A01", "A02"]
    parses["n"] = 0

    # Same document shape, different wells, byte-for-byte the same length.
    after = before.replace('Name="A1"', 'Name="G9"').replace(
        'Name="A2"', 'Name="G8"')
    assert len(after) == len(before), "the fixture must not change the size"
    size_before = path.stat().st_size
    time.sleep(0.01)                  # so the modification time can differ
    _one_file(tmp_path, after)
    assert path.stat().st_size == size_before, (
        "the rewrite changed the file's length, so the size alone would "
        "notice it and this test would not be measuring the timestamp")

    assert czi_read.peek_scene_wells(path).wells == ["G09", "G08"]
    assert parses["n"] == 1, "the rewritten file was served from the cache"


def test_the_filename_fallback_is_applied_after_the_cache(tmp_path, parses):
    """``from_filename`` decides whether a one-scene file may be named by its
    own filename. It is a property of the *question*, not of the document, so
    it is applied to the cached answer rather than baked into it — otherwise
    one call would poison the other's result.
    """
    xml = ('<ImageDocument><Scenes><Scene Index="0" Name="Position 1"/>'
           "</Scenes></ImageDocument>")
    path = _one_file(tmp_path, xml, name="D06.czi")

    named = czi_read.peek_scene_wells(path, from_filename=True)
    unnamed = czi_read.peek_scene_wells(path, from_filename=False)

    assert parses["n"] == 1
    assert named.wells == ["D06"], named.wells
    assert unnamed.wells == [""], unnamed.wells


# ── the plate's layout, likewise ─────────────────────────────────────────────

def test_the_three_full_reads_open_the_file_once(tmp_path, monkeypatch):
    """After the page has read the plate, the worker reads it again to
    validate and ``explode_czi`` reads it once more. Each of those walks every
    file's subblock directory — about a megabyte per file, and a round trip
    per file on a share — for an answer that has not changed.

    Counted as **file opens**, which is what a network share charges for,
    rather than as elapsed time.
    """
    import builtins
    path = _one_file(tmp_path, _plate_xml(n_scenes=4, tiles_per_scene=5))

    opens = {"n": 0}
    real_open = builtins.open

    def _counting(file, *args, **kwargs):
        if str(file) == str(path):
            opens["n"] += 1
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _counting)
    layouts = [czi_read.read_czi_layout(path) for _ in range(3)]

    assert opens["n"] == 1, f"{opens['n']} opens for three reads of one file"
    # Same answer each time, which is the whole claim being made (§7.8).
    assert [layout.scene_wells for layout in layouts] == [
        {0: "A01", 1: "A02", 2: "A03", 3: "A04"}] * 3
    assert all(layout.n_blocks == layouts[0].n_blocks for layout in layouts)


def test_a_layout_read_without_the_times_is_not_served_to_one_that_wants_them(
        tmp_path):
    """``read_times`` changes the answer — a layout read without it carries no
    ``time_scan`` at all — so it is part of the key.

    Ordered cheap-read-first, which is the order that catches a key missing
    it: serving the cheap layout to the caller that asked for the times would
    report a plate as having no timing, and §7's whole point is that "not
    computed" and "computed as nothing" are different answers.
    """
    path = _one_file(tmp_path, _plate_xml(n_scenes=2, tiles_per_scene=3))

    without = czi_read.read_czi_layout(path)
    with_times = czi_read.read_czi_layout(path, read_times=True)

    assert without.time_scan is None
    assert with_times.time_scan is not None, (
        "the cheap read was served to the caller that asked for the times")


def test_a_caller_cannot_poison_the_next_one_s_layout(tmp_path):
    """The cache hands back a copy. Nothing in the tree mutates a layout it
    was given, and the copy is so that nothing has to keep not doing it.

    Three reads, not two, and the scribble goes on the **second**. The store
    copies as well, so the first read holds the freshly built layout and never
    the cached one — writing on that would leave the cache untouched however
    the cache hands its answers out, and the test would pass against a version
    that hands out the stored object itself. Measured: it did.
    """
    path = _one_file(tmp_path, _plate_xml(n_scenes=2, tiles_per_scene=3))

    czi_read.read_czi_layout(path)                  # fills the cache
    served = czi_read.read_czi_layout(path)         # served *from* the cache
    served.warnings.append("scribbled on by its caller")
    served.scene_wells[0] = "H12"
    after = czi_read.read_czi_layout(path)

    assert "scribbled on by its caller" not in after.warnings
    assert after.scene_wells[0] == "A01"
