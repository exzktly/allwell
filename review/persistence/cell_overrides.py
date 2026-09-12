"""Cell-override persistence (``<data_dir>/cell_overrides.json``).

Stores Segmentation tab include/exclude patches for individual cells. Writes
are debounced via ``schedule_save`` to coalesce bursts of toggles into a
single disk write.

Schema v2 adds a per-well **segmentation fingerprint** so re-running the
pipeline (which re-allocates nucleus label IDs from 1) doesn't cause
overrides to silently re-bind to unrelated cells. The fingerprint is the
``<well>_out.zip`` mtime captured at the time the override was set; a
stored entry whose fingerprint no longer matches the on-disk zip is
discarded with a warning.

Two situations cannot yield a verdict, and both are reported rather than
passed off as a match: a row with no stored fingerprint at all (a v1 file,
or a v2 row saved while the well's zip was missing or unreadable), and a row
whose fingerprint has nothing to compare against because the well's
``<well>_out.zip`` is not in the data directory. Those rows are kept — a
moved zip must not cost the user their curation — and counted in a warning
naming how many were loaded unchecked.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from util.well_token import canonical_well_label
from core.safe import swallowed
from review.data.image_identity import CellKey
from review.persistence._io import atomic_write_json

_logger = logging.getLogger("review")

_SCHEMA_VERSION = 2


def path_for(ctx) -> Path | None:
    if ctx.state.dataset.data_dir:
        return ctx.state.dataset.data_dir / "cell_overrides.json"
    return None


def _well_zip_fingerprints(ctx) -> dict[str, str]:
    """Snapshot the ``<well>_out.zip`` mtimes for every loaded well.

    Used both at save time (stored alongside each override) and at load
    time (compared against the file on disk). Keys are canonicalised
    (``a1_out.zip`` -> ``"A01"``) to match ``CellKey.well`` — the override
    map's own keys, which every well is normalised into via
    ``normalize_well``. An unpadded on-disk filename previously left this
    dict keyed by the raw, unpadded stem, so the lookup at both save and
    load time missed on an unpadded dataset; both sides missed the same
    way, so the staleness guard silently never fired instead of failing
    loudly — stale overrides were kept rather than dropped.
    """
    out: dict[str, str] = {}
    data_dir = ctx.state.dataset.data_dir
    if not data_dir:
        return out
    for zip_path in Path(data_dir).glob("*_out.zip"):
        stem = zip_path.stem
        well = canonical_well_label(stem[:-4]) if stem.endswith("_out") else None
        if not well:
            continue
        try:
            out[well] = f"{zip_path.stat().st_mtime_ns}"
        except OSError:
            # No fingerprint for this well. Both callers treat a missing entry
            # as "cannot verify" and say so rather than assuming a match, but
            # the stat failure itself must not be silent — it is the reason a
            # later load reports overrides it could not check.
            swallowed(f"cell_overrides: could not stat {zip_path}")
    return out


def save_to_data_dir(ctx) -> None:
    path = path_for(ctx)
    if path is None:
        return
    fingerprints = _well_zip_fingerprints(ctx)
    overrides = []
    for (well, fov, tp, nid), val in ctx.runtime.review.included_overrides.items():
        overrides.append({
            "well": str(well),
            "fov": str(fov),
            "tp": str(tp),
            "nucleus_id": str(nid),
            "included": str(val),
            # Stamp each row with the fingerprint of the well's zip at
            # the time the override was saved. A later re-run of the
            # pipeline will change this value and the row will be
            # dropped on next load.
            "seg_fp": fingerprints.get(str(well), ""),
        })
    payload = {
        "version": _SCHEMA_VERSION,
        "overrides": overrides,
    }
    try:
        # ``deferrable``: this runs off the 500 ms autosave debounce on the GUI
        # thread, and the payload above is already fully built, so the file
        # operations go to the background writer when one is active.
        atomic_write_json(path, payload, deferrable=True)
    except OSError as exc:
        _logger.warning("Failed to save cell overrides to %s: %s", path, exc)


def _fingerprints_for_data_dir(data_dir: Path) -> dict[str, str]:
    """Qt-free twin of ``_well_zip_fingerprints`` taking a plain path — lets
    :func:`load_overrides` (and its tests) work from a data dir alone rather
    than from a context."""
    out: dict[str, str] = {}
    for zip_path in Path(data_dir).glob("*_out.zip"):
        stem = zip_path.stem
        well = canonical_well_label(stem[:-4]) if stem.endswith("_out") else None
        if not well:
            continue
        try:
            out[well] = f"{zip_path.stat().st_mtime_ns}"
        except OSError:
            swallowed(f"cell_overrides: could not stat {zip_path}")
    return out


def load_overrides(data_dir: Path) -> dict[tuple[str, str, str, str], str]:
    """Read ``<data_dir>/cell_overrides.json`` into a ``CellKey.as_tuple()``
    keyed include-map, applying the same staleness/legacy rules
    :func:`load_from_data_dir` does — extracted as a pure function (no
    ``ctx``/``app``) so it is both the production reader and something a test
    can call directly on a plain path, rather than driving a four-attribute
    app stub to exercise the same logic.

    Returns ``{}`` on a missing/corrupt/empty file — the caller decides
    whether that means "nothing to load" or "don't touch existing state".
    """
    path = Path(data_dir) / "cell_overrides.json"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("Failed to load cell overrides from %s: %s", path, exc)
        return {}
    overrides = data.get("overrides") if isinstance(data, dict) else None
    if not isinstance(overrides, list):
        return {}
    file_version = 1
    try:
        file_version = int(data.get("version", 1))
    except (TypeError, ValueError):
        pass

    fingerprints = _fingerprints_for_data_dir(data_dir)
    new_map: dict = {}
    dropped_stale = 0
    legacy_unfingerprinted = 0
    unverifiable = 0
    for entry in overrides:
        if not isinstance(entry, dict):
            continue
        well = str(entry.get("well", "")).strip()
        fov = str(entry.get("fov", "")).strip()
        tp = str(entry.get("tp", "")).strip()
        nid = str(entry.get("nucleus_id", "")).strip()
        inc = str(entry.get("included", "1")).strip() or "1"
        stored_fp = str(entry.get("seg_fp", "")).strip()
        if not (well and fov and tp and nid):
            continue
        if stored_fp:
            current_fp = fingerprints.get(well, "")
            if not current_fp:
                # The well has no ``<well>_out.zip`` on disk (never written,
                # deleted, or a stat that failed above), so the fingerprint
                # this row carries cannot be checked against anything. That is
                # *not* the same as a match: the row is kept — dropping curation
                # because a zip was moved would be an unrecoverable loss — but
                # it is counted and reported, so an override binding to a
                # re-segmented cell is never indistinguishable from a verified
                # one. This branch previously fell through silently.
                unverifiable += 1
            elif stored_fp != current_fp:
                # Pipeline re-run since this override was saved — nucleus
                # IDs no longer match the original segmentation. Drop.
                dropped_stale += 1
                continue
        else:
            legacy_unfingerprinted += 1
        # Build the lookup key via CellKey — the single source of truth.
        # Previously this strip-only path silently disagreed with the
        # smFISH writer's empty-FOV → "1" rule, so on single-FOV datasets
        # the override was unreachable. CellKey applies the same chain
        # both sides use.
        key = CellKey.from_row(
            {"fov": fov, "timepoint": tp, "nucleus_id": nid}, well=well,
        )
        if not (key.fov and key.tp and key.nid):
            continue
        new_map[key.as_tuple()] = inc
    if dropped_stale:
        _logger.warning(
            "cell_overrides: dropped %d override(s) whose segmentation "
            "fingerprint no longer matches the on-disk well zip (likely a "
            "pipeline re-run since the overrides were saved).",
            dropped_stale,
        )
    if legacy_unfingerprinted:
        # Not gated on ``file_version``: a v2 file can carry rows with an empty
        # ``seg_fp`` too (the well's zip was missing, or unreadable, when the
        # override was saved), and those rows are exactly as unchecked as a v1
        # file's. Gating the warning on the file version meant the v2 case
        # warned about nothing at all. The version is reported so the message
        # says which of the two situations produced it.
        _logger.warning(
            "cell_overrides: %d override(s) in this v%d file carry no "
            "segmentation fingerprint — they may bind to unrelated cells if "
            "the pipeline has been re-run since they were saved. Re-save to "
            "stamp the current segmentation.",
            legacy_unfingerprinted, file_version,
        )
    if unverifiable:
        _logger.warning(
            "cell_overrides: %d override(s) could not be checked against a "
            "segmentation zip because the well's ``<well>_out.zip`` is not in "
            "this data directory; they were kept unverified.",
            unverifiable,
        )
    return new_map


def load_from_data_dir(ctx) -> None:
    data_dir = ctx.state.dataset.data_dir
    if not data_dir:
        return
    new_map = load_overrides(data_dir)
    if not new_map:
        # Refuse to wipe a populated in-memory state with a no-op load.
        return
    ctx.runtime.review.included_overrides.clear()
    ctx.runtime.review.included_overrides.update(new_map)
    # Bump the include-map version so Review Image recomputes; skipped when
    # Image Browser is disabled (no review_image slice).
    _review_image = ctx.state.feature_slice("review_image")
    if _review_image is not None:
        _review_image.override_version += 1
