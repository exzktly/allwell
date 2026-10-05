"""Draw a saved plot from the open dataset's data and capture it, for a caller
outside the gallery — the headless report (``allwell_report.py``).

The same replay the PowerPoint export's vector repair performs
(``controller._regenerate_one``): restore the record's parameters onto its tab,
finish the gating pass on this thread so the capture is not drawn under the
previous pass's ``Included`` flags, redraw, capture. It differs in what it
keeps — both the picture and the vector, handed back rather than attached to
the record — and in what it reports: every plot it does not draw comes back
with the reason, because the caller is writing a manifest in which a missing
figure must say why it is missing.

Reached through the ``saved_plots.render`` / ``saved_plots.gallery`` services,
so the report names no feature.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from .controller import (
    _card_for_tab, _gating_recompute_now, _reapply_target_tab,
    _redraw_keeps_the_scope, gallery_order, restore_saved_plot,
)
from .state import get_store

_logger = logging.getLogger("review")


@dataclass(frozen=True)
class RenderedPlot:
    """A drawn plot's PNG and SVG, or the reason it was not drawn."""

    png: bytes = b""
    svg: bytes = b""
    reason: str = ""


def gallery(ctx) -> list:
    """The open dataset's saved plots, in gallery order (newest first)."""
    store = get_store(ctx)
    store.ensure_loaded(ctx)
    return gallery_order(store)


def render_saved_plot(ctx, rec) -> RenderedPlot:
    """Replay *rec* on the open dataset and capture what it draws."""
    store = get_store(ctx)
    card = _card_for_tab(ctx, ctx.runtime.saved_plots.cards_by_tab or {}, rec.tab)
    if card is None or not hasattr(card, "captureVectorSvg"):
        return RenderedPlot(reason=f"the {rec.tab or 'originating'} tab draws no "
                                   f"chart that can be redrawn")
    if not _redraw_keeps_the_scope(ctx, store, rec, card):
        return RenderedPlot(reason="saved as one panel of a multi-panel figure; "
                                   "a redraw would draw every panel")
    try:
        restore_saved_plot(ctx, rec.id, adopt_groups=True)
        if _gating_recompute_now(ctx):
            _reapply_target_tab(ctx, rec.params.get("active_centre_tab") or rec.tab)
        figure = card.figure
        buf = io.BytesIO()
        with card._publication_capture():
            figure.savefig(buf, format="png", dpi=300, bbox_inches="tight",
                           facecolor=figure.get_facecolor())
        svg = card.captureVectorSvg()
    except Exception as exc:
        _logger.exception("saved_plots: could not draw %r for a report", rec.id)
        return RenderedPlot(reason=f"drawing it failed: {exc}")
    png = buf.getvalue()
    if not png:
        return RenderedPlot(reason="the figure rendered no image")
    return RenderedPlot(png=png, svg=svg or b"")
