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
abstraction.** The natural abstraction is *"give each axis a root, then nest the
channels I want to see under it,"* in the spirit of JMP's Graph Builder drop
zones but shaped to how this data is actually organised.

---

## 3. The proposed model: root + nested channels

Borrow JMP Graph Builder's drag-to-build feel, but structure each axis as a
**root with channels nested under it** rather than a flat list of variables.

### 3.1 Each axis is a root with channels nested under it

Rows and Columns each read as a small **tree**:

```
Rows
  ▸ Well                     ← the root (a facet dimension)
      ↳ DAPI  ⚙ corrected    ← channels nested under it, indented,
      ↳ GFP   ⚙ mask+outline    each with its own render options + LUT
```

- **Root.** Each axis starts with a **root** — a facet dimension: **Well**,
  **Timepoint**, or **FOV**. The root is what the axis primarily steps through
  (the wells down the rows, the timepoints across the columns). You drag a root
  onto the axis from the **Fixed** panel (§3.3), where every not-yet-laid-out
  dimension sits; there is no separate roots tray. **Well**'s values come from the
  plate map (the existing sidebar `WellPlateSelector` / `active_wells`); Timepoint
  and FOV carry a value filter (`active_timepoints` / `active_fovs`) — click the
  root to set it.
- **Channels nested under the root.** Drag a **channel** (DAPI / GFP / RFP /
  NUC+SEG) from the *Channels* tray onto an axis and it **nests under that axis's
  root, indented**. Multiple channels stack as sub-rows/-columns — so *Rows =
  Well › {DAPI, GFP}* renders each well's DAPI and GFP as two adjacent rows. The
  root is optional: with no root the channels sit at the top level (today's
  "channels down the rows"); adding a root just groups them under it.
- **Delete with ×.** Every channel and root carries a small **×**. On a channel
  it removes it; on a root it drops the dimension back to **Fixed** (a dimension
  can't be deleted — it's always a coordinate, so it's either on an axis or
  Fixed).

You read the whole table's shape off the two trees, and you build it by dragging
clear, individual chips — no "layer vs facet" abstraction and no buried list.

### 3.2 A nested channel carries render options, applied on top

A channel isn't just a colour — it is rendered a particular way, and those
options are set **on the channel chip** (its **⚙**), stacking on top of the base
image:

```
channel option = (render ∈ {raw, corrected, mask, ≥threshold},
                  boundary-outline overlay on/off, colormap/LUT)
```

This is exactly today's per-row display controls — the **Source** (raw /
corrected via `use_tophat`), the **Boundaries** outline, the **Binary** mode
(cell **mask** / **≥ threshold**), the colormap, LUT min/max
(`grid_view.build_lut_row`, `controller._row_display_opts`,
`_render_mask_binary`, `_render_threshold_binary`,
`_render_with_boundary_overlay`) — but attached to the *specific channel in the
tree* instead of a detached per-row table. So one nested *DAPI* renders as a
mask with a boundary overlay while the *GFP* beside it stays corrected, each with
its own LUT — and challenge #6 dissolves, because a channel's rendering travels
with its chip wherever it sits.

### 3.3 Zones, and Overlay for composites

| Zone | Meaning | Maps onto today's model |
|---|---|---|
| **Rows** | Root steps down the rows; its channels nest as sub-rows. | `row_dims` (ordered) |
| **Columns** | Root steps across the columns; channels nest; a **Wrap** control tiles a long columns axis into stacked bands. | `col_dims` (ordered) + `group_size`/`group_row_span` |
| **Fixed** | **Derived, not dragged-into.** Every root dimension *not* on an axis (Well / Timepoint / FOV) auto-appears here, pinned to one value for the whole table, with a value picker. Drag one onto Rows/Columns to make it vary; removing a root drops it back here. | `_fixed_off_axis` + the Hold-off-axis combos |
| **Pages** *(optional, later)* | One separate table per value (export as a multi-page/animation set). | new; generalises the montage use-case |

**Fixed is auto-filled.** With four coordinates and two axes, whatever you don't
lay out *must* resolve to a single value or a cell is ambiguous. Rather than make
the user remember that and drag the leftovers somewhere, **Fixed is derived**: it
lists exactly the root dimensions that aren't a root on either axis, each with a
value picker (Well's values come from the plate selection). It doubles as where
those dimensions rest — drag one up onto an axis to make it vary, and the moment
a root leaves an axis it reappears in Fixed. A dimension is therefore always in
exactly one place: a row root, a column root, or Fixed.

**Composite is an Overlay switch on a root's channels, not a separate zone.**
The channels nested under a root carry a **Separate ↔ Overlay** switch. *Separate*
(default) gives one row/column per channel. *Overlay* blends that root's channels
into a single cell (additive LUT-coloured RGB, clipped — the existing
`image_compose.composite_*_rgb`). Because the switch lives on the channels **at
their place in the tree**, a composite is just how one root's channels render —
positioned in the shelf like everything else, not a free-floating "Merge" zone.
The headline case: nest the channels under a **Well** root and flip *Overlay* →
each well gets one row whose cell is the additive blend of its channels. Challenge
#2 dissolves.

**Key simplification:** the two trees are the single source of truth for the
table's shape. Nesting, grouping, holding, and compositing stop being four
disjoint mechanisms and become "root, nested channels, their options, and their
Separate/Overlay mode." Challenges #2 and #3 dissolve.

### 3.4 Live, single-phase

Any edit triggers one **debounced rebuild-and-render** — Build and Generate
merge. Nesting a channel under a root immediately re-lays the grid and renders
thumbnails. There is no empty selector grid: **the rendered thumbnails
are the grid.** Challenge #1 dissolves.

The heavy per-cell dropdown grid is removed from the default surface.
Per-cell overrides (the genuinely irregular table) move to **progressive
disclosure**: click a rendered cell → a small popover exposes that cell's
(Well / channel + options / Timepoint / FOV) for a one-off override, writing into
the same `cells` model the renderer already reads. Challenge #5 dissolves for the
95% case while keeping the escape hatch.

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
- **Well selection** — the plate map is the existing sidebar
  `WellPlateSelector`; the Well dimension reads `active_wells` straight from it,
  so nothing about how wells are picked changes.
- **Persistence** — `persistence/view_state.py` already round-trips
  `row_dims` / `col_dims` / `group_size` / `group_row_span` / `active_*`. The two
  trees serialise to the same fields (each axis = a root dim + its nested channel
  list); the per-channel options block generalises the per-row LUT/display
  snapshot that is already persisted, so saved states keep loading.
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
- Introduce a Qt-free `TableSpec` in `state.py`. Each axis is a small tree:
  `Axis = {root: dim|None, channels: [ChannelSpec...], overlay: bool}` with
  `dim ∈ {well, timepoint, fov}`; the whole spec is `{rows: Axis, cols: Axis,
  fixed: {dim: value}, wrap: int, filters: {dim: set}}` — where the *Fixed set is
  derived* (every dim not a root on either axis) and `fixed[dim]` just remembers
  its pinned value. A
  `ChannelSpec = (channel, render ∈ {raw, corrected, mask, binary}, boundary:
  bool, colormap, lut)`. It is a *view* over the existing fields — `root` +
  `channels` flatten to `row_dims` / `col_dims`, `overlay` → `composite`, `wrap`
  → `group_size`, filters → `active_*`, `well`'s values → `active_wells`. Add a
  `to_spec()` / `apply_spec()` pair so old and new code read/write the same
  state. A `ChannelSpec` maps straight onto the existing `_row_display_opts` /
  `_render_mask_binary` / `_render_threshold_binary` /
  `_render_with_boundary_overlay` / `_row_cmap_name` calls — the *same* renderers,
  selected by the channel's options instead of by a per-row toggle. Default: Rows
  root = the first faceting dim with channels nested (or no root, channels only),
  reproducing today's look.

### Phase 1 — the shelf UI (behind the existing grid)
- New `builder_view.py`: two axis trees (Rows / Columns), a derived **Fixed**
  panel, and a *Channels* tray, using Qt drag-and-drop (`QDrag` / `dropEvent`).
  Reuse the value-filter logic already proven in `tab_view._make_filter_picker`.
  The **Fixed** panel is rebuilt from state each refresh (the dims not a root on
  either axis, each with a value combo); its chips drag onto an axis to become a
  root. The **Well** root binds to the existing sidebar `WellPlateSelector` (reads
  `active_wells`, clicking focuses the plate) — no new well control. Dropping a
  root sets `axis.root`; dropping a channel appends a `ChannelSpec` nested under
  it; each channel chip has a **⚙** popover (render / boundary / LUT) and an **×**;
  a root's **×** returns it to Fixed.
- The channels under a root carry a **Separate ↔ Overlay** toggle
  (writes `axis.overlay`); any edit writes the `TableSpec`, calls `apply_spec()`,
  then the existing `image_table_build_layout` + a debounced
  `image_table_generate`.
- Ship it *above* the current selector grid first, so both drive the same state
  and can be compared side by side.

### Phase 2 — live render, collapse the selector grid
- Merge Build + Generate into one debounced `rebuild()` on any shelf change
  (drop, root/channel change, option edit, fixed-value, filter, wrap, overlay).
- Hide the always-on per-cell dropdown grid; add click-to-edit cell popovers for
  overrides (write into `cells`). Keep a "Show selector grid" escape toggle for
  one release for irregular-table users.

### Phase 3 — per-channel options as the styling home
- Make each channel chip's **⚙** the primary place display is configured; the
  render path reads the channel's `ChannelSpec` as primary and the per-row levels
  table becomes legacy-only. A cell's render (mask / outline / ≥thr / raw /
  corrected) and LUT come from its channel spec, position-independent.

### Phase 4 — polish & retire
- Fold Wrap and Overlay fully into the shelf (retire the Group spinner and the
  standalone Composite toggle button). Retire the legacy selector grid + per-row
  LUT table once the popover path has soaked. Update `test_monolith_budgets.py`
  ratchets and `ARCHITECTURE.md` §5.5.

### Testing
- The Qt-free `TableSpec` ↔ state round-trip (root + nested `ChannelSpec` list)
  is pure-Python and unit-testable with no Qt (matches the `tests/` no-Qt
  scaffold).
- A `test_feature_removal`-style boot+load test still passes (no new core
  imports).
- `view_state` round-trip test extended for the per-axis root + channel specs.

---

## 6. Before / after

| | **Today** | **Proposed** |
|---|---|---|
| Mental model | fixed grid of 4-dropdown cells | a root per axis, channels nested under it |
| Vary >2 dims | nesting **and** grouping **and** hold (3 UIs) | root + nested channels in one tree |
| See the table's shape | read 5+ scattered controls | read the two axis trees |
| Configure → see result | Build, then Generate (2 phases) | live (1 phase) |
| Per-cell widgets | `rows × cols × 4` combos, always shown | rendered thumbnails; edit on click |
| Delete an element | no direct affordance | an **×** on every root / channel / held value |
| Well selection | plate map, but Well can't easily be an axis | plate map feeds a Well root you can nest or fix |
| Off-axis dims | manual "Hold off-axis" combos, easy to forget | **Fixed** auto-lists every dim you didn't lay out, with a value picker |
| Composite | a separate row with no place in the hierarchy | a Separate↔Overlay switch on a root's channels |
| Channel variants (raw/corrected, mask, outline, ≥thr) | per-row toggles, only right when rows=channels | a **⚙** on each nested channel, with its own LUT |

The rendered grid, the compose/LUT/export layers, and persistence are reused;
the change is concentrated in the tab's control surface and in *who writes* the
already-existing layout state.
