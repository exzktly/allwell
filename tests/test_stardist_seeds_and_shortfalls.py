"""Seeded-watershed marker construction, and the FOVs that yield no cells.

Three audit findings in ``pipeline.stardist``:

* **C7** — the marker builder asked ``np.count_nonzero(labels == nid)`` and
  ``ndi.center_of_mass(labels == nid)`` inside a per-nucleus loop, which is one
  and two full passes over the label image *per nucleus*. Measured on 1500
  nuclei in a 2048² frame: 1.43 s for the areas and 23.67 s for the centroids,
  against 0.080 s and 0.250 s vectorised — about 25 s of every FOV, and roughly
  2.7 hours across a 96-well plate at 4 FOVs. The replacement has to be the same
  arithmetic, not merely a similar one, so the equivalence cases below are
  deliberately awkward: single-pixel labels, non-contiguous ids, an annulus
  whose centroid falls in its own hole, labels on every border, exact ``.5``
  centroids in both parities, four integer dtypes and an empty frame.
* **C10** — three ways this function returned zero cells without a word, in a
  module that refuses a near-constant *nuclear* input forty lines above with an
  explicit warning: a constant cytoplasm channel (``threshold_otsu`` raises, the
  fallback mean admits no pixel), every nucleus below ``min_nucleus_area_px``,
  and markers that fall outside the cytoplasm mask and are dropped by
  ``watershed(mask=…)``.
* **C14** — ``parse_threshold`` let ``float("nan")`` through both bounds, since
  NaN is neither ``< 0`` nor ``> 1.0``, and it reached ``predict_instances``.

**What is and is not exercised here.** TensorFlow and StarDist are not installed
in this environment, so the *inference* step is stubbed: every test below
replaces ``_segment_stardist_nuclei`` with a canned label image and drives the
real marker construction, the real Otsu/mask/watershed arithmetic and the real
log lines. Nothing here proves anything about ``predict_instances`` itself.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest
from scipy import ndimage as ndi

import pipeline.stardist as sd
from pipeline.stardist import nucleus_seed_markers, parse_threshold


# ── the marker builder this replaces, kept verbatim as the reference ─────────

def _markers_the_old_way(stardist_labels, min_nucleus_area_px):
    """The per-nucleus loop, copied from before the change, as the oracle."""
    nucleus_ids = np.unique(stardist_labels)
    nucleus_ids = nucleus_ids[nucleus_ids != 0]
    filtered_ids = []
    for nid in nucleus_ids:
        area = int(np.count_nonzero(stardist_labels == nid))
        if area >= min_nucleus_area_px:
            filtered_ids.append(int(nid))
    nuclear_points = np.zeros_like(stardist_labels, dtype=np.int32)
    marker_id = 1
    for nid in filtered_ids:
        cy, cx = ndi.center_of_mass(stardist_labels == nid)
        if np.isnan(cy) or np.isnan(cx):
            continue
        y = int(np.clip(round(float(cy)), 0, stardist_labels.shape[0] - 1))
        x = int(np.clip(round(float(cx)), 0, stardist_labels.shape[1] - 1))
        nuclear_points[y, x] = marker_id
        marker_id += 1
    return nuclear_points, marker_id - 1


def _blobs(shape, rng, ids, radius_hi=12):
    lab = np.zeros(shape, dtype=np.int32)
    yy, xx = np.ogrid[:shape[0], :shape[1]]
    for lid in ids:
        r = int(rng.integers(1, radius_hi))
        cy = int(rng.integers(0, shape[0]))
        cx = int(rng.integers(0, shape[1]))
        lab[(yy - cy) ** 2 + (xx - cx) ** 2 <= r * r] = lid
    return lab


def _cases():
    cases = {}
    cases["empty frame"] = (np.zeros((16, 16), np.int32), 5)
    cases["one pixel of background"] = (np.zeros((1, 1), np.int32), 0)
    sp = np.zeros((20, 20), np.int32)
    sp[0, 0] = 1
    sp[19, 19] = 2
    sp[10, 10] = 3
    cases["single-pixel labels in the corners"] = (sp, 1)
    cases["single-pixel labels, all below min area"] = (sp, 2)
    nc = np.zeros((30, 30), np.int32)
    nc[2:6, 2:6] = 7
    nc[10:12, 10:12] = 900
    nc[20, 20] = 3
    cases["non-contiguous ids"] = (nc, 2)
    bd = np.zeros((25, 25), np.int32)
    bd[0, :] = 1
    bd[:, 0] = 2
    bd[24, :] = 3
    bd[:, 24] = 4
    cases["labels touching every border"] = (bd, 1)
    # Two labels whose centroids land on the *same* pixel: label 2 is the two
    # pixels above and below label 1, so both centroids are (4, 4). The loop
    # wrote one marker over the other and left an id unused; the vectorised
    # assignment has to keep the same one (NumPy's indexed assignment takes the
    # last write, which is the loop's overwrite).
    two = np.zeros((9, 9), np.int32)
    two[4, 4] = 1
    two[3, 4] = 2
    two[5, 4] = 2
    cases["two nuclei sharing a rounded centroid"] = (two, 1)
    ring = np.zeros((21, 21), np.int32)
    yy, xx = np.ogrid[:21, :21]
    d = (yy - 10) ** 2 + (xx - 10) ** 2
    ring[(d <= 100) & (d >= 49)] = 5     # centroid lands inside the hole
    cases["an annulus whose centroid is not in it"] = (ring, 1)
    for dt in (np.int16, np.int32, np.int64, np.uint16):
        cases[f"blobs as {np.dtype(dt).name}"] = (
            _blobs((128, 128), np.random.default_rng(3), range(1, 26)).astype(dt), 10)
    cases["sparse ids up to 1000"] = (
        _blobs((128, 128), np.random.default_rng(4),
               [3, 11, 29, 40, 41, 77, 101, 250, 251, 300, 999, 1000]), 7)
    cases["300 blobs"] = (_blobs((512, 512), np.random.default_rng(5), range(1, 301)), 20)
    cases["min area larger than the frame"] = (
        _blobs((64, 64), np.random.default_rng(6), range(1, 11)), 10 ** 6)
    cases["min area of zero"] = (
        _blobs((64, 64), np.random.default_rng(6), range(1, 11)), 0)
    # Shapes the fast path must decline rather than mis-answer. A float label
    # image would have to be truncated towards zero to index a `bincount`, which
    # merges 2.0 and 2.5 into one object; a negative id would raise. The
    # segmenter's own labels are int32, so these are guards, not live paths.
    cases["float labels, one a half-integer"] = (
        np.array([[0, 1.0, 1.0], [2.5, 2.5, 0], [0, 0, 2.0]], dtype=np.float32), 1)
    cases["float64 labels"] = (
        np.array([[0, 1.0, 1.0], [2.0, 2.0, 0]], dtype=np.float64), 1)
    cases["a negative id"] = (np.array([[0, -1], [2, 2]], dtype=np.int32), 1)
    cases["a boolean mask"] = (np.array([[0, 1], [1, 0]], dtype=bool), 1)
    cases["a single row"] = (np.arange(6, dtype=np.int32).reshape(1, 6), 1)
    cases["a single column"] = (np.arange(6, dtype=np.int32).reshape(6, 1), 1)
    cases["ids in descending spatial order"] = (
        np.array([[9, 9, 0], [0, 4, 4], [1, 0, 0]], dtype=np.int32), 1)
    cases["min area equal to an area exactly"] = (
        np.array([[1, 1, 0], [0, 2, 0], [3, 3, 3]], dtype=np.int32), 2)
    return cases


@pytest.mark.parametrize("name", sorted(_cases()))
def test_the_vectorised_markers_are_the_loop_s_markers(name):
    labels, min_area = _cases()[name]
    want_points, want_n = _markers_the_old_way(labels, min_area)
    got_points, got_n, got_small = nucleus_seed_markers(labels, min_area)
    assert np.array_equal(got_points, want_points), f"marker image differs on {name}"
    assert got_n == want_n
    ids = np.unique(labels)
    ids = ids[ids != 0]
    below = sum(1 for i in ids if np.count_nonzero(labels == i) < min_area)
    assert got_small == below


@pytest.mark.parametrize("offset", [0, 1, 2, 3, 4])
def test_exact_half_pixel_centroids_round_the_same_way(offset):
    """``round`` and ``np.rint`` are both half-to-even; pin it, do not assume it."""
    labels = np.zeros((8, 8), np.int32)
    labels[3, offset] = 1
    labels[3, offset + 1] = 1
    want, _ = _markers_the_old_way(labels, 1)
    got, _n, _s = nucleus_seed_markers(labels, 1)
    assert np.array_equal(got, want)


def test_a_label_image_with_an_absurd_id_does_not_allocate_by_id():
    """``bincount`` sizes by the largest id, so the fast path is bounded."""
    labels = np.zeros((8, 8), np.int64)
    labels[2, 2] = 2 ** 40
    labels[5, 5] = 3
    want, want_n = _markers_the_old_way(labels, 1)
    got, got_n, _small = nucleus_seed_markers(labels, 1)
    assert np.array_equal(got, want)
    assert got_n == want_n == 2


# ── C10: a FOV that yields no cells says which of the four reasons it was ────

def _install_nuclei(monkeypatch, labels):
    monkeypatch.setattr(sd, "_segment_stardist_nuclei", lambda _img: labels)


def _nuclei_and_cytoplasm(n=4, shape=(64, 64)):
    labels = np.zeros(shape, np.int32)
    cyto = np.zeros(shape, np.float32)
    yy, xx = np.ogrid[:shape[0], :shape[1]]
    for i in range(n):
        cy, cx = 16 + 16 * (i // 2), 16 + 16 * (i % 2)
        labels[(yy - cy) ** 2 + (xx - cx) ** 2 <= 25] = i + 1
        cyto[(yy - cy) ** 2 + (xx - cx) ** 2 <= 100] = 900.0
    return labels, cyto


def test_a_constant_cytoplasm_channel_says_it_produced_no_cells(monkeypatch, caplog):
    labels, _cyto = _nuclei_and_cytoplasm()
    _install_nuclei(monkeypatch, labels)
    flat = np.full(labels.shape, 42.0, np.float32)
    sd.set_fov_label("A01_f1_t0")
    with caplog.at_level(logging.WARNING, logger="process_microscopy"):
        out, points, _tophat, mask = sd._segment_stardist_seeded_watershed_cell(
            nir_corr=np.zeros_like(flat), cytoplasm_raw=flat,
            tophat_radius_fluor=5, min_nucleus_area_px=5)
    assert int(out.max()) == 0
    assert not mask.any()
    assert points.max() > 0, "the nuclei were seeded; it is the mask that is empty"
    assert "A01_f1_t0" in caplog.text
    assert "0 cells" in caplog.text


def test_every_nucleus_below_the_area_floor_says_so(monkeypatch, caplog):
    labels, cyto = _nuclei_and_cytoplasm()
    _install_nuclei(monkeypatch, labels)
    sd.set_fov_label("B02_f0_t0")
    with caplog.at_level(logging.WARNING, logger="process_microscopy"):
        out, points, _tophat, _mask = sd._segment_stardist_seeded_watershed_cell(
            nir_corr=np.zeros_like(cyto), cytoplasm_raw=cyto,
            tophat_radius_fluor=5, min_nucleus_area_px=10 ** 6)
    assert int(out.max()) == 0
    assert int(points.max()) == 0
    assert "B02_f0_t0" in caplog.text
    assert "min_nucleus_area_px" in caplog.text
    assert "0 cells" in caplog.text


def test_seeds_outside_the_cytoplasm_mask_are_counted(monkeypatch, caplog):
    """A nucleus whose stain does not cover it is dropped by ``watershed(mask=…)``."""
    labels, cyto = _nuclei_and_cytoplasm()
    # Wipe the cytoplasm stain under one nucleus, leaving its seed unsupported.
    yy, xx = np.ogrid[:cyto.shape[0], :cyto.shape[1]]
    cyto[(yy - 16) ** 2 + (xx - 16) ** 2 <= 144] = 0.0
    _install_nuclei(monkeypatch, labels)
    sd.set_fov_label("C03_f2_t1")
    with caplog.at_level(logging.WARNING, logger="process_microscopy"):
        out, _points, _tophat, _mask = sd._segment_stardist_seeded_watershed_cell(
            nir_corr=np.zeros_like(cyto), cytoplasm_raw=cyto,
            tophat_radius_fluor=20, min_nucleus_area_px=5)
    assert "C03_f2_t1" in caplog.text
    assert "outside the cytoplasm mask" in caplog.text
    assert len(np.unique(out)) - 1 < 4


def test_a_healthy_field_warns_about_none_of_it(monkeypatch, caplog):
    labels, cyto = _nuclei_and_cytoplasm()
    _install_nuclei(monkeypatch, labels)
    sd.set_fov_label("D04_f0_t0")
    with caplog.at_level(logging.WARNING, logger="process_microscopy"):
        out, _points, _tophat, _mask = sd._segment_stardist_seeded_watershed_cell(
            nir_corr=np.zeros_like(cyto), cytoplasm_raw=cyto,
            tophat_radius_fluor=20, min_nucleus_area_px=5)
    assert len(np.unique(out)) - 1 == 4
    assert caplog.text == ""


# ── C14: NaN is not a probability ────────────────────────────────────────────

def test_a_nan_threshold_means_the_model_default():
    assert parse_threshold(float("nan")) is None
    assert parse_threshold("nan") is None
    assert parse_threshold("NaN") is None


def test_the_other_threshold_rules_are_unchanged():
    assert parse_threshold(None) is None
    assert parse_threshold("") is None
    assert parse_threshold("auto") is None
    assert parse_threshold(-1) is None
    assert parse_threshold(0.2) == pytest.approx(0.2)
    assert parse_threshold("0.5") == pytest.approx(0.5)
    with pytest.raises(ValueError):
        parse_threshold(1.5)
    with pytest.raises(ValueError):
        parse_threshold(float("inf"))
