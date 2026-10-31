"""What the identity check (``pipeline/nanowell_identity.py``) says about the wells a figure draws, as data.

Review joins a well's timepoints by nanowell address; the identity check registers each timepoint's montage onto a
reference timepoint's on the central square and confirms every address lands on itself. A figure over wells whose
timepoints were not all confirmed says so in its footer (:func:`identity_note`), because an unverified join looks
exactly like a verified one. A ``contradicted`` timepoint is already kept out of every join
(``series_compute._addressed_grid``); the note names it so its absence is explained.

The page's **Identity** view is computed here too, one well's timepoint against its reference (:func:`identity_pair`):
the Distance map's distances at the reference's measured positions in its common frame (:func:`distance_map`), the
Blink frames resampled into the reference montage's pixels through the registration (:func:`blink_frames`), the
address under the pointer on *Side by side* (:func:`hover_target`), each nanowell's status for the Distance map's
*Status* colouring (:func:`status_map`, :func:`colour_by`), and the Diagnostics: the correlation surface with its
peak and runner-up (:func:`diagnostics`), the crops of the square looked for and where it was found
(:func:`diagnostic_crops`) and the verdict block, one line per check with its state (:func:`checks`).
``_identity_render`` and ``_identity_diagnostics_render`` draw them; nothing here draws.

Qt-free.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from pipeline.nanowell_identity import CONTRADICTED, GOOD, INCONCLUSIVE, NOT_CHECKED, PARTIAL, TOLERANCE_PITCHES

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
        parts.append(_named([f"{w} {'/'.join(t)}" for w, t in bad.items()]) + " contradicted (left out of joins)")
    some = counts.get(PARTIAL) or {}
    if some:
        excluded = getattr(data, "identity_excluded", None) or {}
        left = sum(len(excluded.get(w, {}).get(tp, ())) for w, tps in some.items() for tp in tps)
        parts.append(_named([f"{w} {'/'.join(t)}" for w, t in some.items()])
                     + f" partial ({left:,} nanowell(s) left out of joins)")
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
    """The worst of *well*'s verdicts (``CONTRADICTED`` first, then ``INCONCLUSIVE``, ``NOT_CHECKED``, ``PARTIAL``),
    ``CONFIRMED`` when every timepoint is good, or ``None`` when the data holds no verdicts for it."""
    from pipeline.nanowell_identity import CONFIRMED, PARTIAL

    found = {verdict for verdict, _why in ((getattr(data, "identity", None) or {}).get(well) or {}).values()}
    if not found:
        return None
    for verdict in (CONTRADICTED, INCONCLUSIVE, NOT_CHECKED, PARTIAL):
        if verdict in found:
            return verdict
    return CONFIRMED


def default_timepoint(data, well: str, timepoints) -> str:
    """The timepoint the Identity view opens on: the well's first contradicted one, else its first inconclusive,
    unchecked or partial one, else its first that is not the reference (a reference compared with itself shows
    nothing)."""
    from pipeline.nanowell_identity import PARTIAL, REFERENCE

    verdicts = (getattr(data, "identity", None) or {}).get(well) or {}
    tps = list(timepoints)
    for wanted in (CONTRADICTED, INCONCLUSIVE, NOT_CHECKED, PARTIAL):
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
    moved_um: float | None = None                    # how far it sits from the rest of the grid: a lattice step off
    imprecise_um: float | None = None                # placed differently by less than half a pitch: names unaffected


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
    imprecise = {str(t): float(um) for t, um in (entry.get("tiles") or {}).get("imprecise") or ()}
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
            tiles.append(MappedTile(name, corners, corner, moved.get(name), imprecise.get(name)))
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


# ── the status of each nanowell (the Distance map's *Status* colouring) ─────
#: The *Status* classes in the legend's order, each with what its legend says (``{tol}``: the tolerance, pitches).
STATUS_CLASSES = (
    ("kept", "Lands on itself"),
    ("imprecise", "Lands nearest itself, beyond {tol:g} spacing"),
    ("wrong_name", "Wrong name: lands on another, or another lands on it"),
    ("misregistered", "In a tile placed a lattice step off"),
    ("timepoint_out", "Lands on itself, but the whole timepoint is contradicted, so it is left out"),
    ("not_compared", "Not compared"),
)
#: The classes a timepoint contradicted as a whole turns into ``timepoint_out``: every nanowell of such a timepoint
#: is left out of every figure, so none may read as one that is used (CLAUDE.md §7); a wrong name, a misregistered
#: tile and a nanowell not compared keep their own class, which says more.
_USED_CLASSES = frozenset({"kept", "imprecise"})
#: How far, in pitches, an address no row of the sidecar names may land from itself and still be nearest itself:
#: half the spacing, the nearest any other site can be on either lattice. Used only beyond the sidecar's list of
#: failing addresses (``MAX_LISTED``), where where it landed is not recorded.
NEAREST_ITSELF_PITCHES = 0.5


@dataclass
class StatusMap:
    """Every nanowell of the comparison at its reference position (the reference's common frame, µm) with its class
    (:data:`STATUS_CLASSES`)."""
    addresses: list
    x: np.ndarray
    y: np.ndarray
    status: list
    radius_um: float

    def of(self, address) -> str:
        return self.status[self.addresses.index(tuple(address))]

    def members(self, cls: str) -> set:
        return {a for a, s in zip(self.addresses, self.status) if s == cls}

    @property
    def counts(self) -> dict[str, int]:
        return {cls: sum(s == cls for s in self.status) for cls, _label in STATUS_CLASSES}


def _classes(pair: IdentityPair) -> dict:
    """``address -> class`` from the sidecar entry, the most specific record first: the check's own list of the
    nanowells it left out (``excluded``: ``misregistered_tile``, ``wrong_name``, ``neighbour_of_wrong``) — or, in a
    sidecar written before that list existed, the nanowells of the tiles it found a step off (``tiles.moved``) —
    then each listed failing address (landed on itself: imprecise; on another: a wrong name), then each distance
    (within the tolerance: kept; beyond it, nearest itself — :data:`NEAREST_ITSELF_PITCHES` — imprecise, else a
    wrong name), and every other address of the reference not compared. In a timepoint contradicted as a whole
    (the pair's verdict, the stage cross-check's override included) a nanowell that lands on itself is
    ``timepoint_out``, since it is left out with the timepoint."""
    from pipeline.nanowell_identity import CONTRADICTED

    entry = pair.entry
    tolerance = float((pair.payload or {}).get("tolerance_pitches") or TOLERANCE_PITCHES)
    out: dict = {}
    if entry.get("excluded") is not None:
        for q, r, why, *_rest in entry["excluded"]:
            out[(int(q), int(r))] = "misregistered" if why == "misregistered_tile" else "wrong_name"
    else:
        moved = {str(t) for t, _um in (entry.get("tiles") or {}).get("moved") or ()}
        for address, owner in (getattr(pair.other, "owners", None) or {}).items():
            if owner in moved:
                out[tuple(address)] = "misregistered"
    for row in entry.get("failing") or ():
        address, landed = (int(row[0]), int(row[1])), (int(row[3]), int(row[4]))
        out.setdefault(address, "imprecise" if landed == address and row[2] is not None else "wrong_name")
    for q, r, d in entry.get("distances") or ():
        d = float(d)
        out.setdefault((int(q), int(r)), "kept" if d <= tolerance else
                       "imprecise" if d <= NEAREST_ITSELF_PITCHES else "wrong_name")
    for address in getattr(pair.ref, "sites", None) or {}:
        out.setdefault(tuple(address), "not_compared")
    if pair.verdict == CONTRADICTED:
        out = {address: "timepoint_out" if cls in _USED_CLASSES else cls for address, cls in out.items()}
    return out


def status_map(pair: IdentityPair) -> StatusMap:
    """The *Status* colouring's data: each nanowell's class (:func:`_classes`) at its reference position, or — for
    an address only the compared timepoint holds — at its position there mapped into the reference's frame."""
    ref_sites = getattr(pair.ref, "sites", None) or {}
    other_sites = getattr(pair.other, "sites", None) or {}
    addresses, xy, status = [], [], []
    for address, cls in sorted(_classes(pair).items()):
        if address in ref_sites:
            point = ref_sites[address]
        elif address in other_sites and pair.inverse is not None:
            point = tuple(pair.inverse([other_sites[address]])[0])
        else:
            continue
        addresses.append(address)
        xy.append(point)
        status.append(cls)
    xy = np.array(xy, dtype=float).reshape(-1, 2)
    pitch = getattr(pair.ref, "pitch", None)
    return StatusMap(addresses, xy[:, 0], xy[:, 1], status, 0.35 * pitch if pitch else 1.0)


def colour_by(choice: str, pair: IdentityPair) -> str:
    """What the Distance map colours by for the page's *choice* (``state.IDENTITY_COLOURS``): ``auto`` is the
    status unless the timepoint is confirmed."""
    from pipeline.nanowell_identity import CONFIRMED

    if choice in ("status", "distance"):
        return choice
    return "distance" if pair.verdict == CONFIRMED else "status"


# ── Diagnostics: the registration on the central square ──────────────────────
#: The states a line of the Diagnostics' verdict block is drawn in: passed, failed, a warning (it withholds or
#: qualifies), information (no threshold applies), and a check that did not run.
CHECK_STATES = ("pass", "fail", "warn", "info", "skip")
#: The longest side, in samples, of the Diagnostics' montage crops: a crop is about three pitches wide, a few hundred
#: montage pixels, and more samples than the panel's pixels add nothing.
CROP_MAX_SIDE = 256


@dataclass
class Check:
    """One line of the Diagnostics' verdict block: which check (``key``), how it came out (:data:`CHECK_STATES`),
    and the sentence, with its numbers and their thresholds."""
    key: str
    state: str
    text: str


@dataclass
class Diagnostics:
    """The registration on the square, as the Diagnostics view draws it. Positions are in the compared timepoint's
    common frame; :meth:`relative` measures them from ``origin``, where the heat map's axes start."""
    surface: np.ndarray | None                       # the reduced correlation surface, NaN where not searched
    box: tuple | None                                # (left, top, right, bottom), compared timepoint's common frame
    found_um: tuple | None
    runner_up_um: tuple | None                       # located on the reduced surface (the sidecar keeps its value only)
    expected_um: tuple | None                        # the reference's square, where an unmoved plate puts it
    accepted_um: tuple | None                        # the square the run accepted at the compared timepoint
    checks: list = field(default_factory=list)       # [Check]: the verdict block, top to bottom
    origin: tuple | None = None                      # the expected square, else the match: the heat map's (0, 0)
    pitch_um: float | None = None

    def relative(self, xy) -> tuple | None:
        """*xy* (common frame) measured from :attr:`origin`, in µm; ``None`` for ``None``."""
        if xy is None or self.origin is None:
            return None
        return float(xy[0]) - self.origin[0], float(xy[1]) - self.origin[1]

    @property
    def extent(self) -> tuple | None:
        """``(left, right, bottom, top)`` of the surface measured from :attr:`origin`, for ``imshow(extent=…)``."""
        if self.box is None or self.origin is None:
            return None
        left, top, right, bottom = self.box
        ox, oy = self.origin
        return left - ox, right - ox, bottom - oy, top - oy


def _runner_up_place(surface, box, found, pitch) -> tuple | None:
    """Where the reduced surface is highest more than half a pitch from *found* (the sidecar keeps the runner-up's
    value, not its place): the centre of that sample, common frame."""
    if surface is None or found is None or not pitch or not np.isfinite(surface).any():
        return None
    rows, cols = surface.shape
    cw, ch = (box[2] - box[0]) / cols, (box[3] - box[1]) / rows
    cx, cy = np.meshgrid(box[0] + (np.arange(cols) + 0.5) * cw, box[1] + (np.arange(rows) + 0.5) * ch)
    away = np.where(np.hypot(cx - found[0], cy - found[1]) > 0.5 * pitch, surface, np.nan)
    if not np.isfinite(away).any():
        return None
    iy, ix = np.unravel_index(int(np.nanargmax(away)), away.shape)
    return float(cx[iy, ix]), float(cy[iy, ix])


def diagnostics(pair: IdentityPair) -> Diagnostics:
    """The correlation surface with the match, the runner-up and the expected square, and the verdict block
    (:func:`checks`)."""
    square = pair.entry.get("square") or {}
    surface = box = None
    if square.get("surface") and square.get("surface_box_um"):
        surface = np.array([[np.nan if v is None else float(v) for v in row] for row in square["surface"]], dtype=float)
        box = tuple(float(v) for v in square["surface_box_um"])
    found = tuple(float(v) for v in square["found_um"]) if square.get("found_um") else None
    pitch = getattr(pair.other, "pitch", None) or getattr(pair.ref, "pitch", None)
    ref_entry = ((pair.payload or {}).get("timepoints") or {}).get(pair.reference) or {}
    expected = ref_entry.get("square_um") or getattr(pair.ref, "square_um", None)
    expected = tuple(float(v) for v in expected) if expected else None
    accepted = getattr(pair.other, "square_um", None)
    out = Diagnostics(surface, box, found, _runner_up_place(surface, box, found, pitch), expected, accepted,
                      origin=expected or found, pitch_um=pitch)
    out.checks = checks(pair, out)
    return out


def _sp(value: float) -> str:
    """A distance in nanowell spacings, as a reader says it."""
    return f"{value:.2f} spacing" + ("s" if value > 1 else "")


def _deg(value: float) -> str:
    return f"{float(value) + 0.0:+.1f}°".replace("-0.0°", "+0.0°")


def _square_check(square: dict, pair: IdentityPair) -> Check:
    from pipeline.nanowell_identity import MIN_MARGIN, MIN_PEAK

    if not square:
        return Check("square", "fail", f"Central square not looked for: {pair.reason or 'no reason recorded'}.")
    if square.get("peak") is None:
        return Check("square", "fail", f"Central square not found: {square.get('reason') or 'no reason recorded'}.")
    peak, runner, margin = float(square["peak"]), square.get("runner_up"), float(square.get("margin") or 0.0)
    numbers = (f"best match {peak:.2f} (needs {MIN_PEAK:.2f}), next best "
               f"{'—' if runner is None else f'{float(runner):.2f}'} — lead {margin:.2f} (needs {MIN_MARGIN:.2f})")
    if square.get("ok"):
        return Check("square", "pass", f"Central square found clearly: {numbers}.")
    edge = peak >= MIN_PEAK and margin >= MIN_MARGIN
    return Check("square", "fail", f"Central square not found clearly: {numbers}"
                 + (f"; {square.get('reason') or 'no reason recorded'}." if edge else "."))


def _motion_check(square: dict, diag: Diagnostics, pair: IdentityPair) -> Check:
    if diag.found_um is None or diag.expected_um is None:
        return Check("motion", "skip", "Plate movement not measured: the square was not found.")
    moved = math.dist(diag.found_um, diag.expected_um)
    text = f"Plate moved {moved:.0f} µm and turned {_deg(square.get('rotation_deg') or 0.0)} since {pair.reference}"
    scale = float(square.get("scale") or 1.0)
    if abs(scale - 1.0) >= 0.0005:
        text += f", scale ×{scale:.3f}"
    refined = square.get("refined") or {}
    if refined.get("sites"):
        turn = float(refined.get("rotation_deg") or 0.0) - float(refined.get("registered_rotation_deg") or 0.0)
        ratio = float(refined.get("scale") or 1.0) / float(refined.get("registered_scale") or 1.0)
        if refined.get("kept"):
            text += f" (refit on {int(refined['sites']):,} nanowells: {_deg(turn)}, scale ×{ratio:.3f})"
        else:
            text += (f" (a refit on {int(refined['sites']):,} nanowells would turn it {_deg(turn)} and scale it "
                     f"×{ratio:.3f}: too far, not used)")
    if square.get("turned_deg"):
        text += f"; found only with the reference turned a further {_deg(square['turned_deg'])}"
    return Check("motion", "info", text + ".")


def _agreement_check(entry: dict, square: dict, pair: IdentityPair) -> Check:
    from pipeline.nanowell_identity import SQUARE_AGREEMENT_PITCHES as LIMIT

    agreement = entry.get("square_agreement_pitches")
    name = f"Square the run picked at {pair.timepoint}"
    if agreement is not None:
        a = float(agreement)
        if a <= LIMIT:
            return Check("square_agreement", "pass", f"{name} agrees: {_sp(a)} apart (limit {LIMIT:g}).")
        return Check("square_agreement", "fail", f"{name} is elsewhere: {_sp(a)} away (limit {LIMIT:g}), so the run "
                                                 "took something else for the square.")
    if not square.get("ok"):
        return Check("square_agreement", "skip", f"{name}: not compared, the square was not found.")
    if pair.other is not None and getattr(pair.other, "square_um", None) is None:
        return Check("square_agreement", "skip", f"{name}: none, the run accepted no square there.")
    return Check("square_agreement", "skip", f"{name}: not compared.")


def _outline_check(entry: dict, square: dict) -> Check:
    from pipeline.nanowell_identity import CENTRE_AGREEMENT_PITCHES as AGREES, CENTRE_CONTRADICTS_PITCHES as BEYOND

    centre = entry.get("centre_agreement_pitches")
    if centre is None:
        why = ("the square was not found" if not square.get("ok") else
               "an array outline was not fitted at both timepoints")
        return Check("outline", "skip", f"Array outline not compared: {why}.")
    c = float(centre)
    if c <= AGREES:
        return Check("outline", "pass", f"Array outline agrees: {_sp(c)} apart (limit {AGREES:g}).")
    if c < BEYOND:
        return Check("outline", "warn", f"Array outline loose: {_sp(c)} apart (agrees within {AGREES:g}, contradicts "
                                        f"from {BEYOND:g}), so the timepoint cannot be confirmed.")
    return Check("outline", "fail", f"Array outline disagrees: {_sp(c)} apart (contradicts from {BEYOND:g}): the "
                                    "registration followed something that moved against the array.")


def _tiles_check(entry: dict, pair: IdentityPair) -> Check:
    tiles = entry.get("tiles")
    if not isinstance(tiles, dict):
        return Check("tiles", "skip", "Tiles not compared.")
    pitch = getattr(pair.other, "pitch", None)

    def listed(rows) -> str:
        return ", ".join(f"{t} {float(um):.0f} µm" + (f" ({float(um) / pitch:.2f} spacing)" if pitch else "")
                         for t, um in rows)
    if not tiles.get("applies"):
        reason = tiles.get("reason") or "no reason recorded"
        return Check("tiles", "skip" if "fewer than two" in reason else "warn", f"Tiles not compared: {reason}.")
    if tiles.get("moved"):
        whole = "which group is right cannot be told" if tiles.get("split") else "their nanowells are left out"
        return Check("tiles", "fail", f"Tiles placed a lattice step differently than at {pair.reference}: "
                                      f"{listed(tiles['moved'])}; {whole}.")
    if tiles.get("imprecise"):
        return Check("tiles", "warn", f"Tiles placed as at {pair.reference}, except {listed(tiles['imprecise'])} "
                                      "differently (under half a spacing: names unaffected).")
    return Check("tiles", "pass", f"Tiles placed as at {pair.reference}.")


def _addresses_check(entry: dict, square: dict, pair: IdentityPair) -> Check:
    from pipeline.nanowell_identity import TOLERANCE_PITCHES as LIMIT

    if "compared" not in entry:
        why = "the square was not found" if not square.get("ok") else (pair.reason or "no reason recorded")
        return Check("addresses", "skip", f"Addresses not compared: {why}.")
    compared = int(entry.get("compared") or 0)
    loose = int(entry.get("loose_count") or 0)
    wrong = max(0, int(entry.get("failing_count") or 0) - loose)
    worst = entry.get("max_pitches")
    worst_said = "" if worst is None else f", worst {_sp(float(worst))} (limit {LIMIT:g})"
    if wrong:
        detail = (entry.get("diagnosis") or {}).get("detail")
        return Check("addresses", "fail", f"Addresses: {wrong:,} of {compared:,} land on another nanowell"
                     + (f": {detail}." if detail else "."))
    text = f"Addresses: {compared - loose:,} of {compared:,} land on themselves{worst_said}"
    if loose:
        text += f"; {loose:,} land nearest themselves but beyond {LIMIT:g} (names right)"
    return Check("addresses", "warn" if loose else "pass", text + ".")


#: The state the verdict line is drawn in, by verdict.
_VERDICT_STATE = {"confirmed": "pass", "partial": "warn", "inconclusive": "warn", "contradicted": "fail"}


def checks(pair: IdentityPair, diag: Diagnostics) -> list[Check]:
    """The Diagnostics' verdict block: the verdict and why, then each check the identity check made, in the order
    it made them, with its numbers against its thresholds — the square found, the plate's movement, the square the
    run picked, the array's outline, the tiles, the addresses, and the plate-level stage cross-check when there is
    one. A check that did not run says why (``skip``)."""
    entry = pair.entry
    square = entry.get("square") or {}
    verdict = pair.verdict.replace("_", " ")
    out = [Check("verdict", _VERDICT_STATE.get(pair.verdict, "info"),
                 f"Verdict: {verdict}" + (f" — {pair.reason}." if pair.reason else ".")),
           _square_check(square, pair), _motion_check(square, diag, pair), _agreement_check(entry, square, pair),
           _outline_check(entry, square), _tiles_check(entry, pair), _addresses_check(entry, square, pair)]
    stage = stage_line(pair)
    if stage:
        ok = (((pair.payload or {}).get("stage") or {}).get("timepoints") or {}).get(pair.timepoint, {}).get("ok")
        out.append(Check("stage", {True: "pass", False: "fail"}.get(ok, "skip"), stage))
    return out


@dataclass
class Crops:
    """The Diagnostics' two pictures of the square, on one grid measured from the square's centre (µm): the
    reference's montage around its square (what was looked for) and the compared montage around the match (where
    it was found), turned and scaled into the reference's orientation through the registration when there is one."""
    looked: np.ndarray | None                        # float32, NaN outside the montage; None without the montage
    found: np.ndarray | None
    extent: tuple                                    # (left, right, bottom, top), µm from the square's centre
    half_um: float
    turned: bool                                     # whether ``found`` was mapped through the registration
    accepted: tuple | None                           # the run's own square at the compared timepoint, same coordinates
    found_at: tuple | None                           # the centre of ``found``, the compared timepoint's common frame


def diagnostic_crops(pair: IdentityPair, ref_montage, other_montage, *, max_side: int = CROP_MAX_SIDE
                     ) -> Crops | None:
    """The two crops, each :data:`PATCH_PITCHES` reference pitches either side of its centre — the patch the check
    correlated. *What was looked for* is centred on the reference's square. *Where it was found* is the compared
    montage read at each point of that grid mapped forward through the registration, so it shows the match turned
    into the reference's orientation, centred on ``found_um``; without a registration it is the compared montage
    as it is, around the point where the square was expected. ``None`` without the reference's square or pitch."""
    from pipeline.nanowell_identity import PATCH_PITCHES

    ref = pair.ref
    ref_entry = ((pair.payload or {}).get("timepoints") or {}).get(pair.reference) or {}
    centre = ref_entry.get("square_um") or getattr(ref, "square_um", None)
    pitch = getattr(ref, "pitch", None)
    if centre is None or not pitch:
        return None
    centre = np.asarray([float(v) for v in centre], dtype=float)
    half = PATCH_PITCHES * float(pitch)
    um = float(ref_montage.um_per_px) if ref_montage is not None else float(getattr(ref, "um_per_px", 0) or 0)
    n = int(min(max_side, max(16, round(2 * half / um)))) if um > 0 else max_side
    offsets = -half + (np.arange(n) + 0.5) * (2 * half / n)
    gx, gy = np.meshgrid(offsets, offsets)
    grid = np.column_stack([gx.ravel(), gy.ravel()])
    looked = found = None
    if ref_montage is not None:
        looked = _sample(ref_montage.image, ref_montage.origin_um, ref_montage.um_per_px, grid + centre)
        looked = looked.reshape(n, n).astype(np.float32)
    turned = pair.forward is not None
    accepted = getattr(pair.other, "square_um", None)
    if turned:
        found_at = tuple(float(v) for v in pair.forward([centre])[0])
        if accepted is not None and pair.inverse is not None:
            accepted = tuple(float(v) for v in pair.inverse([accepted])[0] - centre)
        else:
            accepted = None
        points = pair.forward(grid + centre)
    else:
        found_at = tuple(centre)
        accepted = None if accepted is None else (float(accepted[0]) - centre[0], float(accepted[1]) - centre[1])
        points = grid + centre
    if other_montage is not None:
        found = _sample(other_montage.image, other_montage.origin_um, other_montage.um_per_px, points)
        found = found.reshape(n, n).astype(np.float32)
    return Crops(looked, found, (-half, half, half, -half), half, turned, accepted, found_at)


# ── marks on the line plots ──────────────────────────────────────────────────
#: The verdicts a line plot marks at their timepoint, with the word it writes there.
MARK_WORDS = {CONTRADICTED: "contradicted", PARTIAL: "partial", INCONCLUSIVE: "inconclusive",
              NOT_CHECKED: "not checked"}


def timepoint_marks(data, well: str) -> dict[str, str]:
    """``timepoint -> verdict`` for the timepoints of *well* a line plot marks: every ``contradicted`` and
    ``inconclusive`` one, and a ``not_checked`` one when another timepoint of the well was checked (a well never
    checked at all is said once, in the footer, rather than hatched at every timepoint)."""
    verdicts = (getattr(data, "identity", None) or {}).get(well) or {}
    checked = any(verdict != NOT_CHECKED for verdict, _why in verdicts.values())
    return {tp: verdict for tp, (verdict, _why) in verdicts.items()
            if verdict in (CONTRADICTED, PARTIAL, INCONCLUSIVE) or (verdict == NOT_CHECKED and checked)}


def marks_legend(verdicts) -> str:
    """The footer sentence saying what the hatching means, naming only the kinds drawn; ``""`` for none."""
    kinds = [v for v in (INCONCLUSIVE, PARTIAL, CONTRADICTED, NOT_CHECKED) if v in set(verdicts)]
    if not kinds:
        return ""
    hatch = {INCONCLUSIVE: "//", PARTIAL: "\\\\", CONTRADICTED: "xx", NOT_CHECKED: ".."}
    said = ", ".join(f"{hatch[v]} {MARK_WORDS[v]}" for v in kinds)
    joined = ("; contradicted values are points joined to nothing" if CONTRADICTED in kinds or PARTIAL in kinds
              else "")
    return f"Hatched: nanowell identity not confirmed ({said}); lines through them dashed{joined}."
