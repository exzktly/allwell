# Feature workplan

Eight workstreams, planned together because they share sequencing and because a
body of work this size does not survive in conversation. This file is the state
of that work; it is not a design essay and not a changelog.

## How this file is used

The rules are `CLAUDE.md` §6.1, restated here so a future session that opens this
file alone still follows them:

1. **A row moves in the same commit as the change it describes.** Not at the end
   of a session, not in a batch, not once CI is green.
2. **The counts are derived, never typed.** `tests/test_workplan_is_honest.py`
   parses the rows below; no total is written in prose anywhere in this file.
3. **`☑` requires evidence** — the test that pins the row, watched failing
   without the fix. "Implemented" is not `☑`.
4. **An item that turns out not to be worth doing is `⊘`**, with the reason in
   its own note. Never deleted: a dropped row and a forgotten row look identical.
5. **This file is deleted by the PR that closes its last row**, and that PR's
   body says it is gone.

Status glyphs: `☐` not started · `◐` in progress · `☑` done with evidence ·
`⊘` declined, reason in the note.

## Sequencing

```
A scale bar fix ──────────────┐
B coverage measurement ───────┼── independent, ship in any order
C performance pinning ────────┤
D Parquet storage ────────────┘   (C first, so D's win is measured not asserted)
E recent datasets ────────────    independent

F sample variables ──────────┬── F is the foundation
                              ├──► G response curves
                              └──► H statistical tests (the two-factor half)
```

B is deliberately early: it measures everything that follows it. C before D for
the same reason — a performance change with no baseline is a claim.

## The tracker

| # | Workstream | Item | Status | Note |
|---|---|---|---|---|
| 1 | A | `draw_cell_chrome` refuses an oversized scale bar instead of clamping | ☐ | |
| 2 | A | The refusal is reported per pass, not silently | ☐ | |
| 3 | A | Guard: a bar wider than its cell is never drawn short | ☐ | |
| 4 | B | Fast lane emits coverage; slow lane emits coverage | ☐ | |
| 5 | B | `coverage combine` across both lanes | ☐ | |
| 6 | B | Differential coverage on the PR diff, advisory at first | ☐ | |
| 7 | B | One-off zero-coverage inventory, triaged into rows here | ☐ | |
| 8 | C | Complexity assertions for the four fixed hot paths | ☐ | |
| 9 | C | `pytest-benchmark` lane with in-repo baselines | ☐ | |
| 10 | C | Peak-memory ceiling on the CSV load path | ☐ | |
| 11 | D | Parquet cache writer + fingerprint invalidation | ☐ | |
| 12 | D | `WellRepository.rows()` reads Parquet | ☐ | |
| 13 | D | Column projection plumbed from the aggregation call sites | ☐ | |
| 14 | D | Guard: Parquet is never written from a projected frame | ☐ | |
| 15 | D | Equality harness: CSV path vs Parquet path on real plates | ☐ | |
| 16 | E | `RecentDatasets` store on `QSettings` | ☐ | |
| 17 | E | Recents on the no-dataset placeholder (`EmptyState`) | ☐ | |
| 18 | E | Recents card on Home Overview | ☐ | |
| 19 | F | `variables` block: schema, accessors, migration | ☐ | |
| 20 | F | Variables sub-tab: define name / type / unit / values | ☐ | |
| 21 | F | Fill row — along rows or columns, repeat each N | ☐ | |
| 22 | F | Select-then-chip assignment on the tab's own plate | ☐ | |
| 23 | F | Plate readout: colour + glyph + unassigned ring + tooltip | ☐ | |
| 24 | F | Group rows roll up; `Mixed` is shown and never inherited | ☐ | |
| 25 | F | `member_variable_values` + collectors, in one commit | ☐ | |
| 26 | F | Band controls: Group by / Colour by / Split into panels | ☐ | |
| 27 | G | `features/response/` package + registry entry | ☐ | |
| 28 | G | X combo (scope zone) listing Number variables | ☐ | |
| 29 | G | Timepoint selection, defaulting to `focus_timepoint` | ☐ | |
| 30 | G | Log-x default from the data; vehicle control off-scale | ☐ | |
| 31 | G | Omitted groups named in the figure, the CSV and the status line | ☐ | |
| 32 | G | Fit popover: 4PL / Hill, fitted in log space | ☐ | |
| 33 | G | Non-convergence states itself and draws no curve | ☐ | |
| 34 | H | Effect size + CI on every pairwise comparison | ☐ | |
| 35 | H | Omnibus gate (one-way ANOVA / Kruskal-Wallis) | ☐ | |
| 36 | H | Dunnett vs a named control | ☐ | |
| 37 | H | Paired / repeated-measures tests (blocked on F) | ☐ | |
| 38 | H | Two-way ANOVA over two variables (blocked on F) | ☐ | |

---

## A — Scale bar: refuse rather than clamp

**The defect.** `image_compose.draw_cell_chrome` (`well_viewer/features/image_browser/image_table/image_compose.py:889`) clamps the bar to the image:

```python
blen = int(min(bar_px, W - 2))
```

Measured, with `pixel_size_um=0.65` and `scale_bar_um=50` (so `bar_px=77`):

```
crop W=2048  asked 77px (50.0um)  drawn 77px = 50.1um
crop W=  60  asked 77px (50.0um)  drawn 58px = 37.7um   <-- silently wrong
crop W=  40  asked 77px (50.0um)  drawn 38px = 24.7um   <-- silently wrong
```

A wrong number burned into a figure, with nothing on screen or in the log saying
so. This is §7's cardinal case and the same shape as the PowerPoint clip-cap bug:
the exporter producing a value the data never had.

Everything else about the bar was checked and is correct — `bar_px` is computed
once per Generate in source pixels, burned in before any thumbnail or export
downscale, nothing resamples in between, and the live and export paths share the
helper. Only the clamp is wrong.

**The fix.** Draw nothing when the bar cannot be drawn at its true length:

- `draw_cell_chrome` returns the bar's outcome rather than swallowing it. It is
  already a pure array function, so the cleanest shape is for it to *not* draw
  and for the caller to learn why — return the RGB plus a reason, or take an
  out-parameter set as `frame_load` does with `scan_misses` / `raw_fallbacks`.
  Prefer the latter: that pattern is established in this package and the callers
  already thread such sets.
- The threshold is `bar_px > W - 2 * margin`, not `W - 2`, so a bar is refused
  before it touches the edges rather than after.
- **Report it where the pass reports its other shortfalls.** `image_compose`
  already carries `partial_scan_report` and `reset_scan_misses` per Generate, and
  `state.image_table.partial_scans` holds one. Add the same shape for
  `scale_bar_refused`, and have the Image Table's status line name the count and
  the cells. The export path (`export.py:463`) reports through its own return.
- The user-facing sentence must say what to do: the bar is longer than the cell,
  so either shorten *Bar length µm* or widen the crop.

**Tests.** `tests/test_image_table_scalebar_render.py` gets the geometry cases as
pure-lane tests — they need no app, only the array function:

- a bar that fits is drawn at exactly `round(um / px)` pixels, at three cell sizes;
- a bar that does not fit is **not drawn at all**, and the refusal is recorded;
- the refusal reaches the status line (the existing app-level test can assert this).

Prove the guard the way §7.3 asks: run the new geometry test against a worktree of
`origin/main` with `scripts/prove_guard.sh` and require an `AssertionError` naming
the drawn length, not an `ImportError`.

**Note for the reader.** That file's one real render test is `slow`-marked, so it
did not run in the fast lane while the bug was live. The new cases are pure on
purpose.

**Scope note.** `pixel_size_um` is one global value per grid, so a table mixing
magnifications gets one wrong bar for the other magnification. Out of scope here;
recorded as a known limit. The proper fix is reading `PhysicalSizeX` out of the
OME metadata the converter already parses, which belongs with a pipeline change.

---

## B — Coverage measurement

**The problem.** `pytest-cov>=4.0` is in `tests/requirements-dev.txt:31` and
`--cov` appears in neither `scripts/test.sh` nor either workflow. With ~495
source files and ~719 test files, nothing knows which application modules no test
reaches.

**Not a gate on a number.** A percentage threshold over a repo this size is a
number to game, and this codebase's real risk is not thin coverage but *absent*
coverage in specific places. Three moves, in order:

**B1 — Measure both lanes and combine them.** `scripts/test.sh` gains
`--cov=. --cov-report=` behind an opt-in flag (an env var, so the default local
run stays as fast as it is), and `scripts/test_slow.sh` does the same per batch.
The slow lane's shards each produce a `.coverage.<shard>`; `coverage combine`
merges them with the fast lane's.

This step is load-bearing and easy to get wrong: measuring the fast lane alone
reports every full-app-only module as dead, and acting on that list would delete
live code. Do not produce a report until both lanes are in it.

**B2 — Differential coverage on the diff.** The standing CI check is not "is the
repo 80% covered" but "did this PR add lines no test executes". `diff-cover`
against `origin/main` is the smallest thing that does this. It answers the
question a reviewer actually has, and it cannot be satisfied by writing tests for
unrelated code.

Because the slow lane does not run on PRs (§10.4), differential coverage on a PR
sees fast-lane data only — so a PR whose code is genuinely covered by a full-app
test will read as uncovered. Two honest options: run it as advisory (report, do
not fail), or teach it to consult the last combined report from a slow-lane run
on `main`. Start advisory; a check that cries wolf is worse than none.

**B3 — The one-off inventory.** Produce, once, the list of application modules
with zero coverage from either lane. That list is the actual finding here. Every
entry is triaged into one of three: covered by a test that should exist, dead
code to delete, or genuinely unreachable and worth a comment saying so. Each
becomes a numbered row in this tracker rather than a paragraph in a report.

**Pair with mutation testing, scoped.** `mutmut` over `well_viewer/domain/` and
`pipeline/` only — both are Qt-free and pure, so mutants run in the fast lane's
budget. This automates §7.7's "make the edit and watch a test fail" ritual, which
is currently done by hand and therefore only when someone remembers. Out of CI;
a script to run deliberately, like `test_slow.sh`.

**Housekeeping found while reading.** `.github/workflows/ci.yml`'s header comment
says the fast lane is "~1,770 tests, ~60s"; it is ~6,840 tests and ~3.5 min. Fix
the comment in the same PR — a stale number in the file that defines the lane is
exactly the kind of thing a reader trusts.

---

## C — Pinning performance

**The problem.** The repository has fought and won a series of large performance
battles, every one of them recorded as a measurement in `ARCHITECTURE.md` and
none of them pinned by anything: the 70 s → 0.2 s stylesheet call, 1091 ms →
180 ms aggregation over 96 wells, 3.57 s → 1.06 s colony detection, 327 MiB →
135 MiB per frame, 1729 → 867 MiB peak on load. `test_the_fast_lane_stays_fast.py`
guards the harness, not the app. Each of those is free to regress in silence.

Three tiers, cheapest and most durable first.

**C1 — Assert the complexity, not the clock.** Most of the wins were complexity
fixes, and a complexity fix can be tested deterministically: run the function at
sizes *n* and 2*n*, count the operation the fix removed, and assert the ratio is
sub-quadratic. This is immune to machine speed, runs in the fast lane, and fails
loudly on precisely the regression that happened. The four to cover:

- `spatial_compute.detect_colonies` — the DBSCAN frontier set (was quadratic in
  cluster size);
- `stage_layout` first-visit extraction — the `c not in cells[:i]` shape;
- `pipeline.stardist.nucleus_seed_markers` — one whole-array pass, not one per
  nucleus (count array traversals with a wrapped ndarray);
- `data_loading.aggregate_with_threshold_df` — the string-timepoint fallback must
  not run when the numeric column is complete (assert the fallback is not called).

The last one is the model for the others: what regressed was not time but
*whether an expensive branch is entered*, and that is directly assertable.

**C2 — A benchmark lane with in-repo baselines.** `pytest-benchmark`, its own
script (`scripts/bench.sh`), baselines committed as JSON, failing on a regression
beyond a wide band — 1.5×, not 1.1×. CI wall-clock is noisy and a tight band
produces flakes that get muted, which is worse than no check. A wide band still
catches every regression this repo has actually had, all of which were multiples.
Not on PRs; run deliberately, like the slow lane.

**C3 — Memory has its own gauge.** `tracemalloc` peak around `load_well_csv` for
a fixed synthetic plate, asserted against a ceiling with headroom. This is the
1729 → 867 MiB win, and unlike timing it is stable across machines. It also
becomes the acceptance test for workstream D.

**Explicitly out of scope.** Do not try to pin the 70 s stylesheet number — that
one is guarded structurally by the widget reaper, which is the right level.

---

## D — Parquet storage (no DuckDB yet)

**The goal.** Remove the CSV parse from the load path and make it possible to
read a subset of columns, without changing the query engine, the aggregation
functions, or any collector. This is the half of the storage change that is
independently valuable and commits to nothing.

**Why it pays.** A plot reads roughly 7 of 37 columns (`Included`, `QC_Pass`,
`Crop_Pass`, `Gate_Pass`, `area_px`, `timepoint_hours`, the value column, plus
`fov` under per-FOV spread). Today all 37 are parsed from text and held resident
per well, for every well ever touched, for the life of the session. Parquet is
columnar and typed: the parse disappears, the dtypes and categories survive on
disk, and `pd.read_parquet(path, columns=[...])` reads only what is asked for.

**The design.**

- **The CSV stays the source of truth.** Parquet is a cache, written beside the
  dataset at `out/.aw_parquet/<well>.parquet`, dot-prefixed so every dataset scan
  in the app already skips it (the same convention as `.allwell-transit`).
- **Invalidation is a fingerprint, not a timestamp comparison alone.** Store the
  source CSV's `(size, mtime_ns)` in the Parquet file's own key-value metadata and
  compare on read. A mismatch re-converts. This is the same shape as
  `cell_overrides.json`'s `seg_fp` stamping and fails the same safe way.
- **Conversion is `load_well_csv`'s existing output**, written with
  `to_parquet`. That is what keeps the two paths byte-identical by construction:
  the typed read, the renames, the FOV normalisation, the categoricals and the
  circularity backfill all happen exactly once, in the function that already does
  them, and Parquet stores the result. Do not reimplement the read.
- **The write is atomic**, through the existing convention: a staging file beside
  the destination and `os.replace`. `tests/test_dataset_tool_integrity.py` scans
  for the truncating shapes and will fail a bare `to_parquet` onto the live path.
- **A conversion that fails is not fatal.** Log once per dataset and fall back to
  the CSV path for the session. A read-only output volume is an ordinary state
  (the run journal already handles it that way) and must not cost the user their
  dataset.

**The column-projection half is the actual win, and it is a separate row.**
Writing Parquet alone changes load time and nothing else, because
`WellRepository.rows(label)` returns a whole frame and every caller expects one.
To read fewer columns, the callers have to say what they need:

- `aggregate_with_threshold_df` already names its columns — `val_col`, `tp_col`,
  `area_px`, the gate columns, the four verdict columns — so the set is
  derivable from its arguments rather than guessed.
- Add `WellRepository.rows(label, columns=None)` and a small resolver that turns
  an aggregation request into a column list. `None` keeps today's behaviour.
- The cache must then be keyed on the column set, or a narrow read must not
  displace a wide one. Simplest correct rule: the cache holds the widest frame
  requested so far for a well, and a narrower request slices it. That keeps one
  entry per well and never re-reads for a subset.

**The trap.** The verdict columns are *written into* the cached frame by the
projectors after load, and by the gating worker later. A Parquet file must never
be written from a projected frame — it would bake one session's QC verdict into
the cache and serve it to the next session under different parameters. Convert
from the output of `load_well_csv`, before `load_and_project` runs its
projectors. This is the single most important rule in this workstream and is
worth its own guard test.

**Acceptance.** C3's memory gauge, plus a byte-equality harness: for a corpus of
real plates, every column of the CSV-loaded frame equals the Parquet-loaded frame
including dtype and category set, and `aggregate_with_threshold_df` returns
identical `AggPoint` tuples from both. Per §7.5 #8, get the corpus from real
datasets, not from fixtures written for this test — and say in the PR body which
plates it ran against.

**Explicitly not in scope.** DuckDB, any change to `aggregate_with_threshold_df`,
any change to the collectors, and the unbounded `StatsCacheState.cache` (worth
fixing, but it is a memo-eviction bug rather than a storage one — file it as its
own item when this lands).

---

## E — Recent datasets on Home

**Where the list lives.** `QSettings` (`allwell.py:1559` already has the handle
for window geometry). Not the dataset folder: a list *about* many datasets cannot
live inside one of them, and a dataset copied to a colleague must not carry the
author's history of other plates.

Store a capped list (10) of `(path, display_name, opened_at)`. The display name
is `domain/dataset_name.dataset_display_name`'s answer, stored rather than
re-derived — `collect.py` may not touch the filesystem, and re-deriving would
need the `in/` sibling check. Deduplicate by resolved path, most recent first.

**Where it is written.** `load_controller.load_path` (`:123`), on success only. A
folder that opened and yielded nothing readable is not a dataset the user wants
offered back to them.

**The design problem, and it is the whole feature.** Home's body is wrapped in
`ui_helpers.wrap_with_empty_state`, so before a dataset is open the user sees the
shared "Load a directory." placeholder and *not* Home. The one moment a recents
list matters most — launch, nothing loaded — is the one moment Home is hidden.

Two candidate answers:

1. **Put the recents on the placeholder.** `EmptyState` already supports one
   action (`setAction`, which Home is the only user of). Extend it to accept a
   list of secondary entries, and Home passes its recents. Contained, and the
   list appears exactly where the user is looking on launch.
2. **Give Home a no-dataset state of its own**, replacing the shared placeholder
   for this one page.

Prefer (1). (2) means Home stops sharing the wording and the swap moment with the
twelve other wrapped tabs, which is the property `core/empty_state_copy.py` exists
to protect, and re-introduces the drift that module was written to end.

**Where it is shown once a dataset is open.** A card on Home Overview, built
exactly like the saved-plots strip: `collect.recent_datasets(ctx)` returning
plain data, `_build_recents(ctx, page, reg)` in `tab_view.py`, tiles that are
`QPushButton`s with child labels. That precedent is already load-bearing and
tested; follow it rather than inventing a second card idiom.

**Two rules the strip must honour.**

- **A path that no longer exists is shown, not hidden.** An external drive that
  is not mounted is the commonest reason, and silently dropping the entry tells
  the user their history was lost. Show it disabled with the reason on the
  tooltip. Checking existence is a filesystem call, so it happens in the *view*,
  not in `collect.py` — the same split the saved-plot thumbnails already use
  (`collect` returns a path, the view loads the pixels).
- **Clicking one goes through the existing open path**, `run_home_action(ctx,
  "open")`'s sibling, so a recent open and a browsed open cannot diverge.

**Tests.** The store is pure and belongs in the fast lane: capping, dedupe by
resolved path, ordering, a malformed `QSettings` value read as empty rather than
raising. The card itself gets one full-app test asserting a click loads.

---

## F — Sample variables (the two-factor plate map)

**What this is.** Named annotation axes the user declares per plate — `Cell line`
with values `HeLa` / `U2OS`, `Dose` with values `0 … 10 µM` — assigned to wells.
It is the foundation for G (a numeric variable becomes a plot's x axis) and for
two of H's tests.

**Where it lives.** A new Home sub-tab, **Variables**, between Groups and Well
Labels: `well_viewer/features/home/variables/`, registered in
`features/registry.py` with `removable=True`, `reads_well_selection=False`, and
**its own plate in the tab body** rather than the rail plate (the
`features/home/plate_panel.py` precedent).

Home is the page where the user declares what the wells mean, so this belongs
there. It cannot extend Groups: `groups/panel_view._commit_plate_to_current`
guards on the current sub-tab being "Groups" precisely because a second meaning
on the rail plate silently rewrites memberships, and ARCHITECTURE already records
the "two plates meaning different things" hazard. It cannot extend Well Labels
either — that grid is free text feeding legends, and parsing `key: value` out of
free text is §7.4 #2's mistake, with no type and no value ordering.

**Values are objects, not typed strings.** A variable carries `name`, `type`
(`text` | `number`), `unit` (numbers only) and an **ordered list of values**.
That list is what makes renaming a value rewrite every well holding it, and what
stops `1uM` / `1 µM` / `1` accumulating as three distinct doses.

### The storage decision, and the two reviewers disagreed about it

The plotting reviewer proposed an `x_value` field on each `Selection` — one
number per group. The annotation reviewer proposed a per-well store with the
group acting as a brush. **Take the per-well store.** Both of the claims that
decide it were checked against the tree:

- **Groups partition the plate.** `selection_controller.sel_strip_wells`'s own
  docstring is "A well belongs to ≤1 group", and `sel_set_composition` calls it
  with `keep_id`. So a per-well value always rolls up to a group unambiguously,
  while the reverse fails — ungrouped wells exist and a group-keyed store cannot
  annotate them at all.
- **`make_selection` is closed.** Its signature is keyword-only and it builds a
  fresh dict; an `x_value` kwarg raises, and one smuggled into the dict is dropped
  the next time any of the five call sites rebuilds the selection. The
  forward-compat carry-through at `selections_model.py:390` belongs to
  `validate_repair`, not to `make_selection`. Adding the field properly would mean
  touching `make_selection`, `validate_repair`, `to_block` and `from_block` — and
  would still be the wrong key space.
- **`bar_pool_replicates` flips the key space** from set names to well tokens
  mid-plot. `plot_members`' own docstring records that as the reason
  `member_replicate_sets` exists: a mapping written against the sets is silently
  orphaned by a toggle that has nothing to do with it. A group-keyed variable
  store walks into exactly that trap.

So: `pipeline_info.json` gains a `variables` block holding the definitions plus
`values: {well_token: value_id}`. A group row in the Variables tab is an
**assignment target** that displays the rolled-up value of its wells.

**The trap, and it must not be softened.** A well added to a group *after* the
variables were filled has no value. It does not inherit one. The group reads
`Mixed (2)`, the well is ringed on the plate, and a plotted member whose wells
disagree is **excluded with a stated count** rather than resolved to its first
well's value. Fabricating an annotation on a real measurement is §7's cardinal
sin, and silent inheritance is exactly that.

### Assignment

One row above the plate, modelled on Quick Replicates' own row:
**`Fill [Dose ▾] along [Columns ▾] · repeat each [2] · [Apply Fill]`** — values
cycle along the chosen axis over the enabled wells. Any block, row or column
design is then one click.

Everything else is **select-then-chip**: the plate in the `"set"` model with
row/column headers selectable and rubber-band drag (as `groups/controller.rep_add`
already configures it), then a click on a value chip (`widgets/chip_group.py`)
assigns. Two steps, and it matches Well Labels' existing "select cells, then Add
Prefix". Deliberately **not** the `"paint"` model: paint latches an add/remove
polarity on press, so dragging over an assigned well *clears* it, and a chip is
still needed to say which value is being painted.

### Reading it back

Two chips above the plate name the defined variables. The selected one owns the
**fill colour** with a value legend; the other supplies a short **marker glyph**
(`setWellMarker` — `H` / `U`, `0` / `.1`). Clicking the other chip swaps them.
A well in the dataset but **unassigned for the active variable gets a ring**
(`setWellRing`) and a count line, *"8 wells have no Dose."* — never the empty
socket, which already means "not in the dataset". Three independent decoration
layers, no reliance on colour alone, per the rule the dashboards already follow.

Extend `core/ui_helpers.well_plate_tooltip_provider` so hover gives the whole
row: `A01 — shCtrl · Cell line: HeLa · Dose: 1 µM`.

### Reaching the plots

One `LabeledComboBox` in the Plotting band's **scope** zone reading
`Group by: Dose`, plus a popover carrying **Colour by** and **Split into panels**.
Both hidden entirely when no variable is defined — the band's stated rule is that
it only ever carries controls that do something.

The semantics live in `domain/plot_members.py` as `member_variable_values(ctx,
scope, name)` built on the existing `member_wells`, so one module answers "what
is this member" for every key space.

**Two flags, both real.** Grouping by a variable introduces a *third* member-key
shape, and `tests/test_plot_members.py` asserts those keys equal what the
collectors emit — so `plot_members` and the collectors change in **one commit** or
that guard fires. And per-sample fold-change `ControlRef`s are keyed on member
keys, so changing *Group by* orphans saved control references: decide what happens
explicitly rather than discovering it.

### Naming

**Variables**, each with **Values**. Hint: *"A variable is anything you changed
across the plate — cell line, dose, timepoint of treatment. Name it, list its
values, then fill them onto the plate."* Not "Factor" (jargon), not "Condition"
(taken — `make_selection` states a group *is* one condition, so the word would
mean two things on one page), not "Metadata" or "Dimension" (the latter means
image axes in the Image Table).

**The payoff worth stating in the hint:** a variable is not a group. A user with
variables defined need not paint twelve group names to get twelve series.

### Acceptance walkthrough — 2 cell lines × 6 doses × 8 replicates

Rows A–D HeLa, E–H U2OS; column pairs are the six doses.

1. Home → Variables → **+ Variable**, name `Cell line`, type Text, values `HeLa`, `U2OS`.
2. `Fill [Cell line] along [Rows] repeat each [4]` → Apply.
3. **+ Variable**, `Dose`, type Number, unit `µM`, values `0, 0.1, 0.3, 1, 3, 10`.
4. `Fill [Dose] along [Columns] repeat each [2]` → Apply.
5. Click the `Dose` chip to check the fill; click `Cell line` to check the other.
6. Plotting → Bar → `Group by: Dose`, popover `Colour by: Cell line`.

No groups are defined anywhere in that sequence. If it does not fit in a minute,
the fill row is wrong.

---

## G — Response curves (numeric x-axis plotting)

**What this is.** Response plotted against a numeric variable the experimenter
set — concentration, seeding density, MOI, temperature, pre-treatment duration —
with error bars across replicates and an optional fit. A serial dilution is one
case of it and the feature is not named for it.

**Blocked on F.** The x value is a Number variable from F's store. Do not build a
private numeric field for this tab; that was the plotting reviewer's proposal and
it is superseded for the reasons in F.

**A new Plotting sub-tab**, `well_viewer/features/response/`, registered like any
other leaf. Not a mode on Line Graphs: that tab's fourth panel is a CDF whose x is
intensity, so a tab-wide x-source control is already wrong for a quarter of the
figure, and a mode toggle would change the meaning of the timepoint and
fold-change controls beside it. A separate leaf keeps the one-line-removable
property `test_feature_removal.py` enforces.

**The x control is scope, not action.** An `X: Concentration` `LabeledComboBox`
in the band's left zone via `plotting_band_slot`, listing F's Number variables,
added before the trailing Threshold.

**The other axis defaults to one timepoint** — `ctx.state.plot.focus_timepoint`,
the shared cross-tab timepoint, *read without writing* (writing it moves Heat Map
and Distribution). Surface it with the Bar tab's existing multi-select button
idiom; picking more than one draws one curve per timepoint, coloured by
timepoint. Defaulting to all timepoints would open a twelve-timepoint plate as
twelve overlapping curves per condition.

**Log x, and the vehicle control.** Log is the existing Properties toggle
(`plot_style._DEFAULTS["x_log"]`) — no new control — but its default comes from
the data: on when the positive values span two decades or more. The zero-dose
control is drawn **off-scale to the left of an axis break**, one decade below the
lowest positive dose, its tick labelled `0`, excluded from the fit with the
caption saying so. Dropping it is matplotlib's default and a silent substitution;
squeezing it onto the axis is a lie about where it sits. Do **not** reuse the
`_aw_log_plus1` FuncScale — it exists for intensities where +1 is negligible and
would move every point on a nanomolar axis.

**Register this tab's `x_log` in the per-tab pref scoping** (`plot_style.py`'s
`FIGURE_SCOPED_PREF_KEYS`), or a log scale set on Distribution leaks onto a
temperature axis.

**Groups with no value are named in three places**, because the shortfall must
survive into a saved figure: a caption on the axes (`3 of 8 groups omitted: no
Concentration (Ctrl, Blank, Rep 7)`), a row per omitted group in the exported CSV
with an empty x and a reason column, and the status line. If *no* group has a
value, an empty state naming the fix rather than empty axes.

**The fit.** A `Fit ▾` popover in the action zone: `None | 4PL | Hill (bottom = 0)`,
constrain-top/bottom, and *shared across curves*. **Fit in log10(x) space** when x
is log-scaled — fitting in linear x on decade-spaced data weights the top dose
almost exclusively — so EC50 is `10**logEC50` with an asymmetric CI. Report it
**in the legend entry** and in the plot-data CSV, not only in a sidebar, because a
fitted number is meant to be quoted and must survive Save to the gallery.

**Non-convergence draws nothing.** The legend reads `EC50 — (no fit)` and the
caption states the fitter's reason: fewer than four distinct x values, no plateau,
a bound hit. When the optimum sits on a bound the covariance CI is meaningless —
print `—`, never a number.

**Fold change.** `vs t0` is meaningless on a dose axis; `vs control` is exactly
what is wanted. Install the shared controls with a scope of its own and enable the
t0 half only when more than one timepoint is drawn.

**No un-pooled mode.** `plot_members.bar_member_keys` emits well tokens rather
than set names when sets are active and pooling is off; a per-condition x value
cannot resolve per well. Say so in the tab rather than letting it fail quietly.

### Naming

Tab **Response Curves**, beside `Bar Plots` / `Line Graphs` / `Heat Map`.
Controls: `X:`, `Timepoints: n selected`, `Fit ▾`, `Display ▾`, `Save ▾`. No
control says "dose", "concentration" or "EC50" unless the user typed it — except
the fit's own readout, where EC50 is the term of art.

---

## H — Statistical tests

Today the Statistics tab offers Welch and Student t-tests, Wilcoxon rank-sum,
Mann-Whitney U, and a two-well KS test, all pairwise, all at one timepoint, with
Benjamini-Hochberg adjustment across the pairs (`stats/controller.py:158`). The
replicate unit is correctly per-well or per-FOV and never per-cell, with an
explicit refusal below n=2 — that part is right and must not regress.

Five additions, in the order they pay off.

**H1 — Effect size and a confidence interval, on every comparison.** Hedges' *g*
with a bootstrap CI on the difference of means. With n=3 wells a p-value alone
says very little, and this is the cheapest scientific upgrade available. It needs
no new UI: the results panel already prints a block per pair, and
`domain/significance.py` already decides what a bracket says. Add the columns
there and let the label mode choose whether the bracket shows stars, the adjusted
p, or the effect size.

**H2 — An omnibus gate.** One-way ANOVA or Kruskal-Wallis (matching the
parametric choice already in the Test combo) run before the pairwise pass, with
its result printed above them. Reporting fifteen pairwise tests without first
asking whether anything differs is the practice BH-adjustment partially
compensates for; stating the omnibus is more honest and costs one scipy call.
Do not *suppress* the pairwise results when the omnibus is non-significant —
that is a policy the app should not impose — but say so.

**H3 — Dunnett against a named control.** This is the test that matches what the
app already models. The fold-change machinery has a first-class control concept
(`domain/control_ref.ControlRef`, per-member controls, `resolve_control`), and
comparing k treatments to one control is both the common design and strictly more
powerful than all-pairs with BH. The control picker already exists and is already
shared between two tabs (`core/fold_change_controls_view.py`) — reuse it rather
than adding a second way to name a control, or the two will disagree.

`scipy.stats.dunnett` exists in modern scipy; check the pinned version before
committing to it, and be prepared to implement it against the studentised range
if the pin is too old. Note that pin in the row.

**H4 — Paired and repeated-measures tests.** Paired t and Wilcoxon signed-rank.
Replicate sets repeated across plates or days *are* a paired design and are
currently tested as independent, which is both less powerful and slightly wrong.

This one has a prerequisite that must not be skipped: pairing requires knowing
*which* replicate in group A corresponds to which in group B. That correspondence
does not exist in the data model today. It is exactly what workstream F's
annotations provide (a "replicate" or "batch" axis), so H4 is blocked on F and the
row says so. Shipping a paired test that pairs by list order would be a wrong
number with a plausible face.

**H5 — Two-way ANOVA.** Once F exists, "does the drug effect depend on cell
line" becomes expressible. Two factors plus interaction, printed as a small table.
This is the first test needing `statsmodels`; add the dependency here and not
before, and check it against the pins that hold numpy at 1.24 (`_Docs/requirements.txt`
documents that resolution — re-run the dry-run described there).

**A cross-cutting rule for all five.** Every one of these produces a number that
can be undefined — a CI that did not converge, an ANOVA with an empty cell, a
paired test with unmatched replicates. Each reports *undefined* distinctly from
*computed as zero*, in the results panel and in any CSV column, per §7. The
existing `err` slot in `_run_pair_tests`' tuple is the precedent.

**Where the UI grows.** The Test combo gains entries; the results panel gains
rows. `stats/tab_view.py:200` is the roster. Resist adding a second panel: the
tab is already five selectors and one action, and the band exists to keep it that
way.
