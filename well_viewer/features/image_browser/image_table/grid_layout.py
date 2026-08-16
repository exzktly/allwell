"""Image Table layout: axis/cell math and the row<->cell snapshot helpers.

The "decide what goes in each cell" layer, extracted from ``controller.py`` so
the controller keeps event handling + orchestration.

The per-dimension option lists it used to own are ``data/dataset_dims.py`` and
the channel-token vocabulary is ``data/frame_load.py`` — both shared with the
Playground, which is a separately removable sub-tab and may not reach into this
one. They are re-exported here under this module's existing private names so
its own call sites and ``controller``'s re-export block are unchanged.
"""

from __future__ import annotations

# Names used only in annotations. Under ``from __future__ import
# annotations`` these are never evaluated, so importing them for real would
# add startup cost and, for several of these, an import cycle.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from well_viewer.domain.ratio_models import RatioMetric

from well_viewer.domain.viewer_state import parse_rc


from well_viewer.features.image_browser.image_table.image_compose import OVERLAY_CHAN
# The dataset's per-dimension option lists and the channel-token vocabulary are
# the data layer's (`data/dataset_dims.py`, `data/frame_load.py`), shared with the
# Playground. Re-exported under this module's existing private names so its own
# call sites and `controller`'s re-export block are unchanged.
from well_viewer.data.frame_load import is_variant_token  # noqa: F401
from well_viewer.data.dataset_dims import (  # noqa: F401
    channel_options as _channel_options,
    fov_options as _fov_options,
    timepoint_options as _timepoint_options,
    tp_sort_key as _tp_sort_key,
    well_options as _well_options,
)


def _find_ratio_for_channel(ctx, chan: str) -> RatioMetric | None:
    """Return the RatioMetric whose display_label matches *chan*, or None."""
    for r in (ctx.state.channel.ratio_metrics or []):
        if r.display_label().upper() == chan.strip().upper():
            return r
    return None


# ── Plate (96-well) virtual dimensions ───────────────────────────────────────
# Split the single ``well`` dim into its plate-row letter (A–H) and plate-column
# number (1–12) so the Image Table can lay images out in true plate positions
# with correct A–H / 1–12 axis labels. These are *virtual* layout dims: they never
# get their own render-model cell key — the layout composes them back into the real
# ``well`` token (:func:`compose_plate_well`), and the facet labeller derives them
# back from that well for the axis headers. Using two real axes (instead of the old
# "well on one axis + Wrap N" band trick) is what makes every band label correctly,
# not just the first.
PLATE_ROW_DIM = "well_row"
PLATE_COL_DIM = "well_col"
PLATE_DIMS = (PLATE_ROW_DIM, PLATE_COL_DIM)


def _selected_wells(ctx) -> list[str]:
    """Wells the plate draws from — the sidebar plate selection, else every well.

    Sorted by ``parse_rc`` so rows/cols come out in plate order (A1, A2, … B1, …)."""
    sel = sorted(getattr(ctx.state.image_table, "active_wells", set()) or set(),
                 key=parse_rc)
    return sel if sel else _well_options(ctx)


def _well_row_options(ctx) -> list[str]:
    """Distinct plate-row letters (A–H) among the selected wells, top→bottom."""
    seen: list[str] = []
    for w in _selected_wells(ctx):
        r, _c = parse_rc(w)
        if r and r not in seen:
            seen.append(r)
    return sorted(seen)


def _well_col_options(ctx) -> list[str]:
    """Distinct plate-column numbers ("1".."12") among the selected wells, ordered."""
    nums = set()
    for w in _selected_wells(ctx):
        _r, c = parse_rc(w)
        if c:
            nums.add(int(c))
    return [str(n) for n in sorted(nums)]


def plate_well_index(ctx) -> dict[tuple, str]:
    """Map ``(row_letter, col_int)`` → the real well token, for well composition.

    Built from every dataset well (not just the selection) so a partial plate still
    resolves the positions it does cover."""
    idx: dict[tuple, str] = {}
    for w in _well_options(ctx):
        r, c = parse_rc(w)
        if r and c:
            idx[(r.upper(), int(c))] = w
    return idx


def compose_plate_well(vals: dict[str, str], rc_index: dict[tuple, str]) -> None:
    """Resolve a cell's plate row/col dims to the real ``well`` token, in place.

    Sets ``vals['well']`` to the matching well, or ``""`` when that plate position
    isn't in the dataset (so the cell renders blank rather than falling back to a
    held well). No-op when the cell carries no plate dims."""
    r = vals.get(PLATE_ROW_DIM)
    c = vals.get(PLATE_COL_DIM)
    if not (r and c):
        return
    try:
        key = (str(r).upper(), int(c))
    except (TypeError, ValueError):
        vals["well"] = ""
        return
    vals["well"] = rc_index.get(key, "")


def _overlay_active(ctx) -> bool:
    """The Separate↔Overlay switch is on (``state.composite``) — the channel axis
    collapses to a single blended entry instead of one value per channel."""
    return bool(getattr(ctx.state.image_table, "composite", False))


def _axis_dim_values(ctx, dim: str, *, collapse_overlay: bool = True) -> list[str]:
    """Ordered value list a single dimension expands into, from live selections.

    Channel/well filter by ``active_channels``/``active_wells``; timepoint/fov
    filter by ``active_timepoints``/``active_fovs`` (empty set = all, so this is a
    no-op until those filters are populated).

    When Overlay is on, the *channel* dimension collapses to the single
    :data:`OVERLAY_CHAN` sentinel so the axis stops expanding one row/column per
    channel — each cell then blends every channel (see ``overlay_cell_rgb``). Pass
    ``collapse_overlay=False`` to recover the real channel list (what the blend
    path iterates)."""
    if dim == "channel":
        if collapse_overlay and _overlay_active(ctx):
            return [OVERLAY_CHAN]
        opts = _channel_options(ctx)
        sel = getattr(ctx.state.image_table, "active_channels", None) or set()
        if sel:
            # Respect the builder's drag order. ``channel_opts`` is an ordered dict
            # built by iterating the shelf's ``Axis.channels`` in order
            # (state.spec_to_state_fields), so its key order IS the order the user
            # dropped the chips. ``active_channels`` is an unordered set, so filtering
            # ``opts`` (canonical pipeline order) by it would silently re-sort the axis.
            # Order by channel_opts, then append any selected-but-unkeyed tokens
            # (old saved states, Advanced grid) in canonical order.
            copts = getattr(ctx.state.image_table, "channel_opts", None) or {}
            ordered = [c for c in copts if c in sel]
            ordered += [c for c in opts if c in sel and c not in copts]
            return ordered
        # Default (nothing ticked) = the real channels only. Render variants
        # ("(binary)" / "(mask)" / "(raw)") are opt-in — they only join the axis
        # when explicitly selected, so a plain "Channels × Time" build isn't
        # silently multiplied.
        return [c for c in opts if not is_variant_token(c)]
    if dim == "well":
        sel = sorted(getattr(ctx.state.image_table, "active_wells", set()) or set(),
                     key=parse_rc)
        return sel if sel else _well_options(ctx)
    if dim == "timepoint":
        opts = _timepoint_options(ctx)
        sel = getattr(ctx.state.image_table, "active_timepoints", None) or set()
        return [t for t in opts if t in sel] if sel else list(opts)
    if dim == "fov":
        opts = _fov_options(ctx)
        sel = getattr(ctx.state.image_table, "active_fovs", None) or set()
        return [f for f in opts if f in sel] if sel else list(opts)
    if dim == PLATE_ROW_DIM:
        return _well_row_options(ctx)
    if dim == PLATE_COL_DIM:
        return _well_col_options(ctx)
    return []


def _axis_values(ctx, axis: str) -> list[str]:
    """Back-compat single-dim wrapper around :func:`_axis_dim_values`."""
    return _axis_dim_values(ctx, axis)


def _product_values(ctx, dims) -> list[tuple]:
    """Mixed-radix Cartesian product of *dims* (outer→inner order).

    Returns a list of value tuples aligned to *dims*. Empty *dims* → ``[()]`` (a
    single implicit cell). If any dim has no values the product is empty."""
    import itertools
    if not dims:
        return [()]
    lists = [_axis_dim_values(ctx, d) for d in dims]
    return [tuple(p) for p in itertools.product(*lists)]


def _fixed_off_axis(ctx, on_axis: set) -> dict[str, str]:
    """Pick a single held value for each dimension NOT on a row/col axis.

    Prefers the builder's held value (``state.fixed_vals``, set by the shelf's
    Fixed panel via ``image_table_apply_spec``), else the first selected/available
    value. ``well`` is anchored on the sidebar plate's active set."""
    held = getattr(ctx.state.image_table, "fixed_vals", None) or {}

    def _first(seq) -> str:
        return seq[0] if seq else ""

    # When the plate row/col dims drive the axes, ``well`` is composed per cell from
    # them — don't also pin a fixed well, or missing plate positions would fall back
    # to it instead of rendering blank.
    plate_on = bool(set(on_axis) & set(PLATE_DIMS))
    fixed = {"well": "", "channel": "", "timepoint": "", "fov": ""}
    if "well" not in on_axis and not plate_on:
        sel = sorted(getattr(ctx.state.image_table, "active_wells", set()) or set(),
                     key=parse_rc)
        fixed["well"] = held.get("well") or (sel[0] if sel else _first(_well_options(ctx)))
    if "channel" not in on_axis:
        fixed["channel"] = held.get("channel") or _first(_axis_values(ctx, "channel"))
    if "timepoint" not in on_axis:
        fixed["timepoint"] = held.get("timepoint") or _first(_timepoint_options(ctx))
    if "fov" not in on_axis:
        fixed["fov"] = held.get("fov") or _first(_fov_options(ctx))
    return fixed


def _current_axis(ctx, which: str) -> str:
    cb = getattr(ctx.runtime.image_table, f"{which}_axis_cb", None)
    if cb is not None:
        data = cb.currentData()
        if data:
            return str(data)
    # state fallback / default
    return getattr(ctx.runtime.image_table, f"{which}_axis",
                   "channel" if which == "row" else "timepoint")


def _current_dims(ctx, which: str) -> list[str]:
    """Ordered dimension list for the row/col axis (outer→inner).

    State (``row_dims``/``col_dims``, kept in sync by the multi-dim picker) is the
    source of truth; ``effective_*_dims`` falls back to the single legacy axis so
    the flat case and old saved states keep working."""
    st = ctx.state.image_table
    return st.effective_row_dims() if which == "row" else st.effective_col_dims()


def _layout_cell_values_nested(row_dims, col_dims, row_tuple, col_tuple,
                               fixed) -> dict[str, str]:
    """Per-cell dim values for a NESTED layout: off-axis dims held at *fixed*, the
    row dims set from *row_tuple* (aligned to *row_dims*) and col dims from
    *col_tuple*. The caller supplies the already-decoded product tuples
    ``row_prod[r]`` / ``col_prod[c]``."""
    vals = dict(fixed)
    for d, v in zip(row_dims, row_tuple):
        vals[d] = v
    for d, v in zip(col_dims, col_tuple):
        vals[d] = v
    return vals


def _layout_cell_values(row_axis, col_axis, row_vals, col_vals, fixed, r, c,
                        *, n_row_vals=None, group_size=None) -> dict[str, str]:
    """Per-cell (well, channel, timepoint, fov) for the smart layout: off-axis
    dims held at *fixed*, the row axis driven by *r*, the col axis by *c*.

    Grouped/tiled mode (``n_row_vals`` + ``group_size`` given): the COLUMNS axis
    wraps into vertically-stacked bands of ``group_size`` columns, and the ROWS
    axis repeats per band. For grid cell ``(r, c)``:
        band = r // n_row_vals ;  row_idx = r % n_row_vals ;  col_idx = band*G + c
    A ragged final band (col_idx past the last value) leaves the cell blank.
    Non-grouped reduces to the straight ``row_vals[r]`` / ``col_vals[c]``.
    """
    vals = dict(fixed)
    if group_size and n_row_vals:
        band = r // n_row_vals
        row_idx = r % n_row_vals
        col_idx = band * group_size + c
        vals[row_axis] = row_vals[row_idx] if 0 <= row_idx < len(row_vals) else ""
        vals[col_axis] = col_vals[col_idx] if 0 <= col_idx < len(col_vals) else ""
    else:
        vals[row_axis] = row_vals[r]
        vals[col_axis] = col_vals[c]
    return vals


def _all_dim_options(ctx, dim: str) -> list[str]:
    """Every available value for a dimension (not selection-filtered)."""
    if dim == "channel":
        return _channel_options(ctx)
    if dim == "well":
        return _well_options(ctx)
    if dim == "timepoint":
        return _timepoint_options(ctx)
    if dim == "fov":
        return _fov_options(ctx)
    if dim == PLATE_ROW_DIM:
        return _well_row_options(ctx)
    if dim == PLATE_COL_DIM:
        return _well_col_options(ctx)
    return []


# Maps a dimension name → the plain render-model cell key.
_DIM_KEY = {"well": "well", "channel": "chan",
            "timepoint": "tp", "fov": "fov"}


def _snapshot_cells(ctx) -> list[list[dict[str, str]]]:
    """Capture every cell's (well, channel, timepoint, fov) values."""
    snap: list[list[dict[str, str]]] = []
    for row in (getattr(ctx.runtime.image_table, "cells", None) or []):
        snap.append([
            {dim: (cell.get(key) or "") for dim, key in _DIM_KEY.items()}
            for cell in row
        ])
    return snap


def _write_cell(cell, vals: dict[str, str]) -> None:
    for dim, key in _DIM_KEY.items():
        txt = vals.get(dim, "")
        if txt:
            cell[key] = txt
