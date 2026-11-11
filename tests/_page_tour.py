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
