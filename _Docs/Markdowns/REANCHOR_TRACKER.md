# Re-anchoring: one parallel pass at the end, shown on the plate — tracker

**Temporary.** CLAUDE.md §6.1. Two bodies of work on one branch
(`claude/nanowell-reanchor-on-plate`): the review findings on the plate's
display of a well being re-anchored (D rows), and moving a batched remote
nanowell run's re-anchoring to one parallel pass at the end of the run (F
rows), the option the user chose. Deleted by the commit that closes its last
row. A row is `done` only with the test that pins it, watched failing without
the fix. States: `todo`, `done`, `declined`.

## Display (adversarial review of 50db4edd)

| Row | Finding | State | Where / evidence |
|---|---|---|---|
| D1 | A re-run that anchors none of its timepoints still logs "re-anchored.", nothing pulls it again, so the well stays blue for good | done | pipeline/nanowell_task.py::reanchored_line, `unchanged` event; test_a_re_run_that_placed_nothing…, test_the_pipeline_says_re_anchored_only… (mutations `line_always`, `unchanged_parse` bite) |
| D2 | Verification prints "pulled again" even when its rsyncs failed, so the plate turns green over the stale first copy | done | pull_nanowell_results returns the short wells, verify adds them to `missing`; test_verification_says_a_short…, test_verification_keeps_the_remote… (mutations `short_ignored`, `keep` bite) |
| D3 | A re-anchored well first downloaded by the end-of-run retry stays blue: `_refetch` outranks the `download` that came after the re-run | done | set_well_stage('download') clears `_refetch`; test_a_download_after_the_re_run… (mutation `d3` bites) |
| D4 | Hover text says "when the batch ends" on a run with no batches | done | `_detail`: "when its batch or the run ends"; test_it_waits_on_the_server… asserts the text |
| D5 | The rail still counts a re-anchoring well as processed and downloaded while the plate shows it amber | done | `_count`; test_the_rail_does_not_count… (mutation `d5` bites) |
| D6 | A run stopped during the re-anchoring leaves the well amber "processing again" | done | `_on_outcome_changed`; test_a_run_that_ends_during… (mutation `d6` bites) |
| D7 | `test_a_second_pull_that_falls_short_leaves_it_on_the_server` feeds a line nothing parses; no test covers D1–D3 | done | the short-pull test now feeds both real lines, which parse to `short`, and asserts the hover text; D1–D3 covered above |
| D8 | README and ARCHITECTURE claim green only after the second download lands, false given D1/D2 | done | README and ARCHITECTURE rewritten for unchanged / failed / short / unfinished |

## One pass at the end of a batched run

| Row | Item | State | Where / evidence |
|---|---|---|---|
| F1 | The re-anchoring re-runs its wells in parallel on the run's worker pool, not one by one in the parent | todo | |
| F2 | A batch run with `--nanowell_hold_dir DIR` skips the end-of-run pass and links into DIR the archive of every well with a timepoint the pass could anchor | todo | |
| F3 | `--nanowell_reanchor_only` runs the pass alone over the archives in the input folder, against every grid record in the output folder; none to run is not an error | todo | |
| F4 | The batch driver runs one extra batch after the data batches, with `--nanowell_reanchor_only` when its flag file is present; the blank is still linked into the last *data* batch | todo | |
| F5 | After the last data batch the app moves `keep/` into the final batch, names its wells, runs it, and releases them | todo | |
| F6 | `release_batch` keeps (does not delete, does not mark released) a data batch's well whose archive is held in `keep/` | todo | |
| F7 | One predicate decides "this well waits for the final pass", shared by the pipeline and the app | todo | |
| F8 | Stop, suspend and a lost driver before the final pass: the held wells say they were not re-anchored; nothing claims they were | todo | |
| F9 | Non-batched remote runs and local runs keep the pass at the end of their one invocation, now parallel | todo | |
| F10 | Docs: README (what the user sees, the disk cost) and ARCHITECTURE | todo | |
