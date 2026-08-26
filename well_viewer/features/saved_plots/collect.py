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

from well_viewer.core.channel_state_controller import (
    get_all_fluor_gates,
    seg_channel_token_of,
)
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


def capture_params(ctx, tab: str) -> tuple[dict, dict]:
    """Return ``(params, thresholds)`` for the plot currently on *tab*.

    *params* is a full ``view_state`` snapshot (replayable by Restore);
    *thresholds* collects the gating cuts that aren't part of view-state but
    still shape fraction-on / inclusion plots.
    """
    # ``view_state.snapshot`` walks the persisted UI state directly (the
    # snapshot/restore layer is ctx-native and unwraps the app internally).
    params = view_state.snapshot(ctx)
    # Pin the originating tab so Restore navigates back here even if the user
    # had since moved to another tab before the snapshot ran.
    if tab:
        params["active_centre_tab"] = tab

    # The bar / line / scatter plots are group-driven — their traces come from
    # the replicate-set *selections*, which ``view_state`` does NOT snapshot. Save
    # them so Restore reproduces the exact groups on the plot (not whatever the
    # sidebar happens to hold at restore time).
    sels = _capture_selections(ctx)
    if sels:
        params["selections"] = sels

    # The group-driven plots draw the rep-set groups PLUS any individual wells the
    # user soloed onto the plot (``active_solo_wells``) — also absent from the
    # view_state snapshot. Always store it (even empty) so Restore reproduces the
    # exact plotting selection, clearing stale solo wells when the saved plot had
    # none.
    params["active_solo_wells"] = sorted(
        getattr(ctx.state.selection, "active_solo_wells", None) or [])

    # Significance brackets (Bar plot) are recomputed on Restore by re-running the
    # same stats test, so capture the test CONFIG (groups + context). Only when a
    # test has actually been run (``pairwise`` non-empty) and ≥2 groups exist.
    stats_cfg = _capture_stats_config(ctx)
    if stats_cfg is not None:
        params["stats"] = stats_cfg

    # The full gating block — fluor gates, ThreshFracOn / ThreshCountOn cuts, AND the scatter-gate
    # inclusion/thresholding config — captured with the SAME serializer the
    # pipeline-info sidecar uses (``build_gating_block``), so Restore can re-apply
    # the exact Included population that shaped the saved figure. Stored on
    # ``SavedPlot.thresholds`` (keys ``fluor_gates`` / ``thresh_frac_on`` are
    # unchanged, so ``build_summary`` + older records still read cleanly).
    thresholds: dict[str, Any] = _safe(lambda: _capture_gating_block(ctx)) or {}
    return params, thresholds


def _capture_gating_block(ctx) -> dict:
    """The non-default gating block (gate values + scatter-gate config) for the
    live ``ctx.state.cell_gating`` slice — mirrors ``cell_gating.save_to_pipeline_info``."""
    from well_viewer.domain.gating_state import build_gating_block

    cg = ctx.state.cell_gating

    def _sorted_or_none(s):
        return None if s is None else sorted(s)

    return build_gating_block(
        get_all_fluor_gates(ctx),
        dict(cg.thresh_frac_on),
        scatter_gates=cg.scatter_gates or None,
        use_scatter_gates=cg.use_scatter_gates,
        scatter_gate_choices_gating=_sorted_or_none(cg.gating_choices),
        scatter_gate_choices_thresh=_sorted_or_none(cg.thresh_choices),
        use_scatter_thresh=cg.use_scatter_thresh,
        seg_channel_token=seg_channel_token_of(ctx),
        thresh_count_on=dict(cg.thresh_count_on),
    )


def _capture_selections(ctx) -> list | None:
    """A JSON-safe deep copy of the live replicate-set selections (the group
    definitions that drive the group-based plots), or None when there are none."""
    try:
        import copy
        sels = getattr(ctx.state.selection, "selections", None) or []
        if not sels:
            return None
        return copy.deepcopy(list(sels))
    except Exception:
        # Don't lose the saved plot's group definitions silently (audit E10).
        logger.exception("saved plot: failed to capture selections; groups not saved")
        return None


def _capture_stats_config(ctx) -> dict | None:
    """Config to re-run the saved plot's stats tests on Restore (or None if
    there's nothing to reproduce).

    **Every stored comparison, not just the most recent one.** The Statistics
    tab keeps its runs keyed by ``(val_col, target_t, stat)`` precisely so that
    testing a second channel — or a second statistic, or a second timepoint —
    adds a comparison rather than replacing the first, and a Bar plot draws
    whichever of them describe the bars in front of it. ``stats.pairwise`` is a
    derived view of that store meaning "the last run recorded", so capturing it
    saved one comparison out of however many the figure was drawing; a plot of
    GFP bars saved after a CY5 test had been run came back from Restore with the
    CY5 config, which matches no bar on it, and therefore with no brackets at
    all.

    The run context (test / val_col / target_t / statistic) is derived from the
    stored records themselves rather than from the live sidebar, so it matches
    exactly what produced the on-screen brackets; each run's threshold is
    resolved from its own val_col. ``groups`` and ``use_fov`` are shared: one
    set of comparison groups is tested, and the FOV-replicate mode governs the
    replicate unit for all of them.
    """
    st = getattr(ctx.state, "stats", None)
    groups = [g for g in (getattr(st, "groups", []) or []) if getattr(g, "wells", None)]
    contexts = _stored_run_contexts(st)
    if not contexts or len(groups) < 2:
        return None
    try:
        grp_data = _serialize_stats_groups(groups)
        runs = [_stats_run_block(ctx, record) for record in contexts]
    except Exception:
        # Don't lose the saved plot's stats config silently (audit E10).
        logger.exception("saved plot: failed to capture stats config; not saved")
        return None
    cfg: dict[str, Any] = {
        "groups": grp_data,
        "runs": runs,
        # The FOV-replicate mode governs each test's replicate unit; capture it so
        # a restored plot re-runs with the same n and redraws identical brackets.
        "use_fov": bool(ctx.state.plot.use_fov_replicates),
    }
    # The most recent run also sits at the top level, which is where a record
    # written before the block held several runs kept its only one. That keeps
    # one shape for every reader of a single run's context — ``build_summary``,
    # the tests, an operator reading the JSON — rather than two.
    cfg.update(runs[-1])
    return cfg


def _stored_run_contexts(st) -> list[dict]:
    """One representative record per stored comparison run, in the order run.

    A run's records all share its context, so the first of each is enough to
    re-run it. Falls back to ``pairwise`` for a state object that carries no
    ``runs`` store at all, so a hand-built fixture still captures its one run.
    """
    if st is None:
        return []
    stored = getattr(st, "runs", None)
    if isinstance(stored, dict):
        return [records[0] for records in stored.values()
                if records and isinstance(records[0], dict)]
    pairwise = list(getattr(st, "pairwise", []) or [])
    return [pairwise[0]] if pairwise and isinstance(pairwise[0], dict) else []


def _stats_run_block(ctx, record: dict) -> dict:
    """One stored run's re-runnable context, threshold resolved from its own
    value column (a per-channel cut, so two runs on two channels do not share
    one)."""
    val_col = str(record.get("val_col", "") or "")
    return {
        "test": str(record.get("test", "") or ""),
        "target_t": record.get("target_t"),
        "val_col": val_col,
        "statistic": str(record.get("stat", "mean") or "mean"),
        "threshold": float(_threshold_for_val_col(ctx, val_col)),
    }


def _safe(fn) -> Any:
    try:
        return fn()
    except Exception:
        return None


def _fc_control_phrase(fc: dict) -> str:
    """``vs …`` for the control half, from the saved block alone."""
    if str(fc.get("control_mode") or "single") != "per_member":
        return f"vs {fc.get('control_label') or '?'}"
    raw_map = fc.get("control_map")
    if not isinstance(raw_map, dict) or not raw_map:
        return "vs own control (none set)"
    names = []
    for value in raw_map.values():
        name = (value.get("name") if isinstance(value, dict) else value) or ""
        if name and name not in names:
            names.append(str(name))
    if len(names) == 1:
        return f"vs {names[0]}"
    if len(names) == 2:
        return f"vs {names[0]} / {names[1]}"
    # ``names``, not ``raw_map``: the two branches above already dedupe, so
    # counting entries here made five assignments naming three controls read
    # "\u00d75" \u2014 a count of the mapping's size dressed up as a count of controls.
    return f"vs own control (\u00d7{len(names)})"


def _fc_summary(fc: dict) -> str:
    """The fold-change line of a saved plot's summary.

    Read from the persisted block, not from live state, so it has no ``ctx``
    and cannot resolve anything. Under per-sample controls that means it can
    count the *entries* the document carries and name them when there are few,
    but it must not claim a resolution count it has no way to compute — the
    document may have been saved against a dataset this session has not loaded.
    """
    parts = []
    if fc.get("vs_control_on"):
        op = fc.get("op_ctrl", "ratio")
        parts.append(f"{_fc_control_phrase(fc)} ({op})")
    if fc.get("vs_t0_on"):
        op = fc.get("op_t0", "ratio")
        base = fc.get("baseline_tp") or "t0"
        parts.append(f"vs {base} ({op})")
    return " · ".join(parts) if parts else "off"


def build_summary(tab: str, params: dict, thresholds: dict) -> str:
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
    tco = thresholds.get("thresh_count_on") or {}
    if tco:
        c = tco.get(ch)
        if c is not None:
            lines.append(f"ThreshCountOn[{ch}]: {c:g}")

    return "\n".join(lines)
