# Histogram per-cell mode — remediation tracker

Live state for adding a vertical-histogram drawing wherever a beeswarm or violin is offered (CLAUDE.md §6.1). Each
row is updated in the commit that closes it; this file is deleted by the PR that closes its last row.

Decisions (from the user): mirrored histograms only; each sample's bars sum to probability 1; one bin grid and one
width scale per figure; Bins is user-set (2–200, default 30); integer metrics bin on whole numbers; log-spaced bins
on a log value axis; Bar Plots gets one **Show** selector (Bars / Beeswarm / Violin / Histogram) in its Display
popover in place of the Beeswarm and Violin toggles; the Nanowells page's Auto keeps violins; the Distribution tab
gains *Histogram (per group)*; Spatial is out of scope (its violins are views, not a choice beside others).

States: `todo`, `doing`, `done` (with the test that pins it), `declined` (with the reason).

| # | Item | Place | State | Evidence |
|---|------|-------|-------|----------|
| 1 | Shared Qt-free core: shared edges (continuous / integer / log), per-sample probabilities, shared width scale, note, edge cases | `review/domain/column_histogram.py` | done | `tests/test_column_histogram.py` (fixture where per-sample bins, per-sample scale and counts each give other numbers) |
| 2 | Bar state: `histogram`, `hist_bins`, one `per_cell_mode` predicate | `barplot/state.py`, `barplot/_per_cell_mode.py` | done | `tests/test_bar_histogram.py::test_every_per_cell_site_names_the_histogram` |
| 3 | Bar controller: one setter, mutually exclusive modes, controls resynced from state on every redraw | `barplot/controller.py`, `barplot/_per_cell_mode.py` | done | `tests/test_bar_histogram_in_the_tab.py::test_a_restored_view_puts_the_selector_on_its_mode`; `test_bar_state_migration_a8.py` (full-app) |
| 4 | Bar Display popover: Show selector replaces the Beeswarm/Violin toggles; Bins spin box; Smooth greyed unless Violin | `barplot/tab_view.py` | done | `tests/test_bar_histogram_in_the_tab.py::test_show_histogram_draws_it_and_hands_bins_the_control` (mutations: spin unwired, Smooth greyed only under Beeswarm — both red) |
| 5 | Bar renderer: `draw_histogram`, sharing the violin's column pass | `barplot/renderer.py` | done | `tests/test_bar_histogram.py` (exact bar geometry; per-sample scale mutation red), `test_bar_cells_draw_a_stated_sample.py` (one pool at a time) |
| 6 | Bar predicate sweep: mode name, class-ratio decline, `use_per_cell`, debug log, dispatch, cell-scope note | `barplot/renderer.py`, `barplot/cell_scope_compute.py` | done | `test_bar_significance_says_why.py` (histogram case), `test_bar_histogram.py::test_every_per_cell_site_names_the_histogram` |
| 7 | Bar sampled-note wording covers histogram bars | `barplot/_pooled.py` | declined | The note already says a column over the cap *draws a sample*, which is what its histogram bars are binned from — the same statement it makes of the violin's KDE; adding a clause would repeat it. |
| 8 | Bar persistence: snapshot / apply / reset of `histogram`, `hist_bins`; modes set together | `persistence/view_state.py` | done | `tests/test_view_state_bar_histogram.py` |
| 9 | Batch Export bar: Histogram radio, bins spin, passed through to the renderer and title | `batch_export/bar_panel.py` | done | `tests/test_batch_export_bar_histogram.py` (real radio + spin, the figure's note, every render call passes `hist_bins`) |
| 10 | Nanowells compute: `HISTOGRAM` style, `density_above` 0, bins helpers | `nanowell/distribution_style_compute.py` | done | `test_nanowell_distribution_style.py::test_histogram_draws_no_dots_and_only_it_takes_bins`, `::test_the_window_counts_members_in_violins_by_the_style_drawn` |
| 11 | Nanowells state + controller: `hist_bins`, setter, `distribution_style_of`, `draw_view(hist_bins=)` | `nanowell/state.py`, `nanowell/controller.py` | done | `::test_the_page_keeps_its_bin_count_with_its_settings`, `::test_batch_export_reads_the_page_s_show_and_smoothing` |
| 12 | Nanowells controls: Bins on the band; Smoothing usable only under Auto / Violin | `nanowell/distribution_style_view.py` | done | `::test_the_show_control_and_the_smoothing_slider_redraw_the_page` (mutation: Smoothing greyed only under Beeswarm — red) |
| 13 | Nanowells renderer: figure-wide histogram, footer sentence, smoothing line gated to violins | `nanowell/renderer.py` | done | `::test_histogram_bins_counts_on_whole_numbers_with_one_width_scale` (exact widths; integer and narrowest-slot mutations red), `::test_every_panel_shares_the_histogram_s_bins`, `::test_histogram_says_nothing_of_smoothing` |
| 14 | Batch Nanowells passes `hist_bins`; `draw_view` keyword guard extended | `batch_export/nanowell_panel.py`, `tests/test_beeswarm_point_spacing.py` | done | guard red with `hist_bins=` removed from the batch call |
| 15 | Log-spaced bins on a log value axis, decided by the same predicate the style pass uses | `core/plot_style.py` + callers | done | `test_column_histogram.py::test_the_log_predicate_is_the_style_pass_s_own`, `test_bar_histogram_in_the_tab.py::test_a_log_value_axis_gets_log_spaced_bins`, the Nanowells page test (both red with the log argument dropped) |
| 16 | Distribution tab: *Histogram (per group)* beside *Violin (per group)* | `review/features/distribution/` | done | `tests/test_distribution_histogram_per_group.py` (exact geometry; sampled groups counted over every value and no threshold line — both mutations red; the tab's real Mode combo) |
| 17 | Docs: README (Bar Plots, Batch Export, Nanowells, Distribution) and ARCHITECTURE | `_Docs/Markdowns/` | done | README: Bar Plots (Show, Histogram), the sampled-columns bullet, Bar colours, Batch export's Bar mode, Nanowells Show/Bins/Smoothing, Distribution modes; ARCHITECTURE: `domain/column_histogram.py`, `barplot/_per_cell_mode.py`, `_pooled.py`, the colour-mode paths, the Distribution paragraph, `plot_style` row, both trees. Doc tests green. |
| 18 | Mutation checks: per-sample scale, integer rule, unwired spin, smoothing greying, batch keyword | — | done | Each mutation run against HEAD and seen red: per-sample width scale, integer rule (Bar and Nanowells), unwired Bins box, Smooth/Smoothing greying, `hist_bins` and `hist_log` dropped from callers, narrowest-slot half-width, sampled Distribution groups, threshold line on per-group columns. |
