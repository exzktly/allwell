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
| 6 | Reference conditions hidden, dotted line at 1 (0 for subtract) | todo | `review/features/barplot/renderer.py` | |
| 7 | p labels above bars, BH across bars × timepoints, caption naming the test and the estimate | todo | `review/features/barplot/_annotations.py` | |
| 8 | Failure notes on the figure (no / two matches, one well, value ≤ 0, vs t0 on, per-cell modes) | todo | `review/features/barplot/_annotations.py` | |
| 9 | Axis / title suffix | todo | `review/features/barplot/renderer.py` | |
| 10 | CSV columns (`relative_to_baseline_*`, `interaction_*`), stage-1 columns unchanged | todo | `review/features/export/service.py` | |
| 11 | The control, in the Bar tab's Fold change popover (or beside it), driven by a test through the real builder | todo | `review/features/barplot/ratio_baseline_view.py` | |
| 12 | Statistics tab: two-way ANOVA Scale (Raw / Log); raw report names itself a difference of differences | todo | `review/features/stats/controller.py` | |
| 13 | ARCHITECTURE.md and README_WellViewer.md | todo | `_Docs/Markdowns/` | |

Outstanding: 8 · done: 5 · declined: 0
