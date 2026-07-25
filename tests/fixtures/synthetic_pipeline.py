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
# Cytoplasm: a larger disk concentric with each nucleus, so seeded watershed has
# a cell body to grow the nuclear seeds into.
CYTO_RADIUS = 22
CYTO_BG = 120.0
CYTO_PEAK = 1400.0
# Phase / brightfield: cells are *darker* than the field, the way a transmitted
# -light image looks, so the label-free segmenter sees a plausible input.
PHASE_BG = 2200.0
PHASE_PEAK = 700.0

# Channel kinds understood by :func:`write_synthetic_channels`. The kind picks
# the disk radius + intensities; the token (the name in the filename) is chosen
# by the caller.
CHANNEL_KINDS = {
    "nuclear":   (DEFAULT_RADIUS, NUCLEAR_BG, NUCLEAR_PEAK),
    "fluor":     (DEFAULT_RADIUS, FLUOR_BG, FLUOR_PEAK),
    "cytoplasm": (CYTO_RADIUS, CYTO_BG, CYTO_PEAK),
    "phase":     (CYTO_RADIUS, PHASE_BG, PHASE_PEAK),
}


def _disks(shape, centers, radius, bg, peak) -> np.ndarray:
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.full((h, w), float(bg), dtype=np.float32)
    for cy, cx in centers:
        img[((yy - cy) ** 2 + (xx - cx) ** 2) <= radius ** 2] = float(peak)
    return img


def write_synthetic_channels(
    out_dir: Path,
    channels: "dict[str, str]",
    *,
    experiment: str = "exp",
    well: str = "A01",
    fov: str = "1",
    timepoint: str = "0",
    shape=(256, 256),
    centers=DEFAULT_CENTERS,
    sep: str = "_",
) -> "dict[str, Path]":
    """Write one schema-named TIFF per entry of *channels* and return
    ``{token: path}``.

    *channels* maps a channel token (the string that lands in the filename, e.g.
    ``"DAPI"``) to one of the kinds in :data:`CHANNEL_KINDS`. Every channel draws
    the same nuclei centres, so the images are registered with each other the way
    real multi-channel acquisitions are: the cytoplasm and phase kinds simply use
    a larger disk than the nuclear/fluor kinds.

    This is the multi-channel generalisation of :func:`write_synthetic_fov`,
    which stays as the two-channel (nuclear + fluor) shorthand.
    """
    import tifffile

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for token, kind in channels.items():
        try:
            radius, bg, peak = CHANNEL_KINDS[kind]
        except KeyError:
            raise ValueError(
                f"unknown channel kind {kind!r}; expected one of {sorted(CHANNEL_KINDS)}"
            ) from None
        path = out_dir / (sep.join([experiment, token, well, fov, timepoint]) + ".tif")
        tifffile.imwrite(path, _disks(shape, centers, radius, bg, peak))
        written[token] = path
    return written


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


def write_synthetic_well_folder(
    parent: Path,
    *,
    well: str = "A01",
    n_fovs: int = 2,
    timepoint: str = "0",
    nuclear_token: str = "DAPI",
    fluor_token: str = "GFP",
    sep: str = "_",
) -> Path:
    """Write a per-well folder ``parent/<well>/`` with ``n_fovs`` FOVs, each a
    nuclear + fluor TIFF named per the default 5-field schema. Returns the folder.

    Used by the directory-level end-to-end test (``process_well_folder_task``),
    which discovers FOV groups in the folder, segments each, and assembles one
    per-well CSV.
    """
    folder = Path(parent) / well
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(1, n_fovs + 1):
        write_synthetic_fov(
            folder, well=well, fov=str(i), timepoint=timepoint,
            nuclear_token=nuclear_token, fluor_token=fluor_token, sep=sep,
        )
    return folder
