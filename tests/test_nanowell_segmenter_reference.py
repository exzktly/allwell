"""The rim-removed segmenter input is divided by the empty reference well's template, and the reference's false-count
baseline is recorded beside every well's counts (``nanowell_measure`` MEASURE_VERSION 8).

Why: rim removal divides each nanowell by a picture of an empty one. The well's own picture is built from its own
nanowells, and on a loaded plate every one of them holds cells — the reported plate held 47-58 a nanowell — so the
picture carries the cells' texture and dividing by it takes part of the cells away with the rim. Confluence was
already read against the empty reference well for that reason (``test_nanowell_confluence_reference.py``); the count
was not. Now the run hands the segmenter the same reference-divided image confluence is read on
(``nanowell_run._segmenter_rim``), wherever the reference covers every nanowell of the tile.

The scene is the confluence tests' painted brightfield one (bright floor, dark rim and lattice, cells with a dark
edge), the empty well and the loaded one painted at different exposures. Measured on it: the edges of the cells stand
out from the floor's noise by 6.78 sigma on the reference-divided image against 2.90 on the own-divided one at 60
cells a nanowell, 5.73 against 5.11 at 30, and alike (4.70 and 4.73) at 10 — the own picture goes wrong only once
cells, not bare floor, are what most nanowells show at most pixels, which is the loaded plate.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from scipy import ndimage

from pipeline import nanowell_preview as npv
from pipeline import nanowell_reference as nref
from pipeline import nanowell_run as nr
from tests import test_nanowell_confluence_reference as scene

T = scene
RIM = nr.NanowellSettings("100um", segment_on="rim", segment_scope="tile", segment_scale="tile",
                          diameter_override_um=2 * T.R)
RAW = replace(RIM, segment_on="raw")


def _paint(cells_per_site: int, seed: int, gain: float = 1.0, offset: float = 0.0):
    """``(image, the cells' dark-edge pixels)``: the confluence scene's painting, keeping where the edges are."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:T.SIZE, 0:T.SIZE]
    img = np.full((T.SIZE, T.SIZE), T.LATTICE)
    edge = np.zeros(img.shape, bool)
    for x, y in T.CENTRES:
        r = np.hypot(xx + .5 - x, yy + .5 - y)
        img[r <= T.R] = T.FLOOR
        img[(r <= T.R) & (r > T.R - 3)] = T.RIM
    for x, y in T.CENTRES:
        for _ in range(cells_per_site):
            a, d = rng.uniform(0, 2 * np.pi), 0.85 * T.R * np.sqrt(rng.uniform())
            rr = np.hypot(xx + .5 - x - d * np.cos(a), yy + .5 - y - d * np.sin(a))
            img[rr <= 4.0] += 0.04
            img[(rr <= 4.0) & (rr > 2.8)] -= 0.18
            edge |= (rr <= 4.0) & (rr > 2.8)
    img = ndimage.gaussian_filter(img, 1.2) + np.random.default_rng(seed + 1).normal(0, 0.01, img.shape)
    return gain * img + offset, edge


def _norm(image, reference):
    """The run's own normalisation and confluence basis for one timepoint of *image* against *reference*."""
    images = {T.TILE: image}
    template = T._template(image)
    norm = nr._normalise(T.LOCATED, images, template, T.BY_TILE, T.GEOMS, None, T.PITCH)
    out = nr.Measured("0h", template={"source": template.source, "_object": template})
    norm.confluence = nr._confluence_basis(T.LOCATED, images, template, reference, norm, T.BY_TILE, T.GEOMS, T.PITCH,
                                           out)
    return images, template, norm, out


def _given(image, reference, settings=RIM, norm_edit=None):
    """``(the image the segmenter was handed, the tile's contents, its notes, the template it was divided by)``,
    through the run's own ``_tile_contents`` with a segmenter that records its input and finds nothing."""
    images, template, norm, _out = _norm(image, reference)
    if norm_edit is not None:
        norm_edit(norm)
    seen = {}

    def spy(img, **_kw):
        seen["image"] = np.array(img, dtype=float)
        return np.zeros(np.shape(img), dtype=int)

    got, _table, notes = nr._tile_contents(T.TILE, T.BY_TILE[T.TILE], T.LOCATED, images, settings, T.GEOMS,
                                           [None] * len(T.GEOMS), template, spy, None, None, norm)
    return seen["image"], got, notes, template, norm


@pytest.fixture(scope="module")
def reference():
    """The empty reference well read against itself, as the run saves it: its template carrying its threshold."""
    empty, _ = _paint(0, 0)
    _images, _template, _norm_, out = _norm(empty, nref.OWN)
    return nref.Reference(templates={"0h": out.template["_object"]}, false_cells={"0h": 0.04})


@pytest.fixture(scope="module")
def loaded():
    """Sixty cells a nanowell at another exposure, as a loaded plate well is imaged beside the empty one."""
    return _paint(60, seed=70, gain=1.3, offset=0.05)


def test_the_segmenter_is_given_the_tile_divided_by_the_reference_template(reference, loaded):
    """The exact image: the tile, each nanowell at the reference's levels, divided by the reference's template —
    not the well's own, which on this scene is a different array (asserted, so the comparison can tell them apart)."""
    image, _edge = loaded
    given, got, _notes, template, norm = _given(image, reference)
    calibration, levels, _threshold = norm.confluence
    sites = [g for g in nr._local_fit(T.TILE, T.BY_TILE[T.TILE], T.LOCATED, T.GEOMS, calibration)[0]
             if g.index in levels]
    from pipeline import nanowell_measure as nm

    expected = nm.rim_suppress(image, T.PX, calibration, sites, levels=levels)
    own = nm.rim_suppress(image, T.PX, template, sites, levels=norm.levels)
    assert not np.allclose(expected, own, atol=1e-3)
    np.testing.assert_allclose(given, expected)
    assert {c["seg_template"] for c in got.values()} == {nr.SEGMENT_TEMPLATE_REFERENCE}


def test_with_no_reference_template_the_well_s_own_divides_it_and_says_so(loaded):
    image, _edge = loaded
    given, got, _notes, template, norm = _given(image, nref.Reference(default="none here"))
    from pipeline import nanowell_measure as nm

    fit = [g for g in nr._local_fit(T.TILE, T.BY_TILE[T.TILE], T.LOCATED, T.GEOMS, template)[0]
           if g.index in norm.levels]
    np.testing.assert_allclose(given, nm.rim_suppress(image, T.PX, template, fit, levels=norm.levels))
    assert {c["seg_template"] for c in got.values()} == {nr.SEGMENT_TEMPLATE_OWN}
    block = nr._segmenter_template_block(got)
    assert block == {"source": nr.SEGMENT_TEMPLATE_OWN, "nanowells_on_reference": 0,
                     "nanowells_on_own": len(T.GEOMS)}
    assert nr._segmenter_template_block({})["source"] is None                    # nothing counted: neither
    mixed = {0: {"seg_template": nr.SEGMENT_TEMPLATE_REFERENCE}, 1: {"seg_template": nr.SEGMENT_TEMPLATE_OWN}}
    assert nr._segmenter_template_block(mixed) == {"source": "mixed", "nanowells_on_reference": 1,
                                                   "nanowells_on_own": 1}


def test_a_tile_the_reference_does_not_fully_cover_keeps_its_own_template_and_notes_why(reference, loaded):
    """A nanowell with no level against the reference would be flat in the reference image and counted empty, so the
    tile keeps its own template — named in the notes — rather than reporting that nanowell as holding nothing."""
    image, _edge = loaded

    def drop_one(norm):
        calibration, levels, threshold = norm.confluence
        norm.confluence = (calibration, {i: v for i, v in levels.items() if i != 0}, threshold)

    _given_image, got, notes, _template, _norm_ = _given(image, reference, norm_edit=drop_one)
    assert {c["seg_template"] for c in got.values()} == {nr.SEGMENT_TEMPLATE_OWN}
    assert any("has no level for 1 of the tile's nanowells" in n for n in notes)


def test_the_raw_input_is_untouched_by_the_reference(reference, loaded):
    image, _edge = loaded
    given, got, _notes, _template, _norm_ = _given(image, reference, settings=RAW)
    np.testing.assert_allclose(given, image)
    assert {c["seg_template"] for c in got.values()} == {None}


def test_cells_stand_out_more_on_the_reference_divided_image(reference, loaded):
    """The point of the change, measured: the cells' dark edges against the floor's noise, on each image the
    segmenter could be given. Own-divided 2.90 sigma, reference-divided 6.78, on this scene."""
    image, edge = loaded

    def contrast(given):
        yy, xx = np.mgrid[0:T.SIZE, 0:T.SIZE]
        interior = np.zeros(given.shape, bool)
        for x, y in T.CENTRES:
            interior |= np.hypot(xx + .5 - x, yy + .5 - y) <= 0.8 * T.R
        floor = interior & ~ndimage.binary_dilation(edge, iterations=3)
        return float(np.abs(given[edge & interior] - 1).mean() / given[floor].std())

    after = contrast(_given(image, reference)[0])
    before = contrast(_given(image, nref.Reference(default="none here"))[0])
    assert after == pytest.approx(6.78, abs=0.05) and before == pytest.approx(2.90, abs=0.05)


def test_the_reference_well_s_verdict_carries_its_false_count_per_nanowell():
    rows = [{"state": "empty", "n_cells": 0}, {"state": "empty", "n_cells": 0}, {"state": "measured", "n_cells": 1},
            {"state": "measured", "n_cells": 2}, {"state": "occluded", "n_cells": None}]
    assert nref.verdict(rows)["false_cells_per_nanowell"] == pytest.approx(0.75)
    assert nref.verdict([{"state": "occluded", "n_cells": None}])["false_cells_per_nanowell"] is None  # not 0


def test_the_saved_false_count_reaches_every_other_well_s_record(tmp_path, reference):
    """Saved with the reference, loaded by the next well, and put on that well's timepoint template block, which is
    what its grid record holds."""
    from pipeline import nanowell_task as nt

    settings = nr.NanowellSettings("100um", empty_well="H12")
    root = tmp_path / nt.NANOWELL_DIR
    root.mkdir()
    found = {**nref.verdict([{"state": "empty", "n_cells": 0}] * 24 + [{"state": "measured", "n_cells": 1}]),
             "status": nref.EMPTY}
    nref.save(tmp_path, "H12", settings, {"0h": (reference.templates["0h"], found)})
    loaded_ref = nref.load(tmp_path, settings)
    assert loaded_ref.false_cells == {"0h": pytest.approx(1 / 25)}
    image, _edge = _paint(10, seed=5)
    _images, _template, _norm_, out = _norm(image, loaded_ref)
    assert out.template.get("reference_false_cells_per_nanowell") == pytest.approx(1 / 25)


def test_test_detection_says_its_crops_are_not_divided_by_the_reference():
    named = nr.NanowellSettings("100um", segment_on="rim", empty_well="H12")
    assert "H12" in npv.reference_input_note(named, "A01")
    assert npv.reference_input_note(named, "H12") is None                       # the reference reads itself
    assert npv.reference_input_note(replace(named, segment_on="raw"), "A01") is None
    assert npv.reference_input_note(replace(named, empty_well=None), "A01") is None
