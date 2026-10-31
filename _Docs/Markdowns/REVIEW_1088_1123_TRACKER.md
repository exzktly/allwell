# Review of PRs #1088–#1123 — findings and their remediation

A remediation tracker (CLAUDE.md §6.1), written before any code. It holds every finding of an adversarial review
of the 34 pull requests merged to `main` between 2026-10-28 and 2026-10-31 (#1088 to #1123), reviewed against
`main` at `9d322ae` for correctness, usability, and adherence to CLAUDE.md and ARCHITECTURE.md. One row per
finding, updated in the same commit as its fix, closed only with evidence (a test watched failing without the fix),
and deleted by the pull request that closes its last row.

**Status, derived from the rows below:** 51 rows — 51 `todo`, 0 `done`, 0 `declined`.
Recount with `grep -c '| \`todo\` |'` and so on; never edit this line by hand alone.

Each finding was a hypothesis about the tree at `9d322ae`. "Established" means the reviewer ran a script or a
mutation that showed it; "inferred" means it was found by reading. Reproduce a finding against the current tree
before fixing it, and record what was measured when a row turns out to be only partly right. Line numbers are as
of `9d322ae` and will drift.

## High

| # | PR | Finding | Where | Basis | State | Evidence |
|---|---|---|---|---|---|---|
| H1 | #1119 | An incremental run of the empty reference well raises `TypeError`: `verdict` compares `n_cells > 0` on site rows read back from CSV as strings. `reference.json` is not written for the new timepoint and every other well finishes it with blank confluence, sealed complete | `pipeline/nanowell_reference.py:93-94`, fed by `pipeline/nanowell_task.py:826-828` (`_prior_state` → `_read_rows`) | Established | `todo` | |
| H2 | #1119 | A remote re-run without the reference well is not sent `nanowells/_confluence_reference/`, so the re-run wells come back with confluence `None` and the fetch overwrites good local values. A batched resume drops the locally complete reference before `nanowell_reference_first`, with the same effect | `services/remote_plate_grids.py:40-50`, `services/remote_process.py:985-993` | Established | `todo` | |
| H3 | #1116 | Cell Gating subtracts every cell `df_included_mask` leaves out, including location layers (Crop, Field, Excluded_Field, FOV, Focus). A nanowell whose cells are all removed for where they are becomes `measured` with `n = 0` and `count_fc = 0` instead of being excluded with a reason | `review/features/nanowell/cell_gate_compute.py:123`, `series_compute.py:242` | Established | `todo` | |
| H4 | #1093/#1096 | A relabel that fails after rewriting the cell tables but before the sites table and grid is replanned identically on retry, so the cells move a second lattice step and the new backup saves already-moved cells | `datasets/validate/nanowell_relabel.py:343-350`, `datasets/validate/apply.py:162-166` | Established | `todo` | |
| H5 | #1100 | The early job's output feeds the run's `RunEvidence`; a slurmstepd CANCELLED line from an early job ended during staging is kept as the run's termination and checked before success, so the run is reported as ended by the scheduler, naming the early job | `services/pipeline_runner.py:2523`, `services/run_evidence.py:125`, `services/pipeline_runner.py:2645-2647` | Established (message); inferred (success reported as failure) | `todo` | |

## Medium

| # | PR | Finding | Where | Basis | State | Evidence |
|---|---|---|---|---|---|---|
| M1 | #1104 | `no_node_fits` is set by srun's "Requested node configuration is not available" and never cleared when the wrapper recovers from it (branches 3a, 3c′); a later queue-wait timeout or ssh drop is headlined "Slurm has no node that matches … change the scheduler command" | `services/run_evidence.py:137,155` | Established | `todo` | |
| M2 | #1100 | An early job that has ended keeps showing "Waiting for the scheduler" until hand-over, and Max queue wait restarts from zero on the new request; README says the run fails while still uploading | `services/remote_reservation.py:289,327`; `README_WellViewer.md:1774` | Established | `todo` | |
| M3 | #1105 | A tile placed a lattice step off at the reference timepoint is not excluded on the reference side; addresses at the tile edge are joined to different physical nanowells at later timepoints | `pipeline/nanowell_identity.py:887-899` (`_excluded`) | Established (synthetic); frequency inferred | `todo` | |
| M4 | #1105 | `IDENTITY_VERSION` stayed 1 while verdict rules changed, so sidecars written by the earlier rules are reused on unchanged tables and keep verdicts the new rules reverse | `pipeline/nanowell_identity.py:75` | Established | `todo` | |
| M5 | #1103 | The Identity view's remedy for "not checked" is `python -m pipeline.nanowell_identity <out>`, which the packaged app cannot dispatch; no Datasets tool carries or recomputes the sidecar | `review/features/nanowell/identity_compute.py:96,225`; `all_well_launcher.py` | Established | `todo` | |
| M6 | #1103 | `--well` passes the subset to `stage_check`, which overwrites the plate-level stage verdict (fewer than 5 wells) or refits on a subset; a stage contradiction becomes `confirmed` | `pipeline/nanowell_identity.py:1289-1292`, `:1201` | Established | `todo` | |
| M7 | #1119 | Per-well reuse is keyed on the reference well's name only, not its content or presence: a failed reference leaves wells sealed with blank confluence and skipped as up to date; a re-exported reference mixes old and new confluence invisibly | `NanowellSettings.key()`, `KEYED_ON["nanowell_empty_well"]` | Inferred | `todo` | |
| M8 | #1119 | `nanowell_reference_first` has no test; deleting the reorder leaves the suite green | `services/remote_batch_staging.py:82-93` | Established | `todo` | |
| M9 | #1115 | The Statistics view builds its model without the moving average that Distribution ▸ Wells applies, so it tests different values from the dots just shown, contrary to its docs | `review/features/nanowell/controller.py:682-685` | Established | `todo` | |
| M10 | #1116 | *Save nanowell data…* writes gated values under the pipeline's own `n_cells` / `brightness_<ch>` column names with nothing recording the gate; Batch Export's summary does record it | `review/features/nanowell/controller.py:106`, `series_compute.py:418` | Established | `todo` | |
| M11 | #1116 | The Array view's Cells layer and the crop Cells overlay draw ungated cells while counts and captions are gated ("0 cells" caption over 4 drawn cells) | `review/features/nanowell/collect.py:500` | Established | `todo` | |
| M12 | #1093 | Apply sets every `roster_mismatch` block of a SHIFT well to `consistent`, including flagged timepoints it neither moved nor reported | `datasets/validate/nanowell_relabel.py:243,355-357` | Established | `todo` | |
| M13 | #1093 | One-sided rim loss at a correctly addressed timepoint (bubble, glare) is read as a lattice step, and every nanowell is renamed | `datasets/validate/nanowell_relabel.py:118-144` | Established (synthetic); frequency inferred | `todo` | |
| M14 | #1088 | The concatenate step-off check cannot fire on realistic tables (rows exist only at roster addresses, so overlap at the step equals overlap at zero); its test passes only because the fixture puts rows off the roster. A timepoint its own run flagged is never judged | `datasets/concatenate/nanowell_link.py:126-139,179-182`; `tests/test_nanowell_concatenate_link.py:56-58` | Established | `todo` | |
| M15 | #1101 | Handoffs into Analyze clear *Only these wells* but not *Skip wells*, so a stale skip silently narrows, unforces, or refuses the handed-over run | `allwell.py:1980-1990` (`_open_dataset_in_analyze`) | Inferred | `todo` | |
| M16 | #1090 | The fallback copy command appends the key without ensuring `authorized_keys` ends in a newline; the key is glued to the previous line and the substring idempotency check then never repairs it | `services/ssh_key_setup.py:240-245` | Established | `todo` | |
| M17 | #1121 | `/scratch` qualifies by filesystem type alone; a plain directory on `/` puts the run's scratch on the node's boot disk | `pipeline/node_disk.py:84-95,117-139` | Established | `todo` | |

## Low

| # | PR | Finding | Where | Basis | State | Evidence |
|---|---|---|---|---|---|---|
| L1 | #1121/#1122 | The node-local floor is a fixed 20 GB, not the run's own estimate, so `preflight_scratch_space` can refuse a run that fitted before; its advice names an Analyze field that is never sent to a remote | `pipeline/node_disk.py:76`, `pipeline/capacity.py:244`, `process_microscopy.py:3409` | Established (path); frequency inferred | `todo` | |
| L2 | #1121 | The "no node-local disk" decision is a module global, not exported to workers, so parent and workers can decide differently and split scratch | `pipeline/io.py:144-163` | Established | `todo` | |
| L3 | #1121 | FOV checkpoints of a stopped well stay on the shared node-local `/scratch` indefinitely | `pipeline/io.py:400-408` | Inferred | `todo` | |
| L4 | #1107 | A 75 s beat allowance leaves 15 s under the 120 s staleness threshold; NFS attribute caching can exceed it | `services/remote_heartbeat.py:41-47`, `services/remote_wait_snippets.py:58-59` | Inferred | `todo` | |
| L5 | #1121/#1122 | Stale text: ARCHITECTURE's `local_temp_dir` order omits `/scratch`; `remote_scratch.py:172` claims to match `scratch_base_for` exactly; `remote_batch_resume.py:587` probes `~/.allwell/scratch` rather than the run folder | `_Docs/Markdowns/ARCHITECTURE.md:10734`, `services/remote_scratch.py:172`, `services/remote_batch_resume.py:587` | Established | `todo` | |
| L6 | #1119 | The empty-well name is canonicalised into the settings but digested raw, so `h12` and `H12` give different settings keys and re-run the plate | `pipeline/cli_args.py:670-676`, `process_microscopy.py:3451`, `services/pipeline_service.py:81-83` | Established | `todo` | |
| L7 | #1119 | Docs say `_confluence_reference/` is not fetched back from a remote; it is (rsync of `nanowells/`), and a partial remote reference can overwrite the local index | `services/remote_fetch_nanowells.py:115`; ARCHITECTURE confluence section | Established (doc); overwrite inferred | `todo` | |
| L8 | #1119 | Re-anchoring runs the reference well in parallel with the wells that read it | `pipeline/nanowell_task.py:1307-1338` | Inferred | `todo` | |
| L9 | #1119 | The blank *Empty reference well* field's placeholder reads "default"; nothing warns when the named well is not on the plate | `analyze/nanowell_section.py:359` | Established | `todo` | |
| L10 | #1110/#1111 | `gpu_in_use` reports the parent's plan, not the device the worker's Cellpose loaded on | `pipeline/nanowell_parallel.py:88-97` | Inferred | `todo` | |
| L11 | #1108 | The only tests that the batched mask loops reproduce Cellpose's own masks `importorskip` torch and cellpose, so they never run in CI | `tests/test_cellpose_batched_masks.py:76-129` | Established | `todo` | |
| L12 | #1115 | The Statistics cache key omits inputs that change `notes` / `n_roster` / `excluded`, so a Fixed-cohort toggle can show a stale note | `review/features/nanowell/controller.py:727` | Established | `todo` | |
| L13 | #1115 | The family sentence contradicts itself (three samples but "one comparison"; a raw p labelled Dunnett-corrected) | `review/features/nanowell/statistics_compute.py:176,450` | Established | `todo` | |
| L14 | #1116 | `population_version` rises on group and label edits that change no cell's inclusion, forcing a needless recompute | `review/core/stats_cache_state.py:63`, `selection_controller.py:1413,1583` | Inferred | `todo` | |
| L15 | #1116 | `gated_metrics` has no caller; ARCHITECTURE names it as the page and Batch Export API | `review/features/nanowell/controller.py:333` | Established | `todo` | |
| L16 | #1112 | The page's Exclude well list is not honoured by Batch Export ▸ Nanowells | `review/features/batch_export/nanowell_panel.py` | Inferred | `todo` | |
| L17 | #1103 | A tie between two candidate references leaves a timepoint `confirmed` against a reference the check has contradicted | `pipeline/nanowell_identity.py:976` | Established | `todo` | |
| L18 | #1103/#1105 | A timepoint can be contradicted from fewer addresses than `MIN_COMPARED`; ARCHITECTURE says that case is inconclusive | `pipeline/nanowell_identity.py:863-866` | Established | `todo` | |
| L19 | #1105 | `partial` is silent in the run log, the audit summary and the exit code, contrary to the docs | `pipeline/nanowell_identity.py:1094,1271,1378` | Established | `todo` | |
| L20 | #1105 | The Status map paints a wholly contradicted timepoint green ("Lands on itself") | `review/features/nanowell/identity_compute.py:463` | Established | `todo` | |
| L21 | #1105 | The crop caption still says "signal" after the "Site signal" rename | `review/features/nanowell/series_compute.py:311` | Established | `todo` | |
| L22 | #1120 | A hand-over while the early job's node is booting drops the "booting" status back to "Waiting for the scheduler" | `analyze/progress.py:411` | Established | `todo` | |
| L23 | #1100 | A cache hit logs "asking for one now" though nothing is requested; deleting the cache-hit `release()` leaves the tests green | `services/remote_runner.py:356-358` | Established | `todo` | |
| L24 | #1117/#1100 | A node that dies while the early job waits for hand-over gets "resume the run" advice | `services/remote_srun_retry.py` branch 3b | Inferred | `todo` | |
| L25 | #1094 | Folding tasks into CPUs turns `-n 4 --gpus-per-task=1` into one GPU | `services/remote_srun_retry.py:363` (`one_task`) | Established | `todo` | |
| L26 | #1100 | Stop racing `hand_over` can queue a fresh job after Stop | `services/remote_reservation.py:306,343` | Inferred | `todo` | |
| L27 | #1097 | The TimepointPicker's "(not in this source)" marker cannot appear from any of the three import pages | `datasets/czi_import/view.py:938-945`, `nd2_import/view.py:778`, `ome_import/view.py:885-888` | Established (reading) | `todo` | |
| L28 | #1088 | The zero-rows verification is disabled for the whole plan when both datasets are nanowell datasets, not per well | `datasets/concatenate/apply.py:511` | Inferred | `todo` | |

## Process and documentation

| # | Finding | Where | Basis | State | Evidence |
|---|---|---|---|---|---|
| P1 | Seven of the 34 PRs merged with no net version increment (#1088, #1094, #1096, #1101, #1116, #1118, #1119): each bumped on its branch, and a sibling merged first with the same number — the CLAUDE.md §6 two-open-PRs failure | `version.py` across the merges | Established | `todo` | |
