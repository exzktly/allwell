# CSV & Batch Export — Vigorous Evaluation Report

**Date:** 2026-07-05
**Scope:** Every CSV export and every batch export path in All-Well, exercised
with well groups defined **both as replicate sets and as single wells**.
**Test file:** [`tests/test_csv_batch_export_eval.py`](../../tests/test_csv_batch_export_eval.py)
**Result:** ✅ 15/15 new end-to-end tests pass · full export/batch suite green ·
**2 real bugs found and fixed.**

---

## 1. What was evaluated

All-Well writes CSVs from two places (see `ARCHITECTURE.md` §5.3 and the
export/batch-export feature packages):

| # | Surface | Entry point | Grouping unit |
|---|---------|-------------|---------------|
| 1 | On-tab **Line** "Export CSV" | `features/export/service.export_plot_data` | selected wells; replicate-set fallback |
| 2 | On-tab **Bar** "Export CSV" | `features/export/service.export_bar_plot_data` | selected wells (well mode) / replicate sets (group mode) |
| 3 | On-tab **Scatter (cells)** | `features/export/service.export_scatter_data` | selected wells / replicate sets |
| 4 | On-tab **Heatmap** | `features/export/service.export_heatmap_data` | plate cells |
| 5 | On-tab **Distribution** | `features/export/service.export_distribution_data` | plot groups |
| 6 | **Batch Line** | `features/batch_export/base_panel._run_batch` | `BarGroup` = replicate sets + solo wells |
| 7 | **Batch Bar** (pooled & expanded) | `features/batch_export/bar_panel._run_batch` | `BarGroup` = replicate sets + solo wells |
| 8 | **Batch Scatter (cells)** | `features/batch_export/scatter_panel._run_batch` | `BarGroup` = replicate sets + solo wells |
| 9 | **Batch Scatter (aggregate)** | `features/batch_export/scatter_panel._run_batch` | `BarGroup` = replicate sets + solo wells |
| 10 | **Atomic staging / manifest** | `features/batch_export/service.commit_staged_export` | — |

The "replicates vs single wells" distinction is a first-class concept in the
data model: a batch **`BarGroup`** carries `members` (a list of
**`ReplicateSet`**, each with ≥1 well) *and* `solo_wells` (single wells). The
batch CSVs encode this in a **`member_type`** column:

- `replicate_set` — a pooled replicate set (one row per timepoint)
- `replicate_set_member:<name>` — an individual member of a replicate set,
  when "Pool replicate-set wells" is **off** (bar batch, expanded mode)
- `solo_well` — a single well

Every batch test below uses a group that **mixes a replicate set with a single
well** so both branches are covered in one run.

---

## 2. Methodology

- **Deterministic fixture.** Tests load the closed-form
  [`tests/fixtures/mini_dataset`](../../tests/fixtures/mini_dataset.py) (wells
  A01/A02/B01/B02, timepoints 0/24/48 h, 6 cells each). Every measurement is a
  closed-form function of `(well, timepoint, cell)`, so the tests assert
  **exact** aggregate values, not just structure:

  ```
  GFP cell value = 100 + well_index*10 + tp_h*2 + cell   (cell = 1..6)
  per-well mean(tp) = base + tp_h*2 + 3.5
    A01: 103.5 / 151.5 / 199.5      A02: 113.5 / 161.5 / 209.5
    B01: 123.5 / 171.5 / 219.5      B02: 133.5 / 181.5 / 229.5
    pooled mean of A01+A02 @ t0 = 108.5   (pool of 12 cells)
  ```

- **Real app, no stubbed logic.** A full `WellViewerApp` is constructed on the
  offscreen Qt platform and the dataset is loaded through the normal
  `_load_path` path. Only the *save-file dialog* is stubbed (to a temp path);
  the aggregation, collection, figure-render, staging and commit code all run
  for real. The written bytes on disk are parsed back and asserted.

- **Both grouping modes, every surface.** On-tab exports are driven once with a
  raw single-well selection and once with an active replicate-set selection;
  batch exports are driven with a mixed replicate-set + single-well group.

---

## 3. Bugs found and fixed

### Bug 1 — Batch **Line** export was completely broken (wrote nothing)

**File:** `well_viewer/features/batch_export/_line_series_runner.py`

The line batch figure render collects a CDF via
`collect_line_series_for_group(..., include_cdf=True)`, whose `_cdf_for` helper
did:

```python
from well_viewer.data.data_loading import all_fluor_values as _all_fluor_values
```

The function in `data_loading.py` is named **`_all_fluor_values`** (leading
underscore); `all_fluor_values` does not exist. The import raised
`ImportError` on **every** line batch export, the figure job returned an error
string, and `_run_batch_jobs` therefore **discarded the entire staged
export** — including the CSV that had already been written correctly. Net
effect: the Batch Line export silently produced **zero output files**.

This was not caught by the existing suite: no test drove the line batch
`_run_batch` figure path.

**Fix** (one line):

```python
from well_viewer.data.data_loading import _all_fluor_values
```

**Guarded by:** `test_batch_line_replicates_and_single_wells`,
`test_batch_line_regression_figure_render`.

### Bug 2 — Batch **Scatter** exports silently dropped single wells

**File:** `well_viewer/features/batch_export/scatter_panel.py`
(`_app_group_scope`)

`_app_group_scope` set up the app selection state so a batch group's replicate
sets became `selection.selections` and its single wells became
`selection.selected_wells` — and its docstring promises "one selection per
replicate-set member, **plus the group's solo wells**".

But the shared collector `collect_scatter_data` uses **replicate sets _or_
selected wells — never both**:

```python
if active_rsets:
    ... # iterate replicate-set wells only
else:
    ... # selected-wells path
```

So for any batch scatter group that mixed a replicate set with a single well,
the single well was **silently omitted** from both the CSV and the figure —
a data-loss bug squarely in the "replicates + single wells" theme.

**Fix:** represent each solo well as its own single-well selection inside the
batch-only `_app_group_scope`, so the collector's replicate-set branch picks it
up alongside the real replicate sets (changing the shared collector's on-tab
semantics was deliberately avoided). Applies to both scatter cells and scatter
aggregate (they share the scope).

**Guarded by:** `test_batch_scatter_cells_replicates_and_single_wells`,
`test_batch_scatter_agg_replicates_and_single_wells`.

---

## 4. Test inventory (`tests/test_csv_batch_export_eval.py`)

| Test | Surface | Asserts |
|------|---------|---------|
| `test_ontab_line_single_wells` | On-tab line | per-well rows, no `replicate_set` col, exact means (103.5/151.5/199.5), full column schema |
| `test_ontab_line_replicate_set_fallback` | On-tab line | rep-set fallback adds `replicate_set` col, member wells expanded, raw per-well means |
| `test_ontab_bar_well_mode` | On-tab bar | `well` id col, per-well mean @ t0 |
| `test_ontab_bar_group_mode` | On-tab bar | `name` id col, mean-of-per-well-means (108.5), SEM (5.0) |
| `test_ontab_scatter_cells` | On-tab scatter | 36 per-cell rows across 2 wells |
| `test_ontab_heatmap` | On-tab heatmap | 96 plate-cell rows, channel-named value column |
| `test_ontab_distribution` | On-tab distribution | per-cell rows, expected columns |
| `test_batch_line_replicates_and_single_wells` | Batch line | `replicate_set` + `solo_well` rows, exact pooled/solo means, figure + manifest committed |
| `test_batch_line_regression_figure_render` | Batch line | **regression for Bug 1** — CSV + figure produced |
| `test_batch_bar_pooled_replicates_and_single_wells` | Batch bar (pooled) | rep set → 1 `replicate_set` row (108.5), single well `solo_well` (123.5) |
| `test_batch_bar_expanded_replicates_and_single_wells` | Batch bar (expanded) | each member → `replicate_set_member:RepA`, single well `solo_well`, raw per-well means |
| `test_cross_path_solo_well_bytes_match` | On-tab ↔ batch | bar metric columns byte-identical across the two writers |
| `test_batch_scatter_cells_replicates_and_single_wells` | Batch scatter cells | **regression for Bug 2** — A01, A02 **and** B01 present |
| `test_batch_scatter_agg_replicates_and_single_wells` | Batch scatter agg | rep set + single well aggregated points; single well spread = 0 |
| `test_batch_failure_preserves_prior_export` | Atomic staging | mid-run figure failure → prior export intact, no temp dir leaked |

---

## 5. Results

```
$ python -m pytest tests/test_csv_batch_export_eval.py -q
............... [100%]
15 passed
```

The wider export/batch regression lane (`-k "export or batch or scatter or line
or grouping or csv"`) is also green after both fixes.

## 6. How to run

```bash
# lightweight deps only (no ML stack needed)
pip install pytest pytest-qt pytest-mock pytest-timeout numpy pandas matplotlib pillow PySide6 tifffile scipy scikit-image imageio

# the evaluation module (headless Qt)
QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg python -m pytest tests/test_csv_batch_export_eval.py -v
```

The module is auto-marked `slow` (it builds the full app once per module), so
it is excluded by the fast CI gate `-m "not slow"` and runs in the full pass.
