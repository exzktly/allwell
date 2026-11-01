"""Confluence is read against a named empty reference well (``nanowell_measure`` MEASURE_VERSION 7).

Reported: a well whose nanowells held 47-58 cells each read 0-3 % confluent at every timepoint. Confluence is texture
against an empty nanowell: a template of what one looks like, and a threshold for how much texture the bare floor
shows by itself. Both were taken from the well being measured, with no nanowell assumed empty, and on a plate whose
cells were loaded before its first image every nanowell holds cells, so the template and the threshold are the
cells' own texture and almost nothing departs from them. Painted at 52 % true coverage, that read 1-2 %.

The fix: every well is read against the template and threshold of a well the run is told is empty, at the same
timepoint (``nanowell_run._confluence_basis``, ``pipeline.nanowell_reference``). These tests drive the run's own
normalisation and tile measurement over a painted brightfield scene: bright floor, dark rim and lattice, and cells
whose interior is about the floor's level with a thin dark edge, as the reported images show them. The empty well
and the loaded one are painted separately, at different exposures, as two plate wells are imaged.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from pipeline import nanowell_measure as nm
from pipeline import nanowell_reference as nref
from pipeline import nanowell_run as nr
from pipeline.nanowell_detect import FITTED, SiteGeometry

PX, R, PITCH, N = 1.0, 40.0, 110.0, 5
FLOOR, RIM, LATTICE = 0.8, 0.3, 0.25
CENTRES = [(70 + PITCH * c, 70 + PITCH * r) for r in range(N) for c in range(N)]
SIZE = int(140 + PITCH * (N - 1))
GEOMS = {i: SiteGeometry(i, c, R, FITTED, 0.2, 64) for i, c in enumerate(CENTRES)}
TILE = "t0"
LOCATED = SimpleNamespace(pixel_size_um=PX, positions={TILE: (0.0, 0.0)}, timepoint="0h")
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


def _confluence(image, reference):
    """The run's own path for one timepoint: its own template and levels, the confluence basis against *reference*
    (a ``nanowell_reference.Reference``), and each site's confluence as the tile measurement computes it.
    Returns ``(confluence per site, the Measured the basis was recorded on)``."""
    images = {TILE: image}
    template = _template(image)
    norm = nr._normalise(LOCATED, images, template, BY_TILE, GEOMS, None, PITCH)
    out = nr.Measured("tp", template={"source": template.source, "_object": template})
    norm.confluence = nr._confluence_basis(LOCATED, images, template, reference, norm, BY_TILE, GEOMS, PITCH, out)
    fit = [g for g in nr._local_fit(TILE, BY_TILE[TILE], LOCATED, GEOMS, template)[0] if g.index in norm.levels]
    rim = nm.rim_suppress(image, PX, template, fit, levels=norm.levels)
    reference_rim = nr._reference_rim(TILE, BY_TILE[TILE], LOCATED, images, GEOMS, template, norm)
    got = nr._tile_confluence(fit, PX, template, norm, rim, reference_rim)
    return np.array([np.nan if got.get(i) is None else got[i] for i in GEOMS]), out


@pytest.fixture(scope="module")
def reference():
    """The empty reference well, read against itself: its template, carrying the threshold measured on it."""
    empty, _ = _paint(0)
    conf, out = _confluence(empty, nref.OWN)
    return conf, out.template["_object"]


def _against(template) -> nref.Reference:
    return nref.Reference(templates={"0h": template}, default="no such timepoint")


def test_the_reference_well_reads_empty_and_carries_its_threshold(reference):
    conf, template = reference
    assert np.all(conf <= 0.02)
    assert template.texture_cut is not None and template.texture_cut > 0


def test_a_loaded_well_read_against_the_empty_well_reads_its_coverage(reference):
    """Every nanowell about half covered, at a different exposure from the empty well. Against itself (the code
    before the fix) it read 1-2 %; against the empty well it reads within the optical halo of the truth."""
    _, template = reference
    loaded, truth = _paint(60, seed=4, gain=1.3, offset=0.05)
    conf, _ = _confluence(loaded, _against(template))
    assert truth.mean() > 0.45
    assert np.all(np.isfinite(conf))
    assert np.all(conf >= 0.8 * truth) and np.all(conf <= np.minimum(1.0, 1.6 * truth))
    self_read, _ = _confluence(loaded, nref.OWN)
    assert self_read.mean() < 0.1                 # the defect, measured: calibrated on itself it reads near zero


def test_coverage_that_grows_reads_as_growing(reference):
    _, template = reference
    means = [_confluence(_paint(n, seed=7 + n)[0], _against(template))[0].mean() for n in (10, 30, 60)]
    assert means[0] < means[1] < means[2]


def test_a_reference_with_no_threshold_gives_no_confluence(reference):
    _, template = reference
    loaded, _ = _paint(30, seed=2)
    conf, out = _confluence(loaded, _against(replace(template, texture_cut=None)))
    assert np.all(np.isnan(conf))
    assert any(n.startswith(nref.NOTE_PREFIX) for n in out.notes)


def test_a_timepoint_the_reference_lacks_has_no_confluence():
    loaded, _ = _paint(30, seed=2)
    conf, out = _confluence(loaded, nref.Reference(reasons={"0h": "it was not empty"}))
    assert np.all(np.isnan(conf))                         # undefined, never 0
    assert out.notes == [f"{nref.NOTE_PREFIX}: it was not empty"]


def test_with_no_reference_named_there_is_no_confluence():
    loaded, _ = _paint(30, seed=2)
    conf, out = _confluence(loaded, nref.none_named())
    assert np.all(np.isnan(conf)) and "no empty reference well was named" in out.notes[0]


# ── the stored reference ────────────────────────────────────────────────────
def _rows(counts) -> list:
    return [{"timepoint": "0h", "state": nm.MEASURED if n else nm.EMPTY, "n_cells": n, "confluence": 0.0}
            for n in counts]


def test_the_named_empty_well_is_believed_whatever_the_segmenter_counted_in_it():
    """Reported: a remote run given a blank well came back with no confluence anywhere. Its ``reference.json`` said
    ``loaded`` at every timepoint, with more than 70 % of the blank well's nanowells holding an object the segmenter
    counted — debris, in nanowells the user had confirmed empty. The count-based rule refused the well the user named,
    and with it the whole plate's confluence. The well named empty is now believed: the status is ``empty`` at any
    share, and the share is kept, with ``looks_loaded`` above ``MAX_LOADED_SHARE`` for the note and the warning."""
    quiet = nref.verdict(_rows([0] * 39 + [1]))                                # 2.5 %
    assert quiet["status"] == nref.EMPTY and quiet["looks_loaded"] is False
    reported = nref.verdict(_rows([1] * 72 + [0] * 28))                       # the reported blank: 72 % debris
    assert reported["status"] == nref.EMPTY and reported["looks_loaded"] is True
    assert reported["loaded_share"] == 0.72 and reported["false_cells_per_nanowell"] == 0.72
    assert nref.verdict(_rows([0] * 18 + [1, 1]))["status"] == nref.EMPTY     # 10 %, the old rule's "loaded"
    for uncounted in (_rows([None, None]), []):                                # nothing counted: still as named
        found = nref.verdict(uncounted)
        assert found["status"] == nref.EMPTY and found["loaded_share"] is None and found["looks_loaded"] is False


def _settings(**kw):
    return nr.NanowellSettings("100um", **kw)


def test_a_saved_reference_is_read_back_only_under_the_same_settings(tmp_path, reference):
    _, template = reference
    settings = _settings(empty_well="H12")
    # 12h as the earlier count-based rule saved it: refused, so no template. Still read back as no confluence there.
    old_rule = {"status": nref.LOADED, "loaded_share": 1.0, "nanowells_counted": 10, "max_loaded_share": 0.05}
    nref.save(tmp_path, "H12", settings, {"0h": (template, nref.verdict(_rows([3] * 10))),
                                          "12h": (template, old_rule)})
    back = nref.load(tmp_path, settings)
    got, why = back.at("0h")                       # every nanowell held a counted object, and it is believed empty
    assert why == "" and got.texture_cut == template.texture_cut
    np.testing.assert_array_equal(got.array, template.array)
    assert back.at("12h")[0] is None and "not empty" in back.at("12h")[1]
    assert back.at("24h")[0] is None and "no such timepoint" in back.at("24h")[1]
    other = nref.load(tmp_path, _settings(empty_well="H12", cell_diameter_um=9.0))
    assert other.at("0h")[0] is None and "other settings" in other.at("0h")[1]
    assert "has not been measured" in nref.load(tmp_path, _settings(empty_well="G12")).at("0h")[1]
    assert "no empty reference well was named" in nref.load(tmp_path, _settings()).at("0h")[1]


def test_the_reference_well_is_named_in_the_reuse_key():
    assert _settings(empty_well="H12").key() != _settings(empty_well="G12").key()
    assert "empty_well" not in _settings().key()


def test_the_reference_well_goes_first():
    items = [("A01", "a"), ("B02", "b"), ("H12", "h")]
    assert nref.reference_well_first(_settings(empty_well="h12"), items) == ([("H12", "h")], items[:2])
    assert nref.reference_well_first(_settings(), items) == ([], items)


def test_the_reference_well_keeps_its_confluence_and_says_what_it_counted():
    """Its own timepoints keep their confluence however many objects were counted, and one with many carries one
    note naming the share — passed through ``apply`` twice, as kept rows are, it still carries one."""
    sites = _rows([4, 4]) + [dict(r, timepoint="12h") for r in _rows([0, 0])]
    blocks = {"0h": {"notes": []}, "12h": {"notes": []}}
    verdicts = {"0h": nref.verdict(sites[:2]), "12h": nref.verdict(sites[2:])}
    for _ in range(2):                                    # idempotent: kept rows pass through it again
        nref.apply(sites, blocks, "H12", verdicts)
    assert [r["confluence"] for r in sites] == [0.0, 0.0, 0.0, 0.0]
    assert blocks["0h"]["notes"] == [nref.looks_loaded_note(verdicts["0h"])] and blocks["12h"]["notes"] == []
    assert blocks["0h"]["notes"][0].startswith(nref.LOOKS_LOADED_PREFIX) and "100%" in blocks["0h"]["notes"][0]
    assert not blocks["0h"]["notes"][0].startswith(nref.NOTE_PREFIX)         # it is not a missing confluence
    assert blocks["0h"]["confluence_reference"]["status"] == nref.EMPTY


def test_a_well_is_given_the_reference_the_run_names(tmp_path, reference):
    """``nanowell_task._confluence_reference_for``, the one call that decides what a well reads against: itself
    when it is the reference, the saved reference otherwise, and nothing, with the reason, when none is named."""
    from pipeline import nanowell_task as nt

    _, template = reference
    settings = _settings(empty_well="H12")
    path = tmp_path / nt.NANOWELL_DIR / "A01_template.npz"           # the well's own template path, as the task has it
    assert nt._confluence_reference_for("H12", settings, path) is nref.OWN
    assert "has not been measured" in nt._confluence_reference_for("A01", settings, path).at("0h")[1]
    nref.save(tmp_path, "H12", settings, {"0h": (template, nref.verdict(_rows([0] * 10)))})
    assert nt._confluence_reference_for("A01", settings, path).at("0h")[0] is not None
    unnamed = nt._confluence_reference_for("A01", _settings(), path)
    assert unnamed.at("0h")[0] is None and "no empty reference well was named" in unnamed.at("0h")[1]
