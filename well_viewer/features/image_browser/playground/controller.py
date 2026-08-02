"""Playground tab — controller (logic, event handlers, no Qt-app import).

Drives the single-frame correction testbed: populate the well/channel/timepoint/
fov selectors (reusing the Image Table option helpers), load the chosen RAW frame
and its precomputed segmentation mask (reusing the Image Table loaders), apply the
chosen correction via the shared ``process_microscopy.correct_single_frame``
(identical code path to the batch pipeline), and render through the Playground
renderer. Reference-well methods (empty_well / basic) build their per-channel
field on a worker thread.

Widget reads are all guarded (``getattr``) so the controller can be driven purely
through ``app._app_state.playground`` in headless tests.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

import process_microscopy as pm
from well_viewer.features.image_browser.image_table.controller import (
    _channel_options, _fov_options, _load_array, _load_mask_array,
    _timepoint_options, _well_options,
)
from well_viewer.features.image_browser.playground.renderer import (
    build_playground_rgb, render_playground_display,
)
from well_viewer.features.image_browser.playground.worker import (
    request_reference_fields, request_stardist_labels,
)


def _st(ctx):
    return ctx.state.playground


def _is_real_channel(label: str) -> bool:
    """Exclude the virtual NUC+SEG overlay and ratio channels — the Playground
    corrects real single-channel frames only."""
    return bool(label) and label != "NUC+SEG" and "/" not in label


# ── dropdown population ──────────────────────────────────────────────────────

def _set_combo(win, attr: str, options, current):
    cb = getattr(win, attr, None)
    if cb is None:
        return
    from well_viewer.core.ui_helpers import set_combo_values
    set_combo_values(cb, options or ["—"])
    if current and cb.findText(str(current)) >= 0:
        blocked = cb.blockSignals(True)
        try:
            cb.setCurrentText(str(current))
        finally:
            cb.blockSignals(blocked)


def playground_repopulate_dropdowns(ctx) -> None:
    """Refresh the frame selectors from the loaded dataset (idempotent).

    The well is *not* a combo here — it is driven by the ever-present sidebar
    plate (single-well on this tab); see :func:`playground_sync_selected_well`.
    """
    st = ctx.state.playground
    wells = _well_options(ctx)
    channels = [c for c in _channel_options(ctx) if _is_real_channel(c)]
    tps = _timepoint_options(ctx)
    fovs = _fov_options(ctx)
    st.well = st.well or (wells[0] if wells else None)
    st.channel = st.channel or (channels[0] if channels else None)
    st.timepoint = st.timepoint or (tps[0] if tps else None)
    st.fov = st.fov or (fovs[0] if fovs else None)
    # The playground combos are not registered; reach them (widgets) via the
    # blessed ctx.window accessor. _set_combo stays app/window-first (widget I/O).
    _set_combo(ctx.window, "_playground_chan_cb", channels, st.channel)
    _set_combo(ctx.window, "_playground_tp_cb", tps, st.timepoint)
    _set_combo(ctx.window, "_playground_fov_cb", fovs, st.fov)


# ── sidebar-plate well selection (single-well tab) ───────────────────────────

def playground_first_selected_well(ctx):
    """Lowest-plate-rank well in the current sidebar selection (``None`` if the
    selection is empty). The Playground is single-well, so on entry from a
    multi-well tab this picks a stable, well-ordered representative."""
    from well_viewer.domain.selections_model import well_rank
    wells = [w for w in ctx.state.selection.selected_wells
             if w in ctx.state.dataset.well_paths]
    return min(wells, key=well_rank) if wells else None


def playground_sync_selected_well(ctx) -> None:
    """Adopt the sidebar plate's selected well as the frame's well and re-render.

    Wired as the Playground's ``on_selection_changed`` hook, so a click on the
    ever-present sidebar plate drives the rendered frame. No-op (keeps the
    current frame) when nothing is selected or the well is unchanged."""
    tok = playground_first_selected_well(ctx)
    st = _st(ctx)
    if tok is None or tok == st.well:
        return
    st.well = tok
    playground_on_change(ctx)


def playground_activate(ctx) -> None:
    """Refresh the frame selectors, then reconcile the sidebar plate with the
    frame's single well and render. Called when the Playground sub-tab becomes
    active; mirrors smFISH's collapse-to-one-well activation so exactly one well
    is lit on the plate and it matches what's rendered."""
    playground_repopulate_dropdowns(ctx)
    keep = playground_first_selected_well(ctx) or _st(ctx).well
    sel = ctx.state.selection
    if keep is not None and sel.selected_wells != {keep}:
        from well_viewer.core.selection_controller import set_selected_wells
        set_selected_wells(ctx, {keep}, commit=False)
    _st(ctx).well = keep
    playground_on_change(ctx)


# ── widget → state ───────────────────────────────────────────────────────────

def _read_combo(win, attr: str):
    cb = getattr(win, attr, None)
    if cb is None:
        return None
    txt = str(cb.currentText() or "").strip()
    return txt if txt and txt != "—" else None


def _read_float(win, attr: str, default: float) -> float:
    from well_viewer.core.ui_helpers import parse_float_edit
    return parse_float_edit(getattr(win, attr, None), default)


def _read_widgets_into_state(ctx) -> None:
    """Pull current widget values into state. No-op for widgets not built yet
    (so headless tests can set ``ctx.state.playground`` fields directly)."""
    st = _st(ctx)
    win = ctx.window
    # Well is driven by the ever-present sidebar plate (single-well on this tab)
    # via ``playground_sync_selected_well``, so it's not read from a combo here.
    for attr, field in (("_playground_chan_cb", "channel"),
                        ("_playground_tp_cb", "timepoint"),
                        ("_playground_fov_cb", "fov")):
        v = _read_combo(win, attr)
        if v is not None:
            setattr(st, field, v)
    bg_cb = getattr(win, "_playground_bg_cb", None)
    if bg_cb is not None:
        st.background_method = str(bg_cb.currentText() or "none")
    ff_cb = getattr(win, "_playground_ff_cb", None)
    if ff_cb is not None:
        st.flatfield_mode = str(ff_cb.currentText() or "none")
    bw = getattr(win, "_playground_bgwell_edit", None)
    if bw is not None:
        st.background_well = str(bw.text() or "").strip() or None
    st.radius = int(_read_float(win, "_playground_radius_edit", st.radius))
    st.fft_small = _read_float(win, "_playground_fft_small_edit", st.fft_small)
    st.mesh_ratio = _read_float(win, "_playground_mesh_ratio_edit", st.mesh_ratio)
    st.mesh_sigma = _read_float(win, "_playground_mesh_sigma_edit", st.mesh_sigma)
    st.qc_min_area = _read_float(win, "_playground_qc_min_area_edit", st.qc_min_area)
    st.qc_max_area = _read_float(win, "_playground_qc_max_area_edit", st.qc_max_area)
    _read_seg_thresholds(win, st)
    lut = getattr(win, "_playground_cmap_cb", None)
    if lut is not None and hasattr(lut, "lut"):
        name = lut.lut()
        st.cmap_name = (name + "_r") if (hasattr(lut, "isReversed") and lut.isReversed()) else name


def _read_seg_thresholds(win, st) -> None:
    """Pull the two StarDist threshold edits into state.

    Blank / ``auto`` means the pretrained model's own default — the same rule the
    CLI and the Analyze form use, applied by the same
    ``pipeline.stardist.parse_threshold``. An out-of-range entry is *reported*,
    not quietly treated as blank: silently segmenting with a different threshold
    than the one on screen is the substitution this codebase keeps banning.
    """
    from pipeline.stardist import parse_threshold
    errors: list[str] = []
    for attr, field, label in (
        ("_playground_seg_prob_edit", "seg_prob_thresh", "Probability"),
        ("_playground_seg_nms_edit", "seg_nms_thresh", "Overlap (NMS)"),
    ):
        e = getattr(win, attr, None)
        if e is None:
            continue
        try:
            setattr(st, field, parse_threshold(e.text()))
        except ValueError:
            setattr(st, field, None)
            errors.append(label)
    st.seg_param_error = (
        f"{' and '.join(errors)} must be between 0 and 1 — using the model's "
        f"default instead" if errors else "")


# ── LUT ──────────────────────────────────────────────────────────────────────

def _resolve_lut(win, arr):
    """Parse the playground LUT min/max edits ('auto'/blank/number); fall back to
    the array's min/max. Mirrors review_image_resolve_lut."""
    def _num(attr, fallback):
        e = getattr(win, attr, None)
        if e is None:
            return fallback
        txt = str(e.text() or "").strip().lower()
        if not txt or txt == "auto":
            return fallback
        try:
            return float(txt)
        except ValueError:
            return fallback
    a = np.asarray(arr, dtype=np.float32)
    lo = _num("_playground_lmin_entry", float(a.min()) if a.size else 0.0)
    hi = _num("_playground_lmax_entry", float(a.max()) if a.size else 1.0)
    if hi <= lo:
        hi = lo + 1.0
    # NB: do NOT write the computed range back over an "auto" entry. Keeping
    # "auto" live means every correction-parameter change re-derives the window
    # from the current frame, so subtle changes (e.g. robust_mesh tile/sigma)
    # are reflected on screen instead of being masked by a stale, locked LUT.
    return lo, hi


def _set_status(win, text: str) -> None:
    from well_viewer.core.ui_helpers import set_widget_text
    set_widget_text(win, "_playground_status", text)


# ── main render ──────────────────────────────────────────────────────────────

def _load_loose_frame(ctx, path: str):
    """Load one image file straight from disk, cached by path.

    Uses the pipeline's own ``load_2d_gray``, so the array is what a batch run
    would have segmented rather than a lookalike read. Cached in ``image_cache``
    because ``playground_on_change``'s correction memo is keyed on the returned
    object's ``id()`` — a fresh array per call would miss it every time and
    re-run the (~1.8 s at 1024²) correction on every keystroke.
    """
    cache = _st(ctx).image_cache
    key = ("loose", path)
    if key in cache:
        return cache[key]
    try:
        arr = pm.load_2d_gray(Path(path))
    except Exception as exc:
        # Deliberately broad: the user can point this at any file, and each
        # imaging library raises its own type for "that is not an image I can
        # read" (tifffile's TiffFileError descends straight from Exception, not
        # OSError). Not silent — it is logged here and named in the status line
        # by the caller, so a file that will not open says so.
        from well_viewer.core.safe import swallowed
        swallowed(f"playground: could not read loose image {path!r}: {exc}")
        return None
    cache[key] = arr
    return arr


def _resolve_frame(ctx):
    """The frame to work on: ``(raw, label, frame_key)``, or ``(None, msg, None)``.

    Two sources, in priority order. A loose image opened from disk wins — it is
    an explicit "work on this file" act and does not need a dataset at all. With
    no loose image, the frame is the dataset one addressed by the sidebar plate's
    well plus the channel / timepoint / FOV combos.
    """
    st = _st(ctx)
    loose = str(st.loose_image_path or "").strip()
    if loose:
        raw = _load_loose_frame(ctx, loose)
        if raw is None:
            return None, f"Could not read {Path(loose).name}.", None
        # An arbitrary file has no pipeline-produced segmentation, so the only
        # mask it can show is one the live preview computes.
        st.mask_arr = None
        return raw, Path(loose).name, ("loose", loose)

    if not (st.well and st.channel and st.timepoint and st.fov):
        return None, "Select a well, channel, timepoint, and FOV — or open a single image.", None
    if ctx.state.dataset.data_dir is None:
        return None, "Load a dataset first, or open a single image.", None
    raw = _load_array(ctx, st.image_cache, st.well, st.channel, st.timepoint,
                      st.fov, use_tophat=False)
    if raw is None:
        return None, "No raw image for the selected frame.", None
    st.mask_arr = _load_mask_array(ctx, st.image_cache, st.well, st.timepoint, st.fov)
    return (raw,
            f"{st.well} {st.channel} t{st.timepoint} fov{st.fov}",
            (st.well, st.channel, st.timepoint, st.fov))


def _effective_correction_modes(ctx, *, loose: bool) -> tuple[str, str, str]:
    """``(flatfield_mode, background_method, refusal)`` for this render.

    The reference-well modes (``empty_well`` / ``basic``) estimate their field
    from a whole reference well, which a lone file on disk does not have. Rather
    than render an uncorrected frame while the controls still claim a correction
    is applied, those modes are dropped for loose images and *named* in the
    returned refusal so the status line can say what did not run.
    """
    st = _st(ctx)
    ff_mode = st.flatfield_mode
    bg_method = st.background_method if st.source == "corrected" else "none"
    if not loose:
        return ff_mode, bg_method, ""
    dropped: list[str] = []
    if ff_mode in ("empty_well", "basic"):
        dropped.append(f"flat-field {ff_mode}")
        ff_mode = "none"
    if bg_method == "empty_well":
        dropped.append("background empty_well")
        bg_method = "none"
    if not dropped:
        return ff_mode, bg_method, ""
    return ff_mode, bg_method, (
        f"{' and '.join(dropped)} need a loaded plate to sample — not applied")


def playground_on_change(ctx) -> None:
    """Reload + recorrect + rerender on any selector / param / toggle change."""
    st = _st(ctx)
    win = ctx.window
    _read_widgets_into_state(ctx)

    raw, frame_lbl, frame_key = _resolve_frame(ctx)
    if raw is None:
        st.raw_arr = None
        _set_status(win, frame_lbl)          # frame_lbl carries the reason
        return
    st.raw_arr = np.asarray(raw, dtype=np.float32)
    loose = bool(str(st.loose_image_path or "").strip())

    # The binary threshold is per-frame: when the frame identity changes, drop
    # any local override so the control falls back to the channel's app-wide
    # ThreshFracOn default, and refresh the displayed value.
    if getattr(ctx.runtime.playground, "frame_key", None) != frame_key:
        ctx.runtime.playground.frame_key = frame_key
        st.threshold = None
        _refresh_threshold_display(ctx)

    # Raw — no correction.
    if st.source == "raw":
        _render(ctx, st.raw_arr)
        _set_status(win, _with_seg_note(ctx, f"Raw — {frame_lbl}"))
        return

    ff_mode, bg_method, refusal = _effective_correction_modes(ctx, loose=loose)
    chan_lower = (st.channel or "").lower()
    if not loose and _source_needs_reference(st):
        if not st.background_well:
            _set_status(win, "Pick a background well for empty_well / basic.")
            _render(ctx, st.raw_arr)
            return
        if st.model_well != st.background_well:
            _build_reference_then_render(ctx, chan_lower)
            return
        ff = st.ff_field_by_chan.get(chan_lower)
        bg = st.bg_field_by_chan.get(chan_lower)
    else:
        ff = bg = None

    # id(raw) (the pre-np.asarray object _resolve_frame returned, above) is a
    # valid identity proxy for "is this the same decoded frame" ONLY because
    # image_discovery.open_imgref_as_array's decode cache guarantees a returned
    # array is shared by reference on a hit -- same frame -> same object -> same
    # id(); a real frame change, or a test monkeypatching _load_array to return
    # different data for the same nominal (well, channel, tp, fov), correctly
    # misses instead. id(st.raw_arr) would NOT work here -- it's a fresh
    # np.asarray(...) object every single call, never stable across calls.
    # (_load_loose_frame caches by path for the same reason.)
    correction_key = (
        id(raw), st.source, ff_mode, bg_method, st.radius,
        st.fft_small, st.mesh_ratio, st.mesh_sigma, id(ff), id(bg),
    )
    if correction_key == st.last_correction_key and st.last_corrected_arr is not None:
        corrected = st.last_corrected_arr
    else:
        corrected = pm.correct_single_frame(
            st.raw_arr, flatfield_mode=ff_mode,
            background_method=bg_method, radius=st.radius,
            fft_small=st.fft_small, mesh_ratio=st.mesh_ratio, mesh_sigma=st.mesh_sigma,
            ff_field=ff, bg_field=bg)
        st.last_correction_key = correction_key
        st.last_corrected_arr = corrected
    _render(ctx, corrected)
    label = ("Flat-field" if st.source == "flatfield"
             else f"Corrected ({ff_mode}+{bg_method})")
    text = f"{label} — {frame_lbl}"
    if refusal:
        text = f"{text}  ⚠ {refusal}"
    _set_status(win, _with_seg_note(ctx, text))


def _source_needs_reference(st) -> bool:
    """A reference-well field is needed for empty_well/basic flat-field (any
    non-raw source) and for empty_well background (corrected source only)."""
    if st.source == "raw":
        return False
    if st.flatfield_mode in ("empty_well", "basic"):
        return True
    return st.source == "corrected" and st.background_method == "empty_well"


def _effective_threshold(ctx) -> float:
    """Frame-local binary threshold: the user's override if set, else the
    channel's app-wide ThreshFracOn as a read-only default (never written back)."""
    st = _st(ctx)
    if st.threshold is not None:
        return float(st.threshold)
    try:
        from well_viewer.core.channel_state_controller import get_thresh_frac_on
        return float(get_thresh_frac_on(ctx, (st.channel or "").lower()))
    except Exception:
        return 0.0


def _qc_size_filtered(mask, min_area: float, max_area: float):
    """Return *mask* with labels whose pixel area falls outside [min_area,
    max_area] zeroed out (0 = no bound). Mirrors the pipeline's size-based
    debris gate so the Playground can preview it. Returns the mask unchanged
    when no bound is set."""
    if mask is None or (min_area <= 0 and max_area <= 0):
        return mask
    center = np.asarray(mask, dtype=np.int32)
    counts = np.bincount(center.ravel())
    keep = np.ones(counts.shape[0], dtype=bool)
    if min_area > 0:
        keep &= counts >= float(min_area)
    if max_area > 0:
        keep &= counts <= float(max_area)
    keep[0] = True                         # background label stays 0 either way
    drop = np.where(~keep)[0]
    if drop.size == 0:
        return center
    out = center.copy()
    out[np.isin(center, drop)] = 0
    return out


def _cells_above(arr, mask, threshold: float):
    """Label ids whose mean intensity in *arr* exceeds *threshold* (per-cell
    gate over the precomputed mask). None when the mask doesn't match the frame."""
    center = np.asarray(mask, dtype=np.int32)
    if center.shape[:2] != np.asarray(arr).shape[:2]:
        return None
    ids = np.unique(center)
    ids = ids[ids > 0]
    if ids.size == 0:
        return set()
    from scipy import ndimage as ndi
    means = np.atleast_1d(ndi.mean(np.asarray(arr, dtype=np.float32),
                                   labels=center, index=ids))
    return {int(i) for i, m in zip(ids, means) if float(m) > threshold}


def _render(ctx, arr) -> None:
    st = _st(ctx)
    st.display_arr = np.asarray(arr, dtype=np.float32)
    lo, hi = _resolve_lut(ctx.window, arr)
    # Apply the QC size-based cleanup to the mask before display, so the overlay
    # and binary reflect the debris gate. In live mode the labels the preview
    # just computed stand in for the pipeline's precomputed ones — but only
    # while they still describe THIS frame and these settings; stale labels are
    # dropped (and named in the status line) rather than drawn over a frame they
    # were not computed from.
    source_mask = st.mask_arr
    if st.seg_live and st.seg_labels is not None and not _seg_is_stale(ctx):
        source_mask = st.seg_labels
    qc_mask = _qc_size_filtered(source_mask, st.qc_min_area, st.qc_max_area)
    mask = qc_mask if (st.show_overlay or st.binary_mask) else None
    above_ids = None
    thr = None
    if st.binary_mask:
        thr = _effective_threshold(ctx)
        if qc_mask is not None:
            above_ids = _cells_above(arr, qc_mask, thr)
    st.base_rgb = build_playground_rgb(
        arr, mask, lo=lo, hi=hi, cmap_name=st.cmap_name, binary=st.binary_mask,
        show_boundary=st.show_overlay, tint=tuple(st.tint_color),
        above_ids=above_ids, threshold=thr)
    render_playground_display(ctx.window)


def _build_reference_then_render(ctx, chan_lower: str) -> None:
    """Kick off the reference-field worker; re-render when it lands."""
    st = _st(ctx)
    _set_status(ctx.window, f"Building {st.flatfield_mode}/{st.background_method} model "
                            f"from well {st.background_well}…")

    def _on_done(well, chan, ff_field, bg_field):
        if ff_field is not None:
            st.ff_field_by_chan[chan] = ff_field
        if bg_field is not None:
            st.bg_field_by_chan[chan] = bg_field
        st.model_well = st.background_well
        playground_on_change(ctx)

    def _on_failed(msg):
        _set_status(ctx.window, f"Model build failed: {msg}")

    ctx.runtime.playground.ref_bridge = request_reference_fields(
        ctx, st.background_well, chan_lower,
        flatfield_mode=st.flatfield_mode, background_method=st.background_method,
        on_done=_on_done, on_failed=_on_failed)


# ── live StarDist preview ────────────────────────────────────────────────────

def _thr_text(value) -> str:
    return "model" if value is None else f"{float(value):g}"


def _seg_key(ctx) -> tuple:
    """Identity of what a set of live labels describes.

    Everything that decides the pixels StarDist is fed (the frame, the display
    stage, the correction and its parameters) plus the two thresholds. Derived
    from state alone — no array ``id()`` — so it stays comparable across the
    worker-thread round trip that computes the labels.
    """
    st = _st(ctx)
    loose = bool(str(st.loose_image_path or "").strip())
    ff_mode, bg_method, _ = _effective_correction_modes(ctx, loose=loose)
    return (getattr(ctx.runtime.playground, "frame_key", None), st.source,
            ff_mode, bg_method, st.radius, st.fft_small, st.mesh_ratio,
            st.mesh_sigma, None if loose else st.background_well,
            st.seg_prob_thresh, st.seg_nms_thresh)


def _seg_is_stale(ctx) -> bool:
    """True when live labels exist but were computed for a different frame or
    different settings than the ones now on screen."""
    st = _st(ctx)
    return st.seg_labels is not None and st.seg_key != _seg_key(ctx)


def _with_seg_note(ctx, text: str) -> str:
    """Append the live-preview's own state to a status line.

    The preview can be in four states and each has to be distinguishable: off,
    running, holding labels for this frame, or holding STALE labels. The last
    one is the reason this exists — labels that no longer match the frame are
    not drawn, so without a note the overlay would simply be missing.
    """
    st = _st(ctx)
    if st.seg_param_error:
        # Reported whether or not the preview is on: the number in the box is
        # not the number being used, and only saying so in live mode would let
        # a typo sit there looking accepted.
        text = f"{text}  ⚠ {st.seg_param_error}"
    if not st.seg_live:
        return text
    if st.seg_running:
        return f"{text}  •  StarDist running…"
    if st.seg_labels is None:
        return f"{text}  •  {st.seg_status or 'press Segment to run StarDist'}"
    if _seg_is_stale(ctx):
        return (f"{text}  ⚠ StarDist labels are stale (computed for a different "
                f"frame or settings) — press Segment again")
    return f"{text}  •  {st.seg_status}"


def playground_segment_now(ctx) -> None:
    """Run StarDist on the frame currently displayed, on a worker thread.

    Explicitly button-driven: inference is seconds per frame and
    ``playground_on_change`` fires on every keystroke, so running it implicitly
    would make the tab unusable.
    """
    st = _st(ctx)
    win = ctx.window
    if st.seg_running:
        return
    # Re-render first, so the pixels handed to StarDist are the ones the current
    # widget values produce. Without it a control edited since the last render
    # would go into ``_seg_key`` while ``display_arr`` still held the previous
    # frame — labels tagged as describing settings they were not computed under,
    # which is the exact confusion ``seg_key`` exists to prevent. Cheap: the
    # correction is memoized, so an unchanged frame costs a blit.
    playground_on_change(ctx)
    arr = st.display_arr
    if arr is None:
        _set_status(win, "Nothing to segment yet — open an image or pick a frame.")
        return
    st.seg_live = True
    st.seg_running = True
    key = _seg_key(ctx)
    _set_status(win, "StarDist: segmenting… (the first run of a session also "
                     "loads TensorFlow — ~10-30 s, and it stays resident)")

    def _on_done(labels) -> None:
        st.seg_running = False
        st.seg_labels = labels
        st.seg_key = key
        n = int(np.max(labels)) if getattr(labels, "size", 0) else 0
        st.seg_status = (f"StarDist: {n} nuclei "
                         f"(prob={_thr_text(st.seg_prob_thresh)}, "
                         f"nms={_thr_text(st.seg_nms_thresh)})")
        playground_on_change(ctx)

    def _on_failed(msg: str) -> None:
        st.seg_running = False
        st.seg_labels = None
        st.seg_key = None
        st.seg_status = f"StarDist failed: {msg}"
        playground_on_change(ctx)

    ctx.runtime.playground.seg_bridge = request_stardist_labels(
        arr, prob_thresh=st.seg_prob_thresh, nms_thresh=st.seg_nms_thresh,
        on_done=_on_done, on_failed=_on_failed)


def playground_open_loose_image(ctx, path: str) -> None:
    """Adopt *path* as the frame, leaving dataset mode."""
    st = _st(ctx)
    st.loose_image_path = str(path) or None
    st.seg_labels = None
    st.seg_key = None
    st.last_correction_key = None
    st.last_corrected_arr = None
    playground_on_change(ctx)


def playground_clear_loose_image(ctx) -> None:
    """Drop the loose image and go back to the dataset-driven frame."""
    st = _st(ctx)
    st.loose_image_path = None
    st.seg_labels = None
    st.seg_key = None
    st.last_correction_key = None
    st.last_corrected_arr = None
    playground_on_change(ctx)


def playground_send_params_to_analyze(ctx) -> bool:
    """Hand the tuned thresholds to the Analyze form and switch to that pane.

    Returns False when the shell cannot take them (no Analyze pane built), so
    the caller can say so instead of appearing to have done something.
    """
    st = _st(ctx)
    _read_widgets_into_state(ctx)     # hand over what is in the boxes right now
    send = getattr(ctx.window, "send_stardist_params_to_analyze", None)
    if not callable(send):
        return False
    return bool(send(st.seg_prob_thresh, st.seg_nms_thresh))


def _refresh_threshold_display(ctx) -> None:
    """Sync the Threshold stepper to the effective threshold (signals blocked so
    the display sync doesn't trigger a re-render)."""
    stp = getattr(ctx.window, "_playground_threshold_stepper", None)
    if stp is None:
        return
    blocked = stp.blockSignals(True)
    try:
        stp.setValue(_effective_threshold(ctx))
    finally:
        stp.blockSignals(blocked)


def _event_xy(event):
    try:
        pos = event.position()          # Qt6 QMouseEvent
        return float(pos.x()), float(pos.y())
    except AttributeError:
        return float(event.x()), float(event.y())


def playground_pan_press(ctx, event) -> None:
    """Record the drag origin so the next moves pan relative to it."""
    st = _st(ctx)
    mx, my = _event_xy(event)
    ctx.runtime.playground.pan_drag = (mx, my, float(st.pan_x), float(st.pan_y))


def playground_pan_move(ctx, event) -> None:
    """Pan the image by the drag delta (view is kept in state, so it persists
    across source/parameter changes)."""
    drag = getattr(ctx.runtime.playground, "pan_drag", None)
    if drag is None:
        return
    st = _st(ctx)
    mx, my = _event_xy(event)
    sx, sy, ox, oy = drag
    st.pan_x = ox + (mx - sx)
    st.pan_y = oy + (my - sy)
    render_playground_display(ctx.window)


def playground_hover(ctx, event) -> None:
    """Show a tooltip with the hovered pixel's intensity in the displayed frame."""
    st = _st(ctx)
    win = ctx.window
    arr = st.display_arr
    disp = getattr(win, "_playground_disp", None)
    label = getattr(win, "_playground_image_label", None)
    if arr is None or disp is None or label is None:
        return
    a = np.asarray(arr)
    nw, nh, iw, ih = disp["nw"], disp["nh"], disp["iw"], disp["ih"]
    mx, my = _event_xy(event)
    # The pixmap is centred in the (possibly larger) label.
    off_x = max(0, (label.width() - nw) // 2)
    off_y = max(0, (label.height() - nh) // 2)
    px = int((mx - off_x) * iw / max(nw, 1))
    py = int((my - off_y) * ih / max(nh, 1))
    if not (0 <= px < a.shape[1] and 0 <= py < a.shape[0]):
        return
    from PySide6.QtGui import QCursor
    from PySide6.QtWidgets import QToolTip
    QToolTip.showText(QCursor.pos(), f"({px}, {py})  =  {float(a[py, px]):.1f}")
