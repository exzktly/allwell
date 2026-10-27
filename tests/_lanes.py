"""The UI tests: GUI layout and appearance, run locally and never in CI.

Kept free of every import beyond the standard library so that
``tests/conftest.py`` can read the roster to mark the tests ``ui_lane`` without
importing anything else first.

**Where they run.** Locally, ``scripts/test.sh`` — and so ``scripts/preflight.sh``
— runs them as part of the fast lane. CI's fast-lane job passes ``--no-ui`` and
leaves them out, so a pull request's billed run does not pay for them; the
local run before the push is their only gate. ``scripts/test.sh --ui`` runs them
alone.

**What the roster holds.** Tests whose subject is how the GUI *looks and lays
out*: widget geometry, sizing, wrapping and fit, spacing, colours, tones and
painting, stylesheets and theme tokens, phone-layout fitting, dashboards and
their legends. Reusable widgets from ``widgets/`` that carry no app knowledge
belong here as well, because only a change to the widget can break them.

**What does not.** A test that drives a control and asserts what reached the
run spec, the pipeline argv, a state slice or a file on disk is a behaviour
test, even when it builds a Qt form to get there: a change to the controller,
the service or the pipeline can break it with no UI file touched, so it must
run in CI. So is any guard that reads the whole source tree.

Adding a file here: give the reason it is a layout test rather than a behaviour
test. An entry with no reason is indistinguishable from one added to get a
file out of CI.
"""
from __future__ import annotations

#: Test files run by the local fast lane and left out of CI (``scripts/test.sh --no-ui``).
UI_LANE_FILES: dict[str, str] = {
    # ── reusable widgets (widgets/), no app knowledge ──────────────────────
    "test_a_plate_keeps_its_size.py": "plate height-for-width beside a growing sibling",
    "test_button_row.py": "a button row wraps instead of widening its host",
    "test_chip_group.py": "ChipGroup press/selection behaviour of a bare widget",
    "test_color_picker_popover.py": "the colour picker's SV square and hue strip",
    "test_color_swatch_row.py": "swatch layout, gaps and the Custom tile",
    "test_context_banner_fits.py": "a banner shows its whole sentence at any width",
    "test_control_band_threshold_placement.py": "Threshold sits last in the band at row height",
    "test_control_row_spacing.py": "control-row and checkbox geometry as numbers",
    "test_disabled_controls_look_disabled.py": "a disabled control renders differently",
    "test_drawer_edges.py": "drawer geometry on the right and bottom edges",
    "test_eventfilter_qstandarditem.py": "the drawer's event filter survives a stray item",
    "test_flow_group.py": "a label and its control stay one FlowLayout item",
    "test_flow_layout.py": "FlowLayout wrapping and vertical centring",
    "test_flow_layout_visibility.py": "FlowLayout re-lays out on show/hide",
    "test_plate_canvas_role.py": "the canvas plate role's size caps",
    "test_plate_order_editor.py": "the acquisition-ordering plate widget",
    "test_plate_progress.py": "PlateProgress headline, legend and divider layout",
    "test_plate_progress_dial.py": "a well in progress paints as a dial",
    "test_plate_selection_models.py": "the plate widget's selection models and rings",
    "test_plate_selection_rim.py": "a selected well looks selected",
    "test_plate_tooltip_guard.py": "the plate tooltip handler on an inactive window",
    "test_plot_card_header_state.py": "PlotCard header controls keep their state",
    "test_plot_card_save_button.py": "PlotCard's hover save buttons",
    "test_rail_nav_height.py": "a rail nav takes its rows' height, not the region's",
    "test_rail_nav_label_follows_selection.py": "only the selected rail entry is bright",
    "test_responsive_row.py": "a row of panels stacks before it clips",
    "test_segmented_control_tones.py": "the segmented control paints its declared tones",
    "test_selection_row_chips.py": "a saved selection's chips or placeholder",
    "test_split_view_layout.py": "Split View pane sizes and splitter handle",
    "test_stage_path_view.py": "the stage-path view's geometry borrowed from a plate",
    "test_stepper_affixes.py": "a stepper with a suffix validates its text",
    "test_stepper_focus_keeps_height.py": "focusing a stepper keeps its touch height",
    "test_widgets_support.py": "set_variant updates the style property",
    "test_wrap_checkbox.py": "a wrapping checkbox reports its height",
    "test_section_header_layout.py": "a section title keeps one line while it fits",
    # ── theme tokens and the stylesheet (ui/theme.py) ──────────────────────
    "test_theme_accent_pairing.py": "accent2 moves with accent in every theme",
    "test_theme_icon_assets.py": "every icon URL in the stylesheet exists",
    "test_theme_registry.py": "the theme registry's start and fallback themes",
    "test_theme_section_header.py": "section-header titles are styled as headers",
    "test_nanowell_theme.py": "the nanowell occupancy colour ramp",
    "test_home_plate_colors.py": "Home's plate paints one shared colour per state",
    "test_home_plate_render.py": "what Home's plate paints, sampled from pixels",
    "test_cell_viewer_outline.py": "the cell viewer paints the red outline",
    # ── page layout: widths, fit, alignment, placement ─────────────────────
    "test_analyze_banners_are_readable.py": "Analyze's banners have a rule and room",
    "test_analyze_narrow_sidebar_fit.py": "the Analyze form fits the rail width",
    "test_left_panel_width_consistency.py": "the three left panels share one width",
    "test_mode_bar_collapse.py": "the mode bar collapses with the rail",
    "test_page_alignment.py": "every page starts its first row at one height",
    "test_review_actions_placement.py": "the dataset action cluster's place on the band",
    "test_playground_dock_fits.py": "the Playground dock never squeezes its text",
    "test_spin_box_shows_its_value.py": "a spin box has room for its number",
    "test_properties_panel_frame.py": "the Properties panel's frame and label column",
    "test_dataset_equation_row.py": "the 'dataset 1 + dataset 2 = destination' row",
    "test_sidebar_visibility.py": "the rail collapse toggle drives the right sidebar",
    "test_datasets_terminology.py": "Datasets headings and dashboard titles",
    # ── progress dashboards (paint + legend) ───────────────────────────────
    "test_dashboard_legends.py": "every dashboard legend matches what it paints",
    "test_datasets_dashboard_outcomes.py": "every Datasets dashboard paints the same end states",
    "test_merge_plates_dashboard.py": "Merge Plates dashboard colours and markers",
    "test_import_loose_dashboard.py": "Import Loose dashboard stages and markers",
    "test_subset_dashboard.py": "Subset dashboard stage display",
    "test_job_queue_view.py": "the job queue page's captions, scroll area and rows",
    # ── phone layout ───────────────────────────────────────────────────────
    "test_phone_layout.py": "phone layout's rules, type tokens and rescale pass",
    "test_phone_layout_app.py": "phone layout through the real window",
    "test_phone_fit.py": "a desk-shaped form narrowed to a phone",
    "test_phone_datasets_shell.py": "phone shell fits the Datasets pages",
    "test_phone_form_swipe.py": "form pages scroll by a drag on a phone",
    "test_phone_plot_swipe.py": "a swipe on a plot scrolls its page",
    "test_phone_panel_sheet.py": "the Properties panel as a full-width sheet",
    "test_home_phone_layout.py": "Home's Overview as one column on a phone",
    "test_analyze_phone_view.py": "Analyze's form-as-page phone layout",
}
