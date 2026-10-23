"""Test detection: one well at one timepoint, run end to end, so the addresses can be looked at before a plate is.

Qt-free. This is the analysis ``nanowell_task.process_well_zip`` runs for every timepoint, run for one, with
the pieces a person wants to look at kept instead of written: the montage, every site with its address and
state, how the origin was found, the measured overlap against the one a nanowell needs, and a few
rim-suppressed crops beside their segmentation. It writes nothing under the output folder.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
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
    """The import failure behind *exc*, as text, when the segmenter could not be loaded at all; otherwise ``None``.

    ``pipeline.cellpose`` raises ``RuntimeError`` *from* the ``ImportError``, so the chain is walked: a missing
    package and a broken install both end in an import failure, while a segmenter that loaded and then failed on
    the image does not, and is still a measuring failure.
    """
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, ImportError):
            return f"{type(exc).__name__}: {exc}"
        exc = exc.__cause__ or exc.__context__
    return None


def _crop_for(site: dict, images: dict, located, template, segment, px: float, half_um: float,
              level: tuple | None = None) -> dict | None:
    """A rim-suppressed crop of one site and its segmentation, from the tile that owns it."""
    from pipeline import nanowell_measure as nm
    from pipeline.nanowell_detect import SiteGeometry

    owner = site["owner"]
    if owner is None or template is None or owner not in images:
        return None
    left, top = located.positions[owner]
    local = SiteGeometry(0, (site["well_x_um"] - left, site["well_y_um"] - top), site["radius_um"], site["geometry"])
    fit, too_large = nm.fits_template(template, [local], pixel_size_um=px)
    if too_large:
        return None
    # The site's own level, as the run suppressed it (``nanowell_run.Measured.levels``).
    rim = nm.rim_suppress(images[owner], px, template, fit, levels=None if level is None else {0: level})
    cx, cy = local.centre_um[0] / px - 0.5, local.centre_um[1] / px - 0.5
    half = int(np.ceil(half_um / px))
    x0, y0 = max(int(round(cx)) - half, 0), max(int(round(cy)) - half, 0)
    window = rim[y0:y0 + 2 * half + 1, x0:x0 + 2 * half + 1]
    if window.size == 0:
        return None
    labels = nm.segment_tile(window, segment)
    return {"label": site["address"], "n_cells": site["n_cells"], "rim": window.astype(np.float32), "labels": labels}


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
    segment = segmenter or nm.cellpose_segmenter(settings.cell_diameter_um / px)
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
    index_of = {}                                  # a result site -> its index in the timepoint's sites
    for i, row in enumerate(measured.sites):
        result.sites.append({**row, "address": coordinate_text(row["nw_q"], row["nw_r"])})
        index_of[id(result.sites[-1])] = i
    template = measured.template.get("_object")
    ranked = sorted((s for s in result.sites if s["state"] == "measured" and (s["n_cells"] or 0) > 0 and not s["fused"]),
                    key=lambda s: -(s["n_cells"] or 0))
    chosen = ranked[:max(n_crops - 1, 0)]
    empty = next((s for s in result.sites if s["state"] == "empty" and not s["fused"]), None)
    for site in (chosen + ([empty] if empty is not None and n_crops else [])) if segment is not None else []:
        level = measured.levels.get(index_of.get(id(site)))
        crop = _crop_for(site, images, located, template, segment, px, 0.75 * settings.diameter_um, level)
        if crop is not None:
            result.crops.append(crop)
    return result
