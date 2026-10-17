"""Phone layout: the shell half — the controls, the window, the restyle.

``core/phone_layout.py`` holds the flag and the Qt-free rules; this module is
the Qt half that the main window (``allwell.AllWellApp``) owns. It imports no
pane. Everything it touches inside one is handed to it as a
:class:`PhoneHandles` built by the window, which is the one place that knows
where those widgets live — so a pane that moves a widget breaks one line in
``allwell.py`` rather than a reach somewhere in here.

What phone layout does, and where:

* **Window fits its screen** (:meth:`PhoneShell.fit_window`) — in *both*
  layouts. RealVNC resizes the Windows desktop to the phone's shape; a window
  whose minimum is wider than that desktop hangs off its edge, taking every
  control there with it, including the one that would fix it.
* **One column.** The band's ☰ slides the pane's whole left panel (Review's
  sections and well plate, the Analyze form) out over the page from the left
  edge — ``review.core.phone_drawer`` lends the panels to that drawer and
  gives them back.
* **Chrome that suits a thumb**: the header row goes, the mode bar drops its
  glyphs, the page sub-tab strip becomes one "Bar Plots ▾" menu, desk-only
  and destructive buttons go, the keyboard hints go; text size and the way
  back to the desktop layout sit behind the band's "Aa" button.
* **Larger text** (:meth:`PhoneShell.set_text_size`): the size tokens are
  scaled and the stylesheet re-applied — about 6 s on a loaded app, under an
  overlay that swallows the taps made meanwhile — then one explicit pass
  rebuilds what froze a size at construction (:func:`rescale_widgets`), and
  every plot's DPI follows (:func:`scale_canvases`).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, QObject, QRect, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractSpinBox, QApplication, QComboBox, QDialog, QLabel,
    QLineEdit, QMenu, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from core import phone_layout
from core.safe import swallowed
from ui import theme

_LOG = logging.getLogger(__name__)

#: The smallest window phone layout allows: a phone held upright, after the
#: remote viewer's own chrome, with room for the band and one row of content.
PHONE_MIN_W, PHONE_MIN_H = 320, 400

#: Qt's QWIDGETSIZE_MAX — what lifts a size pin.
_QWIDGETSIZE_MAX = 16_777_215

#: How long the restyle overlay outlives the restyle. Taps made during the
#: freeze are queued and delivered once the event loop resumes; the overlay
#: must still be there to receive them, or a second tap on the ☰ menu's
#: "Desktop layout" lands on whatever is underneath.
_OVERLAY_LINGER_MS = 400


@dataclass
class PhoneHandles:
    """Where the widgets phone layout rearranges live. Built by the window.

    Every field is optional so a trimmed build (a profile without one of the
    panes) still gets a working phone layout for what it has.
    """

    header: QWidget | None = None
    band: QWidget | None = None
    mode_bar: QWidget | None = None
    #: Inserted before the mode bar / after it, in the band's layout.
    band_insert_at: int = 0
    #: Shown on the desktop, hidden in phone layout.
    desk_only: list[QWidget] = field(default_factory=list)
    #: The page sub-tab strip host (hidden in phone layout, mirrored by a menu).
    page_tabs_host: QWidget | None = None
    current_page_bar: Callable[[], object] | None = None
    mode: Callable[[], str] = lambda: "review"
    navigator_open: Callable[[], bool] = lambda: False
    set_navigator: Callable[[bool], None] = lambda _open: None
    #: Re-run every mode-dependent sync after a layout change.
    relayout: Callable[[], None] = lambda: None
    dashboards: Callable[[], list] = lambda: []
    #: The ⋯ menu's actions (each optional).
    reload: Callable[[], None] | None = None
    show_help: Callable[[], None] | None = None
    show_log: Callable[[], None] | None = None
    #: The log button's "!" badge, mirrored onto ⋯ while the status bar is gone.
    log_badge: QWidget | None = None
    #: Roots whose page control bands phone layout moves into the Controls
    #: sheet (Review's pages, the Analyze output page). Datasets keeps its own:
    #: its tools put their Run buttons there.
    band_roots: Callable[[], list] = lambda: []
    #: Review's section list, laid out as a grid in the side rail.
    rail_nav: QWidget | None = None
    #: Sections the phone does not offer (desk tools).
    phone_hidden_sections: tuple = ()
    #: The side rail's drawer, sized here to the screen.
    rail_drawer: QWidget | None = None
    #: Analyze's output page: its body layout (turned to two columns in
    #: landscape), the rule between dashboard and log, and the dashboard.
    analyze_body: object | None = None
    analyze_rule: QWidget | None = None
    #: Logs that wrap in phone layout (a sideways-scrolled log on a phone
    #: hides the end of every error line, which is the part that matters).
    logs: Callable[[], list] = lambda: []
    #: The status line's text ("4 well(s) | 72 nuclei"), shown at the top of
    #: the side rail since the status bar is gone on a phone.
    status_text: Callable[[], str] | None = None


# ── the rescale pass ──────────────────────────────────────────────────────
#: A font size inside a stylesheet. Every one in the app's sources is written
#: from ``theme.Typography`` (``tests/test_font_sizes_come_from_tokens.py``),
#: which is what makes rescaling it exact: a sheet was written at whatever
#: scale was in force when its widget wrote it.
_FONT_SIZE_RE = re.compile(r"(font-size\s*:\s*)(\d+(?:\.\d+)?)(px|pt)")

#: Widgets whose fixed width or height is there to fit their text, and so
#: should grow with it. Containers are absent on purpose: a fixed-width rail or
#: dock is a layout decision, and phone layout lifts those widths itself. A
#: composite control that holds text declares ``_aw_fits_text = True``.
_TEXT_CLASSES = (QLabel, QLineEdit, QAbstractSpinBox, QComboBox, QAbstractButton)

# Dynamic properties, not Python attributes: a PySide wrapper can be collected
# and rebuilt for a live C++ widget, and an attribute stashed on the old
# wrapper goes with it (ARCHITECTURE §7, the FlowLayout watcher).
_P_SHEET_BASE, _P_SHEET_WRITTEN = "_awSheetBase", "_awSheetWritten"
_P_FIXED_BASE, _P_FIXED_WRITTEN = "_awFixedBase", "_awFixedWritten"
_P_FONT_BASE, _P_FONT_WRITTEN = "_awFontBase", "_awFontWritten"


def scale_sheet(sheet: str, ratio: float, *, step: float = 1.0) -> str:
    """*sheet* with every font size multiplied by *ratio*, rounded to *step*.

    Scaling up rounds to whole pixels, as a token would. Scaling *back* — to
    recover the size a widget's own sheet was written at — rounds to half a
    pixel, which is the finest step the app's tokens use: ``11 × 1.75`` was
    written as 19, and ``19 ÷ 1.75`` is 10.86, which must come back as 11,
    not stay as a 10.9 the desk never had.
    """
    def _one(m):
        v = round(float(m.group(2)) * ratio / step) * step
        txt = str(int(v)) if v == int(v) else f"{v:g}"
        return f"{m.group(1)}{txt}{m.group(3)}"
    return _FONT_SIZE_RE.sub(_one, sheet)


def _owned(w: QWidget) -> bool:
    """Is *w* inside a widget that sizes its own subtree (``_aw_owns_sizes``)?"""
    p = w.parentWidget()
    while p is not None:
        if getattr(type(p), "_aw_owns_sizes", False):
            return True
        p = p.parentWidget()
    return False


def _rescale_sheet(w: QWidget, prev: float, new: float) -> None:
    sheet = w.styleSheet()
    if not sheet or "font-size" not in sheet:
        return
    base = w.property(_P_SHEET_BASE)
    if base is None or sheet != w.property(_P_SHEET_WRITTEN):
        # Not what this pass last wrote: the widget (re)wrote it itself, from
        # the tokens in force at the time — the previous scale.
        base = scale_sheet(sheet, 1.0 / prev, step=0.5)
    out = scale_sheet(base, new)
    if out != sheet:
        w.setStyleSheet(out)
    w.setProperty(_P_SHEET_BASE, None if new == 1.0 else base)
    w.setProperty(_P_SHEET_WRITTEN, None if new == 1.0 else out)


def _fits_text(w: QWidget) -> bool:
    return isinstance(w, _TEXT_CLASSES) or getattr(type(w), "_aw_fits_text", False)


def _rescale_axis(w: QWidget, axis: str, prev: float, new: float) -> None:
    """Scale one fixed dimension of *w* from the size it had at the desk."""
    lo, hi = ((w.minimumWidth(), w.maximumWidth()) if axis == "w"
              else (w.minimumHeight(), w.maximumHeight()))
    cur = lo if lo == hi and lo > 0 else 0
    p_base, p_written = _P_FIXED_BASE + axis, _P_FIXED_WRITTEN + axis
    base, written = w.property(p_base), w.property(p_written)
    if base is None or written != cur:
        if prev != 1.0 or not cur:
            # Not fixed, or fixed by code while a scale was in force — literal
            # (unscaled) or measured off the scaled font, no way to tell which,
            # so it is left as its code made it rather than guessed at.
            w.setProperty(p_base, None)
            w.setProperty(p_written, None)
            return
        base = cur
    out = round(base * new)
    (w.setFixedWidth if axis == "w" else w.setFixedHeight)(out)
    w.setProperty(p_base, None if new == 1.0 else base)
    w.setProperty(p_written, None if new == 1.0 else out)


def _rescale_fixed(w: QWidget, prev: float, new: float) -> None:
    if not _fits_text(w) or _owned(w):
        return
    _rescale_axis(w, "w", prev, new)
    _rescale_axis(w, "h", prev, new)


def explicit_fonts() -> dict:
    """Every widget's font that its own code set, captured *before* a restyle.

    ``QApplication.setStyleSheet`` re-polishes every widget, and the re-polish
    replaces a font a widget set on itself with the stylesheet's — a heat map
    legend set to 7 pt comes back at the body size. So the fonts are read
    first and put back afterwards (scaled) by :func:`rescale_widgets`.
    """
    out = {}
    for w in QApplication.allWidgets():
        try:
            if w.testAttribute(Qt.WA_SetFont):
                out[w] = QFont(w.font())
        except RuntimeError:
            swallowed()  # deleted C++ widget still listed by allWidgets
    return out


def _font_size(f: QFont) -> tuple[str, float]:
    return ("pt", f.pointSizeF()) if f.pointSizeF() > 0 else ("px", float(f.pixelSize()))


def _rescale_font(w: QWidget, font: QFont, prev: float, new: float) -> None:
    """Put back the font *w* set on itself, at scale *new*."""
    size = _font_size(font)
    base, written = w.property(_P_FONT_BASE), w.property(_P_FONT_WRITTEN)
    if base is None or tuple(written or ()) != size:
        # Set by the widget's code: at the desk that is its desk size; under a
        # scale it is a literal size or one measured off a token — taken as
        # the literal, the commoner case, so it is not shrunk on the way out.
        base = size
    kind, value = base
    f = QFont(font)
    if kind == "pt":
        f.setPointSizeF(value * new)
    else:
        f.setPixelSize(max(1, round(value * new)))
    w.setFont(f)
    w.setProperty(_P_FONT_BASE, None if new == 1.0 else list(base))
    w.setProperty(_P_FONT_WRITTEN, None if new == 1.0 else list(_font_size(f)))


def rescale_widgets(prev: float, new: float, widgets=None, fonts=None) -> int:
    """Bring every live widget from UI scale *prev* to *new*. Returns how many
    widgets defined their own ``_aw_rescale``.

    The application stylesheet is the caller's (``theme.qss()``); this is for
    what a widget froze at construction:

    * font sizes in its own stylesheet, rescaled in place — so a sheet that
      encodes state (a mode segment's active fill, a headline's tone) keeps
      it, which a rebuilt-from-scratch sheet would not;
    * a fixed width or height on a widget that holds text, scaled from the
      size it had at the desk;
    * a font the widget set on itself (*fonts*, from :func:`explicit_fonts`
      taken before the restyle, which wiped them);
    * and last, ``_aw_rescale()`` for widgets with sizes of their own to
      re-derive — an icon's glyph, a dashboard's stage columns.

    One explicit pass rather than an event filter per widget: a filter that
    re-sets a sheet on ``StyleChange`` re-fires ``StyleChange`` (why PlotCard
    reverted ``install_qss_refresh``).
    """
    snapshot = list(widgets if widgets is not None else QApplication.allWidgets())
    owners = []
    for w in snapshot:
        try:
            _rescale_sheet(w, prev, new)
            _rescale_fixed(w, prev, new)
            if callable(getattr(type(w), "_aw_rescale", None)):
                owners.append(w)
        except RuntimeError:
            swallowed()  # deleted C++ widget still listed by allWidgets
    for w, font in (fonts or {}).items():
        try:
            _rescale_font(w, font, prev, new)
        except RuntimeError:
            swallowed()  # deleted since the fonts were read
    # Let the font and style changes the restyle queued land first: an owner
    # re-measures off its own font, which is stale until they have. Input is
    # held back, so a tap made meanwhile still lands on the busy overlay.
    from PySide6.QtCore import QEventLoop
    QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
    for w in owners:
        try:
            w._aw_rescale()
        except RuntimeError:
            swallowed()  # deleted while the pass ran
    return len(owners)


# ── plots ─────────────────────────────────────────────────────────────────
def scale_canvas(canvas, factor: float) -> None:
    """Draw *canvas*'s figure as if the screen's DPI were *factor* times larger.

    One lever for everything matplotlib sizes in points — tick labels, titles,
    line widths, markers — which is all of a plot's text: the renderers fix it
    at 8–9 pt (``review/core/plot_style.py``). The figure keeps its pixel size;
    its size in inches shrinks instead, so the same pixels carry bigger type.
    ``_original_dpi`` is set too because it is what matplotlib's Qt canvas
    multiplies by the device pixel ratio when the window changes screen —
    without it, rotating the phone (a resolution change) drops the scale.
    """
    fig = getattr(canvas, "figure", None)
    if fig is None:
        return
    base = getattr(fig, "_aw_dpi_base", None)
    if base is None:
        base = getattr(fig, "_original_dpi", fig.dpi)
        fig._aw_dpi_base = base
    target = base * factor
    if getattr(fig, "_original_dpi", None) == target and getattr(fig, "_aw_dpi_factor", 1.0) == factor:
        return
    fig._original_dpi = target
    fig._aw_dpi_factor = factor
    ratio = getattr(canvas, "device_pixel_ratio", 1.0) or 1.0
    fig.set_dpi(target * ratio)
    from PySide6.QtGui import QResizeEvent
    size = canvas.size()
    # The canvas re-derives the figure's inches from its pixel size on a
    # resize; sending one with the current size is how to ask it to.
    QApplication.sendEvent(canvas, QResizeEvent(size, size))
    canvas.draw_idle()


def _canvases():
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    return [w for w in QApplication.allWidgets() if isinstance(w, FigureCanvasQTAgg)]


def scale_canvases(factor: float) -> int:
    """Scale every live plot canvas. Returns how many."""
    canvases = _canvases()
    for c in canvases:
        try:
            scale_canvas(c, factor)
        except RuntimeError:
            swallowed()  # canvas deleted while the list was being walked
    return len(canvases)


def _is_phone_controls(w: QWidget) -> bool:
    """A page's block of controls that is not a ``ControlBand`` but goes to the
    Controls sheet like one: marked with the ``phoneControls`` property, and
    carrying ``setStacked`` for its one-column form."""
    return bool(w.property("phoneControls")) and hasattr(w, "setStacked")


class _PhoneEventFilter(QObject):
    """While phone layout is on, the things built or shown later get it too:
    a plot its DPI, a dialog a place on the screen, a page's control band a
    place in the Controls sheet, a stepper its − / +, a toolbar its absence, a
    form row its stacking; and an ⓘ answers a tap, since nothing hovers."""

    def __init__(self, shell: PhoneShell) -> None:
        super().__init__(shell)
        self._shell = shell

    def eventFilter(self, obj, event) -> bool:  # Qt naming
        t = event.type()
        if t == QEvent.MouseButtonRelease and isinstance(obj, QLabel) \
                and obj.objectName() == "InfoHint" and obj.toolTip():
            from PySide6.QtWidgets import QToolTip
            QToolTip.showText(obj.mapToGlobal(obj.rect().bottomLeft()), obj.toolTip(), obj)
            return True
        if t != QEvent.Show or not isinstance(obj, QWidget):
            return False
        if obj.isWindow() and isinstance(obj, QDialog):
            clamp_to_screen(obj)
        elif getattr(type(obj), "draw_idle", None) is not None and hasattr(obj, "figure"):
            scale_canvas(obj, self._shell.plot_factor())
            self._shell.size_canvas(obj, True)
        else:
            self._shell.adapt(obj)
        return False


def fitted_rect(rect: QRect, avail: QRect) -> QRect:
    """*rect* moved — and shrunk only if it must be — to lie inside *avail*."""
    w = min(rect.width(), avail.width())
    h = min(rect.height(), avail.height())
    x = min(max(rect.x(), avail.x()), avail.x() + avail.width() - w)
    y = min(max(rect.y(), avail.y()), avail.y() + avail.height() - h)
    return QRect(x, y, w, h)


def clamp_to_screen(window: QWidget) -> None:
    """Bring a top-level window inside its screen's available area."""
    screen = window.screen() or QGuiApplication.primaryScreen()
    if screen is None:
        return
    avail = screen.availableGeometry()
    if window.minimumWidth() > avail.width() or window.minimumHeight() > avail.height():
        window.setMinimumSize(min(window.minimumWidth(), avail.width()),
                              min(window.minimumHeight(), avail.height()))
    frame = window.frameGeometry()
    fitted = fitted_rect(frame, avail)
    if fitted != frame:
        # frameGeometry includes the title bar; the client rect is inset by it.
        dx, dy = window.x() - frame.x(), window.y() - frame.y()
        window.setGeometry(fitted.x() + dx, fitted.y() + dy,
                           fitted.width() - (frame.width() - window.width()),
                           fitted.height() - (frame.height() - window.height()))


class _BusyOverlay(QWidget):
    """A sheet over the whole window that says what is happening and eats taps."""

    def __init__(self, parent: QWidget, text: str) -> None:
        super().__init__(parent)
        self.setObjectName("PhoneBusyOverlay")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"#PhoneBusyOverlay {{ background-color: {theme.Colors.surface}; }}"
            f"#PhoneBusyOverlay QLabel {{ color: {theme.Colors.text_primary};"
            f" font-size: {theme.Typography.h2_size}px; }}")
        lay = QVBoxLayout(self)
        label = QLabel(text, self)
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        lay.addWidget(label)
        self.setGeometry(parent.rect())

    def event(self, ev) -> bool:  # Qt naming
        if ev.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease,
                         QEvent.MouseButtonDblClick, QEvent.TouchBegin,
                         QEvent.TouchEnd, QEvent.Wheel):
            ev.accept()
            return True
        return super().event(ev)


#: Mode-dependent sizes of the phone's top bar, in phone points: a full bar
#: upright, a slimmer one sideways, where height is what the plots lack.
LANDSCAPE_BAR_PT = 44
#: The side rail leaves this much of the page showing to its right when the
#: phone is upright — the strip a thumb taps to close it; sideways it is a
#: fixed width, since half a landscape screen of sections would be mostly air.
RAIL_GAP_PT = 56
LANDSCAPE_PANEL_PT = 360
#: The Controls panel sideways: as wide as the phone held upright, so whatever a
#: page's controls fit into upright they fit into here. At 360 pt the Image
#: Table's LUT rows held it 45 px wider than itself.
LANDSCAPE_SHEET_PT = phone_layout.PHONE_REFERENCE_WIDTH_PT
#: The Controls sheet's share of an upright screen: the plot above it stays in
#: view and redraws as its controls change.
SHEET_HEIGHT_SHARE = 0.6
#: Analyze's summary column, sideways, and the run plate's height in it.
#: Sideways the screen is always 390 pt tall (the unit is its short side), so
#: the plate's share is fixed: 390 less the 44 pt bar, less about 165 pt for
#: the ETA, headline, progress bar, three stage rows and the tapped-well line
#: at the largest text size. 200 pt measured row H cut off at text size M.
LANDSCAPE_SUMMARY_PT = 300
LANDSCAPE_PLATE_PT = 160
#: How often the side rail's status line follows the status bar while out.
_RAIL_STATUS_MS = 400
#: The least height a plot gets per row of axes in phone layout.
PLOT_ROW_PT = 220


class PhoneShell(QObject):
    """Owns phone layout's controls and drives the switch both ways."""

    def __init__(self, window: QWidget, handles: PhoneHandles, *,
                 settings: Callable[[], object] | None = None) -> None:
        super().__init__(window)
        self._win = window
        self._h = handles
        self._settings = settings
        self._filter = _PhoneEventFilter(self)
        self._desk_state: dict | None = None
        self._text_size = phone_layout.DEFAULT_TEXT_SIZE
        self._applying = False
        self._banner = None
        self._banner_dismissed_for: bool | None = None
        self._screen = None
        #: Widgets this layout hid that were showing, so leaving shows exactly
        #: those again (a band a page had hidden for its own reasons stays so).
        self._hid: list[QWidget] = []
        self._sheet = None
        self._sheet_band = None
        self._landscape: bool | None = None
        self._build_controls()
        self._load_settings()

    # ── settings ──────────────────────────────────────────────────────────
    def _store(self):
        return self._settings() if self._settings is not None else None

    def _load_settings(self) -> None:
        s = self._store()
        if s is None:
            return
        try:
            self._text_size = phone_layout.parse_text_size(
                s.value(phone_layout.KEY_TEXT_SIZE, phone_layout.DEFAULT_TEXT_SIZE))
        except Exception:
            swallowed()

    def _save(self, key: str, value) -> None:
        s = self._store()
        if s is None:
            return
        try:
            s.setValue(key, value)
        except Exception:
            swallowed()

    def saved_preference(self) -> bool:
        s = self._store()
        if s is None:
            return False
        try:
            return phone_layout.parse_bool(s.value(phone_layout.KEY_ON, False))
        except Exception:
            swallowed()
            return False

    # ── sizes ─────────────────────────────────────────────────────────────
    def unit(self) -> float:
        """Logical px per phone point on this window's screen."""
        screen = self._win.screen() or QGuiApplication.primaryScreen()
        g = screen.geometry() if screen is not None else QRect(0, 0, 1640, 980)
        return phone_layout.phone_unit(g.width(), g.height())

    def pt(self, points: float) -> int:
        return phone_layout.touch_px(self.unit(), points)

    def is_landscape(self) -> bool:
        a = self._avail()
        return a.width() > a.height()

    def text_size(self) -> str:
        """The stored choice: ``"S"``, ``"M"`` or ``"L"``."""
        return self._text_size

    def text_scale(self) -> float:
        """The factor the type tokens take on this screen at the chosen size."""
        return phone_layout.type_scale(self.unit(), self._text_size,
                                       theme.desk_body_size())

    def plot_factor(self) -> float:
        """How much plots raise their DPI in phone layout: enough that a
        matplotlib point is a phone point (``phone_layout.plot_dpi_factor``)."""
        return phone_layout.plot_dpi_factor(self.unit()) if phone_layout.is_on() else 1.0

    def set_text_size(self, size: str) -> None:
        self._text_size = phone_layout.parse_text_size(size)
        self._save(phone_layout.KEY_TEXT_SIZE, self._text_size)
        if phone_layout.is_on():
            self._restyle(self.text_scale(), self.unit())
            scale_canvases(self.plot_factor())
            self._apply_sizes()
            self.refresh()

    # ── controls ──────────────────────────────────────────────────────────
    def _build_controls(self) -> None:
        from widgets.icon_button import IconButton

        # Desktop: one of the small icon buttons at the band's right end, just
        # before help, in every mode (allwell mounts it). The right end is in
        # reach on a narrow screen because the window always fits its screen
        # (fit_window), and a phone-shaped screen also gets the banner, whose
        # Switch says in words what this glyph does.
        # Keeps IconButton's own object name: the shared icon-button style is
        # keyed on it, and a name of its own drew it as a filled tile among the
        # flat icons beside it.
        self.desk_button = IconButton("smartphone")
        self.desk_button.setToolTip(
            "Phone layout: touch-sized controls, larger text and "
            "plots, for using All-Well from a phone over remote desktop.")
        self.desk_button.clicked.connect(lambda: self.set_on(True))

        # ☰ slides the pane's whole left panel — Review's sections and well
        # plate, the Analyze form — out over the page from the left; ✕ while
        # it is out.
        self.menu_button = QToolButton()
        self.menu_button.setObjectName("PhoneMenuButton")
        self.menu_button.setText("☰")
        self.menu_button.setCheckable(True)
        self.menu_button.clicked.connect(self._on_menu_clicked)

        self.page_button = QToolButton()
        self.page_button.setObjectName("PhonePageButton")
        self.page_button.setPopupMode(QToolButton.InstantPopup)
        self._page_menu = QMenu(self.page_button)
        self._page_menu.aboutToShow.connect(self._fill_page_menu)
        self.page_button.setMenu(self._page_menu)

        # The page's own controls (channel, property, fold change, display,
        # threshold, …) live in a sheet behind this, so the plot gets the screen.
        self.controls_button = QToolButton()
        self.controls_button.setObjectName("PhoneControlsButton")
        self.controls_button.setText("⚙ Controls")
        self.controls_button.clicked.connect(self.toggle_controls)

        self._band_spacer = QWidget()
        self._band_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        # Upright, the bar has room for ☰, the three modes and ⋯ and no more,
        # so Review's page menu and Controls take a strip of their own under
        # it; sideways they join the bar (``_place_page_controls``).
        from PySide6.QtWidgets import QHBoxLayout
        self._strip = QWidget()
        self._strip.setObjectName("PhoneStrip")
        self._strip.setAttribute(Qt.WA_StyledBackground, True)
        QHBoxLayout(self._strip).addStretch(1)

        # ⋯: text size, reload, help, the app log, and the way back to the
        # desk — one flat menu, no submenus.
        self.options_button = QToolButton()
        self.options_button.setObjectName("PhoneOptionsButton")
        self.options_button.setText("⋯")
        self.options_button.setPopupMode(QToolButton.InstantPopup)
        self._menu = QMenu(self.options_button)
        self._menu.aboutToShow.connect(self._fill_menu)
        self.options_button.setMenu(self._menu)
        from widgets.notice_badge import NoticeBadge
        self._options_badge = NoticeBadge(self.options_button)

        band = self._h.band
        layout = band.layout() if band is not None else None
        if layout is not None:
            at = self._h.band_insert_at
            layout.insertWidget(at, self.menu_button)
            bar_at = layout.indexOf(self._h.mode_bar) if self._h.mode_bar else at
            layout.insertWidget(bar_at + 1, self._band_spacer)
            layout.addWidget(self.options_button)
            host = band.parentWidget()
            host_lay = host.layout() if host is not None else None
            if host_lay is not None:
                host_lay.insertWidget(host_lay.indexOf(band) + 1, self._strip)
        self._place_page_controls(landscape=False)
        changed = getattr(self._h.log_badge, "changed", None)
        if changed is not None:
            changed.connect(self._sync_log_badge)
        # The status line ("4 well(s) | 72 nuclei") at the top of the side
        # rail: the status bar is gone on a phone, and the rail is where the
        # wells it counts are picked — so it follows them while the rail is out.
        self._rail_status = QLabel()
        self._rail_status.setObjectName("PhoneRailStatus")
        self._rail_status.hide()
        # A page's controls can appear after the page change that refresh()
        # follows: a lazily built tab (Image Browser) builds its body only once
        # it is shown, so at the notebook's currentChanged it has no controls
        # yet and ⚙ Controls stayed hidden. adapt() sees the controls arrive and
        # asks for one more refresh, coalesced to the next turn of the loop.
        self._refresh_soon = QTimer(self)
        self._refresh_soon.setSingleShot(True)
        self._refresh_soon.setInterval(0)
        self._refresh_soon.timeout.connect(self._refresh_when_settled)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(_RAIL_STATUS_MS)
        self._status_timer.timeout.connect(self._sync_rail_status)
        drawer = self._h.rail_drawer
        holder = drawer.contentWidget() if drawer is not None else None
        if holder is not None and holder.layout() is not None:
            holder.layout().insertWidget(0, self._rail_status)
            drawer.opened.connect(self._on_rail_opened)
            drawer.closed.connect(self._status_timer.stop)
        for w in self._phone_only():
            w.hide()

    def _on_rail_opened(self) -> None:
        self._sync_rail_status()
        self._status_timer.start()

    def _sync_rail_status(self) -> None:
        fn = self._h.status_text
        text = fn() if fn is not None else ""
        show = phone_layout.is_on() and self._h.mode() == "review" and bool(text)
        self._rail_status.setText(text)
        self._rail_status.setVisible(show)

    def _phone_only(self) -> list[QWidget]:
        return [self.menu_button, self.page_button, self.controls_button,
                self._band_spacer, self.options_button, self._strip]

    def _place_page_controls(self, *, landscape: bool) -> None:
        """The page menu and Controls: in the strip upright, in the bar sideways."""
        strip_lay = self._strip.layout()
        band = self._h.band
        band_lay = band.layout() if band is not None else None
        if landscape and band_lay is not None:
            at = band_lay.indexOf(self._band_spacer)
            band_lay.insertWidget(at, self.page_button, 0, Qt.AlignVCenter)
            band_lay.insertWidget(at + 1, self.controls_button, 0, Qt.AlignVCenter)
        else:
            strip_lay.insertWidget(0, self.page_button)
            strip_lay.addWidget(self.controls_button)

    def _sync_log_badge(self) -> None:
        """Mirror the log button's "!" onto ⋯, where the App log item is: the
        button lives in the status bar, which phone layout hides. Driven by
        the badge's ``changed`` signal — its Show and Hide events never
        arrive while the status bar is hidden, which is the whole time."""
        badge = self._h.log_badge
        tone = badge.tone() if (phone_layout.is_on() and badge is not None) else None
        if tone:
            self._options_badge.notify(tone)
        else:
            self._options_badge.clear()

    # ── the switch ────────────────────────────────────────────────────────
    def is_on(self) -> bool:
        return phone_layout.is_on()

    def set_on(self, on: bool, *, restyle: bool = True) -> None:
        """Enter or leave phone layout. *restyle* False skips the restyle
        (startup has already built everything at the right size)."""
        on = bool(on)
        if on == phone_layout.is_on() or self._applying:
            return
        self._save(phone_layout.KEY_ON, on)
        self._hide_banner()
        if not on:
            self.close_controls(now=True)
        if on:
            self._remember_desk_window()
        phone_layout.set_on(on)
        self._apply_chrome(on)
        if on:
            self._h.set_navigator(False)
        else:
            self._restore_navigator()
        # Steppers and form rows take their phone form *after* the restyle has
        # scaled their desk sizes, and give it up *before* the restyle scales
        # them back — so the rescale pass only ever sees desk-shaped widgets.
        if not on:
            self._adapt_all(False)
        if restyle:
            self._restyle(self.text_scale() if on else 1.0, self.unit() if on else None)
        elif on:
            theme.set_touch_unit(self.unit())
        if on:
            self._adapt_all(True)
        self._apply_sizes()
        # After the restyle: the mode bar takes its natural width, which is
        # only known once its labels are at their new size.
        self._h.relayout()
        scale_canvases(self.plot_factor())
        app = QApplication.instance()
        if app is not None:
            if on:
                app.installEventFilter(self._filter)
            else:
                app.removeEventFilter(self._filter)
        self._apply_window(on)
        self._sync_log_badge()
        self.refresh()

    def _hide(self, w: QWidget | None) -> None:
        if w is not None and not w.isHidden():
            w.hide()
            self._hid.append(w)

    def _apply_chrome(self, on: bool) -> None:
        if on:
            self._hide(self._h.header)
            # The way back is ⋯ › Desktop layout; this one only goes in.
            self._hide(self.desk_button)
            for w in self._h.desk_only:
                self._hide(w)
            self._hide(self._h.page_tabs_host)
        else:
            for w in self._hid:
                try:
                    w.show()
                except RuntimeError:
                    swallowed()  # gone with a page torn down meanwhile
            self._hid = []
        bar = self._h.mode_bar
        if bar is not None and hasattr(bar, "setCompact"):
            bar.setCompact(on, pad=self.pt(12))
        from PySide6.QtWidgets import QTextEdit
        for log in self._h.logs():
            # Wrapped, a log trimmed at its block cap re-anchors a scrolled-up
            # reader slightly off (the trim fix-up counts a block as one line;
            # ARCHITECTURE §6.1). Reading the end of an error line on a phone
            # is worth that.
            log.setLineWrapMode(QTextEdit.WidgetWidth if on else QTextEdit.NoWrap)
        self._options_badge.setInset(self.pt(2) if on else 0)
        for w in self._phone_only():
            w.setVisible(on)
        for d in self._h.dashboards():
            d.setStacked(on)
        nav = self._h.rail_nav
        if nav is not None:
            for key in self._h.phone_hidden_sections:
                row = getattr(nav, "_rows", {}).get(key)
                if row is not None:
                    if on:
                        self._hide(row)
            nav.setColumns(2 if on else 1)

    # ── adapting individual widgets ───────────────────────────────────────
    def _band_managed(self, band: QWidget) -> bool:
        roots = [r for r in self._h.band_roots() if r is not None]
        p = band.parentWidget()
        while p is not None:
            if any(p is r for r in roots):
                return True
            p = p.parentWidget()
        return False

    def adapt(self, w: QWidget) -> None:
        """Give one widget its phone (or desk) form — on entering and leaving
        phone layout for those that exist, and on Show for those built later."""
        on = phone_layout.is_on()
        name = type(w).__name__
        if name == "ControlBand" or _is_phone_controls(w):
            if on and w is not self._sheet_band and self._band_managed(w):
                self._hide(w)
                self._refresh_soon.start()
        elif name == "MplToolbar":
            # Its pan and zoom are modes a mouse drag over VNC fights with;
            # the plot is for reading on a phone, and Reset is in Controls.
            if on:
                self._hide(w)
        elif name == "Stepper" and hasattr(w, "setTouchMode"):
            if w.isTouchMode() != on:
                w.setTouchMode(on)
        elif w.objectName() == "AnalyzeFormRow":
            self._stack_form_row(w, on)

    def _adapt_all(self, on: bool) -> None:
        for w in list(QApplication.allWidgets()):
            try:
                self.adapt(w)
            except RuntimeError:
                swallowed()  # deleted while the list was walked
        self._grab_scrolling(on)

    def _stack_form_row(self, row: QWidget, on: bool) -> None:
        """A label above its field on a phone, beside it at the desk."""
        from PySide6.QtWidgets import QBoxLayout
        lay = row.layout()
        if not isinstance(lay, QBoxLayout) or lay.count() == 0:
            return
        label = lay.itemAt(0).widget()
        want = QBoxLayout.TopToBottom if on else QBoxLayout.LeftToRight
        if lay.direction() == want:
            return
        lay.setDirection(want)
        if label is None:
            return
        if on:
            row.setProperty("_awLabelPin", [label.minimumWidth(), label.maximumWidth()])
            label.setMinimumWidth(0)
            label.setMaximumWidth(_QWIDGETSIZE_MAX)
        else:
            pin = row.property("_awLabelPin")
            if pin:
                label.setMinimumWidth(int(pin[0]))
                label.setMaximumWidth(int(pin[1]))

    def _grab_scrolling(self, on: bool) -> None:
        """Let a drag scroll the long panels, the way a thumb expects.

        Over VNC a touch-drag arrives as a left-button mouse drag, which a Qt
        scroll area ignores. Grabbed only on the side rail, the Controls sheet
        and the logs — never on the plate or a plot, whose drags mean
        something else. A press on a control inside still reaches the control.
        Whether RealVNC's touch mode sends drags this way is not verified on a
        device.
        """
        from PySide6.QtWidgets import QAbstractScrollArea, QScroller
        roots = [self._h.rail_drawer, self._sheet]
        targets = []
        for r in roots:
            if r is not None:
                targets += r.findChildren(QAbstractScrollArea)
        targets += [w for w in QApplication.allWidgets()
                    if type(w).__name__ in ("QTextEdit", "QPlainTextEdit")
                    and w.isReadOnly()]
        for area in targets:
            vp = area.viewport()
            if on:
                QScroller.grabGesture(vp, QScroller.LeftMouseButtonGesture)
            else:
                QScroller.ungrabGesture(vp)

    def _restore_navigator(self) -> None:
        state = self._desk_state or {}
        self._h.set_navigator(state.get("navigator_open", True))

    # ── restyle ───────────────────────────────────────────────────────────
    def _restyle(self, factor: float, unit: float | None) -> None:
        """Re-apply the stylesheet at *factor* type scale and touch *unit*."""
        if abs(factor - theme.ui_scale()) < 1e-6 and unit == theme.touch_unit():
            return
        app = QApplication.instance()
        overlay = _BusyOverlay(self._win, "Resizing for your screen…\n"
                                          "This takes a few seconds.")
        overlay.show()
        overlay.raise_()
        overlay.repaint()
        self._applying = True
        try:
            # A switch, for anything waiting on one to finish: the rescale
            # pass below lets Qt deliver pending events, and timers with them.
            with phone_layout.switching():
                prev = theme.ui_scale()
                fonts = explicit_fonts()
                theme.apply_ui_scale(factor)
                theme.set_touch_unit(unit)
                if app is not None:
                    app.setStyleSheet(theme.qss())
                rescale_widgets(prev, factor, [w for w in QApplication.allWidgets()
                                               if w is not overlay
                                               and w.parentWidget() is not overlay],
                                fonts)
        finally:
            self._applying = False
            QTimer.singleShot(_OVERLAY_LINGER_MS, overlay.deleteLater)

    def _apply_sizes(self) -> None:
        """Pin the touch-sized parts of phone layout, and the desk's back."""
        band = self._h.band
        on = phone_layout.is_on()
        land = self.is_landscape()
        self._landscape = land if on else None
        self._place_page_controls(landscape=on and land)
        if on:
            strip_lay = self._strip.layout()
            strip_lay.setContentsMargins(self.pt(8), self.pt(4), self.pt(8), self.pt(4))
            strip_lay.setSpacing(self.pt(phone_layout.TOUCH_GAP_PT))
            self._strip.setFixedHeight(self.pt(phone_layout.TOP_BAR_PT))
            for b in (self.page_button, self.controls_button):
                b.setFixedHeight(self.pt(phone_layout.TOUCH_MIN_PT))
        self._size_canvases(on)
        if band is not None and band.layout() is not None:
            if on:
                bar = self.pt(LANDSCAPE_BAR_PT if land else phone_layout.TOP_BAR_PT)
                side = self.pt(phone_layout.TOUCH_ROW_PT if not land else 40)
                for b in (self.menu_button, self.options_button):
                    b.setFixedSize(side, side)
                band.setFixedHeight(bar)
                band.layout().setContentsMargins(self.pt(4), 0, self.pt(4), 0)
                band.layout().setSpacing(self.pt(phone_layout.TOUCH_GAP_PT))
            else:
                from widgets.mode_bar import tab_bar_height
                needed = tab_bar_height()
                lay = band.layout()
                for i in range(lay.count()):
                    w = lay.itemAt(i).widget()
                    if w is not None and not w.isHidden():
                        needed = max(needed, w.sizeHint().height())
                band.setFixedHeight(needed)
                band.layout().setContentsMargins(0, 0, theme.Spacing.md, 0)
                band.layout().setSpacing(theme.Spacing.sm)
        drawer = self._h.rail_drawer
        if drawer is not None and on:
            drawer._width_hint = (lambda: self.pt(LANDSCAPE_PANEL_PT)) if land else (
                lambda: max(1, drawer._host.width() - self.pt(RAIL_GAP_PT)))
            drawer._backdrop._alpha = 0.5
        self._apply_analyze_columns(on and land)

    def _size_canvases(self, on: bool) -> None:
        """Give each plot at least 220 pt of height per row of axes.

        Sideways, a three-panel figure squeezed into the screen's height drew
        axes eight times wider than tall. The plot pages scroll, so a taller
        canvas is a scroll away rather than a squashed one.
        """
        for c in _canvases():
            try:
                self.size_canvas(c, on)
            except RuntimeError:
                swallowed()  # canvas deleted while the list was walked

    def size_canvas(self, c, on: bool) -> None:
        base = c.property("_awMinH")
        if on:
            if base is None:
                base = c.minimumHeight()
                c.setProperty("_awMinH", base)
            rows = 1
            for ax in c.figure.axes:
                spec = ax.get_subplotspec()
                if spec is not None:
                    rows = max(rows, spec.get_gridspec().nrows)
            c.setMinimumHeight(max(int(base), rows * self.pt(PLOT_ROW_PT)))
        elif base is not None:
            c.setMinimumHeight(int(base))
            c.setProperty("_awMinH", None)

    def _apply_analyze_columns(self, two: bool) -> None:
        """Sideways, Analyze is two columns: the run summary and plate on the
        left, the log on the right; upright, one above the other."""
        from PySide6.QtWidgets import QBoxLayout
        body = self._h.analyze_body
        if not isinstance(body, QBoxLayout):
            return
        body.setDirection(QBoxLayout.LeftToRight if two else QBoxLayout.TopToBottom)
        if self._h.analyze_rule is not None:
            self._h.analyze_rule.setVisible(not two)
        for d in self._h.dashboards():
            d.setMaximumWidth(self.pt(LANDSCAPE_SUMMARY_PT) if two else _QWIDGETSIZE_MAX)
            plate = getattr(d, "_plate", None)
            if plate is not None and phone_layout.is_on():
                # Sideways the column is short: the plate is as tall as leaves
                # room for the summary above it and the tapped well's line.
                plate.setMaximumHeight(self.pt(LANDSCAPE_PLATE_PT) if two
                                       else _QWIDGETSIZE_MAX)

    # ── window ────────────────────────────────────────────────────────────
    def _avail(self) -> QRect:
        screen = self._win.screen() or QGuiApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else QRect(0, 0, 1640, 980)

    def _remember_desk_window(self) -> None:
        avail = self._avail()
        desk_screen = not phone_layout.is_phone_shaped(avail.width(), avail.height())
        self._desk_state = {
            "navigator_open": self._h.navigator_open(),
            # A geometry captured on a phone-shaped desktop is a phone's, and
            # restoring it at the desk would be the bug this avoids.
            "geometry": self._win.normalGeometry() if desk_screen else None,
            "maximized": self._win.isMaximized(),
        }

    def _apply_window(self, on: bool) -> None:
        if on:
            self.fit_window()
            self._win.showMaximized()
            return
        state = self._desk_state or {}
        if not state.get("maximized"):
            self._win.showNormal()
            geom = state.get("geometry")
            if geom is not None:
                self._win.setGeometry(geom)
        self.fit_window()

    def desk_minimum(self) -> tuple[int, int]:
        return (PHONE_MIN_W, PHONE_MIN_H) if phone_layout.is_on() else (
            phone_layout.DESK_MIN_W, phone_layout.DESK_MIN_H)

    def fit_window(self) -> None:
        """Keep the window's minimum and frame inside the screen, in any layout."""
        avail = self._avail()
        want_w, want_h = self.desk_minimum()
        self._win.setMinimumSize(min(want_w, avail.width()), min(want_h, avail.height()))
        if not (self._win.isMaximized() or self._win.isFullScreen()):
            clamp_to_screen(self._win)
        self._maybe_offer(avail)

    def watch_screen(self) -> None:
        """Re-fit on every screen change. Call once the window has a handle."""
        handle = self._win.windowHandle()
        if handle is None:
            return
        handle.screenChanged.connect(self._on_screen_changed)
        self._on_screen_changed(handle.screen())

    def _on_screen_changed(self, screen) -> None:
        if self._screen is not None:
            try:
                self._screen.availableGeometryChanged.disconnect(self._on_geometry_changed)
            except (RuntimeError, TypeError):
                swallowed()  # already disconnected, or the screen is gone
        self._screen = screen
        if screen is not None:
            screen.availableGeometryChanged.connect(self._on_geometry_changed)
        self.fit_window()

    def _on_geometry_changed(self, _rect) -> None:
        self.fit_window()
        if not phone_layout.is_on() or self._applying:
            return
        # The phone turned (VNC resized the desktop under a maximized window),
        # or a new resolution changed what a phone point is.
        self.close_controls(now=True)
        self._restyle(self.text_scale(), self.unit())
        scale_canvases(self.plot_factor())
        self._apply_sizes()
        self.refresh()

    # ── the offer ─────────────────────────────────────────────────────────
    def _maybe_offer(self, avail: QRect) -> None:
        """Offer the other layout when the screen's shape says it would fit
        better. Never switch: the user may be mid-tap."""
        phone_shaped = phone_layout.is_phone_shaped(avail.width(), avail.height())
        mismatch = phone_shaped != phone_layout.is_on()
        if not mismatch or self._banner_dismissed_for == phone_shaped:
            self._hide_banner()
            return
        self._show_banner(to_phone=phone_shaped)

    def _show_banner(self, *, to_phone: bool) -> None:
        host = self._banner_host()
        if host is None:
            return
        from widgets.context_banner import ContextBanner
        text = ("This screen looks like a phone. Switch to phone layout?"
                if to_phone else
                "This screen looks like a desktop. Switch back to the desktop layout?")
        if self._banner is None:
            self._banner = ContextBanner(text, "Switch", None, icon="smartphone",
                                         cancel_text="Not now")
            self._banner.setObjectName("PhoneLayoutOffer")
            self._banner.confirmed.connect(self._on_banner_confirmed)
            self._banner.cancelled.connect(self._on_banner_cancelled)
            host.insertWidget(1, self._banner)
        self._banner.setText(text)
        self._banner._aw_to_phone = to_phone
        self._banner.show()

    def _banner_host(self):
        central = self._win.centralWidget() if hasattr(self._win, "centralWidget") else None
        return central.layout() if central is not None else None

    def _hide_banner(self) -> None:
        if self._banner is not None:
            self._banner.hide()

    def _on_banner_confirmed(self) -> None:
        to_phone = bool(getattr(self._banner, "_aw_to_phone", True))
        self._hide_banner()
        self.set_on(to_phone)

    def _on_banner_cancelled(self) -> None:
        self._banner_dismissed_for = bool(getattr(self._banner, "_aw_to_phone", True))
        self._hide_banner()

    # ── the Controls sheet ────────────────────────────────────────────────
    def _current_band(self) -> QWidget | None:
        """The controls of the Review page on screen: its control band, if that
        has controls, or else a block the page marked ``phoneControls`` (the
        Image Table's builder and action strip, whose band is empty)."""
        marked = None
        for w in QApplication.allWidgets():
            band = type(w).__name__ == "ControlBand"
            if not (band or _is_phone_controls(w)) or not self._band_managed(w):
                continue
            parent = w.parentWidget()
            if w is not self._sheet_band and (parent is None or not parent.isVisible()):
                continue
            if not band:
                marked = w
            elif (w.scope_count() + w.action_count()) > 0:
                return w
        return marked

    def _ensure_sheet(self):
        land = self.is_landscape()
        if self._sheet is not None and getattr(self._sheet, "_aw_land", None) == land:
            return self._sheet
        if self._sheet is not None:
            self._sheet.closeNow()
            self._sheet.deleteLater()
        from PySide6.QtWidgets import QScrollArea
        from widgets.drawer import Drawer
        band = self._h.band

        def _below_band() -> int:
            return band.mapTo(self._win, band.rect().bottomLeft()).y() + 1 if band else 0

        def _content_height() -> int:
            # As tall as what it holds, up to its share of the window: a sheet
            # taller than its controls is dead space where the plot could be.
            holder = getattr(sheet, "_aw_holder", None)
            lay = holder.layout() if holder is not None else None
            want = (lay.totalHeightForWidth(self._win.width()) if lay is not None
                    and lay.hasHeightForWidth() else
                    holder.sizeHint().height() if holder is not None else 0)
            return min(int(self._win.height() * SHEET_HEIGHT_SHARE), want + self.pt(8))

        # No scrim: the plot above the sheet redraws as its controls change,
        # and a dimmed plot is one the user cannot judge the change by.
        if land:
            sheet = Drawer(self._win, edge="right", width_hint=lambda: self.pt(LANDSCAPE_SHEET_PT),
                           margin=0, top_inset=_below_band, scrim=0.0)
        else:
            sheet = Drawer(self._win, edge="bottom", height_hint=_content_height,
                           margin=0, scrim=0.0)
        sheet.setProperty("phoneSheet", True)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(self.pt(8), self.pt(8), self.pt(8), self.pt(8))
        lay.addStretch(1)
        scroll.setWidget(holder)
        sheet.setContentWidget(scroll)
        sheet._aw_land = land
        sheet._aw_holder = holder
        sheet.closed.connect(self._give_band_back)
        self._sheet = sheet
        return sheet

    def toggle_controls(self) -> None:
        if self._sheet is not None and self._sheet.isOpen():
            self.close_controls()
        else:
            self.open_controls()

    def open_controls(self) -> None:
        band = self._current_band()
        if band is None:
            return
        sheet = self._ensure_sheet()
        parent = band.parentWidget()
        lay = parent.layout() if parent is not None else None
        index = lay.indexOf(band) if lay is not None else -1
        self._sheet_band = band
        band.setProperty("_awHome", index)
        self._sheet_home = parent
        holder = sheet._aw_holder
        holder.layout().insertWidget(0, band)
        band.setStacked(True)
        band.show()
        scope = getattr(band, "_scope", None)       # a ControlBand's flow
        if scope is not None:
            scope.setSpacing(self.pt(phone_layout.TOUCH_GAP_PT))
        self._grab_scrolling(True)
        sheet.open()
        self.controls_button.setText("Done ✕")

    def close_controls(self, *, now: bool = False) -> None:
        if self._sheet is None:
            return
        if now:
            self._sheet.closeNow()
        elif self._sheet.isOpen():
            self._sheet.close()

    def _give_band_back(self) -> None:
        band, self._sheet_band = self._sheet_band, None
        self.controls_button.setText("⚙ Controls")
        if band is None:
            return
        try:
            band.setStacked(False)
            scope = getattr(band, "_scope", None)
            if scope is not None:
                scope.setSpacing(-1)
            home = getattr(self, "_sheet_home", None)
            index = band.property("_awHome")
            if home is not None and home.layout() is not None:
                home.layout().insertWidget(max(0, int(index or 0)), band)
            if phone_layout.is_on():
                band.hide()          # already in self._hid from entering
            else:
                band.show()
        except RuntimeError:
            swallowed()  # the page went while its controls were out

    # ── menus and the drawer ──────────────────────────────────────────────
    def refresh(self) -> None:
        """Sync the band's phone controls with the current mode and drawer."""
        if not phone_layout.is_on():
            return
        open_ = self._h.navigator_open()
        self.menu_button.setChecked(open_)
        self.menu_button.setText("✕" if open_ else "☰")
        review = self._h.mode() == "review"
        bar = self._current_page_bar()
        self.page_button.setVisible(bar is not None and review)
        if bar is not None:
            self.page_button.setText(f"{bar.tabText(bar.currentIndex())} ▾")
        self.controls_button.setVisible(review and self._current_band() is not None)
        land = bool(self._landscape)
        self._strip.setVisible(review and not land
                               and (self.page_button.isVisibleTo(self._strip)
                                    or self.controls_button.isVisibleTo(self._strip)))
        if not review:
            self.close_controls()

    def _refresh_when_settled(self) -> None:
        if phone_layout.is_switching():
            self._refresh_soon.start()     # the switch ends with a refresh of its own
            return
        self.refresh()

    def _current_page_bar(self):
        fn = self._h.current_page_bar
        bar = fn() if fn is not None else None
        return bar if bar is not None and bar.count() > 0 else None

    def _on_menu_clicked(self) -> None:
        self.close_controls()
        self._h.set_navigator(not self._h.navigator_open())
        self.refresh()

    def close_drawer(self) -> None:
        """Close the left panel — after a section is picked in it, say."""
        if phone_layout.is_on() and self._h.navigator_open():
            self._h.set_navigator(False)
            self.refresh()

    def _fill_page_menu(self) -> None:
        self._page_menu.clear()
        bar = self._current_page_bar()
        if bar is None:
            return
        group = QActionGroup(self._page_menu)
        for i in range(bar.count()):
            act = QAction(bar.tabText(i), self._page_menu, checkable=True)
            act.setChecked(i == bar.currentIndex())
            act.setEnabled(bar.isTabEnabled(i))
            act.triggered.connect(lambda _c=False, b=bar, idx=i: self._pick_page(b, idx))
            group.addAction(act)
            self._page_menu.addAction(act)

    def _pick_page(self, bar, idx: int) -> None:
        bar.setCurrentIndex(idx)
        self.close_controls(now=True)
        self.refresh()

    def _text_size_row(self):
        """The ⋯ menu's first item: Text size, as three buttons in one row."""
        from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidgetAction
        act = QWidgetAction(self._menu)
        row = QWidget()
        row.setObjectName("PhoneTextSizeRow")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(self.pt(16), self.pt(4), self.pt(12), self.pt(4))
        lay.setSpacing(self.pt(8))
        lay.addWidget(QLabel("Text size", row), 1)
        for size in phone_layout.TEXT_SIZES:
            b = QPushButton(phone_layout.TEXT_SIZE_LABEL[size][0], row)
            b.setObjectName("PhoneTextSizeButton")
            b.setCheckable(True)
            b.setChecked(size == self._text_size)
            b.setFixedSize(self.pt(48), self.pt(44))
            b.clicked.connect(lambda _c=False, s=size: (self._menu.close(),
                                                        self.set_text_size(s)))
            lay.addWidget(b)
        act.setDefaultWidget(row)
        return act

    def _fill_menu(self) -> None:
        from widgets.owned_action import owned_action
        m = self._menu
        m.clear()
        m.addAction(self._text_size_row())
        m.addSeparator()
        if self._h.reload is not None and self._h.mode() == "review":
            owned_action(m, "Reload dataset").triggered.connect(self._h.reload)
        if self._h.show_help is not None:
            owned_action(m, "Help").triggered.connect(self._h.show_help)
        if self._h.show_log is not None:
            lit = self._h.log_badge is not None and self._h.log_badge.tone() is not None
            owned_action(m, "App log  •" if lit else "App log").triggered.connect(
                self._h.show_log)
        m.addSeparator()
        owned_action(m, "Desktop layout").triggered.connect(lambda: self.set_on(False))
