"""Stylesheet rules for named pieces of the interface that used to style themselves.

A widget that sets its own style sheet reads the colour tokens once, when it is built, and keeps them: it cannot
follow a palette applied after it exists, and it is one more place a colour is decided. These rules were each such
a sheet — the status bar, the version pill, the help manual and the app log, the Properties and Playground docks,
the Cell Browser's thumbnails, the Pipeline Info tree, the focus chip, phone layout's docked sheet and busy overlay,
and three widgets that only asked for a transparent background. Each is keyed by the object name the widget is
built with, and :func:`qss` is included by ``ui.theme.qss``; ``tests/test_no_style_sheet_outside_the_theme.py``
holds the sites that remain.
"""

from __future__ import annotations

import importlib

#: The reusable widgets whose rules the application sheet carries: each module's ``qss()`` reads the tokens when
#: the sheet is built, so the widget follows whichever palette is applied. A widget of ``widgets/`` knows nothing of
#: the app; the app knowing its widgets' rules is the arrow running the right way.
WIDGET_SHEETS = (
    "widgets.card", "widgets.chip_group", "widgets.collapsible_section", "widgets.empty_state", "widgets.icon_button",
    "widgets.kbd_hint", "widgets.lut_selector", "widgets.mode_bar", "widgets.mpl_toolbar", "widgets.plot_card", "widgets.popover",
    "widgets.rail_nav", "widgets.saved_selections_list", "widgets.search_input", "widgets.segmented_control",
    "widgets.stepper", "widgets.toast", "widgets.well_plate_selector",
)


def qss(c, t, r) -> str:
    """The rules, from the token classes *c* (``Colors``), *t* (``Typography``) and *r* (``Radii``)."""
    mono = f"font-family: {t.family_mono};" if t.family_mono else ""
    widgets = "".join(importlib.import_module(name).qss() for name in WIDGET_SHEETS)
    from ui import shell_frame
    return widgets + shell_frame.qss(c) + f"""
QLabel#VersionPill {{
    background-color: {c.brand}; color: {c.brand_fg}; {mono} font-size: {t.small_size}px;
    font-weight: {t.semibold}; padding: 1px 6px; margin-left: 4px; border-radius: {r.xs}px;
}}
QTextBrowser#HelpManual {{ background: transparent; border: none; color: {c.text_primary}; }}
QTextEdit#AppLogView {{
    background-color: {c.card}; color: {c.text_secondary}; {mono} font-size: {t.small_size}px;
    border: 1px solid {c.border_subtle};
}}
#PropertyPanel {{ background-color: {c.rail}; }}
#PlaygroundDock {{ background-color: {c.rail}; border-left: 1px solid {c.border_subtle}; }}
#PlaygroundDock[stacked="true"] {{ border-left: none; border-top: 1px solid {c.border_subtle}; }}
#PlaygroundDockScroll, #PlaygroundDockScroll > QWidget {{ background-color: transparent; }}
#CellThumb {{ background: {c.rail}; border: 1px solid {c.hairline}; }}
#CellThumb:hover {{ border: 1px solid {c.field_edge}; }}
QTreeWidget#PipelineInfoTree {{
    background: {c.card}; alternate-background-color: {c.canvas}; color: {c.text_primary};
}}
QTreeWidget#PipelineInfoTree::item {{ color: {c.text_primary}; padding: 3px 6px; }}
QTreeWidget#PipelineInfoTree::item:selected {{ background: {c.selected}; color: {c.selected_fg}; }}
#FocusChip {{ background: {c.control}; border: 1px solid {c.border_subtle}; border-radius: {r.pill}px; }}
#FocusChipDot {{ color: {c.focused_cell}; font-size: {t.small_size}px; }}
#FocusChipText, #FocusChipClear {{
    color: {c.text_primary}; border: none; background: transparent; padding: 0 2px; font-size: {t.small_size}px;
}}
#FocusChipText:hover {{ text-decoration: underline; }}
#FocusChipClear {{ color: {c.text_secondary}; }}
#BrandTile, #ColorSwatchRow, #GradientStrip, #StatusDot, QSlider#StyledSlider {{ background: transparent; }}
#StagePathView {{ background-color: {c.card}; border: 1px solid {c.border_subtle}; border-radius: {r.md}px; }}
#DockedSheet {{ background-color: {c.rail}; border-right: 1px solid {c.border_subtle}; }}
#PhoneBusyOverlay {{ background-color: {c.canvas}; }}
#PhoneBusyOverlay QLabel {{ color: {c.text_primary}; font-size: {t.h2_size}px; }}
QLabel#ActiveGroupValue {{ color: {c.text_secondary}; font-size: {t.small_size}px; font-weight: {t.semibold}; }}
QLabel#ActiveGroupValue[active="true"] {{ color: {c.accent}; font-weight: {t.bold}; }}
"""
