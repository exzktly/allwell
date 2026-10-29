"""What the identity check (``pipeline/nanowell_identity.py``) says about the wells a figure draws, as data.

Review joins a well's timepoints by nanowell address; the identity check registers each timepoint's montage onto a
reference timepoint's on the central square and confirms every address lands on itself. A figure over wells whose
timepoints were not all confirmed says so in its footer (:func:`identity_note`), because an unverified join looks
exactly like a verified one. A ``contradicted`` timepoint is already kept out of every join
(``series_compute._addressed_grid``); the note names it so its absence is explained.

The page's **Identity** view is computed here too, one well's timepoint against its reference (:func:`identity_pair`):
the Distance map's distances at the reference's measured positions in its common frame (:func:`distance_map`), the
Blink frames resampled into the reference montage's pixels through the registration (:func:`blink_frames`), the
address under the pointer on *Side by side* (:func:`hover_target`), and the registration's correlation surface with
its peak and runner-up (:func:`diagnostics`). ``_identity_render`` draws them; nothing here draws.

Qt-free.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from pipeline.nanowell_identity import CONTRADICTED, GOOD, INCONCLUSIVE, NOT_CHECKED, TOLERANCE_PITCHES

#: Wells named in a footer line at most; the rest are counted.
MAX_NAMED = 6


def _named(items: list[str]) -> str:
    shown = ", ".join(items[:MAX_NAMED])
    return shown + (f" and {len(items) - MAX_NAMED} more" if len(items) > MAX_NAMED else "")


def identity_counts(data, wells) -> dict[str, dict[str, list[str]]]:
    """``verdict -> {well -> [timepoints]}`` over *wells*, in the data's timepoint order."""
    out: dict[str, dict[str, list[str]]] = {}
    identity = getattr(data, "identity", None) or {}
    for well in sorted(set(wells)):
        for tp, (verdict, _why) in (identity.get(well) or {}).items():
            out.setdefault(verdict, {}).setdefault(well, []).append(tp)
    return out


def identity_note(data, wells) -> str:
    """The footer sentence for *wells*: which timepoints the identity check contradicted (left out) and which wells
    it could not confirm (inconclusive or not checked); ``""`` when every timepoint is confirmed or the reference.
    Short, because a footer line is not wrapped: ``Nanowell identity — B02 T3 contradicted (left out); A01, C03 not
    confirmed (see Identity).``"""
    counts = identity_counts(data, wells)
    parts = []
    bad = counts.get(CONTRADICTED) or {}
    if bad:
        parts.append(_named([f"{w} {'/'.join(t)}" for w, t in bad.items()]) + " contradicted (left out)")
    unsure = sorted(set(counts.get(INCONCLUSIVE) or {}) | set(counts.get(NOT_CHECKED) or {}))
    if unsure:
        unchecked = set(counts.get(NOT_CHECKED) or {})
        parts.append(f"{_named(unsure)} " + ("not checked" if unchecked >= set(unsure) else "not confirmed"))
    return f"Nanowell identity — {'; '.join(parts)} (see Identity)." if parts else ""


def all_good(data, wells) -> bool:
    """Whether every timepoint of *wells* is confirmed or the reference."""
    counts = identity_counts(data, wells)
    return all(verdict in GOOD for verdict in counts)


# ── the Identity view ────────────────────────────────────────────────────────
# One well, one timepoint compared with the well's reference timepoint, from the check's sidecar
# (``nanowells/<well>_identity.json``) and the run's own tables: everything the view draws is computed here, and
# ``_identity_render`` only draws it.

#: How far, in pitches, the Distance map's colour scale runs: a nanowell this far off or more is drawn in its top
#: colour. Twice the check's tolerance, so the tolerance sits in the middle of the bar.
DISTANCE_TOP_PITCHES = 0.5
#: The Blink frames label every address whose ``q`` and ``r`` are both multiples of this: a label is wider than a
#: nanowell, so labelling each one buries the image, and one in every third along each axis still covers the whole
#: array, so wrong addresses are seen to jump wherever they are.
LABEL_EVERY = 3
#: The longest side, in pixels, the Blink frames are resampled to: a montage is up to 4096 px a side, and blinking
#: it at full size is a screen's worth of interpolation every half second for no more detail than the axes hold.
BLINK_MAX_SIDE = 1024
#: How near, in pitches, the pointer must be to a site for *Side by side* to light it up.
HOVER_PITCHES = 0.5


def audit_command(out_dir) -> str:
    """The command that runs the identity check over an analysed dataset."""
    return f"python -m pipeline.nanowell_identity {out_dir if out_dir else '<output folder>'}"


def worst_verdict(data, well: str) -> str | None:
    """The worst of *well*'s verdicts (``CONTRADICTED`` first, then ``INCONCLUSIVE``, ``NOT_CHECKED``), ``CONFIRMED``
    when every timepoint is good, or ``None`` when the data holds no verdicts for it."""
    from pipeline.nanowell_identity import CONFIRMED

    found = {verdict for verdict, _why in ((getattr(data, "identity", None) or {}).get(well) or {}).values()}
    if not found:
        return None
    for verdict in (CONTRADICTED, INCONCLUSIVE, NOT_CHECKED):
        if verdict in found:
            return verdict
    return CONFIRMED


def default_timepoint(data, well: str, timepoints) -> str:
    """The timepoint the Identity view opens on: the well's first contradicted one, else its first inconclusive or
    unchecked one, else its first that is not the reference (a reference compared with itself shows nothing)."""
    from pipeline.nanowell_identity import REFERENCE

    verdicts = (getattr(data, "identity", None) or {}).get(well) or {}
    tps = list(timepoints)
    for wanted in (CONTRADICTED, INCONCLUSIVE, NOT_CHECKED):
        for tp in tps:
            if (verdicts.get(tp) or ("",))[0] == wanted:
                return tp
    others = [tp for tp in tps if (verdicts.get(tp) or ("",))[0] != REFERENCE]
    return others[0] if others else (tps[0] if tps else "")


def ring_of(address, kind: str) -> int:
    """How many lattice steps *address* is from the central nanowell: ``max(|q|, |r|, |q + r|)`` on the hexagonal
    lattice (its ``r`` axis is 60° from ``q``), ``max(|q|, |r|)`` on the square one."""
    q, r = int(address[0]), int(address[1])
    return max(abs(q), abs(r)) if kind == "square" else max(abs(q), abs(r), abs(q + r))


@dataclass
class IdentityPair:
    """One timepoint of one well and the reference it was compared with, as the sidecar and the tables say."""
    well: str
    timepoint: str
    reference: str | None
    verdict: str
    reason: str
    entry: dict = field(default_factory=dict)        # the sidecar's entry for the timepoint ({} when there is none)
    payload: dict | None = None                      # the whole sidecar, or None when it is missing or stale
    ref: object = None                               # pipeline.nanowell_identity.Timepoint (no image), or None
    other: object = None
    forward: object = None                           # reference frame -> compared frame, or None
    inverse: object = None                           # compared frame -> reference frame, or None


def _site_rows(data, well: str, timepoints) -> list[dict]:
    sites = getattr(data, "sites", None)
    if sites is None or not len(sites):
        return []
    rows = sites[(sites["well"] == well) & sites["timepoint"].isin(list(timepoints))]
    keep = [c for c in ("timepoint", "nw_q", "nw_r", "well_x_um", "well_y_um", "owner") if c in rows.columns]
    return rows[keep].to_dict("records")


def registration(ref_square, square: dict | None):
    """``(forward, inverse)``: the similarity transform the sidecar's *square* match records, from the reference's
    common frame to the compared timepoint's (``nanowell_identity.mapper``) and back, each a function of points
    (rows); ``(None, None)`` when the match located nothing."""
    from pipeline.nanowell_identity import SquareMatch, Timepoint, mapper

    if not isinstance(square, dict) or not square.get("found_um") or ref_square is None:
        return None, None
    try:
        match = SquareMatch(bool(square.get("ok")), found_um=tuple(float(v) for v in square["found_um"]),
                            rotation_deg=float(square.get("rotation_deg") or 0.0),
                            scale=float(square.get("scale") or 1.0))
        src = np.asarray([float(v) for v in ref_square], dtype=float)
    except (TypeError, ValueError):
        return None, None
    forward = mapper(Timepoint("", "", square_um=tuple(src)), match)
    origin = forward(src)[0]
    a = np.column_stack([forward(src + (1.0, 0.0))[0] - origin, forward(src + (0.0, 1.0))[0] - origin])
    if abs(float(np.linalg.det(a))) < 1e-12:
        return forward, None
    back = np.linalg.inv(a)

    def inverse(points):
        points = np.asarray(points, dtype=float).reshape(-1, 2)
        return (points - origin) @ back.T + src
    return forward, inverse


def identity_pair(data, well: str, timepoint: str, payload: dict | None) -> IdentityPair:
    """The comparison of *well*'s *timepoint* with its reference, read from the sidecar *payload*
    (``nanowell_identity.read_sidecar``; ``None`` when there is none current) and the loaded tables."""
    from pipeline.nanowell_identity import timepoints_of, verdict_in

    if payload is None:
        _verdict, reason = ((getattr(data, "identity", None) or {}).get(well) or {}).get(timepoint, (NOT_CHECKED, ""))
        return IdentityPair(well, timepoint, None, NOT_CHECKED,
                            reason or "the identity check has not been run on these tables")
    entry = (payload.get("timepoints") or {}).get(timepoint)
    entry = entry if isinstance(entry, dict) else {}
    reference = payload.get("reference")
    # The verdict as every other figure reads it (``nanowell_identity.verdicts``): the stage cross-check overrides.
    verdict, reason = verdict_in(payload, timepoint)
    pair = IdentityPair(well, timepoint, reference, verdict, reason, entry, payload)
    if reference is None:
        return pair
    grid = {"timepoints": {tp: block for tp, block in (getattr(data, "grids", {}).get(well) or {}).items()
                           if tp in (reference, timepoint)}}
    built = timepoints_of(grid, _site_rows(data, well, (reference, timepoint)), {})
    pair.ref, pair.other = built.get(reference), built.get(timepoint)
    ref_entry = (payload.get("timepoints") or {}).get(reference) or {}
    ref_square = ref_entry.get("square_um") or (pair.ref.square_um if pair.ref is not None else None)
    pair.forward, pair.inverse = registration(ref_square, entry.get("square"))
    return pair


def identity_message(pair: IdentityPair, out_dir=None) -> str:
    """What the Identity view says instead of a comparison, or ``""`` when there is one to draw: the check not run
    on these tables (with the command that runs it), the timepoint being the reference itself, no reference."""
    from pipeline.nanowell_identity import REFERENCE

    if pair.payload is None or pair.verdict == NOT_CHECKED:
        return (f"{pair.well} at {pair.timepoint}: the nanowell identity check has not been run on these tables "
                f"({pair.reason}). Run it with:\n{audit_command(out_dir)}")
    if pair.reference is None:
        return (f"{pair.well} has no reference timepoint to compare {pair.timepoint} with: "
                f"{pair.payload.get('reference_reason') or pair.reason}.")
    if pair.timepoint == pair.reference or pair.entry.get("verdict") == REFERENCE:
        return (f"{pair.timepoint} is {pair.well}'s reference timepoint: every other timepoint of the well is "
                "compared with it. Choose another timepoint to see its comparison.")
    return ""


def _pitches(value) -> str:
    return "—" if value is None else f"{float(value):.3f}"


def stage_line(pair: IdentityPair) -> str:
    """The plate-level stage cross-check's sentence for the timepoint, or ``""`` when the sidecar has none."""
    stage = (pair.payload or {}).get("stage") if pair.payload else None
    entry = ((stage or {}).get("timepoints") or {}).get(pair.timepoint) if isinstance(stage, dict) else None
    if not isinstance(entry, dict):
        return ""
    residual = entry.get("residual_um")
    at = f"residual {float(residual):.1f} µm" if residual is not None else "no residual"
    said = {True: "agrees", False: "disagrees", None: "cannot decide"}.get(entry.get("ok"), "cannot decide")
    reason = f" — {entry['reason']}" if entry.get("reason") else ""
    return f"Stage cross-check against {stage.get('reference') or 'the plate'}: {said} ({at}){reason}."


def summary_lines(pair: IdentityPair) -> list[str]:
    """The footer's lines: the verdict and why; the counts and distances; the diagnosis; the tiles; the stage."""
    entry = pair.entry
    lines = [f"{pair.verdict.replace('_', ' ').capitalize()}: {pair.reason}" if pair.reason else
             pair.verdict.replace("_", " ").capitalize()]
    if "compared" in entry:
        lines.append(f"{int(entry.get('compared') or 0):,} addresses compared · "
                     f"{int(entry.get('failing_count') or 0):,} failing · median "
                     f"{_pitches(entry.get('median_pitches'))} pitch · max {_pitches(entry.get('max_pitches'))} pitch")
    diagnosis = entry.get("diagnosis") or {}
    if diagnosis.get("kind") not in (None, "none") and diagnosis.get("detail"):
        lines.append(f"Diagnosis ({diagnosis['kind']}): {diagnosis['detail']}.")
    tiles = entry.get("tiles") or {}
    if tiles.get("reason"):
        lines.append(f"Tiles: {tiles['reason']}.")
    stage = stage_line(pair)
    if stage:
        lines.append(stage)
    return lines


@dataclass
class MappedTile:
    """One tile of the compared timepoint drawn in the reference's frame: its outline (corners, in order) when its
    size is known, else its placed corner alone."""
    tile: str
    corners: np.ndarray | None
    corner: tuple
    moved_um: float | None = None                    # how far it sits from the rest of the grid, when it moved


@dataclass
class DistanceMap:
    """Every compared address at its reference position (common frame µm of the reference), with its distance."""
    addresses: list
    x: np.ndarray
    y: np.ndarray
    distance: np.ndarray                             # pitches; NaN for an address only the reference holds
    failing: set
    radius_um: float
    tiles: list
    pitch_um: float | None

    def value_of(self, address) -> float:
        return float(self.distance[self.addresses.index(tuple(address))])


def distance_map(pair: IdentityPair, tile_sizes: dict | None = None) -> DistanceMap:
    """The Distance map's data: the sidecar's distance for each address at the reference's position of that
    address, the failing addresses (one only the reference holds included, at its reference position), and the
    compared timepoint's tiles mapped into the reference frame through the inverse of the registration, a moved one
    carrying how far it moved. A failing address is one the sidecar lists or one farther than the tolerance (the
    list stops at ``nanowell_identity.MAX_LISTED``). *tile_sizes* is ``tile -> (width, height)`` in µm where the acquisition gives one."""
    entry, ref = pair.entry, pair.ref
    sites = getattr(ref, "sites", None) or {}
    values = {(int(q), int(r)): float(d) for q, r, d in entry.get("distances") or ()}
    # The sidecar lists at most ``MAX_LISTED`` failing addresses; every distance beyond the tolerance fails too, so
    # a well with more is outlined in full rather than stopping at the list's end.
    tolerance = float((pair.payload or {}).get("tolerance_pitches") or TOLERANCE_PITCHES)
    failing = {(int(f[0]), int(f[1])) for f in entry.get("failing") or ()} | {a for a, d in values.items()
                                                                              if d > tolerance}
    addresses = [a for a in sorted(set(values) | failing) if a in sites]
    xy = np.array([sites[a] for a in addresses], dtype=float).reshape(-1, 2)
    pitch = getattr(ref, "pitch", None)
    moved = {str(t): float(um) for t, um in (entry.get("tiles") or {}).get("moved") or ()}
    tiles = []
    other = pair.other
    if other is not None and pair.inverse is not None:
        for name, (left, top) in sorted(other.tiles.items()):
            size = (tile_sizes or {}).get(name)
            corner = tuple(pair.inverse([(left, top)])[0])
            corners = None
            if size and size[0] and size[1]:
                w, h = size
                corners = pair.inverse([(left, top), (left + w, top), (left + w, top + h), (left, top + h)])
            tiles.append(MappedTile(name, corners, corner, moved.get(name)))
    return DistanceMap(addresses, xy[:, 0], xy[:, 1], np.array([values.get(a, np.nan) for a in addresses], dtype=float),
                       failing, 0.35 * pitch if pitch else 1.0, tiles, pitch)


@dataclass
class BlinkFrames:
    """The two montages on one pixel grid — the reference's — so a correct mapping keeps the lattice still."""
    reference: np.ndarray                            # float32, the reference montage on the grid
    compared: np.ndarray                             # float32, the compared montage resampled onto it; NaN outside
    extent: tuple                                    # (left, right, bottom, top) in the reference's common frame
    ref_labels: list                                 # [((q, r), x, y)]: reference addresses at reference positions
    compared_labels: list                            # [((q, r), x, y)]: compared addresses, mapped into the frame
    covered: float                                   # the fraction of the grid the compared montage reaches


def _sample(image: np.ndarray, origin, um_per_px: float, points: np.ndarray) -> np.ndarray:
    """*image* (bilinear) at common-frame *points* (rows), NaN outside it."""
    from scipy import ndimage

    px = (points - np.asarray(origin, dtype=float)) / float(um_per_px) - 0.5
    return ndimage.map_coordinates(np.asarray(image, dtype=np.float32), [px[:, 1], px[:, 0]], order=1,
                                   mode="constant", cval=np.nan)


def _labels(sites: dict, transform=None) -> list:
    chosen = [a for a in sorted(sites) if a[0] % LABEL_EVERY == 0 and a[1] % LABEL_EVERY == 0]
    if not chosen:
        return []
    xy = np.array([sites[a] for a in chosen], dtype=float)
    if transform is not None:
        xy = transform(xy)
    return [(a, float(x), float(y)) for a, (x, y) in zip(chosen, xy)]


def blink_frames(pair: IdentityPair, ref_montage, other_montage, *, max_side: int = BLINK_MAX_SIDE) -> BlinkFrames | None:
    """The Blink view's two frames: the reference montage, and the compared montage resampled into the reference
    montage's pixel grid through the registration (each grid point mapped forward into the compared frame and read
    there), both on a grid of at most *max_side* pixels a side. Each frame's labels sit at its own addresses'
    positions — the reference's where the reference puts them, the compared timepoint's mapped back through the
    inverse — so an address that names another nanowell visibly jumps. ``None`` without a registration or either
    montage (``image_compute.Montage``)."""
    if pair.forward is None or pair.inverse is None or ref_montage is None or other_montage is None:
        return None
    h, w = ref_montage.image.shape[:2]
    step = max(1, int(math.ceil(max(h, w) / float(max_side))))
    um = float(ref_montage.um_per_px)
    x0, y0 = (float(v) for v in ref_montage.origin_um)
    cols, rows = np.arange(0, w, step) + (step - 1) / 2.0, np.arange(0, h, step) + (step - 1) / 2.0
    gx, gy = np.meshgrid((cols + 0.5) * um + x0, (rows + 0.5) * um + y0)
    points = np.column_stack([gx.ravel(), gy.ravel()])
    reference = _sample(ref_montage.image, (x0, y0), um, points).reshape(gx.shape)
    compared = _sample(other_montage.image, other_montage.origin_um, other_montage.um_per_px,
                       pair.forward(points)).reshape(gx.shape)
    half = step * um / 2                                  # each grid point is the centre of a step × step cell
    extent = (float(gx[0, 0]) - half, float(gx[0, -1]) + half, float(gy[-1, 0]) + half, float(gy[0, 0]) - half)
    return BlinkFrames(reference.astype(np.float32), compared.astype(np.float32), extent,
                       _labels(pair.ref.sites), _labels(pair.other.sites, pair.inverse),
                       float(np.isfinite(compared).mean()))


@dataclass
class HoverTarget:
    """What *Side by side* lights up for the pointer: the address, where each timepoint puts it, and where the
    registration predicts the reference's site lands in the compared frame."""
    address: tuple
    ref_xy: tuple | None
    other_xy: tuple | None
    predicted_xy: tuple | None


def hover_target(side: str, x: float, y: float, pair: IdentityPair, radius_um: float | None = None
                 ) -> HoverTarget | None:
    """The address under the point (*x*, *y*) on the *side* (``"reference"`` or ``"compared"``) axes — the nearest
    site of that side's own timepoint within *radius_um* (half a pitch by default) — and where it is on both sides;
    ``None`` on empty space."""
    own = pair.ref if side == "reference" else pair.other
    sites = getattr(own, "sites", None) or {}
    if not sites or x is None or y is None:
        return None
    names = list(sites)
    xy = np.array([sites[a] for a in names], dtype=float)
    gap = np.hypot(xy[:, 0] - x, xy[:, 1] - y)
    k = int(np.argmin(gap))
    pitch = getattr(own, "pitch", None) or 0.0
    limit = radius_um if radius_um is not None else HOVER_PITCHES * pitch
    if not gap[k] <= limit:
        return None
    address = names[k]
    ref_sites, other_sites = getattr(pair.ref, "sites", {}) or {}, getattr(pair.other, "sites", {}) or {}
    ref_xy = ref_sites.get(address)
    predicted = None
    if ref_xy is not None and pair.forward is not None:
        predicted = tuple(float(v) for v in pair.forward([ref_xy])[0])
    return HoverTarget(address, None if ref_xy is None else tuple(ref_xy),
                       None if address not in other_sites else tuple(other_sites[address]), predicted)


@dataclass
class Diagnostics:
    """The registration on the square, as the Diagnostics view draws it."""
    surface: np.ndarray | None                       # the reduced correlation surface, NaN where not searched
    box: tuple | None                                # (left, top, right, bottom), compared timepoint's common frame
    found_um: tuple | None
    runner_up_um: tuple | None                       # located on the reduced surface (the sidecar keeps its value only)
    expected_um: tuple | None                        # the reference's square, where an unmoved plate puts it
    accepted_um: tuple | None                        # the square the run accepted at the compared timepoint
    lines: list = field(default_factory=list)


def diagnostics(pair: IdentityPair) -> Diagnostics:
    """The correlation surface with its peak and runner-up, and the transform, agreement and thresholds as text."""
    from pipeline.nanowell_identity import MIN_MARGIN, MIN_PEAK, SQUARE_AGREEMENT_PITCHES

    square = pair.entry.get("square") or {}
    surface = box = None
    if square.get("surface") and square.get("surface_box_um"):
        surface = np.array([[np.nan if v is None else float(v) for v in row] for row in square["surface"]], dtype=float)
        box = tuple(float(v) for v in square["surface_box_um"])
    found = tuple(float(v) for v in square["found_um"]) if square.get("found_um") else None
    pitch = getattr(pair.other, "pitch", None)
    runner = None
    if surface is not None and found is not None and pitch and np.isfinite(surface).any():
        rows, cols = surface.shape
        cw, ch = (box[2] - box[0]) / cols, (box[3] - box[1]) / rows
        cx, cy = np.meshgrid(box[0] + (np.arange(cols) + 0.5) * cw, box[1] + (np.arange(rows) + 0.5) * ch)
        away = np.where(np.hypot(cx - found[0], cy - found[1]) > 0.5 * pitch, surface, np.nan)
        if np.isfinite(away).any():
            iy, ix = np.unravel_index(int(np.nanargmax(away)), away.shape)
            runner = (float(cx[iy, ix]), float(cy[iy, ix]))
    ref_entry = ((pair.payload or {}).get("timepoints") or {}).get(pair.reference) or {}
    expected = ref_entry.get("square_um") or getattr(pair.ref, "square_um", None)
    expected = tuple(float(v) for v in expected) if expected else None
    accepted = getattr(pair.other, "square_um", None)
    lines = []
    if square:
        peak, margin = square.get("peak"), square.get("margin")
        lines.append(f"Peak {_num(peak)} (at least {MIN_PEAK:g} needed) · runner-up {_num(square.get('runner_up'))} · "
                     f"margin {_num(margin)} (at least {MIN_MARGIN:g} needed): "
                     + ("the square is found." if square.get("ok") else
                        f"the square is not found — {square.get('reason') or 'no reason recorded'}."))
        rotation, scale = float(square.get("rotation_deg") or 0.0), float(square.get("scale") or 1.0)
        shift = ("—" if found is None or expected is None else
                 f"({found[0] - expected[0]:+.1f}, {found[1] - expected[1]:+.1f}) µm")
        lines.append(f"Transform, reference square to the match: turn {rotation:+.2f}°, scale {scale:.4f}, "
                     f"translation {shift}.")
    else:
        lines.append("The registration on the square was not attempted: " + (pair.reason or "no reason recorded") + ".")
    agreement = pair.entry.get("square_agreement_pitches")
    if agreement is not None:
        lines.append(f"The square the run accepted at {pair.timepoint} is {float(agreement):.3f} pitch from the match "
                     f"(at most {SQUARE_AGREEMENT_PITCHES:g} agrees).")
    elif accepted is None:
        lines.append(f"The run accepted no square at {pair.timepoint}, so the match has nothing to agree with.")
    return Diagnostics(surface, box, found, runner, expected, accepted, lines)


def _num(value) -> str:
    return "—" if value is None else f"{float(value):.3f}"


# ── marks on the line plots ──────────────────────────────────────────────────
#: The verdicts a line plot marks at their timepoint, with the word it writes there.
MARK_WORDS = {CONTRADICTED: "contradicted", INCONCLUSIVE: "inconclusive", NOT_CHECKED: "not checked"}


def timepoint_marks(data, well: str) -> dict[str, str]:
    """``timepoint -> verdict`` for the timepoints of *well* a line plot marks: every ``contradicted`` and
    ``inconclusive`` one, and a ``not_checked`` one when another timepoint of the well was checked (a well never
    checked at all is said once, in the footer, rather than hatched at every timepoint)."""
    verdicts = (getattr(data, "identity", None) or {}).get(well) or {}
    checked = any(verdict != NOT_CHECKED for verdict, _why in verdicts.values())
    return {tp: verdict for tp, (verdict, _why) in verdicts.items()
            if verdict in (CONTRADICTED, INCONCLUSIVE) or (verdict == NOT_CHECKED and checked)}


def marks_legend(verdicts) -> str:
    """The footer sentence saying what the hatching means, naming only the kinds drawn; ``""`` for none."""
    kinds = [v for v in (INCONCLUSIVE, CONTRADICTED, NOT_CHECKED) if v in set(verdicts)]
    if not kinds:
        return ""
    hatch = {INCONCLUSIVE: "//", CONTRADICTED: "xx", NOT_CHECKED: ".."}
    said = ", ".join(f"{hatch[v]} {MARK_WORDS[v]}" for v in kinds)
    return f"Hatched: nanowell identity not confirmed ({said}); lines through them dashed."
