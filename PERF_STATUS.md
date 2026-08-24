# STATUS — well-selection / plotting performance

**Updated:** 2026-08-24 06:20 UTC
**Branch:** `claude/well-selection-performance-7phaix`
**Base:** `44c0d38` (main, includes PR #847)
**Version:** 9.0.5 on main → **9.0.6** applied
**PR:** not opened yet — deliberately. CI bills on `pull_request:` only, so the PR opens
last, after local fast lane + slow lane + adversarial review all pass.

---

## Gate

| | |
|---|---|
| PR 847 merged | ✅ 03:02 UTC, `44c0d38` |
| Branch rebased onto fresh main | ✅ |
| Step 0a commit carried forward | ✅ `e363890` |

---

## Steps

| # | Step | State | Notes |
|---|---|---|---|
| 0a | Perf bench | ✅ done | runs on fresh main; has both collect and draw phases; baseline captured below |
| 0b | Equivalence corpus | ✅ done | 14 cases green on unchanged HEAD; `tests/test_aggregate_equivalence.py` |
| 1 | String timepoint short-circuit | ✅ done | **6.1x**: `aggregate_well` x96 1,091 → 180 ms, measured back-to-back |
| 2 | Per-well memo | ✅ done | 1,248 calls → **96 distinct keys**; one-click cost 1,150 → **3.4 ms** |
| 5 | Group-visibility cache clear | ✅ done | 312 entries kept instead of discarded per toggle |
| 6 | Visible-only redraw | ✅ done | up to 3 figures per click → 1 |
| 8 | CDF decimation | ✅ done | draw 5,219 → 2,413 ms on its own |
| 9 | CDF collection residual | ✅ done | warm collect 98.6 → **4.8 ms** |
| 11 | **Legend cap (NEW)** | ✅ done | draw 5,219 → 3,290 ms on its own; with Step 8, **1,498 ms** |
| — | Re-measure, decide 3/7/10 | ✅ done | see below; step 4 DROPPED |
| — | ARCHITECTURE.md / README | ✅ done | orchestrator, stats_compute, DatasetState, data_loading rows rewritten; README legend/CDF note |
| — | Local fast lane | ⬜ | required: no CI during dev |
| — | Local slow lane | ⬜ | file or two per process |
| — | Adversarial Opus review | ⬜ | against full local diff |
| — | Push + open PR | ⬜ | LAST |

---

## Baseline on fresh main (`44c0d38`), measured 03:09 UTC

96-well plate, 6 tps x 9 fovs x 400 cells = 2.07M rows, row cache warm.

| Action | ms | `aggregate_well` |
|---|---|---|
| line collect, 96 wells | 1,146 | 96 |
| line collect, 95 → 96 wells (one click) | 1,150 | 96 |
| bar collect, 96 wells x 6 tps | 7,449 | 576 |
| line collect, 12 groups x 8 | 22,054 | 1,824 |
| bar collect, 12 groups x 8 (pooled) | 7,660 | 576 |
| bar collect, 12 groups x 8 (unpooled) | 6,491 | 576 |
| iter_plot_groups, 12 groups x 8 | 946 | — |
| figure draw, 96 series, with legend | 1,394 | — |
| figure draw, 96 series, no legend | 324 | — |

**One 96-well click = 2,540 ms: 45% collect, 55% draw.**

### Step 11 — the legend, diagnosed

Draw with legend 1,394 ms vs without 324 ms ⇒ **the legend costs ~1,070 ms**, ~42% of the
whole click. Invisible until the bench included the draw half.

Decomposition of ONE panel (`scratchpad/legend_prof.py`):

| | ms |
|---|---|
| draw, no legend | 65 |
| draw, legend 8 entries | 146 |
| draw, legend 32 entries | 232 |
| draw, legend 96 entries | 404 |
| draw, legend 96 entries, handles suppressed | 410 |

**Cause: FreeType text measurement inside the legend's offsetbox packing.** Profile of the
96-entry draw: `FT2Font.set_text` 99 ms / 700 calls, `Text._get_layout` 181 ms cumulative,
`offsetbox._get_bbox_and_child_offsets` 326 ms cumulative. `_get_layout` runs ~6x per label
because the VPacker/HPacker re-measures each entry while packing. Roughly 80 ms fixed +
3.4 ms per entry. Suppressing the handles changes nothing (410 vs 404 ms), so it is not the
glyph drawing — it is measuring the label strings.

**It is 4x worse in the real app than in the bench.** `_finalize_line_axes` calls
`_place_legend` on all four panels (`lineplot/controller.py:583-600`), so a 96-series
redraw builds FOUR 96-entry legends.

**Fix: cap the number of legend entries.** A 96-entry legend at fontsize 7 is unreadable —
it overruns the axes and tells the user nothing. Draw the first N (N ~ 12-15) and append a
single "+84 more" entry. This is a legibility fix that happens to be the largest perf win
available. It is user-visible, so it needs a README note and the cap must be stated in the
figure rather than silently applied (CLAUDE.md §7: a degraded artifact must announce
itself). Export paths take the full legend or a higher cap — a saved figure is read at
300 dpi where 96 entries can be legible.

These absolute numbers run ~25-35% higher than the earlier session's measurements of the
same operations (893 → 1,146 ms for the same 96-well collect). Container contention, not a
regression — CLAUDE.md §2 documents the same 3x spread on the test lane. Ratios and call
counts reproduce exactly (1,824 = 12 x 6 x 3 x 8 + 96), so the shape is sound; treat the
ratios as the signal and re-measure everything in one sitting before/after.

---

## Step 1 result

`aggregate_with_threshold_df` now computes `tp_num` first and runs the two full-column
string passes only when an **included** row needs them (`need[mask].any()`), not when any
row does. An excluded row with an unparseable timepoint token — which real plates carry,
since the pipeline writes `nan` for any filename whose token it cannot parse — no longer
drags the whole well onto the slow path.

Measured back-to-back on one container: **1,091 ms → 180 ms for 96 wells, 6.1x.**

Proved by mutation against HEAD in a worktree, all three real `AssertionError`s:

| Mutation | Result |
|---|---|
| gate on every row (`need.any()`) | fails exactly `test_an_excluded_unresolvable_row_does_not_force_the_slow_path` |
| always fast (`if False`) | 9 failures, every fallback case |
| always slow (`if True`) | 2 failures, both fast-path entry counters |

620 surrounding tests pass (`-k "aggregate or timepoint or data_loading or stats or fov or
text_column or lineplot or barplot"`).

Noted, not fixed: on the slow path `timepoint_text` runs twice — once inside
`_ordinal_timepoints_df`, once in the caller. Pinned by a test. Left alone because that
branch now runs only for wells that genuinely carry an unresolvable timepoint, so
de-duplicating it would trade a correctness risk for a saving on the rare path.

## Step 2a — the writer guard (landed)

`tests/test_row_cache_writers.py` enumerates every site that changes what a cached
measurement frame contains, from the syntax tree rather than from a grep. **7 sites found:**

| Site | Shape |
|---|---|
| `data_loading.py` | `Included` seeded at load |
| `cache_projectors.py` | dynamic column (QC / Crop) |
| `home/cell_gating/gating_compute.py` | `Included`, `Gate_Pass`, `Gate_On` |
| `image_browser/review_image/collect.py` | `Included` via **`df.loc`** |
| `core/load_controller.py` | whole-frame replacement |
| `features/smfish/tab_view.py` | whole-frame replacement |
| `scripts/make_static_fonts.py` | `inplace=True` (unrelated, not a frame) |

Two things the guard caught that a grep did not:

- **`review_image/collect.py`'s `df.loc[mask, "Included"] = ...`** — invisible to a search
  for `df["Included"]`. Reached from four call sites, one of which (`load_controller`) does
  not invalidate afterwards, so the bump must live in the function, not its callers.
- **Whole-frame replacement** (`dataset.cache[label] = ...`) — a different syntactic shape
  entirely, and as invalidating as any column write.

And one false positive it produced, then fixed: the first version matched any attribute
spelled `.cache` and reported `stats_compute.py`, which writes `stats_cache.cache[key]` —
a different cache with a different lifetime. A domain predicate applied outside its domain
(§7.1). The owner must be `dataset`; a bite test now pins both directions.

## Step 2b — the memo (landed)

`aggregate_well_cached` in `stats_compute`, called by the collectors. `aggregate_well`
itself is untouched — memoizing inside it would widen its ctx contract from four
attributes to include the stats cache and gate signature, and the pure-logic tests that
build hand-rolled contexts satisfying exactly that narrow surface would fail.

`DatasetState.rows_version` is the new tick, bumped via `note_rows_changed()` at the
writer sites the AST guard enumerated, plus `begin_load` (a dataset swap: two plates can
carry the same well tokens, and the load-time stats-cache clear sits inside a suppressed
`try/except`).

**Key verified by probe, not by reading:** across a full 12-group collect, 1,248 calls
resolve to **96 distinct keys** — only `label` varies. Nothing spurious is in the key.

**Killed the feature and watched it fail** (§7.7 #2): dropping `rows_version` from the key
in a worktree of HEAD, then excluding every cell of a well, returns the pre-edit aggregate —
a full set of plausible numbers, drawn and reported as current. Real `AssertionError`.

Also fixed while here: `GatingWorker`'s **cancel path never invalidated the stats cache**.
The wells already gated keep their rewritten inclusion columns, so a cancelled scan left
group statistics computed from pre-gating rows. Half-applied gating is still applied.

### Measured after Steps 1 + 2

| Action | Before | After | |
|---|---|---|---|
| line collect, 96 wells | 1,146 ms | 272 ms | 4.2x |
| **line collect, 95 → 96 wells (one click)** | **1,150 ms** | **3.4 ms** | **338x** |
| bar collect, 96 wells x 6 tps | 7,449 ms | 7.4 ms | 1,000x |
| line collect, 12 groups x 8 | 22,054 ms | 2,832 ms | 7.8x |
| bar collect, 12 groups x 8 | 6,491 ms | 5.7 ms | 1,139x |
| one 96-well click, collect + draw | 2,540 ms | 1,565 ms | 1.6x |

The click is now **83% draw, 17% collect**. Everything remaining is on the draw side —
Steps 8 and 11.

### Found, not fixed: two thresholds in one collect

The key probe surfaced the reviewer's finding #2 as a live divergence. `compute_rep_stats`
takes the caller's `threshold`; `compute_rep_n_above_stats` and `compute_rep_n_above_total`
ignore it and recompute their own from `active_channel`. With the bench passing a literal
0.0 they disagreed (0.0 vs 50.0) and the memo saw 192 keys instead of 96.

In the Line tab they agree by coincidence — `redraw_line_plots` resolves the cutoff with the
identical expression. They do **not** agree in batch export of a non-active channel, because
`_app_val_col_scope` swaps `active_val_col` but not `active_channel`. That is a
pre-existing correctness bug affecting exported numbers, out of scope for a perf branch,
and Step 3 must not paper over it by picking one threshold. **Flagged for a follow-up PR.**

The bench now passes the app's own cutoff so it measures what the app does.

## Step 5 — group visibility no longer discards the plate's statistics (landed)

`note_group_visibility_changed(ctx)` does the one thing a toggle needs (sync the per-FOV
button bank) and nothing else. `_set_groups_hidden` calls it instead of
`invalidate_stats_cache`.

**Proved by recomputation before changing anything**, not by reading the keys: a full
collect over 12 groups x 8 wells fills **312 entries**; toggling `hidden` on one group and
recomputing all of them gives 312 comparable keys and **0 value differences**.

**Proved to bite** by mutating HEAD to restore the old behaviour — real `AssertionError`:
*"toggling a group's visibility discarded cached statistics that a visibility change
cannot have invalidated"*.

Two things corrected along the way:

- **`review_image.override_version` is a cache key, not a repaint cue.** `data/inclusion.py`
  memoises the per-well excluded-nucleus map on it and names `invalidate_stats_cache` as its
  bump site. The comment there now says so, so the next person does not move or drop it on
  the strength of "it's a UI cue".
- **The seam matters.** Calling the module function directly bypassed
  `app._invalidate_stats_cache`, which the pure-logic tests stub — so a mock context reached
  a Qt button walk. Routed through a matching window seam,
  `app._note_group_visibility_changed`.

### The projector bump: added, then removed

`project_verdict_column` briefly bumped `rows_version`. It broke `test_crop_scope_view` and
would have required teaching **81 test files** about a field none of them is about — the
"widen the contract, then fix the tests" move the review warned against.

It was redundant anyway. The walk has exactly two callers, the QC and crop controllers, and
both call `invalidate_stats_cache` immediately afterwards, which empties the dict the memo
lives in. The other route in is `get_rows`, projecting onto a frame it has just built, which
cannot have a memoized aggregate. Removed, with the reasoning recorded in the roster.

## Steps 8 + 11 — the draw half (landed)

Both are budgets the **caller** sets, `None` for export. `render_line_series` *is* the
export path (its own docstring says so, and `batch_export/base_panel` calls it), so
"decimate in the renderer not the collector" was never a sufficient rule — the budget has
to be a parameter.

- **Step 8** — `_decimated()` thins the CDF to `SCREEN_CDF_POINTS` (512) at the
  `ax_cdf.plot` call. `n` is read off the **full** array before thinning, so the legend
  still says `n=21,600` rather than `n=512`. y comes from the original ranks, so the curve
  still ends at 1.0.
- **Step 11** — `_capped_legend()` names `SCREEN_LEGEND_ENTRIES` (12) traces and appends
  `+84 more (not listed)`. The overflow is stated in the legend rather than left implicit:
  a legend that silently stops at 12 of 96 looks exactly like a complete one.

### The bench was measuring a figure nobody draws

`run_draw` hand-rolled four `ax.plot` loops instead of calling `render_line_series`. It
therefore could not see either budget, and it under-reported the real draw cost by 3.7x
(1,394 ms vs 5,219 ms). Rewritten to drive the real renderer.

| Draw, 96 series | ms |
|---|---|
| no budgets (= pre-change behaviour) | 5,219 |
| legend cap only | 3,290 |
| CDF budget only | 2,413 |
| **both — what the tab now does** | **1,498** |

With budgets unset the renderer does exactly what it did before, so the 5,219 ms row is a
valid pre-change baseline without needing a worktree of main.

### Whole click, end to end

| | collect | draw | total |
|---|---|---|---|
| before | 1,146 ms | 5,219 ms | **6,365 ms** |
| after | 285 ms | 1,498 ms | **1,783 ms** |

**3.6x overall**; the incremental click (95 → 96 wells) is 1,150 ms → 3.4 ms.

Noted, not changed: matplotlib warns that `loc="best"` is slow with many artists. With the
cap at 12 entries it is cheap; on the uncapped export path it is not. Moving the legend is
user-visible and belongs in its own change.

## Step 9 — the CDF collection residual (landed)

With the aggregates memoized, building `cdf_vals` was almost the entire remaining collect:
**95 ms of 99 ms** for 96 wells. `cdf_values_cached` memoizes it on the same
`rows_version` key, minus threshold and `use_sem` (the CDF draws the raw filtered
population, not a statistic of it).

The filter is **passed in** rather than imported — the caller already receives it as a
parameter, and a second copy resolved inside could drift from the one actually used. Its
identity is part of the key, so a caller supplying a different filter cannot be served
another's answer.

Warm collect, 96 wells: **98.6 ms → 4.8 ms**.

## Step 6 — hidden plot tabs defer their redraw (landed)

`plot_orchestrator.redraw` guarded Distribution and Heat Map on whether the tab had ever
been **built**, not whether anyone was looking at it. Once built it stays built, so a user
who had opened both paid for all three figures on every selection change for the rest of
the session — and each 96-series figure is over a second of matplotlib.

Now also guarded on `visible_scope_names(ctx.window)` (Split-View-aware), with a skipped
figure added to **the existing `fc_dirty_scopes` set** rather than a new flag. Both tabs are
already registered redraw scopes, `flush_dirty_scopes` is already wired into the tab-change
handler, and both carry `on_activate` hooks that re-run this fan-out on show. Two routes to
the same repaint and no third flag — a parallel set is how a figure ends up clean by one
mechanism's reckoning and dirty by another's.

**Fails open**, deliberately: a context too thin to report visibility draws everything that
is built. A redundant redraw costs time; a skipped one costs a figure that disagrees with
the plate.

Tested through the real control (§7.6 #1) — `_show_leaf()` switches to "Plotting" and then
the nested notebook page, the way the sub-tab strip does. A test calling
`ctx.features.redraw` directly would prove the figure *can* be refreshed while the app
never does. Proved to bite: reverting the skip gives *"a Distribution tab nobody is looking
at was repainted anyway"*.

## The last uncached loop

The bench kept reporting 672 `aggregate_well` calls for a group collect where the key
probe found only 96 distinct keys. Rather than assume the memo was working, the calls were
attributed by stack: **576 of them came from `compute_rep_n_above_total`**, which had its
own loop reaching the raw function — 12 groups x 6 timepoints x 8 wells, every one
recomputing a well the neighbouring producers had already aggregated at identical settings.

Routed through the memo: **672 → 96 calls, group collect 2,909 → 1,862 ms.**

Worth naming as a method point: the wrapper-level probe said "96 distinct keys" and was
telling the truth about the wrapper while 576 calls went around it. A count taken at the
seam you changed does not describe the calls that never reach that seam.

## Steps 3, 7, 10 — decided on the numbers

- **Step 3 (merge the three `compute_rep_*` producers): NOT DONE, deliberately.** After the
  memo their per-well work is already deduplicated (672 → 96 calls), so the remaining win is
  a constant factor. And it cannot be done safely as specified: the two `n_above` producers
  ignore the caller's threshold and recompute their own from `active_channel`, so a shared
  producer must pick one — which changes exported numbers on the batch path. Flagged as a
  follow-up bug rather than papered over here.
- **Step 7 (cache the pooled group frames): NOT DONE.** `iter_plot_groups` is down to 452 ms
  from 946 ms as a side effect of the other work, its only consumers are Distribution and
  the exporter, and after Step 6 that cost is paid only while Distribution is visible.
  Caching it would hold ~247 MB permanently — twice the row cache — to save ~450 ms on a
  tab the user is looking at. Bad trade.
- **Step 10 (off-thread collect): NOT NEEDED.** Collect is 273 ms cold and 3.6 ms
  incremental. The remaining latency is matplotlib on the UI thread, which a collect worker
  would not touch.

## Final numbers

| Action | Before | After | |
|---|---|---|---|
| line collect, 96 wells | 1,146 ms | 273 ms | 4.2x |
| **line collect, 95 → 96 wells (one click)** | **1,150 ms** | **3.6 ms** | **319x** |
| bar collect, 96 wells x 6 tps | 7,449 ms | 7.5 ms | 993x |
| line collect, 12 groups x 8 | 22,054 ms | 1,862 ms | 11.8x |
| bar collect, 12 groups x 8 | 6,491 ms | 5.6 ms | 1,159x |
| `iter_plot_groups`, 12 groups x 8 | 946 ms | 452 ms | 2.1x |
| figure draw, 96 series | 5,219 ms | 1,589 ms | 3.3x |
| **one 96-well click, collect + draw** | **6,365 ms** | **1,862 ms** | **3.4x** |

`aggregate_well` calls for a 12-group collect: **1,824 → 96** — one per well.

## Decisions log (for user confirmation at the end)

1. **Started early against pre-847 main, then reverted** — user overruled; zero commits lost.
2. **Trigger uses a Bash merge check** (`git log | grep "#847"`) because triggered sessions
   don't inherit MCP connector grants. Worked.
3. **PR opens last, not first** — CI bills on `pull_request:`; feature-branch pushes are free.
4. **Running the fast lane locally, contradicting CLAUDE.md §2** ("CI is the gate"). That
   convention assumes CI runs per push, which it does not for feature branches. With no PR
   open there is no gate at all, so local runs are mandatory here, not wasteful.
5. **Version 9.0.6** — read off fresh main (9.0.5) rather than assumed.
6. **Status doc lives in the scratchpad, not `_Docs/Markdowns/`** — CLAUDE.md §5 caps that
   directory at three live documents and says status write-ups belong in git history / PRs.

## Open risks

- ~~Bench not verified to run~~ — verified, baseline captured.
- The draw half (55%) is larger than the collect half (45%). Steps 1/2/9 address collect
  only; without steps 8 and 11 the felt latency improves by less than half.
- Step 2's writer list must come from an AST guard, not a grep (§7.5 #4).
- Step 4 dropped on evidence; steps 3/7/10 deferred until the post-change numbers exist.
