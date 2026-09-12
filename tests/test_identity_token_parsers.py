"""Regression guards for the identity / token-parser layer (PR 1).

Everything here pins a rule that was broken at the time it was written, and
each rule is one a *later* fix would otherwise have to re-derive: an image
address that resolves to the wrong frame, or a schema slot that names the wrong
column, corrupts every number computed downstream of it.

The five rules:

1. A role suffix is stripped exactly once. The classifier used to pass its own
   result through ``Path(...).stem`` a second time, which truncates at the last
   dot left in the *field* text — and schema tokens legitimately carry dots
   (``1.5h``).
2. There is one filename classifier. ``image_resolver.classify_filename_kind``
   is a vocabulary wrapper over ``image_identity.classify_filename_role``, not a
   second opinion; the two used to disagree about ``phase``.
3. A numeric identity token of ``0`` is a token, not an absence.
4. Slot inference asks the strictest predicate first and breaks ties leftmost,
   so the FOV column cannot answer "is this the well column?".
5. A name that reads as brightfield does not take the segmentation role while
   another channel is available.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from util import fov as fov_mod
from util import timepoint as tp_mod
from util.channel_roles import channel_tokens_from_roles, default_channel_roles
from util.schema_infer import infer_schema
from review.data.frame_load import match_image_ref
from review.data.image_identity import (
    PipelineSchema,
    Role,
    classify_filename_role,
    parse_image_filename,
)
from review.data.image_resolver import (
    classify_filename_kind,
    normalize_numeric_token,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── 1. dotted tokens survive suffix stripping ────────────────────────────────

@pytest.mark.parametrize(
    "name,expect_role,expect_stem",
    [
        ("A1_f1_1.5h_w2_labels.tif", Role.MASK, "A1_f1_1.5h_w2"),
        ("A1_f1_1.5h_w2_overlay.png", Role.OVERLAY, "A1_f1_1.5h_w2"),
        ("A1_f1_1.5h_w2.tif", None, "A1_f1_1.5h_w2"),
        ("A1_f1_1.5h_w2", None, "A1_f1_1.5h_w2"),
        ("A1_f1_T01_w2_labels.tif", Role.MASK, "A1_f1_T01_w2"),
    ],
)
def test_role_suffix_is_stripped_once(name, expect_role, expect_stem):
    role, stem = classify_filename_role(name, fluor_token="w2")
    assert (role, stem) == (expect_role, expect_stem)


def test_dotted_timepoint_reaches_the_parsed_fields():
    """The truncation did not merely shorten a string: it dropped schema slots.

    ``A1_f1_1.5h_w2`` split on ``_`` is four fields; ``A1_f1_1`` is three, so
    the channel slot went missing and the timepoint read as ``1`` — colliding
    with the genuine ``1h`` frame on a first-hit-wins lookup.
    """
    schema = PipelineSchema(fields=("well", "fov", "timepoint", "channel"))
    parsed = parse_image_filename(
        "A1_f1_1.5h_w2_labels.tif", schema=schema, fluor_token="w2",
    )
    assert parsed.fields["timepoint"] == "1.5h"
    assert parsed.fields["channel"] == "w2"
    assert parsed.tp == "1.5"
    assert parsed.ext == ".tif"


# ── 2. one classifier, two vocabularies ──────────────────────────────────────

_ROLE_TO_LEGACY_KIND = {
    Role.MASK: "mask",
    Role.OVERLAY: "overlay",
    Role.SMFISH: "smfish",
    Role.PHASE: "phase",
    Role.FLUOR_TOPHAT: "tophat",
    None: "fluor_raw",
}

_CORPUS = [
    "A01_f1_T01_w2.tif",
    "A01_f1_T01_w2_labels.tif",
    "A01_f1_T01_w2_labels.png",
    "A01_f1_T01_w2_overlay.png",
    "A01_f1_T01_w2_smfish.tif",
    "A01_f1_T01_w2_smfish_w2.tif",
    "A01_f1_T01_w2_tophat.tif",
    "A01_f1_T01_w2_tophat_w2.tif",
    "A01_f1_T01_w2_phase.tif",
    "A01_f1_1.5h_w2_labels.tif",
    "._A01_f1_T01_w2.tif",
    "A01_f1_T01_w2.tiff",
]


@pytest.mark.parametrize("name", _CORPUS)
def test_the_two_classifier_spellings_agree(name):
    role, role_stem = classify_filename_role(name, fluor_token="w2")
    kind, kind_stem = classify_filename_kind(name, fluor_token="w2")
    assert kind == _ROLE_TO_LEGACY_KIND[role]
    assert kind_stem == role_stem


def test_a_channel_scoped_phase_output_is_not_a_raw_fluor_frame():
    assert classify_filename_kind(
        "A01_f1_T01_phase_w2.tif", fluor_token="w2",
    )[0] == "phase"


def test_a_channel_named_phase_is_not_a_phase_role_suffix():
    """``_phase`` is the one role suffix that is also a plausible *channel
    name*. A dataset whose last schema slot is a channel called ``phase``
    writes ``A01_1_phase.tif``, which is indistinguishable from a bare
    phase-role output — so only the channel-scoped spelling is matched, and
    this file stays an ordinary raw frame."""
    assert classify_filename_kind("A01_1_phase.tif", fluor_token="phase") == (
        "fluor_raw", "A01_1_phase",
    )


# ── 3. zero is a token ───────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [0, 0.0, "0"])
def test_numeric_token_zero_is_not_absent(value):
    assert normalize_numeric_token(value) == "0"


@pytest.mark.parametrize("value", [0, 0.0, "0"])
def test_fov_zero_sorts_as_a_number(value):
    unparseable, number, _token = fov_mod.sort_key(value)
    assert unparseable is False
    assert number == 0.0


def test_fov_and_timepoint_sort_keys_agree_on_zero_and_absent():
    """Two modules answering "is this token readable?" must not disagree."""
    for value in (0, 0.0, "0", None, "", 1, "10"):
        assert fov_mod.sort_key(value)[0] == tp_mod.sort_key(value)[0], value


def test_fov_zero_sorts_first_not_last():
    assert sorted([1, 2, 10, 0], key=fov_mod.sort_key) == [0, 1, 2, 10]


# ── 4. slot inference ────────────────────────────────────────────────────────

def test_fov_slot_does_not_take_the_well_role():
    """``f01``–``f12`` are valid 96-well labels (row F), so the well predicate
    answers yes for every FOV token. Both slots then score 1.0, and the tie used
    to go to the rightmost — the FOV slot."""
    stems = [
        f"EXP1_{c}_{w}_{f}_{t}"
        for w in ("A01", "A02", "B01")
        for c in ("w1", "w2")
        for f in ("f01", "f02")
        for t in ("t1", "t2")
    ]
    inferred = infer_schema(stems)
    assert inferred is not None
    assert inferred.fields == [
        "experiment", "channel", "well", "fov", "timepoint",
    ]
    assert inferred.channels == ["w1", "w2"]
    assert inferred.confident is True


def test_channel_slot_is_not_stolen_from_a_narrow_well_slot():
    """Fewer wells than channels: the well slot is the narrower one, so a
    fewest-distinct channel pick would take it if it were still unclaimed."""
    stems = [
        f"{w}_{c}_f01_t1"
        for w in ("A01", "A02")
        for c in ("w1", "w2", "w3")
    ]
    inferred = infer_schema(stems)
    assert inferred is not None
    assert inferred.fields[0] == "well"
    assert inferred.channels == ["w1", "w2", "w3"]


def test_a_bare_number_slot_is_left_unclaimed():
    """A slot of bare integers is equally good evidence for a field number, a
    timepoint index and a site number, and this module's contract is to leave
    what it cannot identify as ``ignore`` rather than pre-fill the Analyze form
    with a confident wrong guess."""
    stems = [f"A01_w1DAPI_{h}" for h in ("0", "24", "48")]
    inferred = infer_schema(stems)
    assert inferred is not None
    assert "fov" not in inferred.fields


# ── 5. channel roles ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "names,expect_nuclear,expect_phase",
    [
        # Reads as both a far-red stain and a brightfield frame. The phase
        # reading has to win: phase_role_conflicts compares against
        # phase_token, so claiming the name as nuclear left that guard with
        # nothing to compare and it returned no conflicts.
        (["NIR_BF", "GFP"], "GFP", "NIR_BF"),
        (["brightfield_NIR", "DAPI"], "DAPI", "brightfield_NIR"),
        # "trans" unanchored matched "transfected", so a fluorescent readout
        # was classified as phase and skipped background correction.
        (["GFP_transfected", "DAPI"], "DAPI", ""),
        (["DAPI", "GFP", "DIC"], "DAPI", "DIC"),
        (["BFP", "DAPI"], "DAPI", ""),
        # The ImageXpress prefix spelling, which this repo's own default
        # tokens use. ``\bbf\b`` matched neither this nor "NIR_BF".
        (["w1BF", "w2GFP"], "w2GFP", "w1BF"),
        # A run-on that merely starts like the hint must not be phase: a
        # transfection marker excluded from background correction is a
        # measurement made on the wrong pixels.
        (["GFPtrans", "DAPI"], "DAPI", ""),
    ],
)
def test_phase_and_nuclear_roles(names, expect_nuclear, expect_phase):
    roles = default_channel_roles(names)
    tokens = channel_tokens_from_roles([(n, roles[n]) for n in names])
    assert tokens.nuclear_token == expect_nuclear
    assert tokens.phase_token == expect_phase


# ── 6. an unspecified axis refuses rather than guesses ───────────────────────

def test_empty_timepoint_spanning_several_refuses():
    images = {("1", "T01"): "a", ("1", "T02"): "b", ("2", "T01"): "c"}
    assert match_image_ref(images, "1", "") == (None, "ambiguous_tp")
    assert match_image_ref(images, "", "T01") == (None, "ambiguous_fov")


def test_a_single_timepoint_dataset_still_resolves():
    images = {("1", "T01"): "a"}
    ref, kind = match_image_ref(images, "1", "")
    assert ref == "a" and kind == "normalized"


# ── 7. the timepoint tolerance is the shared one ─────────────────────────────

_TP_NAME_RE = re.compile(r"\b(?:tp|tps|tp_col|timepoint|target_t|col_tp|rec_tp|pt_t|t)\b")


def test_no_absolute_timepoint_tolerance_in_features():
    """A combo formats timepoints with ``:.4g``, so 2/3 h round-trips ~3e-5 off
    its original float — outside an absolute ``1e-6`` and inside the shared
    relative one. With both spellings live, one tab drew grouped bars for a
    fractional timepoint and blanked the per-well ones beside them.

    The scan is deliberately wider than the sites that were fixed: a guard
    written around the four variable names the first sweep happened to touch
    reports "clean" for the six it missed, which is how a half-swept predicate
    survives its own regression test. Any ``1e-6`` on a line that also names a
    timepoint-ish variable counts.
    """
    offenders = []
    for path in sorted((REPO_ROOT / "review" / "features").rglob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if "1e-6" in line and _TP_NAME_RE.search(line):
                rel = path.relative_to(REPO_ROOT)
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "timepoint comparisons must go through review.domain.tp_match:\n"
        + "\n".join(offenders)
    )
