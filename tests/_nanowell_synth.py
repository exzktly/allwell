"""Seeded point observations with separate, complete geometric ground truth.

Positions are in micrometres. Rotations are counter-clockwise on screen (y
down); an explicitly selected mirror axis is applied after rotation. This is
point-set evidence only, not a simulation of phase optics or detection.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class SyntheticArray:
    points: np.ndarray
    addresses: tuple[tuple[int, int] | None, ...]
    roster: tuple[tuple[int, int], ...]
    origin: tuple[float, float]
    a1: tuple[float, float]
    a2: tuple[float, float]
    square_mask: tuple[bool, ...]
    diameter: float
    pitch: float
    radius: float


def _screen_rotation(angle_deg):
    angle = math.radians(angle_deg)
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, s], [-s, c]])


def make_array(*, radius: float, pitch: float, diameter: float, seed: int = 0,
               rotation_deg: float = 0.0, translation=(0.0, 0.0), mirror_axis=None,
               bubbles=(), dropout_fraction: float = 0.0, outlier_fraction: float = 0.0,
               noise_sigma: float = 0.0, window=None) -> SyntheticArray:
    """Render point centres, retaining ground-truth addresses of hidden sites.

    Bubbles are `(x, y, radius)` disks in the array's native frame. Window
    bounds are `(left, top, right, bottom)` in the observed image frame. Outliers
    have no ground-truth address; their fraction is of the final observations
    (rounded to the nearest integer count). The central square is marked only
    if observed, independently of the complete roster.
    """
    if not all(math.isfinite(v) and v > 0 for v in (radius, pitch, diameter)):
        raise ValueError("radius, pitch and diameter must be finite and positive")
    if not 0 <= dropout_fraction < 1 or not 0 <= outlier_fraction < 1:
        raise ValueError("Dropout and outlier fractions must be in [0, 1)")
    if not math.isfinite(noise_sigma) or noise_sigma < 0:
        raise ValueError("noise_sigma must be finite and non-negative")
    if mirror_axis not in {None, "x", "y"}:
        raise ValueError("mirror_axis must be explicitly x, y, or None")
    origin = np.asarray(translation, dtype=float)
    if origin.shape != (2,) or not np.isfinite(origin).all() or not math.isfinite(rotation_deg):
        raise ValueError("Pose must be finite")
    rng = np.random.default_rng(seed)
    native_basis = np.array([[pitch, pitch / 2], [0.0, -math.sqrt(3) * pitch / 2]])
    reach = math.ceil(2 * radius / pitch)
    roster = []
    native = []
    for q in range(-reach, reach + 1):
        for r in range(-reach, reach + 1):
            xy = native_basis @ (q, r)
            if np.linalg.norm(xy) <= radius + pitch * 1e-12:
                roster.append((q, r))
                native.append(xy)
    native = np.asarray(native)
    keep = np.ones(len(roster), dtype=bool)
    for x, y, bubble_radius in bubbles:
        if not all(math.isfinite(v) for v in (x, y, bubble_radius)) or bubble_radius <= 0:
            raise ValueError("Bubbles require finite centres and positive radii")
        keep &= np.linalg.norm(native - (x, y), axis=1) > bubble_radius
    indices = np.flatnonzero(keep)
    dropped = rng.choice(indices, size=round(len(indices) * dropout_fraction), replace=False)
    keep[dropped] = False
    pose = _screen_rotation(rotation_deg)
    if mirror_axis is not None:
        pose = np.diag((-1.0, 1.0) if mirror_axis == "x" else (1.0, -1.0)) @ pose
    points = native @ pose.T + origin
    if window is not None:
        left, top, right, bottom = window
        if not all(math.isfinite(v) for v in window) or right <= left or bottom <= top:
            raise ValueError("Window must have finite positive extents")
        keep &= ((points[:, 0] >= left) & (points[:, 0] <= right)
                 & (points[:, 1] >= top) & (points[:, 1] <= bottom))
    addresses = tuple(address for address, retained in zip(roster, keep) if retained)
    observed = points[keep] + rng.normal(0, noise_sigma, (int(keep.sum()), 2))
    count = round(len(observed) * outlier_fraction / (1 - outlier_fraction))
    if count:
        bounds = window if window is not None else (
            origin[0] - radius, origin[1] - radius, origin[0] + radius, origin[1] + radius)
        outliers = rng.uniform(bounds[:2], bounds[2:], (count, 2))
        observed = np.concatenate((observed, outliers))
        addresses += (None,) * count
    observed.setflags(write=False)
    basis = pose @ native_basis
    return SyntheticArray(observed, addresses, tuple(roster), tuple(origin),
                          tuple(basis[:, 0]), tuple(basis[:, 1]),
                          tuple(address == (0, 0) for address in addresses),
                          diameter, pitch, radius)


def moved_plate(origins, *, rotation_deg, translation, hidden_wells=(), **array_options):
    """Two observations of a plate; well motion is one screen-rigid transform."""
    before, after = {}, {}
    rotation = _screen_rotation(rotation_deg)
    for well, origin in origins.items():
        before[well] = make_array(translation=origin, **array_options)
        moved = rotation @ np.asarray(origin) + np.asarray(translation)
        options = dict(array_options)
        bubbles = list(options.pop("bubbles", ()))
        if well in hidden_wells:
            bubbles.append((0.0, 0.0, options["pitch"] / 2))
        after[well] = make_array(translation=moved, rotation_deg=rotation_deg,
                                bubbles=bubbles, **options)
    return before, after


# ── rendered tiles ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TilePlan:
    """The tile plan of a synthetic acquisition: it changes between experiments,
    so tests build more than one rather than assuming 5 x 5 at 10 %."""
    rows: int
    cols: int
    tile_px: tuple[int, int]            # (width, height)
    overlap: float                      # fraction of the tile's extent, per axis
    pixel_size_um: float

    @property
    def extent_um(self):
        return self.tile_px[0] * self.pixel_size_um, self.tile_px[1] * self.pixel_size_um

    @property
    def step_um(self):
        ex, ey = self.extent_um
        return ex * (1 - self.overlap), ey * (1 - self.overlap)


@dataclass(frozen=True)
class RenderedTiles:
    plan: TilePlan
    images: dict                        # tile id -> float32 image (rows, cols), y down
    true_left_top_um: dict              # tile id -> where the tile really is
    recorded_left_top_um: dict          # tile id -> what the stage reported (truth + jitter)
    array: SyntheticArray               # in the common frame, hidden sites included
    sites_um: np.ndarray                # every roster site's centre, common frame
    bubble: tuple[float, float, float] | None


_BACKGROUND, _FLOOR, _NANOWELL = 0.35, 0.15, 0.85


def tile_id(plan: TilePlan, row: int, col: int) -> str:
    return f"F{row * plan.cols + col}"


def render_tiles(plan: TilePlan, *, pitch_um: float, diameter_um: float, radius_um: float,
                 rotation_deg: float = 0.0, stage_jitter_um: float = 0.0, vignette: float = 0.0,
                 noise_sigma: float = 0.0, cells: int = 0, debris: int = 0,
                 bubble=None, blur_px: float = 1.2, seed: int = 0) -> RenderedTiles:
    """Phase-contrast-like tiles of one hexagonal array: bright nanowells on a dark lattice.

    The array is the same physical object in every tile: the nanowells, cells,
    debris and bubble are drawn in the common frame at the tiles' *true*
    positions, and only the recorded positions carry the stage jitter. Camera
    noise is drawn per tile, because two exposures of one overlap do not share
    it. Vignetting darkens each tile toward its own corners. The central
    nanowell is square. Everything outside *radius_um* is flat floor.
    """
    from scipy import ndimage

    rng = np.random.default_rng(seed)
    pose = _screen_rotation(rotation_deg)
    native_basis = np.array([[pitch_um, pitch_um / 2], [0.0, -math.sqrt(3) * pitch_um / 2]])
    basis = pose @ native_basis                        # columns a1, a2
    inverse = np.linalg.inv(basis)
    union_w = plan.step_um[0] * (plan.cols - 1) + plan.extent_um[0]
    union_h = plan.step_um[1] * (plan.rows - 1) + plan.extent_um[1]
    centre = np.array([union_w / 2, union_h / 2])

    reach = math.ceil(2 * radius_um / pitch_um)
    roster = [(q, r) for q in range(-reach, reach + 1) for r in range(-reach, reach + 1)
              if np.linalg.norm(native_basis @ (q, r)) <= radius_um + pitch_um * 1e-12]
    sites = np.array([basis @ q_r for q_r in roster]) + centre
    array = SyntheticArray(sites, tuple(roster), tuple(roster), tuple(centre), tuple(basis[:, 0]),
                           tuple(basis[:, 1]), tuple(a == (0, 0) for a in roster),
                           diameter_um, pitch_um, radius_um)

    blobs = []
    for _ in range(cells):                              # non-periodic content: cells ...
        x, y = centre + rng.uniform(-radius_um, radius_um, 2)
        blobs.append((x, y, rng.uniform(6, 12), rng.choice((-0.35, 0.3))))
    for _ in range(debris):                             # ... and small sharp debris
        x, y = centre + rng.uniform(-radius_um, radius_um, 2)
        blobs.append((x, y, rng.uniform(2, 3.5), -0.5))

    images, true_lt, recorded_lt = {}, {}, {}
    px = plan.pixel_size_um
    for row in range(plan.rows):
        for col in range(plan.cols):
            name = tile_id(plan, row, col)
            left, top = col * plan.step_um[0], row * plan.step_um[1]
            true_lt[name] = (left, top)
            jitter = rng.uniform(-stage_jitter_um, stage_jitter_um, 2) if stage_jitter_um else (0.0, 0.0)
            recorded_lt[name] = (left + float(jitter[0]), top + float(jitter[1]))
            w, h = plan.tile_px
            xs = left + (np.arange(w) + 0.5) * px
            ys = top + (np.arange(h) + 0.5) * px
            gx, gy = np.meshgrid(xs, ys)
            pos = np.stack((gx, gy), axis=-1) - centre
            frac = pos @ inverse.T
            base = np.round(frac)
            best = np.full((h, w), np.inf)
            nearest = np.zeros((h, w, 2))
            for dq in (-1, 0, 1):
                for dr in (-1, 0, 1):
                    site = (base + (dq, dr)) @ basis.T
                    dist = np.linalg.norm(pos - site, axis=-1)
                    better = dist < best
                    best = np.where(better, dist, best)
                    nearest = np.where(better[..., None], site, nearest)
            in_array = np.linalg.norm(nearest, axis=-1) <= radius_um + pitch_um * 1e-12
            hidden = np.zeros((h, w), dtype=bool)
            if bubble is not None:
                bx, by, br = bubble
                hidden = np.hypot(gx - bx, gy - by) <= br
            edge = max(px, 1.0)
            disc = 1 / (1 + np.exp(-(diameter_um / 2 - best) / edge))
            local = pos - nearest
            unrot = local @ pose                        # back into the array's own frame
            square = 1 / (1 + np.exp(-(diameter_um / 2 - np.abs(unrot).max(axis=-1)) / edge))
            is_square = np.linalg.norm(nearest, axis=-1) < pitch_um * 1e-6
            shape = np.where(is_square, square, disc)
            image = np.where(in_array, _FLOOR + (_NANOWELL - _FLOOR) * shape, _BACKGROUND)
            image = np.where(hidden, _BACKGROUND, image)
            for x, y, sigma, amp in blobs:
                if left - 4 * sigma <= x <= left + w * px + 4 * sigma and top - 4 * sigma <= y <= top + h * px + 4 * sigma:
                    image = image + amp * np.exp(-((gx - x) ** 2 + (gy - y) ** 2) / (2 * sigma ** 2))
            image = ndimage.gaussian_filter(image, blur_px)
            if vignette:
                rr = np.hypot((np.arange(w) - w / 2)[None, :], (np.arange(h) - h / 2)[:, None]) / math.hypot(w / 2, h / 2)
                image = image * (1 - vignette * rr ** 2)
            if noise_sigma:
                image = image + rng.normal(0, noise_sigma, image.shape)
            images[name] = image.astype(np.float32)
    return RenderedTiles(plan, images, true_lt, recorded_lt, array, sites, bubble)
