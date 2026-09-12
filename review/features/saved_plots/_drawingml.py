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

from review.features.saved_plots._svg_shapes import (
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

#: DrawingML's dash presets, as ``(dash, gap)`` in multiples of the line width —
#: the same way SVG's ``stroke-dasharray`` is conventionally written. A parsed
#: pattern is matched to the nearest of these. Writing every pattern as ``dash``
#: (which is what this did) collapses dashed, dotted and dash-dot into three
#: identical curves and three identical legend keys, so a figure that encodes its
#: series in line style stops being readable — silently, since each line is
#: individually plausible.
_DASH_PRESETS = (
    ("sysDot", (1.0, 1.0)),
    ("dot", (1.0, 3.0)),
    ("sysDash", (3.0, 1.0)),
    ("dash", (4.0, 3.0)),
    ("lgDash", (8.0, 3.0)),
)

#: Patterns of four or more values alternate two dash lengths, which is what
#: dash-dot is. Matched by length before the two-value presets are consulted.
_DASH_DOT_PRESETS = (
    ("dashDot", 4),
    ("lgDashDot", 4),
    ("lgDashDotDot", 6),
)


def dash_preset(dashes: tuple, width_pt: float) -> str:
    """The closest DrawingML dash preset to a parsed ``stroke-dasharray``.

    Matched in units of the line width, because that is how both SVG's presets
    and DrawingML's are defined; a 1 pt dotted line and a 4 pt dotted line have
    very different absolute patterns and the same shape.
    """
    if not dashes:
        return ""
    unit = width_pt if width_pt > 0 else 1.0
    scaled = [d / unit for d in dashes]
    if len(scaled) >= 4:
        return "lgDashDotDot" if len(scaled) >= 6 else "dashDot"
    if len(scaled) == 1:
        scaled = [scaled[0], scaled[0]]
    dash, gap = scaled[0], scaled[1]
    best = min(_DASH_PRESETS,
               key=lambda item: abs(item[1][0] - dash) + abs(item[1][1] - gap))
    return best[0]


#: Characters XML 1.0 forbids outright. ``escape`` does not remove them — it
#: only rewrites ``&``, ``<`` and ``>`` — so a title carrying one produces a
#: slide part that will not parse. Plot titles are free text the user types or
#: pastes, and a paste out of Word carries ``\x0b`` for a soft line break, so
#: this is an ordinary input rather than a hostile one.
_ILLEGAL_XML = frozenset(
    chr(c) for c in [*range(0x00, 0x09), 0x0B, 0x0C, *range(0x0E, 0x20),
                     *range(0xD800, 0xE000), 0xFFFE, 0xFFFF])


def xml_text(value: str) -> str:
    """*value*, escaped and stripped of characters XML cannot carry.

    Dropping them rather than substituting a glyph: they are invisible control codes
    with no printable meaning, and putting a placeholder in a plot title would
    change what the slide says. A tab, newline or carriage return is legal and
    survives.

    Without this a single stray control character does not corrupt one label —
    it makes the whole slide part unparseable, so PowerPoint offers to repair
    the file and the exporter reports success either way.
    """
    return escape("".join(c for c in (value or "") if c not in _ILLEGAL_XML))


def xml_attr(value: str) -> str:
    """The same, quoted for use as an XML attribute value."""
    return quoteattr("".join(c for c in (value or "") if c not in _ILLEGAL_XML))


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

    Two ways to build one. :meth:`fit` scales a figure to fill a target box
    without distorting it and centres what is left over — the same letterboxing
    rule the PDF report's image axis follows, so the two exports frame a figure
    alike. :meth:`anchor` scales the figure's *plotting area* into the box
    instead and lets the labels around it fall where they fall, which is what
    puts every slide's axes in one place.
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

    @classmethod
    def anchor(cls, axes_rect_pt, *, x: int, y: int, cx: int, cy: int) -> Placement:
        """Place a figure by its **plotting area** rather than by its page.

        *axes_rect_pt* is ``(x0, y0, x1, y1)`` in the drawing's own points. It is
        scaled — without distortion — to fill the box, and centred in whichever
        direction it does not fill; everything else in the figure follows it and
        lands outside the box. Handed the same box for every slide, this is what
        puts two figures' axes in the same place and at the same size, whatever
        the crop around each of them measured.
        """
        x0, y0, x1, y1 = axes_rect_pt
        width = points_to_emu(x1 - x0) or 1
        height = points_to_emu(y1 - y0) or 1
        scale = min(cx / width, cy / height)
        used_w = width * scale
        used_h = height * scale
        origin_x = x + (cx - used_w) / 2.0 - x0 * EMU_PER_POINT * scale
        origin_y = y + (cy - used_h) / 2.0 - y0 * EMU_PER_POINT * scale
        return cls(origin_x=int(round(origin_x)), origin_y=int(round(origin_y)),
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
    preset = dash_preset(stroke.dashes, stroke.width_pt)
    dash = f'<a:prstDash val="{preset}"/>' if preset else ""
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
    # The frame has to cover both geometries: a clipped bar's outline can end
    # short of its fill, and sizing the frame from the fill alone would squeeze
    # the outline into it.
    bounds = subpath_bounds(shape.every_subpath()) or (0.0, 0.0, 0.0, 0.0)
    x = placement.emu_x(bounds[0])
    y = placement.emu_y(bounds[1])
    cx = max(_MIN_EXTENT_EMU, placement.emu_x(bounds[2]) - x)
    cy = max(_MIN_EXTENT_EMU, placement.emu_y(bounds[3]) - y)

    def _pt(point) -> str:
        return (f'<a:pt x="{placement.emu_x(point[0]) - x}" '
                f'y="{placement.emu_y(point[1]) - y}"/>')

    def _emit(subpaths, *, fill_mode: str, stroke_flag: str) -> list[str]:
        out = []
        for sub in subpaths:
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
                out.append(f'<a:path w="{cx}" h="{cy}" fill="{fill_mode}" '
                           f'stroke="{stroke_flag}">' + "".join(body)
                           + "</a:path>")
        return out

    fill_mode = "norm" if shape.fill is not None else "none"
    split = shape.stroke_subpaths is not None
    # When a clip split the two, the filled geometry carries no outline and the
    # stroked geometry carries no fill — so the bar is filled to where the axes
    # end and outlined only where the figure actually drew an edge.
    paths = _emit(shape.subpaths, fill_mode=fill_mode,
                  stroke_flag="0" if split or shape.stroke is None else "1")
    if split:
        paths += _emit(shape.stroke_subpaths, fill_mode="none", stroke_flag="1")
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
        f'<p:cNvPr id="{ids.take()}" name={xml_attr(shape.name or "path")}/>'
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
    family = xml_attr(shape.family or "Arial")
    return (
        "<p:sp><p:nvSpPr>"
        f'<p:cNvPr id="{ids.take()}" name={xml_attr(shape.name or "text")}/>'
        '<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr>'
        f'<a:xfrm{rotation}><a:off x="{x}" y="{y}"/>'
        f'<a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>'
        '<p:txBody><a:bodyPr wrap="none" lIns="0" tIns="0" rIns="0" bIns="0"'
        ' anchor="t"/><a:lstStyle/>'
        f'<a:p><a:pPr algn="{align}"/><a:r>'
        f'<a:rPr lang="en-US" sz="{size}"{bold}{italic} dirty="0">{fill}'
        f"<a:latin typeface={family}/><a:cs typeface={family}/></a:rPr>"
        f"<a:t>{xml_text(shape.text)}</a:t></a:r></a:p></p:txBody></p:sp>"
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
        f'<p:cNvPr id="{ids.take()}" name={xml_attr(shape.name or "image")}/>'
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
    return group_xml(
        "".join(body), ids,
        x=placement.origin_x, y=placement.origin_y,
        cx=placement.emu_len(drawing.width_pt),
        cy=placement.emu_len(drawing.height_pt), name=name)


def text_box_xml(text: str, ids: ShapeIds, *, x: int, y: int, cx: int, cy: int,
                 color: str, size_pt: float = 18.0, bold: bool = False,
                 align: str = "l", family: str = "Arial", name: str = "Text",
                 monospace: bool = False, anchor: str = "t",
                 plate: str = "", plate_alpha: int = 55_000,
                 vertical: bool = False) -> str:
    """A plain editable text box — slide titles, captions, the cover block.

    Every line of *text* becomes its own paragraph, so a multi-line caption stays
    multi-line and stays editable line by line.

    *anchor* is where the text sits vertically in its box (``t``/``ctr``/``b``),
    *plate* an optional solid fill behind it at *plate_alpha* (the translucent
    backing an overlay label needs to stay readable over a bright field), and
    *vertical* makes the run read bottom-to-top for a rotated facet strip. The
    three exist for the Image Table's per-field frames, where a label is placed
    over a picture rather than on the slide background.
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
                f"<a:latin typeface={xml_attr(face)}/></a:rPr>"
                f"<a:t>{xml_text(line)}</a:t></a:r></a:p>")
        else:
            runs.append(f'<a:p><a:pPr algn="{align}"/><a:endParaRPr '
                        f'lang="en-US" sz="{size}"/></a:p>')
    if plate:
        alpha = max(0, min(100_000, int(plate_alpha)))
        fill = (f'<a:solidFill><a:srgbClr val="{plate}">'
                f'<a:alpha val="{alpha}"/></a:srgbClr></a:solidFill>')
    else:
        fill = "<a:noFill/>"
    vert = ' vert="vert270"' if vertical else ""
    return (
        "<p:sp><p:nvSpPr>"
        f'<p:cNvPr id="{ids.take()}" name={xml_attr(name)}/>'
        '<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr>'
        f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>{fill}</p:spPr>'
        '<p:txBody><a:bodyPr wrap="square" lIns="0" tIns="0" rIns="0" bIns="0"'
        f'{vert} anchor="{anchor}"><a:normAutofit/></a:bodyPr><a:lstStyle/>'
        + "".join(runs) + "</p:txBody></p:sp>"
    )


def filled_rect_xml(ids: ShapeIds, *, x: int, y: int, cx: int, cy: int,
                    color: str, outline: str = "", outline_w: int = 0,
                    name: str = "Rectangle") -> str:
    """A plain filled rectangle — the scale bar, and its contrasting outline.

    A shape rather than part of the picture, so the bar keeps its length when the
    frame is resized and can be recoloured in PowerPoint like anything else on
    the slide.
    """
    line = ""
    if outline:
        line = (f'<a:ln w="{max(_MIN_EXTENT_EMU, int(outline_w))}">'
                f'<a:solidFill><a:srgbClr val="{outline}"/></a:solidFill>'
                "</a:ln>")
    return (
        "<p:sp><p:nvSpPr>"
        f'<p:cNvPr id="{ids.take()}" name={xml_attr(name)}/>'
        "<p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr>"
        f'<a:xfrm><a:off x="{x}" y="{y}"/>'
        f'<a:ext cx="{max(_MIN_EXTENT_EMU, int(cx))}" '
        f'cy="{max(_MIN_EXTENT_EMU, int(cy))}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>{line}'
        "</p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>"
    )


def picture_xml(ids: ShapeIds, rel_id: str, *, x: int, y: int, cx: int, cy: int,
                name: str = "Picture") -> str:
    """An embedded PNG placed at an explicit box."""
    return (
        "<p:pic><p:nvPicPr>"
        f'<p:cNvPr id="{ids.take()}" name={xml_attr(name)}/>'
        "<p:cNvPicPr/><p:nvPr/></p:nvPicPr>"
        f'<p:blipFill><a:blip r:embed="{rel_id}"/>'
        "<a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr>"
        f'<a:xfrm><a:off x="{x}" y="{y}"/>'
        f'<a:ext cx="{max(_MIN_EXTENT_EMU, int(cx))}" '
        f'cy="{max(_MIN_EXTENT_EMU, int(cy))}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
    )


def group_xml(body: str, ids: ShapeIds, *, x: int, y: int, cx: int, cy: int,
              name: str = "Group") -> str:
    """Wrap already-placed shapes in one group.

    The child coordinate space is set equal to the group's own, so *body* is
    written in slide coordinates and a reader comparing the two sees the same
    numbers rather than a second frame of reference to unpick — the same choice
    :func:`drawing_group_xml` makes.
    """
    cx = max(_MIN_EXTENT_EMU, int(cx))
    cy = max(_MIN_EXTENT_EMU, int(cy))
    return (
        "<p:grpSp><p:nvGrpSpPr>"
        f'<p:cNvPr id="{ids.take()}" name={xml_attr(name)}/>'
        "<p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr>"
        f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/>'
        f'<a:chOff x="{x}" y="{y}"/><a:chExt cx="{cx}" cy="{cy}"/></a:xfrm>'
        "</p:grpSpPr>" + (body or "") + "</p:grpSp>"
    )
