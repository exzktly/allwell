# Architecture-audit fixes — Review area tracker

Live state for the Review-area items of the architecture audit of PRs #992–#1016.
One row per item. Counts are derived from the rows. Deleted by the commit that
closes the last row.

| ID | Rule | State | Evidence / test |
|----|------|-------|-----------------|
| R1 | Hover MFI thread reads held row-cache frames while gating / curation / progress jobs rewrite them; failure reads as "no MFI" | done | `tests/test_image_table_hover_mfi_reads_no_frame_being_written.py` — 5 tests red on origin/main with AssertionErrors naming the torn read, the shared-cache write, the lookup started under a panel job and under a load's gating pass, and the failure read as background; the verdict-copy case red under a mutation that drops `VERDICT_LAYERS` from the copy; the gating-pass case red under a mutation removing the blocker registration |
| R2 | `review/core/stats_cache_state.py` holds logic against the `_state.py` "fields + defaults only" rule | done | `PopulationStore` + budget helpers moved to `review/data/population_store.py`; new guard `tests/test_state_modules_hold_no_machinery.py` fails on origin/main naming exactly the 3 sites in `stats_cache_state.py` (class, `os`, `threading`); 5 `test_guards_bite.py` cases |
| R3 | `held_for == "gating"` literal outside `core/plot_hold` | done | Both sites (`home/controller.py`, `core/ui_helpers.py`) use `HOLD_GATING`; new guard `tests/test_plot_hold_reasons_are_named.py` fails on origin/main naming exactly those 2 sites; 4 `test_guards_bite.py` cases |
| R4 | `phone_panel_sheet` sets a loose `_aw_phone_panel_sheet` attribute on the main window | done | The sheet is found by its `phonePanelSheet` property among the window's direct children (`sheet_of(win)`); `tests/test_phone_panel_sheet.py::test_the_window_holds_one_sheet_and_no_loose_attribute` red on origin/main (`AssertionError: the window carries loose attributes: ['_aw_phone_panel_sheet']`) |
| R5 | Plot-hold early returns in the Plotting leaves are an unguarded hand list | done | Registry-derived `test_every_leaf_s_redraw_returns_while_the_plots_are_held` (+ exemption liveness) in `tests/test_a_plot_leaf_is_wired_everywhere.py`; held on origin/main (the roster was complete), so proved by mutation on HEAD: removing the check from `redraw_heatmap` and `scatter_redraw_active` fails it naming both |
| R6 | `set_qc_params` / `set_fov_selection` dead twins and the docs naming them | todo | |
| R7 | `plot_toggles` silently skips reservation for a non-`ReservedWidthButton` | todo | |
| R8 | Playground `nanowell_view.py` raw pixel sizes; misplaced comment in `tab_view.py` | todo | |
| R9 | Doc drift (ARCH changelog prose, stale facts, fast-lane count command, REREAD_TRACKER L2, orphan header) | todo | |
