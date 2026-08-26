"""Saved Plots gallery tab — a scrollable list of saved-plot cards.

Each card shows the captured panel image at full tab width, its (renameable)
title + originating-tab badge + timestamp, a collapsible parameter summary
(hidden by default), and Restore / Export / Rename / Delete actions.

The page-level Export control offers the whole gallery in either of two formats:
a PDF report, which shows each plot as the picture it was captured as, and a
PowerPoint deck, which redraws each plot as PowerPoint shapes the user can
restyle. Both walk the gallery in the order shown here, which is the order the
▲/▼ buttons curate.

The empty-state is driven by the saved-plot count (not dataset presence), so a
freshly-saved plot shows immediately on first open of the tab.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from widgets.page_body import build_page_body
from well_viewer.core.safe import swallow, swallowed
from well_viewer.core.ui_helpers import clear_layout
from well_viewer.features.saved_plots import controller as C
from well_viewer.features.saved_plots.state import get_store

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_DOWN_BTN_W = 32
_CARD_MIN_W = 1
_UP_BTN_W = 32

#: How long a card stays outlined after the gallery jumps to it, in milliseconds.
#: Long enough to be seen by someone whose eyes were on the thumbnail they
#: clicked, short enough that the outline is never mistaken for a selection the
#: gallery is holding.
_FOCUS_FLASH_MS = 1600

#: Gap left above a card the gallery has jumped to, so it does not sit flush
#: against the control band.
_SCROLL_TOP_MARGIN = 8

#: How many event-loop passes the scroll may re-aim over while the gallery's
#: layout settles. Bounded so a layout that never converges cannot spin.
_SCROLL_SETTLE_TRIES = 8


class _AspectImage(QLabel):
    """A saved figure's preview: sized from the file header, decoded when painted.

    The preview fills the card's width and keeps the figure's aspect ratio, so a
    saved plot reads like the real thing. What it does *not* do is decode the
    file to find that out.

    A saved plot's PNG is a 300 dpi capture — routinely 1800x1200 — and decoding
    one costs about 18 ms. The gallery builds a card per saved plot but shows two
    or three of them at a time, so decoding at build time spent that on every
    card in the list, most of them far below the viewport, and spent it again on
    every repopulate: opening the tab, saving a plot, deleting one, renaming one,
    reordering one. Twenty saved plots was some 0.4 s of decoding, nearly all of
    it for images nobody was looking at.

    Reading the header instead costs 0.02 ms and yields the one thing the layout
    needs, which is the aspect ratio. Qt does not paint a widget that is scrolled
    out of view, so ``paintEvent`` is the visibility signal we already have: the
    pixels are decoded when the card first comes into view, once, and never at
    all for a card the user never scrolls to.
    """

    def __init__(self, image_path, ratio: float, parent=None) -> None:
        super().__init__(parent)
        self._path = image_path
        #: Height as a fraction of width, from the header. Drives the reserved
        #: height, so the card is the right size before any decode happens.
        self._ratio = ratio
        self._src: QPixmap | None = None
        #: Set once the decode has been attempted, so a file that fails to
        #: decode is reported once rather than retried on every repaint.
        self._decoded = False
        #: Width the current scaled pixmap was built for; -1 until there is one.
        self._scaled_for = -1
        self.setAlignment(Qt.AlignCenter)
        # Expand horizontally; height is driven by the aspect ratio.
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setMinimumWidth(_CARD_MIN_W)
        self._reserve_height()

    @classmethod
    def for_path(cls, image_path, parent=None) -> "_AspectImage | None":
        """A preview for *image_path*, or None when there is no readable image.

        Returning None rather than an empty preview keeps "this record's file is
        missing or unreadable" distinguishable from "this plot drew nothing" —
        the caller puts a named placeholder on the card instead.
        """
        ratio = _aspect_ratio_of(image_path)
        if ratio is None:
            return None
        return cls(image_path, ratio, parent)

    def resizeEvent(self, event) -> None:  # Qt override
        self._reserve_height()
        self._rescale()
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # Qt override
        # First paint means the card has actually come into view. Decode now.
        if not self._decoded:
            self._decode()
        super().paintEvent(event)

    def _reserve_height(self) -> None:
        h = max(1, round(max(1, self.width()) * self._ratio))
        if self.height() != h:
            self.setFixedHeight(h)

    def _rescale(self) -> None:
        if self._src is None:
            return
        w = max(1, self.width())
        if self._scaled_for == w:
            return
        self._scaled_for = w
        self.setPixmap(self._src.scaledToWidth(w, Qt.SmoothTransformation))

    def _decode(self) -> None:
        self._decoded = True
        pixmap = QPixmap(str(self._path))
        if pixmap.isNull():
            # The header parsed and the pixels did not. Say so on the card: a
            # preview that silently stays blank is indistinguishable from a
            # figure that drew nothing.
            self.setObjectName("Muted")
            self.setText("(image unavailable)")
            return
        self._src = pixmap
        self._rescale()


def _aspect_ratio_of(image_path) -> float | None:
    """Height over width for *image_path*, read from the header alone.

    None when the path is missing, unreadable, or does not declare a usable
    size — which is the caller's cue to draw a placeholder rather than a preview.
    """
    from PySide6.QtGui import QImageReader

    if image_path is None:
        return None
    reader = QImageReader(str(image_path))
    if not reader.canRead():
        return None
    size = reader.size()
    if not size.isValid() or size.isEmpty():
        return None
    return size.height() / size.width()


def build_saved_plots_tab(ctx, parent: QWidget) -> None:
    # A8 burn-down: Qt widget handles go through the WidgetRegistry (``ctx.widgets``),
    # which stamps ``app._saved_plots_*`` so legacy readers resolve. The repopulate
    # callback stays on the app.
    reg = ctx.widgets

    # The gallery's count is scope (how much is in here) and the two buttons act
    # on it — the standard band, rather than the hand-rolled row this tab used
    # to build, which was 8px shorter than every other page's and so started the
    # gallery under it higher than its neighbours.
    band, body = build_page_body(parent)
    layout = body.layout()

    count_lbl = reg.register("_saved_plots_count_lbl", QLabel("", band))
    band.add_scope("", count_lbl)
    band.add_action(reg.register("_saved_plots_export_btn",
                                 _build_export_button(ctx, band)))
    clear_btn = QPushButton("Clear all", band)
    clear_btn.setProperty("variant", "secondary")
    clear_btn.clicked.connect(lambda: _confirm_clear(ctx))
    band.add_action(clear_btn)

    # Empty-state ↔ gallery stack, toggled by the saved-plot count (NOT by
    # dataset presence) so a just-saved plot appears on first open.
    from widgets.empty_state import EmptyState
    stack = QStackedWidget(parent)
    stack.addWidget(EmptyState(
        text="No saved plots yet.", icon="image",
        hint="On any plot, hover a panel's top-right corner and click Save.",
    ))  # index 0

    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    list_host = QWidget()
    col = QVBoxLayout(list_host)
    col.setContentsMargins(10, 10, 10, 10)
    col.setSpacing(12)
    col.setAlignment(Qt.AlignTop)
    scroll.setWidget(list_host)
    stack.addWidget(scroll)  # index 1

    reg.register("_saved_plots_scroll", scroll)
    reg.register("_saved_plots_stack", stack)
    reg.register("_saved_plots_list", col)
    reg.register("_saved_plots_list_host", list_host)
    # Controller repaints the gallery through this callback (avoids a
    # controller → tab_view import cycle).
    ctx.runtime.saved_plots.repopulate = lambda: populate_saved_plots_grid(ctx)

    layout.addWidget(stack, 1)
    populate_saved_plots_grid(ctx)


def _build_export_button(ctx, band) -> QPushButton:
    """The gallery's Export control — one button, two destinations.

    A menu rather than two buttons because the choice is between *formats of the
    same export*, not between two actions: both send every saved plot, in gallery
    order, to one file. The wording says what the user gets rather than naming the
    file type twice, since "editable in PowerPoint" is the whole reason to pick
    the second one over the first.
    """
    from PySide6.QtWidgets import QMenu

    button = QPushButton("Export…", band)
    button.setToolTip("Export every saved plot to a single file.")
    menu = QMenu(button)
    pdf_action = menu.addAction("PDF report…")
    pdf_action.setToolTip(
        "One page per saved plot, each showing the figure as it was captured.")
    pdf_action.triggered.connect(lambda _=False: C.export_report_pdf(ctx))
    pptx_action = menu.addAction("PowerPoint deck (editable figures)…")
    pptx_action.setToolTip(
        "One slide per saved plot, redrawn as PowerPoint shapes — bars, lines "
        "and labels you can restyle in PowerPoint itself.")
    pptx_action.triggered.connect(lambda _=False: C.export_deck_pptx(ctx))
    # The menu is parented to the button, but ``setMenu`` is what makes the
    # button show it: without it the actions exist and nothing can reach them,
    # which is the failure this file already carries a note about elsewhere —
    # a control that is offered is not a control that works.
    button.setMenu(menu)
    return button


def _confirm_clear(ctx) -> None:
    """Ask before deleting every saved plot for this dataset, and its images.

    An answer that could not be obtained is not consent. This used to fall
    through to clearing when the dialog raised — the rationale being a headless
    run with no dialog available — which turns "the confirmation failed" into
    "the user said yes" for the one action in this tab that destroys work and
    cannot be undone. If the question cannot be asked, nothing is cleared.
    """
    from PySide6.QtWidgets import QMessageBox
    try:
        answer = QMessageBox.question(
            None, "Clear saved plots",
            "Remove all saved plots for this dataset?",
        )
    except Exception:
        swallowed("saved_plots: clear was not confirmed; nothing cleared")
        return
    if answer != QMessageBox.Yes:
        return
    C.clear_saved_plots(ctx)


def populate_saved_plots_grid(ctx) -> None:
    """Rebuild the gallery from the current store contents and flip the
    empty-state based on how many saved plots exist.

    A8 teardown: takes ``ctx`` — the gallery widget handles read through
    ``ctx.widgets`` and the store/capture layer is ctx-native."""
    col = ctx.widgets.get_or_none("_saved_plots_list")
    if col is None:
        return
    clear_layout(col)
    store = get_store(ctx)
    store.ensure_loaded(ctx)
    records = C.gallery_order(store)  # newest first

    lbl = ctx.widgets.get_or_none("_saved_plots_count_lbl")
    if lbl is not None:
        n = len(records)
        lbl.setText(f"{n} saved plot{'' if n == 1 else 's'}")

    stack = ctx.widgets.get_or_none("_saved_plots_stack")
    if stack is not None:
        stack.setCurrentIndex(1 if records else 0)

    total = len(records)
    # Cards indexed by plot id so another page can send the user to one of them.
    # Rebuilt here rather than accumulated: ``clear_layout`` above has already
    # destroyed the previous set of C++ objects, and an index that outlived them
    # would hand out handles to deleted widgets.
    cards: dict[str, QWidget] = {}
    for i, rec in enumerate(records):
        card = _build_saved_card(ctx, store, rec, is_first=(i == 0),
                                 is_last=(i == total - 1))
        cards[rec.id] = card
        col.addWidget(card)
    ctx.runtime.saved_plots.cards = cards


def scroll_to_saved_plot(ctx, plot_id: str) -> bool:
    """Put the gallery card for *plot_id* at the top of the view, and outline it.

    Returns False when the gallery holds no card for that id — the tab has not
    been built yet, or the plot was deleted between the click and this call. The
    caller reports that rather than leaving the user on a page that looks like it
    ignored them.

    The scroll itself is **deferred**, because the caller has just switched to
    this page and the page has no geometry yet: the scroll area's range is still
    zero, every card sits at y=0, and a scroll issued now is a scroll to the top
    of a list that has not been laid out. That is not a hypothetical — it is what
    shipped, and it always landed on the first card. ``_scroll_when_laid_out``
    waits for the layout and then scrolls, so the return value here means "there
    is a card and it has been asked for", which is the most this function can
    honestly promise before the event loop has run.
    """
    cards = ctx.runtime.saved_plots.cards or {}
    card = cards.get(plot_id)
    scroll = ctx.widgets.get_or_none("_saved_plots_scroll")
    if card is None or scroll is None:
        return False
    try:
        # Touching the card is what proves its C++ object is still alive. The
        # gallery may have been rebuilt between the lookup and here, and a
        # function whose whole job is to say whether the jump can land must not
        # answer True for a card that no longer exists.
        card.isVisible()
    except RuntimeError:
        return False
    _scroll_when_laid_out(scroll, card)
    _flash(card)
    return True


def _scroll_when_laid_out(scroll, card) -> None:
    """Scroll *card* to the top of *scroll*'s viewport once there is a layout."""
    from PySide6.QtCore import QTimer

    def _attempt(remaining: int, previous: int) -> None:
        with swallow("saved_plots: scroll to card"):
            host = scroll.widget()
            if host is None or card.parent() is None:
                return                      # the gallery was rebuilt under us
            bar = scroll.verticalScrollBar()
            # Explicitly to the card's top, not ``ensureWidgetVisible``: a saved
            # figure is routinely taller than the viewport, and "make it visible"
            # then means scrolling until its *bottom* shows — the user arrives at
            # the foot of the plot they clicked. A few pixels of margin above it
            # so the card does not sit flush against the band.
            top = card.mapTo(host, card.rect().topLeft()).y()
            target = max(bar.minimum(),
                         min(bar.maximum(), top - _SCROLL_TOP_MARGIN))
            bar.setValue(target)
            # Keep going until the target stops moving. The cards size themselves
            # from their previews' aspect ratios (``_AspectImage`` sets its height
            # in ``resizeEvent``), so the list's height is still changing for a
            # few passes after the page is shown and a scroll computed on the
            # first one lands short of where the card ends up.
            if remaining > 0 and target != previous:
                QTimer.singleShot(0, lambda: _attempt(remaining - 1, target))

    QTimer.singleShot(0, lambda: _attempt(_SCROLL_SETTLE_TRIES, -1))


def _flash(card: QWidget) -> None:
    """Outline *card* for a moment, then put it back the way it was."""
    from PySide6.QtCore import QTimer

    def _set(on: bool) -> None:
        # The timer outlives the gallery: a repopulate between the flash and its
        # clear leaves this closure holding a deleted C++ object, and touching
        # it raises. The card is already gone, which is the outcome the clear
        # wanted, so there is nothing to do about it beyond not crashing.
        with swallow("saved_plots: focus flash"):
            card.setProperty("focusflash", "true" if on else "false")
            style = card.style()
            style.unpolish(card)
            style.polish(card)

    _set(True)
    QTimer.singleShot(_FOCUS_FLASH_MS, lambda: _set(False))


def _build_saved_card(ctx, store, rec, *, is_first: bool = True,
                      is_last: bool = True) -> QWidget:
    card = QFrame()
    card.setObjectName("Panel")
    card.setAttribute(Qt.WA_StyledBackground, True)
    card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
    v = QVBoxLayout(card)
    v.setContentsMargins(12, 12, 12, 12)
    v.setSpacing(8)

    # Header: title + tab badge + timestamp
    header = QHBoxLayout()
    title = QLabel(rec.title or rec.tab, card)
    title.setObjectName("PlotCardTitle")
    header.addWidget(title)
    header.addStretch(1)
    badge = QLabel(rec.tab, card)
    badge.setObjectName("Muted")
    header.addWidget(badge)
    v.addLayout(header)

    # Full-width figure (scales with the tab). The preview sizes itself from the
    # file header here and decodes its pixels the first time it is painted, so a
    # gallery of twenty plots does not decode twenty 300 dpi captures to show two.
    preview = _AspectImage.for_path(store.image_path(ctx, rec), card)
    if preview is not None:
        v.addWidget(preview)
    else:
        missing = QLabel("(image unavailable)", card)
        missing.setObjectName("Muted")
        missing.setAlignment(Qt.AlignCenter)
        v.addWidget(missing)

    ts = QLabel(rec.created_at.replace("T", "  "), card)
    ts.setObjectName("Muted")
    v.addWidget(ts)

    # Parameter summary — collapsed by default, folds out on request.
    if rec.summary:
        from widgets.collapsible_section import CollapsibleSection
        details = CollapsibleSection("Parameters", expanded=False)
        summary = QLabel(rec.summary, card)
        summary.setObjectName("Muted")
        summary.setWordWrap(True)
        summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        details.addWidget(summary)
        v.addWidget(details)

    # Actions
    actions = QHBoxLayout()
    up_btn = QPushButton("▲", card)
    up_btn.setProperty("variant", "secondary")
    up_btn.setToolTip("Move up")
    up_btn.setFixedWidth(_UP_BTN_W)
    up_btn.setEnabled(not is_first)
    up_btn.clicked.connect(lambda _=False, _id=rec.id: C.move_saved_plot(ctx, _id, True))
    actions.addWidget(up_btn)
    down_btn = QPushButton("▼", card)
    down_btn.setProperty("variant", "secondary")
    down_btn.setToolTip("Move down")
    down_btn.setFixedWidth(_DOWN_BTN_W)
    down_btn.setEnabled(not is_last)
    down_btn.clicked.connect(lambda _=False, _id=rec.id: C.move_saved_plot(ctx, _id, False))
    actions.addWidget(down_btn)
    restore_btn = QPushButton("Restore settings", card)
    restore_btn.setToolTip("Re-apply these parameters and open the source tab.")
    restore_btn.clicked.connect(lambda _=False, _id=rec.id: C.restore_saved_plot(ctx, _id))
    actions.addWidget(restore_btn)
    rename_btn = QPushButton("Rename", card)
    rename_btn.setProperty("variant", "secondary")
    rename_btn.clicked.connect(lambda _=False, _id=rec.id, _t=rec.title: _rename(ctx, _id, _t))
    actions.addWidget(rename_btn)
    export_btn = QPushButton("Export…", card)
    export_btn.setProperty("variant", "secondary")
    export_btn.clicked.connect(lambda _=False, _id=rec.id: C.export_saved_plot(ctx, _id))
    actions.addWidget(export_btn)
    actions.addStretch(1)
    del_btn = QPushButton("Delete", card)
    del_btn.setProperty("variant", "secondary")
    del_btn.clicked.connect(lambda _=False, _id=rec.id: C.delete_saved_plot(ctx, _id))
    actions.addWidget(del_btn)
    v.addLayout(actions)

    return card


def _rename(ctx, plot_id: str, current: str) -> None:
    try:
        new_name, ok = QInputDialog.getText(
            None, "Rename saved plot", "Name:", text=current or "")
    except Exception:
        return
    if ok and new_name.strip():
        C.rename_saved_plot(ctx, plot_id, new_name.strip())
