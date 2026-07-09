# Image Table → Table Builder: a drag-and-drop redesign

**Status:** design proposal (no code yet)
**Branch:** `claude/image-table-builder-qjjfc0`
**Scope:** the Image Browser → **Image Table** sub-tab
(`well_viewer/features/image_browser/image_table/`)

This document proposes replacing the Image Table's build-then-generate,
grid-of-dropdowns workflow with a **role-based drag-and-drop builder** modelled
on JMP Pro's Graph Builder, adapted to All-Well's four-dimensional image data.
It first names the concrete design challenges in today's code, then gives the
proposed model, then an incremental implementation plan that reuses the existing
layout math and render pipeline rather than rewriting them.

---

## 1. What the Image Table is today

The data the tab arranges is inherently **four-dimensional**: every rendered
cell is one image identified by a point in

```
(Well, Channel, Timepoint, FOV)
```

plus per-cell *display* choices (colormap/LUT, Raw-vs-Corrected source,
boundary overlay, mask/threshold binary, log scale). "Channel" is generalised —
it also covers the virtual `NUC+SEG` overlay and user-defined ratio metrics
(`grid_layout._channel_options`).

Today that 4-D space is projected onto a **fixed 2-D rectangular grid of
dropdown cells**. Each cell (`grid_view.build_selector_cell`) is a groupbox with
four `QComboBox`es — Well / Channel / Timepoint / FOV. The flow is:

1. **Build** — assemble the empty *selector* grid. Either
   - set the Rows/Cols spinners and fill all `rows × cols × 4` dropdowns by
     hand, or
   - use **Build Layout**: pick a row axis and a column axis and let
     `build_layout.image_table_build_layout` auto-fill every cell, holding the
     off-axis dimensions fixed.
2. **Generate** — `controller.image_table_generate` walks the filled cells,
   loads each image, and lays out the rendered thumbnails in a *second* grid
   below the selector.

To let more than two dimensions vary, three separate mechanisms were bolted on
top of the two-axis model over successive versions:

| Mechanism | Where | What it does |
|---|---|---|
| **Nested / compound axes** | `state.row_dims` / `col_dims`, `_build_nested_layout`, the Rows▾/Columns▾ multi-select drag pickers | Each axis can carry an ordered list of dims; the axis values become the mixed-radix Cartesian product (e.g. rows = Wells › Channels). |
| **Grouping / tiling** | `state.group_size` / `group_row_span`, the Group spinner, `_layout_cell_values(..., n_row_vals, group_size)` | Wrap the columns axis into vertically-stacked bands, repeating the rows axis per band (the "movie montage" case). |
| **Held off-axis values** | the "Hold off-axis" row (Ch/Time/FOV combos), `_fixed_off_axis` | Pin each non-faceted dimension to a single value. |
| **Per-dim filters** | `active_channels` / `active_timepoints` / `active_fovs` / `active_wells`, the Channels▾/Time▾/FOV▾ menus | Bound each dimension's value set so the product doesn't explode. |

---

## 2. The design challenges

Grounded in the current code, the pain points are:

1. **Build-then-Generate is a two-phase indirection.** You configure an
   *abstract* selector grid, then render, then discover it's wrong, then
   reconfigure. The selector grid and the rendered grid are two separate
   surfaces stacked vertically (`tab_view.build_image_table_tab` builds both),
   doubling the vertical footprint and the cognitive load. Nothing you touch
   during "Build" shows an image.

2. **Four dimensions, two axes, three escape hatches.** Nesting, grouping, and
   held-values each solve *part* of "I have 4 dims and 2 visual axes," with
   three different UIs (a drag multi-select popup, a spinner, a row of combos)
   and their own persistence fields. They don't compose cleanly — e.g. grouping
   only applies to the flat single-axis path, not the nested path
   (`build_layout._build_nested_layout` sets `group_size = 0`).

3. **The mapping from dimension → visual position is scattered.** To know "what
   is my table's shape" you must read the Rows▾ picker, the Columns▾ picker, the
   Hold-off-axis combos, the Group spinner, *and* three filter menus. There is
   no single place that says *channel down the rows, time across the columns,
   well held at A1, FOV held at 1*.

4. **No direct manipulation.** You cannot drag Channel onto the rows, drag a
   well into a fixed slot, or drag a rendered thumbnail to reorder. Everything
   is mediated by dropdowns. Reordering nesting is the one drag interaction, and
   it needed a bespoke non-grabbing popup (`_DropdownPanel`) to work around
   Qt.Popup's mouse grab.

5. **Per-cell dropdowns are `O(rows × cols × 4)` widgets** that are almost
   always redundant. In the common case every cell in a row shares a channel and
   every cell in a column shares a timepoint — the grid is a pure product — yet
   the UI still builds and shows four combos per cell. The "Apply to whole row"
   shortcut in each row groupbox exists precisely because per-cell editing is too
   granular for the common case.

6. **Display styling is coupled to the rows axis.** LUT / colormap / source /
   boundaries / binary / log live in a per-row table (`grid_view.build_lut_row`)
   keyed by `lut_row_map`. This only reads naturally when **rows == channels**.
   When channel is on the columns (or held fixed, or nested inner), "per-row
   display" is conceptually wrong — a channel's LUT is a property of the
   *channel*, not of whatever row it lands on. The code already works around this
   by re-keying the LUT table by channel (`_rebuild_lut_row_map_by_channel`).

The through-line: **the rectangular-grid-of-dropdowns is the wrong primary
abstraction.** The natural abstraction is *"assign each of my four dimensions a
role, and style each channel,"* which is exactly what JMP's Graph Builder does
with drop zones.

---

## 3. The proposed model: a role shelf

Borrow JMP Graph Builder's shelf-and-zones idea and specialise it to the image
domain.

### 3.1 Dimensions become draggable chips

A **palette** lists the available dimensions as chips:

- **Well**, **Channel**, **Timepoint**, **FOV** — the four intrinsic dims.
- Channel implicitly includes the virtual `NUC+SEG` overlay and ratio metrics,
  exactly as `_channel_options` already enumerates them.

Each chip shows its current value-count (e.g. "Channel · 3", "Timepoint · 8")
and opens a **value filter** on click (the existing `active_*` sets) so you can
restrict which values participate without leaving the builder.

### 3.2 Zones give each dimension a role

Drag a chip into exactly one zone:

| Zone | Meaning | Maps onto today's model |
|---|---|---|
| **Rows** | Facet down the rows. Multiple chips nest outer→inner. | `row_dims` (ordered) |
| **Columns** | Facet across the columns. Multiple chips nest, and a **Wrap** toggle tiles a long columns axis into stacked bands. | `col_dims` (ordered) + `group_size`/`group_row_span` |
| **Hold** | Not faceted — pinned to a single value shown on the chip; click to change. | `_fixed_off_axis` + the Hold-off-axis combos |
| **Pages** *(optional, later)* | One separate table per value (export as a multi-page/animation set). | new; generalises the montage use-case |
| **Merge** | Channels dropped here composite additively into one cell (LUT-coloured RGB summed → white). | `state.composite` + `image_compose.composite_*_rgb`, generalised from "one appended row" to "a cell role" |

**Key simplification:** every dimension lives in exactly one zone, and the zone
*is* the role. Nesting, grouping, and holding stop being three disjoint
mechanisms and become "which zone, in what order, with what modifier." The
shelf is the single source of truth for the table's shape — challenge #3 and #2
dissolve.

### 3.3 Live, single-phase

Any shelf edit triggers one **debounced rebuild-and-render** — Build and
Generate merge. Dropping Channel on Rows immediately re-lays the grid and
renders thumbnails. There is no empty selector grid: **the rendered thumbnails
are the grid.** Challenge #1 dissolves.

The heavy per-cell dropdown grid is removed from the default surface.
Per-cell overrides (the genuinely irregular table) move to **progressive
disclosure**: click a rendered cell → a small popover exposes that cell's
(Well/Channel/Timepoint/FOV) for a one-off override, writing into the same
`cells` model the renderer already reads. Challenge #5 dissolves for the 95%
case while keeping the escape hatch.

### 3.4 Channel styling is a channel property

Move LUT / colormap / source / boundaries / binary / log off the per-row table
and onto **per-channel** style records, edited from a popover on the Channel
chip (or a compact "Channels" panel). A channel renders the same way wherever it
sits — rows, columns, held, or merged. Challenge #6 dissolves. The existing
per-row `lut` dict becomes a per-channel `channel_style` dict; the render path
(`_row_display_opts`, `_row_cmap_name`, `_parse_lut`) changes from "look up by
row index" to "look up by the cell's channel," which is what
`_rebuild_lut_row_map_by_channel` already approximates.

---

## 4. Why this fits the existing architecture

This is deliberately a **UI-and-state reshaping, not a pipeline rewrite.** The
value-producing layers stay:

- **Layout math** — `grid_layout._product_values`, `_layout_cell_values`,
  `_layout_cell_values_nested`, `_fixed_off_axis` already turn
  `(row_dims, col_dims, held, filters)` into per-cell dim values. The shelf
  writes those exact state fields, so the builder can call the existing
  `image_table_build_layout` / `_build_nested_layout` unchanged.
- **Render + compose** — `image_table_generate`, `image_compose.*`, `lut_ops.*`,
  the zip cache, and `export.py` are untouched. They read the `cells` grid and
  per-cell/-row style; the reshaping only changes *who writes* those.
- **Persistence** — `persistence/view_state.py` already round-trips
  `row_dims` / `col_dims` / `group_size` / `group_row_span` / `active_*`. The
  shelf serialises to the same fields, so saved states keep loading. Per-channel
  style is a small additive block.
- **Feature-removal contract** — the work stays inside
  `features/image_browser/image_table/`; no new module-scope imports of the
  feature from core, so the §5 removability guards hold.

The `cells` grid of combo widgets is retained as the **internal intermediate
representation** the renderer consumes — it just stops being the primary,
always-visible editing surface. That keeps the change low-risk and reversible.

---

## 5. Implementation plan (phased)

Each phase is independently shippable and leaves the tab working.

### Phase 0 — extract the value model (no UI change)
- Introduce a Qt-free `TableSpec` in `state.py`: `{rows: [dim...], cols:
  [dim...], hold: {dim: value}, wrap: int, merge: [chan...], filters:
  {dim: set}}`. It is a *view* over the existing fields (`row_dims`, `col_dims`,
  `group_size`, `active_*`) — add a `to_spec()` / `apply_spec()` pair so old and
  new code read/write the same state.
- Add `channel_style: dict[str, ChannelStyle]` to `ImageTableState`
  (colormap/source/boundaries/binary/log/lut per channel), defaulting from
  today's per-row values. Render path reads channel-keyed style with the per-row
  table as fallback.

### Phase 1 — the shelf UI (behind the existing grid)
- New `builder_view.py`: the palette of dimension chips + the Rows / Columns /
  Hold / Merge drop zones, using Qt drag-and-drop (`QDrag` / `dropEvent`) with
  chips as `QToolButton`-like widgets. Reuse the ordered-nesting and value-filter
  logic already proven in `tab_view._make_dims_picker` / `_make_filter_picker`.
- Dropping/reordering/holding writes the `TableSpec`, calls `apply_spec()`, then
  the existing `image_table_build_layout` + a debounced `image_table_generate`.
- Ship it *above* the current selector grid first, so both drive the same state
  and can be compared side by side.

### Phase 2 — live render, collapse the selector grid
- Merge Build + Generate into one debounced `rebuild()` on any shelf change
  (drop, reorder, hold-value change, filter change, wrap toggle).
- Hide the always-on per-cell dropdown grid; add click-to-edit cell popovers for
  overrides (write into `cells`). Keep a "Show selector grid" escape toggle for
  one release for irregular-table users.

### Phase 3 — channel styling panel
- Replace the per-row levels table with a per-channel style popover on each
  Channel chip (and/or a compact Channels panel). The render path switches to
  channel-keyed style lookup as the primary; per-row becomes legacy-only.

### Phase 4 — polish & retire
- Fold Wrap and Merge fully into the shelf (retire the Group spinner and the
  Composite toggle button). Retire the legacy selector grid + per-row LUT table
  once the popover path has soaked. Update `test_monolith_budgets.py` ratchets
  and `ARCHITECTURE.md` §5.5.

### Testing
- The Qt-free `TableSpec` ↔ state round-trip is pure-Python and unit-testable
  with no Qt (matches the `tests/` no-Qt scaffold).
- A `test_feature_removal`-style boot+load test still passes (no new core
  imports).
- `view_state` round-trip test extended for `channel_style`.

---

## 6. Before / after

| | **Today** | **Proposed** |
|---|---|---|
| Mental model | fixed grid of 4-dropdown cells | assign each dimension a role |
| Vary >2 dims | nesting **and** grouping **and** hold (3 UIs) | drop chips into zones (1 UI) |
| See the table's shape | read 5+ scattered controls | read one shelf |
| Configure → see result | Build, then Generate (2 phases) | live (1 phase) |
| Per-cell widgets | `rows × cols × 4` combos, always shown | rendered thumbnails; edit on click |
| Channel display | per-row table (only right when rows=channels) | per-channel style, position-independent |
| Reorder | drag inside a popup | drag chips / thumbnails on the canvas |

The rendered grid, the compose/LUT/export layers, and persistence are reused;
the change is concentrated in the tab's control surface and in *who writes* the
already-existing layout state.
