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

from util.well_token import canonical_well_label, is_valid_well_name


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
    from well_viewer.features.batch_export.base_panel import _norm_well_token
    from well_viewer.domain import bar_groups

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
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    # A well-token body, optionally group-wrapped: ``[A-H]``/``[A-Ha-h]``
    # followed by one or two digits, and nothing else.
    body = r"\(?\[A-H(?:a-h)?\]\)?\(?\\d\{1,2\}\)?"
    # Either anchored at BOTH ends (``^...$``, matched against a whole token) or
    # at neither (used with ``fullmatch``). A pattern anchored only at the end
    # is the sanctioned *trailing*-token extractor — ``measurements_b10`` →
    # ``B10`` — which judges a suffix of a longer stem, not a whole token, and
    # ARCHITECTURE §15 names it as a separate operation.
    whole_token = re.compile(rf"^(?:\^{body}\$|{body})$")
    offenders = []
    for sub in ("well_viewer", "services", "datasets", "util", "widgets"):
        for path in sorted((root / sub).rglob("*.py")):
            if "__pycache__" in path.parts or path.name == "well_token.py":
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and whole_token.match(node.value)):
                    offenders.append(
                        f"{path.relative_to(root).as_posix()}:{node.lineno} — {node.value!r}")
    assert not offenders, (
        "Whole-token well regex outside util/well_token.py (ARCHITECTURE §15). "
        "Use `canonical_well_label` / `is_valid_well_name` so this module and "
        "the pipeline agree on what a well is:\n  " + "\n  ".join(offenders)
    )
