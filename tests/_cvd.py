"""Simulating how a dichromat sees a colour, so a palette can be measured.

The app's categorical palettes are checked for separation in RGB, which
answers "can a trichromat tell these apart" and nothing else. Roughly one man
in twelve cannot, and a figure whose only encoding of a fact is its colour is
then not degraded for them but wrong, with nothing on screen saying so.

Viénot, Brettel and Mollon's dichromat simulation: convert to linear light,
into LMS cone space, project onto the plane the missing cone type leaves, and
back. Written out here rather than pulled in as a dependency — it is fifteen
lines of arithmetic and the test stack is deliberately thin — with the
matrices from the paper's own worked values.

Deliberately not exact colour science: the projection is the standard
approximation, and the distances it produces are RGB distances, not a
perceptual metric. It is good enough for the question actually being asked,
which is whether two inks collapse onto each other, and its numbers are only
ever compared against floors measured the same way.
"""

from __future__ import annotations

VIEWS = ("normal", "protan", "deutan", "tritan")

_RGB2LMS = ((0.31399022, 0.63951294, 0.04649755),
            (0.15537241, 0.75789446, 0.08670142),
            (0.01775239, 0.10944209, 0.87256922))
_LMS2RGB = ((5.47221206, -4.6419601, 0.16963708),
            (-1.1252419, 2.29317094, -0.1678952),
            (0.02980165, -0.19318073, 1.16364789))
_SIM = {
    "protan": ((0.0, 1.05118294, -0.05116099), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "deutan": ((1.0, 0.0, 0.0), (0.9513092, 0.0, 0.04866992), (0.0, 0.0, 1.0)),
    "tritan": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (-0.86744736, 1.86727089, 0.0)),
}


def _matmul(m, v):
    return [sum(row[i] * v[i] for i in range(3)) for row in m]


def _to_linear(channel: float) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_srgb(channel: float) -> float:
    c = min(1.0, max(0.0, channel))
    return (c * 12.92 if c <= 0.0031308
            else 1.055 * c ** (1 / 2.4) - 0.055) * 255.0


def rgb(value: str) -> tuple[float, float, float]:
    """A ``#rrggbb`` string as three 0–255 floats."""
    text = value.lstrip("#")
    return tuple(float(int(text[i:i + 2], 16)) for i in (0, 2, 4))


def as_seen(value: str, view: str) -> tuple[float, float, float]:
    """*value* as seen under *view* — one of :data:`VIEWS`."""
    if view == "normal":
        return rgb(value)
    linear = [_to_linear(c) for c in rgb(value)]
    lms = _matmul(_RGB2LMS, linear)
    seen = _matmul(_LMS2RGB, _matmul(_SIM[view], lms))
    return tuple(_to_srgb(c) for c in seen)


def separation(colours, view: str = "normal") -> float:
    """The closest two of *colours*, in RGB, as seen under *view*."""
    points = [as_seen(c, view) for c in colours]
    return min(
        sum((a - b) ** 2 for a, b in zip(x, y)) ** 0.5
        for i, x in enumerate(points) for y in points[i + 1:])
