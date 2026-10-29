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

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
import math
import time
from pathlib import Path
from types import MappingProxyType

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

#: How far a fiducial's score must clear the runner-up's to anchor an address, and how many times the runner-up's it
#: must be (tracker F14). Both are needed: a lead alone accepts a square under the floor's noise when everything
#: reads high (cells, debris), a ratio alone accepts 0.05 against 0.01. Rendered squares under defocus, halo and
#: rounding read 0.34 to 0.65 against runner-ups of 0.02 to 0.13; a blur that leaves them 0.21 against 0.10 is
#: refused as ambiguous. Assumed, not calibrated on a real plate (tracker F6).
FIDUCIAL_MARGIN = 0.15
FIDUCIAL_RATIO = 3.0


#: The plate format whose pitch and diameter the run is given rather than knowing (tracker A7).
CUSTOM_FORMAT = "custom"

#: How the array is found. ``hex_circle``: a matched filter at the format's diameter, a hexagonal lattice and a
#: circular array (the plates this was written for). ``infer`` (tracker X35): the nanowells are found by their
#: contrast alone (light wells, dark lattice) and the lattice kind, pitch, orientation and the array's outline are
#: inferred from where they are (``pipeline.nanowell_infer``); the format's pitch and diameter are then only
#: compared with what is measured, and a disagreement is reported.
#: The Cellpose models the label-free count may use, the first the default (tracker L2). ``cpsam`` is Cellpose-SAM,
#: Cellpose 4's one model, which the pinned ``cellpose`` installs; ``cyto3`` (Cellpose 3's generalist) and
#: ``livecell_cp3`` (Cellpose 3's model trained on LIVECell's phase-contrast images of eight cell lines) need a
#: Cellpose 3 install, and a run asking for one on Cellpose 4 is refused by ``pipeline.cellpose``, never segmented
#: with another model under its name. Which counts these plates best has not been measured here (tracker C10): the
#: choice exists so it can be, with Test detection.
CELLPOSE_MODELS = ("cpsam", "cyto3", "livecell_cp3")
#: The model a run's options mean when they name none: every run made before there was a choice segmented with
#: cyto3, and their settings and reuse keys carry no model. A run that names none still means it, so such a run is
#: never taken for a cpsam one; a new run names its model (the form and the command line both send it).
LEGACY_CELLPOSE_MODEL = "cyto3"
#: What the count's segmenter is given (``nanowell_measure.SEGMENT_ON``). A run's options that name none mean the
#: rim-suppressed tile, which every run before there was a choice segmented; a new run names it (``raw`` by default,
#: from the form and the command line).
SEGMENT_ON = ("raw", "rim")
LEGACY_SEGMENT_ON = "rim"
#: How much of a tile the segmenter is given (``nanowell_measure.segment_sites`` / ``segment_tile``): a crop around
#: each nanowell (``nanowells``, the default for a new run: the lattice between them is work that counts nothing) or
#: the whole tile (``tile``), which is what a run's options that name none mean — every earlier run did so.
SEGMENT_SCOPES = ("nanowells", "tile")
#: How the segmenter's input is scaled (``nanowell_measure.SEGMENT_SCALES``); options that name none mean ``tile``,
#: the tile's own percentiles, which is what every earlier run did.
SEGMENT_SCALES = ("levelled", "nanowells", "tile")
LEGACY_SEGMENT_SCALE = "tile"
LEGACY_SEGMENT_SCOPE = "tile"
#: Whether Cellpose resizes the count's input first. ``cell``: by ``30 / (cell diameter in pixels)``, so a cell
#: reaches the 30 pixels its models work at, which is what every earlier run did (``LEGACY_RESIZE``). ``none``: the
#: image is segmented as imaged, which costs far less where cells are small in pixels (a resize of x2 is four times
#: the pixels the network runs on), at whatever the model makes of cells at that size.
RESIZE_MODES = ("cell", "none")
LEGACY_RESIZE = "cell"

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
    cellpose_model: str = LEGACY_CELLPOSE_MODEL  # the model the count segments with; unnamed, an earlier run's
    segment_on: str = LEGACY_SEGMENT_ON         # what that model is given (SEGMENT_ON); unnamed, an earlier run's
    segment_scope: str = LEGACY_SEGMENT_SCOPE   # how much of each tile it is given (SEGMENT_SCOPES); unnamed, the same
    segment_scale: str = LEGACY_SEGMENT_SCALE   # what its input is scaled by (SEGMENT_SCALES); unnamed, the same
    resize: str = LEGACY_RESIZE                 # whether Cellpose resizes that input first (RESIZE_MODES); the same
    #: The nanowell diameter measured on the plate, whatever the geometry (tracker F2): sites are fitted, owned and
    #: measured at it. Set from the well's one diameter by :func:`measure_timepoint` and by :func:`run_well` for its
    #: roster, and by Test detection; never part of the reuse key, which holds the setting (``key``), while the
    #: checkpoint key holds the diameter used (review F21b).
    diameter_override_um: float | None = None

    def __post_init__(self):
        if (self.plate_format == CUSTOM_FORMAT) != (self.custom is not None):
            raise ValueError("A custom plate format needs its pitch and diameter, and only a custom one takes them")
        if self.plate_format != CUSTOM_FORMAT and self.plate_format not in PLATE_FORMATS:
            raise ValueError(f"Unknown plate format {self.plate_format!r}")
        if self.geometry not in GEOMETRIES:
            raise ValueError(f"Unknown nanowell geometry {self.geometry!r}; one of {GEOMETRIES}")
        if self.cellpose_model not in CELLPOSE_MODELS:
            raise ValueError(f"Unknown nanowell Cellpose model {self.cellpose_model!r}; one of {CELLPOSE_MODELS}")
        if self.segment_on not in SEGMENT_ON:
            raise ValueError(f"Unknown nanowell segmenter input {self.segment_on!r}; one of {SEGMENT_ON}")
        if self.segment_scope not in SEGMENT_SCOPES:
            raise ValueError(f"Unknown nanowell segmentation scope {self.segment_scope!r}; one of {SEGMENT_SCOPES}")
        if self.segment_scale not in SEGMENT_SCALES:
            raise ValueError(f"Unknown nanowell segmenter scale {self.segment_scale!r}; one of {SEGMENT_SCALES}")
        if self.resize not in RESIZE_MODES:
            raise ValueError(f"Unknown nanowell Cellpose resize {self.resize!r}; one of {RESIZE_MODES}")

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
        if self.cellpose_model != LEGACY_CELLPOSE_MODEL:   # and a run on the model every earlier run used
            key["cellpose_model"] = self.cellpose_model
        if self.segment_on != LEGACY_SEGMENT_ON:           # and on the input every earlier run segmented
            key["segment_on"] = self.segment_on
        if self.segment_scope != LEGACY_SEGMENT_SCOPE:     # and on the whole tile, as every earlier run did
            key["segment_scope"] = self.segment_scope
        if self.segment_scale != LEGACY_SEGMENT_SCALE:     # and scaled by the tile's own percentiles, the same
            key["segment_scale"] = self.segment_scale
        if self.resize != LEGACY_RESIZE:                   # and resized to the cell diameter, the same
            key["resize"] = self.resize
        return key

    def segmenter_diameter_px(self, pixel_size_um: float) -> float:
        """The cell diameter Cellpose is told, in pixels: 0 (none, so no resize) when ``resize`` is ``none``."""
        return 0.0 if self.resize == "none" else self.cell_diameter_um / float(pixel_size_um)


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
    manual_anchor_um: tuple[float, float] | None = None   # the anchor a person set, in the well frame
    pitch_um: float | None = None                         # the pitch this timepoint was located with (measured)
    #: Inferred geometry only (``settings.geometry == "infer"``): every lattice site of the array as inferred from the
    #: wells found, montage frame, found or not — the roster of an array that is not a circle (tracker X35).
    array_sites: np.ndarray | None = None
    array_shape: str | None = None                        # "circle" or "polygon"
    diameter_um: float | None = None                      # inferred only: the diameter sites are measured at
    array_found: np.ndarray | None = None                 # inferred only: per array site, a light nanowell was found
    notes: list = field(default_factory=list)             # findings of the locating stage that are not refusals
    #: The central square's search: sites scored, best and runner-up squareness, the thresholds, where it was (well
    #: frame) and why none was taken (``nanowell_detect.describe_fiducial``; tracker F1). ``None`` before the search.
    fiducial: dict | None = None


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
              kind: str = "auto", diameter_um: float | None = None):
    """The lattice fitted to one tile alone (tile-local um), or ``None`` when it has too few sites to fit.

    Registration's lattice-phase constraint needs it; a tile of bare floor or a wholly occluded one
    simply contributes the other two sources. *pitch_um* is the working pitch (the nominal one by default);
    *diameter_um* the plate's measured diameter (the format's by default). A matched filter smaller than the
    nanowells has a flat-topped response whose peak wanders: at 100 um on 125.8 um wells one tile's fit had an rms
    of 13 um and its pitch 0.7 % short, which the lattice-phase steps between tiles multiplied into tiles 33 um off
    (tracker F13).
    """
    from pipeline.nanowell_detect import detect_sites

    if settings.infer:
        from pipeline.nanowell_infer import contrast_fit

        return contrast_fit(image, pixel_size_um, expected_pitch_um=pitch_um, kind=kind)
    found = detect_sites(image, pixel_size_um, diameter_um or settings.diameter_um,
                         expected_pitch_um=pitch_um or settings.pitch_um)
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


# ── registration reuse ───────────────────────────────────────────────────────
#: The version of the registration step's algorithm (``tile_register`` and the per-tile lattice fits that feed it).
#: It joins :func:`registration_key`, so positions an older algorithm solved are never taken for this one's. Raise it
#: whenever a change there could move a tile.
REGISTRATION_SCHEME = 3


@dataclass(frozen=True)
class Placement:
    """Where registration put a timepoint's tiles, as the grid record stores it, and where that came from.

    *tiles* are the grid block's ``tiles`` rows; *warnings* the registration's own (not the locating stage's);
    *key* the :func:`registration_key` it was solved under; *fitted* how many tiles' own lattice fits constrained
    it; *reused_from* the output folder whose grid record it was taken from, or ``None`` when this run registered
    the tiles itself; *steps* how each pair's whole-vector choice was made (``{"pair", "from", "moved_um"}``, from
    ``tile_register.choose_step``: the overlap's content, the pairs around it, the well's other timepoints, or the
    stage), which is what to read when a tile sits a step off.
    """
    tiles: tuple
    warnings: tuple
    key: str
    fitted: int
    reused_from: str | None = None
    steps: tuple = ()

    def positions(self) -> dict[str, tuple[float, float]]:
        return {t["tile"]: (float(t["left_um"]), float(t["top_um"])) for t in self.tiles}


def registration_key(record: dict, well: str, timepoint: str, images: dict, settings: NanowellSettings,
                     pitch_um: float, kind: str, diameter_um: float | None = None, preferred: dict | None = None) -> str:
    """A digest of everything registration's result depends on, so stored positions are reused only when
    registering again would produce exactly them.

    Each part, and why it is in the key:

    * :data:`REGISTRATION_SCHEME`: the algorithm itself;
    * :func:`record_digest`: the recorded tile positions, tile size, pixel size and declared overlap, which seed the
      solve and bound its search;
    * every tile's pixels (shape, type and bytes): overlap correlation and the per-tile lattice fits read them, and a
      plate re-exported with corrected images keeps its record but not its registration;
    * the geometry, nominal pitch and diameter, and the diameter measured on the tiles: how each tile's lattice is
      fitted (:func:`_tile_fit`);
    * the working pitch and lattice kind measured on this timepoint: the fits' seed and the search window;
    * *preferred*, the tile offsets the well's other timepoints agree on, when the timepoint is registered again with
      them (:func:`_agree_tile_layouts`): they can change a pair's whole-vector choice.

    Nothing about measurement or segmentation is in it (cell diameter, margin, occlusion and clipping thresholds,
    the segmenter): those act after registration, which is the point of reusing it.
    """
    import hashlib
    import json

    pixels = hashlib.sha256()
    for tile in sorted(images):
        image = np.ascontiguousarray(images[tile])
        pixels.update(json.dumps([tile, list(image.shape), image.dtype.str]).encode())
        pixels.update(image.tobytes())
    payload = json.dumps([REGISTRATION_SCHEME, record_digest(record, well, timepoint), pixels.hexdigest(),
                          settings.geometry, settings.pitch_um, settings.diameter_um, float(pitch_um), kind,
                          None if diameter_um is None else float(diameter_um),
                          None if preferred is None else sorted([f"{i}-{j}", round(float(x), 3), round(float(y), 3)]
                                                                for (i, j), (x, y) in preferred.items())])
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def stored_placement(candidates, key: str, tile_ids) -> Placement | None:
    """The first stored placement among *candidates* solved under *key* for exactly *tile_ids*, or ``None``.

    *candidates* are ``(folder, grid block)`` pairs: this timepoint's block in an earlier run's grid record. A block
    without a ``registration`` key (written before registration was keyed) or with another key is never reused.
    """
    for folder, block in candidates or ():
        stored = (block or {}).get("registration")
        tiles = (block or {}).get("tiles")
        if not isinstance(stored, dict) or stored.get("key") != key or not isinstance(tiles, list):
            continue
        try:
            rows = tuple({**t, "left_um": float(t["left_um"]), "top_um": float(t["top_um"])} for t in tiles)
            fitted = int(stored["tile_lattice_fits"])
        except (KeyError, TypeError, ValueError):
            continue
        if sorted(t["tile"] for t in rows) != sorted(tile_ids):
            continue
        return Placement(rows, tuple(stored.get("warnings") or ()), key, fitted, str(folder),
                         tuple(stored.get("steps") or ()))
    return None


def _solved_placement(registration, key: str, fitted: int) -> Placement:
    from pipeline.tile_register import LATTICE

    steps = tuple({"pair": f"{c.i}-{c.j}", "from": c.step_from, "moved_um": round(float(np.hypot(*c.step_moved_um)), 2)}
                  for c in registration.constraints if c.source == LATTICE)
    return Placement(tuple({"tile": t.tile_id, "left_um": t.left_um, "top_um": t.top_um, "source": t.source,
                            "residual_um": t.residual_um, "correction_um": t.correction_um}
                           for t in registration.tiles), tuple(registration.warnings), key, fitted, steps=steps)


def _grid_block(plan, placement: Placement | None, extra: dict, warnings=()) -> dict:
    block = {"grid_status": plan.status if plan is not None else None,
             "rows": getattr(plan, "rows", None), "cols": getattr(plan, "cols", None),
             "missing": list(getattr(plan, "missing", ())),
             "overlap_um": list(getattr(plan, "overlap_um", (None, None))),
             "overlap_fraction": list(getattr(plan, "overlap_fraction", (None, None)))}
    if placement is not None:
        block["tiles"] = [dict(t) for t in placement.tiles]
        block["registration_warnings"] = list(placement.warnings) + list(warnings)
        block["registration"] = {"key": placement.key, "warnings": list(placement.warnings),
                                 "tile_lattice_fits": placement.fitted, "reused_from": placement.reused_from,
                                 "steps": [dict(step) for step in placement.steps]}
    block.update(extra)
    return block


def _find_square(montage, detection, diameter: float, lattice_pitch: float, settings: NanowellSettings, manual):
    """``(candidate, fiducial block, refusal or None)``: the central square's search on the montage (montage frame).

    The square is looked for at every lattice site near the array's centre, detected or not, and read at several
    sizes (tracker F15): the matched filter finds round nanowells, and a square of another size is not one.

    The orientation check guards against a lattice fit folded onto the wrong row (N3a), and only a square the origin
    would take can say that: a circle's "orientation" is noise (roll-up review X21), and so is that of square-ish
    debris ``resolve_origin`` refuses as ambiguous, which refused whole timepoints a hand-set anchor or the array
    centre would have addressed (review F21). Nor does a candidate on another site than a hand-set anchor's (*manual*,
    montage frame) name the square. The square's measured edge angle is kept either way, against the lattice row the
    fit chose, so a real plate can confirm the square's cut (tracker F19, Q11). The refusal is the reason a folded fit
    gives, or ``None``.
    """
    from pipeline.nanowell_detect import (
        SQUARE_SIZE_FACTORS, check_orientation, describe_fiducial, score_fiducial, square_search_points,
    )
    from pipeline.nanowell_lattice import fiducial_accepted, nearest_site

    near = detection.boundary_centre_um
    if near is None and len(detection.points_um):
        near = np.median(np.asarray(detection.points_um, dtype=float), axis=0)
    search = (detection.points_um if near is None or not detection.fit.ok else
              square_search_points(detection.fit, detection.points_um, near, 2.2 * lattice_pitch))
    candidate, scores = score_fiducial(montage.image, montage.um_per_px, diameter, search,
                                       centre_um=detection.boundary_centre_um, search_radius_um=2.2 * lattice_pitch,
                                       pitch_um=lattice_pitch, size_factors=SQUARE_SIZE_FACTORS)
    fiducial = describe_fiducial(candidate, scores, near_centre=detection.boundary_centre_um is not None)
    fiducial["margin"], fiducial["ratio"] = FIDUCIAL_MARGIN, FIDUCIAL_RATIO
    if fiducial["position_um"] is not None:
        fiducial["position_um"] = [float(v) for v in _montage_to_common(fiducial["position_um"], montage)]
    if candidate is None or not scores:
        return candidate, fiducial, None
    square = max(scores, key=lambda s: s.coherence)
    orientation = check_orientation(detection.fit, square.orientation_deg, square_to_axis_deg=settings.square_edge_deg)
    fiducial.update(edge_deg=orientation.observed_deg, edge_expected_deg=orientation.expected_deg,
                    edge_discrepancy_deg=orientation.discrepancy_deg)
    names_square = fiducial_accepted(candidate, FIDUCIAL_MARGIN, FIDUCIAL_RATIO) and (
        manual is None or nearest_site(detection.fit, manual)[1] == nearest_site(detection.fit, candidate.position)[1])
    if names_square and not orientation.ok:
        return candidate, fiducial, (orientation.reason or "The square's edges disagree with the fitted lattice axis")
    return candidate, fiducial, None


def locate_timepoint(timepoint: str, record: dict, well: str, images: dict, settings: NanowellSettings, *,
                     plate_rigid=None, expected_shape: tuple[int, int] | None = None, stored=None,
                     manual_anchor_um=None, preferred: dict | None = None) -> Located:
    """Place, register, montage and address one (well, timepoint). Never raises on bad data.

    *manual_anchor_um* is the anchor a person set for this timepoint (``util.nanowell_anchors``), in the well frame;
    it is offered to ``resolve_origin`` first, and the grid block records it whether or not it was used.

    *images* maps a tile id to its phase image. A tile of the grid with no image is a status of its
    own (``no_images``), not a smaller grid: a missing tile would change which sites it owns. *stored* is
    ``(folder, grid block)`` pairs for this timepoint from earlier runs: a placement among them solved under this
    timepoint's :func:`registration_key` is taken instead of registering the tiles again (:func:`stored_placement`).
    *preferred* maps a pair of tiles to the offset the well's other timepoints agree it has
    (``tile_register.agreed_offsets``), which settles a pair's whole-vector choice its overlap cannot.
    """
    from pipeline.nanowell_detect import detect_sites, estimate_lattice_fft
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
    # The plate's own diameter, measured on the tiles before anything is sized from one (tracker F2, F13): the
    # per-tile fits registration rests on, the matched filter, the square's scoring ring, the site fits and the
    # template all follow it, and the format only checks it.
    probe = _contrast_lattice(images, plan, px) if settings.infer else None
    if probe is not None:
        (pitch, kind), pitch_source = probe, "contrast"
    else:
        (pitch, pitch_source), kind = working_pitch(images, plan, px, settings), "auto"
    tile_diameter, diameter_info = _tile_diameter(images, plan, px, pitch)
    key = registration_key(record, well, timepoint, images, settings, pitch, kind, tile_diameter, preferred)
    registration = stored_placement(stored, key, [p.tile_id for p in plan.placements])
    if registration is None:
        observations = [TileObservation(p.tile_id, (p.left_um, p.top_um), (p.width_um, p.height_um),
                                        _tile_fit(images[p.tile_id], px, settings, pitch, kind, tile_diameter))
                        for p in plan.placements]
        registration = _solved_placement(
            register_tiles(observations, images=images, pixel_size_um=px, pitch_um=pitch, preferred=preferred), key,
            sum(o.fit is not None for o in observations))
    fitted = registration.fitted
    warnings = [] if fitted else [
        f"No tile's lattice could be fitted (working pitch {pitch:.1f} um, {pitch_source}): every tile was placed "
        "by overlap correlation or where the acquisition record put it, with no lattice constraint"]
    positions = registration.positions()
    montage = build_montage(images, positions, px)
    located = Located(timepoint, OK, "", plan=plan, positions=positions, pixel_size_um=px, montage=montage,
                      pitch_um=pitch)
    located.stage_offset_um = stage_offset(record, well, timepoint, positions, tolerance_um=pitch)
    seed = {"pitch_seed_um": pitch, "pitch_seed_source": pitch_source,
            "tile_lattice_fits": [fitted, len(plan.placements)]}
    if manual_anchor_um is not None:
        located.manual_anchor_um = (float(manual_anchor_um[0]), float(manual_anchor_um[1]))
        seed["manual_anchor_um"] = list(located.manual_anchor_um)

    def refuse(status, reason, **extra):
        located.status, located.reason = status, reason
        located.grid = _grid_block(plan, registration, {"status": status, "reason": reason, **seed, **extra},
                                   warnings)
        return located

    if settings.infer:
        detection, inferred, seed["geometry"] = _detect_by_contrast(montage, settings, tile_diameter, diameter_info)
        if not detection.ok:
            return refuse(NO_LATTICE, detection.reason)
        lattice_pitch = float(detection.fit.pitch)
    else:
        estimate = estimate_lattice_fft(montage.image, montage.um_per_px, expected_pitch_um=pitch)
        if not estimate.ok:
            return refuse(NO_LATTICE, estimate.reason)
        lattice_pitch = float(estimate.pitch_um)
        info = {k: v for k, v in diameter_info.items() if k != "why_not"}
        seed["geometry"] = _diameter_block({"method": "matched_filter", "notes": [], **info}, tile_diameter,
                                           settings, diameter_info["why_not"])
    diameter = located.diameter_um = seed["geometry"]["diameter_used_um"]
    measured = seed["geometry"]["diameter_check"]["measured_um"] is not None
    if measured and check_format(lattice_pitch, diameter).status == "format_mismatch":
        # Both numbers were read off this plate, and they cannot both be right: nanowells as wide as the pitch would
        # overlap. A lattice read on a harmonic (a pitch the square root of three or two times short) does this, and
        # addressing on it would be wrong at every site (review F20d).
        return refuse(NO_LATTICE, f"The nanowells measure {diameter:.1f} um across but the lattice's pitch reads "
                                  f"{lattice_pitch:.1f} um, so neighbouring nanowells would overlap: the pitch or the "
                                  "diameter was misread")
    fmt = check_format(lattice_pitch, settings.diameter_um)
    if fmt.status == "format_mismatch":
        reason = (fmt.reason if hasattr(fmt, "reason") else
                  f"The lattice pitch {lattice_pitch:.1f} um is not the {settings.plate_format} format's")
        if not (settings.infer or measured):
            return refuse(FORMAT_MISMATCH, reason)
        # A measured geometry is the plate itself: the format is a statement to check, not a reason to write
        # nothing (review X35-R3).
        seed["geometry"]["notes"].append(f"{reason}. The plate is addressed as measured")
    if not settings.infer:
        detection = detect_sites(montage.image, montage.um_per_px, diameter, expected_pitch_um=lattice_pitch)
        if not detection.ok:
            return refuse(NO_LATTICE, detection.reason)
    manual = (None if located.manual_anchor_um is None
              else np.subtract(located.manual_anchor_um, montage.origin_um))       # well frame -> montage frame
    candidate, fiducial, folded = _find_square(montage, detection, diameter, lattice_pitch, settings, manual)
    located.fiducial = fiducial
    if folded is not None:
        return refuse(ORIENTATION_REFUSED, folded, fiducial=fiducial)
    resolved = resolve_origin(detection.fit, fiducial=candidate, array_centre=detection.boundary_centre_um,
                              plate_rigid=plate_rigid, fiducial_margin=FIDUCIAL_MARGIN, fiducial_ratio=FIDUCIAL_RATIO,
                              fiducial_refusal=fiducial["reason"], manual=manual, detected=detection.points_um)
    located.fit = detection.fit
    located.pitch_um = float(detection.fit.pitch)
    located.mirrored = bool(detection.fit.mirrored)
    located.detected_common = _montage_to_common(detection.points_um, montage)
    # An inferred array that is not a circle has a centre (the middle of its sites) and no radius: no boundary.
    located.boundary = None if detection.boundary_radius_um is None else {
        "centre_um": tuple(_montage_to_common(detection.boundary_centre_um, montage)),
        "radius_um": detection.boundary_radius_um}
    located.notes = list(seed["geometry"]["notes"])
    if settings.infer:
        located.array_sites, located.array_shape = inferred.roster_um, inferred.shape
        located.array_found = np.asarray(inferred.found, dtype=bool)
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


#: The fewest nanowells fitted at full resolution whose median may stand for the plate's diameter (tracker F2).
MIN_DIAMETER_FITS = 6


def fitted_diameter(images: dict, positions: dict, px: float, sites_common, rough_um: float,
                     pitch_um: float | None = None) -> tuple[float | None, int, str | None]:
    """``(diameter, fits, tile)``: the median diameter of the nanowells fitted at full resolution on one tile.

    The montage is block-averaged, so a diameter read from it is rough (the contrast finder read 125.8 um wells as
    111 to 117 um on rendered plates); a site's edge fitted on its own tile reads them within a few tenths of a
    micrometre. One tile is enough for a median and costs a tenth of a second: the one holding the most sites wholly
    inside it, which is the array's middle unless a bubble covers it. *rough_um* seeds the fit, which keeps only
    radii within 30 % of it. ``None`` with fewer than ``MIN_DIAMETER_FITS`` fitted.
    """
    from pipeline.nanowell_detect import FITTED, MIN_REFINE_RADIUS_PX, refine_sites

    if not rough_um > 0 or rough_um / 2 / px < MIN_REFINE_RADIUS_PX:
        return None, 0, None
    sites = np.asarray(sites_common, dtype=float).reshape(-1, 2)
    best, best_local = None, np.empty((0, 2))
    for name, (left, top) in positions.items():
        h, w = np.asarray(images[name]).shape
        local = sites - (left, top)
        reach = 0.8 * rough_um
        inside = ((local[:, 0] > reach) & (local[:, 0] < w * px - reach)
                  & (local[:, 1] > reach) & (local[:, 1] < h * px - reach))
        if inside.sum() > len(best_local):
            best, best_local = name, local[inside]
    if best is None:
        return None, 0, None
    radii = [g.radius_um for g in refine_sites(images[best], px, best_local, rough_um, pitch_um=pitch_um)
             if g.geometry == FITTED]
    if len(radii) < MIN_DIAMETER_FITS:
        return None, len(radii), best
    return 2.0 * float(np.median(radii)), len(radii), best


def _diameter_block(block: dict, measured: float | None, settings: NanowellSettings, why_not: str) -> dict:
    """The diameter check and the diameter used, in a geometry block: the measured one whenever there is one.

    The format's diameter is used only when nothing could be measured, and that is said; a measured diameter
    beyond ``DIAMETER_AGREEMENT`` of the format's is reported, not acted on, because the measured one is used
    either way (tracker F4: the tolerance once decided *which* diameter was used, and the first real plate, at
    +25.8 %, sat on it).
    """
    from pipeline.nanowell_infer import DIAMETER_AGREEMENT, compare_given

    block["diameter_check"] = compare_given(measured, settings.diameter_um, DIAMETER_AGREEMENT, "nanowell diameter",
                                            "nanowells are found, fitted, owned and measured at the measured "
                                            "diameter")
    block["diameter_used_um"] = settings.diameter_um if measured is None else float(measured)
    if block["diameter_check"]["note"]:
        block["notes"].append(block["diameter_check"]["note"])
    if measured is None:
        block["notes"].append(f"The nanowell diameter could not be measured ({why_not}): the plate format's "
                              f"{settings.diameter_um:g} um is used")
    return block


#: The long side, in pixels, a tile is block-averaged to for the rough diameter: enough for a nanowell of any format
#: at 10x to span tens of pixels, and a fraction of the full tile's cost.
_ROUGH_SIDE_PX = 1024


def _tile_diameter(images: dict, plan, px: float, pitch_um: float | None = None) -> tuple[float | None, dict]:
    """``(diameter or None, info)``: the nanowell diameter measured on the tiles nearest the grid's centre.

    Rough from the light blobs of a block-averaged copy of the tile (``nanowell_infer.find_wells_by_contrast``),
    then fitted at full resolution on that tile (:func:`fitted_diameter`); the first centre tile that gives one
    decides. *info* carries the rough diameter, the number of fits and the tile, and ``why_not`` names the reason
    when none measured. Measured before registration, because registration's per-tile fits need it (tracker F13).
    """
    from pipeline.nanowell_infer import find_wells_by_contrast

    info = {"diameter_rough_um": None, "diameter_fits": 0, "diameter_tile": None, "why_not": ""}
    reasons = []
    for tile in _centre_tiles(plan):
        image = np.asarray(images[tile], dtype=np.float32)
        factor = max(1, int(math.ceil(max(image.shape) / _ROUGH_SIDE_PX)))
        h, w = (image.shape[0] // factor) * factor, (image.shape[1] // factor) * factor
        small = image[:h, :w].reshape(h // factor, factor, w // factor, factor).mean(axis=(1, 3))
        wells = find_wells_by_contrast(small, px * factor)
        if not wells.ok:
            reasons.append(f"{tile}: no nanowells stood out by their contrast ({wells.reason})")
            continue
        fitted, fits, _ = fitted_diameter({tile: image}, {tile: (0.0, 0.0)}, px, wells.points_um, wells.diameter_um,
                                           pitch_um)
        info.update(diameter_rough_um=float(wells.diameter_um), diameter_fits=fits, diameter_tile=tile)
        if fitted is not None:
            return fitted, info
        reasons.append(f"{tile}: {fits} nanowell(s) fitted at full resolution, {MIN_DIAMETER_FITS} needed")
    info["why_not"] = "; ".join(reasons) or "no tile to measure it on"
    return None, info


def _detect_by_contrast(montage, settings: NanowellSettings, tile_diameter: float | None = None,
                        diameter_info: dict | None = None):
    """``(detection, ArrayGeometry or None, geometry block)`` for an inferred geometry: the wells found by contrast on the montage, the
    lattice and array they imply, and how the measured pitch and diameter compare with the settings' (tracker X35).

    The detection is a ``SiteDetection`` so the rest of :func:`locate_timepoint` runs unchanged: its points are the
    wells found, its boundary centre the array's centre (a circle's, or the middle site of any other outline; none
    for an outline the montage cuts) and its radius the circle's. The ``ArrayGeometry`` carries every lattice site
    of the array, which is what the roster of a non-circular array is made from.
    """
    from pipeline.nanowell_detect import SiteDetection
    from pipeline.nanowell_infer import (
        ARRAY_CIRCLE, ARRAY_POLYGON, PITCH_AGREEMENT, compare_given, find_wells_by_contrast,
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
    block["notes"] = [block["pitch_check"]["note"]] if block["pitch_check"]["note"] else []
    # The diameter sites are measured at: the plate's, fitted at full resolution on the tiles (tracker F2, F13), else
    # the montage's light blobs' (tracker F4).
    block.update({k: v for k, v in (diameter_info or {}).items() if k != "why_not"})
    _diameter_block(block, float(wells.diameter_um) if tile_diameter is None else tile_diameter, settings, "")
    if tile_diameter is None:
        # Only the montage's light blobs measured it: say so, and keep it out of the fitted ones (review F20c).
        block["diameter_rough_only"] = True
        block["notes"].append(
            f"The nanowell diameter ({wells.diameter_um:.1f} um) was read only from the montage's light blobs, which "
            "read nanowells up to about a tenth small, because none could be fitted at full resolution ("
            f"{(diameter_info or {}).get('why_not') or 'no tile measured'})")
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
            "n_detected": 0 if located.detected_common is None else len(located.detected_common),
            "fiducial": located.fiducial}


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
    #: site index -> (offset, gain) each site was brought to the template's levels with (``nanowell_measure.rim_suppress``);
    #: not written, kept for a caller that suppresses a site again (Test detection's crops).
    levels: dict = field(default_factory=dict)


def _roster_positions(located: Located, radius_um: float | None, sites=None):
    """Roster addresses and their lattice positions in the montage frame: the disk of *radius_um* about the origin,
    or, for an array that is not a circle, the addresses *sites* (the well's shared roster, tracker X35)."""
    from pipeline.nanowell_lattice import roster

    fit = located.fit
    addresses = tuple(sorted(sites)) if sites is not None else roster(fit, located.origin_montage, radius_um)
    basis = np.column_stack((fit.a1, fit.a2))
    points = np.asarray(located.origin_montage) + np.array([basis @ qr for qr in addresses]).reshape(-1, 2)
    return addresses, points


def _unaddressed_positions(located: Located, radius_um: float | None, settings: NanowellSettings) -> np.ndarray:
    """Every lattice site of an unaddressed timepoint's array, montage frame (tracker F8).

    An address needs the origin; a position does not. The lattice and the array's outline place every nanowell,
    including one a bubble hides, and §1.1's rule is that no nanowell's position is ever skipped. An inferred array
    has its sites already (``array_sites``); a circle has the sites the evidence supports within the well's shared
    *radius_um* (:func:`_evidence_roster`, as for an anchored timepoint), counted from the site nearest the fitted
    outline's centre as a provisional origin that decides positions only — no row takes an address from it. The disk
    alone added the ring beyond the rim when the fitted radius ran large: 18 rows of floor on a rendered 265-site
    array (review F20f). Only with no outline fitted are the sites found all there is.
    """
    from pipeline.nanowell_lattice import index_points, nearest_site as lattice_snap

    montage = located.montage
    detected = located.detected_common - np.asarray(montage.origin_um)
    if located.array_sites is not None:
        return np.asarray(located.array_sites, dtype=float).reshape(-1, 2)
    fit = located.fit
    if located.boundary is None or radius_um is None or fit is None or not fit.ok or fit.phase_origin is None:
        return detected
    centre = np.asarray(located.boundary["centre_um"], dtype=float) - np.asarray(montage.origin_um)
    basis = np.column_stack((fit.a1, fit.a2))
    phase = np.asarray(fit.phase_origin, dtype=float)
    # The array's centre is a site (the square sits there), so the roster is counted from the site nearest the fitted
    # outline's centre rather than the centre itself: a circle fitted to the rim was 31 um (a fifth of a pitch) off on
    # a rendered plate, which swapped five rim sites for five outside the array.
    _, middle = lattice_snap(fit, centre)
    site = phase + basis @ np.asarray(middle, dtype=float)
    provisional = replace(located, origin_montage=tuple(site), indexed=index_points(detected, fit, tuple(site)))
    supported = _evidence_roster({located.timepoint: provisional}, [located.timepoint], radius_um, settings)
    return np.array([site + basis @ np.asarray(a, dtype=float) for a in sorted(supported.roster)],
                    dtype=float).reshape(-1, 2)


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


def _fit_geometry(image, px: float, centres_local: np.ndarray, indices, diameter_um: float, offset,
                  pitch_um: float | None = None):
    """``SiteGeometry`` per site, fitted on *image* and returned in the common frame with global indices.

    A fit needs a nanowell at least ``MIN_REFINE_RADIUS_PX`` across; at a coarser pixel size every site
    is ``predicted`` at the nominal radius, which the row says.
    """
    from pipeline.nanowell_detect import PREDICTED, SiteGeometry, refine_sites

    try:
        fitted = refine_sites(image, px, centres_local, diameter_um, pitch_um=pitch_um)
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
                                   montage.origin_um, located.pitch_um)
        else:
            left, top = located.positions[owner]
            fitted = _fit_geometry(images[owner], px, centres[indices] - (left, top), indices,
                                   settings.diameter_um, (left, top), located.pitch_um)
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
    """One template for the timepoint from every owner tile's visible, fully imaged, fitted sites, or ``None``.

    Every such site's crop is levelled by its own floor and surround (``nanowell_measure.level_crops``) and the
    template is each pixel's modal level over them, so no nanowell has to be empty (tracker L1).
    """
    from pipeline import nanowell_measure as nm
    from pipeline.nanowell_detect import FITTED

    px = located.pixel_size_um
    half = nm.template_half(settings.diameter_um / 2 / px)
    crops = []
    for owner, indices in by_tile.items():
        if owner is None:
            continue
        left, top = located.positions[owner]
        usable = [i for i in indices if (visibility[i].visible_fraction or 0) >= 0.9
                  and coverage[i].imaged_fraction >= 0.99 and geoms[i].geometry == FITTED]
        if usable:
            local = np.array([(geoms[i].centre_um[0] - left, geoms[i].centre_um[1] - top) for i in usable])
            crops.append(nm.site_crops(images[owner], px, local, half).astype(np.float32))
    template = None
    if crops:
        # Levelled together: the floor-to-surround ratio every crop is brought to is the whole well's median.
        raw = np.concatenate(crops)
        del crops
        template = nm.build_template(nm.level_crops(raw, half),
                                     interior_radius_px=nm.SUPPRESSION_RADIUS * settings.diameter_um / 2 / px,
                                     earliest=earliest)
    return template or nm.as_fallback(earliest)


def _local_fit(owner, indices, located, geoms, template):
    """The sites *owner* owns, in its own pixel frame, split into (those the template covers, those it does not)."""
    from pipeline import nanowell_measure as nm

    left, top = located.positions[owner]
    local = [replace(geoms[i], centre_um=(geoms[i].centre_um[0] - left, geoms[i].centre_um[1] - top)) for i in indices]
    return nm.fits_template(template, local, pixel_size_um=located.pixel_size_um)


@dataclass
class Normalisation:
    """How a timepoint's sites are brought to the template's levels, and the one texture threshold over them."""
    levels: dict = field(default_factory=dict)           # site index -> (offset, gain)
    threshold: float | None = None                       # texture threshold, the bare floor's over every site
    backgrounds: dict = field(default_factory=dict)      # fluorescence channel -> the well's floor level
    tile_backgrounds: dict = field(default_factory=dict)  # fluorescence channel -> tile -> that tile's floor level
    block: dict = field(default_factory=dict)            # what the grid record says about it
    notes: list = field(default_factory=list)


#: A site's floor-to-surround contrast, which sets its gain, is the median of the contrasts read within this many
#: lattice pitches of it on its own tile (its own included): its six neighbours on a hexagonal lattice. A median of
#: seven outvotes three wrong readings, and the disk is small enough to follow a tile's vignetting.
CONTRAST_NEIGHBOURHOOD_PITCHES = 1.5
#: Every this-many-th interior pixel joins the pooled texture threshold: a thousand nanowells of 20,000 pixels each
#: would otherwise be held at once, and the threshold is a median and a spread, which a sample measures as well.
THRESHOLD_STRIDE = 4


#: A site's floor-to-surround contrast within this factor of its tile's median is a nanowell's; one beyond it — bare
#: floor outside the array, a bubble, a nanowell under a sheet of cells — has no say in its neighbours' gain and takes
#: theirs. Vignetting moves a contrast across a tile by well under this.
PLAUSIBLE_CONTRAST_FACTOR = 2.0


def _plausible(contrasts, owners) -> list:
    """*contrasts* with each one more than ``PLAUSIBLE_CONTRAST_FACTOR`` from its tile's median replaced by NaN.

    Found by the end-to-end lane: a site outside the array is bare floor, its contrast near zero, and its same-tile
    neighbourhood was mostly sites like it, so its median contrast gave a gain of 0.003 and the noise there, divided
    by it, was segmented as a cell."""
    values = np.asarray(contrasts, dtype=np.float64)
    owners = np.asarray(list(owners), dtype=object)
    out = values.copy()
    for owner in dict.fromkeys(owners.tolist()):
        mine = owners == owner
        finite = values[mine & np.isfinite(values)]
        if not len(finite):
            continue
        typical = float(np.median(finite))
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = values / typical
        out[mine & ~((ratio >= 1 / PLAUSIBLE_CONTRAST_FACTOR) & (ratio <= PLAUSIBLE_CONTRAST_FACTOR))] = np.nan
    return out.tolist()


def _smoothed_offsets(levels: dict, order, owner_of, centres, nanowells: set, radius_um: float) -> dict:
    """*levels* with each site's offset replaced by the median of the offsets within *radius_um* of it on its own
    tile, read from nanowells only (``nanowell_measure.neighbourhood_median``); its gain is left as it is.

    A site's offset comes from its own surround, the lattice ring just outside it, and one ring misread — debris on
    the lattice, the phase halo beside a wall, a crop corner off the tile — moved that one nanowell's floor and
    nothing else: a surround read 0.12 too bright put a nanowell's floor below the flat level between sites while
    its neighbour's stayed put, and one read 0.08 too dark lost 6 of its 20 cells with no other site changed. Light
    added across a well changes over many nanowells, so the median over the six neighbours still follows it and
    outvotes up to three wrong readings, as it does for the gain. Bare floor outside the array (a site not in
    *nanowells*) lends no offset but takes its neighbours'. A site with fewer than three offsets in reach takes its
    tile's median, as its gain does; only a tile with no nanowell offset at all leaves each site its own."""
    from pipeline import nanowell_measure as nm

    offsets = [levels[i][0] if levels.get(i) is not None and i in nanowells else math.nan for i in order]
    smoothed = nm.neighbourhood_median(centres, offsets, radius_um, groups=owner_of)
    return {i: None if levels.get(i) is None else
            ((float(o) if math.isfinite(o) else levels[i][0]), levels[i][1]) for i, o in zip(order, smoothed)}


def _site_levels(located, images, template, fitted: dict, geoms, pitch_um: float, identifiable: bool):
    """``(site index -> (offset, gain), tiles none of whose sites gave a level, the sites whose own contrast is a
    nanowell's)`` for :func:`_normalise`.

    *fitted* maps each tile to the sites it owns that the template covers. A site with no level of its own (its
    surround off the image) takes its tile's median level."""
    from pipeline import nanowell_measure as nm

    px = located.pixel_size_um
    order, owner_of, surrounds, contrasts = [], [], [], []
    for owner, sites in fitted.items():
        if not sites:
            continue
        crops = nm.site_crops(images[owner], px, np.array([g.centre_um for g in sites]), template.half)
        floors, rims = nm.crop_levels(crops, template.half)
        for g, floor, surround in zip(sites, floors, rims):
            contrast = nm.site_contrast(float(floor), float(surround))
            order.append(g.index)
            owner_of.append(owner)
            surrounds.append(float(surround))
            contrasts.append(math.nan if contrast is None else contrast)
    nanowells = set()                                  # sites whose own contrast is a nanowell's
    if identifiable and order:
        plausible = _plausible(contrasts, owner_of)
        nanowells = {i for i, c in zip(order, plausible) if math.isfinite(c)}
        centres = [geoms[i].centre_um for i in order]
        radius = CONTRAST_NEIGHBOURHOOD_PITCHES * pitch_um
        smoothed = nm.neighbourhood_median(centres, plausible, radius, groups=owner_of)
        levels = {i: nm.site_level(s, c, template) for i, s, c in zip(order, surrounds, smoothed)}
        levels = _smoothed_offsets(levels, order, owner_of, centres, nanowells, radius)
    else:                                                       # brightness alone: the surround sets the gain
        levels = {}
        for i, surround in zip(order, surrounds):
            gain = nm.site_gain(surround, 0.0, template)
            levels[i] = None if gain is None else (0.0, gain)
    out, no_gain = {}, []
    for owner, sites in fitted.items():
        mine = {g.index: levels.get(g.index) for g in sites}
        known = [v for v in mine.values() if v is not None]
        if not known:
            if sites:
                no_gain.append(owner)
            continue
        fallback = (float(np.median([o for o, _ in known])), float(np.median([v for _, v in known])))
        out.update({i: v if v is not None else fallback for i, v in mine.items()})
    return out, no_gain, nanowells


def _normalise(located, images, template, by_tile, geoms, channels, pitch_um: float) -> Normalisation:
    """Each site's ``(offset, gain)``, the timepoint's texture threshold and fluorescence backgrounds (``MEASURE_VERSION``
    5), none of which needs an empty nanowell (tracker L1).

    Each site's gain comes from its floor-to-surround contrast (its floor is its modal level, the bare floor's under
    its cells), taken as the median over the sites within ``CONTRAST_NEIGHBOURHOOD_PITCHES`` of it on the same tile
    (``nanowell_measure.neighbourhood_median``), so one nanowell whose floor is lost under cells does not set its own;
    its offset then comes from its own surround (``nanowell_measure.site_level``) and is replaced by the median of its
    same-tile neighbours' (:func:`_smoothed_offsets`), so one misread surround does not move one nanowell alone. Light
    added unevenly across the well cancels from a contrast and is carried by the nanowells' surrounds, which one
    offset per tile could not follow and a median over six neighbours still does. Where the template's floor and surround are too alike to tell an offset from a gain, every site is
    corrected for brightness alone. Divided by the template after that, the bare floor reads 1 on every tile, so one
    texture threshold, from every site's interior with the cells clipped away, serves the whole timepoint. A
    fluorescence channel's background is each tile's median floor level.
    """
    from pipeline import nanowell_measure as nm

    px = located.pixel_size_um
    out = Normalisation()
    tiles = [t for t in by_tile if t is not None]
    identifiable = nm.offset_identifiable(template)
    fitted = {t: _local_fit(t, by_tile[t], located, geoms, template)[0] for t in tiles}
    out.levels, no_gain, nanowells = _site_levels(located, images, template, fitted, geoms, pitch_um, identifiable)
    departures, floors = [], {c: {} for c in (channels or {})}
    for owner in tiles:
        mine = [g for g in fitted[owner] if g.index in out.levels]
        # The floor's texture and level are read from nanowells only: bare floor outside the array divided by the
        # template is no nanowell's floor (every site, when no contrast could be judged).
        floor_sites = [g for g in mine if not nanowells or g.index in nanowells]
        if not floor_sites:
            continue
        rim = nm.rim_suppress(images[owner], px, template, mine, levels=out.levels)
        departures += nm.site_departures(rim, px, floor_sites, stride=THRESHOLD_STRIDE)
        for channel, load in (channels or {}).items():
            image = load(owner)
            levels = {} if image is None else nm.site_floor_levels(image, px, floor_sites)
            if levels:
                floors[channel][owner] = float(np.median(list(levels.values())))
    out.threshold, cell_share = nm.threshold_from_departures(departures)
    out.tile_backgrounds = {c: v for c, v in floors.items() if v}
    out.backgrounds = {c: float(np.median(list(v.values()))) for c, v in out.tile_backgrounds.items()}
    offsets = [o for i, (o, _) in out.levels.items() if i in nanowells]
    out.block = {"measure_version": nm.MEASURE_VERSION,
                 "offset": "per nanowell" if identifiable else "not identifiable",
                 "contrast_neighbourhood_um": CONTRAST_NEIGHBOURHOOD_PITCHES * pitch_um if identifiable else None,
                 # Where on the well light was added, and how much: the spread of the offsets the nanowells were
                 # given (bare floor outside the array and the like left out: its offset describes no nanowell).
                 "offset_range": ([float(np.percentile(offsets, 5)), float(np.percentile(offsets, 95))]
                                  if identifiable and offsets else None),
                 "nanowells_levelled": len(out.levels), "tiles_without_gain": sorted(no_gain),
                 "texture_threshold": out.threshold, "cell_share": cell_share}
    if not identifiable:
        out.notes.append("The template's floor and surround are too alike to tell an offset from a gain: each "
                         "nanowell is corrected for brightness only")
    if no_gain:
        out.notes.append(f"No nanowell's surround could be read on tile(s) {', '.join(sorted(no_gain))}: their "
                         "nanowells cannot be brought to the template's levels and are unmeasured")
    if cell_share is not None and cell_share > 0.5:
        out.notes.append(f"{cell_share:.0%} of the nanowells' floor reads as cells: the texture threshold assumes the "
                         "bare floor is the larger part, so confluence here may read low")
    return out


def _tile_contents(owner, indices, located, images, settings, geoms, addresses, template, segment, channels,
                   kind=None, norm: "Normalisation | None" = None, on_segmented=None, keep=None):
    """Counts, confluence, area estimate, fluorescence and focus for the sites one tile owns, and its cell rows.

    *segment* ``None`` counts no cells: every site keeps its confluence (texture, no segmentation), fluorescence and
    focus, its ``n_cells`` and area estimate are ``None`` (not counted, never zero) and there are no cell rows.
    *on_segmented*, when given, is ``f(n)``, called as *n* more of the tile's nanowells are segmented. *keep*, when
    given, is ``(wanted, kept)``: each wanted site's crop as the segmenter saw it is put in *kept* (:func:`_keep_crops`)."""
    from pipeline import nanowell_measure as nm
    from pipeline.nanowell_sites import rim_focus

    px = located.pixel_size_um
    left, top = located.positions[owner]
    norm = norm or Normalisation()
    fit_sites, too_large = _local_fit(owner, indices, located, geoms, template)
    notes = [f"{len(too_large)} site(s) on tile {owner} exceed the template and are unmeasured"] if too_large else []
    # The template is in units of a site's own surround, so a site with no level to bring it there would be divided
    # in raw units and read as a nonsense departure: it is unmeasured instead (named in the timepoint's notes).
    fit_sites = [g for g in fit_sites if g.index in norm.levels] if norm.levels or norm.block else fit_sites
    if not fit_sites:
        return {}, {}, notes
    rim = nm.rim_suppress(images[owner], px, template, fit_sites, levels=norm.levels)
    # The timepoint's one threshold, from every tile's nanowells (``_normalise``), not this tile's alone.
    confluence = dict(zip((g.index for g in fit_sites), nm.site_confluence(rim, px, fit_sites, norm.threshold)))
    # Each site as the segmenter receives it, written beside its count so a zero the segmenter could not have
    # avoided (cells a few per cent of the tile's range) is told apart from an empty nanowell.
    # Scaled (and, under the nanowells scale, rims and lattice blanked) as the segmenter is given it.
    seen, lowhigh = nm.segmenter_scale(nm.segmenter_input(images[owner], rim, settings.segment_on), px, fit_sites,
                                       settings.segment_scale)
    view = nm.segmenter_view(seen, px, fit_sites, lowhigh=lowhigh)
    channel_images = {c: img for c, f in (channels or {}).items() if (img := f(owner)) is not None}
    counts = area = None
    table: dict = {}
    if segment is not None:
        tick = on_segmented or (lambda _n: None)
        if settings.segment_scope == "nanowells":
            labels = nm.segment_sites(seen, px, fit_sites, segment, on_site=lambda: tick(1), lowhigh=lowhigh)
        else:
            labels = nm.segment_tile(seen, segment, lowhigh=lowhigh)
            tick(len(fit_sites))
        counts = nm.count_cells(labels, px, fit_sites, owned={g.index for g in fit_sites})
        _keep_crops(keep, seen, labels, px, fit_sites, addresses, lowhigh, counts)
        area = nm.area_estimate(labels, px, fit_sites, counts)
        table = _cell_rows(labels, px, fit_sites, counts, addresses, owner, (left, top), channel_images, channels,
                           kind)
    # A tile none of whose nanowells gave a floor level takes the well's (named in the timepoint's notes).
    fluorescence = {c: {f.index: f for f in nm.site_fluorescence(
        img, px, fit_sites, background=norm.tile_backgrounds.get(c, {}).get(owner, norm.backgrounds.get(c)))}
        for c, img in channel_images.items()}
    contents = {g.index: {"n_cells": None if counts is None else counts[g.index].n_cells,
                          "confluence": confluence[g.index],
                          "interior_imaged": nm.interior_imaged(images[owner].shape, px, g),
                          "area": None if area is None else area[g.index],
                          "fluor": {c: f[g.index] for c, f in fluorescence.items()},
                          "focus": rim_focus(images[owner], px, g),
                          "level": norm.levels.get(g.index), "view": view[g.index]} for g in fit_sites}
    return contents, table, notes


def _keep_crops(keep, seen, labels, px, fit_sites, addresses, lowhigh=None, counts=None) -> None:
    """Put each wanted site's crop of *seen* — on the 0-1 scale the segmenter was given it at: *lowhigh*, else its
    tile's 1st-99th percentiles — and of *labels* into ``keep = (wanted, kept)``, with the circle a cell's centre
    must lie in to be counted (``circle``: centre and radius in the crop's pixels) and the labels that were counted
    (``counted``, from *counts*). Nothing when *keep* is ``None``."""
    if keep is None:
        return
    from pipeline import nanowell_measure as nm

    wanted, kept = keep
    seen = np.asarray(seen, dtype=float)
    finite = seen[np.isfinite(seen)]
    if not finite.size:
        return
    lo, hi = lowhigh if lowhigh is not None else np.percentile(finite, nm.SEGMENTER_PERCENTILES)
    for g in fit_sites:
        win = nm.crop_window(g, px, seen.shape) if g.index in wanted else None
        if win is not None:
            cx, cy = g.centre_um[0] / px - 0.5 - win[1].start, g.centre_um[1] / px - 0.5 - win[0].start
            kept[g.index] = {"kind": wanted[g.index], "address": addresses[g.index],
                             "image": ((seen[win] - lo) / (hi - lo if hi > lo else 1.0)).astype(np.float32),
                             "labels": np.asarray(labels[win]),
                             "circle": (float(cx), float(cy), nm.INTERIOR_FRACTION * g.radius_um / px),
                             "counted": frozenset(counts[g.index].labels) if counts and g.index in counts
                             else frozenset()}


def _cell_rows(labels, px, fit_sites, counts, addresses, owner, origin, channel_images, channels, kind) -> dict:
    """The cell table for the cells one tile's segmentation counted, in the well's columns."""
    from pipeline import nanowell_measure as nm

    table = nm.cell_table(labels, px, fit_sites, counts, {g.index: addresses[g.index] for g in fit_sites},
                          tile_origin_um=origin, intensity=channel_images or None)
    n = len(table["label"])
    table["fov"] = [owner] * n
    for channel in (channels or {}):
        table.setdefault(f"mean_{channel}", [None] * n)       # no image for this tile: no value, not a short column
    for old, new in (("address_q", "nw_q"), ("address_r", "nw_r"), ("x_um", "well_x_um"), ("y_um", "well_y_um")):
        table[new] = table.pop(old)
    located_at = [addresses[i] or (None, None) for i in table["site_index"]]
    table["nw_ring"] = [lattice_ring(q, r, kind) for q, r in located_at]
    table["nw_label"] = [None if q is None else f"{q}_{r}" for q, r in located_at]
    return table


def _levelling_columns(got) -> dict:
    """How a site was levelled before segmenting, and how the segmenter saw it; every field ``None`` when the site
    was not measured (``SITE_COLUMNS`` documents them)."""
    level = None if got is None else got.get("level")
    view = None if got is None else got.get("view")
    return {"level_offset": None if level is None else level[0], "level_gain": None if level is None else level[1],
            "segmenter_floor": None if view is None else view.floor,
            "segmenter_contrast": None if view is None else view.contrast}


def _site_row(timepoint, i, address, g, v, c, got, state, owner, detected, fused, channels, kind=None) -> dict:
    q, r = address if address is not None else (None, None)
    row = {"timepoint": timepoint, "nw_q": q, "nw_r": r, "nw_ring": lattice_ring(q, r, kind),
           "well_x_um": g.centre_um[0], "well_y_um": g.centre_um[1], "owner": owner,
           "radius_um": g.radius_um, "geometry": g.geometry, "detected": bool(detected),
           "fused": fused, "imaged_fraction": c.imaged_fraction, "visible_fraction": v.visible_fraction,
           "state": state.state, "partially_visible": state.partially_visible,
           "focus": None if got is None else got["focus"], "n_cells": state.n_cells,
           "confluence": state.confluence,
           "area_est_cells": None if got is None or got["area"] is None else got["area"].area_count,
           "count_disagreement": None if got is None or got["area"] is None else got["area"].count_disagreement,
           **_levelling_columns(got)}
    for channel in (channels or {}):
        f = None if got is None else got["fluor"].get(channel)
        row[f"{channel}_integrated"] = None if f is None else f.integrated
        row[f"{channel}_mean_bg"] = None if f is None else f.mean_minus_background
    return row


def measure_timepoint(located: Located, images: dict, settings: NanowellSettings, array_radius_um: float | None, *,
                      segmenter=None, channels: dict | None = None, earliest_template=None,
                      roster=None, count_cells: bool = True, on_segmented=None, inspect: dict | None = None
                      ) -> Measured:
    """Rows for every roster site of a located timepoint, and the cells inside them.

    *array_radius_um* is the well's one shared array-model radius (so every timepoint has the same
    roster); for an inferred array that is not a circle it is ``None`` and *roster* is the well's shared set of
    addresses instead. *segmenter* maps a rim-suppressed tile to a label image (default: Cellpose's label-free
    path). *count_cells* ``False`` segments nothing: the sites keep their states, confluence and fluorescence, and
    carry no count (``n_cells`` ``None``, so a seen site is ``measured`` with no number, never ``empty``) — what
    Test detection shows when Cellpose cannot be loaded. *channels* maps a fluorescence channel name to
    ``f(tile_id) -> image | None``. A timepoint
    that is not ``ok`` (no origin) is measured on every site of its array, with no address
    (:func:`_unaddressed_positions`). *on_segmented*, when given, is ``f(done, total)``: how many of the timepoint's
    imaged nanowells have been segmented, out of all of them (:class:`_SiteCount`, which says when it is called).
    *inspect*, when given, is filled with the crops of a sample of nanowells as the segmenter saw them
    (``nanowell_inspect.inspection_sites``): what ``nanowells/<well>_segmenter_input.png`` draws.
    """
    from pipeline import nanowell_measure as nm
    from pipeline.nanowell_detect import FITTED, MIN_REFINE_RADIUS_PX
    from pipeline.nanowell_sites import check_format
    from pipeline.tile_grid import ownership_report

    px = located.pixel_size_um
    if located.diameter_um is not None and located.diameter_um != settings.diameter_um:
        settings = replace(settings, diameter_override_um=located.diameter_um)
    addressed = located.status == OK
    if addressed:
        addresses, points_m = _roster_positions(located, array_radius_um, roster)
    else:
        # No origin: every site of the array, with no address (they cannot be joined over time) — tracker F8.
        points_m = _unaddressed_positions(located, array_radius_um, settings)
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
    template = _pooled_template(located, images, settings, geoms, by_tile, visibility, coverage, earliest_template)
    if template is None:
        out.notes.append("No template could be built (no fully imaged, visible site): contents are undefined")
    else:
        out.template = {"source": template.source, "n_sites": template.n_sites, "candidates": template.candidates,
                        "_object": template}
        if template.source != nm.SOURCE_EARLIEST:                  # its own: what flag_template_drift compares
            out.template["floor_ratio"] = nm.floor_ratio(template)
    segment = (segmenter or nm.cellpose_segmenter(settings.segmenter_diameter_px(px), model_type=settings.cellpose_model)
               ) if count_cells else None
    norm = None
    if template is not None:
        norm = _normalise(located, images, template, by_tile, geoms, channels, located.pitch_um or settings.pitch_um)
        out.levels = norm.levels
        out.template["normalisation"] = norm.block
        out.notes += norm.notes
        for channel, well_level in norm.backgrounds.items():
            borrowed = sorted(t for t in by_tile if t is not None and t not in norm.tile_backgrounds.get(channel, {}))
            if borrowed:
                out.notes.append(f"{len(borrowed)} tile(s) gave no {channel} floor level of their own "
                                 f"({', '.join(borrowed)}): their fluorescence background is the well's")
    contents: dict[int, dict] = {}
    imaged = {owner: indices for owner, indices in by_tile.items() if owner is not None and template is not None}
    sites = _SiteCount(on_segmented if segment is not None else None, sum(map(len, imaged.values())))
    keep = _inspection(inspect, points_m, imaged, segment)
    for owner, indices in imaged.items():
        got, table, notes = _tile_contents(owner, indices, located, images, settings, geoms, addresses, template,
                                           segment, channels, kind, norm, on_segmented=sites.tick, keep=keep)
        contents.update(got)
        out.notes += notes
        for name, values in table.items():
            out.cells.setdefault(name, []).extend(values)
    sites.close()
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
    frame_change: str = ""                               # a new timepoint off the kept timepoints' frame: which, how far
    diameter_source: str | None = None                   # where the well's one diameter came from (_settle_diameter)
    #: (timepoint, {site index: crop as the segmenter saw it}) for the first timepoint measured (pipeline.nanowell_inspect)
    inspection: tuple | None = None


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
                "earliest's (beyond a factor of two). Cells may now cover most of most nanowells' floor, so the "
                "template may be cell-coloured and this timepoint's counts and confluence biased")


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


def _say_locating(note, record: dict, well: str, timepoint: str):
    """Tell *note* a timepoint's tiles are being read, placed and registered, and return ``f(status)`` that says
    how long it took. That step is the longest quiet stretch of a nanowell well — minutes on a large one — and
    printed nothing until it had finished, so the run read as stuck. A no-op without *note*."""
    if note is None:
        return lambda _status: None
    started = time.monotonic()
    note(f"{timepoint}: reading, placing and registering {_tile_count(record, well, timepoint)} tile(s)")
    return lambda status: note(
        f"{timepoint}: tiles placed and registered in {time.monotonic() - started:.0f} s ({status})")


def _tile_count(record: dict, well: str, timepoint: str) -> int:
    """How many tiles the record holds for this (well, timepoint): what :func:`_images_for` will try to read."""
    return sum(1 for tile in record["tiles"] if tile["well"] == well and tile["timepoint"] == timepoint)


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


#: Why an inferred array's addresses were moved onto another timepoint's (:func:`_settle_inferred`).
_INFERRED_SHIFT_WHY = "its own middle site was not the array's (a bubble or the image's edge cut its outline short)"


def _shift_origin(located: Located, shift, onto: str, why: str = _INFERRED_SHIFT_WHY) -> None:
    """Move *located*'s addresses by *shift* lattice steps: the same sites, each named ``(q + dq, r + dr)``, and say
    so in its notes, *why* being the reason the move was needed."""
    from pipeline.nanowell_lattice import index_points

    basis = np.column_stack((located.fit.a1, located.fit.a2))
    located.origin_montage = tuple(np.asarray(located.origin_montage) - basis @ np.asarray(shift, dtype=float))
    located.indexed = index_points(located.detected_common - np.asarray(located.montage.origin_um), located.fit,
                                   located.origin_montage)
    located.grid["origin_stage_um"] = _lattice_block(located, OK, "")["origin_stage_um"]
    located.grid.update(origin_shift=[int(shift[0]), int(shift[1])], origin_registered_to=onto)
    located.notes.append(f"Addresses moved by ({int(shift[0])}, {int(shift[1])}) lattice steps to overlay this "
                         f"timepoint's array on {onto}'s: {why}")


def _unaddress(outcome, tp: str, reason: str) -> None:
    """Leave *tp* measured on the nanowells it saw with no address, saying why: its origin cannot be trusted. The
    end-of-run pass can still anchor it from the rest of the plate (``nanowell_task.plan_reanchor``)."""
    located = outcome.located[tp]
    located.status, located.reason, located.origin_montage, located.indexed = UNADDRESSED, reason, None, ()
    located.origin_method = located.origin_agreement = None
    outcome.timepoints[tp].update(status=UNADDRESSED, reason=reason, origin_method=None, origin_agreement=None,
                                  origin_stage_um=None)


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
    """One diameter for the whole well's sites (review X35-R14, tracker F3): the earlier run's when it kept
    timepoints, else the median of the timepoints' measured diameters, else the setting's. Chosen per timepoint it
    flipped within a well (125 um at some timepoints, the setting's 100 at others), so ownership, margins and
    templates differed between them and the template-drift check compared nanowells of different sizes. The median
    is used whether or not it agrees with the setting (tracker F4); a disagreement beyond ``DIAMETER_AGREEMENT`` is
    said. Applied to every *measurable* timepoint, whose note says so; the per-timepoint comparison stays in its
    grid record. A diameter fitted at full resolution outranks one the inferred geometry read only from the
    montage's light blobs (``diameter_rough_only``, about a tenth small). Where the diameter came from — ``measured``,
    ``rough``, ``format`` (nothing could be measured) or an earlier run's source — is kept on the outcome and in every
    timepoint's geometry block, so the plate's comparison counts only measured wells (review F20b, F20c)."""
    from pipeline.nanowell_infer import DIAMETER_AGREEMENT, compare_given

    blocks = [outcome.timepoints[tp].get("geometry", {}) for tp in measurable]
    fitted = [(b.get("diameter_check") or {}).get("measured_um") for b in blocks if not b.get("diameter_rough_only")]
    rough = [(b.get("diameter_check") or {}).get("measured_um") for b in blocks if b.get("diameter_rough_only")]
    measured = [d for d in fitted if d] or [d for d in rough if d]      # a fitted diameter outranks a rough one
    kept = (reference or {}).get("diameter_used_um")
    consequence = "nanowells are fitted, owned and measured at the measured diameter"
    note = ""
    source = "format"
    if kept:
        used = float(kept)
        # The earlier run's own source; a record written before sources were kept says only that it was kept.
        source = (reference or {}).get("diameter_source") or "kept"
        if used != settings.diameter_um:
            note = f"Nanowells are fitted, owned and measured at {used:.1f} um, the diameter the earlier run kept"
    elif measured:
        check = compare_given(float(np.median(measured)), settings.diameter_um, DIAMETER_AGREEMENT,
                              "nanowell diameter (median over the well's timepoints)", consequence)
        used, note = check["measured_um"], check["note"]
        source = "measured" if any(d for d in fitted) else "rough"
    else:
        used = settings.diameter_um
    for tp in measurable:
        located = outcome.located[tp]
        located.notes = [n for n in located.notes if not n.startswith("The measured nanowell diameter")]
        located.notes += [note] if note else []
        located.diameter_um = used
        outcome.timepoints[tp].setdefault("geometry", {}).update(diameter_used_um=used, diameter_source=source)
    outcome.diameter_source = source
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
        _unaddress(outcome, tp, reason)
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


def misanchored(detected: dict, kept=()) -> dict:
    """``timepoint -> step`` for each timepoint whose detected sites match the other timepoints' better moved by a
    lattice step than where they are: its origin is a step off (review X35a-R2). A timepoint whose sites the others
    do not overlap better anywhere is not listed. With two timepoints a step apart, both are listed: which of them
    is wrong cannot be told from the sites alone. *kept* are the sites an earlier run's kept timepoints detected:
    the timepoints of an incremental run are judged against them too, and they are never listed themselves (review
    X35a-R7). A run settles such steps before deciding the rim (:func:`_settle_steps`), so here a timepoint is listed
    only when that could not: one further off than ``nanowell_frames.STEP_REACH``, or against an earlier run's
    sites."""
    from pipeline.nanowell_frames import best_step

    out = {}
    for tp, mine in detected.items():
        others = set(kept).union(*(d for t, d in detected.items() if t != tp))
        if not others:
            continue
        step = best_step(mine, others)
        if step != (0, 0):
            out[tp] = step
    return out


def _detected_addresses(located: Located) -> set:
    """The addresses of the nanowells *located* detected that sit on a lattice site."""
    return {(e.q, e.r) for e in located.indexed if e.q is not None}


def _settle_steps(outcome, anchored, kept: dict | None = None) -> tuple[list, list, dict]:
    """Put a default well's anchored timepoints in one frame before its rim is decided: ``(anchored, unaddressed
    timepoints this made, steps applied)``.

    Every timepoint is anchored on its own evidence, so one whose origin landed a site off (an array centre a fitted
    circle left loose, a square misread) names every nanowell after its neighbour, and Review's join by address then
    pairs each nanowell with its neighbour from that timepoint on. The timepoints are grouped by the lattice step
    their detected nanowells sit at against one another's (``nanowell_frames.settle_frames``, measured against the
    timepoint that detected most); the frame kept is the one holding the best-ranked origin (``ORIGIN_RANK``), then the most
    timepoints, so a square-anchored baseline is never moved onto later timepoints that all share one wrong array
    centre. Every other timepoint's addresses are moved onto it, and its grid block says by how much. When two frames
    tie on both, nothing tells which is right: the timepoints of every frame are left unaddressed, measured with no
    address, rather than one of them being guessed and written as ``ok``.

    *kept* is an earlier run's evidence (incremental: ``detected`` sites and the kept timepoints' ``origins``,
    ``nanowell_task._kept_evidence``). Its timepoints are a frame of their own, measured against directly. When it
    wins, a new timepoint off it is moved onto it, with no re-analysis; when a new frame outranks it or ties with it,
    nothing is moved: ``outcome.frame_change`` says which timepoints sit how far off, and the caller analyses the
    well again over every timepoint, so that the frame is chosen with all of them in view."""
    from pipeline.nanowell_frames import KEPT_OUTRANKED, SHIFT, TIE, settle_frames
    from util.nanowell_anchors import origin_phrase

    kept = kept or {}
    found = {tp: _detected_addresses(outcome.located[tp]) for tp in anchored}
    methods = {tp: outcome.located[tp].origin_method for tp in anchored}
    decision = settle_frames(found, methods, kept.get("detected"), kept.get("origins"))
    if decision.verdict == KEPT_OUTRANKED:
        outcome.frame_change = "; ".join(f"{tp}'s nanowells sit {decision.onto[tp]} lattice steps from the earlier "
                                         "run's" for tp in anchored if decision.onto[tp] != (0, 0))
        return list(anchored), [], {}
    if decision.verdict == TIE:
        names = ", ".join("/".join(frame) for frame in decision.frames)
        for tp in anchored:
            _unaddress(outcome, tp, f"Its nanowells sit a lattice step from those of another timepoint anchored as "
                                    f"well ({names}), so which origin is right cannot be told: measured with no "
                                    "address rather than under its neighbours' addresses. An anchor set by hand on "
                                    "the central square at one of them settles it")
        return [], list(anchored), {}
    if decision.verdict != SHIFT:
        return list(anchored), [], {}
    reference = decision.reference
    reference_method = methods.get(reference) or (kept.get("origins") or {}).get(reference)
    for tp, shift in decision.shifts.items():
        located = outcome.located[tp]
        _shift_origin(located, shift, reference,
                      f"its own origin ({origin_phrase(located.origin_method)}) landed a site off, and "
                      f"{reference}'s ({origin_phrase(reference_method)}) ranks above it or is shared by more "
                      "timepoints")
        outcome.timepoints[tp].update({k: located.grid[k]
                                       for k in ("origin_stage_um", "origin_shift", "origin_registered_to")})
    return list(anchored), [], dict(decision.shifts)


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

    A timepoint whose detections sit a lattice step from the other timepoints' (:func:`misanchored`) is anchored a
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
    kept = kept or {}
    off = misanchored(found, kept.get("detected", ())) if len(anchored) > 1 or kept.get("detected") else {}
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
    # Under the evidence rule a band site is left out of a roster only when its whole disk was seen empty, and the
    # rows an earlier run kept exist only for its roster's sites, so a band site absent from the kept roster was seen
    # whole: without this, a new timepoint that does not image it whole put it back, and every incremental run of the
    # well re-analysed it to the same roster with a reason that said it had grown (review X35a-R6).
    kept_out = band - set(map(tuple, kept["roster"])) if kept.get("roster") is not None else set()

    def supported(detected, seen_whole):
        return frozenset(a for a in candidates
                         if distance[a] <= radius_um - pitch / 4
                         or (a in detected and distance[a] <= radius_um + pitch)
                         or (a in band and a not in seen_whole))

    trusted = [tp for tp in anchored if tp not in off]
    detected = set(kept.get("detected", ())).union(*(found[tp] for tp in trusted))
    seen_whole = set(kept.get("imaged", ())).union(kept_out, *(imaged[tp] for tp in trusted))
    return CircleEvidence(supported(detected, seen_whole), {tp: supported(found[tp], imaged[tp]) for tp in anchored},
                          off)


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
        evidence = None
        if kept is not None and anchored:
            evidence = _evidence_roster(outcome.located, anchored, radius_um, settings, {**kept, "roster": roster})
            added, removed = evidence.roster - roster, roster - evidence.roster
            if added or removed:
                outcome.roster_change = (f"the new timepoints' evidence adds {len(added)} nanowell(s) to the roster the "
                                         f"earlier run kept and removes {len(removed)}")
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
    # Against the first that is not off; in an incremental run, the earlier run's reference (None here).
    reference = None if fixed_roster else next((tp for tp in anchored if tp not in evidence.misanchored), None)
    return roster, {tp: (evidence.own[tp] if tp in evidence.misanchored else roster) for tp in anchored}, reference


def _checkpoint_key(outcome, tp, located, radius_um, roster, settings, shifts) -> dict:
    """What a timepoint's checkpoint must match to be taken back: everything its rows depend on beyond the settings
    the store is keyed on — the radius, the record, how it was located, and the roster: for an inferred well with the
    step its addresses were moved by and the diameter (X35-R1, R5, R14); for a default well with its rule, so a
    disk-rule checkpoint is never taken back into an evidence-rule well (X35a). The well's diameter joins it whatever
    the geometry: every geometry now measures at the median over the well's timepoints, which a resumed run over
    another set of timepoints can move (review F21b)."""
    from pipeline.nanowell_measure import MEASURE_VERSION

    # The registration's own key: the tiles' positions decide which pixels every nanowell is measured from, and a
    # timepoint registered again with its well's agreed tile layout has moved a tile a lattice step (tracker U9b).
    key = {"radius_um": radius_um, "record_digest": outcome.timepoints[tp]["record_digest"],
           "located": [located.status, located.origin_method], "measure_version": MEASURE_VERSION,
           "diameter_used_um": outcome.radius.get("diameter_used_um"),
           "registration": (located.grid.get("registration") or {}).get("key")}
    if located.manual_anchor_um is not None:
        key["manual_anchor_um"] = list(located.manual_anchor_um)      # another click may be another site
    if roster is not None and settings.infer:
        key.update(roster=outcome.radius["roster"], origin_shift=list(shifts.get(tp, (0, 0))))
    elif roster is not None:
        key.update(roster=outcome.radius["roster"], roster_rule=ROSTER_RULE_EVIDENCE)
    if not settings.infer and tp in shifts:              # the same origin moved onto another timepoint's frame
        key["origin_shift"] = list(shifts[tp])
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


class _Steps:
    """:func:`run_well`'s work counted as steps for a progress report: every timepoint located, then every one that
    can be measured measured, so ``2 x timepoints`` in all. A timepoint that cannot be measured counts as done once
    that is known, so the count always reaches its total. ``report(done, total)`` is called with ``done == 0`` first,
    which states the total. *site_report*, when given, is ``f(timepoint, done, total)``: a measure step's own progress,
    in nanowells segmented (:class:`_SiteCount`)."""

    def __init__(self, report, n_timepoints: int, site_report=None, note=None, settings=None, *, cellpose=True):
        self.report, self.total, self.done, self.site_report = report, 2 * n_timepoints, 0, site_report
        # The scale is Cellpose's: a run given another segmenter (a test's) has none to state.
        self.note, self.settings, self.inspected = note, (settings if cellpose else None), None
        if report is not None:
            report(0, self.total)

    def inspect(self, timepoint) -> dict | None:
        """The dict the first timepoint measured fills with its sample of crops (``WellOutcome.inspection``); ``None``
        for every later one, so a well keeps one sample however many timepoints it has."""
        if self.inspected is not None:
            return None
        self.inspected = (timepoint, {})
        return self.inspected[1]

    def scale(self, located) -> None:
        """Say, before a timepoint is measured, the scale Cellpose will see it at (:func:`cellpose_scale_text`)."""
        if self.note is not None and self.settings is not None and located.pixel_size_um:
            self.note(cellpose_scale_text(located, self.settings))

    def tick(self, n: int = 1) -> None:
        if self.report is None or n <= 0:
            return
        self.done = min(self.done + n, self.total)
        self.report(self.done, self.total)

    def skip(self, timepoints, measurable) -> None:
        """The timepoints that will not be measured: done once they are located."""
        self.tick(sum(tp not in measurable for tp in timepoints))

    def finish(self) -> None:
        self.tick(self.total - self.done)

    def sites(self, timepoint) -> Callable[[int, int], None] | None:
        """The ``f(done, total)`` :func:`measure_timepoint` reports *timepoint*'s segmented nanowells through."""
        if self.site_report is None:
            return None
        return lambda done, total: self.site_report(timepoint, done, total)


def _inspection(inspect, points, imaged, segment) -> tuple[dict, dict] | None:
    """``(wanted, inspect)`` for :func:`_tile_contents`, or ``None`` when no crops are to be kept (none asked for, or
    nothing is segmented)."""
    if inspect is None or segment is None:
        return None
    from pipeline.nanowell_inspect import inspection_sites

    return inspection_sites(points, [i for indices in imaged.values() for i in indices]), inspect


def cellpose_scale_text(located, settings: NanowellSettings) -> str:
    """The scale a timepoint's images reach Cellpose at, for the run log: the pixel size, the cell diameter in
    pixels, the factor Cellpose resizes every image by to bring that diameter to its models' 30 pixels, and, for a
    count on crops, a crop's side before and after. A count that finds nothing can be at a scale where a cell is a
    few pixels, or a nanowell fills the network's 256-pixel tile; this is the line that says which."""
    from pipeline.nanowell_measure import CROP_REACH

    px = float(located.pixel_size_um)
    cell_px = settings.cell_diameter_um / px
    factor = 30.0 / cell_px if settings.resize != "none" else 1.0
    text = (f"{located.timepoint}: Cellpose {settings.cellpose_model} at {px:.3g} um/px — a cell of "
            f"{settings.cell_diameter_um:g} um is {cell_px:.1f} px, "
            + (f"so Cellpose resizes each image x{factor:.2f}" if settings.resize != "none" else
               "and Cellpose is set not to resize: each image is segmented as imaged"))
    if settings.segment_scope == "nanowells":
        crop = 2 * CROP_REACH * (located.diameter_um or settings.diameter_um) / 2 / px
        text += f"; a nanowell's crop is {crop:.0f} px, {crop * factor:.0f} px as the network sees it"
    return text


class _SiteCount:
    """A timepoint's segmented nanowells counted for a progress report, out of every nanowell its tiles image.

    ``report(done, total)`` is called with ``0`` when the count starts, which states the total, then each time the
    count crosses another tenth of it, and once more with the final count (:meth:`close`), so a timepoint of a few
    hundred nanowells prints about a dozen lines rather than one per nanowell. The final count can stop short of the
    total: a nanowell that is not segmented (larger than the template, or with no level to scale it) is not counted,
    and the shortfall is the report's to show, not to hide."""

    STEPS = 10

    def __init__(self, report, total: int):
        self.report, self.total, self.done, self.said = report, total, 0, None
        if report is not None and total > 0:
            self._say()

    def _say(self) -> None:
        self.said = self.done
        self.report(self.done, self.total)

    def tick(self, n: int = 1) -> None:
        if self.report is None or self.total <= 0 or n <= 0:
            return
        before = self.done * self.STEPS // self.total
        self.done = min(self.done + n, self.total)
        if self.done * self.STEPS // self.total > before:
            self._say()

    def close(self) -> None:
        if self.report is not None and self.total > 0 and self.said != self.done:
            self._say()


def _layout_pairs(located: Located) -> list[tuple[str, str]]:
    """The neighbouring pairs of a located timepoint's tiles whose two tiles registration placed by lattice phase (the
    pairs whose whole-vector choice can go wrong, and can be put right)."""
    from pipeline.tile_register import LATTICE, TileObservation, neighbour_pairs

    if located.plan is None or not located.positions:
        return []
    sources = {t.get("tile"): t.get("source") for t in located.grid.get("tiles") or ()}
    pairs = neighbour_pairs([TileObservation(p.tile_id, (p.left_um, p.top_um), (p.width_um, p.height_um))
                             for p in located.plan.placements])
    return [(i, j) for i, j in pairs if sources.get(i) == LATTICE and sources.get(j) == LATTICE]


def _agree_tile_layouts(outcome: WellOutcome, timepoints, kept_layouts, locate, note=None) -> None:
    """Register again each timepoint whose tiles sit a lattice step from where the well's other timepoints agree.

    The stage runs one tile program at every timepoint, so a pair of neighbouring tiles sits the same distance apart
    each time, to the stage's repeatability. Registration fixes that distance only up to a whole lattice vector; it
    settles the vector from the overlap's content where it can and from the stage's record otherwise, and a stage
    error past half a pitch then places a tile a step off, with every nanowell in it named after a neighbour. The
    timepoints together see it: ``tile_register.agreed_offsets`` takes, for each pair, the offset most of them agree on
    (three timepoints at least, the earlier run's kept ones included from *kept_layouts*), and a timepoint with a pair
    half a pitch or more from it is located again by *locate* (``f(tp, preferred, stem)``) with the agreed offsets,
    which settle every pair its content cannot (``tile_register.choose_step``). Its grid block records the pairs that
    disagreed (``tile_layout``). Content that disagrees with the agreement still wins, with a registration warning:
    it was measured on that timepoint's own pixels.
    """
    from pipeline.tile_register import agreed_offsets, disagreeing_pairs, pair_offsets

    located = {tp: outcome.located[tp] for tp in timepoints if outcome.located[tp].positions}
    pitches = [loc.pitch_um for loc in located.values() if loc.pitch_um]
    if not pitches:
        return
    pitch = float(np.median(pitches))
    layouts = {tp: pair_offsets(loc.positions, _layout_pairs(loc)) for tp, loc in located.items()}
    for tp, positions in (kept_layouts or {}).items():
        if tp not in layouts and positions:
            pairs = [(i, j) for i in positions for j in positions if i < j]
            layouts[f"kept:{tp}"] = {pair: offset for pair, offset in pair_offsets(positions, pairs).items()
                                     if any(pair in mine for mine in layouts.values())}
    agreed = agreed_offsets(layouts, pitch)
    for tp in located:
        wrong = disagreeing_pairs(layouts[tp], agreed, pitch)
        if not wrong:
            continue
        named = ", ".join(f"{i}-{j} ({distance:.0f} um)" for (i, j), distance in wrong)
        if note is not None:
            note(f"{tp}: tiles {named} sit a lattice step from where the well's other timepoints place them; "
                 "registered again with their layout")
        relocated = locate(tp, preferred=agreed, stem="_agreed")
        still = disagreeing_pairs(pair_offsets(relocated.positions, _layout_pairs(relocated)), agreed, pitch)
        outcome.timepoints[tp]["tile_layout"] = {
            "disagreed": [{"pair": f"{i}-{j}", "um": round(distance, 2)} for (i, j), distance in wrong],
            "still_disagree": [{"pair": f"{i}-{j}", "um": round(distance, 2)} for (i, j), distance in still],
            "compared_with": sorted(name for name in layouts if name != tp)}


def run_well(well: str, record: dict, timepoints, load_phase, settings: NanowellSettings, *,
             segmenter=None, channels: dict | None = None, plate_rigid: dict | None = None,
             expected_shape=None, fixed_radius_um: float | None = None, checkpoint=None,
             reference: dict | None = None, earliest_template=None, spill_dir=None, fixed_roster=None,
             kept_evidence: dict | None = None, stored_placements: dict | None = None,
             manual_anchors: Mapping = MappingProxyType({}), progress=None, site_progress=None,
             note=None, kept_layouts: dict | None = None) -> WellOutcome:
    """Locate every timepoint, agree on one array radius, then measure each against it.

    *load_phase* is ``f(timepoint, tile_id) -> image | None``; *channels* maps a fluorescence name to
    ``f(timepoint, tile_id) -> image | None``. *plate_rigid* maps a timepoint to an ``OriginPrediction``
    in that well's montage frame (from the end-of-run pass; absent on a first run). A timepoint whose
    first stage refuses keeps its status and no rows; the others go on. *fixed_radius_um* is the array
    radius an earlier run of this well already shared across its timepoints: an incremental run adds a
    timepoint to the same roster instead of choosing a new one. *checkpoint* has ``load(timepoint, key)``
    (a :class:`Measured` or ``None``) and ``save(timepoint, key, measured)``: a timepoint measured before
    the process was killed is taken back instead of measured again, but only under the same *key* (the
    shared radius, the record's digest and the registration's key), because a different roster or tile placement
    makes it wrong.
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
    *stored_placements* maps a timepoint to ``(folder, grid block)`` pairs from earlier runs' grid records, whose
    registration :func:`locate_timepoint` takes when it was solved from exactly this timepoint's inputs.
    *manual_anchors* maps a timepoint to the anchor a person set for it, in the well frame (``util.nanowell_anchors``).
    Once every timepoint is located, a timepoint whose tiles sit a lattice step from where the well's other timepoints
    agree they sit is registered again with their layout (:func:`_agree_tile_layouts`); *kept_layouts* maps each
    timepoint an earlier run kept to its registered tile positions (``{tile: (left, top)}``), which take part in that
    agreement without being located again. *progress*, when given, is ``f(done, total)``, called as the work goes (:class:`_Steps`); *site_progress*, when
    given, is ``f(timepoint, done, total)``, the nanowells of the timepoint being measured that are segmented so far;
    *note*, when given, is ``f(text)``, told the scale Cellpose sees each timepoint at before it is measured (only when
    the count is Cellpose's, *segmenter* ``None``).
    """
    outcome = WellOutcome(well)
    timepoints = list(timepoints)
    steps = _Steps(progress, len(timepoints), site_progress, note, settings, cellpose=segmenter is None)

    def locate(tp, preferred=None, stem=""):
        images = _images_for(record, well, tp, load_phase)
        located = locate_timepoint(tp, record, well, images, settings, expected_shape=expected_shape,
                                   plate_rigid=(plate_rigid or {}).get(tp), stored=(stored_placements or {}).get(tp),
                                   manual_anchor_um=manual_anchors.get(tp), preferred=preferred)
        if spill_dir is not None and located.montage is not None:
            located.montage = _spill_montage(located.montage, Path(spill_dir), f"{well}_{tp}{stem}")
        outcome.located[tp] = located
        outcome.timepoints[tp] = dict(located.grid) or {"status": located.status, "reason": located.reason}
        outcome.timepoints[tp]["record_digest"] = record_digest(record, well, tp)
        return located

    for tp in timepoints:
        located_after = _say_locating(note, record, well, tp)
        located_after(locate(tp).status)
        steps.tick()
    _agree_tile_layouts(outcome, timepoints, kept_layouts, locate, note)
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
        outcome.radius["diameter_source"] = outcome.diameter_source
        radius_um = None
    else:
        used = _settle_diameter(outcome, anchored + unaddressed, settings, reference)
        shared = _shared_radius(outcome.located, anchored + unaddressed, settings, fixed_radius_um)
        outcome.radius = {"radius_um": shared.radius_um, "status": shared.status,
                          "deviations_um": shared.deviations_um, "flags": shared.flags, "diameter_used_um": used,
                          "diameter_source": outcome.diameter_source}
        radius_um = shared.radius_um
        if radius_um is None:
            # Unaddressed timepoints too: they are measured against the shared radius, so without one they have no
            # rows and must say so in a status the caller counts as failed (roll-up review X27).
            for tp in anchored + unaddressed:
                outcome.timepoints[tp].update(status=NO_LATTICE, reason="No timepoint fitted an array boundary, so "
                                                                       "no roster radius could be shared")
            steps.finish()
            return outcome
        anchored, stepped_off, shifts = _settle_steps(outcome, anchored, kept_evidence)
        unaddressed += stepped_off
        roster, own, circle_reference = _circle_roster(outcome, anchored, radius_um,
                                                       replace(settings, diameter_override_um=used), fixed_roster,
                                                       fixed_radius_um, kept_evidence)
        reference_tp = circle_reference or reference_tp

    consistency = _consistency(outcome.located, anchored, radius_um, reference, own, reference_tp,
                               frozenset(map(tuple, fixed_roster)) if fixed_roster and own is not None else None)

    # The template a timepoint with too few empty nanowells falls back to: an earlier run's (incremental), else the
    # first this run builds in time. A resumed run gets it back from the checkpoint (tracker C16).
    earliest = earliest_template
    measurable = set(anchored) | set(unaddressed)
    steps.skip(timepoints, measurable)
    for tp in [t for t in timepoints if t in measurable]:          # in time, so "earliest" means it
        located = outcome.located[tp]
        key = _checkpoint_key(outcome, tp, located, radius_um, roster, settings, shifts)
        measured = checkpoint.load(tp, key) if checkpoint is not None else None
        if measured is None:
            steps.scale(located)
            images = _images_for(record, well, tp, load_phase)
            loaders = {name: (lambda tile, _f=f, _tp=tp: _f(_tp, tile)) for name, f in (channels or {}).items()}
            measured = measure_timepoint(located, images, settings, radius_um, segmenter=segmenter,
                                         channels=loaders, earliest_template=earliest, roster=roster,
                                         on_segmented=steps.sites(tp), inspect=steps.inspect(tp))
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
        steps.tick()
    flag_template_drift(outcome.timepoints, timepoints)     # the caller re-runs it over kept timepoints too
    steps.finish()
    outcome.inspection = steps.inspected
    return outcome
