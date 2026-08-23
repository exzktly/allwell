# Review remediation — living tracker

A full review of the Review pane and its supporting layers returned **177
findings** across twelve themes. Most were fixed across pull requests #834–#841
and #843. A reconciliation pass then checked every one of the 177 against the
tree rather than against the report, and found that **128 were closed, 7 were
declined deliberately, and 42 were still owed** — a gap that had gone unnoticed
because progress was being carried in conversation instead of written down.

This file is that record. It is the single source of truth for the state of this
work: an item is done when its row here says so, and the row says so only once
the fix is in the tree with a test that was watched failing without it.

**This document is temporary.** `_Docs/Markdowns/` holds three permanent
documents by design (CLAUDE.md §5); this is one of the sanctioned temporary
trackers, kept for the same reason `GOD_OBJECT_REPOSITORY_ROADMAP.md` is kept —
it is live state, not a write-up. **Delete it in the pull request that closes the
last row.**

## How to read the state column

| State | Meaning |
|---|---|
| `done` | Fixed in the tree, with a test watched failing against the unfixed code |
| `in progress` | Being worked right now |
| `queued` | Planned, not started |
| `declined` | Judged a non-defect on closer reading; the reason is recorded at the site and here |
| `deferred` | Real, but deliberately postponed; the reason and the trigger to revisit are recorded |

## Status

| Group | Scope | Owner | Items | State |
|---|---|---|---|---|
| W1 | Persistence and cross-dataset leakage (theme 6) | worker | 9 | **in progress** |
| W2 | Destructive dataset operations (theme 5) | worker | 8 | **in progress** |
| W3 | Image identity and batch export (themes 4, 7) | worker | 14 | **in progress** |
| ME | smFISH, fold change, stats, guards, docs | lead | 11 | **in progress** |

Counts: **7 done · 0 declined · 1 deferred · 34 outstanding** of 42.

The four groups are partitioned by file, not by theme, so that they can run at
the same time without touching the same lines. Where a theme's items live in
another group's files, the item moves to the group that owns the file.

## Two cross-cutting fixes, sequenced last

Two of these rows are not local edits and must land after the groups they touch,
or they will go red on files another group is still holding:

1. **Widening `tests/test_no_silent_except.py`.** The guard enforces the rule as
   the literal statement `pass` under a bare `except Exception`, so two live
   evasions pass it today: a narrowed type (`except OSError: pass`, item 80) and
   a non-`pass` body that manufactures a plausible answer
   (`except Exception: return frozenset()`, item 49). Returning an empty
   container is worse than passing, because the caller cannot tell the empty
   result from a real one. The widened guard must go green repo-wide, so it
   lands only once every group has fixed its own violations.
2. **The `epsilon` rule (item 39).** The docstring the report named is fixed;
   the same wrong rule is still written in `well_viewer/data/data_loading.py`
   and in the user-facing Ratios tab. A sweep, not an edit.


### Group W1

| # | Sev | State | Item | Where it stands |
|---|---|---|---|---|
| 80 | Medium | queued | `except OSError: pass` plus an absent `<well>_out.zip` makes the v2 staleness fingerprint fail open, and the compensating warning is gated on `file_version < 2`, so a v2  | well_viewer/persistence/cell_overrides.py unchanged since PR #782: lines 64-65 and 110-111 still `except OSError: pass`; line 161 `if current_fp and stored_fp != current_fp` still fails open when the  |
| 81 | Medium | queued | `VIEW_DOMAINS` claims `line_order` but `reset_view` never saves it, so the stale order returns on the next load and permanently reorders `selections`. The module's own pr | well_viewer/persistence/reset_scopes.py:269 still claims "line_order" in VIEW_DOMAINS, but reset_view (line 274) only calls view_state.reset, _save(ctx,"heatmap_layouts") and _save(ctx,"multivariate") |
| 82 | Medium | queued | `absent_wells` / `absent_solo` survive a dataset swap and are reassigned only inside `restore()`, which is skipped when the new dataset has no `view_state` section: datas | ctx.runtime.view_state.absent_wells / absent_solo are assigned only in view_state.restore (lines 462, 485) and cleared only in view_state.reset (lines 1088-1089). load_controller.begin_load (lines 400 |
| 83 | Medium | queued | The heatmap timepoint is persisted as a combo **index**, which §9.7's add-timepoints workflow invalidates. | well_viewer/persistence/heatmap_layouts.py:37 still persists `"tp_index": int(tp_cb.currentIndex())` and line 175-182 restores it by clamping to the live item count — an index, not a timepoint value,  |
| 84 | Medium | queued | `well_labels` are hydrated pruned to loaded wells and then autosaved back, erasing labels for wells absent from a subset load. `view_state` solved this with `absent_wells | well_viewer/persistence/sample_definitions.py:216-219 hydrates well_labels only for tokens in ctx.state.dataset.well_paths, and autosave_to_pipeline_info (line 62) rebuilds the block from ds.well_labe |
| 85 | Medium | queued | Unlocked read-modify-write of `pipeline_info.json` from a viewer sharing a window with a running Analyze pass; a lost update reverts `available_timepoints` / `fluor_token | well_viewer/persistence/smfish.py:124-160 still does an unlocked read-modify-write of pipeline_info.json (json.loads then atomic_write_json), as does domain/gating_state.py:200-238 (save_gating_to_pip |
| 86 | Medium | queued | A bare mid-function `return` skips `apply_range` and the whole gating-hydration tail, so `threshold_min/max` keep the previous dataset's range (`begin_load` clears only t | well_viewer/core/channel_state_controller.py:187-188 still `if all_vals.size == 0: return` inside _recalculate_threshold, skipping channel_state.apply_range (line 195) and the whole cell-gating hydrat |
| 88 | Low | queued | A plate edit rewrites a group's membership from the plate alone, permanently dropping wells the current dataset does not hold — and autosaves that. | well_viewer/features/home/groups/panel_view.py:81-91 builds `new` as `plate.selectedWellIds() ∩ well_paths` and calls sel_set_composition(ctx, sid, new); selection_controller.sel_set_composition (line |
| 90 | Medium | queued | `_confirm_clear` falls through to clearing when the confirmation dialog raises — an unconfirmed destructive wipe of the gallery and its PNGs. | well_viewer/features/saved_plots/tab_view.py:124-134 _confirm_clear still has `except Exception: swallowed()  # no dialog (e.g. headless) — fall through to clearing` followed by an unconditional C.cle |

### Group W2

| # | Sev | State | Item | Where it stands |
|---|---|---|---|---|
| 18 | Medium | queued | The "did anything land?" check is skipped exactly when the filtered archive came out empty — "nothing to compare against" conflated with "nothing landed". | datasets/subset/apply.py:243 still reads `if fname and kept_in_names and fname not in kept_in_names:` — the `and kept_in_names` truthiness test skips the 'did anything land?' check exactly when the fi |
| 67 | High | queued | `_remap._rename` discards `rewrite_member_field`'s `was_rewritten` flag, so a member failing the structural gate keeps dataset 2's original well token inside the destinat | datasets/merge_plates/apply.py:84-88 _remap._rename still returns _remap_well_field(...)[0] and drops the was_rewritten flag; rewrite_member_field (datasets/_common/member_names.py:119-131) returns th |
| 68 | Medium | queued | In-place mode zips and unlinks the TIFs of **every** well-named subfolder under the source, including pre-existing ones; the preview is non-recursive and loose-TIF-only.  | datasets/import_loose/apply.py:217-235 _zip_well_folders still iterates every directory entry of the folder whose name canonicalises to a well and zips/unlinks its TIFs, with no distinction between fo |
| 69 | Medium | queued | `row_matches` reads the `timepoint_hours` **column** and `member_matches` reads the **filename** token. A legacy CSV with a blank `timepoint_hours` loses every row while  | datasets/subset/subset_compute.py:215-225 row_matches still reads float(row['timepoint_hours']) and returns False on a blank/unparsable value, while member_matches (line 182-212) parses the filename's |
| 70 | Medium | queued | Two containers canonicalising to the same well (`A1.zip` + `A01.zip`) collapse by plain dict overwrite in glob order. The `out_csvs` sibling documents its identical overw | datasets/_common/dataset_paths.py:187-205 in_containers still assigns containers[well] = zip_path in glob order with no collision detection or warning, so A1.zip and A01.zip collapse silently; the sib |
| 73 | Low | queued | `replace_folder_with_archive`'s `resolve()` identity conflates symlink and hardlink aliases in both directions: an archived symlink is reported as `kept`, and a second na | util/zip_write.py:153-168 replace_folder_with_archive still builds archived_resolved from Path(p).resolve() and compares p.resolve() for each walked file; there is no st_ino/samefile handling and no m |
| 74 | Medium | queued | `copy2`/`move` silently overwrite same-basename files under `--recursive`; the progress line counts sources, not survivors. | WellPlateZipper.py:186-195 folder_well still does place(f, os.path.join(well_dir, os.path.basename(f))) for shutil.copy2/shutil.move with no existence check, then prints f'{well}: {len(files)} files'  |
| 76 | Low | queued | The `seg_fp` restamp is set only inside `with swallow(...)`; on a failed stat the entry silently keeps the **source** dataset's fingerprint and the carried-over override  | All three sites still assign inside the suppression: datasets/subset/apply.py:121-122, datasets/merge_plates/apply.py:271-272 and datasets/concatenate/apply.py:438-439 each do `with swallow(f"stat {zi |

### Group W3

| # | Sev | State | Item | Where it stands |
|---|---|---|---|---|
| 2 | Medium | queued | `_export_binary_mask` catches every exception into `above_by_nid = {}` and renders an all-black silhouette — identical to "no cells above threshold". Same all-black resul | The exception half is fixed: image_compose.py:247-259 logs and returns None ('reported unrenderable') instead of `above_by_nid = {}`. The second half the finding names is untouched — review_image/coll |
| 12 | Medium | queued | A mid-scan exception returns partially filled dicts indistinguishable from a complete scan, under a "Result: N fluor…" line that reads as an inventory. | data/zip_members.py:223-236 now logs the partial counts and says 'images after the failure point are missing from it', so the log no longer reads as a complete inventory. But `scan_zip_members` still  |
| 23 | Low | queued | A verdict that raises during re-projection leaves the previous `QC_Pass`/`Crop_Pass` values attached: plots filter by the old parameters while the UI shows the new ones. | well_viewer/data/cache_projectors.py:79-83 — `df[column] = verdict(df, lbl).astype(int)` inside try/except that only logs; when the verdict raises during a re-projection the frame keeps the previous Q |
| 45 | Medium | queued | An inherited per-member control mode shows `—` in the Control combo while the export normalizes anyway. | features/batch_export/base_panel.py:251-253 still inherits `_fc_control_mode` / `_fc_control_map` from the plot tab, and `_fc_state()` (:1137-1142) passes `control_mode` straight through to the export |
| 56 | Medium | queued | An unparseable well label yields `well_token=None`, which **disables** the per-well filter in the loose-file fallback, so every well's images are accepted into that one w | well_viewer/data/image_discovery.py:696-714 (find_well_images_and_masks) now logs a warning when a well label does not parse ('the loose-file scan cannot filter by well — images belonging to other wel |
| 57 | Medium | queued | An Overlay cell can never carry the `raw` uncorrected badge, so the status line counts uncorrected cells no badge ever identifies. | controller.py:787-798 returns from the OVERLAY_CHAN branch without ever calling mark_uncorrected, and image_compose.is_raw_fallback (line 1021-1036) keys on _binary_base(cell['chan']) — for an overlay |
| 59 | Low | queued | A ratio cell's raw-fallback is recorded under the numerator/denominator channel, so it is never badged and is counted under a channel absent from the grid. | image_compose._load_ratio_array (line 497-514) loads through _load_array with num_chan/den_chan, and frame_load.load_frame_array:365 records raw_fallbacks under (well, that channel, tp, fov). is_raw_f |
| 60 | Low | queued | `composite_column_rgb` calls `_row_display_opts(ctx, r)` with no channel, so it always loads the **raw** frame for a corrected channel. Dead today (re-export only). | image_compose.composite_column_rgb still calls `use_tophat = _row_display_opts(ctx, r)[0]` with no channel argument (image_compose.py:709); _row_display_opts (line 428) returns (False, False, '') when |
| 61 | Low | queued | `_parse_lut`'s no-spec fallback applies column 0's channel LUT to the whole row. Reachable only with an empty `channel_opts`. | image_compose._parse_lut:411-416 still falls back to _shared_channel_lut(ctx, row_idx), which reads _row_channel -> cells[row_idx][0]['chan'] (line 356-361) — column 0's channel — even when a chan arg |
| 62 | Low | queued | Overlay cells install a hover readout keyed on the sentinel channel, so their pixel readout can never resolve and is always empty. | controller.py:1059-1061 passes cell['chan'] (the '⊕ OVERLAY' sentinel for an overlay cell) into _install_cell_events; _hover_array (line 476-500) then calls _load_array with that sentinel, which resol |
| 99 | Medium | queued | The multi-timepoint aggregate `_draw` omits `color=data["color"]` that the single-tp sibling and the tab both pass. | well_viewer/features/batch_export/scatter_panel.py:844-849 (_run_scatter_agg_multi_tp_job's _draw) still calls ax.errorbar with label/marker/linestyle/capsize/alpha and no `color=data["color"]`, while |
| 100 | Medium | queued | val_col and metric are re-read per job while the channel token is snapshotted, across a `processEvents()` pump. | well_viewer/features/batch_export/base_panel.py:1754 snapshots _ch_selected once outside _run_group, while _run_group re-reads _val_col (line 1791) and _metric (line 1793) from the live combos on ever |
| 102 | Low | queued | The pre-rendered-RGB branch skips chrome and the raw badge and uses a different contrast than the export path for the same cell. | well_viewer/features/image_browser/image_table/controller.py:834-837: for a pre-rendered RGB cell (arr.ndim >= 3 and not NUC+SEG) the live path still does make_overlay_thumb(cropped, sz, sz, lo, hi) w |
| 104 | Low | queued | Conditional scope restore leaves the app in the batch's scope on one branch. | well_viewer/features/batch_export/base_panel.py:663-681 _app_val_col_scope still restores conditionally — `finally: if saved is not None: chan.active_val_col = saved` — so when the pre-existing active |

### Group ME

| # | Sev | State | Item | Where it stands |
|---|---|---|---|---|
| 1 | High | done | A well whose spot-counting pass raises is logged and then dropped from `counts`; `counts.get(k, 0)` writes a full column of `0` into that well's CSV and `done_cb` still r | Fixed: worker.py records failed wells and _write_counts_to_csvs skips them, so they keep their previous counts and land in the run's shortfall instead of a fabricated zero column. tests/test_smfish_failed_well_is_not_zero_filled.py — proved by neutralising the skip at HEAD (real AssertionError). |
| 31 | Medium | deferred | The replicate-set average is a pool-of-cells mean, not the mean-of-per-well-means Bar, Line and Stats use. Possibly deliberate; undocumented either way. | Half-closed: the divergence is now documented at _cell_value and in ARCHITECTURE (pool-of-cells vs mean-of-per-well-means, and when they diverge). Unifying them changes every heatmap number a user has seen and aggregate_group is shared, so which convention is right is the user's call, not mine. Revisit when that decision is made. |
| 34 | Medium | done | The per-cell Events panel ignores `bar_err_settings`, so "None" and "95 % CI" apply to grouped mode only. | Fixed: draw_per_well_n_bars now reads bar_err_settings(ctx) — the same accessor the aggregate path uses — so the Show mode and error-bar type govern the per-cell Events panel, and 95 % CI scales by 1.96. tests/test_per_cell_events_honours_stats_controls.py, proved on origin/main. |
| 39 | Low | done | Docstring says epsilon is *added*; the resolver *clamps* (`np.maximum(den, epsilon)`) — different numbers near and below zero on background-subtracted channels. | Swept: the clamp-not-add rule is now stated consistently in domain/ratio_models.py, data/data_loading.py, the Ratios tab intro text, and README_WellViewer.md. The manual was a fourth site the finding did not name. |
| 47 | Low | queued | `tick_labels` de-duplicates by reference and `distinct_controls` by resolved wells, so a one-well control set plus that well's own token gives an axis title naming one co | The two de-duplications still key on different things and both are pinned as such: fold_change_pairing.py:137-146 (`distinct_controls`) skips on `rec.wells in seen`, while :535-543 (`tick_labels`) bui |
| 48 | Medium | queued | One row edit rebuilds the table twice, and `setCellWidget` destroys the combo whose slot is still on the stack — roughly 192 combos per edit on a 96-member plate. | core/fold_change_pairing_view.py:225-229 — `_write` calls `set_fold_change_state(ctx, control_map=mapping, initiating_scope='')` and then `_populate()`; with no scope skipped, `_sync_widgets_to_state` |
| 49 | Low | done | A bare `except → frozenset()` with no log makes `redraw_scopes_or_defer` defer every scope: the fold-change controls silently stop working. | Fixed: the _current_centre_tab arm now routes through swallowed() like its sibling nine lines above, so a failed lookup is logged instead of returning an empty frozenset that silently defers every fold-change redraw. |
| 87 | High | done | An aborted run's `_after_csv` stamps the old threshold into the **new** dataset's `pipeline_info.json`. | Fixed: tab_view.py captures the dataset at launch and _after_csv refuses to persist (with a WARNING) when the loaded dataset changed. tests/test_smfish_threshold_stays_with_its_dataset.py — proved by neutralising the guard at HEAD (real AssertionError). |
| 110 | Medium | done | The Included/funnel readout is never refreshed on a dataset load, so it keeps describing the previous dataset. | Fixed: _kick_off_gating_after_load now calls cell_gating_refresh_included_count on the no-worker path (the commonest one). tests/test_gating_counts_follow_the_dataset.py, proved on origin/main with 'assert [] == [refresh]'. |
| 116 | Low | done | Modal dialogs opened with `parent=None`, against the repo's documented macOS rule. | Fixed: all three sites now pass ctx.window. tests/test_file_dialogs_are_parented.py is a new AST guard proved on origin/main (AssertionError naming all 3), with catch/clear cases in tests/test_guards_bite.py including the parent = None spelling a literal-only check would miss. |
| 155 | Low | queued | `font-size: 10px` in QSS and bare margin/spacing literals in the two modules whose header comments cite §8 for their *named* width constants. Invisible to `test_hardcoded | The QSS literal is fixed — widgets/selection_chip.py:129 now reads `font-size: {t.caption_size}px`. The spacing half is only partly swept: widgets/run_dashboard.py:43 names `_STAGE_ROW_SPACING_PX = 7` |## Not carried here

The 128 findings already closed and the 7 declined deliberately are not
listed here. They are recorded in the pull requests that closed them, and the
reconciliation that established the split is reproducible: it read every
finding against the tree, not against the report line numbers, which had all
moved.
