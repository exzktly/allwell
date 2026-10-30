"""Confluence is calibrated against an empty start (``nanowell_measure`` MEASURE_VERSION 7).

Reported: a well whose nanowells held 47-58 cells each read 0-3 % confluent at every timepoint. Confluence is texture
against an empty nanowell: a template of what one looks like, and a threshold for how much texture the bare floor
shows by itself. Both were taken from the timepoint being measured, with no nanowell assumed empty — and on a plate
whose cells were loaded before its first image, every nanowell holds cells, so the template and the threshold are
the cells' own texture and almost nothing departs from them. Painted at 52 % true coverage, that read 1-2 %.

The fix: every timepoint is read against the well's earliest template and the threshold measured where it was built
(``nanowell_run._confluence_basis``), and the whole well's confluence is undefined unless that timepoint's counts
show its nanowells empty (``nanowell_run.require_empty_start``). These tests drive the run's own normalisation and
tile measurement over a painted brightfield scene: bright floor, dark rim and lattice, and cells whose interior is
about the floor's level with a thin dark edge, as the reported images show them.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from pipeline import nanowell_measure as nm
from pipeline import nanowell_run as nr
from pipeline.nanowell_detect import FITTED, SiteGeometry

PX, R, PITCH, N = 1.0, 40.0, 110.0, 5
FLOOR, RIM, LATTICE = 0.8, 0.3, 0.25
CENTRES = [(70 + PITCH * c, 70 + PITCH * r) for r in range(N) for c in range(N)]
SIZE = int(140 + PITCH * (N - 1))
GEOMS = {i: SiteGeometry(i, c, R, FITTED, 0.2, 64) for i, c in enumerate(CENTRES)}
TILE = "t0"
LOCATED = SimpleNamespace(pixel_size_um=PX, positions={TILE: (0.0, 0.0)})
BY_TILE = {TILE: list(GEOMS)}


def _paint(cells_per_site: int, *, seed: int = 0, gain: float = 1.0, offset: float = 0.0):
    """``(image, true coverage of each site's interior)``: brightfield cells, 4 px in radius, uniform over each
    nanowell's floor; *gain* and *offset* are the exposure, which changes between timepoints and must not matter."""
    from scipy import ndimage

    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    img = np.full((SIZE, SIZE), LATTICE)
    truth = np.zeros((SIZE, SIZE), bool)
    for x, y in CENTRES:
        r = np.hypot(xx + .5 - x, yy + .5 - y)
        img[r <= R] = FLOOR
        img[(r <= R) & (r > R - 3)] = RIM
    for x, y in CENTRES:
        for _ in range(cells_per_site):
            a, d = rng.uniform(0, 2 * np.pi), 0.85 * R * np.sqrt(rng.uniform())
            rr = np.hypot(xx + .5 - x - d * np.cos(a), yy + .5 - y - d * np.sin(a))
            truth |= rr <= 4.0
            img[rr <= 4.0] += 0.04
            img[(rr <= 4.0) & (rr > 2.8)] -= 0.18
    img = ndimage.gaussian_filter(img, 1.2) + np.random.default_rng(seed + 1).normal(0, 0.01, img.shape)
    coverage = [float(truth[nm._interior_pixels(img.shape, PX, g, nm.INTERIOR_FRACTION)].mean())
                for g in GEOMS.values()]
    return gain * img + offset, np.array(coverage)


def _template(image) -> nm.Template:
    half = nm.template_half(R / PX)
    crops = nm.site_crops(image, PX, CENTRES, half)
    return nm.build_template(nm.level_crops(crops, half), interior_radius_px=nm.SUPPRESSION_RADIUS * R / PX)


def _confluence(image, earliest):
    """The run's own path for one timepoint: its own template and levels, the confluence basis against *earliest*
    (``None``: this timepoint is the reference), and each site's confluence as the tile measurement computes it.
    Returns ``(confluence per site, the Measured the basis was recorded on)``."""
    images = {TILE: image}
    template = _template(image)
    norm = nr._normalise(LOCATED, images, template, BY_TILE, GEOMS, None, PITCH)
    out = nr.Measured("tp", template={"source": template.source, "_object": template})
    norm.confluence = nr._confluence_basis(LOCATED, images, template, earliest, norm, BY_TILE, GEOMS, PITCH, out)
    fit = [g for g in nr._local_fit(TILE, BY_TILE[TILE], LOCATED, GEOMS, template)[0] if g.index in norm.levels]
    rim = nm.rim_suppress(image, PX, template, fit, levels=norm.levels)
    got = nr._tile_confluence(TILE, BY_TILE[TILE], LOCATED, images, GEOMS, template, norm, fit, rim)
    return np.array([np.nan if got.get(i) is None else got[i] for i in GEOMS]), out


@pytest.fixture(scope="module")
def reference():
    """The empty start: the template this timepoint builds, carrying the threshold measured on it."""
    empty, _ = _paint(0)
    conf, out = _confluence(empty, None)
    return conf, out.template["_object"]


def test_the_empty_start_reads_empty_and_carries_its_threshold(reference):
    conf, template = reference
    assert np.all(conf <= 0.02)
    assert template.texture_cut is not None and template.texture_cut > 0


def test_a_loaded_timepoint_read_against_the_empty_start_reads_its_coverage(reference):
    """Every nanowell about half covered, at a different exposure from the empty start. Against itself (the code
    before the fix) it read 1-2 %; against the empty start it reads within the optical halo of the truth."""
    _, template = reference
    loaded, truth = _paint(60, seed=4, gain=1.3, offset=0.05)
    conf, _ = _confluence(loaded, template)
    assert truth.mean() > 0.45
    assert np.all(np.isfinite(conf))
    assert np.all(conf >= 0.8 * truth) and np.all(conf <= np.minimum(1.0, 1.6 * truth))
    self_read, _ = _confluence(loaded, None)
    assert self_read.mean() < 0.1                 # the defect, measured: calibrated on itself it reads near zero


def test_coverage_that_grows_reads_as_growing(reference):
    _, template = reference
    means = [_confluence(_paint(n, seed=7 + n)[0], template)[0].mean() for n in (10, 30, 60)]
    assert means[0] < means[1] < means[2]


def test_a_reference_with_no_threshold_gives_no_confluence(reference):
    _, template = reference
    loaded, _ = _paint(30, seed=2)
    conf, out = _confluence(loaded, replace(template, texture_cut=None))
    assert np.all(np.isnan(conf))
    assert any("no texture threshold" in n for n in out.notes)


# ── require_empty_start ─────────────────────────────────────────────────────
def _rows(per_tp: dict) -> list:
    """Site rows: *per_tp* maps a timepoint to a list of n_cells (``None``: not counted)."""
    return [{"timepoint": tp, "state": nm.MEASURED if n else nm.EMPTY, "n_cells": n, "confluence": 0.25}
            for tp, counts in per_tp.items() for n in counts]


def _blocks(*tps):
    return {tp: {"status": nr.OK, "notes": []} for tp in tps}


def test_a_loaded_start_makes_the_whole_well_s_confluence_undefined():
    """The reported well: 58 cells at its first timepoint."""
    sites = _rows({"0h": [58, 55, 47, 0], "12h": [60, 58, 50, 3]})
    blocks = _blocks("0h", "12h")
    verdict = nr.require_empty_start(sites, blocks, "0h")
    assert verdict["status"] == "loaded" and verdict["loaded_share"] == 0.75
    assert [r["confluence"] for r in sites] == [None] * 8              # undefined, never 0
    for block in blocks.values():
        assert block["confluence_reference"]["status"] == "loaded"
        assert sum("Confluence is undefined" in n and "75%" in n for n in block["notes"]) == 1


def test_an_empty_start_keeps_the_confluence():
    counts = [0] * 39 + [1]                                             # one nanowell of 40 with debris: 2.5 %
    sites = _rows({"0h": counts, "12h": [5] * 40})
    blocks = _blocks("0h", "12h")
    verdict = nr.require_empty_start(sites, blocks, "0h")
    assert verdict["status"] == "empty"
    assert all(r["confluence"] == 0.25 for r in sites)
    assert all(not b["notes"] for b in blocks.values())


def test_just_over_the_limit_is_loaded():
    sites = _rows({"0h": [0] * 18 + [1, 1]})                           # 10 %
    assert nr.require_empty_start(sites, _blocks("0h"), "0h")["status"] == "loaded"


def test_an_uncounted_start_cannot_be_shown_empty():
    sites = _rows({"0h": [None] * 4, "12h": [3] * 4})
    blocks = _blocks("0h", "12h")
    assert nr.require_empty_start(sites, blocks, "0h")["status"] == "unverified"
    assert all(r["confluence"] is None for r in sites)


def test_no_reference_is_no_confluence():
    sites = _rows({"12h": [3] * 4})
    assert nr.require_empty_start(sites, _blocks("12h"), None)["status"] == "none"
    assert all(r["confluence"] is None for r in sites)


def test_the_verdict_is_judged_on_the_reference_not_on_the_first_row():
    """The reference is the timepoint the template came from; an earlier timepoint with no template (none of its
    nanowells visible) has no say."""
    sites = _rows({"0h": [9, 9], "6h": [0, 0, 0, 0]})
    assert nr.require_empty_start(sites, _blocks("0h", "6h"), "6h")["status"] == "empty"


def test_running_it_again_changes_nothing():
    """An incremental run passes the kept rows and blocks through it again."""
    sites = _rows({"0h": [5, 5], "12h": [6, 6]})
    blocks = _blocks("0h", "12h")
    first = nr.require_empty_start(sites, blocks, "0h")
    notes = [list(b["notes"]) for b in blocks.values()]
    assert nr.require_empty_start(sites, blocks, "0h") == first
    assert [b["notes"] for b in blocks.values()] == notes
