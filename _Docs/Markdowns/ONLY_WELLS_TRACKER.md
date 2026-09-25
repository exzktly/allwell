# Remediation tracker — "Run these wells only" (`--only_wells`)

Live state for one body of work (CLAUDE.md §6.1). Deleted by the PR that closes
its last row.

**Goal.** A run can be restricted to a named subset of wells, and the two
"complete this run" handoffs (Datasets → Validate & Repair, Review → Home) send
exactly the wells that still need work, so a repair run touches nothing else —
locally and on a remote host.

**The hazard this whole list exists for.** A subset run writes into a directory
that holds the rest of the plate. Every end-of-run record that is rebuilt from
"the wells this run processed" would, on a subset run, silently replace the
whole plate's record with the subset's (CLAUDE.md §7: degraded output must never
be indistinguishable from good output).

States: `todo` · `doing` · `done` (with the test that pins it, seen failing
without the fix) · `declined` (with the reason).

Counts are derived from the rows below, never typed.

| # | Item | Where | State | Evidence |
|---|------|-------|-------|----------|
| P1 | `--only_wells WELL…` CLI flag, canonicalised like `--force_wells` (C8 == C08) | `pipeline/cli_args.py`, `pipeline/well_skip.py` | done | `tests/test_only_wells_run.py::test_selection_matches_well_labels_canonically`; parser rejects an empty list (`nargs="+"`) |
| P2 | The pool drivers process only the named wells; a named well absent from the input is a loud error, not a silent no-op | `process_microscopy.py` main / `_run_well_worker_pool` | done | `tests/test_only_wells_run.py::test_only_the_named_well_is_processed_and_the_rest_is_left_alone`, `::test_a_named_well_missing_from_the_input_refuses_the_run`, `::test_folder_mode_is_narrowed_and_refused_the_same_way` — mutations M1 (no scoping) and M7 (no refusal) each fail them |
| P3 | Models / scratch preflight: flat-field + background models still built from the whole input; the scratch estimate counts only the subset | `process_microscopy.py` main | done | by construction: the blank pairing and `prepare_models` read `input_dir`, never the narrowed list. `tests/test_only_wells_run.py::test_a_blank_outside_the_subset_still_corrects_it` pins the scenario (no mutation of the ordering breaks it, because nothing depends on it) |
| P4 | `run_report` block: prior records of wells outside the subset survive the sidecar rewrite | `pipeline/run_report.py`, `process_microscopy.py` | done | `tests/test_only_wells_run.py::test_only_the_named_well_…`, `::test_a_forced_subset_without_incremental_…` (M2), `::test_a_well_the_subset_ran_does_not_inherit_its_previous_record` (M5), `::test_the_subset_s_own_record_wins_over_the_prior_one` |
| P5 | `field_quality` block: same, and a subset run's records *replace* (not add to) prior ones unless the run is also incremental | `pipeline/field_quality_record.py`, `process_microscopy.py` | done | `tests/test_only_wells_run.py::test_a_forced_subset_without_incremental_…` (M4: prior read only when incremental), `::test_a_non_incremental_subset_replaces_its_wells_quality_records` (M6) |
| P6 | `run_check` still covers every well in the input, not the subset | `process_microscopy.py` → `record_run_check` | done | `tests/test_only_wells_run.py::test_the_run_check_still_describes_every_well_in_the_input` (M3: narrowed list records B02 with `intent: 0`, `has_in: False`) |
| P7 | `settings_digest` classifies `only_wells` as run scope, not a setting (it must not change the settings key a well was computed under) | `util/settings_digest.py` | done | `NOT_KEYED_ON["only_wells"]`; `tests/test_reuse_is_keyed_on_the_settings.py` partitions the parser (run with the guard family) |
| G1 | Analyze form: an "Only wells" field beside "Force wells", collected into the run options | `analyze/form.py`, `analyze/run_spec.py` | todo | |
| G2 | Run options → pipeline argv (`--only_wells`) for local runs | `services/pipeline_service.py`, `services/pipeline_runner.py` | todo | |
| G3 | Never restored from a sidecar: `_SCOPE_KEYS` in preflight, and `_apply_pipeline_info` clears the field rather than restoring a previous run's subset | `analyze/preflight.py`, `analyze/pinfo.py` | todo | |
| G4 | Progress denominator counts the subset, not the whole plate | `analyze/tab.py::_expected_well_count` | todo | |
| G5 | A subset run says so when it starts (dashboard/log), so it cannot be mistaken for a whole-plate run | `analyze/tab.py` / `allwell.py` handoff announcement | todo | |
| H1 | `_open_dataset_in_analyze` / `datasets/tab.py` handoff accept and apply `only_wells` | `allwell.py`, `datasets/tab.py` | todo | |
| H2 | Validate's "Complete this run in Analyze" sends the wells needing work (partial, no output, needs-force; not zero-cell, not never-fetched) | `datasets/validate/validate_compute.py`, `controller.py` | todo | |
| H3 | Review → Home's "complete this run" sends the same set, read from its record | `review/features/home/collect.py`, `controller.py` | todo | |
| R1 | Remote: argv carries `--only_wells` | `services/remote_runner.py` | todo | |
| R2 | Remote: only the named wells are staged / uploaded | `services/remote_runner.py` | todo | |
| R3 | Remote: only the named wells are expected / fetched / reported; others are neither fetched nor reported missing | `services/remote_runner.py` | todo | |
| R4 | Remote reuse/reattach key includes the subset (a different subset never reattaches to another run's directory) | `services/remote_run_id.py`, `tests/test_reuse_is_keyed_completely.py` | todo | |
| D1 | ARCHITECTURE.md + README_WellViewer.md describe the option and the handoffs | `_Docs/Markdowns/` | todo | |
