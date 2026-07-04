"""Plot Groups persistence in ``persistence.json``.

Plot Groups are quick-load well subsets for the plotting sidebar. They are
stored independently from statistical Sample Definition selections so adding or
removing one never mutates replicate/group definitions.
"""

from __future__ import annotations

import logging

from well_viewer.domain.plot_groups import filter_loaded_wells, validate_repair_plot_groups
from well_viewer.persistence import _doc

_logger = logging.getLogger("well_viewer")

SECTION = "plot_groups"


def save_to_data_dir(app) -> None:
    """Persist Plot Groups to the dataset's consolidated persistence document."""
    groups = validate_repair_plot_groups(app._app_state.selection.plot_groups or [])
    _doc.set_section(app._ctx, SECTION, groups)


def load_from_data_dir(app) -> None:
    """Load persisted Plot Groups and refresh the sidebar list if it exists."""
    raw = _doc.get_section(app._ctx, SECTION)
    loaded = app._app_state.dataset.well_paths.keys()
    groups = filter_loaded_wells(validate_repair_plot_groups(raw), loaded)
    app._app_state.selection.plot_groups = groups
    refresh = getattr(app, "_plot_groups_refresh", None)
    if callable(refresh):
        try:
            refresh()
        except Exception:
            _logger.exception("Plot Groups sidebar refresh failed after load")
