# Subset of an in-only dataset — remediation tracker

Live state for one body of work: letting Subset / Split Dataset take a dataset
that holds only an `in/` folder (a plate converted by Convert OME-TIFF / CZI /
ND2 or Import Loose and not yet analysed), split it by a typed filename schema,
filter it by channel, and edit it in place. Deleted by the pull request that
closes its last row.

Decisions the user made before any code was written:

- In-place editing applies only to in-only datasets. An analysed dataset's
  `out/` would describe images that no longer exist.
- The channel filter applies only to in-only datasets, for the same reason
  (per-channel measurement columns and QC images).
- Schema precedence: the acquisition record (`in/acquisition.json`), then a
  typed schema (which must agree with the record when there is one), then a
  confident inference from the member names; otherwise refuse any filter that
  needs a field, while a wells-only subset needs no schema at all.
- An in-place edit must not need double the space: each well archive is
  rewritten alone and swapped in, and nothing removed is backed up.

States: `open`, `done` (with the test that pins it), `declined` (with the reason).

| # | Item | Place | State | Evidence |
|---|------|-------|-------|----------|
| 1 | Share the raw-dataset helpers between Concatenate and Subset | `datasets/core/raw_dataset.py` | done | Moved from `datasets/concatenate/raw_compute.py`; `unusable_reason` takes the asking tool's name. `tests/test_concatenate_raw.py` and `test_concatenate_in_place.py` pass against the moved module |
| 2 | Resolve an in-only source's schema: record → typed → confident inference → none | `datasets/subset/raw_compute.py` | done | `tests/test_subset_raw.py` (record, typed, typed-vs-record refusal, misfitting names, inferred, none → wells only, archives drifted from the record). Mutation: letting a typed schema bypass the record fails `test_a_typed_schema_that_contradicts_the_record_is_refused` |
| 3 | Channel filter (in-only only); exact-token timepoint match | `datasets/subset/subset_compute.py` | done | Mutations: channel check off fails two tests in `test_subset_raw.py`; exact-token match off fails `test_a_timepoint_token_that_does_not_read_as_hours_is_still_chosen_by_its_name`. Analysed source refuses a channel filter (`test_an_analysed_dataset_refuses_…`) |
| 4 | Copy an in-only subset: `in/` only, record and anchors carried, no `out/` | `datasets/subset/apply_raw.py` | done | `test_an_in_only_dataset_is_subset_by_timepoint_…` (bytes, no `out/`, record valid against the archives), `test_anchors_and_the_short_well_record_follow_…`. Mutation: skipping the record cut fails three tests |
| 5 | Edit in place: per-well atomic rewrite, emptied wells deleted, interrupted run resumable | `datasets/subset/apply_raw.py` | done | Four in-place tests in `test_subset_raw.py`, including a cancel after one well and a resume. Mutations that skip the rename, ignore the stored schema on resume, keep the converter's done marker, or keep stale anchors each fail a named test |
| 6 | Page: in-only source accepted, schema field, channel row, Edit in place | `datasets/subset/view.py`, `controller.py`, `worker.py` | done | `tests/test_subset_raw_page.py`, every run through `runRequested` and the real worker. Mutations: controller dropping channels, worker dropping in_place, the old source rule — each fails named tests |
| 7 | Job queue: spec carries channels / schema / in place; output root of an in-place job | `services/job_kinds.py` | done | `test_a_queued_in_place_job_round_trips_and_names_its_source_as_output`; mutations of `show_job` (schema) and `planned_output` each fail it |
| 8 | ARCHITECTURE.md and README_WellViewer.md | `_Docs/Markdowns/` | done | Full fast lane run over the docs |
