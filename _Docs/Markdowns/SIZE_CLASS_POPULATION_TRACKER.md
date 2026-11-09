# Size-class populations in every plot — remediation tracker

Live state for one body of work (CLAUDE.md §6.1). Deleted by the pull request that closes its last row.

**The ask.** Cells are split into two size classes on Home ▸ Cell Gating (for example *T cells* and *DCs*). Every
plottable metric must take the classes into account and offer them: each class's count, its fold changes, and so
on. The design chosen with the user: an app-wide **Population** switch (All cells / class 1 / class 2) that every cell
plot, metric, threshold count, fold change, statistic and export follows, stated on every figure; plus per-well
class metrics — each class's count, the first class's share, the ratio of the first class to the second.

**Counts are derived from the rows below, never typed.** Count the `done`, `open` and `declined` rows to get the
totals.

| # | Item | State | Where | Evidence |
|---|------|-------|-------|----------|
| 1 | `QCParams.population` (class index or none), saved, restored, carried with the QC parameters | done | `review/domain/qc_params.py` | `test_size_class_population.py::test_the_choice_is_saved_and_restored_with_the_qc_parameters`, `::test_a_choice_restricts_nothing_…` |
| 2 | `Population_Pass` verdict layer: projector, registered, in `VERDICT_LAYERS`; cells outside the chosen class are out of every aggregate | done | `review/data/population_gate.py`, `features/registry.py`, `data_loading.py` | `::test_choosing_a_class_restricts_every_count_…` reads kept ids and `n_total`; fails with the layer removed from `VERDICT_LAYERS` (mutation run) |
| 3 | A table with no area column under a chosen class: no cell can be shown to be in it, so none counts, and the figure says why | done | `population_gate.py` | `::test_a_table_with_no_area_column_has_no_cell_in_any_class`; the footer names such wells (`population_gate.unclassable_wells`) |
| 4 | Changing the population, or the classes while a population is chosen, re-projects every verdict and redraws (not the light size-class path) | done | `qc/controller.py`, `qc/tab_view.py` | `::test_moving_a_class_s_range_while_it_is_chosen_…`; fails with the `only_size_classes_differ` guard removed (mutation run) |
| 5 | The Population control in the Plotting band, shown when the rule is complete and the leaf draws cells | done | `review/core/population_band.py`, `centre_view.py` | `::test_the_picker_is_hidden_until_the_rule_is_complete`, and the selection driven through the real combo |
| 6 | Every cell figure states the population in its footer (on screen and exported) | done | `review/core/observation_band.stamp_cells_footer` | `::test_choosing_a_class_…` reads the footer text on a figure; all six Plotting leaves call `stamp_cells_footer` |
| 7 | Cell Gating funnel names the step; Why removed overlay names and inks it | done | `gating_compute._FUNNEL_LAYERS`, `review_image/collect.EXCLUSION_LAYERS`, `renderer._layer_inks` | `test_exclusion_layers_overlay.py` roster and ink-distance tests pass with the new layer; funnel row added |
| 8 | The per-nanowell class counts and Cell Gating's cell-area curve ignore the population (they must see both classes) | done | `nanowell/controller.gate_tally`, `cell_gating` curve | `::test_the_per_nanowell_class_counts_read_both_classes_whatever_the_population`; curve reads only `_VALIDITY_COLUMNS` |
| 9 | Per-well class metrics: each class's count, share of class 1, class 1 : class 2 ratio, offered as plottable properties | open | TBD after measuring the property path | |
| 10 | README and ARCHITECTURE describe the switch, the layer and the metrics | open | `_Docs/Markdowns/` | switch and layer documented (README *Population — one size class, or every cell*; ARCHITECTURE §6.2j *Restricting the analysis to one class* |
| 11 | The cell views outside the Plotting stack (Statistics, Spatial, Multivariate) follow the layer through the chokepoint; check each names the population on what it shows | open | `review/features/stats`, `spatial`, `multivariate` | |
