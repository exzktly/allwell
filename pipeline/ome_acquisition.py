"""What an OME-TIFF's XML states about each tile it holds, for the acquisition record (tracker I5).

Read per ``(Image index, TheT)``: a multi-image file has one ``<Image>`` per stage position and numbers each
one's planes from ``TheT="0"``; a single-image file packs every position into one ``<Image>``'s T axis, so
its frame index is the ``TheT``. Within one key the stage does not move between a plane's channels and
Z-slices, so the first plane carrying a position is the tile's.

Units are honoured, never assumed. A ``PositionX``/``PositionY`` is a stage coordinate only with a length unit
on both axes (OME's default for those is the reference frame, which has no scale); a ``PhysicalSizeX``/``Y``
defaults to µm, as the OME schema says. Anything unreadable is ``None`` — the record then says it does not
know, rather than carrying a number that placement would trust.

Qt-free.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from pipeline.converter_record import TileSource


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _um(value, unit, default_unit: str | None) -> float | None:
    """*value* in µm, or ``None`` when it is not a number or its unit is not a stated length."""
    from pipeline.ome_explode import _unit_to_um

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    factor = _unit_to_um(unit or default_unit)
    return None if factor is None else number * factor


def _pixels(image):
    return next((el for el in image.iter() if _local(el.tag) == "Pixels"), None)


def _image_calibration(pixels) -> tuple[tuple[float, float] | None, tuple[int, int] | None]:
    """``(pixel size in µm, (width, height) in px)`` of one ``<Pixels>``, each ``None`` when not stated."""
    if pixels is None:
        return None, None
    px = (_um(pixels.get("PhysicalSizeX"), pixels.get("PhysicalSizeXUnit"), "µm"),
          _um(pixels.get("PhysicalSizeY"), pixels.get("PhysicalSizeYUnit"), "µm"))
    try:
        size = (int(pixels.get("SizeX")), int(pixels.get("SizeY")))
    except (TypeError, ValueError):
        size = None
    pixel_size = px if all(v is not None and v > 0 for v in px) else None
    return pixel_size, size if size is not None and min(size) > 0 else None


def _stages(pixels) -> dict[int, tuple[float, float]]:
    """``TheT -> (x, y)`` in µm, for the planes whose position is in a stated length unit."""
    out: dict[int, tuple[float, float]] = {}
    for plane in pixels.iter() if pixels is not None else ():
        if _local(plane.tag) != "Plane":
            continue
        try:
            the_t = int(plane.get("TheT") or 0)
        except ValueError:
            continue
        if the_t in out:
            continue
        x = _um(plane.get("PositionX"), plane.get("PositionXUnit"), None)
        y = _um(plane.get("PositionY"), plane.get("PositionYUnit"), None)
        if x is not None and y is not None:
            out[the_t] = (x, y)
    return out


def ome_tile_sources(ome_xml: str | None) -> dict[tuple[int, int], TileSource]:
    """``{(image index, TheT): TileSource}`` for every plane key the XML states anything about.

    A key the XML does not mention is absent; :func:`tile_source` answers for it with an all-unknown source.
    """
    if not ome_xml:
        return {}
    try:
        root = ET.fromstring(ome_xml)
    except ET.ParseError:
        return {}
    out = {}
    for index, image in enumerate(el for el in root if _local(el.tag) == "Image"):
        pixels = _pixels(image)
        pixel_size, tile_size = _image_calibration(pixels)
        stages = _stages(pixels)
        try:
            size_t = max(1, int(pixels.get("SizeT") or 1)) if pixels is not None else 1
        except ValueError:
            size_t = 1
        for the_t in range(max(size_t, max(stages, default=-1) + 1)):
            out[(index, the_t)] = TileSource(stages.get(the_t), pixel_size, tile_size)
    return out


UNKNOWN = TileSource(None, None, None)


def tile_source(sources: dict, image: int, the_t: int) -> TileSource:
    """The source of one plane key, or an all-unknown one when the XML says nothing about it."""
    return sources.get((image, the_t), UNKNOWN)
