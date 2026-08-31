"""
theme.py
--------
Design tokens for the All-Well interface, plus a base Qt stylesheet
builder.

This module is the single source of truth for colors, typography, spacing,
and radii. Widget code should read the constants here rather than hardcoding
values, and the application-wide chrome comes from :func:`qss`.
"""

from __future__ import annotations


# ── Well palette (single source of truth) ────────────────────────────────────
# 48 maximally-distinct colours used everywhere the app needs to assign a
# colour to a well, replicate, or trace. Generated via a greedy max-min
# selection in OKLab space (min pairwise distance ≈ 0.09). The first colour
# (light blue) is positioned in the most-used slot — the original first colour
# was a dark red that read as too heavy at a single-well selection.
#
# This tuple is the canonical list. Two other consumers reference it:
#   * ``Colors.trace`` / ``CPub.trace`` below — what
#     ``widgets/plot_card.plot_palette()`` feeds into matplotlib's
#     ``axes.prop_cycle`` (used for screen + publication modes).
#   * ``well_viewer.plate_layout.WELL_COLORS`` — a plain ``list()`` copy, read
#     by the ``_rank_color_well`` / ``_rank_color_rset`` helpers and every
#     plot renderer that needs a per-well colour by rank.
#
# Keeping the list here (not in ui/theme/styles.py) lets the ``widgets``
# package — which is supposed to import only ``theme`` — see the full
# palette without reaching into application-side modules.
# NOTE — length must be **96** (one colour per 96-well plate position), not a
# submultiple of it. Well colour is ``WELL_COLORS[well_rank(token) % len]`` and
# ranks step by 12 (the column count) going *down* a plate column, so any length
# that divides a multiple of 12 within one column (e.g. 48 = 4×12) makes the
# colours visibly repeat down each column — rows E–H mirror A–D in "sets of
# four". 96 distinct entries give every well its own colour and remove the wrap.
WELL_COLORS_TUPLE: tuple = (
    "#6AB2FB", "#06F906", "#0606F9", "#DF2020",
    "#138613", "#D406F9", "#FAAC38", "#6E2277",
    "#6AFBE5", "#2E719E", "#EC79B8", "#FAFA38",
    "#774C22", "#131386", "#05C783", "#806AFB",
    "#B37D19", "#C7058D", "#5C05C7", "#2E9E9E",
    "#950437", "#FA7238", "#AAC705", "#224877",
    "#9C39C6", "#D4DD88", "#AA88DD", "#779E2E",
    "#F906BC", "#0553C7", "#068CF9", "#20D6DF",
    "#E56AFB", "#FA387C", "#DD9D88", "#777222",
    "#79EC7F", "#227762", "#6938FA", "#05C705",
    "#B34F19", "#D16167", "#C7A005", "#471386",
    "#B0F906", "#950495", "#0667F9", "#1929B3",
    # ── ranks 48–95 (rows E–H): perceptually spread to the same separation
    #    (min ΔE*ab ≈ 10.8) as the ranks 0–47 block above ──────────────────
    "#7F4660", "#7F0000", "#007F43", "#33337F",
    "#EAC081", "#7F0C51", "#70ADCC", "#EAE15D",
    "#EA965D", "#A55B9B", "#7ACC33", "#7F3333",
    "#70CCA1", "#5D7F46", "#EA3A52", "#A5875B",
    "#DC81EA", "#46747F", "#CC14CC", "#9BA55B",
    "#EACB00", "#8ECC70", "#5B60A5", "#A56A5B",
    "#EA7D00", "#EA5DC5", "#00EA6D", "#CCB351",
    "#CC337A", "#CC708E", "#BBEA5D", "#A5A510",
    "#6A42A5", "#6DEA00", "#00A579", "#EA795D",
    "#21A500", "#1482CC", "#7F3B00", "#00EABB",
    "#A5425C", "#6C467F", "#42A54F", "#A43AEA",
    "#81EAA4", "#3A46EA", "#17C0EA", "#EA5D83",
)


class Colors:
    """Color tokens (DESIGN_TOKENS.md §1)."""

    # ── Surfaces (in increasing elevation) ───────────────────────────────
    titlebar        = "#0A0E15"   # --bg-titlebar  : top window chrome
    surface         = "#0B0F17"   # --bg-app       : app canvas / deepest backdrop
    rail            = "#0E131C"   # --bg-rail      : left rail + properties rail base
    panel           = "#131A24"   # --bg-panel     : cards / sub-panels
    panel_elevated  = "#1A2230"   # --bg-elevated  : default button/input fill
    hover           = "#212B3B"   # --bg-hover     : hover state for controls/rows
    active          = "#2A3548"   # --bg-active    : pressed / selected state
    well_unavailable = "#1F2937"  # fill for a batch-export plate button not in the dataset

    # ── Borders / dividers ───────────────────────────────────────────────
    border_subtle   = "#1B2331"   # --border-subtle : hairlines within a panel
    border          = "#2A3343"   # --border        : default control border
    border_strong   = "#3B475C"   # --border-strong : hover/focus ring on inputs
    # Resting border on push/tool buttons. Defaults to the same subtle hairline
    # buttons used before it was split out; a theme can make buttons read as
    # crisp "toolbar" controls (e.g. the grey theme) without also thickening
    # every panel/divider that reads ``border_subtle``.
    control_border  = "#1B2331"   # --control-border : QPushButton / QToolButton edge

    # ── Text ─────────────────────────────────────────────────────────────
    text_primary    = "#E6E9EF"   # --text-primary   : body, headings, primary labels
    text_secondary  = "#98A2B3"   # --text-secondary : labels-on-controls, metadata
    text_muted      = "#5F6B7C"   # --text-muted     : hints, inactive labels, ticks
    text_faint      = "#404B5C"   # --text-faint     : decorative separators
    #: Ink for a plot element that stands for something **undefined** — today
    #: the dashed edge of the bar plots' "could not be computed" placeholder
    #: and their disabled-well outline. It was called ``text_disabled`` and
    #: documented as "disabled control label text", which it has never been:
    #: no ``:disabled`` rule in ``qss()`` reads it, both consumers are
    #: matplotlib painters, and the name invited exactly the wrong fix —
    #: re-pointing it at the muted grey the stylesheet's ``:disabled`` rules
    #: use took the placeholder's contrast against ``plot_bg`` from 7.75:1 to
    #: 3.23:1, on the one marker whose whole job is to be noticed.
    plot_undefined_ink = "#A0AEC0"
    placeholder     = "#64748B"   # --placeholder    : empty-field placeholder text

    # ── Accent (interactive / brand) ─────────────────────────────────────
    accent          = "#6B8AFD"   # --accent
    accent_hover    = "#84A0FF"   # --accent-hover
    accent_pressed  = "#5772E6"   # darkened --accent for :pressed (not a token)
    accent_dim      = "#2C3A66"   # --accent-dim : low-emphasis selected backgrounds
    accent_fg       = "#F0F4FF"   # --accent-fg  : text/icon on accent-filled buttons
    # A second, distinct hue for "this belongs to the other of two normal
    # groups" (e.g. Merge Plates' dataset-2 wells) — never used for an anomaly
    # or a warning. Deliberately not ``accent``: several plate-map dashboards
    # already use ``accent`` to flag an anomalous well (Concatenate's
    # merged-with-no-CSV, Subset's no-rows-matched), so a well merely
    # belonging to "the other source" must not read as suspect.
    accent2         = "#B98CF7"   # --accent2 : second-group tint, never an anomaly colour

    # ── Brand cue (version pill) ─────────────────────────────────────────
    # A small, theme-swappable brand chip. The default (blue) palette keeps it a
    # transparent pill with white text; a variant theme (e.g. grey) can recolour
    # the text — see ``THEMES``.
    brand           = "transparent"  # --brand    : version-pill fill
    brand_fg        = "#FFFFFF"      # --brand-fg : version-pill text

    # ── Status ───────────────────────────────────────────────────────────
    success         = "#4ADE80"   # --success
    warn            = "#F59E0B"   # --warn
    danger          = "#F87171"   # --danger
    # A well whose numbers are whole and whose output archive is gone. Its own
    # hue because the plate's contract is that no two states share a colour:
    # `warn` would say the run has work left, `success` would say it finished,
    # and the uncoloured cell would say nobody checked. Chosen by measuring
    # against every colour the plate already paints — the first two attempts
    # (`accent2`, `threshold`) were respectively reserved for non-anomaly use
    # and indistinguishable from `warn` once painted, which the plate's own
    # render guard caught.
    stale           = "#22D3EE"   # --stale : output present, archive missing

    # ── Data-viz / trace colors (categorical, keyed to wells) ────────────
    # The 96-colour ``WELL_COLORS_TUPLE`` is the canonical palette and is
    # used as-is here so that matplotlib's ``axes.prop_cycle``
    # (configured by ``widgets/plot_card.plot_palette()``) hands out the
    # same colours every other tab uses for the matching well-rank.
    trace           = WELL_COLORS_TUPLE
    threshold       = "#F5A524"   # --threshold  : dashed line + threshold chip
    threshold_dim   = "#3A2E12"   # --threshold-dim : fill behind threshold-coloured text
    gate            = "#E24A4A"   # scatter gate outline / rubber-band selector
    err_bar         = "#4A5568"   # bar-chart error-bar color
    plot_bg         = "#131A24"   # plot card fill (== panel)
    plot_grid       = "#1F2733"   # --plot-grid  : gridlines inside the figure
    plot_spine      = "#3A4658"   # --plot-spine : axis spines

    # Plot accents with a specific job, kept here rather than inline at the
    # renderer so a palette change is one edit and a reader can see them
    # beside the rest of the plot surface.
    heatmap_selection = "#FF6B35"   # outline around selected heat-map cells
    spatial_observed  = "#4AA3FF"   # observed curve on the Spatial tab
    spatial_reference = "#FF9C4A"   # its random-reference curve
    gate_curve_a      = "#4FC3F7"   # Cell Gating optimizer, control well A
    gate_curve_b      = "#FF8A65"   # …and control well B
    hist_fill         = "#4C78A8"   # Multivariate per-feature histograms
    selection_default = "#888888"   # a saved selection with no colour of its own
    # Bar plots drawn in the "Grey" colour mode (Properties → Colours). One
    # neutral ink for every bar, for figures where the plate-rank colours are
    # decoration rather than information. A single mid grey rather than a
    # screen/publication pair because bar fill is *data* ink and follows
    # ``trace`` — ``Colors.trace`` and ``CPub.trace`` are the same tuple, so a
    # bar must not change colour when the card flips to publication mode.
    #
    # One value therefore has to read on two very different grounds, and the
    # bars are drawn at ``alpha=0.85``, so the number that matters is the
    # *composited* one. Measured (WCAG contrast of the drawn pixel against the
    # ground it sits on): 3.38:1 on the publication white ``CPub.bg``, 3.20:1
    # on the dark card fill ``plot_bg``. Both clear the 3:1 floor for
    # non-text — narrowly, because moving either way trades one ground for the
    # other, and the publication side is the one this mode mostly exists for.
    # The first choice here was a lighter #8A9099, which measures 2.61:1 on
    # white once the alpha is applied under a comment claiming it read on both.
    # ``tests/test_bar_color_mode.py`` pins the pair, so a future palette edit
    # that fails one ground fails a test rather than a reader.
    #
    # Deliberately NOT listed in any ``THEMES`` bundle. The single-token
    # argument above rests on bar fill being theme-independent the way
    # ``WELL_COLORS_TUPLE`` is; a bundle that overrode this one would restore
    # exactly the screen/publication divergence the token exists to avoid,
    # silently, since nothing downstream would notice.
    plot_mono = "#727880"

    # The Line-graph "Grey" mode's shade ramp (Properties → Colours). Several
    # greys rather than the one ``plot_mono`` the bars take, because bars are
    # already told apart by their tick labels and traces are not: collapsing
    # every line onto one ink would leave a panel of curves with nothing but a
    # legend to separate them.
    #
    # Five shades spanning ``#585858``…``#A8A8A8``, twenty levels apart. The
    # span is the point and it was arrived at by measuring, because the obvious
    # rule — "every shade clears the 3:1 non-text floor on **both** the
    # publication white and the dark card" — turns out to *cap* the range at
    # ``#686868``…``#949494``. Fitting a fifth shade inside that band takes the
    # step from 14 levels down to 11 and makes the ramp harder to read, not
    # easier: the rule was buying a guarantee at the cost of the separation it
    # was supposed to protect.
    #
    # What the measurement shows is that the two grounds fail at *opposite*
    # ends, so no shade is ever weak on both. WCAG contrast against white /
    # ``#131A24`` / the darkest bundle ground ``#0A0C10``:
    #
    #   #585858 → 7.11 / 2.46 / 2.75
    #   #6C6C6C → 5.25 / 3.33 / 3.73
    #   #808080 → 3.95 / 4.43 / 4.96
    #   #949494 → 3.03 / 5.76 / 6.45
    #   #A8A8A8 → 2.38 / 7.35 / 8.23
    #
    # So every shade clears 3:1 on at least one ground, the weakest reading
    # anywhere is 2.38:1 — for a two-point line, not text — and the ends that
    # soften are the ends the other ground renders strongest. The identity a
    # reader can rely on regardless is the *marker* and the *dash pattern*,
    # neither of which costs anything in contrast; the shade is the fast,
    # approximate cue that separates neighbouring traces at a glance.
    #
    # Evenly spaced rather than eyeballed: an uneven ramp spends its band
    # unevenly, and the pair that ends up closest is the pair a reader cannot
    # separate. ``tests/test_line_color_mode.py`` pins the floors, the
    # at-least-one-ground rule and the spacing, so a palette edit that breaks
    # any of them fails a test rather than a reader. Theme-independent for the
    # same reason ``plot_mono`` is: a trace is data ink and must not change
    # colour when the card flips to publication.
    plot_grey_ramp = (
        "#585858",
        "#6C6C6C",
        "#808080",
        "#949494",
        "#A8A8A8",
    )

    # ── Contrast inks for "always opaque against any background" use ─────
    # Widgets that paint white-on-dark or black-on-light edges should use
    # these instead of hardcoded ``#FFFFFF`` / ``#000000``. They remain
    # constant across theme switches because the highlight/shadow
    # semantics (lighter than the surface vs darker than the surface)
    # already hold regardless of palette polarity in the v2 dark design.
    ink_light       = "#FFFFFF"
    ink_dark        = "#000000"

    # ── Translucent overlays (rgba; kept as editable constants) ──────────
    inset_hilite    = "rgba(255, 255, 255, 0.30)"  # top inset on "lit" wells
    inset_shadow    = "rgba(0, 0, 0, 0.30)"        # bottom inset on "lit" wells
    focus_ring      = "rgba(107, 138, 253, 0.35)"  # --ring-focus
    drop_shadow_md  = "rgba(0, 0, 0, 0.35)"        # --sh-2 floating overlay shadow
    inner_line_dk   = "rgba(0, 0, 0, 0.40)"        # --sh-1 hairline drop
    success_glow    = "rgba(74, 222, 128, 0.12)"   # halo around the "saved" dot


class CPub:
    """Publication-preview plot surface (DESIGN_TOKENS.md §9).

    A *plot-canvas* theme, not app chrome — the surrounding panel stays dark even
    when the figure interior is white. These feed matplotlib ``rcParams``
    (``figure.facecolor`` / ``axes.facecolor`` / ``axes.edgecolor`` /
    ``xtick.color`` / ``ytick.color`` / ``text.color`` / ``grid.color``); they do
    not appear in :func:`qss`. Used for the "Publication" mode of `PlotCard` and
    as the default background for exported figures.
    """

    bg          = "#FFFFFF"   # --pub-bg        : figure facecolor (print white)
    bg_subtle   = "#F8F9FB"   # --pub-bg-subtle : axes facecolor when a slight inset reads better
    text        = "#1A1D24"   # --pub-text      : title / label text
    text_muted  = "#4C5360"   # --pub-text-muted: tick labels, secondary annotations
    text_faint  = "#999999"   # --pub-text-faint: placeholder/unavailable-content text
    grid        = "#E5E7EB"   # --pub-grid      : gridlines
    spine       = "#4C5360"   # --pub-spine     : axis spines

    # Ink levels for the chrome an exported figure draws around its content.
    # A saved figure is transparent and lands on a white page, so this side of
    # the palette runs dark-on-light — the app's own chrome colours are
    # unreadable there. Named for what each one draws rather than by shade, so
    # a caller picks a role and not a grey.
    label_group = "#111111"   # faceted group-band label (the strongest)
    label_inner = "#333333"   # inner (per-cell) axis label
    label_corner = "#666666"  # the corner label where the two axes meet
    band_face   = "#DFE5EC"   # fill behind a rotated group band
    err_bar     = "#333333"   # error bars on an exported bar/scatter figure
    well_disabled = "#404040" # a well the export deliberately greys out
    placeholder = "#9AA0A6"   # "no data here" text inside an exported cell

    # Publication-mode trace cycle. Uses the same ``WELL_COLORS_TUPLE`` as
    # ``Colors.trace`` (screen mode) so a well's colour is consistent
    # regardless of whether the plot card is in screen or publication
    # mode, and every other tab agrees with what the plot is drawing.
    # If a future iteration needs darkened variants for paper-figure
    # contrast, derive them here from the canonical tuple rather than
    # diverging by hand.
    trace       = WELL_COLORS_TUPLE


# Convenience alias retained for back-compat with callers that imported
# the older ``TRACE_PUB`` name (e.g. matplotlib rcParam loaders).
TRACE_PUB = list(CPub.trace)


class Typography:
    """Type tokens (DESIGN_TOKENS.md §2)."""

    # NOTE: Qt's QSS parser treats every name in the family list as a real
    # face. The CSS generics ``sans-serif`` / ``monospace`` / ``system-ui``
    # are NOT in Qt's font registry — using them triggers an alias-table
    # walk on every QSS evaluation and Qt logs ``qt.qpa.fonts: Populating
    # font family aliases took NN ms. Replace uses of missing font family
    # …``. The values below are placeholders; ``allwell.main`` overwrites
    # both at startup with the platform's real default UI family
    # (``QApplication.font().family()`` — e.g. "Segoe UI" on Windows,
    # ".AppleSystemUIFont" on macOS) and real fixed-pitch family
    # (``QFontDatabase.systemFont(FixedFont).family()`` — e.g. "Menlo"
    # on macOS, "Consolas" on Windows) so QSS interpolation always
    # resolves to a registered family.
    family      = "sans-serif"
    family_mono = "monospace"

    # font sizes (px) — §2.2
    caption_size = 11   # --fs-caption : section eyebrows, tick labels
    small_size   = 12   # --fs-small   : buttons, segmented text, status bar
    body_size    = 13   # --fs-body    : default body, list rows, property labels
    emph_size    = 14   # --fs-emph    : emphasized inline labels, control values
    h3_size      = 15   # --fs-h3      : panel sub-titles
    h2_size      = 17   # --fs-h2      : plot card titles, dialog titles

    # weights — §2.3 (CSS numeric values; map to QFont.Weight in widget code)
    regular  = 400
    medium   = 500
    semibold = 600
    bold     = 700

    # (size, weight) pairs per role
    title   = (h2_size,      semibold)   # plot card / dialog titles
    heading = (h3_size,      semibold)   # panel sub-titles ("Quick select")
    body    = (body_size,    regular)    # default body text, list rows
    label   = (small_size,   medium)     # labels-on-controls, button text
    caption = (caption_size, medium)     # uppercase eyebrow section labels, ticks
    mono    = (small_size,   regular)    # numeric readouts (use ``family_mono``)


class Spacing:
    """4-px linear spacing scale (DESIGN_TOKENS.md §3)."""

    xs  = 4    # --s-1 : icon ↔ label gap, tightest gutter
    sm  = 8    # --s-2 : default control gap
    md  = 12   # --s-3 : field-row gap, padding inside cards
    lg  = 16   # --s-4 : padding inside panels, gap between blocks
    xl  = 24   # --s-6 : outer gutter between major panels
    xxl = 32   # --s-8 : rare large empty-state padding


class Radii:
    """Border-radius tokens (DESIGN_TOKENS.md §4).

    Tightened to a flat, near-rectangular "instrument-panel" feel — the shared
    default for both bundled themes (``blue`` and ``grey``).
    """

    xs   = 3     # --r-xs   : file chip, logo tile, tiny inline indicators
    sm   = 3     # --r-sm   : buttons, inputs, chips — dominant radius
    md   = 4     # --r-md   : cards (plot card, plate card, panel sections)
    lg   = 6     # --r-lg   : floating overlays (toasts, drawers, dialogs)
    pill = 999   # --r-pill : status dot rings, count badges


class Metrics:
    """Control padding tokens (px) — themeable for density.

    Split out from :class:`Spacing` (a pure 4-px layout scale) so the *inside* of
    buttons/inputs can be tightened (a denser, flatter "instrument-panel" control
    feel) without disturbing the layout gutters. :func:`qss` reads these for the
    button / tool-button / input / combo paddings. These tightened values are the
    shared default for both bundled themes (``blue`` and ``grey``).
    """

    btn_pad_v    = 4   # QPushButton / QToolButton vertical padding
    btn_pad_h    = 9   # QPushButton horizontal padding
    toolbtn_pad_h = 4  # QToolButton horizontal padding (kept compact)
    input_pad_v  = 4   # QLineEdit / QSpinBox / QComboBox vertical padding
    input_pad_h  = 8   # …horizontal padding
    # Floor under a spin box's *text* area, so a short range still leaves room
    # for the number in it.
    #
    # Measured rather than chosen: Qt sizes a QSpinBox's text area to the widest
    # value the range can hold plus a fixed **6 px**, and that 6 px is the same
    # whether the range is 1–9 or 0–65535. Six pixels is under half a digit, so
    # any platform whose text renders slightly wider than the metric predicted —
    # a different font fallback, a hinting difference, a fractional-DPI scale —
    # clips the value, and on a two-digit box there is nothing left to read.
    # Groups' "Replicates per group" (range 1–12) is the one that was reported.
    #
    # A floor rather than more padding, because padding does not help: Qt lays
    # the up/down buttons out *inside* the box's right padding, so
    # ``padding-right`` widens the widget and leaves the text area exactly as it
    # was. ``min-width`` applies to the contents rect and is the only lever that
    # grows the part the number is drawn in.
    #
    # 28 px takes a one- or two-digit box from 6 px of slack to 20 / 15, and
    # costs at most 14 px of width. Boxes whose own range already asks for more
    # than this are untouched.
    spin_min_w   = 28
    # Right padding a spin box reserves for its own up/down buttons, so they are
    # laid out in space of their own rather than over the number.
    #
    # Qt puts the stepper INSIDE the box's right padding. With the app's ordinary
    # 8 px there, the 14 px buttons have 6 px more than the padding allows for,
    # and whether that comes out of the text depends on the platform style —
    # which is the shape of a bug that clips the value on one operating system
    # and not another. Reserving the buttons' own width plus the normal gutter
    # removes the question: measured here, it widens the control and leaves the
    # text area exactly as wide as it was, which is what "reserved" means.
    spin_buttons_w = 14 + 8
    # Width of a right-hand inspector dock. One number for every dock in the
    # Review pane, so the Properties sidebar and the Playground's controls do
    # not open to different widths and read as two different kinds of panel.
    dock_w       = 360
    # Thickness of a divider hairline. One pixel is the design intent
    # everywhere; the token exists so the number is stated once rather than
    # written out at each of the dozen frames and QSS pins that use it.
    hairline     = 1

    # Control geometry shared across panes. Each is one *role*, not a size on a
    # scale: two roles that happen to share a number today stay separate tokens
    # so either can move without dragging the other with it. Values are the
    # ones the call sites already carried.
    combo_min_w    = 140  # channel / property combos
    combo_min_w_sm = 120  # the narrower combos in the Cell Browser panels
    num_field_w    = 70   # a numeric entry (LUT min/max, vmin/vmax)
    num_field_w_lg = 80   # …where the value can run longer
    short_field_w  = 64   # a short control beside a longer one (steps, sort dir)
    stepper_w      = 96   # a Stepper and any label pinned to match it
    icon_btn_w     = 28   # a square glyph-only button (zoom, close, delete)
    swatch_w       = 28   # a colour swatch chip (its height varies by host)
    scroll_max_h   = 200  # a scroll area capped so it cannot eat its page
    # Everything a QCheckBox occupies beside its label text: the indicator, the
    # QSS ``spacing`` between it and the label, and Qt's own focus/frame margin.
    # A measurement, not arithmetic over the QSS tokens — those account for only
    # 26 px of it. Taken as ``sizeHint().width() - fontMetrics().horizontalAdvance(text)``
    # on a polished checkbox under this stylesheet, which reads 30–33 across the
    # fluor-row labels; rounded up so the widest of them still clears. It is a
    # floor for call sites that cannot use ``sizeHint()`` because they run before
    # the global stylesheet is applied and it under-reports there.
    checkbox_chrome_w = 34


def _icon_path(name: str) -> str:
    """Absolute forward-slash path to a bundled UI icon (dev + frozen).

    Used by :func:`qss` for native subcontrol glyphs — the dropdown chevron on
    combo boxes / menu-backed tool buttons and the spin-box up/down arrows — so
    they paint a consistent shipped SVG instead of Qt's inconsistent (and on the
    dark theme, invisible) native arrows.
    """
    import os
    import sys

    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", "icons", name).replace(os.sep, "/")


def link_row_qss(object_name: str) -> str:
    """The rules for a row in a column of secondary links, for *object_name*.

    Two places draw this row — Home's Recent datasets card and the column of
    entries an ``EmptyState`` can carry — and they must look identical, because
    on Home they *are* the same list shown in the two states of one page. They
    cannot share an object name (a button on a card is not part of an empty
    state, and the name is what a reader greps), and they cannot share a
    stylesheet host either: ``EmptyState`` sets its own, so that its placeholder
    renders the same wherever it is embedded. So they share this instead. Two
    copies of four rules is how the same control ends up looking like two
    controls after one of them is adjusted.

    A list, not a row of tiles: borderless until hovered, left-aligned so the
    names form an edge the eye can run down, full width so the whole row is the
    target rather than the text in it. A disabled row is a folder that is not
    reachable right now and stays legible — the point of showing it at all is
    that the user can see their history is intact and a drive is missing.
    """
    c, t, r, s = Colors, Typography, Radii, Spacing
    return f"""
QPushButton#{object_name} {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: {r.sm}px;
    color: {c.text_primary};
    font-size: {t.small_size}px;
    padding: {s.xs}px {s.sm}px;
    text-align: left;
}}
QPushButton#{object_name}:hover {{
    background-color: {c.hover};
    border-color: {c.border};
}}
QPushButton#{object_name}:pressed {{
    background-color: {c.panel_elevated};
}}
QPushButton#{object_name}:disabled {{
    color: {c.text_muted};
}}
"""


def qss() -> str:
    """Return the application-wide base stylesheet.

    All color values are interpolated from :class:`Colors` / :class:`Typography`
    / :class:`Radii` / :class:`Spacing`; there are no hardcoded hex literals in
    the returned string.
    """
    c, t, r, s, m = Colors, Typography, Radii, Spacing, Metrics
    _chev = _icon_path("chevron-down.svg")
    _chev_up = _icon_path("chevron-up.svg")
    _check = _icon_path("check.svg")
    _link_row_home = link_row_qss("HomeRecentTile")
    return f"""
* {{
    font-family: {t.family};
    font-size: {t.body_size}px;
    color: {c.text_primary};
    selection-background-color: {c.accent};
    selection-color: {c.accent_fg};
    outline: 0;
}}

QWidget {{
    background-color: {c.surface};
    color: {c.text_primary};
}}
QMainWindow, QDialog {{
    background-color: {c.surface};
}}
QScrollArea {{
    background-color: {c.surface};
    border: 0;
}}

/* ── Structural frames (opt-in via objectName / dynamic property) ─────── */
QFrame#Panel, QFrame[panel="true"] {{
    background-color: {c.panel};
    border: 1px solid {c.border_subtle};
    border-radius: {r.md}px;
}}
/* Home's saved-plot thumbnails. A tile is a button that looks like a card: it
   carries the figure and its title as child labels, so the chrome here is the
   frame around them and the hover state that says it can be pressed. */
QPushButton#HomeThumbTile {{
    background-color: {c.panel};
    border: 1px solid {c.border_subtle};
    border-radius: {r.md}px;
    padding: 0;
    text-align: center;
}}
QPushButton#HomeThumbTile:hover {{
    border-color: {c.accent};
}}
QPushButton#HomeThumbTile:pressed {{
    background-color: {c.hover};
}}
/* A value chip on Home -> Variables: click one to give it to the wells
   selected on the plate. Reads as a token rather than a command button — the
   plate is what the user is acting on, and a row of full-weight buttons above
   it would compete with it. Its left edge carries the value's own plate colour,
   set per chip because that is the only part that varies. */
QPushButton#VariableValueChip {{
    background-color: {c.panel_elevated};
    border: 1px solid {c.border};
    border-left: 3px solid {c.border};
    border-radius: {r.sm}px;
    color: {c.text_primary};
    font-size: {t.small_size}px;
    padding: {s.xs}px {s.sm}px;
}}
QPushButton#VariableValueChip:hover {{
    background-color: {c.hover};
    border-color: {c.border_strong};
}}
/* The Variables tab's own chrome. The header band states which variable the
   plate is showing — one rail selection decides four things and nothing else on
   the page writes the answer down. The message line under it is the tab's one
   place for "what just happened"; it is styled as an accent note rather than as
   a muted hint precisely so it is not mistaken for the standing captions
   around it, which is how the fill's confirmation used to go unread. */
QWidget#VariablesHeader {{
    background-color: {c.panel};
    border-radius: {r.sm}px;
}}
QLabel#VariablesCaption {{
    font-size: {t.caption_size}px;
    font-weight: {t.semibold};
    color: {c.text_muted};
    letter-spacing: 0.08em;
}}
QLabel#VariablesHeaderValue {{
    font-size: {t.small_size}px;
    font-weight: {t.semibold};
    color: {c.text_secondary};
}}
QLabel#VariablesMessage {{
    color: {c.accent};
    font-size: {t.small_size}px;
    padding: {s.xs}px 0px;
}}
/* What the plate currently has selected. Body weight rather than muted: it is
   the answer to the question the user is in the middle of asking, and every
   other line on this half of the page is a caption or a hint. */
QLabel#VariablesSelection {{
    color: {c.text_primary};
    font-size: {t.small_size}px;
}}
/* The four selection shortcuts. Borderless until hovered, so a row of them
   reads as links beside the sentence they act on rather than as four more
   command buttons competing with the chips below. */
QPushButton#VariablesLink {{
    background-color: transparent;
    border: 1px solid transparent;
    color: {c.accent};
    font-size: {t.small_size}px;
    padding: {s.xs}px {s.sm}px;
}}
QPushButton#VariablesLink:hover {{
    background-color: {c.hover};
    border-color: {c.border};
}}
QPushButton#VariablesLink:disabled {{
    color: {c.text_muted};
}}
/* Home's recent-dataset rows — one definition, shared with EmptyState's
   entries; see ``link_row_qss``. */
{_link_row_home}
/* A panel the app has just navigated the user to. Set for a moment after a
   jump-to-this-card action, so the eye lands on the right card in a gallery of
   near-identical ones — the arrival is otherwise silent and the reader has to
   re-find by title what the click already picked out. */
QFrame#Panel[focusflash="true"] {{
    border: 1px solid {c.accent};
}}
QFrame#Rail, QFrame[rail="true"] {{
    background-color: {c.rail};
    border: none;
}}
QFrame#Titlebar {{
    background-color: {c.titlebar};
    border-bottom: 1px solid {c.border_subtle};
}}
/* The shared control band that sits under every Review tab's name row. It is
   chrome, not content, so it takes the rail's fill rather than the canvas's and
   is separated from the content below by the same hairline every other divider
   uses. See ``widgets/control_band.py``. */
/* The rail's collapse toggle. It floats over the boundary between the rail and
   the centre pane with nothing behind it, so unlike every other icon button it
   needs its own fill and edge — without them it is a glyph hanging in mid-air
   over whichever pane happens to be underneath. */
QToolButton#RailToggle {{
    background-color: {c.panel_elevated};
    border: 1px solid {c.border};
    border-radius: {r.sm}px;
}}
QToolButton#RailToggle:hover {{
    background-color: {c.hover};
    border-color: {c.border_strong};
}}

/* The rail head: the loaded dataset's name plus its actions, spanning the rail.
   Flat and full-bleed rather than an inset button — it is the rail's heading,
   and an inset control there reads as one more item in the nav below it. */
QToolButton#DatasetButton {{
    background-color: {c.rail};
    color: {c.text_primary};
    border: none;
    border-bottom: 1px solid {c.border_subtle};
    border-radius: 0px;
    /* Extra room on the right: the rail's collapse toggle floats over this
       row's outer corner, and without the inset it sat on top of the caret. */
    padding: {s.sm}px {s.xl}px {s.sm}px {s.md}px;
    text-align: left;
    font-weight: {t.semibold};
}}
QToolButton#DatasetButton:hover {{ background-color: {c.hover}; }}
QToolButton#DatasetButton::menu-indicator {{
    subcontrol-position: right center;
    subcontrol-origin: padding;
    right: {s.xl}px;
}}

QWidget#ControlBand {{
    background-color: {c.rail};
    border-bottom: 1px solid {c.border_subtle};
}}
/* The band's flow host and its label/control groups are pure containers. The
   blanket ``QWidget`` rule above would fill each of them with the app canvas
   colour, painting a lighter rectangle over the band; naming them lets that be
   undone here without the rule also reaching the controls they contain. */
QWidget#ControlBandScope, QWidget#ControlBandGroup {{
    background-color: transparent;
}}
/* Divider hairlines. Every divider in the app funnels through this rule so a
   rule drawn in one pane can never read differently from the same rule in
   another. ``HRule`` / ``VRule`` are the Analyze pane's and the run
   dashboard's names for the same thing; ``Separator`` is the Review shell's.
   Without an explicit ``border: none`` a QFrame carrying HLine/VLine paints
   Qt's default etched line, which ignores the theme entirely and reads as a
   near-white bar on the dark surfaces. The min/max sizing pins the painted
   band to a single pixel: once the border is gone the frame has no intrinsic
   thickness of its own, and callers that set HLine/VLine without also calling
   ``setFixedHeight(1)`` would otherwise stretch to whatever the layout hands
   them. */
QFrame[hline="true"], QFrame[vline="true"], QFrame#Separator,
QFrame#HRule, QFrame#VRule {{
    background-color: {c.border_subtle};
    border: none;
}}
QFrame#HRule {{
    min-height: 1px;
    max-height: 1px;
}}
QFrame#VRule {{
    min-width: 1px;
    max-width: 1px;
}}

/* ── Labels ──────────────────────────────────────────────────────────── */
QLabel {{
    background: transparent;
    color: {c.text_primary};
}}
QLabel#Title {{
    font-size: {t.h2_size}px;
    font-weight: {t.semibold};
}}
QLabel#Heading {{
    font-size: {t.h3_size}px;
    font-weight: {t.semibold};
}}
/* Section titles from ui_helpers.build_section_header (role="section") — a
   labelled band above each divided section, e.g. on the Cell Gating / Stats
   tabs. Without this they render as plain body text and read as no header. */
QLabel[role="section"] {{
    font-size: {t.h3_size}px;
    font-weight: {t.semibold};
    color: {c.accent};
}}
QLabel#Caption, QLabel[caption="true"] {{
    font-size: {t.caption_size}px;
    font-weight: {t.medium};
    color: {c.text_muted};
}}
QLabel#Secondary, QLabel[secondary="true"] {{
    color: {c.text_secondary};
}}
QLabel#Mono, QLabel[mono="true"] {{
    font-family: {t.family_mono};
    font-size: {t.small_size}px;
}}
QLabel#Muted, QLabel[muted="true"] {{
    color: {c.text_muted};
    font-size: {t.caption_size}px;
}}
QLabel#Accent, QLabel[accent="true"] {{
    color: {c.accent};
}}
/* The ⓘ hover-for-more marker (widgets/info_icon.py). Muted so it reads as an
   affordance beside a control rather than as content of its own, and slightly
   larger than caption text so the glyph stays legible at that weight. */
QLabel#InfoHint {{
    color: {c.text_muted};
    font-size: {t.small_size}px;
}}
/* Text roles that were named at their call sites but styled nowhere. */
QLabel#MutedCaption {{
    color: {c.text_muted};
    font-size: {t.caption_size}px;
    font-weight: {t.medium};
}}
/* Section headings on the Analyze form, its remote-run dialog, the Playground
   panel and the heat-map sidebar. Sized ABOVE body text: a heading set smaller
   than the fields under it (these were caption-sized, 2px below body) reads as
   a label belonging to the row beneath rather than as the title of a group. The
   eyebrow treatment that went with the small size — muted grey, wide tracking —
   goes with it, since wide letter-spacing on primary-coloured text at this size
   reads as stretched rather than deliberate.
   Review's own section headers are a different selector (``role="section"``,
   see core/ui_helpers.build_section_header) and are unaffected. */
QLabel#SectionTitle, QLabel#SidebarHeader {{
    font-size: {t.h3_size}px;
    font-weight: {t.semibold};
    color: {c.text_primary};
}}
QLabel#PlotCardTitle {{
    font-size: {t.h3_size}px;
    font-weight: {t.semibold};
    color: {c.text_primary};
}}
/* ── The Home page (features/home/) ──────────────────────────────────────
   Home is a page of cards read at a glance rather than a form operated
   control by control, so its surfaces are panels on the app canvas and its
   type carries the hierarchy: the dataset name largest, block titles at
   heading size, tile values large enough to read across a desk, and
   everything explanatory muted. */
QScrollArea#HomeScroll, QScrollArea#HomeScroll > QWidget > QWidget {{
    background: {c.surface};
}}
QFrame#HomeCard, QWidget#HomeTile {{
    background: {c.panel};
    border: 1px solid {c.border_subtle};
    border-radius: {r.md}px;
}}
QLabel#HomeTitle {{
    font-size: {t.h2_size}px;
    font-weight: {t.semibold};
    color: {c.text_primary};
}}
QLabel#HomeCardTitle {{
    font-size: {t.h3_size}px;
    font-weight: {t.semibold};
    color: {c.text_primary};
}}
/* The one-line "what needs doing next" row. A left rule rather than a filled
   background: the severity has to be legible without the row competing with
   the plate beneath it, and a tinted panel at this width reads as an alert
   banner even when it is saying that nothing is wrong. */
QFrame#HomeNextStep {{
    background: {c.panel};
    border: 1px solid {c.border_subtle};
    border-left: 3px solid {c.border_strong};
    border-radius: {r.md}px;
}}
/* The two-line strip at the top of Home's Sample map tab: what this layer of
   the plate means, and where to go when the answer is one of the others. The
   sentence carries the page's ordinary text weight and the frame a hairline
   under it, so it reads as a statement about the tab rather than a caption on
   the control beneath it. */
QWidget#HomeRoleStrip {{
    border-bottom: 1px solid {c.border_subtle};
}}
QLabel#HomeRoleText {{
    font-size: {t.small_size}px;
    color: {c.text_secondary};
}}
QLabel#HomeTileLabel {{
    font-size: {t.caption_size}px;
    font-weight: {t.medium};
    color: {c.text_secondary};
}}
QLabel#HomeTileValue {{
    font-size: {t.h2_size}px;
    font-weight: {t.semibold};
    color: {c.text_primary};
}}
/* Channel chips and the "default" marker beside a threshold nobody chose. */
QLabel#HomeChip, QLabel#HomeChipSeg {{
    background: {c.panel_elevated};
    border: 1px solid {c.border};
    border-radius: {r.xs}px;
    padding: 1px 6px;
    font-family: {t.family_mono};
    font-size: {t.small_size}px;
    color: {c.text_primary};
}}
QLabel#HomeChipSeg {{
    border-color: {c.accent};
}}
QLabel#HomeWellChips {{
    font-family: {t.family_mono};
    font-size: {t.small_size}px;
    color: {c.text_primary};
}}
QPushButton#HomeAction {{
    background: {c.panel_elevated};
    border: 1px solid {c.border};
    border-radius: {r.sm}px;
    color: {c.text_primary};
}}
QPushButton#HomeAction:hover {{
    background: {c.hover};
    border-color: {c.border_strong};
}}
/* The notes box is styled as prose, not as a form field: no fill and no
   border at rest, so the page reads as something you look at, and a lift on
   hover that says this one region accepts typing. Adding a mode ("click to
   edit") to a page whose whole premise is that it only reports would cost
   more than the ambiguity it removes. */
QPlainTextEdit#HomeNotes {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {r.xs}px;
    color: {c.text_primary};
}}
QPlainTextEdit#HomeNotes:hover {{
    background: {c.panel_elevated};
    border-color: {c.border_subtle};
}}
QPlainTextEdit#HomeNotes:focus {{
    background: {c.panel_elevated};
    border-color: {c.border};
}}
QLabel#RailHead {{
    font-size: {t.caption_size}px;
    font-weight: {t.semibold};
    color: {c.text_muted};
    letter-spacing: 0.08em;
}}
QWidget#ActiveGroupBanner {{
    background-color: {c.panel};
    border-radius: {r.sm}px;
}}
QLabel#Danger  {{ color: {c.danger}; }}
QLabel#Success {{ color: {c.success}; }}
QLabel#Warn    {{ color: {c.warn}; }}

/* ── Buttons ─────────────────────────────────────────────────────────── */
QPushButton, QToolButton {{
    background-color: {c.panel_elevated};
    color: {c.text_primary};
    border: 1px solid {c.control_border};
    border-radius: {r.sm}px;
    padding: {m.btn_pad_v}px {m.btn_pad_h}px;
    font-size: {t.small_size}px;
    font-weight: {t.medium};
}}
QToolButton {{
    /* Match QPushButton's vertical padding so tool buttons (Crop / Export /
       Build / More / Channels) are the same height as push buttons on toolbars;
       keep horizontal padding compact. The hasMenu padding-right caret rule
       below still overrides the right side. */
    padding: {m.btn_pad_v}px {m.toolbtn_pad_h}px;
}}
QPushButton:hover, QToolButton:hover {{
    background-color: {c.hover};
    border-color: {c.border};
}}
QPushButton:pressed, QToolButton:pressed,
QPushButton:checked, QToolButton:checked {{
    background-color: {c.active};
}}
QPushButton:disabled, QToolButton:disabled {{
    color: {c.text_muted};
    background-color: {c.panel};
    border-color: {c.border_subtle};
}}

/* Dropdown-caret tool buttons (those marked ``hasMenu``). Every one of these is
   InstantPopup (the whole button opens the menu — no split default-action
   region anywhere in the app), so only ``menu-indicator`` is live; it's purely
   decorative, a fixed 10x10 chevron inset from the right edge. Reserve room on
   the right and paint one shared chevron so every menu caret is the same size. */
QToolButton[hasMenu="true"] {{
    padding-right: 20px;
}}
QToolButton[hasMenu="true"]::menu-indicator {{
    image: url({_chev});
    width: 10px;
    height: 10px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
    right: 6px;
    border: none;
    background: transparent;
}}

QPushButton#Primary {{
    background-color: {c.accent};
    border-color: {c.accent};
    color: {c.accent_fg};
    font-weight: {t.semibold};
}}
QPushButton#Primary:hover {{
    background-color: {c.accent_hover};
    border-color: {c.accent_hover};
}}
QPushButton#Primary:pressed {{
    background-color: {c.accent_pressed};
    border-color: {c.accent_pressed};
}}
QPushButton#Primary:disabled {{
    background-color: {c.accent_dim};
    border-color: {c.accent_dim};
    color: {c.text_muted};
}}

QPushButton#Danger {{
    background-color: transparent;
    border-color: {c.border_subtle};
    color: {c.danger};
}}
QPushButton#Danger:hover {{
    background-color: {c.panel};
}}

QPushButton#Ghost, QToolButton#Ghost {{
    background-color: transparent;
    border-color: transparent;
    /* Ghost buttons are icon-like (✕ close, ▲/▾ reorder). The default 10px
       horizontal button padding ate a compact fixed width and clipped the glyph,
       so keep their padding tight. */
    padding: {s.xs}px {s.xs}px;
}}
QPushButton#Ghost:hover, QToolButton#Ghost:hover {{
    background-color: {c.panel};
}}

/* ── Toggle buttons (checkable mode/option buttons) ───────────────────── */
/* e.g. Delete Cell / Crop Region (Segmentation), Beeswarm / Violin / Group by
   Well (plots), Outline / Binary. The default global :checked only darkens the
   fill a touch — too subtle to read as "armed". Give the on-state an accent
   ring + tinted fill so a mode toggle is unmistakably engaged. The
   ``toggle_active`` dynamic property is the non-pseudo-state equivalent some
   call sites set explicitly (synced via unpolish/polish). */
QPushButton[variant="toggle"]:checked,
QPushButton[variant="toggle_active"],
QToolButton[variant="toggle"]:checked {{
    background-color: {c.accent_dim};
    border: 1px solid {c.accent};
    color: {c.text_primary};
}}
QPushButton[variant="toggle"]:checked:hover,
QToolButton[variant="toggle"]:checked:hover {{
    background-color: {c.accent};
    border-color: {c.accent};
    color: {c.accent_fg};
}}

/* ── `variant` values set by ui_helpers._btn and friends ──────────────── */
/* Every value below is set somewhere in the app; before these rules existed
   only `toggle` resolved, so `primary`, `secondary`, `danger`, `muted`,
   `sem`/`sem_warn` and `toggle_muted`/`toggle_accent` rendered as plain
   default buttons. `secondary` is deliberately the default control look —
   naming it keeps the value live and stops it drifting.
   Runtime changes to the property need unpolish()/polish() to repaint;
   values set once at construction do not. */
QPushButton[variant="primary"], QToolButton[variant="primary"] {{
    background-color: {c.accent};
    border-color: {c.accent};
    color: {c.accent_fg};
    font-weight: {t.semibold};
}}
QPushButton[variant="primary"]:hover, QToolButton[variant="primary"]:hover {{
    background-color: {c.accent_hover};
    border-color: {c.accent_hover};
}}
QPushButton[variant="primary"]:pressed, QToolButton[variant="primary"]:pressed {{
    background-color: {c.accent_pressed};
    border-color: {c.accent_pressed};
}}
QPushButton[variant="primary"]:disabled, QToolButton[variant="primary"]:disabled {{
    background-color: {c.accent_dim};
    border-color: {c.accent_dim};
    color: {c.text_muted};
}}

QPushButton[variant="secondary"], QToolButton[variant="secondary"] {{
    background-color: {c.panel_elevated};
    border: 1px solid {c.control_border};
    color: {c.text_primary};
}}

QPushButton[variant="danger"], QToolButton[variant="danger"] {{
    background-color: transparent;
    border-color: {c.border_subtle};
    color: {c.danger};
}}
QPushButton[variant="danger"]:hover, QToolButton[variant="danger"]:hover {{
    background-color: {c.panel};
}}

QPushButton[variant="muted"], QToolButton[variant="muted"], QLabel[variant="muted"] {{
    background-color: transparent;
    border-color: transparent;
    color: {c.text_muted};
}}

/* SEM / SD toggle on the bar + batch-export panels: `sem` is the settled
   state, `sem_warn` flags a spread that cannot be computed as SEM. */
QPushButton[variant="sem"], QToolButton[variant="sem"] {{
    background-color: {c.panel_elevated};
    border: 1px solid {c.control_border};
    color: {c.text_primary};
}}
QPushButton[variant="sem_warn"], QToolButton[variant="sem_warn"] {{
    background-color: {c.panel_elevated};
    border: 1px solid {c.warn};
    color: {c.warn};
}}

/* Bar Plots' Reset Order: `toggle_accent` while a manual order is in force,
   `toggle_muted` when there is nothing to reset. */
QPushButton[variant="toggle_accent"], QToolButton[variant="toggle_accent"] {{
    background-color: {c.accent_dim};
    border: 1px solid {c.accent};
    color: {c.text_primary};
}}
QPushButton[variant="toggle_muted"], QToolButton[variant="toggle_muted"] {{
    background-color: transparent;
    border: 1px solid {c.control_border};
    color: {c.text_muted};
}}

/* Nested sub-tab bars (Plotting / Image Browser / Home). The call
   sites carried comments claiming this name "picks up the existing
   rectangular-tab QSS"; no such rule existed, so all of them fell through to the
   generic QTabBar::tab rule and were indistinguishable from the top level. */
QTabBar#HomeSubTabs::tab,
QTabBar#ImageBrowserSubTabs::tab,
QTabBar#PlottingSubTabs::tab {{
    padding: {s.xs}px {s.md}px;
    min-width: 72px;
    color: {c.text_secondary};
}}
QTabBar#HomeSubTabs::tab:selected,
QTabBar#ImageBrowserSubTabs::tab:selected,
QTabBar#PlottingSubTabs::tab:selected {{
    color: {c.text_primary};
    border-bottom: 2px solid {c.accent};
}}

/* ── Plate-map well buttons (WellButton grid) ─────────────────────────── */
/* Used by the picker grids that use ``WellButton`` / ``build_plate_grid``
   (Statistics / Image Table / Segmentation / Home / Bar Plots).
   Driven by ``WellButton.set_state(...)`` via the dynamic ``state`` property;
   the per-widget overrides in ``WellViewerApp._style_plate_button`` (Stats /
   bar pickers) take precedence for those grids. Keep the 18px box in sync with
   ``well_viewer.views.well_button.WELL_SIZE``. */
QPushButton#WellButton {{
    min-width: 18px; min-height: 18px; max-width: 18px; max-height: 18px;
    padding: 0;
    border-radius: 9px;
    border: 1px solid transparent;
    background-color: {c.panel_elevated};
}}
QPushButton#WellButton[state="empty"]      {{ background-color: {c.panel};         border: 1px solid transparent; }}
QPushButton#WellButton[state="available"]  {{ background-color: {c.panel_elevated}; border: 1px solid {c.border}; }}
QPushButton#WellButton[state="selected"]   {{ background-color: {c.accent};         border: 1px solid {c.accent_pressed}; }}
QPushButton#WellButton[state="active"]     {{ background-color: {c.accent};         border: 1px solid {c.accent_pressed}; }}
QPushButton#WellButton[state="rep_hidden"] {{ background-color: {c.panel};          border: 1px solid {c.border_subtle}; }}
QPushButton#WellButton[state="group"]      {{ background-color: {c.success};        border: 1px solid {c.success}; }}
QPushButton#WellButton[state="warn"]       {{ background-color: {c.warn};           border: 1px solid {c.warn}; }}
QPushButton#WellButton[state="danger"]     {{ background-color: {c.danger};         border: 1px solid {c.danger}; }}
QPushButton#WellButton:hover               {{ background-color: {c.hover}; }}
QPushButton#WellButton:disabled            {{ background-color: {c.panel};          border: 1px solid transparent; }}

/* ── Text inputs / steppers ──────────────────────────────────────────── */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {c.panel_elevated};
    color: {c.text_primary};
    border: 1px solid {c.border};
    border-radius: {r.sm}px;
    padding: {m.input_pad_v}px {m.input_pad_h}px;
    selection-background-color: {c.accent};
    selection-color: {c.accent_fg};
}}
QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover,
QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: {c.border_strong};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {c.accent};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {c.text_muted};
    background-color: {c.panel};
}}
/* See ``Metrics.spin_min_w``: Qt gives a spin box's text area the width of its
   widest value plus a fixed 6 px, whichever the range, and 6 px is under half a
   digit. */
QSpinBox, QDoubleSpinBox {{
    min-width: {m.spin_min_w}px;
    padding-right: {m.spin_buttons_w}px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    background-color: {c.panel_elevated};
    border: none;
    width: 14px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {c.hover};
}}
/* Paint the spin arrows with the shipped chevrons — Qt's native arrows are
   invisible on the dark theme (e.g. the Image Table grid-size boxes looked
   like they had no buttons even though they worked). */
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url({_chev_up});
    width: 8px;
    height: 8px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url({_chev});
    width: 8px;
    height: 8px;
}}

/* ── Combo box ───────────────────────────────────────────────────────── */
QComboBox {{
    background-color: {c.panel_elevated};
    color: {c.text_primary};
    border: 1px solid {c.border};
    border-radius: {r.sm}px;
    padding: {m.input_pad_v}px {m.input_pad_h}px;
}}
QComboBox:hover {{
    border-color: {c.border_strong};
}}
QComboBox:focus, QComboBox:on {{
    border-color: {c.accent};
}}
/* Every other input type here has a disabled rule and this one did not, so a
   combo that a tab had greyed out rendered pixel-identical to a live one —
   ``setEnabled(False)`` took effect (the popup would not open) with nothing on
   screen to say so. Setting ``color`` in the base rule is what makes the
   omission total: a stylesheet colour with no disabled variant is applied in
   every state, so the palette's disabled group never gets a look in. Matches
   the ``QLineEdit`` family, whose base rule this one mirrors token for token. */
QComboBox:disabled {{
    color: {c.text_muted};
    background-color: {c.panel};
}}
QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox::down-arrow {{
    image: url({_chev});
    width: 10px;
    height: 10px;
}}
QComboBox QAbstractItemView {{
    background-color: {c.panel};
    color: {c.text_primary};
    border: 1px solid {c.border};
    border-radius: {r.sm}px;
    selection-background-color: {c.accent_dim};
    selection-color: {c.text_primary};
    outline: 0;
}}

/* ── Check / radio ───────────────────────────────────────────────────── */
/* ``spacing`` is the indicator↔label gap; the vertical ``padding`` gives each
   row breathing room so stacked checkboxes aren't cramped and inline rows don't
   collide with neighbours. */
QCheckBox, QRadioButton {{
    background: transparent;
    color: {c.text_primary};
    spacing: {s.sm}px;
    /* Vertical only — horizontal padding would insert dead space before a
       text-less indicator (e.g. the WrapCheckBox composite), pushing its label
       away from the box. */
    padding: {s.xs}px 0;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    background-color: {c.panel_elevated};
    border: 1px solid {c.border};
    /* No margin here. ``spacing`` above is the single source of the
       indicator↔label gap; a margin on top of it stacks, so the gap became
       spacing + margin everywhere — visible as over-padded rows, and as dead
       space in front of the label of a *text-less* indicator, which is exactly
       what the ``padding`` comment above says not to do (the WrapCheckBox
       composite is built from one). It was added to compensate for a platform
       style offsetting the label short of the declared width; a fixed constant
       cannot do that, because the shortfall is per-style and per-context. */
}}
QCheckBox::indicator {{
    border-radius: {r.xs}px;
}}
QRadioButton::indicator {{
    border-radius: 8px;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {c.border_strong};
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {c.accent};
    border-color: {c.accent};
}}
/* A checkmark glyph on the accent fill — a bare filled square reads ambiguously
   (is it on or off?); the tick makes the checked state unmistakable. */
QCheckBox::indicator:checked {{
    image: url({_check});
}}
QCheckBox:disabled, QRadioButton:disabled {{
    color: {c.text_muted};
}}

/* ── Slider ──────────────────────────────────────────────────────────── */
QSlider::groove:horizontal {{
    height: 4px;
    background-color: {c.border_subtle};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background-color: {c.accent};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 14px;
    height: 14px;
    margin: -5px 0;
    background-color: {c.panel_elevated};
    border: 2px solid {c.rail};
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{
    background-color: {c.hover};
}}

/* ── Tabs ────────────────────────────────────────────────────────────── */
QTabWidget::pane {{
    background-color: {c.surface};
    border: 1px solid {c.border_subtle};
    border-radius: {r.md}px;
}}
/* The two nested page widgets show no bar of their own — theirs is mirrored
   into the mode band (app_shell_view.mirror_tab_widget) — so the pane's border
   closes a frame that is not drawn, and its top edge would start the body
   inside a pixel below where an un-nested page's body starts. */
QTabWidget#ImageBrowserSubTabs::pane,
QTabWidget#HomeSubTabs::pane {{
    border: none;
    border-radius: 0;
}}
QTabBar {{
    background: transparent;
}}
QTabBar::tab {{
    background: transparent;
    color: {c.text_secondary};
    border: none;
    padding: 8px 14px;
    font-size: {t.small_size}px;
    font-weight: {t.medium};
}}
QTabBar::tab:hover {{
    color: {c.text_primary};
}}
QTabBar::tab:selected {{
    color: {c.text_primary};
    border-bottom: 2px solid {c.accent};
}}

/* ── Menus ───────────────────────────────────────────────────────────── */
QMenu {{
    background-color: {c.panel};
    color: {c.text_primary};
    border: 1px solid {c.border};
    border-radius: {r.sm}px;
    padding: 4px;
}}
QMenu::item {{
    padding: 5px 18px;
    border-radius: {r.xs}px;
}}
QMenu::item:selected {{
    background-color: {c.accent_dim};
}}
QMenu::separator {{
    height: 1px;
    background-color: {c.border_subtle};
    margin: 4px 6px;
}}

/* ── Tooltip ─────────────────────────────────────────────────────────── */
QToolTip {{
    background-color: {c.panel};
    color: {c.text_primary};
    border: 1px solid {c.border};
    border-radius: {r.xs}px;
    padding: 4px 6px;
}}

/* ── Group box ───────────────────────────────────────────────────────── */
QGroupBox {{
    background-color: {c.panel};
    border: 1px solid {c.border_subtle};
    border-radius: {r.md}px;
    margin-top: {s.md}px;
    padding: {s.md}px;
    font-weight: {t.semibold};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: {s.md}px;
    padding: 0 4px;
    color: {c.text_secondary};
}}

/* ── Lists / trees / tables ──────────────────────────────────────────── */
QListView, QListWidget, QTreeView, QTreeWidget, QTableView, QTableWidget {{
    background-color: {c.panel};
    color: {c.text_primary};
    border: 1px solid {c.border_subtle};
    border-radius: {r.sm}px;
    outline: 0;
    selection-background-color: {c.accent_dim};
    selection-color: {c.text_primary};
}}
QListView::item, QListWidget::item, QTreeView::item, QTreeWidget::item {{
    padding: 4px 6px;
    border-radius: {r.xs}px;
}}
QListView::item:hover, QListWidget::item:hover,
QTreeView::item:hover, QTreeWidget::item:hover {{
    background-color: {c.hover};
}}
QListView::item:selected, QListWidget::item:selected,
QTreeView::item:selected, QTreeWidget::item:selected {{
    background-color: {c.accent_dim};
}}
QHeaderView::section {{
    background-color: {c.panel_elevated};
    color: {c.text_secondary};
    border: none;
    border-bottom: 1px solid {c.border_subtle};
    padding: 5px 8px;
    font-weight: {t.medium};
}}
QTableView, QTableWidget {{
    gridline-color: {c.border_subtle};
    alternate-background-color: {c.panel_elevated};
}}
QTableView::item, QTableWidget::item {{
    padding: 4px 8px;
}}

/* ── Job Queue rows ──────────────────────────────────────────────────── */
/* Selector-scoped on purpose. A bare `border-bottom` set on the row widget
   itself has no selector, so Qt applies it to every child too and each label,
   tool button and progress bar inside the row draws its own underline. */
QWidget#JobRow {{
    border-bottom: 1px solid {c.border_subtle};
}}
QWidget#JobRow:hover {{
    background-color: {c.hover};
}}
QWidget#JobQueueList {{
    background-color: {c.panel};
    border: 1px solid {c.border_subtle};
    border-radius: {r.sm}px;
}}

/* ── Context banner ──────────────────────────────────────────────────── */
/* A page borrowed for something other than its own job — see
   widgets/context_banner.py. Accent-tinted so it reads as a mode the page is
   in, not as one more row of its chrome. */
QWidget#ContextBanner {{
    background-color: {c.accent_dim};
    border: 1px solid {c.accent};
    border-radius: {r.sm}px;
}}
QWidget#ContextBanner QLabel {{
    color: {c.text_primary};
}}

/* ── The Analyze pane's folder banners ───────────────────────────────── */
/* Two strips at the head of the Analyze sidebar, reporting what the folder in
   the form already holds: the resume offer (analyze.suspend) and the pre-run
   report (analyze.preflight). Both were built with an object name and styled
   nowhere, so they had no fill, no edge and no separation from the form —
   loose page chrome that happened to have buttons in it. Contained here, and
   ranked against each other: the resume strip carries an action the user can
   take right now and takes the accent edge; the pre-run report states what is
   already true and takes the neutral one. The shared fill keeps them one
   family. Both call sites set WA_StyledBackground, without which a plain
   QWidget subclass ignores its own background rule entirely.

   `panel`, not ContextBanner's `accent_dim` fill, and the reason is measured
   rather than a preference. ContextBanner carries one sentence at
   text_primary, which reads 9.07:1 on accent_dim. These two carry three tiers
   — identity, prose, tally — and on accent_dim the muted tier measures 2.04:1
   in the blue theme and 3.16:1 in grey, so the tier whose whole job is to be
   quiet becomes the tier that cannot be read. On `panel` the same three
   measure 14.38 / 6.79 / 3.23 (blue) and 12.65 / 7.93 / 4.48 (grey). */
QWidget#ResumeBanner {{
    background-color: {c.panel};
    border: {m.hairline}px solid {c.accent};
    border-radius: {r.md}px;
}}
QWidget#PreflightBanner {{
    background-color: {c.panel};
    border: {m.hairline}px solid {c.border};
    border-radius: {r.md}px;
}}
/* The four text tiers inside them. Scoped to the banners rather than reusing
   the global QLabel[muted="true"] / [secondary="true"] roles, because those
   are colour-only and these need their own size and weight ramp — one that
   has to stay legible against `panel` rather than against the app canvas. */
QLabel#AnalyzeBannerKicker {{
    color: {c.accent};
    font-size: {t.caption_size}px;
    font-weight: {t.semibold};
}}
QLabel#AnalyzeBannerTitle {{
    color: {c.text_primary};
    font-size: {t.body_size}px;
    font-weight: {t.semibold};
}}
QLabel#AnalyzeBannerBody {{
    color: {c.text_secondary};
    font-size: {t.small_size}px;
}}
/* Ranked below the prose by size, not by colour. It is the one line in the
   strip carrying numbers the user is meant to read, and `text_muted` measures
   3.23:1 on `panel` — the right weight for a hint that restates a control's
   own label, and the wrong one for the only report of what the run did. */
QLabel#AnalyzeBannerTally {{
    color: {c.text_secondary};
    font-size: {t.caption_size}px;
}}

/* ── Splitter ────────────────────────────────────────────────────────── */
/* A styled ::handle with no width/height collapses the grab area to ~1px and
   overrides setHandleWidth — so give it an explicit size per orientation. */
QSplitter::handle {{
    background-color: {c.border_subtle};
}}
QSplitter::handle:horizontal {{
    width: 6px;
}}
QSplitter::handle:vertical {{
    height: 6px;
}}
QSplitter::handle:hover {{
    background-color: {c.border_strong};
}}

/* ── Scrollbars ──────────────────────────────────────────────────────── */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background-color: {c.border};
    border-radius: 5px;
    min-height: 24px;
    min-width: 24px;
}}
QScrollBar::handle:hover {{
    background-color: {c.border_strong};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
    background: transparent;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* ── Progress bar ────────────────────────────────────────────────────── */
QProgressBar {{
    background-color: {c.panel_elevated};
    border: 1px solid {c.border_subtle};
    border-radius: {r.sm}px;
    text-align: center;
    color: {c.text_primary};
}}
QProgressBar::chunk {{
    background-color: {c.accent};
    border-radius: {r.sm}px;
}}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Theme registry
# ─────────────────────────────────────────────────────────────────────────────
# A *theme* is a bundle of token overrides applied onto the token classes above
# (``Colors`` / ``Metrics`` / ``Radii``) before the UI is built. Because
# :func:`qss` reads those classes at call time and every ``widgets/*`` builder
# reads ``theme.Colors.*`` at construction, applying a theme up-front (see
# ``allwell.main`` → :func:`select_theme_from_env`) re-skins the whole app with
# no per-widget rewiring. Selection is a dev-facing env var — ``ALLWELL_THEME``
# — so there is no in-app live switch (which would need every cached inline QSS
# rebuilt on ``QEvent.StyleChange``; see ARCHITECTURE.md §8). ``WELL_COLORS_TUPLE``
# (categorical data identity) and ``CPub`` (white-bg publication export) are
# deliberately theme-independent.

import os as _os
import logging as _logging

_LOG = _logging.getLogger(__name__)

#: The theme the app starts in when nothing names one. ``grey`` is the
#: shipped default; ``blue`` remains available via ``ALLWELL_THEME=blue`` or
#: ``--theme blue``.
#:
#: Note what this does *not* change: the token classes above still hold blue's
#: values, because that is what makes them the restore baseline
#: :func:`apply_theme` resets to (``_THEME_DEFAULTS`` captures them at import).
#: Nothing here applies a theme on its own — the app applies one at startup via
#: :func:`select_theme_from_env`, and a caller that reads ``Colors.*`` without
#: having applied one is reading blue.
DEFAULT_THEME = "grey"

# Classes a theme bundle may override, by name.
_THEMEABLE_CLASSES = {"Colors": Colors, "Metrics": Metrics, "Radii": Radii}

# Grey: a neutral charcoal-grey chrome variant (colour-only) — restrained azure
# accent and a near-black image canvas.
# Shares the flat, tight control feel (``Radii`` / ``Metrics``) with
# the default ``blue`` theme, so this bundle overrides *only* colours. (The look
# was designed against real Nikon NIS-Elements AR screenshots; the
# ``control_border`` split lets its buttons read as crisp toolbar controls
# without thickening panel dividers.)
_GREY = {
    "Colors": {
        "titlebar": "#191B1E", "surface": "#141618", "rail": "#1F2226",
        "panel": "#24272C", "panel_elevated": "#2D3137", "hover": "#373C43",
        "active": "#3E444D",
        # Structural dividers (top-bar border, splitter handle, separators,
        # panel edges) read this. Kept a touch darker than the surfaces so the
        # region seams are subtle dark grooves rather than light lines. Control
        # borders use ``border`` / ``control_border`` and stay crisp.
        "border_subtle": "#101214", "border": "#3F444B", "border_strong": "#50565E",
        "control_border": "#3F444B",
        "text_primary": "#EAECEE", "text_secondary": "#B8BDC4",
        "text_muted": "#878D95", "text_faint": "#5E646C",
        "accent": "#2D8CEB", "accent_hover": "#43A0FF", "accent_pressed": "#1668B8",
        "accent_dim": "#22415E", "accent_fg": "#FFFFFF",
        # ``accent2`` has to move with ``accent``. It is the *neighbouring*
        # hue — the second series colour, the dial's other half — and the
        # relationship, not the absolute value, is what makes the pair read as
        # one family. Left at the default while the five tokens above were
        # overridden, the gap went from 38° to 55° and the saturations diverged
        # (206 against 110), so on every remote-run plate the two stopped
        # looking related. Derived by carrying the default theme's own hue
        # offset (+38°) and saturation/value ratios onto this accent.
        "accent2": "#6D5BE5",
        # The brand tokens (version-pill fill + text) are not overridden here:
        # the pill stays the default transparent-with-white-text in every theme.
        "success": "#3FB950", "warn": "#FFB300", "danger": "#F04747",
        "plot_bg": "#0A0C10", "plot_grid": "#1D2128", "plot_spine": "#50565E",
        "focus_ring": "rgba(45, 140, 235, 0.35)",
    },
}

# name → override bundle. ``blue`` is the *baseline* palette — no overrides,
# because the class values above are it. ``grey`` is a colour-only variant
# sharing blue's Radii/Metrics, and is what ``DEFAULT_THEME`` names, so the app
# starts grey unless told otherwise.
#
# Spelled with a literal ``"blue"`` rather than ``DEFAULT_THEME``. It read
# ``DEFAULT_THEME: {}`` while that constant was ``"blue"`` and the two happened
# to coincide; pointing the constant at ``grey`` would then have written the key
# ``grey`` twice in one literal, and the second entry wins silently — leaving a
# registry with no ``blue`` in it at all, ``apply_theme("blue")`` raising
# KeyError, and nothing to restore the baseline from.
THEMES = {
    "blue": {},
    "grey": _GREY,
}

# Capture the default value of every attribute any theme may touch, so switching
# back to ``blue`` (or between themes) restores untouched tokens.
_THEME_DEFAULTS: dict = {}
for _cname, _bundle in THEMES.items():
    for _k, _attrs in _bundle.items():
        _cls = _THEMEABLE_CLASSES[_k]
        _slot = _THEME_DEFAULTS.setdefault(_k, {})
        for _attr in _attrs:
            if _attr not in _slot:
                _slot[_attr] = getattr(_cls, _attr)

_THEME_HOOKS: list = []


def available_themes() -> tuple:
    """Names of the registered themes (``ALLWELL_THEME`` accepts any of these)."""
    return tuple(THEMES.keys())


def register_theme_hook(fn):
    """Register ``fn(name)`` to run after every :func:`apply_theme`.

    Used by ``ui/theme/styles.py`` to re-derive its ``get_color()`` palette from
    the freshly-applied ``Colors``. Idempotent; returns ``fn`` (usable as a
    decorator).
    """
    if fn not in _THEME_HOOKS:
        _THEME_HOOKS.append(fn)
    return fn


def apply_theme(name: str) -> str:
    """Apply the named theme's token overrides onto the token classes.

    Resets every themeable attribute to its captured default first, then applies
    the bundle, so calls are idempotent and switching between themes is clean.
    Fires the registered post-apply hooks. Raises ``KeyError`` for unknown names.
    """
    if name not in THEMES:
        raise KeyError(f"unknown theme {name!r}; choose from {available_themes()}")
    # 1) restore defaults for anything any theme might have changed
    for _cname, _defaults in _THEME_DEFAULTS.items():
        _cls = _THEMEABLE_CLASSES[_cname]
        for _attr, _val in _defaults.items():
            setattr(_cls, _attr, _val)
    # 2) apply this theme's overrides
    for _cname, _attrs in THEMES[name].items():
        _cls = _THEMEABLE_CLASSES[_cname]
        for _attr, _val in _attrs.items():
            setattr(_cls, _attr, _val)
    for _hook in list(_THEME_HOOKS):
        try:
            _hook(name)
        except Exception:  # a broken hook must not break theming
            _LOG.exception("theme hook %r failed", _hook)
    return name


def select_theme_from_env(default: str = DEFAULT_THEME,
                          var: str = "ALLWELL_THEME") -> str:
    """Apply the theme named by the ``ALLWELL_THEME`` env var (dev-facing).

    Falls back to ``default`` — :data:`DEFAULT_THEME`, i.e. ``grey`` — when the
    var is unset/blank or names an unknown theme (logged). Returns the applied
    theme name. Call once at startup, before building any widgets.
    """
    name = (_os.environ.get(var) or "").strip() or default
    if name not in THEMES:
        _LOG.warning("unknown %s=%r; falling back to %r (available: %s)",
                     var, name, default, ", ".join(available_themes()))
        name = default
    return apply_theme(name)
