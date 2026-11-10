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
| 9 | Batch Export bar: Histogram radio, bins spin, passed through to the renderer and title | `batch_export/bar_panel.py` | todo | |
| 10 | Nanowells compute: `HISTOGRAM` style, `density_above` 0, bins helpers | `nanowell/distribution_style_compute.py` | todo | |
| 11 | Nanowells state + controller: `hist_bins`, setter, `distribution_style_of`, `draw_view(hist_bins=)` | `nanowell/state.py`, `nanowell/controller.py` | todo | |
| 12 | Nanowells controls: Bins on the band; Smoothing usable only under Auto / Violin | `nanowell/distribution_style_view.py` | todo | |
| 13 | Nanowells renderer: figure-wide histogram, footer sentence, smoothing line gated to violins | `nanowell/renderer.py` | todo | |
| 14 | Batch Nanowells passes `hist_bins`; `draw_view` keyword guard extended | `batch_export/nanowell_panel.py`, `tests/test_beeswarm_point_spacing.py` | todo | |
| 15 | Log-spaced bins on a log value axis, decided by the same predicate the style pass uses | `core/plot_style.py` + callers | todo | |
| 16 | Distribution tab: *Histogram (per group)* beside *Violin (per group)* | `review/features/distribution/` | todo | |
| 17 | Docs: README (Bar Plots, Batch Export, Nanowells, Distribution) and ARCHITECTURE | `_Docs/Markdowns/` | todo | |
| 18 | Mutation checks: per-sample scale, integer rule, unwired spin, smoothing greying, batch keyword | — | todo | |
