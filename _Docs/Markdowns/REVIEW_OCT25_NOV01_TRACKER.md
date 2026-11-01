# Review of the pull requests merged 2026-10-25 to 2026-11-01 — remediation tracker

A remediation tracker in the sense of CLAUDE.md §6.1. It records every defect an
adversarial review of the pull requests merged into `main` between 2026-10-25 and
2026-11-01 (#1052 to #1164) found on `main` at ca528ab, one row per finding, with
the fix proposed for it and the state of that fix. Each row is updated in the same
commit as its fix and goes to `done` only with a test that was watched failing
without the fix. The pull request that closes the last row deletes this file and
says so in its body.

The review covered three areas for every pull request in the window: scientific
validity and consistency (calculations, gates, inclusion criteria), adherence to
CLAUDE.md and ARCHITECTURE.md, and implementation accuracy (bugs, edge cases,
incomplete implementations). Findings were reproduced against the tree before
being entered here; areas checked and found sound are listed at the end so a
later reader can tell a cleared area from an unreviewed one.

**Status, derived from the rows below:** 11 rows — 11 `done`, 0 `todo`, 0 `declined`. Recount with
`grep -c '| \`done\` |' _Docs/Markdowns/REVIEW_OCT25_NOV01_TRACKER.md` (and
likewise for `todo` and `declined`); never edit a count by hand alone.

## Rows

| ID | PR | Area | Severity | Finding | Proposed fix | Pinning test | State |
|---|---|---|---|---|---|---|---|
| R1 | #1075 | implementation | Medium | `JobQueue.retry(job, with_downstream=True)` ran the unblock cascade once, before resetting the failed or cancelled downstream jobs. In a chain A → B → C with A succeeded, B failed and C blocked, retrying A with its downstream left C blocked for ever, naming B's failure, and `next_runnable()` never reached it. | After the downstream resets, run `_cascade_unblock` from every downstream job that was reset. | `tests/test_queue_ux_review_2.py::test_k2_retrying_with_downstream_unblocks_what_a_failed_middle_job_stranded` | `done` |
| R2 | #1071 | science | Medium | Under Auto display range, `crop_scale` (`review/features/nanowell/renderer.py`) puts tile crops (raw camera units) and montage crops (8-bit PNG) on one percentile scale, so a montage crop beside tile crops is drawn in the bottom few percent of the ramp and reads as a dark, empty nanowell. | In the Auto branch, compute the montage crops' scale separately and have `CropScale.of` use it whenever one exists; make the caption name both scales. | `tests/test_nanowell_display_range.py` — `test_a_montage_crop_keeps_a_scale_of_its_own_under_auto_too` | `done` |
| R3 | #1066 | implementation | Low | `compact_number` picks the unit suffix before rounding, so 999.7 is captioned `1e+03` and 999,800 `1e+03k`. | Round to three significant figures first, then choose the suffix from the rounded value. | `tests/test_nanowell_extremes_layout.py::test_big_numbers_are_written_short` (999.7 → `1k`, 999,800 → `1M`) | `done` |
| R4 | #1139 | implementation | High | Cells segmented on a seam tile (`seam:N`) are renamed to the real tile the site row names but keep the seam image's own labels, so `(fov, nucleus_id)` collides with that real tile's own cells and two different cells share one `CellKey` (overrides, smFISH write-back, lookups, outlines). | Keep the identity unique when a seam cell is renamed: offset its label past every label the real tiles used at that timepoint. | `tests/test_nanowell_run.py::test_a_cell_counted_on_a_seam_does_not_share_its_identity_with_a_cell_of_the_tile_it_is_reported_under` | `done` |
| R5 | #1131 | science | Medium | The baseline moving-average window is offered under the First-measurable baseline, where every earlier timepoint is by definition unmeasurable, so any window ≥ 2 can only blank every fold change. | `baseline_window_for` returns 0 for First measurable as for Earliest, and the spin box is greyed with that reason. | `tests/test_nanowell_moving_average.py::test_the_baseline_s_window_needs_the_endpoint_s_and_a_baseline_with_something_before_it`, `::test_the_baseline_s_window_control_is_greyed_until_it_can_reach_the_figure`; `tests/test_batch_export_nanowell_mode.py` moved to a named baseline. Found independently by two reviewers (PRs #1114 and #1131) | `done` |
| R6 | #1128 | implementation | Medium | A local `Remedy: force` note (`<well>_local_csv_cut_short.txt`, `<well>_local_zip_unreadable.txt`) left by a remote download is never retired by the forced remote re-run it asks for: the host never had the note, so `mirror_retirements` finds nothing to mirror, and the well stays listed as failed. | Once a forced, non-incremental remote run has been fetched and verified whole, archive the local force notes of the wells it re-ran into `errors/superseded/` (`error_records.retire_force_notes`, called from `FetchedOutputVerifier.verify`). The seal is not fetched, so verification is the evidence used instead. | `tests/test_a_failed_remote_listing_keeps_the_data.py::test_a_forced_remote_re_run_retires_the_local_note_that_asked_for_it` (forced: archived; not forced: left standing) | `done` |
| R7 | #1093 | science | Medium | `nanowell_frames.best_step` accepts any step within ±3 that matches the reference timepoint's addresses strictly better than step (0,0), with no margin, so one nanowell the dense reference missed is enough to shift a sparser timepoint's whole lattice by a step (or, on a tie, unaddress the well). | Accept a non-zero step only when step (0,0) leaves a real share of addresses unmatched and the best step's gain clears the same floor; otherwise return (0,0). | `tests/test_nanowell_timepoint_frames.py::test_one_nanowell_the_reference_missed_does_not_move_a_sparser_timepoint`, with `::test_a_whole_timepoint_a_step_off_is_still_found_by_overlap` holding the other side; every existing step-off test stays green | `done` |
| R8 | #1153 | science | Medium | The mask cache's code identity (`pipeline/nanowell_masks.code_digest`) hashes only `func.__code__`, which holds neither the function's keyword defaults nor the values of module constants it reads. `cellpose_segmenter` leaves `flow_threshold` and `cellprob_threshold` at their signature defaults, so editing either reuses masks the current code would not produce, and the run reports success. | Hash the defaults and keyword defaults of every function digested, and the module's scalar and tuple constants, alongside the code. | `tests/test_nanowell_masks.py::test_the_code_digest_moves_when_a_default_or_a_module_constant_does`, `::test_the_code_digest_is_stable_for_defaults_whose_repr_names_an_address` | `done` |
| R9 | #1148 | implementation | Medium | `pipeline/cellpose._load_with_gpu_room` retries a CUDA out-of-memory load for ever, backing off to 300 s, on the grounds that another worker will free memory. With one worker, a GPU held by another process, or a GPU too small for the model, nothing frees it, so the well never finishes or fails and a Slurm allocation burns to its time limit. | Bound the total wait, then raise `CellposeUnavailable` so the well lands in `errors/` with the reason. | `tests/test_cellpose_load_oom.py::test_a_load_that_never_fits_is_given_up_after_a_bounded_wait` (times out on main) | `done` |
| R10 | #1150 | implementation | Low | Analyze's planner (`services/input_resolution_service._plan_grouped_root`, `well_present_in_input`) matches well archives with case-sensitive `Path.match("*.zip")`, while the pipeline and `iter_well_tokens` use `suffix.lower() == ".zip"`. An `in/` of `A01.ZIP` archives the pipeline reads plans as "nothing to process", and a folder beside an upper-case archive is reported as needing zipping. | Use `util.well_token.is_well_archive` for both predicates, and compare the suffix in any case at the three siblings that answer the same question for the run: `pipeline/capacity.sample_image_bytes`, `pipeline/auto_threshold._input_archive_for`, `util/schema_infer`'s archive sampler. | `tests/test_input_layout_plan.py::test_an_upper_case_zip_is_a_well_archive_to_the_planner_as_to_the_run`, `::test_the_pipeline_s_own_archive_lookups_read_an_upper_case_zip` | `done` |
| R11 | #1150 (sibling of R10) | implementation | Low | Found while fixing R10: the Datasets tools asked `glob("*.zip")`, case-sensitive on Linux, where the pipeline reads `A01.ZIP` as a well — `dataset_paths.in_containers`, `well_spelling_collisions` and `collect_input_image_stems`, `convert_marker.converted_wells` and `destination_problem`, and the Subset view's well list. A plate of upper-case archives lost its containers and image stems to every copying tool, and a converter took a destination holding them for an empty folder. | One helper, `util.dir_scan.zip_files_in` (any case, hidden sidecars left out), at every site. | `tests/test_dataset_tools_read_an_upper_case_zip.py` (two of three fail without the fix) | `done` |

## Checked and found sound

Recorded so a later reader can tell an area that was reviewed and cleared from one nobody looked at.

- **Nanowell science.** The endpoint moving average (trailing, complete-only, a missing timepoint counted as a gap,
  log₂ averaged as logs); the order rebase → cohort → moving average, the same in Distribution, `wells_summary`,
  Trajectories and Statistics; Benjamini–Hochberg step-up and its wording; the named-timepoint baseline; the subset
  builder's all/any and every-timepoint rules; the Extremes split and tie order; rim focus (relative 90th-percentile
  reference, `MIN_SITES`, measurable-only flagging) on the page, the cell layer and Batch Export alike; the focus
  gate's join and its place in `VERDICT_LAYERS`; cell gating's per-cell-mean brightness; the empty-well segmenter
  calibration's fallback and reuse key; smFISH writing blank rather than 0 for uncounted cells.
- **Remote / HPC.** The seal and finished-well definitions; the batch ledger and hand-off acknowledgement;
  `merge_fetched_csvs`; scratch per plate and node-local scratch; the held-node hold script, release and log
  dedupe; the reservation walltime bound; skip wells; `--ntasks` folding.
- **Analyze / Datasets.** `timepoint_plan` and the converters' resume signature; reading FOVs from the zip; the
  Concatenate nanowell link's set-aside rule; the job queue's multi-link cycle check and `materialise`; the
  `zips-only-input` deletions (no live reference left).
- **Tooling.** `change_scope.py` (an unknown diff is code) and the version-bump workflow.

Not reviewed in depth: #1055, #1057, #1061, #1067, #1073, #1078, #1079, #1082, #1085, #1087, #1090, #1091, #1095,
#1122–#1127, #1129, #1133–#1137, #1143, and most of #1138's transport, Slurm and identity commits.

## Lifetime

Every row above is `done`. This file stays until the follow-up that fixes what the slow lane found on `main`
(step 7 of the review) closes its own rows here; that pull request deletes it and says so in its body.
