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

**The debounced path writes on a background thread.** ``_flush`` runs the
domain's ``save`` inside ``_io.background_writes``, so the parts of it that read
application state stay here on the GUI thread while the file operations are
handed to :class:`well_viewer.persistence._writer.PersistenceWriter` — one
serialized worker. On a network share those operations were about a second per
autosave, which is what froze the window whenever the user paused while editing
a variable. ``flush_all`` drains that queue before returning, because its two
callers (quitting, and loading another dataset) cannot proceed until the writes
have landed. Nothing outside this debounced path is affected: an explicit Save
button, the smFISH threshold writer and the gating block all still write inline
and still get a real answer back.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer

from well_viewer.persistence._io import background_writes
from well_viewer.persistence._writer import PersistenceWriter

_DEBOUNCE_MS = 500
_logger = logging.getLogger("well_viewer")


class PersistenceRegistry:
    """Named save/load dispatch with a single shared debounced-save path."""

    def __init__(self, app) -> None:
        self._ctx = app  # a ViewerContext (B4)
        self._save: dict[str, Callable] = {}
        self._load: dict[str, Callable] = {}
        self._pending: set[str] = set()
        self._writer: PersistenceWriter | None = None

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

        Each ``_flush`` only *queues* its write, so the drain at the end is what
        makes this method mean what its name says. Without it the quit path
        would return while the writes were still in flight on a daemon thread
        the interpreter is about to discard, and the dataset-swap path would
        return before the previous plate's state had reached the previous
        plate's folder — the exact loss this method exists to prevent, moved
        one thread across.
        """
        for name in sorted(self._pending):
            self._flush(name)
        if self._writer is not None:
            self._writer.drain()

    def _flush(self, name: str) -> None:
        # The ``QTimer.singleShot`` lambdas ``schedule_save`` arms cannot be
        # cancelled, so they still fire ~500 ms later after ``flush_all`` has
        # already written and drained the domain. Discarding-then-saving
        # unconditionally meant that second firing wrote *again* — and on a
        # dataset swap it landed after ``begin_load``, putting the previous
        # plate's ratios, layouts, line order or crop into the new plate's
        # ``persistence.json``. That is the exact bug ``flush_all`` exists to
        # prevent, so the pending set is the authority on whether a save is
        # still owed, not merely a coalescing hint.
        if name not in self._pending:
            return
        self._pending.discard(name)
        # A debounced autosave runs from a QTimer with no caller to catch a
        # raise — an exception here (e.g. a disk-full write in
        # ``view_state``/``sample_definitions``) would otherwise vanish into
        # the Qt event loop, so the user's edits silently fail to persist with
        # no trace (audit E16). Log it so the failure is at least diagnosable.
        try:
            # The save runs here, on the GUI thread — it reads ``ctx.state`` and
            # builds its payload, and neither may be touched from another
            # thread. Only the file operations it reaches are handed to the
            # writer, and only where the call site opted in — with
            # ``deferrable=True`` on the primitive, or by wrapping the write
            # and its outcome check in one callable given to ``_io.defer_io``.
            with background_writes(lambda job, _n=name: self._enqueue(_n, job)):
                self.save(name)
        except Exception:
            _logger.exception(
                "persistence: debounced autosave for %r failed; the edit was "
                "not written to disk", name)

    # -- the background writer -------------------------------------------

    def _enqueue(self, name: str, job) -> None:
        """Hand one deferred write to the (lazily created) writer thread."""
        if self._writer is None:
            self._writer = PersistenceWriter()
            # Explicitly queued, and stated rather than relied upon.
            # ``failed`` is emitted on the writer thread while
            # ``_on_write_failed`` writes the window's status label, so the
            # delivery has to cross back to the GUI thread. Measured, PySide6's
            # default AutoConnection already resolves that way here — a bound
            # method of a plain (non-QObject) receiver is dispatched against
            # the *sender's* thread affinity, and the writer was created on the
            # GUI thread — but that is a resolution rule this code would rather
            # name than depend on, and a direct connection here would paint a
            # widget from the writer.
            self._writer.failed.connect(self._on_write_failed,
                                        Qt.ConnectionType.QueuedConnection)
        self._writer.submit(name, job)

    def _on_write_failed(self, name: str, detail: str) -> None:
        """Say on screen that a background save did not land.

        The writer has already logged it. This is the other half the user asked
        for: a deferred write has no caller left to return ``False`` to, so
        without a status line the only signal that a plate of variables was
        never written would be finding them gone next session — degraded output
        indistinguishable from good output, which is the defect itself.
        """
        notify = getattr(self._ctx, "notify", None)
        if notify is None:
            return
        msg = (
            f"WARNING — {name} could not be saved to disk ({detail}). The edit "
            f"stays in memory for this session and will be lost when it ends. "
            f"See the log."
        )
        fn = getattr(notify, "set_status", None)
        if callable(fn):
            fn(msg)
        fn = getattr(notify, "toast", None)
        if callable(fn):
            fn(msg, kind="error", msec=8000)


def build_persistence_registry(ctx) -> PersistenceRegistry:
    """Create the registry and register every data-dir persistence domain."""
    from well_viewer.persistence import (
        cell_overrides, crop_region, heatmap_layouts, line_order,
        qc_params, plot_groups, ratios, sample_definitions,
        sample_variables, view_state,
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
    registry.register("sample_variables",
                      save=sample_variables.save_to_data_dir,
                      load=sample_variables.load_from_data_dir)
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
    from core.profile import active_profile
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
