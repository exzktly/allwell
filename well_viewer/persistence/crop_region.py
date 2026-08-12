"""Crop-region persistence — ``crop_region`` section of ``persistence.json``.

Stores the crop rectangle drawn on the Segmentation tab, and the frames it is
applied to, so the crop survives between sessions. The region is a normalized
``(row0, col0, row1, col1)`` tuple in source-mask pixels (see
:mod:`well_viewer.features.crop.region`); the scope is the wells / FOVs /
timepoints the crop filters, with ``null`` meaning "every value of that field".

The scope is saved *because* it is part of what the crop means: reloading a
dataset with the region but without its scope would silently widen a two-well
crop to the whole plate, and every number downstream would move. (The drag
delete's scope is deliberately transient by the opposite argument — it arms a
gesture, not a stored verdict.)

The section is written as an object; a bare four-element list is the older
shape, read as an unscoped crop. An absent / cleared crop removes the section
entirely.

Lives in the consolidated ``persistence.json`` document (like ``ratios`` /
``qc_params`` / ``view_state``), not a standalone sidecar.
"""

from __future__ import annotations

import logging

from well_viewer.persistence import _doc

_logger = logging.getLogger("well_viewer")

_SECTION = "crop_region"

# The scope keys as written to disk, paired with the ``DatasetState`` field each
# one holds. Order is the popover's: wells, FOVs, timepoints.
_SCOPE_KEYS = (
    ("wells", "crop_scope_wells"),
    ("fovs", "crop_scope_fovs"),
    ("tps", "crop_scope_tps"),
)


def _scope_payload(ctx) -> dict:
    """The armed scope as JSON — ``None`` for an un-armed (all-values) field."""
    ds = ctx.state.dataset
    payload = {}
    for key, attr in _SCOPE_KEYS:
        values = getattr(ds, attr, None)
        payload[key] = None if values is None else sorted(str(v) for v in values)
    return payload


def save_to_data_dir(ctx) -> None:
    if not ctx.state.dataset.data_dir:
        return
    region = ctx.state.dataset.crop_region
    if not region:
        # No region means no crop, and a scope with nothing to scope is not
        # worth carrying across sessions — dropping the whole section keeps
        # "cleared" a single state on disk rather than two that read alike.
        _doc.set_section(ctx, _SECTION, None)
        return
    _doc.set_section(ctx, _SECTION, {
        "region": list(region),
        "scope": _scope_payload(ctx),
    })


def _load_scope(ctx, scope) -> None:
    """Hydrate the three scope fields from a saved ``scope`` object.

    Anything unreadable resolves to un-armed rather than to a guess: a scope
    that cannot be trusted must widen the crop to every frame (its documented
    default), never narrow it to a set nobody chose.
    """
    ds = ctx.state.dataset
    ds.reset_crop_scope()
    if not isinstance(scope, dict):
        return
    for key, attr in _SCOPE_KEYS:
        values = scope.get(key)
        if values is None:
            continue
        if not isinstance(values, (list, tuple)):
            _logger.warning(
                "Ignoring malformed crop scope %r: %r", key, values)
            continue
        setattr(ds, attr, {str(v) for v in values})


def load_from_data_dir(ctx) -> None:
    if not ctx.state.dataset.data_dir:
        return
    data = _doc.get_section(ctx, _SECTION)
    region, scope = None, None
    if isinstance(data, dict):
        region, scope = data.get("region"), data.get("scope")
    elif isinstance(data, (list, tuple)):
        # The older shape: a bare region, which meant every frame.
        region = data
    ctx.state.dataset.crop_region = None
    ctx.state.dataset.reset_crop_scope()
    if isinstance(region, (list, tuple)) and len(region) == 4:
        try:
            ctx.state.dataset.crop_region = tuple(int(v) for v in region)
        except (TypeError, ValueError):
            _logger.warning("Ignoring malformed crop_region section: %r", data)
            return
        _load_scope(ctx, scope)
    elif data is not None:
        _logger.warning("Ignoring malformed crop_region section: %r", data)
