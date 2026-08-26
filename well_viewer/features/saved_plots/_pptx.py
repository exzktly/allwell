"""Write a ``.pptx`` file — the Open Packaging container, without a library.

A PowerPoint file is a zip of XML parts wired together by relationship files. The
app has no ``python-pptx`` dependency and this needs none: the parts a deck of
plain slides requires are small, fixed, and written here in full, so the export
adds nothing to the runtime stack it did not already have.

What this builds is deliberately minimal — one slide master, one blank layout,
one theme, and one part per slide. Everything a slide shows is an explicit shape
placed by :mod:`._drawingml`; no placeholder inheritance is involved, which is
what keeps the exported figures where the figure put them regardless of the
template a user later applies.

:class:`PptxBuilder` is the whole interface: make one, add slides to it as XML
shape bodies, and write it out.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from well_viewer.features.saved_plots._drawingml import ShapeIds, inches_to_emu

#: 16:9, the default new-deck size in every current PowerPoint. A 4:3 deck would
#: letterbox every figure the moment it was pasted into a modern template.
SLIDE_WIDTH_EMU = inches_to_emu(13 + 1 / 3)
SLIDE_HEIGHT_EMU = inches_to_emu(7.5)

_NS = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
       'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')

_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

_XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

#: The colour scheme the deck ships with. Nothing this exporter draws references
#: it — every figure shape carries an explicit sRGB colour — but a theme is a
#: required part and PowerPoint reads this one when the user adds their own
#: shapes to an exported slide.
_SCHEME_COLORS = (
    ("dk1", '<a:sysClr val="windowText" lastClr="000000"/>'),
    ("lt1", '<a:sysClr val="window" lastClr="FFFFFF"/>'),
    ("dk2", '<a:srgbClr val="44546A"/>'),
    ("lt2", '<a:srgbClr val="E7E6E6"/>'),
    ("accent1", '<a:srgbClr val="4472C4"/>'),
    ("accent2", '<a:srgbClr val="ED7D31"/>'),
    ("accent3", '<a:srgbClr val="A5A5A5"/>'),
    ("accent4", '<a:srgbClr val="FFC000"/>'),
    ("accent5", '<a:srgbClr val="5B9BD5"/>'),
    ("accent6", '<a:srgbClr val="70AD47"/>'),
    ("hlink", '<a:srgbClr val="0563C1"/>'),
    ("folHlink", '<a:srgbClr val="954F72"/>'),
)


def _empty_tree(name: str) -> str:
    """The shape tree a master or layout needs to be structurally valid."""
    return (
        "<p:spTree><p:nvGrpSpPr>"
        f'<p:cNvPr id="1" name="{name}"/><p:cNvGrpSpPr/><p:nvPr/>'
        "</p:nvGrpSpPr><p:grpSpPr><a:xfrm>"
        '<a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/>'
        "</a:xfrm></p:grpSpPr></p:spTree>"
    )


def _theme_xml() -> str:
    colors = "".join(f"<a:{k}>{v}</a:{k}>" for k, v in _SCHEME_COLORS)
    font = ('<a:latin typeface="Calibri"/><a:ea typeface=""/>'
            '<a:cs typeface=""/>')
    fill = ('<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
            '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
            '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>')
    line = ('<a:ln w="6350" cap="flat" cmpd="sng" algn="ctr">'
            '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
            '<a:prstDash val="solid"/></a:ln>')
    return (
        _XML_DECL
        + '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
        ' name="All-Well">'
        f"<a:themeElements><a:clrScheme name=\"All-Well\">{colors}</a:clrScheme>"
        '<a:fontScheme name="All-Well">'
        f"<a:majorFont>{font}</a:majorFont><a:minorFont>{font}</a:minorFont>"
        "</a:fontScheme>"
        '<a:fmtScheme name="All-Well">'
        f"<a:fillStyleLst>{fill}</a:fillStyleLst>"
        f"<a:lnStyleLst>{line * 3}</a:lnStyleLst>"
        "<a:effectStyleLst>"
        + "<a:effectStyle><a:effectLst/></a:effectStyle>" * 3
        + "</a:effectStyleLst>"
        f"<a:bgFillStyleLst>{fill}</a:bgFillStyleLst>"
        "</a:fmtScheme></a:themeElements>"
        "<a:objectDefaults/><a:extraClrSchemeLst/></a:theme>"
    )


def _slide_master_xml() -> str:
    mapping = ('<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2"'
               ' accent1="accent1" accent2="accent2" accent3="accent3"'
               ' accent4="accent4" accent5="accent5" accent6="accent6"'
               ' hlink="hlink" folHlink="folHlink"/>')
    return (
        _XML_DECL + f"<p:sldMaster {_NS}><p:cSld>"
        '<p:bg><p:bgPr><a:solidFill><a:schemeClr val="bg1"/></a:solidFill>'
        "<a:effectLst/></p:bgPr></p:bg>"
        + _empty_tree("Master") + "</p:cSld>" + mapping
        + '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/>'
        "</p:sldLayoutIdLst></p:sldMaster>"
    )


def _slide_layout_xml() -> str:
    return (
        _XML_DECL + f'<p:sldLayout {_NS} type="blank" preserve="1">'
        f'<p:cSld name="Blank">{_empty_tree("Layout")}</p:cSld>'
        "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>"
    )


def _slide_xml(body: str) -> str:
    return (
        _XML_DECL + f"<p:sld {_NS}><p:cSld><p:spTree>"
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/>'
        "</p:nvGrpSpPr><p:grpSpPr><a:xfrm>"
        '<a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/>'
        f"</a:xfrm></p:grpSpPr>{body}</p:spTree></p:cSld>"
        "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"
    )


def _rels(entries: list[tuple[str, str, str]]) -> str:
    """A ``.rels`` part from ``(id, type, target)`` triples."""
    body = "".join(
        f'<Relationship Id="{rid}" Type="{kind}" Target="{escape(target)}"/>'
        for rid, kind, target in entries)
    return (_XML_DECL + f'<Relationships xmlns="{_PKG_REL_NS}">{body}'
            "</Relationships>")


class Slide:
    """One slide under construction: its shapes, and the images they embed.

    Shape ids are per-slide, so each slide carries its own :class:`ShapeIds`;
    so are relationship ids, which is why a slide mints its own image
    relationships rather than the package handing them out.
    """

    def __init__(self) -> None:
        self.ids = ShapeIds()
        self.body: list[str] = []
        #: ``(relationship_id, png_bytes)`` for every raster block on this slide.
        self.images: list[tuple[str, bytes]] = []

    def add(self, shape_xml: str) -> None:
        if shape_xml:
            self.body.append(shape_xml)

    def add_image(self, png: bytes) -> str:
        """Register a PNG and return the relationship id the slide will use.

        Identical bytes are registered once: a deck of eight heat maps drawn
        from one colour bar would otherwise carry eight copies of it.
        """
        if not png:
            return ""
        for rel_id, existing in self.images:
            if existing == png:
                return rel_id
        # rId1 is the layout, so slide images start at rId2.
        rel_id = f"rId{len(self.images) + 2}"
        self.images.append((rel_id, png))
        return rel_id

    def xml(self) -> str:
        return _slide_xml("".join(self.body))


class PptxBuilder:
    """Collects slides and writes the finished ``.pptx``."""

    def __init__(self, *, title: str = "Saved Plots") -> None:
        self.title = title
        self.slides: list[Slide] = []

    def new_slide(self) -> Slide:
        slide = Slide()
        self.slides.append(slide)
        return slide

    # ── package assembly ──────────────────────────────────────────────────
    def _content_types(self) -> str:
        overrides = [
            ("/ppt/presentation.xml",
             "application/vnd.openxmlformats-officedocument.presentationml."
             "presentation.main+xml"),
            ("/ppt/slideMasters/slideMaster1.xml",
             "application/vnd.openxmlformats-officedocument.presentationml."
             "slideMaster+xml"),
            ("/ppt/slideLayouts/slideLayout1.xml",
             "application/vnd.openxmlformats-officedocument.presentationml."
             "slideLayout+xml"),
            ("/ppt/theme/theme1.xml",
             "application/vnd.openxmlformats-officedocument.theme+xml"),
            ("/docProps/core.xml",
             "application/vnd.openxmlformats-package.core-properties+xml"),
            ("/docProps/app.xml",
             "application/vnd.openxmlformats-officedocument."
             "extended-properties+xml"),
        ]
        overrides += [
            (f"/ppt/slides/slide{i}.xml",
             "application/vnd.openxmlformats-officedocument.presentationml."
             "slide+xml")
            for i in range(1, len(self.slides) + 1)
        ]
        body = (
            '<Default Extension="rels" ContentType="application/vnd.'
            'openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            + "".join(f'<Override PartName="{part}" ContentType="{ctype}"/>'
                      for part, ctype in overrides)
        )
        return (_XML_DECL + '<Types xmlns="http://schemas.openxmlformats.org/'
                f'package/2006/content-types">{body}</Types>')

    def _presentation_xml(self) -> str:
        slide_ids = "".join(
            f'<p:sldId id="{256 + i}" r:id="rId{i + 2}"/>'
            for i in range(len(self.slides)))
        return (
            _XML_DECL + f"<p:presentation {_NS}>"
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/>'
            "</p:sldMasterIdLst>"
            f"<p:sldIdLst>{slide_ids}</p:sldIdLst>"
            f'<p:sldSz cx="{SLIDE_WIDTH_EMU}" cy="{SLIDE_HEIGHT_EMU}"/>'
            '<p:notesSz cx="6858000" cy="9144000"/>'
            "</p:presentation>"
        )

    def _presentation_rels(self) -> str:
        entries = [("rId1", f"{_REL_NS}/slideMaster",
                    "slideMasters/slideMaster1.xml")]
        entries += [(f"rId{i + 2}", f"{_REL_NS}/slide", f"slides/slide{i + 1}.xml")
                    for i in range(len(self.slides))]
        entries.append((f"rId{len(self.slides) + 2}", f"{_REL_NS}/theme",
                        "theme/theme1.xml"))
        return _rels(entries)

    def _core_props(self) -> str:
        return (
            _XML_DECL
            + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/'
            'package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>{escape(self.title)}</dc:title>"
            "<dc:creator>All-Well</dc:creator>"
            "<cp:lastModifiedBy>All-Well</cp:lastModifiedBy>"
            "</cp:coreProperties>"
        )

    def _app_props(self) -> str:
        return (
            _XML_DECL
            + '<Properties xmlns="http://schemas.openxmlformats.org/'
            'officeDocument/2006/extended-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/'
            'docPropsVTypes">'
            "<Application>All-Well</Application>"
            f"<Slides>{len(self.slides)}</Slides>"
            "</Properties>"
        )

    def write(self, out_path: str | Path) -> int:
        """Write the deck. Returns the number of slides written."""
        path = Path(out_path)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Content types first: readers that stream the package expect to
            # meet the part-type map before the parts it describes.
            zf.writestr("[Content_Types].xml", self._content_types())
            zf.writestr("_rels/.rels", _rels([
                ("rId1", f"{_REL_NS}/officeDocument", "ppt/presentation.xml"),
                ("rId2", f"{_PKG_REL_NS}/metadata/core-properties",
                 "docProps/core.xml"),
                ("rId3", f"{_REL_NS}/extended-properties", "docProps/app.xml"),
            ]))
            zf.writestr("docProps/core.xml", self._core_props())
            zf.writestr("docProps/app.xml", self._app_props())
            zf.writestr("ppt/presentation.xml", self._presentation_xml())
            zf.writestr("ppt/_rels/presentation.xml.rels", self._presentation_rels())
            zf.writestr("ppt/theme/theme1.xml", _theme_xml())
            zf.writestr("ppt/slideMasters/slideMaster1.xml", _slide_master_xml())
            zf.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", _rels([
                ("rId1", f"{_REL_NS}/slideLayout", "../slideLayouts/slideLayout1.xml"),
                ("rId2", f"{_REL_NS}/theme", "../theme/theme1.xml"),
            ]))
            zf.writestr("ppt/slideLayouts/slideLayout1.xml", _slide_layout_xml())
            zf.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", _rels([
                ("rId1", f"{_REL_NS}/slideMaster",
                 "../slideMasters/slideMaster1.xml"),
            ]))
            self._write_slides(zf)
        return len(self.slides)

    def _write_slides(self, zf: zipfile.ZipFile) -> None:
        media_index = 0
        for number, slide in enumerate(self.slides, start=1):
            zf.writestr(f"ppt/slides/slide{number}.xml", slide.xml())
            entries = [("rId1", f"{_REL_NS}/slideLayout",
                        "../slideLayouts/slideLayout1.xml")]
            for rel_id, png in slide.images:
                media_index += 1
                name = f"image{media_index}.png"
                zf.writestr(f"ppt/media/{name}", png)
                entries.append((rel_id, f"{_REL_NS}/image", f"../media/{name}"))
            zf.writestr(f"ppt/slides/_rels/slide{number}.xml.rels", _rels(entries))
