"""Ratio metric persistence — ``ratios`` section of ``persistence.json``.

Legacy ``ratios.json`` sidecars are migrated on first load by ``_doc``.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QTimer

from well_viewer.persistence import _doc
from well_viewer.ratio_models import ratios_from_dict, ratios_to_dict

_logger = logging.getLogger("well_viewer")


def save_to_data_dir(app) -> None:
    if not getattr(app, "_data_dir", None):
        return
    _doc.set_section(app, "ratios", ratios_to_dict(app._app_state.channel.ratio_metrics))


def load_from_data_dir(app) -> None:
    if not getattr(app, "_data_dir", None):
        return
    data = _doc.get_section(app, "ratios")
    if data is None:
        return
    app._set_ratio_metrics(ratios_from_dict(data))


def schedule_save(app) -> None:
    """Debounced autosave (matches cell_overrides / line_order). The
    ratio panel fires save on every field-edit signal; without
    debouncing each keystroke produces a JSON write."""
    if getattr(app, "_ratios_save_pending", False):
        return
    if not getattr(app, "_data_dir", None):
        return
    app._ratios_save_pending = True
    QTimer.singleShot(500, lambda: _flush(app))


def _flush(app) -> None:
    app._ratios_save_pending = False
    save_to_data_dir(app)
