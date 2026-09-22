# Adversarial review of the converter branch — remediation tracker

Four adversarial reviewers were run over the diff of
`claude/czi-import-convert-7t0ipi` against `origin/main`, partitioned by area:
the ND2 reading layer, the ND2 Datasets page, the shared converter extraction,
and the shared timepoint math. Every finding below was reproduced by running
the code, not by reading it, and the reproduction is named in the row.

This file exists because the body of work is larger than one session can hold
honestly in its head (CLAUDE.md §6.1). It is live state, not a write-up. It is
deleted by the pull request that closes its last row.

## How to read a row

`state` is one of `open`, `done` or `declined`. A row reaches `done` only when
the defect can no longer recur — which means a test that was **watched failing
without the fix** (§7.7 #2), named in the Evidence column. "Implemented" is not
`done`. A row that turns out not to be a defect is `declined` with the reason
written both here and at the site, never silently dropped.

The counts in the summary below are derived from the rows, never typed
independently: if the summary says four are done, four rows say `done`.

## Summary

| Severity | Total | open | done | declined |
|---|---|---|---|---|
| Critical | 4 | 1 | 3 | 0 |
| High | 5 | 4 | 1 | 0 |
| Medium | 4 | 4 | 0 | 0 |
| Found while fixing | 1 | 0 | 1 | 0 |
| Low / hygiene | 4 | 4 | 0 | 0 |
| Test gap | 13 | 11 | 2 | 0 |
| **Total** | **31** | **24** | **7** | **0** |

`tests/test_converter_review_tracker.py` recomputes every number in that table
from the rows below and fails when one drifts, because a hand-maintained total
is the most confidently wrong line in a file like this (CLAUDE.md §6.1 #3).

---

## Critical — wrong images or wrong numbers, written and reported as success

| # | Finding | Site | State | Evidence |
|---|---|---|---|---|
| C1 | `frame_index` hardcodes a position-outer axis order. `nd2` builds its sequence index from the experiment loops in the **file's own** nesting order (`_coord_info` enumerates `self.experiment`; `sizes` is documented as ordered to match `shape`). `read_nd2_layout` reads that ordered dict and discards the order with four `.get()` calls, so nothing in the module can tell a P-outer file from a T-outer one. A multipoint time-course is physically T-outer — which `scan_timepoint_hours`'s own docstring asserts 120 lines away. 10 of 12 images wrong on a T-outer file, archive complete and correctly named, `validation_report()` empty, run reports success. | `pipeline/nd2_read.py:408` | done | `test_the_sequence_index_is_built_from_the_order_not_from_an_assumption` (indices computed by hand from the loop order, not from the reader's formula) and `test_every_coordinate_reads_its_own_plane_whatever_the_loop_order`, both parametrised over PT/TP/PTZ/TPZ/PZT. Reverting `frame_index` to `position * size_t + t` fails 10. |
| C2 | Z is never projected. `AXIS.frame_coords()` is `{S, C, X, Y}` — Z is a coordinate axis, never inside a frame — so `read_frame(i)` returns a single Z plane and `_plane_from_frame`'s `while plane.ndim > 2: plane.max(axis=0)` is unreachable on any real ND2. Because `frame_index` also omits the Z stride, a Z stack additionally corrupts position and timepoint: 4 of 4 wrong on a 2×2×3 file. The log line, the page, `__init__`, ARCHITECTURE §6.2b and the manual all state that Z is max-projected. | `pipeline/nd2_read.py:411-435`, `pipeline/nd2_explode.py:238` | done | `test_a_z_stack_is_max_projected_across_its_own_sequences` plus `test_the_projection_reads_one_frame_per_z_and_no_more`. Reading only Z=0 fails 6. |
| C3 | The elapsed-hour scan samples frames by the same assumed order, so on a T-outer file it walks positions instead of time and reports the stage traverse as the experiment's duration — a 12-hour timecourse named as a 90-second one, with `reasons == []`, so nothing in the log, the page or the archive says anything. The function's own docstring explains that sampling across positions "folds the stage's traverse into the elapsed times it reports", and then does that. | `pipeline/nd2_read.py:314` | done | `test_the_frame_times_are_the_timepoints_own_whatever_the_loop_order[TP]`. Sampling by `t` alone fails it; the scan and the conversion now share `frame_index_for`. |
| C4 | The drift fix regressed the case it was written for, and made long intervals worse than the code it replaced. `tolerance` is computed from the interval alone and never from the series' measured jitter, so a longer interval licenses a **coarser** rounding unit: 300 s for a 48 h run against main's fixed 60 s. Measured A/B, 48 h + 147 s/round × 24 frames: main fabricates 10.35 min, HEAD fabricates **56.35 min**, both with an empty note. A perfectly regular zero-jitter series (238.75 min × 20) is also named 4.75 min away from its readings. | `pipeline/explode_common.py:246-248`, `:176-198` | open | |

## High

| # | Finding | Site | State | Evidence |
|---|---|---|---|---|
| H1 | The jitter tidying — the reason the module exists — is silently lost past roughly 11 frames on an hourly run, because `_rounding_unit`'s gate divides the usable unit by the gap count. Measured through `explode_ome_tiff` on a 12-frame hourly plate with 5 s/round: HEAD writes `11.0153h.tif`, main writes `11h.tif`. This also regresses `domain/tp_match` — `1.00139h` and `1.00278h` do not merge at `TP_REL_TOL = 1e-3`, so two plates of one experiment converted on different days can no longer be concatenated into one timecourse. | `pipeline/explode_common.py:196` | open | |
| H2 | An RGB (or multi-component) ND2 is accepted and written as a destroyed image. `read_nd2_layout` appends a warning but `validation_report` never refuses it, and `plan_conversion`/`_render` only raise when there are warnings **and** no positions, so the Run button stays enabled. `_plane_from_frame` then asks "is the leading axis a Z stack?" of the RGB sample axis — a predicate applied outside its domain (§7.1) — and an 8×8 frame comes back `(8, 3)`. | `pipeline/nd2_read.py:283-286`, `:164-189` | done | `test_an_rgb_file_is_refused_rather_than_written_as_nonsense` and `test_a_frame_that_does_not_resolve_to_one_plane_is_refused`. Demoting the refusal to a warning fails the first. |
| H3 | `nd2_import` is missing from five kind-keyed rosters: `KIND_LABEL`, `CHAIN_TARGET`, `_DESCRIBE_KEYS`, `ADD_KINDS` and `KIND_ICON`. Measured against the real `JobQueueController`: a queued ND2 job's row reads the raw string `nd2_import` with an empty second line and tooltip, an ND2 conversion **cannot be chained** after another job at all, Convert ND2 is absent from the Job Queue's own "+ Add" menu, and its row wears Subset's icon. | `services/job_kinds.py:31-40`, `:73-82`, `:87-95`; `datasets/job_queue/view.py:43`; `datasets/job_queue/row.py:50-58` | open | |
| H4 | The guard written for exactly H3 cannot see it: `tests/test_job_kinds.py` takes its universe from `KIND_LABEL`, which is missing the same row — a roster held against another roster (§7.5 #10). `tests/test_job_queue_view.py:116` holds the menu against `ADD_KINDS` rather than against `_FEATURES`, the same way. Both should derive from `datasets.tab._FEATURES`, as `tests/test_datasets_tab_job_queue.py:93-109` already does correctly. | `tests/test_job_kinds.py:24-26`, `tests/test_job_queue_view.py:116` | open | |
| H5 | A queue-route run reports a finished conversion over an empty plate. `start_from_queue` calls `show_job` then `_launch` synchronously while the layout read is still off-thread, so `_launch` resets a dashboard whose well set is `[]`; every `wellFraction` signal for the whole run is then discarded by `set_well_progress`'s membership guard, and `_render` skips re-seeding because the run is in flight. **Pre-existing on `origin/main` for Convert CZI** — reproduced there: both zips written, outcome `done`, rail `{pending: 2, converted: 0}`, bar `0 / 2`. Convert OME-TIFF is immune because its read is synchronous. This branch propagates it to ND2 but also moves the site into shared code, so the fix is now one place. | `datasets/core/packed_convert.py:301-303`, `datasets/core/converter_dashboard.py:106-108` | open | |

## Medium

| # | Finding | Site | State | Evidence |
|---|---|---|---|---|
| M1 | Convert and + Queue stay enabled for a destination the run will refuse — an already-converted folder, a finished dataset, a mismatched resume marker. `_render` calls `_update_run_gate(True)` without consulting `destination_problem`; only a small field hint says otherwise, and the verdict line reads as an ordinary plan. Queueing one puts a job in the queue guaranteed to fail. `datasets/ome_import/view.py:829` is the in-repo precedent that does it correctly. Shared with the CZI page. | `datasets/nd2_import/view.py:742` | open | |
| M2 | Five distinct well-naming failures collapse to one sentence that asserts two causes which did not occur. `canonical_well_label` returns `None` for `A1_1`, `Point A1`, `XY01` and `I01`, but the message states the names are present and blames field counts and repeats. NIS multipoint naming (`A1_1, A1_2, B1_1, B1_2`) is sent to the grid, discarding a mapping the file genuinely carries; a 384-well file gets no hint that the converter is 96-well only. | `pipeline/nd2_read.py:147-162`, `pipeline/nd2_explode.py:113-122` | open | |
| M3 | A planning-phase `ValueError` is now reported as a refusal with the traceback suppressed. On main the two worker arms had different exception sets by design: planning caught the tool's own error, converting also caught `ValueError` for `resolve_well_map`'s grid mismatch. The extraction flattens both onto one `spec.refusals`. Every *designed* `ValueError` in planning is already wrapped by the compute layer, so the only one that can now arrive is an unexpected one — and it surfaces as a bare sentence with no traceback logged anywhere. | `datasets/core/packed_convert.py:123-136` | open | |
| M4 | `_rounding_unit`'s stated error model is wrong, in the docstring and in ARCHITECTURE: "half of it at every step". The accumulating term is the error in the *median gap* of the rounded series, which is up to a whole unit. It changes no outcome — the real bound comes from the verification — but it reads as if the ladder gate were the guarantee. | `pipeline/explode_common.py:183-185`, `_Docs/Markdowns/ARCHITECTURE.md:6128-6130` | open | |

### Found while fixing, and fixed here

| # | Finding | Site | State | Evidence |
|---|---|---|---|---|
| C5 | `read_projection` returned a **view into the file's memory map**, so a plane used after the reader closed touched unmapped memory — a segfault with no Python traceback, in a caller that did nothing wrong. Pre-existing, and the Z fix made it worse by hiding it: projecting a stack allocates through `np.maximum` and was accidentally safe, leaving the single-plane case — the common one — as the only one that could crash. Found by the new Z test reading a plane after its `with` block, which is what any caller may do. | `pipeline/nd2_read.py`, `read_projection` | done | `test_a_plane_outlives_the_handle_it_was_read_through`, parametrised over 1 and 3 Z planes. Returning the view instead of copying fails both with a clean `AssertionError` — the flag is bound to a bool before asserting, because letting pytest format its message from a dangling array segfaults inside the reporting, and a guard that crashes instead of failing reports nothing. |

## Low / hygiene

| # | Finding | Site | State | Evidence |
|---|---|---|---|---|
| L1 | Dead duplicate constant left by the channel-roles sweep: `ROLE_LABEL_MIN_W` still declared in the OME view with no reader, its only consumer having moved to `datasets/core/channel_roles_row.py`. The same hunk renamed it from `_ROLE_LABEL_MIN_W`, which makes it read as a deliberate export rather than a leftover. | `datasets/ome_import/view.py:78` | open | |
| L2 | Stale prose naming deleted symbols and "two converters" — a rename with no symbol in it still needs the sweep (§7.5 #6): ARCHITECTURE still says `OmeImportWorker` runs the conversion off the UI thread (that class was deleted); `convert_marker`'s docstring says two tools use it; `remote_manifest`'s comment says "both converters"; `analyze/domain/input.py` names only two converter pages; `tests/test_ome_timepoint_stride.py` says "shared between the two converters". | five sites | open | |
| L3 | `_channel_names` swallows into `raw = []` with no `warnings` entry, so a user gets `c0`/`c1` archives with nothing saying the channel names were lost. Every other degradation route in this module records a reason. | `pipeline/nd2_read.py:236-237` | open | |
| L4 | Two unreachable branches documented as if live: the `min(channel, plane.shape[0] - 1)` clamp (the enclosing guard already establishes the bound) and `if scan is None:` in the token resolver (both callers pass `read_times=True`). | `pipeline/nd2_read.py:430`, `pipeline/nd2_explode.py:375` | open | |

## Test gaps — each proved by a mutation that left the lane green

| # | Mutation that survives | Files run | State | Evidence |
|---|---|---|---|---|
| T1 | `tests/_nd2_fixture.py` cannot write a T-outer file or a Z stack (`n_seq = n_pos * n_t`, no loop-order or Z parameter), so no assertion in the ND2 suite can bite on C1, C2 or C3. Deleting the Z projection outright leaves 39 passed; forcing `size_z=1` leaves 76 passed. **This is the root blocker: fix it before any of C1–C3.** | ND2 suite | done | `write_nd2` takes `loop_order` and `n_z`; `plate_value` keys on all four coordinates. Verified against the real library on PT/TP/PTZ/PZT — 96 planes, zero mismatches. |
| T2 | `test_the_frame_index_walks_one_position_s_series_contiguously` asserts the formula against a fixture built to that same formula, so it passes for the broken implementation on every file it cannot write (§7.8 rule 2). `test_every_coordinate_reads_its_own_plane` varies `n_c` (inside the frame) and never the coordinate axes (where the bug is). | `tests/test_nd2_read.py:110`, `:144` | done | Both replaced by the parametrised tests above, which assert hand-computed indices and vary the coordinate axes rather than only `n_c`. |
| T3 | `tests/test_nd2_fixture_round_trips.py:23` asserts `f.sizes == {...}`. Dict equality ignores order, and the order is the one load-bearing fact in that dict. | `tests/test_nd2_fixture_round_trips.py:23` | open | |
| T4 | The check `regularise_hours`' own docstring calls "the load-bearing one" is untested: `if False:` on the snapped-vs-raw verification leaves all 95 tests in the three relevant files green. | 3 files, 95 tests | open | |
| T5 | The single-timepoint fix's user-visible half is untested: deleting both new caller guards leaves 281 tests green, including the page-level files. The only test calls the shared helper; both page tests use `timepoints=3` and never reach the case. | 6 files, 281 tests | open | |
| T6 | The drift test's eight fixtures are chosen where right and wrong agree. Adding rows that are the same designs shifted by seconds of stage overhead fails against unmodified HEAD: 90.25 min × 8 drifts 1.75 min; 241.75 min × 20 drifts 4.75 min. | `tests/test_ome_timepoint_stride.py` | open | |
| T7 | The resumed-progress test uses `fovs = 1`, so dropping the `fovs` factor from the fix leaves 153 tests green while a real 2-well × 4-FOV plate reports 25%. | 5 files, 153 tests | open | |
| T8 | `test_jitter_is_still_cleaned_off_an_hourly_run` is named "hourly", uses a four-hourly series of 3 frames — the only length at which the new code still cleans it — and the pre-fix implementation passes it too, so it distinguishes nothing about this change. | `tests/test_ome_timepoint_stride.py` | open | |
| T9 | `fovs_per_well` is the one user-editable field the queue round-trip test never gives a non-default value, so deleting its restore line leaves 26 passed. | `tests/test_nd2_import_page.py` | open | |
| T10 | `collect_spec` could drop the destination and the round-trip test would not notice — the comparison is symmetric, so an omission is self-consistent on both sides (§7.8 face two). | `tests/test_nd2_import_page.py` | open | |
| T11 | The run marker's *writing* is never tested; the assertion that looks like it tests it (`not marker.exists()` after a run) is satisfied by an implementation that never writes it — which loses every resume. `resuming = False` is green across both files. | `tests/test_nd2_import_page.py:459` | open | |
| T12 | The open-in-Analyze assertion is a vacuous disjunction: `show_open_in_analyze` sets `_dest_root` unconditionally, so the right-hand side is always true. Deleting the `promote_open_button` call, emitting `""`, or handing `{}` instead of the channel tokens all leave 26 passed. No test clicks the button. | `tests/test_nd2_import_page.py:458` | open | |
| T13 | The `jobFinished` emit on normal completion is not tested by anything — the **full fast lane** is green without it, and that line's absence stalls the Job Queue for the session after every conversion. Pre-existing (same deletion on `origin/main` leaves 181 passed); the refactor changes the blast radius to one line serving three tools. Related: `_launch`'s plate reset is also untested (232 green without it), and without it a second conversion opens wearing the first run's full dials. | `datasets/core/packed_convert.py:323-330`, `:232-237` | open | |

---

## What the review cleared

Worth recording, because these were the areas most at risk and they were checked
by running rather than by reading.

**The worker/controller extraction is behaviour-preserving.** An A/B harness
drove the real controllers offscreen through eight paths — no source selected,
a planning refusal, a success, a destination-already-occupied refusal, for both
OME and CZI — and the event streams were byte-identical against a worktree of
`origin/main`, including the exact `jobFinished` tuples and `set_outcome`
states. Method-by-method equivalence against the deleted `worker.py` files was
confirmed for the log line, the read call, the cancellation message, the
`swallowed()` breadcrumb labels, the exception ordering, and every controller
method. The three deliberate differences that were flattened are benign and are
named in the reviewer's report.

**The reuse key is complete.** Every input to a member name was traced, all nine
mapping fields bite individually, and `tests/test_reuse_is_keyed_completely.py`
holds the roster against the settings dataclass with a written reason per key —
the roster-against-a-roster trap is closed correctly there.

**Other cleared areas:** thread safety in the ND2 exploder (32 positions across
8 workers, 192 images, zero mismatches); `wells_from_positions`' run-grouping,
whose every failure mode is a refusal rather than a wrong mapping; the channel
axis identified by length; the `Nd2ReadError` boundary; no token collisions in
20,686 probes; `timepoint_plan`'s stride handling with no off-by-one;
`_ordinal_suffix`; the 300% progress fix, which reproduces and is fixed;
`swallow` vs `swallowed`; all 14 ND2 page controls connected and read back; the
scan generation counter and bridge lifetime, including the staleness test that
was rewritten after its first form was found to measure nothing; `PACKED_ACTIONS`
now derived from its table; layering and the guard family.
