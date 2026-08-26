"""The Home page's widget tree.

Structure only — every number on screen comes from :mod:`.collect`, and every
behaviour from :mod:`.controller`. What lives here is the arrangement and the
one rendering rule the page is built around: a figure that could not be obtained
draws an em-dash and says why, rather than a zero that would read as a
measurement.

The page is a notebook. **Overview** is what the app opens on — the reporting
column this module builds, whose block order follows the questions a returning
user asks: what is this plate, what should I do about it, did the run finish,
what did I say about it, what is in it, what went wrong, what was it run with,
what have I already done. The tabs after it are where the user *declares* what
the wells mean. Each has its own sub-package beside this module —
``cell_gating/``, ``groups/``, ``labels/``, ``ratios/``, ``pipeline_info/`` —
and each is built by calling its own descriptor's ``build``, whose adapter in
``features/registry.py`` holds the only import of the package, so a sub-package a
profile strips is never imported.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QTabWidget, QVBoxLayout, QWidget,
)

import theme
from well_viewer.core.app_shell_view import mirror_tab_widget
from well_viewer.core.selection_controller import import_selections_from_json
from well_viewer.core.ui_helpers import notes_schedule_save
from widgets.icon_button import IconButton
from widgets.page_body import build_page_body

from . import collect as hc
from .plate_panel import STATE_COLORS, STATE_LABELS, build_home_plate

#: The em-dash a figure the page could not obtain renders as.
DASH = "—"

_NOTES_KEY = "_home_notes_edit"

#: The Import action, registered so the sub-tab handler can hide it on Overview.
IMPORT_KEY = "_home_import_btn"

#: The report this module builds. Every other tab is a definition editor whose
#: body lives in a sub-package beside this one and is declared in the feature
#: registry, so no tab is *selected* by name here — the list comes from
#: ``active_subtabs``. Two per-tab presentation facts are still named, below and
#: in ``controller._TABS_USING_THE_PLATE``: which tabs inset their frame, and
#: which read the well selection. Both would sit better on the descriptors, the
#: way ``per_well_selection`` already does for top-level tabs.
OVERVIEW = "Overview"

def _subtab_inset(ctx, name: str) -> int:
    """This tab's own gutter, in pixels.

    Which tabs want one is the descriptor's ``subtab_inset``, not a list here:
    the report and the read-only panes paint to their own edges, the editable
    grids want a gutter, and a tab added tomorrow should not silently get the
    wrong one because nobody updated a constant. The descriptor carries the
    decision and this supplies the spacing token, so the registry never imports
    ``theme``. The frames themselves are flush with the notebook either way,
    which is what ``test_page_alignment`` measures.
    """
    desc = ctx.features.get(name)
    return theme.Spacing.md if (desc is not None and desc.subtab_inset) else 0


def active_subtabs(ctx) -> tuple[str, ...]:
    """The tabs this build ships: the report, then the registry's Home sub-leaves.

    Enumerated from the registry rather than from a constant filtered by
    ``Profile.includes_subtab``. That helper is a bare string check against the
    ``[subtabs]`` allowlist and never consults descriptors, so asking it about a
    name nothing declares let a manifest drop tabs it had never heard of. Here
    the profile has already been applied — ``build_feature_registry`` runs
    ``Profile.includes`` over the descriptors — so every name a ``[subtabs]``
    line can reference is a declared sub-leaf, and one marked ``removable=False``
    survives regardless. Same mechanism Plotting uses (``centre_view``).
    """
    return (OVERVIEW, *ctx.features.sub_leaves("Home"))


def definition_subtabs(ctx) -> tuple[str, ...]:
    """The tabs that are editors rather than the report.

    Derived from the live list, not a constant: a build that ships fewer tabs
    must not keep claiming the missing ones are on screen.
    """
    return tuple(t for t in active_subtabs(ctx) if t != OVERVIEW)


def _card(parent: QWidget, title: str = "") -> tuple[QFrame, QVBoxLayout]:
    """A titled panel. Returns the frame and the layout its body goes in."""
    frame = QFrame(parent)
    frame.setObjectName("HomeCard")
    outer = QVBoxLayout(frame)
    outer.setContentsMargins(theme.Spacing.lg, theme.Spacing.md,
                             theme.Spacing.lg, theme.Spacing.md)
    outer.setSpacing(theme.Spacing.sm)
    if title:
        label = QLabel(title, frame)
        label.setObjectName("HomeCardTitle")
        outer.addWidget(label)
    return frame, outer


def _muted(parent: QWidget, text: str) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    return label


def build_home_tab(ctx, parent: QWidget) -> None:
    """Build the page shell: the control band, then the sub-tab notebook.

    Overview is built here because it is what the app opens on. The definition
    tabs are built one per event-loop tick by ``controller.drain_home_subtabs``
    immediately afterwards, which is where they were built before they moved
    onto this page — their old page was drained the same way, and code across
    the app (persistence hydration, the plot tabs' threshold quick-edit, the
    Playground's context bar) reads their widgets shortly after a load without
    the user having opened anything. Building them inline instead would put all
    of it on the startup path, which the landing page cannot pay.
    """
    band, body = build_page_body(parent)

    # Every definition on this page autosaves to the data folder on edit, so
    # there is no Save button to pair this with: Import merely merges an
    # external JSON in. It is an action on the page rather than on any one tab,
    # but it means nothing beside the report, so it is hidden on Overview.
    import_btn = ctx.widgets.register(
        IMPORT_KEY, IconButton("plus", band, text="Import"))
    import_btn.setToolTip(
        "Import selections from a JSON file (merges into the current set).")
    import_btn.clicked.connect(lambda _=False: import_selections_from_json(ctx))
    band.add_action(import_btn)

    sub_tabs = QTabWidget(parent)
    # One theme.py rule names every nested sub-tab bar in the pane — this one,
    # Plotting's and the Image Browser's — so the three cannot drift apart.
    sub_tabs.setObjectName("HomeSubTabs")
    sub_tabs.tabBar().setExpanding(False)
    sub_tabs.tabBar().setElideMode(Qt.ElideNone)
    sub_tabs.tabBar().setUsesScrollButtons(True)
    body.layout().addWidget(sub_tabs, 1)
    ctx.runtime.home.subtabs = sub_tabs
    ctx.runtime.home.built = {}

    # Fresh frames mean nothing is built into them yet, so the per-tab
    # "already built" flags reset with them. Cell Gating carries its own —
    # ``build_cell_gating_subtab`` early-returns on it, because persistence can
    # reach that builder with no sub-tab index to hand over — and the frame loop
    # cleared it here before the builders moved onto descriptors. Without the
    # clear, a rebuilt page would early-return into an empty tab whose widget
    # handles point at destroyed C++ objects, with no exception and no log line,
    # since ``build_home_subtab`` would still record the tab as built. No path
    # builds Home twice per runtime today; this costs one line and settles it.
    ctx.runtime.cell_gating.subtab_built = False

    # Every frame exists as soon as this loop ends; only the bodies are drained.
    # The map is what lets a builder be reached by name from outside the
    # notebook — persistence materializes the Cell Gating editor that way,
    # having no sub-tab index to hand over.
    frames = ctx.runtime.home.frames = {}
    for name in active_subtabs(ctx):
        frame = QWidget(sub_tabs)
        inset = _subtab_inset(ctx, name)
        QVBoxLayout(frame).setContentsMargins(inset, inset, inset, inset)
        sub_tabs.addTab(frame, name)
        frames[name] = frame

    from .controller import (
        drain_home_subtabs, on_home_subtab_changed, sync_home_sidebar,
    )
    sub_tabs.currentChanged.connect(lambda idx: on_home_subtab_changed(ctx, idx))

    # The visible bar lives in the shell's mode band beside the mode selector,
    # not above this page's body. ``mirror_tab_widget`` hides this widget's own
    # bar and drives it from one up there, leaving the QTabWidget's API — which
    # the plate-commit guard and several tests read — untouched.
    mirror_tab_widget(ctx.window, "Home", sub_tabs)

    build_home_overview(ctx, sub_tabs.widget(0))
    # Decide the band's and the rail plate's state here rather than leaving it to
    # the activate hook: Home is page 0 and already current when this runs, so no
    # currentChanged fires and on_tab_change has returned early — the opening
    # screen would otherwise show an Import button belonging to tabs it is not on.
    sync_home_sidebar(ctx)
    drain_home_subtabs(ctx)


def build_home_overview(ctx, parent: QWidget) -> None:
    """Build the Overview tab — the report the page opens on."""
    reg = ctx.widgets
    outer = parent.layout() or QVBoxLayout(parent)
    outer.setContentsMargins(0, 0, 0, 0)

    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setObjectName("HomeScroll")
    # Before a dataset is open there is nothing here to report, and a page of
    # em-dashes under a masthead reading "No dataset open" describes its own
    # emptiness at length rather than saying what to do about it. Home takes the
    # same placeholder every other Review page shows, through the same registry,
    # so the wording and the moment of the swap are shared rather than
    # re-decided here. No ``reason``: Home is gated on the dataset alone.
    from well_viewer.core.ui_helpers import wrap_with_empty_state

    stack = wrap_with_empty_state(ctx.window, scroll, icon="home")
    reg.register("_home_empty_stack", stack)
    outer.addWidget(stack)
    # ...but Home is the page the app opens on, so pointing at a control in the
    # rail and stopping there is weaker here than anywhere else. The button runs
    # the same action the masthead and the next-step row use, so "open a
    # dataset" cannot come to mean two things.
    placeholder = getattr(stack, "_empty_placeholder", None)
    if placeholder is not None:
        placeholder.setAction("Open a dataset", _action_handler(ctx, "open"))

    page = QWidget(scroll)
    scroll.setWidget(page)
    body = QVBoxLayout(page)
    body.setContentsMargins(theme.Spacing.xl, theme.Spacing.lg,
                            theme.Spacing.xl, theme.Spacing.xl)
    body.setSpacing(theme.Spacing.lg)

    body.addWidget(_build_masthead(ctx, page, reg))
    body.addWidget(_build_next_step(ctx, page, reg))
    body.addLayout(_build_plate_and_notes(ctx, page, reg))
    body.addWidget(reg.register("_home_tiles", _tile_strip(page)))
    body.addLayout(_build_health_and_receipt(ctx, page, reg))
    body.addWidget(_build_your_work(ctx, page, reg))
    body.addWidget(_build_saved_plots(ctx, page, reg))
    body.addStretch(1)

    from .controller import hide_rail_body, refresh_home
    # Home is the landing page, and the landing page's activation hook never
    # fires — ``build_centre`` selects it before connecting the change handler.
    # So the rail has to be put into Home's state here, or the page looks one
    # way on launch and another way every time after.
    hide_rail_body(ctx)
    refresh_home(ctx)


# ── masthead ────────────────────────────────────────────────────────────────

def _build_masthead(ctx, page: QWidget, reg) -> QWidget:
    host = QWidget(page)
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(theme.Spacing.lg)

    left = QVBoxLayout()
    left.setSpacing(theme.Spacing.xs)
    left.addWidget(reg.register("_home_title", QLabel("", host)))
    reg.get("_home_title").setObjectName("HomeTitle")
    left.addWidget(reg.register("_home_path", _muted(host, "")))
    left.addWidget(reg.register("_home_schema", _muted(host, "")))
    row.addLayout(left, 1)

    right = QVBoxLayout()
    right.setSpacing(theme.Spacing.xs)
    right.setAlignment(Qt.AlignRight | Qt.AlignTop)
    right.addWidget(reg.register("_home_run_meta", _muted(host, "")))
    actions = QHBoxLayout()
    actions.setSpacing(theme.Spacing.xs)
    from .controller import HOME_ACTIONS
    for key, label in HOME_ACTIONS:
        button = QPushButton(label, host)
        button.setObjectName("HomeAction")
        button.clicked.connect(_action_handler(ctx, key))
        actions.addWidget(button)
        reg.register(f"_home_action_{key}", button)
    right.addLayout(actions)
    row.addLayout(right)
    return host


def _action_handler(ctx, key: str):
    def _run(_checked=False) -> None:
        from .controller import run_home_action
        run_home_action(ctx, key)
    return _run


# ── next step ───────────────────────────────────────────────────────────────

def _build_next_step(ctx, page: QWidget, reg) -> QWidget:
    frame, layout = _card(page)
    frame.setObjectName("HomeNextStep")
    row = QHBoxLayout()
    row.setSpacing(theme.Spacing.md)
    row.addWidget(reg.register("_home_next_text", QLabel("", frame)), 1)
    button = reg.register("_home_next_button", QPushButton("", frame))
    button.clicked.connect(lambda _c=False: _run_next_step(ctx))
    row.addWidget(button)
    layout.addLayout(row)
    return frame


def _run_next_step(ctx) -> None:
    from .controller import run_home_action
    run_home_action(ctx, hc.next_step(ctx).get("action", ""))


# ── plate + notes ───────────────────────────────────────────────────────────

def _build_plate_and_notes(ctx, page: QWidget, reg) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(theme.Spacing.lg)

    plate_card, plate_layout = _card(page, "Plate")
    plate_row = QHBoxLayout()
    plate_row.setSpacing(theme.Spacing.lg)
    plate = reg.register("_home_plate", build_home_plate(plate_card))
    from .controller import on_home_plate_selection
    plate.selectionChanged.connect(
        lambda wells: on_home_plate_selection(ctx, wells))
    plate_row.addWidget(plate, 0, Qt.AlignHCenter | Qt.AlignTop)
    plate_row.addWidget(reg.register("_home_legend", _legend(plate_card)), 0,
                        Qt.AlignTop)
    plate_layout.addLayout(plate_row)
    plate_layout.addWidget(reg.register("_home_plate_summary",
                                        _muted(plate_card, "")))
    row.addWidget(plate_card, 7)

    row.addWidget(_build_notes(ctx, page, reg), 5)
    return row


def _legend(parent: QWidget) -> QWidget:
    host = QWidget(parent)
    grid = QGridLayout(host)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(theme.Spacing.sm)
    grid.setVerticalSpacing(theme.Spacing.xs)
    host.setProperty("rows", {})
    return host


def _build_notes(ctx, page: QWidget, reg) -> QWidget:
    """The one editable thing on an otherwise read-only page.

    Always editable rather than click-to-edit: the hover lift already tells the
    reader this region accepts typing, and adding a mode to a page whose whole
    premise is that it only reports would cost more than it saves. It commits on
    focus-out, the same rule the long-form editor follows, and mirrors into that
    editor so the two can never hold different text.
    """
    from PySide6.QtWidgets import QPlainTextEdit

    frame, layout = _card(page, "Notes")

    class _HomeNotes(QPlainTextEdit):
        def focusOutEvent(self, event):
            super().focusOutEvent(event)
            from well_viewer.core.safe import swallowed
            try:
                notes_schedule_save(ctx, widget_key=_NOTES_KEY)
            except Exception:
                swallowed("home notes commit")
            else:
                _set_notes_status(ctx, "saved")

    edit = reg.register(_NOTES_KEY, _HomeNotes(frame))
    edit.setObjectName("HomeNotes")
    edit.setPlaceholderText(
        "What happened on this plate? Anything the numbers won't tell you later.")
    edit.setPlainText(ctx.state.selection.notes_text or "")
    layout.addWidget(edit, 1)
    # The commit is on focus-out and therefore invisible: a user types, clicks
    # away, and nothing on screen distinguishes "written" from "lost". The line
    # below says which, and is the only part of this page that changes without
    # the record changing.
    status = reg.register("_home_notes_status", _muted(frame, NOTES_IDLE))
    layout.addWidget(status)
    edit.textChanged.connect(lambda: _set_notes_status(ctx, "dirty"))
    return frame


#: What the line under the notes box says in each state. Written out rather than
#: assembled so the three readings can be compared side by side — the failure
#: wording is the one that matters and it must not look like the others.
NOTES_IDLE = "Saves when you click away."
NOTES_DIRTY = "Unsaved — saves when you click away."
NOTES_SAVED = "Saved."
_NOTES_STATUS = {"idle": NOTES_IDLE, "dirty": NOTES_DIRTY, "saved": NOTES_SAVED}


def _set_notes_status(ctx, state: str) -> None:
    label = ctx.widgets.get_or_none("_home_notes_status")
    if label is not None:
        label.setText(_NOTES_STATUS.get(state, NOTES_IDLE))


# ── tiles ───────────────────────────────────────────────────────────────────

def _tile_strip(page: QWidget) -> QWidget:
    host = QWidget(page)
    grid = QGridLayout(host)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(theme.Spacing.md)
    grid.setVerticalSpacing(theme.Spacing.md)
    return host


# ── health + receipt ────────────────────────────────────────────────────────

def _build_health_and_receipt(ctx, page: QWidget, reg) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(theme.Spacing.lg)

    health_card, health_layout = _card(page, "Health")
    health_layout.addWidget(reg.register("_home_health", _rows_host(health_card)), 1)
    validate = QPushButton("Check this dataset now", health_card)
    validate.setObjectName("HomeAction")
    validate.clicked.connect(_action_handler(ctx, "validate"))
    health_layout.addWidget(validate, 0, Qt.AlignLeft)
    reg.register("_home_validate_btn", validate)
    row.addWidget(health_card, 7)

    receipt_card, receipt_layout = _card(page, "Run parameters")
    receipt_layout.addWidget(
        reg.register("_home_receipt", _rows_host(receipt_card)), 1)
    row.addWidget(receipt_card, 5)
    return row


def _rows_host(parent: QWidget) -> QWidget:
    host = QWidget(parent)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(theme.Spacing.xs)
    return host


# ── your work ───────────────────────────────────────────────────────────────

def _build_your_work(ctx, page: QWidget, reg) -> QWidget:
    card, layout = _card(page, "Your work here")
    layout.addWidget(reg.register("_home_work", _rows_host(card)))
    return card


# ── saved plots ─────────────────────────────────────────────────────────────

#: The thumbnail tile's width in pixels, and the height its caption is given.
#: A tile is sized rather than let float because the strip is a grid of them and
#: an unsized tile takes its width from the longest plot title in the gallery,
#: which reflows the whole page whenever a plot is renamed.
_THUMB_W = 168
_THUMB_H = 118
_THUMB_CAPTION_H = 32


def _build_saved_plots(ctx, page: QWidget, reg) -> QWidget:
    """The strip of saved-plot thumbnails, and the route from one to the gallery.

    Home already counts what the user has declared about a plate; this shows what
    they have *made* from it. Clicking a thumbnail opens the Saved Plots gallery
    at that plot rather than at the top of the list, which is the difference
    between the strip being a shortcut and being a picture of a shortcut.

    The card is built even in a profile without Saved Plots — building it and
    hiding it keeps the page's block order fixed for every build — and the
    controller hides it when the feature is absent or the gallery is empty.
    """
    card, layout = _card(page, "Saved plots")
    layout.addWidget(reg.register("_home_saved_plots_note", _muted(card, "")))
    host = reg.register("_home_saved_plots", QWidget(card))
    grid = QGridLayout(host)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(theme.Spacing.md)
    grid.setVerticalSpacing(theme.Spacing.md)
    layout.addWidget(host)
    return reg.register("_home_saved_plots_card", card)


def _thumbnail(image_path) -> QPixmap | None:
    """Decode *image_path* straight to thumbnail size, or ``None`` if it is gone.

    Through ``QImageReader.setScaledSize`` rather than by loading the file and
    scaling it after: a saved plot's PNG is a 300 dpi capture — routinely
    1500×960 and a quarter of a megabyte — and Home draws twelve of them at
    152 px on every refresh, on the UI thread. Decoding the full frame first
    does all that work and throws it away. The reader scales during decode, so
    the full-size image never exists.
    """
    from PySide6.QtGui import QImageReader

    if image_path is None:
        return None
    reader = QImageReader(str(image_path))
    source = reader.size()
    if not reader.canRead() or not source.isValid() or source.isEmpty():
        return None
    box_w = _THUMB_W - 2 * theme.Spacing.xs
    scale = min(box_w / source.width(), _THUMB_H / source.height())
    reader.setScaledSize(QSize(max(1, round(source.width() * scale)),
                               max(1, round(source.height() * scale))))
    image = reader.read()
    if image.isNull():
        return None
    return QPixmap.fromImage(image)


def build_saved_plot_tile(ctx, parent: QWidget, entry: dict) -> QWidget:
    """One clickable thumbnail: the figure, and its title underneath.

    A button rather than a styled label, so the tile is reachable by keyboard and
    announces itself as something that can be pressed — a bare pixmap that
    happens to respond to clicks is the affordance this page's own notes warn
    about elsewhere.

    The picture and the caption are **child labels in a vertical layout**, not
    the button's own ``icon`` and ``text``. A QPushButton lays those two out side
    by side, which on a tile this width puts the thumbnail in the left half and
    runs the title off the right edge — and neither ``icon()`` nor ``text()``
    shows it, because both hold exactly what they were given. The labels are
    transparent to the mouse so a click anywhere on the tile still reaches the
    button.
    """
    tile = QPushButton(parent)
    tile.setObjectName("HomeThumbTile")
    tile.setFixedSize(_THUMB_W, _THUMB_H + _THUMB_CAPTION_H)
    tile.setCursor(Qt.PointingHandCursor)
    tile.setToolTip(
        f"{entry['title']} — saved from {entry['tab'] or 'a plot'}"
        f"\nOpen this plot in Saved Plots.")

    column = QVBoxLayout(tile)
    column.setContentsMargins(theme.Spacing.xs, theme.Spacing.xs,
                              theme.Spacing.xs, theme.Spacing.xs)
    column.setSpacing(theme.Spacing.xs)

    picture = QLabel(tile)
    picture.setAlignment(Qt.AlignCenter)
    picture.setFixedHeight(_THUMB_H)
    picture.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    pixmap = _thumbnail(entry.get("image"))
    if pixmap is None:
        # The record exists and its image does not. Saying so on the tile keeps
        # a missing file distinguishable from a plot that drew nothing, which is
        # the whole point of the record carrying a filename at all.
        picture.setObjectName("Muted")
        picture.setText("(image unavailable)")
    else:
        picture.setPixmap(pixmap)
    column.addWidget(picture)

    caption = QLabel(tile)
    caption.setObjectName("Muted")
    caption.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
    caption.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    # Elided rather than wrapped: the tile is a fixed height, so a two-line title
    # would push the picture out of it. The full title is in the tooltip.
    caption.setText(caption.fontMetrics().elidedText(
        entry["title"], Qt.ElideRight, _THUMB_W - 2 * theme.Spacing.sm))
    column.addWidget(caption)

    from .controller import open_saved_plot
    tile.clicked.connect(
        lambda _=False, _id=entry["id"]: open_saved_plot(ctx, _id))
    return tile


# ── rendering helpers used by the controller ────────────────────────────────

def format_value(value) -> str:
    """A figure, or the em-dash that says it could not be obtained."""
    if isinstance(value, hc.Missing):
        return DASH
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def legend_swatch_style(state: str) -> str:
    """The swatch beside a legend row, showing what the plate actually paints.

    A state the plate leaves uncoloured gets an empty outline rather than a
    filled chip: a legend that fills a swatch for a well that carries no fill is
    a legend the reader cannot match to the plate, which is the one job it has.
    """
    colour = STATE_COLORS.get(state)
    if colour is None:
        return (f"background:transparent; border:1px solid {theme.Colors.border_strong};"
                f" border-radius:{theme.Radii.xs}px;")
    return (f"background:{colour}; border-radius:{theme.Radii.xs}px;")


def legend_label(state: str) -> str:
    return STATE_LABELS.get(state, state)
