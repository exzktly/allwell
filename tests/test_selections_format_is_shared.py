"""The seam between the shared block format and Review's plotting model.

`domain/selections_format.py` owns the on-disk ``sample_definitions`` block and
`review/domain/selections_model.py` owns what a selection means on a figure.
The split only holds if two things stay true, and neither is visible to any
test that exercises one side alone.

**One palette, not two.** A selection's ``color`` is baked from the rank of its
lowest well and then *written to disk*. Review bakes it when someone makes a
selection; Datasets re-bakes it in ``merge_plates`` when a remap moves a
condition across the plate. If the two ever baked from different lists, the same
well would be written two colours into the same file and nothing would say so —
a merged plate would simply come back with colours the user did not choose.

That is not hypothetical. Until this module's subject was extracted, the palette
accessor borrowed ``review.domain.plate_layout.WELL_COLORS`` behind a
``try``/``except`` and fell back to a *ten*-colour list whenever the import
failed, which on a 96-well plate repeats a colour every eleventh well. The
fallback is gone and these tests are what keep a second list from growing back.

**No name defined on both sides.** A helper that exists in both modules is the
half-swept state CLAUDE.md §7.5 #5 describes: one concept, two spellings, and
neither one looks wrong at its own site.
"""

from __future__ import annotations

import ast

from tests._repo_scan import REPO_ROOT, parse_source

from domain import selections_format as fmt
from domain.well_palette import WELL_COLORS_TUPLE
from review.domain import selections_model as model
from ui import theme


def test_the_palette_a_colour_is_baked_from_is_the_themes_own_list():
    """Not "it has some colours" — the list itself, in order.

    The wrong implementation this has to exclude is the one that shipped for
    years: a ten-entry fallback that is a perfectly good list of colours and
    the wrong one.
    """
    assert fmt.well_colors() == [str(c) for c in WELL_COLORS_TUPLE]
    assert fmt.well_colors() == [str(c) for c in theme.WELL_COLORS_TUPLE]
    assert len(fmt.well_colors()) == 96, (
        "one colour per 96-well plate position — a submultiple makes the "
        "colours repeat down each column, which is what "
        "tests/test_well_color_no_column_repeat.py explains at length")


def test_a_baked_colour_is_the_palette_entry_for_that_wells_rank():
    """The whole contract in one line: ``palette[well_rank(lowest well)]``.

    Spelled out per well rather than as a property, because "the colours
    differ" is satisfied by any palette at all.
    """
    palette = [str(c) for c in WELL_COLORS_TUPLE]
    for token, rank in [("A01", 0), ("A12", 11), ("B01", 12), ("H12", 95)]:
        assert fmt.well_rank(token) == rank
        sel = fmt.make_selection(name=token, wells=[token])
        assert sel["color"] == palette[rank], (
            f"{token} (rank {rank}) baked {sel['color']}, "
            f"palette[{rank}] is {palette[rank]}")


def test_a_selection_keeps_its_colour_through_a_block_round_trip():
    """The path Datasets actually drives, end to end.

    ``merge_sample_definitions`` drops a remapped selection's ``color`` so that
    ``to_block`` re-bakes it from where the wells landed; this pins that the
    re-bake lands on the palette entry for the new position rather than on
    whatever the old one was.
    """
    block = fmt.to_block([{"name": "Control", "wells": ["A01"]}])
    assert block["selections"][0]["color"] == str(WELL_COLORS_TUPLE[0])

    moved = dict(block["selections"][0])
    moved.pop("color")
    moved["wells"] = ["B01"]
    rebaked = fmt.to_block([moved])
    assert rebaked["selections"][0]["color"] == str(WELL_COLORS_TUPLE[12]), (
        "a condition remapped from A01 to B01 must take B01's rank colour; "
        "keeping A01's is the collision merge_plates_compute drops the field "
        "to avoid")


def _public_defs(rel: str) -> set[str]:
    tree = parse_source(REPO_ROOT / rel)
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def test_neither_half_redefines_a_name_the_other_half_owns():
    """Review's half imports ``well_rank`` and ``_clean_wells`` from the format
    half rather than keeping a copy. A name defined on both sides means the two
    modules have started to drift apart while every test still passes."""
    shared = _public_defs("domain/selections_format.py") & _public_defs(
        "review/domain/selections_model.py")
    assert not shared, (
        "these names are defined in both domain/selections_format.py and "
        f"review/domain/selections_model.py: {', '.join(sorted(shared))}. "
        "One of them is a copy — import it from the format half instead.")


def test_reviews_half_borrows_the_two_helpers_it_shares():
    """The other direction of the same rule: the borrow is the mechanism the
    test above relies on, so it is pinned rather than assumed."""
    assert model.well_rank is fmt.well_rank
    assert model._clean_wells is fmt._clean_wells
