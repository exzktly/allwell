"""A Scatter page's axes as one Data row, "Axes: X GFP Mean · Y RFP Mean ›", whose inspector page holds every
axis picker — each axis's channel and property, and under Observations: Nanowells the X / Y metrics, Subset and
Baseline — rather than eight band items (the user's decision, design-language tracker §9.1 step 20).

Each picker sits in its own :class:`widgets.labelled_field.LabelledField` (label beside it), and that field is what
``add_scope`` returns: the Scatter code hides and shows a picker through its group (``combo.parentWidget()``, the
``*_grp`` registrations), exactly as it did when the group was a band item.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QWidget

from review.core import inspector_panels
from widgets.labelled_field import LabelledField


class AxesPage:
    """The Axes row of a Scatter page's band and the page behind it."""

    def __init__(self, ctx, band) -> None:
        self._fields: list[LabelledField] = []
        self.button, self._panel, self._layout = inspector_panels.make_popover_button(
            "Axes", band, ctx=ctx, tab="data", summary=self.summary)
        self.button.setToolTip("What each axis plots")

    def add_scope(self, label: str, *widgets: QWidget) -> QWidget:
        """Put *widgets* (one picker) on the page under *label*; return the field that holds it, which hides it."""
        (control,) = widgets
        field = LabelledField(label, control, self._panel, above=False)
        self._layout.addWidget(field)
        self._fields.append(field)
        if isinstance(control, QComboBox):
            control.currentIndexChanged.connect(lambda _i: inspector_panels.refresh(self.button))
        return field

    def finish(self) -> None:
        self._layout.addStretch(1)

    def summary(self) -> str:
        """Each axis's shown pickers' values, X first: "X GFP Mean · Y RFP Mean"."""
        parts: dict[str, list[str]] = {"X": [], "Y": []}
        for field in self._fields:
            control = field.control()
            axis = field.label().text()[:1]
            if axis in parts and isinstance(control, QComboBox) and not field.isHidden() and control.currentText():
                parts[axis].append(control.currentText())
        return " · ".join(f"{axis} {' '.join(values)}" for axis, values in parts.items() if values) or "—"
