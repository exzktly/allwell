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

``analyze_tab`` and ``analyze_tab_form`` both re-export ``_numeric_option_errors``
so every existing import path still resolves.
"""

from __future__ import annotations


# ── Entry-boundary parsing + numeric validation ─────────────────────────────
#
# These read the form's text fields, so they live beside the widgets rather than
# in the tab that hosts them. ``analyze_tab`` re-exports
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

#: Whole-number fields that must be strictly positive: key -> label. The
#: pipeline refuses ``--min_nucleus_area_px 0`` outright, and
#: ``build_pipeline_args`` floors it at 1, so a typed ``0`` would silently
#: become a different number from the one on screen.
_POSITIVE_INT_FIELDS = {
    "min_nucleus_area_px": "Minimum nucleus area",
}

#: Float fields, with the bound each one carries and the run option that has to
#: be set for the field to matter: key -> (label, minimum, maximum, gate).
#: ``gate`` is the ``opts`` key whose truthiness turns the field on; ``None``
#: means the field always applies. A field behind an unticked gate is not
#: validated, because its value is never read either.
_FLOAT_FIELDS = {
    "qc_min_area":                 ("QC minimum area", 0.0, None, "qc_remove_debris"),
    "qc_max_area":                 ("QC maximum area", 0.0, None, "qc_remove_debris"),
    "qc_min_solidity":             ("QC minimum solidity", 0.0, 1.0, "qc_remove_debris"),
    "cellpose_diameter":           ("Cellpose diameter", 0.0, None, None),
    "cellpose_flow_threshold":     ("Cellpose flow threshold", None, None, None),
    "cellpose_cellprob_threshold": ("Cellpose cell-probability threshold", None, None, None),
}

#: The Cellpose parameters above are only read when the general-Cellpose
#: routine is selected; ``segmentation_method`` is not a boolean, so it gets
#: its own gate rather than being forced into the table's ``gate`` column.
_CELLPOSE_FIELDS = frozenset(
    k for k in _FLOAT_FIELDS if k.startswith("cellpose_")
)

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


def _int_field_errors(opts: dict) -> list[str]:
    """Whole-number field errors, for both the ``≥ 0`` and ``≥ 1`` tables."""
    errors: list[str] = []
    for table, floor in ((_INT_FIELDS, 0), (_POSITIVE_INT_FIELDS, 1)):
        for key, label in table.items():
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
    cellpose_on = str(opts.get("segmentation_method", "") or "") == "cellpose"
    for key, (label, low, high, gate) in _FLOAT_FIELDS.items():
        if key in _CELLPOSE_FIELDS and not cellpose_on:
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
        if low is not None and value < low:
            errors.append(f"{label} must be ≥ {low:g} (got {raw!r}).")
        elif high is not None and value > high:
            errors.append(f"{label} must be ≤ {high:g} (got {raw!r}).")
    if opts.get("qc_remove_debris"):
        mn = _as_float(_raw(opts, "qc_min_area"))
        mx = _as_float(_raw(opts, "qc_max_area"))
        # Both unreadable values are already reported above as "must be a
        # number"; comparing them would only restate that in worse words.
        if mn is not None and mx is not None and mn > mx:
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
        if value > 1.0:
            errors.append(
                f"{label} must be in [0, 1] (got {raw!r}). The pipeline rejects "
                "an out-of-range threshold, and it would do so mid-run.")
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
            + _threshold_errors(opts))
