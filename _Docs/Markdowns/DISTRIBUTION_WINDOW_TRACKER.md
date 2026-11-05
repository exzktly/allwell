# Distribution window — implementation tracker

This is a tracker in the sense of CLAUDE.md §6.1 for one body of work: drawing a **window** on the Nanowells
page's Distribution view, seeing the nanowells inside it in the **pop-out** as rows of crops over time (Phase 1)
or as a **hex composite** (Phase 2), and later using the window as a Subset condition (Phase 3, not started).

**Every design decision is already made in this file.** It is written so an implementing session only types,
runs and commits. Where it gives code, the code was run against synthetic models on the tree it was written
against and is to be copied as given. Where it gives a name, a string, a constant or a file, use exactly that.
If something here does not fit the tree — a symbol has moved, a test cannot be written as described — **stop
and report** what does not fit; do not redesign. Line numbers were read on `main` at "Version 10.10.13 (#1239)"
merged into this branch; re-find each by its symbol before editing.

The pull request that closes the last row deletes this file and says so in its body. Counts come from the rows
in §9; no total is typed anywhere.

---

## 0. Before starting

1. Read `CLAUDE.md` once, in full. The parts this work leans on: §2 (lanes; `scripts/test.sh <files>`, never
   bare `pytest`), §4 (contracts), §5 (no silent `except`, no hard-coded colours, typed state), §6 (commit and
   push as you go, open the PR last, label `minor`, do not touch `version.py`), §7.6–§7.8 (test through the real
   control; build fixtures where the right and wrong answers differ; prove each test fails without its fix).
2. `git fetch origin main && git log --oneline origin/main -- review/features/nanowell` — if anything landed
   since `b8371e3`, merge `origin/main` into the branch first and re-check the symbols this file names.
3. Rules that apply to every step:
   - **No function-local `from review.features …` import** — `tests/test_deferred_imports.py` budgets them and
     the budget only goes down. New modules import at module scope.
   - `_window_controller.py` reaches the controller exactly as `_qc_controller.py` does: a module-level
     `from review.features.nanowell import controller as _controller` and a `def _nwc(): return _controller`
     accessor; `controller.py` imports `from review.features.nanowell import _window_controller as wnc` at module
     scope beside its `_qc_controller as qcc` import.
   - Colours come from `ui.theme.Colors` (`Colors.accent` for the window) or the plot tokens (`renderer._inks`);
     no literals.
   - Every new test file is added to `tests/_lanes.py::NANOWELL_FEATURE_FILES` with a one-line description
     (`tests/test_the_fast_lane_stays_fast.py::test_a_nanowell_test_file_joins_a_nanowell_roster` fails
     otherwise).
   - Each row is updated (state → `done`, evidence filled in) **in the same commit as its code**.
   - Commit message trailer: the attribution lines the session's system reminder gives.

## 1. Branches and pull requests

| | Branch | Base | PR |
|---|---|---|---|
| Phase 1 | `claude/distribution-window-plan` (this branch) | `main` | PR 1, opened when every Phase 1 row is `done` |
| Phase 2 | `claude/distribution-window-composite`, created from the tip of Phase 1 after PR 1 is opened | `claude/distribution-window-plan` | PR 2, stacked on PR 1 |

- Push each commit as it is made (`git push -u origin <branch>`). Open PR 1 only after §5's checks pass.
- After opening each PR, call `subscribe_pr_activity` for it.
- PR 2's body must contain, verbatim: *"This targets `claude/distribution-window-plan` rather than `main`, so its
  diff is Phase 2 alone. Once PR #<PR 1's number> merges, retarget this PR to `main` before merging it."*
- **Retargeting (required).** When PR 1's merge event arrives: confirm the work reached `main` with
  `git fetch origin main && git cat-file -e origin/main:review/features/nanowell/window_compute.py`; then change
  PR 2's base to `main` (`mcp__github__update_pull_request`, `base: "main"`); then `git fetch origin main`, merge
  `origin/main` into `claude/distribution-window-composite`, run §7's checks and push. Do not merge PR 2 into the
  Phase 1 branch at any point.

## 2. Decisions (final)

1. **Gesture:** drag a rectangle on a Distribution panel. A press and release within `controller.CLICK_SLOP_PX`
   (4 px, Manhattan distance, as the Array view measures it) is a click and selects one nanowell exactly as
   today; anything longer draws a window. There is no lasso and no keyboard shortcut (Esc is **not** bound —
   the strip's ✕ clears).
2. **Columns:** the x extent snaps to the columns it overlaps. Column `i` spans `(i − 0.5, i + 0.5)` on the
   panel's x axis; it is covered when the drag's x span overlaps that interval. A column's **key** is the
   timepoint (`Swarm.label`) along time and the group (`Swarm.group`) along wells. Keys, not x positions, are
   stored, and they are computed on the panel the drag started in.
3. **Range:** y extent is `[lo, hi]` in the metric's units, both inclusive. The y axis is linear on every
   Distribution figure (no metric uses a log axis), so data coordinates are the values.
4. **Panels:** a window covers every panel by default; *This panel only* restricts it to the panel it was drawn
   in (matched by `Panel.title`). If that panel is no longer on the figure the strip says so.
5. **Every / any:** default *every*. Along time, a nanowell is a member under *every* when it has a value inside
   `[lo, hi]` at each covered column its panel has; a nanowell with no value at one of them is not a member.
   Under *any*, one value inside is enough. Along wells each nanowell has one value and the two agree; the
   every/any control is hidden there and when the window covers a single column. Both counts are always shown.
6. **Values as drawn:** membership reads the `DistributionModel` the figure was drawn from (`st.window_model`),
   so Baseline, Fixed cohort, Moving average, Rim focus and the current Subset are already applied. Nanowells
   with no value are never members.
7. **One window per metric and axis**, kept under `window_key(column, axis)` = `f"{column}|{axis}"`. Changing the
   metric or axis hides the window (its strip and band); changing back shows it again.
8. **Changed since drawn:** the window stores `settings_key(st)` and its member count when drawn; when the
   current settings differ, the strip adds *"Baseline, moving average changed since the window was drawn: 37 →
   29 nanowells."* (the names of what changed, then the counts).
9. **Not drawn:** the strip counts members with **no dot inside the window's columns** on the figure, split
   into *in panels not drawn* (past `renderer.MAX_PANELS`), *in densities* (a panel drawn as densities) and *in
   overflow* (a "+N" a slot could not hold).
10. **On the figure:** a band per covered panel from `min(covered) − 0.5` to `max(covered) + 0.5` and `lo` to
    `hi`; a ring on every drawn dot of a member that lies in a covered column with its own value inside the
    range. The band and rings are part of the figure and appear in saved figures.
11. **The strip** (a Qt row between the band and the figure) carries the sentence, typed **Low** and **High**
    fields, the **every / any** combo, **This panel only**, **Open in pop-out** and **✕**. It is shown only on
    the Distribution view while a window exists for the current metric and axis.
12. **Pop-out:** a new row of controls: **Show: This nanowell | Window (N)**, **Layout: Rows | Hex** (Hex is added
    in Phase 2; in Phase 1 the combo holds only Rows), and **◀ Page k of n ▶**. *Open in pop-out* sets Show to
    Window and opens the pop-out. Clicking a crop selects its real nanowell.
13. **Rows:** `renderer.render_site_rows` with one block titled `Rows a–b of n`, 24 rows a page
    (`WINDOW_PAGE = 24`), ranked by the value at the window's latest covered column (largest first, no value
    last, then well, `q`, `r`); each row's label names its well (`with_well=True`). The page index resets to 0
    whenever the window changes.
14. **Crop cache:** `st.crops` becomes a bounded cache of 400 nanowells (`CROPS_KEPT = 400`) that evicts the
    oldest-inserted first and never a protected one (the selected nanowell and the pop-out's visible page).
15. **Composite (Phase 2):** one patch per source well; within a patch, tiles by rank on a hex spiral from the
    centre; patches left to right in rows of `ceil(sqrt(n_wells))`, one empty ring between them; at most 91 tiles
    a page (`COMPOSITE_PAGE = 91`); one brightness scale across every tile (`renderer.crop_scale`, honouring the
    Display range); tiles drawn as squares of side 0.92 pitch units, so gaps show; a dashed frame round the whole;
    each patch labelled with its well above it and each tile marked along its foot in its patch's colour
    (`ui.theme.WELL_COLORS_TUPLE[patch % 96]`); a tile with no crop at the timepoint is a hatched hexagon, and the
    caption counts those by reason. A ◀ timepoint ▶ stepper chooses the timepoint (default: the window's
    ranking column; for a per-nanowell metric, the first timepoint). Hover names the tile's real nanowell in the
    status bar; a click selects it. The title says *"… — not a real array; places assigned by rank within each
    well"*. The composite carries no well id and never touches `st.well`, the plate selection, CSV or Batch
    Export.
16. **Declined, with reasons:**
    - *Crop µm-per-pixel and centre fields* (old row H1): not needed. Each crop is cut at 1.4 × its own
      nanowell's radius, so equal tile sizes show every nanowell at the same nanowell-relative scale; the
      composite caption says so (*"each tile is its nanowell's crop at one size"*).
    - *A live-bound window Subset condition* (old §5.5): circular, since the Subset filters the very figure the
      window is drawn on. Phase 3 freezes the members instead (§8).
    - *Renaming `ExtremeRow` to `SiteRow`*: `render_site_rows` reads only `site`, `rank`, `value` and `values`,
      so Extremes keeps `ExtremeRow` and the window has its own `window_compute.WindowRow` with those fields
      (its own class only to keep `state` → `window_compute` free of a cycle through `extremes_compute`).

## 3. Phase 1 — window and Rows (PR 1)

Do the steps in order; each ends in one commit.

### Step W1 — `window_compute.py` (pure)

Create `review/features/nanowell/window_compute.py` with **exactly** the code in Appendix A.

Create `tests/test_nanowell_window_members.py` (roster description: *"the Distribution window's members, every and
any, snapping and its sentences, from hand-built models"*). Build models from
`distribution_compute.Swarm` / `Panel` / `DistributionModel` with this helper:

```python
def _swarm(label, well, x, values, group=None, start=0):
    sites = [(well, start + i, 0) for i in range(len(values))]
    return Swarm(label, well, x, np.array(values, float), sites, [None] * len(values), len(values), {},
                 group or well)
```

The time fixture (`_time_model()`): panel `"A01"` with swarms `("T0", x=0.0, [1, 2.5, 3])`,
`("T1", x=1.0, [1, 3.1])`, `("T2", x=2.0, [1, 1.2, 3.5])`, ticks `[(0.0, "0"), (1.0, "1"), (2.0, "2")]`; panel
`"B01"` with one swarm `("T0", "B01", x=0.0, [3])`; `DistributionModel("count_fc", "count_fc", "Count fold
change", "time", False, [a01, b01])`.

Tests and the exact expectations (all verified on that fixture):

| Test | Call | Expect |
|---|---|---|
| `test_snap_takes_every_column_the_span_overlaps` | `snap(m, 0, -0.2, 2.2)`, `snap(m, 0, 0.6, 0.7)`, `snap(m, 0, 0.4, 0.6)`, `snap(m, 0, 2.6, 3.0)` | `("T0","T1","T2")`, `("T1",)`, `("T0","T1")`, `()` |
| `test_every_needs_a_value_inside_at_each_covered_timepoint` | `members(m, W(keys=("T0","T1","T2"), lo=2, hi=4, rule="every"))` | `sites() == [("B01",0,0)]`, `n_every == 1`, `n_any == 3` — `(A01,1,0)` is out at T2 (1.2), `(A01,2,0)` has no value at T1; B01's panel has only T0, so T0 is all it needs |
| `test_any_needs_one_value_inside_and_ranks_by_the_latest_column` | same window, `rule="any"` | `sites() == [("A01",2,0), ("A01",1,0), ("B01",0,0)]` (3.5 and 1.2 at T2; B01 has no T2 value, so last), `rank_key == "T2"`, `rows[0].value == 3.5`, `rows[1].value == 1.2`, `math.isnan(rows[2].value)` |
| `test_ties_rank_by_well_then_address` | `W(keys=("T0",), lo=2, hi=4, panel="A01")` | `sites() == [("A01",2,0), ("B01",0,0), ("A01",1,0)]` (3.0, 3.0, 2.5) |
| `test_this_panel_only_keeps_the_panel_it_was_drawn_in` | as above with `panel_only=True` | `[("A01",2,0), ("A01",1,0)]`; with `panel="Z9", panel_only=True`: `missing_panel is True`, no rows |
| `test_a_row_carries_its_value_at_every_timepoint` | the *any* result | `rows[1].values == {"T0": 2.5, "T1": 3.1, "T2": 1.2}` |
| `test_a_window_round_trips_and_a_bad_block_is_dropped` | `from_config(w.to_config())`; `from_config({"lo": 1})`; `lo > hi`; `axis="x"`; empty keys | equal; `None` ×4 |
| `test_the_sentence_states_both_counts_and_what_was_not_drawn` | `label_text(w3, any_result, {"in panels not drawn": 0, "in densities": 1, "in overflow": 0}, "time")` with `w3` the three-column *any* window | `"Window 2–4 · 3 inside at any covered timepoint · 1 at every · 1 with no dot on the figure (1 in densities)"` |
| `test_the_changed_sentence_names_what_changed` | `changed_sentence(replace(w, drawn_under=(("baseline","earliest"),("moving average","0")), n_at_drawing=37), (("baseline","T1"),("moving average","3")), 29)` | `"Baseline, moving average changed since the window was drawn: 37 → 29 nanowells."`; unchanged settings → `""` |
| `test_membership_reads_the_model_not_the_drawing` | a wells-axis model `DistributionModel("n", "n", "Cell count", "wells", True, [g], timepoint="T1")`: one panel `"G"`, ticks `[(0.0, "G")]`, five swarms for wells `W1`–`W5` at x = `(j - 3) * 0.16` for j = 1…5, each `label` = its well, `group="G"`, values `[3.0]`; window `DistributionWindow("n", "wells", ("G",), 2, 4)`; `hits = renderer.HitMap()` with **no** entries and `hits.panels = [(object(), 0)]` | `len(members(...).rows) == 5`, `rank_key == "T1"`; `not_drawn(result, model, hits, max_panels=12, density_above=4) == {"in panels not drawn": 0, "in densities": 5, "in overflow": 0}` |
| `test_the_density_rule_is_the_renderer_s` | for the time fixture and the five-well fixture | `window_compute._members_per_column(m, p) == renderer._members_per_tick(m, p)` for every panel |

**Mutations to run before committing** (each must turn at least one test red with an `AssertionError`): make
`every` use `hit & required` instead of `hit >= required`; change `lo <= value <= hi` to `lo < value < hi`;
in `snap` use `lo <= i <= hi` (centre inside) instead of the overlap test. Record them in row W1.

Commit: *"Nanowells: the Distribution window's members, computed from the model"*.

### Step W2 — state

In `review/features/nanowell/state.py`, `NanowellState`:

- persisted fields, after `popout_cols`:
  ```python
  #: The Distribution windows, ``window_compute.window_key(column, axis) -> DistributionWindow`` (one per metric
  #: and axis; the strip and the pop-out read the one for what is drawn).
  windows: dict = field(default_factory=dict)
  #: What the pop-out shows: the selected nanowell, or the nanowells inside the window.
  popout_source: str = "selected"
  #: How the pop-out lays out the window's nanowells: rows of crops over time, or the hex composite.
  popout_layout: str = "rows"
  ```
- not persisted, beside `extreme_axes`:
  ```python
  window_page: int = 0                    # the pop-out's page of the window's nanowells; 0 when the window changes
  window_press: object = None             # (axes, x px, y px) of a press on the Distribution figure
  window_model: object = None             # the DistributionModel last drawn, which windows are read against
  popout_hits: dict = field(default_factory=dict)   # the pop-out's crop axes -> (well, q, r), for a click
  ```
- module constants beside `EXTREMES_DEFAULT_N`: `POPOUT_SOURCES = ("selected", "window")`,
  `POPOUT_LAYOUTS = ("rows",)` (Phase 2 makes it `("rows", "hex")`).
- `to_config`: add `"windows": {k: w.to_config() for k, w in self.windows.items()}`, `"popout_source"`,
  `"popout_layout"`.
- `apply_config`: `self.windows = {k: w for k, b in (block.items() if isinstance(block, dict) else ()) if
  isinstance(k, str) and (w := DistributionWindow.from_config(b)) is not None}` with `block =
  config.get("windows")`; `popout_source` / `popout_layout` accepted only if in their tuples, else the default.
- `reset_data`: `self.window_page, self.window_press, self.window_model, self.popout_hits = 0, None, None, {}`.
  (Windows survive a reload; they are settings.)
- Import at module scope, directly after the `subset_compute` import:
  `from review.features.nanowell.window_compute import DistributionWindow` and
  `from review.features.nanowell.crop_cache_compute import CropCache` (the latter once R3 exists). Verified: no
  cycle — `window_compute` imports only `distribution_compute`, and its rows are its own `WindowRow` rather than
  `extremes_compute.ExtremeRow`, because `extremes_compute` imports `state`. Importing `state`, `extremes_compute`,
  `window_compute`, `controller`, `renderer` and `tab_view` each in a fresh process succeeded with this import.

Test: add to `tests/test_nanowell_window_members.py`
`test_the_page_keeps_its_windows_and_pop_out_choices`: a `NanowellState` with one window under its key,
`popout_source="window"`; `apply_config(to_config())` on a fresh state restores both; a config with
`"windows": {"k": {"lo": 1}}` and `"popout_source": "nonsense"` restores `{}` and `"selected"`.

Commit: *"Nanowells: keep the Distribution windows with the page's settings"*.

### Step W3 — drawing and the drag

**Renderer** (`renderer.py`):

1. `HitMap` gains `panels: list = field(default_factory=list)   # (axes, index into model.panels)` and
   `values: list = field(default_factory=list)   # per entry, the drawn dots' values`.
2. In `render_distribution`'s panel loop, before `_draw_panel`: `hits.panels.append((ax, i))`.
3. In `_draw_swarm`, after `hits.entries.append(...)`: `hits.values.append(swarm.values[idx])`.
4. New module `review/features/nanowell/_window_render.py`:

```python
"""The Distribution window on the figure: its band on each panel it covers, and a ring on each member's dot inside
it (``window_compute``). Drawn after ``renderer.render_distribution``, from the hit map it returned."""

from __future__ import annotations

from matplotlib.patches import Rectangle

from review.features.nanowell.renderer import DOT_PT

BAND_GID, RING_GID, DRAG_GID = "nw-window-band", "nw-window-ring", "nw-window-drag"


def draw_window(hits, window, result) -> None:
    """Draw *window* (``DistributionWindow``) with its *result* (``WindowResult``) on the axes *hits* records."""
    from ui.theme import Colors

    members = set(result.sites())
    by_panel = dict(hits.panels)
    for ax, index in hits.panels:
        cols = result.covered.get(index)
        if not cols:
            continue
        ax.add_patch(Rectangle((min(cols) - 0.5, window.lo), max(cols) - min(cols) + 1.0, window.hi - window.lo,
                               facecolor=Colors.accent, alpha=0.12, edgecolor=Colors.accent, linewidth=1.0,
                               zorder=1.5, gid=BAND_GID))
    for (ax, xs, ys, sites), values in zip(hits.entries, hits.values):
        cols = result.covered.get(by_panel.get(ax), set())
        keep = [k for k, (x, s, v) in enumerate(zip(xs, sites, values))
                if tuple(s) in members and round(float(x)) in cols and window.lo <= v <= window.hi]
        if keep:
            ax.scatter([xs[k] for k in keep], [ys[k] for k in keep], s=(DOT_PT * 2.0) ** 2, facecolors="none",
                       edgecolors=Colors.accent, linewidths=1.0, zorder=4).set_gid(RING_GID)


def draw_drag(ax, x0, y0, x1, y1):
    """The rectangle a drag is drawing, in data units; returned so the release can remove it."""
    from ui.theme import Colors

    return ax.add_patch(Rectangle((min(x0, x1), min(y0, y1)), abs(x1 - x0), abs(y1 - y0), fill=False,
                                  edgecolor=Colors.accent, linewidth=1.0, linestyle="--", zorder=6, gid=DRAG_GID))
```

(`ui.theme` is not a feature module, so its function-local import does not count against the deferred-import
budget; the renderer already imports `Colors` the same way. `dict(hits.panels)` keys axes objects, which hash by
identity.)

**Controller** — new module `review/features/nanowell/_window_controller.py` with these functions (docstrings in
the house style; every name below is final):

```python
def current(st):
    """(window, result) for what is drawn, or (None, None)."""
    model = st.window_model
    if model is None:
        return None, None
    window = st.windows.get(wc.window_key(model.column, model.axis))
    return (window, wc.members(model, window)) if window is not None else (None, None)

def after_draw(ctx, st, model) -> None:
    """Called by controller.redraw after the Distribution view is drawn: keep the model, draw the window."""
    st.window_model = model
    window, result = current(st)
    if window is not None and st.hits is not None:
        draw_window(st.hits, window, result)
    _nwc()._call(ctx, "_nw_window_refresh")

def on_press(ctx, st, event) -> None:
    if event.inaxes is None or event.button != 1:
        return
    st.window_press = (event.inaxes, float(event.x), float(event.y))

def on_motion(ctx, st, event) -> None:
    press = st.window_press
    if press is None or event.x is None:
        return
    ax, x, y = press
    if abs(event.x - x) + abs(event.y - y) <= _nwc().CLICK_SLOP_PX:
        return
    for patch in [p for p in ax.patches if p.get_gid() == DRAG_GID]:
        patch.remove()
    inverse = ax.transData.inverted()
    (dx0, dy0), (dx1, dy1) = inverse.transform((x, y)), inverse.transform((event.x, event.y))
    draw_drag(ax, dx0, dy0, dx1, dy1)
    canvas = ctx.widgets.get_or_none("_nw_canvas")
    if canvas is not None:
        canvas.draw_idle()

def on_release(ctx, st, event) -> None:
    press, st.window_press = st.window_press, None
    if press is None:
        return
    ax, x, y = press
    for patch in [p for p in ax.patches if p.get_gid() == DRAG_GID]:
        patch.remove()
    ex, ey = (x, y) if event.x is None else (float(event.x), float(event.y))
    nwc = _nwc()
    if abs(ex - x) + abs(ey - y) <= nwc.CLICK_SLOP_PX:
        site = st.hits.nearest(ax, x, y, nwc.CLICK_RADIUS_PX) if st.hits is not None else None
        nwc._select(ctx, site)
        return
    model, index = st.window_model, dict(st.hits.panels).get(ax) if st.hits is not None else None
    if model is None or index is None:
        return
    inverse = ax.transData.inverted()
    (x0, y0), (x1, y1) = inverse.transform((x, y)), inverse.transform((ex, ey))
    keys = wc.snap(model, index, x0, x1)
    if not keys:
        ctx.notify.set_status("The window covers no column: drag across the dots you want.")
        nwc.redraw(ctx)
        return
    draft = wc.DistributionWindow(model.column, model.axis, keys, float(min(y0, y1)), float(max(y0, y1)),
                                  panel=model.panels[index].title, drawn_under=wc.settings_key(st))
    _store(ctx, st, replace(draft, n_at_drawing=len(wc.members(model, draft).rows)))

def _store(ctx, st, window) -> None:
    """Keep *window* (or remove the current one when None), start the pop-out's pages over, save, redraw."""
    model = st.window_model
    key = wc.window_key(model.column, model.axis)
    if window is None:
        st.windows.pop(key, None)
    else:
        st.windows[key] = window
    st.window_page = 0
    nwc = _nwc()
    nwc._save_soon(ctx)
    nwc.redraw(ctx)
    nwc.draw_popout(ctx)

def clear_window(ctx) -> None                      # ✕
def set_window_range(ctx, lo: float, hi: float)    # typed Low/High: swap if lo > hi; keeps keys, rule, panel
def set_window_rule(ctx, rule: str)                # "every" | "any"; ignored otherwise
def set_window_panel_only(ctx, on: bool)
def open_window_popout(ctx)                        # popout_source = "window", window_page = 0, _call("_nw_open_popout"), draw_popout
def set_popout_source(ctx, source: str)            # through controller._set_popout (it saves, refreshes, redraws)
def set_popout_layout(ctx, layout: str)            # through _set_popout, also window_page = 0
def step_window_page(ctx, delta: int)              # clamped to the pages there are; draw_popout
def strip_text(st) -> str                          # label_text + " " + changed_sentence, or "" with no window
def draw_window_popout(ctx, st, fig) -> None       # §Step R2
def on_popout_click(ctx, event) -> None            # §Step R2
```

Each of `clear_window` / `set_window_range` / `set_window_rule` / `set_window_panel_only` reads `window, _ =
current(st)`, returns if `None`, and calls `_store(ctx, st, replace(window, …))` (or `None` to clear).
`strip_text` computes `not_drawn` with `max_panels=renderer.MAX_PANELS`, `density_above=renderer.DENSITY_ABOVE`.
Imports at the top of the module: `from dataclasses import replace`, `from review.features.nanowell import
controller as _controller`, `from review.features.nanowell import window_compute as wc`, `from
review.features.nanowell._window_render import DRAG_GID, draw_drag, draw_window`, `from
review.features.nanowell.renderer import DENSITY_ABOVE, MAX_PANELS, render_message, render_site_rows`.

**Wiring in `controller.py`** (only these lines change):

1. Module import beside `qcc`: `from review.features.nanowell import _window_controller as wnc`.
2. `on_click`: immediately **before** the comment *"A view that draws no clickable nanowells …"*:
   ```python
   if st is not None and st.view == "distribution":
       wnc.on_press(ctx, st, event)             # a click or a window, which the release tells apart
       return
   ```
   and add to the docstring: *"On the Distribution figure a press starts a click or a window, which
   :func:`on_release` tells apart."*
3. `on_motion`: after the identity branch: `if st is not None and st.view == "distribution":
   wnc.on_motion(ctx, st, event); return`.
4. `on_release`: after the identity branch: `if st is not None and st.view == "distribution":
   wnc.on_release(ctx, st, event); return`.
5. `redraw`: replace `elif model is not None:\n        st.model = model` with
   ```python
   elif model is not None:
       st.model = model
       if st.view == "distribution":
           wnc.after_draw(ctx, st, model)
   ```
   (`draw_view` returns `(None, None)` for an empty Distribution, which skips this branch: no window is drawn on
   a message.)

**Existing tests this changes:** a click on the Distribution figure now selects on *release*. `QTest.mouseClick`
sends press and release, so `tests/test_nanowell_page.py::_click_dot` keeps working; any test that calls
`nwc.on_click` directly with a fake event on the Distribution view must also call `nwc.on_release` with the same
position. Find them with `grep -rn "on_click(" tests/ | grep -v array` and update each in this commit.

**Tests** — new file `tests/test_nanowell_window_page.py` (roster: *"drawing a Distribution window on the real
page: the drag, the band and rings, a click still selecting"*), using the `page` fixture and `_loaded` from
`tests/test_nanowell_page.py` (import them as `test_nanowell_selection_redraws_rings_only.py` does). Helper:

```python
def _drag(qtbot, ctx, ax, data_from, data_to):
    """Press at *data_from*, move, release at *data_to* (data units of *ax*), as a person drags."""
    canvas = ctx.widgets.get("_nw_canvas")
    ratio = canvas.devicePixelRatioF()
    def at(xy):
        px, py = ax.transData.transform(xy)
        return QPoint(int(round(px / ratio)), int(round(canvas.height() - py / ratio)))
    QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, at(data_from))
    QTest.mouseMove(canvas, at(((data_from[0] + data_to[0]) / 2, (data_from[1] + data_to[1]) / 2)))
    QTest.mouseMove(canvas, at(data_to))
    QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, at(data_to))
```

The page fixture draws wells A01 and B01 (`COUNTS` in `test_nanowell_page.py`; metric `n`, two timepoints
`t_c`, `t_a`). Tests:

| Test | Does | Asserts |
|---|---|---|
| `test_a_drag_draws_a_window_whose_members_are_the_model_s` | `_drag` on the first panel's axes from `(-0.4, 1.5)` to `(1.4, 4.5)` | `st.windows` has one window; its `set(keys) == {"t_c", "t_a"}`, `lo` and `hi` within 0.05 of 1.5 and 4.5; for each member site read its `n` at `t_c` and at `t_a` from `st.metrics` — the set of `(well, n at t_c, n at t_a)` is `{("A01", 2, 4), ("B01", 3, 3)}` (from `COUNTS`: the nanowells with 2–4 cells at both timepoints); each panel's axes holds exactly one `nw-window-band` patch; at least one `nw-window-ring` collection exists |
| `test_a_click_still_selects_one_nanowell` | `_click_dot(qtbot, ctx, a drawn site)` | `st.selected_sites == [[*site]]` and `st.windows == {}` |
| `test_clearing_removes_the_band` | after the drag, `wnc.clear_window(ctx)` | no `nw-window-band` patch on the figure; `st.windows == {}` |

Mutations (each must fail a test above with an `AssertionError`): make `on_release` treat every release as a
click (return after `_select` unconditionally); make `after_draw` skip `draw_window`; delete
`hits.panels.append((ax, i))` from `render_distribution`. (That members come from the model, not the drawing, is
proved by W1's `test_membership_reads_the_model_not_the_drawing`; on this page every dot is drawn, so a mutation
here could not tell the two apart.)

Commit: *"Nanowells: draw a window on the Distribution figure"*.

### Step W4 — the strip

New module `review/features/nanowell/window_view.py`:

```python
def build_window_strip(ctx, parent) -> QWidget
```

A `QWidget` (`objectName` `"NanowellWindowStrip"`) with a `QHBoxLayout` holding, in order and registered under
these names:

| Widget | Registered as | Behaviour |
|---|---|---|
| `QLabel` (word-wrapped, expanding) | `_nw_window_label` | `wnc.strip_text(st)` |
| `QLabel("Low")` + `QLineEdit` | `_nw_window_lo` | `editingFinished` → parse both fields with `float()`; on `ValueError` restore the text from the window; else `wnc.set_window_range(ctx, lo, hi)` |
| `QLabel("High")` + `QLineEdit` | `_nw_window_hi` | as above |
| `QComboBox` items `"every timepoint"`, `"any timepoint"` (data `"every"`, `"any"`) | `_nw_window_rule_cb` | `wnc.set_window_rule`; visible only when the axis is time and the window has more than one key |
| `QCheckBox("This panel only")` | `_nw_window_panel_box` | `wnc.set_window_panel_only` |
| `QPushButton("Open in pop-out")`, `variant="secondary"` | `_nw_window_popout_btn` | `wnc.open_window_popout` |
| `QPushButton("✕")`, `variant="secondary"`, tooltip `"Clear the window"` | `_nw_window_clear_btn` | `wnc.clear_window` |

It registers `_nw_window_refresh`, a function that: blocks the widgets' signals; sets the strip visible iff
`st.view == "distribution"` and `wnc.current(st)[0]` is not `None`; fills the label, the fields (`f"{v:.6g}"`),
the combo and the check box from the window; restores the signals. Fields use the house `QLineEdit` styling (no
stylesheet in the module).

In `tab_view.build_nanowell_tab`, after `layout.addWidget(guide.frame)`: `layout.addWidget(build_window_strip(ctx,
plot_area))`. At the end of `refresh_controls`, after the `_nw_popout_refresh` call: call
`_nw_window_refresh` the same way (`get_or_none`, `callable`).

Tests (append to `tests/test_nanowell_window_page.py`):

| Test | Does | Asserts |
|---|---|---|
| `test_the_strip_states_the_counts` | after the drag | the strip label's text (`ctx.widgets.get("_nw_window_label").text()`) starts with `"Window "` and contains `"2 inside at every covered timepoint · 5 at any"` |
| `test_the_clear_button_clears` | after the drag, click `_nw_window_clear_btn` (`QTest.mouseClick`) | no `nw-window-band` patch; the strip hidden; `st.windows == {}` |
| `test_typed_bounds_move_the_window` | after the drag, set `_nw_window_lo` to `"2"` and `_nw_window_hi` to `"3"`, emit `editingFinished` | the window's `(lo, hi) == (2.0, 3.0)`; the members' `(well, n at t_c, n at t_a)` set is `{("B01", 3, 3)}` |
| `test_typed_bounds_the_wrong_way_round_are_swapped` | `"3"` and `"2"` | `(2.0, 3.0)` |
| `test_text_that_is_not_a_number_is_put_back` | `"abc"` | the window unchanged, the field shows the old low |
| `test_any_widens_the_window` | after the drag, select `"any timepoint"` | `rule == "any"`; the members' `(well, n at t_c, n at t_a)` set is `{("A01", 1, 2), ("A01", 2, 4), ("A01", 0, 3), ("B01", 3, 3), ("B01", 1, 2)}` |
| `test_another_metric_hides_the_window_and_the_first_brings_it_back` | `nwc.set_metric(ctx, "c")` then back to `"n"` (use whichever setter the metric combo calls — `grep -n "def set_metric" controller.py`) | strip hidden and no band; then visible with the band |

Commit: *"Nanowells: the Distribution window's strip"*.

### Step R2 — the pop-out shows the window as rows

**Constants** in `_window_controller.py`: `WINDOW_PAGE = 24`.

**`draw_window_popout(ctx, st, fig)`:**

```python
window, result = current(st)
if window is None:
    st.popout_hits = {}
    render_message(fig, "Draw a window on the Distribution view to see its nanowells here.",
                   gid="nw-popout-window-empty")
    return
rows = result.rows
if not rows:
    st.popout_hits = {}
    render_message(fig, strip_text(st) + " No nanowell is inside it.", gid="nw-popout-window-empty")
    return
pages = max(1, -(-len(rows) // WINDOW_PAGE))
st.window_page = min(max(st.window_page, 0), pages - 1)
first = st.window_page * WINDOW_PAGE
shown = rows[first:first + WINDOW_PAGE]
nwc = _nwc()
sites = [row.site for row in shown]
st.crops.protected = set(sites) | {tuple(s) for s in st.selected_sites}
missing = tuple(sorted(s for s in sites if s not in st.crops))
image_channel = nwc.page_image_channel(st)
if missing:
    data, metrics = st.data, st.metrics
    nwc._fetch(ctx, "window_crops", missing,
               lambda: nwc.crops_for_sites(data, metrics, missing, channel=image_channel),
               failed=lambda message: {s: {"": nwc.Crop(None, "", f"The crops could not be cut: {message}")}
                                       for s in missing},
               deliver=lambda found: st.crops.update(found))
metric, channel, _note = nwc.resolve_metric("distribution", st.metric, nwc.page_channel_name(ctx), st.data.channels)
label = nwc._label(metric, channel, st.data)
timepoints = [str(tp) for tp in st.data.timepoints()]
model = wc.WindowRows(result.rank_key, st.window_model.column, timepoints)
st.popout_hits = render_site_rows(
    fig, model, [(f"Rows {first + 1}–{first + len(shown)} of {len(rows):,}", shown)],
    {s: nwc.gated_crops(st, s, st.crops[s]) for s in sites if s in st.crops},
    label=label, empty=strip_text(st), title=f"Distribution window · {label}", head=strip_text(st),
    cells=st.crop_cells, numbers=st.crop_metrics, image_channel=image_channel,
    display_range=nwc.display_range(ctx),
    timepoints=nwc.extremes_columns(timepoints, result.rank_key, st.popout_tps),
    metrics=nwc.crop_metrics(st, sites, channel) if st.crop_metrics else None, with_well=True)
```

(If any of `crops_for_sites`, `Crop`, `page_image_channel`, `page_channel_name`, `resolve_metric`, `_label`,
`gated_crops`, `display_range`, `extremes_columns`, `crop_metrics` is not a module attribute of `controller`,
import it at the top of `_window_controller.py` from where `controller` imports it.)

**`render_site_rows`** gets one change, for a per-nanowell metric whose `timepoint` is `""`: the block header
becomes `f"{block_title} · {label}" + (f" at {model.timepoint}" if model.timepoint else "")`. Re-run
`tests/test_nanowell_site_rows.py` — the Extremes pin must still pass (Extremes always has a timepoint).

**`controller.py`:**

1. `_CROP_KINDS = ("crops", "extreme_crops", "window_crops")`.
2. In `_fetch.finish`, the dispatch dict becomes `{"crops": redraw_detail, "popout_crops": draw_popout,
   "window_crops": draw_popout}`.
3. `draw_popout`: after the visibility guard, add
   ```python
   if st.popout_source == "window":
       wnc.draw_window_popout(ctx, st, fig)
       canvas.draw_idle()
       return
   ```
4. `save_popout_figure`: when `st.popout_source == "window"`, save under `"distribution_window_rows"` +
   `"_" + st.metric` (via `ctx.figures.save(fig, name)`) instead of requiring a selected nanowell.

**`on_popout_click(ctx, event)`:** `if event.button == 1 and event.inaxes in st.popout_hits:
_nwc()._select(ctx, st.popout_hits[event.inaxes])`. Connect it in `tab_view._popout_window` after the card is
registered: `card.canvas.mpl_connect("button_press_event", lambda event: wnc.on_popout_click(ctx, event))`. In
selected-nanowell mode `popout_hits` is `{}` (set it to `{}` in `draw_popout`'s selected branch), so a click does
nothing there, as today.

**Pop-out controls** — `window_view.build_popout_window_controls(ctx, window) -> QHBoxLayout`, added in
`tab_view._popout_window` right after `build_popout_view_controls`:

| Widget | Registered as | Behaviour |
|---|---|---|
| `QLabel("Show")` + `QComboBox` items `"This nanowell"` (`"selected"`), `"Window (N)"` (`"window"`) | `_nw_popout_source_cb` | `wnc.set_popout_source`; the window item reads `"Window (N)"` with N the member count, `"Window"` with none |
| `QLabel("Layout")` + `QComboBox` items from `POPOUT_LAYOUTS` (`"Rows"`; Phase 2 adds `"Hex"`) | `_nw_popout_layout_cb` | `wnc.set_popout_layout`; enabled only when Show is Window |
| `QToolButton("◀")`, `QLabel`, `QToolButton("▶")` | `_nw_popout_page_prev`, `_nw_popout_page_label`, `_nw_popout_page_next` | `wnc.step_window_page(ctx, ∓1)`; label `"Page k of n"`; all three hidden unless Show is Window and there is more than one page |

It registers `_nw_popout_window_refresh` (same shape as `_nw_window_refresh`); call it from
`refresh_controls` beside `_nw_window_refresh`, and from `_set_popout` after `_nw_popout_refresh`.

**Tests** — new file `tests/test_nanowell_window_popout.py` (roster: *"the Distribution window in the pop-out: its
rows, pages, clicks and controls"*):

| Test | Does | Asserts |
|---|---|---|
| `test_open_in_pop_out_draws_the_window_s_members_as_rows` | on the page: drag (as in W3), monkeypatch `renderer.render_site_rows` **and** `_window_controller.render_site_rows` with a spy that records `blocks` and calls the real one, click `_nw_window_popout_btn` | the spy's single block's sites == `wnc.current(st)[1].sites()[:24]`, in order; `_nw_popout` visible; `st.popout_source == "window"` |
| `test_rows_page_by_twenty_four` | on the page, monkeypatch `_window_controller.current` to return a real `DistributionWindow` and a `WindowResult` of 30 `WindowRow`s (sites `("A01", i, 0)`, i = 0…29, ranks 1…30); open the pop-out in Window mode | page 1 shows rows 1–24 (`"Rows 1–24 of 30"` in the figure's texts), ▶ shows `"Rows 25–30 of 30"`, ▶ again stays there |
| `test_a_click_on_a_row_selects_its_nanowell` | after opening, click the centre of one crop axes in `st.popout_hits` with `QTest.mouseClick` on the pop-out canvas | `st.selected_sites == [[*that site]]` |
| `test_the_crop_store_keeps_its_bound` | pure: `CropCache(maxlen=3)` with `protected={"a"}`; insert a–e | `list(cache) == ["a", "d", "e"]` |
| `test_the_window_mode_survives_a_restore` | `apply_config(to_config())` | `popout_source == "window"` |

Mutations: draw `rows` instead of `shown` (page ignored) → the paging test fails; drop the
`popout_source == "window"` branch → the first test fails; ignore `protected` → the cache test fails.

Commit: *"Nanowells: the pop-out shows the Distribution window's nanowells as rows"*.

### Step R3 — bound the crop store

New module `review/features/nanowell/crop_cache_compute.py`:

```python
"""The page's store of cut crops, bounded: ``(well, q, r) -> {timepoint: Crop}`` for at most ``CROPS_KEPT``
nanowells. A window's pop-out pages through hundreds of nanowells, and the store was never pruned. The oldest
inserted goes first; a key in ``protected`` (the selected nanowell and the pop-out's page) is never evicted.

Qt-free."""

from __future__ import annotations

from collections import OrderedDict

#: Nanowells' crops kept at most: about sixteen pages of the window's rows.
CROPS_KEPT = 400


class CropCache(OrderedDict):
    def __init__(self, *args, maxlen: int = CROPS_KEPT, **kwargs) -> None:
        self.maxlen = maxlen
        self.protected: set = set()
        super().__init__(*args, **kwargs)

    def __setitem__(self, key, value) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        excess = len(self) - self.maxlen
        if excess > 0:
            for old in [k for k in self if k not in self.protected][:excess]:
                del self[old]
```

(Verified: `OrderedDict.update` goes through `__setitem__`, so `st.crops.update(found)` is bounded too.)

In `state.py`: `crops: dict = field(default_factory=CropCache)` and `reset_data` sets `self.crops = CropCache()`.
In `controller.py` the one other assignment, `st.crops = {}` (in the image-channel setter, ~line 2420), becomes
`st.crops = CropCache()`. `grep -n "crops = {}" review/features/nanowell/*.py` must then find no `st.crops`.

The cache test is the one listed under R2 (`test_the_crop_store_keeps_its_bound`); add
`test_every_place_that_empties_the_store_keeps_it_bounded`: after `reset_data()` and after changing the image
channel on the page, `isinstance(st.crops, CropCache)`.

Commit: *"Nanowells: bound the store of cut crops"*. (R3 may be committed before R2 if convenient; R2's
`st.crops.protected` needs it.)

### Step D1 (Phase 1 half) — docs

- `_Docs/Markdowns/README_WellViewer.md`, in the Nanowells page's Distribution section (after *"Reading the
  figure"*), a subsection **Drawing a window** covering: drag to draw, columns snap, a click still selects; the
  strip (Low/High, every/any with both counts, This panel only, Open in pop-out, ✕); "no dot on the figure" and
  its three reasons; one window per metric and axis; the changed-since sentence. In the pop-out section: **Show:
  This nanowell | Window**, Rows, 24 a page, clicking a row selects it.
- `_Docs/Markdowns/ARCHITECTURE.md`, in the Nanowells page sections: the window modules (`window_compute`,
  `_window_controller`, `_window_render`, `window_view`) and what each owns; Distribution clicks select on
  release; `HitMap.panels` / `HitMap.values`; `render_site_rows` and its two callers; the pop-out's source and
  layout; `CropCache`. Present tense, no "new in" wording (`test_doc_version_sync.py`).

Commit: *"Docs: the Distribution window"*.

## 4. Phase 2 — the composite (PR 2)

Create `claude/distribution-window-composite` from the Phase 1 tip after PR 1 is open.

### Step H2 — `composite_compute.py` (pure)

Create `review/features/nanowell/composite_compute.py` with **exactly** the code in Appendix B.

Tests — new file `tests/test_nanowell_composite_layout.py` (roster: *"the window's hex composite: one patch per
well, tiles by rank on a spiral, pages"*):

| Test | Asserts (verified) |
|---|---|
| `test_the_spiral_fills_rings_outward_without_repeating` | for n in 1, 7, 19, 37, 91: `len(set(spiral(n))) == n`, rings (`views_compute.hex_ring`) non-decreasing, max ring == `radius_for(n)` (0, 1, 2, 3, 5) |
| `test_one_patch_per_well_in_the_order_of_each_well_s_best_rank` | rows `B02` ranks 1, 3, 4 and `A01` ranks 2, 5 → `[p[0] for p in c.patches] == ["B02", "A01"]`; B02's rank 1 tile at `(0.0, 0.0)`; A01's rank 2 tile at `(4.0, 0.0)` |
| `test_tiles_never_overlap` | the minimum distance between tile centres is `1.0` (to 1e-9) |
| `test_rank_not_insertion_order_places_the_tiles` | give the rows in reverse rank order: same tile positions as in rank order |
| `test_the_title_says_it_is_not_a_well` | `c.title(5) == "Composite of 5 of 5 nanowells from 2 wells — not a real array; places assigned by rank within each well"` |
| `test_pages_are_clamped` | 100 rows: `page(rows, 0)` → 91, `page(rows, 1)` → 9, `page(rows, 7)` → 9, `page(rows, -1)` → 91 |

Mutations: sort patches by well name → order test fails; `spiral` starting each ring at `(k, 0)` without
changing the walk → the no-repeat test fails; drop the `+ 2` from `step` → overlap test fails.

Commit: *"Nanowells: lay the window's nanowells out as a hex composite"*.

### Step H3 — drawing the composite

New module `review/features/nanowell/_composite_render.py`:

```python
TILE = 0.46   # half a tile's side, in pitch units: tiles 0.92 across, so a gap shows between neighbours

def render_composite(fig, comp, crops: dict, timepoint: str, *, n_total: int, display_range=None,
                     note: str = "") -> list:
    """Draw *comp* (``composite_compute.Composite``) at *timepoint*; return ``[(x, y, site)]`` for clicks/hover."""
```

Algorithm:

1. `fig.clear()`; one axes `ax = fig.add_axes([0.02, 0.10, 0.96, 0.80])`; `ax.set_aspect("equal")`;
   `ax.set_axis_off()`; `ax._aw_image_panel = True`; `_bg, ink, faint = renderer._inks(ax)`.
2. `scale = renderer.crop_scale([crops.get(t.source, {}).get(timepoint) for t in comp.tiles], display_range)`.
3. For each tile: `crop = (crops.get(t.source) or {}).get(timepoint)`. If `crop` and `crop.image is not None`:
   `ax.imshow(crop.image, cmap="gray", vmin=lo, vmax=hi, extent=(t.x - TILE, t.x + TILE, t.y + TILE, t.y -
   TILE), interpolation="nearest", gid="nw-composite-tile")` with `lo, hi = scale.of(crop)`. Else: a
   `matplotlib.patches.RegularPolygon((t.x, t.y), 6, radius=0.5, fill=False, hatch="////", edgecolor=faint,
   linewidth=0.6, gid="nw-composite-missing")`, and count the reason — `crop.note` if `crop` else
   `"not cut yet"` if `t.source not in crops` else `f"no image at {timepoint}"`.
4. Foot mark per tile: `Rectangle((t.x - TILE, t.y + TILE - 0.08), 2 * TILE, 0.08,
   facecolor=WELL_COLORS_TUPLE[t.patch % 96], linewidth=0, gid="nw-composite-well")` (import
   `WELL_COLORS_TUPLE` from `ui.theme`; it is the data palette, not a chrome colour).
5. Patch labels: `ax.text(x, y, well, ha="center", va="bottom", fontsize=8, color=ink, gid="nw-composite-patch")`
   for each `(well, x, y)` in `comp.patches`.
6. Frame: `Rectangle` round `min/max` of tile centres ± 0.9, `fill=False, linestyle="--", edgecolor=faint,
   gid="nw-composite-frame"`; set `ax.set_xlim` / `ax.set_ylim` to that box plus 0.2 with y inverted (top is
   the smaller y).
7. Title `fig.text(0.02, 0.97, comp.title(n_total) + f" · {timepoint}", va="top", fontsize=10, color=ink,
   gid="nw-composite-title")`. Caption `fig.text(0.02, 0.012, …, fontsize=7, color=faint, wrap=True,
   gid="nw-composite-caption")` = `note` + `"Each tile is its nanowell's crop at one size; every tile on " +
   scale.said() + "."` + `" No image: " + ", ".join(f"{n} {why}" …) + "."` when any were missing.
8. Return `[(t.x, t.y, t.source) for t in comp.tiles]`.

Use `renderer._ink(...)` on texts as the Extremes drawing does so the theme flip repaints them.

**State:** `POPOUT_LAYOUTS = ("rows", "hex")`; new non-persisted fields `composite_tp: str = ""` (`""` = the
default timepoint) and `popout_tiles: list = field(default_factory=list)`; `reset_data` clears both.

**`draw_window_popout`:** after the two empty cases, branch on `st.popout_layout == "hex"`:

```python
page_rows = cc.page(rows, st.window_page)
comp = cc.composite(page_rows)
timepoints = [str(tp) for tp in st.data.timepoints()]
tp = st.composite_tp if st.composite_tp in timepoints else (result.rank_key or timepoints[0])
sites = [t.source for t in comp.tiles]
# … the same `protected` / `missing` / `_fetch("window_crops", …)` block as for rows …
st.popout_hits = {}
st.popout_tiles = render_composite(fig, comp, {s: nwc.gated_crops(st, s, st.crops[s]) for s in sites
                                               if s in st.crops}, tp, n_total=len(rows),
                                   display_range=nwc.display_range(ctx), note=strip_text(st))
return
```

Factor the protected/missing/fetch block into `_want_crops(ctx, st, sites)` used by both layouts. Page counts use
`cc.COMPOSITE_PAGE` for hex and `WINDOW_PAGE` for rows (`_page_size(st)`), in `step_window_page` and the page
label.

**Clicks and hover:** `on_popout_click` — when `st.popout_tiles` and `event.inaxes` is not `None`, take the tile
whose centre is nearest `(event.xdata, event.ydata)` within 0.5 and `_select` its site. New
`on_popout_motion(ctx, event)` — the same lookup; on a hit `ctx.notify.set_status(f"Tile {rank} · {well} {q},{r}")`
(rank from the tile, so keep `(x, y, site, rank)` — return four-tuples from `render_composite` and adjust step 8
accordingly). Connect `motion_notify_event` on the pop-out canvas in `tab_view._popout_window`.

**Timepoint stepper:** in `build_popout_window_controls`, after the page buttons: `QToolButton("◀")`,
`QLabel`, `QToolButton("▶")` registered `_nw_popout_tp_prev`, `_nw_popout_tp_label`, `_nw_popout_tp_next`;
visible only when Show is Window and Layout is Hex; they call `wnc.step_composite_tp(ctx, ∓1)`, which moves
`st.composite_tp` through `st.data.timepoints()` (clamped) and calls `draw_popout`. The label shows the
timepoint name.

**Tests** — new file `tests/test_nanowell_composite_render.py` (roster: *"drawing the composite: tiles, the missing
placeholder, one brightness scale, provenance on click"*):

| Test | Asserts |
|---|---|
| `test_every_tile_is_drawn_or_said_missing` | a 7-tile composite where one crop has `image=None, note="no image of EGFP in archive"` and one site has no crops at all: 5 `nw-composite-tile` images, 2 `nw-composite-missing` patches, caption contains `"1 no image of EGFP in archive"` and `"1 not cut yet"` |
| `test_one_brightness_scale_across_wells` | crops from two wells with pixel ranges 0–100 and 1000–1100: every image's `get_clim()` is identical |
| `test_the_title_names_it_a_composite_not_a_well` | the title text starts with `"Composite of 7 of 7 nanowells from 2 wells — not a real array"` |
| `test_a_click_on_a_tile_selects_its_real_nanowell` (page) | drag a window, open the pop-out, set Layout to Hex, click the centre of the first tile | `st.selected_sites == [[*first tile's source]]`; `st.well` unchanged; `ctx.state.selection.selected_wells` unchanged |
| `test_the_composite_has_no_well_id` (pure) | `Composite` and `CompositeTile` have no attribute named `well` (`not hasattr`) and `dataclasses.fields` of `CompositeTile` are exactly `x, y, source, rank, patch` |

Mutations: scale each tile separately (`crop_scale([crop])` per tile) → the brightness test fails; skip the
missing placeholder → the first test fails.

Commit: *"Nanowells: the pop-out draws the window as a hex composite"*.

### Step H4 — isolation

`test_the_composite_has_no_well_id` and the click test above are the isolation evidence. Additionally grep:
`grep -rn "composite" review/features/batch_export review/features/nanowell/collect.py
review/features/nanowell/controller.py` must show nothing outside the pop-out drawing path. Record the grep's
result in row H4.

### Step D1 (Phase 2 half) — docs

README: in the pop-out section, **Layout: Hex** — what the composite is and is not, patches by well, rank spiral,
91 a page, the timepoint stepper, hover and click, the missing-tile hatch. ARCHITECTURE: `composite_compute`,
`_composite_render`, the hex branch of `draw_window_popout`, the declined µm fields and why. Commit: *"Docs: the
window's hex composite"*.

## 5. Checks before opening PR 1

Run, in this order, on the final commit, and record which commit each result belongs to in the PR body:

1. `ruff check .`
2. The guard family as a set: `scripts/test.sh tests/test_optional_returns_declared.py
   tests/test_no_unbound_names.py tests/test_no_silent_except.py tests/test_safe_helpers_used_right.py
   tests/test_architecture_contracts.py tests/test_layering.py tests/test_guardrails.py
   tests/test_complexity_ratchet.py tests/test_monolith_budgets.py tests/test_stale_app_attrs.py
   tests/test_doc_version_sync.py tests/test_ctx_app_burndown.py tests/test_deferred_imports.py
   tests/test_first_party_imports_resolve.py tests/test_the_fast_lane_stays_fast.py`
3. `scripts/test.sh --nanowell-features` (selects what the change reaches).
4. `scripts/test.sh --ui`.
5. `scripts/test.sh` (the whole fast lane; `controller.py` and `renderer.py` are widely imported).

A failure in 2–5 that the diff did not cause is reproduced on `origin/main` in a worktree before it is called
pre-existing (CLAUDE.md §7.3 #3).

**PR 1:** title *"Nanowells: draw a window on Distribution and see its nanowells in the pop-out"*, label
`minor`, body sections *Summary*, *What the user sees*, *Code*, *Tests* (each new file, and the mutations that
proved them), *Checks run* (with commit), *Docs*, *Not in this PR* (Phase 2 composite, Phase 3 Subset), ending
with the attribution lines.

## 6. Checks before opening PR 2

The same list, on the Phase 2 tip. PR 2: title *"Nanowells: the Distribution window as a hex composite"*, label
`minor`, base `claude/distribution-window-plan`, the retargeting sentence of §1.

## 7. After PR 1 merges

§1's retargeting procedure, then the checks of §5 on the merged result, then push.

## 8. Phase 3 — Use as Subset (not started; not part of the current request)

Decided, for a later session: **Use as Subset** on the strip freezes the current members into a new Subset
condition kind `window_sites` (`subset_compute`), holding the site list and a name (`Window 1`, `Window 2`, …);
a row passes iff its `(well, q, r)` is in the list. It is not live-bound, so it cannot depend on the figure it
filters. Windows drawn on the filtered figure stack as further conditions; each is listed and removable in the
Subset editor. Rows S1 and S2 below stay `todo` until a session is asked to do this phase.

## 9. Rows

States: `todo`, `done` (with the test and the mutation that proved it), `declined` (reason here and at the site).

| Row | Item | Step | State | Evidence |
|---|---|---|---|---|
| W1 | `window_compute.py` and its pure tests | W1 | done | `tests/test_nanowell_window_members.py`, 12 tests. Mutations, each an `AssertionError`: `hit & required` for `hit >= required` fails the every test and the sentence test; the overlap test in `snap` replaced by centre-inside fails the snap test; exclusive bounds (`lo < value < hi`) passed all eleven tabled tests, so `test_the_range_includes_both_of_its_ends` (values exactly on 2.5 and 3.0) was added and fails under that mutation |
| W2 | windows and pop-out choices on `NanowellState`, persisted | W2 | todo | |
| W3 | the drag, the band and rings, the press/release split | W3 | todo | |
| W4 | the strip | W4 | todo | |
| R1 | Pin Extremes' rendering, then generalise `render_extremes` → `render_site_rows` (`ExtremeRow` is kept as the row type: a row needs only `site`, `rank`, `value`, `values`, so the window supplies the same dataclass and no rename is needed) | `renderer.py`, `tests/test_nanowell_site_rows.py` | done | `test_extremes_is_drawn_exactly_as_it_was_before_the_montage_was_generalised` hashes the figure's structure (texts, axes places, image pixels), taken before the refactor and unchanged after; mutation: one space removed from the row label made it fail with a different hash. `test_rows_from_several_wells_name_their_well_and_extremes_rows_do_not` pins `with_well`, with two rows sharing an address in different wells |
| R2 | the pop-out's Window mode: rows, pages, clicks, controls | R2 | todo | |
| R3 | `CropCache` bounding `st.crops` | R3 | todo | |
| D1a | docs, Phase 1 | D1 | todo | |
| H1 | crop µm-per-pixel and centre fields | — | declined | Not needed: each crop is cut at 1.4 × its own nanowell's radius, so equal tiles show every nanowell at one nanowell-relative scale; the caption says so (§2 decision 16) |
| H2 | `composite_compute.py` and its tests | H2 | todo | |
| H3 | drawing the composite, hover, click, timepoint stepper | H3 | todo | |
| H4 | the composite reaches no well field, CSV or Batch Export | H4 | todo | |
| D1b | docs, Phase 2 | D1 | todo | |
| S1 | `window_sites` Subset condition | Phase 3 | todo | |
| S2 | Use as Subset; stacked windows in the Subset editor | Phase 3 | todo | |

---

## Appendix A — `review/features/nanowell/window_compute.py`

```python
"""The Distribution window: a box drawn on the Distribution figure, and the nanowells whose values fall inside it.

**A window is a value range over whole columns.** Its y extent is a range of the metric, inclusive at both ends. Its
x extent is snapped to the columns it overlaps (:func:`snap`) and stored as their keys: on *Along x: Time* a
column is a timepoint (``Swarm.label``), on *Along x: Wells* a group (``Swarm.group``). A dot's horizontal offset
inside its column is packing, not data, so it never decides anything.

**Membership is read from the model, not from the drawing** (:func:`members`). Every nanowell with a value is in its
swarm's ``values`` and ``sites``, whether it was drawn as a dot, packed into a "+N" overflow, or drawn inside a
density — so the window reaches the nanowells a click cannot.

**Every or any.** Over several timepoints, *every* keeps a nanowell only when it has a value inside the range at
each covered timepoint its panel has; a nanowell with no value at one of them is not a member. *Any* keeps it when
one such value is inside. Along wells there is one value per nanowell and the two agree. Both counts are always
computed, so the figure can state both.

Qt-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from review.features.nanowell.distribution_compute import TIME, WELLS

RULES = ("every", "any")


@dataclass(frozen=True)
class WindowRow:
    """One member, as ``renderer.render_site_rows`` draws a row: the fields of ``extremes_compute.ExtremeRow``. Its
    own class, because ``extremes_compute`` imports ``state``, which imports this module."""
    site: tuple               # (well, q, r)
    rank: int                 # 1 is the first
    value: float              # the value it is ranked by (NaN when it has none at the ranking column)
    values: dict              # timepoint -> its value there, over the panel's timepoints


@dataclass(frozen=True)
class DistributionWindow:
    column: str               # the metrics column the window was drawn on (``DistributionModel.column``)
    axis: str                 # ``"time"`` or ``"wells"`` (``DistributionModel.axis``)
    keys: tuple               # the covered columns' keys, in the figure's order
    lo: float
    hi: float                 # lo <= hi, both inclusive, in the metric's units
    panel: str = ""           # the title of the panel it was drawn in
    panel_only: bool = False  # *This panel only*: cover that panel alone, not every panel
    rule: str = "every"
    drawn_under: tuple = ()   # ``settings_key`` when the window was drawn
    n_at_drawing: int = 0     # the members it had then, for the "changed since" sentence

    def to_config(self) -> dict:
        return {"column": self.column, "axis": self.axis, "keys": list(self.keys), "lo": self.lo, "hi": self.hi,
                "panel": self.panel, "panel_only": self.panel_only, "rule": self.rule,
                "drawn_under": [list(p) for p in self.drawn_under], "n_at_drawing": self.n_at_drawing}

    @classmethod
    def from_config(cls, block) -> DistributionWindow | None:
        """The saved window, or ``None`` for a block that is not one (it is then dropped, never half restored)."""
        try:
            lo, hi = float(block["lo"]), float(block["hi"])
            keys = tuple(str(k) for k in block["keys"])
            if (block["axis"] not in (TIME, WELLS) or block.get("rule", "every") not in RULES or not keys
                    or not (math.isfinite(lo) and math.isfinite(hi)) or lo > hi):
                return None
            under = tuple((str(name), str(value)) for name, value in block.get("drawn_under", ()))
            return cls(str(block["column"]), block["axis"], keys, lo, hi, str(block.get("panel", "")),
                       block.get("panel_only") is True, block.get("rule", "every"), under,
                       int(block.get("n_at_drawing", 0)))
        except (KeyError, TypeError, ValueError):
            return None


@dataclass
class WindowResult:
    rows: list                                     # WindowRow per member, ranked
    rank_key: str                                  # the column the rows are ranked at ("" for a per-nanowell metric)
    n_every: int = 0
    n_any: int = 0
    covered: dict = field(default_factory=dict)    # panel index -> set of covered column indices (round(Swarm.x))
    missing_panel: bool = False                    # *This panel only*, and no panel has that title now

    def sites(self) -> list:
        return [row.site for row in self.rows]


def column_key(swarm, axis: str) -> str:
    """What a swarm's column is called in a window: its timepoint along time, its group along wells."""
    return str(swarm.label) if axis == TIME else str(swarm.group)


def window_key(column: str, axis: str) -> str:
    """The key a window is kept under on the state: one per metric and axis."""
    return f"{column}|{axis}"


def settings_key(st) -> tuple:
    """What the values a window reads were computed under: when it differs from ``drawn_under`` the figure says so."""
    return (("baseline", str(st.baseline)), ("moving average", str(st.moving)),
            ("baseline window", str(st.baseline_moving)), ("fixed cohort", "on" if st.cohort else "off"),
            ("subset", repr(st.subset.to_config())))


def snap(model, panel_index: int, x0: float, x1: float) -> tuple:
    """The keys of the columns of panel *panel_index* that the x span ``[x0, x1]`` overlaps, in the panel's order. A
    column ``i`` spans ``(i - 0.5, i + 0.5)``; an empty tuple when the span overlaps no column that has a swarm."""
    lo, hi = min(x0, x1), max(x0, x1)
    panel = model.panels[panel_index]
    keys = []
    for swarm in sorted(panel.swarms, key=lambda s: s.x):
        i = round(swarm.x)
        if lo < i + 0.5 and hi > i - 0.5:
            key = column_key(swarm, model.axis)
            if key not in keys:
                keys.append(key)
    return tuple(keys)


def _key_order(model) -> list:
    out = []
    for panel in model.panels:
        for swarm in sorted(panel.swarms, key=lambda s: s.x):
            key = column_key(swarm, model.axis)
            if key not in out:
                out.append(key)
    return out


def members(model, window: DistributionWindow) -> WindowResult:
    """The nanowells inside *window* on *model*, ranked: by their value at the latest covered column, largest
    first, a nanowell without a value there last, then by well, ``q`` and ``r``."""
    keys = set(window.keys)
    order = [k for k in _key_order(model) if k in keys]
    per_nanowell = model.axis == WELLS and not model.timepoint
    rank_key = "" if per_nanowell else (order[-1] if model.axis == TIME and order else model.timepoint)
    result = WindowResult([], rank_key)
    panels = [(i, p) for i, p in enumerate(model.panels) if not window.panel_only or p.title == window.panel]
    result.missing_panel = window.panel_only and not panels
    every, anyof, series = [], [], {}
    for i, panel in panels:
        mine = [s for s in panel.swarms if column_key(s, model.axis) in keys]
        result.covered[i] = {round(s.x) for s in mine}
        required = {column_key(s, model.axis) for s in mine}
        inside: dict = {}
        for s in panel.swarms:
            key = column_key(s, model.axis)
            for site, value in zip(s.sites, s.values):
                site = tuple(site)
                if not per_nanowell:
                    series.setdefault(site, {})[str(s.label) if model.axis == TIME else model.timepoint] = float(value)
                if key in keys and window.lo <= value <= window.hi:
                    inside.setdefault(site, set()).add(key)
        for site, hit in inside.items():
            anyof.append(site)
            if hit >= required:
                every.append(site)
    result.n_every, result.n_any = len(every), len(anyof)
    chosen = every if window.rule == "every" or model.axis == WELLS else anyof
    rank_values: dict = {}
    for _i, panel in panels:
        for s in panel.swarms:
            # Along time, the value at the ranking column; along wells, the one value each nanowell has.
            if model.axis == WELLS or column_key(s, model.axis) == rank_key:
                for site, value in zip(s.sites, s.values):
                    rank_values.setdefault(tuple(site), float(value))

    def sort_key(site):
        v = rank_values.get(site, math.nan)
        return (math.isnan(v), 0.0 if math.isnan(v) else -v, site[0], site[1], site[2])

    ranked = sorted(set(chosen), key=sort_key)
    result.rows = [WindowRow(site, k, rank_values.get(site, math.nan), dict(series.get(site, {})))
                   for k, site in enumerate(ranked, 1)]
    return result


@dataclass(frozen=True)
class WindowRows:
    """What ``renderer.render_site_rows`` needs of a model, for the window's rows."""
    timepoint: str            # the column ranked at (marked); "" for a per-nanowell metric
    column: str
    timepoints: list          # every timepoint of the data, in order: the montage's columns


def changed_sentence(window: DistributionWindow, now: tuple, n_now: int) -> str:
    """"" when the settings are those the window was drawn under; else which changed and how the count moved."""
    then = dict(window.drawn_under)
    changed = [name for name, value in now if then.get(name) != value]
    if not changed:
        return ""
    return (f"{', '.join(changed).capitalize()} changed since the window was drawn: {window.n_at_drawing:,} → "
            f"{n_now:,} nanowells.")


def label_text(window: DistributionWindow, result: WindowResult, not_drawn: dict, axis: str) -> str:
    """The strip's sentence: the range, the counts, and the members with no dot on the figure, by why."""
    rng = f"{window.lo:.4g}–{window.hi:.4g}"
    if result.missing_panel:
        return f"Window {rng}: the panel it was drawn in ({window.panel}) is not on the figure."
    if axis == TIME and len(window.keys) > 1:
        counts = (f"{result.n_every:,} inside at every covered timepoint · {result.n_any:,} at any"
                  if window.rule == "every" else
                  f"{result.n_any:,} inside at any covered timepoint · {result.n_every:,} at every")
    else:
        counts = f"{len(result.rows):,} nanowells inside"
    hidden = sum(not_drawn.values())
    why = ", ".join(f"{n:,} {k}" for k, n in not_drawn.items() if n)
    return f"Window {rng} · {counts}" + (f" · {hidden:,} with no dot on the figure ({why})" if hidden else "")


def not_drawn(result: WindowResult, model, hits, *, max_panels: int, density_above: int) -> dict:
    """Members with no drawn dot inside the window's columns, by why: ``"in panels not drawn"`` (past
    *max_panels*), ``"in densities"`` (a panel drawn as densities), ``"in overflow"`` (a "+N" a slot could not
    hold). *hits* is the figure's ``renderer.HitMap``, whose ``panels`` say which axes drew which panel."""
    drawn = set()
    index_of = {id(ax): i for ax, i in getattr(hits, "panels", [])}
    for ax, xs, _ys, sites in getattr(hits, "entries", []):
        cols = result.covered.get(index_of.get(id(ax), -1), set())
        drawn.update(tuple(s) for x, s in zip(xs, sites) if round(float(x)) in cols)
    panel_of = {}
    for i, panel in enumerate(model.panels):
        for s in panel.swarms:
            for site in s.sites:
                panel_of.setdefault(tuple(site), i)
    out = {"in panels not drawn": 0, "in densities": 0, "in overflow": 0}
    for row in result.rows:
        if row.site in drawn:
            continue
        i = panel_of.get(row.site, 0)
        if i >= max_panels:
            out["in panels not drawn"] += 1
        elif _members_per_column(model, model.panels[i]) > density_above:
            out["in densities"] += 1
        else:
            out["in overflow"] += 1
    return out


def _members_per_column(model, panel) -> int:
    """How many wells share a column of *panel*: the renderer draws densities above ``DENSITY_ABOVE``."""
    if model.axis == TIME:
        return max(len({s.well for s in panel.swarms}), 1)
    by_col: dict = {}
    for s in panel.swarms:
        by_col.setdefault(round(s.x), set()).add(s.well)
    return max((len(v) for v in by_col.values()), default=1)
```

## Appendix B — `review/features/nanowell/composite_compute.py`

```python
"""The window's nanowells packed into a **composite**: an artificial hex array of tiles, one per nanowell, grouped
into one patch per source well and placed by rank inside each patch.

It is not a well. Its positions say nothing about where a nanowell sits in its well: rank 1 of each well is at its
patch's centre and the rest spiral outward in rank order (:func:`spiral`). Patches are laid out left to right in
rows, in the order of each well's best-ranked member. Every tile keeps its real nanowell (``source``), which is what
a hover names and a click selects. Positions are in **pitch units**: neighbouring tiles' centres are 1 apart.

Qt-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from review.features.nanowell.views_compute import lattice_xy

#: The most tiles one composite page holds: the hexagonal number of a radius-5 patch.
COMPOSITE_PAGE = 91
#: Axial steps around a ring, in walking order (east, then counter-clockwise on screen).
_DIRS = ((1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1))


def spiral(n: int) -> list:
    """The first *n* axial addresses ``(q, r)`` from the centre outward: ``(0, 0)``, then ring 1's six, ring 2's
    twelve, … each ring walked from ``(0, -k)`` eastward and round counter-clockwise."""
    out = [(0, 0)][:n]
    k = 1
    while len(out) < n:
        q, r = 0, -k
        for dq, dr in _DIRS:
            for _ in range(k):
                if len(out) < n:
                    out.append((q, r))
                q, r = q + dq, r + dr
        k += 1
    return out


def radius_for(n: int) -> int:
    """The smallest ring count whose hexagon holds *n* tiles: 1 + 3k(k + 1) >= n."""
    k = 0
    while 1 + 3 * k * (k + 1) < n:
        k += 1
    return k


@dataclass(frozen=True)
class CompositeTile:
    x: float
    y: float                    # pitch units, y down
    source: tuple               # (well, q, r): the real nanowell
    rank: int                   # its rank in the window (1 is first)
    patch: int                  # index into Composite.patches


@dataclass
class Composite:
    tiles: list = field(default_factory=list)
    patches: list = field(default_factory=list)      # (well, label x, label y), label above the patch
    n_wells: int = 0

    def title(self, n_total: int) -> str:
        return (f"Composite of {len(self.tiles):,} of {n_total:,} nanowells from {self.n_wells} "
                f"well{'s' if self.n_wells != 1 else ''} — not a real array; places assigned by rank within each well")


def composite(rows) -> Composite:
    """The composite of *rows* (``ExtremeRow``, ranked), one patch per well."""
    by_well: dict = {}
    for row in rows:
        by_well.setdefault(row.site[0], []).append(row)
    wells = sorted(by_well, key=lambda w: min(r.rank for r in by_well[w]))
    out = Composite(n_wells=len(wells))
    if not wells:
        return out
    radius = max(radius_for(len(v)) for v in by_well.values())
    step = 2 * radius + 2                               # one empty ring between neighbouring patches
    per_row = max(1, math.ceil(math.sqrt(len(wells))))
    for p, well in enumerate(wells):
        cx, cy = (p % per_row) * step, (p // per_row) * step
        members = sorted(by_well[well], key=lambda r: r.rank)
        slots = spiral(len(members))
        xs, ys = lattice_xy([q for q, _ in slots], [r for _, r in slots], 1.0)
        for row, x, y in zip(members, xs, ys):
            out.tiles.append(CompositeTile(cx + float(x), cy + float(y), tuple(row.site), row.rank, p))
        out.patches.append((well, float(cx), float(cy) - radius - 0.9))
    return out


def page(rows, index: int, size: int = COMPOSITE_PAGE) -> list:
    """Page *index* of *rows* (clamped into range): the composite is drawn one page of ranks at a time."""
    pages = max(1, math.ceil(len(rows) / size))
    index = min(max(index, 0), pages - 1)
    return rows[index * size:(index + 1) * size]
```
