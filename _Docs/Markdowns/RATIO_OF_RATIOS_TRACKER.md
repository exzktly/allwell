# Remediation tracker — "Then relative to" (ratio of ratios) and the interaction test

Live state for one body of work (CLAUDE.md §6.1). Deleted by the pull request
that closes its last row. Counts below are derived from the rows; recount them
rather than editing a number by hand.

The question being answered: how does a mutant RNA behave relative to WT RNA
under a drug, compared with the same comparison under vehicle —
`R = (Mut_drug / WT_drug) / (Mut_veh / WT_veh)`, drawn as a bar with its error,
written to the CSV, and tested for significance (the RNA × Treatment
interaction on the log scale; on the raw scale for the subtract operator).

| # | Item | State | Where | Evidence |
|---|------|-------|-------|----------|
| 1 | `match_by_variable` (member → member) under `pair_by_variable`, existing tests untouched | done | `review/domain/variable_pairing.py` | `tests/test_variable_pairing.py::test_matching_names_the_member_not_a_reference`; the 15 pre-existing pairing tests pass unchanged |
| 2 | `contrast_t_test` (pooled within the contrast's own cells, log / raw), cross-checked against `two_way_anova` | done | `review/domain/stats_tests.py` | `tests/test_stats_tests.py::test_the_log_contrast_is_the_two_way_interaction_on_log_values` (t² = F, unbalanced n); mutations pooling over every cell and dropping the log each turn a test red |
| 3 | Pure stage-2 rules: outcome vocabulary, combine, coefficient merge by resolved wells, label of what the test is | done | `review/domain/ratio_of_ratios.py` (+ `stats_tests.bh_adjust`) | `tests/test_ratio_of_ratios.py` (ratio 4 vs difference 1.5 vs swapped 0.25; references cancel by wells); `tests/test_stats_tests.py::test_bh_matches_the_textbook_values_and_skips_tests_that_did_not_run` |
| 4 | Bar-only setting on `BarPlotState`, saved / restored / reset with the view state | done | `review/features/barplot/state.py`, `review/persistence/view_state.py` | `tests/test_view_state_bar_relative_to.py` (round trip by id, absent → off, reset) |
| 5 | Gate in the bar request; stage 2 applied after emission, before decoration; raw CSV pass and Batch Export untouched | done | `review/features/barplot/collect.py`, `_baseline_ratio.py` | `tests/test_bar_relative_to.py` (R = 1.5 on the reported design, test equals `contrast_t_test` on the four cells, declines named); mutations swapping bar and baseline, and dropping the live-pass gate, each turn a test red. Batch Export uses `collect_bar_items_for_group`, which never reaches the hook |
| 6 | Reference conditions hidden, dotted line at 1 (0 for subtract) | done | `review/features/barplot/renderer.py`, `_annotations.visible_bars` | `tests/test_bar_relative_to_figure.py`::test_only_the_compared_bar_is_drawn_and_the_references_are_a_dotted_line (one bar at 1.5, not four); removing the filter turns it red |
| 7 | p labels above bars, BH across bars × timepoints, caption naming the test and the estimate | done | `review/features/barplot/_annotations.annotate_relative_to` | `tests/test_bar_relative_to_figure.py`::test_the_tested_bar_carries_its_adjusted_p_and_the_note_names_the_test; stored brackets withheld with a note (::test_the_stored_brackets_are_withheld_and_say_why) |
| 8 | Failure notes on the figure (no / two matches, one well, value ≤ 0, vs t0 on, per-cell modes) | done | `_baseline_ratio._notice`, `renderer._decline_relative_for_cells` | `tests/test_bar_relative_to.py` (no baseline, one well, single control, vs t0) and `tests/test_bar_relative_to_figure.py`::test_a_per_cell_mode_says_it_is_not_applied |
| 9 | Axis / title suffix | done | `_baseline_ratio.relative_suffix`, `renderer.draw_grouped_bar_mode` | `tests/test_bar_relative_to_figure.py`::test_the_tested_bar_carries_its_adjusted_p_and_the_note_names_the_test; a declined rule adds none (::test_a_declined_rule_adds_no_label_and_draws_stage_one_with_its_reason) |
| 10 | CSV columns (`relative_to_baseline_*`, `interaction_*`), stage-1 columns unchanged | done | `review/features/export/service.py` (`bar_relative_fieldnames`, `attach_bar_relative_columns`) | `tests/test_bar_relative_to_csv.py` (fold_change_mean 0.75 beside relative 1.5; reference and untested rows blank with reasons; no new columns when off); writing stage 2 into `fold_change_*` turns it red |
| 11 | The control, in the Bar tab's Fold change popover (or beside it), driven by a test through the real builder | done | `review/features/barplot/ratio_baseline_view.py`, appended in `tab_view` below the shared fold-change controls | `tests/test_bar_relative_to_control.py` (real combos and checkboxes → slice + redraw; a dead value handler turns it red); `tests/test_bar_relative_to_in_the_tab.py` (slow lane: `build_bar_plots_tab` builds it inside the Fold change popover) |
| 12 | Statistics tab: two-way ANOVA Scale (Raw / Log); raw report names itself a difference of differences | todo | `review/features/stats/controller.py` | |
| 13 | ARCHITECTURE.md and README_WellViewer.md | todo | `_Docs/Markdowns/` | |

Outstanding: 2 · done: 11 · declined: 0
