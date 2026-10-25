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
    # Both from the x pixel size, as the pipeline places tiles (``nanowell_run.locate_timepoint``).
    width = (size.get("x") or 0) * (px.get("x") or 0) or None
    height = (size.get("y") or 0) * (px.get("x") or 0) or None
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


_IMAGE_SUFFIXES = (".tif", ".tiff")


def _member_for(names, data, well: str, timepoint: str, tile: str) -> str | None:
    """The input archive member holding the phase image of *tile* at *timepoint*, by the run's schema.

    Only TIFF members are judged — an archive also carries ``.DS_Store``, ``__MACOSX/`` AppleDouble sidecars
    (``._<name>``, the same stem as the image) and directory entries, none of which is an image (CLAUDE.md
    §7.1). A name with more tokens than the schema matches on its leading ones, as the pipeline's
    ``parse_filename`` reads it.
    """
    fields = [f for f in data.schema.split(":") if f]
    sep = data.separator or "_"
    want = {"channel": data.phase_token.lower(), "fov": tile.lower(), "timepoint": timepoint.lower(),
            "well": well.lower()}
    for name in names:
        base = Path(name).name
        if ("__MACOSX/" in name or base.startswith("._") or name.endswith("/")
                or not base.lower().endswith(_IMAGE_SUFFIXES)):
            continue
        parts = base.rsplit(".", 1)[0].split(sep)
        if len(parts) < len(fields):
            continue
        tokens = {f: p.lower() for f, p in zip(fields, parts)}
        if all(tokens[k] == v for k, v in want.items() if k in tokens):
            return name
    return None


def _cut(image: np.ndarray, cx: float, cy: float, half: float) -> np.ndarray | None:
    h, w = image.shape[:2]
    x0, x1 = int(math.floor(cx - half)), int(math.ceil(cx + half))
    y0, y1 = int(math.floor(cy - half)), int(math.ceil(cy + half))
    if x1 <= 0 or y1 <= 0 or x0 >= w or y0 >= h:
        return None
    # A copy, not a view: a view keeps the whole tile alive for as long as the crop is cached.
    return np.array(image[max(y0, 0):min(y1, h), max(x0, 0):min(x1, w)], copy=True)


def site_crops(data, metrics, site, *, pad: float = 1.4) -> dict:
    """``timepoint -> Crop`` for the nanowell *site* ``(well, q, r)``, over every timepoint of its well."""
    well, q, r = site
    return crops_for_sites(data, metrics, [(well, q, r)], pad=pad).get((well, q, r), {})


def crops_for_sites(data, metrics, sites, *, pad: float = 1.4) -> dict:
    """``(well, q, r) -> {timepoint: Crop}`` for every nanowell in *sites*, each over every timepoint of its well.

    The same crops :func:`site_crops` cuts one nanowell at a time, read in one pass: each well's input archive is
    opened once, and the rows are taken in (timepoint, owner tile) order so an owner tile shared by several of
    the nanowells is decoded once and only one tile is held at a time. The Extremes view cuts up to two dozen
    nanowells over every timepoint, which one site at a time would decode the same tiles over and over.
    """
    import tifffile

    wanted = {(str(w), int(q), int(r)) for w, q, r in sites}
    out: dict = {site: {} for site in wanted}
    if not wanted or metrics is None or not len(metrics):
        return out
    keys = list(zip(metrics["well"], metrics["nw_q"], metrics["nw_r"]))
    picked = metrics[[(w, q, r) in wanted for w, q, r in keys]]
    for well, rows in picked.groupby("well", sort=True):
        records = rows.sort_values("tp_order").to_dict("records")
        # Timepoint first, then owner tile: rows that share a tile at a timepoint come one after another.
        records.sort(key=lambda row: (row["tp_order"], str(row.get("owner") or "")))
        _cut_well(data, str(well), records, pad, out, tifffile)
    return out


def _cut_well(data, well: str, records, pad: float, out: dict, tifffile) -> None:
    """Cut *records* (one well's rows, in timepoint and owner order) into *out*."""
    zf, why_not = None, "the input archive is not beside this dataset"
    archive = Path(data.in_dir) / f"{well}.zip" if data.in_dir is not None else None
    if archive is not None and archive.is_file():
        try:
            zf = zipfile.ZipFile(archive)
        except (OSError, zipfile.BadZipFile) as exc:
            log.warning("Nanowells: %s could not be opened for crops: %s", archive, exc)
            why_not = f"{archive.name} could not be read ({exc})"
    tile_held: list = [None, None]           # [(timepoint, owner), decoded tile or None]: the one tile held
    montage_held: list = [None, None]        # [timepoint, (Montage | None, why)]
    try:
        names = zf.namelist() if zf is not None else []
        for row in records:
            tp = row["timepoint"]
            site = (well, int(row["nw_q"]), int(row["nw_r"]))
            crops = out[site]
            if not (isinstance(row.get("well_x_um"), float) and math.isfinite(row["well_x_um"])):
                crops[tp] = Crop(None, "", f"No position at {tp} ({row.get('state')}).")
                continue
            half_um = pad * float(row.get("radius_um") or 50.0)
            crop, owner = None, row.get("owner")
            owned = isinstance(owner, str) and owner != ""
            if zf is not None and owned:
                if tile_held[0] != (tp, owner):
                    tile_held[:] = [(tp, owner), _owner_tile(zf, names, data, well, tp, owner, tifffile)]
                crop = _tile_crop(tile_held[1], data, row, owner, half_um)
            if crop is None:
                if montage_held[0] != tp:
                    montage_held[:] = [tp, read_montage(data, well, tp)]
                montage, why = montage_held[1]
                if montage is None:
                    crops[tp] = Crop(None, "", why)
                    continue
                cx = (row["well_x_um"] - montage.origin_um[0]) / montage.um_per_px
                cy = (row["well_y_um"] - montage.origin_um[1]) / montage.um_per_px
                image = _cut(montage.image, cx, cy, half_um / montage.um_per_px)
                note = ("from the montage: " + (why_not if zf is None else
                                                "the nanowell has no owner tile (it is fused or shared)"
                                                if not owned else "the owner tile's image could not be found"))
                crop = Crop(image, "montage", note) if image is not None else Crop(None, "", "Off the montage.")
            crops[tp] = crop
    finally:
        if zf is not None:
            zf.close()


def _owner_tile(zf, names, data, well, tp, owner, tifffile) -> np.ndarray | None:
    """The phase image of tile *owner* at *tp*, as the input archive holds it; ``None`` when it cannot be found
    or read."""
    member = _member_for(names, data, well, tp, owner)
    if member is None:
        return None
    try:
        return np.asarray(tifffile.imread(io.BytesIO(zf.read(member))))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, tifffile.TiffFileError) as exc:
        log.warning("Nanowells: could not read %s for a crop: %s", member, exc)
        return None


def _tile_crop(image, data, row, owner, half_um) -> Crop | None:
    if image is None:
        return None
    tp = row["timepoint"]
    block = (data.grids.get(row["well"]) or {}).get(tp) or {}
    tile = next((t for t in block.get("tiles") or [] if str(t.get("tile")) == owner), None)
    px = ((data.acquisition or {}).get(tp) or {}).get("pixel_size_um") or {}
    if tile is None or not px.get("x"):
        return None
    # The pipeline places tiles and sites with the x pixel size on both axes (``nanowell_run.locate_timepoint``),
    # so the crop does too: a y size that differed would put the crop off the nanowell the run measured.
    scale = float(px["x"])
    cx = (row["well_x_um"] - float(tile["left_um"])) / scale
    cy = (row["well_y_um"] - float(tile["top_um"])) / scale
    cut = _cut(image, cx, cy, half_um / scale)
    return Crop(cut, "tile", f"{owner}, full resolution") if cut is not None else None
