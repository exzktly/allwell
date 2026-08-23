# Work package W3 — 14 items

## Item 2 — severity Medium

**Reported location (line numbers are stale):** `well_viewer/features/image_browser/image_table/image_compose.py:244`

**Original finding:** `_export_binary_mask` catches every exception into `above_by_nid = {}` and renders an all-black silhouette — identical to "no cells above threshold". Same all-black result when the well's CSV rows do not cover the displayed `(fov, tp)`. The assignment body evades `test_no_silent_except.py`.

**Verified state at HEAD:** The exception half is fixed: image_compose.py:247-259 logs and returns None ('reported unrenderable') instead of `above_by_nid = {}`. The second half the finding names is untouched — review_image/collect.py:365-370 returns a dict of all-False for `df is None/empty`, `keys is None` and `sub.empty` (CSV rows not covering the displayed fov/tp), which _export_binary_mask:261-262 turns into the identical all-black silhouette, and those early returns fire before the '[binary-mask]' diagnostic log at :405.

## Item 12 — severity Medium

**Reported location (line numbers are stale):** `well_viewer/data/zip_members.py:215`

**Original finding:** A mid-scan exception returns partially filled dicts indistinguishable from a complete scan, under a "Result: N fluor…" line that reads as an inventory.

**Verified state at HEAD:** data/zip_members.py:223-236 now logs the partial counts and says 'images after the failure point are missing from it', so the log no longer reads as a complete inventory. But `scan_zip_members` still returns the same five bare dicts (signature unchanged at :154-168) with no partial flag, so the caller — and therefore the Review UI showing fewer frames — still cannot tell a partial scan from a complete one.

## Item 23 — severity Low

**Reported location (line numbers are stale):** `well_viewer/data/cache_projectors.py:79`

**Original finding:** A verdict that raises during re-projection leaves the previous `QC_Pass`/`Crop_Pass` values attached: plots filter by the old parameters while the UI shows the new ones.

**Verified state at HEAD:** well_viewer/data/cache_projectors.py:79-83 — `df[column] = verdict(df, lbl).astype(int)` inside try/except that only logs; when the verdict raises during a re-projection the frame keeps the previous QC_Pass/Crop_Pass values, so plots still filter by the old parameters while the UI shows the new ones. The `logger.exception` was already present before the remediation (added in 4801d1c2, pre-review); the file has no newer commit.

## Item 45 — severity Medium

**Reported location (line numbers are stale):** `well_viewer/features/batch_export/base_panel.py:224`

**Original finding:** An inherited per-member control mode shows `—` in the Control combo while the export normalizes anyway.

**Verified state at HEAD:** features/batch_export/base_panel.py:251-253 still inherits `_fc_control_mode` / `_fc_control_map` from the plot tab, and `_fc_state()` (:1137-1142) passes `control_mode` straight through to the export, while `_repopulate_fc_control_combo` (:1084-1125) offers only `_FC_NONE_LABEL` ('—') plus concrete refs and has no per-sample entry — so an inherited per-member mode leaves the combo reading '—' while bar_panel.py:484 / base_panel.py:1988 normalise via `build_per_member_controls`. The explanatory comment at :245-250 predates the review (added 2026-08-14, fee820f0) and addresses unnamed members, not the misleading combo.

## Item 56 — severity Medium

**Reported location (line numbers are stale):** `well_viewer/data/data_loading.py:911` + `image_discovery.py:559`

**Original finding:** An unparseable well label yields `well_token=None`, which **disables** the per-well filter in the loose-file fallback, so every well's images are accepted into that one well.

**Verified state at HEAD:** well_viewer/data/image_discovery.py:696-714 (find_well_images_and_masks) now logs a warning when a well label does not parse ('the loose-file scan cannot filter by well — images belonging to other wells may be included'), but the comment states outright 'It is not *fixed* here': _scan_raw_disk_files:585 still skips _raw_file_well_passes entirely when well_token is falsy, and data_loading.extract_well_token (line 964) still returns None. The cross-well contamination can still occur; only a log line marks it.

## Item 57 — severity Medium

**Reported location (line numbers are stale):** `well_viewer/features/image_browser/image_table/controller.py:789` + `image_compose.py:1070`

**Original finding:** An Overlay cell can never carry the `raw` uncorrected badge, so the status line counts uncorrected cells no badge ever identifies.

**Verified state at HEAD:** controller.py:787-798 returns from the OVERLAY_CHAN branch without ever calling mark_uncorrected, and image_compose.is_raw_fallback (line 1021-1036) keys on _binary_base(cell['chan']) — for an overlay cell that is the '⊕ OVERLAY' sentinel, which is never a recorded key, so _cell_title's tooltip is silent too. The status line at controller.py:1108 still counts the member-channel fallbacks recorded by frame_load.load_frame_array:365.

## Item 59 — severity Low

**Reported location (line numbers are stale):** `well_viewer/features/image_browser/image_table/image_compose.py:1070`

**Original finding:** A ratio cell's raw-fallback is recorded under the numerator/denominator channel, so it is never badged and is counted under a channel absent from the grid.

**Verified state at HEAD:** image_compose._load_ratio_array (line 497-514) loads through _load_array with num_chan/den_chan, and frame_load.load_frame_array:365 records raw_fallbacks under (well, that channel, tp, fov). is_raw_fallback keys on the cell's ratio token, so a ratio cell is never badged while its numerator/denominator channel — absent from the grid — carries the count.

## Item 60 — severity Low

**Reported location (line numbers are stale):** `well_viewer/features/image_browser/image_table/image_compose.py:758`

**Original finding:** `composite_column_rgb` calls `_row_display_opts(ctx, r)` with no channel, so it always loads the **raw** frame for a corrected channel. Dead today (re-export only).

**Verified state at HEAD:** image_compose.composite_column_rgb still calls `use_tophat = _row_display_opts(ctx, r)[0]` with no channel argument (image_compose.py:709); _row_display_opts (line 428) returns (False, False, '') when _channel_opts_for gets chan=None, so a corrected channel lazy-loads the raw frame. git log -L on the region shows it untouched since PR #782.

## Item 61 — severity Low

**Reported location (line numbers are stale):** `well_viewer/features/image_browser/image_table/image_compose.py:374`

**Original finding:** `_parse_lut`'s no-spec fallback applies column 0's channel LUT to the whole row. Reachable only with an empty `channel_opts`.

**Verified state at HEAD:** image_compose._parse_lut:411-416 still falls back to _shared_channel_lut(ctx, row_idx), which reads _row_channel -> cells[row_idx][0]['chan'] (line 356-361) — column 0's channel — even when a chan argument was supplied but has no channel_opts entry. Unchanged since PR #782.

## Item 62 — severity Low

**Reported location (line numbers are stale):** `well_viewer/features/image_browser/image_table/controller.py:795`

**Original finding:** Overlay cells install a hover readout keyed on the sentinel channel, so their pixel readout can never resolve and is always empty.

**Verified state at HEAD:** controller.py:1059-1061 passes cell['chan'] (the '⊕ OVERLAY' sentinel for an overlay cell) into _install_cell_events; _hover_array (line 476-500) then calls _load_array with that sentinel, which resolves nothing, so _hover_text returns '' and the readout stays empty for every overlay cell.

## Item 99 — severity Medium

**Reported location (line numbers are stale):** `well_viewer/features/batch_export/scatter_panel.py:793`

**Original finding:** The multi-timepoint aggregate `_draw` omits `color=data["color"]` that the single-tp sibling and the tab both pass.

**Verified state at HEAD:** well_viewer/features/batch_export/scatter_panel.py:844-849 (_run_scatter_agg_multi_tp_job's _draw) still calls ax.errorbar with label/marker/linestyle/capsize/alpha and no `color=data["color"]`, while the single-timepoint sibling at line 697-702 passes it.

## Item 100 — severity Medium

**Reported location (line numbers are stale):** `well_viewer/features/batch_export/base_panel.py:1747`, `bar_panel.py:274`

**Original finding:** val_col and metric are re-read per job while the channel token is snapshotted, across a `processEvents()` pump.

**Verified state at HEAD:** well_viewer/features/batch_export/base_panel.py:1754 snapshots _ch_selected once outside _run_group, while _run_group re-reads _val_col (line 1791) and _metric (line 1793) from the live combos on every job; bar_panel.py:276-278 does the same (_val_col/_metric re-read per job, _ch = _ch_selected). The runner pumps QApplication.processEvents() between jobs (base_panel.py:1641), so a combo change mid-run yields a mismatched channel/column pair.

## Item 102 — severity Low

**Reported location (line numbers are stale):** `well_viewer/features/image_browser/image_table/controller.py:835`

**Original finding:** The pre-rendered-RGB branch skips chrome and the raw badge and uses a different contrast than the export path for the same cell.

**Verified state at HEAD:** well_viewer/features/image_browser/image_table/controller.py:834-837: for a pre-rendered RGB cell (arr.ndim >= 3 and not NUC+SEG) the live path still does make_overlay_thumb(cropped, sz, sz, lo, hi) with the row's _parse_lut window, skipping draw_cell_chrome and mark_uncorrected — while export.py:452-465 sends every non-overlay cell through cell_display_rgb plus chrome plus mark_uncorrected and thumbnails at a fixed 0..255.

## Item 104 — severity Low

**Reported location (line numbers are stale):** `well_viewer/features/batch_export/base_panel.py:664`

**Original finding:** Conditional scope restore leaves the app in the batch's scope on one branch.

**Verified state at HEAD:** well_viewer/features/batch_export/base_panel.py:663-681 _app_val_col_scope still restores conditionally — `finally: if saved is not None: chan.active_val_col = saved` — so when the pre-existing active_val_col was None the app is left holding the batch's column. Its sibling _app_group_scope (line 738) restores unconditionally.

