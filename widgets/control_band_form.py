"""A control band laid out as a form: one column, grouped, labelled alike.

A :class:`~widgets.control_band.ControlBand` is a row: scope controls flowing
from the left, actions pinned right. That is right for a wide pane and wrong for
a narrow sheet, where a flow wraps wherever a label happens to end and leaves
no edge to scan down. :meth:`ControlBand.setForm` hands the band to
:class:`BandForm`, which moves the *same* widgets — nothing is rebuilt, nothing
is copied, so every connection and every later ``setVisible`` / ``setText`` a
page makes still lands — into three sections, in a fixed order (``SECTIONS``):
the two button grids, Display then Actions, short on every page and so always
in view when the sheet opens, then Data, which scrolls.

* **Data** — every scope control (the band's own definition of scope: *what
  data is shown*). One control per row: its label in a fixed-width column on
  the left, the control stretched over the rest. A label is found wherever the
  band's builders put it — the caption ``add_scope`` bundled with the control,
  a ``LabeledComboBox``'s painted prefix, a button whose text reads
  "Timepoints: 1/3 selected" — and moved into the column, so the control shows
  only its value. A self-describing control (a checkbox, a segmented control
  added without a label) spans the row. A standalone label in the scope (a
  hint such as "Plots draw one member per selected well.") is a caption at the
  top of the section, not a row.
* **Display** — actions that open a panel of live options
  (``make_popover_button``: Display ▾, Fold change ▾, Decorate ▾, Fit ▾).
* **Actions** — everything else the action zone holds: commands (Save ▾, Reset
  Order, Run test), the Properties toggle, a checkbox.

Display and Actions are grids of equal-width buttons, enabled ones first. Which
of the two an action joins is read off the ``bandRole`` property its builder
sets (``"display"`` or ``"action"``); with none, a button that drops a menu or
panel is Display and anything else Actions. An icon-only button gets its
``sheetText`` property (or the first clause of its tooltip) as its text, beside
its icon, while it is in the form.

The page keeps owning visibility: a row, a grid cell and a section title follow
the visibility of the widget the page shows and hides (the bundle ``add_scope``
made, or the control itself) through :class:`_Mirror`, so a leaf's controls
coming and going (``ControlBand.show_slot``) reshape the form.

Sizes come from the caller (label column, row height, gap) — this module knows
nothing of phone layout. No All-Well coupling.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QAbstractButton, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel,
    QSizePolicy, QStyle, QStyleOptionButton, QStyleOptionToolButton,
    QStylePainter, QToolButton, QVBoxLayout, QWidget,
)

#: The sections, in the order they are laid out. The button grids come first:
#: they are a row or two whatever the page, while Data runs as long as the page
#: has controls — a dozen rows on Nanowells — and grids laid out after it sat
#: below the fold, where Decorate was reported as missing from the page.
SECTIONS = ("Display", "Actions", "Data")
#: Property a builder sets on an action to say which section it joins.
ROLE_PROPERTY = "bandRole"
ROLE_SECTION = {"display": "Display", "action": "Actions"}
#: Property naming the text an icon-only button shows in the form.
SHEET_TEXT_PROPERTY = "sheetText"
#: Most buttons in one grid row.
GRID_COLUMNS = 3
#: A button whose text carries its own label: "Timepoints: 1/3 selected".
_LABELLED_TEXT = re.compile(r"^([^:▾\n]{1,28}): (.+)$", re.S)
#: The fewest characters a combo is let shrink to in a row.
_COMBO_MIN_CHARS = 6
#: Qt's QWIDGETSIZE_MAX — what lifts a width pin.
_QWIDGETSIZE_MAX = 16_777_215


def split_labelled_text(text: str) -> tuple[str, str] | None:
    """``("Timepoints", "1/3 selected")`` for "Timepoints: 1/3 selected", or
    ``None`` for text that carries no label of its own."""
    m = _LABELLED_TEXT.match(text or "")
    return (m.group(1).strip(), m.group(2)) if m else None


def section_of_action(w: QWidget) -> str:
    """Display or Actions, for a widget from the band's action zone."""
    role = w.property(ROLE_PROPERTY)
    if role in ROLE_SECTION:
        return ROLE_SECTION[role]
    if isinstance(w, QToolButton) and (
            w.menu() is not None or w.popupMode() == QToolButton.InstantPopup):
        return "Display"
    return "Actions"


def sheet_text(w: QWidget) -> str:
    """What an icon-only button reads in the form: its ``sheetText``, else the
    first clause of its tooltip ("Show / hide the figure properties panel" ->
    that whole clause is long, which is why builders set ``sheetText``)."""
    named = w.property(SHEET_TEXT_PROPERTY)
    if named:
        return str(named)
    tip = (w.toolTip() or "").strip()
    for stop in (". ", " — ", " (", "\n"):
        tip = tip.split(stop, 1)[0]
    return tip


class _ValueOnly(QObject):
    """Paints a "Label: value" button as just "value" — its label is in the
    row's label column. Only the painting changes: ``text()`` and every later
    ``setText`` the page makes stay whole, so the next paint follows them."""

    def __init__(self, button: QAbstractButton, label: str) -> None:
        super().__init__(button)
        self._prefix = f"{label}: "

    def value(self, text: str) -> str:
        return text[len(self._prefix):] if text.startswith(self._prefix) else text

    def eventFilter(self, obj, ev) -> bool:  # Qt naming
        if not isinstance(ev, QEvent) or ev.type() != QEvent.Paint:
            return False
        painter = QStylePainter(obj)
        if isinstance(obj, QToolButton):
            opt = QStyleOptionToolButton()
            obj.initStyleOption(opt)
            opt.text = self.value(obj.text())
            painter.drawComplexControl(QStyle.CC_ToolButton, opt)
        else:
            opt = QStyleOptionButton()
            obj.initStyleOption(opt)
            opt.text = self.value(obj.text())
            painter.drawControl(QStyle.CE_PushButton, opt)
        return True


class _Mirror(QObject):
    """Follows the page's show / hide (and enable) of one item and tells the
    form, which re-shapes the row, cell or section it sits in."""

    def __init__(self, item: QWidget, on_change) -> None:
        super().__init__(item)
        self._on_change = on_change

    def eventFilter(self, obj, ev) -> bool:  # Qt naming
        if isinstance(ev, QEvent) and ev.type() in (
                QEvent.ShowToParent, QEvent.HideToParent, QEvent.EnabledChange,
                QEvent.Resize):
            self._on_change()
        return False


class BandForm:
    """The form one band is laid out as while it is in a narrow sheet. Built by
    ``ControlBand.setForm(True)``, undone by :meth:`undo`."""

    def __init__(self, host: QWidget, scope: list[QWidget], actions: list[QWidget], *,
                 label_width: int, row_height: int, gap: int) -> None:
        self.host = host
        self._label_width = label_width
        self._row_height = row_height
        self._gap = gap
        self._undo: list = []          # callables, run in reverse by undo()
        self._mirrors: list[_Mirror] = []
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(gap)
        self._sections: dict[str, dict] = {}
        for name in SECTIONS:
            self._sections[name] = self._section(name)
        for item in scope:
            self._add_scope(item)
        for item in actions:
            self._sections[section_of_action(item)]["buttons"].append(item)
            self._adopt_button(item)
        self._watch(host)               # a new width may want fewer columns
        for name in ("Display", "Actions"):
            self._pack(name)
        self.sync()

    # ── building ────────────────────────────────────────────────────────────
    def _section(self, name: str) -> dict:
        box = QWidget(self.host)
        box.setObjectName("ControlSheetSection")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(self._gap)
        title = QLabel(name.upper(), box)
        title.setObjectName("ControlSheetSectionTitle")
        lay.addWidget(title)
        captions = QVBoxLayout()
        captions.setSpacing(0)
        lay.addLayout(captions)
        if name == "Data":
            body = QVBoxLayout()
            body.setSpacing(self._gap)
        else:
            body = QGridLayout()
            body.setHorizontalSpacing(self._gap)
            body.setVerticalSpacing(self._gap)
        lay.addLayout(body)
        self.host.layout().addWidget(box)
        return {"box": box, "title": title, "captions": captions, "body": body,
                "rows": [], "buttons": [], "hints": []}

    def _watch(self, item: QWidget) -> None:
        mirror = _Mirror(item, self.sync)
        item.installEventFilter(mirror)
        self._mirrors.append(mirror)

    def _add_scope(self, item: QWidget) -> None:
        from widgets.flow_layout import FLOW_BREAK

        if item.property(FLOW_BREAK):        # a row's line break: a form is one row per control already
            return
        sec = self._sections["Data"]
        hidden = item.isHidden()
        if isinstance(item, QLabel):        # a hint: a caption, not a row
            wrap = item.wordWrap()
            item.setWordWrap(True)
            item.setProperty("sheetHint", True)
            self._repolish(item)
            sec["captions"].addWidget(item)
            item.setHidden(hidden)
            sec["hints"].append(item)
            self._undo.append(lambda i=item, w=wrap: (
                i.setWordWrap(w), i.setProperty("sheetHint", None), self._repolish(i)))
            self._watch(item)
            return
        label = self._take_label(item)
        row = QWidget(sec["box"])
        row.setObjectName("ControlSheetRow")
        row.setMinimumHeight(self._row_height)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(self._gap)
        if isinstance(label, QLabel):       # the page's own caption, moved
            self._seat_caption(label, row, rl)
        elif label:
            cap = QLabel(label, row)
            cap.setObjectName("ControlSheetLabel")
            cap.setWordWrap(True)
            cap.setFixedWidth(self._label_width)
            rl.addWidget(cap, 0, Qt.AlignVCenter)
        self._stretch(item)
        if item.layout() is not None:
            for child in item.findChildren(QWidget, options=Qt.FindDirectChildrenOnly):
                if not isinstance(child, QLabel):
                    self._stretch(child)
        self._let_combos_shrink(item)
        rl.addWidget(item, 1)
        item.setHidden(hidden)
        sec["body"].addWidget(row)
        sec["rows"].append((row, item))
        self._watch(item)

    def _seat_caption(self, cap: QLabel, row: QWidget, rl: QHBoxLayout) -> None:
        """Move a page's own caption label into the row's label column — the
        label itself, so a page that rewrites it ("ON threshold" -> "ON spots")
        is still read."""
        group = cap.parentWidget()
        glay = group.layout()
        index = glay.indexOf(cap)
        wrap, lo, hi = cap.wordWrap(), cap.minimumWidth(), cap.maximumWidth()
        hidden = cap.isHidden()
        glay.removeWidget(cap)
        cap.setParent(row)
        cap.setWordWrap(True)
        cap.setFixedWidth(self._label_width)
        cap.setProperty("sheetLabel", True)
        self._repolish(cap)
        rl.addWidget(cap, 0, Qt.AlignVCenter)
        cap.setHidden(hidden)

        def _home(c=cap, g=group, gl=glay, i=index, w=wrap, a=lo, b=hi):
            h = c.isHidden()
            c.setParent(g)
            gl.insertWidget(i, c)
            c.setHidden(h)
            c.setWordWrap(w)
            c.setMinimumWidth(a)
            c.setMaximumWidth(b)
            c.setProperty("sheetLabel", None)
            self._repolish(c)
        self._undo.append(_home)

    def _take_label(self, item: QWidget) -> str | QLabel:
        """The item's label, moved out of the item: a bundled caption moved
        into the label column (returned as the label itself), a combo's painted
        prefix dropped, a button painted as its value only. ``""`` for an item
        that describes itself."""
        lay = item.layout()
        if isinstance(lay, QHBoxLayout):
            first = lay.itemAt(0).widget() if lay.count() else None
            # The caption ``add_scope`` bundled, or one a page bundled itself
            # (a row that starts with a label and holds a control after it).
            if isinstance(first, QLabel) and lay.count() > 1 and first.text():
                stretches = [lay.stretch(i) for i in range(lay.count())]
                for i in range(1, lay.count()):
                    lay.setStretch(i, 1)
                self._undo.append(lambda lo=lay, s=stretches: [
                    lo.setStretch(i, v) for i, v in enumerate(s) if i < lo.count()])
                return first
            return ""
        if hasattr(item, "setLabelShown") and hasattr(item, "label") and item.label():
            item.setLabelShown(False)
            self._undo.append(lambda i=item: i.setLabelShown(True))
            return item.label()
        if isinstance(item, QAbstractButton) and not isinstance(item, QCheckBox):
            parts = split_labelled_text(item.text())
            if parts is not None:
                painter = _ValueOnly(item, parts[0])
                item.installEventFilter(painter)
                item.setProperty("sheetValue", True)
                self._repolish(item)
                item.update()
                self._undo.append(lambda i=item, f=painter: (
                    i.removeEventFilter(f), f.deleteLater(),
                    i.setProperty("sheetValue", None), self._repolish(i), i.update()))
                return parts[0]
        return ""

    def _stretch(self, w: QWidget) -> None:
        """Let *w* fill the row's control column: expanding, and with a width
        pin lifted (a touch stepper pins its width), both given back after."""
        policy = w.sizePolicy()
        w.setSizePolicy(QSizePolicy.Expanding, policy.verticalPolicy())
        self._undo.append(lambda i=w, p=policy: i.setSizePolicy(p))
        hi = w.maximumWidth()
        if 0 < w.minimumWidth() == hi:
            w.setMaximumWidth(_QWIDGETSIZE_MAX)
            self._undo.append(lambda i=w, m=hi: i.setMaximumWidth(m))

    def _let_combos_shrink(self, item: QWidget) -> None:
        """A combo sized to its longest entry holds a narrow row wider than the
        sheet; in a row it shrinks to a few characters and elides instead.
        A combo's own chip styling gives way to the application's for the
        length of the form, so every combo in it reads alike."""
        combos = [item] if isinstance(item, QComboBox) else item.findChildren(QComboBox)
        for c in combos:
            # A combo styled as a chip of its own (the Plotting band's Channel
            # and Property: caption-sized pills) is drawn like every other
            # combo in the form, by the application's sheet.
            sheet = c.styleSheet()
            if sheet:
                c.setStyleSheet("")
                self._undo.append(lambda c=c, q=sheet: c.setStyleSheet(q))
            pol, n = c.sizeAdjustPolicy(), c.minimumContentsLength()
            c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            c.setMinimumContentsLength(_COMBO_MIN_CHARS)
            self._undo.append(lambda c=c, p=pol, n=n: (
                c.setSizeAdjustPolicy(p), c.setMinimumContentsLength(n)))

    def _adopt_button(self, w: QWidget) -> None:
        # Fills its cell both ways, so every cell in a row is one size; the
        # grid's rows are at least a touch row tall (``_pack``).
        policy = w.sizePolicy()
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._undo.append(lambda i=w, p=policy: i.setSizePolicy(p))
        if isinstance(w, QToolButton) and not w.text():
            style = w.toolButtonStyle()
            w.setText(sheet_text(w))
            w.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            self._undo.append(lambda i=w, s=style: (i.setText(""), i.setToolButtonStyle(s)))
        if isinstance(w, QToolButton) and w.objectName() == "IconButton":
            # A flat icon button (``IconButton``, the Properties toggle) carries
            # its own transparent stylesheet, which would leave it the one cell
            # in the grid without a button's fill and edge. In the sheet it
            # takes the application's button style like its neighbours; the
            # band gets its own look back when the form is undone.
            own = w.styleSheet()
            w.setStyleSheet("")
            self._undo.append(lambda i=w, s=own: i.setStyleSheet(s))
        self._watch(w)

    @staticmethod
    def _repolish(w: QWidget) -> None:
        w.style().unpolish(w)
        w.style().polish(w)

    # ── following the page ──────────────────────────────────────────────────
    def _pack(self, name: str) -> None:
        """Lay a button section out as equal-width cells, shown buttons only,
        enabled first: a button that cannot be pressed now is not left between
        two that can."""
        sec = self._sections[name]
        grid: QGridLayout = sec["body"]
        shown = [w for w in sec["buttons"] if not w.isHidden()]
        shown.sort(key=lambda w: not w.isEnabled())
        cols = self._columns(shown)
        if sec.get("packed") == (cols, [id(w) for w in shown]):
            return                      # already laid out like this
        sec["packed"] = (cols, [id(w) for w in shown])
        for w in sec["buttons"]:
            hidden = w.isHidden()
            grid.removeWidget(w)
            w.setParent(sec["box"])
            w.setHidden(hidden)
        for c in range(GRID_COLUMNS):
            grid.setColumnStretch(c, 1 if c < cols else 0)
        for i, w in enumerate(shown):
            grid.addWidget(w, i // cols, i % cols)
        rows = (len(shown) + cols - 1) // cols
        for r in range(max(rows, grid.rowCount())):
            # An emptied row keeps no height: a grid reserves a minimum even
            # for a row with nothing left in it.
            grid.setRowMinimumHeight(r, self._row_height if r < rows else 0)

    def _columns(self, shown: list[QWidget]) -> int:
        """Up to ``GRID_COLUMNS`` equal cells a row — two rows of two rather
        than three and one — and fewer when the widest button would not fit
        its cell at the form's width, so a long label never pushes the grid
        wider than the sheet."""
        n = len(shown)
        cols = 2 if n == 4 else max(1, min(GRID_COLUMNS, n))
        width = self.host.width()
        if width > 0 and shown:
            widest = max(w.sizeHint().width() for w in shown)
            while cols > 1 and cols * widest + (cols - 1) * self._gap > width:
                cols -= 1
        return cols

    def sync(self) -> None:
        """Match every row and section title to what the page shows now."""
        if self._packing:
            return
        self._packing = True
        try:
            for name, sec in self._sections.items():
                for row, item in sec["rows"]:
                    row.setVisible(not item.isHidden())
                if name != "Data":
                    self._pack(name)
                any_shown = (any(not i.isHidden() for _r, i in sec["rows"])
                             or any(not w.isHidden() for w in sec["buttons"])
                             or any(not h.isHidden() for h in sec["hints"]))
                sec["box"].setVisible(any_shown)
        finally:
            self._packing = False

    _packing = False

    def rows(self, section: str) -> list[tuple[QWidget, QWidget]]:
        """``(row, item)`` for every Data row — for tests and the shell."""
        return list(self._sections[section]["rows"])

    def section_box(self, section: str) -> QWidget:
        return self._sections[section]["box"]

    def buttons(self, section: str) -> list[QWidget]:
        return list(self._sections[section]["buttons"])

    def hints(self) -> list[QLabel]:
        return list(self._sections["Data"]["hints"])

    # ── undoing ─────────────────────────────────────────────────────────────
    def undo(self) -> None:
        """Take every adjustment back. The caller moves the widgets home."""
        for m in self._mirrors:
            parent = m.parent()
            if parent is not None:
                parent.removeEventFilter(m)
            m.deleteLater()
        self._mirrors = []
        for fn in reversed(self._undo):
            fn()
        self._undo = []
