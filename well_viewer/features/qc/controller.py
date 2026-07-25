"""App-facing QC layer: hold the live thresholds, project ``QC_Pass`` onto the
row cache, and drive a live re-render when the thresholds change.

The QC verdict is composed with manual curation at the inclusion chokepoint
(:func:`well_viewer.data.data_loading.df_included_mask`): a row counts only when
``Included == 1`` *and* ``QC_Pass == 1``. This module owns the ``QC_Pass``
column; it never touches ``Included`` (manual curation / cell-gating keep that),
so re-gating QC can never clobber hand-curated include/exclude edits.
"""

from __future__ import annotations

from typing import Optional

from .params import QCParams, compute_qc_pass
from well_viewer.core.safe import swallowed
from well_viewer.data.cache_projectors import project_verdict_column


def get_qc_params(ctx) -> QCParams:
    """Current QC thresholds, lazily seeded from the dataset's ``pipeline_info``
    on first access so the view opens with the run's settings.

    A8 residuals-ledger R1/R4: pure typed state — reads/seeds ``ctx.state.qc``
    (thresholds) from ``ctx.state.dataset.pipeline_info``, so it takes ``ctx``
    and names no god-object. This is what lets the row-fetch data service
    (``data_loading.get_rows``) project QC purely from ``ctx``."""
    qc = ctx.state.qc
    if qc.params is None:
        info = getattr(ctx.state.dataset, "pipeline_info", None)
        qc.params = QCParams.from_pipeline_info(info)
    return qc.params


def apply_qc_to_cache(ctx, label: Optional[str] = None) -> None:
    """Write the ``QC_Pass`` column onto cached well frame(s).

    With ``label`` given, only that well; otherwise every cached well. A no-op
    cheap pass when the gate is inactive (writes ``QC_Pass = 1`` so the chokepoint
    sees an explicit all-pass and stays consistent across re-gates). Shares the
    cache walk with ``crop.controller.apply_crop_to_cache`` via
    :func:`well_viewer.data.cache_projectors.project_verdict_column`.

    A8 residuals-ledger R1: takes ``ctx`` — the params and the row cache both
    come from typed state, so the QC projection is a pure ``ctx`` operation the
    row-fetch data service can call."""
    params = get_qc_params(ctx)
    project_verdict_column(
        ctx, label,
        column="QC_Pass",
        verdict=lambda df: compute_qc_pass(df, params),
        layer="QC",
    )


def recompute_qc_all(ctx) -> None:
    """Re-project ``QC_Pass`` onto all cached wells and bump the caches/version
    so downstream consumers recompute on the next read.

    A8 teardown: takes ``ctx`` — projects QC over typed state and invalidates the
    stats cache through the ctx-native ``invalidate_stats_cache`` core helper."""
    from well_viewer.core.selection_controller import invalidate_stats_cache
    apply_qc_to_cache(ctx, None)
    try:
        invalidate_stats_cache(ctx)  # clears stats cache + bumps override_version
    except Exception:
        swallowed()  # was: pass (silent except)


def set_qc_params(ctx, params: QCParams) -> None:
    """Install new thresholds, recompute the verdict everywhere, persist, and
    re-render the active view so the change is immediately visible.

    A8 teardown: takes ``ctx`` — writes typed ``ctx.state.qc`` and drives the
    persistence registry / full redraw through the ctx accessors so it names no
    god-object."""
    ctx.state.qc.params = params
    recompute_qc_all(ctx)
    try:
        pers = ctx.persistence
        if pers is not None:
            pers.schedule_save("qc_params")
    except Exception:
        swallowed()  # was: pass (silent except)
    try:
        from well_viewer.core.plot_orchestrator import redraw_app
        redraw_app(ctx)
    except Exception:
        swallowed()  # was: pass (silent except)


def qc_excluded_count(ctx, labels=None, params: Optional[QCParams] = None) -> "tuple[int, int]":
    """``(n_excluded, n_total)`` across the given wells (default: all cached) —
    powers the live readout in the QC view. Counts only QC failures, independent
    of manual exclusions. Pass ``params`` to preview a candidate threshold set
    without committing it.

    A8 teardown: reads the row cache off typed ``ctx.state`` so it takes ``ctx``."""
    cache = ctx.state.dataset.cache
    labels = list(cache.keys()) if labels is None else list(labels)
    if params is None:
        params = get_qc_params(ctx)
    n_excl = n_tot = 0
    for lbl in labels:
        df = cache.get(lbl)
        if df is None or df.empty:
            continue
        verdict = compute_qc_pass(df, params)
        n_tot += int(len(verdict))
        n_excl += int((verdict == 0).sum())
    return n_excl, n_tot
