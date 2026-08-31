"""The Analyze form and the pipeline agree on what a StarDist threshold is.

``pipeline.stardist.parse_threshold`` is the rule: blank, ``auto``, ``None`` or
a negative value (the CLI's ``-1`` sentinel) all mean *use the pretrained
model's own threshold*; anything else must be a number in ``[0, 1]``, and a
value outside that range is refused by raising rather than clamped.

``analyze.fields._threshold_errors`` restates that rule instead of importing
it, because ``pipeline.stardist`` pulls numpy, scipy and skimage into whatever
imports it and the Analyze form deliberately keeps them out of its graph — the
pipeline runs as a subprocess. A restated rule is two rules the day one of them
changes, so this holds them together.

**The relation is containment, not equality**, and the direction matters. The
form must refuse everything the pipeline would refuse — otherwise a run starts
and dies mid-plate. It must *also* refuse what the pipeline would silently turn
into the pretrained model's own threshold when the user plainly typed a number:
``parse_threshold`` answers ``None`` for text it could not read *and* for any
negative, so ``0,2`` and ``-0.5`` both segment at a sensitivity nobody chose,
with the number the user typed still on screen. Catching that at the boundary is
the whole point of validating the raw text, so an input the form refuses and the
pipeline tolerates is the design rather than a drift.

What would be a drift is the other direction, and that is what
:func:`test_the_pipeline_refuses_nothing_the_form_lets_through` asserts.
:data:`FORM_IS_STRICTER` enumerates the deliberate gap, each entry with the
reason it is there, so an accidental addition to it has to be written down.
"""
from __future__ import annotations

import pytest

from analyze.fields import _numeric_option_errors

stardist = pytest.importorskip("pipeline.stardist")


def _form_refuses(typed: str) -> bool:
    errs = _numeric_option_errors({
        "segmentation_method": "stardist_nuclei",
        "stardist_prob_thresh": typed,
    })
    return bool(errs)


def _pipeline_refuses(typed: str) -> bool:
    """Whether ``parse_threshold`` would reject *typed*, or silently answer
    "model default" for text that was meant to be a number."""
    try:
        value = stardist.parse_threshold(typed)
    except ValueError:
        return True                      # out of [0, 1]
    if value is not None:
        return False                     # a real threshold
    # ``None``: the sentinel when the text reads as a number, otherwise text
    # the pipeline could not read — which is what the form must not let past.
    text = typed.strip().lower()
    if not text or text == "auto":
        return False
    try:
        float(text)
    except ValueError:
        return True
    return False


# Deliberately includes the shapes a keyboard produces: a comma decimal
# separator, a letter O for a zero, words, and the recorded sentinel.
CORPUS = ["", "  ", "auto", "AUTO", "0", "0.2", "0.479", "1", "1.0",
          "-1", "-1.0", "-0.5", "1.5", "2", "100",
          "0,2", "O.2", "point three", "abc", ".", "1e-1", "nan"]


#: Values the form refuses and the pipeline tolerates, with why. Each is a
#: number the user typed that ``parse_threshold`` answers for silently, so the
#: run would segment at a threshold nobody chose and the form would still show
#: what was typed.
FORM_IS_STRICTER = {
    "-1":   "any negative reads as the model's own default; nothing puts the "
            "CLI's sentinel in this box (_restore_stardist_thresholds writes "
            "\"\" for a recorded negative, and says why)",
    "-1.0": "as -1",
    "-0.5": "as -1, and unmistakably a typo",
    "nan":  "float('nan') parses and compares false against every bound, so it "
            "passes a floor, a ceiling and both at once",
}


@pytest.mark.parametrize("typed", CORPUS)
def test_the_pipeline_refuses_nothing_the_form_lets_through(typed):
    """The direction that would start a run and kill it mid-plate."""
    if _pipeline_refuses(typed):
        assert _form_refuses(typed), (
            f"the form accepts {typed!r} and pipeline.stardist.parse_threshold "
            "does not — the run starts and fails after the first well")


@pytest.mark.parametrize("typed", CORPUS)
def test_where_the_form_is_stricter_it_is_stricter_on_purpose(typed):
    if _form_refuses(typed) and not _pipeline_refuses(typed):
        assert typed in FORM_IS_STRICTER, (
            f"the form refuses {typed!r} and the pipeline accepts it, and "
            "nothing says that is deliberate. Either the restated rule has "
            "drifted, or add it to FORM_IS_STRICTER with the reason")


@pytest.mark.parametrize("typed", sorted(FORM_IS_STRICTER))
def test_every_deliberate_gap_is_still_a_gap(typed):
    """Liveness: an entry that no longer describes a real difference is a lie.

    ``parse_threshold`` may well grow a refusal of its own — the ``nan`` entry
    in particular is one the pipeline ought to make — and when it does, this
    fails rather than leaving a roster nobody re-reads.
    """
    assert _form_refuses(typed), f"the form no longer refuses {typed!r}"
    assert not _pipeline_refuses(typed), (
        f"the pipeline now refuses {typed!r} too, so this entry no longer "
        f"describes a gap: {FORM_IS_STRICTER[typed]}")


def test_the_corpus_covers_both_answers():
    """Guards the guard: a corpus that is all-accept or all-refuse proves
    nothing about the two rules matching."""
    verdicts = {_pipeline_refuses(t) for t in CORPUS}
    assert verdicts == {True, False}
