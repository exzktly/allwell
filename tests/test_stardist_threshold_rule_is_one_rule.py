"""The Analyze form and the pipeline agree on what a StarDist threshold is.

``pipeline.stardist.parse_threshold`` is the rule: blank, ``auto``, ``None`` or
a negative value (the CLI's ``-1`` sentinel) all mean *use the pretrained
model's own threshold*; anything else must be a number in ``[0, 1]``, and a
value outside that range is refused by raising rather than clamped.

``analyze_tab_fields._threshold_errors`` restates that rule instead of importing
it, because ``pipeline.stardist`` pulls numpy, scipy and skimage into whatever
imports it and the Analyze form deliberately keeps them out of its graph — the
pipeline runs as a subprocess. A restated rule is two rules the day one of them
changes, so this holds them together: for a corpus of typed values, the form
refuses exactly the inputs the pipeline would not accept.

``parse_threshold`` answers ``None`` for two different reasons — the sentinel,
and text it could not read — which is the ambiguity the form has to resolve. The
corpus names which is which.
"""
from __future__ import annotations

import pytest

from analyze_tab_fields import _numeric_option_errors

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


@pytest.mark.parametrize("typed", CORPUS)
def test_the_form_and_the_pipeline_refuse_the_same_thresholds(typed):
    assert _form_refuses(typed) == _pipeline_refuses(typed), (
        f"the Analyze form and pipeline.stardist.parse_threshold disagree about "
        f"{typed!r}: form refuses={_form_refuses(typed)}, "
        f"pipeline refuses={_pipeline_refuses(typed)}")


def test_the_corpus_covers_both_answers():
    """Guards the guard: a corpus that is all-accept or all-refuse proves
    nothing about the two rules matching."""
    verdicts = {_pipeline_refuses(t) for t in CORPUS}
    assert verdicts == {True, False}
