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
from typing import Dict, List, Optional


@dataclass
class ImageTableState:
    """Image Table data + options + per-generate caches.

    ``rows`` / ``cols`` mirror the grid spinners; ``row_axis`` / ``col_axis``
    are the layout-builder axis keys ("channel"/"well"/"timepoint"/"fov").
    ``active_wells`` / ``active_channels`` are the sidebar / dropdown
    multi-selections. ``zoom`` is the only global display option; top-hat /
    boundaries / binary are per-row (on the LUT/Display boxes). ``last_render``
    / ``image_cache`` are the hot-path caches rebuilt by ``image_table_generate``
    (not persisted).
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

    # composite: when True, image_table_generate appends one extra row at the
    # bottom whose cells additively merge (LUT-colored RGB summed, clipped to
    # white) every row above them in the same column. A transient display
    # toggle driven by the Composite checkbox in the action strip.
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
    last_render: dict = field(default_factory=dict)
    image_cache: dict = field(default_factory=dict)

    def clear_caches(self) -> None:
        """Drop the per-generate render / image caches (call on dataset swap)."""
        self.last_render.clear()
        self.image_cache.clear()

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
# Mirror of image_compose._BINARY_SUFFIX — duplicated so this module stays
# stdlib-only (its import contract forbids importing the Qt/numpy render layer).
_BINARY_SUFFIX = " (binary)"


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
    lut_min: Optional[float] = None
    lut_max: Optional[float] = None
    log: bool = False

    def axis_token(self) -> str:
        """Token this channel contributes to an axis / ``active_channels``.

        A threshold channel rides the ``"<CHAN> (binary)"`` virtual-channel token
        (the plumbing added on main); every other render keeps the plain token and
        resolves its render from ``channel_opts``."""
        base = str(self.channel or "").strip().upper()
        return f"{base}{_BINARY_SUFFIX}" if self.render == "threshold" else base

    def to_dict(self) -> dict:
        return {
            "channel": self.channel, "render": self.render,
            "boundary": bool(self.boundary), "colormap": self.colormap,
            "lut_min": self.lut_min, "lut_max": self.lut_max, "log": bool(self.log),
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "ChannelSpec":
        d = d or {}
        return cls(
            channel=d.get("channel", ""), render=d.get("render", "corrected"),
            boundary=bool(d.get("boundary", False)), colormap=d.get("colormap", "Greys"),
            lut_min=d.get("lut_min"), lut_max=d.get("lut_max"), log=bool(d.get("log", False)),
        )


@dataclass
class Axis:
    """One shelf axis: a root facet dim (or None) with channels nested under it."""

    root: Optional[str] = None
    channels: List[ChannelSpec] = field(default_factory=list)
    overlay: bool = False

    def dims(self) -> List[str]:
        """Ordered dims this axis contributes: root (outer) then 'channel' (inner)."""
        out: List[str] = []
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
    fixed: Dict[str, str] = field(default_factory=dict)
    wrap: int = 0
    filters: Dict[str, set] = field(default_factory=dict)

    def channel_axis(self) -> Optional[str]:
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
    tokens: List[str] = []
    channel_opts: dict = {}
    for cs in channels:
        tok = cs.axis_token()
        tokens.append(tok)
        channel_opts[tok] = cs.to_dict()
    filt = spec.filters or {}
    return {
        "row_dims": spec.rows.dims(),
        "col_dims": spec.cols.dims(),
        "active_channels": set(tokens),
        "active_timepoints": set(filt.get("timepoint", set()) or set()),
        "active_fovs": set(filt.get("fov", set()) or set()),
        "composite": bool(spec.rows.overlay or spec.cols.overlay),
        "group_size": int(spec.wrap or 0),
        "channel_opts": channel_opts,
        "fixed_vals": dict(spec.fixed or {}),
    }


def spec_from_state(state: "ImageTableState") -> TableSpec:
    """Reconstruct the builder tree from the flattened state — inverse of
    ``spec_to_state_fields``, for restore + round-trip tests. Pure — no Qt."""
    opts = dict(getattr(state, "channel_opts", None) or {})
    overlay = bool(getattr(state, "composite", False))

    def axis_from(dims) -> Axis:
        dims = list(dims or [])
        root = next((d for d in dims if d in _ROOT_DIMS), None)
        channels: List[ChannelSpec] = []
        if "channel" in dims:
            channels = [ChannelSpec.from_dict(opts[tok]) for tok in opts]
        return Axis(root=root, channels=channels, overlay=overlay if channels else False)

    return TableSpec(
        rows=axis_from(getattr(state, "row_dims", None)),
        cols=axis_from(getattr(state, "col_dims", None)),
        fixed=dict(getattr(state, "fixed_vals", None) or {}),
        wrap=int(getattr(state, "group_size", 0) or 0),
        filters={
            "timepoint": set(getattr(state, "active_timepoints", None) or set()),
            "fov": set(getattr(state, "active_fovs", None) or set()),
        },
    )
