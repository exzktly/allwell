# Large datasets — opening a plate whose wells do not fit in memory

A remediation tracker (CLAUDE.md §6.1): the plan for the follow-up PR that stacks on the nanowell roll-up
(`claude/nanowell-rollup`, #982), written before any code. One row per item, updated in the same commit as the
work, closed only with evidence, and deleted by the PR that closes its last row.

**Status, derived from the rows below:** 16 rows — 4 `done` (evidence), 4 `answered` (questions), 8 `todo` (plan).
Recount with `grep -c '| \`done\` |'` and so on; never edit this line by hand alone.

## The report

Opening a "very large dataset" in Review raised, in a dialog over the empty Home page:

```
MemoryError: Unable to allocate 8.43 MiB for an array with shape (1104480,) and data type object
  … pandas/core/series.py, line 1031, in __array__
      arr = np.asarray(values, dtype=dtype)
```

Each per-well CSV is about **300 MB uncompressed** (the user's figure).

## Evidence — what is established, and how

| # | Finding | How it is known | State | Evidence |
|---|---|---|---|---|
| E1 | The failing allocation is not what used the memory. 8.43 MiB is exactly 1,104,480 × 8 bytes — one pointer per row of a text column of one well — and an allocation that small fails only once the process has already exhausted what it can have | Arithmetic on the reported numbers (`1104480 * 8 / 2**20 = 8.4265`) | `done` | Established |
| E2 | Opening a dataset reads every well's full frame and keeps all of them, **in the load loop itself**: `load_controller.load_directory` (`:555`) calls `repo.load_well` for every CSV, which is `load_and_project` → `read_well_frame` and stores the frame in `ctx.state.dataset.cache` (no size limit). Everything after it reads that cache: `_rebuild_all_timepoints_cache`, `_rebuild_all_fovs_cache`, and `recalculate_threshold` (channel detection from the first well, the range scan over every well's values, `channel_state_controller.py:201`). The earlier reading of this row named the range scan as the reader; measurement corrected it | Measured on the real app: a probe spying on `read_well_frame` while `ReviewApp._load_path` opened the 4-well mini dataset recorded 4 full-frame reads, all from `load_and_project` ← `well_repository.load_well` ← `load_controller.load_directory:555`, and 4 frames cached | `done` | Measured; not yet confirmed as the path of the user's crash, which needs no confirmation (Q1) |
| E3 | One well the size the user describes costs **201 MiB** held and **1.27 GiB** at peak while it is read, and takes 7.7 s: a synthetic 1,104,480-row CSV of 294 MB (6 text, 22 numeric columns) read through `read_well_frame` on `main`, each measurement in its own process (`ru_maxrss`). A 96-well plate of such wells would hold about 19 GB; 24 wells about 4.8 GB | Measured: `scratchpad/mem/make.py`, `load.py`, `alt.py` (session-local scripts) | `done` | Measured on synthetic data; the real column mix is unknown (Q2) |
| E4 | Column-selective reads already exist in part: `review/data/parquet_cache.py` caches each well's parsed frame as Parquet (measured 51 ms against 3,068 ms for a 120,000-row CSV parse, and 7 ms for 6 of 37 columns). Its `read_cached` reads **whole frames only**, and its docstring names the reason: the row cache holds one frame per well and every `get_rows` caller reads whatever column it wants from it, so a partial frame answers a later caller's column with a `KeyError` or an empty plot. 59 call sites across 39 modules read rows through `get_rows` / `iter_plot_groups` / `iter_plot_group_wells` (counted on the roll-up). Two of them write state *into* the cached frames: manual curation sets `Included` from the overrides (`review/data/curation.py:100`) and intensity gating sets it from the gates (`features/home/cell_gating/gating_compute.py:234`) | Code read and `grep` on the roll-up | `done` | Established |

Measured on the same synthetic well, for M5 and M6: the pyarrow CSV parser reads it in 2.9 s instead of 10.6 s
(at 1.1M rows × 38 columns) but does **not** lower the peak (1.80 GiB against 1.83 GiB); converting the 22 numeric
columns to `float32` halves what is held (201 → 100 MiB at 300 MB; 294 → 146 MiB at 421 MB).

## Open questions — answers change the plan, not only its order

| # | Question | Why it matters | State | Answer |
|---|---|---|---|---|
| Q1 | The full traceback (`%LOCALAPPDATA%\allwell\allwell-boot.log`) | Confirms E2 is the path that crashed, or names another (a pooling view, the curation pass at load, the FOV integrity check) | `answered` | Not needed (user): E2 is accepted as the path. M1's test checks that opening reads no full frame, so it holds whatever the exact frame was |
| Q2 | How many wells, how many rows and columns in the largest, and the machine's RAM | Sets the memory budget (M4) and whether any view can pool every cell at all (M7) | `answered` | User: assume a full 96-well plate of ~300 MB CSVs, ~1.1M rows each — about 19 GB held today by E3's measurement. The budget (M4) defaults to a share of physical RAM read at runtime rather than a figure for one machine |
| Q3 | Whether the user's environment has pyarrow (`python -c "import pyarrow"` in the `allwell` env) | Without it the Parquet cache is off and every open parses every CSV — 7.7 s a well at this size before any memory question — and M2 must fall back to CSV `usecols` | `answered` | Not needed: M2 serves both — the Parquet cache's column reads with pyarrow, and CSV `usecols` reads without it — and M8's benchmark measures both |
| Q4 | Which plots, if any, the user needs over every cell of the plate at once | M7: a whole-plate per-cell view at this size cannot be drawn from all rows; it has to aggregate or sample, and the user should choose which | `answered` | Not needed: M7 aggregates where a view's output is an aggregate and samples, with the n on the figure, where it draws cells; the choice is a setting, not a question |

## Plan — the follow-up PR

The order is the order of work: M1 removes the crash on opening; M2–M4 make the rest of the app safe at this
size; M5–M8 make it cheaper and honest when it still cannot cope.

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| M1 | **Opening reads no rows.** The load loop registers each well's path and reads nothing (E2: today it reads and caches every frame). What the load needs from every well — each channel column's min and max, the timepoints, the row and included counts — comes from a summary computed per well from only the columns it needs (Parquet column read, or CSV `usecols` on a miss), stored beside the Parquet cache under the same source fingerprint so a changed CSV recomputes it. `recalculate_threshold` (channel detection and range scan), `_rebuild_all_timepoints_cache`, `_rebuild_all_fovs_cache` and `get_all_timepoints` read summaries, never `get_rows`; the FOV verdict and any other load-time reader found by the probe are moved to the summary or to first use. Test: opening a plate reads no well's full frame (spy on `read_well_frame`), and the range and timepoints equal those computed from the rows | `review/data/` (a well-summary module beside `parquet_cache.py`), `review/core/channel_state_controller.py`, `review/data/data_loading.py` | `todo` | |
| M2 | **Rows by column, on demand.** `get_rows(ctx, label, columns=...)` backed by a per-well column store: a request loads only the columns not already held (Parquet subset read; on a cache miss, one full parse that writes the Parquet cache, then subset reads from it), and a returned frame always holds every requested column — never a partial frame that answers a later caller with a missing column (the reason `read_cached` is whole-frame today, E4). `columns=None` keeps meaning "all", so every existing caller works unchanged; call sites then declare their columns one group at a time, starting with the ones that run on every plot. Inventory first: the 59 call sites with the columns each reads, written into this row before any conversion | `review/data/data_loading.py`, `review/data/parquet_cache.py`, `review/data/well_repository.py`, call sites | `todo` | |
| M3 | **Verdict and edit columns are derived on every read, never stored only in the cache.** The projectors (`QC_Pass`, `Crop_Pass`, `Gate_Pass`) declare the columns they read and are applied to whatever column set is loaded; curation's `Included` overrides and intensity gating's `Included` (E4) are re-applied from their persistent sources whenever a well's rows are (re)loaded. Precondition for M4: without it, evicting a well loses the user's edits silently. Test: edit, evict, read again — the edit is there | `review/data/curation.py`, `review/features/home/cell_gating/`, `review/data/cache_projectors.py` | `todo` | |
| M4 | **A bounded row cache.** A memory budget (default a share of physical RAM, a setting to change it) measured by the held frames' deep byte size, least-recently-used wells evicted first; the well being drawn is never evicted mid-draw. The budget and what is held are visible (a status line or Pipeline Info), not inferred. Test: a budget smaller than the plate keeps the held bytes under it while every well is still readable | `review/data/data_loading.py`, `core/` setting | `todo` | |
| M5 | **A lower read peak.** The CSV miss path (E3: 1.27 GiB peak for a 201 MiB frame) reads in typed chunks, or through pyarrow, so a well's peak is a small multiple of what it keeps; once M2 lands, the Parquet path reads only requested columns and the CSV is parsed once per well ever. Measured before and after in separate processes, as E3 was | `review/data/data_loading.py` (`load_well_csv`) | `todo` | |
| M6 | **`float32` measurement columns — only if the numbers do not move.** Halves held memory (measured). Gated on running the statistics, fold-change and threshold computations both ways on a real-sized plate and comparing the outputs (CLAUDE.md §7.5 #8: the fixtures must not be the ones that agree by construction); declined with the measured difference if any figure the user reads changes | `review/data/data_loading.py` | `todo` | |
| M7 | **Views that pool every cell.** Inventory which views concatenate all wells' rows (whole-plate scatter, distribution, heat-map averages, statistics, exports). Each gets per-well aggregation where its output is an aggregate, and stated sampling where it draws cells — with the sampled n written on the figure (CLAUDE.md §7: a shortfall must be visible in the artifact). Exports stream well by well rather than concatenating. The user chooses per view (Q4) | `review/features/*/collect.py`, `review/data/data_loading.py` (`iter_plot_groups`) | `todo` | |
| M8 | **A failure that says what happened.** A `MemoryError` while reading a well is caught at the read, and the message names the well, its size on disk, what is already held and the budget — instead of the generic unexpected-error dialog — and the dataset stays open with that well marked unreadable. Plus a scale benchmark script (not in the fast lane) that writes a synthetic plate of the user's well size and records time and peak memory for opening and for one plot, before and after, so the gains are measured against the report rather than asserted | `review/core/load_controller.py`, `scripts/` | `todo` | |

## Docs owed by the PR

`ARCHITECTURE.md`'s data-loading section (the column store, summaries, budget and eviction, derived `Included`),
and the README's Review chapter (opening large datasets, the budget setting, sampled views).
