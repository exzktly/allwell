"""When the file's own well names cannot be used, the reason is the reason.

``CziSet.wells_from_files`` refuses unless the scenes map **one to one** onto
wells: no scene unnamed, and no well claimed twice. One sentence covered all
three ways of failing and quoted the count belonging to the *unnamed* check
whatever had actually failed — so a set whose scenes all resolve but collide
produced a flat contradiction:

    ... do not name a distinct well for every one of their 660 scene(s)
    — 660 of them resolve to a well.

That is what a real 660-scene export said. Every number in it is correct and
the sentence is unusable: it reports the check that passed and says nothing
about the one that did not.

Each case is asserted here against the thing that actually distinguishes it,
rather than against "the message is non-empty" — which the old sentence also
satisfied (§7.8).
"""

from __future__ import annotations

import pytest

from pipeline.czi_explode import _why_the_file_cannot_name_the_wells as why
from pipeline.czi_set import CziSet


def _set(scene_wells, n_files=1):
    return CziSet(scene_wells=list(scene_wells),
                  files=[f"f{i}.czi" for i in range(n_files)])


# ── the case that read as a contradiction ───────────────────────────────────

def test_colliding_wells_are_named_and_counted():
    """The reported case, at its reported size: 660 scenes across 660 files,
    each resolving to a well, landing on ninety-six of them."""
    wells = [f"{row}{col:02d}" for row in "ABCDEFGH" for col in range(1, 13)]
    source = _set([wells[i % 96] for i in range(660)], n_files=660)

    message = why(source, "Exp")

    assert "660 scene(s)" in message
    assert "only 96 distinct well(s)" in message
    # A repeated well named, with how often — which is what turns "something
    # collided" into "this well, seven times".
    assert "(7 scenes)" in message
    # And never the clause that made it a contradiction.
    assert "resolve to a well" not in message


def test_the_collision_message_says_what_would_happen():
    """Not just that it refused: a well written from more than one scene keeps
    only the last, which is the loss being prevented."""
    message = why(_set(["A01", "A01"]), "Exp.czi")

    assert "only the last would survive" in message
    assert "A01 (2 scenes)" in message


def test_the_collision_message_explains_the_model_it_does_not_fit():
    """One scene is one well and its tiles are that well's fields of view. An
    acquisition visiting a well several times as separate scenes is not a
    malformed file — it is a shape this converter does not read."""
    message = why(_set(["A01", "A01"]), "Exp.czi")

    assert "one scene as one well" in message
    assert "fields of view" in message


# ── the two it used to be confused with ─────────────────────────────────────

def test_an_unnamed_scene_is_reported_as_an_unnamed_scene():
    source = _set(["A01", "", "A03", ""])

    message = why(source, "Exp.czi")

    assert "2 of the 4 scene(s)" in message
    assert "name no well" in message
    assert "distinct" not in message, "that is the collision case's word"


def test_a_long_list_of_unnamed_scenes_is_cut_off_and_counted():
    """A readout is not a log, and a count that silently stopped at three
    would say four scenes are unnamed when forty are."""
    source = _set([""] * 40 + ["A01"])

    message = why(source, "Exp.czi")

    assert "40 of the 41 scene(s)" in message
    assert "and 37 more" in message


def test_a_source_naming_nothing_says_so_plainly():
    message = why(_set(["", ""]), "Exp.czi")

    assert "No well is named for any of the 2 scene(s)" in message
    assert "distinct" not in message


# ── shared by all three ─────────────────────────────────────────────────────

@pytest.mark.parametrize("scene_wells", [["", ""], ["A01", ""], ["A01", "A01"]])
def test_every_refusal_says_what_to_do_instead(scene_wells):
    message = why(_set(scene_wells), "Exp.czi")

    assert "Switch the well source to the plate grid" in message


@pytest.mark.parametrize("n_files,expected", [(1, "Exp.czi"), (6, "6 file(s)")])
def test_the_subject_reads_for_one_file_and_for_many(n_files, expected):
    """The sentences name the scenes rather than the source, so that the verb
    does not have to agree with a subject that is one file here and six
    hundred there — "Exp name no wells" was the version that did."""
    message = why(_set(["A01", "A01"], n_files=n_files), "Exp.czi")

    assert expected in message
    assert " name only " in message


# ── and the page is where the sentence has to arrive ────────────────────────
#
# Everything above calls the helper. A helper the caller has stopped using is
# a helper whose tests all pass (§7.8 face one) — measured: reverting
# ``resolve_well_map`` to the old single sentence left every test above green.
# So these two go in through the page's own entry point, on real files.

def _plate(tmp_path, scene_wells, n_scenes):
    from tests._czi_fixture import plate_metadata_xml, plate_tiles, write_czi
    return write_czi(
        tmp_path / "Exp.czi",
        plate_tiles(wells=n_scenes, fovs=1, timepoints=1, channels=1),
        xml=plate_metadata_xml(scene_wells=scene_wells, channels=["DAPI"]))


def _refusal(src):
    from datasets.czi_import.czi_import_compute import (
        ConversionParams, CziImportError, plan_conversion, read_source,
    )
    params = ConversionParams(experiment="E", well_source="file")
    with pytest.raises(CziImportError) as caught:
        plan_conversion(read_source(src, params), params)
    return str(caught.value)


def test_the_page_says_which_wells_collided(tmp_path):
    message = _refusal(_plate(tmp_path, {0: "A01", 1: "A01"}, 2))

    assert "only 1 distinct well(s)" in message
    assert "A01 (2 scenes)" in message
    assert "resolve to a well" not in message


def test_the_page_says_which_scenes_are_unnamed(tmp_path):
    message = _refusal(_plate(tmp_path, {0: "A01"}, 2))

    assert "1 of the 2 scene(s)" in message
    assert "name no well" in message
