"""A control with its label set outside it: above (the default) or beside.

The design language puts a field's label outside the control, never inside it — not painted into a combo box
("Channel: GFP") and not a bold "Folder:" with a colon. :class:`LabelledField` is the one way to put a label with a
control: a ``QLabel`` in the label role (12 px, the secondary ink, no colon) and the control, as one widget, so a
layout that moves one moves both.

The label is a plain ``QLabel`` carrying ``textRole="label"``, which the application stylesheet styles
(``ui/text_roles.py``); this widget knows no more of the app than that one property. A phone form that wants the
label in a column of its own finds it as the first widget of this widget's box layout, the same place it finds the
caption ``ControlBand.add_scope`` bundles (``widgets/control_band_form.py``).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui import theme


class LabelledField(QWidget):
    """*label* set above *control* (or beside it, with ``above=False``)."""

    def __init__(self, label: str, control: QWidget, parent: QWidget | None = None, *,
                 above: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("LabelledField")
        self._label = QLabel(label, self)
        self._label.setProperty("textRole", "label")
        self._label.setBuddy(control)
        self._control = control
        if above:
            lay = QVBoxLayout(self)
            lay.setSpacing(theme.Spacing.xs)
        else:
            lay = QHBoxLayout(self)
            lay.setSpacing(theme.Spacing.sm)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._label, 0, Qt.AlignLeft | (Qt.AlignBottom if above else Qt.AlignVCenter))
        lay.addWidget(control, 1)

    def label(self) -> QLabel:
        return self._label

    def control(self) -> QWidget:
        return self._control

    def setLabelText(self, text: str) -> None:  # noqa: N802 — Qt naming
        self._label.setText(text)
