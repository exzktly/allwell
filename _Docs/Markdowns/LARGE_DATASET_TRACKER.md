# Large datasets — opening a plate whose wells do not fit in memory

A remediation tracker (CLAUDE.md §6.1): the plan for the follow-up PR that stacks on the nanowell roll-up
(`claude/nanowell-rollup`, #982), written before any code. One row per item, updated in the same commit as the
work, closed only with evidence, and deleted by the PR that closes its last row.

**Status, derived from the rows below:** 22 rows — 6 `done` (evidence), 4 `answered` (questions), 3 `declined` (decisions), 9 `todo` (plan).
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

| E5 | Once a well is in the Parquet cache, re-reading the whole frame through the app's own `read_well_frame` takes **0.22 s** (201 MiB held, 699 MiB transient peak) against **15.3 s** for the CSV parse that fills it. So a well dropped from memory is cheap to bring back, and a memory bound can be enforced by evicting whole wells, with no change to what any caller receives | Measured: `scratchpad/mem/appread.py`, each read in its own process | `done` | Measured on synthetic data |
| E6 | What a query engine (DuckDB 1.1.3) would buy, measured against pandas over per-well Parquet. 24 wells (26.5M rows), page cache dropped, each run in its own process: a per-timepoint aggregate takes 0.4 s at a 197 MiB peak in DuckDB; 8.8 s at 2,271 MiB with pandas holding every row; and **3.5 s at 337 MiB with pandas reading one well at a time**. At 96 wells DuckDB's plate-wide aggregates take about 1.2 s; grouped quantiles peak at about 820 MiB, which `memory_limit` did not cap. Costs: a 62 MB native library (10–15 MiB wheels), and NaN poisoning `avg` unless the Parquet stores missing values as NULL. The conclusion that shapes the plan: **memory is bounded by never holding every well, not by the engine**; the engine buys speed, and only for numbers that do not depend on the inclusion verdicts, which every plotted number does (D1) | Measured: `scratchpad/mem/costs.py`, `spill.py`, `nan.py` | `done` | Measured on synthetic data |

Measured on the same synthetic well: the pyarrow CSV parser reads it in 2.9 s instead of 10.6 s
(at 1.1M rows × 38 columns) but does **not** lower the peak (1.80 GiB against 1.83 GiB); converting the 22 numeric
columns to `float32` halves what is held (201 → 100 MiB at 300 MB; 294 → 146 MiB at 421 MB).

## Open questions — answers change the plan, not only its order

| # | Question | Why it matters | State | Answer |
|---|---|---|---|---|
| Q1 | The full traceback (`%LOCALAPPDATA%\allwell\allwell-boot.log`) | Confirms E2 is the path that crashed, or names another (a pooling view, the curation pass at load, the FOV integrity check) | `answered` | Not needed (user): E2 is accepted as the path. M1's test checks that opening reads no full frame, so it holds whatever the exact frame was |
| Q2 | How many wells, how many rows and columns in the largest, and the machine's RAM | Sets the memory budget (M4) and whether any view can pool every cell at all (M7) | `answered` | User: assume a full 96-well plate of ~300 MB CSVs, ~1.1M rows each — about 19 GB held today by E3's measurement. The budget (M4) defaults to a share of physical RAM read at runtime rather than a figure for one machine |
| Q3 | Whether the user's environment has pyarrow (`python -c "import pyarrow"` in the `allwell` env) | Without it the Parquet cache is off and every open parses every CSV — 7.7 s a well at this size before any memory question — and M2 must fall back to CSV `usecols` | `answered` | Not needed: M2 serves both — the Parquet cache's column reads with pyarrow, and CSV `usecols` reads without it — and M8's benchmark measures both |
| Q4 | Which plots, if any, the user needs over every cell of the plate at once | M7: a whole-plate per-cell view at this size cannot be drawn from all rows; it has to aggregate or sample, and the user should choose which | `answered` | Not needed: M7 aggregates where a view's output is an aggregate and samples, with the n on the figure, where it draws cells; the choice is a setting, not a question |

## Decisions — what the plan deliberately does not do

| # | Decision | Reason | State | Evidence |
|---|---|---|---|---|
| D1 | **No query engine in this PR.** DuckDB is not added | Every number a plot draws is filtered by the inclusion chokepoint (`df_included_mask`), and the verdicts it composes — curation, QC, crop, gating, field quality, focus, field exclusions — are computed in Python by seven projectors. Pushing an aggregate down to an engine means re-expressing all seven in SQL, which is a project of its own with a silent-wrong-number failure mode at each one. The memory problem does not need it (E6: one well at a time is bounded in pandas too). Reconsider when the timings M9 records show a view the bounded cache cannot make usable | `declined` | E6 |
| D2 | **No column-subset frames in the row cache** (the `get_rows(columns=...)` the earlier plan proposed) | The projectors fail open by design: a verdict whose input column is absent returns all-pass (`cache_projectors.project_verdict_column`). A frame holding only the columns its first caller asked for would therefore be projected as "every cell included" whenever a QC metric, a centroid or a gate axis was not among them, and nothing would say so. Column reads are used where no verdict is involved — the per-well summaries (M4) | `declined` | Code read: `cache_projectors.py`, the verdict functions' fail-open contract |
| D3 | **No `float32` conversion** | It halves what a well holds (E3's note), but it changes the numbers in every export and statistic, and with the cache bounded (M1) the crash does not depend on it | `declined` | E3; M1 |

## Plan — the follow-up PR

The order is the order of work. M1–M3 bound the memory without letting a bounded cache change any number; M4–M6
remove the passes that read every well at once; M7–M9 make the remaining costs visible.

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| M1 | **A bounded row cache.** `DatasetState.cache` becomes a `RowCache`: a mapping that keeps the held frames' total size (`memory_usage(deep=True)`, measured as each frame enters) under a byte budget by evicting the least recently used well. Every existing `cache[...]`, `in`, `.get`, `.clear` and iteration keeps working. Thread-safe — the gating worker reads rows off the GUI thread, and a read now reorders the cache — with iteration over a snapshot. The budget defaults to a share of physical memory, can be set (`ALLWELL_ROW_CACHE_MB`), and is reported with what is held. Eviction does not bump `rows_version`: a reloaded well is projected from the same state, so its memoized aggregates stay valid. Test: a budget smaller than the plate keeps the held bytes under it while every well stays readable and every number is unchanged | `review/core/row_cache.py` (new), `review/core/dataset_state.py` | `todo` | |
| M2 | **A reloaded well comes back with every edit on it.** Intensity gating is the one writer onto cached frames that is not a registered projector: `GatingWorker` writes `Included` / `Gate_Pass` / `Gate_On` once, and a well evicted afterwards would reload from the CSV with the gates gone — every plot would quietly count ungated cells. Gating becomes a projector, registered before curation (curation overrides gating's `Included`), applying the gates from `ctx.state.cell_gating` exactly as the worker does, and only when the worker's own condition says a gate is active. Test: gate, evict, read again — the three columns are those the worker wrote; and the same with a curation override on top | `review/features/home/cell_gating/`, `review/features/registry.py` | `todo` | |
| M3 | **Nothing reads the resident wells as if they were the plate.** With eviction, iterating `dataset.cache` sees only the wells that happen to be held, so every reader that means "the whole plate" would undercount without error. Each direct reader is audited and either reads every well (through `get_rows`, one at a time) or is shown to mean only the held frames (projector walks, which re-apply on reload). The roster, with the outcome per site, goes in this row before the change. Guard: an AST test that fails on a new read of `dataset.cache` outside the audited sites | `review/core/dataset_state.py`, `review/features/home/cell_gating/controller.py`, `review/features/qc/`, `review/features/crop/controller.py`, `review/data/dataset_dims.py`, a new test | `todo` | |
| M4 | **Opening reads no rows.** The load loop registers each well's path and reads nothing. What opening needs from every well — the timepoints, the FOVs, the row count, the column names (channel detection) and the FOV-label verdict — comes from a per-well summary computed from only those columns (a Parquet column read, or the CSV parse that fills the Parquet cache on a miss), stored beside the Parquet cache under the same source fingerprint. `rebuild_timepoints` / `rebuild_fovs` and channel detection read summaries. Test: opening reads no well's full frame (spy on `read_well_frame`), and the timepoints, FOVs, channels and FOV verdicts equal those computed from the rows | `review/data/well_summary.py` (new), `review/core/load_controller.py`, `review/core/dataset_state.py`, `review/core/channel_state_controller.py` | `todo` | |
| M5 | **The threshold range is scanned one well at a time.** `_recalculate_threshold` concatenates every included value of the channel across the plate before taking a min and a max — 106M floats, 850 MB, for a 96-well plate. It takes each well's min and max instead, holding one well at a time; the result is identical | `review/core/channel_state_controller.py` | `todo` | |
| M6 | **No view holds more than one group's rows at once.** Inventory every caller that keeps several wells' frames alive together — `pd.concat` across wells, lists or dicts of frames, `iter_plot_groups` consumers that collect before reducing — with the bound each one has. A caller whose bound is a replicate group stays; one whose bound is the whole plate reduces per well (or per group) as it goes. The inventory goes in this row first | `review/features/*/`, `review/data/data_loading.py` | `todo` | |
| M7 | **A failure that says what happened.** A `MemoryError` while reading a well is caught at the read and re-raised as an error naming the well, its size on disk, what the cache holds and its budget, instead of the generic unexpected-error dialog | `review/data/data_loading.py` | `todo` | |
| M8 | **The first open parses faster.** The CSV parse that fills the Parquet cache takes 15.3 s for one well of this size (E5) — 24 minutes for a 96-well plate before anything is drawn. The pyarrow CSV reader measured 2.9 s with no higher peak (E3's note). Adopted only if it produces the same frame — values, dtypes, text columns, the same columns — as the current parse on the app's own CSVs, including the messy ones (blank fields, mixed-type columns, a stray string in a numeric column); otherwise declined with the difference | `review/data/data_loading.py` (`load_well_csv`) | `todo` | |
| M9 | **A scale benchmark** (a script, not in the fast lane) that writes a synthetic plate of the user's well size and records the time and peak memory of opening it and of drawing one plate-wide plot, so the result is measured against the report rather than asserted | `scripts/` | `todo` | |

## Docs owed by the PR

`ARCHITECTURE.md`'s data-loading section (the bounded row cache and its budget, gating as a projector, the per-well
summaries, the audited readers of the cache), and the README's Review chapter (opening large datasets, the budget,
what the status shows).
