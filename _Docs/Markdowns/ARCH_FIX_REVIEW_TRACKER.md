# Architecture-audit fixes — Review area tracker

Live state for the Review-area items of the architecture audit of PRs #992–#1016.
One row per item. Counts are derived from the rows. Deleted by the commit that
closes the last row.

| ID | Rule | State | Evidence / test |
|----|------|-------|-----------------|
| R1 | Hover MFI thread reads held row-cache frames while gating / curation / progress jobs rewrite them; failure reads as "no MFI" | todo | |
| R2 | `review/core/stats_cache_state.py` holds logic against the `_state.py` "fields + defaults only" rule | todo | |
| R3 | `held_for == "gating"` literal outside `core/plot_hold` | todo | |
| R4 | `phone_panel_sheet` sets a loose `_aw_phone_panel_sheet` attribute on the main window | todo | |
| R5 | Plot-hold early returns in the Plotting leaves are an unguarded hand list | todo | |
| R6 | `set_qc_params` / `set_fov_selection` dead twins and the docs naming them | todo | |
| R7 | `plot_toggles` silently skips reservation for a non-`ReservedWidthButton` | todo | |
| R8 | Playground `nanowell_view.py` raw pixel sizes; misplaced comment in `tab_view.py` | todo | |
| R9 | Doc drift (ARCH changelog prose, stale facts, fast-lane count command, REREAD_TRACKER L2, orphan header) | todo | |
