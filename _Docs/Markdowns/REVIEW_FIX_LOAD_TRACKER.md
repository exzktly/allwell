# Review fix tracker — REVIEW_LOAD (load, hover MFI, threshold memos)

Live state for the findings of the adversarial review of PRs #1000–#1005 that
concern the dataset load, the Image Table hover MFI and the threshold-only
invalidation. One row per finding. A row moves to `done` only with the test
that pins it, seen failing on the unfixed code. This file is deleted by the
commit that closes its last row.

| ID | Defect | State | Test |
|---|---|---|---|
| H2 | `cell_gating_recompute_now` cancels the load's gating pass (which owns the `gating` plot hold) and never releases the hold; the headless report then marks an undrawn figure "drawn" | done | `test_a_load_draws_its_plots_once.py::test_a_synchronous_recompute_that_cancels_the_load_s_pass_lifts_its_hold` (red: `'gating' == ''`), `::test_the_report_does_not_call_a_figure_drawn_while_the_plots_are_held` (red: 222,204-byte PNG, empty reason) |
| H3 | Image Table hover `mfi_index` (and `auto_lut_by_channel`, `_HOVER_ARRAY`) survive a dataset swap / reload; a job queued before the swap can write after it | done | `tests/test_image_table_hover_follows_the_dataset.py` (slow; drives `app._load_path`): red on the unfixed tree — `hover quoted MFI 104.0 … not 1104.0`, `a lookup queued before the swap wrote into the new dataset's memo`, `pooled Auto LUT window survived the reload`; mutating the load's `clear_caches()` call away and dropping the generation from `_HOVER_ARRAY`'s key each turn it red again. `test_image_table_hover_mfi.py::test_a_new_generation_drops_the_index_and_disowns_old_cells` replaces the direct `clear_caches()` swap assertion |
| M7 | Hover MFI lookups queue FIFO on one non-daemon thread, no cancellation, exit waits; "loading…" can stick while the mouse rests | done | `tests/test_image_table_hover_mfi_runner.py` (latest-wins, daemon + shutdown, teardown stopper, resting refresh on the UI thread). Base probe: four jobs submitted while the first ran all ran `['A','B','C','D']` on a non-daemon `image-table-mfi_0` thread |
| M15 | Deferred Cell Gating read runs on the GUI thread while the GatingWorker mutates the same held frames | done | `test_a_load_reads_cell_gating_once.py::test_the_owed_reading_waits_for_the_load_s_gating_pass` (red: `reads … [True]` — the first-frame read ran while the pass was writing), `::test_a_pass_the_user_started_pays_the_reading_the_tab_deferred` (removing the post-pass pay leaves the reading owed: it timed out, and now fails with an AssertionError naming it) |
| L13 | `get_timepoints_with_rows` offers label-only timepoints Statistics cannot compute | done | `tests/test_statistics_menu_offers_what_statistics_computes.py` (red: the menu offered `[0.0, 24.0, 48.0]` for a well whose 48 h row is label-only) |
| L14 | Stale docs: ARCHITECTURE phase name and range-cache row; `cell_gating/controller.py` docstrings; `channel_state_controller.py` comment | done | docs only (ARCHITECTURE phase name and range-cache sentence, the two `cell_gating/controller.py` docstrings, the `channel_state_controller.py` re-entrancy comment); no test |
| M9 | `invalidate_threshold_results` bumps `override_version` / fires `inclusion_changed_cb`, stranding the per-frame memos documented as surviving a threshold edit | open | — |
| M10 | CDF memo lives in `stats_cache.cache`, which the threshold invalidation clears | open | — |
| M11 | `prefetch_populations` ignores `per_fov_spread`, so FOV-replicate draws read every well twice | open | — |
| W1 | `tests/test_a_threshold_edit_reads_nothing.py` passes with `invalidate_threshold_results` reduced to `return None` | open | — |
| W2 | `tests/test_a_load_draws_its_plots_once.py` never drives a pass cancelled without a replacement | done | the two H2 tests above, added to that file |
