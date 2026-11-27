"""The Nanowells page's **Values** row: which nanowells count and how their values are normalised, on one page.

The band was three rows on every view — the central selections, these settings, and the open view's own controls —
and at a 1,500 px window the second row alone was full: one more control wrapped the band onto a fourth line, taking the
figure's height (``SURFACES_TRACKER.md`` row S19). The surfaces plan puts a sub-option on a page of the inspector,
reached by a band row that reads its value (decision 5, *every row shows its current value*), so these six settings
moved off the band onto the **Values** page of the Data tab:

* **Baseline** and its **Baseline average** — the fold change's reference;
* **Rim focus** — which nanowells count, by how sharp their rim is;
* **Fixed cohort** — the same nanowells at every timepoint;
* the **Moving average** (trailing or centred) — each value averaged over a window of timepoints;
* **Responding above** — the threshold the Distribution view counts against.

Each control is the one the page always had, under the same registry name; only its place changed. The page's
refresh still decides, per view, which of them apply (``tab_view.build_nanowell_tab``'s ``refresh_controls``), and
the row is shown only on a view where at least one does. The row's text reads what is in force — the baseline, then
each setting that is not at its default — so the band still says how the values were made without opening the page.

In phone layout the row is a row of the Controls sheet's Data tab, opening the same page inside the sheet.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from review.core import inspector_panels as panels

#: The row's title, and the page's.
VALUES_TITLE = "Values"

VALUES_TIP = ("Which nanowells count and how their values are normalised: the baseline and its average, Rim focus, "
              "the fixed cohort, the moving average and the Responding threshold. The row reads what is in force.")


def build_values_row(ctx, band):
    """The **Values** band row and its page. Returns ``(button, add_row)``, where ``add_row(caption, *widgets)``
    puts one line of the page together and returns it (shown and hidden by the page's refresh, per view)."""
    button, content, layout = panels.make_popover_button(VALUES_TITLE, band, ctx=ctx, tab="data")
    button.setToolTip(VALUES_TIP)
    ctx.widgets.register("_nw_values_btn", button)
    ctx.widgets.register("_nw_values_panel", content)

    def add_row(caption: str, *widgets: QWidget) -> QWidget:
        row = QWidget(content)
        row.setObjectName("NanowellValuesRow")
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(6)
        if caption:
            line.addWidget(QLabel(caption, row))
        for widget in widgets:
            line.addWidget(widget)
        line.addStretch(1)
        layout.addWidget(row)
        return row

    return button, add_row


def values_summary(*, baseline: str | None, rim_focus: bool, cohort: bool, moving: int | None,
                   centred: bool, baseline_moving: int | None, responding: str | None) -> str:
    """What the **Values** row reads: the baseline, then each setting that is not at its default, in the page's
    order. ``None`` for a setting the open view does not use, which therefore says nothing."""
    parts: list[str] = []
    if baseline:
        parts.append(baseline if baseline_moving in (None, 1) else f"{baseline} (average {baseline_moving})")
    if rim_focus:
        parts.append("rim focus")
    if cohort:
        parts.append("fixed cohort")
    if moving not in (None, 1):
        parts.append(f"{'centred' if centred else 'moving'} average {moving}")
    if responding:
        parts.append(f"responding > {responding}")
    return " · ".join(parts)


@dataclass(frozen=True)
class ValuesLines:
    """The Values page's lines and the control each reads its value from. A line the page's refresh hid is a setting
    the open view does not use."""

    baseline: QWidget
    baseline_cb: QWidget
    baseline_moving: QWidget
    baseline_moving_spin: QWidget
    rim: QWidget
    rim_box: QWidget
    cohort: QWidget
    cohort_box: QWidget
    moving: QWidget
    moving_spin: QWidget
    moving_kind_cb: QWidget
    responding: QWidget
    responding_edit: QWidget

    def rows(self) -> tuple[QWidget, ...]:
        return (self.baseline, self.baseline_moving, self.rim, self.cohort, self.moving, self.responding)

    def summary(self) -> str:
        """:func:`values_summary` of what the controls hold, each read only where its line is shown."""
        def shown(line: QWidget) -> bool:
            return not line.isHidden()

        return values_summary(
            baseline=self.baseline_cb.currentText() if shown(self.baseline) else None,
            rim_focus=self.rim_box.isChecked() and shown(self.rim),
            cohort=self.cohort_box.isChecked() and shown(self.cohort),
            moving=self.moving_spin.value() if shown(self.moving) else None,
            centred=bool(self.moving_kind_cb.currentData()),
            baseline_moving=self.baseline_moving_spin.value() if shown(self.baseline_moving) else None,
            responding=self.responding_edit.text().strip() if shown(self.responding) else None)


def refresh_values_row(group: QWidget, lines: ValuesLines) -> None:
    """Show the row's *group* only where one of the page's lines applies, and read what is in force into it."""
    group.setVisible(any(not row.isHidden() for row in lines.rows()))
    button = group if panels.panel_of(group) is not None else next(
        (child for child in group.findChildren(QWidget) if panels.panel_of(child) is not None), None)
    if button is not None:
        summary = lines.summary()
        panels.set_summary(button, lambda text=summary: text)


def is_offered(ctx, widget: QWidget) -> bool:
    """Whether *widget* is offered on the page now: on screen, or on the Values page behind a row that is.

    A control on the Values page is never on screen until the page is opened, so "is it shown" means "is its row
    shown, and its line on the page": what a test of which controls a view offers has to ask."""
    panel = ctx.widgets.get_or_none("_nw_values_panel")
    button = ctx.widgets.get_or_none("_nw_values_btn")
    if panel is not None and button is not None and panel.isAncestorOf(widget):
        return button.isVisibleTo(ctx.window) and widget.isVisibleTo(panel)
    return widget.isVisibleTo(ctx.window)
