# Review left rail reorganisation — tracker

**This document is temporary.** CLAUDE.md §6.1 requires a body of work with more
than a handful of items to live on disk. It is deleted by the pull request that
closes its last row, and that pull request says so in its body.

It records the work that came out of two reviews of Review's left rail (a UX
review and a code-organisation review, 2026-11-01). Both concluded the rail's
skeleton is right — dataset head, section list, one fixed well-plate slot
(`review/core/rail_plate.py`), the page's own frames, saved selections — and
that the defects are in the layer on top. The user chose five steps; each step
is one section below, and each finding inside a step is one row.

## States

- **`todo`** — not started.
- **`wip`** — started, not finished; the evidence column says where it stopped.
- **`done`** — implemented **and** pinned by a test that was watched failing
  without the change (CLAUDE.md §7.7 #2). The evidence column names the test
  and how the failure was observed.
- **`declined`** — examined and deliberately not done, with the reason.

## Counts

Derived from the rows below — recount them rather than editing a number here.

## Step 1 — the manual describes the rail as it is

| Id | Finding | Place | State | Evidence |
|---|---|---|---|---|
| D1 | The manual says the section list is "grouped into Analysis, Images and Data"; nothing on screen grouped it. | `README_WellViewer.md`, *Section rail* bullet | done | *Section rail* bullet rewritten: Home, then the **Analysis** / **Images** / **Data** captions — true since G1 (`test_rail_nav_groups.py` pins the order). |
| D2 | The manual says the plate map and Saved selections are "pinned to the bottom of the left rail"; the plate sits directly under the section list. | `README_WellViewer.md`, *Plate map + Saved selections* bullet | done | Bullet rewritten: plate, selection footer, Saved selections, directly under the section list — true since S1/S4 (`test_selection_footer.py` pins the order). Checked against an offscreen render of the rail at eade09f. |
| D3 | The manual promises **Select all** / **Select none** under the plate; the rail offered "Deselect all" on plot pages and "Select All" / "Clear" on Image Table. | `README_WellViewer.md`, *Plate-map well picker* | done | *Plate-map well picker* rewritten around the footer's count, **All** and **None**, group mode, and the single-well pages; *Saved selections* bullet gains the header count. |
| D4 | ARCHITECTURE describes the rail's assembly (section icons, frame roster, sidebar_view) and must follow every structural row below. | `ARCHITECTURE.md` | done | §5.4 rewritten (rail assembly, `rail_sections`, footer, `saved_selections.py`); directory tree, §11.1 step 5, §11.2 and two stale mentions (`section_icons.py`, `_sidebar_sample_frame`) corrected. `grep` for `SIDEBAR_SECTION_FRAMES`, `_sidebar_sample_frame`, `section_icons`, `_SECTION_ICONS`, `Deselect all`, `wells active` over `_Docs/Markdowns/` finds nothing outside the trackers. |

## Step 2 — the section list shows its groups

| Id | Finding | Place | State | Evidence |
|---|---|---|---|---|
| G1 | The registry declares Home / Analysis / Images / Data, but the rail draws eleven undifferentiated rows: the group chrome was retired and `group_starts` is unused. Show a caption above each group except Home, derived from each descriptor's `group`, so no new roster is written. | `widgets/rail_nav.py`, `review/core/tab_controller.py::sync_section_nav_from_notebook` | done | `RailNav.addCaption` (not an item: no key, never current); `tab_controller._rail_group_caption` opens each group from `FeatureDescriptor.group`, Home uncaptioned. `tests/test_rail_nav_groups.py` drives the real `sync_section_nav_from_notebook` over the real registry; mutation (caption call removed) → 2 failed. A caption hides while its whole group is hidden; mutation → 1 failed. |
| G2 | Phone layout lays the rail out as a two-column grid (`RailNav.setColumns(2)`); a caption must span both columns and start a fresh grid row, not occupy a tile. | `widgets/rail_nav.py::setColumns` | done | `RailNav._relayout_grid`: a caption takes a grid row across every column and its group's tiles start under it. `tests/test_rail_nav_group_grid.py` (UI lane); mutation (caption placed in one cell) → 1 failed. |
| G1a | smFISH sat under **Images**, but it is an analysis step: it sweeps and sets the spot-count threshold that Apply to All writes into the CSVs (`smFISH_Thresh`), and its frame view exists to validate that cut (reporter, 2026-11-01). Move it to **Analysis**, after Nanowells. | `review/features/registry.py` | done | Descriptor `group="Analysis"`, moved after Nanowells with the reason at the site. `test_rail_nav_groups.py::test_each_group_after_home_opens_with_its_caption` asserts the new order (fails against the old declaration). ARCHITECTURE §5.2's page table corrected, and it now lists Nanowells, which it had been missing. |

## Step 3 — one selection footer, and Saved selections beside it

| Id | Finding | Place | State | Evidence |
|---|---|---|---|---|
| S1 | The same action has different words per page: "Deselect all" (plot pages), "Select All" / "Clear" + "N wells active" (Image Table); plot pages show no count. One reusable footer — count, **All**, **None** — built by one widget. | new `widgets/selection_footer.py`; `review/core/sidebar_view.py`; `image_table/picker_view.py` | done | `widgets/selection_footer.py`, used by `sidebar_view.build_sidebar` and `image_table/picker_view`. `tests/test_selection_footer.py` presses the real buttons on both real builders; mutation (Image Table's All disconnected) → 1 failed. Image Table's count now reads "N wells selected" / "No wells selected". |
| S2 | The main footer's count comes from the existing `_sel_count_lbl` writer in `selection_controller` (which already says "N/M sets visible" in group mode), not a second count. | `review/core/selection_controller.py` (~1178) | done | The footer registers as `_sel_count_lbl`; `refresh_sidebar_map_now` also hides **All** on a single-select plate. `test_selection_footer.py::test_the_main_footer_is_the_count_the_selection_controller_writes`; mutation (registration removed) → 2 failed. Full-app half: `tests/test_selection_footer_app.py` (slow lane). |
| S3 | `select_all` ignores the per-well-tab rule `select_none` already follows: on Cell Browser / Review CSV with groups defined, **All** would show groups instead of selecting wells. Exposing an **All** button makes this reachable. | `review/core/selection_controller.py::select_all` | done | `select_all` now uses `select_none`'s group-mode rule. `tests/test_deselect_all.py::test_all_on_a_per_well_tab_selects_wells_even_with_groups_present` run on a worktree of `origin/main`: `AssertionError: assert set() == {'A1', 'B2', 'C3'}`. |
| S4 | Saved selections sat ~280 px below the plate behind a stretch, collapsed, with no hint it held anything. Put it directly under the footer and show the count in its header ("Saved selections (3)"). | `review/core/sidebar_view.py`, `image_table/picker_view.py`, `refresh_plot_groups_sidebar` | done | Drawer directly under the footer and hint on the main frame, under the instruction on Image Table; header reads "Saved selections (N)" (`saved_selections.saved_selections_title`). `test_selection_footer.py`; mutations (stretch put back above the drawer; header count removed) → 1 failed each. |
| S5 | Image Table's instruction text is pinned at the rail's foot, far from the plate it explains. Put it directly under the footer. | `image_table/picker_view.py` | done | `test_selection_footer.py::test_image_table_puts_help_and_saved_selections_right_under_the_footer` asserts the order footer → instruction → drawer → stretch. |

## Step 4 — the registry owns each section's icon and rail frames

| Id | Finding | Place | State | Evidence |
|---|---|---|---|---|
| R1 | Icons come from a hand-written dict; a new top-level feature missing from it silently gets no glyph and nothing notices. Move the icon onto `FeatureDescriptor.icon`, delete `section_icons.py` and `ReviewApp._SECTION_ICONS`, and guard that every top-level descriptor names an icon `widgets.icons` can draw. | `review/core/section_icons.py`, `review/runtime_app.py:431`, `review/core/feature_registry.py` | done | `tests/test_rail_sections_are_declared.py::test_every_top_level_page_names_a_glyph_the_icon_set_can_draw`; mutation (Spatial's icon blanked) → 1 failed. `runtime_app.py` 834 → 830, budget lowered in `test_guardrails.py`. |
| R2 | The rail's per-page frames are named in two core rosters (`SIDEBAR_SECTION_FRAMES`, the shell's builder). A frame missing from the hide-all roster is never hidden and stays stuck on screen. Declare the frames on the descriptors (`rail_sections`) and derive both the build list and the hide-all list from the registry. | `review/core/tab_controller.py:46`, `review/core/app_shell_view.py:567` | done | `test_rail_sections_are_declared.py::test_the_rail_frames_are_the_declared_ones_main_first`, `::test_every_page_with_a_hook_declares_its_rail_frames`, `::test_the_tab_switch_shows_exactly_the_page_s_frames`; mutation (Image Table's `rail_sections` deleted) → 3 failed. Shell builds frames from `FeatureRegistry.rail_sections()`; `SIDEBAR_SECTION_FRAMES` deleted. |
| R3 | Fifteen activation hooks repeat "show `_sidebar_main_frame`, refresh the plate". With R2 in place core shows each declared frame and refreshes the plate once, before the hook runs; delete the copies. | `review/features/registry.py` | done | `test_rail_sections_are_declared.py::test_hooks_leave_the_rail_frames_to_the_tab_switch`; mutation (show line re-added to `_activate_spatial`) → 1 failed. `tab_controller.show_rail_sections` repaints the plate once; mutation (repaint removed) → 5 failed. smFISH keeps one repaint *after* it narrows the selection, with the reason at the site. |
| R4 | `tests/test_split_sidebar_one_well_picker.py` keeps a private copy of the frame roster; derive it from the same source as R2. | `tests/test_split_sidebar_one_well_picker.py:65` | done | Derived from `app._ctx.features.rail_sections()` (`_section_frames`). Slow lane at 9a84042: `test_split_sidebar_one_well_picker.py` 10 passed. |

## Step 5 — dead frame removed, Saved selections in its own module

| Id | Finding | Place | State | Evidence |
|---|---|---|---|---|
| C1 | `_sidebar_sample_frame` is built, registered and hidden, and never filled or shown. | `app_shell_view.py:582`, `tab_controller.py:51`, `home/controller.py:211` | done | Frame, roster entry and Home's hide call deleted. `test_one_plate_on_the_sample_map.py::test_home_never_shows_a_rail_plate_frame` and `test_home_page.py::test_home_shows_no_rail_body_on_any_tab` rewritten to the declared model; mutation (Home declares the sample frame) → 3 failed in `test_rail_sections_are_declared.py`. |
| C2 | About 400 of `sidebar_view.py`'s 525 lines are the Saved selections feature inside the rail's builder. Move them to `review/core/saved_selections.py`; `build_sidebar` keeps the plate, the footer and the hint. | `review/core/sidebar_view.py` | done | Moved to `review/core/saved_selections.py` (406 lines); `sidebar_view.py` 525 → 145. Callers and four test files import the new module. `tests/test_saved_selections_live_in_their_own_module.py`; mutation (a `_plot_groups_delete` re-defined in `sidebar_view.py`) → 1 failed. |

## Step 6 — the review items first left out, now asked for (2026-11-01)

The reporter asked for these after the first five steps landed, and pointed out
that moving smFISH (G1a) left **Images** a group of one — which is a question
of information architecture, so it goes to a UX review before any code.

| Id | Finding | Place | State | Evidence |
|---|---|---|---|---|
| X1 | `tab_controller.on_tab_change` names `"Heat Map"` and `_heatmap_sidebar_frame`; `sidebar_view.build_sidebar` builds the heat-map frame; `image_browser/controller.py` branches on sub-tab names to fill its sub-tabs and rail frames. | `review/core/tab_controller.py`, `review/core/sidebar_view.py`, `review/features/image_browser/controller.py` | todo | |
| X2 | `features/export/sidebar_view.py` is the right-hand Properties panel, not the rail; the shared name misleads. | `review/features/export/sidebar_view.py` | todo | |
| X3 | Output pages (Review CSV, Saved Plots, Batch Export) sit among the analysis pages. Where they belong is a UX decision. | `review/features/registry.py` | todo | |
| G3 | After G1a, **Images** holds only Image Browser. The reporter notes smFISH, like Nanowells, is defined by a distinct kind of input data. Decide the group structure (names, membership, order) with a UX review, then declare it. | `review/features/registry.py` | todo | |
