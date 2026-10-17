"""Dataset domain state.

Everything that belongs to "the data directory currently open": its paths, the
per-well row cache, the canonical timepoint / FOV lists derived from that cache,
the ``pipeline_info.json`` sidecar, and the lazily-read persistence document.

The timepoint / FOV rebuilds are genuine dataset behaviour and live here as
methods — they were ``ReviewApp._rebuild_all_*_cache`` before.

Qt-free: imports only stdlib, pandas, and the Qt-free ``data_loading`` module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable


from util.fov import sort_key as _fov_sort_key
from review.data.data_loading import parse_timepoint_hours
from review.data.row_cache import RowCache
from review.data.well_summary import WellSummary, well_summaries
from review.data.image_resolver import normalize_numeric_token

# Local alias for the shared numeric-token normaliser: a raw FOV token collapses
# to ``%g`` form so ``1``, ``01`` and ``1.0`` index the same field of view.
_norm_fov = normalize_numeric_token


@dataclass
class DatasetState:
    """The currently-open dataset.

    ``cache`` maps well token → measurement ``DataFrame``; ``well_paths`` maps
    well token → source CSV path. ``all_timepoints_cache`` / ``all_fovs_cache``
    are derived from ``cache`` + ``pipeline_info`` and kept canonical by the
    rebuild methods so every tab's timepoint / FOV menu agrees.
    """

    data_dir: Path | None = None
    in_dir: Path | None = None
    tmp_dir: Path | None = None

    well_paths: dict[str, Path] = field(default_factory=dict)
    # Bounded by a memory budget (``review/data/row_cache.py``): iterating it
    # yields the wells held, not the plate — ask ``get_rows`` for each well.
    cache: RowCache = field(default_factory=RowCache)

    # well token -> WellSummary: what opening the dataset needs from each well
    # (timepoints, FOVs, tokens, columns, the FOV verdict), kept for every well
    # whether or not its rows are held. Filled by ``WellRepository.open_well``.
    summaries: dict[str, WellSummary] = field(default_factory=dict)

    all_timepoints_cache: list[float] = field(default_factory=list)
    all_fovs_cache: list[str] = field(default_factory=list)

    pipeline_info: dict = field(default_factory=dict)
    tok_to_label: dict[str, str] = field(default_factory=dict)
    well_labels: dict[str, str] = field(default_factory=dict)
    # Called with a well's label just before that well is read from disk, by
    # either read path (``data_loading.load_and_project`` for a whole frame,
    # ``narrow_rows`` for a narrow one). Set by the dataset load for its own
    # length so the status bar can say which well each phase is reading
    # (``review/core/load_progress.py``); ``None`` otherwise.
    read_listener: Callable[[str], None] | None = None
    fov_tp_extractor: Callable[[str], tuple[str, str]] | None = None
    persistence_doc: object = None

    # Crop rectangle (row0, col0, row1, col1) in source-mask pixels, or None.
    # Owned by features/crop; cells whose centroid falls outside are excluded at
    # the df_included_mask chokepoint via the Crop_Pass column.
    crop_region: tuple[int, int, int, int] | None = None

    # Which frames that rectangle is applied to — the same wells / FOVs /
    # timepoints mechanism the Segmentation tab's drag-delete arms, with the
    # opposite un-armed default: ``None`` here means *every* value of that
    # field, so an un-scoped crop filters the whole plate. An empty set names no
    # frames at all, so the two cannot be collapsed into one representation.
    #
    # Unlike the delete scope these are persisted (``persistence.json::crop_region``)
    # and cleared on a dataset swap: the scope is part of what a saved crop
    # *means*, so reloading a dataset without it would silently widen the crop to
    # every frame.
    crop_scope_wells: set[str] | None = None
    crop_scope_fovs: set[str] | None = None
    crop_scope_tps: set[str] | None = None

    # well token -> domain.fov_integrity.FovIntegrity, filled by ``get_rows`` as
    # each well's frame is built. A well whose FOV labels don't identify its
    # fields cannot produce per-field statistics, and the fact has to be
    # reported rather than silently absorbed — see ``domain/fov_integrity.py``.
    fov_integrity: dict = field(default_factory=dict)
    # True when ``persistence.json`` existed but could not be read (corrupt
    # JSON, an OS error, or a schema version this build does not understand).
    # The in-memory doc is then empty *by failure*, not because the dataset has
    # no saved state — and the two must not be confused on the next write, or
    # one unreadable byte silently erases every section the user ever saved.
    persistence_doc_degraded: bool = False
    # True when ``persistence.json`` is legible but carries a schema version
    # this build does not know — written by a newer build the user also runs.
    # Distinct from *degraded*: nothing is wrong with the file, so it is not
    # renamed aside; this build simply refuses to write over state it cannot
    # read.
    persistence_doc_foreign: bool = False
    # Fields the user excluded by hand from QC triage, keyed ``(well, fov,
    # timepoint)`` -> ``{"reason", "at"}`` (``data/field_exclusions.py``,
    # persisted by ``persistence/field_exclusions.py``), and the triage
    # findings they accepted, keyed by finding -> ``{"state", "note", "at"}``
    # (``persistence/triage_decisions.py``). Per dataset, so cleared on a swap.
    field_exclusions: dict = field(default_factory=dict)
    triage_decisions: dict = field(default_factory=dict)
    # Bumped by every :meth:`begin_load`, including a reload of the folder
    # already open. A cache keyed on ``data_dir`` alone cannot tell a reload
    # from no change, and a reload is exactly when files on disk changed under
    # it — a copy of analysis, a reset (``SavedPlotsStore.ensure_loaded``).
    load_generation: int = 0

    # Monotonic tick over the *contents* of ``cache``. Every path that changes
    # what a cached measurement frame holds — a projected verdict column, a
    # gating rewrite, a manual curation override, a whole frame replaced after
    # a write-back — bumps this through :meth:`note_rows_changed`, and the
    # per-well aggregation memo carries it in its key.
    #
    # It ticks on *change*, never on a fresh insert. Bumping when a well is
    # first loaded would advance the counter once per well during the cold pass,
    # so each well's entry would be written under a key the next well's load
    # immediately invalidated: the cache would hold ninety-six entries of which
    # only the last was reachable, and the second click would pay the full price
    # again. A label appearing for the first time cannot invalidate another
    # well's aggregate, so it must not.
    #
    # ``tests/test_row_cache_writers.py`` enumerates the writers from the syntax
    # tree and fails on any new one that has not said how it bumps this.
    rows_version: int = 0

    def note_rows_changed(self) -> None:
        """Record that a cached frame's contents changed.

        The one bump site, so a reader can find every caller by name rather
        than by searching for increments of a bare integer.
        """
        self.rows_version += 1

    # ── status ───────────────────────────────────────────────────────────────

    @property
    def has_data(self) -> bool:
        return bool(self.well_paths)

    @property
    def well_count(self) -> int:
        return len(self.well_paths)

    # ── load lifecycle ───────────────────────────────────────────────────────

    def begin_load(self, data_dir: Path) -> None:
        """Reset the per-dataset caches for a switch to *data_dir*.

        The dataset identity is changing, so the well paths, the row cache, and
        the cached persistence document are all dropped — the load pass and the
        rebuild methods below repopulate them. Cleared in place so any view
        still holding a reference sees the empty collections.
        """
        self.data_dir = data_dir
        self.load_generation += 1
        self.persistence_doc = None
        self.well_paths.clear()
        self.summaries.clear()
        self.cache.clear()
        self.fov_integrity.clear()
        self.persistence_doc_degraded = False
        self.persistence_doc_foreign = False
        self.field_exclusions.clear()
        self.triage_decisions.clear()
        # Well labels are per-dataset (persisted in the new dataset's
        # pipeline_info sample_definitions block). The loader merges them in
        # additively and early-returns when the new dataset has no block, so
        # clear here — else the previous dataset's labels leak into this one.
        self.well_labels.clear()
        # A new dataset has no crop until its persisted region (if any) loads.
        self.crop_region = None
        self.reset_crop_scope()
        # A dataset swap is the most invalidating event there is, and it is the
        # one case where clearing the cache is not enough on its own: two plates
        # can carry the same well tokens, so an aggregation memoized for A01 of
        # the old plate would be keyed identically for A01 of the new one. The
        # load path does clear the stats cache, but only inside a suppressed
        # try/except (``recompute_qc_all`` in ``load_controller``), so a failure
        # there would hand the new plate the old plate's numbers under the new
        # plate's name. Ticking here makes the key itself carry the swap.
        self.note_rows_changed()

    def reset_crop_scope(self) -> None:
        """Widen the crop back to every frame in the dataset.

        The armed tokens name wells, FOVs and timepoints of one plate, so they
        are dropped on a dataset swap rather than carried across it — a scope
        that survived would either name wells the new plate does not have (the
        crop then quietly filters nothing) or, worse, wells it does.
        """
        self.crop_scope_wells = None
        self.crop_scope_fovs = None
        self.crop_scope_tps = None

    def well_summaries(self) -> list[WellSummary]:
        """A summary for every well, held or not (``well_summary.well_summaries``)."""
        return well_summaries(self)

    def rebuild_timepoints(self) -> None:
        """Recompute the canonical numeric timepoint list.

        Merged from two sources so every tab that reads ``all_timepoints_cache``
        agrees: every well's timepoints (``timepoint_hours`` or parsed
        ``timepoint`` strings, as summarised) and ``pipeline_info``'s
        ``available_timepoints``.
        """
        all_tps: set[float] = set()
        for summary in self.well_summaries():
            all_tps.update(summary.timepoints)
        for tp in (self.pipeline_info or {}).get("available_timepoints", []) or []:
            parsed = parse_timepoint_hours(str(tp))
            if parsed is not None:
                all_tps.add(parsed)
        self.all_timepoints_cache = sorted(all_tps)

    def rebuild_fovs(self) -> None:
        """Recompute the canonical FOV list from every well + pipeline_info.

        A dataset with rows but no ``fov`` column collapses to a single FOV
        ``"1"`` so the FOV menus are never empty.
        """
        all_fovs: set[str] = set()
        for summary in self.well_summaries():
            all_fovs.update(summary.fovs)
        for fov in (self.pipeline_info or {}).get("available_fovs", []) or []:
            fov_norm = _norm_fov(fov)
            if fov_norm:
                all_fovs.add(fov_norm)
        if not all_fovs and self.well_paths:
            all_fovs.add("1")

        self.all_fovs_cache = sorted(all_fovs, key=_fov_sort_key)
