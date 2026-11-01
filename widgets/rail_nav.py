"""RailNav — vertical accent-bar navigation for the left rail.

Mockup target: the eight section items (Plotting / smFISH / Statistics /
Image Table / Segmentation / Review CSV / Home / Batch Export)
between the rail's mode-seg and the plate. Each row carries a leading
Lucide icon + label + optional trailing count chip. The active row has a
``--accent-dim`` background **and** a 2-px left accent bar drawn via
``::before`` in the mockup CSS — here we paint that as a ``QLabel`` strip
inside the row widget so QSS handles it without a custom ``paintEvent``.

One-of-N selection model. Emits ``currentChanged(key)`` when the active row
changes (either by user click or by ``setCurrentKey``). Items carry an
opaque ``key`` (string by default — caller's choice) for routing to the
corresponding ``QStackedWidget`` page.

API
---
* ``RailNav(parent=None)``
* ``addItem(label, *, icon=None, key=None, count=None) -> row``
  (returns the row widget for further tweaks; ``key`` defaults to the label.)
* ``setCurrentKey(key)`` / ``currentKey() -> str | None``
* ``setCount(key, n | None)`` — show or hide the trailing count chip.
* ``setItemEnabled(key, bool)`` — disable a row (greyed, click-inert).
* ``items() -> list[str]`` — the keys in order.
* ``addCaption(text)`` — a group heading above the rows added after it. Not an
  item: it has no key, is never current, and hides itself while every row in
  its group is hidden.
* signal ``currentChanged(key)``
* ``bindingAdapter()`` — returns ``(getter, setter, signal)``.

Note: the Q8 locked decision drops count asides for the v1 nav rows, but
the API stays in place for future use.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from ui import theme
from widgets import icons
from widgets.selection_chip import SelectionChip


#: The active row's accent strip is never thinner than this, whatever the font.
_STRIP_MIN_PX = 2


class _RailNavRow(QFrame):
    """One item in the RailNav.

    Carries the accent-bar strip on the left + icon + label + optional
    trailing count chip. Click handling is done at the row level so the
    hit target is the whole strip, not just the label.
    """

    clicked = Signal(str)

    def __init__(self, key: str, label: str, *, icon: str | None,
                 parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("RailNavRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setProperty("active", False)
        self.setCursor(Qt.PointingHandCursor)
        self._key = key
        self._icon_name = icon

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 2-px accent strip on the left edge — hidden by default; shown via
        # property selector ``[active="true"]``.
        self._strip = QFrame(self)
        self._strip.setObjectName("RailNavRowStrip")
        self._strip.setAttribute(Qt.WA_StyledBackground, True)
        self._strip.setProperty("active", False)
        # ~13% of body height (2px at 1×, scales on hi-dpi); architecture §7
        # mandates fontMetrics-derived sizes.
        self._strip.setFixedWidth(max(_STRIP_MIN_PX, round(self.fontMetrics().height() * 0.13)))
        outer.addWidget(self._strip, 0)

        body = QFrame(self)
        body.setObjectName("RailNavRowBody")
        body.setAttribute(Qt.WA_StyledBackground, True)
        body.setAttribute(Qt.WA_Hover, True)
        body.setProperty("active", False)
        self._body = body
        bl = QHBoxLayout(body)
        _pad_x, _pad_y = theme.Spacing.md, theme.Spacing.sm
        bl.setContentsMargins(_pad_x, _pad_y, _pad_x, _pad_y)
        bl.setSpacing(theme.Spacing.sm)

        self._glyph = QLabel(body)
        self._glyph.setObjectName("RailNavRowGlyph")
        # ~14× viewer text height — was 15px hardcoded; scales with font.
        # Kept on the row: the *ink* below has to be drawn at the same size as
        # the box reserved here, and it used to be a hardcoded 15 in a box that
        # follows the font. At the default that is a 15px glyph in a 14px box;
        # at 150% font scaling the box grows and the glyph does not. The sweep
        # that made this box font-derived converted the reservation and left
        # the ink behind.
        # Measured off the font the row is drawn in — the stylesheet's, so the
        # row is polished first. Measured before the polish, the box was a
        # pixel smaller than ``_aw_rescale`` makes it off the same font, and a
        # round trip through phone layout changed it (15 px → 16).
        self.ensurePolished()
        _gs = max(12, round(self.fontMetrics().height() * 1.0))
        self._glyph_px = _gs
        self._glyph.setFixedSize(_gs, _gs)
        self._glyph.setVisible(bool(icon))

        self._label = QLabel(label, body)
        self._label.setObjectName("RailNavRowLabel")
        self._label.setProperty("active", False)
        self._label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self._chip = SelectionChip("", variant="muted", parent=body)
        self._chip.setVisible(False)

        bl.addWidget(self._glyph, 0, Qt.AlignVCenter)
        bl.addWidget(self._label, 1, Qt.AlignVCenter)
        bl.addWidget(self._chip, 0, Qt.AlignVCenter)

        outer.addWidget(body, 1)

    def key(self) -> str:
        return self._key

    def setActive(self, on: bool) -> None:
        self.setProperty("active", bool(on))
        # Mirror the property onto the body widget so QSS rules targeting
        # ``QFrame#RailNavRowBody[active="true"]`` resolve. Qt QSS's
        # descendant + property combinator is unreliable across platforms;
        # carrying the property on the body itself avoids that.
        self._body.setProperty("active", bool(on))
        # Carry the property on the strip too — the descendant +
        # property combinator (``QFrame#RailNavRow[active="true"]
        # QFrame#RailNavRowStrip``) doesn't re-evaluate reliably when
        # the parent's property flips, leaving the previous accent bar
        # painted on the first-clicked row.
        self._strip.setProperty("active", bool(on))
        # And on the label, for the same reason, with its own rule rather than
        # ``QFrame#RailNavRow[active="true"] QLabel#RailNavRowLabel``. That
        # descendant rule is resolved when the *label* is polished, and only the
        # row, body and strip were repolished here — so the label kept the
        # colour it was first polished with. The first row added is made active
        # by ``addItem``, so the first entry of every rail (Home in Review, the
        # first tool in Datasets) stayed at the active colour whatever was
        # selected, and the row actually selected never brightened.
        self._label.setProperty("active", bool(on))
        for w in (self, self._body, self._strip, self._label):
            w.style().unpolish(w)
            w.style().polish(w)
        self._refresh_icon()

    def setCount(self, n: int | None) -> None:
        if n is None:
            self._chip.setVisible(False)
            return
        self._chip.setText(str(int(n)))
        self._chip.setVisible(True)

    def setIconName(self, name: str | None) -> None:
        self._icon_name = name
        self._glyph.setVisible(bool(name))
        self._refresh_icon()

    def _aw_rescale(self) -> None:
        """Re-size the glyph box and ink off the rescaled font (24 pt on a
        phone at the Medium size) after a UI scale."""
        self._glyph_px = max(12, round(self.fontMetrics().height() * 1.0))
        self._glyph.setFixedSize(self._glyph_px, self._glyph_px)
        self._refresh_icon()

    def _refresh_icon(self) -> None:
        if not self._icon_name:
            return
        active = bool(self.property("active"))
        token = "accent" if active else "text_muted"
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        self._glyph.setPixmap(
            icons.make_pixmap(self._icon_name, token, self._glyph_px, dpr or 1.0)
        )

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and self.isEnabled():
            self.clicked.emit(self._key)
        super().mousePressEvent(ev)

    def showEvent(self, ev):
        self._refresh_icon()
        super().showEvent(ev)


class _RailNavCaption(QLabel):
    """A group heading in the RailNav — muted small caps, aligned with the
    row labels below it."""

    def __init__(self, text: str, parent: QWidget) -> None:
        super().__init__(text.upper(), parent)
        self.setObjectName("RailNavCaption")
        self._group_text = text


class RailNav(QFrame):
    currentChanged = Signal(str)
    #: A row the *user* clicked — including the current one. Unlike
    #: ``currentChanged`` it does not fire for navigation done by code, which
    #: is what lets a host react to a tap (phone layout closes its drawer).
    itemClicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RailNav")
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(1)
        # Never taller than its rows need. A nav is a list of links, not a
        # region to fill: nothing here has a reason to grow, and the rows
        # carry no height of their own, so surplus height would be divided
        # among them and the list would visibly spread apart.
        #
        # This is a property of the widget rather than of any one host,
        # because the hosts do not agree. The Datasets rail follows the nav
        # with its own ``addStretch(1)``; Review's rail relies on whichever
        # per-page frame beneath it happens to be visible, and Home shows
        # none — so navigating to Home left the nav as the only growable item
        # in the column and its rows went from 33px to 81px. Fixing that in
        # one host would leave the next host to rediscover it.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        self._rows: dict[str, _RailNavRow] = {}
        self._order: list[str] = []
        self._current: str | None = None
        # Rows and captions in the order they were added: what ``setColumns``
        # lays out, and how a caption knows which rows are its group.
        self._entries: list[QWidget] = []

        self.setStyleSheet(self._build_qss())

    # ── API ──────────────────────────────────────────────────────────────
    def addItem(self, label: str, *, icon: str | None = None,
                key: str | None = None, count: int | None = None) -> _RailNavRow:
        k = key if key is not None else label
        if k in self._rows:
            raise ValueError(f"RailNav already has key {k!r}")
        row = _RailNavRow(k, label, icon=icon, parent=self)
        row.clicked.connect(self._on_row_clicked)
        if count is not None:
            row.setCount(count)
        self._rows[k] = row
        self._order.append(k)
        self._entries.append(row)
        self._place(row)
        row.installEventFilter(self)
        self._sync_captions()
        if self._current is None:
            self._set_current_internal(k, emit=False)
        return row

    def addCaption(self, text: str) -> QLabel:
        """Head the rows added after this call with a group caption.

        A caption is not an item — it has no key and can never be current — so
        ``items()`` and the selection model are untouched. It hides itself
        while every row in its group is hidden (phone layout hides some), so a
        heading never stands over nothing.
        """
        cap = _RailNavCaption(text, self)
        self._entries.append(cap)
        self._place(cap)
        self._sync_captions()
        return cap

    def captions(self) -> list[str]:
        """The group captions in order, as given to :meth:`addCaption`."""
        return [e._group_text for e in self._entries if isinstance(e, _RailNavCaption)]

    def eventFilter(self, obj, ev):
        # A row shown or hidden by its host (phone layout hides two) may empty
        # or refill its group.
        if ev.type() in (QEvent.ShowToParent, QEvent.HideToParent) and obj in self._entries:
            self._sync_captions()
        return super().eventFilter(obj, ev)

    def _groups(self) -> list[tuple[QLabel | None, list[_RailNavRow]]]:
        """The entries split at each caption: ``(caption or None, its rows)``."""
        out: list[tuple[QLabel | None, list[_RailNavRow]]] = [(None, [])]
        for e in self._entries:
            if isinstance(e, _RailNavCaption):
                out.append((e, []))
            else:
                out[-1][1].append(e)
        return out

    def _sync_captions(self) -> None:
        for cap, rows in self._groups():
            if cap is not None:
                cap.setVisible(any(not r.isHidden() for r in rows))

    def _place(self, w: QWidget) -> None:
        """Append *w* to the current layout: a list row, or the next grid cell
        (a caption takes a grid row of its own, across every column)."""
        n = getattr(self, "_columns", 1)
        if n == 1:
            self._lay.addWidget(w)
            return
        self._relayout_grid(n)

    def setCurrentKey(self, key: str | None) -> None:
        if key is None or key == self._current:
            return
        if key not in self._rows:
            return
        self._set_current_internal(key, emit=True)

    def currentKey(self) -> str | None:
        return self._current

    def setCount(self, key: str, n: int | None) -> None:
        row = self._rows.get(key)
        if row is not None:
            row.setCount(n)

    def setItemEnabled(self, key: str, enabled: bool) -> None:
        row = self._rows.get(key)
        if row is not None:
            row.setEnabled(bool(enabled))

    def setColumns(self, n: int) -> None:
        """Lay the rows out in *n* columns (1, the default, is the rail's list).

        Phone layout's side rail uses two: nine sections as a column of 48-pt
        rows fill the rail before the well plate is reached, and as a grid of
        tiles they take half that. The rows are moved, not rebuilt, so their
        state and connections are untouched.
        """
        n = max(1, int(n))
        if n == getattr(self, "_columns", 1):
            return
        self._columns = n
        if n == 1:
            lay = self._fresh_layout(QVBoxLayout)
            for e in self._entries:
                lay.addWidget(e)
        else:
            self._relayout_grid(n)
        self._sync_captions()

    def _fresh_layout(self, kind):
        """Detach every entry from the current layout and install a new
        *kind* of layout in its place."""
        old = self.layout()
        for e in self._entries:
            old.removeWidget(e)
        holder = QWidget()
        holder.setLayout(old)          # re-home the old layout so it can die
        holder.deleteLater()
        lay = kind(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        self._lay = lay
        return lay

    def _relayout_grid(self, n: int) -> None:
        """Lay every entry out as an *n*-column grid. Each caption starts a
        grid row of its own spanning every column, and its group's tiles
        follow from the first column; within a group, shown rows come first,
        so a hidden one leaves no hole in the grid."""
        from PySide6.QtWidgets import QGridLayout
        lay = self._fresh_layout(QGridLayout)
        r = 0
        for cap, rows in self._groups():
            if cap is not None:
                lay.addWidget(cap, r, 0, 1, n)
                r += 1
            ordered = ([w for w in rows if not w.isHidden()]
                       + [w for w in rows if w.isHidden()])
            for i, w in enumerate(ordered):
                lay.addWidget(w, r + i // n, i % n)
            r += (len(ordered) + n - 1) // n

    def items(self) -> list[str]:
        return list(self._order)

    # ── binding adapter ──────────────────────────────────────────────────
    def bindingAdapter(self):
        return (self.currentKey, self.setCurrentKey, self.currentChanged)

    # ── internals ────────────────────────────────────────────────────────
    def _on_row_clicked(self, key: str) -> None:
        if key != self._current:
            self._set_current_internal(key, emit=True)
        self.itemClicked.emit(key)

    def _set_current_internal(self, key: str, *, emit: bool) -> None:
        prev = self._current
        if prev is not None:
            prev_row = self._rows.get(prev)
            if prev_row is not None:
                prev_row.setActive(False)
        self._current = key
        self._rows[key].setActive(True)
        if emit:
            self.currentChanged.emit(key)

    def _build_qss(self) -> str:
        c, t, r = theme.Colors, theme.Typography, theme.Radii
        return f"""
        QFrame#RailNav {{
            background: transparent;
            border: 0;
        }}
        QFrame#RailNavRow {{
            background: transparent;
        }}
        QFrame#RailNavRow:disabled {{ color: {c.text_faint}; }}
        QFrame#RailNavRowStrip {{
            background: transparent;
            border: 0;
        }}
        QFrame#RailNavRowStrip[active="true"] {{
            background-color: {c.accent};
            border-top-right-radius: 2px;
            border-bottom-right-radius: 2px;
        }}
        QFrame#RailNavRowBody {{
            background-color: transparent;
            border-radius: {r.sm}px;
            margin: 0 4px 0 6px;
        }}
        QFrame#RailNavRowBody:hover {{
            background-color: {c.panel};
        }}
        QFrame#RailNavRowBody[active="true"] {{
            background-color: {c.accent_dim};
        }}
        QFrame#RailNavRowBody[active="true"]:hover {{
            background-color: {c.accent_dim};
        }}
        QLabel#RailNavRowLabel {{
            color: {c.text_secondary};
            font-family: {t.family};
            font-size: {t.emph_size}px;
            font-weight: 500;
            background: transparent;
        }}
        /* No `:hover` rule for the label, deliberately. Qt resolves a
           *descendant* rule by baking its colour into the label's stylesheet
           palette, and a palette has no notion of ":hover" — so
           `QFrame#RailNavRow:hover QLabel#RailNavRowLabel` applies always,
           whatever else is written beside it, and overrides the
           `text_secondary` base above. Every row then drew at full strength
           and the active one was not distinguished from the rest, which is the
           whole job of this rail. Measured (Qt 6.7.3, inactive/active): the
           [active] rule alone gives #98a2b3 / #e6e9ef; adding the :hover rule
           gives #e6e9ef / #e6e9ef whether the two are one comma group or two
           separate rules — splitting the group changes nothing, which is why
           the rule is gone rather than rewritten.

           Hover is still signalled, by `QFrame#RailNavRowBody:hover` above:
           that one styles the widget it names rather than a descendant of it,
           so the pseudo-state resolves normally. */
        QLabel#RailNavRowLabel[active="true"] {{
            color: {c.text_primary};
        }}
        /* A group caption: the row label's left edge (the 6px body margin,
           the accent strip and the body's own md padding), muted caption type,
           a gap above it that separates the group from the one before. */
        QLabel#RailNavCaption {{
            color: {c.text_muted};
            font-family: {t.family};
            font-size: {t.caption_size}px;
            font-weight: 600;
            letter-spacing: 0.08em;
            background: transparent;
            padding: {theme.Spacing.sm}px 0 {theme.Spacing.xs}px {theme.Spacing.md + 8}px;
        }}
        """


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    host = QWidget()
    host.setObjectName("Rail")
    host.setStyleSheet(f"#Rail {{ background-color: {theme.Colors.rail}; }}")
    host.setWindowTitle("RailNav — demo")
    host.resize(280, 480)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(8, 12, 8, 12)
    lay.setSpacing(8)

    head = QLabel("SECTION", host)
    head.setStyleSheet(
        f"color: {theme.Colors.text_muted}; font-size: {theme.Typography.caption_size}px; "
        f"letter-spacing: 0.08em; font-weight: 600;"
    )
    head.setContentsMargins(9, 0, 0, 0)
    lay.addWidget(head)

    nav = RailNav(host)
    nav.addItem("Plotting",          icon="line-chart")
    nav.addItem("smFISH",            icon="dna")
    nav.addItem("Statistics",        icon="sigma")
    nav.addItem("Image Table",       icon="layout-grid")
    nav.addItem("Segmentation",      icon="scan-line")
    nav.addItem("Review CSV",        icon="file-spreadsheet")
    nav.addItem("Batch Export",icon="boxes")
    nav.addItem("Batch Export",      icon="boxes")
    lay.addWidget(nav)
    lay.addStretch(1)

    out = QLabel("Active: Plotting", host)
    out.setStyleSheet(f"color: {theme.Colors.text_muted}; padding: 0 9px;")
    nav.currentChanged.connect(lambda k: out.setText(f"Active: {k}"))
    lay.addWidget(out)

    host.show()
    _sys.exit(app.exec())
