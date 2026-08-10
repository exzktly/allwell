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


def save_to_data_dir(ctx) -> None:
    """Persist Plot Groups to the dataset's consolidated persistence document."""
    groups = validate_repair_plot_groups(ctx.state.selection.plot_groups or [])
    _doc.set_section(ctx, SECTION, groups)


def load_from_data_dir(ctx) -> None:
    """Load persisted Plot Groups and refresh the sidebar list if it exists.

    Plot Groups have a second home: the ``sample_definitions`` block in
    ``pipeline_info.json`` carries a copy, and its hydrator runs just before
    this one. A dataset can arrive with that block but no ``persistence.json``
    section — a folder shared or copied without the sidecar, or one whose
    groups were only ever written by the sample-definitions Save button. So an
    absent section means "nothing to say here" and leaves the hydrated groups
    alone; only a section that is actually present replaces them.
    """
    raw = _doc.get_section(ctx, SECTION)
    if raw is None:
        raw = ctx.state.selection.plot_groups or []
    loaded = ctx.state.dataset.well_paths.keys()
    groups = filter_loaded_wells(validate_repair_plot_groups(raw), loaded)
    ctx.state.selection.plot_groups = groups
    refresh = getattr(ctx.window, "_plot_groups_refresh", None)
    if callable(refresh):
        try:
            refresh()
        except Exception:
            _logger.exception("Plot Groups sidebar refresh failed after load")
