# CSV column rename — tracker

One clean break: every exported spread / count column takes a name that says
what it holds, and the two plotting tabs stop spelling the same quantity two
ways. Old names disappear in this release; anything downstream reading them
updates once.

This file is live state, not a write-up. Its counts are read off its own rows,
and it is **deleted by the commit that closes its last row** (`CLAUDE.md` §6.1).

## The mapping

| old | new | why |
|---|---|---|
| `time_h` (line) | `timepoint_h` | the bar CSV's name; one word for one thing |
| `err_{band}_{ch}_{metric}` (bar) | `{band}_{ch}_{metric}` | the line CSV's name; the value column beside it is `mean_…` |
| `n_above_threshold` | `events_total` | it is the integer count of events, pooled across a set's wells |
| `n_above_replicate_mean` | `events_per_replicate_mean` | the value the N panel draws: mean events per well, or per FOV |
| `err_n_above_replicate_{band}` | `events_per_replicate_{band}` | its spread across those replicates |
| `n_above_replicate_unit` | `replicate_unit` | `well` / `fov` — what "per replicate" means on this row |
| `err_n_above_{band}` (bar) | *dropped* | held the same number as `events_per_replicate_{band}` |
| `fraction_above` | `fraction_above_threshold` | says which threshold, matches the panel title |
| `err_frac_{band}` | `fraction_{band}` | the spread of the column above it |
| `n_total` | `cells_total` | a count of cells, not a denominator |
| `err_{label}` (Heat Map) | `{value_column}_{label}` | names the column it is the spread of |
| `{axis}_error` (scatter) | `{axis}_{band}` | the band moves into the name, as everywhere else |
| `x_err` / `y_err` (batch scatter) | `x_{band}` / `y_{band}` | same |
| `spread_kind` (scatter) | *dropped* | redundant once the band is in the column name |

Unchanged: `mean_{ch}_{metric}`, `threshold`, `metric`, `replicate_set` /
`wells` / `well_names` / `n_wells` / `well` / `well_name`, every
`fold_change_*`, `spread_unit`, and the Response CSV (its columns already read
plainly).

## Rows

| # | site | state |
|---|---|---|
| 1 | `export/service.line_metric_fieldnames` / `line_metric_row` | **done** |
| 2 | `export/service.bar_metric_fieldnames` / `bar_metric_row` | **done** |
| 3 | `export/service.export_heatmap_data` | **done** |
| 4 | `export/service.export_scatter_agg_data` | **done** |
| 5 | `batch_export/scatter_panel` — both aggregate jobs | **done** |
| 6 | `batch_export/base_panel` / `bar_panel` — fieldname lists they build themselves | **done** |
| 7 | every test asserting an old name | **done** — 10 files; the schema itself is now pinned by `test_the_two_tabs_write_the_same_column_names` |
| 8 | `README_WellViewer.md` + `ARCHITECTURE.md` | **done** |
| 9 | full fast lane + the slow `test_csv_batch_export_eval.py` | todo |
| 10 | grep for stale old names in code, tests and docs | **done** — the sweep also caught prose in six modules that named the old columns |

9 of 10 done.
