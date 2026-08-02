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

from well_viewer.features.image_browser.playground import controller as C
from well_viewer.features.image_browser.playground.state import PlaygroundState


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
    )
    c.statuses = []
    c.masks = []
    c.rendered = []
    monkeypatch.setattr(C, "_set_status", lambda win, text: c.statuses.append(text))
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

    def _fake_request(arr, *, prob_thresh, nms_thresh, on_done, on_failed=None):
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

    def _fake_request(arr, *, prob_thresh, nms_thresh, on_done, on_failed=None):
        captured["on_done"] = on_done
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
    assert ctx.statuses[-1].startswith("Raw")


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
