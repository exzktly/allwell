"""App-facing QC layer: hold the live thresholds, project ``QC_Pass`` onto the
row cache, and drive a live re-render when the thresholds change.

The QC verdict is composed with manual curation at the inclusion chokepoint
(:func:`review.data.data_loading.df_included_mask`): a row counts only when
``Included == 1`` *and* ``QC_Pass == 1``. This module owns the ``QC_Pass``
column; it never touches ``Included`` (manual curation / cell-gating keep that),
so re-gating QC can never clobber hand-curated include/exclude edits.
"""

from __future__ import annotations

import logging


from .params import QCParams, compute_qc_pass
from core.safe import swallow, swallowed
from review.data.cache_projectors import project_verdict_column

_logger = logging.getLogger("review")


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


def qc_input_columns(ctx, label: str | None = None) -> tuple[str, ...]:
    """The columns :func:`apply_qc_to_cache`'s verdict reads (``narrow_rows``)."""
    from review.domain.qc_params import AREA_COLS, CIRCULARITY_COL, REGION_AREA_COL, SOLIDITY_COL

    if not get_qc_params(ctx).is_active:
        return ()
    return (*AREA_COLS, SOLIDITY_COL, REGION_AREA_COL, CIRCULARITY_COL)


def apply_qc_to_cache(ctx, label: str | None = None) -> list[str]:
    """Write the ``QC_Pass`` column onto cached well frame(s).

    With ``label`` given, only that well; otherwise every cached well. A no-op
    cheap pass when the gate is inactive (writes ``QC_Pass = 1`` so the chokepoint
    sees an explicit all-pass and stays consistent across re-gates). Shares the
    cache walk with ``crop.controller.apply_crop_to_cache`` via
    :func:`review.data.cache_projectors.project_verdict_column`.

    A8 residuals-ledger R1: takes ``ctx`` — the params and the row cache both
    come from typed state, so the QC projection is a pure ``ctx`` operation the
    row-fetch data service can call.

    Returns the labels whose verdict could not be computed — see
    :func:`review.data.cache_projectors.project_verdict_column`, which
    drops ``QC_Pass`` from those frames rather than leaving the previous
    parameters' values on them. Registered as a cache projector, so the
    per-well load path calls it and discards the answer (a load already logs
    per well); the re-projection path in :func:`recompute_qc_all` is where it
    reaches the user, because that is where the gate the user just moved
    silently stops applying to a well."""
    params = get_qc_params(ctx)
    return project_verdict_column(
        ctx, label,
        column="QC_Pass",
        verdict=lambda df, _well: compute_qc_pass(df, params),
        layer="QC",
    )


def recompute_qc_all(ctx) -> None:
    """Re-project **every** verdict layer onto all cached wells and bump the
    caches/version so downstream consumers recompute on the next read.

    A8 teardown: takes ``ctx`` — projects QC over typed state and invalidates the
    stats cache through the ctx-native ``invalidate_stats_cache`` core helper.

    **It re-runs the other registered projectors too, and that is a fix rather
    than a tidy-up.** The cache projectors run at *load*, and this is the one
    pass that runs when a control on the Cell Gating panel moves. For a long
    time it re-projected ``QC_Pass`` alone, which was correct while the panel
    held only the debris gate — and silently wrong the moment it grew a second
    switchable layer. Measured on a plate with one field flagged: ticking
    "Exclude fields the run flagged" wrote no ``Field_Pass`` column at all, the
    chokepoint saw nothing to AND, and the exclusion did not happen until the
    dataset was reopened. The panel meanwhile reported the flag correctly, so
    the control looked like it had worked.

    Going through the registry rather than naming the layers keeps that from
    recurring: a verdict layer added later is re-projected by having been
    registered, which is the property the registry exists for. The projectors
    are the same callables ``load_and_project`` runs, so this cannot apply a
    different set of layers than a freshly opened dataset carries.
    """
    finish_qc_recompute(ctx, reproject_verdicts(ctx))


def reproject_verdicts(ctx, *, progress=None) -> list[str]:
    """The data half of :func:`recompute_qc_all`: rewrite every verdict layer.

    Touches no widget, so it may run off the GUI thread — the Cell Gating
    panel's edits run it behind a progress popup
    (:func:`set_qc_params_with_progress`) while nothing else writes the frames.
    Each layer walks every cached well in turn, exactly as the synchronous path
    does; *progress* (``(done, total)``) hears once per layer, because a layer
    called with a label instead of ``None`` is not the same operation — the
    gating projector re-gates a labelled well and skips the unlabelled walk, and
    curation reads a labelled well from disk.

    Returns the wells whose QC verdict could not be computed, for
    :func:`finish_qc_recompute` to report.
    """
    from review.data.cache_projectors import iter_cache_projectors
    projectors = iter_cache_projectors()
    total = 1 + len(projectors)
    failed = apply_qc_to_cache(ctx, None)
    if progress is not None:
        progress(1, total)
    for done, projector in enumerate(projectors, start=2):
        # QC's own is in this list and has just run; running it twice is a
        # pure recompute of the same verdict from the same params, and
        # skipping it would mean matching callables by identity through a
        # lazy-import wrapper.
        try:
            projector(ctx, None)
        except Exception:
            # One layer must never take down the others, and each already
            # reports its own per-well failures through
            # ``project_verdict_column``. This catches only the case where a
            # projector raises outside that walk.
            _logger.exception("verdict re-projection failed for %r", projector)
        if progress is not None:
            progress(done, total)
    return failed


def finish_qc_recompute(ctx, failed: list[str]) -> None:
    """The GUI half of :func:`recompute_qc_all`, after the frames are rewritten."""
    from review.core.notifications import report_verdict_failures
    from review.core.selection_controller import invalidate_stats_cache
    # Reported here, at the re-projection, rather than at the per-well load:
    # this is the pass that runs when the user moves a threshold, and it is the
    # only moment at which a well can silently stop being gated by the settings
    # the controls are showing.
    report_verdict_failures(ctx, layer="QC", column="QC_Pass", failed=failed)
    try:
        invalidate_stats_cache(ctx)  # clears stats cache + bumps override_version
    except Exception:
        swallowed()  # was: pass (silent except)
    # QC_Pass just moved, which moves the composed inclusion every other layer
    # reads. Tell whoever registered without naming them — the Cell Gating tab
    # stamps this to keep its inclusion readout honest, and its absence (or the
    # feature's) simply leaves the callback None.
    cb = ctx.runtime.qc.changed_cb
    if callable(cb):
        try:
            cb()
        except Exception:
            swallowed()  # a listener must never break the QC recompute


def _after_qc_params_applied(ctx) -> None:
    """Repaint, save and redraw once new thresholds are on every frame."""
    _repaint_quality_markers(ctx)
    try:
        pers = ctx.persistence
        if pers is not None:
            pers.schedule_save("qc_params")
    except Exception:
        swallowed()  # was: pass (silent except)
    try:
        from review.core.plot_orchestrator import redraw_app
        redraw_app(ctx)
    except Exception:
        swallowed()  # was: pass (silent except)
    # ``redraw_app`` reaches Line, Distribution and Heat Map. The Nanowells page draws from the same parameters
    # (its rim focus rule and the cells Cell Gating keeps) and is not one of those scopes, so it is told directly;
    # without it, the page beside Cell Gating in Split View went on drawing under the previous rule.
    with swallow("Nanowells: follow the QC parameters"):
        ctx.features.call("nanowell.follow_qc_params", ctx)
    # The Plotting band's Population picker lists the size classes' labels and is offered only while the rule is
    # complete, so any QC edit — a renamed class, a range made incomplete — refreshes it.
    with swallow("Plotting band: follow the size classes"):
        from review.core.population_band import refresh as refresh_population

        refresh_population(ctx)
    # Each class's share and the classes' ratio are channels (``size_classes.class_columns``): a named, renamed or
    # cleared class, or a one-class Population, moves the list.
    with swallow("Channel lists: follow the size classes"):
        from review.core.channel_state_controller import sync_class_channels

        sync_class_channels(ctx)


def only_size_classes_differ(params: QCParams, current: QCParams) -> bool:
    """Whether *params* differs from *current* in the size classes alone (``QCParams.size_classes``), **and** the
    analysis is restricted to no class under either: while it is (``QCParams.population_rule``), moving a class's
    range moves cells in or out of every aggregate (``Population_Pass``), so the edit must take the full
    re-projection rather than the light path."""
    from dataclasses import replace

    if params.population_rule() is not None or current.population_rule() is not None:
        return False
    return params != current and replace(params, size_classes=current.size_classes) == current


def set_population(ctx, population: int | None) -> None:
    """Restrict the whole analysis to size class *population* (its index in the rule), or to every cell (``None``).

    The Plotting band's **Population** control calls this (``qc.set_population``). The choice is a QC parameter
    (``QCParams.population``), so it is saved and restored with the rest of Cell Gating, and it is applied the way
    every QC edit is: every verdict layer re-projected behind a progress popup — the one it moves is
    ``Population_Pass`` (``review.data.population_gate``) — then the save and the redraw of every plot. The
    Segmentation view, the Cell Gating panel and the band are refreshed once the pass is done, from the rows it
    rewrote; the band itself follows from the redraw (``_after_qc_params_applied``)."""
    from dataclasses import replace

    current = get_qc_params(ctx)
    if current.population == population:
        return

    def _after(_counts) -> None:
        _redraw_segmentation(ctx, "follow the population")
        with swallow("Cell Gating: follow the population"):
            ctx.features.call("qc.refresh_gate_widgets", ctx.window)

    set_qc_params_with_progress(ctx, replace(current, population=population), on_done=_after)


#: The per-cell column prefix a gate drawn on a size-class metric reads (``observation.NANOWELL_CELL_PREFIX`` and
#: ``size_class_compute``'s ``size_`` columns).
SIZE_GATE_PREFIX = "nw::size_"


def gates_read_size_classes(ctx) -> bool:
    """Whether a scatter gate is drawn on a size-class metric (a "Nanowell ▸ T cells count" axis): its verdicts
    depend on the classes, so a change of them alone must re-apply the gates."""
    gates = getattr(getattr(ctx.state, "cell_gating", None), "scatter_gates", None) or []
    return any(str((g or {}).get(k) or "").startswith(SIZE_GATE_PREFIX)
               for g in gates if isinstance(g, dict) for k in ("x_col", "y_col"))


def set_size_classes(ctx, params: QCParams) -> None:
    """Install QC parameters that differ from those in force only in their size classes.

    The size classes are not an inclusion layer — they leave every cell in and only say which class a kept cell is
    counted in — so, while the analysis is restricted to no class (:func:`only_size_classes_differ` sends any other
    edit down the full path), no verdict column moves and the re-projection :func:`set_qc_params_with_progress` runs behind a
    progress popup would recompute every held well for nothing. The parameters go into state and are saved, and the
    surfaces that count by class are redrawn: the Nanowells page (``nanowell.follow_qc_params``) and the plots
    (``redraw_app``), whose nanowell rows are keyed on the rule (``nanowell.controller.gate_version``) and so are
    counted again. Cell Gating's cell-area curve, which shades the classes, is drawn again too
    (``cell_gating.replot_cdfs``), and so is the Segmentation view, which outlines each kept cell in its class's
    colour (``review_image.refresh``).

    **One exception includes or leaves out cells**: a scatter gate drawn on a size-class metric judges each cell by
    its nanowell's class counts, so while one is drawn (:func:`gates_read_size_classes`) a gating pass is started
    (``cell_gating.start_worker``), which re-applies every gate against counts under the new classes."""
    ctx.state.qc.params = params
    # The class columns (each class's share and the classes' ratio, ``size_classes.class_columns``) are per-cell
    # values the plots read: rewritten on the held rows under the new rule (no verdict moves — no class is chosen on
    # this path), and the aggregates built on the old values dropped, before anything is drawn again.
    from review.core.selection_controller import invalidate_stats_cache
    from review.data.population_gate import apply_population_to_cache

    apply_population_to_cache(ctx)
    invalidate_stats_cache(ctx)
    _after_qc_params_applied(ctx)
    if gates_read_size_classes(ctx):
        with swallow("Cell Gating: re-apply the gates drawn on size classes"):
            ctx.features.call("cell_gating.start_worker", ctx)
    with swallow("Cell Gating: shade the size classes"):
        ctx.features.call("cell_gating.replot_cdfs", ctx)
    # The Segmentation view outlines each kept cell in its class's colour, so it is drawn again too — kept at the
    # zoom and pan the user had, since the field itself has not changed.
    _redraw_segmentation(ctx, "outline the size classes")


def _redraw_segmentation(ctx, why: str) -> None:
    """Draw the Segmentation view again at the zoom and pan it had — the field itself has not changed. Only when
    the view is built: the flag would otherwise outlive this edit and keep the zoom of the next field."""
    built = getattr(getattr(getattr(ctx, "runtime", None), "review_image", None), "label", None) is not None
    review_image = getattr(ctx.state, "review_image", None)
    if built and review_image is not None:
        with swallow(f"Segmentation: {why}"):
            review_image.preserve_view_on_refresh = True
            ctx.features.call("review_image.refresh", ctx)


def set_qc_params_with_progress(ctx, params: QCParams, *, on_done=None) -> None:
    """Install new QC thresholds from the panel and apply them, the work off the GUI thread.

    The one route a threshold edit takes (``qc/tab_view._apply``). The
    thresholds go into state at once, so the panel and the saved file agree
    with what the user typed; the re-projection of every verdict layer over every
    held well, and the panel's flagged-cell count, run on a thread behind a
    progress popup (``review.core.progress_jobs``); the save, the plate's
    markers and the redraw follow on the GUI thread. *on_done(counts)* — the
    ``(n_excluded, n_total)`` of :func:`qc_excluded_count`, measured on the same
    thread over the frames just rewritten — then lets the panel repaint its own
    sentences.

    Not cancellable: the walk rewrites the wells one by one, and stopping it
    part-way would leave some under the new thresholds and some under the old
    while the boxes showed the new. Two edits queued behind a running one
    collapse to the last (``key``), which reads the thresholds in force when it
    starts and so already carries both.
    """
    from review.core import progress_jobs

    ctx.state.qc.params = params

    def _work(report):
        report.stage("Re-applying the QC gate and the other inclusion layers…")
        failed = reproject_verdicts(ctx, progress=report.progress)
        report.stage("Counting the cells the gate flags…")
        return failed, qc_excluded_count(ctx)

    def _done(result) -> None:
        failed, counts = result
        finish_qc_recompute(ctx, failed)
        _after_qc_params_applied(ctx)
        if on_done is not None:
            on_done(counts)

    def _failed(message: str) -> None:
        # The walk stopped part-way. The thresholds stay in state (they are what
        # the boxes show) and are saved, every memo of the frames is dropped,
        # the plots redraw from what the frames now hold, and the panel repaints
        # — so the count on screen describes the frames rather than the last
        # good apply — and the status line says the apply failed.
        _logger.error("QC thresholds could not be applied: %s", message)
        finish_qc_recompute(ctx, [])
        _after_qc_params_applied(ctx)
        if on_done is not None:
            with swallow("QC apply failure: repaint the panel"):
                on_done(qc_excluded_count(ctx))
        with swallow("QC apply failure status"):
            ctx.notify.set_status(f"QC thresholds could not be applied: {message}")

    progress_jobs.run_in_background(
        ctx, title="Applying QC",
        text="Re-applying the QC gate to every loaded well…",
        work=_work, on_done=_done, on_failed=_failed, key="qc.params",
    )


def _repaint_quality_markers(ctx) -> None:
    """Tell Home's plate the cut moved, if Home has been built.

    The plate's markers come from ``wells_needing_review``, which is a
    function of ``params.focus_min``. Nothing in the redraw fan-out reaches
    Home -- it is a page rather than a plot scope -- so without this the plate
    and the panel that set the cut disagree until the user leaves the page and
    comes back. The callback is installed by Home's own builder and is absent
    before the page is built, which is the normal state on a fresh load.
    """
    cb = ctx.runtime.home.quality_refresh_cb
    if cb is None:
        return
    with swallow("home quality markers"):
        cb()


def qc_excluded_count(ctx, labels=None, params: QCParams | None = None) -> tuple[int, int]:
    """``(n_excluded, n_total)`` across the given wells (default: the wells the
    row cache holds, which on a plate larger than its budget is not every well —
    the readout appends ``data_loading.held_wells_note`` to say so) — powers the
    live readout in the QC view. Counts only QC failures, independent
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
