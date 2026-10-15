"""Entry-boundary parsing and numeric validation for the Analyze form.

These read the form's text fields and turn them into run options, or into the
human-readable errors that block a run before it starts — a bad radius / FFT /
mesh / QC / Cellpose / StarDist value is refused up front instead of being
silently dropped into a default. Qt-free: they take and return plain strings
and dicts.

**Every numeric field reaches this module as the raw text the user typed.**
That is the whole point: a validator that is handed an already-coerced number
cannot tell ``5oo`` from a deliberate blank, so ``_collect_run_options`` keeps
the text and the coercion happens once, in
``services.pipeline_service.build_pipeline_args``, after this function has had
its say. A field that is coerced on its way *into* ``opts`` is structurally
unvalidatable, which is how ``qc_min_area``, the four Cellpose parameters and
the minimum nucleus area came to accept a typo as "no bound at all".

``analyze.tab`` and ``analyze.form`` both re-export ``_numeric_option_errors``
so every existing import path still resolves.
"""

from __future__ import annotations

import math

from analyze.domain.nanowell_fields import nanowell_option_errors
from pipeline.field_quality_record import FOCUS_SCORE_CEILING


# ── Entry-boundary parsing + numeric validation ─────────────────────────────
#
# These read the form's text fields, so they live beside the widgets rather than
# in the tab that hosts them. ``analyze.tab`` re-exports
# ``_numeric_option_errors`` so its old import path still resolves.

#: Whole-number fields where ``0`` is a meaningful value ("off" / "no bound"):
#: key -> label.
_INT_FIELDS = {
    "tophat_radius_nir": "Nuclear tophat radius",
    "tophat_radius_fluor": "Fluor tophat radius",
    "fft_small_nir": "Nuclear FFT small-object px",
    "fft_small_fluor": "Fluor FFT small-object px",
    "tf_threads": "TF threads",
    "workers": "Workers",
    "remote_batch_size": "Remote batch size",
    "stardist_tile_px": "StarDist tile px",
}

#: Whole-number fields that only matter under one segmentation method, so a
#: stale value behind an unselected routine cannot block a run it has no effect
#: on. This is the ``gate`` column of :data:`_FLOAT_FIELDS`, for the int table:
#: key -> the ``segmentation_method`` prefix that turns it on. Without it a
#: leftover ``-4`` in the StarDist tile field refused a Cellpose run, naming a
#: control the user could not see from the routine they had chosen.
_METHOD_GATED_INT_FIELDS = {
    "stardist_tile_px": "stardist",
}

#: Whole-number fields that must be strictly positive: key -> label. Empty
#: today, and kept because the ``≥ 1`` half of the int scan is the shape a
#: future field will want.
#:
#: ``min_nucleus_area_px`` is *not* here. Its widget lives only on the
#: ``stardist_seeded_watershed_cell`` page of the segmentation stack, only that
#: routine reads it, and ``_segmentation_validation_errors`` already refuses a
#: non-positive value inside ``if method == "stardist_seeded_watershed_cell"``
#: — earlier in ``_validate_run_request`` than this scan runs. Listing it here
#: as well gave one field two rules with different wording, and the ungated one
#: refused a Cellpose run over a control that routine does not show.
_POSITIVE_INT_FIELDS: dict[str, str] = {}

#: Float fields, with the bound each one carries and the run option that has to
#: be set for the field to matter: key -> (label, minimum, maximum, gate).
#: ``gate`` is the ``opts`` key whose truthiness turns the field on; ``None``
#: means the field always applies. A field behind an unticked gate is not
#: validated, because its value is never read either.
_FLOAT_FIELDS = {
    "qc_min_area":                 ("QC minimum area", 0.0, None, "qc_remove_debris"),
    "qc_max_area":                 ("QC maximum area", 0.0, None, "qc_remove_debris"),
    "qc_min_solidity":             ("QC minimum solidity", 0.0, 1.0, "qc_remove_debris"),
    # Gated on the measurement, not on a cut of its own: the box is blank by
    # default and a blank box is "no cut stated", which every numeric field
    # here already treats as unset.
    #
    # The upper bound is the estimator's own ceiling (``FOCUS_SCORE_CEILING``,
    # measured 0.8333), so the validator now refuses a cut that no cell could
    # ever clear instead of accepting it and leaving the user with a plate
    # whose every cell is excluded and nothing on screen saying why. It was
    # 2.0, justified by a sentence about the discrete Laplacian of an ideal
    # step -- true of the estimator that has since been deleted, and roughly
    # 2.4x above anything the one that replaced it can return. Rejecting the
    # value IS the feedback the form was missing; no band of "aggressive but
    # legal" is invented here, because where that band sits depends on the
    # objective and the pixel size, which is the whole reason the cut is a
    # number about a microscope.
    "focus_min":                   ("Out-of-focus threshold", 0.0,
                                    FOCUS_SCORE_CEILING, "measure_focus"),
    "cellpose_diameter":           ("Cellpose diameter", 0.0, None, None),
    "cellpose_flow_threshold":     ("Cellpose flow threshold", None, None, None),
    "cellpose_cellprob_threshold": ("Cellpose cell-probability threshold", None, None, None),
}

#: The Cellpose parameters above are only read when a routine that runs
#: Cellpose is selected; ``segmentation_method`` is not a boolean, so it gets
#: its own gate rather than being forced into the table's ``gate`` column.
_CELLPOSE_FIELDS = frozenset(
    k for k in _FLOAT_FIELDS if k.startswith("cellpose_")
)

#: Which routines read each Cellpose parameter. The thresholds are general
#: Cellpose's alone; the diameter is read by the label-free preset too, so a
#: typo there must block that run rather than fall back to auto unannounced.
_CELLPOSE_FIELD_METHODS = {
    "cellpose_diameter": ("cellpose", "cellpose_labelfree"),
}

#: The two StarDist detection thresholds. Blank or ``auto`` means "use the
#: threshold the pretrained model ships with"; anything else must read as a
#: number, and a number in ``[0, 1]``. The rule is
#: ``pipeline.stardist.parse_threshold``'s, restated here rather than imported
#: so the Analyze form does not drag scipy/skimage into its import graph;
#: ``tests/test_stardist_threshold_rule_is_one_rule.py`` holds the two together.
_THRESHOLD_FIELDS = {
    "stardist_prob_thresh": "StarDist probability threshold",
    "stardist_nms_thresh": "StarDist NMS threshold",
}


def _raw(opts: dict, key: str) -> str:
    """The text a field carries, stripped. Blank means "pipeline default"."""
    return str(opts.get(key, "") or "").strip()


def _as_float(text: str) -> float | None:
    """*text* as a float, or ``None`` when it does not read as a number."""
    try:
        return float(text)
    except ValueError:
        return None


def _non_finite_error(label: str, raw: str, value: float) -> str | None:
    """The error for ``nan`` / ``inf``, or ``None`` when *value* is ordinary.

    ``float()`` accepts ``"nan"``, ``"inf"`` and ``"-inf"``, and a bounds check
    written as ``value < low`` / ``value > high`` then waves ``nan`` through:
    every comparison against it is false, so it passes a floor, a ceiling and
    both at once. It reached the pipeline as a real setting — a NaN QC area
    bound excludes every cell, a NaN Cellpose threshold makes every candidate
    compare false — and the form said nothing on the way past. ``inf`` passes
    any check that only has a floor, for the same structural reason.
    """
    if math.isnan(value):
        return (f"{label} must be a real number ({raw!r} is not one — it "
                "compares false against every bound, so nothing downstream "
                "would reject it either). Leave it blank for the default.")
    if math.isinf(value):
        return (f"{label} must be a finite number (got {raw!r}). Leave it "
                "blank for the default.")
    return None


def _int_field_errors(opts: dict) -> list[str]:
    """Whole-number field errors, for both the ``≥ 0`` and ``≥ 1`` tables."""
    errors: list[str] = []
    method = str(opts.get("segmentation_method", "") or "")
    for table, floor in ((_INT_FIELDS, 0), (_POSITIVE_INT_FIELDS, 1)):
        for key, label in table.items():
            needs = _METHOD_GATED_INT_FIELDS.get(key)
            if needs is not None and not method.startswith(needs):
                continue
            raw = _raw(opts, key)
            if not raw:
                continue
            try:
                value = int(raw)
            except ValueError:
                errors.append(f"{label} must be a whole number (got {raw!r}).")
                continue
            if value < floor:
                errors.append(f"{label} must be a whole number ≥ {floor} (got {raw!r}).")
    return errors


def _mesh_errors(opts: dict) -> list[str]:
    errors: list[str] = []
    for key, label, allow_zero in (("mesh_ratio", "Mesh tile ratio", False),
                                   ("mesh_sigma", "Mesh sigma", True)):
        raw = _raw(opts, key)
        if not raw:
            continue
        try:
            val = float(raw)
        except ValueError:
            errors.append(f"{label} must be a number (got {raw!r}).")
            continue
        bad = _non_finite_error(label, raw, val)
        if bad:
            errors.append(bad)
            continue
        if allow_zero and val < 0:
            errors.append(f"{label} must be ≥ 0.")
        elif not allow_zero and val <= 0:
            errors.append(f"{label} must be > 0.")   # 0 → downstream div-by-zero
    return errors


def _float_field_errors(opts: dict) -> list[str]:
    """Errors for the QC bounds and the Cellpose parameters.

    Each field is checked only when the control that reads it is on, so a
    leftover value behind an unticked "Remove debris" or a non-Cellpose routine
    cannot block a run it has no effect on.
    """
    errors: list[str] = []
    method = str(opts.get("segmentation_method", "") or "")
    for key, (label, low, high, gate) in _FLOAT_FIELDS.items():
        if (key in _CELLPOSE_FIELDS
                and method not in _CELLPOSE_FIELD_METHODS.get(key, ("cellpose",))):
            continue
        if gate is not None and not opts.get(gate):
            continue
        raw = _raw(opts, key)
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            errors.append(f"{label} must be a number (got {raw!r}).")
            continue
        bad = _non_finite_error(label, raw, value)
        if bad:
            errors.append(bad)
            continue
        if low is not None and value < low:
            errors.append(f"{label} must be ≥ {low:g} (got {raw!r}).")
        elif high is not None and value > high:
            errors.append(f"{label} must be ≤ {high:g} (got {raw!r}).")
    if opts.get("qc_remove_debris"):
        mn = _as_float(_raw(opts, "qc_min_area"))
        mx = _as_float(_raw(opts, "qc_max_area"))
        # ``0`` in the maximum-area box means *no upper bound*, which is how
        # the pipeline reads it: ``build_pipeline_args`` emits
        # ``--qc_max_area`` only when the value is above zero. Both boxes are
        # built holding the text ``"0"``, not a placeholder, so comparing a
        # typed minimum against an untouched maximum refused the form's own
        # shipped default — a run that works on main, blocked by a message
        # whose remedy is to blank a box that already reads 0.
        #
        # Both unreadable values are already reported above as "must be a
        # number"; comparing them would only restate that in worse words.
        if mn is not None and mx is not None and mx > 0 and mn > mx:
            errors.append("QC minimum area must be ≤ maximum area.")
    return errors


def _threshold_errors(opts: dict) -> list[str]:
    """Errors for the two StarDist detection thresholds.

    ``parse_threshold`` answers ``None`` for two different inputs — the ``-1``
    sentinel a prior run recorded, and text it could not read at all — so the
    difference between them is decided here by asking whether the text reads as
    a number. Only the second is a user error; the first means "the model's own
    default", exactly as blank and ``auto`` do.
    """
    errors: list[str] = []
    if not str(opts.get("segmentation_method", "") or "").startswith("stardist"):
        return errors
    for key, label in _THRESHOLD_FIELDS.items():
        raw = _raw(opts, key)
        if not raw or raw.lower() == "auto":
            continue
        try:
            value = float(raw)
        except ValueError:
            errors.append(
                f"{label} must be a number between 0 and 1 — leave it blank (or "
                f"type 'auto') for the model's own default (got {raw!r}).")
            continue
        bad = _non_finite_error(label, raw, value)
        if bad:
            errors.append(bad)
            continue
        if value > 1.0:
            errors.append(
                f"{label} must be in [0, 1] (got {raw!r}). The pipeline rejects "
                "an out-of-range threshold, and it would do so mid-run.")
        elif value < 0.0:
            # ``parse_threshold`` reads *any* negative as "use the model's own
            # default", so a typed -0.5 does not fail — it silently segments
            # with a threshold the operator did not choose and never sees.
            #
            # No exception for the CLI's ``-1`` sentinel, because nothing puts
            # one in this box: ``_restore_stardist_thresholds`` writes ``""``
            # for any recorded negative, and its own docstring says why —
            # "writing -1.0 into the box would read as a real threshold". A
            # negative here was typed, and a typed negative is a typo.
            errors.append(
                f"{label} must be in [0, 1] (got {raw!r}). A negative value is "
                "read as \"use the model's own default\", which is what blank "
                "or 'auto' says without looking like a threshold.")
    return errors


def _numeric_option_errors(opts: dict) -> list[str]:
    """Return human-readable errors for out-of-range / unparseable numeric run
    options. Empty/blank means "use the pipeline default" and is allowed.

    Every value it reads is the raw text of a form field. Coercing a field on
    its way into ``opts`` puts it out of this function's reach entirely, which
    is not a smaller bug than an unvalidated one — it is the same bug with the
    evidence removed.
    """
    return (_int_field_errors(opts)
            + _mesh_errors(opts)
            + _float_field_errors(opts)
            + _threshold_errors(opts)
            + nanowell_option_errors(opts))
