# Distribution window — plan and tracker

This is a tracker in the sense of CLAUDE.md §6.1, for one body of work: letting a
user **draw a window on the Nanowells page's Distribution view**, see the
nanowells inside it in the **pop-out window** — as rows of crops over time, or
as a **hex composite** that packs them into an artificial array — and use the
window as a **Subset** condition. It is written to be handed to an assistant
session that has not seen the conversation that produced it, so it states the
decisions, the reasons for them, the code each part touches, and how each part
is proved.

The pull request that closes the last row deletes this file and says so in its
body. Counts are derived from the rows in §11; no total is typed anywhere.

Read before starting: `CLAUDE.md` (all of it — §2 on test lanes and the
nanowell-features lane, §4 on the tested contracts, §6 on PR cadence and the
`minor` label, §7 on degraded output, §7.5–§7.8 on proving tests), and the
Nanowells page sections of `_Docs/Markdowns/ARCHITECTURE.md` (search
"Distribution", "Extremes", "pop-out", "Subset", "Array") and
`_Docs/Markdowns/README_WellViewer.md` (the Nanowells page, roughly lines
6000–6750). Line numbers below were read on the tree this plan was written
against (`main` at "Version 10.10.11 (#1233)"); the tree moves, so re-find each
by its symbol before editing. All paths not otherwise qualified are relative to
`review/features/nanowell/`.

---

## 1. Why

The Distribution view draws one dot per nanowell, but not every nanowell is a dot
anyone can click:

- a replicate set of more than four wells is drawn as one density per well
  (`renderer.DENSITY_ABOVE = 4`, `renderer._draw_density`), with no dots at all;
- a tie too large for its slot overflows and is only counted, "+N at 0"
  (`beeswarm_compute.SwarmLayout.overflow`);
- the click map (`renderer.HitMap`) holds only drawn dots, so a click can never
  reach either of the above.

The user wants to select nanowells **by where their values fall**, not by which
dot they can hit, and then look at the selected nanowells' images — individually
over time, and all at once as one artificial array.

## 2. Decisions already made with the user

These are settled. Do not reopen them; if implementation shows one cannot be
met, stop and report rather than substituting something else.

1. **The gesture is a dragged rectangle on the Distribution figure.** Its y
   extent is a value range in the metric's units. Its x extent **snaps to whole
   columns** — the timepoints on *Along x: Time*, the wells on *Along x: Wells*
   — never to a dot's horizontal offset, which is packing jitter, not data.
   No lasso.
2. **A click still selects one nanowell; a drag draws the window.** A press and
   release within `controller.CLICK_SLOP_PX` (4 px) is a click.
3. **The window covers every panel by default**, because the panels share one y
   scale; a "This panel only" option restricts it to the panel it was drawn in.
4. **Time membership defaults to *every*:** on a time axis, a nanowell is in a
   window spanning several timepoints only if its value is inside the range at
   **every** covered timepoint. An **any** toggle switches to "inside at one or
   more". The window's label always states **both** counts, e.g.
   "38 at every timepoint · 112 at any".
5. **Membership reads the values as drawn** — Baseline, Fixed cohort, Moving
   average, Rim focus and the current Subset already applied — and is computed
   from the model's values, so nanowells in densities and in overflow are
   members exactly like drawn dots. A nanowell with no value is never a member.
6. **The results open in the existing pop-out window**, which gains a source
   switch, **This nanowell | Window (N)**. In Window mode a layout selector
   offers **Rows | Hex**:
   - **Rows**: one row per nanowell, one column per timepoint — the Extremes
     montage's layout.
   - **Hex**: the hex composite (decisions 8–10).
7. **Rows are ordered by value at the window's latest covered timepoint**,
   largest first; ties by well, then `q`, then `r`. Paged, 24 rows a page.
8. **The hex composite is called a "Composite", never a well.** Its title says
   what it is, e.g. *"Composite of 143 nanowells from 6 wells — not a real
   array; places assigned by rank"*. Tiles are drawn apart with visible gaps,
   inside a dashed frame, with no montage underneath and no central-square
   marker. It is a distinct type with no well id: it never reaches `st.well`,
   the plate selection (`_selected_wells`), CSV export, Batch Export, the Image
   Table or the Array view's anchor and re-run actions.
9. **The composite is grouped by source well**: each source well has its own
   labelled patch of the hex grid, and **within a patch tiles are placed by
   rank**, most extreme at the patch's centre and spiralling outward.
10. **One shared brightness scale** per channel across every tile of the
    composite (and of the Rows montage), so wells' exposures can be compared.
    The caption says the tiles come from N wells.
11. **Over time:** membership is fixed for the window as it stands; the composite
    steps through timepoints with tiles keeping their places. A tile with no
    image at a timepoint is a **named placeholder** stating why, never a blank,
    and the caption counts placeholders by reason.
12. **"Use as Subset" filters the Distribution itself.** The window becomes a
    named condition in the Subset (modelled on the existing `gate` condition),
    so the dots outside it disappear from every view the Subset governs.
    Windows can then be stacked — a second window drawn on the filtered plot
    narrows further — and each is listed in the Subset editor, where it can be
    removed.

## 3. What the user sees

### 3.1 On the Distribution figure

- Dragging draws a translucent box in the theme accent (`ui.theme`, never a
  literal colour) on every panel it covers, its edges snapped to the columns.
- Every drawn dot inside gets a ring (reuse the selection-ring styling of
  `renderer.redraw_selection_rings`, restyled so it reads differently from the
  one selected nanowell).
- A label on the figure states every count:
  *"Window 2–4 · 38 at every timepoint (112 at any) · 12 not drawn: 8 in
  densities, 4 in overflow"*. It carries typed **lo–hi** fields (precision, and
  usable on the phone layout where dragging is clumsy), an **every | any**
  toggle on a time axis, **This panel only**, **Open in pop-out**, **Use as
  Subset**, and **✕** (Esc also clears).
- The window belongs to one metric. When the Metric changes the window is
  neither drawn nor applied, and the label says so; changing back restores it.
- When Baseline, Moving average, Cohort or Subset change after the window was
  drawn, membership is recomputed and the label says what changed and how the
  count moved ("drawn under baseline 0 h; now 24 h · 37 → 29 nanowells").
- The footer gains a line: nanowells with no value are never in a window.

### 3.2 In the pop-out

- First control row: **This nanowell | Window (N)**. In Window mode the selector
  **Rows | Hex** follows it. (The pop-out already has a grid-shape control,
  `state.popout_grid` / `crop_grid_compute.POPOUT_GRIDS`; that keeps its
  meaning for the one-nanowell crops. The layout selector is a separate control.)
- **Rows**: the Extremes montage layout — row labels (rank, well, address, the
  value ranked by), each crop's numbers inside it, the Cells / Range / Channels /
  Timepoints / Save controls the pop-out already has. "Rows 1–24 of 143" with
  ◀ ▶. Clicking a row selects that real nanowell, so the side panel's site detail
  follows.
- **Hex**: the composite (decisions 8–11), at the page's timepoint, with a
  timepoint stepper. Each tile has a thin tick in its source well's colour
  (`domain.well_palette`); hovering reads *"Tile 37 · A03 3,-2 · count FC 4.1
  at 48 h"*; clicking selects the real source nanowell. Each patch is labelled
  with its well. Page size: the largest hex number of tiles (1, 7, 19, 37, 61,
  91, 127) that fits the figure; beyond that it pages like Rows.
- Neither layout is ever empty without saying why: no window, a window with no
  members, members whose crops cannot be cut — each has its own sentence.

### 3.3 In the Subset editor

- **Use as Subset** adds a condition `window · inside` named `Window 1`,
  `Window 2`, … It is bound live like `gate` conditions
  (`subset_compute.bind_gates`): editing the window moves the subset; a deleted
  window gives every nanowell no value for the condition and the condition
  says so.

## 4. Facts this design rests on (verified on the tree)

- `distribution_compute.Swarm` (line ~139) holds `values` and `sites`
  (`(well, q, r)`) for **every** nanowell with a value, drawn or not — so
  overflow and density members are available to a membership computation.
- `renderer.render_distribution(fig, model, *, selected=()) -> HitMap`
  (line ~235) builds axes for `model.panels[:MAX_PANELS]` (`MAX_PANELS = 12`)
  and returns only the hit map. **Nothing records which axes drew which
  panel** — that mapping must be added.
- The figure's mouse wiring already has press, release and motion:
  `tab_view.py:455–457` connect `controller.on_click`, `on_release`,
  `on_motion`. Today the Distribution path selects on **press**
  (`controller.on_click`, line ~2126, ending in `_select`). The Array view
  already splits press/release with a slop (`_array_press`, `CLICK_SLOP_PX = 4`
  at line ~1374).
- `controller._move_selection_rings` (line ~2183) and
  `renderer.redraw_selection_rings` repaint rings without a full redraw;
  `tests/test_nanowell_selection_redraws_rings_only.py` pins that.
- `extremes_compute.ExtremeRow` (line ~46) and `renderer.render_extremes`
  (line ~1073) draw a ranked list of nanowells as rows of crops over time.
  `extremes_compute`'s docstring explains why Extremes ranks within one well;
  leave that behaviour alone.
- `controller.draw_popout` (line ~1869) draws the pop-out from
  `crops_site(st)` (line ~1833) through `renderer.render_crop_grid`.
- Crops are fetched off the UI thread through `controller._fetch`
  (line ~2072, `core.worker_bridge.run_off_thread`), using
  `image_compute.crops_for_sites` (line ~249) / `site_crops`.
- `state.NanowellState.crops` (line ~235) — `(well, q, r) -> {timepoint: Crop}`
  — is **never pruned** (only reset wholesale at line ~326). A window of hundreds
  of nanowells × timepoints would exhaust memory. `POPOUT_CROPS_KEPT = 24`
  (controller line ~1897) bounds a different store, the pop-out's extra
  channels; it is precedent for a bound.
- `image_compute.Crop` (line ~111) carries no µm-per-pixel and no centre, which
  the composite needs to place tiles from wells of different scales.
  `_tile_crop` (line ~365) is where crops are cut.
- `views_compute.lattice_xy(q, r, pitch_um, kind="hex")` (line ~34),
  `views_compute.well_pitch(data, well)` (line ~48) and
  `views_compute.hex_ring(q, r)` (line ~188) give the hex geometry.
- `subset_compute.Condition` (line ~231), `Subset` (~326), `bind_gates` (~430)
  and the `gate` condition (docstring lines ~28–33) are the precedent for a
  live-bound, named geometric condition.
- `state.VIEWS` (line ~25) lists eight views. **This plan adds none.**
- `controller.py` is ~2,650 lines, `renderer.py` ~1,980, `tab_view.py` ~1,250.
  `tests/test_monolith_budgets.py` had no entry for them when this was written,
  but the per-function complexity cap of 40 (`test_complexity_ratchet.py`)
  applies, and `on_click` / `on_release` are already dispatchers. New logic goes
  in new modules (precedent: `_qc_controller.py`, `_identity_controller.py`,
  `_identity_render.py`).
- Nanowell test files belong to `tests/_lanes.py::NANOWELL_FEATURE_FILES`
  (each with its cost), and run with `scripts/test.sh --nanowell-features`.

## 5. Design

### 5.1 The window and its membership (rows W1, W2)

New Qt-free module **`window_compute.py`** (the `*_compute.py` suffix: pure,
no Qt, no matplotlib):

```python
@dataclass(frozen=True)
class DistributionWindow:
    column: str            # the metric the window was drawn on
    axis: str              # "time" or "wells" (the model's axis)
    labels: tuple          # the covered columns: timepoint labels or wells
    y_low: float
    y_high: float          # data units; a log axis converts through transData
    panel: str | None      # a panel's title for "This panel only", else None (every panel)
    rule: str              # "every" or "any"
    drawn_under: dict      # baseline, moving, cohort, subset config, rim focus — for the "changed since" sentence

@dataclass
class WindowResult:
    sites: list            # (well, q, r), ranked per decision 7
    rank_value: dict       # site -> the value it was ranked by
    rank_label: str        # the timepoint (or well) ranked at
    n_every: int
    n_any: int
    not_drawn: dict        # "densities" / "overflow" -> count of members not drawn as dots
    changed: str           # "" or the sentence of §3.1
```

- `members(model, window, *, hidden=None) -> WindowResult` walks
  `model.panels` (filtered to `window.panel` when set), keeps swarms whose
  `label ∈ window.labels`, and tests `values` against `[y_low, y_high]`
  (inclusive). For `rule == "every"` a site is a member only when it has a
  value inside the range at **every** covered timepoint: a site with no value
  at one covered timepoint is not a member under `every` (it may be under
  `any`). Write that case as a test (§9).
- `not_drawn` comes from the renderer's per-swarm layout: pass the overflowed
  sites and the density panels in through `hidden`, which the renderer must
  now report (row W3). Membership itself never depends on it.
- `snap(model, panel, x0, x1) -> tuple` turns an x span into the covered
  labels: a column is covered when its slot centre (`Swarm.x`) lies in the
  span. A span covering no centre covers nothing, and the label says so.
- `changed_sentence(window, current) -> str` compares `drawn_under` with the
  current settings.
- Membership is **derived, never stored**; only the `DistributionWindow` is
  state.

State: `NanowellState.window: DistributionWindow | None` (as a plain dict in
`to_config` / `apply_config`, `state.py` lines ~340 / ~356, validated like the
other blocks there — a malformed block restores as no window, never raises).
Keep windows keyed by metric (`{column: DistributionWindow}`) so changing the
Metric away and back restores it (§3.1).

### 5.2 Drawing the window (rows W3, W4)

Hand-rolled on the existing press / motion / release wiring, **not**
matplotlib's `RectangleSelector`: the figure is cleared on every redraw (which
destroys selectors), a 12-panel figure would need 12 of them, they compete with
`on_click` for presses, and their artists would leak into saved figures and
`core.plot_style.apply_export_style_to_current`.

New module **`_window_controller.py`**; `controller.on_click` / `on_motion` /
`on_release` delegate to it when `st.view == "distribution"`, so neither
dispatcher grows.

- **Press** stores `st.press = (axes, x_px, y_px)` and selects nothing.
- **Motion** with a press held and moved beyond `CLICK_SLOP_PX` draws a
  `Rectangle` (gid `nw-window-band`) on the press's axes and `draw_idle()`s.
  No full redraw while dragging.
- **Release** within the slop is a click: exactly today's `_select` with
  `st.hits.nearest(...)`. Beyond the slop, both corners go through
  `press_axes.transData.inverted()` (use the display coordinates, so a release
  outside the axes still resolves), x snaps through `window_compute.snap`, and
  the window is stored, saved (`_save_soon`) and the figure redrawn.
- **Axes → panel**: add `panels: list[(axes, Panel)]` to `HitMap`, filled by
  `render_distribution`. Panels past `MAX_PANELS` are not drawn and cannot be
  windowed; the footer already names them.
- `render_distribution` draws the stored window's band and member rings from
  `st.window` on every full redraw, and reports per swarm which sites
  overflowed and which panels were densities (for `not_drawn`).
  `_move_selection_rings` must leave the band and window rings alone.
- The label (§3.1) is a Qt strip above the figure, built in a new
  **`window_view.py`** (`*_view.py` = Qt builder), not text on the figure:
  it holds typed fields and buttons. Typed lo–hi edits the window exactly as
  a drag does.
- Esc and ✕ clear the window for the current metric.

Moving the Distribution click from press to release changes when a click
selects. `tests/test_nanowell_selection_redraws_rings_only.py` and any test
that drives a Distribution press will need a release too — update them in the
same commit and say so.

### 5.3 The Rows montage (rows R1, R2, R3)

- Generalise `renderer.render_extremes` into
  **`render_site_rows(fig, blocks, crops, *, …)`**, where `blocks` is a list
  of `(title, rows)` and a row is a generic `SiteRow` (rename
  `extremes_compute.ExtremeRow`, keeping its fields; add `rank_label`). Extremes
  passes its two blocks (Largest / Smallest); the window passes one. Extremes'
  output must be byte-for-byte unchanged — pin that with a test before the
  refactor (row R1).
- `state.popout_source ∈ {"selected", "window"}` and
  `state.popout_layout ∈ {"rows", "hex"}`, both persisted in `to_config` /
  `apply_config`. `draw_popout` forks on `popout_source`; the window branch
  goes in a helper in `_window_controller.py`.
- Page size `WINDOW_PAGE = 24`; `state.window_page` (not persisted — reset to 0
  whenever membership changes). Caption "Rows 1–24 of 143".
- One `_fetch(ctx, "window_crops", page_key, …)` per page using
  `image_compute.crops_for_sites`, which opens each archive once. Fetch only
  the visible page.
- Row clicks keep the `{axes: site}` hit dict pattern Extremes uses, and call
  `_select`.

**Memory (row R3).** Replace the unbounded `st.crops` dict with a small LRU
(a `collections.OrderedDict` wrapper in a new Qt-free `crop_cache_compute.py`,
or inline in `state.py` if it stays tiny) holding a few hundred site entries,
which never evicts the selected site or the visible page. This also fixes a
pre-existing unbounded growth; say so in the PR.

### 5.4 The hex composite (rows H1, H2, H3, H4)

New Qt-free **`composite_compute.py`** and a renderer module
**`_composite_render.py`**.

```python
@dataclass(frozen=True)
class CompositeTile:
    slot: tuple            # (q, r) in the composite's own lattice
    x_um: float
    y_um: float
    source: tuple          # (well, q, r) — the real nanowell
    rank: int
    value: float           # the value it was ranked by

@dataclass
class Composite:
    tiles: list
    patches: list          # (well, centre_x_um, centre_y_um, label) — one per source well
    pitch_um: float        # the median well_pitch of the source wells
    n_wells: int
    title: str             # decision 8's sentence
```

- **Layout** (decision 9): group members by source well; order the wells by
  their best-ranked member; give each well a patch — a compact hex cluster of
  its members, rank 1 at the patch's centre, then `hex_ring` outward. Lay the
  patches themselves on a coarser hex arrangement, separated by one empty ring
  so patches read as separate. Pitch from `views_compute.well_pitch`, placement
  from `lattice_xy`.
- **Crops** need a scale and a centre: add `um_per_px` and `centre_um` to
  `image_compute.Crop`, filled in `_tile_crop`. Existing callers ignore them.
- **Drawing**: one `imshow` per tile, its extent centre ± half-size in µm, so no
  crop is resampled across wells of different scales. Gaps between tiles
  (tile size < pitch), a dashed outer frame, a patch label per well, a tick in
  the source well's `well_palette` colour per tile. One brightness scale per
  channel across all tiles (decision 10; reuse the Rows montage's shared scale).
- **Placeholder** (decision 11): a tile whose crop is missing at the timepoint
  draws a hatched hexagon labelled with `Crop.note`; the caption counts
  placeholders by reason.
- **Hover / click**: a hit list of `(x, y, source)`; hover writes the tile's
  provenance into the pop-out's status line, click calls `_select(source)`.
- **Timepoint**: the composite draws at the pop-out's timepoint with a stepper;
  membership and slots do not change when it steps.
- **Isolation guard** (row H4): a test that no `Composite` or `CompositeTile`
  value can reach `st.well`, `st.selected_sites` other than as a real
  `(well, q, r)`, the plate selection, CSV export or Batch Export. If an AST
  guard is added for this, add its case to `tests/test_guards_bite.py`.

### 5.5 Use as Subset (rows S1, S2)

- A new condition kind in `subset_compute`, `window`, beside `gate`: names a
  stored window; `bind_windows(subset, windows)` binds it live, as
  `bind_gates` does; `_window_values` evaluates it per nanowell with the same
  `every` / `any` rule as `window_compute.members`. **Share the predicate**
  — one function decides membership for both, so the window's count and the
  Subset's count can never disagree (§7.5 #5, half-swept is worse than unswept).
- A deleted or renamed window gives every nanowell no value for the condition,
  and the condition says so, as gates do.
- Windows drawn on an already-filtered plot are simply further conditions
  (decision 12). The Subset editor lists each by name and removes it.

## 6. Settings and data

New `NanowellState` fields, all validated in `apply_config` so a malformed or
missing block restores to the default and never raises:

| Field | Persisted | Default |
|---|---|---|
| `windows` (`{column: DistributionWindow}`) | yes | `{}` |
| `popout_source` | yes | `"selected"` |
| `popout_layout` | yes | `"rows"` |
| `window_page` | no | `0` |

Window names for the Subset (`Window 1`, …) live with the window.

## 7. Documentation (row D1)

- `ARCHITECTURE.md`: the Nanowells page sections for Distribution (the window,
  the press/release split, `HitMap.panels`), the pop-out (source and layout),
  Extremes (now one client of `render_site_rows`), Subset (the `window`
  condition), `image_compute.Crop`'s new fields, the crop cache bound, and the
  new modules in the package's file table. A running description, no
  "new in" wording (§6 of CLAUDE.md; `test_doc_version_sync.py`).
- `README_WellViewer.md`: the Distribution section (drawing a window, every/any,
  the label, Use as Subset), the pop-out section (Window mode, Rows, Hex and
  what the Composite is and is not), and the Subset section.

## 8. Order of work

Phase 1 — window and Rows (a mergeable PR on its own):
W1 → W2 → W3 → W4 → R1 → R2 → R3 → D1 (the Phase 1 half).

Phase 2 — the composite: H1 → H2 → H3 → H4 → D1 (the Phase 2 half).

Phase 3 — Subset: S1 → S2 → D1 (the Phase 3 half).

Each phase is one PR, opened at the end of the phase (CLAUDE.md §6), labelled
`minor` (a workflow users must learn). Commit and push as you go on the session
branch; no PR until the phase is done.

## 9. Verification

For every row, before it goes to `done`: the test that pins it, **seen failing**
with the fix removed (CLAUDE.md §7.7 #2 — the code is new on the branch, so
mutate HEAD rather than using `scripts/prove_guard.sh` against `main`). Write
the mutation in the row.

Tests that must exist, with the wrong implementation each one must reject
(CLAUDE.md §7.8 — "name the wrong implementation that still passes this"):

1. `tests/test_nanowell_window_members.py` (pure): a panel with five wells
   (density), a tie that overflows, a site inside at two of three covered
   timepoints, a site with no value at one covered timepoint. Assert the exact
   site lists for `every` and `any`, and `not_drawn`. **Must reject:**
   membership from `HitMap.entries` (build the fixture so the drawn dots and
   the members differ); `any` behaving as `every`; a missing value counted as
   inside.
2. `tests/test_nanowell_window_snap.py` (pure): spans that cover zero, one and
   several slot centres; a span between two centres. **Must reject:** snapping
   by dot offset.
3. **One end-to-end test through the real control** (§7.6, §7.8 #3): build the
   real Nanowells page, synthesise press → motion → release `MouseEvent`s on its
   canvas, and assert the exact site list that reaches the pop-out's Rows
   renderer (spy on `render_site_rows`), plus that a release within
   `CLICK_SLOP_PX` still selects exactly one nanowell. **Must reject:** dropping
   the slop branch (every click becomes a window); membership from the hit map.
   If the page needs a full app, it is a `slow` full-app test — run it with
   `scripts/test.sh <file> -m ""` on its own.
4. `tests/test_nanowell_site_rows.py`: Extremes renders identically before and
   after the `render_site_rows` refactor (pin before refactoring); the window's
   block is ordered per decision 7 with ties broken by well, `q`, `r` — build
   ties so insertion order and the rule differ.
5. A crop-cache test: filling past the bound evicts the oldest entry and never
   the selected site or the visible page.
6. `tests/test_nanowell_composite_layout.py` (pure): patches per source well,
   rank 1 at each patch's centre, no two tiles in one slot, a placeholder with
   its reason for a missing crop, the title sentence exactly. **Must reject:**
   ordering by insertion instead of rank (members given against rank order);
   one global spiral ignoring wells.
7. The isolation test of row H4.
8. `tests/test_nanowell_window_subset.py`: the window's count equals the
   Subset's count for the same window under `every` and `any`; a deleted window
   gives "no value" and says so. **Must reject:** a second copy of the
   predicate (mutate one copy and watch the equality fail).
9. A state round-trip test: `to_config` → `apply_config` restores windows,
   source and layout; a malformed block restores defaults.

Add each new test file to `tests/_lanes.py::NANOWELL_FEATURE_FILES` with its
measured cost. Before each phase's PR, run, and report which commit each result
belongs to:

- the guard family (CLAUDE.md §2's list), as a set;
- `ruff check .`;
- `scripts/test.sh --nanowell-features` (it selects the files the change
  reaches);
- `scripts/test.sh --ui` if the pop-out or the window strip changed layout;
- the full-app file(s) touched, a file or two per process, with `-m ""`;
- the full fast lane, since `controller.py` and `renderer.py` are widely
  imported.

## 10. Risks and unknowns

- **Press → release for Distribution clicks** changes timing for an existing
  interaction. Tests that drive presses will need releases.
- **Log-scaled metrics**: y must convert through `transData`, and the typed
  lo–hi fields must be in the metric's units, not log units.
- **Different µm/px across wells** in the composite — handled by drawing each
  tile at its own extent; verify on a dataset that mixes tile and montage crops.
- **Image IO cost** for a page of 24 rows × all timepoints, or a 91-tile
  composite: measure one page on a realistic dataset before declaring the page
  sizes final; report the time in the PR.
- **Panels past `MAX_PANELS`** cannot be windowed (they are not drawn). The
  window's label should say so when the window covers every panel and some are
  undrawn.
- **The Moving average changes values** — a window drawn with it on and then
  off changes membership; §3.1's "changed since" sentence covers it, and test 1
  should include it.

## 11. Rows

States: `todo`, `in progress`, `done` (with the test and its mutation),
`declined` (with the reason, here and at the site).

| Row | Item | Place | State | Evidence |
|---|---|---|---|---|
| W1 | `DistributionWindow`, `WindowResult`, `members` with every/any, `snap`, `changed_sentence` | `window_compute.py` | todo | |
| W2 | `windows` per metric in `NanowellState`, persisted and validated | `state.py` | todo | |
| W3 | Press/motion/release drag with slop; `HitMap.panels`; band and member rings drawn on redraw; renderer reports overflowed sites and density panels | `_window_controller.py`, `controller.py` (delegation only), `renderer.py` | todo | |
| W4 | Window strip: counts, lo–hi fields, every/any, This panel only, Open in pop-out, Use as Subset, ✕ / Esc; "changed since" sentence; metric-change behaviour | `window_view.py`, `tab_view.py` | todo | |
| R1 | Pin Extremes' rendering, then generalise `render_extremes` → `render_site_rows`, `ExtremeRow` → `SiteRow` | `renderer.py`, `extremes_compute.py` | todo | |
| R2 | Pop-out source switch and layout selector; Rows mode with ranking, paging and per-page fetch; row click selects | `popout_view.py`, `_window_controller.py`, `controller.draw_popout`, `state.py` | todo | |
| R3 | Bound `st.crops` with an LRU that keeps the selected site and visible page | `state.py` / `crop_cache_compute.py` | todo | |
| H1 | `Crop.um_per_px` and `Crop.centre_um`, filled in `_tile_crop` | `image_compute.py` | todo | |
| H2 | Composite layout: patches per source well, rank spiral within each | `composite_compute.py` | todo | |
| H3 | Composite drawing: per-tile extent, gaps, dashed frame, labels, well ticks, shared scale, placeholders, hover, click, timepoint stepper | `_composite_render.py`, `popout_view.py` | todo | |
| H4 | Isolation: no composite value reaches a well field, the plate selection, CSV or Batch Export | tests (and a guard if decidable) | todo | |
| S1 | `window` Subset condition, live-bound, sharing `members`' predicate | `subset_compute.py`, `subset_view.py` | todo | |
| S2 | Use as Subset button; stacked windows; listed and removable in the Subset editor | `window_view.py`, `subset_view.py` | todo | |
| D1 | ARCHITECTURE.md and README_WellViewer.md, per §7, for each phase | `_Docs/Markdowns/` | todo | |

Counts are whatever these rows say; read them rather than keeping a total.
