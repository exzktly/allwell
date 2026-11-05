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
