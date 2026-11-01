# Slow-lane coverage tracker: the tooling added since the last slow-lane run

A remediation tracker in the sense of CLAUDE.md §6.1. It is deleted by the pull request that closes its last row.

## Why this exists

The slow lane (`.github/workflows/slow-lane.yml`, `scripts/test_slow.sh`) runs only when someone dispatches it. Its
last run was on 2026-10-16, on a feature branch, and failed; its last green run was 2026-09-12. Between 2026-10-16 and
2026-11-01 about 34,000 lines changed under `review/`, `analyze/` and `datasets/`, most of them on the Nanowells page.

Measured with coverage on 2026-11-01, the two full-app files that reach the Nanowells page (`test_nanowell_page_app.py`,
`test_nanowell_follows_group_edits.py`, six tests) build every one of its views but drive almost none of them:
`_identity_controller` 21 %, `_anchor_queue` 20 %, `_statistics_render` 15 %, `subset_view` 47 %, `controller` 45 %.

The page *itself* is well tested in the fast lane, through its real builder `build_nanowell_tab` inside a minimal
shell. So a full-app test here earns its cost only where the page meets something the minimal shell does not have or
stubs: the shell's mode band, the rail plate, the window's own shortcuts, other tabs reading the same data, the Home
editors, Saved Plots, persistence across a dataset switch. Each row names that seam. A row that would only repeat what a
fast-lane test already drives through the real builder is not a row.

The Analyze and Datasets panes are a different case. Their full-construction tests build the real `AnalyzeTab` /
`DatasetsTab` and run in the fast lane, because neither builds a `ReviewApp`; rows for those panes are opened only
where coverage under those tests shows a module the real tab never reaches.

## Rows

State is `open`, `done` (with the test that pins it, seen failing against a mutation) or `declined` (with the reason).

| Row | Seam | What the test drives | State | Test |
|---|---|---|---|---|
| N1 | Mode band | Each of the page's view tabs clicked in the real band switches the view and draws without a logged error; the strip shows only on the Nanowells page | done | `tests/test_nanowell_tools_in_a_real_app.py::test_the_view_strip_sits_in_the_mode_band_and_each_tab_draws_its_view` — fails with "the strip was never mounted in the band" when `build_view_bar` skips `register_page_tabs` |
| N2 | Rail plate, window shortcuts | The Anchor queue walked with the real rail plate (the fast test stubs it) and driven by its keys: → skips, ← goes back, a canvas click anchors, Esc stops and the rail's selection is restored | done | `tests/test_nanowell_tools_in_a_real_app.py::test_the_anchor_queue_walks_with_the_real_rail_and_its_keys_reach_it_in_the_app_window` — fails on the → step when the shortcuts take `Qt.WidgetShortcut` |
| N3 | Home's Cell Gating panel, other tabs | The band's Rim focus switch mirrors Cell Gating's nanowell focus switch, and the soft nanowell's cell leaves another tab's numbers | done | `tests/test_nanowell_tools_in_a_real_app.py::test_the_band_s_rim_focus_switch_and_cell_gating_s_are_one_setting_and_line_graphs_counts_follow` — fails on `not gate.isChecked()` when `set_rim_focus` stops repainting the Cell Gating panel |
| N4 | Saved Plots | The Statistics view's choices (compare, test, control) and the Subset survive a Saved Plot round trip through the real store | done | `tests/test_nanowell_tools_in_a_real_app.py::test_a_saved_plot_restores_the_statistics_choices_and_the_subset` — fails on the restored tuple when `NanowellState.to_config` stops writing `stats_test` |
| N5 | The app window | The crops pop-out opens owned by the app's window and draws the selected nanowell; its *Go to nanowell* moves the band's strip to Identity and leaves the pop-out open | done | `tests/test_nanowell_tools_in_a_real_app.py::test_the_crops_pop_out_is_owned_by_the_app_window_and_go_to_nanowell_moves_the_band` — fails on `popout.parent() is viewer_app` when the dialog is built without a parent |
| N6 | Split View | With the Nanowells page on screen beside Home's Cell Gating in Split View, a change to Cell Gating's nanowell focus switch left the band's *Rim focus* box and the page's metrics on the old rule until the page was shown again (reproduced: `focus_for` stayed `(True, 0.75)` and the box stayed ticked with `exclude_soft_nanowells` false). `qc.controller._after_qc_params_applied` reached Line, Distribution and Heat Map only; it now calls the page's `nanowell.follow_qc_params` service (`controller.nw_on_qc_params_changed`) | done | `tests/test_nanowell_tools_in_a_real_app.py::test_in_split_view_a_cell_gating_focus_change_reaches_the_nanowells_page_beside_it` — fails with "the Nanowells page kept the previous focus rule" when the service call is removed |
| A1 | Analyze pane | Declined: the real `AnalyzeTab` is built by 63 fast-lane files and the nanowell form files, and needs no `ReviewApp`. Coverage under those files: `advanced_section` 93 %, `channel_rows` 99 %, `run_footer` 97 %, `run_problems` 92 %, `section_summaries` 98 %, `nanowell_section` 88 %, `slurm_job_view` and `storage_options` 100 %. `nanowell_preview` and `nanowell_preflight` read 0 % and 12 % under the `AnalyzeTab()` files alone, and are driven through the real tab's *Test detection* button by `test_nanowell_form.py` in the nanowell features lane | declined | |
| D1 | Datasets pane | Declined: the real `DatasetsTab` and tool views are built by fast-lane files (`job_queue/view` 97 %, `job_queue/controller` 80 %, `validate/view` 86 %, `concatenate/view` 74 %); the low figures are the apply and compute modules (`concatenate/apply` 15 %, `validate/nanowell_relabel` 21 %), which their own tests (`test_concatenate_compute.py`, `test_nanowell_relabel*.py`, `test_validate_e2e.py`, `test_nanowell_concatenate_link.py`) call directly. A `ReviewApp` adds nothing to either | declined | |
| P1 | Phone shell | Declined for this tracker: `ui/phone_shell.py` reads 16.5 % under the `AnalyzeTab` / `DatasetsTab` files, and is driven through the real window by `test_phone_layout_app.py`, `test_phone_datasets_shell.py`, `test_phone_form_swipe.py` and `test_phone_plot_swipe.py`, which are in the UI lane (run locally, never in CI) rather than the slow lane | declined | |

## Counts

Derived from the rows above, never typed: run
`grep -cE '^\| [A-Z][0-9]+ .*\| open \|' _Docs/Markdowns/SLOW_LANE_COVERAGE_TRACKER.md` (and `done`, `declined`).
