# Size classes per nanowell — implementation tracker

**This document is temporary.** CLAUDE.md §6.1 requires a body of work this size to live on disk. It is deleted
by the pull request that closes its last row, and that pull request says so in its body.

## What is being built

A user discriminating two cell types by size alone (for example T cells and dendritic cells, with no marker
imaged) defines two **labelled area ranges** on Home → Cell Gating. Each nanowell's kept cells are counted per
range, and the Nanowells page, the plot tools and the export gain five per-nanowell metrics: the count in each
class, the cells in neither, the ratio of the first class to the second, and the first class's share of the
classified cells. Each well's value is the **mean** over its nanowells; the Distribution view shows the spread
across nanowells, as it does for every metric.

## Decisions (settled with the user, 2026-11-09)

| # | Decision | Why |
|---|---|---|
| D1 | Defined in Review, on Cell Gating, stored with the QC parameters (`QCParams.size_classes`). No re-run. | The per-cell rows already carry `area_px` and the nanowell address (`pipeline/nanowell_task.py`); thresholds get iterated; Cell Gating is where "which cells count as what" lives. Storing with the QC parameters gives persistence, dataset-tool carry-over, the recompute and the redraw through the one route a QC edit already takes. |
| D2 | Two classes, each with a user-typed label and an inclusive `[min, max]` area in **pixels**, read from the same area column as the debris gate. Ranges may leave a gap; they may not overlap. | The user's populations overlap in size; a gap lets ambiguous objects stay out of both classes rather than be forced into one. Pixels match the existing area gate. |
| D3 | Beside each range, its equivalent-circle diameter in µm at the run's pixel size; when timepoints were imaged at different pixel sizes, the panel says so. | Pixel cutoffs silently mean different cell sizes when the pixel size changes. |
| D4 | Classes apply to the cells Cell Gating keeps; a cell in neither range is counted as *unclassified*, never dropped silently. | Degraded output must be distinguishable (CLAUDE.md §7). |
| D5 | Undefined is NaN with a reason, never 0: no ranges set; the well's cells could not be read; a ratio whose second class is empty; a share with no classified cells. A nanowell with no cells has counts of 0. | §7 #3. |
| D6 | Per-well value = mean over the well's nanowells for every size metric; the distribution is the Distribution view's swarm. | The user asked for the average; counts per nanowell are small integers whose median is lumpy. |
| D7 | The user's labels replace "Size class 1" / "Size class 2" wherever a metric is named. | The populations must not be hard-coded. |

## Rows

States: `todo`, `doing`, `done` (with the test that pins it), `declined` (with the reason).

| Row | Item | Place | State | Evidence |
|---|---|---|---|---|
| S1 | Rule model: `SizeClassRule` (labels, ranges, validation, key, text, naming), Qt-free | `review/domain/size_classes.py` | todo | |
| S2 | `QCParams.size_classes`: field, to/from dict, `with_updates`; persisted with the QC block | `review/domain/qc_params.py` | todo | |
| S3 | Per-nanowell class counts over kept cells, carried on the gate tally | `review/features/nanowell/size_class_compute.py`, `cell_gate_compute.py`, `controller.gate_tally` | todo | |
| S4 | Metrics in `site_metrics`, `METRICS`, `metric_kinds`, exclusion reasons and their labels, the long CSV | `series_compute.py`, `distribution_compute.py` | todo | |
| S5 | Rule key in every staleness/memo key (page load, observations memo) | `controller.ensure_loaded` / `redraw`, `observation_compute` | todo | |
| S6 | Well mean for size metrics: well summary, group summary, Distribution line and notes, plot-tool well values | `summary_compute.py`, `distribution_compute.py`, `review/domain/observation.py`, scatter | todo | |
| S7 | User labels in metric names: page combo, figure label, plot tools, Subset fields | `tab_view.py`, `controller.py`, `observation_compute.py`, `subset_compute.py` | todo | |
| S8 | Cell Gating panel row: two label + min + max rows, µm readout, validation message, status line; nanowell datasets only | `review/features/qc/size_class_view.py`, `qc/tab_view.py` | todo | |
| S9 | Cell Gating area curve shows each class's range as a labelled band | `review/features/home/cell_gating/renderer.py` | todo | |
| S10 | Figure footer names the classes and counts the unclassified cells | `size_class_compute.note`, Distribution model | todo | |
| S11 | One test through the real control (type ranges on the panel → metrics on the page) | tests | todo | |
| S12 | New test files on the nanowell-features roster | `tests/_lanes.py` | todo | |
| S13 | Docs: ARCHITECTURE §6.2j, README Cell Gating and Nanowells | `_Docs/Markdowns/` | todo | |

**Counts** (derived from the rows above when updated): 13 rows — todo 13, doing 0, done 0, declined 0.
