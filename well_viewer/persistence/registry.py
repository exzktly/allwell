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

import logging
from collections.abc import Callable

from PySide6.QtCore import QTimer

_DEBOUNCE_MS = 500
_logger = logging.getLogger("well_viewer")


class PersistenceRegistry:
    """Named save/load dispatch with a single shared debounced-save path."""

    def __init__(self, app) -> None:
        self._ctx = app  # a ViewerContext (B4)
        self._save: dict[str, Callable] = {}
        self._load: dict[str, Callable] = {}
        self._pending: set[str] = set()

    def register(self, name: str, *, save: Callable,
                 load: Callable | None = None) -> None:
        """Register a domain's ``save(app)`` and optional ``load(app)``."""
        self._save[name] = save
        if load is not None:
            self._load[name] = load

    def save(self, name: str) -> None:
        """Run *name*'s save immediately."""
        self._save[name](self._ctx)

    def load(self, name: str) -> None:
        """Run *name*'s load (a no-op when the domain registered none)."""
        load = self._load.get(name)
        if load is not None:
            load(self._ctx)

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

    def flush_all(self) -> None:
        """Write every pending debounced save now.

        ``schedule_save`` coalesces edits behind a 500 ms timer, which is right
        while the app is running and wrong at the two moments the timer will
        never fire: quitting, and loading another dataset. On quit the edit was
        simply lost — everything except ``view_state`` had no force-save at
        all, so a ratio, a heat-map layout, a curated cell override, a line
        order, a plot group, a note, a sample definition, a QC parameter or a
        crop made within half a second of pressing ⌘W went nowhere, silently.
        On a dataset load it was worse than lost: a timer that survived
        ``begin_load`` fired *after* the new dataset's directory was in place
        and wrote the previous dataset's state into it.

        Failures are logged per domain and never propagate — this runs on the
        way out, and one domain's disk error must not stop the others being
        written.
        """
        for name in sorted(self._pending):
            self._flush(name)

    def _flush(self, name: str) -> None:
        self._pending.discard(name)
        # A debounced autosave runs from a QTimer with no caller to catch a
        # raise — an exception here (e.g. a disk-full write in
        # ``view_state``/``sample_definitions``) would otherwise vanish into
        # the Qt event loop, so the user's edits silently fail to persist with
        # no trace (audit E16). Log it so the failure is at least diagnosable.
        try:
            self.save(name)
        except Exception:
            _logger.exception(
                "persistence: debounced autosave for %r failed; the edit was "
                "not written to disk", name)


def build_persistence_registry(ctx) -> PersistenceRegistry:
    """Create the registry and register every data-dir persistence domain."""
    from well_viewer.persistence import (
        cell_overrides, crop_region, heatmap_layouts, line_order,
        qc_params, plot_groups, ratios, sample_definitions,
        view_state,
    )

    registry = PersistenceRegistry(ctx)
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

    # Feature-owned removable domains. Their modules (persistence/export_groups.py
    # → Batch Export, persistence/multivariate.py → Multivariate) are physically
    # stripped from a subset bundle, so importing them unconditionally crashes a
    # frozen thin build at startup. Gate on the active profile — the same
    # removability contract every other startup site follows. (This registration
    # ultimately belongs on each feature's descriptor via the PersistenceSpec
    # machinery scaffolded in core/feature_registry.py, which this function is
    # meant to "fold in"; until that lands, gate here.)
    from well_viewer.core.profile import active_profile
    prof = active_profile()

    def _admits(feature: str) -> bool:
        return prof.features is None or feature in prof.features

    if _admits("Batch Export"):
        from well_viewer.persistence import export_groups
        registry.register("export_groups",
                          save=export_groups.save_to_data_dir,
                          load=export_groups.load_from_data_dir)
    if _admits("Multivariate"):
        from well_viewer.persistence import multivariate
        registry.register("multivariate",
                          save=multivariate.save_to_data_dir,
                          load=multivariate.load_from_data_dir)
    return registry
