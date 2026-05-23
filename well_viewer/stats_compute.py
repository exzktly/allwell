"""Statistics computation helpers — replicate-set / group / per-well aggregates.

Hoisted out of ``well_viewer.runtime_app`` (Phase 11 follow-up). Lives in its
own module — not in ``stats_controller`` — because the controllers that
consume these helpers (``barplot_controller``, ``lineplot_controller``,
``scatter_controller``, ``fold_change``) are imported from places that
``stats_controller`` imports back from. Putting them here, behind a thin
imports surface, breaks the cycle.
"""

from __future__ import annotations


def well_aggregate_stats(
    app,
    wells,
    target_t: float,
    *,
    threshold: float,
    val_col: str,
    cell_area_threshold: float,
    fluor_gates: Dict[str, float],
    use_sem: bool,
    per_fov_spread: bool = False,
) -> tuple[float, float, float, float]:
    """Pool per-well means and fractions at *target_t* and return
    ``(mean, mean_err, frac, frac_err)``.

    Each loaded well contributes one mean / fraction value (per the
    active gating); sample SD (ddof=1) is taken across those values
    and divided by √n when ``use_sem`` is True. Shared between
    :meth:`_compute_rep_stats` (line / bar plot rep-set stats) and
    ``scatter_controller._agg_wells`` (scatter aggregate plot) so the
    three paths agree on what "mean across wells under active
    gating" means.

    When ``per_fov_spread`` is True the spread is taken across
    per-FOV means inside each well instead of across wells: the
    per-well mean stays the same but the returned ``mean_err`` is
    the mean of the per-well FOV spreads (a conservative pooling
    that keeps the answer well-defined for both single- and
    multi-well groups).
    """
    well_means: List[float] = []
    well_fracs: List[float] = []
    fov_mean_spreads: List[float] = []
    fov_frac_spreads: List[float] = []
    for lbl in wells:
        if lbl not in app._app_state.dataset.well_paths:
            continue
        pts = app._aggregate_well(
            lbl, threshold=threshold, use_sem=use_sem,
            val_col=val_col,
            cell_area_threshold=cell_area_threshold,
            fluor_gates=fluor_gates,
            per_fov_spread=per_fov_spread,
        )
        for pt in pts:
            if abs(pt[0] - target_t) < 1e-6:
                _, m, sd, f, fsd, *_ = pt
                if not math.isnan(m):
                    well_means.append(m)
                    if per_fov_spread and math.isfinite(sd):
                        fov_mean_spreads.append(sd)
                if not math.isnan(f):
                    well_fracs.append(f)
                    if per_fov_spread and math.isfinite(fsd):
                        fov_frac_spreads.append(fsd)
                break

    if well_means:
        arr = np.asarray(well_means, dtype=float)
        gm = float(arr.mean())
        n = arr.size
        if per_fov_spread:
            gerr = (float(np.mean(fov_mean_spreads))
                    if fov_mean_spreads else 0.0)
        else:
            gsd = float(arr.std(ddof=1)) if n > 1 else 0.0
            gerr = gsd / math.sqrt(n) if (use_sem and n > 1) else gsd
    else:
        gm, gerr = float("nan"), 0.0

    if well_fracs:
        arr = np.asarray(well_fracs, dtype=float)
        gf = float(arr.mean())
        nf = arr.size
        if per_fov_spread:
            ferr = (float(np.mean(fov_frac_spreads))
                    if fov_frac_spreads else 0.0)
        else:
            fsd = float(arr.std(ddof=1)) if nf > 1 else 0.0
            ferr = fsd / math.sqrt(nf) if (use_sem and nf > 1) else fsd
    else:
        gf, ferr = float("nan"), 0.0

    return gm, gerr, gf, ferr


def compute_rep_stats(
    app,
    rset: "ReplicateSet",
    target_t: float,
    threshold: float,
    use_sem: bool,
) -> tuple:
    """
    Mean fluor ± SD/SEM for a single ReplicateSet at *target_t*.

    Statistics are computed across the loaded wells of the set.
    Each well contributes one mean-fluor value; SD/SEM is across those values.
    Results are cached in _stats_cache.

    Thin wrapper over :meth:`_well_aggregate_stats` which carries the
    shared per-well aggregation + ddof=1 stats logic. ``_agg_wells``
    in scatter_controller delegates to the same helper so all three
    plot paths (line / bar / scatter-agg) compute the "mean across
    wells under active gating" identically.
    """
    if not True:
        app._app_state.stats.cache = {}
    cell_area_threshold = app._get_cell_area_threshold()
    fluor_gates = app._get_all_fluor_gates()
    cache_key = ("rep", rset.name, tuple(rset.wells), target_t, threshold, use_sem,
                  app._app_state.channel.active_val_col, cell_area_threshold, tuple(sorted(fluor_gates.items())))
    if cache_key in app._app_state.stats.cache:
        return app._app_state.stats.cache[cache_key]

    gm, gerr, gf, ferr = app._well_aggregate_stats(
        rset.wells, target_t,
        threshold=threshold,
        val_col=app._app_state.channel.active_val_col,
        cell_area_threshold=cell_area_threshold,
        fluor_gates=fluor_gates,
        use_sem=use_sem,
    )

    result = (gm, gerr, gf, ferr)
    app._app_state.stats.cache[cache_key] = result
    return result


def compute_rep_per_fov_stats(
    app,
    rset: "ReplicateSet",
    target_t: float,
    threshold: float,
    use_sem: bool,
) -> tuple:
    """Pool every FOV across all loaded wells of *rset* and compute stats.

    Returns ``(mean, mean_err, frac, frac_err, n_above_pf_mean,
    n_above_pf_spread)`` where the error fields are the SD/SEM across the
    pooled per-FOV means/fractions/counts. FOV identifiers are
    disambiguated by prefixing with the source well label so identical FOV
    numbers in different wells stay distinct.
    """
    if not True:
        app._app_state.stats.cache = {}
    cell_area_threshold = app._get_cell_area_threshold()
    fluor_gates = app._get_all_fluor_gates()
    cache_key = ("rep_pf", rset.name, tuple(rset.wells), target_t, threshold,
                 use_sem, app._app_state.channel.active_val_col, cell_area_threshold,
                 tuple(sorted(fluor_gates.items())))
    if cache_key in app._app_state.stats.cache:
        return app._app_state.stats.cache[cache_key]

    frames = []
    for lbl in rset.wells:
        if lbl not in app._app_state.dataset.well_paths:
            continue
        df = app._get_rows(lbl)
        if df is None or len(df) == 0:
            continue
        if "fov" in df.columns:
            fov_str = (df["fov"].fillna("1").astype(str)
                       .str.strip().replace("", "1"))
        else:
            fov_str = pd.Series(["1"] * len(df), index=df.index)
        df = df.assign(fov=(lbl + "::" + fov_str).to_numpy())
        frames.append(df)

    if not frames:
        result = (float("nan"), 0.0, float("nan"), 0.0, 0.0, 0.0)
        app._app_state.stats.cache[cache_key] = result
        return result

    pooled = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    pts = aggregate_with_threshold_df(
        pooled,
        threshold=threshold,
        use_sem=use_sem,
        val_col=app._app_state.channel.active_val_col,
        cell_area_threshold=cell_area_threshold,
        fluor_gates=fluor_gates,
        per_fov_spread=True,
        ratios=app._app_state.channel.ratio_index,
    )
    matched = [pt for pt in pts if abs(pt[0] - target_t) < 1e-6]
    if matched:
        pt = matched[0]
        result = (
            float(pt[1]),
            float(pt[2]),
            float(pt[3]),
            float(pt[6]) if len(pt) >= 7 else 0.0,
            float(pt[7]) if len(pt) >= 8 else 0.0,
            float(pt[8]) if len(pt) >= 9 else 0.0,
        )
    else:
        result = (float("nan"), 0.0, float("nan"), 0.0, 0.0, 0.0)
    app._app_state.stats.cache[cache_key] = result
    return result


def compute_rep_n_above(app, rset: "ReplicateSet", target_t: float) -> int:
    """Total events-above-threshold contributing to *rset* at *target_t*.

    Sums ``n_above`` (AggPoint index 4) across loaded wells of the set
    after gating, so the bar plot's third panel reports the count of
    cells that actually exceeded the active threshold — the same
    numerator that drives the fraction-above-threshold value.
    """
    if not True:
        app._app_state.stats.cache = {}
    threshold = app._get_thresh_frac_on(app._app_state.channel.active_channel)
    cell_area_threshold = app._get_cell_area_threshold()
    fluor_gates = app._get_all_fluor_gates()
    cache_key = ("rep_n_above", rset.name, tuple(rset.wells), target_t, threshold,
                 app._app_state.channel.active_val_col, cell_area_threshold,
                 tuple(sorted(fluor_gates.items())))
    if cache_key in app._app_state.stats.cache:
        return app._app_state.stats.cache[cache_key]

    total = 0
    for lbl in rset.wells:
        if lbl not in app._app_state.dataset.well_paths:
            continue
        pts = app._aggregate_well(
            lbl, threshold=threshold, use_sem=False,
            val_col=app._app_state.channel.active_val_col,
            cell_area_threshold=cell_area_threshold,
            fluor_gates=fluor_gates,
        )
        matched = [pt for pt in pts if abs(pt[0] - target_t) < 1e-6]
        if matched:
            # AggPoint index 4 = n_above; index 5 = n_total.
            total += int(matched[0][4])
    app._app_state.stats.cache[cache_key] = total
    return total


def compute_group_stats(
    app,
    grp: "BarGroup",
    target_t: float,
    threshold: float,
    use_sem: bool,
) -> tuple:
    """
    Compute mean ± error for a group at *target_t*, respecting replicate sets.

    Results are cached keyed by (group_name, wells_tuple, target_t, threshold,
    use_sem) so repeated calls during the same redraw are free.  The cache is
    cleared by _invalidate_stats_cache() whenever group definitions change.
    """
    if not True:
        app._app_state.stats.cache = {}
    cell_area_threshold = app._get_cell_area_threshold()
    fluor_gates = app._get_all_fluor_gates()
    cache_key = (grp.name, tuple(grp.wells), target_t, threshold, use_sem,
                  app._app_state.channel.active_val_col, cell_area_threshold, tuple(sorted(fluor_gates.items())))
    if cache_key in app._app_state.stats.cache:
        return app._app_state.stats.cache[cache_key]

    rep_means: List[float] = []
    rep_fracs: List[float] = []

    for rset in grp.replicate_sets():
        set_means: List[float] = []
        set_fracs: List[float] = []
        for lbl in rset:
            if lbl not in app._app_state.dataset.well_paths:
                continue
            pts = app._aggregate_well(
                lbl, threshold=threshold, use_sem=False,
                val_col=app._app_state.channel.active_val_col,
                cell_area_threshold=cell_area_threshold,
                fluor_gates=fluor_gates,
            )
            matched = [pt for pt in pts if abs(pt[0] - target_t) < 1e-6]
            if matched:
                _, m, _sd, f, *_ = matched[0]
                if not math.isnan(m): set_means.append(m)
                if not math.isnan(f): set_fracs.append(f)
        if set_means:
            rep_means.append(statistics.mean(set_means))
        if set_fracs:
            rep_fracs.append(statistics.mean(set_fracs))

    if rep_means:
        gm   = statistics.mean(rep_means)
        n_m  = len(rep_means)
        g_sd = statistics.pstdev(rep_means) if n_m > 1 else 0.0
        g_err_m = g_sd / math.sqrt(n_m) if (use_sem and n_m > 1) else g_sd
    else:
        gm, g_err_m = float("nan"), 0.0

    if rep_fracs:
        gf   = statistics.mean(rep_fracs)
        n_f  = len(rep_fracs)
        f_sd = statistics.pstdev(rep_fracs) if n_f > 1 else 0.0
        g_err_f = f_sd / math.sqrt(n_f) if (use_sem and n_f > 1) else f_sd
    else:
        gf, g_err_f = float("nan"), 0.0

    result = (gm, g_err_m, gf, g_err_f)
    app._app_state.stats.cache[cache_key] = result
    return result


