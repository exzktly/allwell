# Properties per plot — remediation tracker

A live tracker (CLAUDE.md §6.1), deleted by the PR that closes its last row.

Request: "Everywhere in the app, changes made to the Properties bar should affect only the plot for which it is
edited." Before this work only the axis keys (limits, log scales, per-axis buckets) and the mark keys (line and
marker styling) were stored per figure; typography, grid, legend, ticks, layout, the export profile and format were
one app-wide dict, so an edit on one plot restyled every plot. The Nanowells page's five views also share one
figure, so even its per-figure settings crossed between views.

States: `open`, `done` (with the test that pins it), `declined` (with the reason).

| # | Item | State | Evidence / reason |
|---|------|-------|-------------------|
| P1 | `plot_style._FigurePrefs`: every pref is stored per figure; an unset key reads the value saved app-wide before this change (legacy) and then the default; no write reaches the app-wide dict | done | `test_properties_per_plot.py::test_a_font_size_set_in_one_plot_s_properties_leaves_another_plot_as_it_was`, `test_figure_scoped_style_prefs.py` (failed on main: `assert 24 == 18`); mutation (style keys back to the app-wide dict) fails 4 tests |
| P2 | A figure that shows several views (Nanowells) keys its prefs per view (`fig._aw_pref_scope`), the open Properties panel follows the view switch | done | `test_properties_per_plot.py::test_a_figure_s_views_each_keep_their_own_settings_and_the_open_panel_follows_the_view`, `::test_a_range_typed_on_the_nanowell_distribution_does_not_reach_the_trajectories` (real page); removing the `set_pref_scope` call, the scope suffix or the panel resync each fails one |
| P3 | Scatter (per-cell and faceted) reads density, log and marker size from its own figure, not the app-wide dict | done | `test_app_wide_style_is_read_only.py` (proven on main, naming `scatter/controller.py:159`, `scatter/_layout.py:120`, `batch_export/scatter_panel.py:744`); `test_legend_font_size_reaches_every_legend.py` asserts the value lands in `axis_prefs['scatter_agg']` |
| P4 | Batch Export styles each figure with its source tab's prefs; its profile and format stay in the panel instead of being written app-wide | done | `test_properties_per_plot.py::test_batch_export_draws_with_its_source_plot_s_style_not_another_plot_s`, `::test_a_batch_style_preset_styles_the_batch_figures_and_no_plot`; mutations (preset written app-wide; style from the app-wide dict) each fail one |
| P5 | The Properties export profile (e.g. Helvetica 22) restyles only the plot it is chosen on | done | `test_properties_per_plot.py::test_a_profile_chosen_in_one_plot_s_properties_restyles_that_plot_only` (through the real combo) |
| P6 | Tests that pinned the shared behaviour rewritten to the request; a guard that no module but persistence writes the app-wide dict | done | `test_app_wide_style_is_read_only.py` (proven by `scripts/prove_guard.sh`: 8 sites on main, the 8 converted) + `test_guards_bite.py::test_app_wide_style_guard_*`; shared-behaviour tests rewritten in `test_figure_scoped_style_prefs.py`, `test_export_axis_prefs_per_figure.py`, `test_legend_font_size_reaches_every_legend.py` |
| P7 | ARCHITECTURE.md and README_WellViewer.md describe per-plot Properties | done | ARCHITECTURE §5.8, Batch Export limits paragraph, §12.6, glossary; README Properties bullet |
