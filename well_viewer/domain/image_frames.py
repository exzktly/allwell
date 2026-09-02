"""A framed image grid: one picture per field, with its overlays as text.

The Image Table's saved figure is a grid of microscopy fields, each carrying
overlays the app burned into its pixels — the identity label, the scale bar, the
"raw" mark. As a picture that is a single flat raster, which is exactly what a
PowerPoint export of it used to be: one image on one slide, nothing selectable,
nothing retypeable.

This is the intermediate the export writes instead. It says where every field
sits, holds that field's pixels *without* the overlays burned in, and states
each overlay as a mark with its own place. The deck builder then emits one
PowerPoint **group** per field — the picture plus a text box per label and a
rectangle for the bar — so a slide arrives as a set of movable frames whose text
can be edited, rather than as a photograph of one.

Geometry is in **inches from the grid's top-left corner, y growing downward** —
PowerPoint's convention, not matplotlib's, because that is where these numbers
are going and a plan that flipped its axis somewhere in the middle would be the
kind of error nothing on the slide reveals.

Pure data plus a zip container. No Qt, no matplotlib, no app: a plan can be
built, written, read back and asserted on from bytes alone, which is what lets
the whole per-field export be tested without constructing a viewer.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from typing import Any

#: Bumped when the on-disk shape changes incompatibly. A plan written by a newer
#: build than the one reading it is declined outright rather than half-read: a
#: frame placed from a field this reader does not understand is a picture in the
#: wrong place, which looks exactly like a picture in the right one.
FORMAT_VERSION = 1

_PLAN_NAME = "grid.json"
_IMAGE_DIR = "frames"


class FramedGridError(ValueError):
    """A stored plan that could not be read back."""


def _hex6(value, field: str) -> str:
    """Six bare hex digits, or :class:`FramedGridError`.

    Validated **here**, at the boundary, because the consumer does not validate:
    ``_deck._hex`` raises on anything that is not six hex digits, deliberately,
    since every colour it was written for is a theme token. Once colours arrive
    off disk that becomes a way for one malformed plan to abort the whole
    gallery's deck — which it did. Refusing the plan instead leaves the export
    running and the grid placed as its picture, which is the documented
    fallback.
    """
    text = str(value or "").lstrip("#").upper()
    if len(text) != 6 or any(c not in "0123456789ABCDEF" for c in text):
        raise FramedGridError(f"{field}: not a six-digit hex colour: {value!r}")
    return text


def _positive(value, field: str) -> float:
    """A finite, positive measurement, or :class:`FramedGridError`.

    A grid whose width read back as garbage used to become ``0.0``, which
    ``_GridFit`` clamps to ``1e-6`` and then scales every rectangle by five
    million — shapes off the slide, no error, no note. Silent and wrong beats
    nothing at all only if nobody looks.
    """
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FramedGridError(f"{field}: not a number: {value!r}") from exc
    if not (number > 0.0) or number != number or number == float("inf"):
        raise FramedGridError(f"{field}: not a positive size: {value!r}")
    return number


def _alpha(value) -> int:
    """An opacity in DrawingML's hundred-thousandths, clamped to its range."""
    try:
        return max(0, min(100_000, int(value)))
    except (TypeError, ValueError) as exc:
        raise FramedGridError(f"not an opacity: {value!r}") from exc


@dataclass(frozen=True)
class Rect:
    """A box in inches from the grid's top-left, y downward."""

    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0

    def as_list(self) -> list[float]:
        return [float(self.x), float(self.y), float(self.w), float(self.h)]

    @classmethod
    def from_list(cls, values) -> Rect:
        try:
            x, y, w, h = (float(v) for v in values)
        except (TypeError, ValueError) as exc:
            raise FramedGridError(f"not a rectangle: {values!r}") from exc
        return cls(x, y, w, h)


@dataclass(frozen=True)
class TextMark:
    """One editable string, and where it goes.

    ``plate`` is the translucent dark box the burned-in identity label draws
    behind itself; carried here because a white label on a bright field is
    unreadable without it, and a deck that dropped it would be showing the label
    in a way the picture never did.
    """

    text: str
    rect: Rect
    size_pt: float = 9.0
    #: Six bare hex digits, the form DrawingML wants.
    color: str = "FFFFFF"
    #: ``"l"``, ``"ctr"`` or ``"r"`` — DrawingML's paragraph alignments.
    align: str = "l"
    #: ``"t"``, ``"ctr"`` or ``"b"`` — where the text sits in its box.
    anchor: str = "t"
    bold: bool = False
    plate: bool = False
    #: The plate's own colour and opacity (DrawingML's hundred-thousandths).
    #: Two marks want a filled box behind them for opposite reasons — an
    #: identity label needs a translucent dark plate to stay legible over a
    #: bright field, a facet group strip a solid pale band to express the
    #: grouping — so the colour travels with the mark rather than being one
    #: constant the deck applies to both.
    plate_color: str = "000000"
    plate_alpha: int = 55_000
    #: Read bottom-to-top, for a rotated facet strip.
    vertical: bool = False
    name: str = "Label"

    def to_dict(self) -> dict:
        return {
            "text": self.text, "rect": self.rect.as_list(),
            "size_pt": float(self.size_pt), "color": self.color,
            "align": self.align, "anchor": self.anchor, "bold": bool(self.bold),
            "plate": bool(self.plate), "plate_color": self.plate_color,
            "plate_alpha": int(self.plate_alpha),
            "vertical": bool(self.vertical), "name": self.name,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TextMark:
        return cls(
            text=str(d.get("text", "")),
            rect=Rect.from_list(d.get("rect") or [0, 0, 0, 0]),
            size_pt=float(d.get("size_pt", 9.0) or 9.0),
            color=_hex6(d.get("color", "FFFFFF") or "FFFFFF", "text colour"),
            align=str(d.get("align", "l") or "l"),
            anchor=str(d.get("anchor", "t") or "t"),
            bold=bool(d.get("bold", False)),
            plate=bool(d.get("plate", False)),
            plate_color=_hex6(d.get("plate_color", "000000") or "000000",
                              "plate colour"),
            plate_alpha=_alpha(d.get("plate_alpha", 55_000)),
            vertical=bool(d.get("vertical", False)),
            name=str(d.get("name", "Label") or "Label"),
        )


@dataclass(frozen=True)
class BarMark:
    """A scale bar as a filled rectangle.

    Its *length* is the whole point of it — that is what states the physical
    scale — so it is carried as geometry and placed proportionally with the
    frame, never re-derived on the far side from a font size or a fraction.
    """

    rect: Rect
    color: str = "FFFFFF"
    #: The contrasting outline the burned-in bar draws around itself.
    halo: str = "000000"
    name: str = "Scale bar"

    def to_dict(self) -> dict:
        return {"rect": self.rect.as_list(), "color": self.color,
                "halo": self.halo, "name": self.name}

    @classmethod
    def from_dict(cls, d: dict) -> BarMark:
        return cls(
            rect=Rect.from_list(d.get("rect") or [0, 0, 0, 0]),
            color=_hex6(d.get("color", "FFFFFF") or "FFFFFF", "bar colour"),
            halo=_hex6(d.get("halo", "000000") or "000000", "bar halo"),
            name=str(d.get("name", "Scale bar") or "Scale bar"),
        )


@dataclass(frozen=True)
class ImageFrame:
    """One field of view: its picture, and the marks drawn over it.

    ``png`` is the field **without** its overlays — they are in ``texts`` and
    ``bars``. A frame whose picture could not be encoded still belongs in the
    plan: its marks and its place are known, and the deck says the picture is
    missing rather than shifting everything after it.
    """

    name: str = "Field"
    rect: Rect = field(default_factory=Rect)
    png: bytes = b""
    texts: tuple[TextMark, ...] = ()
    bars: tuple[BarMark, ...] = ()

    def to_dict(self) -> dict:
        return {"name": self.name, "rect": self.rect.as_list(),
                "texts": [t.to_dict() for t in self.texts],
                "bars": [b.to_dict() for b in self.bars]}


@dataclass(frozen=True)
class FramedGrid:
    """The whole grid: its size in inches, its frames, and the axis labels.

    ``labels`` are the marks that belong to the grid rather than to any one
    field — the facet row/column headers, the group bands, the corner label.
    They are separate from the frames because a user regrouping the fields on a
    slide should not drag the axis labels along with them.
    """

    width_in: float = 0.0
    height_in: float = 0.0
    frames: tuple[ImageFrame, ...] = ()
    labels: tuple[TextMark, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.frames and not self.labels

    @property
    def has_pictures(self) -> bool:
        """Does any field actually carry an image?

        Both routes into the deck ask this before placing a plan, because a grid
        whose every field failed to encode places a page of empty boxes and
        reports it as a success — while the flat snapshot that would have shown
        the user their grid sits unused beside it. The direct export had this
        guard and the gallery route did not, which is the half-swept version of
        the same rule.
        """
        return any(f.png for f in self.frames)


def to_bytes(grid: FramedGrid) -> bytes:
    """Serialise *grid* to the zip container stored beside a saved plot.

    A zip rather than one JSON document because the frames are pictures: base64
    inside JSON would inflate every field by a third and make the file
    unreadable by anything but this module. Here each field is a plain
    ``frames/<n>.png`` a user can open.
    """
    plan: dict[str, Any] = {
        "version": FORMAT_VERSION,
        "width_in": float(grid.width_in),
        "height_in": float(grid.height_in),
        "labels": [t.to_dict() for t in grid.labels],
        "frames": [],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for index, frame in enumerate(grid.frames):
            entry = frame.to_dict()
            if frame.png:
                name = f"{_IMAGE_DIR}/{index}.png"
                zf.writestr(name, frame.png)
                entry["png"] = name
            plan["frames"].append(entry)
        zf.writestr(_PLAN_NAME, json.dumps(plan))
    return buf.getvalue()


def from_bytes(data: bytes) -> FramedGrid:
    """Read a plan back. Raises :class:`FramedGridError` on anything malformed.

    Never a partial grid: a plan that cannot be read in full is refused, because
    a deck built from half of one places some fields correctly and silently
    drops the rest.
    """
    if not data:
        raise FramedGridError("no frame plan")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            plan = json.loads(zf.read(_PLAN_NAME).decode("utf-8"))
            if int(plan.get("version", 0)) > FORMAT_VERSION:
                raise FramedGridError(
                    f"frame plan version {plan.get('version')} is newer than "
                    f"this build understands ({FORMAT_VERSION})")
            frames = []
            for entry in plan.get("frames") or []:
                png = b""
                name = entry.get("png")
                if name:
                    png = zf.read(str(name))
                frames.append(ImageFrame(
                    name=str(entry.get("name", "Field") or "Field"),
                    rect=Rect.from_list(entry.get("rect") or [0, 0, 0, 0]),
                    png=png,
                    texts=tuple(TextMark.from_dict(t)
                                for t in (entry.get("texts") or [])),
                    bars=tuple(BarMark.from_dict(b)
                               for b in (entry.get("bars") or [])),
                ))
            labels = tuple(TextMark.from_dict(t)
                           for t in (plan.get("labels") or []))
    except FramedGridError:
        raise
    except (AttributeError, IndexError, KeyError, OSError, TypeError,
            UnicodeDecodeError, ValueError, zipfile.BadZipFile) as exc:
        # ``AttributeError`` and ``TypeError`` belong here as much as the rest:
        # a JSON document is arbitrary data, so a "frame" that is a string and a
        # "frames" that is a dict are ordinary malformed input rather than
        # programming errors, and letting them escape as themselves broke the
        # promise in this function's own docstring — and, through ``_try_frames``,
        # took every other plot's slide down with the bad plan.
        raise FramedGridError(f"unreadable frame plan: {exc}") from exc
    return FramedGrid(
        width_in=_positive(plan.get("width_in"), "width_in"),
        height_in=_positive(plan.get("height_in"), "height_in"),
        frames=tuple(frames), labels=labels,
    )
