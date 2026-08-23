# Work package W1 — 9 items

## Item 80 — severity Medium

**Reported location (line numbers are stale):** `well_viewer/persistence/cell_overrides.py:62,108,186`

**Original finding:** `except OSError: pass` plus an absent `<well>_out.zip` makes the v2 staleness fingerprint fail open, and the compensating warning is gated on `file_version < 2`, so a v2 file warns about nothing.

**Verified state at HEAD:** well_viewer/persistence/cell_overrides.py unchanged since PR #782: lines 64-65 and 110-111 still `except OSError: pass`; line 161 `if current_fp and stored_fp != current_fp` still fails open when the well's _out.zip is absent (current_fp == ""); line 186 still gates the compensating warning on `file_version < _SCHEMA_VERSION`, so a v2 file with empty seg_fp entries warns about nothing.

## Item 81 — severity Medium

**Reported location (line numbers are stale):** `well_viewer/persistence/reset_scopes.py:270`

**Original finding:** `VIEW_DOMAINS` claims `line_order` but `reset_view` never saves it, so the stale order returns on the next load and permanently reorders `selections`. The module's own promise is "what resets live must equal what is dropped from disk".

**Verified state at HEAD:** well_viewer/persistence/reset_scopes.py:269 still claims "line_order" in VIEW_DOMAINS, but reset_view (line 274) only calls view_state.reset, _save(ctx,"heatmap_layouts") and _save(ctx,"multivariate") — there is no _save(ctx,"line_order"), so the stale `line_order` section stays on disk and line_order.load_from_data_dir re-applies it (and _reorder_selections_by_line_order) on the next load.

## Item 82 — severity Medium

**Reported location (line numbers are stale):** `well_viewer/persistence/view_state.py:105,462`

**Original finding:** `absent_wells` / `absent_solo` survive a dataset swap and are reassigned only inside `restore()`, which is skipped when the new dataset has no `view_state` section: dataset A's wells get unioned into B's saved selection.

**Verified state at HEAD:** ctx.runtime.view_state.absent_wells / absent_solo are assigned only in view_state.restore (lines 462, 485) and cleared only in view_state.reset (lines 1088-1089). load_controller.begin_load (lines 400-440) clears plot/export_prefs/image_view/channel caches but never these, and load_from_data_dir (line 1023) early-returns when the new plate has no view_state section — so dataset A's wells are still unioned into B's snapshot (view_state.py:105,117).

## Item 83 — severity Medium

**Reported location (line numbers are stale):** `well_viewer/persistence/heatmap_layouts.py:130,264`

**Original finding:** The heatmap timepoint is persisted as a combo **index**, which §9.7's add-timepoints workflow invalidates.

**Verified state at HEAD:** well_viewer/persistence/heatmap_layouts.py:37 still persists `"tp_index": int(tp_cb.currentIndex())` and line 175-182 restores it by clamping to the live item count — an index, not a timepoint value, so adding timepoints re-points it at a different tp.

## Item 84 — severity Medium

**Reported location (line numbers are stale):** `well_viewer/persistence/sample_definitions.py:216`

**Original finding:** `well_labels` are hydrated pruned to loaded wells and then autosaved back, erasing labels for wells absent from a subset load. `view_state` solved this with `absent_wells`; this path did not.

**Verified state at HEAD:** well_viewer/persistence/sample_definitions.py:216-219 hydrates well_labels only for tokens in ctx.state.dataset.well_paths, and autosave_to_pipeline_info (line 62) rebuilds the block from ds.well_labels alone via build_sample_definitions_v2 — there is no absent-label stash equivalent to view_state's absent_wells, so labels for wells outside a subset load are erased on the next autosave.

## Item 85 — severity Medium

**Reported location (line numbers are stale):** `well_viewer/persistence/smfish.py:123`, `well_viewer/domain/gating_state.py:200`

**Original finding:** Unlocked read-modify-write of `pipeline_info.json` from a viewer sharing a window with a running Analyze pass; a lost update reverts `available_timepoints` / `fluor_tokens` — the §12.3 symptom.

**Verified state at HEAD:** well_viewer/persistence/smfish.py:124-160 still does an unlocked read-modify-write of pipeline_info.json (json.loads then atomic_write_json), as does domain/gating_state.py:200-238 (save_gating_to_pipeline_info). No file locking exists anywhere in the repo — grep for fcntl/flock/filelock/LOCK_EX returns nothing.

## Item 86 — severity Medium

**Reported location (line numbers are stale):** `well_viewer/core/channel_state_controller.py:150`

**Original finding:** A bare mid-function `return` skips `apply_range` and the whole gating-hydration tail, so `threshold_min/max` keep the previous dataset's range (`begin_load` clears only the cache).

**Verified state at HEAD:** well_viewer/core/channel_state_controller.py:187-188 still `if all_vals.size == 0: return` inside _recalculate_threshold, skipping channel_state.apply_range (line 195) and the whole cell-gating hydration tail (lines 197-220). load_controller.py:415 clears only threshold_range_cache, so ChannelState.threshold_min/max keep the previous dataset's values.

## Item 88 — severity Low

**Reported location (line numbers are stale):** `well_viewer/features/home/groups/panel_view.py:78`

**Original finding:** A plate edit rewrites a group's membership from the plate alone, permanently dropping wells the current dataset does not hold — and autosaves that.

**Verified state at HEAD:** well_viewer/features/home/groups/panel_view.py:81-91 builds `new` as `plate.selectedWellIds() ∩ well_paths` and calls sel_set_composition(ctx, sid, new); selection_controller.sel_set_composition (line 1072) sets s["wells"] = _clean_wells(wells) outright, so wells the current dataset does not hold are dropped from the group and then autosaved.

## Item 90 — severity Medium

**Reported location (line numbers are stale):** `well_viewer/features/saved_plots/tab_view.py:124`

**Original finding:** `_confirm_clear` falls through to clearing when the confirmation dialog raises — an unconfirmed destructive wipe of the gallery and its PNGs.

**Verified state at HEAD:** well_viewer/features/saved_plots/tab_view.py:124-134 _confirm_clear still has `except Exception: swallowed()  # no dialog (e.g. headless) — fall through to clearing` followed by an unconditional C.clear_saved_plots(ctx). git log -L on the function shows it untouched since PR #782, so the comment is the original author's rationale, not a post-review decision.

