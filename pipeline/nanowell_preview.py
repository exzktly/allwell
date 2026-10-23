"""Test detection: one well at one timepoint, run end to end, so the addresses can be looked at before a plate is.

Qt-free. This is the analysis ``nanowell_task.process_well_zip`` runs for every timepoint, run for one, with
the pieces a person wants to look at kept instead of written: the montage, every site with its address and
state, how the origin was found, the measured overlap against the one a nanowell needs, and a few
rim-suppressed crops beside their segmentation. It writes nothing under the output folder.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from pipeline import nanowell_run as nr
from pipeline import nanowell_task as nt
from util.nanowell_address import coordinate_text

log = logging.getLogger(__name__)


@dataclass
class PreviewResult:
    status: str                                   # a ``nanowell_run`` status, or ``"error"``
    reason: str = ""
    well: str = ""
    timepoint: str = ""
    montage: np.ndarray | None = None             # uint8
    um_per_px: float | None = None                # one montage pixel
    origin_um: tuple[float, float] = (0.0, 0.0)   # the well-frame position of montage pixel (0, 0)
    sites: list[dict] = field(default_factory=list)
    origin_method: str | None = None
    origin_agreement: str | None = None
    pitch_um: float | None = None
    angle_deg: float | None = None
    overlap_um: tuple = (None, None)
    overlap_needed_um: float | None = None
    diameter_um: float | None = None              # the nanowell diameter the run measured and sized everything from
    tile_count: int = 0
    crops: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: Whether cells were counted. ``False`` when the segmenter could not be loaded (Cellpose not installed or not
    #: importable): the nanowells, their addresses and states stand, and no site carries a count.
    counted: bool = True

    def counts(self) -> dict[str, int]:
        """Sites per state, with ``fused`` counted beside the states (it is a flag on a measured site)."""
        out: dict[str, int] = {}
        for site in self.sites:
            # Without counting, a seen site says so rather than adding to the sites "measured" with cells counted.
            # Only then: a fused site is measured with no count in an ordinary preview too, and stays "measured".
            state = "not counted" if not self.counted and site["state"] == "measured" else site["state"]
            out[state] = out.get(state, 0) + 1
            if site["fused"]:
                out["fused"] = out.get("fused", 0) + 1
        return out


def _missing_import(exc: BaseException) -> str | None:
    """Why the segmenter could not be loaded at all, as text, when that is what *exc* is; otherwise ``None``.

    The chain is walked for an ``ImportError`` (``pipeline.cellpose`` itself missing from a feature-subset build, or
    an older one raising ``RuntimeError`` from the import) or for ``pipeline.cellpose.CellposeUnavailable``, marked by
    its ``segmenter_unavailable`` attribute: Cellpose or Torch failing to import for any reason — a Torch library
    that will not load raises ``OSError``, which was reported as a measuring failure that drew no nanowell at all —
    or its model failing to build. A segmenter that loaded and then failed on the image is none of these, and is
    still a measuring failure.
    """
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if getattr(exc, "segmenter_unavailable", False):
            cause = exc.__cause__
            return str(exc) if cause is None else f"{type(cause).__name__}: {cause}"
        if isinstance(exc, ImportError):
            return f"{type(exc).__name__}: {exc}"
        exc = exc.__cause__ or exc.__context__
    return None


def _tile_segmented(owner: str, measured, images: dict, located, template, segment, px: float):
    """``(rim-suppressed tile, labels)`` for *owner* exactly as the run made them: every site the tile owns, at the
    level the run brought it to, divided by the template, and the segmenter called once on the whole tile.

    The crops are cut from this rather than segmented on their own, because Cellpose scales each image by its own
    brightest and darkest pixels: a crop holding one nanowell is scaled differently from the tile holding a hundred,
    so its outlines were not the run's, and a crop could outline a cell the run did not count (tracker L3)."""
    from pipeline import nanowell_measure as nm
    from pipeline.nanowell_detect import SiteGeometry

    left, top = located.positions[owner]
    local = [SiteGeometry(i, (row["well_x_um"] - left, row["well_y_um"] - top), row["radius_um"], row["geometry"])
             for i, row in enumerate(measured.sites) if row["owner"] == owner and i in measured.levels]
    fit, _too_large = nm.fits_template(template, local, pixel_size_um=px)
    rim = nm.rim_suppress(images[owner], px, template, fit, levels=measured.levels)
    return rim, nm.segment_tile(rim, segment)


def _crop_for(site: dict, tile, located, px: float, half_um: float) -> dict | None:
    """A rim-suppressed crop of one site and the run's segmentation of it, cut from its owner tile's *tile*
    (:func:`_tile_segmented`), with the tile's scale as the segmenter normalised it (``scale``)."""
    from pipeline import nanowell_measure as nm

    owner = site["owner"]
    if owner is None or tile is None:
        return None
    rim, labels = tile
    left, top = located.positions[owner]
    cx, cy = (site["well_x_um"] - left) / px - 0.5, (site["well_y_um"] - top) / px - 0.5
    half = int(np.ceil(half_um / px))
    x0, y0 = max(int(round(cx)) - half, 0), max(int(round(cy)) - half, 0)
    window = rim[y0:y0 + 2 * half + 1, x0:x0 + 2 * half + 1]
    if window.size == 0:
        return None
    # The tile's own 1st-99th percentiles, which Cellpose scales the whole tile by: drawn on them, a crop shows the
    # contrast the segmenter saw, where a crop stretched to its own range would make every nanowell look alike.
    finite = rim[np.isfinite(rim)]
    scale = tuple(float(v) for v in np.percentile(finite, nm.SEGMENTER_PERCENTILES)) if finite.size else None
    return {"label": site["address"], "n_cells": site["n_cells"], "rim": window.astype(np.float32),
            "labels": labels[y0:y0 + 2 * half + 1, x0:x0 + 2 * half + 1], "scale": scale}


def preview_timepoint(zip_path, well: str, timepoint: str, settings: nr.NanowellSettings, *, grouper, parse, schema,
                      sep: str, phase_token: str, scratch: Path, segmenter=None, n_crops: int = 4,
                      pattern: dict | None = None) -> PreviewResult:
    """Locate, measure and describe one (well, timepoint). Never raises on bad data: a refusal is the result's status."""
    from pipeline import nanowell_measure as nm
    from pipeline.io import extract_zip
    from util.acquisition_record import read_record_file

    zip_path = Path(zip_path)
    result = PreviewResult("error", well=well, timepoint=timepoint)
    try:
        record = nt._placing_record(read_record_file(zip_path.parent / "acquisition.json"), well, pattern)
    except nt.NanowellInputError as exc:
        result.reason = str(exc)
        return result
    if record is None and not pattern:
        result.reason = f"There is no acquisition record next to {zip_path.name}, and no tile grid was given."
        return result
    groups, needed = grouper(zip_path, phase_token, [], scratch, schema=schema, sep=sep)
    index = nt._tile_index(groups, parse, schema, sep)
    described = record is None
    if described:                                       # placed from the described grid, as the run would be
        record = nt._pattern_record(well, zip_path, index, pattern)
    wanted = {Path(nuclear).name for (tp, _fov), (nuclear, _fl) in index.items() if tp == timepoint}
    if not wanted:
        result.reason = f"{zip_path.name} holds no {phase_token} image at timepoint {timepoint!r}."
        return result
    extract_zip(zip_path, scratch, members_to_extract=wanted & set(needed))
    images = {fov: nt._read_tif(nuclear) for (tp, fov), (nuclear, _fl) in index.items() if tp == timepoint}
    try:
        manual = nt._well_manual_anchors(zip_path.parent, well)[0].get(timepoint)   # as the run would use it
    except nt.NanowellInputError as exc:
        result.reason = str(exc)
        return result
    located = nr.locate_timepoint(timepoint, record, well, images, settings,
                                  expected_shape=tuple(pattern["grid"]) if described else None,
                                  manual_anchor_um=manual)
    result.status, result.reason = located.status, located.reason
    result.tile_count = len(images)
    # Everything below is sized from the diameter the run measured, as the run sizes it: the overlap a nanowell
    # needs, the crops and the roster's coverage test read the format's otherwise (review F21a).
    if located.diameter_um is not None:
        settings = replace(settings, diameter_override_um=located.diameter_um)
    result.diameter_um = settings.diameter_um
    plan = located.plan
    if plan is not None:
        result.overlap_um = tuple(plan.overlap_um)
    result.overlap_needed_um = settings.diameter_um + 2 * settings.margin
    if located.montage is not None:
        result.montage, result.um_per_px = located.montage.image, located.montage.um_per_px
        result.origin_um = tuple(located.montage.origin_um)
    if located.fit is not None:
        result.pitch_um, result.angle_deg = located.fit.pitch, located.fit.angle_deg
    result.origin_method, result.origin_agreement = located.origin_method, located.origin_agreement
    if located.status not in (nr.OK, nr.UNADDRESSED) or (located.boundary is None and located.array_sites is None):
        return result
    px = located.pixel_size_um
    segment = segmenter or nm.cellpose_segmenter(settings.cell_diameter_um / px, model_type=settings.cellpose_model)
    missing = None
    try:
        radius, own = nr.own_array_model(located, settings)
        try:
            measured = nr.measure_timepoint(located, images, settings, radius, roster=own, segmenter=segment)
        except Exception as exc:
            missing = _missing_import(exc)
            if missing is None:
                raise
            # Finding the nanowells needs no segmenter, only counting the cells in them does: measure again without
            # counting, so the array is still shown, and say why no site has a count.
            log.warning("Test detection: the segmenter could not be loaded (%s); measuring without counting", missing)
            measured = nr.measure_timepoint(located, images, settings, radius, roster=own, count_cells=False)
            result.counted, segment = False, None
    except Exception as exc:          # the location above is still worth showing; the reason goes with it (X27)
        log.warning("Test detection: measuring %s %s failed", well, timepoint, exc_info=True)
        result.notes = [f"Measuring failed ({type(exc).__name__}: {exc}); the lattice and addresses above stand."]
        return result
    result.notes = list(measured.notes)
    if not result.counted:
        result.notes.insert(0, f"Cells were not counted: Cellpose could not be loaded ({missing}). The nanowells, "
                               "their addresses and states are found without it; a run needs it to count cells, so "
                               "install it (see requirements) before starting one.")
    if not settings.infer:
        # What one timepoint shows of the rim, not what a run will keep (tracker X35a-R5): a run decides it over
        # every timepoint, and a dataset analysed before keeps the roster it was analysed with.
        result.notes.append("The nanowells at the rim are the ones this timepoint's image supports; a run decides them "
                            "over all of the well's timepoints, and adding to an analysed dataset keeps its roster")
    for row in measured.sites:
        result.sites.append({**row, "address": coordinate_text(row["nw_q"], row["nw_r"])})
    if segment is not None:
        result.crops = _sample_crops(result.sites, measured, images, located, segment, px,
                                     0.75 * settings.diameter_um, n_crops)
    return result


def _sample_crops(sites: list, measured, images: dict, located, segment, px: float, half_um: float,
                  n_crops: int) -> list:
    """Up to *n_crops* crops: the most loaded measured sites, then one empty one, each cut from its owner tile
    segmented as the run segments it (:func:`_tile_segmented`)."""
    template = measured.template.get("_object")
    ranked = sorted((s for s in sites if s["state"] == "measured" and (s["n_cells"] or 0) > 0 and not s["fused"]),
                    key=lambda s: -(s["n_cells"] or 0))
    empty = next((s for s in sites if s["state"] == "empty" and not s["fused"]), None)
    wanted = ranked[:max(n_crops - 1, 0)] + ([empty] if empty is not None and n_crops else [])
    crops: dict = {}                               # id(site) -> its crop, so they keep the order chosen
    for owner in dict.fromkeys(s["owner"] for s in wanted):
        if owner is None or template is None or owner not in images:
            continue
        # One tile segmented at a time and dropped once its crops are cut: a full sensor's tile and labels are a
        # couple of hundred megabytes.
        tile = _tile_segmented(owner, measured, images, located, template, segment, px)
        for site in (s for s in wanted if s["owner"] == owner):
            crops[id(site)] = _crop_for(site, tile, located, px, half_um)
        del tile
    return [crops[id(s)] for s in wanted if crops.get(id(s)) is not None]
