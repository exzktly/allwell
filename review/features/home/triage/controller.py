"""What the QC triage table's buttons do.

Every action here changes something the rest of the app already owns, through
the path that already owns it, so triage adds no second way to do anything:

* **Exclude field** writes ``ctx.state.dataset.field_exclusions``, saves it at
  once (a decision to remove data must not wait on a debounce timer the user
  can quit through), and re-projects every verdict layer through
  ``qc.recompute_all`` — the same pass the Cell Gating panel runs — before the
  one redraw fan-out (``plot_orchestrator.redraw_app``).
* **Accept** and **Reopen** write ``ctx.state.dataset.triage_decisions``.
* **Re-run wells** hands the dataset to Analyze through Home's own hand-off
  (``home.controller.open_in_analyze``), with the chosen wells both forced and
  the only wells run, so nothing else on the plate is touched. Nothing is run
  from here: Analyze opens configured, and the user presses Run.

Each change reaches the provenance log by the route every analysis change does
(``review/persistence/provenance.py``): the exclusion and decision blocks are
analysis domains, recorded when their file is written.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.safe import swallow

from . import collect as tc

_logger = logging.getLogger("review")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _after_exclusions_changed(ctx) -> None:
    with swallow("save the field exclusions"):
        ctx.persistence.save("field_exclusions")
    with swallow("re-project the inclusion layers after a field exclusion"):
        ctx.features.call("qc.recompute_all", ctx)
    with swallow("redraw after a field exclusion"):
        from review.core.plot_orchestrator import redraw_app
        redraw_app(ctx)


def exclude_fields(ctx, rows, reason: str) -> int:
    """Exclude every excludable field in *rows* with *reason*. Returns how many
    were newly excluded; rows that cannot be excluded are skipped, not refused,
    and the table says why beside each."""
    reason = str(reason or "").strip()
    if not reason:
        return 0
    store = ctx.state.dataset.field_exclusions
    added = 0
    for row in rows:
        if not row.can_exclude:
            continue
        key = tc.field_key(row.well, row.fov, row.timepoint)
        if key in store:
            continue
        store[key] = {"reason": reason, "at": _now_iso()}
        added += 1
    if added:
        _after_exclusions_changed(ctx)
    return added


def restore_fields(ctx, rows) -> int:
    """Undo the exclusion of every field in *rows*. Returns how many."""
    store = ctx.state.dataset.field_exclusions
    removed = 0
    for row in rows:
        key = tc.field_key(row.well, row.fov, row.timepoint)
        if store.pop(key, None) is not None:
            removed += 1
    if removed:
        _after_exclusions_changed(ctx)
    return removed


def _save_decisions(ctx) -> None:
    with swallow("save the triage decisions"):
        ctx.persistence.save("triage_decisions")


def accept(ctx, rows, note: str = "") -> int:
    decisions = ctx.state.dataset.triage_decisions
    n = 0
    for row in rows:
        if tc.row_state(ctx, row) != tc.STATE_OPEN:
            continue
        decisions[row.key] = {"state": tc.STATE_ACCEPTED, "note": str(note or ""),
                              "at": _now_iso()}
        n += 1
    if n:
        _save_decisions(ctx)
    return n


def reopen(ctx, rows) -> int:
    """Return accepted rows to open, and restore excluded fields."""
    decisions = ctx.state.dataset.triage_decisions
    n = sum(1 for row in rows if decisions.pop(row.key, None) is not None)
    if n:
        _save_decisions(ctx)
    return n + restore_fields(ctx, [r for r in rows
                                    if tc.row_state(ctx, r) == tc.STATE_EXCLUDED])


def rerun_wells(ctx, rows) -> list[str]:
    """Open Analyze configured to re-run the wells *rows* name. Returns them."""
    wells = tc.rerun_wells(rows)
    if not wells:
        return []
    from ..controller import open_in_analyze
    open_in_analyze(ctx, force_wells=wells, only_wells=wells)
    return wells


def open_field(ctx, row) -> bool:
    """Show *row*'s field in Image Browser → Segmentation."""
    if not row.well:
        return False
    return bool(ctx.features.call("image_browser.show_field", ctx,
                                  row.well, row.fov, row.timepoint))
