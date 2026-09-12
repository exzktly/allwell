"""Every well token in the app is judged by ``util.well_token`` (ARCHITECTURE §15).

§15 states the rule and the bug behind it: when two modules kept their own
regex, a folder named ``"A1 "`` was a well to the Analyze-side input resolver
and not a well to the pipeline that then processed it. A local copy is not a
style problem — it is a second definition of what a well *is*.

The copies this file replaced disagreed with the canonicaliser in **both**
directions, which is worth stating plainly because only one of them is a
narrowing:

* **Widening.** ``canonical_well_label`` strips surrounding whitespace, so
  ``"A1 "`` is a well. The local ``^([A-Ha-h])(\\d{1,2})$`` patterns rejected it,
  and ``services/remote_runner``'s uppercase-only ``[A-H]\\d{1,2}`` also rejected
  a lower-case ``a1/`` directory.
* **Narrowing.** The canonicaliser bounds the column to 1–12, so ``A0`` and
  ``A13`` are not wells. The local patterns accepted both.

The membership-key helpers keep an "unrecognised token passes through
upper-cased" fallback, so a non-well is still compared as itself rather than
being coerced into a plausible-looking key. The one visible difference from the
old copies is that an out-of-range column is no longer zero-padded on the way
out (``"A0"`` stays ``"A0"`` instead of becoming ``"A00"``); neither form is a
well, so neither matches anything either helper is compared against.
"""

from __future__ import annotations

import pytest

from tests._repo_scan import python_sources, rel
from util.well_token import canonical_well_label, is_valid_well_name
from tests._repo_scan import parse_source


@pytest.mark.parametrize("raw, expected", [
    ("a1", "A01"), ("A01", "A01"), (" a1 ", "A01"), ("A1 ", "A01"),
    ("H12", "H12"), ("h12", "H12"),
    ("A0", None), ("A13", None), ("I01", None), ("", None), ("notawell", None),
])
def test_canonicaliser_is_the_definition(raw, expected):
    assert canonical_well_label(raw) == expected
    assert is_valid_well_name(raw) is (expected is not None)


def _membership_helpers():
    """The two "canonicalise for a membership test" helpers, which must agree
    with the canonicaliser and with each other."""
    from review.features.batch_export.base_panel import _norm_well_token
    from review.domain import bar_groups

    # bar_groups' copy is nested inside bar_groups_from_data; reach it through
    # the public entry point rather than restating it here.
    def via_bar_groups(tok: str) -> str:
        rsets, _ = bar_groups.bar_groups_from_data(
            {"rep_sets": [{"name": "r", "wells": [tok]}]},
            tok_to_label={"A01": "a", "H12": "h"},
        )
        return rsets[0].wells[0] if rsets and rsets[0].wells else ""

    return _norm_well_token, via_bar_groups


@pytest.mark.parametrize("raw, expected", [
    ("a1", "A01"), (" a1 ", "A01"), ("A1 ", "A01"), ("h12", "H12"),
    ("A0", "A0"), ("A13", "A13"), ("notawell", "NOTAWELL"),
])
def test_membership_canonicaliser_matches(raw, expected):
    norm, _ = _membership_helpers()
    assert norm(raw) == expected


@pytest.mark.parametrize("raw", ["a1", " a1 ", "A1 ", "A01"])
def test_a_valid_well_survives_bar_group_parsing(raw):
    """Every spelling of a real well resolves to the same canonical member."""
    _, via_bar_groups = _membership_helpers()
    assert via_bar_groups(raw) == "A01"


@pytest.mark.parametrize("raw", ["A0", "A13", "I01", "notawell"])
def test_a_non_well_is_dropped_by_bar_group_parsing(raw):
    """A token that is not a 96-well position matches no label and is dropped,
    rather than being padded into something that looks like one."""
    _, via_bar_groups = _membership_helpers()
    assert via_bar_groups(raw) == ""


def test_no_module_keeps_its_own_whole_token_well_regex():
    """A regex that judges a *whole* token as a well is a second definition of
    the rule. Filename-shape patterns (``<WELL>_out.zip``) and log-line scrapers
    are a different operation and are not matched here: this looks only for an
    anchored pattern whose entire body is a well token."""
    import ast
    import re

    # A well-token body, optionally group-wrapped. The row class covers the
    # spellings that actually appear — ``[A-H]``, ``[A-Ha-h]``, ``[a-hA-H]`` —
    # and the column covers ``\d{1,2}``, ``\d\d?`` and ``[0-9]{1,2}``. A guard
    # that only knew the one spelling it had just deleted would pass on the
    # next author's ``[0-9]``.
    row = r"\[(?:A-Ha-h|A-H|a-hA-H|a-h)\]"
    col = r"(?:\\d|\[0-9\])(?:\{1,2\}|\\d\?|\?)?"
    body = rf"\(?{row}\)?\(?{col}\)?"
    # Either anchored at BOTH ends (``^...$``, matched against a whole token) or
    # at neither (used with ``fullmatch``). A pattern anchored only at the end
    # is the sanctioned *trailing*-token extractor — ``measurements_b10`` →
    # ``B10`` — which judges a suffix of a longer stem, not a whole token, and
    # ARCHITECTURE §15 names it as a separate operation.
    whole_token = re.compile(rf"^(?:\^{body}\$|{body})$")
    # The same body anchored only at the end — sanctioned under `re.search`,
    # a whole-token judge under `re.match`.
    tail_anchored = re.compile(rf"^{body}\$$")
    offenders = []
    # The full application surface, including `pipeline/` and the repo-root
    # modules. Those are not incidental: `util/well_token.py`'s own docstring
    # names `process_microscopy._canonical_well_label` and
    # `well_plate_zipper._extract_well_from_filename` as the copies it unified,
    # and §15's story is a folder that was a well to one of them and not to the
    # other — so a guard blind to the pipeline could not see half the bug it
    # exists to prevent.
    for path in python_sources():
        if path.name == "well_token.py":
            continue
        tree = parse_source(path)
        # `re.match` anchors the start itself, so a pattern anchored only at the
        # END is a whole-token judge there — not the sanctioned trailing
        # extractor, which is what `re.search(r"…$")` is. Collect those calls
        # first so the literal inside them is judged by how it is used.
        match_anchored = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("match", "fullmatch") and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and tail_anchored.match(node.args[0].value)):
                match_anchored.add((node.args[0].lineno, node.args[0].value))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if whole_token.match(node.value) or (node.lineno, node.value) in match_anchored:
                offenders.append(f"{rel(path)}:{node.lineno} — {node.value!r}")
    assert not offenders, (
        "Whole-token well regex outside util/well_token.py (ARCHITECTURE §15). "
        "Use `canonical_well_label` / `is_valid_well_name` so this module and "
        "the pipeline agree on what a well is:\n  " + "\n  ".join(offenders)
    )


# ── ranges in the Background well field ──────────────────────────────────

def test_a_colon_names_a_rectangle_of_blanks():
    """``H01:H12`` is the whole of row H, and mixes with ordinary wells.

    Asserted on the wells, not on the count: a rule that expanded the corners
    into *something* twelve long would satisfy a count.
    """
    from util.well_token import split_background_wells

    assert split_background_wells("H01:H12") == [f"H{c:02d}" for c in range(1, 13)]
    assert split_background_wells("A01:B03") == ["A01", "A02", "A03",
                                                 "B01", "B02", "B03"]
    # Either corner first, and case and zero-padding immaterial — the same
    # readings the pairing map's right-hand side has always had.
    assert split_background_wells("D12:A01") == split_background_wells("A01:D12")
    assert split_background_wells("h1:h3") == ["H01", "H02", "H03"]

    # Mixed with singles, in the order written, de-duped across both.
    assert split_background_wells("H01:H03, A01") == ["H01", "H02", "H03", "A01"]
    assert split_background_wells("H01:H03, H02") == ["H01", "H02", "H03"]


def test_an_unreadable_block_is_refused_by_name_not_dropped():
    """Dropping it would remove a well the user believes is a blank.

    The field's whole job is to say which wells are references; a token it
    cannot read has to reach the caller that refuses by name, the same way any
    other well it cannot find does.
    """
    from util.well_token import split_background_wells

    assert split_background_wells("A01:ZZ") == ["A01:ZZ"]
    assert split_background_wells("nonsense") == ["nonsense"]
    # Whitespace still does not separate wells, so this is one unreadable token
    # rather than two good ones — the divergence that killed every batch of a run.
    assert split_background_wells("H12 G12") == ["H12 G12"]


def test_every_reader_of_the_field_sees_the_same_wells(tmp_path):
    """A range must not mean twelve wells here and one token there.

    The blank set is asked by the model build, the remote staging planner, the
    batched hold-out, both incremental-scope readers, the pairing editor and the
    run receipt. They agree by going through one function; this drives the ones
    reachable without a GUI or a remote host and holds them against it.
    """
    from pipeline.empty_well import BlankGroups
    from services.input_resolution_service import _blank_well_tokens
    from util.well_token import blank_wells_for_run, split_background_wells

    field = "H01:H03"
    expected = ["H01", "H02", "H03"]
    assert split_background_wells(field) == expected

    # The pipeline's own plan, pooled: one group holding all three.
    groups = BlankGroups(field, "", corrections=("empty_well",))
    assert groups.blanks() == expected

    # The union both flags feed, which is what staging and the hold-out read.
    assert blank_wells_for_run(field, "") == expected
    # And the incremental-scope reader, which had its own regex once.
    assert _blank_well_tokens({"background_well": field}) == set(expected)
