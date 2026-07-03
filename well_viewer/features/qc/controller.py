"""App-facing QC layer: hold the live thresholds, project ``QC_Pass`` onto the
row cache, and drive a live re-render when the thresholds change.

The QC verdict is composed with manual curation at the inclusion chokepoint
(:func:`well_viewer.data.data_loading.df_included_mask`): a row counts only when
``Included == 1`` *and* ``QC_Pass == 1``. This module owns the ``QC_Pass``
column; it never touches ``Included`` (manual curation / cell-gating keep that),
so re-gating QC can never clobber hand-curated include/exclude edits.
"""

from __future__ import annotations

import logging
from typing import Optional

from .params import QCParams, compute_qc_pass
from well_viewer.core.safe import swallowed

_logger = logging.getLogger("well_viewer")


def get_qc_params(app) -> QCParams:
    """Current QC thresholds, lazily seeded from the dataset's ``pipeline_info``
    on first access so the view opens with the run's settings.

    A8 residuals-ledger R4: the thresholds live on the typed ``QcState`` slice
    (``app._app_state.qc.params``) rather than a loose ``_qc_params`` attribute,
    so this reads/seeds pure typed state — the prerequisite for a ctx-based
    row-fetch data service."""
    qc = app._app_state.qc
    if qc.params is None:
        info = getattr(app._app_state.dataset, "pipeline_info", None)
        qc.params = QCParams.from_pipeline_info(info)
    return qc.params


def apply_qc_to_cache(app, label: Optional[str] = None) -> None:
    """Write the ``QC_Pass`` column onto cached well frame(s).

    With ``label`` given, only that well; otherwise every cached well. A no-op
    cheap pass when the gate is inactive (writes ``QC_Pass = 1`` so the chokepoint
    sees an explicit all-pass and stays consistent across re-gates)."""
    params = get_qc_params(app)
    cache = app._app_state.dataset.cache
    labels = [label] if label is not None else list(cache.keys())
    for lbl in labels:
        df = cache.get(lbl)
        if df is None or df.empty:
            continue
        try:
            df["QC_Pass"] = compute_qc_pass(df, params).astype(int)
        except Exception:  # never let QC break the data path
            _logger.exception("QC: failed to compute QC_Pass for well %s", lbl)


def recompute_qc_all(app) -> None:
    """Re-project ``QC_Pass`` onto all cached wells and bump the caches/version
    so downstream consumers recompute on the next read."""
    apply_qc_to_cache(app, None)
    if hasattr(app, "_invalidate_stats_cache"):
        try:
            app._invalidate_stats_cache()  # clears stats cache + bumps override_version
        except Exception:
            swallowed()  # was: pass (silent except)


def set_qc_params(app, params: QCParams) -> None:
    """Install new thresholds, recompute the verdict everywhere, persist, and
    re-render the active view so the change is immediately visible."""
    app._app_state.qc.params = params
    recompute_qc_all(app)
    if getattr(app, "_persistence", None) is not None:
        try:
            app._persistence.schedule_save("qc_params")
        except Exception:
            swallowed()  # was: pass (silent except)
    if hasattr(app, "_redraw"):
        try:
            app._redraw()
        except Exception:
            swallowed()  # was: pass (silent except)


def qc_excluded_count(app, labels=None, params: Optional[QCParams] = None) -> "tuple[int, int]":
    """``(n_excluded, n_total)`` across the given wells (default: all cached) —
    powers the live readout in the QC view. Counts only QC failures, independent
    of manual exclusions. Pass ``params`` to preview a candidate threshold set
    without committing it."""
    cache = app._app_state.dataset.cache
    labels = list(cache.keys()) if labels is None else list(labels)
    if params is None:
        params = get_qc_params(app)
    n_excl = n_tot = 0
    for lbl in labels:
        df = cache.get(lbl)
        if df is None or df.empty:
            continue
        verdict = compute_qc_pass(df, params)
        n_tot += int(len(verdict))
        n_excl += int((verdict == 0).sum())
    return n_excl, n_tot
