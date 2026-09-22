"""The Convert ND2 plate map.

The behaviour is :class:`datasets.core.converter_dashboard.ConverterDashboard`,
shared with the CZI converter. This keeps a name of its own for the page and
its tests, and an object name a stylesheet rule can address without reaching
every converter's plate at once.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from datasets.core.converter_dashboard import ConverterDashboard


class Nd2ImportDashboard(ConverterDashboard):
    """Wells of an ND2 conversion: planned, in flight, and finished."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, object_name="Nd2ImportDashboard")
