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
| `disputed` | A group reported it fixed, but the claim did not survive verification; what is still wrong is in the right-hand column |
| `deferred` | Real, but deliberately postponed; the reason and the trigger to revisit are recorded |

## Status

| Group | Scope | Owner | Items | State |
|---|---|---|---|---|
| W1 | Persistence and cross-dataset leakage (theme 6) | worker | 9 | **in progress** |
| W2 | Destructive dataset operations (theme 5) | worker | 8 | **in progress** |
| W3 | Image identity and batch export (themes 4, 7) | worker | 14 | **in progress** |
| ME | smFISH, fold change, stats, guards, docs | lead | 11 | **in progress** |

Counts: **40 done · 0 declined · 0 deferred · 2 outstanding** of 42.

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

## Coverage gaps inside rows that are `done`

Verification promoted 29 rows by reading each diff, running the named test, and
mutating the fix in place to watch the test go red. Six of those rows are
genuinely closed in code while their guard is narrower than the rule it stands
for. None is a live defect; each is a place a future regression would pass
unnoticed.

1. **67** — the archive-side and CSV-side refusals are mutually redundant for
   the fixture, so killing either alone stays green. A zip member with no CSV row
   is covered by code and by no test.
2. **12** — the `derive`-at-the-sort half of `find_well_images_and_masks` is
   unpinned; reverting it leaves thirteen tests green, because the test drives
   `_merge_scan` directly.
3. **73** — reverting `p.is_dir() and not p.is_symlink()` to `p.is_dir()` stays
   green: no fixture holds a symlink to a directory.
4. **2** — the `image_compose` consumer branch (`None` meaning unrenderable) was
   confirmed by reading only.
5. **100** — the AST guard scans `base_panel.py` and `bar_panel.py` and cannot
   see `scatter_panel.py`, where the recorded sibling still lives (owed-work 8).
   The guard file does not state that exclusion, which is the half that makes a
   scan surface a rule rather than an accident.
6. **68** — `existing_well_folders` omits `_zip_well_folders`' "well already has
   a matching zip, skip it" rule, so it can announce a folder the run will not
   touch.

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

It recurred after being written down. The commit titled *Defer only the pairing
table's rebuild* also carries the verification of all 31 reported rows — a
separate body of work, by a different agent, swept in by the same `git add -A`.
Knowing the failure mode did not prevent it; staging by path would have.

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
| 80 | Medium | done | `except OSError: pass` plus an absent `<well>_out.zip` makes the v2 staleness fingerprint fail open, and the compensating warning is gated on `file_version < 2`, so a v2  | Verified by reading the diff and by mutation. `well_viewer/persistence/cell_overrides.py` now routes both fingerprint `stat` failures through `swallowed(...)` (in `_well_zip_fingerprints` and `_fingerprints_for_data_dir`), counts a stored fingerprint whose well has no `<well>_out.zip` as `unverifiable` and warns rather than falling through the comparison, and no longer gates the no-fingerprint warning on `file_version < 2`. Both behavioural halves bite: replacing `unverifiable += 1` with `pass` fails `test_stored_fingerprint_with_no_zip_on_disk_is_reported` with a real `AssertionError` ("passed the staleness guard silently; records were []"), and re-gating the warning on `file_version < 2` fails `test_v2_row_without_a_fingerprint_warns` the same way. tests/test_cell_overrides_unverified_fingerprints.py, 3 passed. |
| 81 | Medium | done | `VIEW_DOMAINS` claims `line_order` but `reset_view` never saves it, so the stale order returns on the next load and permanently reorders `selections`. The module's own pr | Verified by reading the diff and by mutation. `reset_scopes.reset_view` now calls `_save(ctx, "line_order")` immediately after `view_state.reset(ctx)`. Deleting that save fails both tests ("reset_view claims ['view_state', 'line_order', 'heatmap_layouts', 'multivariate'] but wrote ['view_state', 'heatmap_layouts', 'multivariate']"), and moving the save to before the reset — the ordering half — fails `test_the_line_order_written_is_the_cleared_one` with the stale order in the message. tests/test_reset_view_persists_every_domain.py, 2 passed. |
| 82 | Medium | done | `absent_wells` / `absent_solo` survive a dataset swap and are reassigned only inside `restore()`, which is skipped when the new dataset has no `view_state` section: datas | Verified by reading the diff and by mutation. `view_state.forget_absent_wells` clears both stashes and is called at the top of `load_from_data_dir`, before the section is read, so every early return is covered. Deleting that call fails `test_a_plate_with_no_saved_view_does_not_inherit_the_previous_plate_s_wells` with `AssertionError: plate A's wells were written into plate B's saved view: ['A03', 'A04', 'B01']`; the X14 case still passes, so the clear did not break the behaviour it guards. tests/test_view_state_absent_wells_dataset_swap.py, 2 passed. |
| 83 | Medium | done | The heatmap timepoint is persisted as a combo **index**, which §9.7's add-timepoints workflow invalidates. | Verified by reading the diff and by mutation. `heatmap_layouts._collect_settings` writes `tp_value` beside `tp_index` and `_restore_timepoint` matches the saved value against the plate's live timepoints with `math.isclose`, declining (with a log line) when the plate does not have it rather than falling back to the index. Forcing the index path fails two tests with real `AssertionError`s — "the heat map came back on a different timepoint than the one saved (24 h)" and "a saved timepoint this plate does not have must leave the combo on its default, not on index 2". The `_collect_settings` defaults-overwrite defect found alongside it is separate and is owed-work item 2 below. tests/test_heatmap_timepoint_persisted_by_value.py, 4 passed. |
| 84 | Medium | done | `well_labels` are hydrated pruned to loaded wells and then autosaved back, erasing labels for wells absent from a subset load. `view_state` solved this with `absent_wells | Verified by reading the diff and by mutation. `persistence/sample_definitions._labels_to_write(ds, keep_absent=…)` merges the on-disk labels of tokens outside `dataset.well_paths` back into the block; both `autosave_to_pipeline_info` and the loud `save_to_pipeline_info` use it, and the reset scope calls the new `autosave_forgetting_absent_labels` so a deliberate wipe is not undone by the merge. Forcing the merge to return the live map only fails `test_a_subset_load_that_autosaves_keeps_the_other_wells_labels` with `AssertionError: labels outside the subset were erased`. tests/test_sample_definitions_absent_labels.py, 3 passed. |
| 85 | Medium | disputed | Unlocked read-modify-write of `pipeline_info.json` from a viewer sharing a window with a running Analyze pass; a lost update reverts `available_timepoints` / `fluor_token | Partial. The two sites the finding named are fixed and proved: `persistence/smfish.py` (`save_threshold`, `clear_thresholds`) and `domain/gating_state.py::save_gating_to_pipeline_info` both route through the new `persistence/_io.update_json_atomic`, whose re-read-and-byte-compare before `os.replace` bites — neutralising the comparison fails all three cases of tests/test_pipeline_info_concurrent_update.py with "the run's fluor_tokens were reverted by the threshold write: ['gfp']". But the reported defect can still occur: `well_viewer/domain/sample_definitions.py::save_to_pipeline_info` is still an unlocked read-modify-write of `pipeline_info.json` (`json.loads` → mutate → `write_json_atomic`) and it is on the viewer's debounced sample-definitions autosave path, so a viewer write can still revert a running pass's `fluor_tokens` / `available_timepoints`. That is owed-work item 1 below; this row cannot close until it is converted. |
| 86 | Medium | done | A bare mid-function `return` skips `apply_range` and the whole gating-hydration tail, so `threshold_min/max` keep the previous dataset's range (`begin_load` clears only t | Verified by reading the diff and by mutation. The `all_vals.size == 0` branch of `channel_state_controller._recalculate_threshold` now calls the new `reset_threshold_range(channel_state)` — which reads the defaults off a fresh `ChannelState` rather than hard-coding them — and logs before returning. Deleting the call fails `test_a_dataset_with_no_values_does_not_inherit_the_previous_range` with `assert (120.0, 48000.0) == (0.0, 1.0)`. The kept `return` is sound: `load_controller` (line 599) hydrates gating separately through `app._load_gating_from_pipeline_info()` in the same load. tests/test_threshold_range_no_cross_dataset_leak.py, 3 passed. |
| 88 | Low | done | A plate edit rewrites a group's membership from the plate alone, permanently dropping wells the current dataset does not hold — and autosaves that. | Verified by reading the diff and by mutation. `groups/panel_view._commit_plate_to_current` now carries through the group's wells outside `dataset.well_paths` instead of rebuilding membership from the plate, with a new `_plate_order` key that tolerates a token `parse_rc` cannot read. Setting the carried-through list to empty fails `test_adding_a_well_keeps_the_members_outside_this_dataset` with `AssertionError: the group members this dataset does not hold were deleted by a plate edit: ['A01', 'A02']`. The tests drive the real `WellPlateSelector` and emit `selectionDragFinished`, so they enter through the closure that held the bug rather than one step past it. tests/test_groups_panel_keeps_absent_wells.py, 3 passed. |
| 90 | Medium | done | `_confirm_clear` falls through to clearing when the confirmation dialog raises — an unconfirmed destructive wipe of the gallery and its PNGs. | Verified by reading the diff and by mutation. `saved_plots/tab_view._confirm_clear` returns after `swallowed(...)` when the confirmation dialog raises instead of falling through to `C.clear_saved_plots(ctx)`. Turning that refusal back into consent fails `test_a_dialog_that_cannot_open_clears_nothing` with `AssertionError: the gallery was wiped without a confirmation, because the confirmation dialog raised`; the answering-no and answering-yes controls both still pass. tests/test_saved_plots_clear_needs_confirmation.py, 3 passed. |

### Group W2

| # | Sev | State | Item | Where it stands |
|---|---|---|---|---|
| 18 | Medium | done | The "did anything land?" check is skipped exactly when the filtered archive came out empty — "nothing to compare against" conflated with "nothing landed". | Verified by reading the diff and by mutation. `subset/apply.py` makes `kept_in_names` a `frozenset | None` — `None` for no raw-input container, an empty set for a container the filter emptied — and the row check reads `kept_in_names is not None`. Restoring the truthiness test (`and kept_in_names`) fails `test_a_row_is_checked_even_when_the_filter_emptied_the_well_s_archive` with `Failed: DID NOT RAISE SubsetError`, and the two controls (a well whose archive kept members, a well with no container at all) stay green. tests/test_subset_verification_gaps.py, 7 passed. |
| 67 | High | done | `_remap._rename` discards `rewrite_member_field`'s `was_rewritten` flag, so a member failing the structural gate keeps dataset 2's original well token inside the destinat | Verified by reading the diff and by mutation. `merge_plates/apply._remap._rename` now keeps `rewrite_member_field`'s `was_rewritten` flag, `_carries_a_well_token` decides which members the question may be asked of (not OS junk, a catalogued image extension), and `_refuse_unrewritten` refuses from both the archive branches and the CSV branch. Neutralising `_refuse_unrewritten` fails three tests with `Failed: DID NOT RAISE MergePlatesError`; breaking the entitlement predicate refuses on `.DS_Store` and fails the pass-through control. One coverage note, not a defect: the archive-side and CSV-side captures are each redundant for the parametrised fixture (it adds the stray member to both the zip and the CSV), so killing either one alone leaves the file green — a zip member with no CSV row is covered by the code but by no test. tests/test_merge_plates_unrewritten_members.py, 4 passed. |
| 68 | Medium | done | In-place mode zips and unlinks the TIFs of **every** well-named subfolder under the source, including pre-existing ones; the preview is non-recursive and loose-TIF-only.  | Verified by reading the diff and by mutation. The in-place scope is announced rather than narrowed: `import_loose_compute.existing_well_folders` walks the same shape `apply._zip_well_folders` walks, and the count reaches the preview, the readout, the confirmation dialog, the run log and the run summary. Dropping the summary sentence fails `test_the_run_summary_names_the_extra_scope` with `assert 'C03' in 'Grouped loose TIFs in … into 4 per-well zip(s).'`, and dropping the `refresh_readout()` that `_on_in_place_toggled` gained fails `test_the_page_states_the_extra_scope_once_the_destructive_mode_is_armed` — that second test ticks the real checkbox, which is the §7.6 shape. One small over-statement left: `existing_well_folders` does not apply `_zip_well_folders`' "a well that already has a matching zip is left alone" rule, so such a folder is named as one the run will zip when the run will skip it. tests/test_import_loose_scope.py, 5 passed. |
| 69 | Medium | done | `row_matches` reads the `timepoint_hours` **column** and `member_matches` reads the **filename** token. A legacy CSV with a blank `timepoint_hours` loses every row while  | Verified by reading the diff and by mutation. `subset_compute.row_hours` resolves a row's timepoint from the numeric column, then the `timepoint` label column, then the `filename` column's own token — the value `member_matches` reads — and `row_verdict` adds the third state `ROW_UNJUDGEABLE` that `apply._verify` names as a cause. Removing the label and filename fallbacks fails two tests (`row_matches({… 'timepoint_hours': ''})` returning False, and the run raising `SubsetError` on a legacy CSV); separately, removing `schema=plan.schema, channels=plan.channels` from the copy path's `row_verdict` call fails `test_the_filename_column_is_the_last_resort_for_a_row_s_timepoint`, so the threading through the real caller is pinned too. tests/test_subset_verification_gaps.py, 7 passed. |
| 70 | Medium | done | Two containers canonicalising to the same well (`A1.zip` + `A01.zip`) collapse by plain dict overwrite in glob order. The `out_csvs` sibling documents its identical overw | Verified by reading the diff and by mutation. `dataset_paths.well_spelling_collisions` asks the folder directly for every well claimed by more than one same-role file (two `in/` zips, two `in/` folders, two `_out.zip`, two `out/` folders, two CSVs under one prefix) and each copy tool refuses through its own `_refuse_well_collisions` before writing anything. Neutralising the detector fails four tests with `Failed: DID NOT RAISE` across Subset, Merge Plates and Concatenate; dropping only Subset's refusal fails only Subset's two, so the three tools are pinned separately. The two documented non-collisions (a zip beside a same-well folder, two CSV prefixes) stay green. tests/test_well_spelling_collisions.py, 7 passed. |
| 73 | Low | done | `replace_folder_with_archive`'s `resolve()` identity conflates symlink and hardlink aliases in both directions: an archived symlink is reported as `kept`, and a second na | Verified by reading the diff and by mutation. `zip_write._entry_identity` is now `(os.path.realpath(path.parent), path.name)` rather than `Path.resolve()`, so a symlink and its target are two entries. Restoring `resolve()` fails both directions with real `AssertionError`s — `assert () == ('alias.tif',)` (a link the archive holds no member for was letting the folder be removed) and `assert ('linked.tif', 'notes.txt') == ('notes.txt',)` (an archived link reported back as kept). Coverage note: the walk's `p.is_dir() and not p.is_symlink()` change is not pinned — reverting it to `p.is_dir()` leaves all six tests green, because no fixture holds a symlink to a directory. tests/test_zip_write_symlink_aliases.py, 6 passed. |
| 74 | Medium | done | `copy2`/`move` silently overwrite same-basename files under `--recursive`; the progress line counts sources, not survivors. | Verified by reading the diff and by mutation. `WellPlateZipper.folder_well` is first-write-wins: an existing different target is recorded as a collision, warned about on stderr with both paths, and left where it is, and the progress line prints survivors rather than sources. Removing the existence check fails three tests, including the data-loss proof outright (`assert {b'first', b'other', b'second'} <= {b'first', b'other'}` — one image's bytes existed nowhere after a move); printing `len(files)` again fails the honest-count assertion. tests/test_zipper_basename_collisions.py, 4 passed. |
| 76 | Low | done | The `seg_fp` restamp is set only inside `with swallow(...)`; on a failed stat the entry silently keeps the **source** dataset's fingerprint and the carried-over override  | Verified by reading the diff and by mutation. The three sites converged on `_common/containers.restamp_overrides`, which **removes** `seg_fp` when the destination `stat` fails instead of leaving the source dataset's fingerprint, and returns the unstamped count each tool states in its own log line through `unstamped_note`. Keeping the stale key fails four tests, including the end-of-chain one where Validate then prunes the carried override (`assert 1 == 0`). tests/test_override_restamp.py, 5 passed. |

### Group W3

| # | Sev | State | Item | Where it stands |
|---|---|---|---|---|
| 2 | Medium | done | `_export_binary_mask` catches every exception into `above_by_nid = {}` and renders an all-black silhouette — identical to "no cells above threshold". Same all-black resul | Verified by reading the diff and by mutation. `review_image/collect.review_build_threshold_map` now returns `None`, with a `[binary-mask]` log line naming the cause, for all three non-measurements (no rows for the well, unresolvable row keys, no rows covering the displayed field), so an all-`False` map means only "measured, nothing passed". Returning the all-`False` map from either path fails its test with `assert {1: False, 2: False} is None`. The consumer half — `image_compose` reporting the cell unrenderable on `None` rather than `or {}` — was read and is correct, but no test drives it; and `review_image/renderer.py` still coerces `None` to `{}` on its own draw path (owed-work item 7). tests/test_scan_and_verdict_partials.py, 13 passed. |
| 12 | Medium | done | A mid-scan exception returns partially filled dicts indistinguishable from a complete scan, under a "Result: N fluor…" line that reads as an inventory. | Verified by reading the diff and by mutation. `zip_members.ScanResult` carries `partial` / `reason`, every abort path in `scan_zip_members` and `scan_folder_members` marks it, and `_merge_scan` carries it onto the accumulators. Not marking the mid-scan failure fails `test_a_truncated_zip_scan_says_it_is_truncated`; dropping `merge_partial(fluor, g)` fails `test_the_flag_survives_the_merge_and_the_sort`. Two notes: the `derive`-at-the-sort half in `find_well_images_and_masks` is **not** pinned (replacing it with `dict(sorted(...))` leaves all 13 green, because that test calls `_merge_scan` directly), and nothing yet reads `partial` on screen (owed-work item 5). tests/test_scan_and_verdict_partials.py, 13 passed. |
| 23 | Low | done | A verdict that raises during re-projection leaves the previous `QC_Pass`/`Crop_Pass` values attached: plots filter by the old parameters while the UI shows the new ones. | Verified by reading the diff and by mutation. `cache_projectors.project_verdict_column` drops the column (`df.drop(columns=[column], inplace=True, errors="ignore")`) when a verdict raises, logs what that means, and returns the labels that failed. Removing the drop fails `test_a_failed_reprojection_drops_the_stale_verdict` with `assert 'QC_Pass' not in Index(['nid', 'QC_Pass'])`, so the stale value is what the test catches. The returned failure list still has no reader (owed-work item 6); the frame-level signal is the primary fix and is live. tests/test_scan_and_verdict_partials.py, 13 passed. |
| 45 | Medium | done | An inherited per-member control mode shows `—` in the Control combo while the export normalizes anyway. | Verified by reading the diff and by mutation. `base_panel` gains a `_FC_PER_SAMPLE` sentinel entry, offered and selected only while the inherited per-sample mode is actually in force, and `_on_ctrl_change` clears the inherited map and removes the entry in place when the user names a control. Never offering the entry fails `test_the_control_combo_names_an_inherited_per_sample_mapping` with `assert '—' == 'Per-sample (from the plot tab)'`; not clearing the map fails `test_picking_one_control_replaces_the_inherited_mapping` with `assert 'per_sample' == 'single'`. The second test drives the real combo through `setCurrentText("B01")`, so the callback is exercised rather than bypassed. tests/test_batch_export_run_snapshot.py, 8 passed. |
| 56 | Medium | done | An unparseable well label yields `well_token=None`, which **disables** the per-well filter in the loose-file fallback, so every well's images are accepted into that one w | Verified by reading the diff and by mutation. `_scan_raw_disk_files` no longer guards the filter behind `if well_token and …`, and `_raw_file_passes_unknown_well` inverts the question to "could this file be someone else's?" — admitting only files whose own names resolve to no well. Restoring the `well_token and` guard, or making the unknown-well filter admit everything, each fails `test_an_unparseable_label_does_not_admit_another_wells_image` with `assert ['A01_GFP_f1_t0.tif'] == []`. The case the fallback exists for still works (`test_files_naming_no_well_are_still_admitted_under_an_unparseable_label`). tests/test_scan_and_verdict_partials.py, 13 passed. |
| 57 | Medium | done | An Overlay cell can never carry the `raw` uncorrected badge, so the status line counts uncorrected cells no badge ever identifies. | Verified by reading the diff and by mutation. `_render_cell`'s overlay branch now calls `mark_uncorrected(ctx, cell, rgb, overlay_chans)` after the chrome, `is_raw_fallback` keys on the new `cell_source_channels` rather than the sentinel, `_cell_title` and `export.py:469` pass `overlay_chans` too, and the status line counts badged cells rather than loader records. Removing the overlay branch's call fails `test_render_cell_calls_mark_uncorrected_for_an_overlay_cell` with `assert [] == [('⊕ OVERLAY', ['GFP', 'DAPI'])]` — the assertion is on the outbound call, which is the boundary the defect lived at. tests/test_image_table_channel_identity.py, 12 passed. |
| 59 | Low | done | A ratio cell's raw-fallback is recorded under the numerator/denominator channel, so it is never badged and is counted under a channel absent from the grid. | Verified by reading the diff and by mutation. `cell_source_channels` resolves a ratio cell to its numerator and denominator, so `is_raw_fallback` asks the loader's record for channels the loader actually opened. Stopping the ratio resolution fails `test_a_ratio_cell_is_a_raw_fallback_when_an_input_fell_back` with `assert ['GFP/DAPI'] == ['GFP', 'DAPI']`; the corrected-inputs control still passes. tests/test_image_table_channel_identity.py, 12 passed. |
| 60 | Low | done | `composite_column_rgb` calls `_row_display_opts(ctx, r)` with no channel, so it always loads the **raw** frame for a corrected channel. Dead today (re-export only). | Verified by reading the diff and by mutation. `composite_column_rgb` now calls `_row_display_opts(ctx, r, chan)`. Dropping the channel argument fails `test_composite_column_loads_each_cell_with_its_own_render_mode` with `At index 0 diff: ('DAPI', False) != ('DAPI', True)` — the corrected channel served raw, exactly the reported symptom. The function remains dead in the app (a `# noqa: F401` re-export) and still has no ratio or overlay handling, which is owed-work item 9. tests/test_image_table_channel_identity.py, 12 passed. |
| 61 | Low | done | `_parse_lut`'s no-spec fallback applies column 0's channel LUT to the whole row. Reachable only with an empty `channel_opts`. | Verified by reading the diff and by mutation. `_parse_lut`'s no-spec branch now uses `_shared_channel_lut_for(ctx, chan)` when a channel was named and falls back to the row's representative channel only when none was. Restoring the unconditional row fallback fails `test_parse_lut_uses_the_named_channels_own_shared_window` with `assert (0.0, 100.0) == (0.0, 9999.0)` — column 0's window on the whole row. tests/test_image_table_channel_identity.py, 12 passed. |
| 62 | Low | disputed | Overlay cells install a hover readout keyed on the sentinel channel, so their pixel readout can never resolve and is always empty. | The code fix is present and correct, but its guard enters one step past the defect. `_install_cell_events` gained a `readout_chan` parameter (`readout = readout_chan or chan`) and the call site in the render loop passes `overlay_chans[0]` for an `OVERLAY_CHAN` cell — I read both in HEAD. The parameter half bites: making `_install_cell_events` ignore `readout_chan` fails `test_the_readout_loads_a_real_channel_for_an_overlay_cell` with `assert ['⊕ OVERLAY'] == ['GFP']`. The **call site** half does not: setting `readout_chan=None` at the call site leaves all 12 tests green, because the test calls `_install_cell_events(..., readout_chan="GFP")` directly and hands the fix its own answer. That is the §7.6 seam — the reported defect is a call-site defect, and nothing pins the call site. One test that drives the render loop (or asserts the argument at that call) closes this row. |
| 99 | Medium | done | The multi-timepoint aggregate `_draw` omits `color=data["color"]` that the single-tp sibling and the tab both pass. | Verified by reading the diff and by mutation. `_run_scatter_agg_multi_tp_job`'s `_draw` passes `color=data["color"]` to `ax.errorbar`. Removing it fails `test_the_multi_tp_scatter_aggregate_draws_each_point_in_its_timepoint_colour` with `assert [None, None] == [(0.1, 0.2, 0.9, 1.0), …]` — the test spies on the `errorbar` call, so the assertion is at the boundary. tests/test_batch_export_run_snapshot.py, 8 passed. |
| 100 | Medium | done | val_col and metric are re-read per job while the channel token is snapshotted, across a `processEvents()` pump. | Verified by reading the diff and by mutation. `_val_col_selected` and `_metric_selected` are hoisted beside `_ch_selected` in both `base_panel._run_group` and `bar_panel._run_job`, so the channel, the column and the metric come from one read. Restoring the per-job live read in either panel fails the AST guard `test_no_per_job_closure_reads_a_live_combo`, which names the offending line and method. Scan-surface note (§7.5 #3): that guard reads only `base_panel.py` and `bar_panel.py`, so it cannot see `scatter_panel.py`, where the recorded sibling (four `_run_scatter_*_job` methods reading live axis combos) still lives — owed-work item 8. tests/test_batch_export_run_snapshot.py, 8 passed. |
| 102 | Low | done | The pre-rendered-RGB branch skips chrome and the raw badge and uses a different contrast than the export path for the same cell. | Verified by reading the diff and by mutation. The `arr.ndim >= 3 and not _is_nuc_seg(chan)` shortcut is gone from `_render_cell`; every non-overlay cell composes through `cell_display_rgb`, takes the chrome and `mark_uncorrected`, and thumbnails at a fixed `0.0..255.0`, matching `export._build_export_figure`. Re-inserting the shortcut fails `test_a_pre_rendered_rgb_cell_gets_the_chrome_and_the_badge` with `AssertionError: the scale bar / identity label was dropped`. tests/test_image_table_channel_identity.py, 12 passed. |
| 104 | Low | done | Conditional scope restore leaves the app in the batch's scope on one branch. | Verified by reading the diff and by mutation. `_app_val_col_scope`'s `finally` restores `chan.active_val_col = saved` unconditionally, matching its sibling `_app_group_scope`. Restoring the `if saved is not None` guard fails `test_the_value_column_scope_restores_a_none` with `assert 'mcherry_total_intensity' is None` — the batch's own column left behind on shared app state, which is the reported defect. tests/test_batch_export_run_snapshot.py, 8 passed. |

### Group ME

| # | Sev | State | Item | Where it stands |
|---|---|---|---|---|
| 1 | High | done | A well whose spot-counting pass raises is logged and then dropped from `counts`; `counts.get(k, 0)` writes a full column of `0` into that well's CSV and `done_cb` still r | Fixed: worker.py records failed wells and _write_counts_to_csvs skips them, so they keep their previous counts and land in the run's shortfall instead of a fabricated zero column. tests/test_smfish_failed_well_is_not_zero_filled.py — proved by neutralising the skip at HEAD (real AssertionError). |
| 31 | Medium | done | The replicate-set average is a pool-of-cells mean, not the mean-of-per-well-means Bar, Line and Stats use. Possibly deliberate; undocumented either way. | Fixed on the user's decision: the averaging metrics (Mean, Mean-all, Fraction, Ratio) now reduce group_replicate_stats through the shared replicate_mean, matching Bar/Line exactly; the replicate unit follows the per-FOV toggle as the plots do. METRIC_COUNT stays the pooled total (a total, not an average) and says so. The dead _pool_filtered_mean and the unused ratios parameter are gone. tests/test_heatmap_matches_bar_and_line.py — proved on origin/main, which returns 2.8 where the bar shows 6.0. |
| 34 | Medium | done | The per-cell Events panel ignores `bar_err_settings`, so "None" and "95 % CI" apply to grouped mode only. | Fixed: draw_per_well_n_bars now reads bar_err_settings(ctx) — the same accessor the aggregate path uses — so the Show mode and error-bar type govern the per-cell Events panel, and 95 % CI scales by 1.96. tests/test_per_cell_events_honours_stats_controls.py, proved on origin/main. |
| 39 | Low | done | Docstring says epsilon is *added*; the resolver *clamps* (`np.maximum(den, epsilon)`) — different numbers near and below zero on background-subtracted channels. | Swept: the clamp-not-add rule is now stated consistently in domain/ratio_models.py, data/data_loading.py, the Ratios tab intro text, and README_WellViewer.md. The manual was a fourth site the finding did not name. |
| 47 | Low | done | `tick_labels` de-duplicates by reference and `distinct_controls` by resolved wells, so a one-well control set plus that well's own token gives an axis title naming one co | Closed as a documentation defect, which is what it was. `tick_labels` is handed bar items carrying a `ControlRef` and nothing else, so resolutions are not available to it and it cannot key on wells the way `distinct_controls` does; the comment claiming the two matched was simply false. The true relationship, the one configuration where they disagree (a single-well replicate set alongside that same well), why the redundancy is preferable to losing the per-bar distinction, and what to change if it ever needs closing, are now written at the site. Both opposing tests stand and both still pass. |
| 48 | Medium | done | One row edit rebuilds the table twice, and `setCellWidget` destroys the combo whose slot is still on the stack — roughly 192 combos per edit on a 96-member plate. | Fixed: `_write` no longer calls `_populate()` — `set_fold_change_state` is passed an empty `initiating_scope`, so its sync already reaches this window's populate. The state write stays synchronous (existing tests are entitled to see the mapping immediately); only the rebuild it triggers is deferred, because `_fill_row`'s `setCellWidget` destroys the combo whose signal is still on the stack. tests/test_pairing_editor_rebuilds_once.py drives the real combo and counts rebuilds at the row read, not at a wrapper that could not see the direct call. Each mutation bites separately: `assert 2 == 1` for the double rebuild, `the combo was replaced while its signal was running` for the defer. |
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
