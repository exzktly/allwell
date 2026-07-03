"""Capture the parameters/thresholds behind a plot + render a readable summary.

``capture_params`` leans on the existing ``view_state.snapshot`` so the saved
record is both a faithful parameter dump AND directly replayable via
``view_state.restore`` (that's how the gallery's Restore action works).
``build_summary`` turns that dump into the human-readable blurb shown under each
gallery thumbnail.
"""

from __future__ import annotations

import logging
from typing import Any

from well_viewer.core.lazy import lazy_names
from well_viewer.persistence import view_state

logger = logging.getLogger(__name__)

# Stats is a removable feature, so it can't be imported at module scope; the lazy
# proxies defer the import to first call (a call, not an ``import`` statement, so
# it stays off the deferred-feature-import budget — see tests/test_deferred_imports).
_serialize_stats_groups, _threshold_for_val_col = lazy_names(
    "well_viewer.features.stats.controller",
    "serialize_stats_groups", "threshold_for_val_col",
)


def capture_params(app, tab: str) -> tuple[dict, dict]:
    """Return ``(params, thresholds)`` for the plot currently on *tab*.

    *params* is a full ``view_state`` snapshot (replayable by Restore);
    *thresholds* collects the gating cuts that aren't part of view-state but
    still shape fraction-on / inclusion plots.
    """
    params = view_state.snapshot(app)
    # Pin the originating tab so Restore navigates back here even if the user
    # had since moved to another tab before the snapshot ran.
    if tab:
        params["active_centre_tab"] = tab

    # The bar / line / scatter plots are group-driven — their traces come from
    # the replicate-set *selections*, which ``view_state`` does NOT snapshot. Save
    # them so Restore reproduces the exact groups on the plot (not whatever the
    # sidebar happens to hold at restore time).
    sels = _capture_selections(app)
    if sels:
        params["selections"] = sels

    # The group-driven plots draw the rep-set groups PLUS any individual wells the
    # user soloed onto the plot (``active_solo_wells``) — also absent from the
    # view_state snapshot. Always store it (even empty) so Restore reproduces the
    # exact plotting selection, clearing stale solo wells when the saved plot had
    # none.
    params["active_solo_wells"] = sorted(
        getattr(app._app_state.selection, "active_solo_wells", None) or [])

    # Significance brackets (Bar plot) are recomputed on Restore by re-running the
    # same stats test, so capture the test CONFIG (groups + context). Only when a
    # test has actually been run (``pairwise`` non-empty) and ≥2 groups exist.
    stats_cfg = _capture_stats_config(app)
    if stats_cfg is not None:
        params["stats"] = stats_cfg

    thresholds: dict[str, Any] = {}
    fluor = _safe(lambda: app._get_all_fluor_gates())
    if isinstance(fluor, dict) and fluor:
        thresholds["fluor_gates"] = {str(k): float(v) for k, v in fluor.items()}
    tfo = _safe(lambda: dict(app._app_state.cell_gating.thresh_frac_on))
    if isinstance(tfo, dict) and tfo:
        thresholds["thresh_frac_on"] = {str(k): float(v) for k, v in tfo.items()}
    return params, thresholds


def _capture_selections(app) -> list | None:
    """A JSON-safe deep copy of the live replicate-set selections (the group
    definitions that drive the group-based plots), or None when there are none."""
    try:
        import copy
        sels = getattr(app._app_state.selection, "selections", None) or []
        if not sels:
            return None
        return copy.deepcopy(list(sels))
    except Exception:
        # Don't lose the saved plot's group definitions silently (audit E10).
        logger.exception("saved plot: failed to capture selections; groups not saved")
        return None


def _capture_stats_config(app) -> dict | None:
    """Config to re-run the current stats test on Restore (or None if there's
    nothing to reproduce). Derives the run context from the live ``pairwise``
    results (test / val_col / target_t / statistic) so it matches exactly what
    produced the on-screen brackets; the threshold is resolved from that val_col.
    """
    st = getattr(app._app_state, "stats", None)
    pairwise = list(getattr(st, "pairwise", []) or []) if st else []
    groups = [g for g in (getattr(st, "groups", []) or []) if getattr(g, "wells", None)]
    if not pairwise or len(groups) < 2:
        return None
    ctx = pairwise[0]  # test/val_col/target_t/stat are constant across the run
    val_col = str(ctx.get("val_col", "") or "")
    try:
        threshold = float(_threshold_for_val_col(app, val_col))
        grp_data = _serialize_stats_groups(groups)
    except Exception:
        # Don't lose the saved plot's stats config silently (audit E10).
        logger.exception("saved plot: failed to capture stats config; not saved")
        return None
    return {
        "groups": grp_data,
        "test": str(ctx.get("test", "") or ""),
        "target_t": ctx.get("target_t"),
        "val_col": val_col,
        "statistic": str(ctx.get("stat", "mean") or "mean"),
        "threshold": threshold,
        # The FOV-replicate mode governs the test's replicate unit; capture it so
        # a restored plot re-runs with the same n and redraws identical brackets.
        "use_fov": bool(_safe(lambda: app._use_fov_spread_active())),
    }


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


def _fc_summary(fc: dict) -> str:
    parts = []
    if fc.get("vs_control_on"):
        op = fc.get("op_ctrl", "ratio")
        parts.append(f"vs {fc.get('control_label') or '?'} ({op})")
    if fc.get("vs_t0_on"):
        op = fc.get("op_t0", "ratio")
        base = fc.get("baseline_tp") or "t0"
        parts.append(f"vs {base} ({op})")
    return " · ".join(parts) if parts else "off"


def build_summary(app, tab: str, params: dict, thresholds: dict) -> str:
    """A compact, human-readable description of the saved plot's settings."""
    lines: list[str] = [f"Tab: {tab}"]

    wells = params.get("selected_wells") or []
    if wells:
        shown = ", ".join(str(w) for w in wells[:8])
        more = f" (+{len(wells) - 8})" if len(wells) > 8 else ""
        lines.append(f"Wells ({len(wells)}): {shown}{more}")

    ch = params.get("active_channel") or "?"
    metric = params.get("active_metric") or "?"
    lines.append(f"Channel · metric: {ch} · {metric}")

    fc = params.get("fold_change") or {}
    lines.append(f"Fold change: {_fc_summary(fc)}")

    if params.get("use_sem") is not None:
        band = "SEM" if params.get("use_sem") else "SD"
        fov = " · per-FOV" if params.get("use_fov_replicates") else ""
        lines.append(f"Error band: {band}{fov}")

    stats_cfg = params.get("stats")
    if isinstance(stats_cfg, dict):
        unit = "per-FOV (FOV mode)" if stats_cfg.get("use_fov") else "per-well"
        lines.append(f"Stats replicates: {unit}")

    tab_combos = (params.get("tabs") or {}).get(tab) or {}
    if tab_combos:
        pretty = ", ".join(f"{k}={v}" for k, v in tab_combos.items())
        lines.append(f"Settings: {pretty}")

    fg = thresholds.get("fluor_gates") or {}
    if fg:
        g = fg.get(ch)
        if g is not None:
            lines.append(f"FluorGating[{ch}]: {g:g}")
    tfo = thresholds.get("thresh_frac_on") or {}
    if tfo:
        t = tfo.get(ch)
        if t is not None:
            lines.append(f"ThreshFracOn[{ch}]: {t:g}")

    return "\n".join(lines)
