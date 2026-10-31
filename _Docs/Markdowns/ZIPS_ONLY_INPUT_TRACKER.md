# Zips-only input — the analysis pipeline stops accepting loose images and well folders

A remediation tracker (CLAUDE.md §6.1), written before any code. One row per item, updated in the same commit as
the work, closed only with evidence, and deleted by the PR that closes its last row.

**Status, derived from the rows below.** Recount with `grep -c '| \`todo\` |'`, `'| \`done\` |'`,
`'| \`declined\` |'` and so on; never type a total here. Each row's state is the only source of truth.

## The decision

The Analyze pane, the pipeline (`process_microscopy.py`) and remote runs accepted three input layouts: per-well
`<WELL>.zip` archives, per-well `<WELL>/` folders, and loose images at the top level of the selected folder (or of
`in/`). The two non-zip layouts were converted on the fly — loose images grouped by `pipeline/well_plate_zipper.py`
at Run, folders processed directly by a parallel "folder mode", mixtures of the two consolidated in place — and a
plate that had already been run could also receive new loose images beside its zips, which Run folded into the
archives and processed incrementally.

The Datasets pane now has a dedicated tool for the conversion (**Datasets → Zip loose folder**,
`datasets/import_loose/`). The owner decided (2026-10-31):

1. **Per-well zips are the only input layout** the pipeline, the Analyze pane and the remote runner accept.
2. **Per-well folders are not accepted either** — folder mode is removed along with the loose-image grouping.
3. **The "fold new loose images into an already-zipped plate, then run incrementally" workflow is dropped**, not
   moved. Analyze refuses any loose image instead.

The gain is maintenance, not run time: for a plate that is already zipped the removed code costs a few directory
listings before analysis starts. What goes is ~1,500–2,000 lines of layout handling — the resume, dedupe and
source-deletion logic that has produced most of the input-side defects recorded in CLAUDE.md §7.1 and §7.5 #5.

## The rule the new code must hold

**Degraded output must never be indistinguishable from good output** (CLAUDE.md §7). Removing a layout must not
mean silently *ignoring* it. Every input that used to be consumed and no longer is must be **refused by name**,
with the remedy (Datasets → Zip loose folder) in the message:

- loose images at the selected root, with no `in/` (used to be grouped into `in/`);
- loose images inside `in/` or a folder named `in` with no well zips (used to be grouped in place);
- loose images beside well zips (used to be folded in — now the run would silently skip them);
- per-well folders and no zips (used to run in folder mode);
- per-well folders holding images beside well zips (used to be consolidated — now silently skipped).

The same refusal reaches all three entry points: the Analyze readout and Run, the pipeline CLI, and remote staging.

## Rows

Phase A — the pipeline (`process_microscopy.py`, `pipeline/`)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| A1 | Delete the `in`-named-directory loose-image organiser and its call in `main()` | `process_microscopy.organize_loose_tifs_into_well_folders`, `main()` | `todo` | |
| A2 | Delete folder mode: `process_well_folder_task`, `process_well_folders`, and the `elif well_folders:` branch of `main()` | `process_microscopy.py` | `todo` | |
| A3 | Delete `find_image_groups` (the on-disk group scanner used only by folder mode); keep `find_image_groups_in_zip` | `process_microscopy.py`; comment mention in `pipeline/well_plate_zipper.py` | `todo` | |
| A4 | Delete `compress_folder_images_to_zip_and_remove` once remote staging (C1) no longer calls it | `process_microscopy.py`, `services/remote_staging.py` | `todo` | |
| A5 | Delete `pipeline/mixed_input.py` (`consolidate_mixed_to_zips`) and its calls: `main()`, the re-export in `services/input_resolution_service.py`, the docstring note in `datasets/import_loose/apply.py` | `pipeline/mixed_input.py` and callers | `todo` | |
| A6 | `main()` decides zips-only: refuse with a named message when the input holds loose images, well folders with images, or no well zips — before any model precompute | `process_microscopy.main`, `pipeline/input_layout.py` | `todo` | |
| A7 | `pipeline/input_layout.py`: delete `find_well_folders`; replace `refuse_images_only_in_ignored_folders` with the zips-only refusal of A6; rewrite the module docstring | `pipeline/input_layout.py` | `todo` | |
| A8 | Remaining `find_well_folders` / `well_folders` consumers go zips-only: `plan_blank_groups` / `pipeline/empty_well.py:204`, `pipeline/completeness.py`, `refuse_uncovered_wells`, `scope_to_only_wells`, `replaced_wells`, `plate_containers` | `pipeline/empty_well.py`, `pipeline/completeness.py`, `process_microscopy.py` | `todo` | |
| A9 | The pipeline module docstring and CLI help stop describing folder / loose input | `process_microscopy.py` header (line ~26), argparse help | `todo` | |

Phase B — the Analyze pane (`services/input_resolution_service.py`, `analyze/`)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| B1 | `plan_input_layout`: remove the `GROUP_LOOSE` / `FOLD_LOOSE` / `ZIP_ROOT` actions; add non-runnable actions for loose images and for folders-only, each with a message naming Datasets → Zip loose folder; readout and Run keep sharing the one planner | `services/input_resolution_service.py` | `todo` | |
| B2 | `resolve_input_output`: strip the zipper run, the resume tail and the `on_success` source-deletion hook; decide whether the return stays a 3-tuple (callers: `analyze/domain/input.py`, `services/pipeline_runner.py`, `services/job_queue.py`, `services/job_kinds.py`) | `services/input_resolution_service.py` and callers | `todo` | |
| B3 | Delete the append-and-incremental flow: `plan_loose_append`, `_plan_loose_append`, `LooseAppendPlan`, `append_loose_images_to_zips`, `select_incremental_scope`, `plan_incremental_scope`, `_scope_verdict`, `ScopePlan`, `_loose_timepoints_for_wells`, and `_zip_timepoints` / `_blank_well_tokens` if nothing else uses them. The user-chosen `--incremental` option stays | `services/input_resolution_service.py`; callers `analyze/domain/input.py:111`, `analyze/preflight.py:485`, comment `analyze/form.py:414` | `todo` | |
| B4 | Delete the in-place grouping helpers: `_group_loose_tifs_in_place`, `_fold_well_folders_into_existing_zips`, `_fold_one_well`, `_member_is_superseded`, `_file_crc`, `_grouped_member_index`, `_is_already_grouped`, `_wells_claimed_by`, `_delete_sources_that_landed`, `groupable_tif_count` | `services/input_resolution_service.py` | `todo` | |
| B5 | `run_zipper`, `build_zipper_cmd`, `find_zipper_script`, `ZIPPER_SCRIPT`, `TIF_GLOB` are then used by Datasets only: move them to `datasets/import_loose/` (CLAUDE.md §3 rule 1) unless remote staging still needs them after C1 | `services/input_resolution_service.py` → `datasets/import_loose/` | `todo` | |
| B6 | Remove the always-on `move_source_tifs` option from the Analyze run spec and the runner (Datasets' own in-place mode keeps its flag) | `analyze/form.py:411`, `analyze/domain/input.py`, `services/pipeline_runner.py` | `todo` | |
| B7 | The run dashboard's grouping stage (`zipper_start` / `zipper_well` / `zipper_done`): no grouping happens any more. Decide whether the stage still announces resolved wells or goes; whichever, no dead "Grouping" label | `analyze/domain/input.py`, run dashboard | `todo` | |
| B8 | Well counting for loose input: `count_expected_wells`, `iter_all_well_tokens`, `_expected_well_count`, `tif_files_in`, `has_well_content` simplify to zips | `services/input_resolution_service.py`, `analyze/` | `todo` | |
| B9 | Other `plan_input_layout` callers still behave with the new actions: `analyze/suspend.py:623`, `analyze/run_spec.py:550`, `analyze/tab.py:393`, `analyze/preflight.py`, docstring `util/schema_infer.py:343` | those files | `todo` | |

Phase C — remote runs (`services/remote_*`)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| C1 | `stage_local_zips` refuses FOLDER / FLAT / MIXED input with the same named message; delete `_zip_up_in_place`, `_stage_mixed_mode`, `_zip_well_folder` and the `run_zipper` staging step | `services/remote_staging.py` | `todo` | |
| C2 | `LocalInputMode`: a remote run started on an earlier version may be recovered or fetched after upgrading, with `folder` / `flat` recorded in its state file. Measure what fetch and recover do with those modes and decide whether to keep reading them (fetch-side only) or refuse with a message | `services/remote_run_state.py`, `remote_fetch.py`, `remote_recover.py`, `remote_runner.py`, `acquisition_transport.py` | `todo` | |
| C3 | `scripts/allwell_remote.py` and `input_well_containers` callers stay correct for zips-only | `scripts/allwell_remote.py`, `services/remote_runner.py`, `services/remote_fetch.py`, `analyze/preflight.py` | `todo` | |

Phase D — tests, guards, docs, release

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| D1 | Tests for removed behaviour are deleted or rewritten around the refusal; each deletion names the row it follows | `tests/` | `todo` | |
| D2 | One refusal test per layout in "The rule" above, through each entry point: the planner (readout + Run), the pipeline `main()`, remote staging. Each watched failing against the pre-change code (§7.7 #2) | new test file(s) | `todo` | |
| D3 | Guard rosters and ratchets follow the deletions: `tests/_lanes.py`, complexity allowlist entries for deleted functions, monolith budgets (only down), `test_a_module_lives_at_its_widest_use` for B5's move | `tests/` | `todo` | |
| D4 | `ARCHITECTURE.md` describes zips-only input everywhere it described the three layouts (≈119 mentions of loose / folder; most are about other subjects) | `_Docs/Markdowns/ARCHITECTURE.md` | `todo` | |
| D5 | `README_WellViewer.md`: Analyze takes per-well zips; loose images and well folders go through Datasets → Zip loose folder first; the add-timepoints-by-dropping-images workflow is gone | `_Docs/Markdowns/README_WellViewer.md` | `todo` | |
| D6 | Version bump — minor (a workflow users must relearn, CLAUDE.md §6) | `version.py` | `todo` | |
| D7 | Reconciliation: re-read every row against the tree, run the guard family, the fast lane, `--remote` and the lanes `scripts/lane_needed.py` names; then delete this tracker in the PR that closes the last row | — | `todo` | |
