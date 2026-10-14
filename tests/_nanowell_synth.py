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
