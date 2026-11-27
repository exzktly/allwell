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
    # Bold row labels with colons in the Output and Plot forms: the page becomes form grids in D26.
    "review/features/batch_export/base_panel.py": 7,
    # The same, on the scatter page of Batch Export (D26).
    "review/features/batch_export/scatter_panel.py": 1,
    # Group rows of the parameter tree set bold: tree items, which the text roles do not reach; the page
    # becomes label/value rows in D27.
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
    # The rail's active-group value, accent while a group is active: the rail is D21
    "review/core/selection_controller.py": 2,
    # Menu buttons' own menu-indicator hides (CLAUDE.md §7.12)
    "review/core/ui_helpers.py": 2,
    # Group cards and chips in their group colours (data), the progress line's outcome colour and a menu-indicator hide: the page is rebuilt in D26
    "review/features/batch_export/base_panel.py": 9,
    # Severity colours of Home's step and QC lines and the plate legend's state swatches (data): Home is D27
    "review/features/home/controller.py": 5,
    # Sample Map's active-group value: Home is D27
    "review/features/home/groups/tab_view.py": 1,
    # Home › Labels' own sheet: a pane's page, which ui/ may not import to include its rules (D27)
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
    # The mode switch's per-segment fill and label: the switch becomes the top bar's pill in D20
    "widgets/mode_bar.py": 3,
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
    # The chip's look follows its accent or muted variant (per instance): the rail count chip becomes plain text in D21
    "widgets/selection_chip.py": 2,
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
    "analyze/channel_rows.py": 1,
    "analyze/log_pane.py": 1,
    "analyze/nanowell_preview.py": 1,
    "analyze/nanowell_section.py": 1,
    "analyze/qc_section.py": 1,
    "analyze/remote_dialog.py": 8,
    "analyze/run_spec.py": 3,
    "analyze/suspend.py": 1,
    "datasets/concatenate/view.py": 4,
    "datasets/core/equation_row.py": 1,
    "datasets/czi_import/view.py": 4,
    "datasets/import_loose/view.py": 4,
    "datasets/job_queue/recipes_view.py": 2,
    "datasets/job_queue/view.py": 10,
    "datasets/merge_plates/view.py": 6,
    "datasets/nd2_import/view.py": 4,
    "datasets/ome_import/view.py": 15,
    "datasets/subset/view.py": 2,
    "datasets/validate/view.py": 4,
    "datasets/watch_folder/view.py": 5,
    "review/core/app_shell_view.py": 1,
    "review/core/fold_change_variable_pairing_view.py": 3,
    "review/core/ui_helpers.py": 1,
    "review/features/barplot/controller.py": 4,
    "review/features/barplot/ratio_baseline_view.py": 1,
    "review/features/barplot/tab_view.py": 5,
    "review/features/batch_export/bar_panel.py": 4,
    "review/features/batch_export/base_panel.py": 13,
    "review/features/batch_export/response_panel.py": 2,
    "review/features/batch_export/scatter_panel.py": 3,
    "review/features/batch_export/variable_select_panel.py": 1,
    "review/features/export/properties_view.py": 1,
    "review/features/heatmap/layout_view.py": 1,
    "review/features/heatmap/tab_view.py": 5,
    "review/features/home/cell_gating/controller.py": 1,
    "review/features/home/cell_gating/fov_view.py": 1,
    "review/features/home/cell_gating/saved_cells_view.py": 1,
    "review/features/home/cell_gating/tab_view.py": 1,
    "review/features/home/controller.py": 1,
    "review/features/home/groups/controller.py": 2,
    "review/features/home/groups/tab_view.py": 2,
    "review/features/home/labels/tab_view.py": 3,
    "review/features/home/ratios/tab_view.py": 1,
    "review/features/home/triage/tab_view.py": 1,
    "review/features/home/variables/editor_view.py": 1,
    "review/features/image_browser/cell_browser/criteria_view.py": 2,
    "review/features/image_browser/cell_browser/sort_view.py": 1,
    "review/features/image_browser/cell_browser/tab_view.py": 1,
    "review/features/image_browser/image_table/builder_view.py": 1,
    "review/features/image_browser/image_table/picker_view.py": 1,
    "review/features/image_browser/playground/tab_view.py": 3,
    "review/features/image_browser/review_image/controller.py": 1,
    "review/features/image_browser/review_image/renderer.py": 6,
    "review/features/lineplot/tab_view.py": 1,
    "review/features/multivariate/feature_picker_view.py": 1,
    "review/features/nanowell/subset_view.py": 2,
    "review/features/nanowell/window_view.py": 1,
    "review/features/preview/panel_view.py": 3,
    "review/features/response/tab_view.py": 1,
    "review/features/saved_cells/tab_view.py": 3,
    "review/features/scatter/cell_viewer_view.py": 5,
    "review/features/scatter/cells_tab_view.py": 8,
    "review/features/smfish/tab_view.py": 7,
    "review/features/stats/controller.py": 1,
    "review/features/stats/tab_view.py": 3,
    "review/persistence/view_state.py": 4,
    "ui/phone_shell.py": 3,
    "widgets/chip_group.py": 2,
    "widgets/drawer.py": 2,
    "widgets/gradient_strip.py": 1,
    "widgets/icon_button.py": 2,
    "widgets/kbd_hint.py": 3,
    "widgets/plot_card.py": 3,
    "widgets/saved_selections_list.py": 2,
    "widgets/selection_chip.py": 2,
}
STATE_IN_LABEL: dict[str, int] = {
    "review/core/cell_lasso_view.py": 1,
    "review/core/group_by_controller.py": 1,
    "review/data/crop_tool.py": 1,
    "review/features/barplot/controller.py": 3,
    "review/features/barplot/renderer.py": 1,
    "review/features/barplot/tab_view.py": 2,
    "review/features/heatmap/layout_view.py": 1,
    "review/features/image_browser/cell_browser/sort_view.py": 1,
    "review/features/image_browser/review_image/renderer.py": 6,
    "review/features/nanowell/anchor_queue_view.py": 1,
    "review/features/preview/panel_view.py": 5,
    "review/features/scatter/cells_tab_view.py": 6,
    "review/persistence/view_state.py": 4,
    "ui/phone_shell.py": 1,
}
UNSET: dict[str, int] = {
    "allwell.py": 1,
    "allwell_report.py": 1,
    "analyze/bg_map.py": 2,
    "analyze/domain/input.py": 3,
    "analyze/form.py": 2,
    "analyze/log_pane.py": 2,
    "analyze/progress.py": 1,
    "analyze/qc_section.py": 1,
    "analyze/remote_storage_dialog.py": 5,
    "analyze/run_spec.py": 1,
    "analyze/well_scope.py": 1,
    "datasets/job_queue/controller.py": 2,
    "datasets/job_queue/recipe_actions.py": 1,
    "datasets/job_queue/recipes_controller.py": 2,
    "datasets/job_queue/row.py": 2,
    "datasets/nd2_import/view.py": 1,
    "datasets/tab.py": 1,
    "pipeline/nanowell_identity.py": 1,
    "review/core/cell_sets.py": 1,
    "review/core/centre_view.py": 1,
    "review/core/channel_state_controller.py": 17,
    "review/core/fold_change_controls_view.py": 1,
    "review/core/fold_change_pairing_view.py": 2,
    "review/core/plot_style.py": 1,
    "review/core/ui_helpers.py": 4,
    "review/data/metric_labels.py": 2,
    "review/domain/size_classes.py": 2,
    "review/domain/variable_pairing.py": 1,
    "review/features/barplot/controller.py": 3,
    "review/features/barplot/ratio_baseline_view.py": 1,
    "review/features/barplot/renderer.py": 10,
    "review/features/barplot/tab_view.py": 1,
    "review/features/batch_export/bar_panel.py": 1,
    "review/features/batch_export/base_panel.py": 2,
    "review/features/batch_export/nanowell_panel.py": 2,
    "review/features/batch_export/response_panel.py": 1,
    "review/features/batch_export/variable_select_panel.py": 2,
    "review/features/distribution/cell_scope_compute.py": 1,
    "review/features/distribution/controller.py": 1,
    "review/features/distribution/tab_view.py": 2,
    "review/features/export/properties_view.py": 4,
    "review/features/export/service.py": 1,
    "review/features/heatmap/controller.py": 2,
    "review/features/heatmap/tab_view.py": 2,
    "review/features/home/cell_gating/controller.py": 4,
    "review/features/home/collect.py": 6,
    # Group rows of the parameter tree set bold: tree items, which the text roles do not reach; the page
    # becomes label/value rows in D27.
    "review/features/home/pipeline_info/tab_view.py": 3,
    "review/features/home/tab_view.py": 1,
    "review/features/home/triage/tab_view.py": 1,
    "review/features/home/variables/controller.py": 1,
    "review/features/image_browser/cell_browser/controller.py": 1,
    "review/features/image_browser/image_table/_colour_key.py": 1,
    "review/features/image_browser/image_table/builder_view.py": 1,
    "review/features/image_browser/image_table/export.py": 1,
    "review/features/image_browser/image_table/grid_ops.py": 1,
    "review/features/image_browser/image_table/tab_view.py": 1,
    "review/features/image_browser/playground/controller.py": 8,
    "review/features/image_browser/playground/nanowell_compute.py": 1,
    "review/features/image_browser/review_image/renderer.py": 8,
    "review/features/multivariate/renderer.py": 1,
    "review/features/nanowell/_composite_render.py": 1,
    "review/features/nanowell/_identity_diagnostics_layout.py": 2,
    "review/features/nanowell/array_compute.py": 2,
    "review/features/nanowell/array_hover_compute.py": 1,
    "review/features/nanowell/collect.py": 1,
    "review/features/nanowell/controller.py": 1,
    "review/features/nanowell/distribution_compute.py": 1,
    "review/features/nanowell/extremes_compute.py": 1,
    "review/features/nanowell/identity_compute.py": 4,
    "review/features/nanowell/moving_compute.py": 2,
    "review/features/nanowell/qc_compute.py": 3,
    "review/features/nanowell/rare_export_compute.py": 5,
    "review/features/nanowell/renderer.py": 1,
    "review/features/nanowell/statistics_compute.py": 1,
    "review/features/nanowell/tab_view.py": 1,
    "review/features/nanowell/window_compute.py": 1,
    "review/features/preview/panel_view.py": 2,
    "review/features/qc/size_class_view.py": 1,
    "review/features/qc/tab_view.py": 3,
    "review/features/response/controller.py": 1,
    "review/features/saved_cells/tab_view.py": 2,
    "review/features/saved_plots/_deck.py": 1,
    "review/features/scatter/_cell_rings.py": 1,
    "review/features/scatter/_observations.py": 1,
    "review/features/stats/_report.py": 5,
    "review/features/stats/controller.py": 6,
    "review/features/stats/tab_view.py": 2,
    "scripts/change_scope.py": 1,
    "services/job_kinds.py": 1,
    "services/recipe_journal.py": 3,
    "services/remote_fetch.py": 2,
    "services/remote_hold.py": 1,
    "services/remote_transport.py": 1,
    "services/run_detect.py": 1,
    "services/suspend_record.py": 1,
    "ui/local_storage_view.py": 1,
    "util/schema_infer.py": 1,
    "widgets/control_band_form.py": 1,
    "widgets/path_field.py": 1,
    "widgets/plate_progress.py": 1,
    "widgets/run_dashboard.py": 5,
}
