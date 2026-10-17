# Review fix tracker — REVIEW_LOAD (load, hover MFI, threshold memos)

Live state for the findings of the adversarial review of PRs #1000–#1005 that
concern the dataset load, the Image Table hover MFI and the threshold-only
invalidation. One row per finding. A row moves to `done` only with the test
that pins it, seen failing on the unfixed code. This file is deleted by the
commit that closes its last row.

| ID | Defect | State | Test |
|---|---|---|---|
| H2 | `cell_gating_recompute_now` cancels the load's gating pass (which owns the `gating` plot hold) and never releases the hold; the headless report then marks an undrawn figure "drawn" | open | — |
| H3 | Image Table hover `mfi_index` (and `auto_lut_by_channel`, `_HOVER_ARRAY`) survive a dataset swap / reload; a job queued before the swap can write after it | open | — |
| M7 | Hover MFI lookups queue FIFO on one non-daemon thread, no cancellation, exit waits; "loading…" can stick while the mouse rests | open | — |
| M15 | Deferred Cell Gating read runs on the GUI thread while the GatingWorker mutates the same held frames | open | — |
| L13 | `get_timepoints_with_rows` offers label-only timepoints Statistics cannot compute | open | — |
| L14 | Stale docs: ARCHITECTURE phase name and range-cache row; `cell_gating/controller.py` docstrings; `channel_state_controller.py` comment | open | — |
| M9 | `invalidate_threshold_results` bumps `override_version` / fires `inclusion_changed_cb`, stranding the per-frame memos documented as surviving a threshold edit | open | — |
| M10 | CDF memo lives in `stats_cache.cache`, which the threshold invalidation clears | open | — |
| M11 | `prefetch_populations` ignores `per_fov_spread`, so FOV-replicate draws read every well twice | open | — |
| W1 | `tests/test_a_threshold_edit_reads_nothing.py` passes with `invalidate_threshold_results` reduced to `return None` | open | — |
| W2 | `tests/test_a_load_draws_its_plots_once.py` never drives a pass cancelled without a replacement | open | — |
