"""Review-image and preview/montage domain state.

The Review Image tab and the image-preview panel carried the largest cluster of
loose ``self._*`` attributes on ``WellViewerApp`` — view transform, drag /
box-select bookkeeping, decoded-frame caches, overlay colours, and the preview
image maps.

Per the teardown plan this phase is deliberately conservative: these are data
containers with only the few genuinely cohesive behaviours (view reset, cache
invalidation). Widget handles and the lazily-built montage arrays stay on the
app as residual coupling.

Qt-free: imports only stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ReviewImageState:
    """Review Image tab state: view transform, interaction bookkeeping,
    decoded-frame caches, and overlay colours.

    ``zoom`` / ``pan_x`` / ``pan_y`` are the canvas view transform;
    ``preserve_view_on_refresh`` keeps them across a redraw. The three
    ``*_cache`` fields memoise the hot decode / threshold paths and are dropped
    by :meth:`clear_caches` when the dataset or overrides change.
    """

    # selection / include-edit
    selected_nucleus: int | None = None
    nucleus_to_iid: dict[int, str] = field(default_factory=dict)
    include_edit_mode: bool = False
    box_anchor: tuple[float, float] | None = None
    box_active: bool = False

    # view transform
    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0
    preserve_view_on_refresh: bool = False

    # drag bookkeeping
    dragging: bool = False
    drag_moved: bool = False
    drag_last_xy: tuple[int, int] = (0, 0)

    # decoded frame
    base_pil: object = None
    is_tif: bool = False
    last_fluor_arr: object = None
    lut_by_channel: dict[str, tuple[float, float]] = field(default_factory=dict)

    # caches (hot path)
    frame_cache: dict | None = None
    include_cache: dict = field(default_factory=dict)
    threshold_map_cache: dict = field(default_factory=dict)
    override_version: int = 0

    # render options
    show_raw: bool = False
    binary_mask: bool = False
    show_outline: bool = True
    boundary_color: tuple[int, int, int] = (255, 64, 64)
    selected_color: tuple[int, int, int] = (255, 230, 64)
    tint_color: tuple[int, int, int] = (255, 255, 255)

    def reset_view(self) -> None:
        """Return the canvas to the un-zoomed, un-panned home view."""
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0

    def clear_caches(self) -> None:
        """Drop the decoded-frame / include / threshold caches — call when the
        dataset or the cell-inclusion overrides change underneath them."""
        self.frame_cache = None
        self.include_cache.clear()
        self.threshold_map_cache.clear()


@dataclass
class PreviewState:
    """Image-preview panel state: the selected well, the decoded image maps,
    and the montage thumbnail list.

    ``fluor`` / ``overlay`` / ``mask`` map a ``(fov, tp)`` key to a decoded
    image reference for the preview panel.
    """

    selected_well: str | None = None
    fluor: dict = field(default_factory=dict)
    overlay: dict = field(default_factory=dict)
    mask: dict = field(default_factory=dict)
    montage_photos: list = field(default_factory=list)
