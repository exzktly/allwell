"""The montage of ranked nanowells over time (``renderer.render_site_rows``) draws what Extremes drew before it was
generalised, and the Distribution window's rows are ordered by the rule the window states.

Asked for: the Distribution window's pop-out draws its nanowells as rows of crops over time, the layout Extremes
already has. Extremes' renderer was generalised so both draw through one function; its output must not move.
The Extremes cases pin the figure's *structure* — every text with its gid, every axes' place in inches, every
image's pixels — hashed, because a pixel hash of the rendered canvas would change with the machine's fonts.
"""

from __future__ import annotations

import hashlib
import zlib

import numpy as np
from matplotlib.figure import Figure

from review.features.nanowell import renderer
from review.features.nanowell.extremes_compute import ExtremeRow, ExtremesModel
from review.features.nanowell.image_compute import Crop

TPS = ["T0", "T1", "T2"]


def _crop(seed: int) -> Crop:
    rng = np.random.default_rng(seed)
    return Crop(rng.integers(0, 4000, (24, 24)).astype(float), "tile", "an owner tile")


def _extremes_fixture():
    def row(site, rank, value):
        return ExtremeRow(site, rank, value, {tp: value + k for k, tp in enumerate(TPS)})

    model = ExtremesModel(well="A01", timepoint="T2", column="count_fc", n=2, timepoints=TPS,
                          largest=[row(("A01", 1, 0), 1, 4.0), row(("A01", 2, -1), 2, 3.0)],
                          smallest=[row(("A01", 0, 0), 1, 0.5), row(("A01", -1, 2), 2, 0.75)],
                          roster=9, ranked=8, excluded={"occluded": 1})
    crops = {r.site: {tp: _crop(zlib.crc32(repr((r.site, tp)).encode())) for tp in TPS}
             for r in model.largest + model.smallest}
    crops[("A01", 0, 0)]["T1"] = Crop(None, "tile", "no image of EGFP in archive")
    return model, crops


def _structure(fig) -> str:
    """Everything the figure was told to draw, in a form that does not depend on the machine's fonts."""
    parts = []
    for text in fig.texts + [t for ax in fig.axes for t in ax.texts]:
        parts.append(("text", text.get_gid(), text.get_text(), tuple(np.round(text.get_position(), 4))))
    for ax in fig.axes:
        parts.append(("axes", tuple(np.round(ax.get_position().bounds, 4))))
        for image in ax.images:
            parts.append(("image", image.get_gid(), hashlib.sha256(np.asarray(image.get_array()).tobytes()).hexdigest(),
                          tuple(image.get_clim())))
        for spine in ax.spines.values():
            parts.append(("spine", spine.get_linewidth()))
    return hashlib.sha256(repr(sorted(parts, key=repr)).encode()).hexdigest()


def test_extremes_is_drawn_exactly_as_it_was_before_the_montage_was_generalised():
    model, crops = _extremes_fixture()
    fig = Figure(figsize=(12.0, 7.0))
    hits = renderer.render_extremes(fig, model, crops, label="Count fold change", note="A note.")
    assert len(hits) == 4 * 3 and set(hits.values()) == {r.site for r in model.largest + model.smallest}
    assert _structure(fig) == EXPECTED_STRUCTURE


EXPECTED_STRUCTURE = "de680e6f31c5f48d56950d8873e16b09b7b39d4e076ab020e5126caf0c219f04"


def _row_labels(fig) -> list:
    return [t.get_text().split("\n")[0] for t in fig.texts if t.get_gid() == "nw-extremes-row"]


def test_rows_from_several_wells_name_their_well_and_extremes_rows_do_not():
    """A window's rows come from any number of wells, so a row's label must say which; Extremes is one well, whose
    name is in its title. Built so that two rows share an address in different wells: without the well they would
    read the same."""
    model, crops = _extremes_fixture()
    rows = [ExtremeRow(("A01", 1, 0), 1, 4.0, {}), ExtremeRow(("B02", 1, 0), 2, 3.0, {})]
    crops = {r.site: {tp: _crop(1) for tp in TPS} for r in rows}
    fig = Figure(figsize=(12.0, 7.0))
    renderer.render_site_rows(fig, model, [("Window", rows)], crops, label="Count fold change", empty="", title="t",
                              head="h", with_well=True)
    assert _row_labels(fig) == ["#1  A01 q 1, r 0", "#2  B02 q 1, r 0"]
    fig = Figure(figsize=(12.0, 7.0))
    renderer.render_extremes(fig, _extremes_fixture()[0], _extremes_fixture()[1], label="Count fold change")
    assert _row_labels(fig) == ["#1  q 1, r 0", "#2  q 2, r -1", "#1  q 0, r 0", "#2  q -1, r 2"]


def test_a_window_s_rows_write_a_cell_count_as_a_number_and_a_fold_change_with_its_times_sign():
    """Extremes' rows are fold changes and write ``3×``; the window's rows are any metric, and a cell count of 3
    is not ``3×``."""
    from review.features.nanowell.window_compute import WindowRow, WindowRows

    rows = [WindowRow(("A01", 1, 0), 1, 3.0, {"T0": 2.0, "T1": 3.0})]
    crops = {("A01", 1, 0): {tp: _crop(1) for tp in ("T0", "T1")}}

    def drawn(column):
        fig = Figure(figsize=(8.0, 5.0))
        renderer.render_site_rows(fig, WindowRows("T1", column, ["T0", "T1"]), [("Window", rows)], crops,
                                  label="x", empty="", title="t", head="h", with_well=True)
        return [t.get_text() for t in fig.texts + [t for ax in fig.axes for t in ax.texts]
                if t.get_gid() in ("nw-extremes-row", "nw-extremes-value")]

    assert drawn("n") == ["#1  A01 q 1, r 0\n3", "2", "3"]
    assert drawn("count_fc") == ["#1  A01 q 1, r 0\n3×", "2×", "3×"]
    assert drawn("brightness_fc_GFP")[1] == "2×" and drawn("log2_count_fc")[1] == "+2"


def test_on_a_phone_s_figure_the_caption_stays_clear_of_the_lowest_crops():
    """The caption is written after the crops (it reports what they found) and wraps to the figure's width, while the
    room under the montage was a fixed 0.45 in. On a phone's narrow figure the caption is five or six lines, and with
    a well excluded on the page ("Excluded on this page: A01 (restore from Excluded on the band).") it ran over the
    lowest crops and the metrics along their foot. The montage now leaves the room the caption needs."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    model, crops = _extremes_fixture()
    fig = Figure(figsize=(4.1, 7.6))                      # the Extremes figure on a 432 x 936 phone, held upright
    FigureCanvasAgg(fig)
    note = ("Excluded on this page: A01 (restore from Excluded on the band). Focus unchecked: 15 under 10 rims in "
            "the well.")
    # Two timepoints, as the phone showed: the crops are then as tall as the height allows, not held small by width.
    renderer.render_extremes(fig, model, crops, label="Count fold change", note=note, timepoints=TPS[1:])
    fig.canvas.draw()
    renderer_ = fig.canvas.get_renderer()
    (caption,) = [t for t in fig.texts if t.get_gid() == "nw-extremes-caption"]
    # The lines as drawn: matplotlib measures a ``wrap=True`` text unwrapped, so its extent cannot be read off it.
    lines = (caption._get_wrapped_text() if caption.get_wrap() else caption.get_text()).count("\n") + 1
    top = (caption.get_position()[1] * fig.bbox.height
           + lines * caption.get_fontsize() * caption._linespacing * fig.dpi / 72)
    lowest = min(ax.get_window_extent(renderer_).y0 for ax in fig.axes)
    assert top <= lowest, f"the caption's top ({top:.0f} px) is over the lowest crops' foot ({lowest:.0f} px)"
