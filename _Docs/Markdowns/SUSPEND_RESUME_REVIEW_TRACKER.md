# Suspend / restart / resume review — remediation tracker

Live state for the findings of two adversarial reviews of the suspend → restart →
resume flow on `main` at `d127fe05` (after #934 and #936): one of the local flow
and the job queue, one of the remote flow. Each row is a finding as the reviewer
stated it, which is a hypothesis about the tree at that commit — reproduce it
before fixing it (CLAUDE.md §6.1). A row goes to `done` only with the test that
pins it, seen failing without the fix. This file is deleted by the PR that
closes its last row.

Counts are not written here; count the rows.

States: `open` · `in progress` · `done` · `declined` (reason in the row and at
the site).

## Local flow and job queue

| ID | Sev | Finding | Where | State | Evidence / notes |
|----|-----|---------|-------|-------|------------------|
| L1 | High | Unfinished-run detection compares the journal's resolved `input_dir` (`<plate>/in`) with the form's `raw` (`<plate>`), so it never matches on the default layouts: a queue restart after Pause, Run after Discard, and Run after Stop/Fail/Kill all skip part-done wells as complete; the queue job is marked succeeded. | `analyze/suspend.py` `_input_has_unfinished_work`; journal `open_run` input_dir | done | Reproduced through the real `PipelineRunner.start` → `open_run` order with the resolver returning `<plate>/in` and the form holding `<plate>`: `_input_has_unfinished_work` returned `None`. Fixed by `_journal_names_input`, which also matches the journal's `spec.opts.raw`. `test_a_killed_run_is_resumed_from_every_entry_point.py::test_an_unfinished_run_is_found_when_the_input_was_resolved_to_in` fails without the fix. |
| L2 | High | Stop can end in rc 0 (the pipeline treats SIGTERM as a suspend and winds down inside the 5 s), and rc 0 with a Stop is reported as `done`: "Processing Complete", success hooks, Review hand-off, `jobFinished(True)`, while the journal says `stopped`. | `services/pipeline_runner.py` local and remote rc-0 branches | done | Reproduced with the SIGTERM-handling stand-in pipeline (`['line', …, 'done', 'finished']`). Fixed by `_enqueue_stopped` on both paths; a stopped remote run also keeps cleanup `none`. `test_a_suspended_run_is_not_a_finished_one.py::test_a_stop_that_winds_down_cleanly_is_not_reported_done` and `test_a_remote_suspend_winds_the_run_down.py::test_a_stopped_remote_run_that_exits_zero_is_neither_done_nor_cleaned` both fail without the fix. |
| L3 | High | The suspend record stores the live form's spec and input at click time, not the running spec: edit the form mid-run, Suspend, Resume → a different plate with different settings runs, and the real plate's offer is forgotten. | `analyze/suspend.py` `_write_suspend_record` | done | Reproduced through the real Suspend button with the form edited mid-run: record `raw` = the edited plateY. The record now takes `PipelineRunner.running_spec`, the launched spec. `test_a_suspended_run_is_not_a_finished_one.py::test_the_suspend_record_describes_the_run_not_the_form` fails without the fix (`plateY == plateX`). |
| L4 | High | A crashed app leaves its local pipeline running (`start_new_session=True`); after a restart, Resume/Run on the same plate consolidates and deletes the `in/<well>/` folders the orphan is reading ("No such file or directory"), and the orphan can overwrite a complete CSV with a partial one. The best-supported explanation of the original "reloading failed to find the pre-existing files" report. | `services/pipeline_service.py` spawn; journal; resume paths | open | |
| L5 | Medium | Suspend during grouping/resolve (busy, no subprocess yet) does nothing, says nothing, and the run completes. | `analyze/suspend.py` `_suspend` early return | open | |
| L6 | Medium | A second Suspend click hard-kills the run (second SIGTERM) and it ends as an error while the UI says "finishing"; closing the app mid wind-down does the same. | `analyze/suspend.py`; `allwell.py` close | open | |
| L7 | Medium | The pipeline's "Re-run with --incremental to continue" advice fails from the CLI after a loose-TIF plate is suspended: folder mode leaves a mixed `in/` the pipeline then refuses. | `process_microscopy.py` mixed-layout refusal | open | |
| L8 | Low | The suspend record's `wells_done` is captured at click time, so the banner under-reports progress. | `analyze/suspend.py` `_write_suspend_record` | done | `_on_run_suspended` now rewrites the record's tally with the run's final count (`_settle_suspend_record_tally`). The same test's tally assertion fails with that call removed: `(0, 6) == (1, 6)`. |
| L9 | Low | With an empty form, the journal offer always outranks the suspend record for the same plate (`closed_at` > `suspended_at`), contradicting the stated tie rule. | `analyze/suspend.py` `_resumable` ranking | open | |
| L10 | Low | Pausing the queue during grouping SIGTERMs the zipper and surfaces as an "Input error" failure on the Analyze pane. | `datasets/job_queue` → `PipelineRunner.suspend` during grouping | open | |
| L11 | Low | Folder-mode runs leave `out/<W>/` holding only checkpoint dotfiles. Not suspend-specific. | `process_microscopy.py` folder mode | open | |

## Remote flow

| ID | Sev | Finding | Where | State | Evidence / notes |
|----|-----|---------|-------|-------|------------------|
| R1 | Critical | A second incremental remote run on unchanged input re-attaches to the first's host directory; the pre-flight counts out.zips (not the uploaded set, not FOV coverage) and declares a full cache hit, or the remote runs without `--incremental` and SKIPs partial wells — success reported with wells partial or never processed. | `services/remote_runner.py` `_reattach_output_dir`, `_pipeline_launch_is_pointless`, `_build_remote_argv` | done | Reproduced against the real `spawn` on FakeRemote: the second incremental run re-attached, and with an unchanged key its own directory still held the previous partial C01. An incremental run now never re-attaches and empties its own output dir; the cache-hit predicate (moved to `remote_run_id`) compares the uploaded well set and never fires for an incremental run. `tests/test_a_second_incremental_remote_run_does_its_work.py`: the two sequence tests fail on the unfixed code with the defect named; the set-comparison test was proved by mutation. |
| R2 | Medium | An incremental (resumed) run replaces a partial well's field-quality and run-report records with ones covering only the resumed groups; the well stops being flagged partial. Local and remote. | `process_microscopy.py` `_fov_total`/`measured_of`; `pipeline/field_quality_record.py`; `pipeline/run_report.py` | open | |
| R3 | Medium-low | A well already started when the suspend lands, but before its first group, prints `— done.` with no CSV/zip; a remote run then fails verification instead of ending suspended. | `process_microscopy.py` after the group loop | done | Reproduced by suspending right after `extract_zip` in a real `process_well_zip_task`: `Well A01 — done.` with nothing written. `_suspended_before_any_group` (both well tasks) returns before the end-of-well path. `test_a_remote_suspend_winds_the_run_down.py::test_a_well_suspended_before_its_first_group_leaves_nothing` fails without it. |
| R4 | Low-medium | The suspended run's remote directory is kept "for the resume" but the resume never uses it; it stays on the host for ever. | `services/pipeline_runner.py` cleanup `none`; `runs/` never pruned | open | |
| R5 | Low | A batched suspend during batch 1's upload leaves a queued driver ssh/srun alive (`_release_driver` returns early on rc 0). | `services/remote_runner.py` `_release_driver` | open | |
| R6 | Low | Incremental staging is applied only on the ZIP layout; MIXED/FOLDER/FLAT re-upload whole wells, and batched mode then skips partial wells as complete. | `services/remote_runner.py` `stage_local_zips` | open | |
| R7 | Low | A partial well from a suspended remote run is journalled `fetched` and tallied as finished. | `services/run_journal` tally | open | |
