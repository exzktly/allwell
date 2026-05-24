"""Ratio metric persistence — ``ratios`` section of ``persistence.json``.

Legacy ``ratios.json`` sidecars are migrated on first load by ``_doc``.
"""

from __future__ import annotations

import logging

from well_viewer.persistence import _doc
from well_viewer.domain.ratio_models import ratios_from_dict, ratios_to_dict

_logger = logging.getLogger("well_viewer")


def save_to_data_dir(app) -> None:
    if not app._app_state.dataset.data_dir:
        return
    _doc.set_section(app, "ratios", ratios_to_dict(app._app_state.channel.ratio_metrics))


def load_from_data_dir(app) -> None:
    if not app._app_state.dataset.data_dir:
        return
    data = _doc.get_section(app, "ratios")
    if data is None:
        return
    app._set_ratio_metrics(ratios_from_dict(data))
