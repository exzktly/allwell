# Cell layer — one gate check, one image path, one focused cell, curation of its own, cells you can pick

A remediation tracker (CLAUDE.md §6.1), written before any code. One row per item, updated in the same commit as
the work, closed only with evidence, and deleted by the PR that closes its last row.

**Status, derived from the rows below:** count them — `grep -c '| \`done\` |'`, `'| \`todo\` |'`,
`'| \`doing\` |'`, `'| \`declined\` |'`. No total is typed here, so none can drift from the rows.

## Where this came from

A design debate between a UX reviewer and an engineering reviewer over a proposed "cell populations" layer
(a single abstraction for gates, saved cell sets and hand curation, and one cell viewer for three surfaces).
Both rejected the abstraction and agreed on five pieces of plumbing and user-facing work, in this order:

1. **One gate predicate**, below the features, with the nanowell values it needs made explicit.
2. **One image path for a single cell**: Scatter's cell popup reads through `review/data/frame_load` like the
   Cell Browser does, with one crop/brightness helper; the three surfaces keep their own jobs.
3. **One focused cell** (`CellKey`), set by any surface that names a cell and shown by every surface that can draw
   it, with a status-bar chip.
4. **Hand curation in its own verdict column**, separate from the `Included` column the intensity gate writes.
5. **Cell identity on Multivariate and Spatial points**, then click-to-focus, then saved cells drawn by lasso.

What was explicitly **not** to be built, and why, is in *Declined by design* at the end. Vocabulary for users:
**gates** (drawn rules, recomputed) and **saved cells** (fixed lists). "Population" is never a new code
identifier — `PopulationStore` / `well_population` already own the word.

Each finding below is a hypothesis about the tree when it was written (§6.1): reproduce before fixing, and record
what was measured beside what was changed.

## 1 — Gates

| # | Item | State | Evidence |
|---|---|---|---|
| G1 | `compute_gate_inclusion`, `_in_gate` and `_norm_gate` live in the Scatter feature (`review/features/scatter/gating.py:322-386`). Cell Gating's projector (registered unconditionally) and the Cell Browser reach them through `_lazy` imports of a removable feature (`cell_gating/gating_compute.py:49`, `cell_browser/collect.py:40`). Move them below the features, update every importer, no re-export left in Scatter for the outside callers | `done` | `review/domain/gates.py` (`compute_gate_inclusion`, `in_gate`, `norm_gate`); Scatter, Cell Gating and the Cell Browser import it directly, the two `_lazy` reaches are gone. `tests/test_one_gate_predicate.py::test_cell_gating_and_the_cell_browser_need_no_scatter_feature` runs both importers with `review.features.scatter` unimportable; on `origin/main` the same script raises `ImportError: scatter is not in this build` from Cell Gating's lazy proxy |
| G2 | `nanowell_values` becomes keyword-only with no default, so a caller cannot leave it out by accident. The debate claimed two callers drop it (`scatter/_cells.py:53`, `scatter/cell_scope_compute.py:53`). Reading the tree: `_cells.well_cloud` is always handed rows already joined with the plotted axes' `nw::` columns (`scatter/collect.py:246`, `scatter/_pooled.py:117`) and the gates it gets are those of the same axes (`gates_for_axes`), so the live plot is probably right. The CSV export's cell scope builds its own rows (`export/cell_scope_compute.py`, which never joins nanowell columns), so a gate on a nanowell axis would mark no cell `plot_in_gate` there. Reproduce both before fixing either | `done` | Part right. Keyword-only, no default: `test_leaving_out_the_nanowell_rows_is_an_error_not_a_silent_empty_gate`. Measured by reading: the live plot is right — both `well_cloud` callers pass rows joined with the plotted axes' `nw::` columns and only those axes' gates (`scatter/controller.py:144` → `gates_for_axes`), so `_cells.py` now states `nanowell_values=None` with that reason. The export half is G3 |

| G3 | Found while checking G2: the per-cell export scope (`scatter/cell_scope_compute.py`, `export/cell_scope_compute.population_mask`) reads rows without the nanowell join, so on a Nanowell ▸ axis the value never resolves and the export would hold no rows at all, not only no `plot_in_gate`. Established by reading, not run. The tab's own Export CSV goes through `collect_scatter_data` and is joined | `done` | Reproduced: on the nanowell fixture with Nanowell ▸ axes the old scope exported **0 of 21** cells of well A01. `CellScope.nanowell_columns` / `nanowell_rows`, joined in `build_chunk`; the joined values are written as columns. `tests/test_scatter_cell_export_reads_nanowell_axes.py` (both tests fail with `assert 0 == 21` when the scope carries no columns) |

## 2 — One image path for a single cell

| # | Item | State | Evidence |
|---|---|---|---|
| V1 | One Qt-free crop helper: padded bounding box of a nucleus, crop, normalise to the shared per-channel display window (`ImageViewState.lut_by_channel`) or the crop's 1st–99th percentile, and the one-pixel outline. Today the Cell Browser has `_bbox_for` / `_norm01` (`cell_browser/worker.py:29-61`) and Scatter `_get_cell_bounds` / min–max (`cell_viewer_view.py:610-647, 985-1038`) | `todo` | |
| V2 | The Cell Browser's composite goes through V1, pixel-identical to today's | `todo` | |
| V3 | Scatter's popup reads through `frame_load.load_frame_array` / `load_mask_array`, addressed by the row's well, field, timepoint token and nucleus id. Delete the private resolver: `_load_output_image_by_filename`, `_load_and_crop_nuclear`, `_load_input_channel_by_filename`, `_pick_member_for_fov`, `_path_carries_channel`, `_well_token_variants`, `_filename_variants`, `_get_cell_bounds`. Each of those guessed a zip member from the row's filename and fell back to the first match on ambiguity, recorded only in the debug panel | `todo` | |
| V4 | Scatter's popup takes its brightness from the shared display window like the Cell Browser and the Image Table, with *Auto* still offered; typed LUT boxes write the shared window rather than a popup-private one | `todo` | |
| V5 | The "Diagnostics" debug panel leaves the popup. A cell that cannot be shown says why, in a sentence, in the image area (no mask, nucleus not in the mask, no frame for a channel) | `todo` | |
| V6 | The popup loads off the GUI thread (`core.worker_bridge.run_off_thread`), and a load that finishes after the user clicked another cell is dropped, not drawn | `todo` | |

## 3 — One focused cell

| # | Item | State | Evidence |
|---|---|---|---|
| F1 | `review/core/focus_state.py`: `FocusState.cell: CellKey \| None`, on `AppState`, cleared on a dataset swap, with one event-bus event when it changes | `todo` | |
| F2 | Status-bar chip: `● B03 · F001 · 24h · #187  ✕`. Hidden with no focused cell; ✕ clears it; clicking it reopens the cell | `todo` | |
| F3 | Setters: a Scatter point click, a Cell Browser tile click, a Segmentation nucleus click and a Review CSV double-click each set the focused cell | `todo` | |
| F4 | Segmentation's `selected_nucleus` is read from the focused cell when it names the field on screen, so a cell focused elsewhere is outlined when Segmentation shows its field | `todo` | |
| F5 | Scatter draws a ring on the focused cell's point (a theme token, not a palette colour) when it is in the plotted cloud | `todo` | |
| F6 | Scatter's popup gets *Show in Segmentation*, so the jump is a button rather than a surprise tab switch | `todo` | |

## 4 — Hand curation in its own column

| # | Item | State | Evidence |
|---|---|---|---|
| C1 | Curation writes a `Curation_Pass` verdict column (0 for a cell deleted by hand) and stops writing `Included` for a deletion. Measured first: every writer in the app writes `"0"` (`review_image/controller.py:145`, `review_image/callbacks.py:306`); a `"1"` override can only come from an older file. Decide and record what a `"1"` does (it used to force `Included = 1` over the intensity gate) | `todo` | |
| C2 | `Curation_Pass` joins `VERDICT_LAYERS` (`data_loading.py:562`), `df_included_mask`, the projector's column declaration for `narrow_rows`, and the funnel, whose "Curation + intensity gate" row (`gating_compute.py:90`) becomes two rows | `todo` | |
| C3 | Segmentation's "why removed" names a hand deletion by its own column (`review_image/collect.py:256` maps `Included` to "deleted here"), and the `Included` readers in Segmentation and the crop region (`crop/region.py:307`, `review_image/collect.py:279,410,957`, `review_image/controller.py:826`) are re-pointed or left with a reason | `todo` | |
| C4 | The export cell-scope block (`export/cell_scope_compute.py:68`) states the new layer in its rule | `todo` | |

## 5 — Cell identity on Multivariate and Spatial, then picking cells

| # | Item | State | Evidence |
|---|---|---|---|
| M1 | Multivariate's matrix carries each cell's field, timepoint token and nucleus id beside `well`, and `_subsample_for_projection` slices them with the rest (`multivariate/controller.py:36-56`) | `todo` | |
| M2 | A click on a Multivariate projection point focuses that cell; the focused cell is ringed; when it is not among the sampled cells the plot says so ("not among the 2,500 sampled cells") | `todo` | |
| M3 | Spatial's points carry the same identity; click-to-focus and the ring | `todo` | |
| M4 | Saved cells: `review/persistence/cell_sets.py`, keyed by `CellKey`, carrying each well's `<well>_out.zip` fingerprint like `cell_overrides.py`; a set whose fingerprint no longer matches reads as stale with how many of its cells still match, never silently empty | `todo` | |
| M5 | Lasso on Spatial, then on Multivariate, saves the cells inside it. A Multivariate selection records that it was drawn on a sample and states its denominator | `todo` | |
| M6 | Saved cells are used without changing the analysis by default: as a Cell Browser source (replacing the sticky *From scatter gate* checkbox with a Source menu) and as a colour on Scatter. One global *Exclude saved cells* slot in Cell Gating is the only way a set changes the numbers | `todo` | |

## Screenshots to check by hand

Every row that changes something on screen gets a rendered screenshot (offscreen Qt, the `images` page dataset
of `tests/_page_datasets.py` extended as needed), sent with the commit that changes it.

## Declined by design (from the debate, with the reason)

| # | Item | State | Evidence |
|---|---|---|---|
| D1 | A polymorphic "population" interface over gates, sets and curation | `declined` | Gates are already one function and lists did not exist; an interface with one and a half implementations does not pay for itself. Users see "gates" and "saved cells", never the storage distinction |
| D2 | Saved cells as verdict layers | `declined` | A verdict removes cells from every aggregate; one per set would shrink every plot whenever a set is saved. One explicit global exclusion slot instead (M6) |
| D3 | One viewer widget for the Scatter popup, the Cell Browser tile and Segmentation | `declined` | Segmentation navigates to a whole field; the others crop one cell. They share the image path and the crop helper instead (V1–V3) |
| D4 | Lasso on Distribution, drawing on a phone, the classifier, Batch Export reading sets | `declined` | Deferred: a 1-D plot wants a range brush; a lasso fights touch scrolling; the classifier needs sets first; a stale set silently shrinking a batch export is the worst place to break §7 |
