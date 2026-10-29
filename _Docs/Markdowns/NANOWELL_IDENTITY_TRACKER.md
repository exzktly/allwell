# Nanowell identity over time — implementation tracker

**This document is temporary.** CLAUDE.md §6.1 requires a body of work this size to live on disk. It is deleted
by the pull request that closes its last row, and that pull request says so in its body.

## Why this exists

Review joins a well's timepoints by nanowell address `(q, r)`. If the address a timepoint was given is wrong —
a lattice step off, the lattice turned by one of its own symmetries, mirrored, or one tile of the montage placed
a step off — every fold change and trajectory pairs a nanowell with a neighbour from that timepoint on, and the
output looks exactly like good output. That is the most damaging silent failure the nanowell path can produce.

Every check the run makes today is built from the same geometric evidence it addressed with: the overlap of the
detected address sets (`pipeline/nanowell_frames.best_step`), the pitch / angle / roster consistency
(`nanowell_sites.check_consistency`, which compares angles **modulo 60°**), and the square's orientation check.
Those agree with each other by construction. Three failures they cannot see:

1. **A turn by a lattice symmetry.** A circular roster turned 60° (hexagonal) or 90° (square) is the same set of
   addresses, and the angle check folds the turn away. A well anchored on its array centre has nothing else.
2. **One tile a lattice step off inside the montage.** Only that tile's nanowells are relabelled; the well-wide
   overlap still prefers no shift.
3. **A decoy square.** Each timepoint looks for its own square (`score_fiducial`, square-versus-circle score);
   two timepoints can each accept a square-looking thing that is not the same physical feature.

## The check (design — rewritten, never annotated)

The central square is the array's only aperiodic landmark. So each timepoint's **full-well montage is registered
onto a reference timepoint's by cross-correlating the square**, the registration is independent of the
addresses either timepoint was given, and then **every address is mapped through it and must land on itself**.

**Reference timepoint.** The earliest timepoint whose square was accepted by the run (its grid block's
`fiducial.accepted`, i.e. the square passed the margin and ratio tests). Every other timepoint is compared with
it (not with its predecessor), so the verdicts compose: if each timepoint agrees with the reference they all
agree with each other. With no such timepoint the well is `inconclusive` throughout, with that reason.

**Lattice-suppressed correlation.** A plain correlation of the square's patch peaks almost as high one lattice
step away, because a round nanowell shares the square's surroundings. So both montages are first replaced by
their *lattice residual* — the image minus the mean of itself shifted by each nearest-neighbour lattice vector
(six on a hexagonal lattice, four on a square one), using that timepoint's own fitted pitch and angle. The
periodic structure cancels; what is left is the aperiodic part: the square (which differs from every
neighbour), cells, debris, the array's rim. The reference's residual patch around its square is correlated
(normalised cross-correlation) over the other timepoint's residual inside a search window, after turning the
patch by the measured rotation and scaling it by the pitch ratio.

- **Search window**: centred on where the reference square lands in the other montage under the frame-to-frame
  prior (the common frames of two timepoints are both the acquisition's tile grid, so a still plate puts it at
  the same common-frame point), radius `SEARCH_PITCHES` (4) pitches. A plate moved further than that is
  `inconclusive`, never guessed.
- **Peak** and **margin**: the best correlation, and its lead over the best correlation more than half a pitch
  from it. Margin below `MIN_MARGIN` is `inconclusive`: something else in the window looks as much like the
  square.
- **Agreement with the run's own square**: the distance from the correlation peak to the square the run
  accepted at that timepoint, when it accepted one. Reported; a disagreement over a quarter pitch is itself a
  failure (a decoy), whatever the address test says.

**Rotation, scale, mirror.** Rotation is the difference of the two timepoints' fitted lattice angles folded into
(−30°, 30°] (hexagonal) or (−45°, 45°] (square): the plate is put back nearly as it was, and a larger turn is
indistinguishable from a symmetry by the angle alone — which is exactly why the addresses are then tested on
positions. Scale is the ratio of fitted pitches. A difference in the `mirrored` flag is `contradicted` outright.

**Address test.** Each address present at both timepoints (`nanowells/<well>.csv` `well_x_um`, `well_y_um`,
common frame) is mapped through `x' = s·R(x − square_ref) + square_found`. Its **distance** to the same address
at the other timepoint must be at most `TOLERANCE_PITCHES` (0.25) of a pitch; and the nearest site at the other
timepoint to the mapped point must carry the same address. A step error moves every site about a pitch; a turn
moves them in proportion to their distance from the square.

**Diagnosis of a failure.** From the address each mapped site actually lands nearest: one constant address step
for all → `step (dq, dr)`; the landed addresses matching one non-identity element of the lattice's point group
(rotations by 60°/90°, reflections) → `rotation` / `reflection`, with the angle; failures confined to one owner
tile → `tile <id>`; anything else → `scattered`, with the failing addresses listed.

**Verdicts.** `confirmed` (square found with margin, every compared address within tolerance and nearest to
itself), `contradicted` (any address failing, a mirror mismatch, or the correlation peak away from the run's
accepted square), `inconclusive` (no reference square, no montage, margin too low, the plate moved beyond the
search window, an unaddressed timepoint, fewer than `MIN_COMPARED` addresses in common), `reference` for the
reference itself. A timepoint never tested reads `not_checked`. **Only `confirmed` and `reference` are good.**

**Stage cross-check (plate level).** Where every well's tiles carry stage positions, each timepoint's address
origin in stage coordinates (`origin_stage_um`) is fitted across wells to the reference timepoint's by the
plate's rigid motion (`nanowell_lattice.fit_plate_rigid`); a well whose origin is off by more than the same
tolerance is flagged. It shares no evidence with the image check.

**Where it lives and what it writes.** `pipeline/nanowell_identity.py` (Qt-free, in the remote manifest) reads
only what the run writes — the montage PNGs in `<well>_out.zip`, `nanowells/<well>_grid.json` and
`nanowells/<well>.csv` — so the run and an offline audit of an existing dataset run the same code on the same
inputs. It writes `nanowells/<well>_identity.json`, carrying a digest of the sites table and grid record it
checked: a sidecar whose digest no longer matches (a relabel, a concatenation, a re-run) is read as
`not_checked`, never as the stale verdict.

**Downstream.** Review reads the sidecars. A `contradicted` timepoint is left out of every cross-timepoint join
(fold change, trajectories, starting cells), with `exclusion_reason = identity_contradicted`; a figure drawing a
well whose timepoints are `inconclusive` or `not_checked` says so in its footer.

**GUI.** A new **Identity** view in the Nanowells page, one well at a time, comparing a chosen timepoint with the
reference: a **distance map** (each nanowell coloured by its distance in pitches, tile boundaries drawn), a
**blink comparator** (the two montages alternating in the reference frame, addresses drawn on both), a
**side-by-side** with a linked cursor (hover an address on one, its mapped and its own position light on the
other), and a **diagnostics** panel (the correlation surface with its peak and margin, the transform, the
verdict and reason). The plate's per-well chips show the worst verdict.

## Rows

States: `todo`, `wip`, `done` (implemented and pinned by a test watched failing without it), `blocked`,
`declined` (with the reason). Counts are read from the rows, never typed.

### A — the check (`pipeline/nanowell_identity.py`)

| id | item | state | evidence |
|---|---|---|---|
| A1 | Lattice residual image (image minus mean of its nearest-neighbour lattice shifts) | done | `test_nanowell_identity.py::test_the_square_is_told_from_its_neighbours_only_after_the_lattice_is_cancelled` |
| A1a | *Finding:* is the residual needed? Measured on the rendered pair: plain correlation of the square peaks 0.983 against 0.975 one step away (margin 0.007); on the residual 0.552 against 0.234 (margin 0.318) | done | same test: the plain margin is asserted under 0.05 and the check `inconclusive` without the residual |
| A2 | Square registration: NCC of the reference's residual patch, turned and scaled, over the other's residual; peak, margin, surface | done | `test_a_moved_and_turned_plate_with_moved_cells_is_confirmed`, `test_a_plate_moved_two_pitches_is_still_found` |
| A3 | Rotation from folded lattice angles, scale from pitch ratio, mirror mismatch | done | `test_a_mirrored_lattice_is_contradicted` (mutation: removing the mirror test fails it); rotation asserted 2.0° in the clean pair |
| A4 | Address test: distance and nearest-address, tolerance 0.25 pitch | done | mutation `if d > TOLERANCE_PITCHES or landed != a` → `if False` fails the step, all four hexagonal symmetry cases, the square-lattice turn and the audit test |
| A5 | Diagnosis: step, rotation / reflection, tile, scattered | done | step / symmetry / tile tests assert the diagnosis by name |
| A5a | *Finding:* a tile placed a lattice step off moves its sites' positions and addresses together, so every address still lands on itself in the common frame: the address test is blind to it. Added A9 | done | `test_one_tile_a_step_off_is_contradicted_and_named` |
| A6 | Verdicts and reasons; reference selection | done | `test_a_well_with_no_accepted_square_is_inconclusive_throughout`, `test_an_unaddressed_timepoint_is_inconclusive_not_confirmed` |
| A7 | Sidecar `<well>_identity.json` with input digest; stale sidecar reads `not_checked` | done | `test_the_sidecar_is_written_read_and_goes_stale_with_its_tables` (mutation: disabling the digest check fails it) |
| A8 | Stage cross-check across wells | done | `test_the_stage_cross_check_flags_the_well_off_the_plate_s_motion` |
| A8a | *Finding:* with four wells the rigid fit absorbs a well a pitch off (172 um became residuals of 26 to 103 um on all four) and accused a good well. `STAGE_MIN_WELLS = 5` | done | `test_the_stage_cross_check_accuses_no_well_on_too_few_wells` |
| A9 | Tile layout test: each tile's registered position relative to the grid must not change between timepoints by a quarter pitch | done | mutation `"ok": not moved` → `"ok": True` fails the tile test |
| A10 | Decoy square: the correlation peak must sit on the square the run accepted | done | mutation removing the agreement test fails `test_a_decoy_square_the_run_accepted_is_contradicted` |

### R — adversarial review of the committed check (4e2f6ba)

Each row is the reviewer's finding as measured on rendered plates (scripts under the session scratchpad), and what
was done about it.

| id | finding | state | evidence |
|---|---|---|---|
| R1 | **False confirm.** Half the tile grid a step off (a row or a column of a 2 × 2, one tile of two) puts the median between the two groups, every tile departs by half a pitch, and "more than half moved" returned *does not apply* — `confirmed`. The single-tile test passes against that code | done | largest group of agreeing tile motions, not a median; two equal groups contradicted; >3 pitches = another grid → `inconclusive`. `test_half_the_grid_a_step_off_is_contradicted_not_waved_through` (row, column, other column), `test_one_of_two_tiles_a_step_off_is_contradicted`, `test_tiles_from_another_grid_withhold_a_confirmation`; the original median + "more than half" code fails all four |
| R2 | **False confirm (batched remote).** The final pass's `stage_check` wrote through `read_sidecar(verify=True)`, which fails once a released well's `_out.zip` is deleted on the remote: the stage block was dropped silently, and the well read its image verdict locally | done | stage block written with `read_sidecar(verify=False)`; the digest is checked where it is read. `test_the_stage_block_survives_a_released_well_s_archive` (mutation back to `verify=True` fails it) |
| R3 | **False accusation.** `stage_check` fitted one rigid motion across both plates of a Merge Plates dataset (every one of 12 wells flagged, 560–700 um) and used plate-rigid origins (circular) | done | per `plates_of`, only `_INDEPENDENT` origins. `test_each_plate_of_a_merged_dataset_is_fitted_on_its_own`, `test_plate_rigid_origins_take_no_part_in_the_stage_check` (each fails under its mutation) |
| R4 | **Wrong blame.** A reference addressed a step off made every correct timepoint `contradicted` and kept the wrong one | done | candidates whose square carries (0, 0) first, then a majority fallback; a tie contradicts both groups. `test_a_reference_addressed_off_its_own_square_is_not_the_reference`, `test_the_majority_decides_when_every_square_carries_its_origin` (fails with the fallback disabled), `test_two_timepoints_that_disagree_alike_are_both_contradicted`. The (0, 0) preference alone is not pinned: with it removed the majority fallback reaches the same answer on the three-timepoint fixture |
| R5 | A plate turned beyond the fold (37°, 40°, 47° hex) without the square anchoring: `inconclusive` (peak 0.18–0.19) only by 0.11 of headroom; the registration takes rotation from the same folded angle | done | `HEX_TURNS_DEG`: ±60°, ±120° beyond the fold are tried when the folded turn finds no square. `test_a_hexagonal_plate_turned_past_the_fold_is_caught` (fails with no turns). 180° stays the stated limitation (S2) |
| R7a | *Finding while fixing R7:* `test_nanowell_identity_run.py` moves a timepoint's fitted array centre a whole pitch to make it anchor a step off; with the centre landmark that timepoint reads `inconclusive` (centre 1.0 pitch off) though every address lands on itself — correct for that evidence. A 0.55-pitch move leaves the timepoint unaddressed instead, so the test keeps the whole pitch and asserts no failing address, the centre disagreement and the inconclusive verdict | done | `test_a_timepoint_anchored_a_step_off_and_put_right_by_the_run_lands_on_itself` |
| R5a | `inconclusive` rows are still joined in every figure; the README did not say so | done | README says inconclusive and not-checked rows are joined; ARCHITECTURE says only contradicted are left out |
| R6 | A *physical* 180° turn of the array (labelled as the run would) is `confirmed`: hexagonal lattice plus square share the 180° symmetry, nothing in the images can tell. E3 tests only a relabel. A limitation, to be stated | done | limitation stated in the module docstring, ARCHITECTURE §6.2i and the README; E3 is a relabel test, said there |
| R7 | The only landmark is the run's own square: a decoy accepted at both timepoints that moves a site between them is followed by the correlation and `confirmed`. Add the array's rim-fitted centre as a second landmark | done | `CENTRE_AGREEMENT_PITCHES` 0.75 withholds the confirmation (inconclusive, not contradicted: a fitted rim is loose by up to half a pitch). `test_a_rim_centre_that_disagrees_with_the_square_withholds_a_confirmation` (fails with the gate removed). The run-output test's mutated array centre is a whole pitch, so that timepoint now reads inconclusive — see R7a |
| R8 | Memory: every montage held at once as float32 (67 MB each at 4096², ~1 GB for 15 timepoints per worker) | done | `montage_loaders`, `Timepoint.pixels`/`release`. `test_a_well_s_memory_does_not_grow_with_its_timepoints`: peak grows by under 1.5 montages from 2 to 8 timepoints; with `release` a no-op it grew by 6.5 |
| R9 | `long_format` still exports the raw per-site columns (fused, fractions, area estimate) of `identity_contradicted` rows | done | `long_format` blanks the measured columns of `identity_contradicted` rows. `test_the_export_carries_nothing_measured_at_a_contradicted_timepoint` (fails without it) |
| R10 | `choose_reference` does not require the reference's status to be `ok` | done | `reference_candidates` requires status `ok`. `test_an_unaddressed_timepoint_is_never_the_reference` |
| R11 | The stage tolerance (0.25 pitch) is uncalibrated and a flag overrules a good image verdict | blocked | needs a real plate's stage repeatability: Q5 |

### S — registration stress test (renders swept over turn, shift, noise, blur, cells, bubbles, lighting, decoys, pixel size, format)

Measured by a reviewer on HEAD 4e2f6ba (scripts and logs under the session scratchpad `stress/`).

| id | finding | state | evidence |
|---|---|---|---|
| S1 | **False confirm.** The square hidden at the other timepoint and a square-looking nanowell 2–3 sites away taken by the run: the correlation follows it and the timepoint is `confirmed` (margin 0.17–0.31). No threshold separates it: the decoy is the best square there. The rim-centre landmark (R7) put the mapped centre 1.75 and 3.0 pitches off | done | a rim-centre disagreement of 1.5 pitches or more contradicts (`CENTRE_CONTRADICTS_PITCHES`). Re-measured: decoys (1,1), (2,−1), (3,0) contradicted, (1,0), (0,1) inconclusive; none confirmed. `test_a_decoy_followed_two_sites_off_is_contradicted_by_the_array_s_outline` (fails with the rule removed). The converse — run right, square hidden, correlation on a decoy — contradicts correct rows (fails safe; stated in ARCHITECTURE) |
| S2 | **False confirm (assumption).** Plate turns that are symmetries of lattice and square (hex 175–185°, square-lattice 90°, 180°, 270°, and 46° folded to −44°) are confirmed under the folded labels; turns between (hex 31–120°) fail safe as inconclusive. The check assumes a turn under 30° (hex) / 45° (square) between timepoints | done | stated as an assumption in the module docstring, `register_on_square`, ARCHITECTURE and the README. Re-measured: hex 175/180/185° and square-lattice 46° folded labels still confirmed — the limitation, not fixable from the images |
| S3 | False inconclusive: camera noise 0.2–0.5, blur 6 px, 1000+ cells at both timepoints or colonies over the square, the 50 um format at 4.3 and 2.15 um/px — the lock was right, the peak under `MIN_PEAK`. A difference-of-Gaussians band-pass (0.04 / 0.25 pitch) on the residual took 14 of 28 good pairs confirmed to 25 of 28 | done | `aperiodic` = residual band-passed at 0.04 / 0.25 pitch. `test_a_noisy_blurred_crowded_pair_is_still_confirmed` (fails with the band-pass removed). Still inconclusive (safe) after it: a bubble 1.5–2.5 pitches away (margin 0.03–0.11), 1000 cells at both, the 50 um format with noise 0.2 or 1000 cells |
| S4 | `MIN_PEAK` is the gate rejecting good data; margin is what separates real from null (null peaks ≤ 0.22, margins ≤ 0.056; clean margins median 0.21, minimum 0.150). The docstring's "leads by 0.4 and more" is wrong | done | margin is the gate: `MIN_MARGIN` 0.10, `MIN_PEAK` 0.15. Re-measured on the band-pass: good margins 0.317–0.534 (18), null ≤ 0.057 (48). The docstring's figure is corrected |
| S5 | False contradicted: a pitch estimate 3 % off (max 0.25 pitch at the rim), an angle 1.5° off (0.23 pitch). Refit rotation and scale from sites matched by nearest position, translation pinned at the square | done | `refine`: turn and scale refitted on position-paired sites, square pinned, within 2° / 5 %. `test_a_pitch_estimate_three_percent_off_is_refitted_not_accused` (fails without it), `test_the_refit_cannot_absorb_a_wrong_name` |
| S6 | False contradicted: one site of 265 moved 0.26 pitch flips the timepoint, and is diagnosed "tile"; site noise 15 um fails 27–33 sites | done | a site landing nearest itself beyond the tolerance is `loose`; up to max(2, 2 %) do not contradict. `test_one_site_a_quarter_pitch_out_is_not_a_wrong_well` (fails with every failure contradicting) |
| S7 | A peak on the edge of the search window (shift 4.05–4.35 pitches) is taken, 0.06–0.46 pitch off; false alarm only | done | a peak on the window's edge is inconclusive. `test_a_peak_on_the_edge_of_the_search_is_not_a_lock` (fails without it) |
| S8 | The reference's square needs ~3.6 pitches of montage around it, so one within 3 pitches of the edge is never checkable | declined | the square is the array's centre and a montage spans the array, so a square within 3 pitches of the montage's edge means the array is cut off there; padding the crop would correlate continued edge pixels, a lock risk this check exists to avoid. Such a timepoint stays inconclusive with the reason |

### B — integration

| id | item | state | evidence |
|---|---|---|---|
| B1 | Run the check at the end of each nanowell well (`nanowell_task._finish_well`), log a warning per failure | done | `test_nanowell_identity_run.py::test_a_run_writes_a_confirmed_identity_sidecar` (through `process_microscopy.main`) |
| B1a | Stage cross-check at the end of the plate (`nanowell_endgame`) | done | runs after the plate diameters; exercised by the same end-to-end run (one well: reference none, nothing flagged) |
| B2 | Remote fetch and batch-resume carry `<well>_identity.json` | done | `test_remote_fetch_nanowells.py` lists and pulls it |
| B3 | Offline audit: `python -m pipeline.nanowell_identity <output_dir>` over an existing dataset | done | `test_the_audit_command_writes_sidecars_and_exits_by_the_worst_verdict` |
| B4 | Remote manifest lists the module | done | `services/remote_manifest.py`; `test_remote_manifest.py` |

### C — downstream

| id | item | state | evidence |
|---|---|---|---|
| C1 | Review reads the sidecars into `NanowellData.identity` | done | `test_nanowell_identity_review.py::test_a_contradicted_timepoint_joins_nothing_and_says_why` |
| C2 | Cross-timepoint metrics leave out `contradicted` timepoints (`identity_contradicted`), and an address only such a timepoint holds is not followed | done | same file, two tests; mutation (no timepoint marked) fails both |
| C3 | Figures footnote wells with `contradicted`, `inconclusive` or `not_checked` timepoints, through `controller.draw_view` (the page and Batch Export) | done | `test_the_page_s_figures_carry_the_note`, `test_the_footer_note_follows_the_verdicts`; mutation removing the note fails it |
| C4 | *Asked for:* the line plots label a timepoint that is not confirmed where it is — a hatched band with the verdict written in it, the lines through it dashed — not only in the footer | done | `test_nanowell_identity_review.py`: `test_trajectories_hatch_an_inconclusive_timepoint_and_dash_the_lines_through_it`, `test_a_contradicted_timepoint_is_cross_hatched_and_its_gap_explained`, `test_a_well_never_checked_is_said_once_not_hatched_everywhere`, `test_the_distribution_over_time_hatches_the_swarm_and_dashes_the_median` |
| C3a | *Finding:* every dataset analysed before this check now says "not checked" in its footers, by design (unverified is not verified). Two page tests pinned the old first footer line and were updated to read the note first; the audit command fills the sidecars of an old dataset | done | `test_nanowell_page_views_ui.py` |

### D — GUI (Nanowells page, Identity view)

| id | item | state | evidence |
|---|---|---|---|
| D1 | Identity view registered; well and timepoint selectors | done | `_identity_controller`, `_identity_render`, `identity_view`; `test_nanowell_identity_view.py` (21 tests) incl. one through the real page builder; removing `redraw`'s or `draw_view`'s identity branch fails it |
| D2 | Distance map with tile boundaries and verdict | done | mutations: distances at the compared positions, moved tile not mapped back — each fails a test |
| D3 | Blink comparator in the reference frame, addresses drawn | done | mutations: no resampling (agreement −0.13), labels not mapped back (gap 0 not 1 pitch), timer never started, tick ignoring a hidden page — each fails a test |
| D4 | Side-by-side with linked cursor | done | mutations: hover predicting the compared site itself, hover not routed from the canvas — each fails a test |
| D5 | Diagnostics panel: correlation surface, peak, margin, transform | done | mutation: peak marked at the expected square fails a test. The well combo shows each well's worst verdict on this view |

### E — evidence that the check works

| id | item | state | evidence |
|---|---|---|---|
| E1 | Clean pair (moved and turned plate, cells redrawn) is `confirmed` | done | `test_a_moved_and_turned_plate_with_moved_cells_is_confirmed` |
| E2 | Whole-well step is `contradicted`, diagnosed as that step | done | `test_a_whole_well_step_is_contradicted_and_named` |
| E3 | 60° / 120° / 180° (hex) and 90° (square) symmetry turn is `contradicted`, diagnosed as a turn | done | `test_a_turn_or_reflection_by_the_hexagonal_lattice_s_symmetry_is_contradicted`, `test_a_90_degree_turn_of_a_square_lattice_is_contradicted` |
| E4 | Reflection and mirror are `contradicted` | done | same parametrised test (reflection), `test_a_mirrored_lattice_is_contradicted` |
| E5 | One tile a step off is `contradicted`, diagnosed as that tile | done | `test_one_tile_a_step_off_is_contradicted_and_named`, `test_failures_confined_to_one_tile_are_diagnosed_as_that_tile` |
| E6 | Decoy square accepted by the run is `contradicted` | done | `test_a_decoy_square_the_run_accepted_is_contradicted` |
| E7 | No square at the reference / anywhere is `inconclusive` | done | `test_a_timepoint_where_the_square_cannot_be_seen_is_inconclusive`, `test_a_well_with_no_accepted_square_is_inconclusive_throughout` |
| E8 | A stale sidecar reads `not_checked` | done | `test_the_sidecar_is_written_read_and_goes_stale_with_its_tables`, `test_a_sidecar_for_other_tables_is_not_believed` |
| E9 | End to end through `process_microscopy.main`: the sidecar is written and `confirmed` | done | `test_nanowell_identity_run.py::test_a_run_writes_a_confirmed_identity_sidecar` |
| E10 | Mutation on the run's own output: with `_settle_steps` disabled, a timepoint anchored a step off is caught and named a step; with it, every address lands on itself (inconclusive only by the moved rim, R7a) | done | `test_nanowell_identity_run.py`, two tests |

### Q — open questions (need real data)

| id | question | state | answer |
|---|---|---|---|
| Q0 | *How the Q rows get answered:* `python -m pipeline.nanowell_identity <output> --numbers` prints the measurements per (well, timepoint) as CSV, numbers only, so a real plate's can be shared and the thresholds set from them | done | `test_the_numbers_table_carries_the_measurements_and_no_sentences` |
| Q1 | Is 0.25 pitch the right tolerance on real plates (thermal / swelling distortion across a 3 mm array)? | todo | |
| Q2 | What margin does a real square give against its lattice-step neighbours? `MIN_MARGIN` (0.10) is set from rendered pairs: good 0.317–0.534, null ≤ 0.057 | todo | |
| Q3 | Plates without a central square: stage cross-check only — is that acceptable? | todo | |
| Q4 | Is 0.75 pitch right for the rim-fitted centre's agreement (`CENTRE_AGREEMENT_PITCHES`)? Real fits were up to 0.47 pitch loose | todo | |
| Q5 | The stage cross-check's tolerance: what is a real stage's repeatability over a plate re-seat? | todo | |

### F — docs

| id | item | state | evidence |
|---|---|---|---|
| F1 | ARCHITECTURE.md section on the identity check | done | §6.2i *Identity over time*, §6.2j *Joining*, §6.2k *The Identity view* |
| F2 | README_WellViewer.md: the Identity view and the audit command | done | *Nanowells*: the verdicts, the footers, the Identity view, the audit command |
