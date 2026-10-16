"""Exact lattice addresses; positive r points counter-clockwise from east.

Hexagonal addressing is the default. Square addressing requires an explicit
kind because a printed ring label alone does not identify its lattice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from operator import index
import re

_HEX_DIRECTIONS = ((1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1))
_LABEL = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")


def _segments(ring: int, kind: str):
    if kind == "hex":
        for side, (q, r) in enumerate(_HEX_DIRECTIONS):
            dq, dr = _HEX_DIRECTIONS[(side + 2) % 6]
            yield (ring * q, ring * r), (dq, dr), ring
    else:
        yield (ring, 0), (0, 1), ring
        yield (ring, ring), (-1, 0), 2 * ring
        yield (-ring, ring), (0, -1), 2 * ring
        yield (-ring, -ring), (1, 0), 2 * ring
        yield (ring, -ring), (0, 1), ring


@dataclass(frozen=True)
class NanowellAddress:
    q: int
    r: int
    kind: str = field(default="hex", kw_only=True)

    def __post_init__(self):
        if self.kind not in {"hex", "square"}:
            raise ValueError(f"Unknown lattice kind: {self.kind!r}")
        for name in ("q", "r"):
            value = getattr(self, name)
            if isinstance(value, bool):
                raise ValueError(f"{name} must be an integer, not a boolean")
            try:
                value = index(value)
            except TypeError as exc:
                raise ValueError(f"{name} must be an integer: {value!r}") from exc
            object.__setattr__(self, name, value)

    @property
    def ring(self) -> int:
        if self.kind == "hex":
            return max(abs(self.q), abs(self.r), abs(self.q + self.r))
        return max(abs(self.q), abs(self.r))

    @property
    def label(self) -> str:
        ring = self.ring
        if ring == 0:
            return "0.0"
        offset = 0
        for (q, r), (dq, dr), length in _segments(ring, self.kind):
            step = (self.q - q) * dq if dq else (self.r - r) * dr
            if 0 <= step < length and (q + step * dq, r + step * dr) == (self.q, self.r):
                return f"{ring}.{offset + step}"
            offset += length
        raise AssertionError(f"Address is outside its ring: {self!r}")


def parse_label(label: str, *, kind: str = "hex") -> NanowellAddress:
    """Read a canonical ring label; malformed input is never normalised silently."""
    if kind not in {"hex", "square"}:
        raise ValueError(f"Unknown lattice kind: {kind!r}")
    match = _LABEL.fullmatch(label) if isinstance(label, str) else None
    if match is None:
        raise ValueError(f"Malformed nanowell label: {label!r}")
    ring, offset = map(int, match.groups())
    if ring == 0:
        if offset != 0:
            raise ValueError(f"Malformed nanowell label: {label!r}")
        return NanowellAddress(0, 0, kind=kind)
    if offset >= (6 if kind == "hex" else 8) * ring:
        raise ValueError(f"Malformed nanowell label: {label!r}")
    for (q, r), (dq, dr), length in _segments(ring, kind):
        if offset < length:
            return NanowellAddress(q + offset * dq, r + offset * dr, kind=kind)
        offset -= length
    raise AssertionError("Validated label exceeded its ring")
