"""Where the phase/brightfield hints are allowed to match (audit C8).

A channel classified ``phase`` skips **both** flat-field and background
subtraction, and the writer then saves it under the usual ``_tophat`` suffix —
so a fluorescence channel misread as phase is quantified from raw frames while
every other channel is corrected, and the filename gives nothing downstream a
way to tell. The hint regex therefore has to be exact about *where* each
alternative may match.

``trans`` and ``bf`` were bounded earlier; ``dic`` was left as a bare substring,
which is inside ``Ca-Indicator``, ``Indicator`` and ``Predicted``. Fixing one
alternative and not its siblings is the half-sweep CLAUDE.md §7.5 #5 warns
about, so the whole vocabulary is enumerated here — including the two long words
that deliberately stay unbounded, so that choice is recorded as a choice.
"""

from __future__ import annotations

import pytest

from util.channel_roles import default_channel_roles, guess_channel_role

PHASE = [
    "DIC", "dic", "w1DIC", "DIC_1", "NIR_DIC", "DIC 20x",
    "BF", "w1BF", "Cy5_BF", "NIR_BF",
    "Trans", "Transmitted", "trans_light",
    "Phase", "phase_contrast", "Phase2",
    "Brightfield", "Brightfield1", "bright_field", "bright-field",
]

NOT_PHASE = [
    # The audit's measured misclassifications: all contain "dic" or "trans".
    "Ca-Indicator", "Indicator", "pH-Indicator", "Predicted",
    "Transferrin647", "TransGFP", "GFP_transfected",
    # Bounded on the right too, which is what keeps these out.
    "DICOM", "BFP", "dictyostelium",
    # Ordinary fluorescence names.
    "GFP", "DAPI", "mCherry", "Cy5", "YFP", "TexasRed",
]


@pytest.mark.parametrize("name", PHASE)
def test_a_phase_name_is_read_as_phase(name):
    assert guess_channel_role(name) == "phase", f"{name!r} did not read as phase"


@pytest.mark.parametrize("name", NOT_PHASE)
def test_a_fluorescence_name_is_never_read_as_phase(name):
    role = guess_channel_role(name)
    assert role != "phase", (
        f"{name!r} classified as phase — it would skip flat-field and background "
        f"correction and still be written with the _tophat suffix"
    )


def test_a_calcium_indicator_keeps_its_corrections_beside_a_real_phase_channel():
    """The whole point, at the level the pipeline actually asks the question."""
    roles = default_channel_roles(["DAPI", "Ca-Indicator", "DIC"])
    assert roles["Ca-Indicator"] == "fluor"
    assert roles["DIC"] == "phase"
    assert roles["DAPI"] == "nuclear"


def test_the_secondary_nuclear_tier_is_deliberately_left_alone():
    """``iRFP670`` still reads as a secondary nuclear candidate, on purpose.

    The audit flagged ``_NUCLEAR_SECONDARY_RE`` for the same unbounded-substring
    shape, and it is not fixed here because the two are not the same mistake.
    ``mRFP``, ``TagRFP`` and ``iRFP`` are RFP-family stains, which is exactly
    what this tier is for, so a left boundary would reject the commonest correct
    spellings. And the cost is not comparable: this tier only proposes which
    channel segmentation runs on, it is overridden outright by any strong
    nuclear stain, and the Analyze form shows it as an editable choice — where a
    phase misclassification silently changes the numbers in the CSV. Pinned so
    the decision is visible rather than looking like an oversight.
    """
    assert guess_channel_role("iRFP670") == "nuclear"
    roles = default_channel_roles(["DAPI", "iRFP670", "GFP"])
    assert roles["DAPI"] == "nuclear"
    assert roles["iRFP670"] == "fluor"
