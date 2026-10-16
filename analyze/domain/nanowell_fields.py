"""The Analyze form's Nanowells section, without Qt: what its fields mean and what makes them wrong.

Like the rest of ``analyze/domain`` this reads the **raw text** the form's fields hold, so a typo is told
from a deliberate blank, and returns plain data. The widgets are ``analyze.nanowell_section``; the argv is
built by ``services.pipeline_service`` from the same option keys.

The option keys are the pipeline's destinations without their prefix dropped (``nanowell_format`` and so on),
so a job's ``opts`` and a sidecar's ``nanowell`` block name the same things.
"""

from __future__ import annotations

import math
import re

#: The plate formats the pipeline knows (``pipeline.nanowell_run.PLATE_FORMATS``), as (option value, label), and
#: the custom one whose diameter and pitch the form gives (``CUSTOM_FORMAT``, tracker A7). Restated here, with a
#: test holding the two together, so the form does not import the pipeline's scipy stack.
CUSTOM_FORMAT = "custom"
PLATE_FORMAT_CHOICES = (("100um", "100 µm nanowells"), ("50um", "50 µm nanowells"),
                        (CUSTOM_FORMAT, "Custom size"))

#: How the array is found (``pipeline.nanowell_run.GEOMETRIES``), as (option value, label); the first is the default
#: and sends no flag, so a run that does not choose keeps the settings (and reuse key) it always had (tracker X35).
GEOMETRY_CHOICES = (("hex_circle", "Hexagonal lattice in a circle"), ("infer", "Infer from the image"))

#: ``--nanowell_scan`` spellings (``pipeline.nanowell_pattern_record.parse_scan``), the first the default.
SCAN_CHOICES = tuple(f"{corner}-{major}{'-serpentine' if serpentine else ''}"
                     for corner in ("top-left", "top-right", "bottom-left", "bottom-right")
                     for major in ("row", "column") for serpentine in (False, True))

#: The fields that describe the tile grid of a dataset with no acquisition record (tracker P15, A8).
GRID_KEYS = ("nanowell_grid", "nanowell_scan", "nanowell_overlap_percent", "nanowell_pixel_size_um")
CUSTOM_KEYS = ("nanowell_diameter_um", "nanowell_pitch_um")

#: Field defaults, shown as placeholders: a blank field sends no flag and the pipeline's own default applies.
DEFAULTS = {
    "nanowell_cell_diameter_um": "15",
    "nanowell_margin_um": "",
    "nanowell_square_edge_deg": "0",
    "nanowell_occluded_below": "0.5",
    "nanowell_clipped_below": "0.5",
    "nanowell_diameter_um": "",
    "nanowell_pitch_um": "",
    "nanowell_grid": "",
    "nanowell_overlap_percent": "",
    "nanowell_pixel_size_um": "",
}

#: Key -> (label, minimum, maximum, minimum is allowed). Mirrors ``pipeline.cli_args.nanowell_options_from_args``.
_NUMERIC = {
    "nanowell_cell_diameter_um": ("Cell diameter", 0.0, None, False),
    "nanowell_margin_um": ("Margin", 0.0, None, True),
    "nanowell_square_edge_deg": ("Square edge angle", -45.0, 45.0, True),
    "nanowell_occluded_below": ("Occlusion threshold", 0.0, 1.0, False),
    "nanowell_clipped_below": ("Clipping threshold", 0.0, 1.0, False),
    "nanowell_diameter_um": ("Nanowell diameter", 0.0, None, False),
    "nanowell_pitch_um": ("Lattice pitch", 0.0, None, False),
    "nanowell_overlap_percent": ("Tile overlap", 0.0, 99.999, True),
    "nanowell_pixel_size_um": ("Pixel size", 0.0, None, False),
}


#: The custom format's own numbers: shown, checked and sent only when the format is Custom.
_CUSTOM_KEYS = frozenset({"nanowell_diameter_um", "nanowell_pitch_um"})


def parse_grid(text: str) -> tuple[int, int] | None:
    """``"5x5"`` -> ``(5, 5)``; ``None`` when it is not ROWSxCOLUMNS (``pipeline.nanowell_pattern_record``'s rule)."""
    found = re.fullmatch(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*", str(text or ""))
    if not found or int(found.group(1)) < 1 or int(found.group(2)) < 1:
        return None
    return int(found.group(1)), int(found.group(2))


def _number(opts: dict, key: str) -> float | None:
    try:
        value = float(str(opts.get(key) or "").strip())
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _format_errors(opts: dict) -> list[str]:
    """A custom format needs both numbers, a pitch the nanowells do not fill; a known one takes neither."""
    if str(opts.get("nanowell_format") or "").strip() != CUSTOM_FORMAT:
        return []
    diameter, pitch = _number(opts, "nanowell_diameter_um"), _number(opts, "nanowell_pitch_um")
    if diameter is None or pitch is None:
        return ["A custom plate format needs the nanowell diameter and the lattice pitch."]
    if diameter > 0 and pitch > 0 and diameter >= 0.95 * pitch:
        return [f"A {diameter:g} µm nanowell on a {pitch:g} µm pitch would touch its neighbours: the pitch is the "
                "centre-to-centre distance and must exceed the diameter."]
    return []


def _grid_errors(opts: dict) -> list[str]:
    """The tile grid is all or nothing: a grid needs its overlap and pixel size, and they need a grid."""
    grid = str(opts.get("nanowell_grid") or "").strip()
    given = [k for k in ("nanowell_overlap_percent", "nanowell_pixel_size_um") if str(opts.get(k) or "").strip()]
    if not grid:
        return ["Tile overlap and pixel size describe the tile grid: give the grid too, or clear them."] if given else []
    errors = [] if parse_grid(grid) else [f"Tile grid must be ROWSxCOLUMNS, e.g. 5x5 (got {grid!r})."]
    scan = str(opts.get("nanowell_scan") or SCAN_CHOICES[0]).strip()
    if scan not in SCAN_CHOICES:
        errors.append(f"Scan order {scan!r} is not one this version knows.")
    for key, label in (("nanowell_overlap_percent", "the tile overlap"), ("nanowell_pixel_size_um", "the pixel size")):
        if key not in given:
            errors.append(f"A tile grid needs {label} as well.")
    return errors


def pattern_from_opts(opts: dict) -> dict | None:
    """The tile grid as the run's ``pattern`` option (``pipeline.cli_args._grid_pattern``), or ``None``."""
    grid = parse_grid(str(opts.get("nanowell_grid") or ""))
    overlap, pixel = _number(opts, "nanowell_overlap_percent"), _number(opts, "nanowell_pixel_size_um")
    if grid is None or overlap is None or pixel is None:
        return None
    return {"grid": list(grid), "scan": str(opts.get("nanowell_scan") or SCAN_CHOICES[0]),
            "overlap_percent": overlap, "pixel_size_um": pixel}


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
    errors += _format_errors(opts) + _grid_errors(opts)
    geometry = str(opts.get("nanowell_geometry") or GEOMETRY_CHOICES[0][0]).strip()
    if geometry not in {value for value, _label in GEOMETRY_CHOICES}:
        errors.append(f"Nanowell geometry {geometry!r} is not one this version knows.")
    if need_phase and not str(opts.get("phase_token") or "").strip():
        errors.append("Nanowell mode needs a Phase channel: tick Phase on the channel the tiles are imaged in.")
    custom = str(opts.get("nanowell_format") or "").strip() == CUSTOM_FORMAT
    for key, (label, low, high, low_ok) in _NUMERIC.items():
        raw = opts.get(key)
        if raw is None or not str(raw).strip():
            continue
        if key in _CUSTOM_KEYS and not custom:
            continue                # hidden and never sent for a known format, so nothing to refuse
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
