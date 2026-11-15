"""Per-cell focus is read at a nucleus's outline, whichever segmenter drew the cells.

``qc_edge_focus`` is the width of the intensity transition at an outline on the
**nuclear** frame. With StarDist nuclei the labels are those outlines. With
seeded watershed the labels are *cell* outlines found on the cytoplasm frame,
and at a cell's outline the nuclear frame has no edge — so reading it there
measured 28 of 81 cells, with values from 0.18 to 15.7 where every nucleus
reads 0.36. The seed nuclei travel with the cells, under the cells' ids, and
are what focus is read from.

Driven through ``process_image_group`` with images on disk; only StarDist's
network and the image writes are replaced.
"""

from __future__ import annotations

import numpy as np
import pytest
import scipy.ndimage as ndi
from tifffile import imwrite

import pipeline.stardist as sd
import process_microscopy as pm
from util.schema import DEFAULT_SCHEMA, parse_schema
from pipeline.field_quality import FOCUS_COLUMN, cell_focus_by_label

SCHEMA = parse_schema(DEFAULT_SCHEMA)
SIZE = 600
PITCH = 60
NUCLEUS_R, CELL_R = 7, 22
DEFOCUS = 1.0


def _plate():
    """A lattice of 81 round cells, each with a small round nucleus at its centre."""
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    nuclei = np.zeros((SIZE, SIZE), np.int32)
    cells = np.zeros((SIZE, SIZE), bool)
    k = 0
    for cy in range(40, SIZE - 30, PITCH):
        for cx in range(40, SIZE - 30, PITCH):
            k += 1
            d2 = (yy - cy) ** 2 + (xx - cx) ** 2
            nuclei[d2 <= NUCLEUS_R ** 2] = k
            cells |= d2 <= CELL_R ** 2
    rng = np.random.default_rng(0)
    nuclear = (ndi.gaussian_filter((nuclei > 0) * 800.0, DEFOCUS) + 100
               + rng.normal(0, 3, nuclei.shape))
    cytoplasm = ndi.gaussian_filter(cells * 600.0, 1.5) + 100 + rng.normal(0, 3, nuclei.shape)
    return nuclei, nuclear, cytoplasm


def _as_stored(frame: np.ndarray) -> np.ndarray:
    return np.clip(frame, 0, 65535).astype(np.uint16)


@pytest.fixture
def seeded_run(tmp_path, monkeypatch):
    nuclei, nuclear, cytoplasm = _plate()
    raw = tmp_path / "raw"
    raw.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    nuc = raw / "exp_DAPI_A01_f1_t0.tif"
    cyt = raw / "exp_GFP_A01_f1_t0.tif"
    imwrite(str(nuc), _as_stored(nuclear))
    imwrite(str(cyt), _as_stored(cytoplasm))
    monkeypatch.setattr(sd, "_segment_stardist_nuclei", lambda img: nuclei.copy())
    monkeypatch.setattr(pm, "_write_processed_images", lambda **k: None)
    records, quality = pm.process_image_group(
        nuclear_path=nuc, fluor_paths=[cyt], fluor_tokens=["GFP"],
        output_dir=out, nuclear_token="DAPI", tophat_nir=False,
        tophat_radius_nir=50, tophat_fluor=[False], tophat_radius_fluor=[50],
        schema=SCHEMA, sep="_", segmentation_method="stardist_seeded_watershed_cell",
        cytoplasm_token="GFP", background_method="none", flatfield_mode="none",
    )
    return nuclei, nuclear, records, quality


def test_every_cell_reads_its_own_nucleus_focus(seeded_run):
    nuclei, nuclear, records, _quality = seeded_run
    assert len(records) == 81
    # The premise: these are cells, not nuclei — several times a nucleus's area.
    assert min(r["area_px"] for r in records) > 5 * np.pi * NUCLEUS_R ** 2

    # The reference: the same estimator on the nuclear frame as the loader
    # returns it, at the nucleus outlines themselves.
    reference = cell_focus_by_label(_as_stored(nuclear).astype(np.float32), nuclei)
    assert all(v is not None for v in reference.values())

    measured = {r["nucleus_id"]: r[FOCUS_COLUMN] for r in records}
    unmeasured = [nid for nid, v in measured.items() if v is None]
    assert unmeasured == [], f"{len(unmeasured)} of 81 cells have no focus value"
    for nid, value in measured.items():
        assert value == pytest.approx(reference[nid], rel=1e-9), nid
    values = np.array(list(measured.values()))
    assert values.max() / values.min() < 1.05       # one focus state, one number


def test_the_field_focus_is_the_median_of_those_values(seeded_run):
    _nuclei, _nuclear, records, quality = seeded_run
    values = [r[FOCUS_COLUMN] for r in records if r[FOCUS_COLUMN] is not None]
    assert quality["focus"] == pytest.approx(float(np.median(values)))
    assert 0.3 < quality["focus"] < 0.45


def test_seed_nuclei_carry_the_ids_of_the_cells_grown_from_them():
    """A nucleus too small to seed has no cell, and is absent here under any id."""
    nuclei = np.zeros((60, 60), np.int32)
    nuclei[5:15, 5:15] = 1          # 100 px
    nuclei[30:32, 30:32] = 2        # 4 px: below the minimum, not seeded
    nuclei[40:52, 40:52] = 3        # 144 px
    seeds = sd.seed_nucleus_labels(nuclei, 20)
    markers, n_markers, n_small = sd.nucleus_seed_markers(nuclei, 20)
    assert (n_markers, n_small) == (2, 1)
    assert sorted(np.unique(seeds)) == [0, 1, 2]
    np.testing.assert_array_equal(seeds == 1, nuclei == 1)
    np.testing.assert_array_equal(seeds == 2, nuclei == 3)
    # Each marker sits inside the nucleus that carries its id.
    np.testing.assert_array_equal(seeds[markers > 0], markers[markers > 0])
