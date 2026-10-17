# Review fix tracker — remote resume (PRs #993 / #994)

Live state for the findings of the adversarial review of PRs #993 and #994 that
concern resuming a batched remote run. One row per finding; each row moves to
`done` in the same commit as its fix, and only with a test that was watched
failing on the unfixed code. This file is deleted by the commit that closes its
last row.

Counts are derived from the rows below: count the `State` column.

| ID | Defect | State | Test |
|----|--------|-------|------|
| H1 | `remote_batch_resume.find_leftovers` / `take_from_remote` adopt any remote `<well>_out.zip` as a finished well, so a well suspended part way (the mid-batch watchdog after an app crash) is downloaded, journalled ok and never completed | done | `test_a_batched_restart_reuses_the_remote.py::test_a_well_the_remote_left_part_way_is_run_again_not_adopted` (red on main: A02 adopted); `test_a_part_way_well_is_not_sealed.py` (mutations bite) |
| L1 | `pipeline.io.merge_fetched_csvs` discards a truncated local CSV whole instead of salvaging its readable rows, and logs a contradictory "no local CSV to merge with" line | done | `test_a_stop_mid_download_leaves_nothing_broken.py::test_an_incremental_download_keeps_the_readable_rows_of_a_truncated_local_csv` (red on main: f1/f2 rows 0) |
| L2 | `analyze/suspend.py` Resume fills the form from the recorded run before `start_run` can refuse, so a refused resume leaves the user's form replaced | done | `test_analyze_resume_offer.py::test_a_resume_the_run_refuses_leaves_the_user_s_form_alone` (red on main: 'Hoechst' == 'MyNuclei') |
| L3 | `remote_fetch_place.local_output_mb`'s CSV-only branch is unreachable, so a CSV-only well rescued by the end-of-run retry stays uncoloured | done — routed CSV-only rescues through it | `test_a_batched_resume_does_not_download_first.py::test_a_csv_only_well_the_retry_rescues_is_painted_downloaded` (red on main: only the CSV line) |
| L4 | The mid-batch watchdog's `sleep` survives `kill "$_aw_wd"` and holds the driver's stdout for up to `MID_BATCH_WATCH_POLL_S` | done | `test_a_vanished_app_suspends_the_batch_in_flight.py::test_the_watchdog_does_not_hold_the_driver_s_output_open` (red on main: 30.0 s) |
| L5 | `remote_srun_retry` says "Slurm will boot one" on a cluster with no powered-down node to boot | done | `test_srun_boot_retry.py::test_with_every_node_busy_and_none_asleep_nothing_is_said_to_boot` (red on main: "Slurm will boot one") |
| W1 | `test_the_launch_command_survives_the_command_line` budget fixture omits `--background_dir` / `--background_dir_alias` | done — bar now the real 8,191 on the whole Windows ssh line; measured packed 4,172/4,224/4,948 and line 4,418/4,470/5,194 for 0/1/21 aliases (the old 4,096 bar on the packed command is exceeded by every blank run) | `test_the_launch_command_survives_the_command_line.py::test_the_whole_windows_command_line_stays_under_the_limit` |
