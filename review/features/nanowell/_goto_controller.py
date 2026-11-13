"""The band's **Nanowell** box: go to one nanowell, the same way on every view of the Nanowells page that draws
single nanowells (:data:`NO_GOTO_VIEWS` are the ones that do not, and do not show it).

Typing an address (``q,r`` or a ring label, optionally led by a well: ``goto_compute``) and pressing Enter

* makes its well the page's well, so the single-well views (Array, Identity, Largest & smallest) show it;
* selects it, so the site detail opens on every view with its plots over time and its crops, the pop-out follows it,
  and the Distribution and Array views ring it;
* frames it where the view has a frame to frame it in: the Array view zooms onto it (as a double-click on it does),
  the Identity view's Distance map, Blink and Side by side zoom onto it and mark it (``identity_focus_compute``).

Reaches ``controller`` through a module import, as ``_window_controller`` does: the controller is imported by this
module's callers first, and every name is read at call time.
"""

from __future__ import annotations

from review.features.nanowell import controller as _controller
from review.features.nanowell import goto_compute as gtc
from review.features.nanowell import views_compute as vc
from review.features.nanowell.series_compute import site_series

#: The views whose figure draws no single nanowell, so the box is not shown on them: QC draws the plate's records,
#: one row per well; Statistics draws a test's table and Position each ring's summary. On those the box could only
#: have opened the site detail beside the figure, which is not a role on the view itself (asked for: hide it on
#: Statistics and Position).
NO_GOTO_VIEWS = frozenset({"qc", "statistics", "position"})


def go_to_nanowell(ctx, text: str) -> None:
    """Go to the nanowell *text* names (module docstring); ``""`` clears the Identity view's focus, which goes back
    to its whole view. Anything the box cannot go to is said on the status line — and on the Identity view also on
    its figure, which says why an address cannot be shown, as it always has."""
    nwc = _controller
    st = nwc._state(ctx)
    text = (text or "").strip()
    if st is None:
        return
    if not text:
        nwc.set_identity_focus(ctx, "")
        return
    if st.data is None or st.metrics is None:
        ctx.notify.set_status("The nanowell tables are not read yet: go to a nanowell once they are.")
        return
    try:
        target = gtc.resolve(text, nwc.page_wells(ctx), nwc.page_well(ctx),
                             lambda well: vc.lattice_kind(st.data, well, nwc.page_timepoint(ctx, well)))
    except ValueError as exc:
        ctx.notify.set_status(str(exc))
        if st.view == "identity":
            nwc.set_identity_focus(ctx, text)            # the figure says why the text names no nanowell
        return
    st.well = target.well
    st.identity_focus = target.address
    st.identity_zoom, st.identity_zoom_for = {}, None
    measured = len(site_series(st.metrics, *target.site)) > 0
    if measured:
        st.selected_sites = [list(target.site)]
        nwc.show_selected_in_popout(ctx)
        st.array_goto = target.site if st.view == "array" else None
        ctx.notify.set_status(f"Nanowell {target.address} in {target.well}: its plots and crops are in the site "
                              "detail.")
    else:
        # Not in the well's table: nothing to select. The Identity view still marks where each timepoint puts the
        # address (it reads the grids, not the table), so the focus is kept for it.
        st.array_goto = None
        ctx.notify.set_status(f"{target.well} has no nanowell {target.address} in its table: nothing to select.")
    nwc._save_soon(ctx)
    nwc._call(ctx, "_nw_refresh_controls")
    nwc.redraw(ctx)
