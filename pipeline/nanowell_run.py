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
from pathlib import Path

import numpy as np

#: The plate formats the pipeline knows: (lattice pitch, nanowell diameter), micrometres. The pitch is nominal: it
#: seeds :func:`working_pitch`, a lattice search on the tiles from half to twice it, and the pitch measured there
#: (or by the lattice fit) is what every later step uses — the per-tile fits, registration, the fiducial search,
#: site de-duplication and the roster radius. 172 um is what ~1,100 nanowells in a 3.0 mm array imply for the
#: 100 um format, and 143 um what ~1,600 imply for the 50 um format, not the diameter-scaled 86 um (~4,400).
PLATE_FORMATS = {"100um": (172.0, 100.0), "50um": (143.0, 50.0)}

OK = "ok"
INCOMPLETE_GRID = "incomplete_grid"
BAD_GRID = "bad_grid"
NO_RECORD = "no_record"
NO_LATTICE = "no_lattice"
FORMAT_MISMATCH = "format_mismatch"
ORIENTATION_REFUSED = "orientation_refused"
UNADDRESSED = "unaddressed"
NO_IMAGES = "no_images"
#: An inferred lattice of another kind than the well's other timepoints' (tracker X35-R4): its addresses cannot be
#: joined with theirs, so it writes no rows.
LATTICE_KIND_MISMATCH = "lattice_kind_mismatch"
#: Statuses after which nothing downstream of them could run: the timepoint writes no site rows.
TERMINAL_WITHOUT_ROWS = (INCOMPLETE_GRID, BAD_GRID, NO_RECORD, NO_LATTICE, FORMAT_MISMATCH,
                         ORIENTATION_REFUSED, NO_IMAGES, LATTICE_KIND_MISMATCH)

#: How far a fiducial's score must clear the runner-up's to anchor an address (assumed; Q-row in the tracker).
FIDUCIAL_MARGIN = 0.3


#: The plate format whose pitch and diameter the run is given rather than knowing (tracker A7).
CUSTOM_FORMAT = "custom"

#: How the array is found. ``hex_circle``: a matched filter at the format's diameter, a hexagonal lattice and a
#: circular array (the plates this was written for). ``infer`` (tracker X35): the nanowells are found by their
#: contrast alone (light wells, dark lattice) and the lattice kind, pitch, orientation and the array's outline are
#: inferred from where they are (``pipeline.nanowell_infer``); the format's pitch and diameter are then only
#: compared with what is measured, and a disagreement is reported.
GEOMETRY_HEX_CIRCLE = "hex_circle"
GEOMETRY_INFER = "infer"
GEOMETRIES = (GEOMETRY_HEX_CIRCLE, GEOMETRY_INFER)


@dataclass(frozen=True)
class NanowellSettings:
    plate_format: str                   # a key of PLATE_FORMATS, or CUSTOM_FORMAT
    margin_um: float | None = None      # ownership margin around a disk; None = a tenth of the diameter
    cell_diameter_um: float = 15.0      # for the segmenter's scale
    square_edge_deg: float = 0.0        # the central square's edge angle to a lattice row (Q11, assumed)
    occluded_below: float = 0.5         # a site seen less than this is occluded and carries no count
    clipped_below: float = 0.5          # a site imaged less than this is clipped and carries no count
    custom: tuple[float, float] | None = None   # (nominal pitch, diameter) um for CUSTOM_FORMAT
    geometry: str = GEOMETRY_HEX_CIRCLE         # how the array is found and its geometry decided (GEOMETRIES)
    #: The nanowell diameter measured on the image, when an inferred geometry found the setting's wrong by more
    #: than ``nanowell_infer.DIAMETER_AGREEMENT``: sites are then fitted, owned and measured at it. Set per
    #: timepoint by :func:`measure_timepoint`, never by a caller, and not in the reuse key (tracker X35-R3).
    diameter_override_um: float | None = None

    def __post_init__(self):
        if (self.plate_format == CUSTOM_FORMAT) != (self.custom is not None):
            raise ValueError("A custom plate format needs its pitch and diameter, and only a custom one takes them")
        if self.plate_format != CUSTOM_FORMAT and self.plate_format not in PLATE_FORMATS:
            raise ValueError(f"Unknown plate format {self.plate_format!r}")
        if self.geometry not in GEOMETRIES:
            raise ValueError(f"Unknown nanowell geometry {self.geometry!r}; one of {GEOMETRIES}")

    @property
    def infer(self) -> bool:
        return self.geometry == GEOMETRY_INFER

    @property
    def pitch_um(self) -> float:
        return self.custom[0] if self.custom else PLATE_FORMATS[self.plate_format][0]

    @property
    def diameter_um(self) -> float:
        if self.diameter_override_um is not None:
            return self.diameter_override_um
        return self.custom[1] if self.custom else PLATE_FORMATS[self.plate_format][1]

    @property
    def margin(self) -> float:
        return self.diameter_um / 10 if self.margin_um is None else self.margin_um

    def key(self) -> dict:
        """Everything here changes the output, so all of it joins the reuse key."""
        key = {"plate_format": self.plate_format, "margin_um": self.margin,
               "cell_diameter_um": self.cell_diameter_um, "square_edge_deg": self.square_edge_deg,
               "occluded_below": self.occluded_below, "clipped_below": self.clipped_below}
        if self.custom:                     # only then: a known format's key is unchanged, so its runs still reuse
            key.update(pitch_um=self.custom[0], diameter_um=self.custom[1])
        if self.infer:                      # the same: a run on the format's geometry keeps the key it always had
            key["geometry"] = self.geometry
        return key


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
    pitch_um: float | None = None                         # the pitch this timepoint was located with (measured)
    #: Inferred geometry only (``settings.geometry == "infer"``): every lattice site of the array as inferred from the
    #: wells found, montage frame, found or not — the roster of an array that is not a circle (tracker X35).
    array_sites: np.ndarray | None = None
    array_shape: str | None = None                        # "circle" or "polygon"
    diameter_um: float | None = None                      # inferred only: the diameter sites are measured at
    array_found: np.ndarray | None = None                 # inferred only: per array site, a light nanowell was found
    notes: list = field(default_factory=list)             # findings of the locating stage that are not refusals


def stage_offset(record: dict, well: str, timepoint: str, positions: dict,
                 tolerance_um: float = float("inf")) -> tuple[float, float] | None:
    """Where the registration's common frame sits in stage coordinates: the median of stage minus registered position.

    ``None`` unless every tile of the (well, timepoint) has a stage position. The stage and image axes
    are taken to be parallel (a stage axis running against the image's would need a sign the record does
    not carry: tracker R8), so this is a translation only; a rotation of the plate is not in it, it is in
    the origins that are compared across timepoints. ``None`` as well when the tiles disagree about the
    offset by more than *tolerance_um* (a pitch, from the caller): registration moves a tile by up to half a
    pitch, but a stage axis running against the image's disagrees by the size of the grid, and re-anchoring
    on such an offset would be confidently wrong.
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
    median = np.median(np.array(offsets), axis=0)
    if float(np.abs(np.array(offsets) - median).max()) > tolerance_um:
        return None
    return float(median[0]), float(median[1])


def _tile_fit(image, pixel_size_um: float, settings: NanowellSettings, pitch_um: float | None = None,
              kind: str = "auto"):
    """The lattice fitted to one tile alone (tile-local um), or ``None`` when it has too few sites to fit.

    Registration's lattice-phase constraint needs it; a tile of bare floor or a wholly occluded one
    simply contributes the other two sources. *pitch_um* is the working pitch (the nominal one by default).
    """
    from pipeline.nanowell_detect import detect_sites

    if settings.infer:
        from pipeline.nanowell_infer import contrast_fit

        return contrast_fit(image, pixel_size_um, expected_pitch_um=pitch_um, kind=kind)
    found = detect_sites(image, pixel_size_um, settings.diameter_um, expected_pitch_um=pitch_um or settings.pitch_um)
    return found.fit if found.ok else None


#: Tiles nearest the grid's centre whose lattice pitch is measured before anything else uses one.
PITCH_PROBE_TILES = 3


def working_pitch(images: dict, plan, pixel_size_um: float, settings: NanowellSettings) -> tuple[float, str]:
    """The pitch to locate this timepoint with, and where it came from: ``"contrast"``, ``"tiles"`` or ``"nominal"``.

    The format's nominal pitch is a guess at the plate (tracker Q12): seeding the per-tile fits with it when the
    array's real pitch differs makes every tile's fit fail (they keep neighbours within 20 % of the seed), and
    registration then falls back to overlap or recorded positions with no fit at all. So the pitch is first
    measured by the lattice search (half to twice the nominal) on the tiles nearest the grid's centre, and their
    median is used; with none measurable, the nominal pitch, and the grid record says which. An inferred geometry
    (tracker X35) first tries :func:`_contrast_lattice`, which needs no seed at all.
    """
    from pipeline.nanowell_detect import estimate_lattice_fft

    measured = []
    for tile in _centre_tiles(plan):
        estimate = estimate_lattice_fft(images[tile], pixel_size_um, expected_pitch_um=settings.pitch_um)
        if estimate.ok and estimate.pitch_um:
            measured.append(float(estimate.pitch_um))
    return (float(np.median(measured)), "tiles") if measured else (settings.pitch_um, "nominal")


def _centre_tiles(plan) -> list[str]:
    rects = {p.tile_id: (p.left_um + p.width_um / 2, p.top_um + p.height_um / 2) for p in plan.placements}
    cx = float(np.mean([c[0] for c in rects.values()]))
    cy = float(np.mean([c[1] for c in rects.values()]))
    return sorted(rects, key=lambda t: (np.hypot(rects[t][0] - cx, rects[t][1] - cy), t))[:PITCH_PROBE_TILES]


def _contrast_lattice(images: dict, plan, pixel_size_um: float) -> tuple[float, str] | None:
    """``(pitch, kind)`` of the lattices fitted to the centre tiles' wells found by contrast, with no seed at all:
    the median pitch and the most common kind (the nearest tile's on a tie), or ``None`` when no centre tile fits.
    Every tile is then fitted as that kind: a sparse edge tile left to choose can pick the other (review X35-R7)."""
    from collections import Counter

    from pipeline.nanowell_infer import contrast_fit

    fits = [fit for tile in _centre_tiles(plan) if (fit := contrast_fit(images[tile], pixel_size_um))]
    if not fits:
        return None
    counts = Counter(fit.kind for fit in fits)
    kind = next(fit.kind for fit in fits if counts[fit.kind] == max(counts.values()))
    return float(np.median([fit.pitch for fit in fits])), kind


def _montage_to_common(point, montage) -> np.ndarray:
    return np.asarray(point, dtype=float) + np.asarray(montage.origin_um, dtype=float)


def _grid_block(plan, registration, extra: dict, warnings=()) -> dict:
    block = {"grid_status": plan.status if plan is not None else None,
             "rows": getattr(plan, "rows", None), "cols": getattr(plan, "cols", None),
             "missing": list(getattr(plan, "missing", ())),
             "overlap_um": list(getattr(plan, "overlap_um", (None, None))),
             "overlap_fraction": list(getattr(plan, "overlap_fraction", (None, None)))}
    if registration is not None:
        block["tiles"] = [{"tile": t.tile_id, "left_um": t.left_um, "top_um": t.top_um, "source": t.source,
                           "residual_um": t.residual_um, "correction_um": t.correction_um}
                          for t in registration.tiles]
        block["registration_warnings"] = list(registration.warnings) + list(warnings)
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
    probe = _contrast_lattice(images, plan, px) if settings.infer else None
    if probe is not None:
        (pitch, kind), pitch_source = probe, "contrast"
    else:
        (pitch, pitch_source), kind = working_pitch(images, plan, px, settings), "auto"
    observations = [TileObservation(p.tile_id, (p.left_um, p.top_um), (p.width_um, p.height_um),
                                    _tile_fit(images[p.tile_id], px, settings, pitch, kind))
                    for p in plan.placements]
    fitted = sum(o.fit is not None for o in observations)
    warnings = [] if fitted else [
        f"No tile's lattice could be fitted (working pitch {pitch:.1f} um, {pitch_source}): every tile was placed "
        "by overlap correlation or where the acquisition record put it, with no lattice constraint"]
    registration = register_tiles(observations, images=images, pixel_size_um=px, pitch_um=pitch)
    positions = registration.positions()
    montage = build_montage(images, positions, px)
    located = Located(timepoint, OK, "", plan=plan, positions=positions, pixel_size_um=px, montage=montage,
                      pitch_um=pitch)
    located.stage_offset_um = stage_offset(record, well, timepoint, positions, tolerance_um=pitch)
    seed = {"pitch_seed_um": pitch, "pitch_seed_source": pitch_source, "tile_lattice_fits": [fitted, len(observations)]}

    def refuse(status, reason, **extra):
        located.status, located.reason = status, reason
        located.grid = _grid_block(plan, registration, {"status": status, "reason": reason, **seed, **extra},
                                   warnings)
        return located

    diameter = settings.diameter_um
    if settings.infer:
        detection, inferred, seed["geometry"] = _detect_by_contrast(montage, settings)
        if not detection.ok:
            return refuse(NO_LATTICE, detection.reason)
        lattice_pitch = float(detection.fit.pitch)
        diameter = located.diameter_um = seed["geometry"]["diameter_used_um"]
    else:
        estimate = estimate_lattice_fft(montage.image, montage.um_per_px, expected_pitch_um=pitch)
        if not estimate.ok:
            return refuse(NO_LATTICE, estimate.reason)
        lattice_pitch = float(estimate.pitch_um)
    fmt = check_format(lattice_pitch, diameter)
    if fmt.status == "format_mismatch":
        reason = (fmt.reason if hasattr(fmt, "reason") else
                  f"The lattice pitch {lattice_pitch:.1f} um is not the {settings.plate_format} format's")
        if not settings.infer:
            return refuse(FORMAT_MISMATCH, reason)
        # An inferred geometry measured the plate itself: the format is a statement to check, not a reason to
        # write nothing (review X35-R3).
        seed["geometry"]["notes"].append(f"{reason}. The inferred geometry addresses the plate as measured")
    if not settings.infer:
        detection = detect_sites(montage.image, montage.um_per_px, settings.diameter_um,
                                 expected_pitch_um=lattice_pitch)
        if not detection.ok:
            return refuse(NO_LATTICE, detection.reason)
    candidate, scores = score_fiducial(montage.image, montage.um_per_px, diameter,
                                       detection.points_um, centre_um=detection.boundary_centre_um,
                                       search_radius_um=2.2 * lattice_pitch)
    # Only a square that scored as one: with the central square hidden (a bubble over it) the best-scoring site
    # is an ordinary circle, whose "orientation" is noise, and checking against it refused a timepoint the array
    # centre could anchor (roll-up review X21).
    if candidate is not None and scores:
        square = max(scores, key=lambda s: s.coherence)
        orientation = check_orientation(detection.fit, square.orientation_deg,
                                        square_to_axis_deg=settings.square_edge_deg)
        if not orientation.ok:
            return refuse(ORIENTATION_REFUSED, orientation.reason if hasattr(orientation, "reason") else
                          "The square's edges disagree with the fitted lattice axis")
    resolved = resolve_origin(detection.fit, fiducial=candidate, array_centre=detection.boundary_centre_um,
                              plate_rigid=plate_rigid, fiducial_margin=FIDUCIAL_MARGIN)
    located.fit = detection.fit
    located.pitch_um = float(detection.fit.pitch)
    located.mirrored = bool(detection.fit.mirrored)
    located.detected_common = _montage_to_common(detection.points_um, montage)
    # An inferred array that is not a circle has a centre (the middle of its sites) and no radius: no boundary.
    located.boundary = None if detection.boundary_radius_um is None else {
        "centre_um": tuple(_montage_to_common(detection.boundary_centre_um, montage)),
        "radius_um": detection.boundary_radius_um}
    if settings.infer:
        located.array_sites, located.array_shape = inferred.roster_um, inferred.shape
        located.array_found = np.asarray(inferred.found, dtype=bool)
        located.notes = list(seed["geometry"]["notes"])
    if resolved.status != "anchored":
        located.status, located.reason = UNADDRESSED, resolved.reason
        located.grid = _grid_block(plan, registration,
                                   {**seed, **_lattice_block(located, UNADDRESSED, resolved.reason)}, warnings)
        return located
    located.origin_montage, located.origin_method = resolved.origin_xy, resolved.method
    located.origin_agreement = resolved.agreement
    located.indexed = index_points(detection.points_um, detection.fit, resolved.origin_xy)
    located.grid = _grid_block(plan, registration, {**seed, **_lattice_block(located, OK, resolved.reason)},
                               warnings)
    return located


def _detect_by_contrast(montage, settings: NanowellSettings):
    """``(detection, ArrayGeometry or None, geometry block)`` for an inferred geometry: the wells found by contrast on the montage, the
    lattice and array they imply, and how the measured pitch and diameter compare with the settings' (tracker X35).

    The detection is a ``SiteDetection`` so the rest of :func:`locate_timepoint` runs unchanged: its points are the
    wells found, its boundary centre the array's centre (a circle's, or the middle site of any other outline; none
    for an outline the montage cuts) and its radius the circle's. The ``ArrayGeometry`` carries every lattice site
    of the array, which is what the roster of a non-circular array is made from.
    """
    from pipeline.nanowell_detect import SiteDetection
    from pipeline.nanowell_infer import (
        ARRAY_CIRCLE, ARRAY_POLYGON, DIAMETER_AGREEMENT, PITCH_AGREEMENT, compare_given, find_wells_by_contrast,
        infer_array,
    )

    block = {"method": "contrast", "notes": []}
    wells = find_wells_by_contrast(montage.image, montage.um_per_px, covered=montage.covered)
    block["rejected"] = wells.rejected
    if not wells.ok:
        return SiteDetection(False, f"No nanowells were found by their contrast: {wells.reason}"), None, block
    geometry = infer_array(wells.points_um, pixel_size_um=montage.um_per_px, covered=montage.covered,
                           edge_points_um=wells.edge_points_um, diameter_um=wells.diameter_um)
    if not geometry.ok:
        return SiteDetection(False, geometry.reason), None, block
    block.update(geometry.summary())
    block["pitch_check"] = compare_given(geometry.fit.pitch, settings.pitch_um, PITCH_AGREEMENT, "pitch",
                                         "addresses use the measured pitch")
    block["diameter_check"] = compare_given(wells.diameter_um, settings.diameter_um, DIAMETER_AGREEMENT,
                                            "nanowell diameter", "nanowells are fitted, owned and measured at the "
                                            "measured diameter")
    # The diameter sites are measured at: the setting's, unless the image shows it is wrong (review X35-R3).
    block["diameter_used_um"] = (settings.diameter_um if block["diameter_check"]["agrees"] is not False
                                 else float(wells.diameter_um))
    block["notes"] = [c["note"] for c in (block["pitch_check"], block["diameter_check"]) if c["note"]]
    if geometry.missing:
        block["notes"].append(f"{geometry.missing} of the array's {len(geometry.found)} lattice sites showed no "
                              "light nanowell (a bubble, or not detected): each keeps its address and is measured "
                              "as it is seen")
    if geometry.shape == ARRAY_POLYGON and geometry.centre_um is None:
        block["notes"].append("The array runs off the imaged area and is not a circle, so its centre cannot be "
                              "placed from the image: only the central square or the rest of the plate can anchor it")
    radius = geometry.radius_um if geometry.shape == ARRAY_CIRCLE else None
    return SiteDetection(True, "", wells.points_um, None, geometry.fit, geometry.centre_um, radius), geometry, block


def _lattice_block(located: Located, status: str, reason: str) -> dict:
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


def lattice_ring(q: int | None, r: int | None, kind: str | None) -> int | None:
    """Lattice steps from the centre: the hex distance on a hexagonal lattice, ``max(|q|, |r|)`` on a square one
    (the rings ``util.nanowell_address`` labels); ``None`` for an unaddressed site."""
    if q is None or r is None:
        return None
    return max(abs(q), abs(r)) if kind == "square" else hex_ring(q, r)


# ── stage two: every roster site, measured ───────────────────────────────────
@dataclass
class Measured:
    timepoint: str
    sites: list = field(default_factory=list)            # one dict per roster site (nanowells/<well>.csv rows)
    cells: dict = field(default_factory=dict)            # per-cell columns
    template: dict = field(default_factory=dict)         # which template, from where, how many sites
    notes: list = field(default_factory=list)            # findings that are not errors


def _roster_positions(located: Located, radius_um: float | None, sites=None):
    """Roster addresses and their lattice positions in the montage frame: the disk of *radius_um* about the origin,
    or, for an array that is not a circle, the addresses *sites* (the well's shared roster, tracker X35)."""
    from pipeline.nanowell_lattice import roster

    fit = located.fit
    addresses = tuple(sorted(sites)) if sites is not None else roster(fit, located.origin_montage, radius_um)
    basis = np.column_stack((fit.a1, fit.a2))
    points = np.asarray(located.origin_montage) + np.array([basis @ qr for qr in addresses]).reshape(-1, 2)
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
    # A polygon array has no bubble mask: the mask is the lattice signal's absence inside a circle, and visibility
    # already reads a nanowell a bubble hides as occluded from its own brightness (light wells, dark lattice), which
    # is the evidence an inferred geometry rests on. No rendered case shows the mask changing a polygon's states, so
    # it is not added on faith (review X35-R9, declined).
    if array_radius_um is None and boundary is not None:      # an inferred circle: its own (tracker X35)
        array_radius_um = boundary["radius_um"]
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
    template = template or nm.as_fallback(earliest)
    if template is None or template.source == nm.SOURCE_EARLIEST:
        return template, set()
    return template, {crop_sites[k] for k in template.used}


def _tile_contents(owner, indices, located, images, settings, geoms, addresses, template, empties, segment, channels,
                   kind=None):
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
    for channel in (channels or {}):
        table.setdefault(f"mean_{channel}", [None] * n)       # no image for this tile: no value, not a short column
    for old, new in (("address_q", "nw_q"), ("address_r", "nw_r"), ("x_um", "well_x_um"), ("y_um", "well_y_um")):
        table[new] = table.pop(old)
    located_at = [addresses[i] or (None, None) for i in table["site_index"]]
    table["nw_ring"] = [lattice_ring(q, r, kind) for q, r in located_at]
    table["nw_label"] = [None if q is None else f"{q}_{r}" for q, r in located_at]
    fluorescence = {c: {f.index: f for f in nm.site_fluorescence(img, px, fit_sites, {g.index for g in mine})}
                    for c, img in channel_images.items()}
    contents = {g.index: {"n_cells": counts[g.index].n_cells, "confluence": confluence[g.index],
                          "interior_imaged": nm.interior_imaged(images[owner].shape, px, g),
                          "area": area[g.index], "fluor": {c: f[g.index] for c, f in fluorescence.items()},
                          "focus": rim_focus(images[owner], px, g)} for g in fit_sites}
    return contents, table, notes


def _site_row(timepoint, i, address, g, v, c, got, state, owner, detected, fused, channels, kind=None) -> dict:
    q, r = address if address is not None else (None, None)
    row = {"timepoint": timepoint, "nw_q": q, "nw_r": r, "nw_ring": lattice_ring(q, r, kind),
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


def measure_timepoint(located: Located, images: dict, settings: NanowellSettings, array_radius_um: float | None, *,
                      segmenter=None, channels: dict | None = None, earliest_template=None,
                      roster=None) -> Measured:
    """Rows for every roster site of a located timepoint, and the cells inside them.

    *array_radius_um* is the well's one shared array-model radius (so every timepoint has the same
    roster); for an inferred array that is not a circle it is ``None`` and *roster* is the well's shared set of
    addresses instead. *segmenter* maps a rim-suppressed tile to a label image (default: Cellpose's label-free
    path). *channels* maps a fluorescence channel name to ``f(tile_id) -> image | None``. A timepoint
    that is not ``ok`` (no origin) is measured on the sites actually detected, with no address.
    """
    from pipeline import nanowell_measure as nm
    from pipeline.nanowell_detect import FITTED, MIN_REFINE_RADIUS_PX
    from pipeline.nanowell_sites import check_format
    from pipeline.tile_grid import ownership_report

    px, montage = located.pixel_size_um, located.montage
    if located.diameter_um is not None and located.diameter_um != settings.diameter_um:
        settings = replace(settings, diameter_override_um=located.diameter_um)
    addressed = located.status == OK
    if addressed:
        addresses, points_m = _roster_positions(located, array_radius_um, roster)
    else:
        # No origin: only the sites actually detected, with no address (they cannot be joined over time).
        points_m = located.detected_common - np.asarray(montage.origin_um)
        addresses = [None] * len(points_m)
    centres, detected = _initial_sites(located, points_m, located.pitch_um or settings.pitch_um)
    report = ownership_report(located.plan, centres, settings.diameter_um, settings.margin,
                              positions_um=located.positions)
    fused = set(report.fused_indices)
    out = Measured(located.timepoint, notes=list(located.notes))
    kind = located.fit.kind if located.fit is not None else None
    geoms, by_tile = _owned_geometries(located, images, settings, centres, report)
    # The format check's second half, which needs fitted nanowells and so could not run while locating (the montage
    # is too coarse to fit them): the sites fitted here at full resolution against the setting's diameter. A
    # finding, not a refusal, because no real plate has yet calibrated it (roll-up review X25).
    fmt = check_format(located.pitch_um or settings.pitch_um, settings.diameter_um,
                       [g.radius_um for g in geoms.values() if g.geometry == FITTED])
    fine_enough = settings.diameter_um / 2 / px >= MIN_REFINE_RADIUS_PX
    # A fit is kept only within 30 % of the setting's radius, so a setting far off fits nothing at all: at a pixel
    # size that could fit, no fitted site is itself the finding.
    if fmt.status == "format_mismatch" or (fmt.ok is None and geoms and fine_enough):
        out.notes.append(fmt.reason)
    visibility, coverage = _visibility_and_coverage(located, geoms, array_radius_um)
    template, empties = _pooled_template(located, images, settings, geoms, by_tile, visibility, coverage,
                                         earliest_template)
    if template is None:
        out.notes.append("No template could be built (no fully imaged, visible site): contents are undefined")
    else:
        out.template = {"source": template.source, "n_sites": template.n_sites, "candidates": template.candidates,
                        "_object": template}
        if template.source != nm.SOURCE_EARLIEST:                  # its own: what flag_template_drift compares
            out.template["floor_ratio"] = nm.floor_ratio(template)
    segment = segmenter or nm.cellpose_segmenter(settings.cell_diameter_um / px)
    contents: dict[int, dict] = {}
    for owner, indices in by_tile.items():
        if owner is None or template is None:
            continue
        got, table, notes = _tile_contents(owner, indices, located, images, settings, geoms, addresses, template,
                                           empties, segment, channels, kind)
        contents.update(got)
        out.notes += notes
        for name, values in table.items():
            out.cells.setdefault(name, []).extend(values)
    for i, address in enumerate(addresses):
        got = contents.get(i)
        state = nm.site_state(visible_fraction=visibility[i].visible_fraction,
                              imaged_fraction=coverage[i].imaged_fraction,
                              n_cells=None if got is None else got["n_cells"],
                              confluence=None if got is None else got["confluence"],
                              interior_imaged=None if got is None else got["interior_imaged"], fused=i in fused,
                              addressed=addressed, occluded_below=settings.occluded_below,
                              clipped_below=settings.clipped_below)
        out.sites.append(_site_row(located.timepoint, i, address, geoms[i], visibility[i], coverage[i], got, state,
                                   report.owners[i], detected[i], i in fused, channels, kind))
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
    earliest_template: tuple | None = None               # (timepoint, Template): the first own template in time
    roster_change: str = ""                              # a kept roster the new evidence changes (X35a-R1): why


_DRIFT_NOTE = "The empty-nanowell template departs from the earliest timepoint's"


def flag_template_drift(blocks: dict, order) -> None:
    """Compare each timepoint's template with the well's earliest one, in place on the grid blocks (tracker C12).

    *blocks* maps a timepoint to its grid block and *order* is the well's timepoints in time. The earliest is the
    first timepoint in *order* whose block carries a ``floor_ratio`` of its own (one that fell back to an earlier
    template has none), whichever run measured it — kept by an incremental run, restored from a
    checkpoint, or measured now — so a later run never compares with whatever it happened to measure first.
    Every later timepoint with a ratio records ``drift_vs_earliest`` and ``earliest_timepoint``; over
    ``TEMPLATE_DRIFT_LIMIT`` it is ``suspect`` and its notes say so. Recomputed whole on every call, so a kept
    block is never flagged twice or against a stale earliest.
    """
    from pipeline import nanowell_measure as nm

    earliest_tp = earliest = None
    for tp in order:
        block = blocks.get(tp) or {}
        template = block.get("template")
        if not isinstance(template, dict):
            continue
        for key in ("drift_vs_earliest", "earliest_timepoint", "suspect"):
            template.pop(key, None)
        block["notes"] = [n for n in block.get("notes") or [] if not n.startswith(_DRIFT_NOTE)]
        ratio = template.get("floor_ratio")
        if not ratio:
            continue
        if earliest is None:
            earliest_tp, earliest = tp, ratio
            continue
        drift = nm.ratio_drift(ratio, earliest)
        template.update(drift_vs_earliest=drift, earliest_timepoint=earliest_tp)
        if drift is not None and drift > nm.TEMPLATE_DRIFT_LIMIT:
            template["suspect"] = True
            block["notes"].append(
                f"{_DRIFT_NOTE} ({earliest_tp}): its floor against the surround is {ratio / earliest:.2f}x the "
                "earliest's (beyond a factor of two). Most nanowells here may hold cells, so the template may be "
                "cell-coloured and this timepoint's counts and confluence biased")


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


def _shared_radius(located: dict, timepoints, settings: NanowellSettings, fixed_radius_um: float | None):
    """One array radius for the well's roster, from the timepoints' boundaries, or the earlier run's *fixed* one,
    each timepoint's deviation flagged past half the array's measured pitch (not the format's nominal guess)."""
    from pipeline.nanowell_sites import shared_array_radius

    measured = [located[tp].pitch_um for tp in timepoints if located[tp].pitch_um]
    pitch = float(np.median(measured)) if measured else settings.pitch_um
    shared = shared_array_radius({tp: _bounds(located[tp]) for tp in timepoints}, pitch)
    if fixed_radius_um is None:
        return shared
    own = {tp: located[tp].boundary["radius_um"] for tp in timepoints if located[tp].boundary is not None}
    return replace(shared, radius_um=fixed_radius_um, status="shared",
                   deviations_um={tp: r - fixed_radius_um for tp, r in own.items()},
                   flags={tp: ("radius_disagrees" if abs(r - fixed_radius_um) > pitch / 2 else None)
                          for tp, r in own.items()})


def _own_roster(located: Located) -> frozenset:
    """The addresses of an anchored timepoint's own inferred array sites, relative to its origin."""
    from pipeline.nanowell_lattice import index_points

    return frozenset((p.q, p.r) for p in index_points(located.array_sites, located.fit, located.origin_montage)
                     if not p.outlier)


#: A timepoint whose own inferred roster differs from the well's shared one in more than this many sites (or this
#: share of them, if larger) is flagged: a bubble over the array's edge, or a different array.
_ROSTER_TOLERANCE = (2, 0.02)
#: How many lattice steps either way a timepoint's addresses may be moved to overlay its array on the reference's.
_REGISTER_REACH = 3


def _refuse_other_kinds(outcome, candidates, kept_kind: str | None = None) -> list:
    """Timepoints whose inferred lattice kind is not the well's, refused with the reason (review X35-R4, R13).

    The well's kind is *kept_kind* when an earlier run kept timepoints of it (an incremental run joins them by
    address, so they decide), else the most common among *candidates*, the earliest's on a tie: a hexagonal and
    a square address cannot name the same nanowell, so a timepoint of the other kind can neither join the vote on
    the roster nor be joined to the others over time. Returns the timepoints refused."""
    from collections import Counter

    kinds = {tp: outcome.located[tp].fit.kind for tp in candidates}
    counts = Counter(kinds.values())
    if kept_kind is None and len(counts) < 2:
        return []
    well_kind = kept_kind or next(kinds[tp] for tp in candidates if counts[kinds[tp]] == max(counts.values()))
    refused = [tp for tp in candidates if kinds[tp] != well_kind]
    for tp in refused:
        others = ("the timepoints the earlier run kept" if kept_kind else
                  f"the well's other timepoints ({counts[well_kind]} of {len(candidates)})")
        reason = (f"Its nanowells fit a {kinds[tp]} lattice, {others} a {well_kind} one: its addresses could not "
                  "be joined with theirs")
        outcome.located[tp].status, outcome.located[tp].reason = LATTICE_KIND_MISMATCH, reason
        outcome.timepoints[tp].update(status=LATTICE_KIND_MISMATCH, reason=reason)
    return refused


def _shift_origin(located: Located, shift, onto: str) -> None:
    """Move *located*'s addresses by *shift* lattice steps: the same sites, each named ``(q + dq, r + dr)``."""
    from pipeline.nanowell_lattice import index_points

    basis = np.column_stack((located.fit.a1, located.fit.a2))
    located.origin_montage = tuple(np.asarray(located.origin_montage) - basis @ np.asarray(shift, dtype=float))
    located.indexed = index_points(located.detected_common - np.asarray(located.montage.origin_um), located.fit,
                                   located.origin_montage)
    located.grid["origin_stage_um"] = _lattice_block(located, OK, "")["origin_stage_um"]
    located.grid.update(origin_shift=[int(shift[0]), int(shift[1])], origin_registered_to=onto)
    located.notes.append(f"Addresses moved by ({int(shift[0])}, {int(shift[1])}) lattice steps to overlay this "
                         f"timepoint's array on {onto}'s: its own middle site was not the array's (a bubble or the "
                         "image's edge cut its outline short)")


def _lit(located: Located) -> frozenset:
    """The addresses of an anchored timepoint's sites where a whole light nanowell was found, relative to its origin."""
    from pipeline.nanowell_lattice import index_points

    sites = np.asarray(located.array_sites)[np.asarray(located.array_found, dtype=bool)]
    if not len(sites):
        return frozenset()
    return frozenset((p.q, p.r) for p in index_points(sites, located.fit, located.origin_montage) if not p.outlier)


def _axes(kind: str):
    """The lattice's row directions as functions of an address: two on a square lattice, three on a hexagonal one."""
    axes = [("q", lambda a: a[0]), ("r", lambda a: a[1])]
    return axes + [("q+r", lambda a: a[0] + a[1])] if kind == "hex" else axes


def _spans(sites, kind: str) -> tuple:
    """``(first, last)`` row of *sites* along each of the lattice's row directions, counting only rows that hold at
    least ``_MIN_EDGE_GROUP`` sites: a bright speck beside the array is a row of one, and must not decide where the
    array ends (review X35-R12)."""
    from collections import Counter

    out = []
    for _name, f in _axes(kind):
        counts = Counter(f(a) for a in sites)
        rows = [v for v, n in counts.items() if n >= _MIN_EDGE_GROUP] or list(counts)
        out.append((min(rows), max(rows)))
    return tuple(out)


def _step_onto(own, reference, kind: str) -> tuple[tuple[int, int] | None, str]:
    """The step that puts *own* in *reference*'s frame, and ``""``; or ``None`` and why it cannot be told.

    A step is determined along a row direction only where both arrays span the same number of rows: then their
    first rows are the same row. Where the spans differ, one of the two lost a whole edge row (or saw one the other
    did not), nothing in the sites says which edge, and a step chosen by overlap breaks the tie by whichever edge
    the reference happened to lose — the wrong way in about half of such wells (review X35-R11)."""
    mine, theirs = _spans(own, kind), _spans(reference, kind)
    for (name, _f), (a0, a1), (b0, b1) in zip(_axes(kind), mine, theirs):
        if a1 - a0 != b1 - b0:
            return None, (f"its array spans {a1 - a0 + 1} rows along {name} and the reference's {b1 - b0 + 1}: one of "
                          "them lost or gained an edge row, and which edge cannot be told")
    step = (theirs[0][0] - mine[0][0], theirs[1][0] - mine[1][0])
    if kind == "hex" and theirs[2][0] - mine[2][0] != step[0] + step[1]:
        return None, "its rows along q, r and q+r do not agree on one step"
    if max(abs(step[0]), abs(step[1])) > _REGISTER_REACH:
        return None, f"the step its rows imply, {step}, is more than {_REGISTER_REACH} rows"
    return step, ""


def _register_to_reference(located: dict, anchored, fixed_roster) -> tuple[str | None, dict, dict]:
    """Put every anchored timepoint's addresses in one frame (review X35-R1, X35-R11).

    Returns ``(reference, shifts, ambiguous)``: the reference timepoint, the step each moved timepoint was moved by,
    and the reason for each timepoint whose step cannot be told.

    Only a polygon anchored on its own middle site (``array_centre``) can need moving: that middle is the mean of
    the sites it saw, and a bubble or an edge cutting its outline short moves it by a step, so its addresses would
    name its neighbours' nanowells and the trajectories would join the wrong ones. A circle's centre is fitted to
    its rim and does not move that way, and the central square or the plate is evidence of its own; neither is
    moved, and a disagreement with one is reported as a roster mismatch. Each polygon is put in the reference's
    frame by :func:`_step_onto`, from the rows its lit sites span; where that cannot be told it is not addressed
    rather than addressed by a guess, and the end-of-run pass may still anchor it from the plate. The reference is
    the earlier run's *fixed_roster* (``None`` is returned for it), else an anchored timepoint that is not such a
    polygon, else the polygon that spans the most rows — rows count only with ``_MIN_EDGE_GROUP`` sites, so a speck
    beside the array does not widen it, and nanowells do not appear, so the widest saw the most of the array; when
    a majority lost a row it is still the one that did not that sets the frame — then the one whose width the most
    others share, the one that saw the most sites, and the earliest."""
    lit = {tp: _lit(located[tp]) for tp in anchored}
    movable = [tp for tp in anchored if located[tp].origin_method == "array_centre"
               and located[tp].array_shape == "polygon" and lit[tp]]
    kind = next((located[tp].fit.kind for tp in anchored), "hex")
    if fixed_roster:
        reference, onto, reference_tp = frozenset(tuple(map(int, a)) for a in fixed_roster), "the earlier run's roster", None
    elif anchored:
        fixed_frames = [tp for tp in anchored if tp not in movable and lit[tp]]
        widths = {tp: tuple(b - a for a, b in _spans(lit[tp], kind)) for tp in movable}
        agreement = {tp: sum(widths[o] == widths[tp] for o in movable) for tp in movable}
        reference_tp = (max(fixed_frames, key=lambda tp: (len(lit[tp]), -anchored.index(tp))) if fixed_frames else
                        max(movable, key=lambda tp: (sum(widths[tp]), agreement[tp], len(lit[tp]),
                                                     -anchored.index(tp))) if movable
                        else None)
        if reference_tp is None:
            return None, {}, {}
        reference, onto = lit[reference_tp], reference_tp
    else:
        return None, {}, {}
    shifts, ambiguous = {}, {}
    for tp in movable:
        if tp == reference_tp:
            continue
        step, why = _step_onto(lit[tp], reference, kind)
        if step is None:
            ambiguous[tp] = f"Its addresses cannot be put in {onto}'s frame: {why}, so they would be a guess"
        elif step != (0, 0):
            _shift_origin(located[tp], step, onto)
            shifts[tp] = step
    return reference_tp, shifts, ambiguous


def _neighbours(kind: str):
    return ((1, 0), (-1, 0), (0, 1), (0, -1)) + (((1, -1), (-1, 1)) if kind == "hex" else ())


def _groups(sites, kind: str) -> list[set]:
    """Lattice-connected groups of *sites* (neighbours one step apart)."""
    left, out = set(sites), []
    while left:
        group, todo = set(), [left.pop()]
        while todo:
            a = todo.pop()
            group.add(a)
            for dq, dr in _neighbours(kind):
                b = (a[0] + dq, a[1] + dr)
                if b in left:
                    left.discard(b)
                    todo.append(b)
        out.append(group)
    return out


#: Sites lit at one timepoint only join the roster as a lattice-connected group of at least this many touching the
#: sites the timepoints agree on: a whole edge row a bubble hid elsewhere, not a bright speck off the array.
_MIN_EDGE_GROUP = 3


def _agreed_sites(located: dict, anchored, own: dict, kind: str) -> tuple[frozenset, dict]:
    """The roster several timepoints support (review X35-R12), and per timepoint the sites it alone proposed.

    A site is in it when at least two timepoints' own arrays hold it (an unimaged rim site of a circle, a site
    under a bubble at one of them), or when it was lit at one timepoint and belongs to a group of at least
    ``_MIN_EDGE_GROUP`` connected sites lit there that touches the agreed ones (an edge row a bubble hid at the
    others: a nanowell does not appear, but a bright speck can). Anything else one timepoint alone proposed is left
    out and named on that timepoint, so a single false positive cannot add sites to every timepoint for good."""
    from collections import Counter

    held = Counter(a for sites in own.values() for a in sites)
    agreed = {a for a, n in held.items() if n >= 2}
    lit = {tp: _lit(located[tp]) for tp in anchored}
    seen_once = Counter(a for sites in lit.values() for a in sites)
    once = {a for a, n in seen_once.items() if n == 1} - agreed
    for group in _groups(once, kind):
        touches = any((a[0] + dq, a[1] + dr) in agreed for a in group for dq, dr in _neighbours(kind))
        if len(group) >= _MIN_EDGE_GROUP and touches:
            agreed |= group
    agreed = frozenset(agreed)
    return agreed, {tp: own[tp] - agreed for tp in anchored}


def _shared_roster(located: dict, anchored, fixed_roster) -> tuple[frozenset, dict, dict]:
    """One roster for an inferred array (tracker X35), once every anchored timepoint is in one frame
    (:func:`_register_to_reference`): the earlier run's *fixed_roster*, so an incremental run adds a timepoint to the
    same roster; one timepoint's own array when it is the only one; otherwise the sites the timepoints support
    together (:func:`_agreed_sites`) — a site hidden at most timepoints but seen at one is kept and written as
    occluded where hidden, and a site only one timepoint proposed without such support is named there and left
    out. Returns the roster, the block the grid record keeps (``array_radius``, with no radius: a set of sites,
    not a disk about the origin, because a disk's tolerance adds sites the plate does not have and fits no other
    outline), and each anchored timepoint's own address set (what the consistency check compares)."""
    own = {tp: _own_roster(located[tp]) for tp in anchored}
    # An empty one is an earlier run that anchored nothing (a well re-anchored from the rest of the plate): it
    # shared no roster, so this run's anchored timepoints decide one rather than measuring none.
    if fixed_roster:
        shared, status = frozenset(tuple(map(int, a)) for a in fixed_roster), "shared"
    elif len(anchored) == 1:
        shared, status = own[anchored[0]], "shared"
    elif anchored:
        shared, alone = _agreed_sites(located, anchored, own, located[anchored[0]].fit.kind)
        status = "shared"
        for tp, extra in alone.items():
            if extra:
                located[tp].notes.append(
                    f"{len(extra)} site(s) only this timepoint's array held, which no other timepoint supports, are "
                    f"not in the well's roster (a bright speck off the array, or a misread edge): "
                    f"{', '.join(str(a) for a in sorted(extra)[:6])}{' …' if len(extra) > 6 else ''}")
    else:
        shared, status = frozenset(), "no_anchored_timepoint"
    differences = {tp: len(sites ^ shared) for tp, sites in own.items()}
    limit = max(_ROSTER_TOLERANCE[0], _ROSTER_TOLERANCE[1] * len(shared))
    flags = {tp: ("roster_disagrees" if n > limit else None) for tp, n in differences.items()}
    shapes = {located[tp].array_shape for tp in anchored if located[tp].array_shape}
    # One outline, or "mixed": a bubble over the rim can make one timepoint's circle read as a polygon.
    shape = shapes.pop() if len(shapes) == 1 else ("mixed" if shapes else None)
    return shared, {"radius_um": None, "shape": shape, "status": status,
                    "roster": [list(a) for a in sorted(shared)], "roster_differences": differences,
                    "deviations_um": {}, "flags": flags}, own


def _settle_diameter(outcome, measurable, settings: NanowellSettings, reference: dict | None) -> float:
    """One diameter for the whole well's sites (review X35-R14): the earlier run's when it kept timepoints, else
    the setting's unless the median of the timepoints' measured diameters disagrees with it beyond
    ``DIAMETER_AGREEMENT``, then that median. Chosen per timepoint it flipped within a well (125 um at some
    timepoints, the setting's 100 at others), so ownership, margins and templates differed between them and the
    template-drift check compared nanowells of different sizes. Applied to every *measurable* timepoint, whose note
    says so; the per-timepoint comparison stays in its grid record."""
    from pipeline.nanowell_infer import DIAMETER_AGREEMENT, compare_given

    checks = [outcome.timepoints[tp].get("geometry", {}).get("diameter_check") or {} for tp in measurable]
    measured = [c["measured_um"] for c in checks if c.get("measured_um")]
    kept = (reference or {}).get("diameter_used_um")
    consequence = "nanowells are fitted, owned and measured at the measured diameter"
    note = ""
    if kept:
        used = float(kept)
        if used != settings.diameter_um:
            note = f"Nanowells are fitted, owned and measured at {used:.1f} um, the diameter the earlier run kept"
    elif measured:
        check = compare_given(float(np.median(measured)), settings.diameter_um, DIAMETER_AGREEMENT,
                              "nanowell diameter (median over the well's timepoints)", consequence)
        used, note = (settings.diameter_um, "") if check["agrees"] is not False else (check["measured_um"], check["note"])
    else:
        used = settings.diameter_um
    for tp in measurable:
        located = outcome.located[tp]
        located.notes = [n for n in located.notes if not n.startswith("The measured nanowell diameter")]
        located.notes += [note] if note else []
        located.diameter_um = used
        outcome.timepoints[tp].setdefault("geometry", {})["diameter_used_um"] = used
    return used


def _settle_inferred(outcome, timepoints, anchored, unaddressed, fixed_roster, reference=None):
    """Before an inferred well's roster is shared (review X35-R1, R4, R11, R13): refuse timepoints of another
    lattice kind than the well's (an earlier run's kept kind included), put the anchored polygons in one frame, and
    unaddress those whose frame cannot be told — measured on the wells they saw, with no address. Such a timepoint
    can still be anchored at the end of the run from the plate, but only when at least three other wells were
    anchored on their own evidence at both timepoints. Returns the anchored and unaddressed timepoints that remain,
    the registration's reference and the steps applied."""
    refused = _refuse_other_kinds(outcome, anchored + unaddressed, (reference or {}).get("lattice_kind"))
    anchored = [tp for tp in anchored if tp not in refused]
    unaddressed = [tp for tp in unaddressed if tp not in refused]
    reference_tp, shifts, ambiguous = _register_to_reference(outcome.located, anchored, fixed_roster)
    for tp, reason in ambiguous.items():                   # measured on the sites seen, with no address
        located = outcome.located[tp]
        located.status, located.reason, located.origin_montage, located.indexed = UNADDRESSED, reason, None, ()
        located.origin_method = located.origin_agreement = None
        outcome.timepoints[tp].update(status=UNADDRESSED, reason=reason, origin_method=None, origin_agreement=None,
                                      origin_stage_um=None)
    for tp in shifts:                                       # the grid block was written before the move
        outcome.timepoints[tp].update({k: outcome.located[tp].grid[k]
                                       for k in ("origin_stage_um", "origin_shift", "origin_registered_to")})
    return ([tp for tp in anchored if tp not in ambiguous],
            [tp for tp in timepoints if tp in unaddressed or tp in ambiguous], reference_tp, shifts)


#: The roster rule a default (hexagonal lattice in a circle) well's grid record names when it keeps its roster as a
#: set of addresses (tracker X35a). A record without one was analysed with the disk rule — every site within the
#: shared radius plus a quarter pitch — and keeps it.
ROSTER_RULE_EVIDENCE = "evidence"


@dataclass
class CircleEvidence:
    """What a default well's timepoints show about which lattice sites its circle holds (tracker X35a)."""
    roster: frozenset                                   # the sites the evidence supports
    own: dict                                           # timepoint -> the sites its own evidence supports
    misanchored: dict                                   # timepoint -> (dq, dr): its nanowells sit that step off


def _misanchored(detected: dict) -> dict:
    """``timepoint -> step`` for each timepoint whose detected sites match the other timepoints' better moved by one
    lattice step than where they are: its origin is a step off (review X35a-R2). A timepoint whose sites the others
    do not overlap better anywhere is not listed. With two timepoints a step apart, both are listed: which of them
    is wrong cannot be told."""
    steps = [(dq, dr) for dq in (-1, 0, 1) for dr in (-1, 0, 1)]
    out = {}
    for tp, mine in detected.items():
        others = set().union(*(d for t, d in detected.items() if t != tp))
        if not others:
            continue
        overlap = {s: len({(q + s[0], r + s[1]) for q, r in mine} & others) for s in steps}
        best = max(steps, key=lambda s: (overlap[s], s == (0, 0)))
        if best != (0, 0) and overlap[best] > overlap[(0, 0)]:
            out[tp] = best
    return out


def _evidence_roster(located: dict, anchored, radius_um: float, settings: NanowellSettings,
                     kept: dict | None = None) -> CircleEvidence:
    """The sites of a circular array the evidence supports (tracker X35a), and each timepoint's own share of it.

    The disk rule, every site within the shared radius plus a quarter pitch, added a ring the plate does not have
    (18 sites on a rendered 265-site array, every one written ``occluded``) and, where the fitted circle ran through
    the midpoints of a hexagonal outline, left out six detected nanowells at its corners (a 61-site 50 um array). No
    tolerance on the radius separates the two: a drawn site of an array the image cuts lay 0.24 pitch beyond the
    fitted radius, a phantom 0.15 beyond it on another plate. So a site belongs to the array when it lies a quarter
    pitch inside the circle; or a nanowell was detected there at some anchored timepoint (within a pitch of the
    rim); or it lies within the old quarter pitch of the rim and no anchored timepoint imaged its whole disk, so
    nothing could have shown it absent. A site within that band whose whole disk was imaged and where no nanowell was
    ever detected is floor, and is not in the roster. The first anchored timepoint's lattice measures the distances.

    A timepoint whose detections sit a lattice step from the other timepoints' (:func:`_misanchored`) is anchored a
    step off, and its detections decide nothing: taken at face value one such timepoint put 17 rows of floor into
    every timepoint of a well (review X35a-R2). *kept* is an earlier run's evidence for the timepoints it kept
    (``{"detected": sites, "imaged": sites}``, from its rows), so that an incremental run decides the roster a run
    over every timepoint would (review X35a-R1).
    """
    from pipeline.nanowell_lattice import _disk_covered, roster as disk_roster
    from pipeline.nanowell_lattice import TileRectangle

    first = located[anchored[0]]
    pitch = float(first.fit.pitch)
    candidates = disk_roster(first.fit, first.origin_montage, radius_um + 0.75 * pitch)
    basis = np.column_stack((first.fit.a1, first.fit.a2))
    distance = {a: float(np.linalg.norm(basis @ np.asarray(a, dtype=float))) for a in candidates}
    found = {tp: {(e.q, e.r) for e in located[tp].indexed if e.q is not None} for tp in anchored}
    misanchored = _misanchored(found) if len(anchored) > 1 else {}
    band = {a for a in candidates if radius_um - pitch / 4 < distance[a] <= radius_um + pitch / 4}
    imaged = {tp: set() for tp in anchored}
    for tp in anchored:
        loc = located[tp]
        tiles = [TileRectangle(p.tile_id, loc.positions[p.tile_id][0], loc.positions[p.tile_id][1],
                               loc.positions[p.tile_id][0] + p.width_um, loc.positions[p.tile_id][1] + p.height_um)
                 for p in loc.plan.placements]
        tp_basis = np.column_stack((loc.fit.a1, loc.fit.a2))
        origin = np.asarray(loc.origin_montage) + np.asarray(loc.montage.origin_um)
        for a in band:
            x, y = origin + tp_basis @ np.asarray(a, dtype=float)
            if _disk_covered(x, y, settings.diameter_um / 2, tiles):
                imaged[tp].add(a)
    kept = kept or {}

    def supported(detected, seen_whole):
        return frozenset(a for a in candidates
                         if distance[a] <= radius_um - pitch / 4
                         or (a in detected and distance[a] <= radius_um + pitch)
                         or (a in band and a not in seen_whole))

    trusted = [tp for tp in anchored if tp not in misanchored]
    detected = set(kept.get("detected", ())).union(*(found[tp] for tp in trusted))
    seen_whole = set(kept.get("imaged", ())).union(*(imaged[tp] for tp in trusted))
    return CircleEvidence(supported(detected, seen_whole), {tp: supported(found[tp], imaged[tp]) for tp in anchored},
                          misanchored)


def _circle_roster(outcome, anchored, radius_um, settings, fixed_roster, fixed_radius_um, kept=None):
    """``(roster, own, reference)`` of a default well (tracker X35a): a kept set of addresses; or, for a well first
    analysed before sets were kept (a radius and no roster), ``(None, None, None)`` — the disk rule it was analysed
    with, so its new timepoints get the same roster as its old ones; or, for a fresh well, the sites the evidence
    supports. A set is recorded in ``outcome.radius`` with its rule.

    *own* is what each anchored timepoint's own evidence supports when one of them is anchored a step off (it is
    then a ``roster_mismatch`` against *reference*, the first that is not, and says why); otherwise the roster for
    each, since a rim nanowell a bubble hides at one timepoint is still that timepoint's (review X35a-R2). With a
    kept roster and *kept* evidence, the evidence over the kept and the new timepoints is compared with the kept
    roster, and a difference is put in ``outcome.roster_change`` for the caller to re-run the well over every
    timepoint: an incremental run must give the roster a run over all of them would (review X35a-R1)."""
    if fixed_roster:
        roster = frozenset(tuple(map(int, a)) for a in fixed_roster)
        if kept is not None and anchored:
            full = _evidence_roster(outcome.located, anchored, radius_um, settings, kept).roster
            added, removed = full - roster, roster - full
            if added or removed:
                outcome.roster_change = (f"the new timepoints' evidence adds {len(added)} nanowell(s) to the roster the "
                                         f"earlier run kept and removes {len(removed)}")
        evidence = None
    elif fixed_radius_um is None and anchored:
        evidence = _evidence_roster(outcome.located, anchored, radius_um, settings)
        roster = evidence.roster
    else:
        return None, None, None
    outcome.radius.update(roster=[list(a) for a in sorted(roster)], roster_rule=ROSTER_RULE_EVIDENCE)
    if evidence is None or not evidence.misanchored:
        return roster, {tp: roster for tp in anchored}, None
    for tp, step in evidence.misanchored.items():
        outcome.located[tp].notes.append(
            f"Its nanowells sit {step} lattice steps from the other timepoints': its origin is probably wrong, so its "
            "addresses may name neighbouring nanowells, and its detections were not used to decide the well's rim")
    reference = next((tp for tp in anchored if tp not in evidence.misanchored), None)
    return roster, {tp: (evidence.own[tp] if tp in evidence.misanchored else roster) for tp in anchored}, reference


def _checkpoint_key(outcome, tp, located, radius_um, roster, settings, shifts) -> dict:
    """What a timepoint's checkpoint must match to be taken back: everything its rows depend on beyond the settings
    the store is keyed on — the radius, the record, how it was located, and the roster: for an inferred well with the
    step its addresses were moved by and the diameter (X35-R1, R5, R14); for a default well with its rule, so a
    disk-rule checkpoint is never taken back into an evidence-rule well (X35a)."""
    key = {"radius_um": radius_um, "record_digest": outcome.timepoints[tp]["record_digest"],
           "located": [located.status, located.origin_method]}
    if roster is not None and settings.infer:
        key.update(roster=outcome.radius["roster"], origin_shift=list(shifts.get(tp, (0, 0))),
                   diameter_used_um=outcome.radius["diameter_used_um"])
    elif roster is not None:
        key.update(roster=outcome.radius["roster"], roster_rule=ROSTER_RULE_EVIDENCE)
    return key


def own_array_model(located: Located, settings: NanowellSettings | None = None) -> tuple[float | None, frozenset | None]:
    """``(radius, roster)`` a single timepoint is measured against on its own (Test detection), as a run would: an
    inferred array's own sites (none without an origin), otherwise its boundary's radius and, when it is anchored
    and *settings* are given, the sites the evidence supports (:func:`_evidence_roster`)."""
    if located.array_sites is not None:
        return None, (_own_roster(located) if located.origin_montage is not None else frozenset())
    radius = located.boundary["radius_um"]
    if settings is None or located.origin_montage is None:
        return radius, None
    return radius, _evidence_roster({located.timepoint: located}, [located.timepoint], radius, settings).roster


def _consistency(located: dict, anchored, radius_um: float | None, reference: dict | None,
                 own_rosters: dict | None = None, reference_tp: str | None = None,
                 reference_roster: frozenset | None = None) -> dict:
    """``timepoint -> consistency`` of the anchored timepoints' lattices with each other (and an earlier run's).

    An inferred array passes each timepoint's *own* address set (*own_rosters*), not the shared roster every
    timepoint is measured against, so a timepoint that saw a different array is a ``roster_mismatch`` (review
    X35-R1); *reference_tp* is then the registration's reference, and *reference_roster* an earlier run's roster."""
    from pipeline.nanowell_sites import TimepointLattice, check_consistency

    lattices = []
    for tp in anchored:
        addresses = own_rosters[tp] if own_rosters is not None else _roster_positions(located[tp], radius_um)[0]
        lattices.append(TimepointLattice(tp, located[tp].fit.pitch, located[tp].fit.angle_deg, 0.0,
                                         frozenset(addresses)))
    if reference_tp is not None:
        lattices.sort(key=lambda t: t.timepoint != reference_tp)          # stable: the reference first
    if reference is not None:
        lattices.insert(0, TimepointLattice(reference["timepoint"], reference["pitch_um"], reference["angle_deg"],
                                            0.0, reference_roster))
    reference_angle = lattices[0].angle_deg if lattices else 0.0
    # The plate's rotation between timepoints is only measurable across wells; within one well the angle
    # is not checked (and the grid block says so), so a plate put back rotated is not called inconsistent.
    lattices = [replace(t, plate_rotation_deg=t.angle_deg - reference_angle) for t in lattices]
    return {c.timepoint: c for c in check_consistency(lattices)}


def _spill_montage(montage, folder: Path, stem: str):
    """*montage* with its two arrays moved to ``.npy`` files in *folder* and read back as memory maps."""
    folder.mkdir(parents=True, exist_ok=True)
    arrays = {}
    for name in ("image", "covered"):
        path = folder / f"{stem}_{name}.npy"
        np.save(path, getattr(montage, name))
        arrays[name] = np.load(path, mmap_mode="r")
    return replace(montage, **arrays)


def run_well(well: str, record: dict, timepoints, load_phase, settings: NanowellSettings, *,
             segmenter=None, channels: dict | None = None, plate_rigid: dict | None = None,
             expected_shape=None, fixed_radius_um: float | None = None, checkpoint=None,
             reference: dict | None = None, earliest_template=None, spill_dir=None, fixed_roster=None,
             kept_evidence: dict | None = None) -> WellOutcome:
    """Locate every timepoint, agree on one array radius, then measure each against it.

    *load_phase* is ``f(timepoint, tile_id) -> image | None``; *channels* maps a fluorescence name to
    ``f(timepoint, tile_id) -> image | None``. *plate_rigid* maps a timepoint to an ``OriginPrediction``
    in that well's montage frame (from the end-of-run pass; absent on a first run). A timepoint whose
    first stage refuses keeps its status and no rows; the others go on. *fixed_radius_um* is the array
    radius an earlier run of this well already shared across its timepoints: an incremental run adds a
    timepoint to the same roster instead of choosing a new one. *checkpoint* has ``load(timepoint, key)``
    (a :class:`Measured` or ``None``) and ``save(timepoint, key, measured)``: a timepoint measured before
    the process was killed is taken back instead of measured again, but only under the same *key* (the
    shared radius and the record's digest), because a different roster or tile placement makes it wrong.
    *reference* is a timepoint an earlier run kept (``timepoint``, ``pitch_um``, ``angle_deg``,
    ``roster_size`` from its grid block): the new timepoints are checked for consistency against it, as they
    would have been had they run together. *spill_dir*, when given, is where each timepoint's montage goes once
    it is located: every timepoint is located before any is measured, so held in memory a long time-course
    holds them all at once (~32 MB a timepoint at the 4096-pixel cap; roll-up review X23). Spilled, each is a
    read-only memory map of a file there, which the caller removes with the rest of its scratch. *fixed_roster* is
    the earlier run's shared roster of an inferred array that is not a circle (``[[q, r], ...]``), the counterpart of
    *fixed_radius_um* (tracker X35). *kept_evidence* is the evidence an earlier run's kept timepoints left about a
    default well's rim (``{"detected": sites, "imaged": sites}``): when the new timepoints change the roster it implies,
    ``outcome.roster_change`` says how, and the caller re-runs the well over every timepoint (tracker X35a-R1).
    """
    outcome = WellOutcome(well)
    timepoints = list(timepoints)
    for tp in timepoints:
        images = _images_for(record, well, tp, load_phase)
        located = locate_timepoint(tp, record, well, images, settings, expected_shape=expected_shape,
                                   plate_rigid=(plate_rigid or {}).get(tp))
        if spill_dir is not None and located.montage is not None:
            located.montage = _spill_montage(located.montage, Path(spill_dir), f"{well}_{tp}")
        outcome.located[tp] = located
        outcome.timepoints[tp] = dict(located.grid) or {"status": located.status, "reason": located.reason}
        outcome.timepoints[tp]["record_digest"] = record_digest(record, well, tp)
        del images
    anchored = [tp for tp in timepoints if outcome.located[tp].status == OK]
    unaddressed = [tp for tp in timepoints if outcome.located[tp].status == UNADDRESSED]
    # An inferred array has a shared set of addresses instead of a shared radius, whatever its outline (X35).
    roster = own = reference_tp = None
    shifts: dict = {}
    if settings.infer and fixed_radius_um is None:
        anchored, unaddressed, reference_tp, shifts = _settle_inferred(outcome, timepoints, anchored, unaddressed,
                                                                       fixed_roster, reference)
        roster, outcome.radius, own = _shared_roster(outcome.located, anchored, fixed_roster)
        if anchored:          # consistency is judged against the timepoint that saw the well's roster most nearly
            differences = outcome.radius["roster_differences"]
            reference_tp = min(anchored, key=lambda tp: (differences[tp], anchored.index(tp)))
        outcome.radius["diameter_used_um"] = _settle_diameter(outcome, anchored + unaddressed, settings, reference)
        radius_um = None
    else:
        shared = _shared_radius(outcome.located, anchored + unaddressed, settings, fixed_radius_um)
        outcome.radius = {"radius_um": shared.radius_um, "status": shared.status,
                          "deviations_um": shared.deviations_um, "flags": shared.flags}
        radius_um = shared.radius_um
        if radius_um is None:
            # Unaddressed timepoints too: they are measured against the shared radius, so without one they have no
            # rows and must say so in a status the caller counts as failed (roll-up review X27).
            for tp in anchored + unaddressed:
                outcome.timepoints[tp].update(status=NO_LATTICE, reason="No timepoint fitted an array boundary, so "
                                                                       "no roster radius could be shared")
            return outcome
        roster, own, circle_reference = _circle_roster(outcome, anchored, radius_um, settings, fixed_roster,
                                                       fixed_radius_um, kept_evidence)
        reference_tp = circle_reference or reference_tp

    consistency = _consistency(outcome.located, anchored, radius_um, reference, own, reference_tp,
                               frozenset(map(tuple, fixed_roster)) if fixed_roster and own is not None else None)

    # The template a timepoint with too few empty nanowells falls back to: an earlier run's (incremental), else the
    # first this run builds in time. A resumed run gets it back from the checkpoint (tracker C16).
    earliest = earliest_template
    measurable = set(anchored) | set(unaddressed)
    for tp in [t for t in timepoints if t in measurable]:          # in time, so "earliest" means it
        located = outcome.located[tp]
        key = _checkpoint_key(outcome, tp, located, radius_um, roster, settings, shifts)
        measured = checkpoint.load(tp, key) if checkpoint is not None else None
        if measured is None:
            images = _images_for(record, well, tp, load_phase)
            loaders = {name: (lambda tile, _f=f, _tp=tp: _f(_tp, tile)) for name, f in (channels or {}).items()}
            measured = measure_timepoint(located, images, settings, radius_um, segmenter=segmenter,
                                         channels=loaders, earliest_template=earliest, roster=roster)
            del images
            if checkpoint is not None:
                checkpoint.save(tp, key, measured)
        if earliest is None and measured.template.get("_object") is not None \
                and measured.template["source"] != "earliest":
            earliest = measured.template["_object"]
        if outcome.earliest_template is None and measured.template.get("_object") is not None \
                and measured.template["source"] != "earliest":
            outcome.earliest_template = (tp, measured.template["_object"])
        block = outcome.timepoints[tp]
        agree = consistency.get(tp)
        block.update(consistency=None if agree is None else agree.status,
                     consistency_detail="" if agree is None else agree.detail, angle_checked=False,
                     radius_flag=outcome.radius["flags"].get(tp),
                     radius_deviation_um=outcome.radius["deviations_um"].get(tp),
                     roster_size=len(measured.sites),
                     template={k: v for k, v in measured.template.items() if k != "_object"},
                     notes=list(measured.notes))
        status = None if agree is None else agree.status
        if (reference is not None and status == "consistent" and tp in anchored
                and len(measured.sites) != reference["roster_size"]):
            status = "roster_mismatch"
            block["consistency_detail"] = (f"{len(measured.sites)} addresses against {reference['roster_size']} "
                                           f"at {reference['timepoint']}")
        block["consistency"] = status
        for row in measured.sites:
            row["consistency"] = status
        outcome.sites.extend(measured.sites)
        for name, values in measured.cells.items():
            outcome.cells.setdefault(name, []).extend(values)
        outcome.cells.setdefault("timepoint", []).extend([tp] * len(measured.cells.get("label", ())))
    flag_template_drift(outcome.timepoints, timepoints)     # the caller re-runs it over kept timepoints too
    return outcome
