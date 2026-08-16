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
    include_edit_mode: bool = False
    crop_edit_mode: bool = False
    box_anchor: tuple[float, float] | None = None
    box_active: bool = False

    # Armed scope for the drag-delete: which wells / FOVs / timepoints the next
    # rubber-band delete applies to, beyond the frame it is drawn on.
    #
    # ``None`` means "whatever the control row is showing" — the displayed well,
    # FOV or timepoint. An *empty set* is a different thing: the user unchecked
    # every entry in that dropdown, which resolves to no targets at all. The two
    # must not collide, which is why this is ``set | None`` rather than the
    # empty-set-means-all convention ``ImageTableState.active_fovs`` uses.
    #
    # Transient by design: never persisted, and cleared on a dataset swap, so an
    # armed plate-wide scope can never come back on the next session or bind to
    # a different plate's wells.
    delete_scope_wells: set[str] | None = None
    delete_scope_fovs: set[str] | None = None
    delete_scope_tps: set[str] | None = None

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
    # Per-channel display LUT moved to the shared ``AppState.image_view``
    # (core/image_view_state.py) so Review Image and the Image Table share one
    # brightness-per-channel default (S4).

    # caches (hot path)
    frame_cache: dict | None = None
    include_cache: dict = field(default_factory=dict)
    threshold_map_cache: dict = field(default_factory=dict)
    override_version: int = 0
    # {nucleus_id: mean fluorescence intensity} for the currently displayed
    # channel / FOV / timepoint — rebuilt each full refresh so the hover tooltip
    # can report the cell-under-cursor's MFI without re-slicing the CSV per move.
    mfi_by_nid: dict | None = None

    # render options
    # Which image stage the canvas shows: "corrected" (final = flat-field +
    # background, the saved top-hat image — default), "raw" (unprocessed
    # fluorescence), or "flatfield" (raw with only the selected flat-field
    # applied, recomputed on the fly — a validation view of the flat-field step
    # in isolation, before any background/top-hat subtraction).
    image_source: str = "corrected"
    # Per-(channel, flatfield-mode) cache of the recomputed mean-1 flat-field
    # field for the "flatfield" view, so the (possibly multi-image) estimate is
    # computed once per channel. Cleared on dataset load / rescan.
    flatfield_field_cache: dict = field(default_factory=dict)
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

    def reset_delete_scope(self) -> None:
        """Disarm the drag-delete scope back to "the displayed frame only".

        Called on a dataset swap alongside ``PreviewState.clear_caches`` — the
        well tokens in an armed scope belong to the plate that was loaded when
        it was armed, and silently carrying them into the next dataset is how a
        stray plate-wide delete happens.
        """
        self.delete_scope_wells = None
        self.delete_scope_fovs = None
        self.delete_scope_tps = None

    def clear_caches(self) -> None:
        """Drop the decoded-frame / include / threshold caches — call when the
        dataset or the cell-inclusion overrides change underneath them."""
        self.frame_cache = None
        self.include_cache.clear()
        self.threshold_map_cache.clear()
        self.flatfield_field_cache.clear()


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
    # Top-hat (background-subtracted) fluorescence refs decoded alongside
    # ``fluor``; the Review Image "corrected" stage prefers these. Transient,
    # rebuilt on every ``update_preview`` — was the loose ``app._preview_tophat_fluor``.
    tophat_fluor: dict = field(default_factory=dict)
    # Re-entry guard for ``update_preview``: it drives the FOV combo, whose
    # signal handler re-enters the refresh path, which can re-enter here through
    # the channel / well callbacks. Was the loose ``app._update_preview_in_progress``.
    update_in_progress: bool = False

    def clear_caches(self) -> None:
        """Drop the decoded preview image maps — call on dataset swap so the
        panel never shows the previous dataset's pixels before the next
        ``update_preview`` redecodes."""
        self.fluor.clear()
        self.overlay.clear()
        self.mask.clear()
        self.tophat_fluor.clear()
