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
| C3a | *Finding:* every dataset analysed before this check now says "not checked" in its footers, by design (unverified is not verified). Two page tests pinned the old first footer line and were updated to read the note first; the audit command fills the sidecars of an old dataset | done | `test_nanowell_page_views_ui.py` |

### D — GUI (Nanowells page, Identity view)

| id | item | state | evidence |
|---|---|---|---|
| D1 | Identity view registered; well and timepoint selectors | todo | |
| D2 | Distance map with tile boundaries and verdict | todo | |
| D3 | Blink comparator in the reference frame, addresses drawn | todo | |
| D4 | Side-by-side with linked cursor | todo | |
| D5 | Diagnostics panel: correlation surface, peak, margin, transform | todo | |

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
| E10 | Mutation on the run's own output: with `_settle_steps` disabled, a timepoint anchored a step off is caught and named a step; with it, `confirmed` | done | `test_nanowell_identity_run.py`, two tests |

### Q — open questions (need real data)

| id | question | state | answer |
|---|---|---|---|
| Q1 | Is 0.25 pitch the right tolerance on real plates (thermal / swelling distortion across a 3 mm array)? | todo | |
| Q2 | What margin does a real square give against its lattice-step neighbours? `MIN_MARGIN` is a synthetic choice | todo | |
| Q3 | Plates without a central square: stage cross-check only — is that acceptable? | todo | |

### F — docs

| id | item | state | evidence |
|---|---|---|---|
| F1 | ARCHITECTURE.md section on the identity check | todo | |
| F2 | README_WellViewer.md: the Identity view and the audit command | todo | |
