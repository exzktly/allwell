# Re-reads — opening a large plate reads each well many times over

A remediation tracker (CLAUDE.md §6.1), written before any code. One row per item, updated in the same commit as
the work, closed only with evidence, and deleted by the PR that closes its last row. The large-dataset tracker
before it (in the git history of `_Docs/Markdowns/LARGE_DATASET_TRACKER.md`) bounded what the row cache *holds*;
this one is about how often the app *reads*, and what each read costs, once the plate no longer fits.

**Status, derived from the rows below:** 21 rows — 6 `done`, 15 `todo`, 0 `declined`.
Recount with `grep -c '| \`done\` |'` and so on; never edit this line by hand alone.

## The report

On 9.12.9, immediately after an analysis run, a dataset opened normally. A recipe was then applied to it with three
scopes ticked — **Sample variables, Sample definitions, Gating** — and the reload crashed. Every reopen after that
read the first `.csv.gz` slowly, passed the rest "almost instantly", sat for a while after the progress finished,
and crashed without the dataset appearing. Each well's `.csv.gz` is about **300 MB**. Switching back to the Home
tab took **up to five minutes**. One later reopen, unchanged, did not crash. No boot log was found in
`~/.cache/allwell/` (only remote-bootstrap logs), so there is no stack for the crash. The machine's RAM is not
known.

## Evidence — what is established, and how

Measurements are on a synthetic plate of 8 wells × 1,500,000 rows, 41 columns, 172 MB of `.csv.gz` per well and
about 465 MB per frame in memory, opened through the real `ReviewApp._load_path` offscreen, with
`ALLWELL_ROW_CACHE_MB=1000` so that two wells fit, as on the reporter's machine. "+recipe" writes
`cell_gating = {"fluor_gates": {"gfp": 50.0}}`, two `sample_definitions` groups of four wells each and one
two-valued `variables` axis into `pipeline_info.json`. Reads are counted by wrapping
`data_loading.read_well_frame` (full frames) and `parquet_cache.read_cached` (any Parquet read); peak memory is
RSS sampled every 20 ms from `/proc/self/statm`, with every thread's stack recorded at the peak.

| # | Finding | How it is known | State | Evidence |
|---|---|---|---|---|
| E1 | The "almost instant" wells are by design: `load_controller._open_wells` stops reading rows once the row cache has evicted a well, and registers the rest from their summaries. On a plate whose first well nearly fills the budget, only one or two wells are read during the progress bar | Read in `_open_wells`; reproduced (3 full reads during `_open_wells` on the fixture) | `done` | Established |
| E2 | One open re-reads every well many times after the progress bar finishes. Without the recipe: **64** full reads of 8 wells. With it: **165**, about 20 per well | Measured, counts above | `todo` | Established; fixed by R1–R6 |
| E3 | Peak memory: **3.3 GB** without the recipe, **4.3–4.7 GB** with it, against a 1 GB budget and a 3.7 GB plate. The budget bounds what is held, not the transient of a read | Measured | `todo` | Established; R7, R8 |
| E4 | Reading one well costs several times its frame. 2M rows, 589 MB frame: CSV parse 16.3 s at 3.6 GB peak RSS; Parquet hit 0.4 s at 1.9 GB | Measured in a fresh process per read | `todo` | Established; R8 |
| E5 | The Home tab after a gating pass re-reads every well: 8.0 s and 8 full reads on the fixture, 0.1 s and 0 reads without a gate. `per_well_focus_tallies` is memoized on `rows_version`, which the gating pass bumps once per well | Measured by calling `activate_home` twice after the load settles | `todo` | Established; R5 |
| E6 | With a gate, the gating worker and the GUI thread read wells at the same time (at most 2 concurrent reads, against 1 without). A process-wide lock around `read_well_frame` brought that to 1 and left the peak unchanged (4.35 GB vs 4.39 GB): the second thread's cost at the peak was pandas `take` inside the gating pass, not a read | Measured; the lock was reverted | `todo` | Established; R7 |
| E7 | The Distribution KDE built a `200 × n` matrix with four temporaries: 917 MiB peak for the 200,000-value sample cap | `tracemalloc` | `done` | Fixed in `906e002` (`_gaussian_kde` sums in blocks); `tests/test_distribution_kde_is_bounded.py`, which fails at 917 MiB on the old code |

Not established: that the reporter's crash was memory exhaustion. It is the mechanism the measurements point to and
the one an unchanged reopen surviving fits, but no stack or RAM figure confirms it.

## Where the 165 reads come from

Full reads per pass, one open with the recipe, 8 wells (the pass named is the nearest caller that is not plumbing):

| Reads | Pass |
|---|---|
| 48 | `group_replicate_stats` ← `collect_scatter_agg_data` ← `redraw_scatter_agg` (from `_recalculate_threshold`) |
| 32 | `group_replicate_stats` ← `compute_rep_stats` / `compute_rep_n_above_stats` ← `collect_line_series` |
| 22 | `cell_gating_load_cell_areas` — three times per open: twice from `set_ratio_metrics`, once from `_recalculate_threshold` |
| 16 | `cdf_values_cached` ← `collect_line_series`, once after load and again when the gating pass finishes |
| 16 | `_recalculate_threshold`'s own `every_well` scan and `get_all_timepoints` ← `refresh_distribution_timepoints` |
| 10 | `per_well_focus_tallies` ← `wells_needing_review` ← Home's `_paint_plate` |
| 6 | `stats_update_tp_menu` |
| 6 | the gating worker's `_run_inner` (cache misses only) |
| 6 | `_cell_gating_refresh_opt_timepoints` |
| 3 | `_open_wells` |

Every one of these passes needs a handful of columns, or a few numbers per well, and reads the whole frame to get
them, because `get_rows` is the only per-well read most of them know.

## The plan

The rule this follows is the one F2 of the previous tracker set: **a plate-wide pass reads each well narrowly
(`narrow_rows`, which carries every verdict layer's declared input columns, so D2's fail-open trap does not apply)
and keeps a small per-well answer (`plate_scan.per_well`), never the frame.** Nothing here changes a number any view
draws; each row's test holds its output equal to the full-frame path on a fixture where a wrong column set would
change the answer.

| # | Item | Where | State | Evidence |
|---|---|---|---|---|
| R1 | **`group_replicate_stats` reads the whole frame for two numbers.** Its per-well branch memoizes the aggregate (`aggregate_well_cached`) but then calls `_safe_rows` for `_raw_extra`'s median and all-cell mean on every call — per group, per timepoint, per producer. Move `_raw_extra` behind `plate_scan.per_well` keyed on `(target_t, threshold, val_col, gates, ratios, on_col)`, reading narrowly. The FOV branch needs the rows and keeps them, one well at a time | `review/domain/stats_compute.py` | `done` | 80 of 165 reads. On the fixture, one open with the recipe went from 165 full reads to 84 and from 88 s to 70 s; the scatter-aggregate and line-statistics rows are gone from the breakdown. `tests/test_replicate_extras_read_narrowly.py`: the extras equal the whole-frame `_raw_extra` at both timepoints with every verdict layer on (values differ threefold between them), the thresholding on-column decides "above", a tolerance-only target is computed for itself, and three producers over two timepoints make two narrow reads. Mutations restoring the full-frame read, filing every entry under the first timepoint, and ignoring the on-column each fail it |
| R2 | **`cell_gating_load_cell_areas` runs three times per open** and reads every well each time. Run it once, after the last input it depends on (ratios) is hydrated, and read narrowly | `review/features/home/cell_gating/controller.py`, `load_controller.load_directory` | `todo` | 22 reads |
| R3 | **Timepoint and range scans read full frames.** `_recalculate_threshold`'s extent scan, `get_all_timepoints`, `stats_update_tp_menu` and `_cell_gating_refresh_opt_timepoints` each want the value or timepoint column of every well. Narrow reads through `plate_scan`; one timepoint scan per open shared by its three consumers | `review/core/channel_state_controller.py`, `review/data/data_loading.py`, stats and Cell Gating controllers | `todo` | 28 reads |
| R4 | **`cdf_values_cached` reads full frames** for one column of values. Narrow read | line plot `collect` | `todo` | 16 reads |
| R5 | **Home's focus tallies rescan the plate after every gating change.** The tally depends on inclusion, so the invalidation is right; the cost is that each rescan is a full read. Make it narrow, and decide whether the gating pass should compute the tally while it holds the well (it already visits every well) | `review/data/focus_gate.py`, Cell Gating worker | `todo` | E5 |
| R6 | **The plate is drawn twice when a load starts a gating pass.** The post-load redraw drew pre-gating numbers, and `_cell_gating_on_finished` redrew everything again; the channel refresh inside the load also drew Scatter, Scatter Aggregate, Response and the Heat Map whatever tab was in front. **Decided by the reporter: do not draw twice.** | `review/core/plot_hold.py`, `load_controller`, the redraw chokepoints | `done` | A load holds every plot and draws the visible tab once at its end; a load that starts a gating pass hands the hold to it, every plot shows *Applying cell gating…*, and the pass's end (or failure) draws the visible tab once. On the fixture the per-open draws before gating finished went from line, distribution and heat map twice plus scatter, scatter aggregate and response once, to none; the open went from 70 s to 45–52 s. `tests/test_a_load_draws_its_plots_once.py`: one draw after the pass with the user on Line Graphs during it, the placeholder meanwhile, a failed pass still lifting the hold, and no off-screen Scatter Aggregate collection during a reload with groups. Mutations — never handing the hold to the pass, dropping the load hold, a failed pass keeping the hold — each fail it |
| R7 | **The gating pass's own transient.** Find what the worker allocates at the peak (`take` on a boolean mask over the whole frame inside `apply_fluor_gating`) and write the verdict columns without copying the frame | `review/features/home/cell_gating/cell_gating_compute.py` | `todo` | E6 |
| R8 | **A read costs 3–6× its frame.** Measure where: `table_to_dataframe` on the Parquet path; `fillna` copies, `normalize_fov_series` and `_categorize_text_columns` on the CSV path. Candidates: `to_pandas(self_destruct=True, split_blocks=True)`, in-place FOV normalisation. Adopted only if the frame is identical (values, dtypes, categories) | `review/data/data_loading.py`, `review/data/parquet_cache.py` | `todo` | E4 |
| G1 | **The benchmark measures the case.** Extend `tests/perf/bench_large_plate.py` with `--gate` and `--groups`, the read counts per pass (the table above) and the Home-after-gating time, so each row's effect is a number against this file's baseline | `tests/perf/bench_large_plate.py` | `todo` | |
| G2 | **A read budget the suite enforces.** A slow-lane test opens the mini dataset with a gate and two groups and counts full reads per well per open, against a budget that only moves down (the shape of the other ratchets). Proven by restoring one removed re-read and watching it fail | `tests/` | `todo` | |
| G3 | **The crash leaves evidence.** The reporter found no `allwell-boot.log` | `startup/boot_diag.py`, `allwell.py`, `review/core/load_progress.py` | `done` | Not established why the file was absent: importing `allwell` creates `~/.cache/allwell/allwell-boot.log` from source and with `sys.frozen`/`_MEIPASS` simulated, the packaged launcher reaches that import, nothing disables or deletes the log, and the reporter's `bootstrap-*.log` files sit in the same folder. Established and fixed: a session the system kills (out of memory) writes no crash marker, so the next launch overwrote its log; and `user_state_dir` accepted an existing directory it could not write, losing the log with no fallback. A session that reached the event loop without `[boot] clean exit` is now kept as `allwell-boot.log.1` with a note (unless its pid is still alive), the next launch says so in the log drawer with the path, the drawer names the log's path, each load phase is written to the log (`dataset load: <phase>`), and unwritable state directories are skipped. `tests/test_a_killed_session_leaves_its_log.py` (9 tests, each shown to fail on its HEAD mutation) and `tests/test_log_notice_badge_app.py`. Still to ask the reporter: `ls -la ~/.cache/allwell/` and `ls -ld ~/.cache/allwell` |
| G4 | **Docs.** ARCHITECTURE's rows for `plate_scan`, `stats_compute`, `load_controller` and the Cell Gating controller describe the read each pass makes; README's large-plate section says what an open costs | `_Docs/Markdowns/` | `todo` | |
| S1 | **The status line went quiet after the CSV count** (reported during this work: "sits at 40/40 with a spinning mouse icon with no indication of what is happening"). On a plate larger than the row cache the count races ahead and every later phase reads wells with nothing on screen | `review/core/load_progress.py`, `load_controller`, `data_loading._announce_read`, `narrow_rows` | `done` | Each phase is named, each well read from disk on the GUI thread is named with a running read count, "Loaded" comes last, and the gating pass counts its wells once the load has finished. `tests/test_a_load_says_what_it_is_doing.py`; removing the read announcement and saying "Loaded" before the phases each fail it. Repainting between phases delivers queued signals, so a short gating pass can finish inside the load: the hold is handed over, and "applying cell gating…" said, only if the pass is still running |
| Q1 | **Question for the reporter:** the machine's RAM, and whether the Distribution tab was open. Settles whether E3's peak at their well size crosses physical memory | — | `todo` | |

## Acceptance

Measured on G1's fixture, against the baseline above:

- full reads per well per open with the recipe: from about 20 to at most 2 (the open itself and the gating pass);
- the Home tab after a gating pass: from 8.0 s to under 1 s;
- peak memory with the recipe: no higher than without it.
