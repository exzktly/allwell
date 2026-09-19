"""Home's behaviour: paint the page from the record, and act on what it says.

Every function takes ``ctx`` first and reads only typed state — the page holds
no numbers of its own. Repainting is idempotent and cheap because everything it
draws is already in memory: the completeness record was written by the run and
loaded with the sidecar.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui import theme
from review.core.lazy import lazy
from review.core.record_sync import resync_run_check
from core.safe import swallow
from review.core.selection_controller import (
    groups_centre_refresh, update_active_group_label,
)

from . import collect as hc
from review.data.field_quality_cache import flagged_by_well
from .plate_panel import (
    FLAGGED_FIELD_GLYPH, paint_field_quality, paint_plate, well_tooltip,
)
# The Sample Map tab's layer: which one is showing, and the entry rules for it.
# Module scope, because nothing under ``home/sample_map`` imports this module
# back — there is no cycle here to work around, and the deferred-feature-import
# budget is a ratchet rather than a place to put convenience.
from .sample_map.controller import current_layer, on_tab_entered
from .tab_view import (
    IMPORT_KEY, build_recent_tile, build_saved_plot_tile, definition_subtabs,
    format_value, legend_label, legend_swatch_style,
)

# Cell Gating's controller is reached for two refreshes the sub-tab handler and
# the selection hook drive. Its tab body, and the other four, are built through
# the feature registry instead — see ``build_home_subtab``.
_cell_gating_sync_scatter_gates = lazy(
    "review.features.home.cell_gating.controller",
    "cell_gating_sync_scatter_gates")
_cell_gating_load_cell_areas = lazy(
    "review.features.home.cell_gating.controller",
    "cell_gating_load_cell_areas")
# Saved Plots is removable and lives on another page, so Home reaches its
# "open this plot" entry point through a proxy — resolved on first click, and
# only in a build that ships the feature.
_focus_saved_plot = lazy("review.features.saved_plots.controller",
                         "focus_saved_plot")

_logger = logging.getLogger("review")

#: The masthead's actions, in the order they appear. Each is handled by
#: :func:`run_home_action`, which is also what the next-step row calls — one
#: place decides what an action means.
#:
#: Deliberately almost empty. Reveal-folder and open-dataset are carried by the
#: shell's own action cluster a few pixels above this page, and Analyze and
#: Datasets are two of the three mode segments in the title bar, permanently on
#: screen. Repeating a control that is already visible does not make it easier
#: to find; it makes two of them to keep working, and teaches that this page is
#: where you go to leave it. Reload stays because nothing else offers it and the
#: page reports a record that another process can replace underneath it.
HOME_ACTIONS = (
    ("reload", "Reload"),
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


# ── the sub-tab notebook ────────────────────────────────────────────────────

def _subtabs(ctx):
    return ctx.runtime.home.subtabs


def current_subtab(ctx) -> str:
    """The label of the Home tab now showing, or ``""`` before the page builds."""
    sub = _subtabs(ctx)
    if sub is None or sub.count() == 0:
        return ""
    try:
        return str(sub.tabText(sub.currentIndex()))
    except RuntimeError:
        return ""


def build_home_subtab(ctx, idx: int) -> None:
    """Build the body of the sub-tab at *idx*, once.

    Idempotent: the per-name ``ctx.runtime.home.built`` map guards a second
    call, so the drain and a user's click cannot both construct the same body.
    A builder that raises leaves its tab empty and the rest of the page intact
    — the alternative is a landing page that fails to open because one editor
    could not build.
    """
    sub = _subtabs(ctx)
    if sub is None or idx < 0 or idx >= sub.count():
        return
    name = sub.tabText(idx)
    built = ctx.runtime.home.built
    if built is None:
        built = ctx.runtime.home.built = {}
    if name == "Overview" or built.get(name):
        return
    desc = ctx.features.get(name)
    if desc is None or desc.build is None:
        return
    try:
        desc.build(ctx, sub.widget(idx))
        built[name] = True
    except Exception:
        _logger.exception("Home: build sub-tab %r failed", name)


def drain_home_subtabs(ctx) -> None:
    """Build the definition tabs one per event-loop tick, after Overview.

    Home is the page the app opens on and is built eagerly, so its bodies
    cannot all be constructed inline. They are not built lazily on first click
    either: these editors' widgets are read after a load by persistence
    hydration and by other tabs, without the user having opened them, and that
    was true when they were drained as a page of their own. Draining preserves
    that while keeping startup off the hook.
    """
    sub = _subtabs(ctx)
    if sub is None:
        return

    def _step(idx: int = 0) -> None:
        if _subtabs(ctx) is not sub:
            return          # the page was rebuilt under us; that build drains itself
        try:
            count = sub.count()
        except RuntimeError:
            return          # the window went away mid-drain
        if idx >= count:
            return
        build_home_subtab(ctx, idx)
        QTimer.singleShot(0, lambda: _step(idx + 1))

    QTimer.singleShot(0, _step)


def sync_home_sidebar(ctx) -> None:
    """Put the page's own chrome in the state the current tab warrants.

    **Home has no rail body**, which is what :func:`activate_home` has always
    said and what the rail's sample frame used to contradict. That frame carried
    ``_rep_map_plate``, and two descriptor fields grew to manage it:
    ``reads_well_selection``, which greyed it on a tab that asked nothing of it,
    and ``owns_plate``, which hid it on a tab drawing a plate of its own —
    because two plates on one screen said different things, the rail's coloured
    by group membership and the tab's by variable value, which is the hazard
    ``groups/panel_view`` guards its commit against.

    Both are gone, and so is the plate. It edited a group's membership and never
    wrote ``ctx.state.selection.selected_wells`` — measured, not assumed — so the
    two tabs it was described as feeding, Cell Gating's CDFs and Add Prefix's
    fallback, were reading a selection it could not set. It is the Groups layer's
    plate on the Sample Map tab now, one of three that take turns in one slot,
    and that tab publishes its selection so those two finally read the plate the
    user is looking at. With nothing in the rail frame there is no state to
    manage: it stays hidden, and the fields that decided between three states
    have no reader.

    All that is left here is the **Import** button, which merges a
    sample-definitions JSON — so it names nothing on Overview, and nothing on
    the Variables layer either. The Sample Map tab reaches this through
    ``ctx.runtime.home.sync_sidebar`` rather than importing it: this module
    imports that one, so the call has to travel by attribute lookup, which is
    the indirection ``plot_members`` already uses for the bar tab's drag order.
    """
    name = current_subtab(ctx)
    frame = ctx.widgets.get_or_none("_sidebar_sample_frame")
    if frame is not None:
        frame.setVisible(False)
    btn = ctx.widgets.get_or_none(IMPORT_KEY)
    if btn is not None:
        # Import brings in replicate *selections* and well labels, which is not
        # the thing a user on a page of variables would expect a button called
        # Import to take. That used to be a per-tab rule; the merge makes it a
        # per-**layer** one, since Variables is a layer of a tab whose other two
        # layers are exactly what Import writes.
        btn.setVisible(name in definition_subtabs(ctx)
                       and current_layer(ctx) != "Variables")


def current_help_subject(ctx) -> str:
    """What the help drawer should scroll to for the tab Home is showing.

    The sub-tab's own name, except on **Sample Map**, where the three ways of
    annotating a well are layers with a heading each — so the drawer lands on
    the one the user is looking at rather than at the top of the section.

    Answered here rather than in ``allwell`` so the shell makes **one** deferred
    feature import instead of two. That budget is a ratchet, and a page's own
    "which of me is showing" question belongs to the page.
    """
    name = current_subtab(ctx) or ""
    if name != "Sample Map":
        return name
    return current_layer(ctx) or name


def on_home_subtab_changed(ctx, idx: int) -> None:
    """Build the now-current tab if the drain has not reached it, then refresh."""
    build_home_subtab(ctx, idx)
    name = current_subtab(ctx)
    if name == "Cell Gating":
        # Pick up any gates drawn on the Scatter tab since the last visit.
        _cell_gating_sync_scatter_gates(ctx)
    elif name == "Pipeline Info":
        cb = ctx.runtime.pipeline_info.refresh
        if callable(cb):
            cb()            # re-read in case the dataset changed
    elif name == "Sample Map":
        # The three annotation editors are layers of this tab, so the entry
        # rules are the layer's rather than the sub-tab's. This branch used to
        # read ``"Replicates"``, which the registry stopped being able to produce
        # when they merged — so entering the tab quietly stopped deselecting the
        # active group, and the first drag on the plate rewrote whichever group
        # was left over from the last visit.
        on_tab_entered(ctx)
        if ctx.widgets.has("_rep_active_group_lbl"):
            update_active_group_label(ctx)
    sync_home_sidebar(ctx)


# ── page lifecycle ──────────────────────────────────────────────────────────

def activate_home(ctx) -> None:
    """Fired when the page is shown.

    Home is the one page that shows **no rail frame**. Every other feature's
    activation hook re-shows one of the sidebar's per-page frames, which
    ``on_tab_change`` has just hidden; Home does not, because its own plate is
    on the page and the rail's picker would be a second one on screen.

    That used to leave the rail's nav as the only growable item in its column,
    so its links spread over the full height. The column now carries a trailing
    spacer and ``RailNav`` refuses to exceed its size hint, so a page with no
    rail body is a supported state rather than one that happens to work.
    """
    hide_rail_body(ctx)
    resync_run_check(ctx)
    refresh_home(ctx)
    sync_home_sidebar(ctx)
    if current_subtab(ctx) in definition_subtabs(ctx):
        groups_centre_refresh(ctx)
        _cell_gating_sync_scatter_gates(ctx)


def hide_rail_body(ctx) -> None:
    """Put the rail into the state Home wants: navigation, and nothing else.

    ``on_tab_change`` has already hidden every frame by the time
    :func:`activate_home` runs, so this is redundant there — and it is not
    redundant at startup, which is the case it exists for. The landing page is
    selected before the change handler is connected
    (``core/centre_view.build_centre``), so the handler never fires for it and
    the rail keeps whatever it was built showing. Without this, Home had two
    appearances: the plate picker on launch, none after visiting another page
    and coming back.
    """
    frame = ctx.widgets.get_or_none("_sidebar_main_frame")
    if frame is not None:
        frame.setVisible(False)


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
    # A new dataset's notes are whatever it carries, not an unsaved edit — the
    # status line has to start over with them or the previous dataset's "Saved."
    # reads as a statement about this one.
    from .tab_view import NOTES_IDLE
    status = _widget(ctx, "_home_notes_status")
    if status is not None:
        status.setText(NOTES_IDLE)
    refresh_home(ctx)


def on_home_selection_changed(ctx) -> None:
    """Keep Home's plate showing the app-wide selection, and Cell Gating fed.

    Cell Gating is a tab on this page, and its CDF is drawn from the selected
    wells' cell areas — so a selection change reloads them, exactly as it did
    when Cell Gating lived on a page of its own.

    Only while that tab is the one showing. The reload is a per-well ``get_rows``
    plus a Python-level pass over every cell, per channel; on the page the app
    opens on, where clicking wells is the primary interaction and nothing draws a
    CDF, paying it on every click is the same cost the ``_is_tab_visible`` guard
    exists to keep off the plot tabs.
    """
    if (current_subtab(ctx) == "Cell Gating"
            and ctx.runtime.cell_gating.fluor_gate_edits is not None):
        _cell_gating_load_cell_areas(ctx)
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
    from review.core.selection_controller import set_selected_wells
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
    _paint_saved_plots(ctx)
    _paint_recents(ctx)
    paint_recents_placeholder(ctx)
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
        # A record written by the Datasets Validate tool describes the folder
        # at the moment it was scanned, which can be long after the run — the
        # wording has to say so or the two become indistinguishable.
        bits.append(
            f"checked {ident['finished_at']}"
            if ident["source"] == "validate"
            else f"run finished {ident['finished_at']}")
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
    # Image quality, on the marker channel rather than as a ninth colour —
    # see ``plate_panel.FLAGGED_FIELD_GLYPH`` for why the colour vocabulary is
    # closed, and why field quality would not belong in it even if it were
    # not. The same mapping feeds the tooltip, so a glyph cannot appear
    # without the sentence that explains it.
    quality = flagged_by_well(ctx)
    paint_field_quality(plate, quality)
    plate.setWellTooltipProvider(
        lambda well: well_tooltip(well, cells, record_wells, quality))

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
    # The marker gets a legend row of its own, and only when the plate carries
    # one. The plate's own invariant is that no well can be left without a row
    # to explain it; a glyph nobody can look up breaks that just as surely as
    # two states sharing a colour would. It is listed last and with the glyph
    # in place of a swatch, because it is a different channel rather than a
    # ninth colour — see ``plate_panel.FLAGGED_FIELD_GLYPH``.
    if quality:
        row = len(hc.plate_legend(ctx))
        mark = QLabel(FLAGGED_FIELD_GLYPH, host)
        mark.setFixedSize(theme.Spacing.md, theme.Spacing.md)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(mark, row, 0)
        layout.addWidget(QLabel("Field flagged on image quality", host), row, 1)
        layout.addWidget(QLabel(str(len(quality)), host), row, 2)

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


#: How many thumbnails fit on one row of the strip. Fixed rather than computed
#: from the card's width so the grid does not reflow while the page is being
#: painted, which is when its width is still zero.
_THUMBS_PER_ROW = 6


#: Rows on the Overview card. More than the placeholder shows, because here the
#: user is already in a dataset and browsing sideways rather than picking one to
#: start on — but still short of the store's cap, so the card cannot grow into
#: the page's dominant block.
RECENTS_ON_CARD = 8


def _paint_recents(ctx) -> None:
    """Fill the recent-datasets card, or hide it when there is no history yet.

    Hidden rather than shown empty: "you have opened no datasets" is a sentence
    only ever read by someone who has, at that moment, a dataset open — which
    makes it a contradiction on the page it appears on. The first launch's
    version of this list lives on the placeholder instead, where it is true.

    The dataset currently open is dropped from the rows. It is the subject of
    every other block on this page, and offering to open it again is offering to
    do nothing.
    """
    card = _widget(ctx, "_home_recents_card")
    host = _widget(ctx, "_home_recents")
    note = _widget(ctx, "_home_recents_note")
    if card is None or host is None or note is None:
        return
    rows = recent_rows(ctx, RECENTS_ON_CARD,
                       exclude=getattr(ctx.state.dataset, "data_dir", None))
    # Cleared before the early return, not after it: a card hidden while still
    # holding the last dataset's rows shows them again the moment anything
    # re-shows it, and the state that gets here is a *transition* (the history
    # emptied, or every row filtered out) rather than the static
    # feature-presence check the saved-plots card early-outs on.
    _clear(host)
    card.setVisible(bool(rows))
    if not rows:
        return
    unreachable = sum(1 for row in rows if not row[3])
    note.setText(
        "Click one to open it."
        + (f" {unreachable} of these cannot be reached right now — hover for "
           "why." if unreachable else ""))
    layout = host.layout()
    for row in rows:
        layout.addWidget(build_recent_tile(ctx, host, row))


def _paint_saved_plots(ctx) -> None:
    """Fill the saved-plot strip, or hide the card when there is nothing to show.

    Hidden rather than shown empty in two different situations, which the note
    keeps apart: a build without the Saved Plots feature has no such thing to
    report, and a build with it and an empty gallery has nothing saved *yet*.
    The first is not a state the user can act on, so the card goes away
    entirely; the second is, so it says so and points at how.
    """
    card = _widget(ctx, "_home_saved_plots_card")
    host = _widget(ctx, "_home_saved_plots")
    note = _widget(ctx, "_home_saved_plots_note")
    if card is None or host is None or note is None:
        return
    strip = hc.saved_plots_strip(ctx)
    if not strip["available"]:
        card.setVisible(False)
        return
    card.setVisible(True)
    _clear(host)
    total = strip["total"]
    shown = strip["shown"]
    if not shown:
        note.setText("No plots saved from this dataset yet — hover a plot's "
                     "top-right corner and click Save to put one here.")
        return
    hidden = total - len(shown)
    note.setText(
        f"{total} saved plot{'' if total == 1 else 's'}"
        + (f" — the {len(shown)} most recent are shown; open Saved Plots for "
           f"the other {hidden}." if hidden else ". Click one to open it."))
    grid = host.layout()
    for index, entry in enumerate(shown):
        grid.addWidget(build_saved_plot_tile(ctx, host, entry),
                       index // _THUMBS_PER_ROW, index % _THUMBS_PER_ROW)


def open_saved_plot(ctx, plot_id: str) -> None:
    """Send the user from a Home thumbnail to that plot in the gallery.

    Reached through the feature registry rather than by importing Saved Plots:
    the feature is removable, and Home must build in a profile that ships without
    it. Anything that stops the jump working is said out loud — arriving at the
    top of an unrelated page with no explanation is the shape this exact kind of
    cross-page link fails in.
    """
    if ctx.features.get("Saved Plots") is None:
        return
    with swallow("home: open saved plot"):
        if not _focus_saved_plot(ctx, plot_id):
            ctx.notify.set_status(
                "That saved plot could not be opened — it may have been "
                "deleted. Open Saved Plots to see what is there.")


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
        value.setWordWrap(True)
        # A receipt line is normally a plain statement of what was set, but a
        # setting can record that a step did not run — a reference-well
        # correction with no well named leaves every frame untouched, and the
        # output is bit-for-bit identical to a corrected one. That reads as
        # ordinary text unless it is coloured like the warning it is.
        if row.get("severity"):
            value.setStyleSheet(
                f"color:{_SEVERITY_COLORS.get(row['severity'], theme.Colors.text_muted)};")
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
    # What this declaration *does*, beside what there is of it. A count alone
    # puts groups, variables, labels and saved selections in one column as
    # four interchangeable tallies, which is exactly the reading that made
    # them hard to tell apart: two of the four change which bars exist, one
    # changes only what a bar is called, and one changes nothing on any figure.
    effect = str(row.get("effect") or "")
    if effect:
        chip = QLabel(effect, host)
        chip.setObjectName("Muted")
        line.addWidget(chip)
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
#:
#: The shortfall states are not here. Showing a user the gap a second time is
#: not a next step — every one of them is fixed by running the pipeline again
#: over what is missing, so they all route to ``complete``.
_ACTION_PAGE = {
    "plot": "Plotting",
}

#: Actions that land on a tab of this page rather than on another page.
_ACTION_SUBTAB = {"samples": "Sample Map"}


def run_home_action(ctx, action: str) -> None:
    """Everything a Home button can do, in one place.

    The next-step row and the masthead share this, so "open this in Analyze"
    cannot come to mean two things depending on which control was pressed.

    Actions that cannot be performed — no dataset to reload, a pane the running
    profile does not include — do nothing rather than half-doing something.
    """
    with swallow(f"home action {action}"):
        if action == "open":
            _open_dataset(ctx)
            return
        if action == "reload":
            _reload_dataset(ctx)
            return
        if action in _PANE_INDEX:
            _show_pane(ctx, _PANE_INDEX[action])
            return
        if action == "validate":
            _open_validate(ctx)
            return
        subtab = _ACTION_SUBTAB.get(action)
        if subtab:
            show_home_subtab(ctx, subtab)
            return
        if action == "complete":
            _complete_run_in_analyze(ctx)
            return
        page = _ACTION_PAGE.get(action)
        if page:
            notebook = getattr(ctx.window, "_notebook", None)
            if notebook is not None:
                notebook.setCurrentByName(page)


def show_home_subtab(ctx, name: str) -> None:
    """Select the Home tab labelled *name*.

    Navigating within the page rather than to it: the button that offers to take
    the user to their groups is itself on Home, so ``setCurrentByName("Home")``
    would be a no-op and the offer would do nothing.
    """
    sub = _subtabs(ctx)
    if sub is None:
        return
    for i in range(sub.count()):
        if sub.tabText(i) == name:
            sub.setCurrentIndex(i)
            return


#: Ten entries is the store's cap; the placeholder shows fewer, because a
#: launch screen offering ten folders is a file manager and the user came here
#: to get back to the one or two they are actually working on.
RECENTS_ON_PLACEHOLDER = 5


def recent_rows(ctx, limit: int, *, exclude=None) -> list:
    """The recent-datasets rows, with Home's own answer to "and then repaint".

    The rules — which folders are reachable, what each row is called, what its
    tooltip says — are ``core.recent_view.entry_rows``, shared with the rail
    head's dataset menu so the three surfaces cannot drift. What Home adds is
    the callback a removal runs afterwards: **both** of its surfaces, because
    the card and the placeholder are the same list in the two states of one
    page, and a row forgotten on one while the other still holds it is a list
    that disagrees with itself the moment a dataset is opened or closed.
    """
    from review.core.recent_view import entry_rows

    return entry_rows(ctx, limit, exclude=exclude,
                      on_removed=lambda: _repaint_recents(ctx))


def _repaint_recents(ctx) -> None:
    """Redraw both surfaces that show the history. See :func:`recent_rows`."""
    _paint_recents(ctx)
    paint_recents_placeholder(ctx)


def paint_recents_placeholder(ctx) -> None:
    """Put the recents on Home's no-dataset placeholder.

    The one moment this list matters most is launch, with nothing loaded — and
    that is precisely the moment Home's body is hidden behind the shared
    placeholder. So the placeholder carries them, through ``EmptyState``'s own
    entries API, rather than Home growing a no-dataset state of its own: the
    shared wording and the shared moment of the swap are what
    ``core/empty_state_copy.py`` exists to protect, and one page opting out of
    them re-introduces exactly the drift that module ended.
    """
    stack = _widget(ctx, "_home_empty_stack")
    placeholder = getattr(stack, "_empty_placeholder", None) if stack else None
    if placeholder is None or not hasattr(placeholder, "setEntries"):
        return
    placeholder.setEntries(recent_rows(ctx, RECENTS_ON_PLACEHOLDER))


def _open_dataset(ctx) -> None:
    """The folder picker, reached from Home's empty state and its next-step row.

    The window's own ``_browse`` rather than a second ``QFileDialog`` here: the
    rail head's Open menu action already calls it, and it is the only place
    that knows what happens after a folder is chosen — the picker is followed by
    a deferred load, not a direct one.
    """
    browse = getattr(ctx.window, "_browse", None)
    if callable(browse):
        browse()


def _reload_dataset(ctx) -> None:
    data_dir = getattr(ctx.state.dataset, "data_dir", None)
    if data_dir is None:
        return
    from review.core.load_controller import load_directory
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
    open_tool("validate", dataset_path=dataset_root(ctx))


def _complete_run_in_analyze(ctx) -> None:
    """Hand this dataset to Analyze, configured to finish what it started.

    The offer Home makes for every kind of shortfall it can report. Opening
    Analyze on the folder alone is not enough to complete a run, in two
    independent ways, and both were paid for once already in Validate & Repair's
    own handoff — this goes through the same shell entry point rather than
    re-deriving them:

    * the form keeps whatever was in it, so the schema, separator, channel roles
      and — the one that silently breaks the done-set lookup — the ``csv_prefix``
      would all have to be re-entered by hand and matched exactly. The dataset's
      own recorded sidecar goes with it instead. Review has already read that
      file, so it is passed from state rather than re-read.
    * a plain re-run *skips* any well whose archive and CSV both exist, however
      few of its FOV-groups landed, which is precisely the state this page
      exists to report. So the run is asked for FOV-level incremental mode, and
      the wells an incremental run would skip forever are named explicitly.

    Reached through the top-level window because the Analyze pane is the outer
    shell's, not Review's; ``ctx.window`` is Review's own. Duck-typed and
    guarded, the same shape as the Datasets handoff above, because a build that
    mounts no Analyze pane must do nothing rather than raise.
    """
    shell = ctx.window.window()
    handoff = getattr(shell, "_open_dataset_in_analyze", None)
    if not callable(handoff):
        return
    root = dataset_root(ctx)
    if not root:
        return
    handoff(
        Path(root),
        dict(getattr(ctx.state.dataset, "pipeline_info", None) or {}),
        incremental=True,
        force_wells=hc.wells_needing_force(ctx),
    )


def dataset_root(ctx) -> str:
    """The folder a Datasets tool wants, which is not the one Review loaded.

    Review's ``data_dir`` is where the measurements CSVs are, and for the layout
    the pipeline writes that is the ``out/`` subfolder — Analyze's own loader
    descends into it (``load_controller.load_path``). Every Datasets tool wants
    the folder *above* it, the one holding both ``in/`` and ``out/``, because
    that is what a dataset is to them (``datasets.core.dataset_paths``).
    Handing them ``out/`` fails their check and leaves the user re-picking a
    folder the app already had open.

    ``in_dir`` is the honest signal: the loader sets it precisely when it
    detected that layout, and leaves it ``None`` for a flat folder of CSVs,
    which has no parent to prefer and is not a dataset those tools can read
    anyway.
    """
    dataset = getattr(ctx.state, "dataset", None)
    in_dir = getattr(dataset, "in_dir", None)
    if in_dir is not None:
        return str(Path(in_dir).parent)
    data_dir = getattr(dataset, "data_dir", None)
    return str(data_dir) if data_dir else ""


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
