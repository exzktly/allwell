"""Image Table layout + dimension resolution.

Axis/cell layout math, the per-dimension option lists (well / channel / FOV /
timepoint), channel enumeration, and the row<->cell snapshot helpers — the
"decide what goes in each cell" layer. Extracted from ``controller.py`` so the
controller keeps event handling + orchestration. Token helpers come from the
``image_compose`` layer (downward dependency). ``controller`` re-exports these
names, so call sites and external importers (playground) are unchanged.
"""

from __future__ import annotations

from well_viewer.domain.viewer_state import parse_rc

from typing import Dict, List, Optional  # noqa: F401

from well_viewer.features.image_browser.image_table.image_compose import (  # noqa: F401
    NUC_SEG_TOKEN,
    _norm_token,
)


def _channel_options(ctx) -> List[str]:
    """All channel tokens available for selection (uppercase for display).

    Sourced from the canonical ``ctx.state.channel.fluor_channels`` /
    ``ctx.state.channel.smfish_channels`` lists (built by ``_recalculate_threshold`` from
    pipeline_info.json + CSV detection — same lists every other tab uses).
    Falls back to the raw ``ctx.state.dataset.pipeline_info`` dict for the rare case
    where the tab is repopulated before the first ``_recalculate_threshold``
    pass. Adds the virtual ``NUC+SEG`` token at the end when a nuclear
    channel is known — that's where the pre-rendered segmentation overlay
    images live, and they're how the Image Table replaces the old Movie
    Montage tab.
    """
    info = ctx.state.dataset.pipeline_info or {}
    chans: List[str] = []

    fluor = list(ctx.state.channel.fluor_channels or [])
    if not fluor:
        nuclear_pi = (info.get("nuclear_token") or "").strip().lower()
        if nuclear_pi:
            fluor.append(nuclear_pi)
        for tok in info.get("fluor_tokens", []) or []:
            tok = str(tok).strip().lower()
            if tok and tok not in fluor:
                fluor.append(tok)
    for tok in fluor:
        tok = str(tok).strip().lower()
        if tok and tok not in chans:
            chans.append(tok)

    smfish = ctx.state.channel.smfish_channels or []
    for tok in smfish:
        tok = str(tok).strip().lower()
        if tok and tok not in chans:
            chans.append(tok)

    options = [c.upper() for c in chans]

    seg_tok = (ctx.state.channel.seg_channel_token or "").strip()
    if not seg_tok:
        seg_tok = (info.get("nuclear_token") or "").strip()
    if seg_tok:
        options.append(NUC_SEG_TOKEN)

    # Append user-defined ratio metrics as virtual channels.  The display
    # label "GFP/RFP" is what appears in the dropdown; _find_ratio_for_channel
    # resolves it back to the RatioMetric at render time.
    for r in (ctx.state.channel.ratio_metrics or []):
        lbl = r.display_label()
        if lbl and lbl not in options:
            options.append(lbl)

    return options


def _find_ratio_for_channel(ctx, chan: str):
    """Return the RatioMetric whose display_label matches *chan*, or None."""
    for r in (ctx.state.channel.ratio_metrics or []):
        if r.display_label().upper() == chan.strip().upper():
            return r
    return None


def _tp_sort_key(tp: str):
    """Sort key for timepoint strings; uses parse_timepoint_hours so
    ``01d02h30m``-style values order chronologically."""
    from well_viewer.data.data_loading import parse_timepoint_hours
    h = parse_timepoint_hours(str(tp))
    return (h is None, h if h is not None else 0.0, str(tp))


def _cache_column_tokens(ctx, column: str) -> List[str]:
    """Unique normalised tokens from ``column`` across every cached DataFrame.

    ``ctx.state.dataset.cache`` values are DataFrames since the pandas migration; iterating
    one with ``for row in df`` yields *column names*, not rows. Pull the column
    out vectorised instead — and skip frames that don't carry it at all.
    """
    seen: List[str] = []
    cache = ctx.state.dataset.cache or {}
    for df in cache.values():
        if df is None or column not in getattr(df, "columns", ()):
            continue
        for val in df[column].dropna().unique():
            tok = _norm_token(val)
            if tok and tok not in seen:
                seen.append(tok)
    return seen


def _timepoint_options(ctx) -> List[str]:
    info = ctx.state.dataset.pipeline_info or {}
    seen: List[str] = []
    for tp in (info.get("available_timepoints") or []):
        tok = _norm_token(tp)
        if tok and tok not in seen:
            seen.append(tok)
    if seen:
        return sorted(seen, key=_tp_sort_key)
    # Fallback: derive timepoints from loaded CSV rows when pipeline_info
    # doesn't list them (older runs, custom schemas, etc.).
    return sorted(_cache_column_tokens(ctx, "timepoint"), key=_tp_sort_key)


def _fov_options(ctx) -> List[str]:
    info = ctx.state.dataset.pipeline_info or {}
    seen: List[str] = []
    for fv in (info.get("available_fovs") or []):
        tok = _norm_token(fv)
        if tok and tok not in seen:
            seen.append(tok)
    if seen:
        return seen
    return _cache_column_tokens(ctx, "fov")


def _well_options(ctx) -> List[str]:
    return sorted(ctx.state.dataset.well_paths.keys(), key=parse_rc)


def _axis_dim_values(ctx, dim: str) -> List[str]:
    """Ordered value list a single dimension expands into, from live selections.

    Channel/well filter by ``active_channels``/``active_wells``; timepoint/fov
    filter by ``active_timepoints``/``active_fovs`` (empty set = all, so this is a
    no-op until those filters are populated)."""
    if dim == "channel":
        opts = _channel_options(ctx)
        sel = getattr(ctx.state.image_table, "active_channels", None) or set()
        return [c for c in opts if c in sel] if sel else list(opts)
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
    return []


def _axis_values(ctx, axis: str) -> List[str]:
    """Back-compat single-dim wrapper around :func:`_axis_dim_values`."""
    return _axis_dim_values(ctx, axis)


def _product_values(ctx, dims) -> List[tuple]:
    """Mixed-radix Cartesian product of *dims* (outer→inner order).

    Returns a list of value tuples aligned to *dims*. Empty *dims* → ``[()]`` (a
    single implicit cell). If any dim has no values the product is empty."""
    import itertools
    if not dims:
        return [()]
    lists = [_axis_dim_values(ctx, d) for d in dims]
    return [tuple(p) for p in itertools.product(*lists)]


def _fixed_off_axis(ctx, on_axis: set) -> Dict[str, str]:
    """Pick a single held value for each dimension NOT on a row/col axis.

    Prefers the matching Global Options dropdown (so the user can change it and
    re-Build), else the first selected/available value."""
    def _global(attr: str) -> str:
        cb = getattr(ctx.runtime.image_table, attr, None)
        return cb.currentText().strip() if cb is not None else ""

    def _first(seq) -> str:
        return seq[0] if seq else ""

    fixed = {"well": "", "channel": "", "timepoint": "", "fov": ""}
    if "well" not in on_axis:
        sel = sorted(getattr(ctx.state.image_table, "active_wells", set()) or set(),
                     key=parse_rc)
        fixed["well"] = sel[0] if sel else _first(_well_options(ctx))
    if "channel" not in on_axis:
        fixed["channel"] = _global("global_chan_cb") or _first(_axis_values(ctx, "channel"))
    if "timepoint" not in on_axis:
        fixed["timepoint"] = _global("global_tp_cb") or _first(_timepoint_options(ctx))
    if "fov" not in on_axis:
        fixed["fov"] = _global("global_fov_cb") or _first(_fov_options(ctx))
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


def _current_dims(ctx, which: str) -> List[str]:
    """Ordered dimension list for the row/col axis (outer→inner).

    State (``row_dims``/``col_dims``, kept in sync by the multi-dim picker) is the
    source of truth; ``effective_*_dims`` falls back to the single legacy axis so
    the flat case and old saved states keep working."""
    st = ctx.state.image_table
    return st.effective_row_dims() if which == "row" else st.effective_col_dims()


def _layout_cell_values_nested(row_dims, col_dims, row_tuple, col_tuple,
                               fixed) -> Dict[str, str]:
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
                        *, n_row_vals=None, group_size=None) -> Dict[str, str]:
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


def _all_dim_options(ctx, dim: str) -> List[str]:
    """Every available value for a dimension (not selection-filtered)."""
    if dim == "channel":
        return _channel_options(ctx)
    if dim == "well":
        return _well_options(ctx)
    if dim == "timepoint":
        return _timepoint_options(ctx)
    if dim == "fov":
        return _fov_options(ctx)
    return []


_DIM_KEY = {"well": "well_cb", "channel": "chan_cb",
            "timepoint": "tp_cb", "fov": "fov_cb"}


def _snapshot_cells(ctx) -> List[List[Dict[str, str]]]:
    """Capture every cell's (well, channel, timepoint, fov) selections."""
    snap: List[List[Dict[str, str]]] = []
    for row in (getattr(ctx.runtime.image_table, "cells", None) or []):
        snap.append([
            {dim: cell[key].currentText().strip() for dim, key in _DIM_KEY.items()}
            for cell in row
        ])
    return snap


def _write_cell(cell, vals: Dict[str, str]) -> None:
    for dim, key in _DIM_KEY.items():
        cb = cell.get(key)
        txt = vals.get(dim, "")
        if cb is None or not txt:
            continue
        cb.blockSignals(True)
        try:
            if cb.findText(txt) >= 0:
                cb.setCurrentText(txt)
        finally:
            cb.blockSignals(False)
