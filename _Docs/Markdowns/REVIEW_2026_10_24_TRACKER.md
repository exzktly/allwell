# Remediation tracker — adversarial review of the PRs merged 24 October (#1041–#1054)

Six reviewers read #1041–#1054 against `main` at 2207c57. One row per finding. A row is `done` only with a
regression test watched failing on the pre-fix code; `declined` and `deferred` rows carry their reason. This file
is deleted by the PR that closes its last row.

| id | PR | sev | finding | state | where / evidence |
|---|---|---|---|---|---|
| A1 | 1042 | Med | a well measured with no cell reported as having no cell table | done | `collect._cell_brightness`, `array_compute.cells_into`; `test_nanowell_cell_dots` |
| A2 | 1041 | Low | labels footer says "zoom in" when nothing has a label | done | `renderer._draw_labels`; `test_nanowell_array_controls` |
| A3 | 1041 | Low | wheel zooms the wrong way past a limit after a refit | done | `controller._zoom_to`; `test_nanowell_array_controls` |
| A4 | 1042 | Low | duplicated tables loop rebinding `well` | done | `collect._cell_brightness` (removed with A1) |
| A5 | 1042 | Low | a well's cells split across two tables: only the first read | deferred | unverified; no real layout writes it |
| A6 | 1042 | Low | Overlays tooltip/note/README omit Cells; ARCHITECTURE names the wrong function | done | doc-only |
| F1 | 1044 | Med | Load settings restores "Each nanowell" for a pre-flag run that segmented whole tiles | done | `analyze/nanowell_section._apply_nanowell_settings`; `test_nanowell_segment_on` |
| F2 | 1053 | Med-Low | Enlarge crops button stale after the selection is cleared | done | `controller._draw_crop_view`; `test_nanowell_crop_view` |
| F3 | 1053 | Low-Med | crop view survives a dataset change; enlarges a site the detail does not show | done | `state.reset_data`, `controller._crop_view_site`; `test_nanowell_crop_view` |
| F4 | 1050 | Low | restored 19.5 µm size overwritten by the px carry-over | done | `_apply_nanowell_settings`; `test_nanowell_segment_on` |
| F5 | — | Low | `nanowell_segment_scale` has no form control, so an old `tile` run cannot be reproduced from the form | deferred | outside these PRs (3ed5bd0); a feature, not a fix |
| S1 | 1053 | High | ImageXpress `_s1_w1` per-well zips: sites taken for channels | done | `util/schema_infer._pick_channel_slot`; `test_schema_infer` |
| S2 | 1053 | Med | two prefixes on coinciding tiles still taken for the channel | declined | not decidable from names alone: each prefix has a sibling too; documented in the docstring |
| S3 | 1053 | Low | no-groups reason misses a partner differing only by extension | done | `pipeline/nanowell_task.no_groups_reason`; `test_nanowell_cli` |
| S4 | 1054 | Low | a log with only horizontal range is a dead spot for the page drag | done | `ui/phone_shell._inner_area_owns_drag`; `test_phone_form_swipe` |
| S5 | 1054 | ? | a log as tall as the viewport may take every page drag | open | unverified |
| P1 | 1049 | Med | `levelled` levels by the interior median, shifting crowded nanowells' floors | done | `nanowell_measure._level_interiors`; `test_nanowell_segment_crops` |
| P2 | 1044 | Med-Low | overlapping crops can count one cell twice (custom pitch < ~2.42 r) | open | `nanowell_measure.segment_sites` |
| P3 | 1044 | Low | Cellpose drops a mask over 40 % of a crop: a nanowell-filling object counted empty | open | `pipeline/cellpose.py` crop calls |
| P4 | 1045 | Low | network/mask timing breakdown missing for single-image calls | open | `pipeline/cellpose.py`, `nanowell_task._log_sites` |
| P5 | 1045 | Low | MEASURE_VERSION not bumped when the count's normalisation changed | done | bumped to 6 with P1 |
| P6 | 1047 | Low | a previous run's segmenter-input sheet left beside newer results | open | `nanowell_inspect.write_sheet` |
| R1 | 1052 | High | a nanowell well resent alone cannot be re-anchored from the plate | open | `services/remote_finished_wells`, `pipeline_runner` |
| R2 | 1052 | Med | field-mode "finished here" ignores fields/timepoints the input gained | open | `remote_finished_wells.py:74-77` |
| R3 | 1052 | Med | host wells computed with `--no_auto_threshold` miss every later local reuse | done | `util/settings_digest.settings_key_for`; `test_a_half_finished_well_resumes_across_builds` |
| R4 | 1052 | Low-Med | an `--only_wells` subset run gates the plate from the subset | open | `pipeline_runner._regate_locally` |
| R5 | 1051 | Low | `.AppleDouble`, `__MACOSX/`, `Icon\r` not treated as OS debris | open | `datasets/core/member_names.is_os_junk_member` |
| R6 | 1051 | Low | `.DS_Store` still makes a destination "already has unrelated files" | open | `datasets/core/dataset_paths.destination_has_content` |
| C1 | 1049 | High | the Cellpose env's pip resolves numpy 2, breaking imagecodecs | done | `requirements-remote-cellpose.txt` pins numpy 1.24, opencv < 4.11 (pip dry-run resolves numpy-1.24.4 opencv-4.10.0.84); probe covers both; `test_bootstrap_cellpose_env` |
| C2 | 1049 | High | runs routed to the Cellpose env even when its install failed | done | `remote_conda.aw_cellpose_env_ready`; `test_remote_cellpose_env` (3 states) |
| C3 | 1043 | Med | an old nanowell plate's key never matched: `cellpose_model` kept as cpsam | done | `util/settings_digest._before_cpsam`; pinned to 1355778^1's normalised digests in `test_a_half_finished_well_resumes_across_builds` |
| C4 | 1043 | Med | Batch Export nanowell mode: `KeyError: 'state'` on a group with no table | done | `nanowell_panel._load`; `test_batch_export_nanowell_mode` |
| C5 | 1049 | Low | precision (bf16/TF32/fp32) is in no reuse key | open | doc |
