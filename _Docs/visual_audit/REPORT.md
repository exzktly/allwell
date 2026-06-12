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
| Empty state | 4 | 4 | 4 | 4 | 4 | 4 | 4 | ✅ control bars greyed until a dataset loads |
| Plotting · Bar Plots | 3 | 4 | 3 | 3 | 4 | 4 | 4 | Tall 4-row toolbar; unlabeled icons; stray bar labels |
| Plotting · Line Graphs | 4 | 4 | 3 | 3 | 4 | 4 | 4 | Unlabeled icons; legend repeated per subplot |
| Plotting · Scatter (per-cell) | 4 | 4 | 3 | 2 | 4 | 4 | 4 | `gfp` lowercase vs `GFP`; mpl toolbar appears |
| Plotting · Scatter (per-well) | 3 | 4 | 3 | 2 | 3 | 3 | 3 | Blank 0–1 axes; casing; mpl toolbar |
| Plotting · Distribution | 4 | 4 | 4 | 3 | 4 | 4 | 4 | mpl toolbar appears (inconsistent w/ line/bar) |
| Plotting · Heat Map | 3 | 3 | 3 | 4 | 4 | 3 | 4 | ✅ dark bg fixed; still cramped when no layout configured |
| Statistics | 3 | 4 | 3 | 2 | 4 | 4 | 4 | ✅ "Run test" + column alignment fixed; casing (`gfp`) remains |
| Image Table | 3 | 4 | 3 | 3 | 3 | 3 | 3 | ✅ row table aligned, labels fixed, row-boxes captioned; still dense by nature |
| Segmentation · Review Image | 3 | 3 | 3 | 3 | 3 | 3 | 3 | Dense single-row toolbar; many buttons |
| Segmentation · Playground | – | – | – | – | – | – | – | Did not render (optional deps) |
| smFISH | 4 | 3 | 3 | 3 | 4 | 4 | 4 | ✅ duplicate message fixed; controls greyed until data loads |
| Review CSV | 4 | 4 | 4 | 4 | 5 | 4 | 5 | Header text right-aligned for text cols |
| Sample Def · Cell Gating | 4 | 4 | 4 | 4 | 5 | 4 | 5 | ✅ CDF axes now focus on the data range |
| Sample Def · Groups | 4 | 4 | 4 | 3 | 4 | 4 | 4 | ✅ empty-state hint fills the void; footer reads in context |
| Sample Def · Well Labels | 4 | 5 | 4 | 3 | 5 | 4 | 5 | `a01` lowercase vs plate `A01` |
| Sample Def · Ratios | 4 | 3 | 4 | 4 | 4 | 4 | 4 | Header alignment; split action bar |
| Sample Def · Pipeline Info | 5 | 5 | 5 | 5 | 5 | 5 | 5 | — (reference for the rest) |
| Sample Def · Notes | 5 | 5 | 4 | 4 | 5 | 5 | 5 | Doc toolbar icons unlabeled |
| Batch Export | 3 | 3 | 3 | 3 | 3 | 3 | 4 | Dense two-column; large top gap |
| Analyze | 4 | 4 | 4 | 4 | 3 | 4 | 4 | ✅ schema field widened/shows start; right pane still sparse when idle |
| Help drawer | 4 | 4 | 4 | 4 | 4 | 4 | 4 | Underlay not dimmed |

---

## Per-view findings

### Empty state — `00-empty-state.png` *(fixed in this pass)*
The "Load a directory" placeholder is clear and well-centered.
- **[H→fixed]** The Plotting control bar (Channel / Property / Threshold / Fold change /
  Timepoints / Group by Well / Beeswarm / Violin / Smooth) is now **greyed out until a
  dataset loads**, so the empty state no longer presents a wall of live-looking controls
  that do nothing. The sub-tab selector stays active.
- **[L]** Header icon buttons (refresh / image / home / info) are icon-only — but they all
  carry tooltips (hover-only; see cross-cutting note 4).

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
Good, readable. *(The matplotlib nav toolbar here is consistent with the other plots — see
the closed re-investigation note in Deferred.)*

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

### Image Table — `30-image-table.png` *(fixed in this pass)*
Was the single most cluttered view. Fixes applied:
- **[H→fixed]** The tinted "Row 1 / Row 2" shortcut boxes (Well + Channel that apply to a
  whole row) now carry an **"Apply to whole row"** caption, so they read as row-wide
  controls rather than duplicate cells beside the (1,1)…(2,3) grid.
- **[H→fixed]** The **"Per-row levels & display"** table no longer stretches the label
  column — Color/Min/Max/Fit/Top-hat/Boundaries/Binary now hug the left next to each
  "Row N" label (the large empty gap is gone).
- **[M→fixed]** Cell label no longer truncates ("Timepoint:" shows in full); the
  **"Bound."** column header is now **"Boundaries"**.
- *Remaining:* the per-cell grid is inherently dense (4 dropdowns × R×C) — acceptable for
  the feature; no further change made.

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
- **Cell Gating** `70-sample-def-cell-gating.png`: well-organized. **[M→fixed]** each CDF
  x-axis is now clamped to its data range, so the curve spreads across the plot instead of
  cramming against one edge while a gate line at 0 stretched the axis.
- **Groups** `70-sample-def-groups.png`: **[M→fixed]** the empty list now shows a centered
  "No groups yet — use +Add above or From selection below" hint, filling the void so the
  footer reads in context instead of marooned at the bottom.
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
Good structure (schema form | run dashboard with plate + legend). **[M→fixed]** the **Schema
string** field no longer clips — it expands to fill the row and shows from the start.
**[M]** the right pane still has a large empty lower half once the dashboard is idle
(left as-is; it fills during a run).

### Help drawer — `95-dialog-help.png`
Readable right-side drawer with Workflow / Shortcuts / Reference sections. **[L]** the
underlying app is not dimmed, so the drawer edge blends into the content.

---

## Cross-cutting findings (highest leverage)

1. **[H] Channel-name casing is inconsistent** — `GFP` (global combo, plot titles, Cell Gating)
   vs `gfp` (Scatter X/Y fields, Statistics, Pipeline Info). Pick one display casing
   (uppercase for fluor tokens) and apply everywhere combos are populated.
2. **[H→fixed] Control bars stayed active in empty / no-data states** (plotting bar, smFISH,
   Segmentation LUT) — now greyed out until a dataset loads (central gating on the shared
   `TabCtrl` marker), re-enabled on load.
3. **[H→fixed] Plot background theme was inconsistent** — Heat Map rendered white while all
   other plots use the dark plot theme; now themed.
4. **[L] Icon-only buttons** (window header, plot cards, Sample-Definitions doc toolbar).
   *Correction after code review:* these **already have `setToolTip` text** — they simply
   don't surface in static screenshots (hover-only). No change needed; the discoverability
   concern is lower than the screenshots suggested.
5. **[resolved — no change] matplotlib nav toolbar** — looked uneven but is actually
   consistent (every PlotCard embeds one; it just scrolls off-screen on the tall stacked
   plots). See the closed re-investigation note under Deferred.
6. **[M] Duplicate / redundant messaging** — smFISH empty text twice *(fixed)*; status
   "Ready." in both the status bar and the status-dot label *(still present)*.
7. **[fixed] Marooned action bars & label↔control gaps** — Groups (empty-state hint added)
   and Image Table (per-row controls now hug the left) resolved.

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

## Fixes applied in this pass

| Sev | Fix | File(s) | Status |
|-----|-----|---------|--------|
| H | **Heat Map dark background** — figure/axes background and empty-cell fill now track the active plot theme (`_bg` token) instead of matplotlib-default white. | `well_viewer/features/heatmap/controller.py` | ✅ done |
| H | **Statistics "Run test" alignment** — moved to its own row under the form; added a trailing stretch column so labels/fields hug the left instead of splitting width 3 ways. | `well_viewer/features/stats/view.py` | ✅ done |
| H | **smFISH duplicate empty-state message** — full guidance now shown once (on the canvas); the status line is a concise one-liner. | `well_viewer/features/smfish/tab_view.py` | ✅ done |
| M | **Image Table clutter** — fixed the truncated "Timepoint:" cell label (width 54→72); the per-row levels table no longer stretches the label column (controls hug the left instead of floating far-right with a big gap); "Bound." → "Boundaries"; the tinted row-shortcut boxes now carry an "Apply to whole row" caption so they read as row-wide controls, not stray cells. | `well_viewer/features/image_table/grid_view.py` | ✅ done |
| — | **`flush_dirty_scopes` regression** (functional) — `tab_controller` imported it from `fold_change_controls`, which never re-exported it, so every tab switch threw `ImportError` (Qt-swallowed). Added the missing re-export and replaced the debug `traceback.print_exc()` with a logged swallow. | `well_viewer/domain/fold_change_controls.py`, `well_viewer/core/tab_controller.py` | ✅ done |
| M | **Control bars greyed out in the empty / no-data state** — the per-tab control rows (which all share the `TabCtrl` object name) plus the plotting ctxbar's channel row are now disabled until a dataset loads, and re-enabled the moment one does. Central one-point gating in `refresh_plot_empty_states`; Segmentation + Statistics control rows tagged `TabCtrl` so they're included. | `well_viewer/core/ui_helpers.py`, `well_viewer/runtime_app.py`, `well_viewer/features/preview/panel_view.py`, `well_viewer/features/stats/view.py` | ✅ done |
| L | **Analyze schema-string field** — was a fixed 170 px that clipped the schema; now expands to fill the row and shows from the start. | `analyze_tab_form.py` | ✅ done |
| L | **Groups empty state** — the empty list panel now shows a centered "No groups yet" hint instead of a void above the marooned "From selection / Import" footer. | `widgets/saved_selections_list.py` | ✅ done |
| L | **Cell Gating CDF auto-range** — each CDF x-axis is clamped to its data range (+ pad) so a gate line at 0 no longer stretches the axis and crams the curve against one edge. | `well_viewer/features/grouping/cell_gating_tab_view.py` | ✅ done |

Verified by re-rendering: see `00-empty-state.png`, `10-plotting-heat-map.png`, `20-statistics.png`,
`50-smfish.png`, `70-sample-def-cell-gating.png`, `70-sample-def-groups.png`, `90-analyze.png`
(the screenshots in this folder are the post-fix state). `ruff` clean; the runnable fast
test lane (700 tests) passes — the 5 + 7 skipped/errored modules are pre-existing
`tifffile`/heavy-ML import gaps in the audit environment, unrelated to these changes.

The harness also gained a canvas-compositing step in `grab()`: offscreen window-grab failed
to blit the deeply-nested heatmap canvas (it came out white though the figure rendered dark),
so each `FigureCanvas` is now drawn synchronously and composited onto the window pixmap.

## Deferred (higher-risk or larger-scope — recommended follow-ups)

| # | Sev | Item | Why deferred |
|---|-----|------|--------------|
| 1 | H | **Channel-token casing** (`gfp`→`GFP` in Scatter/Statistics) | Not display-only: `col_for_scatter_entry` builds CSV column names straight from the combo text (`f"{entry}_{metric}"`), and view-state persistence stores the raw text. A safe fix needs a display↔token mapping across ~8 read/persistence sites — a focused refactor, not a one-liner. **Per the user's instruction this is the only item intentionally left undone.** |

### Re-investigated and closed without a code change

- **matplotlib nav toolbar consistency** — on closer inspection it is already consistent:
  every PlotCard plot (line / bar / scatter / distribution / heatmap) embeds the same
  `MplToolbar`, and the raw-canvas tabs (cell-gating, smFISH) add one explicitly. It only
  *looked* absent on Line/Bar because those are tall 3-subplot plots where the toolbar
  scrolls below the fold. The toolbar provides pan/zoom that image-based tabs rely on, so
  removing it would lose function — no change made.

## Discovered functional regression (now fixed)

The `flush_dirty_scopes` `ImportError` thrown on every tab switch — `tab_controller`
imported the symbol from `fold_change_controls`, which re-exported the other scope helpers
(`get_scope`, `registered_scopes`) but had omitted `flush_dirty_scopes`. Added the missing
re-export and swapped the debug `traceback.print_exc()` for a logged swallow. Tab switches
no longer raise; the deferred fold-change scope redraw now actually flushes on tab change.
