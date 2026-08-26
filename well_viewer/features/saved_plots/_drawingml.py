"""Turn parsed SVG shapes into PowerPoint DrawingML.

The back half of the PowerPoint export. Every shape :mod:`._svg_shapes` produced
becomes one real PowerPoint object: a filled or stroked ``<p:sp>`` carrying a
custom geometry, a text box holding a live text run, or a ``<p:pic>`` wrapping an
embedded PNG. Nothing is flattened into a picture of a chart — clicking a bar in
the exported deck selects that bar, and its fill is a fill PowerPoint can change.

The whole figure goes into one ``<p:grpSp>``, so it moves and scales as a unit
while a double-click still reaches the individual shapes inside it.

**Units.** SVG user space in a Matplotlib figure is points; PowerPoint measures in
EMU (914,400 per inch, 12,700 per point) and font sizes in hundredths of a point.
This module is the only place that knows that, which is why the shape model above
it can stay in the units the figure was drawn in.
"""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from well_viewer.features.saved_plots._svg_shapes import (
    Drawing, ImageShape, Paint, PathShape, Stroke, TextShape, subpath_bounds,
)

#: English Metric Units per point, and per inch. The two constants the whole
#: geometry conversion rests on.
EMU_PER_POINT = 12700
EMU_PER_INCH = 914400

#: A shape narrower or shorter than this collapses to nothing in PowerPoint and
#: is dropped from the selection pane. Axis spines and error-bar whiskers are
#: exactly this case — zero-width rectangles — so every extent is floored here.
_MIN_EXTENT_EMU = 1

#: Where a text run's baseline sits inside the line box PowerPoint lays out for
#: it, as a fraction of the font size, and how tall that line box is. Both are
#: approximations: the SVG gives a baseline and PowerPoint positions a frame, and
#: no font metrics are available to bridge them exactly. They are accurate to
#: well under a point at the sizes plot labels use, and a label a user wants
#: moved is a label they can now drag.
_BASELINE_FRACTION = 0.85
_LINE_BOX_FRACTION = 1.20

#: Rough mean advance width per character, as a fraction of the font size. Only
#: sizes the text frame — the run's alignment does the positioning, so a frame
#: that is too wide or too narrow still places the text on its anchor.
_ADVANCE_FRACTION = 0.62

#: SVG ``text-anchor`` → the DrawingML paragraph alignment that puts the run's
#: anchor point in the same place.
_ALIGNMENT = {"start": "l", "middle": "ctr", "end": "r"}

#: SVG ``stroke-linejoin`` / ``stroke-linecap`` → their DrawingML spellings.
_JOIN = {"miter": '<a:miter lim="800000"/>', "round": "<a:round/>",
         "bevel": "<a:bevel/>"}
_CAP = {"butt": "flat", "round": "rnd", "square": "sq"}


def points_to_emu(value: float) -> int:
    return int(round(value * EMU_PER_POINT))


def inches_to_emu(value: float) -> int:
    return int(round(value * EMU_PER_INCH))


class ShapeIds:
    """Hands out the per-slide shape ids DrawingML requires to be unique.

    Ids start at 2 because the slide's own root group takes 1.
    """

    def __init__(self, start: int = 2) -> None:
        self._next = start

    def take(self) -> int:
        value = self._next
        self._next += 1
        return value


class Placement:
    """Where on the slide a drawing goes, and how much it is scaled to get there.

    Built by :meth:`fit`, which scales a figure to fill a target box without
    distorting it and centres what is left over — the same letterboxing rule the
    PDF report's image axis follows, so the two exports frame a figure alike.
    """

    def __init__(self, origin_x: int, origin_y: int, scale: float) -> None:
        self.origin_x = origin_x
        self.origin_y = origin_y
        self.scale = scale

    @classmethod
    def fit(cls, drawing: Drawing, *, x: int, y: int, cx: int, cy: int) -> Placement:
        width = points_to_emu(drawing.width_pt) or 1
        height = points_to_emu(drawing.height_pt) or 1
        scale = min(cx / width, cy / height)
        used_w = width * scale
        used_h = height * scale
        return cls(origin_x=int(round(x + (cx - used_w) / 2.0)),
                   origin_y=int(round(y + (cy - used_h) / 2.0)),
                   scale=scale)

    def emu_x(self, point_x: float) -> int:
        return int(round(self.origin_x + point_x * EMU_PER_POINT * self.scale))

    def emu_y(self, point_y: float) -> int:
        return int(round(self.origin_y + point_y * EMU_PER_POINT * self.scale))

    def emu_len(self, length_pt: float) -> int:
        return int(round(length_pt * EMU_PER_POINT * self.scale))

    def size_pt(self, size_pt: float) -> float:
        return size_pt * self.scale


# ── paint ────────────────────────────────────────────────────────────────────

def _color_xml(paint: Paint) -> str:
    if paint.alpha >= 0.999:
        return f'<a:srgbClr val="{paint.rgb}"/>'
    alpha = max(0, min(100000, int(round(paint.alpha * 100000))))
    return f'<a:srgbClr val="{paint.rgb}"><a:alpha val="{alpha}"/></a:srgbClr>'


def _fill_xml(paint: Paint | None) -> str:
    if paint is None:
        return "<a:noFill/>"
    return f"<a:solidFill>{_color_xml(paint)}</a:solidFill>"


def _line_xml(stroke: Stroke | None, placement: Placement) -> str:
    if stroke is None:
        return "<a:ln><a:noFill/></a:ln>"
    width = max(0, placement.emu_len(stroke.width_pt))
    cap = _CAP.get(stroke.linecap, "flat")
    dash = '<a:prstDash val="dash"/>' if stroke.dashes else ""
    join = _JOIN.get(stroke.linejoin, "<a:round/>")
    return (f'<a:ln w="{width}" cap="{cap}">'
            f"<a:solidFill>{_color_xml(stroke.paint)}</a:solidFill>"
            f"{dash}{join}</a:ln>")


# ── geometry ─────────────────────────────────────────────────────────────────

def _custom_geometry(shape: PathShape, placement: Placement) -> tuple[str, tuple]:
    """The ``<a:custGeom>`` for a path, plus the ``(x, y, cx, cy)`` frame it
    lives in.

    The frame is the path's own bounding box, and the path coordinates are
    written relative to it in the same EMU scale — so the geometry is 1:1 with
    the frame and resizing the shape in PowerPoint scales it the way the user
    expects.
    """
    bounds = subpath_bounds(shape.subpaths) or (0.0, 0.0, 0.0, 0.0)
    x = placement.emu_x(bounds[0])
    y = placement.emu_y(bounds[1])
    cx = max(_MIN_EXTENT_EMU, placement.emu_x(bounds[2]) - x)
    cy = max(_MIN_EXTENT_EMU, placement.emu_y(bounds[3]) - y)

    def _pt(point) -> str:
        return (f'<a:pt x="{placement.emu_x(point[0]) - x}" '
                f'y="{placement.emu_y(point[1]) - y}"/>')

    fill_mode = "norm" if shape.fill is not None else "none"
    stroke_flag = "1" if shape.stroke is not None else "0"
    paths = []
    for sub in shape.subpaths:
        body = []
        for seg in sub:
            if seg[0] == "M":
                body.append(f"<a:moveTo>{_pt(seg[1])}</a:moveTo>")
            elif seg[0] == "L":
                body.append(f"<a:lnTo>{_pt(seg[1])}</a:lnTo>")
            elif seg[0] == "C":
                body.append("<a:cubicBezTo>"
                            + "".join(_pt(p) for p in seg[1:])
                            + "</a:cubicBezTo>")
            elif seg[0] == "Z":
                body.append("<a:close/>")
        if body:
            paths.append(f'<a:path w="{cx}" h="{cy}" fill="{fill_mode}" '
                         f'stroke="{stroke_flag}">' + "".join(body) + "</a:path>")
    geometry = ("<a:custGeom><a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>"
                f'<a:rect l="0" t="0" r="{cx}" b="{cy}"/>'
                "<a:pathLst>" + "".join(paths) + "</a:pathLst></a:custGeom>")
    return geometry, (x, y, cx, cy)


def _path_shape_xml(shape: PathShape, placement: Placement, ids: ShapeIds) -> str:
    geometry, (x, y, cx, cy) = _custom_geometry(shape, placement)
    if not shape.subpaths:
        return ""
    return (
        "<p:sp><p:nvSpPr>"
        f'<p:cNvPr id="{ids.take()}" name={quoteattr(shape.name or "path")}/>'
        "<p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr>"
        f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        f"{geometry}{_fill_xml(shape.fill)}{_line_xml(shape.stroke, placement)}"
        "</p:spPr>"
        "<p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>"
    )


# ── text ─────────────────────────────────────────────────────────────────────

def _text_frame(shape: TextShape, placement: Placement) -> tuple:
    """``(x, y, cx, cy, rot)`` for a text run's frame, in EMU and 60000ths of a
    degree.

    SVG rotates a label about its anchor point; PowerPoint rotates a shape about
    the centre of its own frame. The two agree once the frame's centre is itself
    rotated about the anchor by the same angle, which is what this does — so a
    rotated y-axis label lands exactly where the figure drew it rather than a
    frame-width away from it.
    """
    import math

    size = placement.size_pt(shape.size_pt)
    width_pt = max(size, _ADVANCE_FRACTION * size * max(1, len(shape.text)))
    height_pt = _LINE_BOX_FRACTION * size

    # The frame in unrotated space, as an offset from the anchor point.
    align = _ALIGNMENT.get(shape.anchor, "l")
    if align == "ctr":
        left_pt = -width_pt / 2.0
    elif align == "r":
        left_pt = -width_pt
    else:
        left_pt = 0.0
    top_pt = -_BASELINE_FRACTION * size

    centre_dx = left_pt + width_pt / 2.0
    centre_dy = top_pt + height_pt / 2.0
    radians = math.radians(shape.rotation_deg)
    cos, sin = math.cos(radians), math.sin(radians)
    rotated_dx = centre_dx * cos - centre_dy * sin
    rotated_dy = centre_dx * sin + centre_dy * cos

    cx = max(_MIN_EXTENT_EMU, points_to_emu(width_pt))
    cy = max(_MIN_EXTENT_EMU, points_to_emu(height_pt))
    centre_x = placement.emu_x(shape.x) + points_to_emu(rotated_dx)
    centre_y = placement.emu_y(shape.y) + points_to_emu(rotated_dy)
    rot = int(round(shape.rotation_deg * 60000)) % 21600000
    return (centre_x - cx // 2, centre_y - cy // 2, cx, cy, rot)


def _text_shape_xml(shape: TextShape, placement: Placement, ids: ShapeIds) -> str:
    x, y, cx, cy, rot = _text_frame(shape, placement)
    size = max(100, int(round(placement.size_pt(shape.size_pt) * 100)))
    align = _ALIGNMENT.get(shape.anchor, "l")
    bold = ' b="1"' if shape.weight == "bold" else ""
    italic = ' i="1"' if shape.style in ("italic", "oblique") else ""
    fill = _fill_xml(shape.fill) if shape.fill is not None else _fill_xml(
        Paint(rgb="000000"))
    rotation = f' rot="{rot}"' if rot else ""
    family = quoteattr(shape.family or "Arial")
    return (
        "<p:sp><p:nvSpPr>"
        f'<p:cNvPr id="{ids.take()}" name={quoteattr(shape.name or "text")}/>'
        '<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr>'
        f'<a:xfrm{rotation}><a:off x="{x}" y="{y}"/>'
        f'<a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>'
        '<p:txBody><a:bodyPr wrap="none" lIns="0" tIns="0" rIns="0" bIns="0"'
        ' anchor="t"/><a:lstStyle/>'
        f'<a:p><a:pPr algn="{align}"/><a:r>'
        f'<a:rPr lang="en-US" sz="{size}"{bold}{italic} dirty="0">{fill}'
        f"<a:latin typeface={family}/><a:cs typeface={family}/></a:rPr>"
        f"<a:t>{escape(shape.text)}</a:t></a:r></a:p></p:txBody></p:sp>"
    )


# ── raster blocks ────────────────────────────────────────────────────────────

def _image_shape_xml(shape: ImageShape, placement: Placement, ids: ShapeIds,
                     rel_id: str) -> str:
    x = placement.emu_x(shape.x)
    y = placement.emu_y(shape.y)
    cx = max(_MIN_EXTENT_EMU, placement.emu_len(shape.width))
    cy = max(_MIN_EXTENT_EMU, placement.emu_len(shape.height))
    flips = ""
    if shape.flip_h:
        flips += ' flipH="1"'
    if shape.flip_v:
        flips += ' flipV="1"'
    return (
        "<p:pic><p:nvPicPr>"
        f'<p:cNvPr id="{ids.take()}" name={quoteattr(shape.name or "image")}/>'
        "<p:cNvPicPr/><p:nvPr/></p:nvPicPr>"
        f'<p:blipFill><a:blip r:embed="{rel_id}"/>'
        "<a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr>"
        f'<a:xfrm{flips}><a:off x="{x}" y="{y}"/>'
        f'<a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
    )


# ── assembly ─────────────────────────────────────────────────────────────────

def drawing_group_xml(drawing: Drawing, placement: Placement, ids: ShapeIds,
                      add_image, *, name: str = "Figure") -> str:
    """Every shape in *drawing*, wrapped in one group.

    *add_image* is called with the PNG bytes of each raster block and must
    return the relationship id the slide will carry for it — the slide owns its
    relationships, so it is the slide builder that mints them.

    The group's child coordinate space is set equal to its own, so children are
    written in slide coordinates and a reader comparing the two sees the same
    numbers rather than a second frame of reference to unpick.
    """
    body = []
    for shape in drawing.shapes:
        if isinstance(shape, PathShape):
            body.append(_path_shape_xml(shape, placement, ids))
        elif isinstance(shape, TextShape):
            body.append(_text_shape_xml(shape, placement, ids))
        elif isinstance(shape, ImageShape):
            rel_id = add_image(shape.png)
            if rel_id:
                body.append(_image_shape_xml(shape, placement, ids, rel_id))
    x = placement.origin_x
    y = placement.origin_y
    cx = max(_MIN_EXTENT_EMU, placement.emu_len(drawing.width_pt))
    cy = max(_MIN_EXTENT_EMU, placement.emu_len(drawing.height_pt))
    return (
        "<p:grpSp><p:nvGrpSpPr>"
        f'<p:cNvPr id="{ids.take()}" name={quoteattr(name)}/>'
        "<p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr>"
        f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/>'
        f'<a:chOff x="{x}" y="{y}"/><a:chExt cx="{cx}" cy="{cy}"/></a:xfrm>'
        "</p:grpSpPr>" + "".join(body) + "</p:grpSp>"
    )


def text_box_xml(text: str, ids: ShapeIds, *, x: int, y: int, cx: int, cy: int,
                 size_pt: float = 18.0, bold: bool = False,
                 color: str = "1A1A1A", align: str = "l",
                 family: str = "Arial", name: str = "Text",
                 monospace: bool = False) -> str:
    """A plain editable text box — slide titles, captions, the cover block.

    Every line of *text* becomes its own paragraph, so a multi-line caption stays
    multi-line and stays editable line by line.
    """
    face = "Consolas" if monospace else family
    size = max(100, int(round(size_pt * 100)))
    weight = ' b="1"' if bold else ""
    runs = []
    for line in (text or "").split("\n"):
        if line:
            runs.append(
                f'<a:p><a:pPr algn="{align}"/><a:r>'
                f'<a:rPr lang="en-US" sz="{size}"{weight} dirty="0">'
                f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
                f"<a:latin typeface={quoteattr(face)}/></a:rPr>"
                f"<a:t>{escape(line)}</a:t></a:r></a:p>")
        else:
            runs.append(f'<a:p><a:pPr algn="{align}"/><a:endParaRPr '
                        f'lang="en-US" sz="{size}"/></a:p>')
    return (
        "<p:sp><p:nvSpPr>"
        f'<p:cNvPr id="{ids.take()}" name={quoteattr(name)}/>'
        '<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr>'
        f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>'
        '<p:txBody><a:bodyPr wrap="square" lIns="0" tIns="0" rIns="0" bIns="0"'
        ' anchor="t"><a:normAutofit/></a:bodyPr><a:lstStyle/>'
        + "".join(runs) + "</p:txBody></p:sp>"
    )
