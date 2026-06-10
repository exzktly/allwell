"""Playground tab — typed feature-local state.

Per ARCHITECTURE.md §5.9 the Playground's runtime *data* (the chosen frame, the
selected correction method + parameters, the cached reference-well fields, and
the view/display options) lives here as a typed dataclass aggregated on
``AppState`` (``app._app_state.playground``), rather than as loose
``app._playground_*`` data attributes. Qt widget handles (the selector combos,
the canvas QLabel, LUT QLineEdits, the ``LutSelector``) stay on ``app`` as
residual coupling — only plain data moves here.

Qt-free: imports only stdlib, so controllers can import it with no cycle (the
``tests/test_guardrails.py`` import contract enforces that boundary).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlaygroundState:
    """Playground frame selection + method/params + reference fields + view."""

    # frame selection (match the image_table option helpers' tokens)
    well: "str | None" = None
    channel: "str | None" = None        # uppercase fluor token, e.g. "GFP"
    timepoint: "str | None" = None
    fov: "str | None" = None

    # correction method selection (same values as the Analyze tab / pipeline)
    flatfield_mode: str = "none"        # FLATFIELD_MODES
    background_method: str = "none"     # BACKGROUND_METHODS
    radius: int = 50
    fft_small: float = 0.0
    mesh_ratio: float = 3.0
    mesh_sigma: float = 3.0

    # reference-well models (empty_well / basic), keyed by lowercase channel token
    background_well: "str | None" = None
    ff_field_by_chan: dict = field(default_factory=dict)   # token -> mean-1 field
    bg_field_by_chan: dict = field(default_factory=dict)   # token -> raw additive field
    model_well: "str | None" = None     # which well the cached fields were built from

    # view
    source: str = "corrected"           # "raw" | "flatfield" | "corrected"
    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0

    # display controls (mirror the Segmentation review image)
    cmap_name: str = "Greys_r"
    binary_mask: bool = False
    show_overlay: bool = True           # precomputed-mask boundary overlay on/off
    tint_color: tuple = (255, 255, 255)
    # Frame-local binary threshold (intensity units). None ⇒ fall back to the
    # channel's app-wide ThreshFracOn as the default; editing it here NEVER
    # writes back to the app-wide cell-gating value.
    threshold: "float | None" = None

    # per-render caches (rebuilt on change; never persisted)
    raw_arr: object = None              # last-loaded raw frame
    mask_arr: object = None             # last-loaded precomputed labels
    display_arr: object = None          # the array currently shown (raw or corrected)
    image_cache: dict = field(default_factory=dict)
    base_rgb: object = None             # last rendered RGB (for zoom/pan re-blit)

    def clear_caches(self) -> None:
        """Drop per-frame caches + reference fields (call on dataset swap)."""
        self.raw_arr = None
        self.mask_arr = None
        self.display_arr = None
        self.base_rgb = None
        self.image_cache.clear()
        self.ff_field_by_chan.clear()
        self.bg_field_by_chan.clear()
        self.model_well = None
