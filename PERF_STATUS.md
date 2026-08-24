# STATUS — well-selection / plotting performance

**Updated:** 2026-08-24 04:05 UTC
**Branch:** `claude/well-selection-performance-7phaix`
**Base:** `44c0d38` (main, includes PR #847)
**Version:** main is at 9.0.5 → this PR bumps to **9.0.6** (not yet applied)
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
| 5 | Group-visibility cache clear | ⬜ not started | split cache-clear from UI cues |
| 6 | Visible-only redraw | ⬜ not started | extend `fc_dirty_scopes`, no parallel flag |
| 8 | CDF decimation | ⬜ not started | at `ax_cdf.plot` only, `n` from full array |
| 9 | CDF collection residual | ⬜ not started | `_all_fluor_values_filtered` |
| 11 | **Legend cost (NEW)** | ⬜ not started | diagnosed below; largest single cost in the click |
| — | Re-measure, decide 3/7/10 | ⬜ | step 4 DROPPED |
| — | ARCHITECTURE.md / README | ⬜ | |
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
