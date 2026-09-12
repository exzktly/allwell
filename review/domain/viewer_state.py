"""State/pure helpers for incremental viewer modularization."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from collections.abc import Callable


def make_schema_extractor(sep: str, fov_idx: int, tp_idx: int):
    def _extract(stem: str) -> tuple[str, str]:
        parts = stem.split(sep)
        fov = parts[fov_idx] if 0 <= fov_idx < len(parts) else "unknown"
        tp = parts[tp_idx] if 0 <= tp_idx < len(parts) else "unknown"
        return fov, tp

    return _extract


def extract_well_token(label: str) -> str | None:
    """Extract normalized well token from trailing label text.

    Example:
        "measurements_B10" -> "B10"
    """
    m = re.search(r"([A-Ha-h])(\d{1,2})$", label)
    return f"{m.group(1).upper()}{int(m.group(2)):02d}" if m else None


def parse_rc(label: str) -> tuple[str | None, str | None]:
    """Split a well label into its (row-letter, zero-padded-column) sort key.

    ``"A1"`` / ``"measurements_a01"`` -> ``("A", "01")``; junk -> ``(None, None)``.
    Pure — the ctx-free replacement for the ``ReviewApp._parse_rc``
    god-method, usable directly as a ``sorted(key=parse_rc)`` callable."""
    m = re.search(r"([A-Ha-h])(\d{1,2})$", label)
    if not m:
        return None, None
    return m.group(1).upper(), f"{int(m.group(2)):02d}"


PIPELINE_INFO_FILENAME = "pipeline_info.json"


def resolve_pipeline_info_path(out_dir: Path) -> Path:
    """Where a dataset's ``pipeline_info.json`` is, or where it would go.

    The Analyze layout loads the sibling ``out/`` folder as the data dir while
    the sidecar sits at the dataset root, so a reader has to look one level up
    before giving in. Returns the primary path when neither exists — the caller
    decides whether a missing file means "nothing to load" or "write it here".

    This is the same fallback :func:`read_pipeline_info` applies; it is a
    function so that the three writers which have to *save* into the same place
    a load came from cannot look somewhere else.
    """
    primary = out_dir / PIPELINE_INFO_FILENAME
    if primary.exists():
        return primary
    parent = out_dir.parent / PIPELINE_INFO_FILENAME
    return parent if parent.exists() else primary


def read_pipeline_info(
    out_dir: Path,
    *,
    logger=None,
    check_parent: bool = True,
) -> tuple[Callable[[str], tuple[str, str]] | None, list[str], set[str], dict]:
    if logger is None:
        logger = logging.getLogger("review")

    info_path = (resolve_pipeline_info_path(out_dir) if check_parent
                 else out_dir / PIPELINE_INFO_FILENAME)

    if not info_path.exists():
        return None, [], set(), {}

    try:
        data = json.loads(info_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read pipeline_info.json (%s): %s — using legacy regex", info_path, exc)
        return None, [], set(), {}
    if not isinstance(data, dict):
        logger.warning("pipeline_info.json (%s) is not a JSON object — using legacy regex", info_path)
        return None, [], set(), {}

    # Extract the channel-token lists up front. These should survive even
    # when the index fields are bad — otherwise a single typo in
    # fov_index / tp_index loses every channel and the viewer falls back
    # to detecting them from CSV column names.
    fluor_tokens = [str(t).lower() for t in data.get("fluor_tokens", []) or []]
    smfish_tokens = set(str(t).lower() for t in data.get("smfish_tokens", []) or [])
    sep = str(data.get("separator", "_"))

    def _safe_int(key: str, default: int = -1) -> int:
        try:
            return int(data[key])
        except (KeyError, TypeError, ValueError):
            return default

    fov_idx = _safe_int("fov_index")
    tp_idx = _safe_int("tp_index")

    pipeline_info = {
        "schema": str(data.get("schema", "")),
        "schema_fields": [str(f).strip() for f in data.get("schema_fields", []) or []],
        "separator": sep,
        "well_index": _safe_int("well_index"),
        "channel_index": _safe_int("channel_index"),
        "fov_index": fov_idx,
        "tp_index": tp_idx,
        "nuclear_token": str(data.get("nuclear_token", "")).strip().lower(),
        "fluor_tokens": list(fluor_tokens),
        # Carried alongside its sibling so a consumer holding the parsed block
        # can answer "did this run have smFISH channels?" without re-reading the
        # file or borrowing a runtime scratch list that is filled in later.
        # Sorted because the source is a set and the value is displayed.
        "smfish_tokens": sorted(smfish_tokens),
        "available_timepoints": [
            str(tp).strip()
            for tp in data.get("available_timepoints", []) or []
            if str(tp).strip()
        ],
        "available_fovs": [
            str(fov).strip()
            for fov in data.get("available_fovs", []) or []
            if str(fov).strip()
        ],
        # ── Feature-additive keys (backwards-compatible) ──────────────────────
        # These are written by the pipeline only once the corresponding feature
        # ships (label-free seg / QC tooling / shape features). Datasets analyzed
        # before those features have none of them, so each reads through a
        # ``.get`` default and the viewer behaves identically on a legacy
        # sidecar: an absent key is "this run predates the feature", never "the
        # feature produced nothing". Adding a key here means giving it a default
        # that a pre-feature dataset can be read through unchanged.
        "schema_version": _safe_int("schema_version", 1),
        "segmentation_method": str(data.get("segmentation_method", "") or "").strip().lower(),
        "phase_token": str(data.get("phase_token", "") or "").strip().lower(),
        "shape_features": [
            str(s).strip().lower()
            for s in data.get("shape_features", []) or []
            if str(s).strip()
        ],
        "qc_options": dict(data.get("qc_options", {}) or {}),
    }

    if fov_idx >= 0 and tp_idx >= 0 and fov_idx == tp_idx:
        logger.warning(
            "pipeline_info.json fov_index and tp_index both point at column %d "
            "(%s) — every image would collapse to one (fov, tp) bucket. Falling "
            "back to legacy regex.", fov_idx, info_path,
        )
        return None, fluor_tokens, smfish_tokens, pipeline_info

    if tp_idx < 0:
        logger.warning("pipeline_info.json has invalid tp_index (%s); using legacy regex", info_path)
        return None, fluor_tokens, smfish_tokens, pipeline_info

    if fov_idx < 0:
        logger.info("Loaded pipeline_info from %s  sep=%r fov=<single> tp=%d", info_path, sep, tp_idx)

        def _extract_single_fov(stem: str) -> tuple[str, str]:
            parts = stem.split(sep)
            tp = parts[tp_idx] if 0 <= tp_idx < len(parts) else "unknown"
            return "1", tp

        return _extract_single_fov, fluor_tokens, smfish_tokens, pipeline_info

    logger.info(
        "Loaded pipeline_info from %s  sep=%r fov=%d tp=%d fluor=%s smfish=%s",
        info_path, sep, fov_idx, tp_idx, fluor_tokens, smfish_tokens,
    )
    return make_schema_extractor(sep, fov_idx, tp_idx), fluor_tokens, smfish_tokens, pipeline_info


# ── Batch export utilities (merged from batch_export.py) ─────────────────────

def groups_with_loaded_wells(groups, well_paths: dict) -> list:
    """Return groups that contain at least one loaded well label."""
    return [g for g in groups if any(w in well_paths for w in g.wells)]


# ── App-level state container (merged from app_state.py) ─────────────────────

