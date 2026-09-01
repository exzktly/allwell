"""One spelling for the words every plot title and error-band control uses.

``"SEM" if use_sem else "SD"`` was written out at **thirteen** call sites —
eight of them assigning to ``band_lbl``, which is what a grep for that name
found, and five more that do not: a toggle's text, two button captions, an
events-panel title and the Saved Plots summary line. §7.5 #4 exactly — the
first count described the search rather than the codebase, and the guard is
what produced the real one. The ``t = …`` clause was
written at three sites: the two branches of a helper private to the bar
renderer, and the Distribution title, which had never gone near that helper. Both
are *vocabulary*: every plot title that carries an error band says the same two
words, and a figure whose title says SD over a band drawn as SEM is the silent
substitution §7 of ``CLAUDE.md`` is about. Eight copies is eight places for that
to drift, and the ninth caller — the Response tab, which had to match the others
— is what made the count the problem rather than the observation.

They live in ``data/metric_labels.py`` now, beside ``fraction_title`` and
``threshold_qualifier``, which are the same kind of thing. This guard is what
notices a tenth copy: §7.5 #5's rule that a sweep is only worth anything if
every sibling is converted, held against the tree rather than against the memory
of having done it.
"""

from __future__ import annotations

import ast

from tests._repo_scan import parsed_sources

#: Files that may still spell the band label out, and why. Empty on purpose —
#: an exemption here is a claim that some caller needs its own vocabulary, and
#: none does.
_MAY_SPELL_IT_OUT: dict[str, str] = {}

#: Where the one spelling lives.
_HOME = "well_viewer/data/metric_labels.py"


def _is_the_band_expression(node: ast.AST) -> bool:
    """``<anything> if <anything> else <anything>`` yielding "SEM"/"SD".

    Matched on the *values* rather than on the condition or the variable name,
    because the copies differ in both — some read ``use_sem``, one takes it as a
    parameter — and it is the pair of words that is the vocabulary.
    """
    if not isinstance(node, ast.IfExp):
        return False
    ends = {getattr(node.body, "value", None), getattr(node.orelse, "value", None)}
    return ends == {"SEM", "SD"}


def test_the_band_label_is_spelled_in_exactly_one_place():
    offenders = []
    for path, tree in parsed_sources():
        rel = path.as_posix()
        if rel.endswith(_HOME) or any(rel.endswith(k) for k in _MAY_SPELL_IT_OUT):
            continue
        for node in ast.walk(tree):
            if _is_the_band_expression(node):
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        "a plot title or an error-band control spells the SD/SEM label itself "
        "instead of asking "
        f"``metric_labels.spread_label``: {offenders}")


def test_the_one_place_really_does_spell_it():
    """A guard that finds nothing because the helper has been renamed away
    would report a greener tree, not a converted one."""
    from well_viewer.data.metric_labels import spread_label

    assert spread_label(True) == "SEM"
    assert spread_label(False) == "SD"


def test_the_timepoint_clause_is_spelled_in_exactly_one_place():
    """``t = 24 h`` / ``t = [24, 48] h`` — the same argument, one layer up.

    Matched on the literal, because that is what a second copy would contain
    whatever it called its function.
    """
    offenders = []
    for path, tree in parsed_sources():
        rel = path.as_posix()
        if rel.endswith(_HOME):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                text = "".join(v.value for v in node.values
                               if isinstance(v, ast.Constant)
                               and isinstance(v.value, str))
                if text.startswith("t = ") and text.endswith(" h"):
                    offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        "a plot title builds its own ``t = … h`` clause instead of asking "
        f"``metric_labels.timepoint_title``: {offenders}")


def test_the_exemptions_still_name_files_that_exist():
    """An exemption for a file that has moved is a stale claim, and a stale
    claim in an allowlist is indistinguishable from one somebody added to make
    a failure go away."""
    known = {path.as_posix() for path, _ in parsed_sources()}
    stale = [k for k in _MAY_SPELL_IT_OUT
             if not any(p.endswith(k) for p in known)]
    assert not stale, stale
