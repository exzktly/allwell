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
    monkeypatch.setattr(C, "_set_status", lambda win, text: c.statuses.append(text))
    monkeypatch.setattr(C, "render_playground_display", lambda win: None)
    monkeypatch.setattr(
        C, "build_playground_rgb",
        lambda arr, mask, **kw: c.masks.append(mask) or np.zeros((*np.shape(arr), 3),
                                                                dtype=np.uint8))
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


# ── handoff to Analyze ───────────────────────────────────────────────────────

def test_params_are_handed_to_the_shell(ctx):
    st = ctx.state.playground
    st.seg_prob_thresh, st.seg_nms_thresh = 0.3, None
    sent = []
    ctx.window.send_stardist_params_to_analyze = (
        lambda p, n: sent.append((p, n)) or True)
    assert C.playground_send_params_to_analyze(ctx) is True
    assert sent == [(0.3, None)]


def test_a_shell_without_an_analyze_pane_reports_failure(ctx):
    """False, so the caller can say the values were not copied instead of
    looking like it worked."""
    assert C.playground_send_params_to_analyze(ctx) is False


def test_an_out_of_range_threshold_is_reported_even_with_the_preview_off(ctx, tif):
    """A number in the box that is not the number being used has to say so —
    otherwise a typo sits there looking accepted."""
    class _Edit:
        def __init__(self, text):
            self._t = text

        def text(self):
            return self._t

    ctx.window._playground_seg_prob_edit = _Edit("1.5")
    ctx.state.playground.seg_live = False
    C.playground_open_loose_image(ctx, str(tif))

    assert ctx.state.playground.seg_prob_thresh is None
    assert "Probability" in ctx.statuses[-1]
    assert "between 0 and 1" in ctx.statuses[-1]


def test_segmenting_re_renders_first_so_the_key_matches_the_pixels(ctx, tif, monkeypatch):
    """A control edited since the last render must reach the frame StarDist is
    given, not just the key the labels are tagged with."""
    st = ctx.state.playground
    C.playground_open_loose_image(ctx, str(tif))
    renders_before = len(ctx.masks)

    captured = {}

    def _fake_request(arr, *, prob_thresh, nms_thresh, on_done, on_failed=None):
        captured["arr"] = arr
        captured["on_done"] = on_done
        return object()

    monkeypatch.setattr(C, "request_stardist_labels", _fake_request)
    st.radius = 12                              # changed since the last render
    C.playground_segment_now(ctx)

    # It re-rendered, and handed StarDist the freshly rendered frame.
    assert len(ctx.masks) > renders_before
    assert captured["arr"] is st.display_arr

    # The key stamped on the result describes the settings actually used.
    captured["on_done"](_labels())
    assert st.seg_key == C._seg_key(ctx)
    assert not C._seg_is_stale(ctx)
