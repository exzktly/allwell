"""A tour of every page of the full app.

:func:`tour` navigates to each page in turn and yields its name once the page is on screen and
holds its data; the caller measures or photographs it. On a phone it also yields the phone's own
chrome (the rail drawer, the Controls sheet, the Wells sheet) over a plot page. The pages come from
the app itself — the Review sections the rail shows, each section's page tabs, the Datasets tools,
Analyze's views — so a page added tomorrow is visited without anyone adding it here.
"""

from __future__ import annotations

from collections.abc import Iterator

from tests._page_datasets import prepare
from tests._screens import pump


def tour(win, qapp, *, kind: str, wells: set[str], phone: bool) -> Iterator[str]:
    review, shell = win._review, win._phone

    def reselect() -> None:
        # Some pages (the Image Browser's single-well pickers) narrow the selection; the next page starts
        # from the full one again.
        review._set_selected_wells(set(wells), commit=True)
        pump(qapp, 20, 300)

    win._on_titlebar_mode_seg(0)
    pump(qapp)
    seen: set[str] = set()                            # the inspector pages already measured, by title
    nav = review._section_nav
    for key, _label in nav.shownItems():
        row = getattr(nav, "_rows", {}).get(key)
        if phone and row is not None and row.isHidden():
            continue                                  # Review CSV and Batch Export are not offered on a phone
        reselect()
        nav.setCurrentKey(key)
        pump(qapp, 20, 250)
        bar = review._page_tabs_bar_by_name.get(key)
        if bar is None:
            prepare(win, qapp, kind, key, wells)
            yield key
            yield from _controls_sheet(shell, qapp, key, phone)
            continue
        for i in range(bar.count()):
            bar.setCurrentIndex(i)
            pump(qapp, 20, 250)
            name = f"{key}/{bar.tabText(i)}"
            prepare(win, qapp, kind, name, wells)
            yield name
            yield from _controls_sheet(shell, qapp, name, phone)
            # On the nanowell dataset only: on the others the Nanowells page shows its empty state, and the pages
            # opened over it would be measured as pages with nothing to show.
            if kind == "nanowell":
                yield from _inspector_pages(review, qapp, name, seen)
    if kind == "mini":
        win._on_titlebar_mode_seg(2)
        pump(qapp)
        for tool in shell._h.tool_names() if phone else win._datasets.tool_names():
            (shell._pick_tool if phone else win._datasets.show_tool)(tool)
            pump(qapp, 20, 200)
            yield f"datasets/{tool}"
        win._on_titlebar_mode_seg(1)
        pump(qapp)
        views = shell._h.page_views() if phone else [""]
        for view in views:
            if view:
                shell._pick_page_view(view)
                pump(qapp, 20, 200)
            yield f"analyze/{view or 'form'}"
    if phone and kind == "mini":
        win._on_titlebar_mode_seg(0)
        pump(qapp)
        reselect()
        nav.setCurrentKey("Plotting")
        review._page_tabs_bar_by_name["Plotting"].setCurrentIndex(0)
        pump(qapp, 20, 300)
        shell._h.set_navigator(True)
        pump(qapp, 20, 300)
        yield "chrome/drawer"
        shell._h.set_navigator(False)
        pump(qapp, 20, 200)
        shell.open_controls()
        pump(qapp, 20, 300)
        yield "chrome/controls sheet"
        shell.close_controls(now=True)
        pump(qapp, 20, 200)
        shell.open_wells()
        pump(qapp, 20, 300)
        yield "chrome/wells sheet"
        shell.close_wells(now=True)
        pump(qapp, 20, 200)


#: The suffix of a page name the tour yields with that page's ⚙ Controls sheet open over it.
SHEET_SUFFIX = " ▸ Controls"


def _controls_sheet(shell, qapp, name: str, phone: bool) -> Iterator[str]:
    """On a phone, every page's ⚙ Controls sheet, open over its page: the sheet holds most of a page's controls
    there, laid out as a form, and the tour used to open it on one Plotting page only — while the sheets of ten other
    pages scrolled sideways. At the desk, and on a page with no controls to lend, nothing."""
    if not phone or shell._current_band() is None:
        return
    shell.open_controls()
    pump(qapp, 20, 300)
    yield name + SHEET_SUFFIX
    shell.close_controls(now=True)
    pump(qapp, 20, 200)


#: The pages whose inspector pages the tour opens, each once, on the first of the page's views that offers it: the
#: Nanowells page, whose views share most of the inspector's pages ("every inspector page on all six screens"). Every
#: page's would be the whole app's.
INSPECTOR_PAGES_OF = ("Nanowells/",)


def _inspector_pages(review, qapp, name: str, seen: set[str]) -> Iterator[str]:
    """Each inspector page the page's band opens — a row such as *Values ›* or *Layers ›*, the Display and Style
    tabs, and the rows a Display tab holds — open over the page, yielded as ``"<page> ▸ <title>"``, each title once.
    A page is opened as a press of its button opens it, and closed again before the next."""
    from PySide6.QtWidgets import QAbstractButton

    from review.core import inspector_panels as panels
    from review.core import inspector_service
    from widgets.control_band import ControlBand

    if not name.startswith(INSPECTOR_PAGES_OF):
        return
    # The page's own band: the one holding its Metric (on a phone it may be lent to the Controls sheet, closed).
    anchor = review._ctx.widgets.get_or_none("_nw_metric_cb")
    band = anchor
    while band is not None and not isinstance(band, ControlBand):
        band = band.parentWidget()
    for band in [band] if band is not None else []:
        for button in band.findChildren(QAbstractButton):
            panel = panels.panel_of(button)
            if panel is None or panel.title in seen or not _offered(button, band):
                continue
            ctx = panels._ctx_of(button, panel)
            if ctx is None:
                continue
            seen.add(panel.title)
            panels.press(button)
            pump(qapp, 20, 300)
            yield f"{name} ▸ {panel.title}"
            # The rows a root page holds that open a page of their own (Display ▸ Display range).
            for row in panel.content.findChildren(QAbstractButton):
                inner = panels.panel_of(row)
                label = inner.title if inner is not None else row.text().split(":")[0].strip()
                if label in seen or not (inner is not None or row.property("inspectorRow")):
                    continue
                seen.add(label)
                if inner is not None:
                    panels.press(row)
                else:
                    row.click()
                pump(qapp, 20, 300)
                yield f"{name} ▸ {panel.title} ▸ {label}"
            inspector_service.close(ctx)
            pump(qapp, 20, 200)


def _offered(button, band) -> bool:
    """Whether *button* is on the band's current view: not hidden itself nor by any group between it and the band."""
    widget = button
    while widget is not None and widget is not band:
        if widget.isHidden():
            return False
        widget = widget.parentWidget()
    return widget is band
