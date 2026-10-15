"""Test detection: one well at one timepoint, run end to end, so the addresses can be looked at before a plate is.

Qt-free. This is the analysis ``nanowell_task.process_well_zip`` runs for every timepoint, run for one, with
the pieces a person wants to look at kept instead of written: the montage, every site with its address and
state, how the origin was found, the measured overlap against the one a nanowell needs, and a few
rim-suppressed crops beside their segmentation. It writes nothing under the output folder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pipeline import nanowell_run as nr
from pipeline import nanowell_task as nt


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

    def counts(self) -> dict[str, int]:
        """Sites per state, with ``fused`` counted beside the states (it is a flag on a measured site)."""
        out: dict[str, int] = {}
        for site in self.sites:
            out[site["state"]] = out.get(site["state"], 0) + 1
            if site["fused"]:
                out["fused"] = out.get("fused", 0) + 1
        return out


def _crop_for(site: dict, images: dict, located, template, segment, px: float, half_um: float) -> dict | None:
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
    rim = nm.rim_suppress(images[owner], px, template, fit)
    cx, cy = local.centre_um[0] / px - 0.5, local.centre_um[1] / px - 0.5
    half = int(np.ceil(half_um / px))
    x0, y0 = max(int(round(cx)) - half, 0), max(int(round(cy)) - half, 0)
    window = rim[y0:y0 + 2 * half + 1, x0:x0 + 2 * half + 1]
    if window.size == 0:
        return None
    labels = nm.segment_tile(window, segment)
    return {"label": site["address"], "n_cells": site["n_cells"], "rim": window.astype(np.float32), "labels": labels}


def preview_timepoint(zip_path, well: str, timepoint: str, settings: nr.NanowellSettings, *, grouper, parse, schema,
                      sep: str, phase_token: str, scratch: Path, segmenter=None, n_crops: int = 4) -> PreviewResult:
    """Locate, measure and describe one (well, timepoint). Never raises on bad data: a refusal is the result's status."""
    from pipeline import nanowell_measure as nm
    from pipeline.io import extract_zip
    from util.acquisition_record import read_record_file

    zip_path = Path(zip_path)
    result = PreviewResult("error", well=well, timepoint=timepoint)
    record = read_record_file(zip_path.parent / "acquisition.json")
    if record is None:
        result.reason = f"There is no acquisition record next to {zip_path.name}."
        return result
    groups, needed = grouper(zip_path, phase_token, [], scratch, schema=schema, sep=sep)
    index = nt._tile_index(groups, parse, schema, sep)
    wanted = {Path(nuclear).name for (tp, _fov), (nuclear, _fl) in index.items() if tp == timepoint}
    if not wanted:
        result.reason = f"{zip_path.name} holds no {phase_token} image at timepoint {timepoint!r}."
        return result
    extract_zip(zip_path, scratch, members_to_extract=wanted & set(needed))
    images = {fov: nt._read_tif(nuclear) for (tp, fov), (nuclear, _fl) in index.items() if tp == timepoint}
    located = nr.locate_timepoint(timepoint, record, well, images, settings)
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
    if located.status not in (nr.OK, nr.UNADDRESSED) or located.boundary is None:
        return result
    px = located.pixel_size_um
    segment = segmenter or nm.cellpose_segmenter(settings.cell_diameter_um / px)
    measured = nr.measure_timepoint(located, images, settings, located.boundary["radius_um"], segmenter=segment)
    result.notes = list(measured.notes)
    for row in measured.sites:
        q, r = row["nw_q"], row["nw_r"]
        result.sites.append({**row, "address": None if q is None else f"{q},{r}"})
    template = measured.template.get("_object")
    ranked = sorted((s for s in result.sites if s["state"] == "measured" and (s["n_cells"] or 0) > 0 and not s["fused"]),
                    key=lambda s: -(s["n_cells"] or 0))
    chosen = ranked[:max(n_crops - 1, 0)]
    empty = next((s for s in result.sites if s["state"] == "empty" and not s["fused"]), None)
    for site in chosen + ([empty] if empty is not None and n_crops else []):
        crop = _crop_for(site, images, located, template, segment, px, 0.75 * settings.diameter_um)
        if crop is not None:
            result.crops.append(crop)
    return result
