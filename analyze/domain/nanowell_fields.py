"""The Analyze form's Nanowells section, without Qt: what its fields mean and what makes them wrong.

Like the rest of ``analyze/domain`` this reads the **raw text** the form's fields hold, so a typo is told
from a deliberate blank, and returns plain data. The widgets are ``analyze.nanowell_section``; the argv is
built by ``services.pipeline_service`` from the same option keys.

The option keys are the pipeline's destinations without their prefix dropped (``nanowell_format`` and so on),
so a job's ``opts`` and a sidecar's ``nanowell`` block name the same things.
"""

from __future__ import annotations

import math

#: The plate formats the pipeline knows (``pipeline.nanowell_run.PLATE_FORMATS``), as (option value, label).
#: Restated here, with a test holding the two together, so the form does not import the pipeline's scipy stack.
PLATE_FORMAT_CHOICES = (("100um", "100 µm nanowells"), ("50um", "50 µm nanowells"))

#: Field defaults, shown as placeholders: a blank field sends no flag and the pipeline's own default applies.
DEFAULTS = {
    "nanowell_cell_diameter_um": "15",
    "nanowell_margin_um": "",
    "nanowell_square_edge_deg": "0",
    "nanowell_occluded_below": "0.5",
    "nanowell_clipped_below": "0.5",
}

#: Key -> (label, minimum, maximum, minimum is allowed). Mirrors ``pipeline.cli_args.nanowell_options_from_args``.
_NUMERIC = {
    "nanowell_cell_diameter_um": ("Cell diameter", 0.0, None, False),
    "nanowell_margin_um": ("Margin", 0.0, None, True),
    "nanowell_square_edge_deg": ("Square edge angle", -45.0, 45.0, True),
    "nanowell_occluded_below": ("Occlusion threshold", 0.0, 1.0, False),
    "nanowell_clipped_below": ("Clipping threshold", 0.0, 1.0, False),
}


def nanowell_enabled(opts: dict) -> bool:
    return bool(str(opts.get("nanowell_format") or "").strip())


def nanowell_option_errors(opts: dict, *, need_phase: bool = False) -> list[str]:
    """Human-readable errors for the nanowell options; nothing when the section is off.

    A field behind an off section is not validated, because it is not sent. *need_phase* asks for the Phase
    channel to be marked on the form: not asked while run-time detection is about to replace the form's
    channels (the run's own argument check still refuses a missing one, by name).
    """
    if not nanowell_enabled(opts):
        return []
    errors = []
    known = {value for value, _label in PLATE_FORMAT_CHOICES}
    if str(opts["nanowell_format"]).strip() not in known:
        errors.append(f"Nanowell plate format {opts['nanowell_format']!r} is not one of {sorted(known)}.")
    if need_phase and not str(opts.get("phase_token") or "").strip():
        errors.append("Nanowell mode needs a Phase channel: tick Phase on the channel the tiles are imaged in.")
    for key, (label, low, high, low_ok) in _NUMERIC.items():
        raw = opts.get(key)
        if raw is None or not str(raw).strip():
            continue
        try:
            value = float(str(raw).strip())
        except ValueError:
            errors.append(f"{label} must be a number (got {raw!r}).")
            continue
        if not math.isfinite(value):
            errors.append(f"{label} must be a finite number (got {raw!r}).")
        elif value < low or (value == low and not low_ok) or (high is not None and value > high):
            bound = f"{low:g} to {high:g}" if high is not None else f"above {low:g}" if not low_ok else f"{low:g} or more"
            errors.append(f"{label} must be {bound} (got {raw!r}).")
    return errors
