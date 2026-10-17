"""Playground — find a nanowell array in one well and suggest the Analyze form's Nanowells settings for it.

Qt-free. The Analyze form's Nanowells section asks for numbers a person usually does not know before the
first run: which plate format, whether the array is the hexagonal lattice in a circle the format assumes or
something to infer, the nanowell diameter and lattice pitch of a custom plate, and the angle of the central
square's edges to a lattice row. Everything here answers those from the image, with the pipeline's own
detectors, so a suggestion is what a run would itself measure:

* :func:`montage_from_archive` stitches one well's tiles at one timepoint exactly as a run does
  (``pipeline.nanowell_run.locate_timepoint`` with an inferred geometry: placement from the acquisition record,
  registration, the montage). Only the montage is kept; the run's own addressing is not, because the point here
  is to measure the plate rather than to test a setting.
* :func:`suggest_from_image` finds the wells by their contrast (``pipeline.nanowell_infer``), fits the lattice
  and the array's outline, scores the central square (``pipeline.nanowell_detect.score_fiducial``), resolves the
  origin the way a run would (square first, then the array's centre), and turns what it measured into the form's
  field values in :class:`NanowellSuggestion`.

**A field this cannot measure is left blank and says so**, rather than filled with a guess: the cell diameter,
the occlusion and clipping thresholds and the margin keep the pipeline's defaults, and the tile grid fields stay
blank whenever an acquisition record places the tiles (the form's own rule: a record wins over them).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import io
import math
from pathlib import Path

import numpy as np

#: The plate formats the pipeline knows, as (option value, label). Read from ``analyze.domain.nanowell_fields``
#: would cross a pane boundary, so the known formats come from the pipeline's own table and the labels are the
#: form's, restated: ``tests/test_playground_nanowell_compute.py`` holds the two together.
FORMAT_LABELS = {"100um": "100 µm nanowells", "50um": "50 µm nanowells", "custom": "Custom size"}
GEOMETRY_LABELS = {"hex_circle": "Hexagonal lattice in a circle", "infer": "Infer from the image"}

#: A measured square-edge angle within this many degrees of a lattice row is reported as the default 0: the run
#: tolerates 15 degrees either way (``nanowell_detect.SQUARE_AXIS_TOLERANCE_DEG``), and a few degrees of
#: measurement scatter is not a reason to send a non-default value (which also changes the run's reuse key).
SQUARE_SNAP_DEG = 5.0
#: Channel names that are the phase (or brightfield) image nanowells are found in, matched case-insensitively.
_PHASE_HINTS = ("phase", "ph", "bf", "bright", "trans", "dic")


@dataclass(frozen=True)
class FieldSuggestion:
    """One row of the Analyze form's Nanowells section: what to type, or blank for the form's default."""
    label: str              # the form's row label
    key: str                # the run option key (``analyze.domain.nanowell_fields``)
    value: str              # what to enter; "" leaves the field blank (its default)
    note: str               # where the value came from, or why it is left blank


@dataclass
class NanowellSuggestion:
    ok: bool
    reason: str = ""
    source: str = ""                                    # what was analysed, in words
    image: np.ndarray | None = None                     # what was analysed (the montage, or the frame)
    um_per_px: float | None = None
    fields: list[FieldSuggestion] = field(default_factory=list)
    facts: list[tuple[str, str]] = field(default_factory=list)   # (what, value): the measurement behind the fields
    notes: list[str] = field(default_factory=list)
    # Overlay, image pixels (continuous: pixel i covers [i, i + 1)).
    wells_px: np.ndarray | None = None                  # wells found
    roster_px: np.ndarray | None = None                 # every lattice site of the array
    roster_found: tuple[bool, ...] = ()
    origin_px: tuple[float, float] | None = None        # the site addresses count from (0,0 / 0.0)
    square_px: tuple[float, float] | None = None        # the central square, when one scored as a square
    radius_px: float | None = None                      # a circular array's radius
    centre_px: tuple[float, float] | None = None        # the array's centre
    diameter_px: float | None = None                    # the measured nanowell diameter

    def as_text(self) -> str:
        """The suggestion as plain text, for the clipboard and the log."""
        if not self.ok:
            return f"Nanowell array not found: {self.reason}"
        lines = [f"Analyze → Nanowells, from {self.source}:"]
        for f in self.fields:
            lines.append(f"  {f.label}: {f.value or '(leave blank)'}" + (f"  — {f.note}" if f.note else ""))
        lines.append("Measured:")
        lines += [f"  {what}: {value}" for what, value in self.facts]
        lines += [f"Note: {n}" for n in self.notes]
        return "\n".join(lines)


def _fmt(value: float, places: int = 1) -> str:
    """A number as a field would hold it: no trailing zeros, no exponent."""
    text = f"{value:.{places}f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _wrap(angle: float, period: float) -> float:
    return (angle + period / 2) % period - period / 2


def choose_format(pitch_um: float, diameter_um: float) -> tuple[str, str]:
    """``(format, note)``: a known format whose diameter and nominal pitch both agree with the measured ones,
    else ``custom``.

    The diameter is held to ``DIAMETER_AGREEMENT`` and the pitch to ``PITCH_AGREEMENT`` — the tolerances a run
    with an inferred geometry reports a disagreement beyond — so a known format is suggested only where a run
    would raise no disagreement against it. Custom carries the measured numbers and is always faithful.
    """
    from pipeline.nanowell_infer import DIAMETER_AGREEMENT, PITCH_AGREEMENT
    from pipeline.nanowell_run import PLATE_FORMATS

    near = None
    for name, (pitch, diameter) in PLATE_FORMATS.items():
        diameter_ok = abs(diameter_um / diameter - 1.0) <= DIAMETER_AGREEMENT
        pitch_ok = abs(pitch_um / pitch - 1.0) <= PITCH_AGREEMENT
        if diameter_ok and pitch_ok:
            return name, f"measured {diameter_um:.1f} µm nanowells on a {pitch_um:.1f} µm pitch match it"
        if diameter_ok:
            near = (name, pitch)
    if near is not None:
        return "custom", (f"the nanowells match the {FORMAT_LABELS[near[0]]} format's size, but the pitch "
                          f"({pitch_um:.1f} µm) is not its {near[1]:g} µm")
    return "custom", "no known format matches the measured size and pitch"


def _phase_channel(channels: list[str]) -> str | None:
    if len(channels) == 1:
        return channels[0]
    for hint in _PHASE_HINTS:
        for channel in channels:
            low = channel.lower()
            if low == hint or (len(hint) > 2 and hint in low):
                return channel
    return None


@dataclass
class ArchiveMontage:
    ok: bool
    reason: str = ""
    well: str = ""
    timepoint: str = ""
    channel: str = ""
    image: np.ndarray | None = None
    covered: np.ndarray | None = None
    um_per_px: float | None = None
    facts: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def archive_contents(zip_path, *, fields=None, sep: str | None = None) -> dict:
    """``{timepoint: {channel: {field: member}}}`` for the images in a well archive.

    *fields* are the filename schema's slots in order (the dataset's, from ``pipeline_info.json``) and *sep* its
    separator; both default to the pipeline's ``experiment_channel_well_fov_timepoint``.
    """
    import zipfile

    from util.image_exts import is_run_input_member
    from util.schema import DEFAULT_SCHEMA, DEFAULT_SEP, parse_schema

    fields = list(fields) if fields else parse_schema(DEFAULT_SCHEMA)
    sep = sep or DEFAULT_SEP
    out: dict = {}
    with zipfile.ZipFile(zip_path) as archive:
        for name in archive.namelist():
            if not is_run_input_member(name):
                continue
            tokens = Path(name).stem.split(sep)
            if len(tokens) != len(fields):
                continue
            meta = dict(zip(fields, tokens))
            if not {"channel", "fov", "timepoint"} <= set(meta):
                continue
            out.setdefault(meta["timepoint"], {}).setdefault(meta["channel"], {})[meta["fov"]] = name
    return out


def _read_member(archive, name: str) -> np.ndarray:
    import tifffile

    image = np.squeeze(np.asarray(tifffile.imread(io.BytesIO(archive.read(name))), dtype=np.float32))
    if image.ndim != 2:
        raise ValueError(f"{Path(name).name} is not a single 2-D plane (shape {image.shape})")
    return image


def montage_from_archive(zip_path, *, timepoint: str | None = None, channel: str | None = None,
                         fields=None, sep: str | None = None) -> ArchiveMontage:
    """One well's tiles at one timepoint stitched as a run stitches them. Never raises on bad data.

    The acquisition record must sit beside the archive (``acquisition.json``, as in a dataset's ``in/``):
    it is what places the tiles. *timepoint* defaults to the well's first in time; *channel* to the one
    named like a phase or brightfield image (or the only one).
    """
    import zipfile

    from pipeline import nanowell_run as nr
    from pipeline.nanowell_task import timepoint_order
    from util.acquisition_record import AcquisitionRecordError, read_record_file
    from util.well_token import canonical_well_label

    zip_path = Path(zip_path)
    well = canonical_well_label(zip_path.stem) or zip_path.stem
    out = ArchiveMontage(False, well=well)
    record_file = zip_path.parent / "acquisition.json"
    try:
        record = read_record_file(record_file)
    except AcquisitionRecordError as exc:
        out.reason = f"The acquisition record beside {zip_path.name} cannot be used: {exc}"
        return out
    if record is None:
        out.reason = (f"There is no acquisition record ({record_file.name}) beside {zip_path.name}, so the tiles "
                      "cannot be placed. Open one tile, or a stitched image, with Open image… and use "
                      "From this frame instead.")
        return out
    order = timepoint_order(record, well)
    if not order:
        out.reason = f"The acquisition record has no tiles for well {well}."
        return out
    try:
        contents = archive_contents(zip_path, fields=fields, sep=sep)
    except (OSError, zipfile.BadZipFile) as exc:
        out.reason = f"{zip_path.name} cannot be read: {exc}"
        return out
    timepoint = timepoint or next((tp for tp in order if tp in contents), order[0])
    if timepoint not in contents:
        layout = (sep or "_").join(fields) if fields else "experiment_channel_well_fov_timepoint"
        out.reason = (f"{zip_path.name} holds no image named for timepoint {timepoint!r} under the filename "
                      f"schema {layout}.")
        return out
    channels = sorted(contents[timepoint])
    channel = channel or _phase_channel(channels)
    if channel is None:
        channel = channels[0]
        out.notes.append(f"No channel is named like a phase image ({', '.join(channels)}); {channel} was used.")
    if channel not in contents[timepoint]:
        out.reason = f"{zip_path.name} has no {channel!r} images at {timepoint}; it has {', '.join(channels)}."
        return out
    out.timepoint, out.channel = timepoint, channel
    try:
        with zipfile.ZipFile(zip_path) as archive:
            images = {fov: _read_member(archive, name) for fov, name in contents[timepoint][channel].items()}
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        out.reason = f"A tile could not be read: {exc}"
        return out
    # An inferred geometry, so nothing about the plate format is assumed: the format named here is only what a
    # run would compare its measurement with, and it decides nothing about the montage.
    settings = nr.NanowellSettings("100um", geometry=nr.GEOMETRY_INFER)
    located = nr.locate_timepoint(timepoint, record, well, images, settings)
    if located.montage is None:
        out.reason = f"The tiles could not be stitched: {located.reason or located.status}"
        return out
    montage = located.montage
    out.ok, out.image, out.covered, out.um_per_px = True, montage.image, montage.covered, montage.um_per_px
    plan = located.plan
    out.facts.append(("Tiles", f"{len(images)} ({plan.rows} × {plan.cols} grid)" if plan is not None
                      else str(len(images))))
    if plan is not None and all(o is not None for o in plan.overlap_um):
        out.facts.append(("Tile overlap", f"{plan.overlap_um[0]:.0f} × {plan.overlap_um[1]:.0f} µm"))
    if located.pixel_size_um:
        out.facts.append(("Tile pixel size", f"{located.pixel_size_um:g} µm"))
    if montage.downsample > 1:
        out.facts.append(("Montage", f"downsampled {montage.downsample}× to {montage.um_per_px:.2f} µm per pixel"))
    return out


def suggest_from_image(image, um_per_px: float, *, covered=None, source: str = "the image",
                       record_places_tiles: bool = False) -> NanowellSuggestion:
    """Measure the array in *image* and suggest the Analyze form's Nanowells fields. Never raises on bad data.

    *covered* marks the pixels that hold image (a montage's coverage); *record_places_tiles* says an acquisition
    record placed the tiles, which is what leaves the tile-grid fields blank rather than unknown.
    """
    from pipeline.nanowell_detect import MIN_SQUARENESS, score_fiducial
    from pipeline.nanowell_infer import ARRAY_CIRCLE, find_wells_by_contrast, infer_array
    from pipeline.nanowell_lattice import index_points, resolve_origin
    from pipeline.nanowell_run import FIDUCIAL_MARGIN
    from util.nanowell_address import NanowellAddress

    image = np.asarray(image, dtype=np.float32)
    out = NanowellSuggestion(False, source=source, image=image, um_per_px=um_per_px)
    if not (isinstance(um_per_px, (int, float)) and math.isfinite(um_per_px) and um_per_px > 0):
        out.reason = "The pixel size must be a positive number of µm."
        return out
    if covered is None:
        covered = np.isfinite(image)
    wells = find_wells_by_contrast(image, um_per_px, covered=covered)
    if not wells.ok:
        rejected = ", ".join(f"{n} {why}" for why, n in wells.rejected.items() if n)
        out.reason = f"No nanowells were found by their contrast: {wells.reason}" + (
            f" (light blobs set aside: {rejected})" if rejected else "") + (
            ". Nanowells are found as light disks on a dark lattice, in a phase or brightfield image; check the "
            "pixel size too, since a wrong one makes every length wrong.")
        return out
    geometry = infer_array(wells.points_um, pixel_size_um=um_per_px, covered=covered,
                           edge_points_um=wells.edge_points_um, diameter_um=wells.diameter_um)
    to_px = lambda xy: np.asarray(xy, dtype=float) / um_per_px          # noqa: E731 - um frame -> continuous px
    out.wells_px, out.diameter_px = to_px(wells.points_um), wells.diameter_um / um_per_px
    if not geometry.ok:
        out.reason = f"Nanowells were found ({len(wells.points_um)}), but no array could be inferred: {geometry.reason}"
        return out
    fit = geometry.fit
    pitch, diameter = float(fit.pitch), float(wells.diameter_um)
    out.roster_px, out.roster_found = to_px(geometry.roster_um), geometry.found
    if geometry.centre_um is not None:
        out.centre_px = tuple(to_px(geometry.centre_um))
    if geometry.radius_um is not None:
        out.radius_px = geometry.radius_um / um_per_px

    # The central square and the origin, as a run resolves them: the square when it clears its margin, else
    # the array's centre (a circle's, or the middle site of a whole polygon).
    candidate, scores = score_fiducial(image, um_per_px, diameter, wells.points_um, centre_um=geometry.centre_um,
                                       search_radius_um=2.2 * pitch if geometry.centre_um is not None else None)
    square = max(scores, key=lambda s: s.coherence) if candidate is not None and scores else None
    if square is not None:
        out.square_px = tuple(to_px(square.position_um))
    resolved = resolve_origin(fit, fiducial=candidate, array_centre=geometry.centre_um, fiducial_margin=FIDUCIAL_MARGIN)
    rings = None
    if resolved.status == "anchored":
        out.origin_px = tuple(to_px(resolved.origin_xy))
        addresses = [p for p in index_points(geometry.roster_um, fit, resolved.origin_xy) if not p.outlier]
        rings = max((NanowellAddress(p.q, p.r, kind=fit.kind).ring for p in addresses), default=0)

    out.fields, edge_value = _field_rows(fit, geometry, square, pitch, diameter, record_places_tiles)
    kind = "hexagonal" if fit.kind == "hex" else "square"
    out.facts = [
        ("Lattice", f"{kind}, pitch {pitch:.1f} µm, row angle {fit.angle_deg:+.1f}°"),
        ("Nanowell diameter", f"{diameter:.1f} µm (median of {len(wells.points_um)} wells found)"),
        ("Array outline", (f"circle, radius {geometry.radius_um:.0f} µm" if geometry.shape == ARRAY_CIRCLE
                           else "polygon (the hull of the wells found)") + (", runs off the image" if geometry.clipped
                                                                             else "")),
        ("Lattice sites", f"{len(geometry.found)} in the array, {sum(geometry.found)} with a well found, "
                          f"{geometry.missing} imaged but dark (a bubble, or not detected)"),
    ]
    if geometry.centre_um is not None:
        cx, cy = geometry.centre_um
        out.facts.append(("Array centre", f"x {cx:.0f}, y {cy:.0f} µm in the image ({cx / um_per_px:.0f}, "
                                          f"{cy / um_per_px:.0f} px)"))
    if square is not None:
        out.facts.append(("Central square", f"squareness {square.coherence:.2f} (a square ≥ {MIN_SQUARENESS:g}), "
                                            f"edges at {square.orientation_deg:+.1f}°"))
    if resolved.status == "anchored":
        ox, oy = resolved.origin_xy
        how = {"fiducial": "the central square", "array_centre": "the array's centre"}.get(resolved.method,
                                                                                            resolved.method)
        out.facts.append(("Address origin (0.0)", f"from {how}, at x {ox:.0f}, y {oy:.0f} µm; "
                                                  f"{rings} ring(s) out to the rim"))
    else:
        out.facts.append(("Address origin (0.0)", f"not resolved: {resolved.reason}"))
        out.notes.append("Addresses cannot be anchored from this image alone; in a run the rest of the plate "
                         "(other wells and timepoints) can anchor them.")
    if resolved.agreement == "conflict":
        out.notes.append("The central square and the array's centre disagree about the origin; the square wins in a "
                         "run, as here.")
    if fit.kind == "hex" and square is not None and edge_value:
        out.notes.append("A hexagonal lattice turned past ±30° folds onto another row, which shifts a measured "
                         "square-edge angle by 30°: check the value against the plate's mask before using it.")
    if geometry.clipped:
        out.notes.append("The array runs off the image, so its outline and site count are lower bounds.")
    if geometry.outliers:
        out.notes.append(f"{geometry.outliers} light blob(s) lie off the lattice and were given no address.")
    out.ok = True
    return out


def _field_rows(fit, geometry, square, pitch: float, diameter: float,
                record_places_tiles: bool) -> tuple[list[FieldSuggestion], str]:
    """The form's rows for what was measured, and the square-edge value suggested ("" for the default)."""
    from pipeline.nanowell_infer import ARRAY_CIRCLE

    plate_format, format_note = choose_format(pitch, diameter)
    circle_hex = fit.kind == "hex" and geometry.shape == ARRAY_CIRCLE
    geometry_choice = "hex_circle" if circle_hex else "infer"
    geometry_note = ("a hexagonal lattice in a circular array: the format's own geometry" if circle_hex else
                     f"a {'hexagonal' if fit.kind == 'hex' else 'square'} lattice in a "
                     f"{'circular' if geometry.shape == ARRAY_CIRCLE else 'non-circular'} array, which only "
                     "Infer from the image addresses")
    if square is None:
        edge_value, edge_note = "", ("no central square was found (hidden, or the plate has none), so the angle "
                                     "cannot be measured: the default 0 is assumed")
    else:
        measured = _wrap(square.orientation_deg - fit.angle_deg, 90.0)
        if abs(measured) <= SQUARE_SNAP_DEG:
            edge_value, edge_note = "", f"measured {measured:+.1f}°, within {SQUARE_SNAP_DEG:g}° of a row: the default 0"
        else:
            edge_value, edge_note = _fmt(round(measured)), f"measured {measured:+.1f}° from the central square's edges"

    custom = plate_format == "custom"
    fields = [
        FieldSuggestion("Mode", "nanowell_format", "Nanowell arrays (on)", "tick it: this well is a nanowell array"),
        FieldSuggestion("Plate format", "nanowell_format", FORMAT_LABELS[plate_format], format_note),
        FieldSuggestion("Array geometry", "nanowell_geometry", GEOMETRY_LABELS[geometry_choice], geometry_note),
        FieldSuggestion("Nanowell diameter (µm)", "nanowell_diameter_um", _fmt(diameter) if custom else "",
                        "measured" if custom else "set by the plate format"),
        FieldSuggestion("Lattice pitch (µm)", "nanowell_pitch_um", _fmt(pitch) if custom else "",
                        "measured" if custom else "set by the plate format"),
        FieldSuggestion("Cell diameter (µm)", "nanowell_cell_diameter_um", "",
                        "not measured here: the default 15, or your cells' size"),
        FieldSuggestion("Margin (µm)", "nanowell_margin_um", "",
                        f"the default, a tenth of the nanowell diameter (~{diameter / 10:.0f} µm)"),
        FieldSuggestion("Square edge angle (°)", "nanowell_square_edge_deg", edge_value, edge_note),
        FieldSuggestion("Occlusion threshold", "nanowell_occluded_below", "", "not measured here: the default"),
        FieldSuggestion("Clipping threshold", "nanowell_clipped_below", "", "not measured here: the default"),
    ]
    grid_note = ("the acquisition record places the tiles, and wins over these" if record_places_tiles else
                 "only for a dataset with no acquisition record: describe its tile grid from the acquisition "
                 "software")
    fields += [FieldSuggestion(label, key, "", grid_note) for label, key in (
        ("Tile grid", "nanowell_grid"), ("Scan order", "nanowell_scan"), ("Tile overlap (%)", "nanowell_overlap_percent"),
        ("Pixel size (µm)", "nanowell_pixel_size_um"))]

    return fields, edge_value
