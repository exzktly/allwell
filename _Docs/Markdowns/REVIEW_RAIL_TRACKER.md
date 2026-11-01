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
| D1 | The manual says the section list is "grouped into Analysis, Images and Data"; nothing on screen grouped it. | `README_WellViewer.md`, *Section rail* bullet | todo | |
| D2 | The manual says the plate map and Saved selections are "pinned to the bottom of the left rail"; the plate sits directly under the section list. | `README_WellViewer.md`, *Plate map + Saved selections* bullet | todo | |
| D3 | The manual promises **Select all** / **Select none** under the plate; the rail offered "Deselect all" on plot pages and "Select All" / "Clear" on Image Table. | `README_WellViewer.md`, *Plate-map well picker* | todo | |
| D4 | ARCHITECTURE describes the rail's assembly (section icons, frame roster, sidebar_view) and must follow every structural row below. | `ARCHITECTURE.md` | todo | |

## Step 2 — the section list shows its groups

| Id | Finding | Place | State | Evidence |
|---|---|---|---|---|
| G1 | The registry declares Home / Analysis / Images / Data, but the rail draws eleven undifferentiated rows: the group chrome was retired and `group_starts` is unused. Show a caption above each group except Home, derived from each descriptor's `group`, so no new roster is written. | `widgets/rail_nav.py`, `review/core/tab_controller.py::sync_section_nav_from_notebook` | todo | |
| G2 | Phone layout lays the rail out as a two-column grid (`RailNav.setColumns(2)`); a caption must span both columns and start a fresh grid row, not occupy a tile. | `widgets/rail_nav.py::setColumns` | todo | |

## Step 3 — one selection footer, and Saved selections beside it

| Id | Finding | Place | State | Evidence |
|---|---|---|---|---|
| S1 | The same action has different words per page: "Deselect all" (plot pages), "Select All" / "Clear" + "N wells active" (Image Table); plot pages show no count. One reusable footer — count, **All**, **None** — built by one widget. | new `widgets/selection_footer.py`; `review/core/sidebar_view.py`; `image_table/picker_view.py` | todo | |
| S2 | The main footer's count comes from the existing `_sel_count_lbl` writer in `selection_controller` (which already says "N/M sets visible" in group mode), not a second count. | `review/core/selection_controller.py` (~1178) | todo | |
| S3 | `select_all` ignores the per-well-tab rule `select_none` already follows: on Cell Browser / Review CSV with groups defined, **All** would show groups instead of selecting wells. Exposing an **All** button makes this reachable. | `review/core/selection_controller.py::select_all` | todo | |
| S4 | Saved selections sat ~280 px below the plate behind a stretch, collapsed, with no hint it held anything. Put it directly under the footer and show the count in its header ("Saved selections (3)"). | `review/core/sidebar_view.py`, `image_table/picker_view.py`, `refresh_plot_groups_sidebar` | todo | |
| S5 | Image Table's instruction text is pinned at the rail's foot, far from the plate it explains. Put it directly under the footer. | `image_table/picker_view.py` | todo | |

## Step 4 — the registry owns each section's icon and rail frames

| Id | Finding | Place | State | Evidence |
|---|---|---|---|---|
| R1 | Icons come from a hand-written dict; a new top-level feature missing from it silently gets no glyph and nothing notices. Move the icon onto `FeatureDescriptor.icon`, delete `section_icons.py` and `ReviewApp._SECTION_ICONS`, and guard that every top-level descriptor names an icon `widgets.icons` can draw. | `review/core/section_icons.py`, `review/runtime_app.py:431`, `review/core/feature_registry.py` | done | `tests/test_rail_sections_are_declared.py::test_every_top_level_page_names_a_glyph_the_icon_set_can_draw`; mutation (Spatial's icon blanked) → 1 failed. `runtime_app.py` 834 → 830, budget lowered in `test_guardrails.py`. |
| R2 | The rail's per-page frames are named in two core rosters (`SIDEBAR_SECTION_FRAMES`, the shell's builder). A frame missing from the hide-all roster is never hidden and stays stuck on screen. Declare the frames on the descriptors (`rail_sections`) and derive both the build list and the hide-all list from the registry. | `review/core/tab_controller.py:46`, `review/core/app_shell_view.py:567` | done | `test_rail_sections_are_declared.py::test_the_rail_frames_are_the_declared_ones_main_first`, `::test_every_page_with_a_hook_declares_its_rail_frames`, `::test_the_tab_switch_shows_exactly_the_page_s_frames`; mutation (Image Table's `rail_sections` deleted) → 3 failed. Shell builds frames from `FeatureRegistry.rail_sections()`; `SIDEBAR_SECTION_FRAMES` deleted. |
| R3 | Fifteen activation hooks repeat "show `_sidebar_main_frame`, refresh the plate". With R2 in place core shows each declared frame and refreshes the plate once, before the hook runs; delete the copies. | `review/features/registry.py` | done | `test_rail_sections_are_declared.py::test_hooks_leave_the_rail_frames_to_the_tab_switch`; mutation (show line re-added to `_activate_spatial`) → 1 failed. `tab_controller.show_rail_sections` repaints the plate once; mutation (repaint removed) → 5 failed. smFISH keeps one repaint *after* it narrows the selection, with the reason at the site. |
| R4 | `tests/test_split_sidebar_one_well_picker.py` keeps a private copy of the frame roster; derive it from the same source as R2. | `tests/test_split_sidebar_one_well_picker.py:65` | wip | Derived from `app._ctx.features.rail_sections()` (`_section_frames`). Full-app file: needs its slow-lane run (`scripts/test.sh tests/test_split_sidebar_one_well_picker.py -m ""`) before `done`. |

## Step 5 — dead frame removed, Saved selections in its own module

| Id | Finding | Place | State | Evidence |
|---|---|---|---|---|
| C1 | `_sidebar_sample_frame` is built, registered and hidden, and never filled or shown. | `app_shell_view.py:582`, `tab_controller.py:51`, `home/controller.py:211` | done | Frame, roster entry and Home's hide call deleted. `test_one_plate_on_the_sample_map.py::test_home_never_shows_a_rail_plate_frame` and `test_home_page.py::test_home_shows_no_rail_body_on_any_tab` rewritten to the declared model; mutation (Home declares the sample frame) → 3 failed in `test_rail_sections_are_declared.py`. |
| C2 | About 400 of `sidebar_view.py`'s 525 lines are the Saved selections feature inside the rail's builder. Move them to `review/core/saved_selections.py`; `build_sidebar` keeps the plate, the footer and the hint. | `review/core/sidebar_view.py` | todo | |

## Raised by the reviews, outside the five steps

| Id | Finding | State | Reason |
|---|---|---|---|
| X1 | `tab_controller.on_tab_change` names `"Heat Map"` and `_heatmap_sidebar_frame`; `image_browser/controller.py` branches on sub-tab names to fill frames. | declined | Not among the five steps chosen. The heat-map frame lives *inside* the main frame and drives the plate's drag mode by focus, so it does not fit R2's frame model without changing the one-picker rule; it needs its own design. |
| X2 | `features/export/sidebar_view.py` is the right-hand Properties panel, not the rail; the shared name misleads. | declined | Not among the five steps chosen; a rename touching the feature-naming guard. |
| X3 | Output pages (Review CSV, Saved Plots, Batch Export) sit among analysis pages. | declined | Not among the five steps chosen. G1's **Data** caption already separates them visually; moving them is a navigation change for the user to decide. |
