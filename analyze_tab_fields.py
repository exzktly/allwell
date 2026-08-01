"""Entry-boundary parsing and numeric validation for the Analyze form.

These read the form's text fields and turn them into run options, or into the
human-readable errors that block a run before it starts — a bad radius / FFT /
mesh / QC value is refused up front instead of being silently dropped into a
default. Qt-free: they take and return plain strings and dicts.

``analyze_tab`` and ``analyze_tab_form`` both re-export ``_numeric_option_errors``
so every existing import path still resolves.
"""

from __future__ import annotations


# ── Entry-boundary parsing + numeric validation ─────────────────────────────
#
# These read the form's text fields, so they live beside the widgets rather than
# in the tab that hosts them. ``analyze_tab`` re-exports
# ``_numeric_option_errors`` so its old import path still resolves.

def _safe_qc_float(text: str) -> float:
    """Parse a QC bound from a text field; 0.0 (= no bound) on empty/invalid."""
    try:
        return float(str(text).strip() or "0")
    except (TypeError, ValueError):
        return 0.0


def _safe_seg_float(text: str, default: float) -> float:
    """Parse a segmentation-parameter field; *default* on empty/invalid."""
    try:
        return float(str(text).strip() or default)
    except (TypeError, ValueError):
        return default


_INT_FIELDS = {
    "tophat_radius_nir": "Nuclear tophat radius",
    "tophat_radius_fluor": "Fluor tophat radius",
    "fft_small_nir": "Nuclear FFT small-object px",
    "fft_small_fluor": "Fluor FFT small-object px",
    "tf_threads": "TF threads",
    "workers": "Workers",
    "remote_batch_size": "Remote batch size",
}


def _numeric_option_errors(opts: dict) -> list[str]:
    """Return human-readable errors for out-of-range / unparseable numeric run
    options. Empty/blank means "use the pipeline default" and is allowed."""
    errors: list[str] = []
    for key, label in _INT_FIELDS.items():
        raw = str(opts.get(key, "") or "").strip()
        if not raw:
            continue
        try:
            if int(raw) < 0:
                errors.append(f"{label} must be a whole number ≥ 0 (got {raw!r}).")
        except ValueError:
            errors.append(f"{label} must be a whole number (got {raw!r}).")
    for key, label, allow_zero in (("mesh_ratio", "Mesh tile ratio", False),
                                   ("mesh_sigma", "Mesh sigma", True)):
        raw = str(opts.get(key, "") or "").strip()
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
    mn = opts.get("qc_min_area") or 0.0
    mx = opts.get("qc_max_area") or 0.0
    if mn and mx and mn > mx:
        errors.append("QC minimum area must be ≤ maximum area.")
    sol = opts.get("qc_min_solidity") or 0.0
    if not (0.0 <= sol <= 1.0):
        errors.append("QC minimum solidity must be between 0 and 1.")
    return errors
