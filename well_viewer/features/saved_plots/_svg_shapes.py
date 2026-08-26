"""Read a Matplotlib SVG into a flat list of drawable shapes.

This is the front half of the PowerPoint export. Matplotlib's SVG writer emits a
small, very regular subset of SVG — filled/stroked ``<path>`` elements, ``<use>``
instances of marker paths kept in ``<defs>``, ``<text>`` runs (when
``svg.fonttype`` is ``"none"``, which is how the capture in
``widgets/fig_clipboard.render_figure_svg`` renders it), rasterised ``<image>``
blocks for things like ``imshow``, and rectangular ``<clipPath>`` definitions.
Nothing here tries to be a general SVG reader; it handles that subset exactly and
says so when it meets something outside it.

Everything is resolved into **absolute user-space coordinates** as it is read:
the transform stack is applied to every point, presentation styles are inherited
down the tree, and ``<use>`` references are expanded into real geometry. What
comes out is a :class:`Drawing` — a page size in points plus a flat, ordered list
of :class:`PathShape` / :class:`TextShape` / :class:`ImageShape` — which the
DrawingML writer beside this module turns into PowerPoint shapes one-for-one.

SVG user units in a Matplotlib figure are points, so the coordinates here are
points throughout and the writer is the only place that knows about EMUs.
"""

from __future__ import annotations

import base64
import math
import re
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"

#: Cubic segments are flattened to this many line segments when a path has to be
#: clipped. Only clipped paths are flattened — everything inside its clip
#: rectangle keeps its curves, so a marker that is fully on-axes stays a curve in
#: PowerPoint and only one that straddles the axes boundary is polygonised.
_FLATTEN_STEPS = 16

#: How far outside a clip rectangle a point may sit before the path is treated as
#: needing a clip at all. Matplotlib routinely draws the axes spines exactly on
#: the clip boundary, and floating-point noise there would otherwise polygonise
#: every one of them for no visible gain.
_CLIP_EPS = 1e-6


# ── style ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Paint:
    """A solid colour plus its opacity, or ``None`` for "no paint"."""

    rgb: str                    # 6 hex digits, upper case, no leading '#'
    alpha: float = 1.0          # 0.0–1.0


@dataclass(frozen=True)
class Stroke:
    """A stroked outline: colour, width in points, and the join/cap style."""

    paint: Paint
    width_pt: float = 1.0
    linejoin: str = "round"     # round | miter | bevel
    linecap: str = "butt"       # butt | round | square
    dashes: tuple[float, ...] = ()   # dash pattern in points; empty = solid


# ── shapes ───────────────────────────────────────────────────────────────────

#: One subpath: an ordered list of segments, each ``("M", pt)``, ``("L", pt)``,
#: ``("C", c1, c2, pt)`` or ``("Z",)``, with every point already in absolute
#: user space.
Subpath = list[tuple]


@dataclass
class PathShape:
    """Filled and/or stroked geometry."""

    subpaths: list[Subpath]
    fill: Paint | None = None
    stroke: Stroke | None = None
    name: str = "path"


@dataclass
class TextShape:
    """A single text run, anchored the way SVG anchors it.

    ``x``/``y`` are the SVG anchor point — ``y`` on the text baseline — and
    ``anchor`` is one of ``start`` / ``middle`` / ``end``. ``rotation_deg`` is
    clockwise about that same anchor point, matching SVG's ``rotate(a cx cy)``.
    """

    text: str
    x: float
    y: float
    size_pt: float = 10.0
    family: str = "Arial"
    weight: str = "normal"
    style: str = "normal"
    anchor: str = "start"
    rotation_deg: float = 0.0
    fill: Paint | None = None
    name: str = "text"


@dataclass
class ImageShape:
    """A rasterised block (``imshow``, a colour bar, an inset image)."""

    png: bytes
    x: float
    y: float
    width: float
    height: float
    flip_h: bool = False
    flip_v: bool = False
    name: str = "image"


@dataclass
class Drawing:
    """A parsed figure: its page size in points and the shapes on it."""

    width_pt: float
    height_pt: float
    shapes: list = field(default_factory=list)

    #: Constructs met that this reader does not implement, each named once.
    #: The caller surfaces these rather than letting an export quietly drop
    #: part of a figure.
    unsupported: list[str] = field(default_factory=list)


class SvgParseError(ValueError):
    """The bytes handed in were not an SVG this reader can work with."""


# ── numbers, colours, transforms ─────────────────────────────────────────────

_NUM_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")

_NAMED_COLORS = {
    "black": "000000", "white": "FFFFFF", "red": "FF0000", "green": "008000",
    "blue": "0000FF", "gray": "808080", "grey": "808080", "none": "",
}


def _numbers(text: str) -> list[float]:
    return [float(m.group()) for m in _NUM_RE.finditer(text or "")]


def _parse_color(value: str) -> str | None:
    """An SVG colour as 6 upper-case hex digits, or ``None`` for no paint."""
    raw = (value or "").strip().lower()
    if not raw or raw in ("none", "transparent"):
        return None
    if raw.startswith("#"):
        digits = raw[1:]
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        if len(digits) == 8:          # #rrggbbaa — alpha is carried separately
            digits = digits[:6]
        if len(digits) == 6:
            return digits.upper()
        return None
    if raw.startswith("rgb"):
        parts = _numbers(raw)[:3]
        if len(parts) == 3:
            return "".join(f"{max(0, min(255, int(round(p)))):02X}" for p in parts)
        return None
    named = _NAMED_COLORS.get(raw)
    return named.upper() if named else None


def _mat_mul(m: tuple, n: tuple) -> tuple:
    """Compose two ``(a, b, c, d, e, f)`` affine matrices — *m* then *n* applied
    to the result, i.e. the SVG convention where the outer transform is left."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (a1 * a2 + c1 * b2,
            b1 * a2 + d1 * b2,
            a1 * c2 + c1 * d2,
            b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1,
            b1 * e2 + d1 * f2 + f1)


_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _apply(m: tuple, pt: tuple) -> tuple:
    a, b, c, d, e, f = m
    x, y = pt
    return (a * x + c * y + e, b * x + d * y + f)


_TRANSFORM_RE = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")


def _parse_transform(value: str) -> tuple:
    """Parse an SVG ``transform`` attribute into an affine matrix."""
    out = _IDENTITY
    for kind, body in _TRANSFORM_RE.findall(value or ""):
        args = _numbers(body)
        if kind == "matrix" and len(args) == 6:
            m = tuple(args)
        elif kind == "translate" and args:
            m = (1.0, 0.0, 0.0, 1.0, args[0], args[1] if len(args) > 1 else 0.0)
        elif kind == "scale" and args:
            sx = args[0]
            sy = args[1] if len(args) > 1 else sx
            m = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif kind == "rotate" and args:
            rad = math.radians(args[0])
            cos, sin = math.cos(rad), math.sin(rad)
            m = (cos, sin, -sin, cos, 0.0, 0.0)
            if len(args) >= 3:      # rotate about (cx, cy)
                cx, cy = args[1], args[2]
                m = _mat_mul((1.0, 0.0, 0.0, 1.0, cx, cy), m)
                m = _mat_mul(m, (1.0, 0.0, 0.0, 1.0, -cx, -cy))
        elif kind == "skewX" and args:
            m = (1.0, 0.0, math.tan(math.radians(args[0])), 1.0, 0.0, 0.0)
        elif kind == "skewY" and args:
            m = (1.0, math.tan(math.radians(args[0])), 0.0, 1.0, 0.0, 0.0)
        else:
            continue
        out = _mat_mul(out, m)
    return out


def _mean_scale(m: tuple) -> float:
    """The transform's average linear scale — what a stroke width is multiplied
    by. The geometric mean of the two axis scales, so a figure scaled evenly
    keeps its line weights proportional."""
    a, b, c, d = m[0], m[1], m[2], m[3]
    sx = math.hypot(a, b)
    sy = math.hypot(c, d)
    return math.sqrt(sx * sy) if sx > 0 and sy > 0 else max(sx, sy, 1.0)


# ── declarations (``style="…"`` plus presentation attributes) ────────────────

#: The properties this reader understands. Anything else in a ``style`` string is
#: ignored rather than guessed at.
_STYLE_KEYS = (
    "fill", "stroke", "stroke-width", "fill-opacity", "stroke-opacity",
    "opacity", "stroke-linejoin", "stroke-linecap", "stroke-dasharray",
    "font-family", "font-size", "font-weight", "font-style", "font",
    "text-anchor",
)


def _declarations(elem) -> dict:
    """The style declarations on one element, ``style="…"`` last so it wins.

    Matplotlib writes almost everything into ``style``, but ``<defs>`` marker
    paths and hand-built SVGs use presentation attributes, and both spellings
    reach here through the same dictionary.
    """
    out = {}
    for key in _STYLE_KEYS:
        value = elem.get(key)
        if value is not None:
            out[key] = value.strip()
    for chunk in (elem.get("style") or "").split(";"):
        if ":" not in chunk:
            continue
        key, _, value = chunk.partition(":")
        key = key.strip().lower()
        if key in _STYLE_KEYS:
            out[key] = value.strip()
    return out


#: ``font: <style> <weight> <size>px <family list>`` — the shorthand Matplotlib
#: writes for every text run. Style and weight are optional and may appear in
#: either order, which is why they are matched as a single leading blob.
_FONT_SHORTHAND_RE = re.compile(
    r"^\s*(?P<lead>(?:[a-z-]+\s+)*?)(?P<size>[\d.]+)(?P<unit>px|pt|em)?\s+(?P<family>.+)$",
    re.I)


def _expand_font_shorthand(decls: dict) -> dict:
    """Split a ``font:`` shorthand into the longhand keys the rest of the reader
    reads. Longhand already present wins, matching CSS cascade order within one
    declaration block."""
    shorthand = decls.get("font")
    if not shorthand:
        return decls
    match = _FONT_SHORTHAND_RE.match(shorthand)
    if match is None:
        return decls
    out = dict(decls)
    lead = (match.group("lead") or "").split()
    for word in lead:
        low = word.lower()
        if low in ("italic", "oblique"):
            out.setdefault("font-style", low)
        elif low == "bold" or low.isdigit():
            out.setdefault("font-weight", low)
    size = float(match.group("size"))
    # A Matplotlib SVG's user unit *is* the point, and it writes the font size in
    # "px" of that same user space — so px and pt are the same number here. A
    # real CSS pixel would be 0.75pt, and converting would shrink every label in
    # the deck by a quarter.
    out.setdefault("font-size", str(size))
    out.setdefault("font-family", match.group("family"))
    return out


def _first_family(value: str) -> str:
    """The first usable family from a CSS font stack.

    PowerPoint takes exactly one typeface per run, so a stack of eight fallbacks
    has to collapse to its head. ``sans-serif`` and friends are generic keywords
    rather than installed fonts, so they map to the face PowerPoint would pick
    anyway instead of being written through as a literal name.
    """
    generics = {"sans-serif": "Arial", "serif": "Times New Roman",
                "monospace": "Consolas", "cursive": "Segoe Script",
                "fantasy": "Impact", "system-ui": "Arial"}
    for part in (value or "").split(","):
        name = part.strip().strip("'\"").strip()
        if not name:
            continue
        return generics.get(name.lower(), name)
    return "Arial"


def _paint_from(decls: dict, key: str, inherited: dict) -> Paint | None:
    """The fill or stroke paint named by *key*, or ``None`` for no paint."""
    raw = decls.get(key, inherited.get(key))
    if raw is None:
        # SVG's initial values: fill black, stroke none.
        raw = "#000000" if key == "fill" else "none"
    rgb = _parse_color(raw)
    if rgb is None:
        return None
    alpha = _float_or(decls.get(f"{key}-opacity", inherited.get(f"{key}-opacity")), 1.0)
    alpha *= _float_or(decls.get("opacity", inherited.get("opacity")), 1.0)
    if alpha <= 0.0:
        return None
    return Paint(rgb=rgb, alpha=max(0.0, min(1.0, alpha)))


def _float_or(value, default: float) -> float:
    try:
        return float(str(value).strip().rstrip("px").rstrip("pt"))
    except (TypeError, ValueError):
        return default


def _stroke_from(decls: dict, inherited: dict, scale: float) -> Stroke | None:
    paint = _paint_from(decls, "stroke", inherited)
    if paint is None:
        return None
    width = _float_or(decls.get("stroke-width", inherited.get("stroke-width")), 1.0)
    dash_raw = decls.get("stroke-dasharray", inherited.get("stroke-dasharray")) or ""
    dashes = tuple(n * scale for n in _numbers(dash_raw)) if "none" not in dash_raw else ()
    return Stroke(
        paint=paint,
        width_pt=max(0.0, width * scale),
        linejoin=(decls.get("stroke-linejoin", inherited.get("stroke-linejoin")) or "round"),
        linecap=(decls.get("stroke-linecap", inherited.get("stroke-linecap")) or "butt"),
        dashes=dashes,
    )


# ── path data ────────────────────────────────────────────────────────────────

_CMD_RE = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)")

#: How many numbers each path command consumes per repetition.
_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2,
          "A": 7, "Z": 0}


def _tokenize_path(data: str):
    """Yield ``(command_letter, [numbers…])`` groups from a ``d`` attribute."""
    cmd = None
    args: list[float] = []
    for letter, number in _CMD_RE.findall(data or ""):
        if letter:
            if cmd is not None:
                yield cmd, args
            cmd, args = letter, []
        elif cmd is not None:
            args.append(float(number))
    if cmd is not None:
        yield cmd, args


class _PathBuilder:
    """Turns path commands into absolute-coordinate subpaths.

    Quadratic segments are raised to cubics and smooth (``S``/``T``) segments
    have their reflected control point computed here, so everything downstream
    sees only move / line / cubic / close — the four things DrawingML's
    ``a:path`` can express directly.
    """

    def __init__(self) -> None:
        self.subpaths: list[Subpath] = []
        self._cur: Subpath = []
        self._pt = (0.0, 0.0)
        self._start = (0.0, 0.0)
        self._prev_cubic_ctrl = None
        self._prev_quad_ctrl = None

    def _flush(self) -> None:
        if len(self._cur) > 1:
            self.subpaths.append(self._cur)
        self._cur = []

    def move_to(self, pt) -> None:
        self._flush()
        self._cur = [("M", pt)]
        self._pt = self._start = pt
        self._prev_cubic_ctrl = self._prev_quad_ctrl = None

    def line_to(self, pt) -> None:
        if not self._cur:
            self._cur = [("M", self._pt)]
        self._cur.append(("L", pt))
        self._pt = pt
        self._prev_cubic_ctrl = self._prev_quad_ctrl = None

    def cubic_to(self, c1, c2, pt) -> None:
        if not self._cur:
            self._cur = [("M", self._pt)]
        self._cur.append(("C", c1, c2, pt))
        self._pt = pt
        self._prev_cubic_ctrl = c2
        self._prev_quad_ctrl = None

    def quad_to(self, ctrl, pt) -> None:
        # A quadratic is exactly representable as a cubic; do the lift here so
        # the shape model never has to carry two curve kinds.
        px, py = self._pt
        qx, qy = ctrl
        ex, ey = pt
        c1 = (px + 2.0 / 3.0 * (qx - px), py + 2.0 / 3.0 * (qy - py))
        c2 = (ex + 2.0 / 3.0 * (qx - ex), ey + 2.0 / 3.0 * (qy - ey))
        self.cubic_to(c1, c2, pt)
        self._prev_quad_ctrl = ctrl

    def close(self) -> None:
        if self._cur:
            self._cur.append(("Z",))
            self._pt = self._start
        self._flush()
        self._prev_cubic_ctrl = self._prev_quad_ctrl = None

    def finish(self) -> list[Subpath]:
        self._flush()
        return self.subpaths

    # ── reflected control points for the smooth variants ──────────────────
    def reflected_cubic(self) -> tuple:
        if self._prev_cubic_ctrl is None:
            return self._pt
        return (2 * self._pt[0] - self._prev_cubic_ctrl[0],
                2 * self._pt[1] - self._prev_cubic_ctrl[1])

    def reflected_quad(self) -> tuple:
        if self._prev_quad_ctrl is None:
            return self._pt
        return (2 * self._pt[0] - self._prev_quad_ctrl[0],
                2 * self._pt[1] - self._prev_quad_ctrl[1])

    @property
    def point(self) -> tuple:
        return self._pt


def parse_path_data(data: str, unsupported: list | None = None) -> list[Subpath]:
    """Parse an SVG ``d`` attribute into absolute-coordinate subpaths.

    Elliptical arcs (``A``) are the one command with no DrawingML custom-geometry
    equivalent that a straight translation could produce; Matplotlib never emits
    one, so rather than approximate silently this records the fact in
    *unsupported* and draws the arc's chord.
    """
    builder = _PathBuilder()
    for cmd, args in _tokenize_path(data):
        upper = cmd.upper()
        rel = cmd.islower()
        arity = _ARITY.get(upper)
        if arity is None:
            continue
        if arity == 0:
            builder.close()
            continue
        # A repeated argument group continues the same command, except that a
        # repeated moveto implicitly becomes a lineto (SVG 1.1 §8.3.2).
        chunks = [args[i:i + arity] for i in range(0, len(args), arity)]
        for index, chunk in enumerate(chunks):
            if len(chunk) < arity:
                break
            effective = "L" if (upper == "M" and index > 0) else upper
            _emit_segment(builder, effective, chunk, rel, unsupported)
    return builder.finish()


def _rel(builder: _PathBuilder, rel: bool, x: float, y: float) -> tuple:
    if not rel:
        return (x, y)
    px, py = builder.point
    return (px + x, py + y)


def _emit_segment(builder, cmd: str, a: list, rel: bool, unsupported) -> None:
    """Append one parsed command to *builder*."""
    if cmd == "M":
        builder.move_to(_rel(builder, rel, a[0], a[1]))
    elif cmd == "L":
        builder.line_to(_rel(builder, rel, a[0], a[1]))
    elif cmd == "H":
        px, py = builder.point
        builder.line_to((px + a[0], py) if rel else (a[0], py))
    elif cmd == "V":
        px, py = builder.point
        builder.line_to((px, py + a[0]) if rel else (px, a[0]))
    elif cmd == "C":
        builder.cubic_to(_rel(builder, rel, a[0], a[1]),
                         _rel(builder, rel, a[2], a[3]),
                         _rel(builder, rel, a[4], a[5]))
    elif cmd == "S":
        builder.cubic_to(builder.reflected_cubic(),
                         _rel(builder, rel, a[0], a[1]),
                         _rel(builder, rel, a[2], a[3]))
    elif cmd == "Q":
        builder.quad_to(_rel(builder, rel, a[0], a[1]),
                        _rel(builder, rel, a[2], a[3]))
    elif cmd == "T":
        builder.quad_to(builder.reflected_quad(), _rel(builder, rel, a[0], a[1]))
    elif cmd == "A":
        if unsupported is not None and "elliptical arc" not in unsupported:
            unsupported.append("elliptical arc")
        builder.line_to(_rel(builder, rel, a[5], a[6]))


def transform_subpaths(subpaths: list[Subpath], matrix: tuple) -> list[Subpath]:
    """Map every point of every subpath through an affine *matrix*.

    Cubic control points transform affinely along with their endpoints, so a
    curve stays a curve — no flattening is involved.
    """
    if matrix == _IDENTITY:
        return subpaths
    out = []
    for sub in subpaths:
        moved = []
        for seg in sub:
            if seg[0] == "Z":
                moved.append(seg)
            else:
                moved.append((seg[0], *(_apply(matrix, p) for p in seg[1:])))
        out.append(moved)
    return out


# ── rectangular clipping ─────────────────────────────────────────────────────

def subpath_bounds(subpaths: list[Subpath]) -> tuple | None:
    """``(x0, y0, x1, y1)`` over every point, or ``None`` for empty geometry.

    Control points are included, so the box is the convex-hull bound rather than
    the tight curve bound. That is the conservative direction: it can only make a
    path look like it needs clipping when it does not, never the reverse.
    """
    xs: list[float] = []
    ys: list[float] = []
    for sub in subpaths:
        for seg in sub:
            for pt in seg[1:]:
                xs.append(pt[0])
                ys.append(pt[1])
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _inside(bounds: tuple, rect: tuple) -> bool:
    return (bounds[0] >= rect[0] - _CLIP_EPS and bounds[1] >= rect[1] - _CLIP_EPS
            and bounds[2] <= rect[2] + _CLIP_EPS and bounds[3] <= rect[3] + _CLIP_EPS)


def _disjoint(bounds: tuple, rect: tuple) -> bool:
    return (bounds[2] < rect[0] - _CLIP_EPS or bounds[0] > rect[2] + _CLIP_EPS
            or bounds[3] < rect[1] - _CLIP_EPS or bounds[1] > rect[3] + _CLIP_EPS)


def _flatten(sub: Subpath) -> list[tuple]:
    """A subpath as a polyline, subdividing cubics into fixed-length runs."""
    pts: list[tuple] = []
    cur = None
    for seg in sub:
        if seg[0] == "M":
            cur = seg[1]
            pts.append(cur)
        elif seg[0] == "L":
            cur = seg[1]
            pts.append(cur)
        elif seg[0] == "C" and cur is not None:
            p0, (c1, c2, p3) = cur, seg[1:]
            for i in range(1, _FLATTEN_STEPS + 1):
                t = i / _FLATTEN_STEPS
                u = 1.0 - t
                pts.append((
                    u * u * u * p0[0] + 3 * u * u * t * c1[0]
                    + 3 * u * t * t * c2[0] + t * t * t * p3[0],
                    u * u * u * p0[1] + 3 * u * u * t * c1[1]
                    + 3 * u * t * t * c2[1] + t * t * t * p3[1]))
            cur = p3
        elif seg[0] == "Z" and pts:
            pts.append(pts[0])
            cur = pts[0]
    return pts


def _clip_polygon(points: list[tuple], rect: tuple) -> list[tuple]:
    """Sutherland–Hodgman clip of a filled polygon against an axis-aligned box."""
    x0, y0, x1, y1 = rect
    edges = (
        (lambda p: p[0] >= x0, lambda a, b: _cut_x(a, b, x0)),
        (lambda p: p[0] <= x1, lambda a, b: _cut_x(a, b, x1)),
        (lambda p: p[1] >= y0, lambda a, b: _cut_y(a, b, y0)),
        (lambda p: p[1] <= y1, lambda a, b: _cut_y(a, b, y1)),
    )
    poly = list(points)
    for keep, cut in edges:
        if not poly:
            return []
        out: list[tuple] = []
        prev = poly[-1]
        for cur in poly:
            if keep(cur):
                if not keep(prev):
                    out.append(cut(prev, cur))
                out.append(cur)
            elif keep(prev):
                out.append(cut(prev, cur))
            prev = cur
        poly = out
    return poly


def _cut_x(a: tuple, b: tuple, x: float) -> tuple:
    if b[0] == a[0]:
        return (x, a[1])
    t = (x - a[0]) / (b[0] - a[0])
    return (x, a[1] + t * (b[1] - a[1]))


def _cut_y(a: tuple, b: tuple, y: float) -> tuple:
    if b[1] == a[1]:
        return (a[0], y)
    t = (y - a[1]) / (b[1] - a[1])
    return (a[0] + t * (b[0] - a[0]), y)


def _clip_segment(a: tuple, b: tuple, rect: tuple) -> tuple | None:
    """Liang–Barsky clip of one line segment; ``None`` when it misses the box."""
    x0, y0, x1, y1 = rect
    dx, dy = b[0] - a[0], b[1] - a[1]
    t_enter, t_leave = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]),
                 (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p == 0:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            t_enter = max(t_enter, t)
        else:
            t_leave = min(t_leave, t)
    if t_enter > t_leave:
        return None
    return ((a[0] + t_enter * dx, a[1] + t_enter * dy),
            (a[0] + t_leave * dx, a[1] + t_leave * dy))


def _clip_polyline(points: list[tuple], rect: tuple) -> list[list[tuple]]:
    """Clip an open polyline, returning the runs that survive."""
    runs: list[list[tuple]] = []
    current: list[tuple] = []
    for a, b in zip(points, points[1:]):
        piece = _clip_segment(a, b, rect)
        if piece is None:
            current = []
            continue
        start, end = piece
        if current and _close_enough(current[-1], start):
            current.append(end)
        else:
            current = [start, end]
            runs.append(current)
    return runs


def _close_enough(a: tuple, b: tuple) -> bool:
    return abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9


def _as_subpath(points: list[tuple], closed: bool) -> Subpath:
    sub: Subpath = [("M", points[0])]
    sub.extend(("L", p) for p in points[1:])
    if closed:
        sub.append(("Z",))
    return sub


def clip_subpaths(subpaths: list[Subpath], rect: tuple, *,
                  filled: bool) -> list[Subpath]:
    """Clip geometry to an axis-aligned *rect*.

    Geometry entirely inside the rectangle is returned untouched — curves and
    all — because that is the overwhelmingly common case and PowerPoint should
    receive the real curve rather than a sixteen-segment imitation of it. Only a
    subpath that actually crosses the boundary is flattened and cut, and a
    subpath entirely outside is dropped.

    *filled* selects the algorithm: a filled subpath is clipped as a polygon, so
    the cut edge closes along the rectangle; a stroked one is clipped as an open
    polyline, so the cut simply ends the line.
    """
    bounds = subpath_bounds(subpaths)
    if bounds is None or _inside(bounds, rect):
        return subpaths
    out: list[Subpath] = []
    for sub in subpaths:
        sub_bounds = subpath_bounds([sub])
        if sub_bounds is None or _disjoint(sub_bounds, rect):
            continue
        if _inside(sub_bounds, rect):
            out.append(sub)
            continue
        points = _flatten(sub)
        if len(points) < 2:
            continue
        if filled:
            clipped = _clip_polygon(points, rect)
            if len(clipped) >= 3:
                out.append(_as_subpath(clipped, closed=True))
        else:
            for run in _clip_polyline(points, rect):
                if len(run) >= 2:
                    out.append(_as_subpath(run, closed=False))
    return out


# ── the walk ─────────────────────────────────────────────────────────────────

def _tag(elem) -> str:
    """The local tag name, namespace stripped."""
    tag = elem.tag
    if isinstance(tag, str) and tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag if isinstance(tag, str) else ""


def _href(elem) -> str:
    return (elem.get(f"{{{_XLINK_NS}}}href") or elem.get("href") or "").strip()


def _length(value, default: float = 0.0) -> float:
    """A length attribute in user units, dropping any ``pt``/``px`` suffix.

    Matplotlib's root ``width``/``height`` are in points and its ``viewBox`` is
    in the same user units, so no unit conversion is involved anywhere in this
    reader.
    """
    return _float_or(value, default)


class _Walker:
    """Depth-first walk of the SVG tree, accumulating shapes.

    Carries three things down: the current transform, the inherited style
    declarations, and the clip rectangle in force. Everything a node emits is
    already resolved against all three.
    """

    def __init__(self, root) -> None:
        self.root = root
        self.defs_paths: dict[str, tuple[list[Subpath], dict]] = {}
        self.clips: dict[str, tuple] = {}
        self.shapes: list = []
        self.unsupported: list[str] = []

    # ── defs ──────────────────────────────────────────────────────────────
    def collect_defs(self, elem) -> None:
        """Index every ``<defs>`` entry in the tree.

        Indexed over the whole document before the drawing walk, not as the walk
        reaches each ``<defs>``: Matplotlib writes a marker's ``<defs>`` inside
        the first group that uses it, and later groups reference the same id, so
        an index built lazily would be complete only by luck of document order.
        """
        for node in elem.iter():
            if _tag(node) == "defs":
                for child in node:
                    self._index_def(child)

    def _index_def(self, child) -> None:
        tag = _tag(child)
        node_id = child.get("id")
        if tag == "path" and node_id:
            decls = _expand_font_shorthand(_declarations(child))
            self.defs_paths[node_id] = (
                parse_path_data(child.get("d"), self.unsupported), decls)
        elif tag == "clipPath" and node_id:
            rect = self._clip_rect(child)
            if rect is not None:
                self.clips[node_id] = rect

    def _clip_rect(self, clip_path) -> tuple | None:
        """The bounding rectangle of a ``<clipPath>``.

        Matplotlib only ever writes a rectangle here (the axes' data area). A
        clip path built from some other shape still yields *a* rectangle — its
        bounding box — which clips less than the real path would. That is
        recorded as unsupported rather than passed off as exact.
        """
        for child in clip_path:
            tag = _tag(child)
            if tag == "rect":
                x = _length(child.get("x"))
                y = _length(child.get("y"))
                return (x, y, x + _length(child.get("width")),
                        y + _length(child.get("height")))
            if tag == "path":
                bounds = subpath_bounds(
                    parse_path_data(child.get("d"), self.unsupported))
                if bounds is not None:
                    if "non-rectangular clip path" not in self.unsupported:
                        self.unsupported.append("non-rectangular clip path")
                    return bounds
        return None

    def _clip_for(self, elem, inherited_clip):
        ref = (elem.get("clip-path") or "").strip()
        match = re.match(r"url\(#([^)]+)\)", ref)
        if match is None:
            return inherited_clip
        rect = self.clips.get(match.group(1))
        if rect is None:
            return inherited_clip
        if inherited_clip is None:
            return rect
        return (max(rect[0], inherited_clip[0]), max(rect[1], inherited_clip[1]),
                min(rect[2], inherited_clip[2]), min(rect[3], inherited_clip[3]))

    # ── the walk proper ───────────────────────────────────────────────────
    def walk(self, elem, matrix, inherited: dict, clip, name: str) -> None:
        tag = _tag(elem)
        if tag in ("defs", "metadata", "title", "desc", "style"):
            return
        decls = _expand_font_shorthand(_declarations(elem))
        matrix = _mat_mul(matrix, _parse_transform(elem.get("transform")))
        clip = self._clip_for(elem, clip)
        name = elem.get("id") or name
        if tag in ("g", "svg", "a"):
            merged = {**inherited, **decls}
            for child in elem:
                self.walk(child, matrix, merged, clip, name)
            return
        merged = {**inherited, **decls}
        if tag == "path":
            self._emit_path(parse_path_data(elem.get("d"), self.unsupported),
                            merged, decls, matrix, clip, name)
        elif tag == "rect":
            self._emit_path(self._rect_subpaths(elem), merged, decls, matrix,
                            clip, name)
        elif tag == "use":
            self._emit_use(elem, merged, matrix, clip, name)
        elif tag == "text":
            self._emit_text(elem, merged, matrix, name)
        elif tag == "image":
            self._emit_image(elem, matrix, name)
        elif tag in ("line", "polyline", "polygon", "circle", "ellipse"):
            # Matplotlib never writes these, so meeting one means the SVG came
            # from somewhere else. Saying so beats dropping part of a figure.
            if tag not in self.unsupported:
                self.unsupported.append(f"<{tag}> element")

    @staticmethod
    def _rect_subpaths(elem) -> list[Subpath]:
        x = _length(elem.get("x"))
        y = _length(elem.get("y"))
        w = _length(elem.get("width"))
        h = _length(elem.get("height"))
        return [[("M", (x, y)), ("L", (x + w, y)), ("L", (x + w, y + h)),
                 ("L", (x, y + h)), ("Z",)]]

    def _emit_path(self, subpaths, merged: dict, decls: dict, matrix, clip,
                   name: str) -> None:
        if not subpaths:
            return
        fill = _paint_from(decls, "fill", merged)
        stroke = _stroke_from(decls, merged, _mean_scale(matrix))
        if fill is None and stroke is None:
            return
        moved = transform_subpaths(subpaths, matrix)
        if clip is not None:
            moved = clip_subpaths(moved, clip, filled=fill is not None)
            if not moved:
                return
        self.shapes.append(PathShape(subpaths=moved, fill=fill, stroke=stroke,
                                     name=name))

    def _emit_use(self, elem, merged: dict, matrix, clip, name: str) -> None:
        """Expand a ``<use>`` of a ``<defs>`` path into real geometry.

        The instance's own style wins over the definition's — that is how
        Matplotlib recolours one shared marker path per series.
        """
        ref = _href(elem)
        entry = self.defs_paths.get(ref[1:] if ref.startswith("#") else ref)
        if entry is None:
            if "unresolved <use> reference" not in self.unsupported:
                self.unsupported.append("unresolved <use> reference")
            return
        subpaths, def_decls = entry
        offset = (1.0, 0.0, 0.0, 1.0, _length(elem.get("x")), _length(elem.get("y")))
        own = _expand_font_shorthand(_declarations(elem))
        decls = {**def_decls, **own}
        self._emit_path(subpaths, {**merged, **decls}, decls,
                        _mat_mul(matrix, offset), clip, name)

    def _emit_text(self, elem, merged: dict, matrix, name: str) -> None:
        text = "".join(elem.itertext())
        if not text.strip():
            return
        anchor_pt = _apply(matrix, (_length(elem.get("x")), _length(elem.get("y"))))
        # The rotation SVG applies about the anchor, plus whatever rotation the
        # enclosing transform carries. Read off the matrix rather than only the
        # element's own ``rotate(...)``, so a figure placed by a rotated group
        # still lands the right way up.
        rotation = math.degrees(math.atan2(matrix[1], matrix[0]))
        size = _float_or(merged.get("font-size"), 10.0) * _mean_scale(matrix)
        weight = (merged.get("font-weight") or "normal").lower()
        self.shapes.append(TextShape(
            text=text,
            x=anchor_pt[0], y=anchor_pt[1],
            size_pt=size,
            family=_first_family(merged.get("font-family") or ""),
            weight="bold" if weight in ("bold", "bolder") or _is_bold_number(weight)
            else "normal",
            style=(merged.get("font-style") or "normal").lower(),
            anchor=(merged.get("text-anchor") or "start").lower(),
            rotation_deg=rotation,
            fill=_paint_from(_declarations(elem), "fill", merged),
            name=name,
        ))

    def _emit_image(self, elem, matrix, name: str) -> None:
        png = _decode_data_uri(_href(elem))
        if not png:
            if "external image reference" not in self.unsupported:
                self.unsupported.append("external image reference")
            return
        x = _length(elem.get("x"))
        y = _length(elem.get("y"))
        w = _length(elem.get("width"))
        h = _length(elem.get("height"))
        # ``walk`` has already folded this element's own ``transform`` into
        # *matrix*; re-applying it here would compose the flip twice and cancel
        # it, putting the raster block back the way it came off disk — upside
        # down, and silently, since the rectangle it lands in is the same size.
        local = matrix
        corners = [_apply(local, p) for p in
                   ((x, y), (x + w, y), (x + w, y + h), (x, y + h))]
        xs = [p[0] for p in corners]
        ys = [p[1] for p in corners]
        if abs(local[1]) > 1e-9 or abs(local[2]) > 1e-9:
            # A rotated or skewed raster block would need the picture rotated to
            # match; PowerPoint can do that, but Matplotlib does not produce one
            # and guessing the angle is how an image ends up subtly wrong.
            if "rotated raster block" not in self.unsupported:
                self.unsupported.append("rotated raster block")
        self.shapes.append(ImageShape(
            png=png, x=min(xs), y=min(ys),
            width=max(1e-6, max(xs) - min(xs)),
            height=max(1e-6, max(ys) - min(ys)),
            flip_h=local[0] < 0, flip_v=local[3] < 0, name=name,
        ))


def _is_bold_number(weight: str) -> bool:
    try:
        return int(weight) >= 600
    except (TypeError, ValueError):
        return False


def _decode_data_uri(href: str) -> bytes:
    """The bytes behind a ``data:...;base64,`` URI, or ``b""``."""
    if not href.startswith("data:"):
        return b""
    _, _, payload = href.partition(",")
    if ";base64" not in href.split(",", 1)[0]:
        return b""
    try:
        return base64.b64decode("".join(payload.split()))
    except (ValueError, TypeError):
        return b""


def parse_svg(svg_bytes: bytes) -> Drawing:
    """Read a Matplotlib SVG into a :class:`Drawing`.

    Raises :class:`SvgParseError` when the bytes are not parseable XML or carry
    no ``<svg>`` root — the two cases where there is nothing to draw at all, as
    distinct from a figure that drew partially and reports it in ``unsupported``.
    """
    try:
        root = ET.fromstring(svg_bytes)
    except ET.ParseError as exc:
        raise SvgParseError(f"not valid XML: {exc}") from exc
    if _tag(root) != "svg":
        raise SvgParseError(f"root element is <{_tag(root)}>, not <svg>")

    width = _length(root.get("width"))
    height = _length(root.get("height"))
    view_box = _numbers(root.get("viewBox") or "")
    matrix = _IDENTITY
    if len(view_box) == 4 and view_box[2] > 0 and view_box[3] > 0:
        # Matplotlib's viewBox matches its width/height one-to-one, but honouring
        # the mapping costs three lines and makes the reader correct for an SVG
        # where they differ instead of silently mis-scaling it.
        if width <= 0 or height <= 0:
            width, height = view_box[2], view_box[3]
        scale = (width / view_box[2], height / view_box[3])
        matrix = _mat_mul((scale[0], 0.0, 0.0, scale[1], 0.0, 0.0),
                          (1.0, 0.0, 0.0, 1.0, -view_box[0], -view_box[1]))
    if width <= 0 or height <= 0:
        raise SvgParseError("the SVG declares no usable width/height")

    walker = _Walker(root)
    walker.collect_defs(root)
    walker.walk(root, matrix, {}, None, "figure")
    return Drawing(width_pt=width, height_pt=height, shapes=walker.shapes,
                   unsupported=walker.unsupported)
