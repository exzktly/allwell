"""The Convert CZI plate map.

The behaviour is :class:`datasets.core.converter_dashboard.ConverterDashboard`,
shared with the ND2 converter — the two were identical bar the class name and
the object name. This keeps the name the page and its tests already use, and
the object name a stylesheet can address on its own.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from datasets.core.converter_dashboard import ConverterDashboard


class CziImportDashboard(ConverterDashboard):
    """Wells of a CZI conversion: planned, in flight, and finished."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, object_name="CziImportDashboard")
