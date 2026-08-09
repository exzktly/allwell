"""ControlBand — the one control row every Review tab uses.

A tab's controls divide into two kinds, and mixing them is why no two tabs in
the pane looked alike:

* **Scope** — *what data is shown*: channel, property, timepoint, threshold.
  Left-packed, and it flows: when the pane is narrow the controls wrap onto a
  second line rather than being squeezed until labels overlap the widget beside
  them.
* **Actions** — *how it is shown, and what to do with it*: a ``Display ▾``
  popover, ``Export ▾``, the Properties button. Pinned flush to the right edge.

Nothing is centred, and there is no third zone. Which view is being shown is the
tab bar's job, not the band's.

Why the scope zone is a :class:`~widgets.flow_layout.FlowLayout` and not a
``QHBoxLayout``: a box layout has no wrap, so at narrow widths Qt compresses the
children until a label is drawn over the combo next to it. The flow layout wraps
instead, and label/control pairs added through :meth:`add_scope` are bundled with
``flow_group`` so a label can never be stranded on a different line from the
control it names.

A page whose body is a stack of leaf views — one page, several sub-tabs — still
gets **one** band, shared by every leaf. A leaf claims a :class:`ControlBandSlot`
from the band, adds its own scope groups and actions through the slot exactly as
it would through the band, and :meth:`ControlBand.show_slot` shows one leaf's
contributions while hiding the rest. Without that, a page-level band carrying the
controls common to every leaf plus a per-leaf band under it puts two strips of
chrome above the plot, which is the stacking the band exists to remove.

Two details that are easy to get wrong and are handled here:

* The action zone is aligned to the top. Without that it centres itself against
  a wrapped scope zone and the buttons drift half a row down.
* ``FlowLayout.heightForWidth`` returns ~0 for an empty scope zone, so the band
  would collapse behind its own actions; the host's minimum height is floored to
  the action zone's.

No All-Well coupling — a plain reusable widget.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSizePolicy, QWidget

import theme
from widgets.checkbox_fit import fit_checkbox_width
from widgets.flow_layout import FlowLayout, flow_group, sync_flow_height


#: Sentinel for "this band's page has not chosen a leaf yet" — distinct from
#: ``None``, which is the page explicitly showing no leaf's controls at all.
_NO_SLOT = object()


def _fit_checkboxes(widgets) -> None:
    """Floor every checkbox's width to what its own style needs for its label.

    Done here rather than at each call site for the reason ``trailing=True``
    lives here: the rule belongs with the control row, not in each builder's
    memory. A ``QCheckBox`` whose style places the label further right than
    ``sizeHint()`` budgeted clips its own text, and the flow layout then packs
    the next control immediately against the cut — reported on macOS for the
    Distribution tab's ``log x`` / ``log y`` boxes. See
    :mod:`widgets.checkbox_fit`; it is a no-op wherever the label already fits,
    which is every case under Fusion.
    """
    for widget in widgets:
        if isinstance(widget, QCheckBox):
            fit_checkbox_width(widget)


class ControlBandSlot(QWidget):
    """One leaf view's contribution to a band shared by several leaves.

    Built by :meth:`ControlBand.slot`. It mirrors the band's own building API, so
    a leaf that used to own a whole band changes only where it gets that object
    from — every ``add_scope`` / ``add_action`` call below reads the same.

    It is also a real widget, and leaves rely on that: they pass their band to
    ``QComboBox(band)`` and friends as a parent for controls that live inside a
    popover rather than in the row. A slot is a child of the band that is never
    put in a layout, so it is never shown and never painted; it exists to own
    those widgets and to remember which of the band's items belong to this leaf.
    """

    def __init__(self, band: "ControlBand", name: str) -> None:
        super().__init__(band)
        self.setObjectName("ControlBandSlot")
        self._band = band
        self._name = name
        self._items: list[QWidget] = []

    @property
    def name(self) -> str:
        return self._name

    def add_scope(self, label: str, *widgets: QWidget) -> QWidget:
        return self._claim(self._band.add_scope(label, *widgets))

    def add_action(self, widget: QWidget) -> QWidget:
        return self._claim(self._band.add_action(widget))

    def _claim(self, item: QWidget) -> QWidget:
        """Record *item* as this leaf's, and hide it if another leaf is current.

        ``QLayout.addWidget`` shows what it is given, and leaves are built
        lazily — the first time each is switched to. Without this, a leaf built
        while a *different* leaf is on screen would drop its controls into the
        visible row and stay there, because the switch that would have hidden
        them already happened.
        """
        self._items.append(item)
        item.setVisible(self._band.is_slot_current(self._name))
        return item

    def setSlotVisible(self, visible: bool) -> None:  # Qt-ish naming
        """Show or hide everything this leaf put in the band.

        Not ``setVisible`` — that is the slot's own (never-shown) widget
        visibility, and overriding it would hide the controls the moment Qt
        polished the slot itself.
        """
        for item in self._items:
            item.setVisible(visible)


class ControlBand(QWidget):
    """One row: scope controls left (flowing), actions right (flush)."""

    #: Height reserved for one row of controls, whatever that row happens to
    #: hold. Qt sizes each control from its own font and padding, so a row of
    #: ``QPushButton``s measures 24px, of ``LabeledComboBox``es 25, of
    #: ``QToolButton``s 27 and of ``QComboBox``es 28 — which made an unwrapped
    #: band 41, 43, 44 or 49px tall purely from which control a page happened to
    #: put in it, and moved the content under it by the same amount. The band is
    #: chrome shared by every page, so its single-row height is a constant here
    #: rather than an accident of the tallest child.
    ROW_H = 28

    @classmethod
    def band_height(cls) -> int:
        """The height of a band showing one row — the same on every page.

        Pages with no controls at all reserve this by adding an empty band, so
        the content below starts at the same y as it does on a page that has
        controls (:mod:`widgets.page_body`).
        """
        return cls.ROW_H + 2 * theme.Spacing.sm

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._slots: dict[str, ControlBandSlot] = {}
        #: Which leaf's controls the row is showing. ``_NO_SLOT`` until the page
        #: first selects one, which is what lets a band with no leaves at all
        #: behave exactly as it did before slots existed.
        self._current_slot: str | None | object = _NO_SLOT
        self.setObjectName("ControlBand")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

        s = theme.Spacing
        root = QHBoxLayout(self)
        root.setContentsMargins(s.md, s.sm, s.md, s.sm)
        root.setSpacing(s.md)

        # The host and the label/control groups are pure containers, but
        # theme.qss's blanket ``QWidget`` rule fills every plain widget with the
        # app canvas colour — so without an explicit transparent rule they paint
        # a lighter rectangle over the band's own fill. Both carry a name so
        # that rule can reach them without also reaching the controls inside.
        self._scope_host = QWidget(self)
        self._scope_host.setObjectName("ControlBandScope")
        self._scope = FlowLayout(self._scope_host)
        self._scope.setContentsMargins(0, 0, 0, 0)
        self._scope.setSpacing(s.sm)
        sync_flow_height(self._scope_host)

        self._actions = QHBoxLayout()
        self._actions.setSpacing(s.sm)
        self._actions.setAlignment(Qt.AlignTop)

        # The scope host takes the slack, which is what pins the actions right.
        #: Scope items pinned to the end of the row — see ``add_scope``.
        self._trailing: list[QWidget] = []
        root.addWidget(self._scope_host, 1, Qt.AlignTop)
        root.addLayout(self._actions)

        # A real minimum, not just a floor inside ``heightForWidth``. A parent
        # layout holding this band asks its ``QWidgetItem`` for the height, and
        # ``QWidgetItem`` asks the *inner* layout — ``wid->layout()->heightForWidth()``
        # — whenever the widget has one, so an override on this class is never
        # consulted and the band would still be sized by its tallest child.
        # ``setMinimumHeight`` is enforced by the widget itself, which every
        # layout respects.
        self.setMinimumHeight(self.band_height())

    # ── building ────────────────────────────────────────────────────────────
    def add_scope(self, label: str, *widgets: QWidget,
                  trailing: bool = False) -> QWidget:
        """Add a scope control, optionally with a label bound to it.

        The label and its control(s) are added as **one** flow item, so a wrap
        can never separate them. Pass an empty *label* for a self-describing
        control (a menu button that already reads "All 3 timepoints ▾").

        ``trailing=True`` pins the item to the END of the scope zone whatever
        order the tab builds its controls in. Threshold is the case it exists
        for: it is the one scope control that narrows the data rather than
        choosing which data to show, and every tab that has one wants it last —
        but tabs add their controls in whatever order their builder runs, so
        Threshold ended up mid-row with Timepoints after it. A flag keeps the
        rule with the control instead of making each builder remember to call
        it last.
        """
        if not widgets:
            raise ValueError("add_scope needs at least one widget")
        _fit_checkboxes(widgets)
        if label:
            cap = QLabel(label, self._scope_host)
            cap.setObjectName("Muted")
            item = flow_group(self._scope_host, cap, *widgets)
            item.setObjectName("ControlBandGroup")
        elif len(widgets) == 1:
            item = widgets[0]
            item.setParent(self._scope_host)
        else:
            item = flow_group(self._scope_host, *widgets)
            item.setObjectName("ControlBandGroup")
        if trailing:
            self._trailing.append(item)
        self._scope.addWidget(item)
        self._restack_trailing()
        return item

    def _restack_trailing(self) -> None:
        """Re-append the pinned items so they stay last as others arrive."""
        for item in self._trailing:
            self._scope.move_to_end(item)

    def add_action(self, widget: QWidget) -> QWidget:
        """Add an action to the right zone, in call order, flush to the edge."""
        _fit_checkboxes((widget,))
        widget.setParent(self)
        self._actions.addWidget(widget)
        return widget

    # ── per-leaf slots ──────────────────────────────────────────────────────
    def slot(self, name: str) -> ControlBandSlot:
        """The :class:`ControlBandSlot` called *name*, created on first ask.

        Controls the whole page shares are added to the band directly and are
        never hidden; controls belonging to one leaf go through that leaf's slot.
        Because the band adds items in call order and the shared controls are
        added when the band is built, a leaf's controls always follow them.
        """
        existing = self._slots.get(name)
        if existing is not None:
            return existing
        created = ControlBandSlot(self, name)
        self._slots[name] = created
        return created

    def show_slot(self, name: str | None) -> None:
        """Show slot *name*'s controls and hide every other slot's.

        Pass ``None`` to hide them all. A name with no slot is not an error: the
        leaf that would own it is built lazily and may not exist yet. The choice
        is remembered either way, so that leaf's controls are hidden on arrival
        if the page has since moved on to another one.
        """
        self._current_slot = name
        for slot_name, slot in self._slots.items():
            slot.setSlotVisible(slot_name == name)

    def current_slot(self) -> str | None:
        """The slot the row is showing, or ``None`` if it is showing none."""
        return None if self._current_slot is _NO_SLOT else self._current_slot

    def is_slot_current(self, name: str) -> bool:
        """Whether slot *name*'s controls belong in the row right now."""
        return self._current_slot is _NO_SLOT or self._current_slot == name

    def slot_names(self) -> tuple[str, ...]:
        return tuple(self._slots)

    # ── geometry ────────────────────────────────────────────────────────────
    @property
    def scope_host(self) -> QWidget:
        """The widget hosting the scope flow — for tests and for hiding the zone."""
        return self._scope_host

    def action_count(self) -> int:
        return self._actions.count()

    def scope_count(self) -> int:
        return self._scope.count()

    def _action_height(self) -> int:
        hint = self._actions.sizeHint().height()
        return hint if hint > 0 else 0

    def _chrome_height(self) -> int:
        m = self.layout().contentsMargins()
        return m.top() + m.bottom()

    def _scope_width_for(self, total_width: int) -> int:
        """Width the scope zone gets once margins and the action zone are taken."""
        m = self.layout().contentsMargins()
        taken = m.left() + m.right() + self._actions.sizeHint().width()
        if self._actions.count():
            taken += self.layout().spacing()
        return max(total_width - taken, 1)

    # Qt asks the parent layout for our height at a given width; answer from the
    # flow at the width the scope zone will actually get. Without this the band
    # inherits QWidget's cached size hint, which is computed from whatever width
    # it happened to have when first shown — for a tab built inside a stacked
    # page that is a narrow transient width, and the flow's "everything wrapped
    # onto one column" answer gets pinned as the band's height for good.
    def hasHeightForWidth(self) -> bool:  # Qt naming
        return True

    def heightForWidth(self, width: int) -> int:  # Qt naming
        scope = self._scope.heightForWidth(self._scope_width_for(width))
        return max(scope, self._action_height(), self.ROW_H) + self._chrome_height()

    def minimumSizeHint(self):  # Qt naming
        """One row of controls — never shorter than the action zone.

        ``FlowLayout.heightForWidth`` returns ~0 for an empty scope zone, which
        would let the band collapse behind its own buttons. ``ROW_H`` floors it
        further, so a band of short controls (and a band with nothing in it at
        all) still measures :meth:`band_height`.
        """
        hint = super().minimumSizeHint()
        row = self._scope.heightForWidth(1_000_000)   # unwrapped: a single row
        hint.setHeight(
            max(row, self._action_height(), self.ROW_H) + self._chrome_height()
        )
        return hint

    def sizeHint(self):  # Qt naming
        hint = super().sizeHint()
        width = self.width() or hint.width()
        hint.setHeight(self.heightForWidth(width))
        return hint
