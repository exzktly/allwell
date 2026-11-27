"""The Nanowells page's **status line**: one chip under the figure for each condition that shapes it.

Four things narrow or annotate what the page draws without being part of any one control the user is looking at: a
**Subset**, wells **excluded**, a Distribution **window** and Home's **Cell Gating**. A fifth, a **Resolve** walk,
takes the page over while it runs. Each was said somewhere different — the Subset in its combo's colour, the
excluded wells by a band button that came and went, the window by a strip that appeared between the band and the
figure, the gating in a sentence of the footer, the walk by a guide card above the figure — so a figure narrowed
three ways looked like one narrowed by none (``SURFACES_TRACKER.md`` row S20).

The line holds one chip per condition that applies, in that order, each reading what it is (``Subset: … ›``,
``Excluded: 2 wells``, ``Window: 1.2–3.4 ›``, ``Cell Gating: 1,204 cells left out ›``, ``Resolve: 3 of 17 · B04 at
24h ›``), and is not shown at all when none does. A chip opens the page that owns its condition:

* **Subset** — the Subset's Conditions page in the inspector (the band's *Conditions ›*);
* **Excluded** — the menu of the wells to bring back, the same menu *Excluded (n)* on the band holds;
* **Window** — the **Window** page of the inspector, which holds what was the strip: the sentence, Low and High,
  *every | any timepoint*, **This panel only**, **Open in pop-out**, **Use as Subset** and ✕;
* **Cell Gating** — Home ▸ Cell Gating, where the gates are set;
* **Resolve** — the **Resolve** page of the inspector, which holds what was the guide card: what is wrong with the
  timepoint, what to do and where, its class's one action, and the walk's Back, Skip and Stop. The band's *Queue*
  group keeps them too, with One per well, in the Well's place, because their keys (← → Esc) are shortcuts of that
  group and would stop working whenever a page holding them was closed.

A window just drawn and a walk just started open their pages, as the strip and the card appeared unasked; a page
whose condition has gone is left.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtWidgets import QToolButton, QWidget

from review.core import inspector_panels as panels
from review.core import inspector_service as svc
from review.core.inspector_panels import CHEVRON
from widgets.flow_layout import FlowLayout, sync_flow_height

#: The chips, in the order they are read.
CHIPS: tuple[str, ...] = ("subset", "excluded", "window", "gates", "walk")

#: The inspector pages two chips open, by key: their registry key and title.
WINDOW_PAGE = "nanowell/window"
WINDOW_TITLE = "Window"
RESOLVE_PAGE = "nanowell/resolve"
RESOLVE_TITLE = "Resolve"

_TIPS = {
    "subset": "The Subset narrows the nanowells drawn. Open its conditions.",
    "excluded": "Wells left out of every figure on this page. Choose one to bring it back.",
    "window": "A window on the Distribution keeps the nanowells inside it. Open its range and actions.",
    "gates": "Home's Cell Gating leaves cells out of the counts and brightness here. Open Cell Gating.",
    "walk": "A Resolve walk is running. Open what to do at this timepoint.",
}


def _state(ctx):
    return ctx.state.feature_slice("nanowell")


def chip_texts(ctx) -> dict[str, str]:
    """What each chip that applies now reads, by :data:`CHIPS` key, in order; absent keys do not apply."""
    from review.features.nanowell import _anchor_queue as aq
    from review.features.nanowell import _window_controller as wnc

    st = _state(ctx)
    if st is None:
        return {}
    out: dict[str, str] = {}
    subset = getattr(st, "subset", None)
    if subset is not None and subset.is_active() and st.view not in _NO_VALUE_VIEWS:
        out["subset"] = f"Subset: {subset.label()} {CHEVRON}"
    wells = list(getattr(st, "excluded_wells", ()) or ())
    if wells:
        out["excluded"] = f"Excluded: {wells[0]}" if len(wells) == 1 else f"Excluded: {len(wells)} wells"
    window = wnc.current(st)[0] if st.view == "distribution" else None
    if window is not None:
        out["window"] = f"{WINDOW_TITLE}: {window.lo:.3g}–{window.hi:.3g} {CHEVRON}"
    gate = getattr(st, "gate", None)
    left_out = int(getattr(gate, "excluded", 0) or 0) if gate is not None else 0
    fields = len(getattr(gate, "fields", ()) or ()) if gate is not None else 0
    if (left_out or fields) and st.view not in _NO_VALUE_VIEWS:
        said = f"{left_out:,} cells left out" if left_out else f"{fields:,} fields left out"
        out["gates"] = f"Cell Gating: {said} {CHEVRON}"
    queue = aq.active_queue(ctx)
    if queue is not None and aq.current_view(ctx) == st.view:
        out["walk"] = f"{RESOLVE_TITLE}: {queue.position_text()} {CHEVRON}"
    return out


#: The views that draw no nanowell's value (``tab_view.NO_VALUE_VIEWS``): neither the Subset nor Cell Gating
#: changes what they draw, so neither has a chip there.
_NO_VALUE_VIEWS = ("qc", "identity")


@dataclass
class StatusLine:
    """The line, its chips, and the two pages it opens."""

    ctx: object
    host: QWidget
    chips: dict[str, QToolButton]
    window_page: QWidget
    resolve_page: QWidget
    excluded_menu_of: object                 # () -> QMenu: the band's Excluded menu
    shown: dict[str, bool] = field(default_factory=dict)

    def refresh(self) -> None:
        """Show a chip for each condition that applies, reading its text; open the Window or Resolve page when its
        condition has just begun, and leave it when its condition has ended."""
        texts = chip_texts(self.ctx)
        for key, chip in self.chips.items():
            text = texts.get(key)
            chip.setVisible(text is not None)
            if text is not None and chip.text() != text:
                chip.setText(text)
        menu = self.excluded_menu_of()
        if menu is not None and self.chips["excluded"].menu() is not menu:
            self.chips["excluded"].setMenu(menu)
        self.host.setVisible(bool(texts))
        for key, page in (("window", self.window_page), ("walk", self.resolve_page)):
            now, before = key in texts, self.shown.get(key, False)
            if now and not before:
                self.open(key)
            elif before and not now and svc.is_showing(self.ctx, page):
                svc.pop(self.ctx)
            self.shown[key] = now

    def open(self, key: str) -> None:
        """What pressing the chip *key* does (Excluded is a menu, and opens itself)."""
        ctx = self.ctx
        if key == "subset":
            button = ctx.widgets.get_or_none("_nw_subset_edit_btn")
            panel = panels.panel_of(button) if button is not None else None
            if panel is not None and not svc.is_showing(ctx, panel.content):
                panels.press(button)
        elif key == "window" and not svc.is_showing(ctx, self.window_page):
            self.window_page.setVisible(True)
            svc.push(ctx, WINDOW_PAGE, WINDOW_TITLE, self.window_page, tab="data", anchor=self.chips[key])
        elif key == "walk" and not svc.is_showing(ctx, self.resolve_page):
            self.resolve_page.setVisible(True)
            svc.push(ctx, RESOLVE_PAGE, RESOLVE_TITLE, self.resolve_page, tab="data", anchor=self.chips[key])
        elif key == "gates":
            ctx.features.query("cell_gating.show", ctx, default=None)


def build_status_line(ctx, parent: QWidget, *, window_page: QWidget, resolve_page: QWidget,
                      excluded_menu_of) -> StatusLine:
    """The line, hidden until a condition applies; the page puts it under its figure. *window_page* and
    *resolve_page* are the widgets the Window and Resolve chips open, built hidden and in no layout by the page.
    Registers ``_nw_status_line``, ``_nw_status_<key>`` per chip and ``_nw_status_refresh``."""
    host = QWidget(parent)
    host.setObjectName("NanowellStatusLine")
    flow = FlowLayout(host, h_spacing=6)
    sync_flow_height(host)
    chips: dict[str, QToolButton] = {}
    for key in CHIPS:
        chip = QToolButton(host)
        chip.setProperty("variant", "secondary")
        chip.setProperty("statusChip", True)
        chip.setToolTip(_TIPS[key])
        if key == "excluded":
            # A menu of commands, the band's own: the theme's chevron is its one arrow (CLAUDE.md §7.12).
            chip.setPopupMode(QToolButton.InstantPopup)
            chip.setProperty("hasMenu", True)
        chip.hide()
        flow.addWidget(chip)
        chips[key] = chip
        ctx.widgets.register(f"_nw_status_{key}", chip)
    line = StatusLine(ctx, host, chips, window_page, resolve_page, excluded_menu_of)
    for key, chip in chips.items():
        if key != "excluded":
            chip.clicked.connect(lambda _=False, k=key: line.open(k))
    host.hide()
    ctx.widgets.register("_nw_status_line", host)
    ctx.widgets.register("_nw_status_refresh", line.refresh)
    return line
