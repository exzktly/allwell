"""A scale bar states a physical length, so it is refused rather than shortened.

``draw_cell_chrome`` used to clamp an over-long bar to the cell width
(``blen = min(bar_px, W - 2)``) and say nothing. Measured on the shipped code at
0.65 µm/px with a 50 µm bar (77 source pixels), a 60-px crop drew 58 px and
presented it as 50 µm — 37.7 µm burned into an exported figure, with nothing on
screen, in the file or in the log to say the number was wrong. It is the silent
substitution §7 of ``CLAUDE.md`` exists to prevent, and it is the exporter
producing a value the data never had.

These are pure-lane on purpose. The one test that renders a bar through the real
app is ``test_image_table_scalebar_render.py``, which is ``slow``-marked and so
did not run in the fast lane for the whole life of the defect.
"""

from __future__ import annotations

import numpy as np
import pytest

from well_viewer.features.image_browser.image_table.image_compose import (
    apply_cell_chrome,
    bar_fits,
    bar_margin,
    draw_cell_chrome,
    reset_scan_misses,
    scale_bar_refusal_report,
)

#: 50 µm at 0.65 µm/px — the numbers the defect was measured with.
BAR_PX = 77
UM_PER_PX = 0.65


def _frame(w: int, h: int | None = None) -> np.ndarray:
    return np.full((h if h is not None else w, w, 3), 30, np.uint8)


def _drawn_length(before: np.ndarray, after: np.ndarray) -> int:
    """The white bar's length in pixels, 0 when no bar was drawn."""
    if after is before or not (after != before).any():
        return 0
    core = np.all(after == 255, axis=2)
    cols = np.nonzero(core.any(axis=0))[0]
    return int(cols.max() - cols.min() + 1) if cols.size else 0


class _State:
    def __init__(self):
        self.scale_bar_refusals: set = set()


class _Ctx:
    def __init__(self):
        self.state = type("S", (), {"image_table": _State()})()


@pytest.mark.parametrize("width", [2048, 512, 240, 100])
def test_a_bar_that_fits_is_drawn_at_its_true_length(width):
    """The length is the measurement, so it must not vary with the cell."""
    frame = _frame(width)
    out = draw_cell_chrome(frame, bar_px=BAR_PX, bar_color="white",
                           bar_thickness_px=4)
    assert _drawn_length(frame, out) == BAR_PX, (
        f"a {BAR_PX}px bar in a {width}px cell must be {BAR_PX}px, so it still "
        f"reads {BAR_PX * UM_PER_PX:.1f} µm"
    )


@pytest.mark.parametrize("width", [60, 40, 20])
def test_a_bar_that_does_not_fit_is_not_drawn_at_all(width):
    """Never shortened: a clamped bar is a wrong number that looks right."""
    frame = _frame(width)
    out = draw_cell_chrome(frame, bar_px=BAR_PX, bar_color="white",
                           bar_thickness_px=4)
    drawn = _drawn_length(frame, out)
    assert drawn == 0, (
        f"a {BAR_PX}px bar was drawn {drawn}px long in a {width}px cell, which "
        f"reads {drawn * UM_PER_PX:.1f} µm and claims to be "
        f"{BAR_PX * UM_PER_PX:.1f} µm"
    )


def test_the_fit_rule_leaves_room_for_the_margin_the_bar_is_drawn_with():
    """``bar_fits`` and the drawing must agree about the inset, or a bar sits
    against the edge at exactly the widths the predicate calls fine."""
    h = 200
    margin = bar_margin(h)
    exact = 100 - 2 * margin
    assert bar_fits(100, h, exact)
    assert not bar_fits(100, h, exact + 1)


def test_a_refused_bar_does_not_take_the_identity_label_with_it():
    """The two marks are independent; only the bar is in question."""
    frame = _frame(60)
    out = draw_cell_chrome(frame, bar_px=BAR_PX, label_text="A01",
                           label_corner="tl")
    assert (out != frame).any(), "the label must still be burned in"
    assert _drawn_length(frame, out) == 0


def test_a_refusal_is_recorded_against_the_cell_it_happened_to():
    ctx = _Ctx()
    cell = {"well": "B07", "chan": "GFP", "tp": "24h", "fov": "1"}
    kwargs = {"bar_px": BAR_PX, "bar_corner": "br", "bar_color": "white",
              "bar_thickness_px": 4, "label_text": ""}
    apply_cell_chrome(ctx, _frame(60), dict(kwargs), cell)
    assert scale_bar_refusal_report(ctx) == ["B07"]


def test_a_bar_that_fits_records_nothing():
    ctx = _Ctx()
    cell = {"well": "B07", "chan": "GFP", "tp": "24h", "fov": "1"}
    kwargs = {"bar_px": BAR_PX, "bar_corner": "br", "bar_color": "white",
              "bar_thickness_px": 4, "label_text": ""}
    apply_cell_chrome(ctx, _frame(512), dict(kwargs), cell)
    assert scale_bar_refusal_report(ctx) == []


def test_the_record_is_scoped_to_one_render_pass():
    """A refusal from the previous Generate must not be reported against this
    one — the same rule the three records beside it already follow."""
    ctx = _Ctx()
    cell = {"well": "B07", "chan": "GFP", "tp": "24h", "fov": "1"}
    apply_cell_chrome(ctx, _frame(60), {"bar_px": BAR_PX}, cell)
    assert scale_bar_refusal_report(ctx) == ["B07"]
    reset_scan_misses(ctx)
    assert scale_bar_refusal_report(ctx) == []


def test_a_cell_with_no_chrome_is_returned_untouched():
    ctx = _Ctx()
    frame = _frame(240)
    assert apply_cell_chrome(ctx, frame, None, {"well": "A01"}) is frame
    assert apply_cell_chrome(ctx, None, {"bar_px": 10}, {"well": "A01"}) is None
