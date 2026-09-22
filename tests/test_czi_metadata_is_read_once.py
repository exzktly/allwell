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
