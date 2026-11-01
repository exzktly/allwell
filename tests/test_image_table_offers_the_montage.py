"""The Image Table offers a nanowell run's montage and stops offering an overlay it never wrote
(ARCHITECTURE §6.2l).

A nanowell run's ``out.zip`` holds one stitched montage per well and timepoint and no segmentation overlay, so the
Image Table's NUC+SEG row was "(no image)" in every cell and the picture the run analysed was never offered. The
virtual channel ``MONTAGE`` resolves through ``frame_load`` to that picture (a montage has no FOV, so the cell's
FOV is ignored); NUC+SEG is offered only for a run that writes overlays.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from review.data.frame_load import MONTAGE_TOKEN, NUC_SEG_TOKEN, load_frame_array  # noqa: E402
from review.data.nanowell_images import montage_member  # noqa: E402


def _loaded(qtbot, path):
    from tests._minimal_shell import load_dataset, minimal_shell

    ctx, _window = minimal_shell(qtbot)
    load_dataset(ctx, path)
    ctx.state.dataset.data_dir = path
    return ctx


def test_a_nanowell_run_offers_its_montage_first_and_no_overlay(qtbot, tmp_path):
    from review.data.dataset_dims import channel_options
    from tests.fixtures.nanowell_dataset import build_nanowell_dataset

    ctx = _loaded(qtbot, build_nanowell_dataset(tmp_path / "nw") / "out")
    options = channel_options(ctx)
    assert options[0] == MONTAGE_TOKEN and NUC_SEG_TOKEN not in options


def test_a_standard_run_keeps_its_overlay_and_has_no_montage(qtbot, mini_dataset):
    from review.data.dataset_dims import channel_options

    options = channel_options(_loaded(qtbot, mini_dataset))
    assert NUC_SEG_TOKEN in options and MONTAGE_TOKEN not in options


def test_a_montage_cell_draws_the_picture_the_run_kept_whatever_its_fov(qtbot, tmp_path):
    import io
    import zipfile

    from PIL import Image

    from tests.fixtures.nanowell_dataset import build_nanowell_dataset

    out = build_nanowell_dataset(tmp_path / "nw") / "out"
    ctx = _loaded(qtbot, out)
    with zipfile.ZipFile(out / "A01_out.zip") as zf:
        want = np.asarray(Image.open(io.BytesIO(zf.read("A01_24h_montage.png"))).convert("L"), dtype=np.float32)
    for tp in ("24", "24h"):                       # the dropdown's normalised hours and the run's own label
        got = load_frame_array(ctx, {}, "A01", MONTAGE_TOKEN, tp, "F9")
        assert got is not None and np.array_equal(got, want)
    assert load_frame_array(ctx, {}, "A01", MONTAGE_TOKEN, "48", "F0") is None


def test_the_member_is_found_by_its_timepoint_not_by_a_prefix_collision():
    names = ["A01_0h_montage.png", "A01_24h_montage.png", "A011_0h_montage.png"]
    assert montage_member(names, "A01", "24") == "A01_24h_montage.png"
    assert montage_member(names, "A01", "0h") == "A01_0h_montage.png"
    assert montage_member(names, "A01", "12") is None
