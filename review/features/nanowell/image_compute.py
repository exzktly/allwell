"""The pictures behind the numbers: a well's montage and tiles (Array QC, V7a) and a nanowell's crops (V3c).

**Montage.** The run stores one downsampled 8-bit phase montage per (well, timepoint) in ``<well>_out.zip``
(role ``montage``, ``<well>_<timepoint>_montage.png``); the grid record gives its origin and scale in the
well frame (``montage_origin_um``, ``montage_um_per_px``) and every tile's placement — left, top, how it was
placed (``source``) and the registration residual. Tile sizes come from the sidecar's acquisition block.

**Crops.** A nanowell is cut from its **owner tile** at each timepoint, from the input archive the run read
(``in/<well>.zip``), at full resolution; the tile-local centre is the site's well-frame position less the
tile's placement, over the pixel size. When the input archive is not beside the dataset (an output folder
copied alone) the crop is cut from the montage instead, at its lower resolution, and says so. A timepoint
whose crop cannot be cut at all says why — never a blank square presented as a picture.

Qt-free; reads files, so the page calls it on a worker.
"""

from __future__ import annotations

import io
import logging
import math
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger("review")


@dataclass(frozen=True)
class TileBox:
    tile: str
    left_um: float
    top_um: float
    width_um: float | None
    height_um: float | None
    source: str
    residual_um: float | None


@dataclass
class Montage:
    image: np.ndarray                      # 8-bit, rows × columns
    origin_um: tuple[float, float]         # the well-frame position of the image's top-left corner
    um_per_px: float

    def extent(self) -> tuple[float, float, float, float]:
        """``(left, right, bottom, top)`` in well µm, for ``imshow(extent=…)`` with y down."""
        h, w = self.image.shape[:2]
        x0, y0 = self.origin_um
        return x0, x0 + w * self.um_per_px, y0 + h * self.um_per_px, y0


def _png(data: bytes) -> np.ndarray:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        return np.asarray(im.convert("L"))


def read_montage(data, well: str, timepoint: str) -> tuple[Montage | None, str]:
    """The montage of *well* at *timepoint* and ``""``, or ``(None, why not)``."""
    block = (data.grids.get(well) or {}).get(timepoint) or {}
    origin, scale = block.get("montage_origin_um"), block.get("montage_um_per_px")
    if not origin or not scale:
        return None, f"The grid record has no montage for {well} at {timepoint} ({block.get('status', 'no record')})."
    if data.out_dir is None:
        return None, "The output folder is not known."
    archive = Path(data.out_dir) / f"{well}_out.zip"
    member = f"{well}_{timepoint}_montage.png"
    try:
        with zipfile.ZipFile(archive) as zf:
            image = _png(zf.read(member))
    except FileNotFoundError:
        return None, f"{archive.name} is not in the output folder."
    except KeyError:
        return None, f"{archive.name} holds no {member}."
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        return None, f"The montage could not be read ({exc})."
    return Montage(image, (float(origin[0]), float(origin[1])), float(scale)), ""


def tile_boxes(data, well: str, timepoint: str) -> list[TileBox]:
    """Each tile's placement in the well frame, as the grid record states it."""
    block = (data.grids.get(well) or {}).get(timepoint) or {}
    acquisition = (data.acquisition or {}).get(timepoint) or {}
    px, size = acquisition.get("pixel_size_um") or {}, acquisition.get("tile_size_px") or {}
    width = (size.get("x") or 0) * (px.get("x") or 0) or None
    height = (size.get("y") or 0) * (px.get("y") or 0) or None
    out = []
    for tile in block.get("tiles") or []:
        if not isinstance(tile, dict) or tile.get("left_um") is None or tile.get("top_um") is None:
            continue
        out.append(TileBox(str(tile.get("tile")), float(tile["left_um"]), float(tile["top_um"]), width, height,
                           str(tile.get("source") or ""), tile.get("residual_um")))
    return out


@dataclass
class Crop:
    image: np.ndarray | None
    source: str                            # "tile" (full resolution) or "montage"
    note: str                              # why there is no image, or what the image is


def _member_for(names, data, timepoint: str, tile: str) -> str | None:
    """The input archive member holding the phase image of *tile* at *timepoint*, by the run's schema."""
    fields = [f for f in data.schema.split(":") if f]
    sep = data.separator or "_"
    phase = data.phase_token.lower()
    want = {"channel": phase, "fov": tile.lower(), "timepoint": timepoint.lower()}
    for name in names:
        stem = Path(name).name.rsplit(".", 1)[0]
        parts = stem.split(sep)
        if len(parts) != len(fields):
            continue
        tokens = {f: p.lower() for f, p in zip(fields, parts)}
        if all(tokens.get(k) == v for k, v in want.items()):
            return name
    return None


def _cut(image: np.ndarray, cx: float, cy: float, half: float) -> np.ndarray | None:
    h, w = image.shape[:2]
    x0, x1 = int(math.floor(cx - half)), int(math.ceil(cx + half))
    y0, y1 = int(math.floor(cy - half)), int(math.ceil(cy + half))
    if x1 <= 0 or y1 <= 0 or x0 >= w or y0 >= h:
        return None
    return np.asarray(image[max(y0, 0):min(y1, h), max(x0, 0):min(x1, w)])


def site_crops(data, metrics, site, *, pad: float = 1.4) -> dict:
    """``timepoint -> Crop`` for the nanowell *site* ``(well, q, r)``, over every timepoint of its well."""
    import tifffile

    well, q, r = site
    rows = metrics[(metrics["well"] == well) & (metrics["nw_q"] == q) & (metrics["nw_r"] == r)]
    rows = rows.sort_values("tp_order")
    archive = None
    if data.in_dir is not None and (Path(data.in_dir) / f"{well}.zip").is_file():
        archive = Path(data.in_dir) / f"{well}.zip"
    out = {}
    zf = zipfile.ZipFile(archive) if archive is not None else None
    try:
        names = zf.namelist() if zf is not None else []
        for row in rows.to_dict("records"):
            tp = row["timepoint"]
            if not (isinstance(row.get("well_x_um"), float) and math.isfinite(row["well_x_um"])):
                out[tp] = Crop(None, "", f"No position at {tp} ({row.get('state')}).")
                continue
            half_um = pad * float(row.get("radius_um") or 50.0)
            crop = None
            if zf is not None and row.get("owner"):
                crop = _tile_crop(zf, names, data, tp, str(row["owner"]), row, half_um, tifffile)
            if crop is None:
                montage, why = read_montage(data, well, tp)
                if montage is None:
                    out[tp] = Crop(None, "", why)
                    continue
                cx = (row["well_x_um"] - montage.origin_um[0]) / montage.um_per_px
                cy = (row["well_y_um"] - montage.origin_um[1]) / montage.um_per_px
                image = _cut(montage.image, cx, cy, half_um / montage.um_per_px)
                note = ("from the montage: the input archive is not beside this dataset" if zf is None
                        else "from the montage: the owner tile's image could not be found")
                crop = Crop(image, "montage", note) if image is not None else Crop(None, "", "Off the montage.")
            out[tp] = crop
    finally:
        if zf is not None:
            zf.close()
    return out


def _tile_crop(zf, names, data, tp, owner, row, half_um, tifffile) -> Crop | None:
    block = (data.grids.get(row["well"]) or {}).get(tp) or {}
    tile = next((t for t in block.get("tiles") or [] if str(t.get("tile")) == owner), None)
    px = ((data.acquisition or {}).get(tp) or {}).get("pixel_size_um") or {}
    member = _member_for(names, data, tp, owner)
    if tile is None or not px.get("x") or member is None:
        return None
    try:
        image = np.asarray(tifffile.imread(io.BytesIO(zf.read(member))))
    except (OSError, ValueError, KeyError) as exc:
        log.warning("Nanowells: could not read %s for a crop: %s", member, exc)
        return None
    sx, sy = float(px["x"]), float(px.get("y") or px["x"])
    cx = (row["well_x_um"] - float(tile["left_um"])) / sx
    cy = (row["well_y_um"] - float(tile["top_um"])) / sy
    cut = _cut(image, cx, cy, half_um / sx)
    return Crop(cut, "tile", f"{owner}, full resolution") if cut is not None else None
