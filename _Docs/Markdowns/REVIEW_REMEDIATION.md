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

Counts: **9 done · 31 reported (unverified) · 0 declined · 0 deferred · 2 outstanding** of 42.

The four groups are partitioned by file, not by theme, so that they can run at
the same time without touching the same lines. Where a theme's items live in
another group's files, the item moves to the group that owns the file.

## Resuming this work in a new session

Everything needed to pick this up is in this file and in git. Nothing
load-bearing lives in a scratch directory, because those do not survive a
session.

**Where the work is.** Branch `claude/codebase-review-report-5oiiam` (checked
out locally as `claude/fix-7-remainder`), stacked on top of the commits that
pull request #843 already carries. That pull request is open, green and
mergeable; the remediation commits on top of it are **not pushed**, deliberately,
because each push costs a CI run. Push once the parallel groups have landed, and
update the #843 description in the same breath — it currently describes only the
work up to `598ebb01` and says nothing about the 42-item remediation. The
version is already bumped for this pull request; do not bump it again.

**How to read the state.** The group tables below are the source of truth. A row
is finished only when its state column says `done` and its right-hand column
names the test that pins it. The counts line above is derived from the rows —
recompute it rather than editing it by hand:

```
python3 - <<'EOF'
import re
p = "_Docs/Markdowns/REVIEW_REMEDIATION.md"
s = open(p).read()
rows = re.findall(r"^\| \d+ \| [^|]* \| ([a-z ]+) \|", s, re.M)
n = {k: sum(1 for r in rows if r.strip() == k) for k in ("done", "declined", "deferred")}
line = (f"Counts: **{n['done']} done · {n['declined']} declined · "
        f"{n['deferred']} deferred · {len(rows) - sum(n.values())} outstanding** "
        f"of {len(rows)}.")
open(p, "w").write(re.sub(r"^Counts: .*$", line, s, count=1, flags=re.M))
print(line)
EOF
```

**The rule that produced this document.** CLAUDE.md section 6.1. Move the row in
the same commit as the fix; never in a batch afterwards. `done` requires a test
watched failing without the fix — `scripts/prove_guard.sh <test_file>` does that
against the base branch, and for a behaviour that does not exist on the base at
all, neutralise the fix at HEAD instead and watch the test go red. Read the
failure: an `ImportError` or a `TypeError` on a symbol the fix introduces
measured nothing, and a test that imports such a symbol has to be rewritten not
to.

**Who owns which files.** The four groups are partitioned by file so they can
run at once. Anything editing outside its own list collides:

| Group | Files |
|---|---|
| W1 | `well_viewer/persistence/**`, `well_viewer/core/channel_state_controller.py`, `well_viewer/domain/gating_state.py`, `well_viewer/features/home/groups/panel_view.py`, `well_viewer/features/saved_plots/tab_view.py` |
| W2 | `datasets/**`, `util/zip_write.py`, `WellPlateZipper.py` |
| W3 | `well_viewer/features/image_browser/**`, `well_viewer/features/batch_export/**`, `well_viewer/data/{image_discovery,zip_members,cache_projectors}.py` |
| lead | `well_viewer/features/smfish/**`, the fold-change modules, `well_viewer/features/heatmap/`, `well_viewer/features/barplot/**`, `widgets/**`, `theme.py`, the guards, the docs |

**Commits that are not evidence.** Several commits are titled `Checkpoint:` and
hold work the groups had not finished. They exist so an interrupted session
loses nothing; they prove nothing, and no row was moved for them. Judge each
item by the commit that names it and moves its row.

**Still to verify.** Every row a parallel group reports as fixed needs its claim
read against the code before its row moves. A group's report is a hypothesis
until the diff has been read. Each group's own working state — what it has
closed, what is half-done and what remains of it — is in
`_Docs/Markdowns/_remediation/W1.md`, `W2.md` and `W3.md`, written there
rather than reported in conversation so that it survives a session ending.
Those files are the groups' claims; this file is the record.

## Owed work found while doing this work

Recorded here rather than in a group's report, because a report is read once and
a tracker is read every time someone picks the work up.

1. **`well_viewer/domain/sample_definitions.py::save_to_pipeline_info` is still
   an unlocked read-modify-write of `pipeline_info.json`** — the other half of
   item 85. The two sites the item named now go through
   `persistence._io.update_json_atomic`, which re-reads and byte-compares
   immediately before the replace and re-applies on a concurrent write; this one
   was outside the owning group's files and was not converted. Keep the pre-v2
   backup inside or before the mutate when routing it through.
2. **`heatmap_layouts._collect_settings` overwrites the entire saved settings
   block with defaults when the Heat Map tab was never built in the session.**
   Found while fixing item 83 and deliberately left, because fixing only the
   timepoint half would leave one function holding two rules. Measured: on disk
   `{cmap: magma, scale_mode: Manual, vmin: 10.0, vmax: 900.0, repset_avg: True,
   log_scale: True, tp_value: 72.0}`, written back as `{cmap: '', scale_mode:
   Auto, vmin: None, vmax: None, repset_avg: False, log_scale: False, tp_value:
   None}`. Seven of the eight fields predate the item-83 change. One guard at the
   top of the function — decline to rewrite `hm.persisted_settings` when the tab
   was never built — closes all eight.
3. **`datasets/validate/validate_compute.py` should surface the well-spelling
   collision** that the copy tools now refuse (item 70), so a user hitting that
   refusal has somewhere to check the rest of the plate.
4. **ARCHITECTURE.md owes the deltas** each group lists in its own file. It was
   left untouched on purpose: three groups editing one document at once would
   have collided, and the version-literal guard makes such a conflict expensive
   to resolve.

5. **`ScanResult.partial` reaches no user-facing surface.** Item 12 makes a
   truncated scan say so in its own return value; nothing yet reads it —
   `data/frame_load.py` and `features/image_browser/review_image/renderer.py`
   are the two surfaces that should. Until they do, the honest verdict exists
   and the user still cannot see it.
6. **`project_verdict_column`'s failure list has no reader** (item 23). The QC
   and Crop controllers should surface which wells' verdicts could not be
   re-projected.
7. **`review_image/renderer.py` still coerces a `None` threshold map to `{}`** —
   the same "unmeasured looks like empty" conflation as item 2, one surface
   over. No regression; it was simply outside the item's scope.
8. **`scatter_panel`'s four `_run_scatter_*_job` methods read every axis combo
   and `self._scatter_mode` live, per job** (the neighbourhood of item 100). A
   mode change mid-run splits one manifest across two export kinds. Fixing it
   needs a snapshot threaded through four signatures, so it is recorded rather
   than half-swept.
9. **`composite_column_rgb` has no ratio or overlay handling at all** (found
   around item 60). A real gap, though the function is presently dead in the app
   — a re-export kept only for its import.
10. **`_WELL_TOKEN_IN_TEXT_RE` reads a FOV token `f1` as the well `F01`**
    (`data/image_resolver.py`). Pre-existing and unrelated to any item here.

## An unmeasured risk introduced by item 31

Making the heatmap agree with Bar and Line replaced one pooled `aggregate_group`
call per cell with one `aggregate_well` call **per well in the cell**. That is
correct — the mean of each well's mean cannot be computed any other way — but
`_compute_global_range` calls `_cell_value` on the order of timepoints × rows ×
columns on every redraw, and the helper the change deleted carried a comment
saying full-frame concatenation in that loop had been the bottleneck on
timepoint-slider scrubs.

**This has not been measured.** A replicate set of eight wells now does eight
aggregations where it did one, and whether the per-well cache upstream absorbs
that is unknown. Scrub the timepoint slider on a large plate with replicate
averaging on, and if it drags, the fix is to memoise per well and timepoint
rather than to go back to pooling — pooling is the wrong number.

## A lesson this work paid for

Checkpointing a group's in-flight tree into `HEAD` silently invalidated the
other groups' guard proofs. `scripts/prove_guard.sh` compares against a base
ref, and once a checkpoint carried the fixes into the history, the base
contained them too — so proofs came back "passed on the base", which reads as
*the rule already held* rather than as *this measured nothing*. Both W2 and W3
hit it; W2 re-based its proofs on the last commit before its package began, and
W3 switched to mutating the fix at `HEAD` instead.

If groups are again run in parallel with checkpoint commits between them, either
prove against a ref taken before any of the work started, or mutate the fix in
place. This is CLAUDE.md §7.3 #1 — a harness that fails open will happily
confirm anything — arriving through a route nobody had written down.

## A note on the commit history

Several commits here were staged with `git add -A` while more than one group was
writing, so their contents are wider than their messages. In particular the
commit titled *Destructive dataset operations* also carries persistence work
belonging to another group, and the `Checkpoint:` commits carry whatever was in
the tree at that moment. The history was already pushed when this was noticed and
is not being rewritten. Read the group status files in `_remediation/` for what
each group actually did; the commit messages are reliable about intent and not
about extent.

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
| 80 | Medium | reported | `except OSError: pass` plus an absent `<well>_out.zip` makes the v2 staleness fingerprint fail open, and the compensating warning is gated on `file_version < 2`, so a v2  | well_viewer/persistence/cell_overrides.py unchanged since PR #782: lines 64-65 and 110-111 still `except OSError: pass`; line 161 `if current_fp and stored_fp != current_fp` still fails open when the  |
| 81 | Medium | reported | `VIEW_DOMAINS` claims `line_order` but `reset_view` never saves it, so the stale order returns on the next load and permanently reorders `selections`. The module's own pr | well_viewer/persistence/reset_scopes.py:269 still claims "line_order" in VIEW_DOMAINS, but reset_view (line 274) only calls view_state.reset, _save(ctx,"heatmap_layouts") and _save(ctx,"multivariate") |
| 82 | Medium | reported | `absent_wells` / `absent_solo` survive a dataset swap and are reassigned only inside `restore()`, which is skipped when the new dataset has no `view_state` section: datas | ctx.runtime.view_state.absent_wells / absent_solo are assigned only in view_state.restore (lines 462, 485) and cleared only in view_state.reset (lines 1088-1089). load_controller.begin_load (lines 400 |
| 83 | Medium | reported | The heatmap timepoint is persisted as a combo **index**, which §9.7's add-timepoints workflow invalidates. | well_viewer/persistence/heatmap_layouts.py:37 still persists `"tp_index": int(tp_cb.currentIndex())` and line 175-182 restores it by clamping to the live item count — an index, not a timepoint value,  |
| 84 | Medium | reported | `well_labels` are hydrated pruned to loaded wells and then autosaved back, erasing labels for wells absent from a subset load. `view_state` solved this with `absent_wells | well_viewer/persistence/sample_definitions.py:216-219 hydrates well_labels only for tokens in ctx.state.dataset.well_paths, and autosave_to_pipeline_info (line 62) rebuilds the block from ds.well_labe |
| 85 | Medium | reported | Unlocked read-modify-write of `pipeline_info.json` from a viewer sharing a window with a running Analyze pass; a lost update reverts `available_timepoints` / `fluor_token | well_viewer/persistence/smfish.py:124-160 still does an unlocked read-modify-write of pipeline_info.json (json.loads then atomic_write_json), as does domain/gating_state.py:200-238 (save_gating_to_pip |
| 86 | Medium | reported | A bare mid-function `return` skips `apply_range` and the whole gating-hydration tail, so `threshold_min/max` keep the previous dataset's range (`begin_load` clears only t | well_viewer/core/channel_state_controller.py:187-188 still `if all_vals.size == 0: return` inside _recalculate_threshold, skipping channel_state.apply_range (line 195) and the whole cell-gating hydrat |
| 88 | Low | reported | A plate edit rewrites a group's membership from the plate alone, permanently dropping wells the current dataset does not hold — and autosaves that. | well_viewer/features/home/groups/panel_view.py:81-91 builds `new` as `plate.selectedWellIds() ∩ well_paths` and calls sel_set_composition(ctx, sid, new); selection_controller.sel_set_composition (line |
| 90 | Medium | reported | `_confirm_clear` falls through to clearing when the confirmation dialog raises — an unconfirmed destructive wipe of the gallery and its PNGs. | well_viewer/features/saved_plots/tab_view.py:124-134 _confirm_clear still has `except Exception: swallowed()  # no dialog (e.g. headless) — fall through to clearing` followed by an unconditional C.cle |

### Group W2

| # | Sev | State | Item | Where it stands |
|---|---|---|---|---|
| 18 | Medium | reported | The "did anything land?" check is skipped exactly when the filtered archive came out empty — "nothing to compare against" conflated with "nothing landed". | datasets/subset/apply.py:243 still reads `if fname and kept_in_names and fname not in kept_in_names:` — the `and kept_in_names` truthiness test skips the 'did anything land?' check exactly when the fi |
| 67 | High | reported | `_remap._rename` discards `rewrite_member_field`'s `was_rewritten` flag, so a member failing the structural gate keeps dataset 2's original well token inside the destinat | datasets/merge_plates/apply.py:84-88 _remap._rename still returns _remap_well_field(...)[0] and drops the was_rewritten flag; rewrite_member_field (datasets/_common/member_names.py:119-131) returns th |
| 68 | Medium | reported | In-place mode zips and unlinks the TIFs of **every** well-named subfolder under the source, including pre-existing ones; the preview is non-recursive and loose-TIF-only.  | datasets/import_loose/apply.py:217-235 _zip_well_folders still iterates every directory entry of the folder whose name canonicalises to a well and zips/unlinks its TIFs, with no distinction between fo |
| 69 | Medium | reported | `row_matches` reads the `timepoint_hours` **column** and `member_matches` reads the **filename** token. A legacy CSV with a blank `timepoint_hours` loses every row while  | datasets/subset/subset_compute.py:215-225 row_matches still reads float(row['timepoint_hours']) and returns False on a blank/unparsable value, while member_matches (line 182-212) parses the filename's |
| 70 | Medium | reported | Two containers canonicalising to the same well (`A1.zip` + `A01.zip`) collapse by plain dict overwrite in glob order. The `out_csvs` sibling documents its identical overw | datasets/_common/dataset_paths.py:187-205 in_containers still assigns containers[well] = zip_path in glob order with no collision detection or warning, so A1.zip and A01.zip collapse silently; the sib |
| 73 | Low | reported | `replace_folder_with_archive`'s `resolve()` identity conflates symlink and hardlink aliases in both directions: an archived symlink is reported as `kept`, and a second na | util/zip_write.py:153-168 replace_folder_with_archive still builds archived_resolved from Path(p).resolve() and compares p.resolve() for each walked file; there is no st_ino/samefile handling and no m |
| 74 | Medium | reported | `copy2`/`move` silently overwrite same-basename files under `--recursive`; the progress line counts sources, not survivors. | WellPlateZipper.py:186-195 folder_well still does place(f, os.path.join(well_dir, os.path.basename(f))) for shutil.copy2/shutil.move with no existence check, then prints f'{well}: {len(files)} files'  |
| 76 | Low | reported | The `seg_fp` restamp is set only inside `with swallow(...)`; on a failed stat the entry silently keeps the **source** dataset's fingerprint and the carried-over override  | All three sites still assign inside the suppression: datasets/subset/apply.py:121-122, datasets/merge_plates/apply.py:271-272 and datasets/concatenate/apply.py:438-439 each do `with swallow(f"stat {zi |

### Group W3

| # | Sev | State | Item | Where it stands |
|---|---|---|---|---|
| 2 | Medium | reported | `_export_binary_mask` catches every exception into `above_by_nid = {}` and renders an all-black silhouette — identical to "no cells above threshold". Same all-black resul | The exception half is fixed: image_compose.py:247-259 logs and returns None ('reported unrenderable') instead of `above_by_nid = {}`. The second half the finding names is untouched — review_image/coll |
| 12 | Medium | reported | A mid-scan exception returns partially filled dicts indistinguishable from a complete scan, under a "Result: N fluor…" line that reads as an inventory. | data/zip_members.py:223-236 now logs the partial counts and says 'images after the failure point are missing from it', so the log no longer reads as a complete inventory. But `scan_zip_members` still  |
| 23 | Low | reported | A verdict that raises during re-projection leaves the previous `QC_Pass`/`Crop_Pass` values attached: plots filter by the old parameters while the UI shows the new ones. | well_viewer/data/cache_projectors.py:79-83 — `df[column] = verdict(df, lbl).astype(int)` inside try/except that only logs; when the verdict raises during a re-projection the frame keeps the previous Q |
| 45 | Medium | reported | An inherited per-member control mode shows `—` in the Control combo while the export normalizes anyway. | features/batch_export/base_panel.py:251-253 still inherits `_fc_control_mode` / `_fc_control_map` from the plot tab, and `_fc_state()` (:1137-1142) passes `control_mode` straight through to the export |
| 56 | Medium | reported | An unparseable well label yields `well_token=None`, which **disables** the per-well filter in the loose-file fallback, so every well's images are accepted into that one w | well_viewer/data/image_discovery.py:696-714 (find_well_images_and_masks) now logs a warning when a well label does not parse ('the loose-file scan cannot filter by well — images belonging to other wel |
| 57 | Medium | reported | An Overlay cell can never carry the `raw` uncorrected badge, so the status line counts uncorrected cells no badge ever identifies. | controller.py:787-798 returns from the OVERLAY_CHAN branch without ever calling mark_uncorrected, and image_compose.is_raw_fallback (line 1021-1036) keys on _binary_base(cell['chan']) — for an overlay |
| 59 | Low | reported | A ratio cell's raw-fallback is recorded under the numerator/denominator channel, so it is never badged and is counted under a channel absent from the grid. | image_compose._load_ratio_array (line 497-514) loads through _load_array with num_chan/den_chan, and frame_load.load_frame_array:365 records raw_fallbacks under (well, that channel, tp, fov). is_raw_f |
| 60 | Low | reported | `composite_column_rgb` calls `_row_display_opts(ctx, r)` with no channel, so it always loads the **raw** frame for a corrected channel. Dead today (re-export only). | image_compose.composite_column_rgb still calls `use_tophat = _row_display_opts(ctx, r)[0]` with no channel argument (image_compose.py:709); _row_display_opts (line 428) returns (False, False, '') when |
| 61 | Low | reported | `_parse_lut`'s no-spec fallback applies column 0's channel LUT to the whole row. Reachable only with an empty `channel_opts`. | image_compose._parse_lut:411-416 still falls back to _shared_channel_lut(ctx, row_idx), which reads _row_channel -> cells[row_idx][0]['chan'] (line 356-361) — column 0's channel — even when a chan arg |
| 62 | Low | reported | Overlay cells install a hover readout keyed on the sentinel channel, so their pixel readout can never resolve and is always empty. | controller.py:1059-1061 passes cell['chan'] (the '⊕ OVERLAY' sentinel for an overlay cell) into _install_cell_events; _hover_array (line 476-500) then calls _load_array with that sentinel, which resol |
| 99 | Medium | reported | The multi-timepoint aggregate `_draw` omits `color=data["color"]` that the single-tp sibling and the tab both pass. | well_viewer/features/batch_export/scatter_panel.py:844-849 (_run_scatter_agg_multi_tp_job's _draw) still calls ax.errorbar with label/marker/linestyle/capsize/alpha and no `color=data["color"]`, while |
| 100 | Medium | reported | val_col and metric are re-read per job while the channel token is snapshotted, across a `processEvents()` pump. | well_viewer/features/batch_export/base_panel.py:1754 snapshots _ch_selected once outside _run_group, while _run_group re-reads _val_col (line 1791) and _metric (line 1793) from the live combos on ever |
| 102 | Low | reported | The pre-rendered-RGB branch skips chrome and the raw badge and uses a different contrast than the export path for the same cell. | well_viewer/features/image_browser/image_table/controller.py:834-837: for a pre-rendered RGB cell (arr.ndim >= 3 and not NUC+SEG) the live path still does make_overlay_thumb(cropped, sz, sz, lo, hi) w |
| 104 | Low | reported | Conditional scope restore leaves the app in the batch's scope on one branch. | well_viewer/features/batch_export/base_panel.py:663-681 _app_val_col_scope still restores conditionally — `finally: if saved is not None: chan.active_val_col = saved` — so when the pre-existing active |

### Group ME

| # | Sev | State | Item | Where it stands |
|---|---|---|---|---|
| 1 | High | done | A well whose spot-counting pass raises is logged and then dropped from `counts`; `counts.get(k, 0)` writes a full column of `0` into that well's CSV and `done_cb` still r | Fixed: worker.py records failed wells and _write_counts_to_csvs skips them, so they keep their previous counts and land in the run's shortfall instead of a fabricated zero column. tests/test_smfish_failed_well_is_not_zero_filled.py — proved by neutralising the skip at HEAD (real AssertionError). |
| 31 | Medium | done | The replicate-set average is a pool-of-cells mean, not the mean-of-per-well-means Bar, Line and Stats use. Possibly deliberate; undocumented either way. | Fixed on the user's decision: the averaging metrics (Mean, Mean-all, Fraction, Ratio) now reduce group_replicate_stats through the shared replicate_mean, matching Bar/Line exactly; the replicate unit follows the per-FOV toggle as the plots do. METRIC_COUNT stays the pooled total (a total, not an average) and says so. The dead _pool_filtered_mean and the unused ratios parameter are gone. tests/test_heatmap_matches_bar_and_line.py — proved on origin/main, which returns 2.8 where the bar shows 6.0. |
| 34 | Medium | done | The per-cell Events panel ignores `bar_err_settings`, so "None" and "95 % CI" apply to grouped mode only. | Fixed: draw_per_well_n_bars now reads bar_err_settings(ctx) — the same accessor the aggregate path uses — so the Show mode and error-bar type govern the per-cell Events panel, and 95 % CI scales by 1.96. tests/test_per_cell_events_honours_stats_controls.py, proved on origin/main. |
| 39 | Low | done | Docstring says epsilon is *added*; the resolver *clamps* (`np.maximum(den, epsilon)`) — different numbers near and below zero on background-subtracted channels. | Swept: the clamp-not-add rule is now stated consistently in domain/ratio_models.py, data/data_loading.py, the Ratios tab intro text, and README_WellViewer.md. The manual was a fourth site the finding did not name. |
| 47 | Low | queued | `tick_labels` de-duplicates by reference and `distinct_controls` by resolved wells, so a one-well control set plus that well's own token gives an axis title naming one co | The two de-duplications still key on different things and both are pinned as such: fold_change_pairing.py:137-146 (`distinct_controls`) skips on `rec.wells in seen`, while :535-543 (`tick_labels`) bui |
| 48 | Medium | queued | One row edit rebuilds the table twice, and `setCellWidget` destroys the combo whose slot is still on the stack — roughly 192 combos per edit on a 96-member plate. | core/fold_change_pairing_view.py:225-229 — `_write` calls `set_fold_change_state(ctx, control_map=mapping, initiating_scope='')` and then `_populate()`; with no scope skipped, `_sync_widgets_to_state` |
| 49 | Low | done | A bare `except → frozenset()` with no log makes `redraw_scopes_or_defer` defer every scope: the fold-change controls silently stop working. | Fixed: the _current_centre_tab arm now routes through swallowed() like its sibling nine lines above, so a failed lookup is logged instead of returning an empty frozenset that silently defers every fold-change redraw. |
| 87 | High | done | An aborted run's `_after_csv` stamps the old threshold into the **new** dataset's `pipeline_info.json`. | Fixed: tab_view.py captures the dataset at launch and _after_csv refuses to persist (with a WARNING) when the loaded dataset changed. tests/test_smfish_threshold_stays_with_its_dataset.py — proved by neutralising the guard at HEAD (real AssertionError). |
| 110 | Medium | done | The Included/funnel readout is never refreshed on a dataset load, so it keeps describing the previous dataset. | Fixed: _kick_off_gating_after_load now calls cell_gating_refresh_included_count on the no-worker path (the commonest one). tests/test_gating_counts_follow_the_dataset.py, proved on origin/main with 'assert [] == [refresh]'. |
| 116 | Low | done | Modal dialogs opened with `parent=None`, against the repo's documented macOS rule. | Fixed: all three sites now pass ctx.window. tests/test_file_dialogs_are_parented.py is a new AST guard proved on origin/main (AssertionError naming all 3), with catch/clear cases in tests/test_guards_bite.py including the parent = None spelling a literal-only check would miss. |
| 155 | Low | done | `font-size: 10px` in QSS and bare margin/spacing literals in the two modules whose header comments cite §8 for their *named* width constants. Invisible to `test_hardcoded | Fixed the named sites: run_dashboard:458 now uses _STAGE_ROW_SPACING_PX (it sat beside a constant of the same value), and plate_progress's four bare spacings are either theme.Spacing.sm or a named off-scale constant carrying its justification. The guard's blind spots — setSpacing/setContentsMargins (147 sites) and QSS font-size — are recorded in test_hardcoded_tokens.py as a known gap with the reason, rather than left to be rediscovered. |

## Not carried here

The 128 findings already closed and the 7 declined deliberately are not listed
here. They are recorded in the pull requests that closed them — #834 through
#841 and #843 — and each carries its evidence in the commit that closed it.

The reconciliation that established the split checked all 177 against the tree
rather than against the report, whose line numbers had all moved. Its full
per-finding ledger (verdict, evidence at the tree, and the guard test where one
exists) was delivered to the user as a file rather than committed, to keep this
directory to the three permanent documents plus this tracker. It is
reproducible: read each finding of the original review against the current code,
classify it fixed / partial / declined / open with the file and line that
settles it, and do not trust the report's line numbers.

The split it found was 128 fixed, 5 partial, 7 declined, 37 open. The 5 partial
and 37 open are the 42 rows above.
