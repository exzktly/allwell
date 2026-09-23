"""When the file's own well names cannot be used, the reason is the reason.

``CziSet.scene_well_groups`` refuses unless **every** scene names a well. Two
conditions do that — none of them names one, or some do and some do not — and
one sentence covered both. It quoted the count of scenes that *did* resolve
whatever had actually failed, so the message was built out of the check that
passed. On a real 660-scene export that came out as a flat contradiction:

    ... do not name a distinct well for every one of their 660 scene(s)
    — 660 of them resolve to a well.

Every number in it is correct and the sentence is unusable. It was also
refusing something that is not a fault at all: those 660 scenes were seven
imaged positions in each of ninety-six wells, which
``tests/test_several_scenes_make_one_well.py`` covers.

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


# ── some scenes named, some not ─────────────────────────────────────────────

def test_an_unnamed_scene_is_reported_as_an_unnamed_scene():
    source = _set(["A01", "", "A03", ""])

    message = why(source, "Exp.czi")

    assert "2 of the 4 scene(s)" in message
    assert "name no well" in message
    assert "scene 1, 3" in message


def test_a_long_list_of_unnamed_scenes_is_cut_off_and_counted():
    """A readout is not a log, and a count that silently stopped at three
    would say four scenes are unnamed when forty are."""
    source = _set([""] * 40 + ["A01"])

    message = why(source, "Exp.czi")

    assert "40 of the 41 scene(s)" in message
    assert "and 37 more" in message


def test_the_half_named_message_says_why_half_is_worse_than_none():
    """The reason it is all-or-nothing rather than a best effort: the named
    scenes would land on their real wells and the rest wherever the grid put
    them, so the mapping is right exactly where it would be spot-checked."""
    message = why(_set(["A01", ""]), "Exp.czi")

    assert "spot-checked" in message


# ── and nothing named at all ────────────────────────────────────────────────

def test_a_source_naming_nothing_says_so_plainly():
    message = why(_set(["", ""]), "Exp.czi")

    assert "No well is named for any of the 2 scene(s)" in message
    assert "name no well" not in message, "that is the half-named case's words"


# ── shared by both ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("scene_wells", [["", ""], ["A01", ""]])
def test_every_refusal_says_what_to_do_instead(scene_wells):
    message = why(_set(scene_wells), "Exp.czi")

    assert "Switch the well source to the plate grid" in message


@pytest.mark.parametrize("n_files,expected", [(1, "Exp.czi"), (6, "6 file(s)")])
def test_the_subject_reads_for_one_file_and_for_many(n_files, expected):
    """The sentences name the scenes rather than the source, so that the verb
    does not have to agree with a subject that is one file here and six
    hundred there — "Exp name no wells" was the version that did."""
    message = why(_set(["A01", ""], n_files=n_files), "Exp.czi")

    assert expected in message
    assert " name no well " in message


# ── and the page is where the sentence has to arrive ────────────────────────
#
# Everything above calls the helper. A helper the caller has stopped using is
# a helper whose tests all pass (§7.8 face one) — measured: reverting
# ``resolve_well_map`` to the old single sentence left every test above green.
# So this one goes in through the page's own entry point, on a real file.

def test_the_page_says_which_scenes_are_unnamed(tmp_path):
    from datasets.czi_import.czi_import_compute import (
        ConversionParams, CziImportError, plan_conversion, read_source,
    )
    from tests._czi_fixture import plate_metadata_xml, plate_tiles, write_czi

    src = write_czi(
        tmp_path / "Exp.czi",
        plate_tiles(wells=2, fovs=1, timepoints=1, channels=1),
        xml=plate_metadata_xml(scene_wells={0: "A01"}, channels=["DAPI"]))
    params = ConversionParams(experiment="E", well_source="file")

    with pytest.raises(CziImportError) as caught:
        plan_conversion(read_source(src, params), params)

    assert "1 of the 2 scene(s)" in str(caught.value)
    assert "name no well" in str(caught.value)
