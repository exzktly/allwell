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
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

import theme
from widgets.flow_layout import FlowLayout, flow_group, sync_flow_height


class ControlBand(QWidget):
    """One row: scope controls left (flowing), actions right (flush)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
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
        root.addWidget(self._scope_host, 1, Qt.AlignTop)
        root.addLayout(self._actions)

    # ── building ────────────────────────────────────────────────────────────
    def add_scope(self, label: str, *widgets: QWidget) -> QWidget:
        """Add a scope control, optionally with a label bound to it.

        The label and its control(s) are added as **one** flow item, so a wrap
        can never separate them. Pass an empty *label* for a self-describing
        control (a menu button that already reads "All 3 timepoints ▾").
        """
        if not widgets:
            raise ValueError("add_scope needs at least one widget")
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
        self._scope.addWidget(item)
        return item

    def add_action(self, widget: QWidget) -> QWidget:
        """Add an action to the right zone, in call order, flush to the edge."""
        widget.setParent(self)
        self._actions.addWidget(widget)
        return widget

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
        return max(scope, self._action_height()) + self._chrome_height()

    def minimumSizeHint(self):  # Qt naming
        """One row of controls — never shorter than the action zone.

        ``FlowLayout.heightForWidth`` returns ~0 for an empty scope zone, which
        would let the band collapse behind its own buttons.
        """
        hint = super().minimumSizeHint()
        row = self._scope.heightForWidth(1_000_000)   # unwrapped: a single row
        hint.setHeight(max(row, self._action_height()) + self._chrome_height())
        return hint

    def sizeHint(self):  # Qt naming
        hint = super().sizeHint()
        width = self.width() or hint.width()
        hint.setHeight(self.heightForWidth(width))
        return hint
