"""Quick-replicate helpers — drives the "Quick Replicates" dropdowns on the
sample-definitions panel. (The rep-map plate is now a ``widgets.WellPlateSelector``
wired in ``views/replicate_panel_view.py``; its drag edits the current selection
via ``WellViewerApp._sel_set_composition`` directly — no controller helpers.)"""

from __future__ import annotations



from well_viewer.domain.batch_models import ReplicateSet
from well_viewer.domain.selections_model import color_for_wells






from well_viewer.domain.selections_model import (  # noqa: F401
    replicate_display_label,
    well_display_label,
)

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_DLG_MIN_W = 420
_DLG_MIN_H = 480






def rank_color_rset(ctx, rset) -> str:
    """Colour for a ReplicateSet / BarGroup (or anything with a ``wells``
    attribute) — honours the per-selection ``color`` override when an entry
    in ``_selections`` shares its ``name``, otherwise falls back to the
    rank colour of its lowest well.

    A8 teardown: takes ``ctx`` — delegates to the pure domain colour resolver
    over ``ctx.state.selection`` (no god-object)."""
    from well_viewer.domain.selections_model import color_for_replicate_set
    return color_for_replicate_set(rset, ctx.state.selection.selections)






def rep_sets_loaded(ctx) -> list[ReplicateSet]:
    """One ReplicateSet per selection that has ≥1 loaded well (ignores hidden),
    in ``_selections`` order — derived straight from the unified model.

    A8 teardown: reads typed ``ctx.state.selection`` / ``ctx.state.dataset`` so
    it takes ``ctx`` and names no god-object."""
    out: list[ReplicateSet] = []
    for s in ctx.state.selection.selections:
        wells = s.get("wells") or []
        if any(w in ctx.state.dataset.well_paths for w in wells):
            out.append(ReplicateSet(s.get("name") or "", list(wells)))
    return out



def rep_color_for(ctx, lbl: str) -> str | None:
    """Return the colour for the selection that owns *lbl*, or None if the
    well is not in any selection."""
    for s in ctx.state.selection.selections:
        if lbl in (s.get("wells") or []):
            return ctx.window._sel_color(s)
    return None



def rank_color_wells(ctx, wells) -> str:
    """Colour for a set of wells — the rank colour of its lowest well, so all
    of its wells (and its line/bar trace) share one colour.

    A8 teardown: the pure implementation now lives in ``domain.selections_model``
    (``color_for_wells``); this thin ctx-signature wrapper stays for the legacy
    ``WellViewerApp._rank_color_wells`` god-method delegate (the arg is unused)."""
    return color_for_wells(wells)

