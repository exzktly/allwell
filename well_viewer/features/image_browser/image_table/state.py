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

    # per-generate caches (rebuilt each render; never persisted)
    last_render: dict = field(default_factory=dict)
    image_cache: dict = field(default_factory=dict)

    def clear_caches(self) -> None:
        """Drop the per-generate render / image caches (call on dataset swap)."""
        self.last_render.clear()
        self.image_cache.clear()
