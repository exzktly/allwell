"""EmptyState — a centered icon + message placeholder for "nothing here yet".

Used where a figure / list / tab has no data: a large muted glyph above a
one-line tip (and an optional secondary line). Token-styled; the icon size
derives from the font (DPI-aware).

API
---
* ``EmptyState(text="", icon="image", parent=None, *, hint="")``
* ``setText(text)`` / ``setHint(text)`` / ``setIconName(name)``
* ``setAction(label, on_click)`` / ``clearAction()`` — an optional button that
  takes the hint's place. Absent unless asked for, so a site that only describes
  its emptiness looks exactly as it did.
* ``setEntries(entries)`` / ``clearEntries()`` — an optional column of secondary
  links *below* the action, each an ``(label, sublabel, on_click, enabled,
  tooltip)``. For a placeholder whose way out is not only "do this" but "or go
  back to one of these". Also absent unless asked for.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)


def entry_row_text(label: str, sublabel: str) -> str:
    """The two lines of a secondary-link row, as one button label.

    Shared with Home's Recent datasets card, which draws the same rows outside
    an ``EmptyState``. It was two formatters — a newline here and three spaces
    there — so one list rendered as two stacked lines in the placeholder and as
    one run-on line on the card, which is the same defect as two stylesheets for
    one control, in the layer above.
    """
    return f"{label}\n{sublabel}" if sublabel else label

import theme
from widgets import icons


class EmptyState(QWidget):
    def __init__(self, text: str = "", icon: str = "image", parent=None,
                 *, hint: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("EmptyState")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._icon_name = icon

        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.Spacing.lg, theme.Spacing.lg,
                                 theme.Spacing.lg, theme.Spacing.lg)
        # Anchor stack at the top with a single bottom stretch — when the
        # host card is short, content grows downward into the stretch
        # instead of overflowing past the card's bottom edge (a vertical
        # centering layout with two stretches squeezes the wrapped hint
        # off-screen when the card is constrained).

        self._glyph = QLabel(self)
        self._glyph.setAlignment(Qt.AlignCenter)
        self._text = QLabel(text, self)
        self._text.setObjectName("EmptyStateText")
        self._text.setAlignment(Qt.AlignCenter)
        self._text.setWordWrap(True)
        self._hint = QLabel(hint, self)
        self._hint.setObjectName("EmptyStateHint")
        self._hint.setAlignment(Qt.AlignCenter)
        self._hint.setWordWrap(True)
        self._hint.setVisible(bool(hint))

        outer.addSpacing(theme.Spacing.lg)
        # Only the fixed-size glyph is horizontally centered. The two word-wrap
        # labels take the FULL width (their text is centered internally): a
        # ``Qt.AlignHCenter`` here would size each label to its one-line sizeHint
        # width, defeating the layout's ``heightForWidth`` so a wrapped second
        # line has no height reserved and overlaps the first (the reported
        # collision on the Multivariate empty state).
        outer.addWidget(self._glyph, 0, Qt.AlignHCenter)
        outer.addSpacing(theme.Spacing.md)
        outer.addWidget(self._text, 0)
        outer.addSpacing(theme.Spacing.xs)
        outer.addWidget(self._hint, 0)
        # Horizontally centred like the glyph rather than stretched: a
        # full-width button on a page whose whole content is three centred
        # lines reads as a banner, not as the one thing to press.
        self._action = QPushButton("", self)
        self._action.setObjectName("EmptyStateAction")
        self._action.setVisible(False)
        self._action_slot = None
        # Whether a hint may stand beside an action; see ``setAction``.
        self._keep_hint = False
        outer.addSpacing(theme.Spacing.md)
        outer.addWidget(self._action, 0, Qt.AlignHCenter)
        # A column of secondary links under the action, empty and hidden until a
        # site sets one. Centred on the same axis as the button rather than
        # stretched, so the page keeps reading as one centred stack; the rows
        # inside it are left-aligned against each other, because a list of names
        # of different lengths centred individually has no edge for the eye to
        # run down.
        self._entries = QWidget(self)
        self._entries.setObjectName("EmptyStateEntries")
        self._entries_layout = QVBoxLayout(self._entries)
        self._entries_layout.setContentsMargins(0, 0, 0, 0)
        self._entries_layout.setSpacing(theme.Spacing.xs)
        self._entries.setVisible(False)
        outer.addSpacing(theme.Spacing.md)
        outer.addWidget(self._entries, 0, Qt.AlignHCenter)
        outer.addStretch(1)
        # Belt-and-suspenders against the same 1-line-sizeHint quirk: reserve a
        # two-line minimum for each wrap label, sized to that label's OWN font
        # (the title renders at the larger h3 size, so the widget's default
        # font metrics would under-reserve it).
        from PySide6.QtGui import QFont, QFontMetrics
        _title_f = QFont(self.font())
        _title_f.setPixelSize(theme.Typography.h3_size)
        self._text.setMinimumHeight(QFontMetrics(_title_f).height() * 2)
        self._hint.setMinimumHeight(self.fontMetrics().height() * 2)

        self.setStyleSheet(self._build_qss())
        self._refresh_glyph()

    # ── API ──────────────────────────────────────────────────────────────
    def setText(self, text: str) -> None:
        self._text.setText(text or "")

    def setHint(self, text: str) -> None:
        self._hint.setText(text or "")
        self._sync_hint_visibility()

    def setIconName(self, name: str) -> None:
        self._icon_name = name
        self._refresh_glyph()

    def setAction(self, label: str, on_click, *, keep_hint: bool = False) -> None:
        """Show a button in place of the hint, replacing any previously set one.

        For a placeholder whose way out is one action the page can perform
        itself. Most sites have no such action — the hint names a control
        somewhere else — and those keep no button at all. The hint's text is
        kept, so ``clearAction`` brings the line back. ARCHITECTURE.md
        § "Empty states" has why the button replaces it rather than joining it.

        *keep_hint* shows both. The default exists because a hint usually points
        at a control **elsewhere**, and printing that under a button which does
        the job right here sends the reader away from the answer. A hint that is
        the *payoff* for pressing this very button is the other case, and it is
        the one this flag is for — opt-in, so no existing placeholder changes.
        """
        if self._action_slot is not None:
            self._action.clicked.disconnect(self._action_slot)
            self._action_slot = None
        self._action.setText(label or "")
        if callable(on_click):
            def _slot(_checked=False, _fn=on_click):
                _fn()
            self._action.clicked.connect(_slot)
            self._action_slot = _slot
        self._keep_hint = bool(keep_hint)
        self._action.setVisible(bool(label))
        self._sync_hint_visibility()

    def setEntries(self, entries) -> None:
        """Show a column of secondary links below the action, replacing any set before.

        *entries* is a sequence of ``(label, sublabel, on_click, enabled,
        tooltip, on_remove)``. Fixed width rather than optional tails: a
        variadic tuple whose meaning depends on its length is harder to read
        than the row it encodes, so carrying the remove callback widened the
        row for every caller rather than making a last field optional. An entry
        with ``enabled=False`` is still **drawn** — greyed, with *tooltip* saying
        why — because the commonest reason a site disables one of these is a
        path on a drive that is not mounted, and dropping the row instead would
        tell the user their history had been lost rather than that their disk is
        elsewhere.

        *on_remove* is a callable, or ``None`` for a row that cannot be taken
        off the list. When it is set the row grows a × revealed on hover, and
        that × is live **even on a disabled row** — an unreachable folder is
        precisely the entry a user wants gone.

        Passing an empty sequence is :meth:`clearEntries`.
        """
        self.clearEntries()
        rows = list(entries or ())
        for label, sublabel, on_click, enabled, tooltip, on_remove in rows:
            self._entries_layout.addWidget(
                self._make_entry(label, sublabel, on_click, enabled, tooltip,
                                 on_remove))
        self._entries.setVisible(bool(rows))

    def entryAt(self, index: int):
        """The clickable row at *index*, whichever shape :meth:`setEntries` gave it.

        A row with a remove callback is a container around the button; one
        without is the button itself. Callers ask here rather than each
        spelling out that difference — and the tests press what this returns,
        so a change of shape cannot leave them driving the wrapper instead of
        the control.
        """
        from widgets.removable_row import row_entry

        item = self._entries_layout.itemAt(index)
        widget = item.widget() if item is not None else None
        return row_entry(widget) if widget is not None else None

    def removeButtonAt(self, index: int):
        """The × on the row at *index*, or ``None`` when the row carries none."""
        from widgets.removable_row import row_remove_button

        item = self._entries_layout.itemAt(index)
        widget = item.widget() if item is not None else None
        return row_remove_button(widget) if widget is not None else None

    def entryCount(self) -> int:
        """How many rows :meth:`setEntries` is currently showing."""
        return self._entries_layout.count()

    def clearEntries(self) -> None:
        while self._entries_layout.count():
            item = self._entries_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # ``setParent(None)`` alone: PySide6 hands ownership back to
                # Python, so the row is freed when the last reference goes.
                # Adding ``deleteLater()`` instead defers the C++ delete to the
                # next event-loop turn, which a test that never spins one never
                # reaches — measured at +60 live widgets over 20 refreshes,
                # against +0 without it.
                widget.setParent(None)
        self._entries.setVisible(False)

    def _make_entry(self, label: str, sublabel: str, on_click,
                    enabled: bool, tooltip: str, on_remove=None) -> QWidget:
        """One row of :meth:`setEntries`.

        A ``QPushButton`` rather than a label with a mouse handler, so it is
        reachable by keyboard and announced as a button, and so being disabled
        is a real widget state — reported to assistive technology and refusing
        the click — rather than a look. Its *colour* when disabled is a theme
        token like every other, in ``theme.link_row_qss``.

        With *on_remove* set the button is wrapped in a row carrying a ×, and
        the returned widget is then the row rather than the button — which is
        why the caller adds what this returns instead of assuming a button.
        """
        button = QPushButton(self._entries)
        button.setObjectName("EmptyStateEntry")
        button.setText(entry_row_text(label, sublabel))
        button.setEnabled(bool(enabled))
        button.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
        if tooltip:
            button.setToolTip(tooltip)
        if callable(on_click) and enabled:
            def _slot(_checked=False, _fn=on_click):
                _fn()
            button.clicked.connect(_slot)
        if not callable(on_remove):
            return button
        from widgets.removable_row import removable_row
        return removable_row(
            button, on_remove, parent=self._entries,
            object_name="EmptyStateEntryRemove",
            tooltip=f"Forget “{label}” — take it off this list. The folder "
                    "itself is not touched.")

    def clearAction(self) -> None:
        if self._action_slot is not None:
            self._action.clicked.disconnect(self._action_slot)
            self._action_slot = None
        self._action.setText("")
        self._action.setVisible(False)
        # Back to the default: a placeholder with no action has no reason to
        # carry the exception ``setAction(keep_hint=True)`` granted it, and a
        # flag left set is a rule that outlives what it was for.
        self._keep_hint = False
        self._sync_hint_visibility()

    def _sync_hint_visibility(self) -> None:
        """Show the hint only when no action button is standing in for it.

        Called from both action setters as well as from ``setHint``, because the
        shared refresher (``ui_helpers._apply_reason``) rewrites the hint on
        every refresh: a site that suppressed the line once at build time would
        have it painted back the first time a dataset was closed.

        Whether an action exists is read off the button's *text*, not off
        ``isVisible()``: a child of a widget that has not been shown yet reports
        itself invisible however it was configured, and a placeholder is built
        long before the page it covers is first painted.

        A site that asked for both keeps both — see :meth:`setAction`.
        """
        self._hint.setVisible(
            bool(self._hint.text())
            and (self._keep_hint or not self._action.text()))

    # ── internals ────────────────────────────────────────────────────────
    def _icon_px(self) -> int:
        return max(32, round(self.fontMetrics().height() * 3.0))

    def _refresh_glyph(self) -> None:
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        px = self._icon_px()
        # Without an explicit fixed size, QLabel with a scaled-pixmap can
        # report a sizeHint larger than the pixmap's logical pixels, which
        # makes the next layout row (the text) overlap the picture above.
        # Pin the label's footprint to the pixmap's logical size and turn
        # off scaledContents so the pixmap renders 1:1.
        self._glyph.setFixedSize(px, px)
        self._glyph.setScaledContents(False)
        # ``text_muted`` rather than ``text_faint``: against the panel surface
        # the faint token measures about 2:1, which on a dark theme reads as an
        # absent glyph rather than a quiet one. This is the only picture on an
        # otherwise empty page.
        self._glyph.setPixmap(
            icons.make_pixmap(self._icon_name, "text_muted", px, dpr or 1.0)
        )

    def showEvent(self, event):
        self._refresh_glyph()
        super().showEvent(event)

    def _build_qss(self) -> str:
        # The hint carries the action — what to click to leave this state — so it
        # cannot be the least legible thing on the page. On ``text_muted`` it
        # measured about 3.2:1 against the panel surface, under the 4.5:1 that
        # 12px text needs. It moves to ``text_secondary``, which puts it on the
        # title's colour, so the hierarchy is carried by size and weight instead:
        # colour alone was doing a job the contrast budget could not afford.
        c, t = theme.Colors, theme.Typography
        return f"""
        #EmptyState {{ background: transparent; }}
        QLabel#EmptyStateText {{
            color: {c.text_secondary};
            font-size: {t.h3_size}px;
            font-weight: {t.semibold};
            background: transparent;
        }}
        QLabel#EmptyStateHint {{
            color: {c.text_secondary};
            font-size: {t.small_size}px;
            background: transparent;
        }}
        /* The optional action, when a site sets one. Same shape as the app's
           other secondary buttons — this is the way out of an empty page, not
           a call to action competing with the message above it. */
        QPushButton#EmptyStateAction {{
            background: {c.panel_elevated};
            border: 1px solid {c.border};
            border-radius: {theme.Radii.sm}px;
            color: {c.text_primary};
            padding: {theme.Spacing.xs}px {theme.Spacing.lg}px;
        }}
        QPushButton#EmptyStateAction:hover {{
            background: {c.hover};
            border-color: {c.border_strong};
        }}
        /* The secondary links — one definition, shared with Home's Recent
           datasets card; see theme.link_row_qss. */
        QWidget#EmptyStateEntries {{ background: transparent; }}
        {theme.link_row_qss("EmptyStateEntry")}
        {theme.row_remove_qss("EmptyStateEntryRemove")}
        """


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    es = EmptyState(
        "No wells or well groups selected",
        icon="grid",
        hint="Select wells on the plate, or define a group, to plot.",
    )
    es.setWindowTitle("EmptyState — demo")
    es.resize(480, 360)
    es.show()
    _sys.exit(app.exec())
