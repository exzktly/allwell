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
from typing import NamedTuple

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


def _set_count(win, text: str) -> None:
    from well_viewer.core.ui_helpers import set_widget_text
    set_widget_text(win, "_playground_count", text)


# ── the shared status-row progress bar ───────────────────────────────────────
#
# The same widget smFISH's Apply-to-All and the dataset load drive
# (``features/smfish/tab_view.py``), reached the same way, so the app reports
# every long job in one place instead of one per tab. All three helpers run on
# the GUI thread; the workers reach them only through the bridge's queued
# ``progress`` / ``stage`` signals.

def _show_progress(ctx, total: int) -> None:
    """Show the bar. ``total <= 0`` means indeterminate — for a job whose length
    is genuinely unknown, which is honest where a fake percentage is not."""
    bar = ctx.widgets.get_or_none("_progress_bar")
    if bar is None:
        return
    bar.setRange(0, max(0, int(total)))
    bar.setValue(0)
    bar.show()


def _step_progress(ctx, done: int, total: int) -> None:
    bar = ctx.widgets.get_or_none("_progress_bar")
    if bar is None:
        return
    if bar.maximum() != max(1, int(total)):
        bar.setRange(0, max(1, int(total)))
    bar.setValue(int(done))


def _hide_progress(ctx) -> None:
    """Called on **every** exit path, including failure. A bar left running
    after a job died is its own false statement about what is happening."""
    bar = ctx.widgets.get_or_none("_progress_bar")
    if bar is None:
        return
    bar.setRange(0, 1)
    bar.setValue(0)
    bar.hide()


def _count_text(facts: dict) -> str:
    """The Cells read-out: how many objects this render actually drew.

    A second *formatting* of ``display_facts`` — the dict :func:`_render`
    records — never a second derivation, so it cannot disagree with the
    paragraph read-out or with the picture. It is the number the two StarDist
    thresholds are tuned against, which is why it gets a line of its own instead
    of living five lines into a paragraph.

    Every branch that has no count prints ``—``, never ``0``: a frame whose
    labels went stale, or that has no mask at all, has an *unknown* number of
    cells, and printing zero for it would be a measurement the tab never made.
    """
    if not facts:
        return "Cells: —"
    if facts.get("stale_labels"):
        return "Cells: — (stale — press Segment)"
    if facts.get("provenance", "none") == "none":
        return "Cells: — (no mask)"

    kept, found = facts.get("labels_kept", 0), facts.get("labels_in", 0)
    line = f"Cells: {kept} drawn"
    if kept != found:
        line += f" (QC dropped {found - kept} of {found})"
    # Only with the per-cell rule is "above" a statement about cells; under the
    # per-pixel and footprint rules no per-cell verdict was reached, so there is
    # no subset to name.
    if facts.get("binary_rule") == "per-cell" and facts.get("above") is not None:
        thr = facts.get("threshold")
        thr_txt = "—" if thr is None else f"{float(thr):g}"
        line += f"  ·  {facts['above']} above {thr_txt}"
    if not facts.get("shape_ok"):
        line += "  ⚠ mask does not fit this frame"
    return f"{line}\n({facts.get('provenance')})"


# ── the status line: what is on screen, and what produced it ─────────────────

_STAGE_LABELS = {"raw": "Raw", "flatfield": "Flat-field only",
                 "corrected": "Corrected"}


def _correction_text(st, ff_mode: str, bg_method: str) -> str:
    """The correction actually applied, with the parameter that fed it.

    Names the *effective* modes rather than the widget values, so a stage that
    silently drops the background half (or a loose image that cannot do a
    reference-well correction) reads as what ran, not what was asked for.
    """
    radius_methods = ("top_hat", "sliding_disk", "robust_mesh", "fft_bandpass")
    bg = f"{bg_method} r={st.radius:g}" if bg_method in radius_methods else bg_method
    return f"{ff_mode} + {bg}"


def _range_text(arr) -> str:
    """min–max and mean of the displayed array.

    Load-bearing, not decoration. The LUT re-derives its window from this array
    on every render while it reads ``auto``, so a background change can leave
    the picture looking identical while the data underneath moved — for
    ``fov_median``, a scalar subtraction, the two are pixel-identical after
    auto-scaling. These numbers are how a correction proves it ran.
    """
    if arr is None:
        return "range —"
    a = np.asarray(arr, dtype=np.float32)
    if not a.size:
        return "range —"
    return (f"range {float(a.min()):.4g}–{float(a.max()):.4g} "
            f"(mean {float(a.mean()):.4g})")


def _threshold_source(st) -> str:
    """Where the binary threshold came from — the two are easy to confuse."""
    return ("set on this tab" if st.threshold is not None
            else "this channel's ON threshold")


_PROVENANCE_PHRASE = {
    "pipeline mask": ("the mask a pipeline run already saved for this frame. It "
                      "does NOT change when you change a correction — nothing "
                      "here re-segments it"),
    "live StarDist": "labels this tab computed with the thresholds above",
    "none": "none",
}


def _cells(n: int) -> str:
    return f"{n} label" if n == 1 else f"{n} labels"


def _describe_mask(facts: dict) -> str:
    """What mask was calculated, in words, including what was thrown away."""
    provenance = facts.get("provenance", "none")
    if provenance == "none":
        return "Mask: none — nothing to outline or fill."
    parts = [f"Mask: {provenance} — {_PROVENANCE_PHRASE[provenance]}.",
             f"{_cells(facts['labels_in'])} found."]
    kept, total = facts["labels_kept"], facts["labels_in"]
    if kept != total:
        parts.append(f"QC size filter kept {kept} of {total}.")
    if not facts["shape_ok"]:
        mh, mw = facts["mask_shape"] or (0, 0)
        fh, fw = facts["frame_shape"]
        parts.append(f"⚠ It does NOT match this frame (mask {mw}×{mh}, frame "
                     f"{fw}×{fh}), so it cannot be used per cell.")
    return " ".join(parts)


def _describe_shown(facts: dict) -> str:
    """What is actually on screen, naming *which* rule drew it."""
    if not facts["binary"]:
        if facts["outlines"] and facts["labels_kept"]:
            return (f"Shown: the image, with outlines around "
                    f"{_cells(facts['labels_kept'])}.")
        if facts["outlines"]:
            return "Shown: the image only — outlines are on, but there is no mask."
        return "Shown: the image only — outlines are off."

    thr = facts["threshold"]
    thr_txt = "—" if thr is None else f"{float(thr):g}"
    rule = facts["binary_rule"]
    # Naming the fill colour matters as much as naming the rule: one colour per
    # label is how touching cells stay countable, and all-white is how two of
    # them read as one object.
    paint = ("each label in its own colour" if facts.get("label_colors")
             else "white")
    if rule == "per-cell":
        return (f"Shown: binary — {paint} where a label's MEAN intensity in this "
                f"image exceeds {thr_txt} ({facts['threshold_source']}). "
                f"{facts['above']} of {facts['labels_kept']} pass.")
    if rule == "per-pixel":
        return (f"Shown: binary — but with no usable mask this is a PER-PIXEL "
                f"threshold: every pixel above {thr_txt} is white. There are no "
                f"labels to colour, and it is not a per-cell measurement.")
    return (f"Shown: binary — every labelled pixel is filled, {paint} "
            f"({_cells(facts['labels_kept'])}); the threshold is not used.")


def _status_text(ctx, frame_lbl: str, ff_mode: str, bg_method: str,
                 refusal: str = "") -> str:
    """The read-out: what was calculated, and what is on screen, in words.

    Every ambiguity this tab can produce is spelled out here rather than left to
    be inferred from the picture — because the picture cannot show any of them.
    Binary mode in particular renders three different rules identically (see
    :func:`_binary_rule`), the outlines can come from a pipeline run or from
    this tab, and the LUT auto-scales so a correction can move the data without
    moving the image. Call it *after* ``_render``, which records the facts.
    """
    st = _st(ctx)
    facts = st.display_facts or {}
    stage = _STAGE_LABELS.get(st.source, st.source)
    correction = ("no correction applied on this stage" if st.source == "raw"
                  else _correction_text(st, ff_mode, bg_method))
    lines = [f"Frame: {frame_lbl}",
             f"Stage: {stage} — {correction}",
             f"Image: {_range_text(st.display_arr)}",
             _describe_mask(facts),
             _describe_shown(facts)]
    if refusal:
        lines.append(f"⚠ {refusal}")
    lines += _describe_segmentation(ctx)
    return "\n".join(lines)


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


def correction_controls_enabled(stage: str) -> dict[str, bool]:
    """Which Correction controls the chosen stage actually consults.

    The stage is a master switch, not a view option: ``raw`` returns before any
    correction runs, and ``flatfield`` applies only the flat-field half — radius,
    small-px and the two mesh values are *background* parameters
    (``correct_single_frame`` passes them to ``_subtract_background_step`` and
    nowhere else), so they are dead there too.

    Returned as a mapping rather than decided inline in the widget builder so the
    rule is testable without constructing the tab, and so the builder cannot
    drift from what :func:`_effective_correction_modes` actually does.
    """
    flat_or_corrected = stage in ("flatfield", "corrected")
    corrected = stage == "corrected"
    return {
        "flatfield": flat_or_corrected,
        "background": corrected,
        "background_params": corrected,      # radius / small px / mesh ratio+sigma
        "background_well": flat_or_corrected,
    }


def _effective_correction_modes(ctx, *, loose: bool,
                                stage: str | None = None) -> tuple[str, str, str]:
    """``(flatfield_mode, background_method, refusal)`` for a stage.

    *stage* defaults to the one on screen; the Segment button passes
    ``"corrected"`` explicitly, because the pipeline segments the corrected
    channel whatever the Playground happens to be displaying.

    The reference-well modes (``empty_well`` / ``basic``) estimate their field
    from a whole reference well, which a lone file on disk does not have. Rather
    than render an uncorrected frame while the controls still claim a correction
    is applied, those modes are dropped for loose images and *named* in the
    returned refusal so the status line can say what did not run.
    """
    st = _st(ctx)
    stage = stage or st.source
    ff_mode = st.flatfield_mode if stage in ("flatfield", "corrected") else "none"
    bg_method = st.background_method if stage == "corrected" else "none"
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


class _RefFields(NamedTuple):
    """What the reference-well cache can supply for one set of modes.

    ``ff_mode`` / ``bg_method`` come back **downgraded to** ``"none"`` when the
    field that mode needs is absent, so a caller cannot ask
    ``correct_single_frame`` for a correction it has no field for — which is a
    silent no-op inside ``apply_flatfield`` — nor report a mode that did not run.
    """

    ff_mode: str
    bg_method: str
    ff: object
    bg: object
    pending: bool        # a needed field has not been built yet → build it
    refusal: str         # a needed field was attempted and cannot be built


def _needs_reference(ff_mode: str, bg_method: str) -> bool:
    """True when these *effective* modes need a field estimated from a whole
    reference well. Derived from the effective modes, not the widget values, so
    it cannot disagree with :func:`_effective_correction_modes` about the stage."""
    return ff_mode in ("empty_well", "basic") or bg_method == "empty_well"


def _reference_keys(well, chan: str, ff_mode: str, bg_method: str):
    """``(ff_key, bg_key)`` — the cache keys these modes need, None where they
    need nothing.

    The flat-field key carries the mode because ``basic`` and ``empty_well``
    estimate **different** fields from the same frames
    (``estimate_basic_flatfield`` versus ``normalize_flatfield(B)``); a key
    without it hands one mode the other's field. The additive background field
    has a single estimator, so its key does not need one.
    """
    ff_key = ("ff", well, chan, ff_mode) if ff_mode in ("empty_well", "basic") else None
    bg_key = ("bg", well, chan) if bg_method == "empty_well" else None
    return ff_key, bg_key


def _reference_fields_for(ctx, *, ff_mode: str, bg_method: str, chan: str) -> _RefFields:
    """Resolve the reference fields these modes need, from the cache alone.

    The single seam both the render path and the Segment button go through, so
    the frame on screen and the frame handed to StarDist can never be corrected
    differently. It never builds anything — it reports ``pending`` and lets the
    caller decide (the render path starts a worker; the Segment button refuses
    and says to wait).
    """
    st = _st(ctx)
    well = st.background_well
    ff_key, bg_key = _reference_keys(well, chan, ff_mode, bg_method)
    needed = [k for k in (ff_key, bg_key) if k is not None]
    if not needed:
        return _RefFields(ff_mode, bg_method, None, None, False, "")
    if any(k not in st.ref_fields and k not in st.ref_missing for k in needed):
        return _RefFields(ff_mode, bg_method, None, None, True, "")

    ff = st.ref_fields.get(ff_key) if ff_key is not None else None
    bg = st.ref_fields.get(bg_key) if bg_key is not None else None
    dropped: list[str] = []
    reasons: list[str] = []
    if ff_key is not None and ff is None:
        dropped.append(f"flat-field {ff_mode}")
        reasons.append(st.ref_missing.get(ff_key, "no field"))
        ff_mode = "none"
    if bg_key is not None and bg is None:
        dropped.append(f"background {bg_method}")
        reasons.append(st.ref_missing.get(bg_key, "no field"))
        bg_method = "none"
    if not dropped:
        return _RefFields(ff_mode, bg_method, ff, bg, False, "")
    return _RefFields(ff_mode, bg_method, ff, bg, False,
                      f"{' and '.join(dropped)} could not be built from well "
                      f"{well} ({reasons[0]}) — not applied")


def _join_refusals(*parts: str) -> str:
    return "; ".join(p for p in parts if p)


def playground_on_change(ctx) -> None:
    """Reload + recorrect + rerender on any selector / param / toggle change."""
    st = _st(ctx)
    win = ctx.window
    _read_widgets_into_state(ctx)

    raw, frame_lbl, frame_key = _resolve_frame(ctx)
    if raw is None:
        st.raw_arr = None
        # Nothing is rendered on this path, so the facts from the previous frame
        # would otherwise keep a count on screen beside a blank canvas.
        st.display_facts = {}
        _set_status(win, frame_lbl)          # frame_lbl carries the reason
        _set_count(win, _count_text(st.display_facts))
        return
    st.raw_arr = np.asarray(raw, dtype=np.float32)
    # The decoded frame's identity, for the correction memo — see _corrected_frame.
    ctx.runtime.playground.raw_id = id(raw)
    loose = bool(str(st.loose_image_path or "").strip())

    # The binary threshold is per-frame: when the frame identity changes, drop
    # any local override so the control falls back to the channel's app-wide
    # ThreshFracOn default, and refresh the displayed value.
    if getattr(ctx.runtime.playground, "frame_key", None) != frame_key:
        ctx.runtime.playground.frame_key = frame_key
        st.threshold = None
        _refresh_threshold_display(ctx)

    # Raw — no correction. Every Correction control is inert here; the stage
    # control greys them out, and the status names the stage so it is legible
    # why nothing responds.
    if st.source == "raw":
        _render(ctx, st.raw_arr)
        _set_status(win, _status_text(ctx, frame_lbl, "none", "none", ""))
        return

    ff_mode, bg_method, refusal = _effective_correction_modes(ctx, loose=loose)
    chan_lower = (st.channel or "").lower()
    ff = bg = None
    if not loose and _needs_reference(ff_mode, bg_method):
        if not st.background_well:
            _set_status(win, "Pick a background well for empty_well / basic.")
            _render(ctx, st.raw_arr)
            return
        ref = _reference_fields_for(ctx, ff_mode=ff_mode, bg_method=bg_method,
                                    chan=chan_lower)
        if ref.pending:
            _build_reference_then_render(ctx, chan_lower, ff_mode=ff_mode,
                                         bg_method=bg_method)
            return
        # Downgraded where a field could not be built, so what follows corrects
        # — and reports — only what it actually has a field for.
        ff_mode, bg_method, ff, bg = ref.ff_mode, ref.bg_method, ref.ff, ref.bg
        refusal = _join_refusals(refusal, ref.refusal)

    corrected = _corrected_frame(ctx, ff_mode=ff_mode, bg_method=bg_method,
                                 ff=ff, bg=bg)
    _render(ctx, corrected)
    _set_status(win, _status_text(ctx, frame_lbl, ff_mode, bg_method, refusal))


def _corrected_frame(ctx, *, ff_mode: str, bg_method: str, ff, bg, store: bool = True):
    """``correct_single_frame`` for these settings, memoized.

    Shared by the render path and the Segment button so a preview and the
    segmentation drawn over it can never come from different pixels.

    The memo key carries the frame's identity via
    ``ctx.runtime.playground.raw_id`` — ``id()`` of the pre-``np.asarray`` object
    ``_resolve_frame`` returned. That is a valid proxy for "same decoded frame"
    ONLY because ``image_discovery.open_imgref_as_array``'s decode cache returns
    the same array object on a hit, and ``_load_loose_frame`` caches by path for
    the same reason. ``id(st.raw_arr)`` would NOT work — that is a fresh
    ``np.asarray(...)`` every call, never stable.

    *store* is False when the Segment button computes a correction for a stage
    that is not the one on screen: caching it would evict the displayed frame's
    entry and make the next render recompute (~1.8 s at 1024²) for nothing.
    """
    st = _st(ctx)
    key = (getattr(ctx.runtime.playground, "raw_id", None), ff_mode, bg_method,
           st.radius, st.fft_small, st.mesh_ratio, st.mesh_sigma, id(ff), id(bg))
    if key == st.last_correction_key and st.last_corrected_arr is not None:
        return st.last_corrected_arr
    corrected = pm.correct_single_frame(
        st.raw_arr, flatfield_mode=ff_mode, background_method=bg_method,
        radius=st.radius, fft_small=st.fft_small, mesh_ratio=st.mesh_ratio,
        mesh_sigma=st.mesh_sigma, ff_field=ff, bg_field=bg)
    if store:
        st.last_correction_key = key
        st.last_corrected_arr = corrected
    return corrected


def _effective_threshold(ctx) -> float:
    """Frame-local binary threshold: the user's override if set, else the
    channel's app-wide ThreshFracOn as a read-only default (never written back).

    Deliberately the *intensity* cutoff whatever Property the plots are showing.
    This number is applied to image pixels — ``_cells_above`` compares it against
    each label's mean intensity in the frame on screen — so a spot-count cutoff
    would be measured against the wrong quantity entirely. At its -1 default it
    would pass every cell and paint the whole frame, while the stepper beside it
    (built with ``minimum=0``) clamped the display to 0.
    """
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


def _mask_source(ctx):
    """``(mask, provenance)`` for the overlay — and *which* mask it is.

    The outlines come from one of two very different things and look identical
    either way, so the caller has to be able to say which:

    * ``"pipeline mask"`` — the mask a run already produced, read out of the
      dataset's output zip. **It does not respond to anything in the Correction
      section**, because changing a correction does not re-segment. A user
      watching outlines that cannot move needs to be told why.
    * ``"live StarDist"`` — labels this tab computed, and only while they still
      describe the frame and settings on screen.
    * ``"none"`` — nothing to draw.

    With live mode on and the labels stale, this returns nothing rather than
    quietly falling back to the pipeline's mask: showing one mask under a status
    line that describes another is a substitution the user cannot detect.
    """
    st = _st(ctx)
    if st.seg_live:
        if st.seg_labels is not None and not _seg_is_stale(ctx):
            return st.seg_labels, "live StarDist"
        return None, "none"
    if st.mask_arr is not None:
        return st.mask_arr, "pipeline mask"
    return None, "none"


def _label_count(mask) -> int:
    """How many non-zero labels a mask holds (0 for no mask)."""
    if mask is None:
        return 0
    ids = np.unique(np.asarray(mask, dtype=np.int32))
    return int((ids > 0).sum())


def _binary_rule(mask, above_ids, threshold, shape_ok: bool) -> str:
    """Which of ``build_frame_rgb``'s three binary rules this render will take.

    They produce identical-looking white-on-black and mean completely different
    things, and the choice is made silently by what happens to be available:

    * ``per-cell`` — a label is filled when its MEAN intensity clears the
      threshold. What the pipeline's cell gating means.
    * ``per-pixel`` — no usable mask, so every *pixel* above the threshold is
      filled. Not a cell measurement at all.
    * ``footprint`` — a mask but no per-cell verdict; every labelled pixel is
      filled and the threshold is not consulted.

    Naming the rule is the whole point of the read-out: a user cannot tell these
    apart by looking.
    """
    if mask is not None and shape_ok and above_ids is not None:
        return "per-cell"
    if threshold is not None:
        return "per-pixel"
    return "footprint"


def _render(ctx, arr) -> None:
    st = _st(ctx)
    st.display_arr = np.asarray(arr, dtype=np.float32)
    lo, hi = _resolve_lut(ctx.window, arr)
    # Apply the QC size-based cleanup to the mask before display, so the overlay
    # and binary reflect the debris gate.
    source_mask, provenance = _mask_source(ctx)
    qc_mask = _qc_size_filtered(source_mask, st.qc_min_area, st.qc_max_area)
    mask = qc_mask if (st.show_overlay or st.binary_mask) else None
    above_ids = None
    thr = None
    shape_ok = (qc_mask is not None
                and np.asarray(qc_mask).shape[:2] == np.asarray(arr).shape[:2])
    if st.binary_mask:
        thr = _effective_threshold(ctx)
        if qc_mask is not None:
            above_ids = _cells_above(arr, qc_mask, thr)
    st.display_facts = {
        "provenance": provenance,
        # Live labels exist but describe a different frame/settings, so
        # ``_mask_source`` dropped them. Recorded separately because provenance
        # is "none" here for the same reason it is when there was never a mask
        # at all, and those two must not read as the same thing — one has no
        # cells, the other has an unknown number.
        "stale_labels": bool(st.seg_live and st.seg_labels is not None
                             and _seg_is_stale(ctx)),
        "labels_in": _label_count(source_mask),
        "labels_kept": _label_count(qc_mask),
        "shape_ok": bool(shape_ok),
        "mask_shape": None if source_mask is None else tuple(
            np.asarray(source_mask).shape[:2]),
        "frame_shape": tuple(np.asarray(arr).shape[:2]),
        "outlines": bool(st.show_overlay),
        "binary": bool(st.binary_mask),
        # Only true where there were labels to colour — the per-pixel fallback
        # has none, so the read-out must not claim colours it did not draw.
        "label_colors": bool(st.binary_label_colors and shape_ok),
        "threshold": thr,
        "threshold_source": _threshold_source(st),
        "above": None if above_ids is None else len(above_ids),
        "binary_rule": _binary_rule(qc_mask, above_ids, thr, shape_ok),
    }
    st.base_rgb = build_playground_rgb(
        arr, mask, lo=lo, hi=hi, cmap_name=st.cmap_name, binary=st.binary_mask,
        show_boundary=st.show_overlay, tint=tuple(st.tint_color),
        above_ids=above_ids, threshold=thr,
        label_colors=bool(st.binary_label_colors))
    _set_count(ctx.window, _count_text(st.display_facts))
    render_playground_display(ctx.window)


def _build_reference_then_render(ctx, chan_lower: str, *, ff_mode: str,
                                 bg_method: str) -> None:
    """Kick off the reference-field worker; re-render when it lands.

    The modes and the well are captured **here**, at request time, and the
    result is filed under keys built from those captures — never from live
    state. A dropdown changed while the worker runs would otherwise file the
    field under the settings in force when it returned, i.e. under settings it
    was not computed for.
    """
    st = _st(ctx)
    req_well = st.background_well
    ff_key, bg_key = _reference_keys(req_well, chan_lower, ff_mode, bg_method)
    _set_status(ctx.window, f"Building {ff_mode}/{bg_method} model "
                            f"from well {req_well}…")
    # Determinate: the build's cost is one decode per sampled frame, and it
    # knows how many it will attempt.
    _show_progress(ctx, 1)

    def _on_progress(done: int, total: int) -> None:
        _step_progress(ctx, done, total)
        _set_status(ctx.window, f"Building {ff_mode}/{bg_method} model from "
                                f"well {req_well}… ({done}/{total} frames)")

    def _on_done(well, chan, ff_field, bg_field):
        _hide_progress(ctx)
        # A build that produced nothing is recorded as ATTEMPTED, with a reason.
        # Leaving the key simply absent would respawn this worker on the next
        # keystroke, forever; recording it as built would silently correct with
        # no field at all. Both were the shape of the bug this replaced.
        why = f"no usable {chan.upper()} frames in well {well}"
        if ff_key is not None:
            if ff_field is None:
                st.ref_missing[ff_key] = why
            else:
                st.ref_fields[ff_key] = ff_field
        if bg_key is not None:
            if bg_field is None:
                st.ref_missing[bg_key] = why
            else:
                st.ref_fields[bg_key] = bg_field
        elif bg_field is not None:
            # The empty_well flat-field estimates B on the way to B/mean(B), so
            # the additive field is already paid for — cache it under its own
            # key rather than re-sampling the well if the background is switched
            # to empty_well next.
            st.ref_fields[("bg", well, chan)] = bg_field
        playground_on_change(ctx)

    def _on_failed(msg):
        _hide_progress(ctx)
        _set_status(ctx.window, f"Model build failed: {msg}")

    ctx.runtime.playground.ref_bridge = request_reference_fields(
        ctx, req_well, chan_lower,
        flatfield_mode=ff_mode, background_method=bg_method,
        on_done=_on_done, on_failed=_on_failed, on_progress=_on_progress)


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
    # Pinned to "corrected": segmentation always runs on the corrected frame
    # whatever stage is displayed, so the display stage must NOT appear here —
    # switching Raw ↔ Corrected would otherwise mark labels stale that describe
    # exactly the same pixels.
    ff_mode, bg_method, _ = _effective_correction_modes(ctx, loose=loose,
                                                        stage="corrected")
    if not loose and _needs_reference(ff_mode, bg_method):
        # The RESOLVED modes, so labels computed while a reference field was
        # missing (the correction downgraded to none) are not mistaken for
        # labels computed with it once the field exists.
        ref = _reference_fields_for(ctx, ff_mode=ff_mode, bg_method=bg_method,
                                    chan=(st.channel or "").lower())
        ff_mode, bg_method = ref.ff_mode, ref.bg_method
    return (getattr(ctx.runtime.playground, "frame_key", None),
            ff_mode, bg_method, st.radius, st.fft_small, st.mesh_ratio,
            st.mesh_sigma, None if loose else st.background_well,
            st.seg_prob_thresh, st.seg_nms_thresh)


def _seg_is_stale(ctx) -> bool:
    """True when live labels exist but were computed for a different frame or
    different settings than the ones now on screen."""
    st = _st(ctx)
    return st.seg_labels is not None and st.seg_key != _seg_key(ctx)


def _describe_segmentation(ctx) -> list[str]:
    """The live preview's own state, as read-out lines.

    It can be in five states and each has to be distinguishable: a bad threshold
    typed in, off, running, holding labels for this frame, or holding STALE
    ones. The stale case is the reason this exists — those labels are not drawn,
    so without a line saying why, the overlay would simply be missing.
    """
    st = _st(ctx)
    lines: list[str] = []
    if st.seg_param_error:
        # Reported whether or not the preview is on: the number in the box is
        # not the number being used, and only saying so in live mode would let
        # a typo sit there looking accepted.
        lines.append(f"⚠ {st.seg_param_error}")
    if not st.seg_live:
        return lines
    if st.seg_running:
        lines.append("StarDist running…")
    elif st.seg_labels is None:
        lines.append(st.seg_status or "StarDist: press Segment to run.")
    elif _seg_is_stale(ctx):
        lines.append("⚠ StarDist labels are stale (the frame or the settings "
                     "changed since they were computed) — press Segment again.")
    else:
        lines.append(st.seg_status)
    return lines


def _segmentation_frame(ctx):
    """``(array, note)`` — the frame StarDist should see.

    Always the **corrected** frame, whatever stage the tab is displaying,
    because that is what the pipeline segments. Segmenting the raw frame because
    the view happened to be on Raw — and reporting the nucleus count identically
    — is the silent substitution this returns a *note* to prevent: when the
    stage on screen is not the one segmented, the caller says so.

    ``(None, reason)`` when the corrected frame cannot be built, which happens
    only for the reference-well modes before their field exists.
    """
    st = _st(ctx)
    if st.raw_arr is None:
        return None, "Nothing to segment yet — open an image or pick a frame."
    loose = bool(str(st.loose_image_path or "").strip())
    ff_mode, bg_method, _refusal = _effective_correction_modes(
        ctx, loose=loose, stage="corrected")

    ff = bg = None
    refusal = ""
    if not loose and _needs_reference(ff_mode, bg_method):
        if not st.background_well:
            return None, ("The corrected frame needs the empty_well / basic "
                          "reference field — pick a Bg well and let it build first.")
        # The same resolver the render path uses, so the pixels StarDist sees are
        # corrected exactly as the ones on screen.
        ref = _reference_fields_for(ctx, ff_mode=ff_mode, bg_method=bg_method,
                                    chan=(st.channel or "").lower())
        if ref.pending:
            return None, ("The corrected frame needs the empty_well / basic "
                          "reference field — let it finish building first.")
        ff_mode, bg_method, ff, bg = ref.ff_mode, ref.bg_method, ref.ff, ref.bg
        refusal = ref.refusal

    # Only cache when this IS the displayed correction; otherwise the memo's one
    # slot would evict the frame on screen and cost a full recompute on the next
    # render.
    arr = _corrected_frame(ctx, ff_mode=ff_mode, bg_method=bg_method, ff=ff,
                           bg=bg, store=st.source == "corrected")
    notes = []
    if st.source != "corrected":
        notes.append(f"segmented the corrected frame (you are viewing "
                     f"{_STAGE_LABELS.get(st.source, st.source)})")
    if refusal:
        notes.append(refusal)
    return arr, _join_refusals(*notes)


def playground_segment_now(ctx) -> None:
    """Run StarDist on the corrected frame, on a worker thread.

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
    # would go into ``_seg_key`` while the frame still held the previous
    # settings — labels tagged as describing settings they were not computed
    # under, which is the exact confusion ``seg_key`` exists to prevent. Cheap:
    # the correction is memoized, so an unchanged frame costs a blit.
    playground_on_change(ctx)
    arr, note = _segmentation_frame(ctx)
    if arr is None:
        _set_status(win, note)
        return
    st.seg_stage_note = note
    st.seg_live = True
    st.seg_running = True
    key = _seg_key(ctx)
    _set_status(win, "StarDist: segmenting… (the first run of a session also "
                     "loads TensorFlow — ~10-30 s, and it stays resident)")
    # Indeterminate: one frame's inference has no interior milestones to count,
    # and the wait is dominated by a one-off model load whose length depends on
    # the machine. A percentage here would be invented.
    _show_progress(ctx, 0)

    def _on_stage(name: str) -> None:
        # The model-load phase is the long one and happens once per session;
        # reporting it identically to the segmentation leaves the first Segment
        # looking like a hang.
        _set_status(win, f"StarDist: {name}…")

    def _on_done(labels) -> None:
        _hide_progress(ctx)
        st.seg_running = False
        st.seg_labels = labels
        st.seg_key = key
        n = int(np.max(labels)) if getattr(labels, "size", 0) else 0
        st.seg_status = (f"StarDist: {n} nuclei "
                         f"(prob={_thr_text(st.seg_prob_thresh)}, "
                         f"nms={_thr_text(st.seg_nms_thresh)})")
        if st.seg_stage_note:
            st.seg_status = f"{st.seg_status} — {st.seg_stage_note}"
        playground_on_change(ctx)

    def _on_failed(msg: str) -> None:
        # Hidden here too, which is the whole point of doing it per branch: the
        # commonest failure in a fresh environment is "StarDist is not
        # installed", and a bar still sweeping under that message would say the
        # work continues.
        _hide_progress(ctx)
        st.seg_running = False
        st.seg_labels = None
        st.seg_key = None
        st.seg_status = f"StarDist failed: {msg}"
        playground_on_change(ctx)

    ctx.runtime.playground.seg_bridge = request_stardist_labels(
        arr, prob_thresh=st.seg_prob_thresh, nms_thresh=st.seg_nms_thresh,
        on_done=_on_done, on_failed=_on_failed, on_stage=_on_stage)


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
