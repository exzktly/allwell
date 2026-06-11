"""Synthetic raw-image generator for end-to-end pipeline tests.

Mirrors the determinism philosophy of ``mini_dataset.py`` (which fabricates
*CSV* rows for the Review side) but one layer earlier: it writes schema-named
*raw TIFFs* the analysis pipeline can actually segment. Nuclei are solid bright
disks on a dark background — empirically the ``2D_versatile_fluo`` StarDist
model detects these reliably (9/9), so a test can assert an exact nucleus count
and known geometry without a real microscope dataset.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

# A 3×3 grid of well-separated nuclei centres on a 256² field.
DEFAULT_CENTERS = [
    (50, 50), (50, 128), (50, 206),
    (128, 50), (128, 128), (128, 206),
    (206, 50), (206, 128), (206, 206),
]
DEFAULT_RADIUS = 12          # disk radius in px -> area ≈ π·r² ≈ 452
NUCLEAR_BG = 80.0
NUCLEAR_PEAK = 3000.0
FLUOR_BG = 200.0
FLUOR_PEAK = 800.0


def _disks(shape, centers, radius, bg, peak) -> np.ndarray:
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.full((h, w), float(bg), dtype=np.float32)
    for cy, cx in centers:
        img[((yy - cy) ** 2 + (xx - cx) ** 2) <= radius ** 2] = float(peak)
    return img


def write_synthetic_fov(
    out_dir: Path,
    *,
    experiment: str = "exp",
    nuclear_token: str = "DAPI",
    fluor_token: str = "GFP",
    well: str = "A01",
    fov: str = "1",
    timepoint: str = "0",
    shape=(256, 256),
    centers=DEFAULT_CENTERS,
    radius: int = DEFAULT_RADIUS,
    sep: str = "_",
) -> tuple[Path, Path]:
    """Write one nuclear + one fluor TIFF named per the default 5-field schema
    (``experiment:channel:well:fov:timepoint``). Returns ``(nuclear, fluor)``.

    The fluor channel is bright *inside* the nuclei (peak) and dim elsewhere
    (bg) so per-cell ``<fluor>_mean_intensity`` is well above background and
    assertable.
    """
    import tifffile

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    nuclear = _disks(shape, centers, radius, NUCLEAR_BG, NUCLEAR_PEAK)
    fluor = _disks(shape, centers, radius, FLUOR_BG, FLUOR_PEAK)

    def _name(channel: str) -> str:
        return sep.join([experiment, channel, well, fov, timepoint]) + ".tif"

    nuclear_path = out_dir / _name(nuclear_token)
    fluor_path = out_dir / _name(fluor_token)
    tifffile.imwrite(nuclear_path, nuclear)
    tifffile.imwrite(fluor_path, fluor)
    return nuclear_path, fluor_path
