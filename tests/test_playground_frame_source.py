"""The Playground's two frame sources, and which mask it draws.

Historically the tab could only show a frame out of a *loaded dataset*: the well
came from the sidebar plate and the channel / timepoint / FOV from combos filled
off ``ctx.state.dataset.well_paths``. That makes it useless at the moment it is
most wanted — choosing segmentation settings *before* committing a plate to a
run, when no dataset exists yet. A loose image opened straight from disk is the
second source, and it wins when set.

The other half of this file is the live StarDist preview's label source. Live
labels stand in for the pipeline's precomputed mask, but only while they still
describe the frame on screen: labels computed for a different frame or different
settings are dropped and named, never quietly drawn over the wrong image.

Pure controller-level tests — no ``viewer_app`` / ``loaded_app`` fixture, so they
run in the fast lane.
"""

from __future__ import annotations

import types

import numpy as np
import pytest

from review.core.widget_registry import WidgetRegistry
from review.features.image_browser.playground import controller as C
from review.features.image_browser.playground.state import PlaygroundState


class _FakeBar:
    """The QProgressBar API the three progress helpers actually use."""

    def __init__(self) -> None:
        self.visible = False
        self.minimum_, self.maximum_, self.value_ = 0, 1, 0
        self.shown_ranges: list[tuple[int, int]] = []
        self.values: list[int] = []

    def setRange(self, lo: int, hi: int) -> None:
        self.minimum_, self.maximum_ = lo, hi

    def maximum(self) -> int:
        return self.maximum_

    def setValue(self, v: int) -> None:
        self.value_ = v
        self.values.append(v)

    def show(self) -> None:
        self.visible = True
        self.shown_ranges.append((self.minimum_, self.maximum_))

    def hide(self) -> None:
        self.visible = False


@pytest.fixture
def ctx(monkeypatch):
    """A minimal context: playground state, an empty dataset, a window stub.

    Statuses are captured rather than pushed at a QLabel, and the render is
    stubbed down to the one decision under test (which mask reaches the
    renderer), so no Qt widget tree is built.
    """
    c = types.SimpleNamespace(
        window=types.SimpleNamespace(),
        state=types.SimpleNamespace(
            playground=PlaygroundState(),
            dataset=types.SimpleNamespace(data_dir=None, well_paths={})),
        runtime=types.SimpleNamespace(playground=types.SimpleNamespace()),
        widgets=WidgetRegistry(),
    )
    # A stand-in for the shared status-row QProgressBar: the controller drives it
    # through the same three helpers the real one uses, so its visibility and
    # range are assertable without building a widget tree.
    c.bar = _FakeBar()
    c.widgets.register("_progress_bar", c.bar)
    c.statuses = []
    c.counts = []
    c.masks = []
    c.rendered = []
    monkeypatch.setattr(C, "_set_status", lambda win, text: c.statuses.append(text))
    monkeypatch.setattr(C, "_set_count", lambda win, text: c.counts.append(text))
    monkeypatch.setattr(C, "render_playground_display", lambda win: None)
    def _capture(arr, mask, **kw):
        c.masks.append(mask)
        c.rendered.append((np.asarray(arr).copy(), mask))
        return np.zeros((*np.shape(arr), 3), dtype=np.uint8)
    monkeypatch.setattr(C, "build_playground_rgb", _capture)
    return c


@pytest.fixture
def tif(tmp_path):
    tifffile = pytest.importorskip("tifffile")
    path = tmp_path / "frame.tif"
    data = (np.random.default_rng(1).random((32, 32)) * 1000).astype(np.uint16)
    tifffile.imwrite(path, data)
    return path


# ── frame source ─────────────────────────────────────────────────────────────

def test_no_dataset_and_no_file_says_both_ways_out(ctx):
    C.playground_on_change(ctx)
    assert ctx.state.playground.raw_arr is None
    # The message must offer the loose-image route, not only the dataset one —
    # a user with no dataset would otherwise conclude the tab needs one.
    assert "open a single image" in ctx.statuses[-1]


def test_a_loose_image_is_read_without_any_dataset(ctx, tif, monkeypatch):
    def _no_dataset_loads(*a, **k):
        raise AssertionError("the dataset loader must not be reached")

    monkeypatch.setattr(C, "_load_array", _no_dataset_loads)
    monkeypatch.setattr(C, "_load_mask_array", _no_dataset_loads)

    C.playground_open_loose_image(ctx, str(tif))
    st = ctx.state.playground
    assert st.raw_arr.shape == (32, 32)
    assert tif.name in ctx.statuses[-1]
    # An arbitrary file has no pipeline-produced segmentation.
    assert st.mask_arr is None


def test_the_loose_frame_keeps_one_identity_so_the_correction_memo_hits(ctx, tif):
    """``playground_on_change`` keys its correction cache on ``id(raw)``; a fresh
    array per call would re-run the (seconds-long) correction on every keystroke."""
    a = C._load_loose_frame(ctx, str(tif))
    b = C._load_loose_frame(ctx, str(tif))
    assert a is b


def test_an_unreadable_file_is_reported_not_silently_blank(ctx, tmp_path):
    bogus = tmp_path / "not-an-image.tif"
    bogus.write_text("nope")
    C.playground_open_loose_image(ctx, str(bogus))
    assert ctx.state.playground.raw_arr is None
    assert "Could not read" in ctx.statuses[-1]


def test_clearing_returns_to_the_dataset_source(ctx, tif):
    C.playground_open_loose_image(ctx, str(tif))
    C.playground_clear_loose_image(ctx)
    assert ctx.state.playground.loose_image_path is None
    assert "Select a well" in ctx.statuses[-1]


def test_loading_a_dataset_ends_loose_mode(ctx, tif):
    C.playground_open_loose_image(ctx, str(tif))
    # What features/registry._loaded_playground does on a dataset swap.
    ctx.state.playground.clear_caches()
    assert ctx.state.playground.loose_image_path is None


# ── reference-well correction on a lone file ─────────────────────────────────

def test_reference_modes_are_refused_by_name_on_a_loose_image(ctx, tif):
    """empty_well / basic estimate their field from a whole reference well, which
    one file does not have. The frame still renders — but the status says which
    correction did not run, rather than presenting an uncorrected frame as
    corrected."""
    st = ctx.state.playground
    st.flatfield_mode = "empty_well"
    st.background_method = "empty_well"
    C.playground_open_loose_image(ctx, str(tif))

    msg = ctx.statuses[-1]
    assert "flat-field empty_well" in msg
    assert "background empty_well" in msg
    assert "need a loaded plate" in msg
    assert st.raw_arr is not None            # still drew something


def test_ordinary_correction_is_unaffected_on_a_loose_image(ctx, tif):
    st = ctx.state.playground
    st.flatfield_mode = "none"
    st.background_method = "sliding_disk"
    st.radius = 8
    C.playground_open_loose_image(ctx, str(tif))
    assert "sliding_disk" in ctx.statuses[-1]
    assert "need a loaded plate" not in ctx.statuses[-1]


# ── live-label source and staleness ──────────────────────────────────────────

def _labels():
    lab = np.zeros((32, 32), dtype=np.int32)
    lab[4:10, 4:10] = 1
    lab[20:26, 20:26] = 2
    return lab


def test_live_labels_replace_the_precomputed_mask(ctx, tif):
    st = ctx.state.playground
    C.playground_open_loose_image(ctx, str(tif))
    lab = _labels()
    st.seg_live = True
    st.seg_labels = lab
    st.seg_key = C._seg_key(ctx)
    st.seg_status = "StarDist: 2 nuclei (prob=model, nms=model)"

    C.playground_on_change(ctx)
    assert ctx.masks[-1] is lab
    assert "2 nuclei" in ctx.statuses[-1]


def test_labels_go_stale_when_the_correction_changes(ctx, tif):
    st = ctx.state.playground
    C.playground_open_loose_image(ctx, str(tif))
    st.seg_live = True
    st.seg_labels = _labels()
    st.seg_key = C._seg_key(ctx)

    st.radius = 25                               # a different corrected frame
    C.playground_on_change(ctx)
    assert C._seg_is_stale(ctx)
    # Dropped, not drawn against a frame they do not describe...
    assert ctx.masks[-1] is None
    # ...and the disappearance is explained.
    assert "stale" in ctx.statuses[-1]


def test_labels_go_stale_when_a_threshold_changes(ctx, tif):
    st = ctx.state.playground
    C.playground_open_loose_image(ctx, str(tif))
    st.seg_live = True
    st.seg_labels = _labels()
    st.seg_key = C._seg_key(ctx)
    assert not C._seg_is_stale(ctx)

    st.seg_prob_thresh = 0.2
    assert C._seg_is_stale(ctx)


def test_live_mode_off_keeps_the_pipeline_mask(ctx, tif):
    st = ctx.state.playground
    C.playground_open_loose_image(ctx, str(tif))
    st.seg_labels = _labels()
    st.seg_key = C._seg_key(ctx)
    st.seg_live = False

    C.playground_on_change(ctx)
    assert ctx.masks[-1] is None                 # a loose image has no mask
    assert "nuclei" not in ctx.statuses[-1]


def test_segmenting_with_nothing_displayed_says_so(ctx):
    C.playground_segment_now(ctx)
    assert "Nothing to segment" in ctx.statuses[-1]
    assert ctx.state.playground.seg_running is False


def test_a_failed_run_clears_the_labels_and_names_the_reason(ctx, tif, monkeypatch):
    st = ctx.state.playground
    C.playground_open_loose_image(ctx, str(tif))

    captured = {}

    def _fake_request(arr, *, prob_thresh, nms_thresh, on_done, on_failed=None,
                      on_stage=None):
        captured["on_failed"] = on_failed
        return object()

    monkeypatch.setattr(C, "request_stardist_labels", _fake_request)
    C.playground_segment_now(ctx)
    assert st.seg_running is True

    captured["on_failed"]("StarDist is not installed in this environment")
    assert st.seg_running is False
    assert st.seg_labels is None
    assert "not installed" in ctx.statuses[-1]


def test_a_finished_run_reports_the_count_and_the_thresholds(ctx, tif, monkeypatch):
    st = ctx.state.playground
    st.seg_prob_thresh = 0.25
    C.playground_open_loose_image(ctx, str(tif))

    captured = {}

    def _fake_request(arr, *, prob_thresh, nms_thresh, on_done, on_failed=None,
                      on_stage=None):
        captured["on_done"] = on_done
        captured["on_stage"] = on_stage
        captured["thresholds"] = (prob_thresh, nms_thresh)
        return object()

    monkeypatch.setattr(C, "request_stardist_labels", _fake_request)
    C.playground_segment_now(ctx)
    assert captured["thresholds"] == (0.25, None)

    captured["on_done"](_labels())
    assert st.seg_live is True
    assert "2 nuclei" in st.seg_status
    assert "prob=0.25" in st.seg_status and "nms=model" in st.seg_status


# ── the stage control: which correction actually runs ────────────────────────
#
# Reported as "changing any mode of background correction or flat field does
# nothing". Four causes stacked; three are covered here (the fourth, the LUT
# auto-window, is deliberately unchanged — which is why these assert on the
# rendered ARRAY, not on the picture).

@pytest.fixture
def gradient_tif(tmp_path):
    """A frame with a real background gradient, so subtracting one is visible."""
    tifffile = pytest.importorskip("tifffile")
    yy, xx = np.mgrid[0:64, 0:64]
    img = (400 + 3 * yy + 2 * xx).astype(np.float32)
    img[20:30, 20:30] += 2000                     # something object-like
    path = tmp_path / "gradient.tif"
    tifffile.imwrite(path, img.astype(np.uint16))
    return path


def _rendered(ctx):
    """The array handed to the renderer on the last draw."""
    return ctx.rendered[-1][0]


@pytest.mark.parametrize(("stage", "expected"), [
    ("raw", {"flatfield": False, "background": False,
             "background_params": False, "background_well": False}),
    ("flatfield", {"flatfield": True, "background": False,
                   "background_params": False, "background_well": True}),
    ("corrected", {"flatfield": True, "background": True,
                   "background_params": True, "background_well": True}),
])
def test_which_controls_each_stage_consults(stage, expected):
    """The rule the panel greys out by. Radius / small-px / mesh are background
    parameters — ``correct_single_frame`` passes them nowhere else — so they are
    dead on the flat-field stage too."""
    assert C.correction_controls_enabled(stage) == expected


def test_changing_the_background_method_changes_the_pixels(ctx, gradient_tif):
    """The regression test for the report. Asserts on the rendered array rather
    than the image, so it is unaffected by the auto-LUT that can make all four of
    these look identical on screen."""
    st = ctx.state.playground
    st.source = "corrected"
    st.radius = 12
    C.playground_open_loose_image(ctx, str(gradient_tif))

    seen = {}
    for method in ("none", "fov_median", "sliding_disk", "robust_mesh"):
        st.background_method = method
        C.playground_on_change(ctx)
        arr = _rendered(ctx)
        seen[method] = (round(float(arr.min()), 3), round(float(arr.max()), 3),
                        round(float(arr.mean()), 3))
    assert len(set(seen.values())) == 4, f"methods produced identical data: {seen}"


def test_the_raw_stage_ignores_the_correction_controls_and_says_so(ctx, gradient_tif):
    """Inert *by design* on this stage — the bug was that nothing said so."""
    st = ctx.state.playground
    st.source = "raw"
    C.playground_open_loose_image(ctx, str(gradient_tif))
    first = _rendered(ctx).copy()

    for method in ("sliding_disk", "robust_mesh"):
        st.background_method = method
        C.playground_on_change(ctx)
        assert np.array_equal(_rendered(ctx), first)
    assert "Stage: Raw" in ctx.statuses[-1]


def test_the_flatfield_stage_names_the_flatfield_it_applied(ctx, gradient_tif):
    """It used to print a bare ``Flat-field — <frame>``, naming neither mode —
    so the one remaining signal went quiet exactly where the Background control
    had gone inert."""
    st = ctx.state.playground
    st.source = "flatfield"
    st.flatfield_mode = "gaussian"
    st.background_method = "robust_mesh"
    C.playground_open_loose_image(ctx, str(gradient_tif))

    status = ctx.statuses[-1]
    assert "Flat-field only" in status
    assert "gaussian" in status
    # The background is not applied on this stage, and the line says "+ none"
    # rather than implying robust_mesh ran.
    assert "robust_mesh" not in status


def test_the_status_line_carries_the_data_range(ctx, gradient_tif):
    """The mitigation for the untouched auto-LUT: when the picture cannot show
    that a correction ran, these numbers can."""
    st = ctx.state.playground
    st.source = "corrected"
    st.background_method = "none"
    C.playground_open_loose_image(ctx, str(gradient_tif))
    before = ctx.statuses[-1]

    st.background_method = "fov_median"
    C.playground_on_change(ctx)
    after = ctx.statuses[-1]

    assert "range" in before and "mean" in before
    assert before != after, "the range readout must move when the data does"


# ── outline provenance ───────────────────────────────────────────────────────

def test_the_overlay_names_whose_mask_it_is(ctx):
    """Outlines from a pipeline run and outlines from this tab look identical,
    and only one of them responds to the Correction controls."""
    st = ctx.state.playground
    st.mask_arr = np.zeros((16, 16), dtype=np.int32)
    st.mask_arr[4:8, 4:8] = 1

    st.seg_live = False
    mask, provenance = C._mask_source(ctx)
    assert mask is st.mask_arr and provenance == "pipeline mask"

    st.seg_live = True
    st.seg_labels = np.zeros((16, 16), dtype=np.int32)
    st.seg_key = C._seg_key(ctx)
    mask, provenance = C._mask_source(ctx)
    assert mask is st.seg_labels and provenance == "live StarDist"


def test_stale_live_labels_drop_the_overlay_instead_of_reverting(ctx):
    """The subtle one. On a dataset frame the overlay used to fall back to the
    pipeline's saved mask while the status said the live labels were stale — two
    different claims on screen, with no way to tell which was drawn."""
    st = ctx.state.playground
    st.mask_arr = np.zeros((16, 16), dtype=np.int32)
    st.mask_arr[4:8, 4:8] = 1
    st.seg_live = True
    st.seg_labels = np.zeros((16, 16), dtype=np.int32)
    st.seg_key = C._seg_key(ctx)

    st.radius = 99                                # invalidates the labels
    assert C._seg_is_stale(ctx)
    mask, provenance = C._mask_source(ctx)
    assert mask is None and provenance == "none"


def test_switching_display_stage_does_not_make_labels_stale(ctx):
    """Segmentation always runs on the corrected frame, so the *displayed* stage
    is not part of what the labels describe."""
    st = ctx.state.playground
    st.source = "corrected"
    st.seg_labels = np.zeros((16, 16), dtype=np.int32)
    st.seg_key = C._seg_key(ctx)

    st.source = "raw"
    assert not C._seg_is_stale(ctx)


# ── segmentation runs on the corrected frame, whatever is displayed ──────────

def test_segmentation_uses_the_corrected_frame_while_viewing_raw(ctx, gradient_tif):
    """The pipeline segments the corrected channel. Segmenting raw because the
    view happened to be on Raw, and reporting the count identically, is the
    substitution this prevents."""
    st = ctx.state.playground
    st.radius = 12
    st.background_method = "sliding_disk"
    st.source = "raw"
    C.playground_open_loose_image(ctx, str(gradient_tif))

    arr, note = C._segmentation_frame(ctx)
    assert not np.array_equal(arr, st.raw_arr), "must not segment the raw frame"
    assert "corrected frame" in note and "Raw" in note


def test_no_stage_note_when_the_corrected_frame_is_what_is_shown(ctx, gradient_tif):
    st = ctx.state.playground
    st.source = "corrected"
    C.playground_open_loose_image(ctx, str(gradient_tif))
    _arr, note = C._segmentation_frame(ctx)
    assert note == ""


def test_a_reference_mode_without_its_field_refuses_by_name(ctx, gradient_tif):
    """empty_well needs a field built from a whole well. Rather than segment
    something else, say what is missing."""
    st = ctx.state.playground
    st.source = "corrected"
    C.playground_open_loose_image(ctx, str(gradient_tif))
    st.loose_image_path = None                    # pretend a dataset frame
    st.background_method = "empty_well"
    st.background_well = ""

    arr, reason = C._segmentation_frame(ctx)
    assert arr is None
    assert "reference field" in reason and "Bg well" in reason


# ── the read-out: what was calculated, and what is on screen ─────────────────
#
# None of what follows is visible in the picture. The outlines from a pipeline
# run and from this tab look identical; binary mode renders three different
# rules identically; and a mask that does not fit the frame silently changes
# what binary mode even means. So it is stated in words, and asserted here.

def _with_mask(ctx, monkeypatch, mask):
    """Give the loose-image path a mask, which it otherwise never has."""
    real = C._resolve_frame

    def _patched(c):
        raw, label, key = real(c)
        c.state.playground.mask_arr = mask
        return raw, label, key
    monkeypatch.setattr(C, "_resolve_frame", _patched)


def _labels_3():
    lab = np.zeros((64, 64), dtype=np.int32)
    lab[10:20, 10:20] = 1                     # bright, large
    lab[40:48, 40:48] = 2                     # dim, large
    lab[55:58, 55:58] = 3                     # tiny — the QC filter drops it
    return lab


@pytest.fixture
def framed(ctx, gradient_tif):
    st = ctx.state.playground
    st.source = "corrected"
    st.radius = 12
    st.background_method = "sliding_disk"
    C.playground_open_loose_image(ctx, str(gradient_tif))
    return ctx


def test_the_readout_states_the_frame_stage_and_range(framed):
    status = framed.statuses[-1]
    assert "Frame: gradient.tif" in status
    assert "Stage: Corrected" in status and "sliding_disk" in status
    assert "Image: range" in status and "mean" in status


def test_the_readout_says_a_pipeline_mask_will_not_follow_the_correction(
        ctx, gradient_tif, monkeypatch):
    """The reported confusion: outlines that cannot move, with nothing saying so."""
    _with_mask(ctx, monkeypatch, _labels_3())
    ctx.state.playground.source = "corrected"
    C.playground_open_loose_image(ctx, str(gradient_tif))

    status = ctx.statuses[-1]
    assert "Mask: pipeline mask" in status
    assert "does NOT change when you change a correction" in status
    assert "3 labels found" in status


def test_the_readout_reports_what_the_qc_filter_removed(ctx, gradient_tif, monkeypatch):
    _with_mask(ctx, monkeypatch, _labels_3())
    st = ctx.state.playground
    st.qc_min_area = 50                       # drops the 3x3 label
    C.playground_open_loose_image(ctx, str(gradient_tif))

    status = ctx.statuses[-1]
    assert "QC size filter kept 2 of 3" in status
    assert "outlines around 2 labels" in status


def test_binary_names_the_per_cell_rule_and_the_threshold_source(
        ctx, gradient_tif, monkeypatch):
    _with_mask(ctx, monkeypatch, _labels_3())
    st = ctx.state.playground
    st.binary_mask = True
    # After the frame, not before: opening a frame clears any local threshold so
    # the control falls back to the channel's value (see playground_on_change).
    C.playground_open_loose_image(ctx, str(gradient_tif))
    st.threshold = 800.0
    C.playground_on_change(ctx)

    status = ctx.statuses[-1]
    assert "MEAN intensity" in status
    assert "800" in status and "set on this tab" in status
    assert "pass." in status


def test_binary_says_when_it_fell_back_to_a_per_pixel_threshold(
        ctx, gradient_tif, monkeypatch):
    """A mask that does not fit the frame turns binary mode into something else
    entirely — a pixel threshold — and the picture cannot show the difference."""
    wrong_size = np.zeros((32, 32), dtype=np.int32)
    wrong_size[2:10, 2:10] = 1
    _with_mask(ctx, monkeypatch, wrong_size)
    st = ctx.state.playground
    st.binary_mask = True
    st.threshold = 500.0
    C.playground_open_loose_image(ctx, str(gradient_tif))

    status = ctx.statuses[-1]
    assert "does NOT match this frame" in status
    assert "PER-PIXEL" in status
    assert "not a per-cell measurement" in status


def test_binary_with_no_mask_at_all_is_also_named(framed):
    st = framed.state.playground
    st.binary_mask = True
    st.threshold = 500.0
    C.playground_on_change(framed)

    status = framed.statuses[-1]
    assert "Mask: none" in status
    assert "PER-PIXEL" in status


def test_the_readout_distinguishes_the_two_threshold_sources(
        ctx, gradient_tif, monkeypatch):
    """Whether the cut came from this tab or from the channel's app-wide ON
    threshold changes what the binary picture means."""
    _with_mask(ctx, monkeypatch, _labels_3())
    st = ctx.state.playground
    st.binary_mask = True
    st.threshold = None                       # fall back to the channel's value
    C.playground_open_loose_image(ctx, str(gradient_tif))
    assert "this channel's ON threshold" in ctx.statuses[-1]

    st.threshold = 123.0
    C.playground_on_change(ctx)
    assert "set on this tab" in ctx.statuses[-1]


def test_the_readout_says_when_outlines_are_simply_off(framed):
    st = framed.state.playground
    st.show_overlay = False
    C.playground_on_change(framed)
    assert "outlines are off" in framed.statuses[-1]


def test_a_single_label_reads_as_singular(framed, monkeypatch):
    one = np.zeros((64, 64), dtype=np.int32)
    one[10:20, 10:20] = 1
    _with_mask(framed, monkeypatch, one)
    C.playground_on_change(framed)
    status = framed.statuses[-1]
    assert "1 label found" in status and "1 labels" not in status


# ── the reference-well cache: which field a mode actually gets ───────────────
#
# Reported as "when I change to empty well for correction, it applies no change.
# The other flat fields do appear to work". gaussian is estimated from the frame
# itself, so it always works; empty_well and basic estimate a field from a whole
# reference well first, and the cache that held those fields was keyed on the
# well ALONE. It therefore answered "already built" for fields it had never
# built — a different channel's, a different mode's, or one whose build found no
# frames — and ``apply_flatfield`` no-ops silently on the None that followed.

@pytest.fixture
def builds(monkeypatch):
    """Capture reference-field build requests instead of running a worker.

    Each entry exposes the ``on_done`` the controller passed, so a test can land
    a result (or the absence of one) at the moment of its choosing.
    """
    calls = []

    def _request(ctx, well, chan, *, flatfield_mode, background_method,
                 on_done, on_failed=None, on_progress=None, max_samples=30):
        calls.append(types.SimpleNamespace(
            well=well, chan=chan, ff_mode=flatfield_mode,
            bg_method=background_method, on_done=on_done,
            on_failed=on_failed, on_progress=on_progress))
        return object()

    monkeypatch.setattr(C, "request_reference_fields", _request)
    return calls


@pytest.fixture
def plate(ctx, monkeypatch):
    """A *dataset* frame rather than a loose file, so the reference-well modes
    are live (they are dropped by name on a loose image)."""
    frame = (400.0 + 3 * np.mgrid[0:32, 0:32][0]).astype(np.float32)

    def _resolve(c):
        c.state.playground.mask_arr = None
        return frame, "H12 GFP t1 fov1", ("A01", "GFP", "1", "1")

    monkeypatch.setattr(C, "_resolve_frame", _resolve)
    st = ctx.state.playground
    st.source = "corrected"
    st.channel = "GFP"
    st.background_well = "H12"
    return ctx


def _field(value: float):
    return np.full((32, 32), value, dtype=np.float32)


def test_switching_basic_to_empty_well_rebuilds_instead_of_reusing_the_field(
        plate, builds):
    """The reported case. ``basic`` and ``empty_well`` estimate DIFFERENT fields
    from the same frames, so a cache that cannot tell them apart hands the second
    mode the first one's field and the picture never changes."""
    st = plate.state.playground
    st.flatfield_mode = "basic"
    C.playground_on_change(plate)
    assert len(builds) == 1
    builds[0].on_done("H12", "gfp", _field(1.5), None)
    with_basic = _rendered(plate)

    st.flatfield_mode = "empty_well"
    C.playground_on_change(plate)
    assert len(builds) == 2, "empty_well was served the basic field"
    assert builds[1].ff_mode == "empty_well"
    builds[1].on_done("H12", "gfp", _field(3.0), None)

    assert not np.allclose(with_basic, _rendered(plate))


def test_switching_channel_rebuilds_rather_than_correcting_with_no_field(
        plate, builds):
    """The fields are per channel. A cache keyed on the well alone says "built"
    for a channel it never sampled."""
    st = plate.state.playground
    st.flatfield_mode = "basic"
    C.playground_on_change(plate)
    builds[0].on_done("H12", "gfp", _field(1.5), None)

    st.channel = "DAPI"
    C.playground_on_change(plate)
    assert len(builds) == 2 and builds[1].chan == "dapi"


def test_turning_on_the_empty_well_background_builds_its_own_field(plate, builds):
    """The additive background field is only estimated when the BACKGROUND is
    empty_well. Having built a flat-field for the same well must not stand in
    for it."""
    st = plate.state.playground
    st.flatfield_mode = "basic"
    C.playground_on_change(plate)
    builds[0].on_done("H12", "gfp", _field(1.5), None)

    st.background_method = "empty_well"
    C.playground_on_change(plate)
    assert len(builds) == 2 and builds[1].bg_method == "empty_well"


def test_a_field_that_cannot_be_built_is_named_and_attempted_only_once(
        plate, builds):
    """A build that finds no frames must neither respawn on the next keystroke
    nor let the read-out claim the mode ran."""
    st = plate.state.playground
    st.flatfield_mode = "empty_well"
    C.playground_on_change(plate)
    builds[0].on_done("H12", "gfp", None, None)

    status = plate.statuses[-1]
    assert "could not be built" in status and "H12" in status
    # Downgraded to what actually ran, not named as if it had been applied.
    assert "empty_well" not in status.split("Stage:")[1].split("\n")[0]

    C.playground_on_change(plate)
    assert len(builds) == 1, "an impossible build was retried"


def test_a_build_is_filed_under_the_modes_it_was_requested_with(plate, builds):
    """A dropdown changed while the worker runs must not relabel its result."""
    st = plate.state.playground
    st.flatfield_mode = "basic"
    C.playground_on_change(plate)
    st.flatfield_mode = "empty_well"                  # changed mid-build
    builds[0].on_done("H12", "gfp", _field(1.5), None)

    assert ("ff", "H12", "gfp", "basic") in st.ref_fields
    assert ("ff", "H12", "gfp", "empty_well") not in st.ref_fields


def test_segmentation_and_display_resolve_the_same_reference_fields(plate, builds):
    """Both go through one resolver, so the pixels StarDist sees are the pixels
    on screen — the two gates cannot drift apart again."""
    st = plate.state.playground
    st.flatfield_mode = "basic"
    C.playground_on_change(plate)
    builds[0].on_done("H12", "gfp", _field(1.5), None)

    arr, note = C._segmentation_frame(plate)
    assert arr is not None and note == ""
    assert np.allclose(arr, _rendered(plate))


def test_segmentation_waits_rather_than_segmenting_an_uncorrected_frame(
        plate, builds):
    st = plate.state.playground
    st.flatfield_mode = "empty_well"
    C.playground_on_change(plate)                     # build requested, pending

    arr, reason = C._segmentation_frame(plate)
    assert arr is None and "building" in reason


# ── the Cells count ─────────────────────────────────────────────────────────
#
# A formatting of the same display_facts the paragraph read-out uses, so the two
# cannot disagree. Its whole job at the edges is to print "—" where there is no
# count, because 0 is a measurement and "unknown" is not.

def test_the_count_names_the_drawn_number_and_its_source():
    txt = C._count_text({"provenance": "live StarDist", "labels_in": 3,
                         "labels_kept": 3, "shape_ok": True,
                         "binary_rule": "footprint"})
    assert "Cells: 3 drawn" in txt and "live StarDist" in txt
    assert "above" not in txt


def test_no_mask_reads_as_a_dash_not_as_zero():
    assert C._count_text({"provenance": "none"}) == "Cells: — (no mask)"


def test_stale_labels_read_as_a_dash_not_as_zero():
    txt = C._count_text({"provenance": "none", "stale_labels": True})
    assert "—" in txt and "Segment" in txt and "0" not in txt


def test_the_threshold_subset_appears_only_under_the_per_cell_rule():
    facts = {"provenance": "pipeline mask", "labels_in": 3, "labels_kept": 3,
             "shape_ok": True, "binary_rule": "per-cell", "above": 2,
             "threshold": 120.0}
    assert "2 above 120" in C._count_text(facts)
    # Under the other two rules no per-cell verdict was reached at all.
    facts["binary_rule"] = "per-pixel"
    assert "above" not in C._count_text(facts)


def test_the_count_flags_a_mask_that_does_not_fit_the_frame():
    txt = C._count_text({"provenance": "pipeline mask", "labels_in": 3,
                         "labels_kept": 3, "shape_ok": False,
                         "binary_rule": "footprint"})
    assert "does not fit" in txt


def test_the_count_reports_what_the_qc_filter_dropped():
    txt = C._count_text({"provenance": "pipeline mask", "labels_in": 3,
                         "labels_kept": 2, "shape_ok": True,
                         "binary_rule": "footprint"})
    assert "Cells: 2 drawn" in txt and "QC dropped 1 of 3" in txt


def test_the_count_row_is_written_by_every_render(framed):
    assert framed.counts and framed.counts[-1].startswith("Cells:")


def test_the_count_clears_when_no_frame_can_be_shown(ctx):
    C.playground_on_change(ctx)
    assert ctx.counts[-1] == "Cells: —"


# ── binary mode's fill colour ────────────────────────────────────────────────

def test_the_readout_names_the_fill_colour_binary_mode_used(framed, monkeypatch):
    """All-white and one-colour-per-label answer different questions, and the
    read-out has to say which one is on screen."""
    st = framed.state.playground
    _with_mask(framed, monkeypatch, _labels_3())
    st.binary_mask = True
    C.playground_on_change(framed)
    assert "own colour" in framed.statuses[-1]

    st.binary_label_colors = False
    C.playground_on_change(framed)
    assert "white" in framed.statuses[-1] and "own colour" not in framed.statuses[-1]


def test_a_mask_that_does_not_fit_reports_no_label_colours(framed, monkeypatch):
    """That render falls through to the per-pixel threshold, which has no labels
    — so the facts must not claim colours the renderer never drew."""
    st = framed.state.playground
    _with_mask(framed, monkeypatch, np.ones((8, 8), dtype=np.int32))
    st.binary_mask = True
    st.binary_label_colors = True
    C.playground_on_change(framed)
    assert st.display_facts["label_colors"] is False


# ── the shared progress bar ─────────────────────────────────────────────────
#
# The Playground's two long jobs run off-thread and used to report only a line
# of text. They now drive the same status-row bar smFISH and the dataset load
# use. The rule the tests below encode is that the bar is *hidden on every exit
# path*: one left sweeping after a failure says the work continues.

def test_the_reference_build_shows_a_determinate_bar_and_counts_frames(
        plate, builds):
    C_bar = plate.bar
    st = plate.state.playground
    st.flatfield_mode = "basic"
    C.playground_on_change(plate)
    assert C_bar.visible is True
    assert C_bar.shown_ranges[-1][1] > 0          # determinate: it knows the total

    builds[0].on_progress(3, 30)
    assert C_bar.maximum() == 30 and C_bar.value_ == 3
    assert "3/30 frames" in plate.statuses[-1]

    builds[0].on_done("H12", "gfp", _field(1.5), None)
    assert C_bar.visible is False


def test_a_failed_reference_build_hides_the_bar(plate, builds):
    st = plate.state.playground
    st.flatfield_mode = "basic"
    C.playground_on_change(plate)
    assert plate.bar.visible is True
    builds[0].on_failed("the well vanished")
    assert plate.bar.visible is False
    assert "Model build failed" in plate.statuses[-1]


def _segment_stub(monkeypatch, captured):
    def _fake(arr, *, prob_thresh, nms_thresh, on_done, on_failed=None,
              on_stage=None):
        captured.update(on_done=on_done, on_failed=on_failed, on_stage=on_stage)
        return object()
    monkeypatch.setattr(C, "request_stardist_labels", _fake)


def test_segmentation_shows_an_indeterminate_bar_and_names_its_phases(
        ctx, tif, monkeypatch):
    """One frame's inference has no interior milestones, so a percentage would be
    invented — but the phases are worth naming, because the model load is the
    10-30 s one."""
    captured: dict = {}
    _segment_stub(monkeypatch, captured)
    C.playground_open_loose_image(ctx, str(tif))
    C.playground_segment_now(ctx)

    assert ctx.bar.visible is True
    assert ctx.bar.shown_ranges[-1] == (0, 0)      # indeterminate
    captured["on_stage"]("loading TensorFlow and the StarDist model")
    assert "loading TensorFlow" in ctx.statuses[-1]
    captured["on_stage"]("segmenting")
    assert "segmenting" in ctx.statuses[-1]

    captured["on_done"](_labels())
    assert ctx.bar.visible is False


def test_a_failed_segmentation_hides_the_bar(ctx, tif, monkeypatch):
    """The commonest failure in a fresh environment is "StarDist is not
    installed", and a bar still sweeping under that message would say the work
    is continuing."""
    captured: dict = {}
    _segment_stub(monkeypatch, captured)
    C.playground_open_loose_image(ctx, str(tif))
    C.playground_segment_now(ctx)
    assert ctx.bar.visible is True

    captured["on_failed"]("StarDist is not installed in this environment")
    assert ctx.bar.visible is False
    assert "not installed" in ctx.state.playground.seg_status
