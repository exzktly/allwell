"""Clicking the Playground's threshold curve shows the outlines at that point.

The curve answers "how many nuclei at each threshold"; the question a user is
asking is "which ones", and only the picture answers it. A click on a point of
the curve therefore writes that threshold into the Probability box and
re-segments the displayed frame through the Segment button's own path.

Everything here goes through the real controls (CLAUDE.md §7.6, §7.8 rule 3):
the tab is built by its real constructor, the sweep is started by pressing the
real button, the curve window is the real one ``show_threshold_curve`` opens,
and the pick is a real mouse press on its canvas at the pixel where the point is
drawn. Two seams are replaced, both *below* the controls: the sweep's worker
(the environment installs no TensorFlow) and the single-frame segmentation
worker, whose fake returns labels that depend on the threshold it is handed —
so the outlines on the image say which threshold produced them, and an
implementation that segmented at the previous threshold, or at none, draws
different pixels (§7.8 rule 2).

The assertions sit at the boundary (§7.4 #1): the pixels on the canvas label,
the words in the read-out, the text in the Probability box, and the ring drawn
on the curve.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import math

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")
tifffile = pytest.importorskip("tifffile")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

import review.features.image_browser.playground.controller as pc  # noqa: E402
import review.features.image_browser.playground.worker as pw  # noqa: E402
from review.features.image_browser.playground.sweep_curve_view import (  # noqa: E402
    CURVE_GID, PICKED_GID,
)

# Three square "nuclei". Label k is blob k, and a segmentation at threshold t
# keeps the first ``_kept(t)`` of them — so each threshold band draws a
# different set of outlines.
_BLOBS = ((slice(8, 18), slice(8, 18)),
          (slice(8, 18), slice(40, 50)),
          (slice(40, 50), slice(24, 34)))
_SIZE = 64


def _kept(t: float) -> int:
    return 3 if t < 0.4 else (2 if t < 0.7 else 1)


def _labels_at(t: float) -> np.ndarray:
    lab = np.zeros((_SIZE, _SIZE), dtype=np.int32)
    for k, (r, c) in enumerate(_BLOBS[:_kept(t)], start=1):
        lab[r, c] = k
    return lab


@pytest.fixture
def frame_tif(tmp_path):
    img = np.full((_SIZE, _SIZE), 100, dtype=np.uint16)
    for r, c in _BLOBS:
        img[r, c] = 600
    # One hot pixel far from every blob, so the blobs render grey and only an
    # outline is white.
    img[_SIZE - 1, _SIZE - 1] = 4000
    path = tmp_path / "blobs.tif"
    tifffile.imwrite(path, img)
    return path


@pytest.fixture
def playground(shell_ctx):
    from PySide6.QtWidgets import QHBoxLayout, QWidget
    from review.features.image_browser.playground.tab_view import (
        build_playground_tab,
    )
    ctx, host = shell_ctx
    frame = QWidget()
    QHBoxLayout(frame)
    build_playground_tab(ctx, frame)
    host._test_playground_frame = frame
    return ctx


@pytest.fixture
def swept(playground, frame_tif, monkeypatch):
    """The loose frame on screen, swept by pressing the real button.

    Returns ``(ctx, requests)``: *requests* records the probability threshold
    of every segmentation the tab asked for.
    """
    ctx = playground
    win = ctx.window
    st = ctx.state.playground
    pc.playground_open_loose_image(ctx, str(frame_tif))
    assert st.raw_arr is not None, "the loose frame did not load"

    # Upscale the blit so every image pixel covers at least one screen pixel;
    # the outlines are one pixel wide and a downscaled blit could drop them.
    vp = win._playground_image_canvas.viewport()
    fit = min(max(1, vp.width()) / _SIZE, max(1, vp.height()) / _SIZE)
    st.zoom = float(max(1, math.ceil(1.0 / fit)))

    def _fake_sweep(arr, thresholds, **kw):
        kw["on_done"]([_kept(t) for t in thresholds])
        return object()

    requests: list = []

    def _fake_segment(arr, *, prob_thresh, nms_thresh, on_done,
                      on_failed=None, on_stage=None):
        requests.append(prob_thresh)
        # ``None`` is "the model's own default"; stand in a mid-range value so
        # a pick that never reached the box segments the wrong band rather
        # than raising inside the fake.
        on_done(_labels_at(0.5 if prob_thresh is None else float(prob_thresh)))
        return object()

    monkeypatch.setattr(pw, "request_threshold_sweep", _fake_sweep)
    monkeypatch.setattr(pc, "request_stardist_labels", _fake_segment)
    win._playground_sweep_btn.click()
    popup = ctx.runtime.playground.sweep_curve_popup
    assert popup is not None, "the sweep opened no curve window"
    popup.resize(820, 460)
    popup._canvas.draw()
    yield ctx, requests
    popup.close()


def _curve_line(popup):
    return next(ln for ax in popup._fig.axes for ln in ax.lines
                if ln.get_gid() == CURVE_GID)


def _press(popup, x, y) -> None:
    """A real left-button press at data coordinates (*x*, *y*) of the curve."""
    canvas = popup._canvas
    canvas.draw()
    ax = _curve_line(popup).axes
    px, py = ax.transData.transform((x, y))
    ratio = canvas.devicePixelRatioF()
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier,
                     QPoint(int(round(px / ratio)),
                            int(round(canvas.height() - py / ratio))))


def _press_point(popup, threshold: float) -> int:
    """Press on the drawn point for *threshold*; return the count drawn there."""
    xs, ys = _curve_line(popup).get_data()
    i = int(np.argmin(np.abs(np.asarray(xs) - threshold)))
    _press(popup, float(xs[i]), float(ys[i]))
    return int(ys[i])


def _canvas_pixel(ctx, r: int, c: int) -> tuple[int, int, int]:
    """The colour on the canvas label where image pixel (*r*, *c*) is shown."""
    win = ctx.window
    img = win._playground_image_label.pixmap().toImage()
    d = win._playground_disp
    x = int((c + 0.5) * d["nw"] / d["iw"])
    y = int((r + 0.5) * d["nh"] / d["ih"])
    q = img.pixelColor(x, y)
    return q.red(), q.green(), q.blue()


_WHITE = (255, 255, 255)
# A corner of each blob: on its outline when the blob is segmented, and plain
# grey blob interior-colour when it is not.
_CORNERS = [(r.start, c.start) for r, c in _BLOBS]


def _outlined(ctx) -> list[bool]:
    return [_canvas_pixel(ctx, r, c) == _WHITE for r, c in _CORNERS]


def _status(ctx) -> str:
    return ctx.window._playground_status.text()


def test_clicking_a_point_shows_that_thresholds_outlines_and_names_it(swept):
    """Excludes the click that changes the plot and not the picture.

    Two picks in opposite bands, so the outlines after the second cannot be
    the first's left standing: 0.2 segments all three blobs, 0.8 only one.
    """
    ctx, requests = swept
    popup = ctx.runtime.playground.sweep_curve_popup
    assert _outlined(ctx) == [False, False, False], "outlines before any pick"

    n = _press_point(popup, 0.2)
    assert requests == [pytest.approx(0.2)], "the click did not segment at the clicked threshold"
    assert ctx.window._playground_seg_prob_edit.text() == "0.2"
    assert _outlined(ctx) == [True, True, True]
    assert "StarDist: 3 nuclei (prob=0.2," in _status(ctx)
    assert n == 3

    n = _press_point(popup, 0.8)
    assert requests[-1] == pytest.approx(0.8)
    assert ctx.window._playground_seg_prob_edit.text() == "0.8"
    assert _outlined(ctx) == [True, False, False], (
        "the outlines are not the clicked threshold's")
    assert "StarDist: 1 nuclei (prob=0.8," in _status(ctx)
    assert "prob=0.2" not in _status(ctx)
    assert n == 1


def test_the_picked_point_is_ringed_on_the_curve(swept):
    """Excludes the curve that gives no sign of which point the image shows.

    Asserted by position and by its label, and against a second pick, so a ring
    drawn at the first click and never moved fails too.
    """
    ctx, _requests = swept
    popup = ctx.runtime.playground.sweep_curve_popup

    def _rings():
        return [ln for ax in popup._fig.axes for ln in ax.lines
                if ln.get_gid() == PICKED_GID]

    assert _rings() == [], "a ring before anything was picked"
    _press_point(popup, 0.5)
    rings = _rings()
    assert len(rings) == 1
    assert float(rings[0].get_xdata()[0]) == pytest.approx(0.5)
    assert int(rings[0].get_ydata()[0]) == 2
    assert rings[0].get_label() == "picked: 0.5 (2 on the curve)"

    _press_point(popup, 0.9)
    rings = _rings()
    assert len(rings) == 1
    assert float(rings[0].get_xdata()[0]) == pytest.approx(0.9)


def test_a_press_off_the_curve_changes_nothing(swept):
    """Excludes the pick that snaps any press in the axes to some point.

    The press lands at the top of the axes above the middle of the curve, where
    no point is drawn: nothing may be segmented, the box keeps its text, and the
    picture keeps its outlines.
    """
    ctx, requests = swept
    popup = ctx.runtime.playground.sweep_curve_popup
    _press_point(popup, 0.2)
    before = (list(requests), ctx.window._playground_seg_prob_edit.text(),
              _outlined(ctx))
    ax = _curve_line(popup).axes
    top = ax.get_ylim()[1] * 0.97
    _press(popup, 0.5, top)
    after = (list(requests), ctx.window._playground_seg_prob_edit.text(),
             _outlined(ctx))
    assert after == before


def test_a_curve_from_other_settings_is_not_picked_from(swept):
    """Excludes the threshold read off one set of pixels and shown on another.

    Changing the NMS threshold moves every count on the curve, so after it the
    curve describes pixels no longer on screen and a pick from it is refused —
    with a sentence, and with the box left alone.
    """
    ctx, requests = swept
    popup = ctx.runtime.playground.sweep_curve_popup
    ctx.window._playground_seg_nms_edit.setText("0.1")
    pc.playground_on_change(ctx)
    _press_point(popup, 0.2)
    assert requests == []
    assert ctx.window._playground_seg_prob_edit.text() == ""
    assert "different frame or settings" in _status(ctx)


def test_a_press_before_any_sweep_picks_nothing(playground):
    """No curve, no pick: the controller's guard, reached directly because
    there is no window to press before a sweep has run."""
    ctx = playground
    pc.playground_sweep_pick(ctx, 0.3)
    assert ctx.window._playground_seg_prob_edit.text() == ""
    assert ctx.state.playground.sweep_picked is None
    assert ctx.state.playground.seg_running is False


def test_a_click_while_a_worker_runs_changes_nothing_and_says_why(swept,
                                                                  monkeypatch):
    """Excludes the pick that lands while the session's one model is busy.

    Both workers are held in flight — the fakes below never call back — so the
    refusal is reached through the real running flags, not through a flag the
    test set. The box keeps the threshold of the segmentation in flight, no
    further segmentation is asked for, the outlines on the canvas stay as they
    were, and the read-out says why.
    """
    ctx, requests = swept
    popup = ctx.runtime.playground.sweep_curve_popup
    box = ctx.window._playground_seg_prob_edit

    _press_point(popup, 0.5)                      # completes: two outlines
    assert _outlined(ctx) == [True, True, False]

    held: list = []

    def _held_segment(arr, *, prob_thresh, nms_thresh, on_done,
                      on_failed=None, on_stage=None):
        held.append(prob_thresh)
        return object()

    monkeypatch.setattr(pc, "request_stardist_labels", _held_segment)
    _press_point(popup, 0.2)                      # starts, never finishes
    assert held == [pytest.approx(0.2)]
    assert ctx.state.playground.seg_running is True
    before = (box.text(), _outlined(ctx))

    _press_point(popup, 0.8)
    assert held == [pytest.approx(0.2)], "a second segmentation was started"
    assert (box.text(), _outlined(ctx)) == before
    assert "Still running" in _status(ctx)


def test_a_click_while_a_sweep_runs_changes_nothing_and_says_why(swept,
                                                                 monkeypatch):
    ctx, requests = swept
    popup = ctx.runtime.playground.sweep_curve_popup
    box = ctx.window._playground_seg_prob_edit

    _press_point(popup, 0.2)                      # completes: three outlines
    assert _outlined(ctx) == [True, True, True]

    monkeypatch.setattr(pw, "request_threshold_sweep",
                        lambda *a, **k: object())  # in flight, never returns
    ctx.window._playground_sweep_btn.click()
    assert ctx.state.playground.sweep_running is True
    n_before = len(requests)

    _press_point(popup, 0.8)
    assert len(requests) == n_before, "segmented while the sweep was running"
    assert box.text() == "0.2"
    assert _outlined(ctx) == [True, True, True]
    assert "Still running" in _status(ctx)


def test_a_press_with_the_toolbars_zoom_armed_picks_nothing(swept):
    """Excludes the pick that re-segments the frame every time the user drags
    out a zoom box. Disarming it again and pressing the same point does pick,
    so the press itself is known to land on the line."""
    ctx, requests = swept
    popup = ctx.runtime.playground.sweep_curve_popup
    nav = popup._canvas.toolbar
    assert nav is not None, "the window has no navigation toolbar"
    nav.zoom()                                   # arm
    assert str(nav.mode)
    _press_point(popup, 0.2)
    assert requests == []
    assert ctx.window._playground_seg_prob_edit.text() == ""
    assert _outlined(ctx) == [False, False, False]

    nav.zoom()                                   # disarm
    assert not str(nav.mode)
    _press_point(popup, 0.2)
    assert requests == [pytest.approx(0.2)]


def test_a_refused_sweep_offers_no_threshold_to_pick(playground):
    """A curve that is a refusal (no nuclei at any threshold) has no measured
    point to pick. The window draws no line, so a press cannot reach the pick;
    this is the controller's own half, for any other caller."""
    from review.domain.threshold_sweep import curve_from_counts, sweep_grid
    ctx = playground
    grid = sweep_grid()
    st = ctx.state.playground
    st.sweep_curve = curve_from_counts(grid, [0] * len(grid))
    assert st.sweep_curve.declined
    pc.playground_sweep_pick(ctx, 0.3)
    assert ctx.window._playground_seg_prob_edit.text() == ""
    assert st.sweep_picked is None
