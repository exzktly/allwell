# Analyze sidebar UX — remediation tracker

Live state for the Analyze sidebar UX pass (branch `claude/analyze-sidebar-ux`).
It is the sanctioned exception in CLAUDE.md §6.1: delete it in the PR that closes
the last row. The source is a UX review of the sidebar rendered on `main` at
`7b58776`; the finding ids (A1, D1, …) are that review's.

States: `open`, `done` (with the test that pins it), `declined` (with the reason),
`deferred` (out of this branch's scope, with the reason). Counts are read from the
rows, never typed.

| # | Finding | Change | State | Evidence |
|---|---|---|---|---|
| 1 | A1 — the default form cannot run | Flat-field and Background subtraction default to `none` | done | `test_analyze_sidebar_ux.py::test_a_fresh_form_corrects_nothing_and_needs_no_blank_well`, `test_qc_tooling.py::test_analyze_default_corrections_are_none_end_to_end`; fails with the old default (mutation M3) |
| 2 | D1 — orphaned "Segmentation channel" label in Label-free mode | Hide the whole row, not its right-hand cell | done | `::test_label_free_hides_the_segmentation_channel_label_too`; fails with the cell hidden instead of the row (M1) |
| 3 | B1 — the longest expert section is the one open by default | "Quality Control" → "Image correction & QC", folded | done | `test_qc_tooling.py::test_analyze_qc_section_present_and_gated` reads the new title; folded with the other optional sections |
| 4 | F2 — errors styled as help text | The segmentation hint and folder readout take the `Error` style when they carry a problem | done | `::test_a_segmentation_problem_is_drawn_as_an_error`, `::test_an_empty_status_line_takes_no_room`; fail with `_set_status` never choosing Error (M4) |
| 5 | D2 — gaps left by empty status labels | Hide a status label while its text is empty | done | `::test_an_empty_status_line_takes_no_room`; the method-block gaps (same finding) by `::test_the_method_block_is_as_tall_as_the_chosen_page`, which fails with a plain stack (M2) |
| 6 | C1 — command-line ids as option labels | Friendly labels for flat-field, background and Cellpose model; the id stays in userData | done | `::test_a_combo_shows_a_label_and_records_the_id`, `::test_a_sidecar_restores_a_combo_by_its_recorded_id` |
| 7 | C4 — help copy contradicts the defaults | Fix the `sliding_disk` "(default…)" tooltip and the "Both blank" threshold hint | done | Copy only; read in the render (`panel_default.png`). No test: a tooltip's wording is not behaviour |
| 8 | C3 — duplicated hints, units in labels | Drop the "(0 → auto-select 4)" / "(process count override)" lines; plainer labels | done | Copy only; units and `0 = …` moved after the field (`_field_note`), checked in the render |
| 9 | C5 / C2 — title case drift, "Channel Tokens", "token" jargon, "Run on:" colon | Sentence-case titles; "Channels & segmentation"; "Cytoplasm channel"; no colon | done | `test_analyze_help_section.py` holds every title against `ANALYZE_SECTION_HELP` and the manual |
| 10 | F3 — noisy idle run controls | Suspend and Stop are hidden while no run is going | done | `::test_suspend_and_stop_are_shown_only_while_running`; fails with Suspend left visible (M5) |
| 11 | D3 — row labels sit above their field text | Row labels are vertically centred on their field | done | Checked in the render; geometry, no behaviour to assert |
| 12 | D4 — stale 438 px comment | Correct the comment in `analyze/tab.py` | done | Comment rewritten |
| 13 | A3 / A6 — Run is two screens down | Run on + the run buttons pinned in a footer under the scrolling form | done | `::test_run_is_on_screen_without_scrolling` |
| 14 | B2 — Background well shown when nothing reads it | The Background well and blank-pairing rows hide unless an `empty_well` method is chosen, and say they are required when shown empty | done | `::test_the_blank_well_rows_appear_only_when_a_correction_reads_them` |
| 15 | D6 — "Remote options" is a pseudo-section | A real section at the foot of the form, shown only for a remote | done | `::test_remote_options_show_only_for_a_remote`, `test_analyze_help_section.py::test_focus_reaches_every_single_section` |
| 16 | Docs | ARCHITECTURE.md §6 and README Analyze sections match the form | done | ARCHITECTURE.md §6.1 and the widgets table; README §3–§5, §9, §11, §12 |
| 17 | F1 — validation is a chain of modals | Continuous inline validation | deferred | A redesign of `_validate_run_request` and its queue twin; its own PR |
| 18 | A2 / A5 / E1 / E3 / D4-layout / B3 / B4 — re-sectioning, nanowell mode switch, one run-scope choice, role combo, one-column layout, header switches, folded summaries | — | deferred | Each restructures widgets that restore and handoff code reach by name; out of this branch's scope |
| 19 | A6 — Run on defaults to the first remote | Default kept; the footer keeps the choice in sight beside Run | declined | Changing which host a run goes to by default is a behaviour change a user who configured remotes relies on; the footer answers the review's complaint (the choice was out of sight) without it |
