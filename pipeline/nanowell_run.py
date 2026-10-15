"""One well at one timepoint: tiles in, addressed nanowell rows out (tracker §3.1).

Qt-free; the task body the pipeline runs in nanowell mode in place of the per-field loop. It
is the chain the earlier sections built, in order, with every stage's refusal kept as a named
status instead of an exception, so a bad timepoint costs its rows and not the well:

1. :func:`locate_timepoint` places the tiles from the acquisition record, registers them,
   builds the montage, finds the lattice and the sites, resolves the origin (fiducial, then
   array centre, then a plate-rigid prediction handed in), checks the square's orientation
   and the plate format, and indexes the sites. Nothing here reads a tile twice at full
   resolution except the registration, which is the only step that has to.
2. :func:`measure_timepoint` takes the roster the well's shared array radius allows, fits each
   site on its owner tile at full resolution, classifies it (visible, imaged, fused), and
   measures its contents (``nanowell_measure``).

Positions are in micrometres in the registration's common frame (the "well frame"), where pixel
``i`` of a tile at ``left`` covers ``[i, i + 1) * pixel_size + left``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

#: The plate formats the pipeline knows: (lattice pitch, nanowell diameter), micrometres.
PLATE_FORMATS = {"100um": (172.0, 100.0), "50um": (86.0, 50.0)}

OK = "ok"
INCOMPLETE_GRID = "incomplete_grid"
BAD_GRID = "bad_grid"
NO_RECORD = "no_record"
NO_LATTICE = "no_lattice"
FORMAT_MISMATCH = "format_mismatch"
ORIENTATION_REFUSED = "orientation_refused"
UNADDRESSED = "unaddressed"
NO_IMAGES = "no_images"
#: Statuses after which nothing downstream of them could run: the timepoint writes no site rows.
TERMINAL_WITHOUT_ROWS = (INCOMPLETE_GRID, BAD_GRID, NO_RECORD, NO_LATTICE, FORMAT_MISMATCH,
                         ORIENTATION_REFUSED, NO_IMAGES)

#: How far a fiducial's score must clear the runner-up's to anchor an address (assumed; Q-row in the tracker).
FIDUCIAL_MARGIN = 0.3


@dataclass(frozen=True)
class NanowellSettings:
    plate_format: str                   # a key of PLATE_FORMATS
    margin_um: float | None = None      # ownership margin around a disk; None = a tenth of the diameter
    cell_diameter_um: float = 15.0      # for the segmenter's scale
    square_edge_deg: float = 0.0        # the central square's edge angle to a lattice row (Q11, assumed)

    @property
    def pitch_um(self) -> float:
        return PLATE_FORMATS[self.plate_format][0]

    @property
    def diameter_um(self) -> float:
        return PLATE_FORMATS[self.plate_format][1]

    @property
    def margin(self) -> float:
        return self.diameter_um / 10 if self.margin_um is None else self.margin_um

    def key(self) -> dict:
        """Everything here changes the output, so all of it joins the reuse key."""
        return {"plate_format": self.plate_format, "margin_um": self.margin,
                "cell_diameter_um": self.cell_diameter_um, "square_edge_deg": self.square_edge_deg}


@dataclass
class Located:
    """What the first stage learned about one timepoint; enough to measure it later, or to say why not."""
    timepoint: str
    status: str
    reason: str = ""
    grid: dict = field(default_factory=dict)             # the _grid.json block for this timepoint
    plan: object = None                                   # tile_grid.GridPlan
    positions: dict = field(default_factory=dict)         # tile id -> registered (left, top)
    pixel_size_um: float | None = None
    montage: object = None
    fit: object = None                                    # LatticeFit, montage frame
    origin_montage: tuple[float, float] | None = None
    origin_method: str | None = None
    origin_agreement: str | None = None
    boundary: object = None                               # ArrayBoundary, common frame
    detected_common: np.ndarray | None = None             # detected centres, common frame
    indexed: tuple = ()
    mirrored: bool = False
    stage_offset_um: tuple[float, float] | None = None    # the common frame's place in stage coordinates


def stage_offset(record: dict, well: str, timepoint: str, positions: dict) -> tuple[float, float] | None:
    """Where the registration's common frame sits in stage coordinates: the median of stage minus registered position.

    ``None`` unless every tile of the (well, timepoint) has a stage position. The stage and image axes
    are taken to be parallel (a stage axis running against the image's would need a sign the record does
    not carry: tracker R8), so this is a translation only; a rotation of the plate is not in it, it is in
    the origins that are compared across timepoints.
    """
    offsets = []
    for tile in record["tiles"]:
        if tile["well"] != well or tile["timepoint"] != timepoint:
            continue
        if tile["stage_xy_um"] is None or tile["field"] not in positions:
            return None
        left, top = positions[tile["field"]]
        offsets.append((tile["stage_xy_um"][0] - left, tile["stage_xy_um"][1] - top))
    if not offsets:
        return None
    return float(np.median([o[0] for o in offsets])), float(np.median([o[1] for o in offsets]))


def _tile_fit(image, pixel_size_um: float, settings: NanowellSettings):
    """The lattice fitted to one tile alone (tile-local um), or ``None`` when it has too few sites to fit.

    Registration's lattice-phase constraint needs it; a tile of bare floor or a wholly occluded one
    simply contributes the other two sources.
    """
    from pipeline.nanowell_detect import detect_sites

    found = detect_sites(image, pixel_size_um, settings.diameter_um, expected_pitch_um=settings.pitch_um)
    return found.fit if found.ok else None


def _montage_to_common(point, montage) -> np.ndarray:
    return np.asarray(point, dtype=float) + np.asarray(montage.origin_um, dtype=float)


def _grid_block(plan, registration, extra: dict) -> dict:
    block = {"grid_status": plan.status if plan is not None else None,
             "rows": getattr(plan, "rows", None), "cols": getattr(plan, "cols", None),
             "missing": list(getattr(plan, "missing", ())),
             "overlap_um": list(getattr(plan, "overlap_um", (None, None))),
             "overlap_fraction": list(getattr(plan, "overlap_fraction", (None, None)))}
    if registration is not None:
        block["tiles"] = [{"tile": t.tile_id, "left_um": t.left_um, "top_um": t.top_um, "source": t.source,
                           "residual_um": t.residual_um, "correction_um": t.correction_um}
                          for t in registration.tiles]
        block["registration_warnings"] = list(registration.warnings)
    block.update(extra)
    return block


def locate_timepoint(timepoint: str, record: dict, well: str, images: dict, settings: NanowellSettings, *,
                     plate_rigid=None, expected_shape: tuple[int, int] | None = None) -> Located:
    """Place, register, montage and address one (well, timepoint). Never raises on bad data.

    *images* maps a tile id to its phase image. A tile of the grid with no image is a status of its
    own (``no_images``), not a smaller grid: a missing tile would change which sites it owns.
    """
    from pipeline.nanowell_detect import check_orientation, detect_sites, estimate_lattice_fft, score_fiducial
    from pipeline.nanowell_lattice import index_points, resolve_origin
    from pipeline.nanowell_sites import check_format
    from pipeline.tile_grid import OK as GRID_OK, place_from_record, tiles_from_record
    from pipeline.tile_register import TileObservation, build_montage, register_tiles

    found = tiles_from_record(record, well, timepoint)
    if found is None:
        return Located(timepoint, NO_RECORD, f"The acquisition record has no tiles for {well} at {timepoint}")
    tiles, tile_size, pixel_size, overlap = found
    plan = place_from_record(tiles, tile_size_px=tile_size, pixel_size_um=pixel_size,
                             declared_overlap_percent=overlap, expected_shape=expected_shape)
    if plan.status != GRID_OK:
        status = INCOMPLETE_GRID if plan.status == "incomplete_grid" else BAD_GRID
        return Located(timepoint, status, plan.reason, _grid_block(plan, None, {"status": status}), plan)
    absent = sorted(p.tile_id for p in plan.placements if p.tile_id not in images)
    if absent:
        return Located(timepoint, NO_IMAGES, f"No image for tile(s) {', '.join(absent)}",
                       _grid_block(plan, None, {"status": NO_IMAGES}), plan)
    px = float(pixel_size["x"])
    observations = [TileObservation(p.tile_id, (p.left_um, p.top_um), (p.width_um, p.height_um),
                                    _tile_fit(images[p.tile_id], px, settings))
                    for p in plan.placements]
    registration = register_tiles(observations, images=images, pixel_size_um=px, pitch_um=settings.pitch_um)
    positions = registration.positions()
    montage = build_montage(images, positions, px)
    located = Located(timepoint, OK, "", plan=plan, positions=positions, pixel_size_um=px, montage=montage)
    located.stage_offset_um = stage_offset(record, well, timepoint, positions)

    def refuse(status, reason, **extra):
        located.status, located.reason = status, reason
        located.grid = _grid_block(plan, registration, {"status": status, "reason": reason, **extra})
        return located

    estimate = estimate_lattice_fft(montage.image, montage.um_per_px, expected_pitch_um=settings.pitch_um)
    if not estimate.ok:
        return refuse(NO_LATTICE, estimate.reason)
    fmt = check_format(estimate.pitch_um, settings.diameter_um)
    if fmt.status == "format_mismatch":
        return refuse(FORMAT_MISMATCH, fmt.reason if hasattr(fmt, "reason") else
                      f"The lattice pitch {estimate.pitch_um:.1f} um is not the {settings.plate_format} format's")
    detection = detect_sites(montage.image, montage.um_per_px, settings.diameter_um,
                             expected_pitch_um=estimate.pitch_um)
    if not detection.ok:
        return refuse(NO_LATTICE, detection.reason)
    candidate, scores = score_fiducial(montage.image, montage.um_per_px, settings.diameter_um,
                                       detection.points_um, centre_um=detection.boundary_centre_um,
                                       search_radius_um=2.2 * settings.pitch_um)
    if scores:
        square = max(scores, key=lambda s: s.coherence)
        orientation = check_orientation(detection.fit, square.orientation_deg)
        if not orientation.ok:
            return refuse(ORIENTATION_REFUSED, orientation.reason if hasattr(orientation, "reason") else
                          "The square's edges disagree with the fitted lattice axis")
    resolved = resolve_origin(detection.fit, fiducial=candidate, array_centre=detection.boundary_centre_um,
                              plate_rigid=plate_rigid, fiducial_margin=FIDUCIAL_MARGIN)
    located.fit = detection.fit
    located.mirrored = bool(detection.fit.mirrored)
    located.detected_common = _montage_to_common(detection.points_um, montage)
    located.boundary = None if detection.boundary_centre_um is None else {
        "centre_um": tuple(_montage_to_common(detection.boundary_centre_um, montage)),
        "radius_um": detection.boundary_radius_um}
    if resolved.status != "anchored":
        located.status, located.reason = UNADDRESSED, resolved.reason
        located.grid = _grid_block(plan, registration, _lattice_block(located, estimate, UNADDRESSED, resolved.reason))
        return located
    located.origin_montage, located.origin_method = resolved.origin_xy, resolved.method
    located.origin_agreement = resolved.agreement
    located.indexed = index_points(detection.points_um, detection.fit, resolved.origin_xy)
    located.grid = _grid_block(plan, registration, _lattice_block(located, estimate, OK, resolved.reason))
    return located


def _lattice_block(located: Located, estimate, status: str, reason: str) -> dict:
    fit = located.fit
    origin_stage = None
    if located.origin_montage is not None and located.stage_offset_um is not None:
        common = _montage_to_common(located.origin_montage, located.montage)
        origin_stage = [float(common[0] + located.stage_offset_um[0]), float(common[1] + located.stage_offset_um[1])]
    return {"status": status, "reason": reason, "lattice_kind": fit.kind, "pitch_um": fit.pitch,
            "stage_offset_um": None if located.stage_offset_um is None else list(located.stage_offset_um),
            "origin_stage_um": origin_stage, "montage_origin_um": list(located.montage.origin_um),
            "angle_deg": fit.angle_deg, "mirrored": located.mirrored,
            "origin_method": located.origin_method, "origin_agreement": located.origin_agreement,
            "array_boundary": located.boundary, "montage_um_per_px": located.montage.um_per_px,
            "n_detected": 0 if located.detected_common is None else len(located.detected_common)}


def hex_ring(q: int | None, r: int | None) -> int | None:
    """Hex distance from the centre in axial coordinates; ``None`` for an unaddressed site."""
    if q is None or r is None:
        return None
    return (abs(q) + abs(r) + abs(q + r)) // 2


# ── stage two: every roster site, measured ───────────────────────────────────
@dataclass
class Measured:
    timepoint: str
    sites: list = field(default_factory=list)            # one dict per roster site (nanowells/<well>.csv rows)
    cells: dict = field(default_factory=dict)            # per-cell columns
    template: dict = field(default_factory=dict)         # which template, from where, how many sites
    notes: list = field(default_factory=list)            # findings that are not errors


def _roster_positions(located: Located, radius_um: float):
    """Roster addresses and their lattice positions in the montage frame."""
    from pipeline.nanowell_lattice import roster

    fit = located.fit
    addresses = roster(fit, located.origin_montage, radius_um)
    basis = np.column_stack((fit.a1, fit.a2))
    points = np.asarray(located.origin_montage) + np.array([basis @ qr for qr in addresses])
    return addresses, points


def _initial_sites(located: Located, points_montage: np.ndarray, pitch_um: float):
    """Each roster site's starting centre (common frame): the detected one if it is near, else the lattice's."""
    common = points_montage + np.asarray(located.montage.origin_um)
    detected = located.detected_common
    out, was_detected = common.copy(), np.zeros(len(common), dtype=bool)
    if detected is not None and len(detected):
        for k, point in enumerate(common):
            d = np.linalg.norm(detected - point, axis=1)
            if d.min() <= pitch_um / 4:
                out[k], was_detected[k] = detected[int(np.argmin(d))], True
    return out, was_detected


def _fit_geometry(image, px: float, centres_local: np.ndarray, indices, diameter_um: float, offset):
    """``SiteGeometry`` per site, fitted on *image* and returned in the common frame with global indices.

    A fit needs a nanowell at least ``MIN_REFINE_RADIUS_PX`` across; at a coarser pixel size every site
    is ``predicted`` at the nominal radius, which the row says.
    """
    from pipeline.nanowell_detect import PREDICTED, SiteGeometry, refine_sites

    try:
        fitted = refine_sites(image, px, centres_local, diameter_um)
    except ValueError:
        fitted = [SiteGeometry(i, tuple(c), diameter_um / 2, PREDICTED) for i, c in enumerate(centres_local)]
    shift = np.asarray(offset, dtype=float)
    return [replace(g, index=int(index), centre_um=(g.centre_um[0] + shift[0], g.centre_um[1] + shift[1]))
            for g, index in zip(fitted, indices)]


def _owned_geometries(located: Located, images: dict, settings: NanowellSettings, centres, report):
    """``(geoms by global index, indices by owner)``: owner tile at full resolution, fused/unimaged on the montage."""
    montage, px = located.montage, located.pixel_size_um
    by_tile: dict[str | None, list[int]] = {}
    for i, owner in enumerate(report.owners):
        by_tile.setdefault(owner, []).append(i)
    geoms: dict[int, object] = {}
    for owner, indices in by_tile.items():
        if owner is None:
            local = centres[indices] - np.asarray(montage.origin_um)
            fitted = _fit_geometry(montage.image, montage.um_per_px, local, indices, settings.diameter_um,
                                   montage.origin_um)
        else:
            left, top = located.positions[owner]
            fitted = _fit_geometry(images[owner], px, centres[indices] - (left, top), indices,
                                   settings.diameter_um, (left, top))
        geoms.update({g.index: g for g in fitted})
    return geoms, by_tile


def _visibility_and_coverage(located: Located, geoms: dict, array_radius_um: float):
    """Visibility on the montage (where a bubble is visible as a whole); coverage from the registered tiles."""
    from pipeline.nanowell_sites import bubble_mask, site_coverage, site_visibility

    montage, boundary = located.montage, located.boundary
    ordered = [geoms[i] for i in range(len(geoms))]
    on_montage = [replace(g, centre_um=(g.centre_um[0] - montage.origin_um[0], g.centre_um[1] - montage.origin_um[1]))
                  for g in ordered]
    mask = None
    if boundary is not None and located.fit is not None:
        mask = bubble_mask(montage.image, montage.um_per_px, located.fit,
                           np.asarray(boundary["centre_um"]) - montage.origin_um, boundary["radius_um"])
    visibility = {v.index: v for v in site_visibility(montage.image, montage.um_per_px, on_montage, mask=mask)}
    rects = [(located.positions[p.tile_id][0], located.positions[p.tile_id][1],
              located.positions[p.tile_id][0] + p.width_um, located.positions[p.tile_id][1] + p.height_um)
             for p in located.plan.placements]
    coverage = {c.index: c for c in site_coverage(
        ordered, rects, array_centre_um=None if boundary is None else boundary["centre_um"],
        array_radius_um=array_radius_um)}
    return visibility, coverage


def _pooled_template(located: Located, images: dict, settings: NanowellSettings, geoms, by_tile, visibility,
                     coverage, earliest):
    """One template for the timepoint from every owner tile's visible, fully imaged, fitted sites.

    Returns ``(template or None, the site indices whose crops were the least textured: the empty ones)``.
    """
    from pipeline import nanowell_measure as nm
    from pipeline.nanowell_detect import FITTED

    px = located.pixel_size_um
    half = nm.template_half(settings.diameter_um / 2 / px)
    crops, crop_sites = [], []
    for owner, indices in by_tile.items():
        if owner is None:
            continue
        left, top = located.positions[owner]
        usable = [i for i in indices if (visibility[i].visible_fraction or 0) >= 0.9
                  and coverage[i].imaged_fraction >= 0.99 and geoms[i].geometry == FITTED]
        if usable:
            local = np.array([(geoms[i].centre_um[0] - left, geoms[i].centre_um[1] - top) for i in usable])
            crops.append(nm.site_crops(images[owner], px, local, half))
            crop_sites += usable
    template = None
    if crops:
        template = nm.build_template(np.concatenate(crops),
                                     interior_radius_px=nm.SUPPRESSION_RADIUS * settings.diameter_um / 2 / px,
                                     earliest=earliest)
    template = template or earliest
    if template is None or template.source == nm.SOURCE_EARLIEST:
        return template, set()
    return template, {crop_sites[k] for k in template.used}


def _tile_contents(owner, indices, located, images, settings, geoms, addresses, template, empties, segment, channels):
    """Counts, confluence, area estimate, fluorescence and focus for the sites one tile owns, and its cell rows."""
    from pipeline import nanowell_measure as nm
    from pipeline.nanowell_sites import rim_focus

    px = located.pixel_size_um
    left, top = located.positions[owner]
    local = [replace(geoms[i], centre_um=(geoms[i].centre_um[0] - left, geoms[i].centre_um[1] - top)) for i in indices]
    fit_sites, too_large = nm.fits_template(template, local, pixel_size_um=px)
    notes = [f"{len(too_large)} site(s) on tile {owner} exceed the template and are unmeasured"] if too_large else []
    if not fit_sites:
        return {}, {}, notes
    rim = nm.rim_suppress(images[owner], px, template, fit_sites)
    labels = nm.segment_tile(rim, segment)
    counts = nm.count_cells(labels, px, fit_sites, owned={g.index for g in fit_sites})
    mine = [g for g in fit_sites if g.index in empties]
    threshold = nm.texture_threshold(rim, px, mine) if mine else None
    confluence = dict(zip((g.index for g in fit_sites), nm.site_confluence(rim, px, fit_sites, threshold)))
    area = nm.area_estimate(labels, px, fit_sites, counts)
    channel_images = {c: img for c, f in (channels or {}).items() if (img := f(owner)) is not None}
    table = nm.cell_table(labels, px, fit_sites, counts, {g.index: addresses[g.index] for g in fit_sites},
                          tile_origin_um=(left, top), intensity=channel_images or None)
    n = len(table["label"])
    table["fov"] = [owner] * n
    for old, new in (("address_q", "nw_q"), ("address_r", "nw_r"), ("x_um", "well_x_um"), ("y_um", "well_y_um")):
        table[new] = table.pop(old)
    located_at = [addresses[i] or (None, None) for i in table["site_index"]]
    table["nw_ring"] = [hex_ring(q, r) for q, r in located_at]
    table["nw_label"] = [None if q is None else f"{q}_{r}" for q, r in located_at]
    fluorescence = {c: {f.index: f for f in nm.site_fluorescence(img, px, fit_sites, {g.index for g in mine})}
                    for c, img in channel_images.items()}
    contents = {g.index: {"n_cells": counts[g.index].n_cells, "confluence": confluence[g.index],
                          "area": area[g.index], "fluor": {c: f[g.index] for c, f in fluorescence.items()},
                          "focus": rim_focus(images[owner], px, g)} for g in fit_sites}
    return contents, table, notes


def _site_row(timepoint, i, address, g, v, c, got, state, owner, detected, fused, channels) -> dict:
    q, r = address if address is not None else (None, None)
    row = {"timepoint": timepoint, "nw_q": q, "nw_r": r, "nw_ring": hex_ring(q, r),
           "well_x_um": g.centre_um[0], "well_y_um": g.centre_um[1], "owner": owner,
           "radius_um": g.radius_um, "geometry": g.geometry, "detected": bool(detected),
           "fused": fused, "imaged_fraction": c.imaged_fraction, "visible_fraction": v.visible_fraction,
           "state": state.state, "partially_visible": state.partially_visible,
           "focus": None if got is None else got["focus"], "n_cells": state.n_cells,
           "confluence": state.confluence,
           "area_est_cells": None if got is None else got["area"].area_count,
           "count_disagreement": None if got is None else got["area"].count_disagreement}
    for channel in (channels or {}):
        f = None if got is None else got["fluor"].get(channel)
        row[f"{channel}_integrated"] = None if f is None else f.integrated
        row[f"{channel}_mean_bg"] = None if f is None else f.mean_minus_background
    return row


def measure_timepoint(located: Located, images: dict, settings: NanowellSettings, array_radius_um: float, *,
                      segmenter=None, channels: dict | None = None, earliest_template=None) -> Measured:
    """Rows for every roster site of a located timepoint, and the cells inside them.

    *array_radius_um* is the well's one shared array-model radius (so every timepoint has the same
    roster). *segmenter* maps a rim-suppressed tile to a label image (default: Cellpose's label-free
    path). *channels* maps a fluorescence channel name to ``f(tile_id) -> image | None``. A timepoint
    that is not ``ok`` (no origin) is measured on the sites actually detected, with no address.
    """
    from pipeline import nanowell_measure as nm
    from pipeline.tile_grid import ownership_report

    px, montage = located.pixel_size_um, located.montage
    addressed = located.status == OK
    if addressed:
        addresses, points_m = _roster_positions(located, array_radius_um)
    else:
        # No origin: only the sites actually detected, with no address (they cannot be joined over time).
        points_m = located.detected_common - np.asarray(montage.origin_um)
        addresses = [None] * len(points_m)
    centres, detected = _initial_sites(located, points_m, settings.pitch_um)
    report = ownership_report(located.plan, centres, settings.diameter_um, settings.margin,
                              positions_um=located.positions)
    fused = set(report.fused_indices)
    out = Measured(located.timepoint)
    geoms, by_tile = _owned_geometries(located, images, settings, centres, report)
    visibility, coverage = _visibility_and_coverage(located, geoms, array_radius_um)
    template, empties = _pooled_template(located, images, settings, geoms, by_tile, visibility, coverage,
                                         earliest_template)
    if template is None:
        out.notes.append("No template could be built (no fully imaged, visible site): contents are undefined")
    else:
        out.template = {"source": template.source, "n_sites": template.n_sites, "candidates": template.candidates,
                        "_object": template}
    segment = segmenter or nm.cellpose_segmenter(settings.cell_diameter_um / px)
    contents: dict[int, dict] = {}
    for owner, indices in by_tile.items():
        if owner is None or template is None:
            continue
        got, table, notes = _tile_contents(owner, indices, located, images, settings, geoms, addresses, template,
                                           empties, segment, channels)
        contents.update(got)
        out.notes += notes
        for name, values in table.items():
            out.cells.setdefault(name, []).extend(values)
    for i, address in enumerate(addresses):
        got = contents.get(i)
        state = nm.site_state(visible_fraction=visibility[i].visible_fraction,
                              imaged_fraction=coverage[i].imaged_fraction,
                              n_cells=None if got is None else got["n_cells"],
                              confluence=None if got is None else got["confluence"], fused=i in fused,
                              addressed=addressed)
        out.sites.append(_site_row(located.timepoint, i, address, geoms[i], visibility[i], coverage[i], got, state,
                                   report.owners[i], detected[i], i in fused, channels))
    return out


# ── a whole well: every timepoint ────────────────────────────────────────────
@dataclass
class WellOutcome:
    well: str
    timepoints: dict = field(default_factory=dict)       # timepoint -> the _grid.json block
    sites: list = field(default_factory=list)            # rows for nanowells/<well>.csv, every timepoint
    cells: dict = field(default_factory=dict)            # per-cell columns, every timepoint
    radius: dict = field(default_factory=dict)           # the shared array radius and each timepoint's deviation
    located: dict = field(default_factory=dict)          # timepoint -> Located (not written; the end-of-run pass reads it)
    notes: list = field(default_factory=list)


def record_digest(record: dict, well: str, timepoint: str) -> str:
    """A short digest of everything the record says about this (well, timepoint): tile positions and calibration.

    A reused timepoint is only still right if this has not changed (the images themselves are not hashed,
    exactly as the per-field path does not hash them: replacing them is what ``--force`` is for).
    """
    import hashlib
    import json

    tiles = sorted((t["field"], t["mosaic_xy_px"], t["stage_xy_um"]) for t in record["tiles"]
                   if t["well"] == well and t["timepoint"] == timepoint)
    meta = record.get("timepoints", {}).get(timepoint, {})
    payload = json.dumps([tiles, meta.get("tile_size_px"), meta.get("pixel_size_um"),
                          meta.get("declared_overlap_percent")], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _images_for(record: dict, well: str, timepoint: str, load_phase) -> dict:
    """Phase image per tile of this (well, timepoint) in the record; a tile that cannot be read is left out."""
    images = {}
    for tile in record["tiles"]:
        if tile["well"] == well and tile["timepoint"] == timepoint:
            image = load_phase(timepoint, tile["field"])
            if image is not None:
                images[tile["field"]] = np.asarray(image, dtype=np.float32)
    return images


def _bounds(located: Located):
    from pipeline.nanowell_lattice import ArrayBoundary

    if located.boundary is None:
        return ArrayBoundary("unresolved", "no boundary")
    return ArrayBoundary("fitted", "", tuple(located.boundary["centre_um"]), located.boundary["radius_um"])


def run_well(well: str, record: dict, timepoints, load_phase, settings: NanowellSettings, *,
             segmenter=None, channels: dict | None = None, plate_rigid: dict | None = None,
             expected_shape=None, fixed_radius_um: float | None = None) -> WellOutcome:
    """Locate every timepoint, agree on one array radius, then measure each against it.

    *load_phase* is ``f(timepoint, tile_id) -> image | None``; *channels* maps a fluorescence name to
    ``f(timepoint, tile_id) -> image | None``. *plate_rigid* maps a timepoint to an ``OriginPrediction``
    in that well's montage frame (from the end-of-run pass; absent on a first run). A timepoint whose
    first stage refuses keeps its status and no rows; the others go on. *fixed_radius_um* is the array
    radius an earlier run of this well already shared across its timepoints: an incremental run adds a
    timepoint to the same roster instead of choosing a new one.
    """
    from pipeline.nanowell_sites import TimepointLattice, check_consistency, shared_array_radius

    outcome = WellOutcome(well)
    timepoints = list(timepoints)
    for tp in timepoints:
        images = _images_for(record, well, tp, load_phase)
        located = locate_timepoint(tp, record, well, images, settings, expected_shape=expected_shape,
                                   plate_rigid=(plate_rigid or {}).get(tp))
        outcome.located[tp] = located
        outcome.timepoints[tp] = dict(located.grid) or {"status": located.status, "reason": located.reason}
        outcome.timepoints[tp]["record_digest"] = record_digest(record, well, tp)
        del images
    anchored = [tp for tp in timepoints if outcome.located[tp].status == OK]
    unaddressed = [tp for tp in timepoints if outcome.located[tp].status == UNADDRESSED]
    shared = shared_array_radius({tp: _bounds(outcome.located[tp]) for tp in anchored + unaddressed},
                                 settings.pitch_um)
    if fixed_radius_um is not None:
        own = {tp: outcome.located[tp].boundary["radius_um"] for tp in anchored + unaddressed
               if outcome.located[tp].boundary is not None}
        shared = replace(shared, radius_um=fixed_radius_um, status="shared",
                         deviations_um={tp: r - fixed_radius_um for tp, r in own.items()},
                         flags={tp: ("radius_disagrees" if abs(r - fixed_radius_um) > settings.pitch_um / 2 else None)
                                for tp, r in own.items()})
    outcome.radius = {"radius_um": shared.radius_um, "status": shared.status,
                      "deviations_um": shared.deviations_um, "flags": shared.flags}
    if shared.radius_um is None:
        for tp in anchored:
            outcome.timepoints[tp].update(status=NO_LATTICE, reason="No timepoint fitted an array boundary, so "
                                                                   "no roster radius could be shared")
        return outcome

    lattices = []
    for tp in anchored:
        located = outcome.located[tp]
        addresses, _ = _roster_positions(located, shared.radius_um)
        lattices.append(TimepointLattice(tp, located.fit.pitch, located.fit.angle_deg, 0.0, frozenset(addresses)))
    reference_angle = lattices[0].angle_deg if lattices else 0.0
    # The plate's rotation between timepoints is only measurable across wells; within one well the angle
    # is not checked (and the grid block says so), so a plate put back rotated is not called inconsistent.
    lattices = [replace(t, plate_rotation_deg=t.angle_deg - reference_angle) for t in lattices]
    consistency = {c.timepoint: c for c in check_consistency(lattices)}

    earliest = None
    for tp in anchored + unaddressed:
        located = outcome.located[tp]
        images = _images_for(record, well, tp, load_phase)
        loaders = {name: (lambda tile, _f=f, _tp=tp: _f(_tp, tile)) for name, f in (channels or {}).items()}
        measured = measure_timepoint(located, images, settings, shared.radius_um, segmenter=segmenter,
                                     channels=loaders, earliest_template=earliest)
        del images
        if earliest is None and measured.template.get("_object") is not None \
                and measured.template["source"] != "earliest":
            earliest = measured.template["_object"]
        block = outcome.timepoints[tp]
        agree = consistency.get(tp)
        block.update(consistency=None if agree is None else agree.status,
                     consistency_detail="" if agree is None else agree.detail, angle_checked=False, radius_flag=shared.flags.get(tp),
                     radius_deviation_um=shared.deviations_um.get(tp), roster_size=len(measured.sites),
                     template={k: v for k, v in measured.template.items() if k != "_object"},
                     notes=list(measured.notes))
        for row in measured.sites:
            row["consistency"] = None if agree is None else agree.status
        outcome.sites.extend(measured.sites)
        for name, values in measured.cells.items():
            outcome.cells.setdefault(name, []).extend(values)
        outcome.cells.setdefault("timepoint", []).extend([tp] * len(measured.cells.get("label", ())))
    return outcome
