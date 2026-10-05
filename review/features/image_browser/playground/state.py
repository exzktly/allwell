"""Playground tab — typed feature-local state.

Per ARCHITECTURE.md §5.9 the Playground's runtime *data* (the chosen frame, the
selected correction method + parameters, the cached reference-well fields, and
the view/display options) lives here as a typed dataclass aggregated on
``AppState`` (``ctx.state.playground``), rather than as loose
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
    well: str | None = None
    channel: str | None = None        # uppercase fluor token, e.g. "GFP"
    timepoint: str | None = None
    fov: str | None = None

    # Loose-image mode: one file opened straight from disk, bypassing the
    # dataset loaders entirely. Set, this WINS over the four selectors above —
    # it is how the tab is usable before a plate has ever been analysed, which
    # is exactly when segmentation parameters want tuning. Cleared on dataset
    # load (see clear_caches) so opening a plate returns to the plate.
    loose_image_path: str | None = None

    # correction method selection (same values as the Analyze tab / pipeline)
    flatfield_mode: str = "none"        # FLATFIELD_MODES
    background_method: str = "none"     # BACKGROUND_METHODS
    radius: int = 50
    fft_small: float = 0.0
    mesh_ratio: float = 3.0
    mesh_sigma: float = 3.0

    # Reference-well models (empty_well / basic).
    #
    # Keyed by everything that decides WHICH field gets estimated, because the
    # estimators differ: ``("ff", well, chan, mode)`` for the mean-1 illumination
    # field — ``basic`` and ``empty_well`` produce different fields from the same
    # frames, so the mode is part of the key — and ``("bg", well, chan)`` for the
    # additive background, which has only one estimator. A cache keyed on less
    # than this answers "already built" for a field it never built, and the
    # correction then silently does nothing (``apply_flatfield`` no-ops on None).
    background_well: str | None = None
    ref_fields: dict = field(default_factory=dict)    # key -> field array
    # Keys whose build was ATTEMPTED and produced nothing, with the reason. Held
    # apart from ref_fields so "not built yet" and "cannot be built" stay
    # distinguishable: the first triggers a build, the second must not (it would
    # respawn a worker on every keystroke) and is reported instead of applied.
    ref_missing: dict = field(default_factory=dict)   # key -> why

    # view
    source: str = "corrected"           # "raw" | "flatfield" | "corrected"
    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0

    # display controls (mirror the Segmentation review image)
    cmap_name: str = "Greys_r"
    # Per-source LUT memory: source ("raw"/"flatfield"/"corrected") -> (min, max)
    # entry texts, so each stage keeps the range the user set for it.
    lut_by_source: dict = field(default_factory=dict)
    binary_mask: bool = False
    # Colour each label in binary mode instead of painting them all white.
    # Default on: touching cells drawn the same white merge into one shape, and
    # "did it split those two nuclei?" is the main question this tab is asked.
    binary_label_colors: bool = True
    show_overlay: bool = True           # precomputed-mask boundary overlay on/off
    tint_color: tuple = (255, 255, 255)
    # Frame-local binary threshold (intensity units). None ⇒ fall back to the
    # channel's app-wide ThreshFracOn as the default; editing it here NEVER
    # writes back to the app-wide cell-gating value.
    threshold: float | None = None
    # QC size-based cleanup (px², 0 = no bound) — drops mask labels outside the
    # bounds from the overlay / binary, previewing the pipeline's debris gate.
    qc_min_area: float = 0.0
    qc_max_area: float = 0.0

    # ── live StarDist preview ────────────────────────────────────────────────
    # Segment the displayed frame here, with tunable thresholds, instead of only
    # showing the labels a pipeline run already baked. ``seg_live`` gates the
    # whole feature; the thresholds are None for "the pretrained model's own
    # default", the same spelling the pipeline uses.
    seg_live: bool = False
    seg_prob_thresh: float | None = None
    seg_nms_thresh: float | None = None
    # The last computed label image and the identity it was computed from
    # (frame + correction + thresholds). When the key no longer matches what is
    # on screen the labels are STALE and must be reported as such rather than
    # drawn as if they described the current frame.
    seg_labels: object = None
    seg_key: tuple | None = None
    seg_status: str = ""
    seg_running: bool = False
    # ── threshold sweep ──────────────────────────────────────────────────────
    # Nucleus count against the probability threshold, for the frame on screen.
    # The result is a ``review.domain.threshold_sweep.ThresholdCurve``; it is
    # held as ``object`` for the same reason ``seg_labels`` is, so this module
    # stays free of anything it would otherwise have to import.
    sweep_curve: object = None
    # The last focus distribution drawn, held as ``object`` for the same
    # reason ``sweep_curve`` is: this module is a dataclass and importing the
    # domain type for an annotation alone would give it a dependency it does
    # not otherwise have.
    focus_distribution: object = None
    # What the curve on screen describes, captured when the sweep was
    # DISPATCHED. ``_seg_key``'s tuple minus the probability threshold, which
    # the sweep varies by design. Held rather than re-derived because the tab
    # stays fully interactive for the minutes a sweep takes: a key read when
    # the sweep finishes is the key of whatever the user has clicked on since,
    # and comparing that against itself can never find anything stale.
    sweep_key: tuple | None = None
    sweep_running: bool = False
    # Set by the Stop button and polled inside the sweep loop, which is the
    # only long-running thing this tab starts: M segmentations rather than one.
    sweep_stop: bool = False
    sweep_status: str = ""
    # Set when the frame segmented was not the stage on screen (segmentation
    # always runs on the corrected frame, as the pipeline does). Reported with
    # the nucleus count so a count can never be read as belonging to a picture
    # it did not come from.
    seg_stage_note: str = ""
    # An out-of-range threshold typed into either field. Held separately from
    # seg_status so it is reported even with the preview switched off — a value
    # that is quietly not being used has to say so wherever it was typed.
    seg_param_error: str = ""

    # What the last render actually did, recorded by ``_render`` so the read-out
    # can state it rather than re-derive it (a second derivation is a second
    # chance to describe something the renderer did not do). Keys are documented
    # by ``controller._display_facts``.
    display_facts: dict = field(default_factory=dict)

    # per-render caches (rebuilt on change; never persisted)
    raw_arr: object = None              # last-loaded raw frame
    mask_arr: object = None             # last-loaded precomputed labels
    display_arr: object = None          # the array currently shown (raw or corrected)
    image_cache: dict = field(default_factory=dict)
    base_rgb: object = None             # last rendered RGB (for zoom/pan re-blit)
    # Memoized correct_single_frame() result + the identity key it was computed
    # from, so playground_on_change can skip re-running the expensive pipeline
    # (~1.8s+ per call at 1024², per pipeline/background.py's own docstring) when
    # nothing that affects the output has actually changed -- covers a redundant
    # re-render on tab (re)activation and toggling "Source" back and forth.
    # Single slot: this tab shows one frame at a time.
    last_correction_key: tuple | None = None
    last_corrected_arr: object = None

    def clear_caches(self) -> None:
        """Drop per-frame caches + reference fields (call on dataset swap)."""
        self.raw_arr = None
        self.mask_arr = None
        self.display_arr = None
        self.base_rgb = None
        self.image_cache.clear()
        self.ref_fields.clear()
        self.ref_missing.clear()
        self.last_correction_key = None
        self.last_corrected_arr = None
        # Loading a plate is a deliberate "work on this dataset" act, so it ends
        # loose-image mode rather than leaving the tab showing a stray file while
        # the sidebar plate appears to drive it.
        self.loose_image_path = None
        self.seg_labels = None
        self.sweep_curve = None
        self.focus_distribution = None
        self.sweep_key = None
        self.sweep_running = False
        self.sweep_stop = False
        self.sweep_status = ""
        self.seg_key = None
        self.seg_status = ""
