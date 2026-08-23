"""Two filename tokens that differ only by alphabet are two tokens (audit C15).

``\\d`` is Unicode by default, so it matches every decimal digit in Unicode —
ARABIC-INDIC ZERO, FULLWIDTH ONE and some six hundred others — and ``int()`` and
``float()`` convert all of them. ``canonical_well_label("A١")``,
``canonical_well_label("A１")`` and ``canonical_well_label("A1")`` therefore all
returned ``"A01"``, merging three distinct filename tokens — three distinct
files — onto one well; ``parse_hours`` did the same to timepoints, and its
``float()`` fallback reached even the strings the regex would have refused.

The rule these parsers already state elsewhere is that an unreadable token is
kept as an opaque label rather than guessed at. A digit from another alphabet is
exactly that.
"""

from __future__ import annotations

import pytest

from util.timepoint import parse_hours, sort_key
from util.well_token import canonical_well_label, is_valid_well_name

# One per script, plus the fullwidth forms a Japanese/Chinese Windows locale
# produces and the Arabic-Indic forms a right-to-left one does.
NON_ASCII_DIGIT_WELLS = ["A１", "A١", "A٠١", "Ａ1", "A₁", "A１２"]
NON_ASCII_DIGIT_TIMEPOINTS = ["１", "１h", "١h", "٢d", "１２", "t１"]


@pytest.mark.parametrize("token", NON_ASCII_DIGIT_WELLS)
def test_a_well_token_with_non_ascii_digits_is_not_a_well(token):
    assert canonical_well_label(token) is None, (
        f"{token!r} canonicalised to {canonical_well_label(token)!r}, merging it "
        f"onto a well written with ASCII digits"
    )
    assert not is_valid_well_name(token)


@pytest.mark.parametrize("token", ["A1", "a01", "H12", "b3", "A01"])
def test_ordinary_well_tokens_still_canonicalise(token):
    assert canonical_well_label(token) is not None


@pytest.mark.parametrize("token", NON_ASCII_DIGIT_TIMEPOINTS)
def test_a_timepoint_token_with_non_ascii_digits_is_opaque(token):
    assert parse_hours(token) is None, (
        f"{token!r} parsed as {parse_hours(token)!r} hours"
    )
    # And an opaque label sorts into the opaque block rather than claiming an hour.
    assert sort_key(token)[0] is True


@pytest.mark.parametrize("token,hours", [
    ("0", 0.0), ("24", 24.0), ("1.5h", 1.5), ("2d", 48.0), ("90min", 1.5),
    ("02d04h30m", 52.5), ("T01", 1.0), ("day3", 3.0),
])
def test_ordinary_timepoint_tokens_still_parse(token, hours):
    assert parse_hours(token) == pytest.approx(hours)


def test_nan_and_inf_still_parse_as_they_are_documented_to():
    """The module docstring promises these parse; the ASCII gate must not eat them."""
    assert parse_hours("nan") != parse_hours("nan")     # NaN
    assert parse_hours("inf") == float("inf")
