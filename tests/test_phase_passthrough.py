"""The phase channel skips background correction and is passed through raw.

A channel marked "Phase" (brightfield) must not be background-corrected —
top-hat / sliding-disk on a phase-contrast image is meaningless — and its
original image is what gets resaved. This verifies `_correct_image_group`
leaves the phase channel byte-identical to its input while a normal fluor
channel is altered, for both the fluor-slot and nuclear-slot cases.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("tifffile")

import numpy as np  # noqa: E402

from process_microscopy import _correct_image_group, parse_schema  # noqa: E402

_SCHEMA = parse_schema("experiment:channel:well:fov:timepoint")


def _structured_image(seed):
    rng = np.random.default_rng(seed)
    # Structure + noise so top-hat actually changes a non-phase channel.
    yy, xx = np.mgrid[0:32, 0:32].astype(np.float32)
    base = 100.0 + 40.0 * np.sin(xx / 5.0) + 20.0 * (yy / 32.0)
    return (base + rng.random((32, 32)).astype(np.float32) * 10.0).astype(np.float32)


def _correct(*, nuclear_token, fluor_tokens, phase_token, nir, fluors):
    return _correct_image_group(
        nir, list(fluors),
        nuclear_path=Path("exp_nuc_A01_f01_T01.tif"),
        nuclear_token=nuclear_token, fluor_tokens=list(fluor_tokens),
        tophat_nir=True, tophat_radius_nir=15,
        tophat_fluor=[True] * len(fluor_tokens),
        tophat_radius_fluor=[15] * len(fluor_tokens),
        fft_small_nir=0, fft_small_fluor=[0] * len(fluor_tokens),
        flatfield_mode="none", flatfield_models={},
        background_method="top_hat", background_models={},
        smfish_tokens=[], mesh_ratio=3.0, mesh_sigma=3.0,
        schema=_SCHEMA, sep="_", phase_token=phase_token,
    )


def test_phase_fluor_channel_passed_through_raw():
    nir = _structured_image(0)
    gfp = _structured_image(1)
    bf = _structured_image(2)
    gfp_in, bf_in = gfp.copy(), bf.copy()

    _, _, _, fluor_tophat, _ = _correct(
        nuclear_token="dapi", fluor_tokens=["gfp", "bf"], phase_token="bf",
        nir=nir, fluors=[gfp, bf],
    )

    # Phase channel ("bf") is untouched; the normal channel ("gfp") is corrected.
    assert np.array_equal(fluor_tophat[1], bf_in)
    assert not np.array_equal(fluor_tophat[0], gfp_in)


def test_phase_nuclear_channel_passed_through_raw():
    nir = _structured_image(3)
    gfp = _structured_image(4)
    nir_in = nir.copy()

    _, _, nir_corr, fluor_tophat, _ = _correct(
        nuclear_token="bf", fluor_tokens=["gfp"], phase_token="bf",
        nir=nir, fluors=[gfp],
    )

    # Nuclear slot IS the phase channel (label-free case) → raw pass-through.
    assert np.array_equal(nir_corr, nir_in)


def test_no_phase_token_corrects_all_channels():
    nir = _structured_image(5)
    bf = _structured_image(6)
    bf_in = bf.copy()

    _, _, _, fluor_tophat, _ = _correct(
        nuclear_token="dapi", fluor_tokens=["bf"], phase_token="",
        nir=nir, fluors=[bf],
    )

    # Without a phase token, every channel is corrected as usual.
    assert not np.array_equal(fluor_tophat[0], bf_in)


def test_phase_match_is_case_insensitive():
    nir = _structured_image(7)
    bf = _structured_image(8)
    bf_in = bf.copy()

    _, _, _, fluor_tophat, _ = _correct(
        nuclear_token="dapi", fluor_tokens=["BF"], phase_token="bf",
        nir=nir, fluors=[bf],
    )

    assert np.array_equal(fluor_tophat[0], bf_in)


def test_process_image_group_forwards_phase_token(monkeypatch):
    """The orchestrator threads phase_token into _correct_image_group (the full
    GUI → worker plumbing lands here)."""
    import process_microscopy as pm

    seen = {}
    nir = _structured_image(9)
    bf = _structured_image(10)

    monkeypatch.setattr(pm, "_load_image_group", lambda n, f, t: (nir, [bf]))

    def _capture(nir_raw, fluor_raws, **kwargs):
        seen["phase_token"] = kwargs.get("phase_token")
        return nir_raw, fluor_raws, nir_raw, [fluor_raws[0]], [fluor_raws[0]]

    monkeypatch.setattr(pm, "_correct_image_group", _capture)
    monkeypatch.setattr(
        pm, "_segment_image_group",
        lambda **k: (np.zeros((32, 32), np.int32), None, None, None))
    monkeypatch.setattr(pm, "_write_processed_images", lambda **k: None)
    monkeypatch.setattr(pm, "_quantify_image_group", lambda **k: [])

    pm.process_image_group(
        Path("exp_bf_A01_f01_T01.tif"), [Path("exp_bf_A01_f01_T01.tif")],
        fluor_tokens=["bf"], output_dir=Path("."), nuclear_token="dapi",
        tophat_nir=True, tophat_radius_nir=15, tophat_fluor=[True],
        tophat_radius_fluor=[15], schema=_SCHEMA, phase_token="bf",
    )
    assert seen["phase_token"] == "bf"
