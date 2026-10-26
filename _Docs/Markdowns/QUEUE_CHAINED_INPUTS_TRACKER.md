# Job Queue chained inputs — implementation tracker

**This document is temporary.** CLAUDE.md §6.1 requires a plan of this size to
live on disk rather than in conversation. It is deleted by the pull request that
closes its last row, and that pull request says so in its body.

## What this work is for

A queued job could take its input from one earlier queued job, and only Analyze
could be queued against an input that did not exist yet. The motivating case
could not be queued at all: two CZI scans of one plate are converted, the two
converted datasets are concatenated, and the concatenation is analysed. That
needs four things, which are the four parts below:

1. A job whose inputs come from **more than one** earlier job (Concatenate and
   Merge plates take two datasets).
2. What a converter knows about the files it wrote (filename schema, channel
   roles) **surviving a Concatenate or Merge** on its way to Analyze.
3. Setting up the next job against an output that **does not exist yet**: the
   tool pages accept a pending input, and the queue rows offer "Queue next step
   from this output".
4. Pages that build pickers from the input's contents (Subset's wells,
   timepoints and fields; Analyze's channels) building them from a
   **projection** of what the earlier job will produce, and a run-time check that
   refuses a job whose choices the real output does not contain.

## Rules for this file

- A row moves to `done` in the same commit as its code, and only with the test
  that pins it, watched failing without the fix (§7.7 #2).
- A row that turns out wrong is `declined` with the reason, never deleted.
- A finding made along the way becomes a new row under the row that found it.
- Counts are derived from the rows, never typed.

States: `todo`, `doing`, `done`, `declined`.

## Part 1 — more than one input from earlier jobs

| Row | What | State | Where / evidence |
|---|---|---|---|
| 1.1 | `JobSpec` holds every input link (`links()` / `sources()`); old queue files load unchanged, and a file written now still loads in an older version (which keeps the first link) | done | `services/job_queue.py` `ChainLink`, `JobSpec.links/set_links/sources`; `test_job_queue_multi_input.py::test_both_links_survive_a_save_and_load`, `::test_a_file_from_before_multi_input_links_loads_unchanged`, `::test_an_older_reader_keeps_the_first_link` |
| 1.2 | `JobQueue.set_link(job, target, source)` per input; `set_chain` keeps its meaning; cycle check walks every source | done | `JobQueue.set_link`, `_check_links`; `::test_set_link_keeps_the_other_input`, `::test_a_cycle_through_an_ancestors_second_input_is_refused` (fails with the cycle walk cut to first links) |
| 1.3 | `next_runnable` waits for every source to succeed | done | `next_runnable`; `::test_waits_for_every_source_not_just_the_first` (fails with first-source-only) |
| 1.4 | `materialise` fills every linked input, and names the one it could not | done | `materialise`; `::test_materialise_fills_both_inputs_from_their_own_sources`, `::test_materialise_names_the_input_it_could_not_fill` (both fail with first-link-only) |
| 1.5 | `_cascade_block`: a job is blocked when any source fails; the message names that source | done | `_cascade_block`; `::test_a_failure_of_the_second_source_blocks_the_job` (fails with first-source-only) |
| 1.6 | `_cascade_unblock`: retrying one source unblocks a job only when no other source is still failed or blocked | done | `_cascade_unblock`, `_still_stopping`; `::test_retrying_one_of_two_failed_sources_keeps_the_job_blocked` (fails without the other-source check) |
| 1.7 | Order check, topological repair, `remove` and `clear_terminal` handle every link | done | `_chain_order_valid`, `_topological_order`, `remove`; `::test_order_check_sees_the_second_source`, `::test_topological_repair_respects_every_source`, `::test_removing_one_source_drops_only_its_link` (each fails under its mutation) |
| 1.8 | `services/job_kinds.INPUT_TARGETS` (two inputs for Concatenate and Merge plates); `QueueBridge.set_link` | done | `job_kinds.INPUT_TARGETS`, `QueueBridge.set_link`; `test_job_kinds.py::test_the_first_input_is_the_one_run_after_fills`, `test_job_queue_multi_input.py::test_the_bridge_links_two_conversions_into_one_concatenate` |

## Part 2 — a converter's layout survives Concatenate / Merge

| Row | What | State | Where / evidence |
|---|---|---|---|
| 2.1 | `produced_layout` passes the inputs' layout through Concatenate, Merge plates, Subset and Validate when every input's layout is known and they agree; otherwise records none | done | `job_kinds.LAYOUT_PRESERVING_KINDS`, `produced_layout(kind, spec, upstream)`; `test_layout_passes_through_the_queue.py` unit cases (agree / disagree / one from disk / short list) |
| 2.2 | `QueueBridge._on_job_finished` hands `produced_layout` the layout of each input's source job | done | `QueueBridge._input_layouts`; `test_layout_passes_through_the_queue.py::test_analyze_after_a_concatenate_of_two_conversions_gets_their_layout` fails with the bridge passing no upstream |

## Part 3 — set up the next job against an output that is not written yet

| Row | What | State | Where / evidence |
|---|---|---|---|
| 3.1 | `planned_output(kind, spec)`: the folder a queued job will write, or `None` when it cannot be known before it runs | todo | |
| 3.2 | `PathField` pending mode: shows the planned path and why it is not there yet, counts as valid | todo | |
| 3.3 | Concatenate page accepts pending inputs; the disk-reading checks (gap, in-place) wait for the run; Run is off | todo | |
| 3.4 | Merge plates page accepts pending inputs | todo | |
| 3.5 | Validate and Subset pages accept a pending input | todo | |
| 3.6 | Analyze's input accepts a pending output and says so | todo | |
| 3.7 | `QueueSession.begin_next_step`: opens the tool with inputs pre-filled; Save links each input still holding a planned path, so Browse-ing a real folder drops that link and swapping the two fields swaps the links. Editing a linked job shows its links the same way | todo | |
| 3.8 | Row menu: "Queue next step from this output ▸" (with "Concatenate with ▸ / Merge plates with ▸" naming the second job); "Dataset 1 from ▸ / Dataset 2 from ▸" on two-input jobs; the row's second line names each input's source | todo | |
| 3.9 | Concatenate page: Swap dataset 1 and 2 | todo | |

## Part 4 — pickers from a projection, checked at run time

| Row | What | State | Where / evidence |
|---|---|---|---|
| 4.1 | `projected_inventory(kind, spec, upstream)`: wells / fields / timepoints / channels a queued job will produce, each `None` when it cannot be known | todo | |
| 4.2 | Converter jobs record what their page scanned (wells, fields, timepoints, channels) at queue time | todo | |
| 4.3 | Subset page with a pending source builds its pickers from the projection; an unknown dimension says "all — decided when it runs" and queues as all | todo | |
| 4.4 | Analyze form with a pending input is pre-filled from the projected layout | todo | |
| 4.5 | A Subset job naming wells, timepoints or fields its real source lacks fails, naming them, instead of quietly running on fewer | todo | |

## Closing

| Row | What | State | Where / evidence |
|---|---|---|---|
| C.1 | ARCHITECTURE.md Job Queue section rewritten for multi-input links, pass-through layout, pending inputs, projection | todo | |
| C.2 | README_WellViewer.md: queueing a next step, two-input chains | todo | |
| C.3 | Version bump, guard family, touched test files, fast lane | todo | |
| C.4 | Reconcile every row against the tree; delete this file | todo | |
