"""``nanowells/<well>_segmenter_input.png``: a sample of nanowells as the count's segmenter was given them.

Asked for: "is there a way to view, for a given well, what was sent for cell detection?" — Test detection shows it,
but runs on the app's own machine, which on a Windows install whose Torch cannot load means never, while the analysis
runs on a remote GPU. The run now writes the sample itself and a remote run fetches it.
"""

from __future__ import annotations

import struct
import zlib

import numpy as np

from pipeline import nanowell_inspect as ni
from tests.test_nanowell_cli import WELL, _run_main, _write_plate, rendered_well  # noqa: F401


def _decode(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", data[16:24])
    pos, idat = 8, b""
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        if kind == b"IDAT":
            idat += data[pos + 8:pos + 8 + n]
        pos += 12 + n
    raw = np.frombuffer(zlib.decompress(idat), np.uint8).reshape(h, 1 + 3 * w)
    assert not raw[:, 0].any()                                            # filter type 0 on every row
    return raw[:, 1:].reshape(h, w, 3)


def test_the_sample_is_the_nanowells_nearest_the_centre_and_farthest_from_it():
    points = [(x, y) for y in range(5) for x in range(5)]                 # a 5 x 5 grid; centre (2, 2) is index 12
    picked = ni.inspection_sites(points, range(25), n=4)
    assert picked[12] == "centre" and sum(v == "centre" for v in picked.values()) == 4
    assert {i for i, v in picked.items() if v == "edge"} == {0, 4, 20, 24}
    assert ni.inspection_sites(points[:3], range(3), n=8) == {1: "centre", 0: "centre", 2: "centre"}
    assert ni.inspection_sites(points, [], n=4) == {}


def test_a_crop_is_drawn_on_the_segmenter_s_scale_with_its_cells_outlined(tmp_path):
    """Grey levels are the crop's own 0-1 values, not stretched: a crop the model saw at 0.4-0.6 is drawn mid-grey."""
    image = np.full((40, 40), 0.5, np.float32)
    labels = np.zeros((40, 40), np.int32)
    labels[10:20, 10:20] = 1
    kept = {3: {"kind": "centre", "address": (0, 0), "image": image, "labels": labels},
            9: {"kind": "edge", "address": (5, 0), "image": image * 0, "labels": labels * 0}}
    path = tmp_path / "s.png"
    assert ni.write_sheet(path, ("0h", kept))
    rgb = _decode(path)
    step = ni.CELL_PX + ni.GAP_PX
    assert rgb.shape == (ni.GAP_PX + 2 * step, ni.GAP_PX + step, 3)
    top = rgb[ni.GAP_PX:ni.GAP_PX + ni.CELL_PX, ni.GAP_PX:ni.GAP_PX + ni.CELL_PX]
    bottom = rgb[ni.GAP_PX + step:ni.GAP_PX + step + ni.CELL_PX, ni.GAP_PX:ni.GAP_PX + ni.CELL_PX]
    assert tuple(top[0, 0]) == ni.FRAME["centre"] and tuple(bottom[0, 0]) == ni.FRAME["edge"]
    assert tuple(top[ni.CELL_PX - 10, ni.CELL_PX - 10]) == (127, 127, 127)          # mid-grey, as given
    assert (top == ni.OUTLINE).all(axis=-1).sum() > 0 and not (bottom == ni.OUTLINE).all(axis=-1).any()
    assert tuple(bottom[ni.CELL_PX // 2, ni.CELL_PX // 2]) == (0, 0, 0)
    assert not ni.write_sheet(tmp_path / "none.png", None) and not (tmp_path / "none.png").exists()


def test_a_nanowell_run_writes_its_wells_sheet_from_the_crops_the_segmenter_was_given(tmp_path, monkeypatch,
                                                                                       rendered_well,  # noqa: F811
                                                                                       caplog):
    """Through the real run: the sheet is written, and its crops are the segmenter's own input — the sample's
    crops, captured where the segmenter is called, carry the labels it returned (a segmenter that marks the centre
    of every crop, so every kept crop has some). The run log states the scale the
    timepoint reached Cellpose at, before it is measured."""
    import logging

    from pipeline import nanowell_run as nr

    captured = []
    real = nr._keep_crops

    def spy(keep, seen, labels, px, fit_sites, addresses, lowhigh=None):
        real(keep, seen, labels, px, fit_sites, addresses, lowhigh)
        if keep is not None:
            captured.append(dict(keep[1]))
    monkeypatch.setattr(nr, "_keep_crops", spy)
    import tests.test_nanowell_cli as cli

    def centre_mark(crop):                       # labels a block at the centre of every crop: what it returned
        labels = np.zeros(np.shape(crop), np.int32)
        h, w = labels.shape
        labels[h // 2 - 1:h // 2 + 2, w // 2 - 1:w // 2 + 2] = 1
        return labels
    monkeypatch.setattr(cli, "threshold_segmenter", lambda: centre_mark)
    rendered, record, _truth = rendered_well
    plate = _write_plate(tmp_path, rendered, record)
    with caplog.at_level(logging.INFO, logger="allwell.nanowell"):
        _run_main(monkeypatch, plate, "--nanowell_format", "100um")
    messages = [r.getMessage() for r in caplog.records]
    scale = [m for m in messages if "Cellpose" in m and "um/px" in m]
    first_count = next(k for k, m in enumerate(messages) if "nanowells segmented" in m)
    assert scale == [f"Well {WELL} — 0h: Cellpose cpsam at " + scale[0].split(" at ", 1)[1]]
    assert messages.index(scale[0]) < first_count
    png = plate / "out" / "nanowells" / f"{WELL}{ni.SUFFIX}"
    rgb = _decode(png)
    kept = {k: v for part in captured for k, v in part.items()}
    assert len(kept) == 2 * ni.PER_GROUP and {v["kind"] for v in kept.values()} == {"centre", "edge"}
    assert all(v["labels"].any() for v in kept.values())                 # each crop's labels are the segmenter's
    # Scaled by the nanowells' interiors, with rims and lattice blanked: everything in a crop outside its nanowell's
    # counting circle is one plain value — on a run that scaled by the tile, the rim is there, in many values.
    whole = [np.asarray(v["image"]) for v in kept.values() if v["image"].shape[0] == v["image"].shape[1]]
    assert whole
    for image in whole:
        n = image.shape[0]
        yy, xx = np.mgrid[:n, :n]
        beyond = np.hypot(yy - (n - 1) / 2, xx - (n - 1) / 2) > 0.95 * n / 2 / 1.2      # past 0.95 of the radius
        assert len(np.unique(image[beyond])) == 1
    assert rgb.shape[0] == ni.GAP_PX + 2 * (ni.CELL_PX + ni.GAP_PX)
