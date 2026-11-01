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

**Status, derived from the rows below.** Recount with
`grep -c '| \`done\` |' _Docs/Markdowns/REVIEW_OCT25_NOV01_TRACKER.md` (and
likewise for `todo` and `declined`); never edit a count by hand alone.

## Rows

| ID | PR | Area | Severity | Finding | Proposed fix | Pinning test | State |
|---|---|---|---|---|---|---|---|
| R1 | #1075 | implementation | Medium | `JobQueue.retry(job, with_downstream=True)` ran the unblock cascade once, before resetting the failed or cancelled downstream jobs. In a chain A → B → C with A succeeded, B failed and C blocked, retrying A with its downstream left C blocked for ever, naming B's failure, and `next_runnable()` never reached it. | After the downstream resets, run `_cascade_unblock` from every downstream job that was reset. | `tests/test_queue_ux_review_2.py::test_k2_retrying_with_downstream_unblocks_what_a_failed_middle_job_stranded` | `done` |
| R2 | #1071 | science | Medium | Under Auto display range, `crop_scale` (`review/features/nanowell/renderer.py`) puts tile crops (raw camera units) and montage crops (8-bit PNG) on one percentile scale, so a montage crop beside tile crops is drawn in the bottom few percent of the ramp and reads as a dark, empty nanowell. | In the Auto branch, compute the montage crops' scale separately and have `CropScale.of` use it whenever one exists; make the caption name both scales. | `tests/test_nanowell_display_range.py` — mixed tile and montage crops with no range draw the montage crop on its own percentiles | `todo` |
| R3 | #1066 | implementation | Low | `compact_number` picks the unit suffix before rounding, so 999.7 is captioned `1e+03` and 999,800 `1e+03k`. | Round to three significant figures first, then choose the suffix from the rounded value. | caption test: `compact_number(999.7) == "1k"`, `compact_number(999800) == "1M"` | `todo` |
| R4 | #1139 | implementation | High | Cells segmented on a seam tile (`seam:N`) are renamed to the real tile the site row names but keep the seam image's own labels, so `(fov, nucleus_id)` collides with that real tile's own cells and two different cells share one `CellKey` (overrides, smFISH write-back, lookups, outlines). | Keep the identity unique when a seam cell is renamed: offset its label past every label the real tiles used at that timepoint. | `tests/test_nanowell_run.py` — cells on the seam and inside the reported tile give unique `(fov, label)` | `todo` |
| R5 | #1131 | science | Medium | The baseline moving-average window is offered under the First-measurable baseline, where every earlier timepoint is by definition unmeasurable, so any window ≥ 2 can only blank every fold change. | `baseline_window_for` returns 0 for First measurable as for Earliest, and the spin box is greyed with that reason. | `tests/test_nanowell_moving_average.py` | `todo` |
| R6 | #1128 | implementation | Medium | A local `Remedy: force` note (`<well>_local_csv_cut_short.txt`, `<well>_local_zip_unreadable.txt`) left by a remote download is never retired by the forced remote re-run it asks for: the host never had the note, so `mirror_retirements` finds nothing to mirror, and the well stays listed as failed. | When the fetcher places a well that the run rewrote from scratch and whose seal was fetched, archive that well's local force notes into `errors/superseded/`. | beside `tests/test_failure_records_retire_when_a_well_completes.py` | `todo` |
| R7 | #1093 | science | Medium | `nanowell_frames.best_step` accepts any step within ±3 that matches the reference timepoint's addresses strictly better than step (0,0), with no margin, so one nanowell the dense reference missed is enough to shift a sparser timepoint's whole lattice by a step (or, on a tie, unaddress the well). | Accept a non-zero step only when step (0,0) leaves a real share of addresses unmatched and the best step's gain clears the same floor; otherwise return (0,0). | beside the step-off tests: the one-missed-nanowell case keeps step (0,0) and verdict AGREE | `todo` |
