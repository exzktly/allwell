"""The rosters of the design-language guards: per file, how many sites of each rule's breach existed when the
guard was written. Each only shrinks (``tests/_design_scan.py``); the rows that empty them are in
``_Docs/Markdowns/DESIGN_LANGUAGE_TRACKER.md`` (D10 text roles, D11 buttons, D12 spacing and style sheets, D13
wording). Generated once from the tree and edited down by hand from then on — never regenerated upward.
"""

from __future__ import annotations

TEXT_ROLE: dict[str, int] = {
    # The active well row set in bold by a per-row sheet: the selected-item weight, restyled with the
    # Datasets pages (D24).
    "datasets/concatenate/dashboard.py": 1,
    # Group rows of the parameter tree set bold: tree items, which the text roles do not reach, in a tree that
    # shows the whole raw pipeline_info.json (D27 kept it a tree).
    "review/features/home/pipeline_info/tab_view.py": 3,
    # The active stage row set bold by a per-row sheet: the selected-item weight, restyled with the Run card
    # (D22).
    "widgets/run_dashboard.py": 1,
}
BUTTON_CONSTRUCTOR: dict[str, int] = {
}
STYLE_SHEET: dict[str, int] = {
    # A menu button's own menu-indicator hide (CLAUDE.md §7.12) and each map entry's swatch in its data colour
    "analyze/bg_map.py": 2,
    # The Advanced disclosure's flat look; the dialog is restyled in D28
    "analyze/remote_dialog.py": 1,
    # Each phase row's dot in its state colour (data), and the active row's selected weight (D24)
    "datasets/concatenate/dashboard.py": 2,
    # The Plotting band's chip-styled Channel, Property and Group-by combos: the band is rebuilt in D14
    "review/core/centre_view.py": 3,
    # Menu buttons' own menu-indicator hides (CLAUDE.md §7.12)
    "review/core/ui_helpers.py": 2,
    # Each export group's dot, name tag, well chips, solo-well tags and selected border in the group's own colour (data)
    "review/features/batch_export/base_panel.py": 5,
    # The plate legend's state swatches and markers and each health row's severity dot, in their state colours (data)
    "review/features/home/controller.py": 3,
    # Home › Labels' own sheet: a pane's page, which ui/ may not import to include its rules
    "review/features/home/labels/tab_view.py": 1,
    # A channel button filled with the channel's own colour (data)
    "review/features/image_browser/cell_browser/tab_view.py": 1,
    # Channel and colour-map swatches in their data colours, and the builder shelf's own scoped sheet: the Image Table is D18
    "review/features/image_browser/image_table/builder_view.py": 4,
    # The figure's facet labels at the point sizes the figure plan computes: figure layout, not chrome
    "review/features/image_browser/image_table/facet_view.py": 4,
    # A channel button filled with the channel's own colour (data)
    "review/features/image_browser/review_image/renderer.py": 1,
    # A comparison group's dot in its group colour (data)
    "review/features/stats/controller.py": 1,
    # Phone layout's rescale pass rewriting a widget's own font sizes in place (ARCHITECTURE §8, The UI scale)
    "ui/phone_shell.py": 1,
    # install_qss_refresh, the opt-in helper that re-applies a widget's own sheet on a style change
    "widgets/_support.py": 1,
    # The demo's swatch in a well colour (data), under __main__
    "widgets/collapsible_section.py": 1,
    # A picker swatch in the colour it offers (data)
    "widgets/color_picker_popover.py": 1,
    # Restoring a combo's own chip sheet when the phone form ends: goes with the Plotting band in D14
    "widgets/control_band_form.py": 1,
    # The drawer's edge rule depends on which side it opens from (per instance)
    "widgets/drawer.py": 1,
    # The toolbar's coordinate readout label: restyled with the plot pages (D14)
    "widgets/mpl_toolbar.py": 1,
    # The badge's fill in the tone it is showing (per instance)
    "widgets/notice_badge.py": 1,
    # Plate dots and legend swatches in their state colours (data), and the legend's small size on a phone
    "widgets/plate_progress.py": 4,
    # The module demo's rail host, under __main__
    "widgets/rail_nav.py": 1,
    # The active stage row's selected weight (D22)
    "widgets/run_dashboard.py": 1,
    # A saved selection's dot in its own colour (data)
    "widgets/saved_selections_list.py": 1,
    # The toast's own sheet appended to the app's when it is shown over another window
    "widgets/toast.py": 1,
    # Resets the app's checkbox indicator for the switch it paints itself (scoped to itself)
    "widgets/toggle_switch.py": 1,
    # Resets the app's checkbox spacing for the wrapping label it paints itself (scoped to itself)
    "widgets/wrap_checkbox.py": 1,
    # Resets the app's radio spacing for the wrapping label it paints itself (scoped to itself)
    "widgets/wrap_radio.py": 1,
}
SPACING: dict[str, int] = {
    # -1 is Qt's "use the style's spacing" sentinel, restored when phone layout ends: not a gap.
    "review/features/home/groups/tab_view.py": 1,
    # The margin round the Image Table's tiles and their gap read from the user's settings: the figure's own
    # layout, not chrome.
    "review/features/image_browser/image_table/controller.py": 2,
    # Phone layout's touch points (self.pt(n)) and Qt's -1 default sentinel: sizes in phone points, scaled by
    # the touch unit, which the Spacing scale does not express.
    "ui/phone_shell.py": 5,
    # The mode switch's inner gap: the switch becomes the top bar's pill in D20.
    "widgets/mode_bar.py": 1,
}
SENTENCE_CASE: dict[str, int] = {
}
STATE_IN_LABEL: dict[str, int] = {
}
UNSET: dict[str, int] = {
}
