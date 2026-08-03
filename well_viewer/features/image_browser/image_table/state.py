"""Image Table tab — typed feature-local state.

Per ARCHITECTURE.md §5.9 the Image Table's runtime *data* (selections, axis
choices, display options, export settings, and the per-generate caches) lives
here as a typed dataclass aggregated on ``AppState`` (``ctx.state.image_table``),
rather than as loose ``ctx._image_table_*`` attributes. Qt widget handles (the
selector-cell combos, LUT QLineEdit pairs, dropdowns, menu actions, the
``CropTool`` helper, the sidebar plate) deliberately stay on ``ctx`` as
residual coupling — only plain data moves here.

Qt-free: imports only stdlib, so controllers can import it with no cycle (the
``tests/test_guardrails.py`` import contract enforces that boundary).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ImageTableState:
    """Image Table data + options + per-generate caches.

    ``rows`` / ``cols`` mirror the grid spinners; ``row_axis`` / ``col_axis``
    are the layout-builder axis keys ("channel"/"well"/"timepoint"/"fov").
    ``active_wells`` / ``active_channels`` are the sidebar / dropdown
    multi-selections. ``zoom`` is the only global display option; top-hat /
    boundaries / binary are per-row (on the LUT/Display boxes). ``last_render``
    / ``image_cache`` are the hot-path bookkeeping rebuilt by
    ``image_table_generate`` (not persisted); neither holds decoded pixels —
    those live only in the bounded cache in ``data/image_discovery``.
    """

    # selections
    active_wells: set = field(default_factory=set)
    active_channels: set = field(default_factory=set)

    # grid + layout axes
    rows: int = 2
    cols: int = 3
    row_axis: str = "channel"
    col_axis: str = "timepoint"

    # Nested / compound axes: each of rows and columns can be an ORDERED list of
    # dims (outer→inner), e.g. row_dims=["well", "channel"]. row_vals / col_vals
    # then become the mixed-radix Cartesian product of the chosen dims, so >2
    # dimensions can vary at once (e.g. wells × channels on rows, timepoints
    # across columns). Empty list = fall back to the single row_axis / col_axis
    # (today's flat behaviour), so saved view-state round-trips unchanged.
    row_dims: list = field(default_factory=list)
    col_dims: list = field(default_factory=list)
    # Per-dimension selection filters for the product (mirror active_wells /
    # active_channels for the other two dims) so a nested product doesn't explode.
    active_timepoints: set = field(default_factory=set)
    active_fovs: set = field(default_factory=set)

    # grouped / tiled layout. ``group_size`` wraps the COLUMNS axis into
    # vertically-stacked bands of this many columns; the ROWS axis repeats per
    # band. 0 = off (one band → today's flat layout). ``group_row_span`` records
    # how many row-axis values (e.g. channels) each band spans, so the LUT
    # row→group mapping (shared-per-channel LUT across bands) can be rebuilt and
    # survives view-state restore. Set by image_table_build_layout; 0 when not
    # grouped.
    group_size: int = 0
    group_row_span: int = 0

    # display options. Top-hat / boundaries / binary are now per ROW (stored on
    # the per-row LUT/Display boxes — see grid_view.build_lut_row), not global.
    zoom: float = 1.0

    # Rendered-grid spacing (px), independently user-tunable (Spacing ▾ in the
    # action strip). ``cell_gap`` is the gap between adjacent cells within a
    # group; ``row_group_gap`` / ``col_group_gap`` are the extra gaps inserted
    # between facet groups down the rows / across the columns (the non-uniform
    # spacing of the grouped view — different axes can differ).
    cell_gap: int = 2
    row_group_gap: int = 18
    col_group_gap: int = 18

    # Rendered-table label font sizes (px). ``label_font_pt`` sizes the per-value
    # axis labels (and the corner); ``group_font_pt`` sizes the facet-group band
    # strips. Tunable from the Properties sidebar.
    label_font_pt: int = 11
    group_font_pt: int = 11

    # Scale bar (burned into the rendered pixels so live grid == export). The bar is
    # ``round(scale_bar_um / pixel_size_um)`` px long, drawn in ``scale_bar_img_corner``
    # of the image, on the cell(s) selected by ``scale_bar_cells`` ("all", or a grid
    # corner "tl"/"tr"/"bl"/"br"). ``scale_bar_show`` is the master on/off switch and
    # defaults ON, so simply entering a pixel size (the one value the pipeline can't
    # supply — bar length defaults to 50 µm) draws the bar; the toggle then hides it
    # without clearing the values. ``pixel_size_um`` defaults to 0 (= no bar) so a
    # fresh table never shows a wrong-scale bar before it's calibrated.
    scale_bar_show: bool = True
    pixel_size_um: float = 0.0
    scale_bar_um: float = 50.0
    scale_bar_cells: str = "br"
    scale_bar_img_corner: str = "br"
    scale_bar_color: str = "auto"   # "auto" = black/white per local contrast
    # Bar thickness in px-at-a-nominal-240 px cell (scaled to each image like the
    # label font). User-tunable because a too-thin bar can disappear on downscale.
    scale_bar_thickness_px: int = 4

    # Burned-in cell labels (identity text drawn into the pixels). ``label_mode``
    # "none"/"identity"; ``label_cells`` picks which cell(s) ("all" or a grid corner);
    # ``label_img_corner`` is the in-image position; the rest are type styling.
    label_mode: str = "none"
    label_cells: str = "all"
    label_img_corner: str = "tl"
    label_font_px: int = 12
    label_color: str = "white"
    label_bg: bool = True

    # composite: the shelf's per-root Separate↔Overlay switch. When True the
    # channel axis collapses to a single entry (the OVERLAY_CHAN sentinel value)
    # and each cell additively blends (LUT-colored RGB summed, clipped to white)
    # every selected channel into one image — "blend this root's channels into one
    # cell". The layout emits the sentinel (grid_layout._axis_dim_values) and the
    # renderers (controller.image_table_generate + export) blend it via
    # image_compose.overlay_cell_rgb.
    composite: bool = False

    # export-dialog settings (pad/cell-gap/titles/dpi/transparent-bg)
    export_opts: dict = field(default_factory=dict)

    # Builder (root + nested-channels tree) authoring state.
    # ``channel_opts`` maps a channel token as it appears on an axis (e.g.
    # "GFP" or "GFP (binary)") -> a ChannelSpec-as-dict (render/boundary/
    # colormap/lut/log). The render seam (image_compose._row_* helpers) reads
    # this by the cell's channel, so per-channel display travels with the
    # channel regardless of which axis it sits on. ``fixed_vals`` remembers the
    # single value each off-axis root dimension is pinned to (the Fixed panel);
    # it mirrors the global_*_cb widgets that ``_fixed_off_axis`` reads, so the
    # Qt-free spec round-trip and persistence don't need the widgets.
    channel_opts: dict = field(default_factory=dict)
    fixed_vals: dict = field(default_factory=dict)

    # per-generate caches (rebuilt each render; never persisted)
    # {(row, col): None} — which cells the last Generate drew, NOT their pixels.
    # Holding each cell's frame here pinned the whole grid's pixels for as long as
    # the table was on screen, outside any cache budget; the consumers that read
    # it (export, the composite column) lazy-load a missing array instead, which
    # is a decode-cache hit.
    last_render: dict = field(default_factory=dict)
    image_cache: dict = field(default_factory=dict)
    # Pooled Auto-LUT window per base channel, {chan_lower: (lo, hi)}, computed each
    # Generate so a channel left on Auto uses ONE brightness window across every cell
    # (comparable) instead of each cell auto-fitting independently. Not persisted.
    auto_lut_by_channel: dict = field(default_factory=dict)
    # Set of (well, chan_lower, variant, tp, fov) tuples the parallel prefetch
    # (controller._prefetch_cell_arrays) has already warmed, across Generates —
    # separate from image_cache (which holds ref-dicts, not a per-image marker)
    # so a second Generate on an unchanged grid skips re-submitting work to the
    # thread pool instead of just cheaply re-hitting _load_array's own caches.
    prefetched_cells: set = field(default_factory=set)
    # (well, chan_lower, variant) scans that came up empty during the CURRENT
    # render pass, cleared at the top of each Generate / export run. It stops one
    # pass from re-scanning the same missing well/channel once per cell without
    # writing the miss into ``image_cache``, which outlives the pass — an empty
    # scan there would pin "no images here" for the session, so a drive that
    # stalled for a moment would leave a permanently half-empty grid.
    scan_misses: set = field(default_factory=set)
    # (well, chan_lower, tp, fov) cells that asked for the corrected (top-hat)
    # image during the CURRENT render pass and were served the raw frame because
    # the corrected set does not cover them. Same per-pass lifetime as
    # ``scan_misses``; the Generate status line reports its size so a grid that
    # mixes corrected and raw frames says so instead of reading as uniform.
    raw_fallbacks: set = field(default_factory=set)

    def clear_caches(self) -> None:
        """Drop the per-generate render / image caches (call on dataset swap)."""
        self.last_render.clear()
        self.image_cache.clear()
        self.auto_lut_by_channel.clear()
        self.prefetched_cells.clear()
        self.scan_misses.clear()
        self.raw_fallbacks.clear()

    def effective_row_dims(self) -> list:
        """Ordered row dimensions — ``row_dims`` if set, else ``[row_axis]``.

        Lets the layout code treat the flat (single-axis) and nested (multi-dim)
        cases uniformly, and keeps saved states that only have ``row_axis`` working
        with no migration."""
        return list(self.row_dims) if self.row_dims else [self.row_axis]

    def effective_col_dims(self) -> list:
        """Ordered column dimensions — ``col_dims`` if set, else ``[col_axis]``."""
        return list(self.col_dims) if self.col_dims else [self.col_axis]


# ── Builder value model (root + nested-channels tree) ───────────────────────
# The drag-and-drop shelf edits a ``TableSpec`` and flattens it onto the fields
# above via ``spec_to_state_fields`` (and restores via ``spec_from_state``). Pure
# data, so the mapping is unit-testable with no Qt.

_ROOT_DIMS = ("well", "timepoint", "fov")
# Mirror of image_compose's variant suffixes — duplicated so this module stays
# stdlib-only (its import contract forbids importing the Qt/numpy render layer).
# ``corrected`` deliberately has no suffix: it is the default render, so leaving
# its token bare keeps every previously saved view's ``channel_opts`` keys valid.
_BINARY_SUFFIX = " (binary)"
_MASK_SUFFIX = " (mask)"
_RAW_SUFFIX = " (raw)"
_RENDER_SUFFIXES = {
    "threshold": _BINARY_SUFFIX,
    "mask": _MASK_SUFFIX,
    "raw": _RAW_SUFFIX,
}


@dataclass
class ChannelSpec:
    """A nested channel + the render options applied on top of its base image.

    ``render`` ∈ {raw, corrected, mask, threshold}; ``boundary`` overlays cell
    outlines; ``colormap``/``lut_min``/``lut_max``/``log`` are the LUT. These map
    onto the per-row Source / Boundaries / Binary / colormap the renderer reads,
    but keyed by the channel so display travels with the channel on either axis.
    """

    channel: str
    render: str = "corrected"
    boundary: bool = False
    colormap: str = "Greys"
    lut_min: float | None = None
    lut_max: float | None = None
    log: bool = False
    # Binary renders only: give every label id its own colour instead of painting
    # them all white, so two nuclei that touch are not one shape.
    label_colors: bool = False

    def axis_token(self) -> str:
        """Token this channel contributes to an axis / ``active_channels``.

        The render mode is part of the token, because the token is the identity
        the whole grid is built on: ``spec_to_state_fields`` keys ``channel_opts``
        by it and ``grid_layout._axis_dim_values`` derives the channel axis from
        those keys. A token that ignored the render would give the same channel
        shown two ways — its image and its cell mask — one key and therefore one
        row, and the second chip would vanish silently.

        ``corrected`` keeps the bare token so previously saved views still
        resolve; the other three ride ``"<CHAN> (raw|mask|binary)"``.
        """
        base = str(self.channel or "").strip().upper()
        return f"{base}{_RENDER_SUFFIXES.get(self.render, '')}"

    def to_dict(self) -> dict:
        return {
            "channel": self.channel, "render": self.render,
            "boundary": bool(self.boundary), "colormap": self.colormap,
            "lut_min": self.lut_min, "lut_max": self.lut_max, "log": bool(self.log),
            "label_colors": bool(self.label_colors),
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> ChannelSpec:
        d = d or {}
        return cls(
            channel=d.get("channel", ""), render=d.get("render", "corrected"),
            boundary=bool(d.get("boundary", False)), colormap=d.get("colormap", "Greys"),
            lut_min=d.get("lut_min"), lut_max=d.get("lut_max"), log=bool(d.get("log", False)),
            label_colors=bool(d.get("label_colors", False)),
        )


@dataclass
class Axis:
    """One shelf axis: a root facet dim (or None) with channels nested under it."""

    root: str | None = None
    channels: list[ChannelSpec] = field(default_factory=list)
    overlay: bool = False

    def dims(self) -> list[str]:
        """Ordered dims this axis contributes: root (outer) then 'channel' (inner)."""
        out: list[str] = []
        if self.root:
            out.append(self.root)
        if self.channels:
            out.append("channel")
        return out


@dataclass
class TableSpec:
    """The whole builder tree. ``fixed`` = the pinned value of each root dim not on
    an axis (the derived Fixed panel); ``filters`` = per-root value filters."""

    rows: Axis = field(default_factory=Axis)
    cols: Axis = field(default_factory=Axis)
    fixed: dict[str, str] = field(default_factory=dict)
    wrap: int = 0
    # Plate (96-well) mode: lay each well's image out in true plate positions —
    # rows = plate row (A–H), cols = plate column (1–12) — using the builder's first
    # channel as the (fixed) image. Mutually exclusive with wrap. When set,
    # ``spec_to_state_fields`` overrides the axes with the virtual well_row/well_col
    # dims regardless of what roots/channels sit in the zones.
    plate: bool = False
    filters: dict[str, set] = field(default_factory=dict)

    def channel_axis(self) -> str | None:
        if self.rows.channels:
            return "rows"
        if self.cols.channels:
            return "cols"
        return None


def spec_to_state_fields(spec: TableSpec) -> dict:
    """Flatten a ``TableSpec`` onto the existing ImageTableState fields the layout
    math + render seam consume. Pure — no Qt, no ctx."""
    ax = spec.channel_axis()
    channels = (spec.rows.channels if ax == "rows"
                else spec.cols.channels if ax == "cols" else [])
    tokens: list[str] = []
    channel_opts: dict = {}
    for cs in channels:
        tok = cs.axis_token()
        tokens.append(tok)
        channel_opts[tok] = cs.to_dict()
    filt = spec.filters or {}
    fixed_vals = dict(spec.fixed or {})
    if getattr(spec, "plate", False):
        # Plate mode: force the well_row × well_col axes and hold the (first) channel
        # fixed, so each cell is that channel's image for the well at (row, col).
        if tokens:
            fixed_vals["channel"] = tokens[0]
        return {
            "row_dims": ["well_row"],
            "col_dims": ["well_col"],
            "active_channels": set(tokens),
            "active_timepoints": set(filt.get("timepoint", set()) or set()),
            "active_fovs": set(filt.get("fov", set()) or set()),
            "composite": False,
            "group_size": 0,
            "channel_opts": channel_opts,
            "fixed_vals": fixed_vals,
        }
    return {
        "row_dims": spec.rows.dims(),
        "col_dims": spec.cols.dims(),
        "active_channels": set(tokens),
        "active_timepoints": set(filt.get("timepoint", set()) or set()),
        "active_fovs": set(filt.get("fov", set()) or set()),
        "composite": bool(spec.rows.overlay or spec.cols.overlay),
        "group_size": int(spec.wrap or 0),
        "channel_opts": channel_opts,
        "fixed_vals": fixed_vals,
    }


def spec_from_state(state: ImageTableState) -> TableSpec:
    """Reconstruct the builder tree from the flattened state — inverse of
    ``spec_to_state_fields``, for restore + round-trip tests. Pure — no Qt."""
    opts = dict(getattr(state, "channel_opts", None) or {})
    overlay = bool(getattr(state, "composite", False))
    row_dims = list(getattr(state, "row_dims", None) or [])
    col_dims = list(getattr(state, "col_dims", None) or [])
    filters = {
        "timepoint": set(getattr(state, "active_timepoints", None) or set()),
        "fov": set(getattr(state, "active_fovs", None) or set()),
    }
    fixed = dict(getattr(state, "fixed_vals", None) or {})

    if row_dims == ["well_row"] and col_dims == ["well_col"]:
        # Plate mode — carry the fixed channel back onto the rows axis so the shelf's
        # channel picker (and the next apply) stay consistent.
        channels = [ChannelSpec.from_dict(opts[tok]) for tok in opts]
        return TableSpec(
            rows=Axis(root=None, channels=channels, overlay=False),
            cols=Axis(root=None, channels=[], overlay=False),
            fixed=fixed,
            plate=True,
            filters=filters,
        )

    def axis_from(dims) -> Axis:
        dims = list(dims or [])
        root = next((d for d in dims if d in _ROOT_DIMS), None)
        channels: list[ChannelSpec] = []
        if "channel" in dims:
            channels = [ChannelSpec.from_dict(opts[tok]) for tok in opts]
        return Axis(root=root, channels=channels, overlay=overlay if channels else False)

    return TableSpec(
        rows=axis_from(row_dims),
        cols=axis_from(col_dims),
        fixed=fixed,
        wrap=int(getattr(state, "group_size", 0) or 0),
        filters=filters,
    )
