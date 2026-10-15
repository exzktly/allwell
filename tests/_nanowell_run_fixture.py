"""A rendered plate well as the pipeline sees it: tiles, an acquisition record, a stand-in segmenter."""

import numpy as np
from scipy import ndimage

from tests._nanowell_synth import TilePlan, render_tiles

PX = 4.3
PITCH, DIAMETER = 172.0, 100.0


def build_well(rows=2, cols=2, overlap=0.1, tile=(700, 700), *, well="A01", timepoint="T0", seed=1,
               rotation=7.0, cells=0, array_shift_um=(0.0, 0.0), radius_fraction=0.26):
    """``(rendered, record)``; the record holds the mosaic positions the stage reported."""
    plan = TilePlan(rows, cols, tile, overlap, PX)
    extent = (plan.step_um[0] * (cols - 1) + plan.extent_um[0], plan.step_um[1] * (rows - 1) + plan.extent_um[1])
    rendered = render_tiles(plan, pitch_um=PITCH, diameter_um=DIAMETER, radius_um=radius_fraction * min(extent),
                            rotation_deg=rotation, noise_sigma=0.05, blur_px=1.0, seed=seed, cells=cells,
                            array_shift_um=array_shift_um)
    return rendered, record_for(rendered, well=well, timepoint=timepoint)


def record_for(rendered, *, well="A01", timepoint="T0", drop=(), started_at=None):
    """A valid acquisition record (``util.acquisition_record``) for one rendered well at one timepoint."""
    from util.acquisition_record import new_record, new_timepoint

    plan = rendered.plan
    record = new_record()
    meta = new_timepoint(started_at=started_at)
    meta.update(pixel_size_um={"x": PX, "y": PX}, tile_size_px={"x": plan.tile_px[0], "y": plan.tile_px[1]},
                declared_overlap_percent={"x": plan.overlap * 100, "y": plan.overlap * 100})
    record["timepoints"][timepoint] = meta
    record["tiles"] = [{"well": well, "timepoint": timepoint, "field": name,
                        "mosaic_xy_px": [left / PX, top / PX], "stage_xy_um": None}
                       for name, (left, top) in rendered.recorded_left_top_um.items() if name not in drop]
    return record


def threshold_segmenter(level=1.2, smooth=1.5, min_px=12):
    """Smoothed threshold, components, small ones dropped: a deterministic stand-in for Cellpose.

    The rim-suppressed tile of a noisy phase image has speckle of a few per cent per pixel, so a
    bare threshold finds a dozen one-pixel "cells" in every site; any real segmenter smooths.
    """
    def segment(rim):
        smoothed = ndimage.gaussian_filter(np.asarray(rim, dtype=float), smooth)
        labels, n = ndimage.label(smoothed > level)
        sizes = ndimage.sum(np.ones_like(labels), labels, index=np.arange(1, n + 1))
        for k, size in enumerate(sizes, start=1):
            if size < min_px:
                labels[labels == k] = 0
        return labels
    return segment


def paint_cell(rendered, site_um, *, dx_um=0.0, dy_um=0.0, radius_px=4.0, amount=0.35):
    """Add a bright blob to every tile that sees ``site_um + (dx, dy)`` (the same cell in an overlap)."""
    cx, cy = site_um[0] + dx_um, site_um[1] + dy_um
    for name, image in rendered.images.items():
        left, top = rendered.true_left_top_um[name]
        yy, xx = np.mgrid[0:image.shape[0], 0:image.shape[1]]
        inside = np.hypot((xx + 0.5) * PX + left - cx, (yy + 0.5) * PX + top - cy) <= radius_px * PX
        image[inside] += amount
