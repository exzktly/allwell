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


class _PhoneEventFilter(QObject):
    """While phone layout is on: plots built later get its scale, and a
    dialog that opens larger than the screen is brought inside it."""

    def __init__(self, shell: PhoneShell) -> None:
        super().__init__(shell)
        self._shell = shell

    def eventFilter(self, obj, event) -> bool:  # Qt naming
        if event.type() != QEvent.Show or not isinstance(obj, QWidget):
            return False
        if obj.isWindow() and isinstance(obj, QDialog):
            clamp_to_screen(obj)
        elif getattr(type(obj), "draw_idle", None) is not None and hasattr(obj, "figure"):
            scale_canvas(obj, self._shell.plot_factor())
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
        self._text_size = phone_layout.AUTO
        self._applying = False
        self._banner = None
        self._banner_dismissed_for: bool | None = None
        self._screen = None
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
                s.value(phone_layout.KEY_TEXT_SIZE, phone_layout.AUTO))
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

    # ── controls ──────────────────────────────────────────────────────────
    def _build_controls(self) -> None:
        from widgets.icon_button import IconButton

        # Desktop: a labelled button at the header's *left*, beside the
        # version pill — the left edge is the one part of a too-wide window
        # still on a narrow screen, and the label says what it does without a
        # tooltip, which a touch screen never shows.
        self.desk_button = IconButton("smartphone", text="Phone layout")
        self.desk_button.setObjectName("PhoneLayoutButton")
        self.desk_button.setToolTip(
            "Switch to phone layout: one column, larger text and plots, for "
            "using All-Well from a phone over remote desktop.")
        self.desk_button.clicked.connect(lambda: self.set_on(True))

        # Phone: ☰ slides the pane's whole left panel — Review's sections and
        # well plate, the Analyze form — out over the page from the left.
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

        self._band_spacer = QWidget()
        self._band_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        # Phone layout's own settings — text size, and the way back to the
        # desktop layout — behind the same phone glyph the desk button wears.
        self.options_button = QToolButton()
        self.options_button.setObjectName("PhoneOptionsButton")
        self.options_button.setText("Aa")
        self.options_button.setPopupMode(QToolButton.InstantPopup)
        self._menu = QMenu(self.options_button)
        self._menu.aboutToShow.connect(self._fill_menu)
        self._size_menu = QMenu("Text size", self._menu)
        self.options_button.setMenu(self._menu)

        band = self._h.band
        layout = band.layout() if band is not None else None
        if layout is not None:
            at = self._h.band_insert_at
            layout.insertWidget(at, self.menu_button)
            bar_at = layout.indexOf(self._h.mode_bar) if self._h.mode_bar else at
            layout.insertWidget(bar_at + 1, self.page_button)
            layout.insertWidget(bar_at + 2, self._band_spacer)
            # Before the band's last widget, Help, which keeps the rightmost slot.
            layout.insertWidget(max(0, layout.count() - 1), self.options_button)
        for w in self._phone_only():
            w.hide()

    def _phone_only(self) -> list[QWidget]:
        return [self.menu_button, self.page_button, self._band_spacer,
                self.options_button]

    # ── the switch ────────────────────────────────────────────────────────
    def is_on(self) -> bool:
        return phone_layout.is_on()

    def set_on(self, on: bool, *, restyle: bool = True) -> None:
        """Enter or leave phone layout. *restyle* False skips the text scale
        (startup has already built everything at the right size)."""
        on = bool(on)
        if on == phone_layout.is_on() or self._applying:
            return
        self._save(phone_layout.KEY_ON, on)
        self._hide_banner()
        if on:
            self._remember_desk_window()
        phone_layout.set_on(on)
        self._apply_chrome(on)
        if on:
            self._h.set_navigator(False)
        else:
            self._restore_navigator()
        if restyle:
            self._apply_text_scale(self.text_scale() if on else 1.0)
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
        self.refresh()

    def _apply_chrome(self, on: bool) -> None:
        if self._h.header is not None:
            self._h.header.setVisible(not on)
        for w in self._h.desk_only:
            w.setVisible(not on)
        if self._h.page_tabs_host is not None:
            self._h.page_tabs_host.setVisible(not on)
        bar = self._h.mode_bar
        if bar is not None and hasattr(bar, "setCompact"):
            bar.setCompact(on)
        for w in self._phone_only():
            w.setVisible(on)
        for d in self._h.dashboards():
            d.setStacked(on)

    def _restore_navigator(self) -> None:
        state = self._desk_state or {}
        self._h.set_navigator(state.get("navigator_open", True))

    # ── text size ─────────────────────────────────────────────────────────
    def text_size(self) -> float:
        """The stored choice: a preset, or ``AUTO``."""
        return self._text_size

    def text_scale(self) -> float:
        """The scale the stored choice means on this screen."""
        avail = self._avail()
        return phone_layout.resolve_text_size(
            self._text_size, avail.width(), avail.height())

    def plot_factor(self) -> float:
        """How much larger plots draw their text in phone layout.

        The square root of the text scale, not the scale itself: in one column
        a plot already gets the screen's full width, and the renderers lay out
        their axes with fixed margins, so the full factor pushed titles off the
        figure and y-labels into the tick numbers (measured at 1.75 on a
        1080-wide portrait desktop). ``phone_layout.plot_factor`` is the rule.
        """
        return phone_layout.plot_factor(theme.ui_scale()) if phone_layout.is_on() else 1.0

    def set_text_size(self, stored: float) -> None:
        self._text_size = stored
        self._save(phone_layout.KEY_TEXT_SIZE, stored)
        if phone_layout.is_on():
            self._apply_text_scale(self.text_scale())
            scale_canvases(self.plot_factor())
            self.refresh()

    def _apply_text_scale(self, factor: float) -> None:
        if abs(factor - theme.ui_scale()) < 1e-6:
            return
        app = QApplication.instance()
        overlay = _BusyOverlay(self._win, "Resizing text…\nThis takes a few seconds.")
        overlay.show()
        overlay.raise_()
        overlay.repaint()
        self._applying = True
        try:
            prev = theme.ui_scale()
            fonts = explicit_fonts()
            theme.apply_ui_scale(factor)
            if app is not None:
                app.setStyleSheet(theme.qss())
            rescale_widgets(prev, factor, [w for w in QApplication.allWidgets()
                                           if w is not overlay and w.parentWidget() is not overlay],
                            fonts)
            self._repin_band()
        finally:
            self._applying = False
            QTimer.singleShot(_OVERLAY_LINGER_MS, overlay.deleteLater)

    def _repin_band(self) -> None:
        """The band is pinned to a tab bar's height; re-measure at the new size."""
        band = self._h.band
        if band is None or band.layout() is None:
            return
        from widgets.mode_bar import tab_bar_height
        needed = tab_bar_height()
        lay = band.layout()
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if w is not None and not w.isHidden():
                needed = max(needed, w.sizeHint().height())
        band.setFixedHeight(needed)

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
        if phone_layout.is_on():
            # Rotating the phone: VNC resized the desktop under a maximized
            # window. Auto's pick depends on the screen, so it may change.
            if self._text_size == phone_layout.AUTO:
                self._apply_text_scale(self.text_scale())
                scale_canvases(self.plot_factor())

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

    # ── menus and the drawer ──────────────────────────────────────────────
    def refresh(self) -> None:
        """Sync the band's phone controls with the current mode and drawer."""
        if not phone_layout.is_on():
            return
        self.menu_button.setChecked(self._h.navigator_open())
        bar = self._current_page_bar()
        self.page_button.setVisible(bar is not None and self._h.mode() == "review")
        if bar is not None:
            self.page_button.setText(f"{bar.tabText(bar.currentIndex())} ▾")

    def _current_page_bar(self):
        fn = self._h.current_page_bar
        bar = fn() if fn is not None else None
        return bar if bar is not None and bar.count() > 0 else None

    def _on_menu_clicked(self) -> None:
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
        self.refresh()

    def _fill_menu(self) -> None:
        m = self._menu
        m.clear()
        # One submenu for the life of the shell, emptied and refilled here:
        # ``clear()`` on the parent drops only the actions it owns, so the
        # submenu survives it, and a submenu made afresh on every open left
        # callers holding a wrapper onto one already deleted.
        sizes = self._size_menu
        sizes.clear()
        m.addMenu(sizes)
        group = QActionGroup(sizes)
        avail = self._avail()
        auto_pick = phone_layout.auto_text_size(avail.width(), avail.height())
        for stored in (phone_layout.AUTO, *phone_layout.PRESETS):
            label = phone_layout.text_size_label(stored)
            if stored == phone_layout.AUTO:
                label = f"Auto ({phone_layout.text_size_label(auto_pick)})"
            act = QAction(label, sizes, checkable=True)
            act.setChecked(stored == self._text_size)
            act.triggered.connect(lambda _c=False, s=stored: self.set_text_size(s))
            group.addAction(act)
            sizes.addAction(act)
        m.addSeparator()
        desk = m.addAction("Desktop layout")
        desk.triggered.connect(lambda: self.set_on(False))
