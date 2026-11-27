"""ModeBar — the app's top-level Review / Analyze / Datasets selector.

These three are not tabs of one screen. They are three different applications
sharing a window, and switching between them replaces the entire interface. The
control has to say so at rest, not only once a mode is active.

It is a pill (design language §3.1, window style W1): a rounded track in the
canvas colour holding the three, the current one in the selected look —
the selected fill and its ink — and the others in the secondary ink on the
track. The selected fill, not a solid accent: the accent is kept for the one
primary action on a page, and a mode switch drawn in it was a second loudest
thing on every screen. The bar's default height is measured off a ``QTabBar``
so it and the centre pane's tab bar share a bottom edge whatever the theme does
to tab padding. Its look is :func:`qss`, keyed on each segment's ``active``
property.

No All-Well coupling — a plain reusable widget.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QTabBar, QWidget

from ui import theme
from widgets import icons as _icons

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).

_ICON_PX = 14
#: The track round the segments, and its corners and theirs (§3.1: a 3-pixel track, radii 9 and 6).
_TRACK_INSET = 3
_TRACK_RADIUS = 9
_SEGMENT_RADIUS = 6


def tab_bar_height() -> int:
    """The height a themed ``QTabBar`` takes, measured rather than guessed.

    The mode bar has to line up with the centre pane's tab bar exactly — they
    share a bottom edge, and a pixel either way reads as a misalignment across
    the whole top of the window. Deriving the number from a throwaway tab bar
    keeps the two in step through any theme change; a literal here would go
    stale the first time the tab bar's padding moved.

    Deliberately not cached. The result depends on the application stylesheet
    and font, and a module-level cache freezes whichever of those happened to
    be in force at the first call — which, for a widget built during startup,
    is a stylesheet that has not been applied yet.
    """
    probe = QTabBar()
    probe.addTab("Ag")               # ascender + descender: the tallest case
    probe.ensurePolished()           # unpolished, a tab bar under-reports by 1px
    height = probe.sizeHint().height()
    probe.deleteLater()
    return height


class _ModeSegment(QWidget):
    """One mode. Filled whether or not it is the current one."""

    clicked = Signal()

    def __init__(self, text: str, icon: str | None, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setObjectName("ModeSegment")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self._text = text
        self._icon = icon
        self._active = False

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.Spacing.xs + 2)
        row.addStretch(1)

        self._icon_lbl = QLabel(self)
        self._icon_lbl.setVisible(bool(icon))
        row.addWidget(self._icon_lbl)

        self._text_lbl = QLabel(text, self)
        self._text_lbl.setObjectName("ModeSegmentText")
        row.addWidget(self._text_lbl)
        row.addStretch(1)

        self.setActive(False)

    def setActive(self, active: bool) -> None:
        self._active = bool(active)
        c = theme.Colors
        fg = c.selected_fg if self._active else c.text_secondary
        # The property on the label too, not a descendant selector: Qt does not reliably re-evaluate a rule keyed
        # on a parent's property when only the parent is repolished.
        for w in (self, self._text_lbl):
            w.setProperty("active", self._active)
            w.style().unpolish(w)
            w.style().polish(w)
        # The glyph is repainted in the segment's own ink rather than handed in as a fixed QIcon, whose Normal
        # pixmap is one colour in both states.
        if self._icon:
            self._icon_lbl.setPixmap(
                _icons.make_pixmap(self._icon, fg, _ICON_PX,
                                   self.devicePixelRatioF())
            )

    def isActive(self) -> bool:
        return self._active

    def setIconShown(self, shown: bool) -> None:
        """Show or drop the glyph; the label always stays."""
        self._icon_lbl.setVisible(bool(shown and self._icon))

    def text(self) -> str:
        return self._text

    def mousePressEvent(self, event) -> None:  # Qt naming
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ModeBar(QWidget):
    """Flush, equal-width segments across the full width of its host."""

    currentChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None, *, height: int | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ModeBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._row = QHBoxLayout(self)
        inset = _TRACK_INSET
        self._row.setContentsMargins(inset, inset, inset, inset)
        self._row.setSpacing(0)
        self._segments: list[_ModeSegment] = []
        self._data: list[object] = []
        self._current = -1
        self._fill = False
        # Default to the tab bar's height rather than to the segments' own
        # content height: a segment is a label and a glyph, so left to itself
        # the bar collapses to about 14px and sits as a thin strip beside a
        # full-height tab row.
        self.setFixedHeight(tab_bar_height() if height is None else height)

    def setFillHeight(self, fill: bool = True) -> None:  # Qt naming
        """Stretch to the host row's height instead of pinning one.

        The default is a pinned height matched to a tab bar, which is right when
        the bar sits beside one. When it sits *inside* a taller strip, a pinned
        height leaves a gap above and below and the bar reads as a floating chip
        row rather than one solid band down the left edge.
        """
        self._fill = bool(fill)
        if fill:
            self.setMinimumHeight(0)
            self.setMaximumHeight(16_777_215)   # Qt's QWIDGETSIZE_MAX
            self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        else:
            self.setFixedHeight(tab_bar_height())
            self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def addSegment(self, text: str, icon: str | None = None,
                   data: object | None = None) -> int:
        """Add a mode. *icon* is a ``widgets.icons`` glyph name, not a QIcon —
        the segment recolours it to match whichever state it is in."""
        idx = len(self._segments)
        seg = _ModeSegment(text, icon, self)
        seg.clicked.connect(lambda i=idx: self.setCurrentIndex(i))
        self._row.addWidget(seg, 1)
        self._segments.append(seg)
        self._data.append(data if data is not None else text)
        if self._current < 0:
            self.setCurrentIndex(0)
        return idx

    def setCurrentIndex(self, index: int) -> None:
        if not (0 <= index < len(self._segments)) or index == self._current:
            return
        self._current = index
        for i, seg in enumerate(self._segments):
            seg.setActive(i == index)
        self.currentChanged.emit(index)

    def currentIndex(self) -> int:
        return self._current

    def currentData(self) -> object | None:
        if 0 <= self._current < len(self._data):
            return self._data[self._current]
        return None

    def setCurrentData(self, value: object) -> None:
        if value in self._data:
            self.setCurrentIndex(self._data.index(value))

    def count(self) -> int:
        return len(self._segments)

    def setCompact(self, compact: bool, *, pad: int | None = None) -> None:
        """Labels only, no glyphs — for a band too narrow for both.

        The labels stay because they are the only thing that says what each
        segment is on a touch screen, where a tooltip never appears.
        """
        pad = (theme.Spacing.sm if pad is None else int(pad)) if compact else 0
        self._compact = compact
        for seg in self._segments:
            seg.setIconShown(not compact)
            # Flush, the segments are as wide as the bar shares out; at its
            # own width in a band, they need their own room around the label.
            seg.layout().setContentsMargins(pad, 0, pad, 0)
        self._equalise_widths()

    def _equalise_widths(self) -> None:
        """One width for all three segments while compact, so the active fill
        does not jump in size as the mode changes. Measured from the labels'
        current font, so it is re-run after every UI scale: the widest label
        measured before a restyle enlarged it would clip it afterwards."""
        for seg in self._segments:
            seg.setMinimumWidth(0)
        if getattr(self, "_compact", False):
            widest = max((s.sizeHint().width() for s in self._segments), default=0)
            for seg in self._segments:
                seg.setMinimumWidth(widest)
        self.updateGeometry()

    def _aw_rescale(self) -> None:
        """Re-measure the pinned height and the compact widths after a UI scale
        (segments rescale their own fonts)."""
        if not self._fill:
            self.setFixedHeight(tab_bar_height())
        self._equalise_widths()

    def segmentText(self, index: int) -> str:
        return self._segments[index].text()

    def isSegmentActive(self, index: int) -> bool:
        return self._segments[index].isActive()


def qss() -> str:
    """The pill: the track in the canvas colour, the current segment in the selected look."""
    c, t = theme.Colors, theme.Typography
    return f"""
    QWidget#ModeBar {{ background-color: {c.canvas}; border-radius: {_TRACK_RADIUS}px; }}
    QWidget#ModeSegment {{ background-color: transparent; border-radius: {_SEGMENT_RADIUS}px; }}
    QWidget#ModeSegment[active="true"] {{ background-color: {c.selected}; }}
    QWidget#ModeSegment QLabel {{ background: transparent; }}
    QLabel#ModeSegmentText {{
        background: transparent; color: {c.text_secondary}; font-size: {t.caption_size}px; font-weight: {t.semibold};
    }}
    QLabel#ModeSegmentText[active="true"] {{ color: {c.selected_fg}; }}
    """
