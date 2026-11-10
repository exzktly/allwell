# Size-class ratio and per-class fold changes — remediation tracker

Live state for one body of work (CLAUDE.md §6.1). Deleted by the pull request that closes its last row.

**The ask.** Cell Gating's two size classes (for example *T cells* and *DCs*) are offered on every plot through the
Population switch and each class's share. The user asked for two more things: the fold change of each class's count
on the Nanowells plots ("T cell fold change"), and the **ratio** of the two classes as a plottable metric
("necessary"). The share-only answer, which declined the ratio, is withdrawn.

**Counts are derived from the rows below, never typed.**

| # | Item | State | Where | Evidence |
|---|------|-------|-------|----------|
| 1 | Nanowells: each class's count fold change and the ratio's fold change, over the page's Baseline, NaN when undefined | done | `nanowell/series_compute.site_metrics`, `size_class_compute.FC_COLUMNS` | `test_nanowell_size_fold_changes.py::test_each_class_s_fold_change_…`, `::test_the_ratio_s_fold_change_…`; fail when the fold change is taken over the count's baseline instead (mutation run) |
| 2 | Their undefined reasons (state, class reason, baseline state, empty class / zero ratio at baseline) | done | `series_compute.exclusion_reason`, `_value_reason` | `::test_undefined_fold_changes_say_why`, `::test_every_new_reason_has_a_figure_label_and_a_tag`; `series_compute.zero_baseline_reason` is shared with `moving_compute` |
| 3 | Registered on the page's metric menu, the Baseline control, the plots' Nanowells observations, the long export, the well statistic | done | `distribution_compute`, `state.METRIC_KEYS`, `summary_compute`, `observation_compute`, `controller` | `::test_the_page_offers_them_…`; `test_nanowell_moving_average.py::test_the_fold_change_columns_agree_with_the_data_layer_s_kinds` caught the moving-average roster and passes |
| 4 | Verify the earlier claim: with Population on one class, the Nanowells *Count fold change* is that class's | declined | test | Measured false, and changed: with the Population layer in the nanowell tally the count lost a class while the class counts did not, and every size metric was refused as "classes exceed the count". The nanowell metrics now ignore the layer; the per-class fold changes are the answer. `::test_the_population_switch_leaves_the_nanowell_metrics_…` (fails with the layer put back, mutation run) |
| 5 | Cell plots: a ratio channel per rule (`shape_class_ratio::A/B`) whose per-timepoint value is n(A) / n(B), through every aggregate path | open | `population_gate`, `data_loading._aggregate_timepoint` and its callers, `well_population`, `barplot/collect` | |
| 6 | The ratio's spread and its fold change: per-field ratios as replicates; fold change from the aggregate mean | open | `_aggregate_timepoint`, `fold_change` | |
| 7 | Per-cell views (Distribution, per-cell Scatter, Multivariate, Spatial, Cell Browser) do not draw the ratio as a cell value, and say why | open | each view | |
| 8 | README and ARCHITECTURE describe both; the README sentence that declined the ratio is rewritten | open | `_Docs/Markdowns/` | |
