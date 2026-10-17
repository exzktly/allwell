# Review fix tracker — remote resume (PRs #993 / #994)

Live state for the findings of the adversarial review of PRs #993 and #994 that
concern resuming a batched remote run. One row per finding; each row moves to
`done` in the same commit as its fix, and only with a test that was watched
failing on the unfixed code. This file is deleted by the commit that closes its
last row.

Counts are derived from the rows below: count the `State` column.

| ID | Defect | State | Test |
|----|--------|-------|------|
| H1 | `remote_batch_resume.find_leftovers` / `take_from_remote` adopt any remote `<well>_out.zip` as a finished well, so a well suspended part way (the mid-batch watchdog after an app crash) is downloaded, journalled ok and never completed | open | — |
| L1 | `pipeline.io.merge_fetched_csvs` discards a truncated local CSV whole instead of salvaging its readable rows, and logs a contradictory "no local CSV to merge with" line | open | — |
| L2 | `analyze/suspend.py` Resume fills the form from the recorded run before `start_run` can refuse, so a refused resume leaves the user's form replaced | open | — |
| L3 | `remote_fetch_place.local_output_mb`'s CSV-only branch is unreachable, so a CSV-only well rescued by the end-of-run retry stays uncoloured | open | — |
| L4 | The mid-batch watchdog's `sleep` survives `kill "$_aw_wd"` and holds the driver's stdout for up to `MID_BATCH_WATCH_POLL_S` | open | — |
| L5 | `remote_srun_retry` says "Slurm will boot one" on a cluster with no powered-down node to boot | open | — |
| W1 | `test_the_launch_command_survives_the_command_line` budget fixture omits `--background_dir` / `--background_dir_alias` | open | — |
