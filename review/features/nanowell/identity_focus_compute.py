"""Going to one nanowell on the Identity view: read the address a reader typed, find where each timepoint puts it,
and work out the limits that centre a view on it.

Qt-free and canvas-free. The page's *Nanowell* box (``identity_view``) hands its text here through the controller;
``_identity_controller.draw_identity`` resolves it against the pair being drawn (:func:`focus_marks`), the renderer
marks it (``_identity_render.draw_focus``) and ``_identity_zoom.restore`` zooms onto it with :func:`focus_limits`.

An address is typed the way the page writes it: ``q,r`` as the Array view's *Addresses* layer and Blink's labels draw
it (``-2,3``; a space or brackets are accepted too), or the ring label the run's tables carry (``3.7``,
``util.nanowell_address.parse_label``). Text that is neither is refused with a sentence saying what is accepted,
never read as some other nanowell.

Where each timepoint puts the address is kept apart on purpose, because the two disagree exactly when the identity
check fails: the reference's own site, and the compared timepoint's site *of the same name* — which, mapped back into
the reference's frame, sits on another nanowell when the address names another nanowell.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

#: How many nanowell spacings a focused view shows from its centre to its nearer edge: about nine nanowells across,
#: enough to see the neighbours an address could have been confused with.
FOCUS_HALF_PITCHES = 4.0
#: The modes that draw nanowells in a frame a reader can go to. Diagnostics draws crops about the central square and
#: its match surface, in µm from the square, so a nanowell elsewhere in the array is not on it.
FOCUS_MODES = ("distance", "blink", "sides")

_COORDINATE = re.compile(r"\(?\s*(-?\d+)\s*(?:,\s*|\s+)(-?\d+)\s*\)?\Z")
_RING = re.compile(r"\d+\.\d+\Z")


def parse_address(text: str, kind: str = "hex") -> tuple[int, int]:
    """The ``(q, r)`` *text* names: ``q,r`` (``-2,3``, ``(-2, 3)``, ``-2 3``) or a ring label (``3.7``) on a
    lattice of *kind*. Raises ``ValueError`` with the sentence the page shows for anything else."""
    from util.nanowell_address import parse_label

    said = (text or "").strip()
    match = _COORDINATE.fullmatch(said)
    if match:
        return int(match.group(1)), int(match.group(2))
    if _RING.fullmatch(said):
        try:
            address = parse_label(said, kind=kind)
        except ValueError:
            raise ValueError(f"{said!r} is not a ring label on this {kind} lattice: ring {said.split('.')[0]} has "
                             f"no place {said.split('.')[1]}.") from None
        return address.q, address.r
    raise ValueError(f"{said!r} is not a nanowell address: type q,r as the addresses are written (for example "
                     "-2,3), or a ring label such as 3.7.")


@dataclass
class FocusMarks:
    """Where one address is on each timepoint of a pair, in each frame the Identity view draws."""
    address: tuple | None
    text: str                                 # what the page says about it, drawn on the figure
    ref_xy: tuple | None = None               # the reference's site, the reference's common frame
    other_xy: tuple | None = None             # the compared timepoint's site of that name, its own common frame
    other_in_ref: tuple | None = None         # that site mapped into the reference's frame (the registration)
    predicted_xy: tuple | None = None         # the reference's site mapped into the compared frame
    pitch: float | None = None

    @property
    def found(self) -> bool:
        return self.ref_xy is not None or self.other_xy is not None


def _xy(point) -> tuple[float, float]:
    return float(point[0]), float(point[1])


def _distance_said(pair, address) -> str:
    for q, r, d in (pair.entry or {}).get("distances") or ():
        if (int(q), int(r)) == address:
            return f"lands {float(d):.2f} spacing from itself at {pair.timepoint}"
    return f"its landing was not measured at {pair.timepoint}"


def focus_marks(pair, text: str) -> FocusMarks | None:
    """Where the address *text* names is on *pair* (``identity_compute.IdentityPair``); ``None`` for no text. A
    malformed address, or one neither timepoint holds, comes back with nothing found and a sentence saying so."""
    if not (text or "").strip():
        return None
    ref, other = pair.ref, pair.other
    kind = getattr(ref, "kind", None) or getattr(other, "kind", None) or "hex"
    try:
        address = parse_address(text, kind)
    except ValueError as exc:
        return FocusMarks(None, str(exc))
    name = f"{address[0]},{address[1]}"
    ref_sites, other_sites = getattr(ref, "sites", None) or {}, getattr(other, "sites", None) or {}
    ref_xy = _xy(ref_sites[address]) if address in ref_sites else None
    other_xy = _xy(other_sites[address]) if address in other_sites else None
    other_in_ref = _xy(pair.inverse([other_xy])[0]) if other_xy is not None and pair.inverse is not None else None
    predicted = _xy(pair.forward([ref_xy])[0]) if ref_xy is not None and pair.forward is not None else None
    pitch = getattr(ref, "pitch", None) or getattr(other, "pitch", None)
    if ref_xy is None and other_xy is None:
        said = f"Nanowell {name} is not addressed at {pair.reference} or at {pair.timepoint}."
    elif ref_xy is None:
        said = f"Nanowell {name}: not addressed at {pair.reference}; shown where {pair.timepoint} puts it."
    elif other_xy is None:
        said = f"Nanowell {name}: not addressed at {pair.timepoint}."
    else:
        said = f"Nanowell {name}: {_distance_said(pair, address)}."
    return FocusMarks(address, said, ref_xy, other_xy, other_in_ref, predicted, pitch)


def focus_centres(marks: FocusMarks | None, mode: str) -> dict:
    """The point each zoomable axes of *mode* centres on — ``{axes name: (x, y)}``, empty when nothing is found or
    the mode has no frame to go to. Distance map and Blink draw in the reference's frame; *Side by side*'s two
    montages share one set of limits (``_identity_render.render_sides``), so both centre where the reference puts the
    address, or where the compared timepoint does when only it holds it."""
    if marks is None or not marks.found or mode not in FOCUS_MODES:
        return {}
    if mode == "sides":
        centre = marks.ref_xy or marks.other_xy
        return {"reference": centre, "compared": centre}
    centre = marks.ref_xy or marks.other_in_ref
    return {"map": centre} if centre is not None else {}


def focus_half(marks: FocusMarks, mode: str) -> float | None:
    """Half the focused view's shorter side, in µm: :data:`FOCUS_HALF_PITCHES` spacings, widened on *Side by side*
    so the compared timepoint's site of the address stays in view beside the reference's when the plate moved."""
    if not marks.pitch:
        return None
    half = FOCUS_HALF_PITCHES * float(marks.pitch)
    if mode == "sides" and marks.ref_xy is not None and marks.other_xy is not None:
        apart = float(np.hypot(marks.other_xy[0] - marks.ref_xy[0], marks.other_xy[1] - marks.ref_xy[1]))
        half = max(half, apart + float(marks.pitch))
    return half


def focus_limits(home_xlim, home_ylim, centre, half: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """Limits centred on *centre* whose shorter side is ``2 * half``, in the full view's proportions and with its
    axis directions (an image's y runs down), so the panel keeps its shape; never wider than the full view."""
    width = float(home_xlim[1]) - float(home_xlim[0])
    height = float(home_ylim[1]) - float(home_ylim[0])
    if not width or not height:
        return (float(home_xlim[0]), float(home_xlim[1])), (float(home_ylim[0]), float(home_ylim[1]))
    shrink = min(1.0, 2.0 * half / min(abs(width), abs(height)))
    cx, cy = float(centre[0]), float(centre[1])
    return ((cx - width * shrink / 2, cx + width * shrink / 2), (cy - height * shrink / 2, cy + height * shrink / 2))
