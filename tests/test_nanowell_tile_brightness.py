"""Tiles of different brightness and offset, and one nanowell's misread surround, measure alike (MEASURE_VERSION 5).

Reported: 1,472 nanowells found and 17 with a confluence value. Rendered here as a 3x3-tile well whose tiles differ
by a gain step and an additive offset, as real tiles do: before, the empty nanowells — chosen as the least textured on
raw intensity — gathered on the dimmest tiles, each tile took its texture threshold from its own empty nanowells,
and every nanowell on the others had no confluence. Now every seen nanowell has one, and every number agrees with the
same well imaged with no difference between tiles.
"""

from __future__ import annotations

import functools

import numpy as np
import pytest

from pipeline import nanowell_measure as nm
from pipeline import nanowell_run as nr
from tests._nanowell_run_fixture import build_well, paint_cell, threshold_segmenter

SETTINGS = nr.NanowellSettings("100um")


@pytest.fixture(scope="module")
def well():
    rendered, record = build_well(rows=3, cols=3, timepoint="0h")
    truth = dict(zip(rendered.array.addresses, rendered.sites_um))
    loaded = list(truth)[::7][:30]
    for address in loaded:
        paint_cell(rendered, truth[address], dx_um=3.0)
    base = {k: np.asarray(v, dtype=np.float32) for k, v in rendered.images.items()}
    return base, record, loaded


def _measure(base, record, gain_step=0.0, offset_step=0.0):
    """Tile k is seen through gain ``1 + gain_step * (k % 3)`` and offset ``offset_step * (k % 2)``."""
    images = {f: image * (1 + gain_step * (k % 3)) + offset_step * (k % 2)
              for k, (f, image) in enumerate(sorted(base.items()))}
    located = nr.locate_timepoint("0h", record, "A01", images, SETTINGS)
    radius, roster = nr.own_array_model(located, SETTINGS)
    measured = nr.measure_timepoint(located, images, SETTINGS, radius, roster=roster, segmenter=threshold_segmenter())
    return {(r["nw_q"], r["nw_r"]): r for r in measured.sites}, measured


@pytest.fixture(scope="module")
def even(well):
    base, record, _ = well
    return _measure(base, record)


@pytest.mark.parametrize("gain_step, offset_step", [(0.15, 0.0), (0.15, 0.3), (0.3, 0.5)])
def test_every_seen_nanowell_has_a_confluence_and_it_does_not_depend_on_the_tile(well, even, gain_step, offset_step):
    base, record, _ = well
    reference, _ = even
    sites, measured = _measure(base, record, gain_step, offset_step)
    seen = [k for k, r in sites.items() if r["state"] in ("measured", "empty")]
    assert len(seen) > 500
    assert [k for k in seen if sites[k]["confluence"] is None] == []
    assert max(abs(sites[k]["confluence"] - reference[k]["confluence"]) for k in seen) < 0.03
    assert {k: (sites[k]["state"], sites[k]["n_cells"]) for k in seen} == \
        {k: (reference[k]["state"], reference[k]["n_cells"]) for k in seen}
    block = measured.template["normalisation"]
    assert block["measure_version"] == nm.MEASURE_VERSION and block["offset"] == "per nanowell" and block["tiles_without_gain"] == []


def test_empty_nanowells_read_empty_and_loaded_ones_textured_on_every_tile(well):
    base, record, loaded = well
    sites, _ = _measure(base, record, 0.3, 0.5)
    by_tile_empty = {}
    for (q, r), row in sites.items():
        if row["state"] == "empty" and (q, r) != (0, 0):                # the central square is not round: not a well
            by_tile_empty.setdefault(row["owner"], []).append(row["confluence"])
    assert len(by_tile_empty) == 9                                      # every tile has empty nanowells measured
    # One empty nanowell of about 500, (-12, 10), reads 0.14 here and under even light alike: its fitted radius is
    # 0.8 um (0.19 px) over the template's, so its rim does not quite divide out, and the texture threshold is now the
    # bare floor's own over every nanowell (0.076), where the least-textured quarter's noisier template set it at
    # 0.102 and hid the mismatch (tracker L7).
    readings = [c for v in by_tile_empty.values() for c in v]
    assert max(readings) < 0.15 and sum(c >= 0.1 for c in readings) <= 0.01 * len(readings)
    assert min(sites[a]["confluence"] for a in loaded if a in sites and sites[a]["state"] == "measured") > 0.1


def test_light_added_toward_the_middle_of_the_well_is_measured_as_if_it_were_not_there(well, even):
    """The report behind tracker L1: the middle of the well is brighter and undercounts there. Rendered here as light
    added in a broad peak at the array's centre, half the floor's brightness at its top, so it changes across every
    tile rather than from tile to tile. One offset per tile could not follow it; each nanowell's own level does, and
    every seen nanowell's state, count and confluence match the evenly lit well's."""
    from tests._nanowell_run_fixture import PX

    base, record, _ = well
    _, measured_even = even
    centre = np.mean([[r["well_x_um"], r["well_y_um"]] for r in measured_even.sites], axis=0)
    images = {}
    for name, image in base.items():
        left, top = record_left_top(record, name)
        yy, xx = np.mgrid[0:image.shape[0], 0:image.shape[1]]
        distance = np.hypot((xx + 0.5) * PX + left - centre[0], (yy + 0.5) * PX + top - centre[1])
        images[name] = (image + 0.5 * np.exp(-(distance / 900.0) ** 2)).astype(np.float32)
    located = nr.locate_timepoint("0h", record, "A01", images, SETTINGS)
    radius, roster = nr.own_array_model(located, SETTINGS)
    measured = nr.measure_timepoint(located, images, SETTINGS, radius, roster=roster, segmenter=threshold_segmenter())
    sites = {(r["nw_q"], r["nw_r"]): r for r in measured.sites}
    reference, _ = even
    seen = [k for k, r in sites.items() if r["state"] in ("measured", "empty") and k in reference]
    assert len(seen) > 500
    assert {k: (sites[k]["state"], sites[k]["n_cells"]) for k in seen} == \
        {k: (reference[k]["state"], reference[k]["n_cells"]) for k in seen}
    # The added light also slopes across each nanowell — by about 0.05 of the floor over one near the peak — which a
    # nanowell's one offset and gain cannot take out: 8 of 583 empty nanowells there read 0.035 to 0.048 where the
    # evenly lit well reads under 0.015, and every other nanowell matches (tracker L6).
    differences = [abs(sites[k]["confluence"] - reference[k]["confluence"]) for k in seen]
    assert max(differences) < 0.05 and sum(d > 0.03 for d in differences) <= 0.02 * len(differences)
    low, high = measured.template["normalisation"]["offset_range"]
    assert high - low > 0.2                                    # the block says light was added, and how much


def record_left_top(record, field):
    """A tile's recorded top-left corner in micrometres."""
    px = next(iter(record["timepoints"].values()))["pixel_size_um"]["x"]
    tile = next(t for t in record["tiles"] if t["field"] == field)
    return tile["mosaic_xy_px"][0] * px, tile["mosaic_xy_px"][1] * px


def test_the_run_counts_with_the_model_its_settings_name(well, monkeypatch):
    """Tracker L2, on the run's own path: with no segmenter passed (as the task passes none), what reaches
    ``pipeline.cellpose._segment_cellpose`` is the setting's model. Test detection builds its own segmenter and is
    pinned in ``test_nanowell_preview.py``."""
    from pipeline import cellpose

    asked = []

    def fake(image, *, model_type="cyto3", **_):
        asked.append(model_type)
        return np.zeros(np.shape(image), dtype=np.int32)

    monkeypatch.setattr(cellpose, "_segment_cellpose", fake)
    base, record, _ = well
    settings = nr.NanowellSettings("100um", cellpose_model="livecell_cp3")
    located = nr.locate_timepoint("0h", record, "A01", base, settings)
    radius, roster = nr.own_array_model(located, settings)
    nr.measure_timepoint(located, base, settings, radius, roster=roster)
    assert asked and set(asked) == {"livecell_cp3"}



#: The misread-surround cases render at 2 um per pixel, where a 10 um cell is five pixels across (at the fixture's
#: 4.3 um a cell is two pixels and twenty of them do not stay apart in a 100 um nanowell).
FINE_PX = 2.0


def _fine_well():
    """A fresh copy of the misread-surround cases' well: rendered once (12 s), copied for each case, which draws
    its cells and its surround into its own copy."""
    import copy

    return copy.deepcopy(_rendered_fine_well())


@functools.cache
def _rendered_fine_well():
    from tests._nanowell_run_fixture import DIAMETER, PITCH, record_for
    from tests._nanowell_synth import TilePlan, render_tiles

    tile = int(round(700 * 4.3 / FINE_PX))
    plan = TilePlan(2, 2, (tile, tile), 0.1, FINE_PX)
    extent = (plan.step_um[0] + plan.extent_um[0], plan.step_um[1] + plan.extent_um[1])
    rendered = render_tiles(plan, pitch_um=PITCH, diameter_um=DIAMETER, radius_um=0.26 * min(extent),
                            rotation_deg=7.0, noise_sigma=0.05, blur_px=1.0 * 4.3 / FINE_PX, seed=1)
    return rendered, record_for(rendered)


def _disk_add(rendered, centre_um, outer_um, amount, inner_um=-1.0):
    """Add *amount* between *inner_um* and *outer_um* of *centre_um*, on every tile that sees it.

    Only the pixels of the disk's bounding box are measured: every pixel outside it is farther than *outer_um*.
    Over the whole tile, the forty-one disks of one case took 13.6 s of a test with a 90 s budget.
    """
    for name, image in rendered.images.items():
        left, top = rendered.true_left_top_um[name]
        x0 = max(0, int(np.floor((centre_um[0] - outer_um - left) / FINE_PX - 0.5)))
        x1 = min(image.shape[1], int(np.ceil((centre_um[0] + outer_um - left) / FINE_PX + 0.5)) + 1)
        y0 = max(0, int(np.floor((centre_um[1] - outer_um - top) / FINE_PX - 0.5)))
        y1 = min(image.shape[0], int(np.ceil((centre_um[1] + outer_um - top) / FINE_PX + 0.5)) + 1)
        if x0 >= x1 or y0 >= y1:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        r = np.hypot((xx + 0.5) * FINE_PX + left - centre_um[0], (yy + 0.5) * FINE_PX + top - centre_um[1])
        image[y0:y1, x0:x1][(r > inner_um) & (r <= outer_um)] += amount


def _loaded_pair(surround=0.0):
    """Two neighbouring nanowells holding the same twenty well-separated 10 um cells, the first with its surround
    (the lattice ring 62-100 um from its centre) read *surround* brighter, as debris or a halo there would."""
    rendered, record = _fine_well()
    truth = dict(zip(rendered.array.addresses, rendered.sites_um))
    rng = np.random.default_rng(3)
    cells = []
    while len(cells) < 20:
        p = rng.uniform(-38, 38, 2)
        if np.hypot(*p) < 38 and all(np.hypot(*(p - q)) > 14 for q in cells):
            cells.append(p)
    pair = [(1, 2), (2, 2)]
    for address in pair:
        for p in cells:
            _disk_add(rendered, np.asarray(truth[address]) + p, 5.0, 0.5)
    if surround:
        _disk_add(rendered, truth[pair[0]], 100.0, surround, inner_um=62.0)
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    measured = nr.measure_timepoint(located, rendered.images, SETTINGS, located.boundary["radius_um"],
                                    segmenter=threshold_segmenter(level=1.2, smooth=1.0, min_px=6))
    rows = {(r["nw_q"], r["nw_r"]): r for r in measured.sites}
    return [rows[a] for a in pair]

@pytest.fixture(scope="module")
def clean_pair():
    return _loaded_pair()


@pytest.mark.parametrize("surround", [0.12, -0.08])
def test_one_nanowell_s_misread_surround_does_not_move_its_floor_or_lose_its_cells(clean_pair, surround):
    """Reported: neighbouring nanowells with similar cells and brightness get wildly different counts, down to
    twenty visible cells counted as zero. A nanowell's offset came from its own surround alone, so one misread ring
    moved that nanowell's floor and nothing else: +0.12 put it below the flat level between sites (segmenter floor
    -0.11 against its neighbour's 0.75) and -0.08 lost 6 of its 20 cells even to this stand-in segmenter. Its offset
    is now its neighbours' median, as its gain already was."""
    clean, _ = clean_pair
    misread, neighbour = _loaded_pair(surround)
    assert clean["n_cells"] == neighbour["n_cells"] == 20                      # the fixture's premise
    assert misread["n_cells"] == 20
    assert misread["level_offset"] == pytest.approx(clean["level_offset"], abs=0.01)
    assert misread["segmenter_floor"] == pytest.approx(clean["segmenter_floor"], abs=0.1)
