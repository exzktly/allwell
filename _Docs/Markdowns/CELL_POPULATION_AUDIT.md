# Cell population audit — remediation tracker

**Bug class.** Two numbers or pictures shown (or exported) together describe different populations of cells:
one built from every cell the pipeline counted, the other from the cells Review's inclusion rule keeps
(`review.data.data_loading.df_included_mask`: manual curation `Included`, `QC_Pass` — the QC size and shape
gate — `Crop_Pass`, `Gate_Pass` for the scatter gates, and the Field / Focus / Border / Excluded-field / FOV
passes). The reported instance: on the Nanowells page the Cells dots were drawn from the raw cell table while
the count under each nanowell was the run's `n_cells` less the cells Cell Gating leaves out, so with QC area
limits set a nanowell showed more dots than its count.

This tracker is live state for the audit and its fixes. Each row has a state: `open` (found, not fixed),
`done` (fixed, with the test that pins it), `cleared` (checked, not a defect — reason given), `declined`
(a defect not worth fixing — reason given). Counts are derived from the rows; do not type a total here.
Delete this file in the pull request that closes its last `open` row.

## Findings

| ID | Where | Finding | State | Evidence / reason |
|---|---|---|---|---|
| P1 | `review/features/nanowell/collect.py` `cell_positions`, `array_compute.cells_into`, `image_compute._cells_in`, `renderer._draw_crop_cells` | Cells dots (Array view and crops) drawn from every counted cell; the count under each nanowell excludes cells Cell Gating leaves out. Crop dots also included neighbouring nanowells' cells. | done | `tests/test_nanowell_cell_dots.py::test_with_the_qc_size_gate_on_the_filled_dots_are_the_cells_the_counts_count` (fails on the old code with `{0: 2, …} != {0: 1, …}`), `::test_crop_dots_are_the_nanowell_s_own_cells_with_the_left_out_ones_hollow`, `::test_the_gate_tally_names_each_cell_it_leaves_out_in_the_form_the_dots_are_keyed_in`. Five mutations each caught. |
| P2 | `review/features/nanowell/cell_scope_compute.py:25` | Nanowells *Save cell data…* exports with `include_gate=False` (drops `Gate_Pass`), while the page's counts are tallied with the scatter gates applied (`controller.gate_tally` → `df_included_mask(frame)`). With scatter gates in inclusion mode the exported cells include cells the counts and plots leave out. | open | Traced in code; not yet reproduced. |
| P3 | `review/features/nanowell/series_compute.py:406-420` (`long_format`, used by `controller.export_long_csv`) | The long-format CSV writes the gated count as `n_cells` beside the pipeline's `area_est_cells` and `count_disagreement`, which were computed against the run's raw count, and the file carries no column saying the count was gated or by how many cells. | open | Traced in code; not yet reproduced. |
| P4 | `review/features/lineplot/controller.py:900-904` | The Line Graphs status line's "N nuclei" sums the summaries' raw `n_rows`, while the plots and their n panel use the included cells. | open | Traced in code; not yet reproduced. |
| C1 | `review/features/home/cell_gating/gating_compute.py:412` `_cell_gating_valid_mask` | The Cell Gating CDFs and optimiser omit the fluorescence gate (`Included`). | cleared | Deliberate and documented at the site: the curves are what the user reads to set that gate, so filtering by it would be circular. |
| C2 | `allwell_report.py` | Headless report could draw from raw rows. | cleared | It constructs a real `ReviewApp` and loads through `_load_path`, so the cache projectors apply. Pending the agent check that every inclusion layer is restored headless. |
| C3 | `review/data/cell_pool.py:703` | Uses raw `n_rows`. | cleared | Only an upper bound that decides whether to take the sampled path; never displayed. |
| C4 | `review/features/export/service.py`, `review/features/batch_export/_cell_rows.py` | Cell exports read `get_rows` directly. | cleared | Both go through `cell_scope_compute.build_chunk`, which applies `df_included_mask` with the scope's own `include_gate`. |
| C5 | `review/features/scatter/cell_viewer_view.py:378` | Reads raw rows. | cleared | A single-row lookup by index for one cell's image; no population statement. |
| C6 | Distribution, Bar, Scatter, Stats, Multivariate, Spatial, Response, Heatmap collectors | Per-cell plot collectors. | cleared | Each applies `df_included_mask` directly or through `_all_fluor_values_filtered` / `well_cloud`; the scatter's `include_gate=False` is deliberate (gate membership is its own marker). |
| C7 | `review/features/batch_export/_line_series_runner.py:145`, `scatter_panel.py:293` | Read raw rows. | cleared | The first goes through `_all_fluor_values_filtered`; the second reads timepoints only, to fill a picker. |
| C8 | `review/features/barplot/renderer.py:156` `_pooled_rows` | Returns raw rows. | cleared | Defined but never called. |
| Q1 | `review/features/smfish/worker.py:~194` | `counts.get(k, 0)` writes 0 spots for any cell absent from `counts`; the all-miss diagnostic does not fire on a partial miss. A different class (not-computed stored as zero), recorded here because it also changes a reported number. | open | Not yet established whether an absent cell genuinely has zero spots. |
