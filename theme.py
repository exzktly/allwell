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
# This tuple is the canonical list. Three other consumers reference it:
#   * ``Colors.trace`` / ``CPub.trace`` below — what
#     ``widgets/plot_card.plot_palette()`` feeds into matplotlib's
#     ``axes.prop_cycle`` (used for screen + publication modes).
#   * ``ui.theme.styles._WELL_COLORS`` — derives a name→hex dict
#     (``WELL_COLOR_1`` … ``WELL_COLOR_96``) for QSS template substitution.
#   * ``well_viewer.plate_layout.WELL_COLORS`` — re-exports it for the
#     ``_rank_color_well`` / ``_rank_color_rset`` helpers and every plot
#     renderer that needs a per-well colour by rank.
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

    # ── Accent (interactive / brand) ─────────────────────────────────────
    accent          = "#6B8AFD"   # --accent
    accent_hover    = "#84A0FF"   # --accent-hover
    accent_pressed  = "#5772E6"   # darkened --accent for :pressed (not a token)
    accent_dim      = "#2C3A66"   # --accent-dim : low-emphasis selected backgrounds
    accent_fg       = "#F0F4FF"   # --accent-fg  : text/icon on accent-filled buttons

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

    # ── Data-viz / trace colors (categorical, keyed to wells) ────────────
    # The 96-colour ``WELL_COLORS_TUPLE`` is the canonical palette and is
    # used as-is here so that matplotlib's ``axes.prop_cycle``
    # (configured by ``widgets/plot_card.plot_palette()``) hands out the
    # same colours every other tab uses for the matching well-rank.
    trace           = WELL_COLORS_TUPLE
    threshold       = "#F5A524"   # --threshold  : dashed line + threshold chip
    plot_bg         = "#131A24"   # plot card fill (== panel)
    plot_grid       = "#1F2733"   # --plot-grid  : gridlines inside the figure
    plot_spine      = "#3A4658"   # --plot-spine : axis spines

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
    grid        = "#E5E7EB"   # --pub-grid      : gridlines
    spine       = "#4C5360"   # --pub-spine     : axis spines

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
QFrame#Rail, QFrame[rail="true"] {{
    background-color: {c.rail};
    border: none;
}}
QFrame#Titlebar {{
    background-color: {c.titlebar};
    border-bottom: 1px solid {c.border_subtle};
}}
QFrame[hline="true"], QFrame[vline="true"], QFrame#Separator {{
    background-color: {c.border_subtle};
    border: none;
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

/* Dropdown-caret tool buttons (those marked ``hasMenu``). Reserve room on the
   right and paint one shared chevron so every menu caret is the same size.
   Qt's native arrows are inconsistent — the small ``menu-indicator`` on
   instant-popup buttons (Channels / More / Crop) vs the ``menu-button`` on split
   buttons (Build table / Export) — so we override both with the same SVG glyph. */
QToolButton[hasMenu="true"] {{
    padding-right: 20px;
}}
/* Instant-popup buttons: the whole button opens the menu, so ``menu-indicator``
   is purely decorative — a fixed 10x10 chevron inset from the right edge. */
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
/* Split buttons (MenuButtonPopup — Build table / Export): ``menu-button`` is the
   *clickable* dropdown region, not just decoration. It must span the full button
   height and the whole reserved right strip, otherwise arrow clicks land on the
   default-action area and fire it instead of opening the menu. Keep it a
   transparent, full-height 20px box — the glyph is drawn by ``menu-arrow`` below. */
QToolButton[hasMenu="true"]::menu-button {{
    width: 20px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
    border: none;
    background: transparent;
}}
/* The caret glyph inside the split button's menu-button. Pin it to the same 10x10
   chevron as the instant-popup ``menu-indicator`` — otherwise Qt paints its own
   native down-arrow, which scales with the full-height menu-button and reads as a
   huge caret next to the small carets on Channels / More / Crop. */
QToolButton[hasMenu="true"]::menu-arrow {{
    image: url({_chev});
    width: 10px;
    height: 10px;
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

/* ── Plate-map well buttons (WellButton grid) ─────────────────────────── */
/* Used by the picker grids that use ``WellButton`` / ``build_plate_grid``
   (Statistics / Image Table / Segmentation / Sample Definitions / Bar Plots).
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

DEFAULT_THEME = "blue"

# Classes a theme bundle may override, by name.
_THEMEABLE_CLASSES = {"Colors": Colors, "Metrics": Metrics, "Radii": Radii}

# Grey: a neutral charcoal-grey chrome variant (colour-only) — restrained azure
# accent, near-black image canvas, and a yellow version-pill text cue via
# ``brand_fg``. Shares the flat, tight control feel (``Radii`` / ``Metrics``) with
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
        # brand (pill fill) inherits the default 'transparent'; only the text
        # colour differs, so just brand_fg is overridden.
        "brand_fg": "#FFE100",
        "success": "#3FB950", "warn": "#FFB300", "danger": "#F04747",
        "plot_bg": "#0A0C10", "plot_grid": "#1D2128", "plot_spine": "#50565E",
        "focus_ring": "rgba(45, 140, 235, 0.35)",
    },
}

# name → override bundle. ``blue`` is the built-in default (no overrides; the
# class values above are its palette). ``grey`` is a colour-only variant sharing
# blue's Radii/Metrics.
THEMES = {
    DEFAULT_THEME: {},
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

_ACTIVE_THEME = DEFAULT_THEME
_THEME_HOOKS: list = []


def available_themes() -> tuple:
    """Names of the registered themes (``ALLWELL_THEME`` accepts any of these)."""
    return tuple(THEMES.keys())


def active_theme() -> str:
    """Name of the currently-applied theme."""
    return _ACTIVE_THEME


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
    global _ACTIVE_THEME
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
    _ACTIVE_THEME = name
    for _hook in list(_THEME_HOOKS):
        try:
            _hook(name)
        except Exception:  # a broken hook must not break theming
            _LOG.exception("theme hook %r failed", _hook)
    return name


def select_theme_from_env(default: str = DEFAULT_THEME,
                          var: str = "ALLWELL_THEME") -> str:
    """Apply the theme named by the ``ALLWELL_THEME`` env var (dev-facing).

    Falls back to ``default`` when the var is unset/blank or names an unknown
    theme (logged). Returns the applied theme name. Call once at startup, before
    building any widgets.
    """
    name = (_os.environ.get(var) or "").strip() or default
    if name not in THEMES:
        _LOG.warning("unknown %s=%r; falling back to %r (available: %s)",
                     var, name, default, ", ".join(available_themes()))
        name = default
    return apply_theme(name)
