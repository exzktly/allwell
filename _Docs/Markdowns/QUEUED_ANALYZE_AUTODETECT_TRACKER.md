# Tracker — detect a queued Analyze job's schema and channels at run time

Live state for one body of work (CLAUDE.md §6.1). Deleted by the PR that closes
its last row. Counts are the rows below; nothing here is typed as a total.

A queued Analyze job freezes the form's filename schema, separator and channel
tokens at enqueue time, but the input they describe may not exist until an
earlier job in the queue produces it. The work lets a job say "detect these
from the input when you run", resolves that at dequeue, and fails the job with
the reason when detection cannot decide.

States: `todo`, `doing`, `done` (with the test that pins it, seen failing
without the fix), `declined` (with the reason).

| # | Item | State | Where | Evidence |
|---|------|-------|-------|----------|
| 1 | `RunSpec.auto_detect` persisted beside `opts` (never reaches the pipeline's argv); form checkbox that sets it; queue row says so | done | `services/run_spec.py`, `analyze/`, `services/job_kinds.py` | `test_queued_analyze_detects_at_run_time.py::test_the_checkbox_queues_a_job_whose_input_does_not_exist_yet`, `::test_the_picker_greys_out_and_run_stays_armed_with_an_invalid_schema`, `::test_the_row_names_a_blank_unchained_job_and_the_deferred_layout`; `test_run_detect.py::test_auto_detect_and_upstream_layout_round_trip`. Mutations: picker enable, preview skip each red |
| 2 | One Qt-free resolver shared by the form's scan and the run-time path (same confidence rule) | done | `services/run_detect.py`, `analyze/domain/input.py` | `test_queued_analyze_detects_at_run_time.py::test_the_form_s_detect_button_writes_what_the_shared_rule_says` (swapping the shared rule changes what the button writes) |
| 3 | Resolver called in `start_run`, so every entry point (Run, queue, resume) applies it; detection logged and carried in the job's success message | done | `analyze/run_spec.py`, `analyze/tab.py` | `::test_start_from_queue_hands_the_runner_the_detected_layout` asserts the options `PipelineRunner.start` receives, the log line and the dashboard status; `::test_the_finished_job_s_message_carries_what_was_detected`. Mutations: skip resolution, blank the success message — each red |
| 4 | Detection that cannot decide fails the run with the reason (no fallback to the form's frozen values) | done | `services/run_detect.py` | `test_run_detect.py::test_a_single_channel_plate_fails_rather_than_keeping_the_frozen_schema`, `::test_an_empty_input_fails_and_says_where`, `::test_roles_with_no_segmentation_channel_fail`; `::test_undecidable_detection_fails_the_job_and_starts_nothing`. Mutation: drop the confidence refusal — red |
| 5 | Queue-time validation skips the schema/channel checks a flagged job defers, tolerates an input that does not exist yet (and an empty one, for a job to be chained); run-time checks on the resolved spec | done | `analyze/run_spec.py`, `analyze/seg_params.py`, `analyze/bg_map.py`, `analyze/form.py`, `services/run_detect.py` | `::test_the_checkbox_queues_a_job_whose_input_does_not_exist_yet`, `::test_the_queue_accepts_an_unwritten_input_without_detection`, `::test_the_run_button_still_asks_the_input_about_the_blank_well`, `::test_a_job_never_given_an_input_fails_with_the_reason`, `test_analyze_fields_are_validated_not_defaulted.py::test_a_blank_input_queues_for_chaining_and_is_refused_if_never_filled`. Mutations on each of the four deferrals — each red |
| 6 | Channel-naming options (`cytoplasm_token`, `smfish_tokens`, `cellpose_nuclei_channel`, label-free phase, phase conflicts) checked against the detected channels at run time | done | `services/run_detect.py` | `test_run_detect.py::test_a_stale_smfish_mark_fails_the_run`, `::test_a_cellpose_nuclei_channel_the_input_lacks_fails`, `::test_watershed_needs_a_cytoplasm_channel_the_input_carries`, `::test_label_free_without_a_detected_phase_channel_fails`. Mutation: `channel_option_errors` returns `[]` — red |
| 7 | Edit round trip: `hydrate_job` restores the flag; `collect_spec` writes it back | done | `analyze/domain/job_edit.py` | `test_analyze_job_edit.py` round trips now carry `auto_detect: True`; `::test_hydrating_a_job_without_the_flag_unticks_the_box`. Mutation: hydrate skips the box — 6 red |
| 8 | Converters queue their channel roles, and a chained Analyze job takes the converter's known layout ahead of a scan | done | `datasets/core/packed_convert.py`, `datasets/core/channel_roles_row.py`, `services/job_queue.py`, `services/job_kinds.py`, `datasets/job_queue/controller.py` | `test_converter_roles_travel_the_queue.py` (page → spec → edit, `produced_layout`, `materialise`, retry, restart); `::test_a_chained_analyze_job_runs_with_the_converter_s_roles` through the real `QueueBridge` and `AnalyzeTab`. Mutations on each hop — each red |
| 9 | Docs: ARCHITECTURE + README; version bump | done | `_Docs/Markdowns/`, `version.py` | ARCHITECTURE §6 (Analyze run spec, run-time detection), §6b (job queue, job_kinds, converters), services table; README Job Queue; `version.py` 9.10.2 |
| 10 | Adversarial review of the pushed diff; each finding gets its own row below | todo | | |
