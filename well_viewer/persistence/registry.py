"""Dispatch table + one shared debounce path for the data-dir JSON domains.

Before this, every autosaving persistence domain (``ratios``,
``heatmap_layouts``, ``cell_overrides``, ``line_order``, ``notes``) carried its
own *identical* ``schedule_save`` / ``_flush`` debounce pair plus an
``app._<domain>_save_pending`` boolean on the god-object — five copies of the
same five-line coalescer. ``PersistenceRegistry`` holds the one debounce
implementation and the one pending-set; domains register a ``save`` (and
optional ``load``) callable and are addressed by name.

Replaces the ~17 thin ``WellViewerApp._<domain>_{save,load}_to_data_dir`` /
``_schedule_save`` wrapper methods — call sites use
``app._persistence.{save,load,schedule_save}(name)`` directly.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QTimer

_DEBOUNCE_MS = 500


class PersistenceRegistry:
    """Named save/load dispatch with a single shared debounced-save path."""

    def __init__(self, app) -> None:
        self._app = app
        self._save: dict[str, Callable] = {}
        self._load: dict[str, Callable] = {}
        self._pending: set[str] = set()

    def register(self, name: str, *, save: Callable,
                 load: Optional[Callable] = None) -> None:
        """Register a domain's ``save(app)`` and optional ``load(app)``."""
        self._save[name] = save
        if load is not None:
            self._load[name] = load

    def save(self, name: str) -> None:
        """Run *name*'s save immediately."""
        self._save[name](self._app)

    def load(self, name: str) -> None:
        """Run *name*'s load (a no-op when the domain registered none)."""
        load = self._load.get(name)
        if load is not None:
            load(self._app)

    def schedule_save(self, name: str) -> None:
        """Debounced save — coalesces a burst of edits into one disk write.

        Repeated calls for the same domain within the debounce window collapse
        to a single ``save``; the registered ``save`` itself no-ops when there
        is no data directory.
        """
        if name in self._pending:
            return
        self._pending.add(name)
        QTimer.singleShot(_DEBOUNCE_MS, lambda: self._flush(name))

    def _flush(self, name: str) -> None:
        self._pending.discard(name)
        self.save(name)


def build_persistence_registry(app) -> PersistenceRegistry:
    """Create the registry and register every data-dir persistence domain."""
    from well_viewer.persistence import (
        cell_overrides, crop_region, export_groups, heatmap_layouts, line_order,
        qc_params, plot_groups, ratios, sample_definitions, view_state,
    )

    registry = PersistenceRegistry(app)
    registry.register("ratios",
                      save=ratios.save_to_data_dir,
                      load=ratios.load_from_data_dir)
    registry.register("heatmap_layouts",
                      save=heatmap_layouts.save_to_data_dir,
                      load=heatmap_layouts.load_from_data_dir)
    registry.register("cell_overrides",
                      save=cell_overrides.save_to_data_dir,
                      load=cell_overrides.load_from_data_dir)
    registry.register("line_order",
                      save=line_order.save_to_data_dir,
                      load=line_order.load_from_data_dir)
    registry.register("view_state",
                      save=view_state.save_to_data_dir,
                      load=view_state.load_from_data_dir)
    registry.register("plot_groups",
                      save=plot_groups.save_to_data_dir,
                      load=plot_groups.load_from_data_dir)
    registry.register("notes", save=sample_definitions.save_notes)
    registry.register("sample_definitions",
                      save=sample_definitions.autosave_to_pipeline_info)
    registry.register("qc_params",
                      save=qc_params.save_to_data_dir,
                      load=qc_params.load_from_data_dir)
    registry.register("crop_region",
                      save=crop_region.save_to_data_dir,
                      load=crop_region.load_from_data_dir)
    registry.register("export_groups",
                      save=export_groups.save_to_data_dir,
                      load=export_groups.load_from_data_dir)
    return registry
