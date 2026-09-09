"""The blank-well → treatment-wells map, and the one answer to "which are blanks".

A plate whose blocks carry treatment-specific autofluorescence cannot be
corrected from one pooled reference: a compound that fluoresces puts real signal
into its own blank, and pooling spreads that signal across every well on the
plate. The map pairs each blank with the wells it corrects.

Everything here is pure parsing, which is the point — the rules that decide
which well is corrected by which blank are decidable without a plate, an image
or a Qt widget, and this is where they are decided.
"""

from __future__ import annotations

import pytest

from util.well_token import (
    blank_wells_for_run,
    format_background_well_map,
    parse_background_well_map,
)


def test_a_written_map_parses_to_blanks_and_the_wells_they_correct():
    assert parse_background_well_map("H12=A01:B02; G12=C01,C02") == {
        "H12": ["A01", "A02", "B01", "B02"],
        "G12": ["C01", "C02"],
    }


def test_a_block_is_the_rectangle_between_its_corners():
    """Rows by columns, in reading order, either corner first.

    Asserted as the exact list rather than as a count: a rectangle and a
    row-major run of the same length are the same size and different wells, and
    only one of them is the block the user drew.
    """
    assert parse_background_well_map("H12=B02:C04")["H12"] == [
        "B02", "B03", "B04", "C02", "C03", "C04"]
    assert parse_background_well_map("H12=C04:B02")["H12"] == \
        parse_background_well_map("H12=B02:C04")["H12"]
    # A one-well "block" is that well, not an error.
    assert parse_background_well_map("H12=A01:A01")["H12"] == ["A01"]


def test_the_spellings_a_user_might_write_all_land_on_one_key():
    """Padding, case and spacing are the user's; the map's keys are canonical."""
    loose = parse_background_well_map("  h12 = a1 : b2 ")
    assert loose == {"H12": ["A01", "A02", "B01", "B02"]}
    assert parse_background_well_map("H12=A01\nG12=B01") == \
        parse_background_well_map("H12=A01; G12=B01")
    # An already-parsed mapping round-trips, which is what a saved run replays.
    assert parse_background_well_map({"h12": ["a1", "a2"]}) == {"H12": ["A01", "A02"]}


def test_nothing_maps_to_nothing():
    for empty in ("", None, {}, "   ", ";;"):
        assert parse_background_well_map(empty) == {}


@pytest.mark.parametrize("text,expected_words", [
    ("H12", "not a blank-well assignment"),
    ("ZZ=A01", "not a well"),
    ("H12=", "mapped to nothing"),
    ("H12=nonsense", "not a well or a block"),
    ("H12=A01:ZZ99", "not a well or a block"),
    ("H12=A01; G12=A01", "mapped to two blanks"),
    ("H12=A01; H12=B01", "appears twice"),
    ("H12=A01,H12", "named as a blank and also"),
])
def test_a_map_that_could_be_read_two_ways_is_refused_and_says_why(
        text, expected_words):
    """Refused, not resolved.

    Every one of these has a rule that could absorb it — take the first claim,
    drop the unreadable token, ignore the empty group — and every such rule ends
    with a well corrected by a reference the user did not choose, which is
    exactly what the map exists to prevent. The message has to name the offending
    text, because the alternative is a user re-reading a 96-well map by eye.
    """
    with pytest.raises(ValueError) as excinfo:
        parse_background_well_map(text)
    assert expected_words in str(excinfo.value)


def test_the_written_form_round_trips_through_the_parser():
    """What the editor and the sidecar store is the explicit list, never a range.

    A rectangle is a claim about the plate's shape that stops being true the
    moment somebody edits one well out of the block, so the machine writes what
    it means. Ranges stay accepted on the way in, for the human typing a command.
    """
    mapping = parse_background_well_map("H12=A01:A03; G12=B01")
    written = format_background_well_map(mapping)
    assert written == "H12=A01,A02,A03; G12=B01"
    assert parse_background_well_map(written) == mapping


def test_blank_wells_for_run_is_the_union_of_both_spellings():
    assert blank_wells_for_run("H12", "") == ["H12"]
    assert blank_wells_for_run("", "H12=A01") == ["H12"]
    assert blank_wells_for_run("H12,G12", "F12=A01") == ["H12", "G12", "F12"]
    # A blank named both ways is one blank, listed once.
    assert blank_wells_for_run("h12", "H12=A01") == ["H12"]
    assert blank_wells_for_run("", "") == []


def test_blank_wells_for_run_reads_a_list_the_way_the_pipeline_does():
    """One answer to "which wells are blanks", not a sixth spelling of it.

    ``"H12 G12"`` is one token to ``split_background_wells`` — whitespace does
    not separate wells, commas and semicolons do — and a local
    whitespace-splitting regex that read it as two is what once made every batch
    of a remote run die on the empty-well guard. This pins the union to the
    pipeline's own reading rather than to a convenient one.
    """
    assert blank_wells_for_run("H12 G12", "") == ["H12 G12"]
    assert blank_wells_for_run("H12, G12", "") == ["H12", "G12"]
    assert blank_wells_for_run("H12; G12", "") == ["H12", "G12"]


def test_a_non_well_identifier_survives_as_a_blank():
    """``background_well_key`` matches a non-well name case-insensitively, so a
    folder or archive literally called ``blank`` can be the reference. The union
    must not canonicalise that away."""
    assert blank_wells_for_run("blank", "") == ["blank"]


def test_a_block_holds_out_the_runs_blanks_and_covers_the_rest():
    """The documented example has to parse, and it contains both blanks.

    ``H12=A01:D12; G12=E01:H12`` is the natural way to write "top half, bottom
    half", and the bottom-half rectangle contains G12 and H12. Refusing it made
    the example printed in the manual and in ARCHITECTURE a map the parser
    rejects — found by a test fixture, not by reading. A block is a shorthand
    for a region, so the run's blanks are held out of it; between the two entries
    every one of the 96 wells is then accounted for exactly once.
    """
    mapping = parse_background_well_map("H12=A01:D12; G12=E01:H12")
    assert sorted(mapping) == ["G12", "H12"]
    covered = [w for wells in mapping.values() for w in wells]
    assert len(covered) == len(set(covered)) == 94
    assert "H12" not in covered and "G12" not in covered
    assert "H11" in mapping["G12"] and "D12" in mapping["H12"]

    # One entry's block may contain a *later* entry's blank, so the hold-out set
    # cannot be built as the entries are read: A01 is the second blank and sits
    # inside the first entry's rectangle.
    second = parse_background_well_map("D12=A01:D12; A01=E01:H12")
    assert "A01" not in second["D12"] and "D12" not in second["D12"]
    assert len(second["D12"]) == 46 and len(second["A01"]) == 48


def test_a_blank_named_individually_is_still_refused():
    """A block is a region; a single well is a statement about that well."""
    for bad in ("H12=A01,H12", "H12=H12", "H12=A01; G12=H12"):
        with pytest.raises(ValueError) as excinfo:
            parse_background_well_map(bad)
        assert "blank" in str(excinfo.value).lower(), bad
