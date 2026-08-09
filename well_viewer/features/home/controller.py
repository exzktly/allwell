"""Home's behaviour: paint the page from the record, and act on what it says.

Every function takes ``ctx`` first and reads only typed state — the page holds
no numbers of its own. Repainting is idempotent and cheap because everything it
draws is already in memory: the completeness record was written by the run and
loaded with the sidecar.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

import theme
from well_viewer.core.safe import swallow

from . import collect as hc
from .plate_panel import paint_plate, well_tooltip
from .tab_view import format_value, legend_label, legend_swatch_style

#: The masthead's actions, in the order they appear. Each is handled by
#: :func:`run_home_action`, which is also what the next-step row calls — one
#: place decides what "open this in Analyze" means.
#:
#: Reveal-folder and open-dataset are deliberately absent: the shell already
#: carries both in the mode band's action cluster, a few pixels above this page
#: and visible on every Review tab. A second copy here would be a second thing
#: to keep working.
HOME_ACTIONS = (
    ("reload", "Reload"),
    ("analyze", "Analyze"),
    ("datasets", "Datasets"),
)

#: Index of each pane in the shell's central stack, mirroring
#: ``tab_controller.PANE_MODES``. Read from there rather than restated so a
#: fourth pane cannot leave this mapping behind.
_PANE_INDEX = {"analyze": 1, "datasets": 2}

_SEVERITY_COLORS = {
    "ok": theme.Colors.success,
    "info": theme.Colors.text_secondary,
    "accent": theme.Colors.accent,
    "warn": theme.Colors.warn,
    "danger": theme.Colors.danger,
    "warning": theme.Colors.warn,
    "error": theme.Colors.danger,
}


def _widget(ctx, name):
    return ctx.widgets.get_or_none(name)


def _clear(host: QWidget) -> None:
    layout = host.layout()
    if layout is None:
        return
    while layout.count():
        item = layout.takeAt(0)
        child = item.widget()
        if child is not None:
            child.setParent(None)


def activate_home(ctx) -> None:
    """Fired when the page is shown."""
    refresh_home(ctx)


def on_home_dataset_loaded(ctx) -> None:
    """Fired after a dataset loads, once the sidecar is in state.

    The notes editor is re-read here rather than only at build time: the
    hydrator writes the slice before any widget exists, and Home may have been
    built for a previous dataset.
    """
    edit = _widget(ctx, "_home_notes_edit")
    if edit is not None:
        text = ctx.state.selection.notes_text or ""
        if edit.toPlainText() != text:
            blocked = edit.blockSignals(True)
            try:
                edit.setPlainText(text)
            finally:
                edit.blockSignals(blocked)
    refresh_home(ctx)


def on_home_selection_changed(ctx) -> None:
    """Keep Home's plate showing the app-wide selection."""
    plate = _widget(ctx, "_home_plate")
    if plate is None:
        return
    wells = sorted(ctx.state.selection.selected_wells or ())
    if sorted(plate.selectedWellIds() or ()) != wells:
        blocked = plate.blockSignals(True)
        try:
            plate.setSelectedWellIds(wells)
        finally:
            plate.blockSignals(blocked)


def on_home_plate_selection(ctx, wells) -> None:
    """A click on Home's plate is an app-wide well selection.

    Selecting rather than navigating: a click that teleported the reader off the
    summary page would make multi-select impossible and an exploratory click
    expensive. The next-step row is where navigation lives.
    """
    slice_ = ctx.state.feature_slice("home")
    if slice_ is not None:
        slice_.last_plate_selection = set(wells or ())
    from well_viewer.core.selection_controller import set_selected_wells
    with swallow("home plate selection"):
        set_selected_wells(ctx, set(wells or ()))


def refresh_home(ctx) -> None:
    """Repaint every block from the record. Safe before the page is built."""
    if _widget(ctx, "_home_title") is None:
        return
    _paint_masthead(ctx)
    _paint_next_step(ctx)
    _paint_plate(ctx)
    _paint_tiles(ctx)
    _paint_rows(ctx, "_home_health", hc.health_rows(ctx), kind="health")
    _paint_rows(ctx, "_home_receipt", hc.run_parameters(ctx), kind="receipt")
    _paint_rows(ctx, "_home_work", hc.your_work(ctx), kind="work")
    slice_ = ctx.state.feature_slice("home")
    if slice_ is not None:
        slice_.last_rendered_for = str(
            getattr(ctx.state.dataset, "data_dir", "") or "")


def _paint_masthead(ctx) -> None:
    ident = hc.identity(ctx)
    _widget(ctx, "_home_title").setText(ident["name"] or "No dataset open")
    _widget(ctx, "_home_path").setText(ident["path"])
    schema = ident["schema"]
    _widget(ctx, "_home_schema").setText(
        f"{schema}   ·   separator \"{ident['separator']}\"" if schema
        else "no filename schema recorded")

    bits = []
    if ident["finished_at"]:
        bits.append(f"run finished {ident['finished_at']}")
    else:
        bits.append("no completeness record")
    if ident["host"]:
        bits.append(ident["host"])
    if ident["suspended"]:
        bits.append("run was suspended")
    _widget(ctx, "_home_run_meta").setText("   ·   ".join(bits))


def _paint_next_step(ctx) -> None:
    step = hc.next_step(ctx)
    label = _widget(ctx, "_home_next_text")
    label.setText(step["text"])
    label.setStyleSheet(
        f"color:{_SEVERITY_COLORS.get(step['severity'], theme.Colors.text_primary)};")
    button = _widget(ctx, "_home_next_button")
    button.setText(step["label"])


def _paint_plate(ctx) -> None:
    plate = _widget(ctx, "_home_plate")
    if plate is None:
        return
    cells = hc.plate_cells(ctx)
    record_wells = (ctx.state.dataset.pipeline_info or {}).get("run_check") or {}
    record_wells = record_wells.get("wells") or {}
    if cells:
        plate.setEnabledWells(sorted(cells))
    paint_plate(plate, cells, hc.plate_fractions(ctx))
    plate.setWellTooltipProvider(
        lambda well: well_tooltip(well, cells, record_wells))

    host = _widget(ctx, "_home_legend")
    _clear(host)
    layout = host.layout()
    for index, (state, _label, count) in enumerate(hc.plate_legend(ctx)):
        swatch = QLabel("", host)
        swatch.setFixedSize(theme.Spacing.md, theme.Spacing.md)
        swatch.setStyleSheet(legend_swatch_style(state))
        layout.addWidget(swatch, index, 0)
        layout.addWidget(QLabel(legend_label(state), host), index, 1)
        value = QLabel(str(count), host)
        value.setObjectName("Muted" if not count else "")
        layout.addWidget(value, index, 2)

    totals = ((ctx.state.dataset.pipeline_info or {}).get("run_check") or {}
              ).get("totals") or {}
    if totals.get("intent"):
        covered = int(totals.get("covered_any") or 0)
        intent = int(totals.get("intent") or 0)
        _widget(ctx, "_home_plate_summary").setText(
            f"{covered:,} of {intent:,} field groups covered")
    else:
        _widget(ctx, "_home_plate_summary").setText(hc.record_reason(ctx))


def _paint_tiles(ctx) -> None:
    host = _widget(ctx, "_home_tiles")
    _clear(host)
    layout = host.layout()
    tiles = hc.stat_tiles(ctx)
    for index, tile in enumerate(tiles):
        layout.addWidget(_tile_widget(host, tile), 0, index)
    chips = hc.channel_chips(ctx)
    if chips:
        layout.addWidget(_channel_widget(host, chips), 0, len(tiles))


def _tile_widget(parent: QWidget, tile) -> QWidget:
    card = QWidget(parent)
    card.setObjectName("HomeTile")
    column = QVBoxLayout(card)
    column.setContentsMargins(theme.Spacing.md, theme.Spacing.sm,
                              theme.Spacing.md, theme.Spacing.sm)
    column.setSpacing(theme.Spacing.xs)
    label = QLabel(tile.label, card)
    label.setObjectName("HomeTileLabel")
    column.addWidget(label)
    value = QLabel(format_value(tile.value), card)
    value.setObjectName("HomeTileValue" if tile.available else "Muted")
    if not tile.available:
        value.setToolTip(tile.value.reason)
    column.addWidget(value)
    sub = QLabel(tile.sub, card)
    sub.setObjectName("Muted")
    sub.setWordWrap(True)
    column.addWidget(sub)
    return card


def _channel_widget(parent: QWidget, chips) -> QWidget:
    card = QWidget(parent)
    card.setObjectName("HomeTile")
    column = QVBoxLayout(card)
    column.setContentsMargins(theme.Spacing.md, theme.Spacing.sm,
                              theme.Spacing.md, theme.Spacing.sm)
    column.setSpacing(theme.Spacing.xs)
    label = QLabel("Channels", card)
    label.setObjectName("HomeTileLabel")
    column.addWidget(label)
    row = QHBoxLayout()
    row.setSpacing(theme.Spacing.xs)
    for chip in chips:
        text = chip["token"]
        if chip["role"]:
            text += f" · {chip['role']}"
        widget = QLabel(text, card)
        widget.setObjectName("HomeChipSeg" if chip["segmentation"] else "HomeChip")
        if chip["segmentation"]:
            widget.setToolTip("segmentation channel — nuclei are found here")
        row.addWidget(widget)
    row.addStretch(1)
    column.addLayout(row)
    return card


def _paint_rows(ctx, widget_name: str, rows, *, kind: str) -> None:
    host = _widget(ctx, widget_name)
    if host is None:
        return
    _clear(host)
    layout = host.layout()
    for row in rows:
        layout.addWidget(_row_widget(host, row, kind=kind))


def _row_widget(parent: QWidget, row: dict, *, kind: str) -> QWidget:
    host = QWidget(parent)
    line = QHBoxLayout(host)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(theme.Spacing.sm)

    if kind == "health":
        dot = QLabel("●", host)
        dot.setStyleSheet(
            f"color:{_SEVERITY_COLORS.get(row.get('severity'), theme.Colors.text_muted)};")
        line.addWidget(dot)
        label = QLabel(str(row.get("label") or ""), host)
        line.addWidget(label)
        value = QLabel(str(row.get("value") or ""), host)
        value.setObjectName("Muted")
        value.setWordWrap(True)
        line.addWidget(value, 1)
        if row.get("wells"):
            wells = QLabel("  ".join(row["wells"][:8]), host)
            wells.setObjectName("HomeWellChips")
            line.addWidget(wells)
        return host

    if kind == "receipt":
        label = QLabel(str(row.get("label") or ""), host)
        label.setObjectName("HomeTileLabel")
        line.addWidget(label)
        value = QLabel(str(row.get("value") or ""), host)
        line.addWidget(value, 1)
        if row.get("is_default"):
            chip = QLabel("default", host)
            chip.setObjectName("HomeChip")
            chip.setToolTip(
                "this value came from the pretrained model, not from a choice "
                "made for this run")
            line.addWidget(chip)
        return host

    label = QLabel(str(row.get("label") or ""), host)
    line.addWidget(label)
    line.addStretch(1)
    count = int(row.get("count") or 0)
    total = row.get("total")
    if count:
        text = f"{count} of {total}" if total else str(count)
    else:
        # A real zero: the user has defined nothing. Not an em-dash — that is
        # reserved for a figure the page could not obtain at all.
        text = "None yet"
    value = QLabel(text, host)
    value.setObjectName("" if count else "Muted")
    line.addWidget(value)
    return host


#: Where each action lands, for the ones that are a Review page.
_ACTION_PAGE = {
    "samples": "Sample Definitions",
    "plot": "Plotting",
    "partial": "Review CSV",
    "errors": "Review CSV",
}


def run_home_action(ctx, action: str) -> None:
    """Everything a Home button can do, in one place.

    The next-step row and the masthead share this, so "open this in Analyze"
    cannot come to mean two things depending on which control was pressed.

    Actions that cannot be performed — no dataset to reload, a pane the running
    profile does not include — do nothing rather than half-doing something.
    """
    with swallow(f"home action {action}"):
        if action == "reload":
            _reload_dataset(ctx)
            return
        if action in _PANE_INDEX:
            _show_pane(ctx, _PANE_INDEX[action])
            return
        if action == "validate":
            _open_validate(ctx)
            return
        page = _ACTION_PAGE.get(action)
        if page:
            notebook = getattr(ctx.window, "_notebook", None)
            if notebook is not None:
                notebook.setCurrentByName(page)


def _reload_dataset(ctx) -> None:
    data_dir = getattr(ctx.state.dataset, "data_dir", None)
    if data_dir is None:
        return
    from well_viewer.core.load_controller import load_directory
    load_directory(ctx, data_dir)


def _open_validate(ctx) -> None:
    """Hand this dataset to Datasets → Validate & Repair.

    The offer Home makes to a dataset with no completeness record — one
    produced before the check existed, or whose remote check could not run.
    The folder is carried across: a button labelled "Check this dataset now"
    that lands on an empty form with the wrong tool showing does less than it
    says, and the user has to re-find a folder the app already had open.
    """
    _show_pane(ctx, _PANE_INDEX["datasets"])
    pane = getattr(ctx.window, "_datasets_pane", None)
    open_tool = getattr(pane, "open_tool", None)
    if not callable(open_tool):
        return
    data_dir = getattr(ctx.state.dataset, "data_dir", None)
    open_tool("validate", dataset_path=str(data_dir) if data_dir else "")


def _show_pane(ctx, index: int) -> None:
    """Switch the shell's central pane stack.

    The stack lives on the Review window because the rail has to stay put
    across a mode change (§2); reaching it here is the same route the title
    bar's own mode segments take.
    """
    stack = getattr(ctx.window, "_central_pane_stack", None)
    if stack is None or not (0 <= index < stack.count()):
        return
    if stack.currentIndex() != index:
        stack.setCurrentIndex(index)
