"""The ``cell_browser.browse`` service: open the Cell Browser on one population.

Another page (Saved Cells) names a population — a saved set or one scatter
gate — and this shows exactly its cells: it switches to Image Browser → Cell
Browser, sets the gallery's Source the way the user would, and collects. Going
through the Source menu rather than around it keeps the sidebar telling the
truth about what the gallery holds.
"""

from __future__ import annotations

from review.features.image_browser.cell_browser import criteria_view
from review.features.image_browser.controller import show_image_browser_subtab


def browse(ctx, source: str, *, gate: str | None = None) -> bool | None:
    """Show *source* (``"set:<name>"`` or ``"gates"``) in the Cell Browser.

    With ``"gates"``, *gate* narrows the gallery to that one gate (``None``: all
    of them). Returns ``None`` when there is no Cell Browser page to show it in,
    and ``False`` when the page is there but the source is not — the set was
    deleted, or the gate no longer exists — so the caller can say which.
    """
    if not show_image_browser_subtab(ctx, "Cell Browser"):
        return None
    rt = ctx.runtime.cell_browser
    combo = getattr(rt, "source_cb", None)
    if combo is None:
        return None
    idx = combo.findData(source)
    if idx < 0:
        return False
    if source == "gates":
        names = criteria_view._gate_names(ctx)
        if gate is not None and gate not in names:
            return False
        rt.gate_choices = None if gate is None else {gate}
    if combo.currentIndex() == idx:
        criteria_view._on_source_changed(ctx)        # same source, new gate: collect again
    else:
        combo.setCurrentIndex(idx)                   # fires the change, which collects
    return True
