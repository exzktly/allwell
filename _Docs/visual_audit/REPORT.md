# AllWell — Visual UI Audit

**Date:** 2026-06-11 · **Build:** v6.2.0 · **Method:** every view rendered head­less
(Qt offscreen, `QWidget.grab()`) at 1640×980 via `tests/visual/render_views.py`, loaded
with the deterministic `mini_dataset` (4 wells A01/A02/B01/B02 × 3 timepoints, GFP +
mCherry + DAPI) and all wells selected. Screenshots in [`screenshots/`](./screenshots).

Each view is graded against the requested criteria on a 1–5 scale
(5 = excellent): **Clutter-free · Alignment · Action clarity · Consistency ·
Cleanliness · Usability · Professionalism**. Findings are tagged
**[H]igh / [M]edium / [L]ow**.

> **Coverage note.** Image-bearing views (Image Table, Segmentation→Review Image,
> smFISH) render their *empty/placeholder* state because the CSV fixture carries no
> image archives — this is intentional empty-state coverage. **Segmentation→Playground
> could not render** (it imports `process_microscopy`/`tifffile`, optional deps absent in
> the audit environment) — see screenshot `40-segmentation-playground.png` (blank).

---

## Score summary

| View | Clut | Algn | Act | Cons | Clean | Use | Prof | Worst issues |
|------|:--:|:--:|:--:|:--:|:--:|:--:|:--:|------|
| Empty state | 2 | 4 | 3 | 3 | 3 | 3 | 3 | Full plot toolbar active with no data |
| Plotting · Bar Plots | 3 | 4 | 3 | 3 | 4 | 4 | 4 | Tall 4-row toolbar; unlabeled icons; stray bar labels |
| Plotting · Line Graphs | 4 | 4 | 3 | 3 | 4 | 4 | 4 | Unlabeled icons; legend repeated per subplot |
| Plotting · Scatter (per-cell) | 4 | 4 | 3 | 2 | 4 | 4 | 4 | `gfp` lowercase vs `GFP`; mpl toolbar appears |
| Plotting · Scatter (per-well) | 3 | 4 | 3 | 2 | 3 | 3 | 3 | Blank 0–1 axes; casing; mpl toolbar |
| Plotting · Distribution | 4 | 4 | 4 | 3 | 4 | 4 | 4 | mpl toolbar appears (inconsistent w/ line/bar) |
| Plotting · Heat Map | 2 | 3 | 3 | 1 | 2 | 3 | 2 | **White figure in dark UI**; cramped |
| Statistics | 3 | 2 | 3 | 2 | 3 | 3 | 3 | **"Run test" floats far-right, misaligned**; casing |
| Image Table | 1 | 2 | 2 | 2 | 2 | 2 | 2 | **Most cluttered**; dup Row boxes; label/control gap; truncation |
| Segmentation · Review Image | 3 | 3 | 3 | 3 | 3 | 3 | 3 | Dense single-row toolbar; many buttons |
| Segmentation · Playground | – | – | – | – | – | – | – | Did not render (optional deps) |
| smFISH | 2 | 3 | 2 | 2 | 2 | 2 | 3 | **Duplicate empty-state message**; controls active w/ no data |
| Review CSV | 4 | 4 | 4 | 4 | 5 | 4 | 5 | Header text right-aligned for text cols |
| Sample Def · Cell Gating | 4 | 4 | 4 | 4 | 4 | 4 | 4 | CDF auto-range crams data to edge |
| Sample Def · Groups | 3 | 3 | 3 | 3 | 3 | 3 | 4 | Action bar marooned at bottom; big empty middle |
| Sample Def · Well Labels | 4 | 5 | 4 | 3 | 5 | 4 | 5 | `a01` lowercase vs plate `A01` |
| Sample Def · Ratios | 4 | 3 | 4 | 4 | 4 | 4 | 4 | Header alignment; split action bar |
| Sample Def · Pipeline Info | 5 | 5 | 5 | 5 | 5 | 5 | 5 | — (reference for the rest) |
| Sample Def · Notes | 5 | 5 | 4 | 4 | 5 | 5 | 5 | Doc toolbar icons unlabeled |
| Batch Export | 3 | 3 | 3 | 3 | 3 | 3 | 4 | Dense two-column; large top gap |
| Analyze | 3 | 4 | 4 | 4 | 3 | 4 | 4 | Schema string truncated; large empty right pane |
| Help drawer | 4 | 4 | 4 | 4 | 4 | 4 | 4 | Underlay not dimmed |

---

## Per-view findings

### Empty state — `00-empty-state.png`
The "Load a directory" placeholder is clear and well-centered, **but the entire Plotting
control bar** (Channel `—`, Property, Threshold 50.0, Fold change, Timepoints, Group by
Well, Beeswarm, Violin, Smooth) **is fully rendered and interactive with no data behind
it**. It implies actions that do nothing.
- **[H]** Hide or disable the per-tab control bars until a dataset is loaded; show only the
  empty-state prompt.
- **[M]** Header icon buttons (refresh / image / home / info) are icon-only and ambiguous.

### Plotting · Bar Plots / Line Graphs — `10-plotting-bar-plots.png`, `10-plotting-line-graphs.png`
Plots themselves are clean and professional. Issues:
- **[M]** The control bar is **four stacked rows** (channel row · fold-change row · timepoints
  row · error-band row) — tall and busy; consider grouping/collapsing.
- **[M]** Plot-card icon buttons (download / copy / save / sliders) are unlabeled — add tooltips.
- **[L]** Bar Plots renders **tiny stray "A01"/"B02" labels** at the very top of the axes.
- **[L]** Legend is repeated in each stacked subplot.

### Plotting · Scatter — `11-scatter-per-cell-points.png`, `11-scatter-per-well-aggregate.png`
- **[M]** Channel fields read **`gfp` (lowercase)** while the rest of the app shows `GFP`.
- **[M]** Per-well aggregate shows an **empty 0.0–1.0 plot** (no data) — looks broken; needs a
  "select ≥2 timepoints / no aggregate to show" empty state.
- **[L]** A matplotlib navigation toolbar appears at the bottom here but not on line/bar plots.

### Plotting · Distribution — `10-plotting-distribution.png`
Good, readable. **[L]** matplotlib nav toolbar present (inconsistent with line/bar tabs).

### Plotting · Heat Map — `10-plotting-heat-map.png`
- **[H]** The heat-map figure is rendered on a **bright white background inside the dark UI** —
  visually jarring and inconsistent with every other plot (which use the dark plot theme).
- **[M]** The grid + colorbar are cramped into a small region with large empty margins.

### Statistics — `20-statistics.png`
- **[H]** **"Run test" button alignment**: it sits far-right on the *Channel* row only, leaving
  the Test / Timepoint / Property / Statistic rows with empty right-space. It reads as
  unrelated to the form. Anchor it to the form (e.g. below the fields, full-width, or
  right-aligned across all rows).
- **[M]** `gfp` lowercase. **[L]** Large empty results panel before a run — add a hint.

### Image Table — `30-image-table.png`
The single most cluttered view.
- **[H]** **Duplicate selectors**: "Row 1 / Row 2" boxes on the left (Well + Channel) sit
  beside a (1,1)…(2,3) grid whose cells *also* carry Well/Channel/Timepoint/FOV. The
  relationship is unclear and the screen is dense with ~24 input fields.
- **[H]** **"Per-row levels & display"** row labels (Row 1 / Row 2) are on the far left while
  their controls (Color/Min/Max/Top-hat/Bound./Binary) are on the far right with a vast
  empty gap between — hard to associate a row with its controls.
- **[M]** Truncated labels: grid cells show **"Timepoi"**; column abbreviated **"Bound."**.

### Segmentation · Review Image — `40-segmentation-review-image.png`
- **[M]** Toolbar packs ~10 buttons in one row (Delete Cell · Crop Region · Clear crop · Reset
  · Raw · Flat-field · Corrected · Fit · − · +). Raw/Flat-field/Corrected look like a
  segmented control but read as loose buttons.
- **[L]** "No FOV selected." sits mid-form; LUT/colour controls remain active with no image.

### smFISH — `50-smfish.png`
- **[H]** The empty-state message **"No smFISH data was generated…"** is shown **twice** — once
  as a yellow line under the toolbar and once centered in the panel.
- **[M]** All controls (Channel/FOV/Timepoint/Thresh/LUT) are active with no data.

### Review CSV — `60-review-csv.png`
Clean and professional. **[L]** Column headers are right-aligned even for text columns
(well, channel), which looks slightly off against left-read values.

### Sample Definitions
- **Cell Gating** `70-sample-def-cell-gating.png`: well-organized; **[M]** CDF plots auto-range
  so the threshold marker sits at x=0 while data crams against the right edge (partly fixture
  data, but the auto-range should focus on the data).
- **Groups** `70-sample-def-groups.png`: **[M]** the "From selection / Import…" action bar is
  marooned at the very bottom of a large empty panel, far from the GROUPS header and its
  +Add/Clear-All controls.
- **Well Labels** `70-sample-def-well-labels.png`: excellent grid. **[L]** cells show `a01`
  lowercase vs uppercase `A01` elsewhere.
- **Ratios** `70-sample-def-ratios.png`: clean; **[L]** split action bar (Add/Remove bottom-left,
  Apply bottom-right) and right-aligned column headers.
- **Pipeline Info** `70-sample-def-pipeline-info.png`: **reference-quality** — grouped tree,
  alternating rows, clear Parameter/Value. Use as the design north-star.
- **Notes** `70-sample-def-notes.png`: clean. **[L]** doc toolbar (save/download/+/×) icons
  unlabeled.

### Batch Export — `80-batch-export.png`
Two-card layout (Export Groups | Output Settings) is reasonable. **[M]** dense; **[L]** large
empty band between the header and content.

### Analyze — `90-analyze.png`
Good structure (schema form | run dashboard with plate + legend). **[M]** the **Schema
string** field is truncated ("nnel:well:fov:timepoint"); **[M]** the right pane has a large
empty lower half once the dashboard is idle.

### Help drawer — `95-dialog-help.png`
Readable right-side drawer with Workflow / Shortcuts / Reference sections. **[L]** the
underlying app is not dimmed, so the drawer edge blends into the content.

---

## Cross-cutting findings (highest leverage)

1. **[H] Channel-name casing is inconsistent** — `GFP` (global combo, plot titles, Cell Gating)
   vs `gfp` (Scatter X/Y fields, Statistics, Pipeline Info). Pick one display casing
   (uppercase for fluor tokens) and apply everywhere combos are populated.
2. **[H] Control bars stay active in empty / no-data states** (Empty plotting bar, smFISH,
   Segmentation LUT). Disable or hide controls until their data exists.
3. **[H] Plot background theme is inconsistent** — Heat Map renders white while all other plots
   use the dark plot theme.
4. **[M] Icon-only buttons lack tooltips** in three places: window header (refresh/present/
   home/info), every plot card (download/copy/save/sliders), and the Sample-Definitions doc
   toolbar (save/download/+/×). Add tooltips so actions are discoverable.
5. **[M] matplotlib nav toolbar appears unevenly** (Distribution / Scatter / Cell Gating yes;
   Line / Bar no). Choose one policy — the `PlotCard` already provides export/copy, so the
   redundant mpl toolbar can be hidden everywhere for a cleaner, consistent look.
6. **[M] Duplicate / redundant messaging** — smFISH empty text twice; status "Ready." in both
   the status bar and the status-dot label.
7. **[M] Marooned action bars & label↔control gaps** — Groups (bottom action bar) and Image
   Table (per-row display) separate related elements across large empty space.

## Discovered functional bug (not visual)

While switching tabs the harness logged, on **every** notebook page change:

```
ImportError: cannot import name 'flush_dirty_scopes' from
'well_viewer.domain.fold_change_controls'  (tab_controller.py:178)
```

`tab_controller.on_notebook_current_changed` imports `flush_dirty_scopes`, but that symbol
does not exist in `fold_change_controls.py` (the module comment at line 199 still references
it). Qt swallows slot exceptions so the app keeps working, but the fold-change "dirty scope"
flush never runs on tab change. **Recommend fixing or removing the dead call** — outside the
visual scope, flagged for the maintainer.

---

## Prioritized fix backlog

| # | Sev | Fix | Primary file(s) |
|---|-----|-----|-----------------|
| 1 | H | Uppercase channel-token display in Scatter & Statistics combos | `well_viewer/features/scatter/*`, `well_viewer/features/stats/view.py` |
| 2 | H | Dark plot background for Heat Map (screen mode) | `well_viewer/features/heatmap/*`, `widgets/plot_card.py` |
| 3 | H | Statistics "Run test" button alignment | `well_viewer/features/stats/view.py` |
| 4 | H | Remove duplicate smFISH empty-state message | `well_viewer/features/smfish/tab_view.py` |
| 5 | M | Tooltips on icon-only buttons | `all_well.py`, `widgets/plot_card.py`, sample-def toolbar |
| 6 | M | Hide redundant matplotlib nav toolbar | `widgets/plot_card.py` / per-tab figure setup |
| 7 | M | Image Table: associate per-row controls; fix truncated labels | `well_viewer/features/image_table/grid_view.py` |
| 8 | M | Disable plot/control bars when no dataset loaded | `well_viewer/core/centre_view.py` + tab views |
| 9 | L | Analyze schema-string field width; Groups action-bar placement | `analyze_tab.py`, `well_viewer/features/grouping/*` |
